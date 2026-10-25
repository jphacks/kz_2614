"""AC-01/03/04/05: v2 wire, MCP, voice lifecycle and stop races, all simulated."""
import asyncio
from copy import deepcopy
import json
import time
import unittest
import uuid
from unittest.mock import patch

import httpx
from mcp import Client

from server.ai.llm.tail_control import TailCoordinator, tail_lease_context
from server.ai.llm.tail_v2 import TailControlError, V2Device, validate_state, validate_ack
from server.ai.llm import tail_tools
from server.ai.tail_mcp_server import create_tail_server
from tail_v2_mock import MockFollower, FIXTURE


class V2Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.device = MockFollower()
        self.control = TailCoordinator(enabled=lambda: True,
            device=V2Device("http://127.0.0.1:1", transport=httpx.MockTransport(self.device.handle)))
        self.session = FIXTURE["session_id"]
        self.lease = self.fresh()

    def fresh(self, session=None, owns=lambda: True):
        return self.control.lease(session or self.session, 0, str(uuid.uuid4()), owns)

    async def tap(self, lease=None, duration=1):
        lease = lease or self.lease
        return await lease.call_tap(lambda: self.control.tap(lease, duration), duration)

    async def asyncTearDown(self):
        await self.control.stop(self.session)
        tasks = list(self.control._background)
        if tasks:
            await asyncio.gather(*tasks)

    async def test_normal_shared_fixture_and_budget(self):
        result = await self.tap(duration=5)
        self.assertEqual(result["motion"], "tapping")
        self.device.expire()
        await self.tap(self.fresh(), 5)
        self.device.expire()
        with self.assertRaises(TailControlError):
            await self.tap(self.fresh(), .5)
        stopped = await self.control.stop(self.session)
        self.assertEqual(stopped["status"], "command_stopped")
        self.assertEqual(stopped["stop_revision"], 6)
        self.assertEqual(self.device.spent, 10000)
        self.assertTrue(all(set(b) == {"device_epoch", "expected_revision", "command_id", "session_id", "duration_ms"}
                            for a, b in self.device.requests if a == "tap"))
        # Stops never refund the rolling PC budget.
        with self.assertRaises(TailControlError):
            await self.tap(self.fresh(str(uuid.uuid4())), .5)

    async def test_unsupported_and_unprepared_devices_never_receive_start(self):
        for changes in ({"protocol_version": 1}, {"capabilities": []}, {"control_mode": "neutral"},
                        {"calibrated": False}, {"start_enabled": False}, {"device_epoch": ""},
                        {"revision": True}, {"limits": {}}, {"motion": "moving"}):
            with self.subTest(changes=changes):
                self.setUp()
                self.device.state.update(changes)
                with self.assertRaises(TailControlError):
                    await self.tap()
                await self.control.wait_stopped(self.session)
                self.assertEqual(self.device.accepted, [])
                self.assertEqual(self.control.stop_snapshot(self.session)["status"], "disabled")

    async def test_disabled_or_missing_permit_cannot_be_enabled_by_mcp_env(self):
        self.control._enabled = lambda: False
        with self.assertRaises(TailControlError):
            await self.tap()
        self.assertEqual(self.device.requests, [])
        with patch.dict("os.environ", {"TAIL_CONTROL_ENABLED": "true", "TAIL_DEVICE_URL": "http://127.0.0.1:1"}):
            async with Client(create_tail_server()) as client:
                result = await client.call_tool("tap_tail", {"duration_seconds": 1})
                self.assertTrue(result.is_error)
        self.assertEqual(self.device.requests, [])

    async def test_delayed_open_and_tap_cannot_restart_after_stop_or_next_session(self):
        for action in ("open", "tap"):
            with self.subTest(action=action):
                self.setUp()
                arrived, release = asyncio.Event(), asyncio.Event()
                async def delay(name, body):
                    if name == action and not arrived.is_set():
                        arrived.set()
                        await release.wait()
                self.device.before = delay
                pending = asyncio.create_task(self.tap())
                await asyncio.wait_for(arrived.wait(), 1)
                result = await self.control.stop(self.session)
                self.assertEqual(result["status"], "command_stopped")
                self.assertFalse(pending.done(), "Stop must not await the pending start")
                next_session = str(uuid.uuid4())
                # A still-running local start may conservatively reject a new tap;
                # independent wire requests prove the device fence across sessions.
                state = deepcopy(self.device.state)
                next_open = self.control._body(state, session_id=next_session)
                self.assertEqual(self.device.apply("open", next_open).status_code, 200)
                release.set()
                with self.assertRaises((TailControlError, httpx.HTTPStatusError)):
                    await pending
                self.assertEqual(self.device.state["session_id"], next_session)
                self.assertEqual(self.device.state["motion"], "idle")
                self.assertEqual(len([a for a, _ in self.device.accepted if a == "tap"]), 0)

    async def test_tap_wins_race_stop_refreshes_revision_without_waiting_for_ack(self):
        accepted, release = asyncio.Event(), asyncio.Event()
        async def hold_ack(action, body, response):
            if action == "tap":
                accepted.set()
                await release.wait()
        self.device.after = hold_ack
        pending = asyncio.create_task(self.tap())
        await accepted.wait()
        result = await self.control.stop(self.session)
        self.assertEqual(result["status"], "command_stopped")
        stops = [body for action, body in self.device.requests if action == "stop"]
        self.assertEqual([x["expected_revision"] for x in stops], [1, 2])
        self.assertNotEqual(stops[0]["command_id"], stops[1]["command_id"])
        release.set()
        with self.assertRaises(TailControlError):
            await pending
        self.assertEqual(self.device.state["phase"], "closed")

    async def test_old_model_and_old_stop_cannot_affect_new_session(self):
        await self.tap()
        await self.control.stop(self.session)
        new_session = str(uuid.uuid4())
        await self.tap(self.fresh(new_session))
        before = len(self.device.requests)
        with self.assertRaises(TailControlError):
            await self.tap(self.lease)
        await self.lease.stop()
        self.assertEqual(len(self.device.requests), before)
        self.assertEqual(self.device.state["session_id"], new_session)
        await self.control.stop(new_session)

    async def test_old_turn_fails_after_model_completion_and_before_http_dispatch(self):
        alive = True
        lease = self.fresh(owns=lambda: alive)
        ready, release = asyncio.Event(), asyncio.Event()
        async def hold_state(action, body, response):
            if action == "state":
                ready.set()
                await release.wait()
        self.device.after = hold_state
        pending = asyncio.create_task(self.tap(lease))
        await ready.wait()
        alive = False
        release.set()
        with self.assertRaises(TailControlError):
            await pending
        await self.control.wait_stopped(self.session)
        self.assertEqual(self.device.accepted, [])
        with self.assertRaises(TailControlError):
            await self.tap(lease)

    async def test_unknown_start_is_not_retried_and_cleanup_is_independent(self):
        async def drop(action, body, response):
            if action == "tap":
                raise httpx.ReadTimeout("lost start acknowledgement")
        self.device.after = drop
        with self.assertRaises(httpx.ReadTimeout):
            await self.tap()
        result = await self.control.wait_stopped(self.session)
        self.assertEqual(result["status"], "command_stopped")
        self.assertEqual(len([a for a, _ in self.device.requests if a == "tap"]), 1)
        with self.assertRaises(TailControlError):
            await self.tap(self.lease)

    async def test_stop_ack_loss_or_get_failure_never_confirms_or_reenables(self):
        for fail in ("stop", "state"):
            with self.subTest(fail=fail):
                self.setUp()
                await self.tap()
                async def broken(action, body, response):
                    if action == fail:
                        raise httpx.ReadTimeout("lost")
                self.device.after = broken
                result = await self.control.stop(self.session)
                self.assertEqual(result["status"], "unconfirmed")
                count = len(self.device.requests)
                with self.assertRaises(TailControlError):
                    await self.tap(self.fresh(str(uuid.uuid4())))
                self.assertEqual(len(self.device.requests), count)

    async def test_stop_whole_deadline_and_repeated_409_are_bounded(self):
        await self.tap()
        async def hung(action, body):
            if action == "stop":
                await asyncio.sleep(60)
        self.device.before = hung
        with patch("server.ai.llm.tail_control.STOP_DEADLINE_SECONDS", .08):
            began = time.monotonic()
            result = await self.control.stop(self.session)
        self.assertLess(time.monotonic() - began, .4)
        self.assertEqual(result["status"], "unconfirmed")

    async def test_epoch_change_closes_permission_without_automatic_reopen(self):
        await self.tap()
        old = deepcopy(self.device.state)
        self.device.reboot()
        with self.assertRaises(TailControlError):
            await self.tap(self.fresh())
        self.assertEqual((await self.control.wait_stopped(self.session))["status"], "unconfirmed")
        for action in ("open", "tap", "stop"):
            body = self.control._body(old, **({"session_id": self.session} if action != "stop" else {}),
                                      **({"duration_ms": 500} if action == "tap" else {}))
            self.assertEqual(self.device.apply(action, body).json()["error"], "stale_epoch")
        self.assertEqual(self.device.state["phase"], "closed")
        self.assertEqual(len([a for a, _ in self.device.requests if a == "open"]), 1)

    async def test_mcp_real_client_schema_validation_and_http_roundtrip(self):
        with tail_lease_context(self.lease):
            async with tail_tools._open_tail_session(5) as client:
                definitions, names = await tail_tools._discover_tools(client)
                self.assertEqual(names, {"tap_tail", "stop_tail", "get_tail_status"})
                for tool in definitions:
                    self.assertFalse(tool["function"]["parameters"]["additionalProperties"])
                result = await self.lease.call_tap(lambda: client.call_tool("tap_tail", {"duration_seconds": .5}), .5)
                self.assertFalse(tail_tools._tool_result(result, "tap_tail", {})["isError"])
                self.assertEqual(self.device.accepted[-1][1]["duration_ms"], 500)
                for value in (True, "1", 0, 6):
                    rejected = await client.call_tool("tap_tail", {"duration_seconds": value})
                    self.assertTrue(rejected.is_error)
        self.assertEqual((await self.lease.stop())["status"], "command_stopped")

    async def test_cancelled_mcp_permit_is_rechecked_after_discovery(self):
        arrived, release = asyncio.Event(), asyncio.Event()
        async def hold(action, body, response):
            if action == "state":
                arrived.set()
                await release.wait()
        self.device.after = hold
        pending = asyncio.create_task(self.tap())
        await arrived.wait()
        self.lease.dispatch_allowed = False
        release.set()
        with self.assertRaises(TailControlError):
            await pending
        await self.control.wait_stopped(self.session)
        self.assertEqual(self.device.accepted, [])

    async def test_device_budget_rejection_is_not_retried_and_stop_ignores_enable_gate(self):
        self.device.spent = 10000
        with self.assertRaises(httpx.HTTPStatusError):
            await self.tap()
        self.control._enabled = lambda: False
        self.assertEqual((await self.control.wait_stopped(self.session))["status"], "command_stopped")
        self.assertEqual(len([a for a, _ in self.device.requests if a == "tap"]), 1)

    async def test_mismatched_stop_ack_or_stale_get_is_unconfirmed(self):
        for failure in ("command_id", "stale_get", "persistent_conflict"):
            with self.subTest(failure=failure):
                self.setUp()
                await self.tap()
                async def corrupt(action, body, response):
                    if failure == "command_id" and action == "stop":
                        return httpx.Response(200, json={**response.json(), "command_id": str(uuid.uuid4())})
                    if failure == "stale_get" and action == "state":
                        return httpx.Response(200, json={**response.json(), "revision": 0})
                    if failure == "persistent_conflict" and action == "stop":
                        return httpx.Response(409, json={"error": "stale_revision"})
                self.device.after = corrupt
                began = time.monotonic()
                self.assertEqual((await self.control.stop(self.session))["status"], "unconfirmed")
                self.assertLess(time.monotonic() - began, 5)
                self.assertLessEqual(len([a for a, _ in self.device.requests if a == "stop"]), 8)

    async def test_mcp_rejects_extra_arguments_before_device_dispatch(self):
        with tail_lease_context(self.lease):
            async with tail_tools._open_tail_session(5) as client:
                for arguments in ({"duration_seconds": 1, "url": "http://other"},
                                  {"duration_seconds": 1, "session_id": self.session},
                                  {"duration_seconds": "1"}, {"duration_seconds": True}):
                    result = await client.call_tool("tap_tail", arguments)
                    self.assertTrue(result.is_error)
        self.assertEqual(self.device.requests, [])

    async def test_real_http_transport_uses_exact_v2_paths_and_json(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        device = self.device
        observed = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def serve(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"]))) if self.command == "POST" else None
                observed.append((self.command, self.path, self.headers.get("Content-Type"), self.headers.get("Authorization")))
                response = device.apply(self.path.rsplit("/", 1)[-1], body)
                self.send_response(response.status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response.content)))
                self.end_headers()
                self.wfile.write(response.content)
            do_GET = serve
            do_POST = serve
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.control._device = V2Device(f"http://127.0.0.1:{server.server_port}")
            await self.tap()
            self.assertEqual((await self.control.stop(self.session))["status"], "command_stopped")
            self.assertEqual([p for _, p, _, _ in observed], ["/api/v2/tail/state", "/api/v2/tail/open",
                "/api/v2/tail/tap", "/api/v2/tail/stop", "/api/v2/tail/state"])
            self.assertTrue(all(content == "application/json" for method, _, content, _ in observed if method == "POST"))
            self.assertTrue(all(auth is None for _, _, _, auth in observed))
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join(timeout=2)

    async def test_model_sse_to_real_mcp_to_v2(self):
        from test_tail_tool_chat import sse, tool_event, event, final
        calls = []
        def model(request):
            calls.append(json.loads(request.content))
            return sse([tool_event(), event(finish="tool_calls")]) if len(calls) == 1 else final()
        with tail_lease_context(self.lease):
            async with httpx.AsyncClient(base_url="http://model.test", transport=httpx.MockTransport(model)) as client:
                events = [x async for x in tail_tools.stream_tail_chat(client, model="mock", user_input="トントンして",
                    current_user_text="トントンして", system_prompt="test", max_output_tokens=100, context_length=1024, timeout_seconds=5)]
        self.assertEqual(events[-1]["tool_calls"], ("tap_tail",))
        self.assertEqual(len([a for a, _ in self.device.accepted if a == "tap"]), 1)
        self.assertFalse(json.loads(calls[1]["messages"][-1]["content"])["isError"])

    async def test_one_owner_no_automatic_takeover_and_local_stop_no_reopen(self):
        await self.tap()
        self.device.expire()
        with self.assertRaises(TailControlError):
            await self.tap(self.fresh(str(uuid.uuid4())))
        body = self.control._body(self.device.state)
        self.device.apply("stop", body)
        with self.assertRaises(TailControlError):
            await self.tap(self.fresh())
        await self.control.wait_stopped(self.session)
        self.assertEqual(len([a for a, _ in self.device.requests if a == "open"]), 1)

    def test_device_fixture_duplicate_ordering_and_legacy_bypass(self):
        for step in FIXTURE["normal_sequence"]:
            action = step["action"]
            if action == "expire":
                self.device.expire()
            else:
                body = self.control._body(self.device.state, **({"session_id": self.session} if action != "stop" else {}),
                                          **({"duration_ms": 5000} if action == "tap" else {}))
                response = self.device.apply(action, body)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.device.apply(action, body).status_code, 409)
            self.assertEqual({key: self.device.state[key] for key in ("phase", "motion", "revision")},
                             {key: step[key] for key in ("phase", "motion", "revision")})
        for action in ("angle", "teleop", "konkon", "mode"):
            self.assertEqual(self.device.apply(action, {}).status_code, 409)

    def test_response_mismatch_and_strict_json_types(self):
        for changes in ({"revision": True}, {"protocol_version": True}, {"revision": -1},
                        {"device_epoch": "ABC"}, {"start_enabled": 1}, {"capabilities": "revision_fence"},
                        {"limits": {**FIXTURE["state"]["limits"], "budget_ms": True}}):
            with self.subTest(changes=changes), self.assertRaises(TailControlError):
                validate_state({**FIXTURE["state"], **changes})
        body = self.control._body(self.device.state)
        ack = self.device.apply("stop", body).json()
        for changes in ({"command_id": str(uuid.uuid4())}, {"device_epoch": "b" * 32}, {"revision": 9}):
            with self.assertRaises(TailControlError):
                validate_ack({**ack, **changes}, "stop", body)

    async def test_transport_html_redirect_duplicate_json_and_wrong_ack_fail_closed(self):
        for response in (httpx.Response(200, text="<html>login</html>"), httpx.Response(302, headers={"location": "http://other"}),
                         httpx.Response(200, text='{"protocol_version":2,"protocol_version":1}')):
            device = V2Device("http://127.0.0.1:1", transport=httpx.MockTransport(lambda _: response))
            with self.assertRaises((TailControlError, ValueError, httpx.HTTPStatusError)):
                await device.request("state")


if __name__ == "__main__":
    unittest.main()
