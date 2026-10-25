"""Real VoiceSessions loop + mock v2 device: DELETE, expiry, interrupt, failures."""
import asyncio
import time
import threading
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx

from server.api.server import DashboardHandler
from server.ai.llm.tail_control import TailCoordinator
from server.ai.llm.tail_v2 import TailControlError, V2Device
from server.ai.voice.sessions import VoiceSessions, SESSION_TTL_SECONDS
from tail_v2_mock import MockFollower


class VoiceTailV2Tests(unittest.TestCase):
    def setUp(self):
        self.device = MockFollower()
        self.coordinator = TailCoordinator(enabled=lambda: True,
            device=V2Device("http://127.0.0.1:1", transport=httpx.MockTransport(self.device.handle)))
        self.manager = VoiceSessions(tail_coordinator=self.coordinator)
        pipeline = SimpleNamespace(close=AsyncMock())
        with patch.object(self.manager, "_create_pipeline", AsyncMock(return_value=pipeline)):
            created = self.manager.create_session()
        self.sid = created["session_id"]
        self.turn = str(uuid.uuid4())
        self.session, self.generation = self.manager.begin_turn(self.sid, self.turn)
        self.lease = self.session.tail_lease

    def tearDown(self):
        if self.session.active:
            self.manager.stop_session(self.sid)
        self.manager._submit(self.coordinator.wait_stopped(self.sid)).result(timeout=5)
        self.manager._loop.call_soon_threadsafe(self.manager._loop.stop)
        self.manager._loop_thread.join(timeout=3)
        self.assertFalse(self.manager._loop_thread.is_alive())

    def tap(self, lease=None):
        lease = lease or self.lease
        return self.manager._submit(lease.call_tap(lambda: self.coordinator.tap(lease, 1), 1)).result(timeout=2)

    def stopped(self):
        return self.manager._submit(self.coordinator.wait_stopped(self.sid)).result(timeout=5)

    def test_voice_delete_returns_independent_tail_result_and_invalidates_immediately(self):
        self.tap()
        ready = threading.Event()
        async def inference():
            self.session.task = asyncio.current_task()
            ready.set()
            await asyncio.sleep(60)
        inference_future = self.manager._submit(inference())
        self.assertTrue(ready.wait(timeout=1))
        handler = object.__new__(DashboardHandler)
        handler.path = f"/api/v1/voice/sessions/{self.sid}"
        handler._send_json = Mock()
        started = time.monotonic()
        with patch("server.api.server._voice_sessions", return_value=self.manager), patch("server.api.server._audio_streams"):
            handler.do_DELETE()
        self.assertLess(time.monotonic() - started, 5)
        response = handler._send_json.call_args.args[1]
        self.assertEqual(response["state"], "stopped")
        self.assertEqual(response["session_id"], self.sid)
        self.assertFalse(response["audio_device_stop_confirmed"])
        self.assertEqual(response["tail_control"]["status"], "command_stopped")
        self.assertEqual(response["tail_control"]["feedback"], "command_only")
        self.assertFalse(self.session.active)
        self.assertTrue(self.lease.revoked)
        # Cancellation acknowledgement may arrive just after the device response.
        # The stop path must not wait for native inference to acknowledge it.
        with self.assertRaises(Exception):
            inference_future.result(timeout=1)
        with self.assertRaises(TailControlError):
            self.tap()

    def test_delete_never_authorized_reports_disabled_without_device_http(self):
        response = self.manager.stop_session(self.sid)
        self.assertEqual(response["tail_control"]["status"], "disabled")
        self.assertEqual(self.device.requests, [])

    def test_delete_unknown_stop_is_not_audio_success_or_disabled(self):
        self.tap()
        async def drop(action, body):
            if action == "stop":
                raise httpx.ReadTimeout("offline")
        self.device.before = drop
        response = self.manager.stop_session(self.sid)
        self.assertEqual(response["state"], "stopped")
        self.assertEqual(response["tail_control"]["status"], "unconfirmed")
        self.assertFalse(response["audio_device_stop_confirmed"])

    def test_interrupt_fences_old_turn_keeps_voice_and_new_explicit_turn_can_start(self):
        self.tap()
        self.assertTrue(self.manager.interrupt_turn(self.sid, self.generation, self.turn))
        self.assertEqual(self.stopped()["status"], "command_stopped")
        self.assertTrue(self.session.active)
        self.assertEqual(self.session.generation, self.generation)
        with self.assertRaises(TailControlError):
            self.tap()
        _, generation = self.manager.begin_turn(self.sid, str(uuid.uuid4()))
        self.assertEqual(generation, self.generation)
        self.tap(self.session.tail_lease)
        # A delayed stop from the old MCP turn cannot close the new turn.
        self.manager._submit(self.lease.stop()).result(timeout=1)
        self.assertEqual(self.device.state["motion"], "tapping")

    def test_expiry_closes_device_without_another_user_request(self):
        self.tap()
        with self.manager._lock:
            self.session.busy = False
            self.session.last_activity = time.monotonic() - SESSION_TTL_SECONDS - 1
        # The scheduled expiry tick runs even if clients send nothing further.
        deadline = time.monotonic() + 2
        while self.session.active and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(self.session.active)
        self.assertEqual(self.stopped()["status"], "command_stopped")

    def test_cancel_and_abandon_each_stop_without_waiting_for_llm(self):
        for end in ("cancel", "abandon", "output_failure"):
            self.tap(self.session.tail_lease)
            if end == "cancel":
                self.manager.cancel_stream_turn(self.sid, self.generation)
            elif end == "abandon":
                self.manager.abandon_turn(self.session, turn_id=self.session.active_turn_id)
            else:
                self.manager.stop_tail_for_session(self.sid, self.generation)
            self.assertEqual(self.stopped()["status"], "command_stopped")
            self.session.busy = False
            self.manager.begin_turn(self.sid, str(uuid.uuid4()))

    def test_epoch_change_retires_voice_session(self):
        self.tap()
        self.device.reboot()
        self.session.busy = False
        self.manager.begin_turn(self.sid, str(uuid.uuid4()))
        with self.assertRaises(TailControlError):
            self.tap(self.session.tail_lease)
        self.assertFalse(self.session.active)
        self.assertEqual(self.stopped()["status"], "unconfirmed")


if __name__ == "__main__":
    unittest.main()
