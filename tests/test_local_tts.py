"""Local TTS checks without loading the model or contacting an external service."""

from __future__ import annotations

import concurrent.futures
import contextlib
import io
import os
import sys
import tempfile
import threading
import time
import types
import unittest
import wave
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from Programs.ServerSide.STTLLMTTS.tts.irodori import (
    IrodoriTTS, SpeechSynthesisError, TTSConfig,
)
from Programs.ServerSide.STTLLMTTS.tts.local_backend import LocalIrodoriBackend


class AudioTensor:
    def detach(self):
        return self

    def float(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return np.zeros((1, 2400), dtype=np.float32)


class LocalTTSTests(unittest.TestCase):
    def setUp(self):
        LocalIrodoriBackend._runtimes.clear()
        self.config = TTSConfig(
            backend="local", checkpoint="model.safetensors",
            model_device="cpu", codec_device="cpu",
            model_precision="fp32", codec_precision="fp32",
        )
        self.runtime = Mock()
        self.runtime.synthesize.return_value = types.SimpleNamespace(
            audio=AudioTensor(), sample_rate=48000,
        )
        self.api = types.SimpleNamespace(
            RuntimeKey=lambda **kwargs: types.SimpleNamespace(**kwargs),
            SamplingRequest=lambda **kwargs: types.SimpleNamespace(**kwargs),
            InferenceRuntime=types.SimpleNamespace(from_key=Mock(return_value=self.runtime)),
        )

    @contextlib.contextmanager
    def fake_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tokenizer").mkdir()
            (root / "tokenizer" / "tokenizer_config.json").write_text("{}")
            with patch.object(LocalIrodoriBackend, "_import_runtime", return_value=self.api), \
                 patch.object(LocalIrodoriBackend, "_checkpoint_path", return_value=root / "model.safetensors"), \
                 patch.object(LocalIrodoriBackend, "_codec_path", return_value=root / "weights.pth"):
                yield

    def test_returns_pcm_wav_bytes_and_reuses_runtime_across_conversations(self):
        with self.fake_model(), patch.object(IrodoriTTS, "_get_client", side_effect=AssertionError("Gradio must stay unused")):
            first = IrodoriTTS(self.config).synthesize("こんにちは。")
            IrodoriTTS(replace(self.config, caption="静かな声")).synthesize("おやすみ。")
        self.assertEqual((first.media_type, first.file_extension), ("audio/wav", ".wav"))
        with wave.open(io.BytesIO(first.audio_bytes)) as audio:
            self.assertEqual((audio.getframerate(), audio.getnchannels(), audio.getsampwidth()), (48000, 1, 2))
            self.assertEqual(audio.getnframes(), 2400)
        self.assertEqual(self.api.InferenceRuntime.from_key.call_count, 1)
        self.assertEqual(self.runtime.synthesize.call_count, 2)
        request = self.runtime.synthesize.call_args.args[0]
        self.assertTrue(request.no_ref)
        self.assertEqual(request.caption, "静かな声")

    def test_serializes_shared_runtime_between_instances(self):
        state_lock = threading.Lock()
        active, peak = 0, 0

        def synthesize(request):
            nonlocal active, peak
            with state_lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.03)
            with state_lock:
                active -= 1
            return types.SimpleNamespace(audio=AudioTensor(), sample_rate=48000)

        self.runtime.synthesize.side_effect = synthesize
        with self.fake_model(), concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(IrodoriTTS(self.config).synthesize, "こんにちは。") for _ in range(2)]
            for future in futures:
                future.result(timeout=3)
        self.assertEqual(peak, 1)
        self.assertEqual(self.api.InferenceRuntime.from_key.call_count, 1)

    def test_reference_snapshot_is_used_and_temporary_copy_removed_on_failure(self):
        seen_paths = []

        def fail(request):
            path = Path(request.ref_wav)
            seen_paths.append(path)
            self.assertEqual(path.read_bytes(), b"original reference")
            self.assertFalse(request.no_ref)
            raise RuntimeError("model failed")

        self.runtime.synthesize.side_effect = fail
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.wav"
            reference.write_bytes(b"original reference")
            client = IrodoriTTS(replace(self.config, reference_audio_path=str(reference)))
            reference.write_bytes(b"replaced reference")
            with self.fake_model(), self.assertRaises(SpeechSynthesisError) as error:
                client.synthesize("こんにちは。")
            self.assertEqual(str(error.exception.__cause__), "model failed")
        self.assertTrue(seen_paths)
        self.assertTrue(all(not path.exists() for path in seen_paths))

    def test_size_limit_rejects_generated_audio(self):
        with self.fake_model(), self.assertRaises(SpeechSynthesisError) as error:
            IrodoriTTS(replace(self.config, max_audio_bytes=100)).synthesize("こんにちは。")
        self.assertIn("VOICE_TTS_MAX_AUDIO_BYTES", str(error.exception.__cause__))

    def test_model_load_failure_is_propagated_and_not_cached(self):
        self.api.InferenceRuntime.from_key.side_effect = RuntimeError("weights unavailable")
        with self.fake_model(), self.assertRaises(SpeechSynthesisError) as error:
            IrodoriTTS(self.config).synthesize("こんにちは。")
        self.assertEqual(str(error.exception.__cause__), "weights unavailable")
        self.assertEqual(LocalIrodoriBackend._runtimes, {})

    def test_env_selects_local_backend_and_explicit_cache(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {"VOICE_TTS_BACKEND": "local", "HF_HOME": directory,
                                    "IRODORI_SOURCE_DIR": "/existing/source",
                                    "IRODORI_CODEC_REPO": "/existing/weights.pth",
                                    "IRODORI_NUM_STEPS": "4"}, clear=True), \
             patch("Programs.ServerSide.STTLLMTTS.tts.irodori.get_saved_settings", return_value={}), \
             patch("Programs.ServerSide.STTLLMTTS.tts.irodori.reference_voice_path", return_value=Path(directory) / "absent.wav"):
            config = TTSConfig.from_env()
        self.assertEqual(config.backend, "local")
        self.assertEqual(config.checkpoint, "Aratako/Irodori-TTS-v4.1-Small")
        self.assertEqual(config.cache_dir, str(Path(directory) / "hub"))
        self.assertEqual(config.codec_repo, "/existing/weights.pth")
        self.assertEqual(config.num_steps, 4)
        self.assertTrue(config.local_files_only)

    def test_default_backend_remains_gradio(self):
        self.assertEqual(TTSConfig().backend, "gradio")
        with self.assertRaisesRegex(ValueError, "VOICE_TTS_BACKEND"):
            IrodoriTTS(replace(self.config, backend="unsupported"))

    def test_hub_resolution_uses_explicit_cache_and_stays_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = root / "snapshot" / "model.safetensors"
            checkpoint.parent.mkdir()
            checkpoint.touch()
            hub = types.ModuleType("huggingface_hub")
            hub.snapshot_download = Mock(return_value=str(checkpoint.parent))
            hub.hf_hub_download = Mock(return_value=str(root / "weights.pth"))
            config = replace(self.config, checkpoint="Aratako/Irodori-TTS-v4.1-Small",
                             cache_dir=str(root / "explicit-cache"))
            with patch.dict(sys.modules, {"huggingface_hub": hub}):
                backend = LocalIrodoriBackend(config)
                self.assertEqual(backend._checkpoint_path(), checkpoint)
                self.assertEqual(backend._codec_path(), root / "weights.pth")
            for call in (hub.snapshot_download.call_args, hub.hf_hub_download.call_args):
                self.assertEqual(call.kwargs["cache_dir"], str(root / "explicit-cache"))
                self.assertTrue(call.kwargs["local_files_only"])


if __name__ == "__main__":
    unittest.main()
