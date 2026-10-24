"""PCM transport, playback notifications and browser lifecycle regression checks."""

import io
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import Mock

from Programs.ServerSide.dashboard.server import DashboardHandler


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

    def test_pcm_worklet_resampling_frames_and_unconditional_silence(self):
        self.run_browser_checks("test_pcm_worklet.js")

    def test_input_gain_live_controls_storage_and_retry_identity(self):
        self.run_browser_checks("dashboard_input_gain_test.js")


if __name__ == "__main__":
    unittest.main()
