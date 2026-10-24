"""Reference reuse checks with tiny CPU tensors and fake Irodori inference only."""

from __future__ import annotations

import asyncio
import concurrent.futures
import tempfile
import threading
import time
import types
import unittest
from dataclasses import dataclass, replace
from pathlib import Path
from unittest.mock import Mock, patch

import torch

from Programs.ServerSide.STTLLMTTS.tts.irodori import TTSConfig
from Programs.ServerSide.STTLLMTTS.tts.local_backend import LocalIrodoriBackend


@dataclass
class Request:
    text: str
    caption: str | None = None
    ref_wav: str | None = None
    ref_latent: str | None = None
    no_ref: bool = False
    num_steps: int | None = None
    max_seconds: float = 15
    max_ref_seconds: float | None = None
    seed: int | None = None
    ref_normalize_db: float | None = -16.0
    ref_ensure_max: bool = True


class ReferenceCacheTests(unittest.TestCase):
    def setUp(self):
        LocalIrodoriBackend._reference_cache.clear()
        LocalIrodoriBackend._reference_cache_bytes = 0
        self.config = TTSConfig(
            backend="local", checkpoint="model.safetensors", model_device="cpu",
            codec_device="cpu", model_precision="fp32", codec_precision="fp32",
            reference_max_seconds=5,
        )
        self.latent = torch.arange(24, dtype=torch.float32).reshape(1, 6, 4).requires_grad_()
        self.codec = types.SimpleNamespace(
            deterministic_encode=True,
            encode_waveform=Mock(side_effect=lambda *args, **kwargs: self.latent),
        )
        self.requests = []
        self.loaded = []
        self.paths = []
        self.runtime = types.SimpleNamespace(
            codec=self.codec, default_max_ref_seconds=120,
            model_cfg=types.SimpleNamespace(use_speaker_condition_resolved=True, latent_dim=4),
            key=types.SimpleNamespace(model_device="cpu", codec_device="cpu"),
            synthesize=Mock(side_effect=self.generate),
        )
        self.api = types.SimpleNamespace(
            SamplingRequest=Request,
            _load_audio=Mock(return_value=(torch.zeros(2, 200), 20)),
        )
        self.lock = threading.RLock()
        self.original_temporary = tempfile.NamedTemporaryFile
        self.patchers = [
            patch.object(LocalIrodoriBackend, "_load", return_value=(self.api, self.runtime, self.lock)),
            patch.object(LocalIrodoriBackend, "_encode_wav", return_value=b"generated WAV"),
            patch("Programs.ServerSide.STTLLMTTS.tts.local_backend.tempfile.NamedTemporaryFile", side_effect=self.temporary),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.assert_temporary_files_deleted)

    def temporary(self, *args, **kwargs):
        temporary = self.original_temporary(*args, **kwargs)
        self.paths.append(Path(temporary.name))
        return temporary

    def assert_temporary_files_deleted(self):
        self.assertTrue(all(not path.exists() for path in self.paths))

    def generate(self, request):
        self.requests.append(request)
        if getattr(request, "ref_latent", None):
            self.assertIsNone(request.ref_wav)
            self.assertFalse(request.no_ref)
            self.loaded.append(torch.load(request.ref_latent, map_location="cpu", weights_only=True))
        elif request.ref_wav:
            self.assertTrue(Path(request.ref_wav).is_file())
        else:
            self.assertTrue(request.no_ref)
        return types.SimpleNamespace(
            audio=object(), sample_rate=48000,
            stage_timings=[("prepare_reference", 0.01), ("sample_rf", 0.02), ("decode_latent", 0.03)],
        )

    def synthesize(self, reference=b"same reference", config=None):
        return LocalIrodoriBackend(config or self.config).synthesize("test reply", reference_audio=reference)

    def test_reuses_one_cpu_snapshot_across_conversations_and_preserves_preprocessing(self):
        self.synthesize()
        with torch.no_grad():
            self.latent.fill_(999)
        self.synthesize()
        self.api._load_audio.assert_called_once()
        self.codec.encode_waveform.assert_called_once()
        args, kwargs = self.codec.encode_waveform.call_args
        # Trim at the source sample rate before the codec downmixes/resamples.
        self.assertEqual(tuple(args[0].shape), (1, 2, 100))
        self.assertEqual(kwargs, {"sample_rate": 20, "normalize_db": -16.0, "ensure_max": True})
        self.assertTrue(torch.equal(self.loaded[0], self.loaded[1]))
        cached, size = next(iter(LocalIrodoriBackend._reference_cache.values()))
        self.assertEqual(cached.device.type, "cpu")
        self.assertFalse(cached.requires_grad)
        self.assertIsNone(cached.grad_fn)
        self.assertEqual(tuple(cached.shape), (1, 6, 4))
        self.assertEqual(size, cached.numel() * cached.element_size())
        self.assertNotEqual(cached.data_ptr(), self.latent.data_ptr())
        self.assert_temporary_files_deleted()

    def test_content_settings_and_runtime_changes_invalidate_cache(self):
        self.synthesize(b"voice A")
        self.synthesize(b"voice B")
        self.synthesize(b"voice B", replace(self.config, reference_max_seconds=3))
        self.synthesize(b"voice B", replace(self.config, codec_precision="bf16"))
        self.assertEqual(self.codec.encode_waveform.call_count, 4)
        self.assertEqual(self.codec.encode_waveform.call_args.args[0].shape[-1], 100)
        self.runtime.__dict__.pop("_nengitsune_reference_cache_token")
        self.synthesize(b"voice B", replace(self.config, codec_precision="bf16"))
        self.assertEqual(self.codec.encode_waveform.call_count, 5)

    def test_normalization_and_effective_model_default_are_in_cache_key(self):
        def request(**kwargs):
            kwargs["max_ref_seconds"] = None
            return Request(**kwargs, ref_normalize_db=self.normalize, ref_ensure_max=self.ensure)

        self.api.SamplingRequest = request
        self.runtime.default_max_ref_seconds = 2
        self.normalize, self.ensure = None, False
        self.synthesize()
        self.synthesize()
        self.assertEqual(self.codec.encode_waveform.call_count, 1)
        self.assertEqual(self.codec.encode_waveform.call_args.args[0].shape[-1], 40)
        self.assertEqual(self.codec.encode_waveform.call_args.kwargs["normalize_db"], None)
        self.assertFalse(self.codec.encode_waveform.call_args.kwargs["ensure_max"])
        self.normalize = -16
        self.synthesize()
        self.ensure = True
        self.synthesize()
        self.runtime.default_max_ref_seconds = 3
        self.synthesize()
        self.assertEqual(self.codec.encode_waveform.call_count, 4)

    def test_failed_encoding_is_not_cached_and_retry_reencodes(self):
        self.codec.encode_waveform.side_effect = [RuntimeError("encoder unavailable"), self.latent]
        with self.assertRaisesRegex(RuntimeError, "encoder unavailable"):
            self.synthesize()
        self.assertEqual(len(LocalIrodoriBackend._reference_cache), 0)
        self.assert_temporary_files_deleted()
        self.synthesize()
        self.assertEqual(self.codec.encode_waveform.call_count, 2)

    def test_generation_failure_and_cancellation_remove_latent_files_but_keep_valid_cache(self):
        for failure in (RuntimeError("model failed"), asyncio.CancelledError()):
            self.runtime.synthesize.side_effect = failure
            with self.assertRaises(type(failure)):
                self.synthesize()
            self.assert_temporary_files_deleted()
        self.runtime.synthesize.side_effect = self.generate
        self.synthesize()
        self.assertEqual(self.codec.encode_waveform.call_count, 1)
        self.assertTrue(any(path.suffix == ".pt" for path in self.paths))

    def test_serialization_failure_also_removes_partial_latent_file(self):
        with patch.object(torch, "save", side_effect=OSError("temporary disk unavailable")):
            with self.assertRaisesRegex(OSError, "temporary disk unavailable"):
                self.synthesize()
        self.assert_temporary_files_deleted()
        self.synthesize()
        self.assertEqual(self.codec.encode_waveform.call_count, 1)

    def test_lru_entry_and_byte_limits_and_oversized_latent_bypass(self):
        for audio in (b"A", b"B", b"A", b"C", b"B"):
            self.synthesize(audio)
        self.assertEqual(self.codec.encode_waveform.call_count, 4)
        self.assertEqual(len(LocalIrodoriBackend._reference_cache), 2)
        LocalIrodoriBackend._reference_cache.clear()
        LocalIrodoriBackend._reference_cache_bytes = 0
        with patch.object(LocalIrodoriBackend, "REFERENCE_CACHE_MAX_BYTES", 100):
            self.synthesize(b"A")
            self.synthesize(b"B")
            self.assertEqual(len(LocalIrodoriBackend._reference_cache), 1)
            self.assertLessEqual(LocalIrodoriBackend._reference_cache_bytes, 100)
        LocalIrodoriBackend._reference_cache.clear()
        LocalIrodoriBackend._reference_cache_bytes = 0
        before = self.codec.encode_waveform.call_count
        with patch.object(LocalIrodoriBackend, "REFERENCE_CACHE_MAX_BYTES", 95):
            self.synthesize()
            self.synthesize()
        self.assertEqual(self.codec.encode_waveform.call_count, before + 2)
        self.assertEqual(len(LocalIrodoriBackend._reference_cache), 0)
        self.assertEqual(LocalIrodoriBackend._reference_cache_bytes, 0)

    def test_shared_runtime_lock_prevents_duplicate_encoding_and_parallel_inference(self):
        def encode(*args, **kwargs):
            self.assertTrue(self.lock._is_owned())
            time.sleep(0.02)
            return self.latent

        def generate(request):
            self.assertTrue(self.lock._is_owned())
            return self.generate(request)

        self.codec.encode_waveform.side_effect = encode
        self.runtime.synthesize.side_effect = generate
        barrier = threading.Barrier(2)

        def call():
            barrier.wait(timeout=3)
            return self.synthesize()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(call) for _ in range(2)]
            self.assertEqual([future.result(timeout=3) for future in futures], [b"generated WAV"] * 2)
        self.codec.encode_waveform.assert_called_once()
        self.assertEqual(self.runtime.synthesize.call_count, 2)

    def test_no_reference_and_unsupported_or_stochastic_runtime_keep_original_flow(self):
        self.synthesize(None)
        self.assertTrue(self.requests[-1].no_ref)
        self.assertEqual(self.paths, [])
        self.codec.deterministic_encode = False
        self.synthesize()
        self.assertTrue(self.requests[-1].ref_wav)
        self.assertIsNone(self.requests[-1].ref_latent)
        self.codec.deterministic_encode = True
        del self.api._load_audio
        self.synthesize()
        self.assertTrue(self.requests[-1].ref_wav)
        self.api._load_audio = Mock()
        self.api.SamplingRequest = lambda **kwargs: types.SimpleNamespace(**kwargs)
        self.synthesize()
        self.assertTrue(self.requests[-1].ref_wav)
        self.codec.encode_waveform.assert_not_called()

    def test_configured_reference_is_read_without_modifying_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "reference.wav"
            source.write_bytes(b"saved user reference")
            config = replace(self.config, reference_audio_path=str(source))
            self.synthesize(None, config)
            self.synthesize(None, config)
            self.assertEqual(source.read_bytes(), b"saved user reference")
        self.codec.encode_waveform.assert_called_once()

    def test_logs_only_allowed_stage_numbers_and_cache_metadata(self):
        original = self.generate

        def generate(request):
            result = original(request)
            result.stage_timings += [
                ("tokenize_text", 1), ("private reference text", 2), ([], 0),
                ("sample_rf", float("inf")), ("decode_latent", True),
            ]
            return result

        self.runtime.synthesize.side_effect = generate
        with self.assertLogs("Programs.ServerSide.STTLLMTTS.tts.local_backend", level="INFO") as captured:
            self.synthesize(b"private reference bytes")
            self.synthesize(b"private reference bytes")
        logged = "\n".join(captured.output)
        self.assertIn("hit=False", logged)
        self.assertIn("hit=True", logged)
        for stage in ("prepare_reference", "sample_rf", "decode_latent"):
            self.assertEqual(logged.count("stage=" + stage), 2)
        for forbidden in ("test reply", "private reference", "tokenize_text", ".wav", ".pt"):
            self.assertNotIn(forbidden, logged)
        for path in self.paths:
            self.assertNotIn(str(path), logged)


if __name__ == "__main__":
    unittest.main()
