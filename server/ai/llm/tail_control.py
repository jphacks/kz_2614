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

    async def call_tap(self, callback, duration_seconds):
        self.coordinator.check(self)
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
            if self._owner not in (None, lease.session_id):
                raise TailControlError("The fixed doll belongs to another active conversation")

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

    async def tap(self, lease, seconds):
        self.check(lease)
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
        try:
            async with asyncio.timeout(START_DEADLINE_SECONDS):
                def guard():
                    self.check(lease)
                    if not lease.dispatch_allowed:
                        raise TailControlError("The MCP caller cancelled its start permit")
                state = await self._transport().request("state", guard=guard)
                guard()
                with self._lock:
                    self._remember(state)
                if not state["start_enabled"] or not state["calibrated"] or state["motion"] != "idle":
                    raise TailControlError("Device start permission, calibration or idle state missing")
                if state["phase"] == "closed":
                    # A local stop cannot silently authorize a new open in this session.
                    if self._used:
                        raise TailControlError("Device closed unexpectedly; no automatic reopen")
                    guard()
                    with self._lock:
                        self._used = True
                        self._results[lease.session_id] = stop_state("unconfirmed", self._device_epoch)
                    state = await self._transport().request("open", self._body(state, session_id=lease.session_id), guard=guard)
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
                                                                           duration_ms=duration), guard=guard)
                guard()
                with self._lock:
                    self._remember(state)
                return state
        except BaseException as exc:
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

    def revoke_session(self, session_id, lease=None):
        with self._lock:
            if lease is not None:
                lease.revoked = True
                if lease.epoch != self._epoch:
                    return None
            if self._owner != session_id:
                return None
            if self._ticket is not None:
                return self._ticket
            self._epoch += 1  # Fence immediately, before cancellation/scheduling/HTTP.
            ticket = StopTicket(self._epoch, session_id, self._clock() + STOP_DEADLINE_SECONDS,
                                self._device_epoch, self._used)
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
                            ack = await self._stop_request(ticket, "stop", body)
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
                        result = stop_state("command_stopped", ticket.epoch, ack["revision"])
                        break
            except (Exception, asyncio.CancelledError):
                pass  # Never turn an uncertain stop into disabled/success.
        with self._lock:
            if self._ticket is ticket:
                self._results[ticket.session_id] = result
                self._results.move_to_end(ticket.session_id)
                while len(self._results) > 256:
                    self._results.popitem(last=False)
                self._blocked = self._blocked or result["status"] == "unconfirmed"
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
