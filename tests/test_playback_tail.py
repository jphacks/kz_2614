"""Actual PCM playback owns motion; all device traffic uses the v2 mock."""
import asyncio
import httpx
import threading
import time
import uuid
import unittest
from unittest.mock import Mock, patch

from server.ai.llm.tail_v2 import TailControlError
from server.ai.voice.pcm_stream import ServerAudioStreams, _StreamState
import test_tail_voice_v2 as voice_tail


class PlaybackTailTests(unittest.TestCase):
    stopped = voice_tail.VoiceTailV2Tests.stopped
    def setUp(self):
        voice_tail.VoiceTailV2Tests.setUp(self)
        self.streams = ServerAudioStreams(self.manager)
        self.state = _StreamState(self.sid, self.generation, Mock(), stage="llm",
                                  turn_id=self.turn, has_audio=True)
        self.streams._states[self.sid] = self.state

    def tearDown(self):
        self.streams.close()
        voice_tail.VoiceTailV2Tests.tearDown(self)

    def event(self, event):
        return self.streams.playback(self.sid, self.generation, self.turn, event)

    def flush(self):
        self.manager._submit(asyncio.sleep(.03)).result(timeout=2)

    def test_generation_and_explicit_model_permit_do_not_start(self):
        self.flush()
        self.assertEqual(self.device.requests, [])
        with self.assertRaises(TailControlError):
            self.manager._submit(self.lease.call_tap(lambda: self.coordinator.tap(self.lease, 1), 1)).result(2)
        self.assertEqual(self.device.requests, [])

    def test_completed_generation_first_started_one_burst_and_end(self):
        self.session.busy = False  # Completed generation retains ownership.
        self.state.turn_complete = True
        self.event("started"); self.flush()
        self.assertEqual([a for a, _ in self.device.accepted], ["open", "tap"])
        self.assertEqual(self.device.accepted[-1][1]["duration_ms"], 2000)
        self.event("started"); self.flush()
        self.assertEqual(len(self.device.accepted), 2)
        self.device.expire()
        self.event("started"); self.flush()
        self.assertEqual(len(self.device.accepted), 2)  # Never renew after duration.
        self.event("ended")
        self.assertEqual(self.stopped()["status"], "command_stopped")
        self.assertTrue(self.lease.playback_terminal)

    def test_failure_before_start_and_replay_cannot_open(self):
        self.event("failed")
        self.event("started"); self.flush()
        self.assertEqual(self.device.requests, [])
        self.assertTrue(self.lease.revoked)

    def test_model_stop_before_audio_remains_sticky(self):
        self.manager._submit(self.lease.stop()).result(2)
        self.event("started"); self.flush()
        self.assertEqual(self.device.requests, [])

    def test_end_before_late_start_does_not_reopen(self):
        self.state.turn_complete = True
        self.event("ended")
        self.manager.start_tail_for_playback(self.sid, self.generation, self.turn)
        self.flush()
        self.assertEqual(self.device.requests, [])

    def new_turn(self):
        self.session.busy = False
        self.turn = str(uuid.uuid4())
        self.manager.begin_turn(self.sid, self.turn)
        self.lease = self.session.tail_lease
        self.state.turn_id = self.turn
        self.state.turn_complete = False
        self.state.stage = "llm"

    def test_either_opt_in_disabled_and_effective_pipeline_config(self):
        self.coordinator._enabled = lambda: False
        self.event("started"); self.flush()
        self.coordinator._enabled = lambda: True
        self.event("started"); self.flush()  # Disabled first start cannot be retried.
        self.assertEqual(self.device.requests, [])
        self.session.pipeline.llm.config.tail_mcp_enabled = False
        with patch.dict("os.environ", {"TAIL_MCP_ENABLED": "true"}):
            self.new_turn()
        self.assertFalse(self.lease.playback_enabled)
        self.event("started"); self.flush()
        self.assertEqual(self.device.requests, [])
        self.session.pipeline.llm.config.tail_mcp_enabled = True
        with patch.dict("os.environ", {"TAIL_MCP_ENABLED": "false"}):
            self.new_turn()
        self.event("started"); self.flush()
        self.assertEqual([a for a, _ in self.device.accepted], ["open", "tap"])

    def test_unsupported_device_never_opens(self):
        self.device.state["capabilities"] = []
        self.event("started"); self.flush()
        self.assertEqual(self.device.accepted, [])
        self.assertTrue(self.lease.revoked)

    def test_budget_and_interruption_keep_new_turn_and_old_events_fenced(self):
        clock = [100.0]
        self.coordinator._clock = lambda: clock[0]
        for _ in range(5):
            old = self.turn
            self.event("started"); self.flush()
            self.assertEqual(self.device.state["motion"], "tapping")
            self.manager.interrupt_turn(self.sid, self.generation, self.turn)
            self.assertEqual(self.stopped()["status"], "command_stopped")
            self.new_turn()
            self.manager.start_tail_for_playback(self.sid, self.generation, old)
            self.flush()
            self.assertEqual(self.device.state["motion"], "idle")
        self.event("started"); self.flush()
        self.assertEqual(len([a for a, _ in self.device.accepted if a == "tap"]), 5)
        self.assertEqual(self.device.spent, 10000)
        self.assertTrue(all(body["duration_ms"] == 2000 for action, body in self.device.accepted if action == "tap"))
        # A skipped sixth reply is not queued or retried. After the device's
        # window clears, only a new turn may start again.
        clock[0] += 61
        self.device.spent = 0
        self.event("started"); self.flush()
        self.assertEqual(len([a for a, _ in self.device.accepted if a == "tap"]), 5)
        self.new_turn()
        self.event("started"); self.flush()
        self.assertEqual(len([a for a, _ in self.device.accepted if a == "tap"]), 6)

    def test_status_before_playback_is_available_without_motion(self):
        state = self.manager._submit(self.coordinator.status(self.lease)).result(2)
        self.assertEqual(state["motion"], "idle")
        self.assertEqual(self.device.accepted, [])

    def test_old_pcm_end_or_failure_cannot_revoke_a_new_manager_turn(self):
        old_turn = self.turn
        self.state.turn_complete = True
        self.session.busy = False
        new_turn = str(uuid.uuid4())
        self.manager.begin_turn(self.sid, new_turn)
        current = self.session.tail_lease
        for event in ("failed", "ended"):
            self.state.stage = "reply_ready"
            self.streams.playback(self.sid, self.generation, old_turn, event)
            self.assertFalse(current.revoked)
        self.manager.start_tail_for_playback(self.sid, self.generation, new_turn)
        self.flush()
        self.assertEqual(self.device.state["motion"], "tapping")

    def test_ambiguous_open_or_tap_inhibits_future_reply_even_after_confirmed_stop(self):
        for action in ("open", "tap"):
            self.session.tail_playback_inhibited = False
            self.new_turn()
            async def lose_ack(request_action, body, response):
                if request_action == action:
                    raise httpx.ReadTimeout("accepted but acknowledgement lost")
            self.device.after = lose_ack
            self.event("started"); self.flush()
            self.assertEqual(self.stopped()["status"], "command_stopped")
            self.assertTrue(self.session.tail_playback_inhibited)
            self.device.after = None
            before = len(self.device.accepted)
            self.new_turn()
            self.event("started"); self.flush()
            self.assertEqual(len(self.device.accepted), before)

    def unknown_http_response_inhibits_next_reply(self, status):
        async def replace_ack(action, body, response):
            if action == "tap":
                return httpx.Response(status, text="upstream response unknown")
        self.device.after = replace_ack
        self.event("started"); self.flush()
        self.assertEqual(self.stopped()["status"], "command_stopped")
        self.assertTrue(self.session.tail_playback_inhibited)
        self.assertEqual(len([a for a, _ in self.device.accepted if a == "tap"]), 1)
        self.device.after = None
        before = len(self.device.accepted)
        self.new_turn()
        self.event("started"); self.flush()
        self.assertEqual(len(self.device.accepted), before)

    def test_accepted_tap_http_500_inhibits_future_reply(self):
        self.unknown_http_response_inhibits_next_reply(500)

    def test_accepted_tap_http_502_inhibits_future_reply(self):
        self.unknown_http_response_inhibits_next_reply(502)

    def test_accepted_tap_redirect_inhibits_future_reply(self):
        self.unknown_http_response_inhibits_next_reply(302)

    def test_stop_while_each_start_boundary_held_never_late_motion(self):
        for action in ("state", "open", "tap"):
            with self.subTest(action=action):
                if self.lease.revoked:
                    self.session.busy = False
                    self.turn = str(uuid.uuid4())
                    self.manager.begin_turn(self.sid, self.turn)
                    self.lease = self.session.tail_lease
                    self.state.turn_id = self.turn
                entered, release = threading.Event(), threading.Event()
                async def hold(request_action, body):
                    if request_action == action and not entered.is_set():
                        entered.set()
                        while not release.is_set():
                            await asyncio.sleep(.005)
                self.device.before = hold
                self.event("started")
                self.assertTrue(entered.wait(2))
                self.event("failed")
                self.assertTrue(self.lease.revoked)
                self.assertEqual(self.stopped()["status"], "command_stopped" if action != "state" else "disabled")
                release.set(); self.flush()
                self.assertEqual(self.device.state["motion"], "idle")
                self.assertEqual(self.device.state["phase"], "closed")
        self.assertEqual([a for a, _ in self.device.accepted if a == "tap"], [])
