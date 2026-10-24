"""Desired-frequency persistence and MCP transport without hardware movement."""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.mcpserver.exceptions import ToolError

from Programs.ServerSide.dashboard import tail_settings
from Programs.ServerSide.STTLLMTTS import tail_mcp_server as tail


class TailFrequencyStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "data" / "tail_settings.json"
        self.environment = patch.dict(os.environ, {"NENGITSUNE_TAIL_SETTINGS_PATH": str(self.path)})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.directory.cleanup()

    def test_default_is_configuration_only_and_does_not_create_a_file(self):
        self.assertEqual(tail_settings.get_tail_frequency(), {
            "rate_hz": 0.6, "interval_ms": 1667,
            "device_confirmed": False, "applies_to": "configuration",
        })
        self.assertFalse(self.path.exists())

    def test_setting_is_persisted_as_only_desired_rate_and_can_be_read_again(self):
        for rate, interval in ((0.3, 3333), (0.8, 1250), (1, 1000)):
            result = tail_settings.set_tail_frequency(rate)
            self.assertEqual(result["interval_ms"], interval)
            self.assertFalse(result["device_confirmed"])
            self.assertEqual(json.loads(self.path.read_text()), {"rate_hz": float(rate)})
            self.assertEqual(tail_settings.get_tail_frequency(), result)

    def test_bool_nonfinite_and_invalid_values_do_not_overwrite_the_prior_setting(self):
        tail_settings.set_tail_frequency(0.8)
        for value in (True, False, "0.6", None, float("nan"), float("inf"), -float("inf"),
                      0.2999, 1.0001, -1, 0, 1 + 1j, 10 ** 1000):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    tail_settings.set_tail_frequency(value)
                self.assertEqual(json.loads(self.path.read_text()), {"rate_hz": 0.8})

    def test_atomic_replace_failure_keeps_the_previous_file_and_removes_staging_file(self):
        tail_settings.set_tail_frequency(0.6)
        with patch.object(tail_settings.os, "replace", side_effect=OSError("simulated write failure")):
            with self.assertRaises(RuntimeError):
                tail_settings.set_tail_frequency(0.9)
        self.assertEqual(json.loads(self.path.read_text()), {"rate_hz": 0.6})
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_corrupt_or_untrusted_persisted_state_is_reported(self):
        self.path.parent.mkdir()
        for body in ("not JSON", "[]", '{"rate_hz":true}', '{"rate_hz":0.8,"device_confirmed":true}'):
            self.path.write_text(body)
            with self.subTest(body=body), self.assertRaises(RuntimeError):
                tail_settings.get_tail_frequency()


class TailFrequencyToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_tools_only_get_put_desired_frequency_without_authentication(self):
        requests = []
        stored = {"rate_hz": 0.6}

        def handle(request):
            requests.append(request)
            if request.method == "PUT":
                stored.update(json.loads(request.content))
            return httpx.Response(200, json={**stored, "interval_ms": 0,
                                           "device_confirmed": False, "applies_to": "configuration"})

        client = httpx.AsyncClient
        with patch.dict(os.environ, {"TAIL_DASHBOARD_URL": "http://127.0.0.1:8765"}), \
             patch.object(tail.httpx, "AsyncClient", side_effect=lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs)), \
             patch.object(tail, "_request", side_effect=AssertionError("Hardware API must not be called")):
            changed = await tail.set_tail_frequency(0.8)
            current = await tail.get_tail_frequency()
        self.assertEqual(changed, current)
        self.assertEqual(changed, {"rate_hz": 0.8, "interval_ms": 1250,
                                   "device_confirmed": False, "applies_to": "configuration"})
        self.assertEqual([request.method for request in requests], ["PUT", "GET"])
        self.assertTrue(all(request.url.path == "/api/v1/tail/frequency" for request in requests))
        self.assertTrue(all("Authorization" not in request.headers for request in requests))
        self.assertEqual(json.loads(requests[0].content), {"rate_hz": 0.8})

    async def test_tool_validation_rejects_bool_and_nonfinite_before_http(self):
        with patch.object(tail.httpx, "AsyncClient") as client:
            for value in (True, False, None, "0.6", float("nan"), float("inf"), 0.2, 1.1, 10 ** 1000):
                with self.subTest(value=repr(value)), self.assertRaises(ToolError):
                    await tail.set_tail_frequency(value)
            client.assert_not_called()

    async def test_tool_failure_is_sanitized_and_does_not_claim_device_application(self):
        client = httpx.AsyncClient
        with patch.dict(os.environ, {"TAIL_DASHBOARD_URL": "http://127.0.0.1:8765"}), \
             patch.object(tail.httpx, "AsyncClient", side_effect=lambda **kwargs: client(
                 transport=httpx.MockTransport(lambda request: httpx.Response(503, json={"error": "private-upstream-detail"})), **kwargs)), \
             self.assertLogs("nen_gitsune.tail_mcp", level="WARNING") as logs:
            with self.assertRaises(ToolError) as error:
                await tail.get_tail_frequency()
        self.assertNotIn("private-upstream-detail", str(error.exception) + "\n".join(logs.output))
        self.assertIn("実機で確認されていません", str(error.exception))

    async def test_dashboard_url_rejects_nonlocal_destinations_and_embedded_options(self):
        for url in ("https://example.com", "http://192.168.1.10:8765", "http://127.0.0.1/?setting=test", "http://name:secret@localhost"):
            with self.subTest(url=url), patch.dict(os.environ, {"TAIL_DASHBOARD_URL": url}), self.assertRaises(RuntimeError):
                tail._dashboard_base_url()


class TailFrequencyStdioTests(unittest.TestCase):
    def test_stdio_initialize_new_tools_and_unauthenticated_pc_http_round_trip(self):
        from Programs.ServerSide.dashboard import server as dashboard

        requests = []

        class Handler(dashboard.DashboardHandler):
            def do_GET(self):
                requests.append(("GET", self.path, self.headers.get("Authorization")))
                super().do_GET()

            def do_PUT(self):
                requests.append(("PUT", self.path, self.headers.get("Authorization")))
                super().do_PUT()

            def log_message(self, *args):
                pass

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "NENGITSUNE_TAIL_SETTINGS_PATH": str(Path(directory) / "tail_settings.json"),
        }), tempfile.TemporaryFile(mode="w+") as errlog:
            server = dashboard.DashboardHTTPServer(("127.0.0.1", 0))
            server.RequestHandlerClass = Handler
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            def body(result):
                wire = result.model_dump(by_alias=True)
                self.assertFalse(wire.get("isError", False))
                structured = wire.get("structuredContent")
                if structured:
                    return structured.get("result", structured)
                return json.loads(next(item["text"] for item in wire["content"] if item["type"] == "text"))

            async def exercise():
                params = StdioServerParameters(
                    command=sys.executable,
                    args=[str(Path(tail.__file__).resolve())],
                    env={"TAIL_DASHBOARD_URL": f"http://127.0.0.1:{server.server_port}",
                         "TAIL_DEVICE_URL": "",
                         "TAIL_CONTROL_ENABLED": "false", "TAIL_MCP_LOG_LEVEL": "WARNING"},
                )
                async with stdio_client(params, errlog=errlog) as (read, write):
                    async with ClientSession(read, write, read_timeout_seconds=10) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        self.assertEqual({tool.name for tool in tools.tools}, {
                            "set_tail_frequency", "get_tail_frequency", "tap_tail", "stop_tail", "get_tail_status",
                        })
                        self.assertEqual(body(await session.call_tool("get_tail_frequency", {}))["rate_hz"], 0.6)
                        saved = body(await session.call_tool("set_tail_frequency", {"rate_hz": 0.8}))
                        self.assertEqual(saved["rate_hz"], 0.8)
                        self.assertFalse(saved["device_confirmed"])
                        self.assertEqual(body(await session.call_tool("get_tail_frequency", {})), saved)
                        invalid = (await session.call_tool("set_tail_frequency", {"rate_hz": True})).model_dump(by_alias=True)
                        self.assertTrue(invalid["isError"])

            try:
                asyncio.run(exercise())
                self.assertEqual(requests, [("GET", "/api/v1/tail/frequency", None),
                                            ("PUT", "/api/v1/tail/frequency", None),
                                            ("GET", "/api/v1/tail/frequency", None)])
                self.assertEqual(json.loads((Path(directory) / "tail_settings.json").read_text()), {"rate_hz": 0.8})
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
