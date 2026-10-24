"""Irodori-TTS の Gradio API を使う音声合成クライアント。"""

from __future__ import annotations

import mimetypes
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from ..settings_store import get_saved_settings, reference_voice_path

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

MAX_REFERENCE_AUDIO_BYTES = 10 * 1024 * 1024


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

    @classmethod
    def from_env(cls) -> "TTSConfig":
        saved = get_saved_settings().get("tts", {})
        server_url = os.getenv("IRODORI_URL", cls.server_url).strip() or cls.server_url
        uploaded_reference_path = reference_voice_path()
        reference_audio_path = (
            str(uploaded_reference_path)
            if uploaded_reference_path.is_file()
            else os.getenv("VOICE_TTS_REFERENCE_AUDIO", "").strip()
        )
        return cls(
            server_url=server_url.rstrip("/") + "/",
            checkpoint=saved.get("checkpoint", os.getenv("VOICE_TTS_CHECKPOINT", cls.checkpoint)),
            model_device=os.getenv("VOICE_TTS_MODEL_DEVICE", cls.model_device),
            model_precision=os.getenv("VOICE_TTS_MODEL_PRECISION", cls.model_precision),
            codec_device=os.getenv("VOICE_TTS_CODEC_DEVICE", cls.codec_device),
            codec_precision=os.getenv("VOICE_TTS_CODEC_PRECISION", cls.codec_precision),
            caption=saved.get("caption", os.getenv("VOICE_TTS_CAPTION", cls.caption)),
            seed=os.getenv("VOICE_TTS_SEED", cls.seed),
            reference_audio_path=reference_audio_path,
            max_audio_bytes=max(1_024, int(os.getenv("VOICE_TTS_MAX_AUDIO_BYTES", str(cls.max_audio_bytes)))),
        )


@dataclass(frozen=True)
class SynthesizedSpeech:
    """スマホへ返す音声データ。サーバー上の一時ファイルは参照しない。"""

    audio_bytes: bytes
    media_type: str
    file_extension: str


class IrodoriTTS:
    """ローカルの Irodori-TTS Gradio サーバーから音声を生成する。"""

    def __init__(self, config: TTSConfig | None = None) -> None:
        self.config = config or TTSConfig.from_env()
        self._client: Any | None = None
        self._client_lock = threading.Lock()
        self._synthesis_lock = threading.Lock()
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

    def _get_client(self) -> Any:
        with self._client_lock:
            if self._client is None:
                try:
                    from gradio_client import Client
                except ImportError as exc:
                    raise SpeechSynthesisError(
                        "gradio_client is not installed; install app/requirements.txt"
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

    def synthesize(self, text: str) -> SynthesizedSpeech:
        """短い返答を合成し、音声バイト列と配信形式を返す。"""
        spoken_text = text.strip()
        if not spoken_text:
            raise ValueError("text must not be empty")
        if len(spoken_text) > 1_000:
            raise ValueError("text must be at most 1000 characters")

        reference_audio: Any = None
        temporary_reference_path: Path | None = None
        try:
            if self._reference_audio_bytes is not None or self.config.reference_audio_path:
                try:
                    from gradio_client import handle_file
                except ImportError as exc:
                    raise SpeechSynthesisError(
                        "gradio_client is not installed; install app/requirements.txt"
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

            client = self._get_client()
            with self._synthesis_lock:
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
        return self._read_audio(result)
