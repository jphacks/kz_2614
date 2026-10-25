"""Strict PC implementation of docs/api/mvp-tail-control-v2.md (no v1 fallback)."""
from __future__ import annotations

import ipaddress
import json
import math
import re
import uuid
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

LIMITS = {"min_duration_ms": 500, "max_duration_ms": 5000, "rate_hz": 0.3,
          "window_ms": 60000, "budget_ms": 10000}
MAX_REVISION = 9007199254740991


class TailControlError(RuntimeError):
    pass


class DeviceEpochChanged(TailControlError):
    pass


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise TailControlError("Duplicate JSON key")
        value[key] = item
    return value


def valid_uuid(value: Any) -> bool:
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except ValueError:
        return False


def duration_ms(value: Any) -> int:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0.5 <= value <= 5:
        raise TailControlError("duration_seconds must be a finite number between 0.5 and 5")
    return round(value * 1000)


def validate_state(value: Any) -> dict[str, Any]:
    required = {"protocol_version", "capabilities", "control_mode", "device_epoch", "revision", "session_id",
                "phase", "motion", "start_enabled", "calibrated", "feedback", "limits"}
    if not isinstance(value, dict) or not required <= value.keys():
        raise TailControlError("Missing v2 state object")
    if (type(value.get("protocol_version")) is not int or value["protocol_version"] != 2
            or not isinstance(value.get("capabilities"), list)
            or any(not isinstance(item, str) for item in value["capabilities"])
            or "revision_fence" not in value["capabilities"]
            or value.get("control_mode") != "v2_exclusive"
            or value.get("feedback") != "command_only"):
        raise TailControlError("Device does not implement the exclusive v2 revision fence")
    if (not isinstance(value.get("device_epoch"), str)
            or re.fullmatch(r"[0-9a-f]{32}", value["device_epoch"]) is None
            or type(value.get("revision")) is not int
            or not 0 <= value["revision"] <= MAX_REVISION):
        raise TailControlError("Invalid device epoch/revision")
    if (value.get("phase") not in ("open", "closed") or value.get("motion") not in ("idle", "tapping")
            or type(value.get("start_enabled")) is not bool or type(value.get("calibrated")) is not bool):
        raise TailControlError("Invalid device state")
    if value["phase"] == "closed":
        if value.get("session_id") is not None or value["motion"] != "idle":
            raise TailControlError("Inconsistent closed state")
    elif not valid_uuid(value.get("session_id")):
        raise TailControlError("Invalid open session")
    if "session_prepare" in value["capabilities"]:
        if "permission_session_id" not in value or (value["permission_session_id"] is not None
                and not valid_uuid(value["permission_session_id"])):
            raise TailControlError("Invalid session preparation permission")
    limits = value.get("limits")
    if (not isinstance(limits, dict) or set(limits) != set(LIMITS)
            or any(type(limits[key]) not in (int, float) if key == "rate_hz"
                   else type(limits[key]) is not int for key in LIMITS)
            or limits != LIMITS):
        raise TailControlError("Device limits differ from the shared v2 contract")
    return dict(value)


def validate_ack(value: Any, action: str, body: dict[str, Any]) -> dict[str, Any]:
    state = validate_state(value)
    if (state.get("command_id") != body["command_id"]
            or state["device_epoch"] != body["device_epoch"]
            or state["revision"] != body["expected_revision"] + 1):
        raise TailControlError("Device acknowledgement does not match this command")
    if action == "prepare":
        if ("session_prepare" not in state["capabilities"] or state["phase"] != "closed"
                or not state["start_enabled"] or not state["calibrated"]
                or state.get("permission_session_id") != body["session_id"]):
            raise TailControlError("Preparation acknowledgement does not match this session")
    elif action == "disarm":
        if (state["phase"] != "closed" or state["start_enabled"]
                or state.get("permission_session_id") is not None):
            raise TailControlError("Disarm acknowledgement still has permission")
    elif action == "stop":
        if state["phase"] != "closed":
            raise TailControlError("Stop acknowledgement is not closed")
    elif (state["phase"] != "open" or state["session_id"] != body["session_id"]
          or state["motion"] != ("tapping" if action == "tap" else "idle")
          or not state["start_enabled"] or not state["calibrated"]):
        raise TailControlError("Start acknowledgement does not match this command")
    return state


def fixed_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/")):
        raise TailControlError("Configure one fixed private Follower HTTP origin")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        if parsed.hostname != "localhost" and not parsed.hostname.endswith(".local"):
            raise TailControlError("Follower origin must be a private IP or .local host")
    else:
        if not (address.is_private or address.is_loopback or address.is_link_local):
            raise TailControlError("Follower origin must be private")
    return value.rstrip("/")


class V2Device:
    """The origin is frozen at construction, never accepted from Web/model arguments."""
    def __init__(self, origin: str, *, transport=None):
        self.origin = fixed_origin(origin)
        self.transport = transport

    async def request(self, action: str, body: dict[str, Any] | None = None, *,
                      timeout: float = 2.0, guard: Callable[[], None] = lambda: None):
        async with httpx.AsyncClient(base_url=self.origin, trust_env=False, follow_redirects=False,
                                     transport=self.transport, timeout=httpx.Timeout(timeout, connect=min(1, timeout))) as client:
            # Revalidate the voice owner immediately before the HTTP boundary.
            guard()
            response = await client.request("GET" if body is None else "POST", "/api/v2/tail/" + action,
                                            **({"json": body} if body is not None else {}))
            response.raise_for_status()
            if response.status_code != 200 or len(response.content) > 16384:
                raise TailControlError("Invalid v2 HTTP response")
            value = json.loads(response.content, object_pairs_hook=unique_object,
                               parse_constant=lambda _: (_ for _ in ()).throw(TailControlError("Non-finite JSON")))
            return validate_state(value) if body is None else validate_ack(value, action, body)
