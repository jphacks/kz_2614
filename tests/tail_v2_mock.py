"""Independent executable v2 device model; never contacts a real device.

Happy-Emori can consume fixtures/tail_v2.json and replay these requests against
firmware. This model is an oracle for PC tests, not firmware acceptance evidence.
"""
from copy import deepcopy
import json
from pathlib import Path
import threading
import uuid

import httpx

FIXTURE = json.loads((Path(__file__).parent / "fixtures/tail_v2.json").read_text())


class MockFollower:
    def __init__(self):
        self.state = deepcopy(FIXTURE["state"])
        self.requests = []
        self.accepted = []
        self.spent = 0
        self.lock = threading.RLock()
        self.before = None
        self.after = None

    def expire(self):
        with self.lock:
            if self.state["motion"] == "tapping":
                self.state["motion"] = "idle"
                self.state["revision"] += 1

    def reboot(self):
        with self.lock:
            self.state = deepcopy(FIXTURE["state"])
            self.state["device_epoch"] = uuid.uuid4().hex
            self.spent = 0

    async def handle(self, request):
        action = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content) if request.content else None
        self.requests.append((action, deepcopy(body)))
        if self.before:
            await self.before(action, body)
        response = self.apply(action, body)
        if self.after:
            replacement = await self.after(action, body, response)
            if replacement is not None:
                response = replacement
        return response

    def apply(self, action, body):
        with self.lock:
            s = self.state
            if action == "state" and body is None:
                return httpx.Response(200, json=deepcopy(s))
            def error(code, message):
                return httpx.Response(code, json={"error": message})
            if action not in {"open", "tap", "stop"}:
                return error(409, "mode_not_ready")  # Legacy motion paths locked out.
            keys = {"device_epoch", "expected_revision", "command_id"}
            if action != "stop":
                keys.add("session_id")
            if action == "tap":
                keys.add("duration_ms")
            if not isinstance(body, dict) or set(body) != keys or type(body.get("expected_revision")) is not int:
                return error(400, "invalid_request")
            if body["device_epoch"] != s["device_epoch"]:
                return error(409, "stale_epoch")
            if body["expected_revision"] != s["revision"]:
                return error(409, "stale_revision")
            if action != "stop":
                if not s["start_enabled"]:
                    return error(403, "start_disabled")
                if not s["calibrated"]:
                    return error(503, "not_calibrated")
                if s["control_mode"] != "v2_exclusive":
                    return error(409, "mode_not_ready")
                if s["motion"] != "idle" or s["phase"] != ("closed" if action == "open" else "open"):
                    return error(409, "invalid_phase")
                if action == "tap":
                    if s["session_id"] != body["session_id"]:
                        return error(409, "session_mismatch")
                    if type(body["duration_ms"]) is not int or not 500 <= body["duration_ms"] <= 5000:
                        return error(400, "invalid_request")
                    if self.spent + body["duration_ms"] > 10000:
                        return error(429, "budget_exceeded")
                    self.spent += body["duration_ms"]
                    s["motion"] = "tapping"
                s["phase"] = "open"
                s["session_id"] = body["session_id"]
            else:
                s.update(phase="closed", session_id=None, motion="idle")
            s["revision"] += 1
            self.accepted.append((action, deepcopy(body)))
            return httpx.Response(200, json={**deepcopy(s), "command_id": body["command_id"]})
