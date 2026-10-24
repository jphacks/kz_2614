"""PCM transport, playback notifications and browser lifecycle regression checks."""

import io
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import Mock, patch

from Programs.ServerSide.dashboard.server import DashboardHandler, main


MODULE = "Programs.ServerSide.dashboard.server"


class DevelopmentAPITests(unittest.TestCase):
    def handler(self, path, body=b""):
        handler = object.__new__(DashboardHandler)
        handler.path = path
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        handler._send_json = Mock()
        return handler

    def test_health_and_settings_are_accessible_with_plain_requests(self):
        handler = self.handler("/api/v1/dashboard/health")
        handler.do_GET()
        handler._send_json.assert_called_once_with(200, {"status": "ok", "settings_api": "ready"})

        handler = self.handler("/api/v1/dashboard/settings", b'{"volume": 30}')
        with patch(f"{MODULE}.save_settings", return_value={"volume": 30}) as save:
            handler.do_PUT()
        save.assert_called_once_with({"volume": 30})
        handler._send_json.assert_called_once_with(200, {
            "settings": {"volume": 30}, "applies": "new_pipeline_instance",
        })

    def test_voice_sessions_start_and_stop_with_plain_requests(self):
        session_id = "12345678-1234-1234-1234-123456789abc"
        handler = self.handler("/api/v1/voice/sessions")
        created = {"session_id": session_id, "generation": 0}
        with patch(f"{MODULE}._voice_sessions") as manager, patch(f"{MODULE}._AUDIO_STREAMS", None):
            manager.return_value.create_session.return_value = created
            handler.do_POST()
            handler._send_json.assert_called_once_with(201, created)

            handler.path = f"/api/v1/voice/sessions/{session_id}"
            stopped = {"session_id": session_id, "stage": "stopped"}
            manager.return_value.stop_session.return_value = stopped
            handler.do_DELETE()
            manager.return_value.stop_session.assert_called_once_with(session_id)
            handler._send_json.assert_called_with(200, stopped)

    def test_startup_accepts_private_network_bind(self):
        with patch("sys.argv", ["dashboard", "--host", "0.0.0.0", "--port", "8765"]), \
             patch(f"{MODULE}.load_dotenv"), patch(f"{MODULE}.DashboardHTTPServer") as server:
            main()
        server.assert_called_once_with(("0.0.0.0", 8765))
        server.return_value.serve_forever.assert_called_once_with()
        server.return_value.server_close.assert_called_once_with()


class ReplyPlaybackPolicyTests(unittest.TestCase):
    def test_response_allows_same_origin_audio_and_browser_blob_urls(self):
        handler = object.__new__(DashboardHandler)
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.wfile = io.BytesIO()
        handler._send_bytes(200, b"dashboard", "text/html")

        headers = dict(call.args for call in handler.send_header.call_args_list)
        directives = {
            values[0]: values[1:]
            for part in headers["Content-Security-Policy"].split(";")
            if (values := part.split())
        }
        self.assertIn("'self'", directives["media-src"])
        self.assertIn("blob:", directives["media-src"])
        self.assertEqual(directives["object-src"], ["'none'"])
        self.assertEqual(handler.wfile.getvalue(), b"dashboard")


class VoiceBrowserRecoveryTests(unittest.TestCase):
    def run_browser_checks(self, filename):
        node = os.getenv("NODE") or shutil.which("node")
        if not node:
            self.skipTest("Set NODE or put Node.js on PATH to run browser logic checks")
        result = subprocess.run(
            [node, str(Path(__file__).with_name(filename))],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_browser_ndjson_phrase_playback_reconnect_and_completion_fence(self):
        self.run_browser_checks("dashboard_voice_test.js")

    def test_browser_event_backpressure_preserves_pcm_and_unaccepted_cursor(self):
        self.run_browser_checks("dashboard_event_backpressure_test.js")

    def test_browser_pcm_start_microphone_selection_and_cancellation(self):
        self.run_browser_checks("dashboard_start_test.js")

    def test_browser_pcm_silence_sequence_retry_overflow_and_stale_acks(self):
        self.run_browser_checks("dashboard_recording_test.js")

    def test_browser_independent_transport_lanes_and_bounded_pcm_retry(self):
        self.run_browser_checks("dashboard_transport_lanes_test.js")

    def test_browser_barge_in_keeps_pcm_and_rejects_old_reply(self):
        self.run_browser_checks("dashboard_barge_in_test.js")

    def test_browser_pcm_output_frames_reconnect_and_interruption(self):
        self.run_browser_checks("dashboard_pcm_output_test.js")

    def test_pcm_output_worklet_resampling_drain_reset_and_limits(self):
        self.run_browser_checks("test_pcm_output_worklet.js")

    def test_pcm_worklet_resampling_frames_and_unconditional_silence(self):
        self.run_browser_checks("test_pcm_worklet.js")

    def test_input_gain_live_controls_storage_and_retry_identity(self):
        self.run_browser_checks("dashboard_input_gain_test.js")


if __name__ == "__main__":
    unittest.main()
