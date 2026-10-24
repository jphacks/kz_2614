"""faster-whisper を使う日本語音声認識クライアント。"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


load_dotenv(Path(__file__).with_name(".env"), override=False)

WHISPER_MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "whisper"


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

    @classmethod
    def from_env(cls) -> "WhisperConfig":
        return cls(
            model_id=os.getenv("WHISPER_MODEL_ID", cls.model_id),
            device=os.getenv("WHISPER_DEVICE", cls.device),
            compute_type=os.getenv("WHISPER_COMPUTE_TYPE", cls.compute_type),
            language=os.getenv("WHISPER_LANGUAGE", cls.language),
            local_files_only=_env_bool("WHISPER_LOCAL_FILES_ONLY"),
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

        model = self._load_model()
        language = self.config.language.strip().lower()
        segments, info = model.transcribe(
            str(path),
            language=None if language in {"", "auto"} else language,
            beam_size=3,
            condition_on_previous_text=False,
            vad_filter=True,
        )
        text = " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()
        duration = getattr(info, "duration", None)
        language_probability = getattr(info, "language_probability", None)
        return Transcription(
            text=text,
            language=getattr(info, "language", None),
            language_probability=(float(language_probability) if language_probability is not None else None),
            duration_seconds=(float(duration) if duration is not None else None),
        )
