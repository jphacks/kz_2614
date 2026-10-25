"""Exercises compiled firmware, not a Python reimplementation of the device."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest
import uuid

FIXTURE = json.loads((Path(__file__).parents[1] / 'fixtures/tail_v2.json').read_text())
SESSION = FIXTURE['session_id']


class FirmwareTests(unittest.TestCase):
    def setUp(self):
        self.proc = subprocess.Popen([os.environ['TAIL_V2_RUNNER']], text=True,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.addCleanup(self.close)
        self.current = self.call(action='prepared_boot')['body']

    def close(self):
        self.proc.stdin.close()
        self.proc.stdout.close()
        self.assertEqual(self.proc.wait(timeout=5), 0)

    def call(self, **event):
        self.proc.stdin.write(json.dumps(event) + '\n')
        self.proc.stdin.flush()
        response = json.loads(self.proc.stdout.readline())
        if 'protocol_version' in response['body']:
            self.current = response['body']
        return response

    def body(self, action, **overrides):
        b = dict(device_epoch=self.current['device_epoch'], expected_revision=self.current['revision'],
                 command_id=str(uuid.uuid4()))
        if action != 'stop':
            b['session_id'] = SESSION
        if action == 'tap':
            b['duration_ms'] = 500
        return {**b, **overrides}

    def request(self, action, body=None, **event):
        return self.call(action='request', path='/api/v2/tail/' + action,
                         body=json.dumps(body if body is not None else self.body(action)), **event)

    def ok(self, action, **overrides):
        response = self.request(action, self.body(action, **overrides))
        self.assertEqual(response['status'], 200, response)
        return response

    def state(self, **event):
        return self.call(action='request', path='/api/v2/tail/state', method='GET', **event)['body']

    def test_shared_pc_fixture_exact_state_and_sequence(self):
        self.assertEqual(self.current, FIXTURE['state'])
        for step in FIXTURE['normal_sequence']:
            if step['action'] == 'expire':
                result = self.call(action='tick', now_ms=500)
            else:
                result = self.ok(step['action'])
            for key in ['phase', 'motion', 'revision']:
                self.assertEqual(result['body'][key], step[key])

    def test_boot_is_closed_disarmed_no_pwm_writes(self):
        self.ok('open'); self.ok('tap')
        result = self.call(action='reboot')
        self.assertEqual(result['writes'], 0)
        self.assertEqual(result['body']['revision'], 0)
        self.assertFalse(result['body']['start_enabled'])
        self.assertEqual(result['body']['phase'], 'closed')
        self.assertEqual(result['body']['motion'], 'idle')
        self.assertIsNone(result['body']['session_id'])

    def test_delayed_open_and_tap_after_stop_and_next_session(self):
        old_open = self.body('open')
        self.ok('stop')
        self.assertEqual(self.request('open', old_open)['body'], {'error': 'stale_revision'})
        self.ok('open')
        old_tap = self.body('tap')
        self.ok('stop')
        new_session = str(uuid.uuid4())
        self.ok('open', session_id=new_session)
        for action, old in [('open', old_open), ('tap', old_tap)]:
            self.assertEqual(self.request(action, old)['status'], 409)
            self.assertEqual(self.state()['session_id'], new_session)
            self.assertEqual(self.current['motion'], 'idle')

    def test_duplicates_do_not_execute_or_extend_deadline(self):
        op = self.body('open')
        self.assertEqual(self.request('open', op)['status'], 200)
        self.assertEqual(self.request('open', op)['status'], 409)
        tap = self.body('tap', duration_ms=1000)
        self.assertEqual(self.request('tap', tap)['status'], 200)
        self.assertEqual(self.request('tap', tap, now_ms=800)['status'], 409)
        self.assertEqual(self.state(now_ms=1000)['motion'], 'idle')
        self.assertEqual(self.current['revision'], 3)

    def test_old_epoch_all_mutations_rejected(self):
        requests = {a: self.body(a) for a in ['open', 'tap', 'stop']}
        old = self.current['device_epoch']
        self.call(action='reboot')
        self.assertNotEqual(self.current['device_epoch'], old)
        for action, body in requests.items():
            self.assertEqual(self.request(action, body)['body'], {'error': 'stale_epoch'})
        self.assertEqual(self.state()['revision'], 0)

    def test_revision_future_and_negative_rejected(self):
        self.assertEqual(self.request('open', self.body('open', expected_revision=1))['body'], {'error': 'stale_revision'})
        self.assertEqual(self.request('open', self.body('open', expected_revision=-1))['status'], 400)
        self.assertEqual(self.state()['revision'], 0)

    def test_expiry_racing_stop_requires_fresh_revision(self):
        self.ok('open'); self.ok('tap')
        stale_stop = self.body('stop')
        self.assertEqual(self.request('stop', stale_stop, now_ms=500)['body'], {'error': 'stale_revision'})
        self.assertEqual(self.state()['revision'], 3)
        self.ok('stop')
        self.assertEqual(self.current['revision'], 4)

    def test_stop_ack_lost_retry_is_not_cached_success(self):
        self.ok('open'); self.ok('tap')
        stop = self.body('stop')
        self.request('stop', stop)  # discard the received ACK, as if lost in transit
        self.assertEqual(self.request('stop', stop)['status'], 409)
        self.state()
        ack = self.ok('stop')['body']
        state = self.state()
        self.assertEqual(state['device_epoch'], ack['device_epoch'])
        self.assertGreaterEqual(state['revision'], ack['revision'])
        self.assertEqual(state['phase'], 'closed')

    def test_disabled_uncalibrated_neutral_relax_and_stop(self):
        for enabled, calibrated, ready, error in [(False, True, True, 'start_disabled'),
                (True, False, True, 'not_calibrated'), (True, True, False, 'mode_not_ready')]:
            with self.subTest(error=error):
                self.call(action='configure', enabled=enabled, calibrated=calibrated, ready=ready)
                before = self.state()
                self.assertEqual(self.request('open')['body'], {'error': error})
                self.assertEqual(self.state(), before)
                self.ok('stop')
                self.assertEqual(self.request('open')['body'], {'error': error})

    def test_invalid_phase_and_wrong_session(self):
        self.assertEqual(self.request('tap')['body'], {'error': 'invalid_phase'})
        self.ok('open')
        self.assertEqual(self.request('open')['body'], {'error': 'invalid_phase'})
        self.assertEqual(self.request('tap', self.body('tap', session_id=str(uuid.uuid4())))['body'], {'error': 'session_mismatch'})
        self.ok('tap')
        self.assertEqual(self.request('tap')['body'], {'error': 'invalid_phase'})

    def test_no_legacy_motion_bypass_before_during_after_stop(self):
        for stage in ['closed', 'open', 'tapping', 'stopped']:
            if stage == 'open': self.ok('open')
            elif stage == 'tapping': self.ok('tap')
            elif stage == 'stopped': self.ok('stop')
            before = self.state()
            for path in ['teleop', 'angle', 'konkon', 'mode', 'tail/tap']:
                for body in ['angle=90', 'mode=teleop', 'mode=neutral', 'mode=relax', '{}']:
                    with self.subTest(stage=stage, path=path, body=body):
                        r = self.call(action='request', path='/api/' + path, body=body)
                        self.assertEqual(r['status'], 409)
                        self.assertEqual(self.state(), before)

    def test_legacy_and_local_stops_advance_same_fence(self):
        for local in [False, True]:
            self.ok('open'); self.ok('tap')
            old = self.body('tap')
            result = self.call(action='local_stop') if local else self.call(action='request', path='/api/tail/stop')
            self.assertEqual(result['body']['revision'], old['expected_revision'] + 1)
            self.assertEqual(result['body']['motion'], 'idle')
            self.assertEqual(self.request('tap', old)['status'], 409)
            self.assertEqual(self.call(action='tick', now_ms=100)['writes'], 0)

    def test_sliding_budget_early_stop_does_not_refund(self):
        self.ok('open'); self.ok('tap', duration_ms=5000); self.ok('stop')
        self.ok('open'); self.ok('tap', duration_ms=5000); self.ok('stop')
        self.ok('open')
        self.assertEqual(self.request('tap', now_ms=59999)['body'], {'error': 'budget_exceeded'})
        self.assertEqual(self.request('tap', now_ms=60000)['status'], 200)

    def test_sliding_window_does_not_reset_recent_reservations(self):
        self.ok('open'); self.ok('tap', duration_ms=5000); self.ok('stop')
        self.call(action='tick', now_ms=59000)
        self.ok('open'); self.ok('tap', duration_ms=5000); self.ok('stop')
        self.call(action='tick', now_ms=60000)
        self.ok('open'); self.ok('tap', duration_ms=5000); self.ok('stop'); self.ok('open')
        self.assertEqual(self.request('tap')['body'], {'error': 'budget_exceeded'})
        self.assertEqual(self.request('tap', now_ms=118999)['status'], 429)
        self.assertEqual(self.request('tap', now_ms=119000)['status'], 200)

    def test_budget_ring_minimum_duration_capacity(self):
        for _ in range(20):
            self.ok('open'); self.ok('tap'); self.ok('stop')
        self.ok('open')
        self.assertEqual(self.request('tap')['status'], 429)
        self.assertEqual(self.request('tap', now_ms=60000)['status'], 200)

    def test_duration_bounds_and_strict_json_types(self):
        self.ok('open')
        for duration in [None, True, False, '500', 499, 5001, 500.5, 500.0, float('nan'), float('inf')]:
            with self.subTest(duration=duration):
                self.assertEqual(self.request('tap', self.body('tap', duration_ms=duration))['status'], 400)
        self.ok('tap', duration_ms=500); self.ok('stop'); self.ok('open')
        self.ok('tap', duration_ms=5000)

    def test_strict_json_unknown_duplicate_and_extra_data(self):
        b = self.body('open')
        raw = json.dumps(b)
        cases = [raw[:-1] + ',"rate_hz":0.3}', raw[:-1] + ',"session_id":"' + SESSION + '"}',
                 raw[:-1] + ',"expected_revision":0}', raw + '{}', raw + 'junk', '[]', '{}',
                 raw.replace('"expected_revision": 0', '"expected_revision": true'),
                 raw.replace('"expected_revision": 0', '"expected_revision": "0"'),
                 raw.replace('"expected_revision": 0', '"expected_revision": 0.000000000000000001'),
                 raw.replace('"expected_revision": 0', '"expected_revision": 0e0'),
                 raw.replace('"expected_revision": 0', '"expected_revision": 9007199254740992'),
                 raw.replace('"expected_revision": 0', '"expected_revision": 01'),
                 raw.replace('"session_id"', '"session_id\\u0000hidden"'),
                 raw.replace(SESSION, SESSION + '\\u0000hidden'), raw + ' ' * 1024, '\v' + raw]
        before = self.state()
        for invalid in cases:
            with self.subTest(invalid=invalid):
                response = self.call(action='request', path='/api/v2/tail/open', body=invalid)
                self.assertEqual(response['status'], 400)
                self.assertEqual(self.state(), before)
        for ct in ['', 'text/plain', 'application/x-www-form-urlencoded']:
            self.assertEqual(self.call(action='request', path='/api/v2/tail/open', body=raw, content_type=ct)['status'], 400)

    def test_uuid_epoch_formats_and_exact_field_sets(self):
        for key, value in [('device_epoch', 'A'*32), ('device_epoch', 'f'*31),
                ('device_epoch', 'g'*32), ('command_id', 'not-uuid'), ('session_id', None),
                ('expected_revision', None)]:
            self.assertEqual(self.request('open', self.body('open', **{key: value}))['status'], 400)
        self.assertEqual(self.request('stop', self.body('stop', session_id=SESSION))['status'], 400)
        self.assertEqual(self.request('open', self.body('open', duration_ms=500))['status'], 400)

    def test_stop_holds_last_command_and_no_later_writes(self):
        self.ok('open'); self.ok('tap', duration_ms=5000)
        before = self.call(action='tick', now_ms=20)
        self.assertGreater(before['writes'], 0)
        self.ok('stop')
        after = self.call(action='tick', now_ms=1000)
        self.assertEqual(after['writes'], before['writes'])
        self.assertEqual(after['last_millidegrees'], before['last_millidegrees'])

    def test_finite_motion_deadline_and_rate(self):
        self.call(action='configure', enabled=True, calibrated=True, ready=True, low=90, high=91, position=90000)
        self.ok('open'); self.ok('tap', duration_ms=5000)
        for now in range(20, 1001, 20):
            result = self.call(action='tick', now_ms=now)
        self.assertEqual(result['last_millidegrees'], 90300)  # 0.3Hz, no 1 degree/sec round-up
        before = self.call(action='tick', now_ms=4999)
        result = self.call(action='tick', now_ms=5000)
        self.assertEqual(result['body']['motion'], 'idle')
        self.assertEqual(result['writes'], before['writes'])
        self.assertEqual(result['body']['phase'], 'open')

    def test_millis_wrap_does_not_expire_or_refund_early(self):
        self.call(action='tick', now_ms=2**32 - 100)
        self.ok('open'); self.ok('tap', duration_ms=500)
        self.assertEqual(self.state(now_ms=2**32 + 399)['motion'], 'tapping')
        self.assertEqual(self.state(now_ms=2**32 + 400)['motion'], 'idle')

    def test_pwm_failure_closes_disarms_and_fences(self):
        self.ok('open'); self.ok('tap')
        old = self.body('tap')
        self.call(action='pwm_fail')
        result = self.call(action='tick', now_ms=20)
        self.assertFalse(result['body']['start_enabled'])
        self.assertEqual(result['body']['phase'], 'closed')
        self.assertEqual(self.request('tap', old)['status'], 409)

    def test_revision_rollover_rotates_epoch_disarms_and_rejects(self):
        self.ok('open'); self.ok('tap')
        self.call(action='near_overflow')
        old = self.body('stop')
        self.assertEqual(self.request('stop', old)['body'], {'error': 'stale_epoch'})
        state = self.state()
        self.assertEqual(state['revision'], 0)
        self.assertNotEqual(state['device_epoch'], old['device_epoch'])
        self.assertFalse(state['start_enabled'])
        self.assertEqual(state['phase'], 'closed')

    @unittest.skipUnless(os.getenv('TAIL_V2_PC_WORKTREE'), 'PC checkout not supplied')
    def test_pc_validator_accepts_actual_firmware_json(self):
        root = Path(os.environ['TAIL_V2_PC_WORKTREE'])
        spec = importlib.util.spec_from_file_location('pc_tail_v2', root / 'server/ai/llm/tail_v2.py')
        pc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pc)
        pc.validate_state(self.state())
        for action in ['open', 'tap', 'stop']:
            body = self.body(action)
            result = self.request(action, body)
            pc.validate_ack(result['body'], action, body)
