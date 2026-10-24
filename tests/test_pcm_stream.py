"""Streaming VAD state, bounded PCM ingestion and reply/playback fences."""

from __future__ import annotations

import struct
import base64
import io
import threading
import time
import unittest
import uuid
import wave
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from server.ai.voice.pcm_stream import ServerAudioStreams
from server.ai.voice.server_vad import (
    FRAME_BYTES, FRAME_SAMPLES, SAMPLE_RATE, ServerVADSettings,
    ServerVoiceActivity, SileroStreamingVAD,
)
from server.ai.voice.sessions import VoiceAPIError, VoiceSession


SILENCE = bytes(FRAME_BYTES)
VOICE = struct.pack("<h", 4000) * FRAME_SAMPLES


class SpeechProbability:
    """Deterministic probabilities isolate segmentation from the neural classifier."""

    def __init__(self):
        self.calls = 0
        self.resets = 0

    def reset(self):
        self.resets += 1

    def probability(self, pcm):
        self.calls += 1
        return 0.9 if struct.unpack_from("<h", pcm)[0] else 0.01


class ServerVADTests(unittest.TestCase):
    def make_vad(self):
        return ServerVoiceActivity(ServerVADSettings(), SpeechProbability())

    def test_real_silero_state_matches_batched_onnx_and_resets_independently(self):
        from faster_whisper.vad import get_vad_model

        model = get_vad_model()
        pcm = np.random.default_rng(42).integers(-300, 301, size=FRAME_SAMPLES * 32, dtype=np.int16)
        samples = pcm.astype(np.float32) / 32768.0
        expected = np.asarray(model(np.pad(samples, (0, FRAME_SAMPLES))))[:32].reshape(-1)
        first, other = SileroStreamingVAD(), SileroStreamingVAD()
        actual = [first.probability(frame.tobytes()) for frame in pcm.reshape(-1, FRAME_SAMPLES)]
        np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-7)
        self.assertAlmostEqual(other.probability(pcm[:FRAME_SAMPLES].tobytes()), actual[0], places=7)
        first.reset()
        self.assertAlmostEqual(first.probability(pcm[:FRAME_SAMPLES].tobytes()), actual[0], places=7)

    def test_idle_memory_is_bounded_and_short_noise_never_starts_speech(self):
        vad = self.make_vad()
        for _ in range(200):
            self.assertIsNone(vad.feed(SILENCE * 8).completed)
        for _ in range(30):
            self.assertFalse(vad.feed(VOICE * 5).started)
            vad.feed(SILENCE * 5)
        self.assertFalse(vad.speaking)
        self.assertLessEqual(vad.buffered_bytes, FRAME_BYTES * 20)

    def test_server_confirms_speech_preserves_preroll_and_finishes_on_silence(self):
        vad = self.make_vad()
        vad.feed(SILENCE * 20)
        self.assertFalse(vad.feed(VOICE * 11).started)
        self.assertTrue(vad.feed(VOICE).started)
        self.assertEqual(vad.audio_bytes[:FRAME_BYTES * 8], SILENCE * 8)
        self.assertEqual(vad.audio_bytes[FRAME_BYTES * 8:], VOICE * 12)
        self.assertIsNone(vad.feed(SILENCE * 27).completed)
        completed = vad.feed(SILENCE).completed
        self.assertIsNotNone(completed)
        self.assertIn(VOICE * 12, completed)
        self.assertFalse(vad.speaking)
        self.assertEqual(vad.buffered_bytes, 0)

    def test_subframe_packets_and_maximum_duration_do_not_lose_or_grow_audio(self):
        vad = self.make_vad()
        self.assertFalse(vad.feed(VOICE[:100]).started)
        vad.feed(VOICE[100:])
        self.assertEqual(vad.model.calls, 1)
        completed = None
        for _ in range(700):
            update = vad.feed(VOICE)
            if update.completed is not None:
                completed = update.completed
                break
        self.assertIsNotNone(completed)
        self.assertLessEqual(len(completed), int(20.64 * SAMPLE_RATE * 2))


class FakeManager:
    def __init__(self):
        self.session_id = str(uuid.uuid4())
        self.session = VoiceSession(self.session_id)
        self.active = True
        self.expired = False
        self.preview_release, self.final_release = threading.Event(), threading.Event()
        self.preview_started, self.final_started = threading.Event(), threading.Event()
        self.previews, self.finals = [], []
        self.paths = []
        self.progress = {"stage": "stt", "transcript": ""}
        self.stream_cancelled = threading.Event()
        self.emit_acknowledgement = False

    def stream_turn(self, session, generation, turn_id, path, events):
        future = Future()
        self.stream_cancelled.clear()

        def run():
            base = {"session_id": session.session_id, "generation": generation, "turn_id": turn_id}
            try:
                self.paths.append(path)
                with wave.open(str(path)) as audio:
                    self.finals.append(audio.readframes(audio.getnframes()))
                self.final_started.set()
                audio = io.BytesIO()
                with wave.open(audio, "wb") as writer:
                    writer.setnchannels(1)
                    writer.setsampwidth(2)
                    writer.setframerate(48_000)
                    writer.writeframes(bytes(960))
                encoded = base64.b64encode(audio.getvalue()).decode("ascii")
                events.put({**base, "type": "transcript", "text": "確定した文字"})
                events.put({**base, "type": "text_delta", "text": "返答"})
                if self.emit_acknowledgement:
                    events.put({**base, "type": "acknowledgement", "text": "うん", "audio_base64": encoded, "media_type": "audio/wav"})
                self.progress = {"stage": "tts", "transcript": "確定した文字"}
                events.put({**base, "type": "audio_chunk", "index": 0, "text": "返答", "audio_base64": encoded, "media_type": "audio/wav"})
                deadline = time.monotonic() + 3
                while not self.final_release.wait(timeout=0.005):
                    if self.stream_cancelled.is_set() or time.monotonic() > deadline:
                        raise VoiceAPIError(409, "session_stopped", "stopped")
                self.get_active_session(session.session_id, generation)
                events.put({**base, "type": "text_delta", "text": "です。"})
                events.put({**base, "type": "audio_chunk", "index": 1, "text": "です。", "audio_base64": encoded, "media_type": "audio/wav"})
                events.put({**base, "type": "complete", "transcript": "確定した文字", "response_text": "返答です。",
                            "speech": {"url": f"/turns/{turn_id}/audio", "media_type": "audio/wav"}})
                future.set_result(None)
            except Exception as error:
                events.put({**base, "type": "error", "error": getattr(error, "code", "voice_processing_failed"), "message": str(error)})
                future.set_exception(error)
            finally:
                session.busy = False
                events.put(None)

        threading.Thread(target=run, daemon=True).start()
        return future

    def cancel_stream_turn(self, session_id, generation):
        self.stream_cancelled.set()

    def timeout_stream_turn(self, session, **kwargs):
        self.active = False
        self.stream_cancelled.set()

    def get_active_session(self, session_id, generation, *, touch=False):
        if not self.active or self.expired or session_id != self.session_id:
            raise VoiceAPIError(404, "session_not_found", "stopped or expired")
        if generation != self.session.generation:
            raise VoiceAPIError(409, "generation_mismatch", "wrong generation")
        return self.session

    def transcribe_preview(self, session_id, generation, path):
        self.paths.append(path)
        try:
            with wave.open(str(path)) as audio:
                self.previews.append(audio.readframes(audio.getnframes()))
                assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (16000, 1, 2)
            self.preview_started.set()
            if not self.preview_release.wait(timeout=3):
                raise TimeoutError("test did not release preview")
            return {"session_id": session_id, "generation": generation, "transcript": "途中の文字"}
        finally:
            path.unlink(missing_ok=True)

    def begin_turn(self, session_id, turn_id):
        self.get_active_session(session_id, self.session.generation)
        if self.session.busy:
            raise VoiceAPIError(409, "session_busy", "busy")
        self.session.busy = True
        self.session.active_turn_id = turn_id
        self.session.turn_ids.add(turn_id)
        return self.session, self.session.generation

    def interrupt_turn(self, session_id, generation, turn_id):
        self.get_active_session(session_id, generation)
        if self.session.active_turn_id != turn_id:
            return False
        self.stream_cancelled.set()
        self.session.active_turn_id = None
        self.session.busy = False
        return True

    def process_turn(self, session, generation, turn_id, path):
        self.paths.append(path)
        try:
            with wave.open(str(path)) as audio:
                self.finals.append(audio.readframes(audio.getnframes()))
            self.final_started.set()
            if not self.final_release.wait(timeout=3):
                raise TimeoutError("test did not release final turn")
            self.get_active_session(session.session_id, generation)
            return {
                "session_id": session.session_id, "generation": generation, "turn_id": turn_id,
                "transcript": "確定した文字", "response_text": "返答です。",
                "speech": {"url": f"/turns/{turn_id}/audio", "media_type": "audio/wav"},
            }
        finally:
            session.busy = False

    def get_turn_status(self, session_id, turn_id):
        return {**self.progress, "session_id": session_id, "turn_id": turn_id, "generation": 0}

    def abandon_turn(self, session):
        session.busy = False


class PCMStreamTests(unittest.TestCase):
    def setUp(self):
        self.manager = FakeManager()
        self.now = 0.0
        self.models = []

        def vad():
            model = SpeechProbability()
            self.models.append(model)
            return ServerVoiceActivity(ServerVADSettings(), model)

        self.streams = ServerAudioStreams(self.manager, vad_factory=vad, clock=lambda: self.now)
        self.sequence = 0

    def tearDown(self):
        self.manager.preview_release.set()
        self.manager.final_release.set()
        self.streams.close()
        self.assertTrue(all(not path.exists() for path in self.manager.paths))

    def push(self, pcm, *, input_gain=1.0):
        response = self.streams.push_audio(self.manager.session_id, 0, self.sequence, pcm, input_gain=input_gain)
        self.sequence += 1
        return response

    def feed(self, frame, count, *, input_gain=1.0):
        response = None
        while count:
            number = min(8, count)
            response = self.push(frame * number, input_gain=input_gain)
            count -= number
        return response

    def status(self):
        return self.streams.get_status(self.manager.session_id, 0)

    def wait_stage(self, stage):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            snapshot = self.status()
            if snapshot["stage"] == stage:
                return snapshot
            time.sleep(0.005)
        self.fail(f"Expected {stage}, received {snapshot}")

    def finish_utterance(self):
        self.feed(VOICE, 40)
        response = self.feed(SILENCE, 28)
        self.assertTrue(self.manager.final_started.wait(timeout=3))
        return response

    def reply_ready(self):
        self.finish_utterance()
        self.manager.final_release.set()
        return self.wait_stage("reply_ready")

    def test_sequence_duplicate_gap_and_pcm_validation_do_not_append_audio(self):
        self.push(SILENCE * 8)
        calls = self.models[0].calls
        duplicate = self.streams.push_audio(self.manager.session_id, 0, 0, VOICE * 8)
        self.assertEqual((duplicate["sequence"], duplicate["next_sequence"]), (0, 1))
        self.assertEqual(self.models[0].calls, calls)
        with self.assertRaises(VoiceAPIError) as gap:
            self.streams.push_audio(self.manager.session_id, 0, 2, VOICE)
        self.assertEqual((gap.exception.status, gap.exception.expected_sequence), (409, 1))
        for data, code in ((b"x", 400), (bytes(32002), 413)):
            with self.assertRaises(VoiceAPIError) as error:
                self.streams.push_audio(self.manager.session_id, 0, 1, data)
            self.assertEqual(error.exception.status, code)

    def test_input_gain_clips_pcm16_without_mutating_source_and_preserves_silence(self):
        self.status()
        vad = self.streams._states[self.manager.session_id].vad
        pattern = np.array([-32768, -20000, -1000, -1, 0, 1, 1000, 20000, 32767], dtype="<i2")
        source = bytearray(np.resize(pattern, FRAME_SAMPLES).tobytes())
        original = bytes(source)
        with patch.object(vad, "feed", wraps=vad.feed) as feed:
            self.push(source, input_gain=2.0)
            actual = np.frombuffer(feed.call_args.args[0], dtype="<i2")[:len(pattern)].tolist()
            self.assertEqual(actual, [-32768, -32768, -2000, -2, 0, 2, 2000, 32767, 32767])
            self.assertEqual(bytes(source), original)
            self.push(SILENCE, input_gain=0.25)
            self.assertEqual(feed.call_args.args[0], SILENCE)
            self.push(original)
            self.assertEqual(feed.call_args.args[0], original)

    def test_duplicate_gain_is_not_reapplied_and_reply_pcm_still_reaches_vad(self):
        with patch.object(self.streams, "_apply_input_gain", wraps=self.streams._apply_input_gain) as apply:
            self.push(VOICE, input_gain=0.5)
            duplicate = self.streams.push_audio(self.manager.session_id, 0, 0, VOICE, input_gain=2.0)
            self.assertEqual(duplicate["next_sequence"], 1)
            self.assertEqual(apply.call_count, 1)
            self.streams._states[self.manager.session_id].stage = "tts"
            self.push(VOICE, input_gain=2.0)
            self.assertEqual(apply.call_count, 2)

    def test_invalid_input_gain_is_rejected_without_consuming_a_sequence(self):
        for gain in (None, "1.0", False, True, float("nan"), float("inf"), -float("inf"),
                     0, -1, 0.2499, 2.0001, 1 + 1j, 10 ** 1000):
            with self.subTest(gain=repr(gain)):
                with self.assertRaises(VoiceAPIError) as error:
                    self.streams.push_audio(self.manager.session_id, 0, 0, SILENCE, input_gain=gain)
                self.assertEqual((error.exception.status, error.exception.code), (400, "invalid_input_gain"))
        self.assertEqual(self.status()["next_sequence"], 0)

    def test_partially_mutated_vad_failure_never_acknowledges_or_refeeds_the_packet(self):
        self.push(SILENCE * 8)
        state = self.streams._states[self.manager.session_id]

        def fail_after_mutation(pcm):
            state.vad._audio.extend(pcm[:FRAME_BYTES])
            raise RuntimeError("simulated native VAD failure")

        with patch.object(state.vad, "feed", side_effect=fail_after_mutation) as feed:
            with self.assertRaises(VoiceAPIError) as error:
                self.push(VOICE * 8)
            self.assertEqual((error.exception.code, error.exception.retryable), ("vad_unavailable", False))
            partial = state.vad.audio_bytes
            snapshot = self.status()
            self.assertEqual((snapshot["next_sequence"], snapshot["stage"]), (1, "failed"))
            self.assertTrue(snapshot["fatal"])
            for sequence in (1, 1, 2, 0):
                with self.subTest(sequence=sequence), self.assertRaises(VoiceAPIError) as retry:
                    self.streams.push_audio(self.manager.session_id, 0, sequence, VOICE * 8)
                self.assertEqual((retry.exception.status, retry.exception.code, retry.exception.retryable),
                                 (503, "vad_unavailable", False))
            self.assertEqual(feed.call_count, 1)
            self.assertEqual(state.vad.audio_bytes, partial)
            self.assertEqual(self.status()["next_sequence"], 1)
        with self.assertRaises(VoiceAPIError) as reader:
            self.streams.read_events(self.manager.session_id, 0, 0, timeout=0)
        self.assertEqual((reader.exception.code, reader.exception.retryable), ("vad_unavailable", False))

    def test_terminal_vad_failure_cannot_be_revived_by_old_reply_progress_or_events(self):
        reply = self.reply_ready()
        state = self.streams._states[self.manager.session_id]
        cursor = state.event_cursor
        with patch.object(state.vad, "feed", side_effect=RuntimeError("VAD failure")):
            with self.assertRaises(VoiceAPIError):
                self.push(SILENCE * 8)
        self.manager.progress = {"stage": "llm", "transcript": "late progress"}
        pending = Future()
        state.final_future = pending
        self.assertEqual(self.status()["stage"], "failed")
        late = {
            "session_id": self.manager.session_id, "generation": 0, "turn_id": reply["turn_id"],
            "type": "complete", "transcript": "late result", "response_text": "late reply",
        }
        pending.set_result(late)
        with self.streams._lock:
            self.assertFalse(self.streams._append_event_locked(state, late))
        snapshot = self.status()
        self.assertEqual((snapshot["stage"], snapshot["error"]), ("failed", "vad_unavailable"))
        self.assertTrue(snapshot["fatal"])
        self.assertEqual(snapshot["event_cursor"], cursor)

    def test_gain_changes_reach_preview_and_final_wav_without_stopping_the_session(self):
        self.feed(VOICE, 50, input_gain=0.5)
        self.assertTrue(self.manager.preview_started.wait(timeout=3))
        self.assertEqual(struct.unpack_from("<h", self.manager.previews[0])[0], 2000)
        self.feed(VOICE, 20, input_gain=2.0)
        self.feed(SILENCE, 28, input_gain=2.0)
        self.assertTrue(self.manager.final_started.wait(timeout=3))
        final = self.manager.finals[0]
        self.assertEqual(struct.unpack_from("<h", final)[0], 2000)
        self.assertEqual(struct.unpack_from("<h", final, FRAME_BYTES * 50)[0], 8000)
        self.assertEqual(set(np.frombuffer(final, dtype="<i2").tolist()), {0, 2000, 8000})
        self.assertEqual(self.status()["session_id"], self.manager.session_id)
        self.assertEqual(self.status()["generation"], 0)
        self.assertTrue(self.manager.active)

    def test_live_preview_is_single_inflight_and_final_result_is_authoritative(self):
        self.feed(SILENCE, 20)
        self.feed(VOICE, 50)
        self.assertTrue(self.manager.preview_started.wait(timeout=3))
        self.feed(VOICE, 50)
        self.assertEqual(len(self.manager.previews), 1)
        self.manager.preview_release.set()
        deadline = time.monotonic() + 3
        while self.status()["transcript"] != "途中の文字" and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual(self.status()["transcript"], "途中の文字")
        self.feed(SILENCE, 28)
        self.assertTrue(self.manager.final_started.wait(timeout=3))
        self.manager.progress = {"stage": "llm", "transcript": "確定した文字"}
        self.assertEqual(self.status()["stage"], "llm")
        self.manager.final_release.set()
        snapshot = self.wait_stage("reply_ready")
        self.assertEqual(snapshot["transcript"], "確定した文字")
        self.assertEqual(snapshot["response_text"], "返答です。")
        self.assertTrue(snapshot["speech"]["url"].endswith("/audio"))
        self.assertEqual(len(self.manager.finals), 1)
        self.assertEqual(self.manager.finals[0][:8 * FRAME_BYTES], SILENCE * 8)

    def test_late_preview_cannot_replace_stt_or_final_text(self):
        self.finish_utterance()
        self.manager.progress = {"stage": "tts", "transcript": "確定文字"}
        self.assertEqual(self.status()["stage"], "tts")
        self.manager.preview_release.set()
        self.manager.final_release.set()
        self.assertEqual(self.wait_stage("reply_ready")["transcript"], "確定した文字")

    def test_processing_and_playback_continue_vad_without_interrupting_on_silence(self):
        reply = self.reply_ready()
        before = self.models[0].calls
        self.feed(SILENCE, 100)
        self.assertEqual(self.models[0].calls, before + 100)
        turn = reply["turn_id"]
        self.assertEqual(self.streams.playback(self.manager.session_id, 0, turn, "started")["stage"], "playing")
        self.feed(SILENCE, 32)
        self.assertEqual(self.models[0].calls, before + 132)
        self.streams.playback(self.manager.session_id, 0, turn, "ended")
        self.now = 0.59
        self.assertEqual(self.feed(VOICE, 8)["stage"], "cooldown")
        self.now = 0.6
        self.assertEqual(self.status()["stage"], "listening")
        self.feed(VOICE, 16)
        self.assertEqual(self.status()["stage"], "speaking")

    def test_playback_failure_and_wrong_turn_do_not_restart_listening(self):
        reply = self.reply_ready()
        with self.assertRaises(VoiceAPIError) as error:
            self.streams.playback(self.manager.session_id, 0, "old-turn", "ended")
        self.assertEqual(error.exception.code, "playback_turn_mismatch")
        failed = self.streams.playback(self.manager.session_id, 0, reply["turn_id"], "failed")
        self.assertEqual((failed["stage"], failed["error"]), ("reply_ready", "playback_failed"))
        self.feed(SILENCE, 100)
        self.assertEqual(self.status()["stage"], "reply_ready")
        self.assertEqual(len(self.manager.finals), 1)

    def test_stop_expiry_and_generation_fence_clear_memory_and_suppress_late_results(self):
        self.finish_utterance()
        self.manager.active = False
        self.manager.session.active = False
        self.manager.session.generation += 1
        self.streams.discard(self.manager.session_id)
        self.manager.final_release.set()
        with self.assertRaises(VoiceAPIError) as stopped:
            self.status()
        self.assertEqual(stopped.exception.status, 404)
        self.assertEqual(self.streams._states, {})
        self.manager.active = True
        self.manager.session.generation = 0
        with self.assertRaises(VoiceAPIError) as wrong_generation:
            self.streams.get_status(self.manager.session_id, 1)
        self.assertEqual(wrong_generation.exception.code, "generation_mismatch")
        self.status()
        self.manager.expired = True
        with self.assertRaises(VoiceAPIError):
            self.status()
        self.assertEqual(self.streams._states, {})

    def test_final_worker_capacity_has_no_unbounded_pending_queue(self):
        for _ in range(4):
            self.assertTrue(self.streams._final_capacity.acquire(blocking=False))
        try:
            self.feed(VOICE, 20)
            result = self.feed(SILENCE, 28)
            self.assertEqual((result["stage"], result["error"]), ("failed", "server_busy"))
            self.assertEqual(self.manager.finals, [])
        finally:
            for _ in range(4):
                self.streams._final_capacity.release()

    def test_rejected_begin_turn_remains_failed_in_status_and_later_audio_acks(self):
        self.manager.begin_turn = Mock(side_effect=VoiceAPIError(
            409, "session_turn_limit", "この会話の発話数上限に達しました。",
        ))
        self.feed(VOICE, 20)
        with self.assertRaises(VoiceAPIError) as error:
            self.feed(SILENCE, 28)
        self.assertEqual((error.exception.status, error.exception.code), (409, "session_turn_limit"))
        status = self.status()
        self.assertEqual((status["stage"], status["error"]), ("failed", "session_turn_limit"))
        self.assertIsNone(self.streams._states[self.manager.session_id].final_future)
        self.sequence = status["next_sequence"]
        later = self.push(VOICE * 8)
        self.assertEqual((later["stage"], later["error"]), ("failed", "session_turn_limit"))
        self.assertEqual(self.manager.finals, [])
        self.manager.begin_turn.assert_called_once()
        self.assertTrue(self.streams._final_capacity.acquire(blocking=False))
        self.streams._final_capacity.release()

    def test_failed_worker_submission_reports_service_error_and_releases_the_turn(self):
        self.feed(VOICE, 20)
        with patch.object(self.streams._final_workers, "submit", side_effect=RuntimeError("executor unavailable")):
            with self.assertRaises(VoiceAPIError) as error:
                self.feed(SILENCE, 28)
        self.assertEqual((error.exception.status, error.exception.code), (503, "voice_unavailable"))
        self.assertFalse(self.manager.session.busy)
        status = self.status()
        self.assertEqual((status["stage"], status["error"]), ("failed", "voice_unavailable"))
        self.assertIsNone(self.streams._states[self.manager.session_id].final_future)
        self.sequence = status["next_sequence"]
        self.assertEqual(self.push(SILENCE * 8)["stage"], "failed")
        self.assertEqual(self.manager.finals, [])


if __name__ == "__main__":
    unittest.main()
