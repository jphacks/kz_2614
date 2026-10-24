"""Turn progress is visible before reply completion and fenced after stop/expiry."""

from __future__ import annotations

import asyncio
import copy
import concurrent.futures
import io
import tempfile
import threading
import time
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from Programs.ServerSide.dashboard.server import DashboardHandler
from Programs.ServerSide.dashboard.settings_store import DEFAULT_SETTINGS
from Programs.ServerSide.STTLLMTTS.stt.whisper import (
    AudioDurationExceeded, CudaUnavailableError, Transcription, WhisperConfig, WhisperSTT,
)
from Programs.ServerSide.STTLLMTTS.tts.irodori import SynthesizedSpeech
from Programs.ServerSide.STTLLMTTS.voice.pipeline import VoicePipeline
from Programs.ServerSide.STTLLMTTS.voice.sessions import (
    SESSION_TTL_SECONDS, VoiceAPIError, VoiceSessions,
)


class VoiceProgressTests(unittest.TestCase):
    transcript = "話し終わりの入力です。"

    def setUp(self):
        self.entered = {stage: threading.Event() for stage in ("stt", "llm", "tts")}
        self.released = {stage: threading.Event() for stage in ("stt", "llm", "tts")}

        async def wait_stage(stage):
            self.entered[stage].set()
            while not self.released[stage].is_set():
                await asyncio.sleep(0.005)

        async def stt_operation(path):
            await wait_stage("stt")
            return Transcription(self.transcript, "ja", 1.0, 2.0)

        async def llm_operation(*args, **kwargs):
            await wait_stage("llm")
            return types.SimpleNamespace(text="聞いているよ。")

        async def tts_operation(text):
            await wait_stage("tts")
            return SynthesizedSpeech(b"reply audio", "audio/wav", ".wav")

        self.stt = types.SimpleNamespace(transcribe_file_async=stt_operation)
        self.llm = types.SimpleNamespace(
            config=types.SimpleNamespace(max_history_messages=8),
            generate_response=llm_operation,
            close=AsyncMock(),
        )
        self.tts = types.SimpleNamespace(synthesize_async=tts_operation)
        with patch(
            "Programs.ServerSide.STTLLMTTS.voice.pipeline.get_dashboard_settings",
            return_value=copy.deepcopy(DEFAULT_SETTINGS),
        ):
            self.pipeline = VoicePipeline(
                stt=self.stt, llm=self.llm, tts=self.tts, acknowledgements=Mock(),
            )
        self.manager = VoiceSessions()
        with patch.object(self.manager, "_create_pipeline", AsyncMock(return_value=self.pipeline)):
            created = self.manager.create_session()
        self.session_id = created["session_id"]
        self.turn_id = str(uuid.uuid4())
        self.session, self.generation = self.manager.begin_turn(self.session_id, self.turn_id)

    def tearDown(self):
        for event in self.released.values():
            event.set()
        with self.manager._lock:
            for session in tuple(self.manager._sessions.values()):
                self.manager._retire_locked(session)
        self.manager._loop.call_soon_threadsafe(self.manager._loop.stop)
        self.manager._loop_thread.join(timeout=3)
        self.assertFalse(self.manager._loop_thread.is_alive())

    def begin_processing(self):
        return self.manager._submit(self.manager._process_turn(
            self.session, self.generation, self.turn_id, Path("test-recording.wav"),
        ))

    def status(self):
        return self.manager.get_turn_status(self.session_id, self.turn_id)

    def wait_entered(self, stage):
        self.assertTrue(self.entered[stage].wait(timeout=3), stage)

    def release_all(self):
        for event in self.released.values():
            event.set()

    def assert_missing(self, turn_id=None):
        with self.assertRaises(VoiceAPIError) as error:
            self.manager.get_turn_status(self.session_id, turn_id or self.turn_id)
        self.assertEqual(error.exception.status, 404)

    def test_stt_text_is_visible_while_llm_and_tts_are_still_pending(self):
        future = self.begin_processing()
        self.wait_entered("stt")
        self.assertEqual((self.status()["stage"], self.status()["transcript"]), ("stt", ""))
        self.released["stt"].set()
        self.wait_entered("llm")
        self.assertEqual((self.status()["stage"], self.status()["transcript"]), ("llm", self.transcript))
        self.assertFalse(future.done())
        self.assertFalse(self.entered["tts"].is_set())
        self.released["llm"].set()
        self.wait_entered("tts")
        self.assertEqual((self.status()["stage"], self.status()["transcript"]), ("tts", self.transcript))
        self.assertFalse(future.done())
        self.released["tts"].set()
        result = future.result(timeout=3)
        self.assertEqual(self.status(), {
            "session_id": self.session_id, "turn_id": self.turn_id,
            "generation": self.generation, "stage": "complete", "transcript": self.transcript,
        })
        self.assertEqual(result["transcript"], self.transcript)
        self.assertEqual(result["response_text"], "聞いているよ。")

    def test_only_current_turn_is_kept_and_old_callbacks_are_ignored(self):
        self.release_all()
        self.begin_processing().result(timeout=3)
        previous_turn = self.turn_id
        current_turn = str(uuid.uuid4())
        self.manager.begin_turn(self.session_id, current_turn)
        self.assert_missing(previous_turn)
        self.manager._update_turn_progress(
            self.session, self.generation, previous_turn, "tts", "late previous text",
        )
        self.manager._update_turn_progress(
            self.session, self.generation + 1, current_turn, "tts", "wrong generation",
        )
        current = self.manager.get_turn_status(self.session_id, current_turn)
        self.assertEqual((current["stage"], current["transcript"]), ("stt", ""))

    def test_stop_clears_progress_and_rejects_late_updates(self):
        future = self.begin_processing()
        self.wait_entered("stt")
        self.released["stt"].set()
        self.wait_entered("llm")
        self.manager.stop_session(self.session_id)
        self.manager._update_turn_progress(
            self.session, self.generation, self.turn_id, "tts", "late result",
        )
        self.assert_missing()
        self.assertIsNone(self.session.progress)
        self.release_all()
        with self.assertRaises(BaseException):
            future.result(timeout=3)

    def test_expired_progress_is_not_revived_and_polling_does_not_extend_expiry(self):
        original_activity = self.session.last_activity
        original_expiry = self.session.progress.expires_at
        self.status()
        self.assertEqual(self.session.last_activity, original_activity)
        self.assertEqual(self.session.progress.expires_at, original_expiry)
        self.session.progress.expires_at = time.monotonic() - 1
        self.manager._update_turn_progress(
            self.session, self.generation, self.turn_id, "llm", "too late",
        )
        self.assert_missing()
        self.assertIsNone(self.session.progress)

    def test_idle_session_expiry_removes_completed_transcript(self):
        self.release_all()
        self.begin_processing().result(timeout=3)
        self.session.last_activity = time.monotonic() - SESSION_TTL_SECONDS - 1
        self.assert_missing()
        self.assertIsNone(self.session.progress)

    def test_failure_retains_transcript_but_does_not_log_it(self):
        self.release_all()
        self.tts.synthesize_async = AsyncMock(side_effect=RuntimeError("synthesis unavailable"))
        with self.assertLogs("Programs.ServerSide.STTLLMTTS.voice.pipeline", level="INFO") as logs:
            with self.assertRaises(VoiceAPIError) as error:
                self.manager.process_turn(self.session, self.generation, self.turn_id, Path("recording.wav"))
        self.assertEqual(error.exception.code, "voice_processing_failed")
        self.assertEqual((self.status()["stage"], self.status()["transcript"]), ("failed", self.transcript))
        self.assertNotIn(self.transcript, "\n".join(logs.output))
        self.manager._update_turn_progress(self.session, self.generation, self.turn_id, "complete", "late")
        self.assertEqual(self.status()["stage"], "failed")


class ProgressHTTPHandlerTests(unittest.TestCase):
    def setUp(self):
        self.session_id, self.turn_id = str(uuid.uuid4()), str(uuid.uuid4())
        self.handler = object.__new__(DashboardHandler)
        self.handler.path = f"/api/v1/voice/sessions/{self.session_id}/turns/{self.turn_id}/status"
        self.handler.headers = {}
        self.handler._send_json = Mock()

    def test_progress_route_returns_status_and_404_for_expired_turns(self):
        expected = {"stage": "llm", "transcript": "recognized"}
        with patch("Programs.ServerSide.dashboard.server._voice_sessions") as manager:
            manager.return_value.get_turn_status.return_value = expected
            self.handler.do_GET()
            manager.return_value.get_turn_status.assert_called_once_with(self.session_id, self.turn_id)
        self.handler._send_json.assert_called_with(200, expected)
        with patch("Programs.ServerSide.dashboard.server._voice_sessions") as manager:
            manager.return_value.get_turn_status.side_effect = VoiceAPIError(404, "turn_not_found", "expired")
            self.handler.do_GET()
        self.assertEqual(self.handler._send_json.call_args.args[0], 404)


class LiveTranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.stt = WhisperSTT(WhisperConfig(device="cpu", compute_type="int8", timeout_seconds=5))
        self.pipeline = types.SimpleNamespace(stt=self.stt, close=AsyncMock())
        self.manager = VoiceSessions()
        with patch.object(self.manager, "_create_pipeline", AsyncMock(return_value=self.pipeline)):
            created = self.manager.create_session()
        self.session_id, self.generation = created["session_id"], created["generation"]
        self.session = self.manager._sessions[self.session_id]
        self.entered, self.release = threading.Event(), threading.Event()

    def tearDown(self):
        self.release.set()
        deadline = time.monotonic() + 3
        while self.manager._preview_inflight and time.monotonic() < deadline:
            time.sleep(0.01)
        with self.manager._lock:
            for session in tuple(self.manager._sessions.values()):
                self.manager._retire_locked(session)
        self.manager._loop.call_soon_threadsafe(self.manager._loop.stop)
        self.manager._loop_thread.join(timeout=3)
        self.directory.cleanup()

    def recording(self):
        path = Path(self.directory.name) / f"{uuid.uuid4()}.webm"
        path.write_bytes(b"temporary recording")
        return path

    def blocked_transcription(self, path):
        self.entered.set()
        self.release.wait(timeout=3)
        return Transcription("録音途中の言葉", "ja", 1.0, 2.0)

    def test_preview_only_transcribes_and_does_not_create_a_final_turn(self):
        self.stt.transcribe_file = Mock(return_value=Transcription("録音途中の言葉", "ja", 1.0, 2.0))
        path = self.recording()
        result = self.manager.transcribe_preview(self.session_id, self.generation, path)
        self.assertEqual(result, {
            "session_id": self.session_id, "generation": self.generation,
            "transcript": "録音途中の言葉",
        })
        self.assertFalse(path.exists())
        self.assertEqual(self.session.turn_ids, set())
        self.assertIsNone(self.session.progress)
        self.assertFalse(self.session.preview_busy)

    def test_no_speech_returns_empty_text_and_decode_errors_are_retryable(self):
        self.stt.transcribe_file = Mock(return_value=Transcription("", "ja", 0.0, 2.0))
        self.assertEqual(self.manager.transcribe_preview(
            self.session_id, self.generation, self.recording(),
        )["transcript"], "")
        for failure, status, code in (
            (ValueError("incomplete webm"), 400, "invalid_audio"),
            (AudioDurationExceeded("too long"), 413, "audio_too_long"),
            (CudaUnavailableError("no GPU"), 503, "cuda_unavailable"),
            (RuntimeError("model unavailable"), 503, "stt_unavailable"),
        ):
            with self.subTest(code=code):
                self.stt.transcribe_file = Mock(side_effect=failure)
                path = self.recording()
                with self.assertRaises(VoiceAPIError) as error:
                    self.manager.transcribe_preview(self.session_id, self.generation, path)
                self.assertEqual((error.exception.status, error.exception.code), (status, code))
                self.assertFalse(path.exists())
                self.assertFalse(self.session.preview_busy)

    def test_native_worker_keeps_gate_and_file_after_both_timeouts(self):
        self.stt.config = WhisperConfig(device="cpu", compute_type="int8", timeout_seconds=0.01)
        self.stt.transcribe_file = self.blocked_transcription
        first_path = self.recording()
        with patch("Programs.ServerSide.STTLLMTTS.voice.sessions.PREVIEW_TIMEOUT_SECONDS", 0.05):
            with self.assertRaises(VoiceAPIError) as error:
                self.manager.transcribe_preview(self.session_id, self.generation, first_path)
        self.assertEqual(error.exception.status, 504)
        self.assertTrue(self.entered.is_set())
        self.assertTrue(first_path.exists())
        self.assertTrue(self.session.preview_busy)
        rejected_path = self.recording()
        with self.assertRaises(VoiceAPIError) as rejected:
            self.manager.transcribe_preview(self.session_id, self.generation, rejected_path)
        self.assertEqual(rejected.exception.code, "preview_busy")
        self.assertFalse(rejected_path.exists())
        self.manager.stop_session(self.session_id)
        with patch.object(self.manager, "_create_pipeline", AsyncMock(return_value=self.pipeline)):
            replacement = self.manager.create_session()
        replacement_path = self.recording()
        with self.assertRaises(VoiceAPIError) as replacement_error:
            self.manager.transcribe_preview(
                replacement["session_id"], replacement["generation"], replacement_path,
            )
        self.assertEqual(replacement_error.exception.code, "preview_busy")
        self.assertFalse(replacement_path.exists())
        self.release.set()
        deadline = time.monotonic() + 3
        while self.manager._preview_inflight and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.manager._preview_inflight, 0)
        self.assertFalse(first_path.exists())

    def test_stop_during_native_work_discards_transcript(self):
        self.stt.transcribe_file = self.blocked_transcription
        path = self.recording()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.manager.transcribe_preview, self.session_id, self.generation, path)
            self.assertTrue(self.entered.wait(timeout=3))
            self.manager.stop_session(self.session_id)
            self.release.set()
            with self.assertRaises(VoiceAPIError) as error:
                future.result(timeout=3)
        self.assertEqual(error.exception.code, "session_stopped")
        self.assertFalse(path.exists())

    def test_final_turn_supersedes_an_already_running_preview(self):
        self.stt.transcribe_file = self.blocked_transcription
        path = self.recording()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.manager.transcribe_preview, self.session_id, self.generation, path)
            self.assertTrue(self.entered.wait(timeout=3))
            self.manager.begin_turn(self.session_id, str(uuid.uuid4()))
            self.release.set()
            with self.assertRaises(VoiceAPIError) as error:
                future.result(timeout=3)
        self.assertEqual(error.exception.code, "transcription_superseded")
        self.assertFalse(path.exists())

    def test_generation_expiry_and_final_turn_busy_reject_preview(self):
        self.stt.transcribe_file = Mock()
        with self.assertRaises(VoiceAPIError) as mismatch:
            self.manager.transcribe_preview(self.session_id, self.generation + 1, self.recording())
        self.assertEqual(mismatch.exception.code, "generation_mismatch")
        self.manager.begin_turn(self.session_id, str(uuid.uuid4()))
        with self.assertRaises(VoiceAPIError) as busy:
            self.manager.transcribe_preview(self.session_id, self.generation, self.recording())
        self.assertEqual(busy.exception.code, "session_busy")
        self.manager.abandon_turn(self.session)
        self.session.last_activity = time.monotonic() - SESSION_TTL_SECONDS - 1
        with self.assertRaises(VoiceAPIError) as expired:
            self.manager.transcribe_preview(self.session_id, self.generation, self.recording())
        self.assertEqual(expired.exception.status, 404)
        self.stt.transcribe_file.assert_not_called()


class LiveTranscriptionHTTPTests(unittest.TestCase):
    def setUp(self):
        self.session_id = str(uuid.uuid4())
        self.handler = object.__new__(DashboardHandler)
        self.handler.path = f"/api/v1/voice/sessions/{self.session_id}/transcription"
        self.handler._send_json = Mock()
        self.handler.connection = Mock()
        self.audio = b"\x1a\x45\xdf\xa3preview-prefix"
        self.handler.rfile = io.BytesIO(self.audio)
        self.handler.headers = {
            "Content-Type": "audio/webm",
            "X-Voice-Generation": "0", "Content-Length": str(len(self.audio)),
        }

    def test_raw_prefix_and_generation_are_passed_to_manager(self):
        def preview(session_id, generation, path):
            self.assertEqual(path.read_bytes(), self.audio)
            path.unlink()
            return {"session_id": session_id, "generation": generation, "transcript": "途中"}

        with patch("Programs.ServerSide.dashboard.server._voice_sessions") as manager:
            manager.return_value.transcribe_preview.side_effect = preview
            self.handler.do_POST()
        self.handler._send_json.assert_called_with(200, {
            "session_id": self.session_id, "generation": 0, "transcript": "途中",
        })

    def test_generation_size_type_and_signature_are_validated(self):
        scenarios = (
            ({"X-Voice-Generation": ""}, None, 400),
            ({"X-Voice-Generation": "-1"}, None, 400),
            ({"Content-Type": "text/plain"}, None, 415),
            ({"Content-Length": "25000001"}, None, 413),
            ({"Content-Length": "7"}, b"garbage", 400),
        )
        original = dict(self.handler.headers)
        for changes, body, status in scenarios:
            with self.subTest(changes=changes):
                self.handler.headers = {**original, **changes}
                self.handler.rfile = io.BytesIO(self.audio if body is None else body)
                with patch("Programs.ServerSide.dashboard.server._voice_sessions") as manager:
                    self.handler.do_POST()
                    manager.assert_not_called()
                self.assertEqual(self.handler._send_json.call_args.args[0], status)


if __name__ == "__main__":
    unittest.main()
