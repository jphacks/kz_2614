"""Runtime status observes inference; reading it never starts inference."""

from __future__ import annotations

import asyncio
import io
import json
import os
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx

from Programs.ServerSide.dashboard.runtime_status import (
    RuntimeObservation, component_configuration, get_runtime_status,
)
from Programs.ServerSide.STTLLMTTS.llm.lm_studio import VoiceLLMClient, VoiceLLMConfig
from Programs.ServerSide.STTLLMTTS.stt.whisper import (
    CudaUnavailableError, Transcription, WhisperConfig, WhisperSTT,
)
from Programs.ServerSide.STTLLMTTS.tts.irodori import IrodoriTTS, TTSConfig
from Programs.ServerSide.STTLLMTTS.tts.local_backend import LocalIrodoriBackend
from Programs.ServerSide.STTLLMTTS.voice.sessions import (
    SESSION_TTL_SECONDS, VoiceSession, VoiceSessions,
)


def wav_bytes() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\0\0" * 160)
    return output.getvalue()


class ObservationTests(unittest.TestCase):
    def test_configuration_does_not_claim_a_loaded_model_or_cuda(self):
        observation = RuntimeObservation(component_configuration(
            "configured-model", device="cuda", precision="float16", backend="local",
        ))
        initial = observation.snapshot()
        self.assertEqual(initial["state"], "unverified")
        self.assertIsNone(initial["model"])
        self.assertIsNone(initial["device"])
        self.assertEqual(initial["configured"]["device"], "cuda")

        observation.begin(loading=True)
        observation.loaded(model="loaded-model", device="cpu", precision="fp32")
        self.assertEqual(observation.snapshot()["state"], "loading")
        observation.finish(loading=True)
        self.assertEqual(observation.snapshot()["state"], "loaded")

        observation.begin()
        self.assertEqual(observation.snapshot()["state"], "processing")
        observation.success()
        observation.finish()
        ready = observation.snapshot()
        self.assertEqual(ready["state"], "ready")
        self.assertEqual(ready["device"], "cpu")
        self.assertIsNotNone(ready["last_success_at"])

    def test_errors_are_sanitized_and_success_recovers(self):
        observation = RuntimeObservation(component_configuration("model"))
        observation.begin()
        observation.failure(RuntimeError("secret-token /home/private/audio transcript"))
        observation.finish()
        failed = observation.snapshot()
        self.assertEqual(failed["state"], "error")
        self.assertEqual(failed["error_type"], "RuntimeError")
        self.assertNotIn("secret-token", json.dumps(failed))
        observation.success()
        self.assertEqual(observation.snapshot()["state"], "ready")
        self.assertIsNone(observation.snapshot()["error_type"])

    def test_cancelled_or_unobserved_work_does_not_become_ready(self):
        observation = RuntimeObservation(component_configuration("model"))
        observation.begin()
        observation.finish()
        self.assertEqual(observation.snapshot()["state"], "unverified")

    def test_model_paths_are_not_exposed(self):
        self.assertEqual(component_configuration(r"C:\Users\private\weights.pt")["model"], "weights.pt")
        self.assertEqual(component_configuration("/home/private/model.safetensors")["model"], "model.safetensors")
        self.assertEqual(component_configuration("Systran/faster-whisper-small")["model"], "Systran/faster-whisper-small")


class RuntimeSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.environment = patch.dict(os.environ, {
            "NENGITSUNE_SETTINGS_PATH": str(Path(self.directory.name) / "settings.json"),
            "WHISPER_DEVICE": "cpu", "WHISPER_COMPUTE_TYPE": "int8",
            "VOICE_TTS_BACKEND": "local", "VOICE_TTS_CHECKPOINT": "test/local-model",
            "LM_STUDIO_API_KEY": "never-return-this-token",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_reading_configuration_does_not_create_clients_or_load_models(self):
        with patch.object(WhisperSTT, "_load_model", side_effect=AssertionError("no load")), \
             patch.object(IrodoriTTS, "_get_client", side_effect=AssertionError("no connection")), \
             patch("httpx.AsyncClient", side_effect=AssertionError("no client")):
            status = get_runtime_status()
        self.assertEqual(status["active_sessions"], 0)
        self.assertTrue(status["observation_only"])
        self.assertEqual(status["components"]["stt"]["configured"]["device"], "cpu")
        self.assertEqual(status["components"]["tts"]["configured"]["backend"], "local")
        self.assertTrue(all(value["state"] == "unverified" for value in status["components"].values()))
        self.assertNotIn("never-return-this-token", json.dumps(status))

    def test_active_settings_are_kept_separate_from_next_session_settings(self):
        observation = RuntimeObservation(component_configuration(
            "old-stt", device="cuda", precision="float16", backend="faster_whisper",
        ))
        observation.loaded(model="old-stt", device="cuda", precision="float16")
        manager = SimpleNamespace(runtime_snapshot=lambda: {
            "active_sessions": 1, "components": {"stt": [observation.snapshot()]},
        })
        status = get_runtime_status(manager)
        stt = status["components"]["stt"]
        self.assertEqual(stt["active"][0]["model"], "old-stt")
        self.assertEqual(stt["active"][0]["device"], "cuda")
        self.assertEqual(stt["configured"]["device"], "cpu")
        self.assertTrue(stt["settings_pending"])

    def test_config_error_is_reported_without_raw_exception_message(self):
        with patch.object(WhisperConfig, "from_env", side_effect=ValueError("private path and key")):
            status = get_runtime_status()
        self.assertEqual(status["components"]["stt"]["state"], "error")
        self.assertEqual(status["components"]["stt"]["error_type"], "ValueError")
        self.assertNotIn("private path", json.dumps(status))

    def test_session_snapshot_is_pure_and_ignores_expired_sessions(self):
        manager = object.__new__(VoiceSessions)
        manager._lock = threading.RLock()
        reader = Mock(return_value=RuntimeObservation(component_configuration("model")).snapshot())
        live = VoiceSession("live", pipeline=SimpleNamespace(stt=SimpleNamespace(runtime_status=reader)))
        expired = VoiceSession("expired", pipeline=SimpleNamespace(stt=SimpleNamespace(runtime_status=Mock())))
        expired.last_activity = time.monotonic() - SESSION_TTL_SECONDS - 10
        manager._sessions = {"live": live, "expired": expired}
        manager._retire_locked = Mock(side_effect=AssertionError("must not retire"))
        self.assertEqual(manager.runtime_session_ids(), {"live"})
        status = manager.runtime_snapshot()
        self.assertEqual(status["active_sessions"], 1)
        self.assertEqual(len(status["components"]["stt"]), 1)
        self.assertEqual(set(manager._sessions), {"live", "expired"})
        manager._retire_locked.assert_not_called()
        expired.pipeline.stt.runtime_status.assert_not_called()


class ClientObservationTests(unittest.IsolatedAsyncioTestCase):
    def test_stt_silence_does_not_claim_whisper_loaded(self):
        stt = WhisperSTT(WhisperConfig(device="cpu", compute_type="int8"))
        # Even a cached model is not evidence of inference on a silent upload.
        stt._model = object()
        with patch.object(stt, "_transcribe_file_impl", return_value=Transcription("", None, None, 1.0)):
            stt.transcribe_file("unused.wav")
        self.assertEqual(stt.runtime_status()["state"], "unverified")
        self.assertIsNone(stt.runtime_status()["device"])

    def test_silence_after_prior_load_does_not_refresh_inference_timestamp(self):
        stt = WhisperSTT(WhisperConfig(device="cpu", compute_type="int8"))
        stt._model = object()
        stt._runtime_observation.loaded(model="cached", device="cpu", precision="int8")
        with patch("Programs.ServerSide.dashboard.runtime_status._now", side_effect=["earlier", "later"]):
            stt._runtime_observation.success()
            with patch.object(stt, "_transcribe_file_impl", return_value=Transcription("", None, None, 1.0)):
                stt.transcribe_file("unused.wav")
        self.assertEqual(stt.runtime_status()["last_success_at"], "earlier")

    def test_stt_reports_actual_load_and_model_failure(self):
        stt = WhisperSTT(WhisperConfig(device="cpu", compute_type="int8"))
        model = SimpleNamespace(model=SimpleNamespace(device="cpu", compute_type="int8"))
        with patch.object(stt, "_load_model_impl", return_value=model):
            stt._load_model()
        self.assertEqual(stt.runtime_status()["state"], "loaded")
        self.assertEqual(stt.runtime_status()["device"], "cpu")
        with patch.object(stt, "_load_model_impl", side_effect=CudaUnavailableError("secret details")):
            with self.assertRaises(CudaUnavailableError):
                stt._load_model()
        self.assertEqual(stt.runtime_status()["state"], "error")
        self.assertEqual(stt.runtime_status()["error_type"], "CudaUnavailableError")

    def test_stt_missing_engine_metadata_stays_unknown(self):
        stt = WhisperSTT(WhisperConfig(device="cuda", compute_type="float16"))
        with patch.object(stt, "_load_model_impl", return_value=object()):
            stt._load_model()
        status = stt.runtime_status()
        self.assertEqual(status["state"], "loaded")
        self.assertIsNone(status["device"])
        self.assertIsNone(status["precision"])

    async def test_llm_success_observes_instance_but_not_device(self):
        client = VoiceLLMClient(VoiceLLMConfig(model="requested"))
        await client.client.aclose()
        def respond(request):
            self.assertEqual(client.runtime_status()["state"], "processing")
            return httpx.Response(200, json={
                "model_instance_id": "observed-instance", "output": [{"type": "message", "content": "返事"}],
            })
        client.client = httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(respond))
        try:
            await client.generate_response("private transcript")
            status = client.runtime_status()
            self.assertEqual(status["state"], "ready")
            self.assertEqual(status["model"], "observed-instance")
            self.assertIsNone(status["device"])
            self.assertIsNone(status["precision"])
            self.assertNotIn("private transcript", json.dumps(status))
        finally:
            await client.close()

    async def test_llm_failure_is_recorded_safely(self):
        client = VoiceLLMClient(VoiceLLMConfig(tail_mcp_enabled=False))
        await client.client.aclose()
        client.client = httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(
            lambda request: httpx.Response(401, json={"error": "secret"}),
        ))
        try:
            with self.assertRaises(httpx.HTTPStatusError):
                await client.generate_response("private transcript")
            self.assertEqual(client.runtime_status()["state"], "error")
            self.assertEqual(client.runtime_status()["error_type"], "HTTPStatusError")
            self.assertNotIn("secret", json.dumps(client.runtime_status()))
        finally:
            await client.close()

    async def test_llm_stream_reports_processing_until_final_result(self):
        client = VoiceLLMClient(VoiceLLMConfig(tail_mcp_enabled=False))
        await client.client.aclose()
        content = (
            'data: {"type":"message.delta","content":"返事"}\n\n'
            'data: {"type":"chat.end","result":{"model_instance_id":"stream-model","output":[]}}\n\n'
        )
        client.client = httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=content),
        ))
        try:
            iterator = client.stream_response("private transcript")
            self.assertEqual(await anext(iterator), "返事")
            self.assertEqual(client.runtime_status()["state"], "processing")
            with self.assertRaises(StopAsyncIteration):
                await anext(iterator)
            self.assertEqual(client.runtime_status()["state"], "ready")
            self.assertEqual(client.runtime_status()["model"], "stream-model")
        finally:
            await client.close()

    def test_gradio_success_does_not_claim_remote_model_or_cuda(self):
        client = IrodoriTTS(TTSConfig(backend="gradio", model_device="cuda"))
        def predict(**kwargs):
            self.assertEqual(client.runtime_status()["state"], "processing")
            return wav_bytes()
        with patch.object(client, "_get_client", return_value=SimpleNamespace(predict=predict)):
            client.synthesize("private response")
        status = client.runtime_status()
        self.assertEqual(status["state"], "ready")
        self.assertIsNone(status["model"])
        self.assertIsNone(status["device"])
        self.assertEqual(status["configured"]["device"], "cuda")
        self.assertNotIn("private response", json.dumps(status))

    def test_local_auto_device_is_observed_after_successful_load(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = TTSConfig(
                backend="local", model_device="auto", source_dir=str(root),
                cache_dir=str(root / "cache"), local_files_only=False,
            )
            backend = LocalIrodoriBackend(config)
            observation = RuntimeObservation(component_configuration("model", device="auto"))
            backend.observation = observation
            api = SimpleNamespace(
                RuntimeKey=lambda **kwargs: kwargs,
                InferenceRuntime=SimpleNamespace(from_key=Mock(return_value=object())),
            )
            with patch.object(backend, "_device", return_value=("cpu", "fp32")), \
                 patch.object(backend, "_checkpoint_path", return_value=root / "model.safetensors"), \
                 patch.object(backend, "_codec_path", return_value=root / "codec.pth"), \
                 patch.object(backend, "_import_runtime", return_value=api):
                backend._load()
            self.assertEqual(observation.snapshot()["state"], "loaded")
            self.assertEqual(observation.snapshot()["device"], "cpu")
            self.assertEqual(observation.snapshot()["precision"], "fp32")


if __name__ == "__main__":
    unittest.main()
