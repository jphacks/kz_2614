"""Conversation-button preparation and independent permission release."""
import asyncio
import threading
import uuid
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import httpx
from server.ai.llm.tail_control import TailCoordinator
from server.ai.llm.tail_v2 import V2Device
from server.ai.voice.sessions import VoiceSessions
from tail_v2_mock import MockFollower

class SessionPrepareTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict('os.environ', {'TAIL_SESSION_PREPARE_ENABLED':'true'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.device = MockFollower()
        self.device.state.update(start_enabled=False, permission_session_id=None)
        self.device.state['capabilities'].append('session_prepare')
        self.coordinator = TailCoordinator(enabled=lambda: True, device=V2Device(
            'http://127.0.0.1:1', transport=httpx.MockTransport(self.device.handle)))
        self.manager = VoiceSessions(tail_coordinator=self.coordinator)
        pipeline = SimpleNamespace(close=AsyncMock(), llm=SimpleNamespace(config=SimpleNamespace(tail_mcp_enabled=True)))
        self.pipeline = patch.object(self.manager, '_create_pipeline', AsyncMock(return_value=pipeline))
        self.pipeline.start(); self.addCleanup(self.pipeline.stop)

    def tearDown(self):
        for sid in list(self.manager._sessions):
            self.manager.stop_session(sid)
        self.manager._submit(asyncio.sleep(.05)).result(2)
        self.manager._loop.call_soon_threadsafe(self.manager._loop.stop)
        self.manager._loop_thread.join(3)

    def create(self):
        return self.manager.create_session()['session_id']

    def tap(self, sid):
        session = self.manager._sessions[sid]; session.busy=False
        turn = str(uuid.uuid4()); self.manager.begin_turn(sid, turn)
        lease=session.tail_lease; lease.playback_active=True
        return self.manager._submit(lease.call_tap(lambda:self.coordinator.tap(lease, .5), .5, playback=True)).result(3)

    def test_button_prepares_without_motion_delete_disarms_and_next_session_works(self):
        sid=self.create()
        self.assertTrue(self.device.state['start_enabled'])
        self.assertEqual(self.device.state['motion'],'idle')
        self.assertEqual([a for a,_ in self.device.accepted],['prepare'])
        result=self.manager.stop_session(sid)
        self.assertEqual(result['tail_control']['status'],'command_stopped')
        self.assertFalse(self.device.state['start_enabled'])
        self.assertIsNone(self.device.state['permission_session_id'])
        self.create()

    def test_replies_keep_permission_but_delete_revokes_after_completed_stop(self):
        sid=self.create()
        for _ in range(2):
            self.tap(sid)
            session=self.manager._sessions[sid]
            self.manager.stop_tail_for_session(sid,0,session.active_turn_id)
            self.manager._submit(self.coordinator.wait_stopped(sid)).result(3)
            self.assertTrue(self.device.state['start_enabled'])
        self.manager.stop_session(sid)
        self.assertFalse(self.device.state['start_enabled'])

    def test_unknown_prepare_ack_is_disarmed_and_not_retried(self):
        async def lost(action,body,response):
            if action=='prepare':raise httpx.ReadTimeout('lost')
        self.device.after=lost
        with self.assertRaises(Exception):self.create()
        self.assertEqual([a for a,_ in self.device.accepted],['prepare','disarm'])
        self.assertFalse(self.device.state['start_enabled'])

    def test_unsupported_and_uncalibrated_never_prepare(self):
        self.device.state['capabilities']=['revision_fence']
        with self.assertRaises(Exception):self.create()
        self.assertEqual(self.device.accepted,[])
        self.device.state['capabilities'].append('session_prepare')
        self.device.state['calibrated']=False
        with self.assertRaises(Exception):self.create()
        self.assertEqual(self.device.accepted,[])

    def test_second_conversation_cannot_take_permission(self):
        sid=self.create()
        with self.assertRaises(Exception):self.create()
        self.assertEqual(self.device.state['permission_session_id'],sid)
        self.tap(sid)

    def test_reboot_between_conversations_requires_new_button(self):
        sid=self.create();self.manager.stop_session(sid)
        self.device.state['device_epoch']=uuid.uuid4().hex;self.device.state['revision']=0
        self.create()
        self.assertTrue(self.device.state['start_enabled'])

    def test_delete_during_prepare_send_fences_delayed_request(self):
        entered,release=threading.Event(),threading.Event()
        async def hold(action,body):
            if action=='prepare':
                entered.set()
                while not release.is_set():await asyncio.sleep(.005)
        self.device.before=hold
        errors=[]
        def run():
            try:self.create()
            except Exception as exc:errors.append(exc)
        thread=threading.Thread(target=run);thread.start()
        try:
            self.assertTrue(entered.wait(8));sid=next(iter(self.manager._sessions))
            self.manager.stop_session(sid)
        finally:
            release.set();thread.join(4)
        self.assertTrue(errors)
        self.assertFalse(self.device.state['start_enabled'])
        self.assertNotIn('prepare',[a for a,_ in self.device.accepted])

    def test_delete_upgrades_inflight_reply_stop_to_disarm(self):
        sid=self.create();self.tap(sid)
        entered,release=threading.Event(),threading.Event()
        async def hold(action,body):
            if action=='stop':
                entered.set()
                while not release.is_set():await asyncio.sleep(.005)
        self.device.before=hold
        session=self.manager._sessions[sid]
        self.manager.stop_tail_for_session(sid,0,session.active_turn_id)
        self.assertTrue(entered.wait(8))
        with self.manager._lock:self.manager._retire_locked(session)
        release.set()
        result=self.manager._submit(self.coordinator.wait_stopped(sid)).result(4)
        self.assertEqual(result['status'],'command_stopped')
        self.assertFalse(self.device.state['start_enabled'])
        self.assertEqual([a for a,_ in self.device.accepted][-2:],['stop','disarm'])
