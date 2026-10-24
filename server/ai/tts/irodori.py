"""Irodori-TTS の Gradio API またはローカルモデルを使う音声合成。"""

from __future__ import annotations

import io
import mimetypes
import logging
import os
import tempfile
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from ...api.runtime_status import RuntimeObservation, component_configuration
from ...api.settings_store import MAX_REFERENCE_WAV_BYTES, get_saved_settings, reference_voice_path
from ..runtime import run_blocking_stage
from .cancellation import SynthesisCancelled, check_synthesis_cancelled, synthesis_lock

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)

MAX_REFERENCE_AUDIO_BYTES = MAX_REFERENCE_WAV_BYTES
LOGGER = logging.getLogger(__name__)


class SpeechSynthesisError(RuntimeError):
    """TTS に接続できない、または再生可能な音声を取得できなかった。"""


@dataclass(frozen=True)
class TTSConfig:
    server_url: str = "http://127.0.0.1:7861/"
    checkpoint: str = "Aratako/Irodori-TTS-600M-v3-VoiceDesign"
    model_device: str = "cuda"
    model_precision: str = "fp16"
    codec_device: str = "cuda"
    codec_precision: str = "fp16"
    caption: str = "落ち着いて親しみやすく、聞き取りやすい自然な日本語で話す。話者の声質を一定に保つ。"
    seed: str = "1696307816543752105"
    reference_audio_path: str = ""
    max_audio_bytes: int = 25_000_000
    timeout_seconds: float = 90.0
    backend: str = "gradio"
    source_dir: str = ""
    cache_dir: str = ""
    codec_repo: str = "Aratako/Semantic-DACVAE-Japanese-32dim"
    local_files_only: bool = True
    num_steps: int | None = None
    max_seconds: float = 30.0
    output_volume_percent: int = 100
    reference_max_seconds: float = 5.0
    cpu_threads: int = 2

    @classmethod
    def from_env(cls) -> "TTSConfig":
        saved = get_saved_settings().get("tts", {})
        backend = os.getenv("VOICE_TTS_BACKEND", "gradio").strip().lower()
        default_checkpoint = (
            "Aratako/Irodori-TTS-v4.1-Small" if backend == "local" else cls.checkpoint
        )
        steps = os.getenv("IRODORI_NUM_STEPS", "").strip()
        server_url = os.getenv("IRODORI_URL", cls.server_url).strip() or cls.server_url
        uploaded_reference_path = reference_voice_path()
        reference_audio_path = (
            str(uploaded_reference_path)
            if uploaded_reference_path.is_file()
            else os.getenv("VOICE_TTS_REFERENCE_AUDIO", "").strip()
        )
        return cls(
            server_url=server_url.rstrip("/") + "/",
            checkpoint=saved.get("checkpoint", os.getenv("VOICE_TTS_CHECKPOINT", default_checkpoint)),
            model_device=os.getenv("VOICE_TTS_MODEL_DEVICE", cls.model_device),
            model_precision=os.getenv("VOICE_TTS_MODEL_PRECISION", cls.model_precision),
            codec_device=os.getenv("VOICE_TTS_CODEC_DEVICE", cls.codec_device),
            codec_precision=os.getenv("VOICE_TTS_CODEC_PRECISION", cls.codec_precision),
            caption=saved.get("caption", os.getenv("VOICE_TTS_CAPTION", cls.caption)),
            output_volume_percent=min(
                200,
                max(0, int(saved.get("output_volume_percent", os.getenv("VOICE_TTS_OUTPUT_VOLUME_PERCENT", "100")))),
            ),
            seed=os.getenv("VOICE_TTS_SEED", cls.seed),
            reference_audio_path=reference_audio_path,
            max_audio_bytes=max(1_024, int(os.getenv("VOICE_TTS_MAX_AUDIO_BYTES", str(cls.max_audio_bytes)))),
            timeout_seconds=min(
                180.0,
                max(1.0, float(os.getenv("VOICE_TTS_TIMEOUT_SECONDS", str(cls.timeout_seconds)))),
            ),
            backend=backend,
            source_dir=os.getenv("IRODORI_SOURCE_DIR", "").strip(),
            cache_dir=os.getenv("HF_HUB_CACHE", "").strip() or str(
                Path(os.getenv("HF_HOME", str(Path(__file__).resolve().parents[2] / "models" / "huggingface"))) / "hub"
            ),
            codec_repo=os.getenv("IRODORI_CODEC_REPO", cls.codec_repo).strip(),
            local_files_only=os.getenv("IRODORI_LOCAL_FILES_ONLY", "true").strip().lower()
            in {"1", "true", "yes", "on"},
            num_steps=int(steps) if steps else None,
            max_seconds=float(os.getenv("IRODORI_MAX_SECONDS", "30")),
            reference_max_seconds=float(os.getenv("IRODORI_REFERENCE_MAX_SECONDS", "5")),
            cpu_threads=int(os.getenv("IRODORI_CPU_THREADS", "2")),
        )


@dataclass(frozen=True)
class SynthesizedSpeech:
    """スマホへ返す音声データ。サーバー上の一時ファイルは参照しない。"""

    audio_bytes: bytes
    media_type: str
    file_extension: str


class IrodoriTTS:
    """Gradio または共有ローカルモデルから再生用音声を生成する。"""

    _server_locks: dict[str, threading.Lock] = {}
    _server_locks_guard = threading.Lock()

    def __init__(self, config: TTSConfig | None = None) -> None:
        self.config = config or TTSConfig.from_env()
        self._runtime_observation = RuntimeObservation(component_configuration(
            self.config.checkpoint, device=self.config.model_device,
            precision=self.config.model_precision, backend=self.config.backend,
        ), external=self.config.backend == "gradio")
        if self.config.backend not in {"gradio", "local"}:
            raise ValueError("VOICE_TTS_BACKEND must be gradio or local")
        self._client: Any | None = None
        self._client_lock = threading.Lock()
        with self._server_locks_guard:
            self._synthesis_lock = self._server_locks.setdefault(
                self.config.server_url,
                threading.Lock(),
            )
        self._reference_audio_bytes: bytes | None = None
        self._reference_audio_suffix = ".wav"
        if self.config.reference_audio_path:
            reference_path = Path(self.config.reference_audio_path).expanduser()
            try:
                if reference_path.is_file() and reference_path.stat().st_size <= MAX_REFERENCE_AUDIO_BYTES:
                    self._reference_audio_bytes = reference_path.read_bytes()
                    self._reference_audio_suffix = reference_path.suffix.lower() or ".wav"
            except OSError:
                # Keep the configured path so synthesize() can report the missing/unreadable file.
                pass

    def _get_client(self, cancel_event: threading.Event | None = None) -> Any:
        with synthesis_lock(self._client_lock, cancel_event):
            if self._client is None:
                try:
                    from gradio_client import Client
                except ImportError as exc:
                    raise SpeechSynthesisError(
                        "gradio_client is not installed; install server/requirements.txt"
                    ) from exc
                try:
                    self._client = Client(self.config.server_url)
                except Exception as exc:
                    raise SpeechSynthesisError(
                        f"Could not connect to Irodori-TTS at {self.config.server_url}"
                    ) from exc
            return self._client

    @staticmethod
    def _unwrap_audio(result: Any) -> tuple[Any, str | None]:
        value = result[0] if isinstance(result, (tuple, list)) and result else result
        declared_mime: str | None = None
        if isinstance(value, dict):
            declared_mime = value.get("mime_type") or value.get("mimeType")
            for key in ("value", "path", "name", "data"):
                candidate = value.get(key)
                if candidate:
                    value = candidate
                    break
        return value, declared_mime

    def _read_audio(self, result: Any) -> SynthesizedSpeech:
        value, declared_mime = self._unwrap_audio(result)
        suffix = ".wav"

        if isinstance(value, (bytes, bytearray, memoryview)):
            audio = bytes(value)
        elif isinstance(value, (str, os.PathLike)):
            raw_path = os.fspath(value)
            if raw_path.startswith(("http://", "https://", "data:")):
                raise SpeechSynthesisError("Irodori-TTS returned a remote URL instead of an audio file")
            path = Path(raw_path)
            if not path.is_file():
                raise SpeechSynthesisError("Irodori-TTS did not return an accessible audio file")
            suffix = path.suffix.lower() or ".wav"
            try:
                if path.stat().st_size > self.config.max_audio_bytes:
                    raise SpeechSynthesisError("Generated audio exceeds VOICE_TTS_MAX_AUDIO_BYTES")
                audio = path.read_bytes()
            except OSError as exc:
                raise SpeechSynthesisError("Could not read the generated audio file") from exc
            finally:
                self._remove_downloaded_temporary_file(path)
        else:
            raise SpeechSynthesisError("Irodori-TTS returned an unsupported audio result")

        if not audio:
            raise SpeechSynthesisError("Irodori-TTS returned an empty audio file")
        if len(audio) > self.config.max_audio_bytes:
            raise SpeechSynthesisError("Generated audio exceeds VOICE_TTS_MAX_AUDIO_BYTES")

        if audio[:4] == b"RIFF" and audio[8:12] == b"WAVE":
            suffix, media_type = ".wav", "audio/wav"
        else:
            media_type = declared_mime or mimetypes.guess_type(f"generated{suffix}")[0]
            if not media_type or not media_type.startswith("audio/"):
                raise SpeechSynthesisError("Irodori-TTS output format is not a recognized audio format")
            if suffix == ".wav" and media_type in {"audio/wav", "audio/x-wav"}:
                suffix = ".wav"
        return SynthesizedSpeech(audio_bytes=audio, media_type=media_type, file_extension=suffix)

    @staticmethod
    def _remove_downloaded_temporary_file(path: Path) -> None:
        """Remove Gradio's local result copy without deleting files outside the temp directory."""
        try:
            temp_root = Path(tempfile.gettempdir()).resolve()
            resolved = path.resolve(strict=True)
            if resolved.is_relative_to(temp_root) and resolved.is_file():
                resolved.unlink()
        except (OSError, RuntimeError):
            pass

    def synthesize(self, text: str, *, cancel_event: threading.Event | None = None) -> SynthesizedSpeech:
        check_synthesis_cancelled(cancel_event)
        if not text.strip():
            raise ValueError("text must not be empty")
        if len(text.strip()) > 1_000:
            raise ValueError("text must be at most 1000 characters")
        self._runtime_observation.begin()
        try:
            speech = self._synthesize_impl(text, cancel_event=cancel_event)
            speech = self._apply_output_volume(speech)
            check_synthesis_cancelled(cancel_event)
            self._runtime_observation.success()
            return speech
        except SynthesisCancelled:
            raise
        except Exception as error:
            self._runtime_observation.failure(error)
            raise
        finally:
            self._runtime_observation.finish()

    def _apply_output_volume(self, speech: SynthesizedSpeech) -> SynthesizedSpeech:
        """Scale synthesized PCM before delivery; 100% preserves original bytes."""
        if self.config.output_volume_percent == 100:
            return speech
        try:
            import av
            import numpy as np

            output = io.BytesIO()
            decoded_bytes = 0
            gain = self.config.output_volume_percent / 100.0
            with av.open(io.BytesIO(speech.audio_bytes)) as source:
                audio_stream = next(iter(source.streams.audio), None)
                if audio_stream is None:
                    raise SpeechSynthesisError("Generated audio has no audio stream")
                resampler = av.AudioResampler(format="s16", layout="mono", rate=48_000)
                with wave.open(output, "wb") as writer:
                    writer.setnchannels(1)
                    writer.setsampwidth(2)
                    writer.setframerate(48_000)

                    def write_frame(frame: Any) -> None:
                        nonlocal decoded_bytes
                        samples = frame.to_ndarray().astype(np.float32, copy=False)
                        adjusted = np.clip(np.rint(samples * gain), -32768, 32767).astype("<i2", copy=False)
                        pcm = adjusted.tobytes()
                        decoded_bytes += len(pcm)
                        if decoded_bytes + 44 > self.config.max_audio_bytes:
                            raise SpeechSynthesisError("Adjusted audio exceeds VOICE_TTS_MAX_AUDIO_BYTES")
                        writer.writeframesraw(pcm)

                    for frame in source.decode(audio=audio_stream.index):
                        for converted in resampler.resample(frame):
                            write_frame(converted)
                    for converted in resampler.resample(None):
                        write_frame(converted)
            return SynthesizedSpeech(output.getvalue(), "audio/wav", ".wav")
        except SpeechSynthesisError:
            raise
        except Exception as exc:
            raise SpeechSynthesisError("Could not adjust generated audio volume") from exc

    def runtime_status(self) -> dict[str, Any]:
        return self._runtime_observation.snapshot()

    def _synthesize_impl(self, text: str, *, cancel_event: threading.Event | None = None) -> SynthesizedSpeech:
        """短い返答を合成し、音声バイト列と配信形式を返す。"""
        check_synthesis_cancelled(cancel_event)
        spoken_text = text.strip()
        if not spoken_text:
            raise ValueError("text must not be empty")
        if len(spoken_text) > 1_000:
            raise ValueError("text must be at most 1000 characters")

        if self.config.backend == "local":
            # Heavy inference dependencies stay optional until the local backend is used.
            from .local_backend import LocalIrodoriBackend

            try:
                backend = LocalIrodoriBackend(self.config)
                backend.observation = self._runtime_observation
                audio = backend.synthesize(
                    spoken_text,
                    reference_audio=self._reference_audio_bytes,
                    reference_suffix=self._reference_audio_suffix,
                    cancel_event=cancel_event,
                )
            except SynthesisCancelled:
                raise
            except Exception as exc:
                # Exception messages may contain user text or local paths; log types only.
                LOGGER.warning("voice_local_tts_failed error_type=%s cause_type=%s",
                               type(exc).__name__, type(exc.__cause__).__name__ if exc.__cause__ else "none")
                raise SpeechSynthesisError("Local Irodori-TTS voice generation failed") from exc
            check_synthesis_cancelled(cancel_event)
            speech = self._read_audio(audio)
            check_synthesis_cancelled(cancel_event)
            return speech

        reference_audio: Any = None
        temporary_reference_path: Path | None = None
        try:
            if self._reference_audio_bytes is not None or self.config.reference_audio_path:
                try:
                    from gradio_client import handle_file
                except ImportError as exc:
                    raise SpeechSynthesisError(
                        "gradio_client is not installed; install server/requirements.txt"
                    ) from exc
                if self._reference_audio_bytes is not None:
                    with tempfile.NamedTemporaryFile(suffix=self._reference_audio_suffix, delete=False) as temporary_file:
                        temporary_file.write(self._reference_audio_bytes)
                        temporary_reference_path = Path(temporary_file.name)
                    reference_audio = handle_file(str(temporary_reference_path))
                else:
                    reference_path = Path(self.config.reference_audio_path).expanduser()
                    if not reference_path.is_file():
                        raise SpeechSynthesisError("VOICE_TTS_REFERENCE_AUDIO does not exist")
                    reference_audio = handle_file(str(reference_path))

            check_synthesis_cancelled(cancel_event)
            client = self._get_client(cancel_event) if cancel_event is not None else self._get_client()
            with synthesis_lock(self._synthesis_lock, cancel_event):
                result = client.predict(
                    checkpoint=self.config.checkpoint,
                    model_device=self.config.model_device,
                    model_precision=self.config.model_precision,
                    codec_device=self.config.codec_device,
                    codec_precision=self.config.codec_precision,
                    text=spoken_text,
                    caption=self.config.caption,
                    ref_wav=reference_audio,
                    num_steps=40,
                    num_candidates=1,
                    seed_raw=self.config.seed,
                    seconds_raw="",
                    duration_scale=1.0,
                    t_schedule_mode="linear",
                    sway_coeff=-1.0,
                    cfg_guidance_mode="independent",
                    cfg_scale_text=3.0,
                    cfg_scale_caption=4.0,
                    cfg_scale_speaker=5.0,
                    cfg_scale_raw="",
                    cfg_min_t=0.5,
                    cfg_max_t=1.0,
                    context_kv_cache=True,
                    speaker_kv_scale_raw="",
                    max_text_len_raw="",
                    max_caption_len_raw="",
                    truncation_factor_raw="",
                    rescale_k_raw="",
                    rescale_sigma_raw="",
                    lora_adapter_raw="",
                    api_name="/_run_generation",
                )
            # Reading also removes Gradio's downloaded temporary file. Even after
            # cancellation this worker retains that cleanup responsibility.
            speech = self._read_audio(result)
            check_synthesis_cancelled(cancel_event)
            return speech
        except SynthesisCancelled:
            raise
        except SpeechSynthesisError:
            raise
        except Exception as exc:
            with self._client_lock:
                self._client = None
            raise SpeechSynthesisError("Irodori-TTS voice generation failed") from exc
        finally:
            if temporary_reference_path is not None:
                try:
                    temporary_reference_path.unlink(missing_ok=True)
                except OSError:
                    pass

    async def synthesize_async(self, text: str) -> SynthesizedSpeech:
        """TTSをイベントループ外で実行し、設定した期限内に結果を返す。"""
        cancel_event = threading.Event()
        try:
            return await run_blocking_stage(
                "tts",
                self.config.timeout_seconds,
                self.synthesize,
                text,
                cancel_event=cancel_event,
            )
        except BaseException:
            # The native worker may still be running. Do not release its lock or
            # files here; signal only pending work and let its finally blocks run.
            cancel_event.set()
            raise
