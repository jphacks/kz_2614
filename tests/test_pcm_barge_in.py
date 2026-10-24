"""Confirmed server speech interrupts replies without ending the PCM session."""

from __future__ import annotations

import asyncio
import io
import threading
import time
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from server.ai.llm.lm_studio import VoiceLLMReply
from server.ai.stt.whisper import Transcription
from server.ai.tts.irodori import SynthesizedSpeech
from server.ai.voice.pcm_stream import ServerAudioStreams
from server.ai.voice.pipeline import VoiceStreamTurnResult
from server.ai.voice.server_vad import ServerVoiceActivity
from server.ai.voice.sessions import VoiceAPIError, VoiceSessions
from test_pcm_stream import SILENCE, VOICE, SpeechProbability


class GatedPipeline:
    def __init__(self, first_stage, *, hold_native_stt=False):
        self.first_stage = first_stage
        self.hold_native_stt = hold_native_stt
        self.entered = [threading.Event(), threading.Event()]
        self.release = [threading.Event(), threading.Event()]
        self.cancelled = threading.Event()
        self.native_release = threading.Event()
        self.late_rejected = threading.Event()
        self.previewed = threading.Event()
        self.calls = []
        output = io.BytesIO()
        with wave.open(output, "wb") as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(48_000)
            audio.writeframes(bytes(960))
        self.speech = SynthesizedSpeech(output.getvalue(), "audio/wav", ".wav")

        async def transcribe(path, *, on_worker_finished=None):
            self.previewed.set()
            if on_worker_finished is not None:
                on_worker_finished()
            return Transcription("new interim", "ja", 1.0, 1.0)

        self.stt = SimpleNamespace(transcribe_file_async=transcribe)

    async def process_audio_file_stream(self, path, publish, *, on_progress,
                                       on_stt_dispatched, on_stt_worker_finished):
        index = len(self.calls)
        self.calls.append((Path(path), asyncio.current_task()))
        on_stt_dispatched()
        if index == 0 and self.hold_native_stt:
            def finish_native():
                self.native_release.wait(timeout=5)
                on_stt_worker_finished()
            threading.Thread(target=finish_native, daemon=True).start()
        else:
            on_stt_worker_finished()
        stage = self.first_stage if index == 0 else "llm"
        on_progress(stage, None if stage == "stt" else f"utterance {index}")
        if stage != "stt":
            publish({"type": "transcript", "text": f"utterance {index}"})
        if index == 0 and stage == "playing":
            publish({"type": "audio_chunk", "index": 0, "text": "old", "speech": self.speech})
        self.entered[index].set()
        try:
            while not self.release[index].is_set():
                await asyncio.sleep(0.005)
        except asyncio.CancelledError:
            if index == 0:
                self.cancelled.set()
                try:
                    publish({"type": "audio_chunk", "index": 1, "text": "late", "speech": self.speech})
                except asyncio.CancelledError:
                    self.late_rejected.set()
            raise
        if not (index == 0 and stage == "playing"):
            publish({"type": "audio_chunk", "index": 0, "text": "new", "speech": self.speech})
        return VoiceStreamTurnResult(
            Transcription(f"utterance {index}", "ja", 1.0, 1.0),
            VoiceLLMReply(f"reply {index}", (), {}), {},
        )

    async def close(self):
        pass


class CapturingSessions(VoiceSessions):
    def __init__(self):
        self.recordings = []
        self.event_queues = []
        super().__init__()

    def stream_turn(self, session, generation, turn_id, path, events):
        self.recordings.append(Path(path))
        self.event_queues.append(events)
        return super().stream_turn(session, generation, turn_id, path, events)


class PCMBargeInTests(unittest.TestCase):
    def start(self, stage="llm", *, hold_native_stt=False):
        self.pipeline = GatedPipeline(stage, hold_native_stt=hold_native_stt)
        self.manager = CapturingSessions()
        with patch.object(self.manager, "_create_pipeline", AsyncMock(return_value=self.pipeline)):
            session = self.manager.create_session()
        self.session_id = session["session_id"]
        self.sequence = 0
        self.streams = ServerAudioStreams(
            self.manager, vad_factory=lambda: ServerVoiceActivity(model=SpeechProbability()),
        )
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.pipeline.native_release.set()
        for release in self.pipeline.release:
            release.set()
        try:
            self.manager.stop_session(self.session_id)
        except VoiceAPIError:
            pass
        self.streams.close()
        self.manager._loop.call_soon_threadsafe(self.manager._loop.stop)
        self.manager._loop_thread.join(timeout=3)
        self.assertFalse(self.manager._loop_thread.is_alive())
        self.assertTrue(all(not path.exists() for path in self.manager.recordings))

    def feed(self, frame, count):
        while count:
            length = min(count, 8)
            result = self.streams.push_audio(self.session_id, 0, self.sequence, frame * length)
            self.sequence += 1
            count -= length
        return result

    def status(self):
        return self.streams.get_status(self.session_id, 0)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.005)
        self.fail("Timed out waiting for gated fake processing")

    def events(self):
        return self.streams.read_events(self.session_id, 0, 0, timeout=0)

    def first_turn(self):
        self.feed(VOICE, 40)
        self.feed(SILENCE, 28)
        self.assertTrue(self.pipeline.entered[0].wait(timeout=3))
        return self.status()["turn_id"]

    def interrupt(self, old_turn):
        result = self.feed(VOICE, 12)
        self.assertEqual(result["stage"], "speaking")
        self.assertEqual(result["generation"], 0)
        self.assertNotEqual(result["turn_id"], old_turn)
        interruption = result["interruption"]
        self.assertEqual(interruption["turn_id"], old_turn)
        self.assertEqual(interruption["interrupted_turn_id"], old_turn)
        self.assertEqual(interruption["new_turn_id"], result["turn_id"])
        self.assertTrue(interruption["cancellation_requested"])
        self.assertTrue(interruption["native_work_may_continue"])
        return result["turn_id"]

    def finish_new_turn(self, turn_id):
        self.feed(VOICE, 28)
        self.assertTrue(self.pipeline.previewed.wait(timeout=3))
        self.feed(SILENCE, 28)
        self.assertTrue(self.pipeline.entered[1].wait(timeout=3))
        self.assertEqual(self.status()["turn_id"], turn_id)
        self.pipeline.release[1].set()
        self.wait_for(lambda: self.status()["turn_complete"])
        self.assertEqual(self.status()["response_text"], "reply 1")

    def test_llm_barge_in_keeps_sequence_preview_and_new_reply_without_late_old_events(self):
        self.start()
        old_turn = self.first_turn()
        new_turn = self.interrupt(old_turn)
        self.assertTrue(self.pipeline.cancelled.wait(timeout=3))
        self.assertTrue(self.pipeline.late_rejected.wait(timeout=3))
        self.finish_new_turn(new_turn)
        events = self.events()
        interruptions = [event for event in events if event["type"] == "interruption"]
        self.assertEqual(len(interruptions), 1)
        later = [event for event in events if event["cursor"] > interruptions[0]["cursor"]]
        self.assertTrue(later)
        self.assertTrue(all(event["turn_id"] == new_turn for event in later))
        self.assertEqual(self.status()["next_sequence"], self.sequence)
        self.assertEqual(len(self.pipeline.calls), 2)

    def test_silence_short_noise_and_duplicate_pcm_do_not_interrupt_processing(self):
        self.start()
        old_turn = self.first_turn()
        self.feed(SILENCE, 80)
        self.feed(VOICE, 5)
        self.feed(SILENCE, 28)
        self.assertEqual(self.status()["turn_id"], old_turn)
        self.assertFalse(any(event["type"] == "interruption" for event in self.events()))
        self.assertFalse(self.pipeline.cancelled.is_set())
        new_turn = self.interrupt(old_turn)
        duplicate = self.streams.push_audio(self.session_id, 0, self.sequence - 1, VOICE * 4)
        self.assertEqual(duplicate["next_sequence"], self.sequence)
        self.assertEqual(duplicate["turn_id"], new_turn)
        self.assertEqual(len([event for event in self.events() if event["type"] == "interruption"]), 1)

    def test_playing_reply_is_interrupted_and_old_playback_cannot_mutate_new_input(self):
        self.start("playing")
        old_turn = self.first_turn()
        self.pipeline.release[0].set()
        self.wait_for(lambda: self.status()["turn_complete"])
        self.streams.playback(self.session_id, 0, old_turn, "started")
        self.assertEqual(self.status()["stage"], "playing")
        new_turn = self.interrupt(old_turn)
        before = self.status()
        for event in ("started", "ended", "failed"):
            with self.subTest(event=event), self.assertRaises(VoiceAPIError) as error:
                self.streams.playback(self.session_id, 0, old_turn, event)
            self.assertEqual(error.exception.code, "playback_turn_mismatch")
            self.assertEqual(self.status(), before)
        with self.assertRaises(VoiceAPIError):
            self.manager.get_speech(self.session_id, old_turn)
        self.assertEqual(before["interruption"]["new_turn_id"], new_turn)

    def test_tts_wait_is_cancelled_while_new_pcm_keeps_arriving(self):
        self.start("tts")
        old_turn = self.first_turn()
        new_turn = self.interrupt(old_turn)
        self.assertTrue(self.pipeline.cancelled.wait(timeout=3))
        self.finish_new_turn(new_turn)
        self.assertEqual(self.status()["generation"], 0)

    def test_old_native_stt_cleanup_retains_wav_and_does_not_clear_new_busy_task(self):
        self.start("stt", hold_native_stt=True)
        old_turn = self.first_turn()
        old_path = self.manager.recordings[0]
        new_turn = self.interrupt(old_turn)
        self.assertTrue(self.pipeline.cancelled.wait(timeout=3))
        self.assertTrue(old_path.exists(), "Cancellation cannot delete a native decoder's recording")
        self.feed(VOICE, 28)
        self.feed(SILENCE, 28)
        self.assertTrue(self.pipeline.entered[1].wait(timeout=3))
        session = self.manager.get_active_session(self.session_id, 0)
        current_task = session.task
        self.assertTrue(session.busy)
        self.assertEqual(session.active_turn_id, new_turn)
        self.assertFalse(self.manager.interrupt_turn(self.session_id, 0, old_turn))
        self.assertIs(session.task, current_task)
        self.manager.timeout_stream_turn(session, generation=0, turn_id=old_turn)
        self.manager.abandon_turn(session, turn_id=old_turn)
        self.assertIs(self.manager.get_active_session(self.session_id, 0), session)
        self.assertTrue(session.busy)
        self.assertIs(session.task, current_task)
        identity = {"session_id": self.session_id, "generation": 0, "turn_id": old_turn}
        self.manager.event_queues[0].put({**identity, "type": "audio_chunk", "audio_base64": "late"})
        self.manager.event_queues[0].put({**identity, "type": "complete", "response_text": "late"})
        self.pipeline.native_release.set()
        self.wait_for(lambda: not old_path.exists())
        self.assertTrue(session.busy)
        self.assertIs(session.task, current_task)
        self.assertEqual(self.manager.get_turn_status(self.session_id, new_turn)["stage"], "llm")
        interruption = self.status()["interruption"]
        self.assertFalse(any(event["turn_id"] == old_turn for event in self.events()
                             if event["cursor"] > interruption["cursor"]))
        self.pipeline.release[1].set()
        self.wait_for(lambda: self.status()["turn_complete"])
        self.assertEqual(len({path for path, _ in self.pipeline.calls}), 2)

    def test_speech_candidate_survives_playback_end_and_cooldown_boundary(self):
        self.start("playing")
        now = [0.0]
        self.streams._clock = lambda: now[0]
        old_turn = self.first_turn()
        self.pipeline.release[0].set()
        self.wait_for(lambda: self.status()["turn_complete"])
        self.streams.playback(self.session_id, 0, old_turn, "started")
        self.feed(VOICE, 8)  # 256 ms: a candidate, not a confirmed interruption.
        self.streams.playback(self.session_id, 0, old_turn, "ended")
        self.assertEqual(self.feed(VOICE, 3)["stage"], "cooldown")
        now[0] = 0.6
        self.assertEqual(self.status()["stage"], "listening")
        snapshot = self.feed(VOICE, 1)  # Exactly 384 ms across both boundaries.
        self.assertEqual(snapshot["stage"], "speaking")
        self.assertNotEqual(snapshot["turn_id"], old_turn)
        self.assertTrue(self.streams._states[self.session_id].vad.audio_bytes.endswith(VOICE * 12))
        self.assertFalse(any(event["type"] == "interruption" for event in self.events()))

    def test_stop_after_interruption_fences_all_old_and_new_requests(self):
        self.start()
        old_turn = self.first_turn()
        self.interrupt(old_turn)
        stopped = self.manager.stop_session(self.session_id)
        self.streams.discard(self.session_id)
        self.assertEqual(stopped["generation"], 1)
        for operation in (
            lambda: self.status(),
            lambda: self.streams.push_audio(self.session_id, 0, self.sequence, SILENCE),
            lambda: self.streams.read_events(self.session_id, 0, 0, timeout=0),
        ):
            with self.assertRaises(VoiceAPIError) as error:
                operation()
            self.assertEqual(error.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
