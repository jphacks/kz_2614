"""Desired tail frequency on the PC; never an acknowledgement from hardware."""

from __future__ import annotations

import json
import math
import os
import tempfile
import threading
from numbers import Real
from pathlib import Path
from typing import Any


DEFAULT_RATE_HZ = 0.6
MIN_RATE_HZ = 0.3
MAX_RATE_HZ = 1.0
_lock = threading.RLock()


def _settings_path() -> Path:
    configured = os.getenv("NENGITSUNE_TAIL_SETTINGS_PATH", "").strip()
    return Path(configured).expanduser() if configured else Path(__file__).resolve().parent / "data" / "tail_settings.json"


def _validated_rate(value: Any) -> float:
    try:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError
        rate = float(value)
        if not math.isfinite(rate) or not MIN_RATE_HZ <= rate <= MAX_RATE_HZ:
            raise ValueError
        return rate
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("トントンの頻度は0.3〜1.0Hzの数値で指定してください。") from exc


def _result(rate: float) -> dict[str, Any]:
    return {
        "rate_hz": rate,
        "interval_ms": int(round(1000 / rate)),
        "device_confirmed": False,
        "applies_to": "configuration",
    }


def get_tail_frequency() -> dict[str, Any]:
    with _lock:
        path = _settings_path()
        if not path.is_file():
            return _result(DEFAULT_RATE_HZ)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or set(payload) != {"rate_hz"}:
                raise ValueError
            return _result(_validated_rate(payload["rate_hz"]))
        except (OSError, ValueError, TypeError) as exc:
            raise RuntimeError("保存したトントンの頻度を読み取れません。") from exc


def set_tail_frequency(rate_hz: float) -> dict[str, Any]:
    rate = _validated_rate(rate_hz)
    with _lock:
        path = _settings_path()
        temporary_path: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=".tail-settings-", suffix=".json", delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump({"rate_hz": rate}, temporary, ensure_ascii=False, allow_nan=False)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, path)
        except OSError as exc:
            raise RuntimeError("トントンの頻度を保存できません。") from exc
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        return _result(rate)
