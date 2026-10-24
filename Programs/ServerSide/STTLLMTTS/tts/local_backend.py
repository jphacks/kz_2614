"""Lazy local Irodori inference, with shared models and in-memory WAV output."""

from __future__ import annotations

import importlib
import io
import logging
import math
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .irodori import TTSConfig


logger = logging.getLogger(__name__)


class LocalIrodoriBackend:
    # Pipelines are created per conversation; models must survive across pipelines.
    _runtimes: dict[tuple[str, ...], tuple[Any, Any, threading.RLock]] = {}
    _runtimes_guard = threading.Lock()

    def __init__(self, config: TTSConfig) -> None:
        self.config = config
        self.source_dir = Path(config.source_dir).expanduser() if config.source_dir else (
            Path(__file__).resolve().parents[1] / "third_party" / "Irodori-TTS"
        )
        self.cache_dir = Path(config.cache_dir).expanduser() if config.cache_dir else Path(
            os.getenv("HF_HUB_CACHE", str(Path(os.getenv("HF_HOME", "~/.cache/huggingface")).expanduser() / "hub"))
        )
        if config.num_steps is not None and config.num_steps < 1:
            raise ValueError("IRODORI_NUM_STEPS must be positive or empty")
        if not math.isfinite(config.max_seconds) or not 0.5 <= config.max_seconds <= 60:
            raise ValueError("IRODORI_MAX_SECONDS must be between 0.5 and 60")

    def _import_runtime(self) -> Any:
        source = self.source_dir.resolve()
        if not (source / "irodori_tts" / "inference_runtime.py").is_file():
            raise FileNotFoundError("Set IRODORI_SOURCE_DIR to the official Irodori-TTS source")
        if str(source) not in sys.path:
            sys.path.insert(0, str(source))
        return importlib.import_module("irodori_tts.inference_runtime")

    def _checkpoint_path(self) -> Path:
        checkpoint = Path(self.config.checkpoint).expanduser()
        if checkpoint.is_file():
            return checkpoint.resolve()
        if checkpoint.suffix in {".pt", ".safetensors"} or checkpoint.is_absolute():
            raise FileNotFoundError("VOICE_TTS_CHECKPOINT does not exist")
        from huggingface_hub import snapshot_download

        snapshot = Path(snapshot_download(
            repo_id=self.config.checkpoint.removeprefix("hf://"),
            allow_patterns=["model.safetensors", "tokenizer/*"],
            cache_dir=str(self.cache_dir),
            local_files_only=self.config.local_files_only,
        ))
        result = snapshot / "model.safetensors"
        if not result.is_file():
            raise FileNotFoundError("The Irodori checkpoint is not cached")
        return result

    def _codec_path(self) -> Path:
        codec = Path(self.config.codec_repo).expanduser()
        if codec.is_file():
            return codec.resolve()
        if codec.is_absolute() or codec.suffix == ".pth":
            raise FileNotFoundError("IRODORI_CODEC_REPO does not exist")
        from huggingface_hub import hf_hub_download

        return Path(hf_hub_download(
            repo_id=self.config.codec_repo.removeprefix("hf://"),
            filename="weights.pth",
            cache_dir=str(self.cache_dir),
            local_files_only=self.config.local_files_only,
        ))

    @staticmethod
    def _device(requested: str, precision: str) -> tuple[str, str]:
        if requested == "auto" or requested.startswith("cuda"):
            import torch

            available = torch.cuda.is_available()
            if requested == "auto":
                return ("cuda", precision) if available else ("cpu", "fp32")
            if not available:
                raise RuntimeError("Local Irodori requests CUDA, but PyTorch cannot use CUDA")
        if requested == "cpu" and precision != "fp32":
            raise ValueError("Local CPU inference requires fp32 precision")
        return requested, precision

    def _load(self) -> tuple[Any, Any, threading.RLock]:
        config = self.config
        key = (
            str(self.source_dir.resolve()), config.checkpoint, config.codec_repo,
            config.model_device, config.model_precision, config.codec_device,
            config.codec_precision, str(self.cache_dir.resolve()), str(config.local_files_only),
        )
        with self._runtimes_guard:
            shared = self._runtimes.get(key)
            if shared is None:
                model_device, model_precision = self._device(config.model_device, config.model_precision)
                codec_device, codec_precision = self._device(config.codec_device, config.codec_precision)
                checkpoint, codec = self._checkpoint_path(), self._codec_path()
                if config.local_files_only and not any(
                    (directory / "tokenizer" / "tokenizer_config.json").is_file()
                    for directory in (checkpoint.parent, checkpoint.parent.parent)
                ):
                    raise FileNotFoundError("Local Irodori inference requires bundled tokenizer files")
                api = self._import_runtime()
                runtime_key = api.RuntimeKey(
                    checkpoint=str(checkpoint), model_device=model_device,
                    model_precision=model_precision, codec_repo=str(codec),
                    codec_device=codec_device, codec_precision=codec_precision,
                )
                runtime = api.InferenceRuntime.from_key(runtime_key)
                shared = (api, runtime, threading.RLock())
                self._runtimes[key] = shared
            return shared

    def synthesize(
        self, text: str, *, reference_audio: bytes | None = None,
        reference_suffix: str = ".wav",
    ) -> bytes:
        reference_path: Path | None = None
        try:
            if reference_audio is None and self.config.reference_audio_path:
                original = Path(self.config.reference_audio_path).expanduser()
                if not original.is_file() or original.stat().st_size > 10 * 1024 * 1024:
                    raise ValueError("The reference voice is missing or exceeds 10 MiB")
                reference_audio = original.read_bytes()
            if reference_audio is not None:
                if not reference_audio or len(reference_audio) > 10 * 1024 * 1024:
                    raise ValueError("The reference voice is empty or exceeds 10 MiB")
                with tempfile.NamedTemporaryFile(suffix=reference_suffix, delete=False) as temporary:
                    temporary.write(reference_audio)
                    reference_path = Path(temporary.name)
            api, runtime, lock = self._load()
            with lock:
                result = None
                try:
                    request = api.SamplingRequest(
                        text=text, caption=self.config.caption or None,
                        ref_wav=str(reference_path) if reference_path else None,
                        no_ref=reference_path is None, num_steps=self.config.num_steps,
                        max_seconds=self.config.max_seconds,
                        seed=int(self.config.seed) if self.config.seed.strip() else None,
                    )
                    result = runtime.synthesize(request)
                    return self._encode_wav(result.audio, int(result.sample_rate))
                finally:
                    # SamplingResult also holds the candidate tensor list. Release it
                    # before returning unused PyTorch workspace to CTranslate2/STT.
                    result = None
                    self._release_cuda_workspace(runtime)
        finally:
            if reference_path is not None:
                reference_path.unlink(missing_ok=True)

    def _release_cuda_workspace(self, runtime: Any) -> None:
        key = getattr(runtime, "key", None)
        devices = []
        for name in ("model_device", "codec_device"):
            actual = getattr(key, name, None)
            devices.append(actual if isinstance(actual, str) else getattr(self.config, name))
        if not any(device == "auto" or device.startswith("cuda") for device in devices):
            return
        try:
            torch = importlib.import_module("torch")
            if torch.cuda.is_available():
                # Live model/codec tensors remain owned by the shared runtime.
                torch.cuda.empty_cache()
        except Exception as exc:
            # Cleanup must not replace the original inference exception or discard a
            # successfully encoded WAV if the CUDA context itself is unhealthy.
            logger.warning("voice_tts_workspace_release_failed error_type=%s", type(exc).__name__)

    def _encode_wav(self, audio: Any, sample_rate: int) -> bytes:
        import soundfile

        samples = audio.detach().float().cpu().numpy()
        if samples.ndim == 2:
            samples = samples.T
        if samples.size == 0:
            raise ValueError("Irodori returned empty audio")
        if samples.size * 2 + 44 > self.config.max_audio_bytes:
            raise ValueError("Generated audio exceeds VOICE_TTS_MAX_AUDIO_BYTES")
        output = io.BytesIO()
        soundfile.write(output, samples, sample_rate, format="WAV", subtype="PCM_16")
        return output.getvalue()
