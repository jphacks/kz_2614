"""PCM input keeps flowing while generated text/audio is replayable by cursor."""

from __future__ import annotations

import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, patch

from Programs.ServerSide.STTLLMTTS.stt.whisper import Transcription, WhisperConfig, WhisperSTT
from Programs.ServerSide.STTLLMTTS.voice.pcm_stream import (
    MAX_STREAM_EVENT_BYTES, MAX_STREAM_EVENTS, ServerAudioStreams,
)
from Programs.ServerSide.STTLLMTTS.voice.server_vad import ServerVoiceActivity
from Programs.ServerSide.STTLLMTTS.voice.sessions import VoiceAPIError, VoiceSessions
from test_pcm_stream import FakeManager, SpeechProbability, SILENCE, VOICE
from test_voice_merge import make_pipeline


class PCMEventTests(unittest.TestCase):
    def setUp(self):
        self.manager = FakeManager()
        self.manager.preview_release.set()
        self.now = 0.0
        self.streams = ServerAudioStreams(
            self.manager, clock=lambda: self.now,
            vad_factory=lambda: ServerVoiceActivity(model=SpeechProbability()),
        )
        self.sequence = 0

    def tearDown(self):
        self.manager.final_release.set()
        self.streams.close()
        self.assertTrue(all(not path.exists() for path in self.manager.paths))

    def feed(self, frame, count):
        while count:
            length = min(count, 8)
            self.streams.push_audio(self.manager.session_id, 0, self.sequence, frame * length)
            self.sequence += 1
            count -= length

    def status(self):
        return self.streams.get_status(self.manager.session_id, 0)

    def until_event(self, kind, after=0):
        deadline = time.monotonic() + 3
        received = []
        while time.monotonic() < deadline:
            events = self.streams.read_events(self.manager.session_id, 0, after, timeout=0.1)
            received.extend(events)
            if events:
                after = events[-1]["cursor"]
            if any(event["type"] == kind for event in received):
                return received
        self.fail(f"No {kind} event received")

    def start_turn(self):
        self.feed(VOICE, 40)
        self.feed(SILENCE, 28)
        self.assertTrue(self.manager.final_started.wait(timeout=3))

    def test_text_and_audio_arrive_before_complete_and_pcm_keeps_acknowledging(self):
        self.manager.emit_acknowledgement = True
        self.start_turn()
        early = self.until_event("audio_chunk")
        self.assertIn("text_delta", [event["type"] for event in early])
        self.assertIn("acknowledgement", [event["type"] for event in early])
        self.assertFalse(any(event["type"] == "complete" for event in early))
        self.assertTrue(all(event["session_id"] == self.manager.session_id and event["generation"] == 0 for event in early))
        self.assertEqual([event["cursor"] for event in early], list(range(1, len(early) + 1)))
        status = self.status()
        self.assertTrue(status["output_streaming"])
        self.assertFalse(status["turn_complete"])
        turn = status["turn_id"]
        self.streams.playback(self.manager.session_id, 0, turn, "started")
        with self.assertRaises(VoiceAPIError) as error:
            self.streams.playback(self.manager.session_id, 0, turn, "ended")
        self.assertEqual(error.exception.code, "playback_not_complete")
        before = self.streams._states[self.manager.session_id].vad.model.calls
        self.feed(SILENCE, 80)
        self.assertEqual(self.streams._states[self.manager.session_id].vad.model.calls, before + 80)
        self.assertEqual(self.status()["next_sequence"], self.sequence)
        self.manager.final_release.set()
        later = self.until_event("complete", early[-1]["cursor"])
        self.assertEqual(len([event for event in early + later if event["type"] == "audio_chunk"]), 2)
        completed = self.status()
        self.assertTrue(completed["turn_complete"])
        self.assertEqual(completed["stage"], "playing")
        self.assertEqual(completed["response_text"], "返答です。")
        last = later[-1]["cursor"]
        self.assertEqual(self.streams.read_events(self.manager.session_id, 0, last, timeout=0), [])
        self.assertEqual(self.streams.read_events(self.manager.session_id, 0, last - 1, timeout=0)[0]["cursor"], last)
        self.streams.playback(self.manager.session_id, 0, turn, "ended")
        self.assertEqual(self.status()["stage"], "cooldown")
        self.now = 0.6
        self.assertEqual(self.status()["stage"], "listening")

    def test_playback_failure_during_generation_holds_reply_until_successful_end(self):
        self.start_turn()
        self.until_event("audio_chunk")
        turn = self.status()["turn_id"]
        self.streams.playback(self.manager.session_id, 0, turn, "failed")
        self.manager.final_release.set()
        self.until_event("complete")
        self.assertEqual((self.status()["stage"], self.status()["error"]), ("reply_ready", "playback_failed"))
        self.now = 100
        self.feed(SILENCE, 40)
        self.assertEqual(self.status()["stage"], "reply_ready")
        self.streams.playback(self.manager.session_id, 0, turn, "started")
        self.streams.playback(self.manager.session_id, 0, turn, "ended")
        self.assertEqual(self.status()["stage"], "cooldown")

    def test_bounded_log_rejects_expired_and_future_cursors_without_silent_loss(self):
        self.status()
        state = self.streams._states[self.manager.session_id]
        state.turn_id = str(uuid.uuid4())
        event = {"type": "text_delta", "text": "x", "session_id": state.session_id,
                 "generation": 0, "turn_id": state.turn_id}
        with self.streams._lock:
            for _ in range(MAX_STREAM_EVENTS + 5):
                self.streams._append_event_locked(state, event)
        self.assertEqual(len(state.events), MAX_STREAM_EVENTS)
        self.assertLessEqual(state.event_bytes, MAX_STREAM_EVENT_BYTES)
        with self.assertRaises(VoiceAPIError) as expired:
            self.streams.read_events(state.session_id, 0, 0, timeout=0)
        self.assertEqual(expired.exception.code, "events_expired")
        with self.assertRaises(VoiceAPIError) as future:
            self.streams.read_events(state.session_id, 0, state.event_cursor + 1, timeout=0)
        self.assertEqual(future.exception.code, "invalid_event_cursor")
        earliest = state.events[0][0]["cursor"]
        replay = self.streams.read_events(state.session_id, 0, earliest - 1, timeout=0)
        self.assertEqual(replay[0]["cursor"], earliest)
        self.assertEqual(replay[-1]["cursor"], state.event_cursor)

    def test_byte_budget_and_single_oversized_event_are_explicitly_bounded(self):
        self.status()
        state = self.streams._states[self.manager.session_id]
        state.turn_id = str(uuid.uuid4())
        event = {"type": "text_delta", "text": "x" * 240, "session_id": state.session_id,
                 "generation": 0, "turn_id": state.turn_id}
        with patch("Programs.ServerSide.STTLLMTTS.voice.pcm_stream.MAX_STREAM_EVENT_BYTES", 1024):
            with self.streams._lock:
                for _ in range(8):
                    self.streams._append_event_locked(state, event)
                self.assertLessEqual(state.event_bytes, 1024)
                self.assertGreater(state.events[0][0]["cursor"], 1)
                accepted = self.streams._append_event_locked(state, {**event, "type": "audio_chunk", "audio_base64": "x" * 2048})
            self.assertFalse(accepted)
            self.assertEqual(state.events[-1][0]["error"], "stream_event_too_large")
            self.assertEqual(state.stage, "failed")
            self.assertTrue(self.manager.stream_cancelled.is_set())

    def test_stop_wakes_long_reader_and_old_generation_cannot_replay(self):
        self.status()
        entered = threading.Event()

        def read():
            entered.set()
            return self.streams.read_events(self.manager.session_id, 0, 0, timeout=5)

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(read)
            self.assertTrue(entered.wait(timeout=1))
            self.manager.active = False
            self.streams.discard(self.manager.session_id)
            with self.assertRaises(VoiceAPIError) as stopped:
                future.result(timeout=1)
        self.assertEqual(stopped.exception.status, 404)
        self.assertEqual(self.streams._states, {})
        self.manager.active = True
        self.manager.session.generation = 1
        with self.assertRaises(VoiceAPIError) as generation:
            self.streams.read_events(self.manager.session_id, 0, 0, timeout=0)
        self.assertEqual(generation.exception.code, "generation_mismatch")


class NativeStreamOwnershipTests(unittest.TestCase):
    def exercise_reader_release(self, *, timeout):
        entered, release = threading.Event(), threading.Event()
        paths = []
        stt = WhisperSTT(WhisperConfig(device="cpu", compute_type="int8", timeout_seconds=0.01 if timeout else 5))

        def native(path):
            paths.append(Path(path))
            entered.set()
            release.wait(timeout=3)
            return Transcription("固定テスト入力", "ja", 1.0, 1.0)

        stt.transcribe_file = native
        pipeline = make_pipeline(["返答です。"])
        pipeline.stt = stt
        manager = VoiceSessions()
        streams = ServerAudioStreams(manager, vad_factory=lambda: ServerVoiceActivity(model=SpeechProbability()))
        try:
            with patch.object(manager, "_create_pipeline", AsyncMock(return_value=pipeline)):
                created = manager.create_session()
            sequence = 0
            for frame, count in ((VOICE, 20), (SILENCE, 28)):
                while count:
                    length = min(8, count)
                    streams.push_audio(created["session_id"], 0, sequence, frame * length)
                    sequence += 1
                    count -= length
            self.assertTrue(entered.wait(timeout=2))
            if timeout:
                time.sleep(0.05)
            else:
                manager.stop_session(created["session_id"])
                streams.discard(created["session_id"])
            self.assertTrue(paths[0].exists(), "Native STT still owns this file")
            release.set()
            deadline = time.monotonic() + 3
            while paths[0].exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(paths[0].exists())
        finally:
            release.set()
            streams.close()
            with manager._lock:
                for session in tuple(manager._sessions.values()):
                    manager._retire_locked(session)
            manager._loop.call_soon_threadsafe(manager._loop.stop)
            manager._loop_thread.join(timeout=3)
            self.assertFalse(manager._loop_thread.is_alive())

    def test_cancelled_stream_retains_wav_until_native_stt_finishes(self):
        self.exercise_reader_release(timeout=False)

    def test_timed_out_stream_retains_wav_until_native_stt_finishes(self):
        self.exercise_reader_release(timeout=True)


if __name__ == "__main__":
    unittest.main()
