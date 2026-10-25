"""Voice-owned permits and independent v2 stopping for one fixed doll.

The MCP server lives in this process so the final HTTP boundary can recheck the
actual voice owner. No model-supplied session, URL, epoch or revision is trusted.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import os
import threading
import time
import uuid
from typing import Any, Callable

import httpx

from .tail_v2 import TailControlError, DeviceEpochChanged, V2Device, duration_ms, valid_uuid

STOP_DEADLINE_SECONDS = 4.5  # Includes GET/retries; leaves margin inside Web's five seconds.
START_DEADLINE_SECONDS = 6.0


def stop_state(status="disabled", epoch=None, revision=None):
    return {"status": status, "device_epoch": epoch, "stop_revision": revision, "feedback": "command_only"}


@dataclass(eq=False)
class TailLease:
    coordinator: TailCoordinator
    session_id: str
    generation: int
    turn_id: str
    epoch: int
    owns: Callable[[], bool]
    end_session: Callable[[], None] = lambda: None
    revoked: bool = False
    attempted: bool = False
    dispatch_allowed: bool = False
    dispatched: bool = False
    duration: int = 0
    playback_owned: bool = False
    playback_enabled: bool = False
    playback_active: bool = False
    playback_started: bool = False
    playback_terminal: bool = False
    inhibit_playback: Callable[[], None] = lambda: None

    async def call_tap(self, callback, duration_seconds, *, playback=False):
        if self.playback_owned and not playback:
            raise TailControlError("Voice motion is owned by actual audio playback")
        self.coordinator.check_start(self)
        if self.attempted:
            raise TailControlError("This turn already attempted a start; do not retry")
        self.duration = duration_ms(duration_seconds)
        self.attempted = True
        self.dispatch_allowed = True
        try:
            return await callback()
        finally:
            self.dispatch_allowed = False

    async def stop(self):
        return await self.coordinator.stop(self.session_id, self)


_CURRENT_LEASE: ContextVar[TailLease | None] = ContextVar("tail_lease", default=None)


def current_tail_lease():
    return _CURRENT_LEASE.get()


@contextmanager
def tail_lease_context(lease):
    token = _CURRENT_LEASE.set(lease)
    try:
        yield
    finally:
        _CURRENT_LEASE.reset(token)


@dataclass
class StopTicket:
    serial: int
    session_id: str
    deadline: float
    epoch: str | None
    used: bool
    task: asyncio.Task | None = None
    disarm: bool = False


class TailCoordinator:
    def __init__(self, *, device=None, enabled=None, clock=time.monotonic):
        self._lock = threading.RLock()
        # Environment alone cannot authorize: every start also proves the v2
        # contract, exclusive mode, device permission and calibration over HTTP.
        self._enabled = enabled or (lambda: os.getenv("TAIL_CONTROL_ENABLED", "false").lower() in {"1", "true", "yes", "on"})
        self._device = device
        self._origin = os.getenv("TAIL_DEVICE_URL", "")
        self._clock = clock
        self._epoch = 0
        self._owner: str | None = None
        self._state = None
        self._device_epoch = None
        self._used = False
        self._busy = False
        self._blocked = False
        self._ticket: StopTicket | None = None
        self._results: OrderedDict[str, dict] = OrderedDict()
        self._reservations = deque()
        self._background = set()
        self._prepared_owner = None

    def lease(self, session_id, generation, turn_id, owns, end_session=lambda: None):
        with self._lock:
            return TailLease(self, session_id, generation, turn_id, self._epoch, owns, end_session)

    def check(self, lease):
        # Session code takes its lock before ours; never invert that order.
        if not lease.owns():
            raise TailControlError("The voice session/turn is no longer active")
        with self._lock:
            if (lease.coordinator is not self or lease.revoked or lease.epoch != self._epoch
                    or self._blocked or self._ticket is not None):
                raise TailControlError("Stopped/stale request or device stop unconfirmed")
            if not self._enabled() or not valid_uuid(lease.session_id):
                raise TailControlError("Tail control is disabled or session invalid")
            if (self._owner not in (None, lease.session_id)
                    or self._prepared_owner not in (None, lease.session_id)):
                raise TailControlError("The fixed doll belongs to another active conversation")

    def check_start(self, lease):
        self.check(lease)
        with self._lock:
            if lease.playback_owned and (not lease.playback_enabled or not lease.playback_active
                                         or lease.playback_terminal):
                raise TailControlError("Actual audio playback is not active or enabled")

    def _transport(self):
        if self._device is None:
            self._device = V2Device(self._origin)
        return self._device

    def _remember(self, state):
        if self._device_epoch is not None and state["device_epoch"] != self._device_epoch:
            self._blocked = True
            raise DeviceEpochChanged("Device reboot detected; end the conversation, no automatic reopen")
        if self._state is not None and state["revision"] < self._state["revision"]:
            self._blocked = True
            raise TailControlError("Device revision moved backwards")
        self._device_epoch = state["device_epoch"]
        self._state = state

    @staticmethod
    def _body(state, **extra):
        return {"device_epoch": state["device_epoch"], "expected_revision": state["revision"],
                "command_id": str(uuid.uuid4()), **extra}

    @staticmethod
    def _known_start_rejection(exc):
        if not isinstance(exc, httpx.HTTPStatusError):
            return False
        codes = {400: {"invalid_request"}, 403: {"start_disabled"},
                 409: {"stale_epoch", "stale_revision", "invalid_phase", "session_mismatch", "mode_not_ready"},
                 429: {"budget_exceeded"}, 503: {"not_calibrated"}}
        try:
            value = exc.response.json()
        except ValueError:
            return False
        return (isinstance(value, dict) and set(value) == {"error"}
                and isinstance(value["error"], str)
                and value["error"] in codes.get(exc.response.status_code, set()))

    def preparation_enabled(self):
        return self._enabled() and os.getenv("TAIL_SESSION_PREPARE_ENABLED", "false").lower() in {"1", "true", "yes", "on"}

    async def prepare_session(self, lease):
        """Only new explicit voice-session creation calls this; never an LLM tool."""
        self.check(lease)
        with self._lock:
            if self._busy or self._owner is not None or self._prepared_owner is not None:
                raise TailControlError("Previous conversation still owns the device")
            self._busy = True
            self._owner = self._prepared_owner = lease.session_id
        try:
            async with asyncio.timeout(START_DEADLINE_SECONDS):
                def guard():
                    self.check(lease)
                state = await self._transport().request("state", guard=guard)
                guard()
                if ("session_prepare" not in state["capabilities"] or not state["calibrated"]
                        or state["phase"] != "closed" or state["motion"] != "idle"
                        or state.get("permission_session_id") is not None):
                    raise TailControlError("Device cannot prepare a new conversation")
                with self._lock:
                    # A new explicit session may adopt a new boot only with no old
                    # owner/ticket/unconfirmed stop. check() fenced those above.
                    self._device_epoch = state["device_epoch"]
                    self._state = state
                def mutation_guard():
                    guard()
                    with self._lock:
                        self._used = True
                        self._results[lease.session_id] = stop_state("unconfirmed", self._device_epoch)
                state = await self._transport().request("prepare", self._body(state, session_id=lease.session_id), guard=mutation_guard)
                guard()
                with self._lock:
                    self._remember(state)
                return state
        except BaseException:
            ticket = self.revoke_session(lease.session_id, release=True)
            if ticket is not None:
                await asyncio.shield(self.schedule_stop(ticket))
            raise
        finally:
            with self._lock:
                self._busy = False

    async def tap(self, lease, seconds):
        self.check_start(lease)
        duration = duration_ms(seconds)
        with self._lock:
            if not lease.dispatch_allowed or lease.dispatched or lease.duration != duration:
                raise TailControlError("MCP start has no current explicit-request permit")
            lease.dispatched = True
            if self._busy:
                raise TailControlError("An open/tap request is already in flight")
            now = self._clock()
            while self._reservations and self._reservations[0][0] <= now - 60:
                self._reservations.popleft()
            if sum(ms for _, ms in self._reservations) + duration > 10000:
                raise TailControlError("60-second movement budget exceeded")
            self._busy = True
            self._owner = lease.session_id
        mutation_sent = False
        try:
            async with asyncio.timeout(START_DEADLINE_SECONDS):
                def guard():
                    self.check_start(lease)
                    if not lease.dispatch_allowed:
                        raise TailControlError("The MCP caller cancelled its start permit")
                def mutation_guard():
                    nonlocal mutation_sent
                    guard()
                    mutation_sent = True  # Called at the final HTTP send boundary.
                state = await self._transport().request("state", guard=guard)
                guard()
                with self._lock:
                    if (self._used and self._prepared_owner == lease.session_id
                            and state["phase"] == "closed" and self._state is not None
                            and state["revision"] != self._state["revision"]):
                        raise TailControlError("Device stopped outside this conversation; no automatic reopen")
                    self._remember(state)
                if not state["start_enabled"] or not state["calibrated"] or state["motion"] != "idle":
                    raise TailControlError("Device start permission, calibration or idle state missing")
                if self._prepared_owner == lease.session_id and state.get("permission_session_id") != lease.session_id:
                    raise TailControlError("Conversation permission was revoked on the device")
                if state["phase"] == "closed":
                    # A local stop cannot silently authorize a new open in this session.
                    if self._used and self._prepared_owner != lease.session_id:
                        raise TailControlError("Device closed unexpectedly; no automatic reopen")
                    guard()
                    with self._lock:
                        self._used = True
                        self._results[lease.session_id] = stop_state("unconfirmed", self._device_epoch)
                    state = await self._transport().request("open", self._body(state, session_id=lease.session_id), guard=mutation_guard)
                    mutation_sent = False
                    guard()
                    with self._lock:
                        self._remember(state)
                elif not self._used or state["session_id"] != lease.session_id:
                    raise TailControlError("Unknown or mismatched device session; no takeover")
                guard()
                with self._lock:
                    # Conservative reservation: unknown starts are never refunded.
                    self._reservations.append((self._clock(), duration))
                state = await self._transport().request("tap", self._body(state, session_id=lease.session_id,
                                                                           duration_ms=duration), guard=mutation_guard)
                mutation_sent = False
                guard()
                with self._lock:
                    self._remember(state)
                return state
        except BaseException as exc:
            if lease.playback_owned and mutation_sent and not self._known_start_rejection(exc):
                lease.inhibit_playback()
            if isinstance(exc, DeviceEpochChanged):
                lease.end_session()
            ticket = self.revoke_session(lease.session_id, lease)
            if ticket is not None:
                self.schedule_stop(ticket)
            raise
        finally:
            with self._lock:
                self._busy = False

    async def status(self, lease):
        self.check(lease)
        state = await self._transport().request("state", guard=lambda: self.check(lease))
        self.check(lease)
        try:
            with self._lock:
                self._remember(state)
        except TailControlError as exc:
            if isinstance(exc, DeviceEpochChanged):
                lease.end_session()
            ticket = self.revoke_session(lease.session_id, lease)
            if ticket:
                self.schedule_stop(ticket)
            raise
        return state

    def stop_snapshot(self, session_id=None):
        with self._lock:
            return dict(self._results.get(session_id, stop_state()))

    def revoke_session(self, session_id, lease=None, *, release=False):
        with self._lock:
            releasing = release and self._prepared_owner == session_id
            if releasing and self._owner is None:
                self._owner = session_id
                self._used = True
            if releasing and self._ticket is not None and self._ticket.session_id == session_id:
                self._ticket.disarm = True
                return self._ticket
            if lease is not None:
                lease.revoked = True
                lease.playback_active = False
                lease.playback_terminal = True
                if lease.epoch != self._epoch and not releasing:
                    return None
            if self._owner != session_id:
                return None
            if self._ticket is not None:
                return self._ticket
            self._epoch += 1  # Fence immediately, before cancellation/scheduling/HTTP.
            ticket = StopTicket(self._epoch, session_id, self._clock() + STOP_DEADLINE_SECONDS,
                                self._device_epoch, self._used)
            ticket.disarm = releasing
            self._ticket = ticket
            self._results[session_id] = stop_state("unconfirmed" if ticket.used else "disabled", ticket.epoch)
            return ticket

    def schedule_stop(self, ticket):
        with self._lock:
            if ticket.task is None:
                ticket.task = asyncio.create_task(self.finish_stop(ticket), name="tail-v2-independent-stop")
                self._background.add(ticket.task)
                ticket.task.add_done_callback(self._background.discard)
            return ticket.task

    async def _stop_request(self, ticket, action, body=None):
        def guard():
            with self._lock:
                if self._ticket is not ticket:
                    raise TailControlError("Old stop cannot affect a new conversation")
        remaining = ticket.deadline - self._clock()
        if remaining <= 0:
            raise TimeoutError
        return await self._transport().request(action, body, timeout=min(1.0, remaining), guard=guard)

    async def finish_stop(self, ticket):
        result = stop_state("unconfirmed", ticket.epoch)
        if not ticket.used:
            result = stop_state()
        else:
            try:
                async with asyncio.timeout(max(0, ticket.deadline - self._clock())):
                    # No start queue / LLM wait here. A stale revision is the ONLY
                    # retry case; all retries have fresh IDs and the same epoch.
                    with self._lock:
                        state = self._state
                    for _ in range(8):
                        if state is None:
                            state = await self._stop_request(ticket, "state")
                        if state["device_epoch"] != ticket.epoch:
                            raise TailControlError("Epoch changed while stopping")
                        body = self._body(state)
                        try:
                            action = "disarm" if ticket.disarm else "stop"
                            ack = await self._stop_request(ticket, action, body)
                        except httpx.HTTPStatusError as exc:
                            if exc.response.status_code != 409:
                                raise
                            state = await self._stop_request(ticket, "state")
                            continue
                        state = await self._stop_request(ticket, "state")
                        if (state["device_epoch"] != ticket.epoch or state["revision"] < ack["revision"]
                                or state["phase"] != "closed" or state["motion"] != "idle"
                                or state["session_id"] is not None):
                            raise TailControlError("Fresh GET did not confirm command stop")
                        with self._lock:
                            if ticket.disarm and action != "disarm":
                                continue  # Session ended while ordinary stop was in flight.
                            if ticket.disarm and (state["start_enabled"] or state.get("permission_session_id") is not None):
                                raise TailControlError("Fresh GET did not confirm disarm")
                            result = stop_state("command_stopped", ticket.epoch, ack["revision"])
                            # Commit under the same lock as the upgrade check. A
                            # concurrent DELETE either upgrades this ticket or creates
                            # a new disarm after the ordinary stop completes.
                            return self._complete_stop(ticket, result)
            except (Exception, asyncio.CancelledError):
                pass  # Never turn an uncertain stop into disabled/success.
        return self._complete_stop(ticket, result)

    def _complete_stop(self, ticket, result):
        with self._lock:
            if self._ticket is ticket:
                self._results[ticket.session_id] = result
                self._results.move_to_end(ticket.session_id)
                while len(self._results) > 256:
                    self._results.popitem(last=False)
                self._blocked = self._blocked or result["status"] == "unconfirmed"
                if ticket.disarm:
                    self._prepared_owner = None
                self._owner = None
                self._used = False
                self._ticket = None
        return dict(result)

    async def stop(self, session_id=None, lease=None):
        if lease is not None:
            with self._lock:
                if lease.epoch != self._epoch or lease.revoked:
                    return self.stop_snapshot(session_id)
        # No owner is inferred from a stale model call without a trusted lease.
        if session_id is None:
            return stop_state()
        ticket = self.revoke_session(session_id, lease)
        if ticket is None:
            return self.stop_snapshot(session_id)
        return await asyncio.shield(self.schedule_stop(ticket))

    async def wait_stopped(self, session_id):
        with self._lock:
            ticket = self._ticket if self._ticket and self._ticket.session_id == session_id else None
        if ticket is not None:
            await asyncio.shield(self.schedule_stop(ticket))
        return self.stop_snapshot(session_id)


_COORDINATOR = None


def get_tail_coordinator():
    global _COORDINATOR
    if _COORDINATOR is None:
        _COORDINATOR = TailCoordinator()
    return _COORDINATOR
