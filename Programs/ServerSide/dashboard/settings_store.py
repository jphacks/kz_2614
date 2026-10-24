"""Validated, server-side settings for the local voice dashboard."""

from __future__ import annotations

import copy
import io
import json
import math
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
    "vad": {
        "threshold": 0.65,
        "end_threshold": 0.35,
        "min_speech_ms": 384,
        "silence_ms": 896,
        "preroll_ms": 640,
        "max_speech_seconds": 20.0,
    },
    "stt": {
        "model_id": "Systran/faster-whisper-small",
        "language": "ja",
        "device": "cuda",
        "compute_type": "float16",
        "vad_filter": True,
    },
    "llm": {
        "model": "google/gemma-4-26b-a4b-qat",
        "max_output_tokens": 160,
        "context_length": 4096,
        "max_history_messages": 8,
        "temperature": 0.7,
        "timeout_seconds": 45.0,
    },
    "tts": {
        "checkpoint": "Aratako/Irodori-TTS-600M-v3-VoiceDesign",
        "caption": "落ち着いて親しみやすく、聞き取りやすい自然な日本語で話す。話者の声質を一定に保つ。",
        "output_volume_percent": 100,
    },
    "behavior": {
        "persona": "gentle",
        "response_length": "short",
        "question_frequency": "rarely",
        "custom_instruction": "",
        "backchannel_enabled": True,
        "ack_delay_seconds": 0.9,
    },
}


def get_settings_defaults() -> dict[str, Any]:
    """Defaults must match the backend that a new pipeline will actually use."""
    defaults = copy.deepcopy(DEFAULT_SETTINGS)
    llm = defaults["llm"]
    llm_model = os.getenv("VOICE_LLM_MODEL", "").strip()
    if llm_model:
        llm["model"] = llm_model
    llm["max_output_tokens"] = min(1024, max(32, int(os.getenv("VOICE_LLM_MAX_TOKENS", "160"))))
    llm["context_length"] = min(65_536, max(512, int(os.getenv("VOICE_LLM_CONTEXT_LENGTH", "4096"))))
    llm["max_history_messages"] = min(32, max(0, int(os.getenv("VOICE_LLM_HISTORY_MESSAGES", "8"))))
    llm["temperature"] = min(1.5, max(0.0, float(os.getenv("VOICE_LLM_TEMPERATURE", "0.7"))))
    llm["timeout_seconds"] = min(180.0, max(5.0, float(os.getenv("VOICE_LLM_TIMEOUT_SECONDS", "45"))))
    defaults["behavior"]["ack_delay_seconds"] = min(
        10.0, max(0.0, float(os.getenv("VOICE_ACK_DELAY_SECONDS", "0.9")))
    )
    defaults["tts"]["output_volume_percent"] = min(
        200, max(0, int(os.getenv("VOICE_TTS_OUTPUT_VOLUME_PERCENT", "100")))
    )
    if os.getenv("VOICE_TTS_BACKEND", "gradio").strip().lower() == "local":
        defaults["tts"]["checkpoint"] = "Aratako/Irodori-TTS-v4.1-Small"
    return defaults


def validate_vad_settings(values: dict[str, Any]) -> dict[str, int | float]:
    """Keep persisted controls and the streaming VAD within the same bounds."""
    result: dict[str, int | float] = {}
    for key in DEFAULT_SETTINGS["vad"]:
        value = values.get(key, DEFAULT_SETTINGS["vad"][key])
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or (isinstance(value, float) and not math.isfinite(value))
        ):
            raise ValueError(f"vad.{key} must be a finite number")
        if key.endswith("_ms") and not isinstance(value, int):
            raise ValueError(f"vad.{key} must be an integer")
        result[key] = value
    if not 0 < result["end_threshold"] < result["threshold"] < 1:
        raise ValueError("発話検出のしきい値は 0 < 終了 < 開始 < 1 にしてください。")
    if not 128 <= result["min_speech_ms"] <= 1000:
        raise ValueError("発話として認める長さは128〜1000msにしてください。")
    if not 320 <= result["silence_ms"] <= 2000:
        raise ValueError("話し終わりの待ち時間は320〜2000msにしてください。")
    if not result["min_speech_ms"] + 128 <= result["preroll_ms"] <= 2000:
        raise ValueError("先頭に残す音声は最小発話時間+128ms以上、2000ms以下にしてください。")
    if not 1 <= result["max_speech_seconds"] <= 30:
        raise ValueError("発話の最大長は1〜30秒にしてください。")
    return result


def _vad_environment_settings() -> dict[str, int | float]:
    values = copy.deepcopy(DEFAULT_SETTINGS["vad"])
    for key, env_name in (
        ("threshold", "VOICE_VAD_THRESHOLD"),
        ("end_threshold", "VOICE_VAD_END_THRESHOLD"),
        ("min_speech_ms", "VOICE_VAD_MIN_SPEECH_MS"),
        ("silence_ms", "VOICE_VAD_SILENCE_MS"),
        ("preroll_ms", "VOICE_VAD_PREROLL_MS"),
        ("max_speech_seconds", "VOICE_VAD_MAX_SPEECH_SECONDS"),
    ):
        raw = os.getenv(env_name)
        if raw is not None:
            values[key] = int(raw) if key.endswith("_ms") else float(raw)
    return validate_vad_settings(values)


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
    # Legacy settings files predate VAD controls. Preserve their environment values
    # until this section is explicitly saved, rather than silently resetting them.
    if "vad" not in parsed:
        parsed["vad"] = _vad_environment_settings()
    return validate_settings(parsed)


def _merge_defaults(settings: dict[str, Any] | None) -> dict[str, Any]:
    merged = get_settings_defaults()
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

    current = get_settings_defaults()
    current["vad"] = _vad_environment_settings()
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


def _integer(value: Any, field: str, *, minimum: int, maximum: int, default: int) -> int:
    if value is None:
        value = default
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{field} must be an integer between {minimum} and {maximum}")
    return value


def _number(value: Any, field: str, *, minimum: float, maximum: float, default: float) -> float:
    if value is None:
        value = default
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
    ):
        raise ValueError(f"{field} must be a number between {minimum} and {maximum}")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{field} must be a number between {minimum} and {maximum}")
    if not minimum <= value <= maximum:
        raise ValueError(f"{field} must be a number between {minimum} and {maximum}")
    return float(value)


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

    values["vad"] = validate_vad_settings(values["vad"])

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

    llm = values["llm"]
    llm["model"] = _string(llm.get("model"), "llm.model", maximum=200, default=DEFAULT_SETTINGS["llm"]["model"])
    llm["max_output_tokens"] = _integer(
        llm.get("max_output_tokens"), "llm.max_output_tokens", minimum=32, maximum=1024,
        default=DEFAULT_SETTINGS["llm"]["max_output_tokens"],
    )
    llm["context_length"] = _integer(
        llm.get("context_length"), "llm.context_length", minimum=512, maximum=65_536,
        default=DEFAULT_SETTINGS["llm"]["context_length"],
    )
    if llm["max_output_tokens"] > llm["context_length"]:
        raise ValueError("llm.max_output_tokens must not exceed llm.context_length")
    llm["max_history_messages"] = _integer(
        llm.get("max_history_messages"), "llm.max_history_messages", minimum=0, maximum=32,
        default=DEFAULT_SETTINGS["llm"]["max_history_messages"],
    )
    llm["temperature"] = _number(
        llm.get("temperature"), "llm.temperature", minimum=0.0, maximum=1.5,
        default=DEFAULT_SETTINGS["llm"]["temperature"],
    )
    llm["timeout_seconds"] = _number(
        llm.get("timeout_seconds"), "llm.timeout_seconds", minimum=5.0, maximum=180.0,
        default=DEFAULT_SETTINGS["llm"]["timeout_seconds"],
    )

    tts = values["tts"]
    tts["checkpoint"] = _string(
        tts.get("checkpoint"), "tts.checkpoint", maximum=200, default=DEFAULT_SETTINGS["tts"]["checkpoint"]
    )
    tts["caption"] = _string(tts.get("caption"), "tts.caption", maximum=500, default=DEFAULT_SETTINGS["tts"]["caption"])
    tts["output_volume_percent"] = _integer(
        tts.get("output_volume_percent"), "tts.output_volume_percent", minimum=0, maximum=200,
        default=DEFAULT_SETTINGS["tts"]["output_volume_percent"],
    )

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
    behavior["ack_delay_seconds"] = _number(
        behavior.get("ack_delay_seconds"), "behavior.ack_delay_seconds", minimum=0.0, maximum=10.0,
        default=DEFAULT_SETTINGS["behavior"]["ack_delay_seconds"],
    )
    if behavior.get("backchannel_enabled") is not True:
        raise ValueError("Backchannels are required in the MVP and cannot be disabled")
    behavior["backchannel_enabled"] = True
    return values


def save_settings(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload, dict):
        current_settings = None
        for section in ("vad", "llm"):
            if section not in payload:
                # Older clients must not overwrite settings sections they cannot display.
                if current_settings is None:
                    current_settings = get_dashboard_settings()
                payload = {**payload, section: current_settings[section]}
        behavior = payload.get("behavior")
        if isinstance(behavior, dict) and "ack_delay_seconds" not in behavior:
            if current_settings is None:
                current_settings = get_dashboard_settings()
            # A client predating the configurable acknowledgement delay must
            # preserve the saved value when it updates other behavior fields.
            payload = {
                **payload,
                "behavior": {
                    **behavior,
                    "ack_delay_seconds": current_settings["behavior"]["ack_delay_seconds"],
                },
            }
        tts = payload.get("tts")
        if isinstance(tts, dict) and "output_volume_percent" not in tts:
            if current_settings is None:
                current_settings = get_dashboard_settings()
            # A client predating output-volume control must preserve its saved value.
            payload = {
                **payload,
                "tts": {
                    **tts,
                    "output_volume_percent": current_settings["tts"]["output_volume_percent"],
                },
            }
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
