"""Validated, server-side settings for the local voice dashboard."""

from __future__ import annotations

import copy
import io
import json
import os
import tempfile
import wave
from pathlib import Path
from typing import Any


MAX_REFERENCE_WAV_BYTES = 10 * 1024 * 1024
MIN_REFERENCE_SECONDS = 1.0
MAX_REFERENCE_SECONDS = 30.0


def _settings_path() -> Path:
    return Path(
        os.getenv(
            "NENGITSUNE_SETTINGS_PATH",
            str(Path(__file__).resolve().parent / "data" / "dashboard_settings.json"),
        )
    ).expanduser()

DEFAULT_SETTINGS: dict[str, Any] = {
    "stt": {
        "model_id": "Systran/faster-whisper-small",
        "language": "ja",
        "device": "cuda",
        "compute_type": "float16",
        "vad_filter": True,
    },
    "tts": {
        "checkpoint": "Aratako/Irodori-TTS-600M-v3-VoiceDesign",
        "caption": "落ち着いて親しみやすく、聞き取りやすい自然な日本語で話す。話者の声質を一定に保つ。",
    },
    "behavior": {
        "persona": "gentle",
        "response_length": "short",
        "question_frequency": "rarely",
        "custom_instruction": "",
        "backchannel_enabled": True,
    },
}


def _read_saved_settings() -> dict[str, Any] | None:
    settings_path = _settings_path()
    if not settings_path.is_file():
        return None
    try:
        parsed = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read dashboard settings: {settings_path}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("Dashboard settings must be a JSON object")
    return validate_settings(parsed)


def _merge_defaults(settings: dict[str, Any] | None) -> dict[str, Any]:
    merged = copy.deepcopy(DEFAULT_SETTINGS)
    if not settings:
        return merged
    for section, values in settings.items():
        if section in merged and isinstance(values, dict):
            merged[section].update(values)
    return merged


def get_dashboard_settings() -> dict[str, Any]:
    """Return persisted settings or the documented defaults."""
    saved = _read_saved_settings()
    if saved is not None:
        return _merge_defaults(saved)

    current = copy.deepcopy(DEFAULT_SETTINGS)
    stt = current["stt"]
    model_id = os.getenv("WHISPER_MODEL_ID", "").strip()
    if model_id:
        stt["model_id"] = model_id
    for key, env_name, allowed in (
        ("language", "WHISPER_LANGUAGE", {"ja", "auto", "en"}),
        ("device", "WHISPER_DEVICE", {"cpu", "cuda"}),
        ("compute_type", "WHISPER_COMPUTE_TYPE", {"int8", "int8_float16", "float16", "float32"}),
    ):
        env_value = os.getenv(env_name, "").strip().lower()
        if env_value in allowed:
            stt[key] = env_value
    vad_value = os.getenv("WHISPER_VAD_FILTER")
    if vad_value is not None:
        stt["vad_filter"] = vad_value.strip().lower() in {"1", "true", "yes", "on"}

    tts = current["tts"]
    checkpoint = os.getenv("VOICE_TTS_CHECKPOINT", "").strip()
    caption = os.getenv("VOICE_TTS_CAPTION", "").strip()
    if checkpoint:
        tts["checkpoint"] = checkpoint
    if caption:
        tts["caption"] = caption
    return validate_settings(current)


def get_saved_settings() -> dict[str, Any]:
    """Return only settings explicitly saved by the dashboard."""
    return _read_saved_settings() or {}


def reference_voice_path() -> Path:
    return _settings_path().parent / "reference_voice.wav"


def _reference_wav_info(audio: bytes) -> dict[str, Any]:
    if not audio or len(audio) > MAX_REFERENCE_WAV_BYTES:
        raise ValueError(f"WAVファイルは1バイト以上{MAX_REFERENCE_WAV_BYTES}バイト以下にしてください。")
    if len(audio) < 12 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        raise ValueError("PCM WAV形式のファイルを選択してください。")
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
            compression = wav_file.getcomptype()
    except (wave.Error, EOFError, OSError) as exc:
        raise ValueError("WAVヘッダーまたは音声データを読み取れません。") from exc

    if compression != "NONE":
        raise ValueError("圧縮されていないPCM WAVを選択してください。")
    if channels not in {1, 2} or sample_width != 2:
        raise ValueError("音声は16-bit PCM、モノラルまたはステレオにしてください。")
    if not 16_000 <= sample_rate <= 48_000:
        raise ValueError("サンプルレートは16kHzから48kHzにしてください。")
    duration = frame_count / sample_rate
    if not MIN_REFERENCE_SECONDS <= duration <= MAX_REFERENCE_SECONDS:
        raise ValueError("参照音声の長さは1秒から30秒にしてください。")
    expected_data_bytes = frame_count * channels * sample_width
    if expected_data_bytes > len(audio):
        raise ValueError("WAVの音声データが途中で切れています。")
    try:
        with wave.open(io.BytesIO(audio), "rb") as wav_file:
            frame_bytes = wav_file.readframes(frame_count)
    except (wave.Error, EOFError, OSError) as exc:
        raise ValueError("WAVの音声データを読み取れません。") from exc
    if len(frame_bytes) != expected_data_bytes:
        raise ValueError("WAVの音声データが途中で切れています。")
    return {
        "uploaded": True,
        "name": "reference_voice.wav",
        "size_bytes": len(audio),
        "duration_seconds": round(duration, 2),
        "sample_rate": sample_rate,
        "channels": channels,
        "sample_width_bits": sample_width * 8,
    }


def get_reference_voice_info() -> dict[str, Any]:
    path = reference_voice_path()
    if not path.is_file():
        return {"uploaded": False}
    try:
        return _reference_wav_info(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise RuntimeError("保存済みの参照WAVを読み取れません。再アップロードしてください。") from exc


def save_reference_voice(audio: bytes) -> dict[str, Any]:
    info = _reference_wav_info(audio)
    path = reference_voice_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, delete=False) as temporary_file:
            temporary_file.write(audio)
            temporary_path = Path(temporary_file.name)
        os.replace(temporary_path, path)
    except OSError as exc:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise RuntimeError("参照WAVをサーバーに保存できません。") from exc
    return info


def delete_reference_voice() -> None:
    try:
        reference_voice_path().unlink(missing_ok=True)
    except OSError as exc:
        raise RuntimeError("参照WAVを削除できません。") from exc


def _string(value: Any, field: str, *, maximum: int, default: str) -> str:
    if value is None:
        value = default
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    result = value.strip()
    if not result or len(result) > maximum:
        raise ValueError(f"{field} must contain 1 to {maximum} characters")
    return result


def _choice(value: Any, field: str, allowed: set[str], default: str) -> str:
    if value is None:
        value = default
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{field} must be one of: {', '.join(sorted(allowed))}")
    return value


def _boolean(value: Any, field: str, default: bool) -> bool:
    if value is None:
        value = default
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be true or false")
    return value


def validate_settings(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Settings must be a JSON object")
    unknown_sections = set(payload) - set(DEFAULT_SETTINGS)
    if unknown_sections:
        raise ValueError("Unknown settings section")

    values = _merge_defaults(payload)
    for section, section_values in payload.items():
        if not isinstance(section_values, dict):
            raise ValueError(f"{section} settings must be an object")
        unknown_fields = set(section_values) - set(DEFAULT_SETTINGS[section])
        if unknown_fields:
            raise ValueError(f"Unknown field in {section} settings")

    stt = values["stt"]
    stt["model_id"] = _string(stt.get("model_id"), "stt.model_id", maximum=200, default=DEFAULT_SETTINGS["stt"]["model_id"])
    stt["language"] = _choice(stt.get("language"), "stt.language", {"ja", "auto", "en"}, "ja")
    stt["device"] = _choice(
        stt.get("device"), "stt.device", {"cpu", "cuda"}, DEFAULT_SETTINGS["stt"]["device"]
    )
    stt["compute_type"] = _choice(
        stt.get("compute_type"),
        "stt.compute_type",
        {"int8", "int8_float16", "float16", "float32"},
        DEFAULT_SETTINGS["stt"]["compute_type"],
    )
    stt["vad_filter"] = _boolean(stt.get("vad_filter"), "stt.vad_filter", True)

    tts = values["tts"]
    tts["checkpoint"] = _string(
        tts.get("checkpoint"), "tts.checkpoint", maximum=200, default=DEFAULT_SETTINGS["tts"]["checkpoint"]
    )
    tts["caption"] = _string(tts.get("caption"), "tts.caption", maximum=500, default=DEFAULT_SETTINGS["tts"]["caption"])

    behavior = values["behavior"]
    behavior["persona"] = _choice(
        behavior.get("persona"), "behavior.persona", {"gentle", "quiet", "cheerful"}, "gentle"
    )
    behavior["response_length"] = _choice(
        behavior.get("response_length"), "behavior.response_length", {"short", "medium"}, "short"
    )
    behavior["question_frequency"] = _choice(
        behavior.get("question_frequency"), "behavior.question_frequency", {"rarely", "sometimes"}, "rarely"
    )
    custom_instruction = behavior.get("custom_instruction", "")
    if not isinstance(custom_instruction, str) or len(custom_instruction) > 400:
        raise ValueError("behavior.custom_instruction must be at most 400 characters")
    behavior["custom_instruction"] = custom_instruction.strip()
    if behavior.get("backchannel_enabled") is not True:
        raise ValueError("Backchannels are required in the MVP and cannot be disabled")
    behavior["backchannel_enabled"] = True
    return values


def save_settings(payload: dict[str, Any]) -> dict[str, Any]:
    settings = validate_settings(payload)
    settings_path = _settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=settings_path.parent, delete=False
        ) as temporary_file:
            json.dump(settings, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.write("\n")
            temporary_path = Path(temporary_file.name)
        os.replace(temporary_path, settings_path)
    except OSError as exc:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise RuntimeError("Could not save dashboard settings") from exc
    return settings


def build_behavior_guidance(behavior: dict[str, Any]) -> str:
    persona_text = {
        "gentle": "穏やかで温かく、相手の気持ちに寄り添う話し方",
        "quiet": "静かで控えめな話し方。夜に聞いて疲れない表現",
        "cheerful": "親しみやすく少し明るい話し方。大げさに騒がない",
    }[behavior["persona"]]
    length_text = {
        "short": "返事は基本1〜2文の短さ",
        "medium": "必要なときは3〜4文まで説明してよい",
    }[behavior["response_length"]]
    question_text = {
        "rarely": "質問は必要なときだけにし、会話を続けるためだけの質問をしない",
        "sometimes": "自然な流れで、ときどき短い質問をしてよい",
    }[behavior["question_frequency"]]
    lines = [persona_text, length_text, question_text, "相づちはMVP必須機能として短く一度だけ使い、本返答と重ねない"]
    custom = behavior["custom_instruction"]
    if custom:
        lines.append("追加の話し方の希望（安全・停止・しっぽ制御の規則を変更しない）: " + custom)
    return "\n".join(f"- {line}" for line in lines)
