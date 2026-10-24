"""Lazy local Irodori inference, with shared models and in-memory WAV output."""

from __future__ import annotations

import hashlib
import importlib
import io
import logging
import math
import os
import sys
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...dashboard.settings_store import MAX_REFERENCE_WAV_BYTES
from .cancellation import check_synthesis_cancelled, synthesis_lock

if TYPE_CHECKING:
    from .irodori import TTSConfig


logger = logging.getLogger(__name__)


def load_audio_with_soundfile_fallback(path: str | Path, primary: Any) -> Any:
    """Handle torchaudio versions requiring an optional TorchCodec installation."""
    try:
        return primary(path)
    except ImportError:
        import soundfile
        import torch

        samples, sample_rate = soundfile.read(str(path), dtype="float32", always_2d=True)
        return torch.from_numpy(samples.T.copy()), sample_rate


class LocalIrodoriBackend:
    # Pipelines are created per conversation; models must survive across pipelines.
    _runtimes: dict[tuple[str, ...], tuple[Any, Any, threading.RLock]] = {}
    _runtime_descriptors: dict[tuple[str, ...], dict[str, str]] = {}
    _runtimes_guard = threading.Lock()
    # Reference tensors are independent snapshots on CPU, shared across conversations.
    # The token in each key identifies the loaded runtime without retaining the model.
    _reference_cache: OrderedDict[tuple[Any, ...], tuple[Any, int]] = OrderedDict()
    _reference_cache_bytes = 0
    _reference_cache_guard = threading.Lock()
    REFERENCE_CACHE_MAX_ENTRIES = 2
    REFERENCE_CACHE_MAX_BYTES = 16 * 1024 * 1024

    def __init__(self, config: TTSConfig) -> None:
        self.config = config
        self.runtime_details: dict[str, str] = {}
        self.observation: Any | None = None
        self.source_dir = Path(config.source_dir).expanduser() if config.source_dir else (
            Path(__file__).resolve().parents[1] / "third_party" / "Irodori-TTS"
        )
        self.cache_dir = Path(config.cache_dir).expanduser() if config.cache_dir else Path(
            os.getenv("HF_HUB_CACHE", str(Path(os.getenv("HF_HOME", "~/.cache/huggingface")).expanduser() / "hub"))
        )
        if not math.isfinite(config.reference_max_seconds) or not 1 <= config.reference_max_seconds <= 30:
            raise ValueError("IRODORI_REFERENCE_MAX_SECONDS must be between 1 and 30")
        if not 1 <= config.cpu_threads <= 16:
            raise ValueError("IRODORI_CPU_THREADS must be between 1 and 16")
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
        if self.config.codec_device == "cpu" or self.config.model_device == "cpu":
            # High thread counts slow short codec workloads and contend with live STT.
            importlib.import_module("torch").set_num_threads(self.config.cpu_threads)
        api = importlib.import_module("irodori_tts.inference_runtime")
        # Keep vendor files unchanged; adapt only the runtime audio loading boundary.
        if not getattr(api, "_nengitsune_soundfile_fallback", False):
            primary = api._load_audio
            api._load_audio = lambda path: load_audio_with_soundfile_fallback(path, primary)
            api._nengitsune_soundfile_fallback = True
        return api

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

    def _load(self, cancel_event: threading.Event | None = None) -> tuple[Any, Any, threading.RLock]:
        if self.observation is not None:
            self.observation.begin(loading=True)
        try:
            runtime = self._load_impl(cancel_event)
            if self.observation is not None:
                self.observation.loaded(**self.runtime_details)
            return runtime
        finally:
            if self.observation is not None:
                self.observation.finish(loading=True)

    def _load_impl(self, cancel_event: threading.Event | None = None) -> tuple[Any, Any, threading.RLock]:
        config = self.config
        key = (
            str(self.source_dir.resolve()), config.checkpoint, config.codec_repo,
            config.model_device, config.model_precision, config.codec_device,
            config.codec_precision, str(self.cache_dir.resolve()), str(config.local_files_only),
        )
        with synthesis_lock(self._runtimes_guard, cancel_event):
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
                self._runtime_descriptors[key] = {
                    "model": config.checkpoint,
                    "device": model_device,
                    "precision": model_precision,
                }
            self.runtime_details = dict(self._runtime_descriptors.get(key, {}))
            return shared

    def synthesize(
        self, text: str, *, reference_audio: bytes | None = None,
        reference_suffix: str = ".wav", cancel_event: threading.Event | None = None,
    ) -> bytes:
        reference_path: Path | None = None
        latent_path: Path | None = None
        try:
            check_synthesis_cancelled(cancel_event)
            if reference_audio is None and self.config.reference_audio_path:
                original = Path(self.config.reference_audio_path).expanduser()
                if not original.is_file() or original.stat().st_size > MAX_REFERENCE_WAV_BYTES:
                    raise ValueError("The reference voice is missing or exceeds 100 MiB")
                reference_audio = original.read_bytes()
            if reference_audio is not None:
                if not reference_audio or len(reference_audio) > MAX_REFERENCE_WAV_BYTES:
                    raise ValueError("The reference voice is empty or exceeds 100 MiB")
                with tempfile.NamedTemporaryFile(suffix=reference_suffix, delete=False) as temporary:
                    temporary.write(reference_audio)
                    reference_path = Path(temporary.name)
            check_synthesis_cancelled(cancel_event)
            api, runtime, lock = self._load(cancel_event) if cancel_event is not None else self._load()
            lock_started = time.perf_counter()
            with synthesis_lock(lock, cancel_event):
                logger.info("voice_tts_lock_wait seconds=%.3f", time.perf_counter() - lock_started)
                result = None
                try:
                    request = api.SamplingRequest(
                        text=text, caption=self.config.caption or None,
                        ref_wav=str(reference_path) if reference_path else None,
                        no_ref=reference_path is None, num_steps=self.config.num_steps,
                        max_seconds=self.config.max_seconds,
                        max_ref_seconds=self.config.reference_max_seconds,
                        seed=int(self.config.seed) if self.config.seed.strip() else None,
                    )
                    if reference_audio is not None and reference_path is not None:
                        latent_path = self._cached_reference_path(
                            api, runtime, request, reference_audio, reference_path,
                        )
                        if latent_path is not None:
                            request.ref_wav = None
                            request.ref_latent = str(latent_path)
                    check_synthesis_cancelled(cancel_event)
                    result = runtime.synthesize(request)
                    # Native inference cannot be interrupted safely. Its worker owns
                    # the result and reference files until it returns, then discards
                    # canceled audio before WAV encoding or delivery.
                    check_synthesis_cancelled(cancel_event)
                    self._log_stage_timings(result)
                    return self._encode_wav(result.audio, int(result.sample_rate))
                finally:
                    # SamplingResult also holds the candidate tensor list. Release it
                    # before returning unused PyTorch workspace to CTranslate2/STT.
                    result = None
                    self._release_cuda_workspace(runtime)
        finally:
            try:
                if latent_path is not None:
                    latent_path.unlink(missing_ok=True)
            finally:
                if reference_path is not None:
                    reference_path.unlink(missing_ok=True)

    def _cached_reference_path(
        self, api: Any, runtime: Any, request: Any, audio: bytes, path: Path,
    ) -> Path | None:
        """Use the official precomputed-latent input only on compatible runtimes.

        This runs inside the shared inference lock, so concurrent conversations do
        not encode the same reference twice. Unsupported and stochastic codecs keep
        their original waveform path; failures in a supported encoder remain errors.
        """
        codec = getattr(runtime, "codec", None)
        if (
            getattr(codec, "deterministic_encode", None) is not True
            or getattr(getattr(runtime, "model_cfg", None), "use_speaker_condition_resolved", None) is not True
            or not callable(getattr(codec, "encode_waveform", None))
            or not callable(getattr(api, "_load_audio", None))
            or not all(hasattr(request, name) for name in (
                "ref_latent", "ref_normalize_db", "ref_ensure_max", "max_ref_seconds",
            ))
        ):
            return None
        # SamplingRequest currently accepts paths, not tensors. Keep tensors in
        # memory and expose a short-lived .pt snapshot for this inference only.
        try:
            runtime_state = vars(runtime)
        except TypeError:
            return None
        owner = runtime_state.setdefault("_nengitsune_reference_cache_token", object())
        maximum = request.max_ref_seconds
        if maximum is None:
            maximum = getattr(runtime, "default_max_ref_seconds", None)
        if not isinstance(maximum, (int, float)) or not math.isfinite(maximum):
            return None
        normalize = request.ref_normalize_db
        if normalize is not None and (
            not isinstance(normalize, (int, float)) or not math.isfinite(normalize)
        ):
            return None
        config = self.config
        key = (
            owner, hashlib.sha256(audio).digest(), str(self.source_dir.resolve()),
            config.checkpoint, config.codec_repo, config.model_device, config.model_precision,
            config.codec_device, config.codec_precision, str(self.cache_dir.resolve()),
            config.local_files_only, normalize, bool(request.ref_ensure_max), float(maximum),
        )
        started = time.perf_counter()
        cls = LocalIrodoriBackend
        with cls._reference_cache_guard:
            cached = cls._reference_cache.get(key)
            if cached is not None:
                cls._reference_cache.move_to_end(key)
        import torch

        if cached is None:
            # Match runtime._load_reference_latent's waveform path: trim at the
            # original sample rate, then let the official codec downmix, resample,
            # normalize and deterministically encode. Store the UNPATCHED latent.
            waveform, sample_rate = load_audio_with_soundfile_fallback(path, api._load_audio)
            if maximum > 0:
                waveform = waveform[:, :max(1, int(maximum * float(sample_rate)))]
            latent = codec.encode_waveform(
                waveform.unsqueeze(0), sample_rate=int(sample_rate),
                normalize_db=normalize, ensure_max=bool(request.ref_ensure_max),
            )
            if (
                not torch.is_tensor(latent) or latent.ndim != 3 or latent.shape[0] != 1
                or latent.shape[1] == 0
                or latent.shape[2] != runtime.model_cfg.latent_dim
                or not bool(torch.isfinite(latent).all())
            ):
                raise ValueError("Reference codec returned an invalid latent")
            latent = latent.detach().to(device="cpu", copy=True).contiguous()
            size = latent.numel() * latent.element_size()
            with cls._reference_cache_guard:
                if cls.REFERENCE_CACHE_MAX_ENTRIES > 0 and size <= cls.REFERENCE_CACHE_MAX_BYTES:
                    while cls._reference_cache and (
                        len(cls._reference_cache) >= cls.REFERENCE_CACHE_MAX_ENTRIES
                        or cls._reference_cache_bytes + size > cls.REFERENCE_CACHE_MAX_BYTES
                    ):
                        _, (_, removed_size) = cls._reference_cache.popitem(last=False)
                        cls._reference_cache_bytes -= removed_size
                    cls._reference_cache[key] = (latent, size)
                    cls._reference_cache_bytes += size
        else:
            latent = cached[0]
        logger.info(
            "voice_tts_reference_cache hit=%s seconds=%.3f",
            cached is not None, time.perf_counter() - started,
        )
        latent_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".pt", delete=False) as temporary:
                latent_path = Path(temporary.name)
                torch.save(latent, temporary)
            return latent_path
        except BaseException:
            if latent_path is not None:
                latent_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _log_stage_timings(result: Any) -> None:
        timings = getattr(result, "stage_timings", ())
        if not isinstance(timings, (tuple, list)):
            return
        for entry in timings:
            if not isinstance(entry, (tuple, list)) or len(entry) != 2:
                continue
            stage, seconds = entry
            if not isinstance(stage, str) or stage not in {"prepare_reference", "sample_rf", "decode_latent"}:
                continue
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
                continue
            try:
                value = float(seconds)
            except (OverflowError, ValueError):
                continue
            if math.isfinite(value) and value >= 0:
                logger.info("voice_tts_stage_completed stage=%s seconds=%.3f", stage, value)

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
