"""Real HTTP boundaries for desired-frequency settings, without device calls."""

from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from server.api import server as dashboard
from server.api import tail_settings
from server.ai import tail_mcp_server


class TailFrequencyHTTPTests(unittest.TestCase):
    route = "/api/v1/tail/frequency"

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "tail_settings.json"
        environment = patch.dict(os.environ, {"NENGITSUNE_TAIL_SETTINGS_PATH": str(self.path)})
        environment.start()
        self.addCleanup(environment.stop)

        class Handler(dashboard.DashboardHandler):
            def log_message(self, *args):
                pass

        self.server = dashboard.DashboardHTTPServer(("127.0.0.1", 0))
        self.server.RequestHandlerClass = Handler
        self.addCleanup(self.server.server_close)
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True,
        )
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())

    def request(self, method, body=None, *, content_type="application/json"):
        headers = {}
        if body is not None and content_type is not None:
            headers["Content-Type"] = content_type
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request(method, self.route, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read()), dict(response.getheaders())
        finally:
            connection.close()

    def save_initial_rate(self):
        status, payload, _ = self.request("PUT", b'{"rate_hz":0.8}')
        self.assertEqual((status, payload["rate_hz"]), (200, 0.8))
        return self.path.read_bytes()

    def assert_saved_rate(self, original):
        self.assertEqual(self.path.read_bytes(), original)
        status, payload, _ = self.request("GET")
        self.assertEqual((status, payload["rate_hz"]), (200, 0.8))

    def test_plain_http_round_trip_only_changes_pc_configuration(self):
        with patch("server.ai.llm.tail_v2.V2Device.request", side_effect=AssertionError("No hardware request")) as device, \
             patch.object(dashboard, "_voice_sessions", side_effect=AssertionError("No voice runtime")) as voice, \
             patch.object(dashboard, "_audio_streams", side_effect=AssertionError("No audio runtime")) as audio:
            status, default, _ = self.request("GET")
            self.assertEqual((status, default["rate_hz"]), (200, 0.6))
            self.assertFalse(self.path.exists())
            status, saved, headers = self.request(
                "PUT", b'{"rate_hz":0.8}', content_type="application/json; charset=utf-8",
            )
            self.assertEqual(status, 200)
            self.assertEqual(saved, {"rate_hz": 0.8, "interval_ms": 1250,
                                     "device_confirmed": False, "applies_to": "configuration"})
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(json.loads(self.path.read_text()), {"rate_hz": 0.8})
            self.assertEqual(self.request("GET")[:2], (200, saved))
            device.assert_not_called()
            voice.assert_not_called()
            audio.assert_not_called()

    def test_put_requires_json_media_type_and_preserves_the_prior_value(self):
        original = self.save_initial_rate()
        for media_type in (None, "text/plain", "application/octet-stream"):
            with self.subTest(media_type=media_type):
                status, payload, headers = self.request(
                    "PUT", b'{"rate_hz":0.9}', content_type=media_type,
                )
                self.assertEqual((status, payload["error"]), (415, "unsupported_media_type"))
                self.assertEqual(headers["Connection"], "close")
                self.assert_saved_rate(original)

    def test_invalid_json_values_shapes_and_unknown_keys_do_not_replace_the_rate(self):
        original = self.save_initial_rate()
        documents = [
            b'{"rate_hz":true}', b'{"rate_hz":false}', b'{"rate_hz":NaN}',
            b'{"rate_hz":Infinity}', b'{"rate_hz":-Infinity}', b'{"rate_hz":1e309}',
            b'{"rate_hz":0.2999}', b'{"rate_hz":1.0001}', b'{"rate_hz":"0.6"}',
            b'{"rate_hz":null}', b'{"rate_hz":[]}', b'{"rate_hz":{}}',
            b'{"rate_hz":0.9,"device_confirmed":true}', b'{}', b'[]', b'null',
            b'"0.6"', b'{"rate_hz":', b'{"rate_hz":0.9}\xff',
        ]
        for document in documents:
            with self.subTest(document=document):
                status, payload, _ = self.request("PUT", document)
                self.assertEqual((status, payload["error"]), (400, "invalid_tail_frequency"))
                self.assert_saved_rate(original)

    def test_oversized_json_is_rejected_before_the_setting_changes(self):
        original = self.save_initial_rate()
        status, _, headers = self.request("PUT", b" " * (dashboard.MAX_REQUEST_BYTES + 1))
        self.assertEqual(status, 413)
        self.assertEqual(headers["Connection"], "close")
        self.assert_saved_rate(original)

    def test_save_failure_is_http_500_and_retains_the_prior_value(self):
        original = self.save_initial_rate()
        with patch.object(tail_settings.os, "replace", side_effect=OSError("simulated save failure")):
            status, payload, _ = self.request("PUT", b'{"rate_hz":0.9}')
        self.assertEqual((status, payload["error"]), (500, "tail_settings_unavailable"))
        self.assertNotIn("simulated save failure", json.dumps(payload))
        self.assert_saved_rate(original)

    def test_corrupt_saved_configuration_is_http_500(self):
        self.path.write_text('{"rate_hz":true}', encoding="utf-8")
        status, payload, _ = self.request("GET")
        self.assertEqual((status, payload["error"]), (500, "tail_settings_unavailable"))
        self.assertNotIn("rate_hz", payload)
        self.assertEqual(self.path.read_text(), '{"rate_hz":true}')


if __name__ == "__main__":
    unittest.main()
