"""faster-whisper を使う日本語音声認識クライアント。"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from ..settings_store import get_saved_settings

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

# ServerSide/whisper is the existing model-cache folder and is ignored by Git.
WHISPER_MODEL_DIR = Path(__file__).resolve().parents[2] / "whisper"
DEFAULT_MAX_AUDIO_BYTES = 25_000_000
DEFAULT_MAX_AUDIO_SECONDS = 60.0


class AudioDurationExceeded(ValueError):
    """Audio decoded by faster-whisper exceeds the accepted turn length."""


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class WhisperConfig:
    model_id: str = "Systran/faster-whisper-small"
    device: str = "cpu"
    compute_type: str = "int8"
    language: str = "ja"
    local_files_only: bool = False
    max_audio_bytes: int = DEFAULT_MAX_AUDIO_BYTES
    max_duration_seconds: float = DEFAULT_MAX_AUDIO_SECONDS
    vad_filter: bool = True

    @classmethod
    def from_env(cls) -> "WhisperConfig":
        saved = get_saved_settings().get("stt", {})
        return cls(
            model_id=saved.get("model_id", os.getenv("WHISPER_MODEL_ID", cls.model_id)),
            device=saved.get("device", os.getenv("WHISPER_DEVICE", cls.device)),
            compute_type=saved.get("compute_type", os.getenv("WHISPER_COMPUTE_TYPE", cls.compute_type)),
            language=saved.get("language", os.getenv("WHISPER_LANGUAGE", cls.language)),
            local_files_only=_env_bool("WHISPER_LOCAL_FILES_ONLY"),
            max_audio_bytes=max(1_024, int(os.getenv("WHISPER_MAX_AUDIO_BYTES", str(cls.max_audio_bytes)))),
            max_duration_seconds=max(
                1.0,
                float(os.getenv("WHISPER_MAX_AUDIO_SECONDS", str(cls.max_duration_seconds))),
            ),
            vad_filter=saved.get(
                "vad_filter", _env_bool("WHISPER_VAD_FILTER", default=cls.vad_filter)
            ),
        )


@dataclass(frozen=True)
class Transcription:
    text: str
    language: str | None
    language_probability: float | None
    duration_seconds: float | None


class WhisperSTT:
    """モデルを遅延ロードし、ファイルから文字起こしする。音声の保存は行わない。"""

    def __init__(self, config: WhisperConfig | None = None) -> None:
        self.config = config or WhisperConfig.from_env()
        self._model: Any | None = None
        self._model_lock = threading.Lock()

    def _load_model(self) -> Any:
        with self._model_lock:
            if self._model is None:
                WHISPER_MODEL_DIR.mkdir(parents=True, exist_ok=True)
                try:
                    from faster_whisper import WhisperModel
                except ImportError as exc:
                    raise RuntimeError(
                        "faster-whisper is not installed; install Programs/ServerSide/app/requirements.txt"
                    ) from exc

                self._model = WhisperModel(
                    self.config.model_id,
                    device=self.config.device,
                    compute_type=self.config.compute_type,
                    download_root=str(WHISPER_MODEL_DIR),
                    local_files_only=self.config.local_files_only,
                )
            return self._model

    def transcribe_file(self, audio_path: str | Path) -> Transcription:
        """Transcribe an existing audio file; faster-whisper decodes supported formats."""
        path = Path(audio_path).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"Audio file does not exist: {path}")
        try:
            file_size = path.stat().st_size
        except OSError as exc:
            raise ValueError("Audio file cannot be accessed") from exc
        if file_size <= 0:
            raise ValueError("Audio file is empty")
        if file_size > self.config.max_audio_bytes:
            raise ValueError("Audio file exceeds WHISPER_MAX_AUDIO_BYTES")

        model = self._load_model()
        language = self.config.language.strip().lower()
        segments, info = model.transcribe(
            str(path),
            language=None if language in {"", "auto"} else language,
            beam_size=3,
            condition_on_previous_text=False,
            vad_filter=self.config.vad_filter,
        )
        duration = getattr(info, "duration", None)
        if duration is not None and float(duration) > self.config.max_duration_seconds:
            raise AudioDurationExceeded("Audio file exceeds WHISPER_MAX_AUDIO_SECONDS")
        text = " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()
        language_probability = getattr(info, "language_probability", None)
        return Transcription(
            text=text,
            language=getattr(info, "language", None),
            language_probability=(float(language_probability) if language_probability is not None else None),
            duration_seconds=(float(duration) if duration is not None else None),
        )
