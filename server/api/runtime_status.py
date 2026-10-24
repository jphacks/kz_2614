"""Read-only snapshots of configured and observed voice component state.

Snapshots never contact services or load models. A successful observation is
timestamped history, not a claim that an external service is still reachable.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _model_label(value: Any) -> str | None:
    """Keep model names useful without returning a private absolute model path."""
    if value is None:
        return None
    label = str(value).strip()
    if not label:
        return None
    if PureWindowsPath(label).is_absolute() or "\\" in label:
        label = PureWindowsPath(label).name
    elif PurePosixPath(label).is_absolute() or label.startswith(("~/", "./", "../")):
        label = PurePosixPath(label).name
    return "".join(character for character in label if character.isprintable())[:200] or None


def component_configuration(
    model: Any, *, device: Any = None, precision: Any = None, backend: Any = None,
) -> dict[str, Any]:
    return {
        "model": _model_label(model),
        "device": str(device)[:40] if device is not None else None,
        "precision": str(precision)[:40] if precision is not None else None,
        "backend": str(backend)[:40] if backend is not None else None,
    }


class RuntimeObservation:
    """Small thread-safe state owned by one existing inference client.

    Only whitelisted descriptors and exception class names are retained; neither
    inference inputs nor exception messages can enter a status response.
    """

    def __init__(self, configuration: dict[str, Any], *, external: bool = False) -> None:
        self.configuration = dict(configuration)
        self.external = external
        self._lock = threading.Lock()
        self._inflight = 0
        self._loading = 0
        self._loaded = False
        self._ready = False
        self._observed: dict[str, Any] = {"model": None, "device": None, "precision": None}
        self._last_success_at: str | None = None
        self._last_error_at: str | None = None
        self._error_type: str | None = None

    def begin(self, *, loading: bool = False) -> None:
        with self._lock:
            self._inflight += 1
            if loading:
                self._loading += 1

    def finish(self, *, loading: bool = False) -> None:
        with self._lock:
            self._inflight = max(0, self._inflight - 1)
            if loading:
                self._loading = max(0, self._loading - 1)

    def _record_descriptors(self, values: dict[str, Any]) -> None:
        for key in ("model", "device", "precision"):
            if key in values:
                value = values[key]
                self._observed[key] = _model_label(value) if key == "model" else (
                    str(value)[:40] if value is not None else None
                )

    def loaded(self, **observed: Any) -> None:
        with self._lock:
            self._loaded = True
            self._record_descriptors(observed)

    def success(self, **observed: Any) -> None:
        with self._lock:
            self._ready = True
            self._last_success_at = _now()
            self._error_type = None
            self._record_descriptors(observed)

    def failure(self, error: BaseException) -> None:
        with self._lock:
            self._error_type = type(error).__name__[:80]
            self._last_error_at = _now()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = (
                "loading" if self._loading else "processing" if self._inflight else
                "error" if self._error_type else "ready" if self._ready else
                "loaded" if self._loaded else "unverified"
            )
            detail = {
                "unverified": "まだ実処理を確認していません。設定値だけでは利用可能と判定しません。",
                "loading": "モデルを読み込んでいます。",
                "processing": "現在、音声処理を実行または待機しています。",
                "loaded": "モデルの読み込みを確認済みです。推論成功はまだ確認していません。",
                "ready": "直近の実処理は成功しました。現在の接続確認を実行した結果ではありません。",
                "error": "直近の実処理でエラーが発生しました。詳細はサーバー側で確認してください。",
            }[state]
            if self.external:
                detail += " 外部サービスの実行デバイス・精度は未確認です。"
            return {
                **self._observed,
                "backend": self.configuration.get("backend"),
                "configured": dict(self.configuration),
                "state": state,
                "detail": detail,
                "loaded": self._loaded,
                "last_success_at": self._last_success_at,
                "last_error_at": self._last_error_at,
                "error_type": self._error_type,
            }


def _configured_components() -> dict[str, dict[str, Any]]:
    # Lazy imports keep observation usable while these clients are importing.
    from ..ai.llm.lm_studio import VoiceLLMConfig
    from ..ai.stt.whisper import WhisperConfig
    from ..ai.tts.irodori import TTSConfig

    factories = {
        "stt": (WhisperConfig.from_env, lambda config: component_configuration(
            config.model_id, device=config.device, precision=config.compute_type, backend="faster_whisper",
        )),
        "llm": (VoiceLLMConfig.from_env, lambda config: component_configuration(
            config.model, backend="lm_studio",
        )),
        "tts": (TTSConfig.from_env, lambda config: component_configuration(
            config.checkpoint, device=config.model_device, precision=config.model_precision, backend=config.backend,
        )),
    }
    result: dict[str, dict[str, Any]] = {}
    for name, (factory, describe) in factories.items():
        try:
            result[name] = {"configuration": describe(factory()), "error_type": None}
        except Exception as error:
            result[name] = {
                "configuration": component_configuration(None),
                "error_type": type(error).__name__[:80],
            }
    return result


def get_runtime_status(manager: Any | None = None) -> dict[str, Any]:
    """Describe next-session configuration separately from live client observations."""
    configured = _configured_components()
    live = manager.runtime_snapshot() if manager is not None else {
        "active_sessions": 0, "components": {},
    }
    result: dict[str, Any] = {
        "checked_at": _now(),
        "active_sessions": live.get("active_sessions", 0),
        "observation_only": True,
        "components": {},
    }
    for name in ("stt", "llm", "tts"):
        selection = configured[name]["configuration"]
        active = []
        for row in live.get("components", {}).get(name, []):
            entry = dict(row)
            entry["settings_pending"] = entry.get("configured") != selection
            active.append(entry)
        states = {entry.get("state", "unverified") for entry in active}
        state = next((candidate for candidate in (
            "loading", "processing", "error", "loaded", "ready",
        ) if candidate in states), "unverified")
        if configured[name]["error_type"]:
            state = "error"
        result["components"][name] = {
            "configured": selection,
            "active": active,
            "state": state,
            "settings_pending": any(entry["settings_pending"] for entry in active),
            "error_type": configured[name]["error_type"],
            "detail": (
                "次の会話用の設定を読み込めません。" if configured[name]["error_type"] else
                "稼働中の会話がないため、次の会話用の設定を表示しています。" if not active else
                "稼働中の会話で観測した状態です。成功時刻と次の会話用の設定を確認してください。"
            ),
        }
    return result
