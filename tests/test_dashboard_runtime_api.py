"""Development dashboard observation needs no credentials or voice processing."""

import copy
import unittest
from unittest.mock import Mock, patch

from Programs.ServerSide.dashboard.server import DashboardHandler
from Programs.ServerSide.dashboard.settings_store import DEFAULT_SETTINGS


class RuntimeHTTPTests(unittest.TestCase):
    def handler(self):
        handler = object.__new__(DashboardHandler)
        handler.path = "/api/v1/dashboard/runtime"
        handler.headers = {}
        handler._send_json = Mock()
        return handler

    def test_request_without_credentials_can_observe_runtime(self):
        handler = self.handler()
        with (
            patch("Programs.ServerSide.dashboard.server._VOICE_SESSIONS", None),
            patch("Programs.ServerSide.dashboard.server._AUDIO_STREAMS", None),
            patch("Programs.ServerSide.dashboard.server.get_dashboard_settings", return_value=copy.deepcopy(DEFAULT_SETTINGS)),
            patch("Programs.ServerSide.dashboard.runtime_status.get_runtime_status", return_value={"components": {}}) as status,
        ):
            handler.do_GET()
        status.assert_called_once_with(None)
        self.assertEqual(handler._send_json.call_args.args[0], 200)

    def test_empty_server_observation_does_not_start_sessions_or_models(self):
        handler = self.handler()
        with (
            patch("Programs.ServerSide.dashboard.server._VOICE_SESSIONS", None),
            patch("Programs.ServerSide.dashboard.server._AUDIO_STREAMS", None),
            patch("Programs.ServerSide.dashboard.server._voice_sessions") as sessions,
            patch("Programs.ServerSide.dashboard.server._audio_streams") as streams,
            patch("Programs.ServerSide.dashboard.server.get_dashboard_settings", return_value=copy.deepcopy(DEFAULT_SETTINGS)),
            patch("Programs.ServerSide.dashboard.runtime_status.get_runtime_status", return_value={"components": {}}) as status,
        ):
            handler.do_GET()
        status.assert_called_once_with(None)
        sessions.assert_not_called()
        streams.assert_not_called()
        code, body = handler._send_json.call_args.args
        self.assertEqual(code, 200)
        self.assertEqual(body["vad"], {"configured": DEFAULT_SETTINGS["vad"], "active": []})

    def test_active_vad_selection_is_filtered_without_any_expiry_or_reset(self):
        handler = self.handler()
        manager, streams = Mock(), Mock()
        manager.runtime_session_ids.return_value = {"active"}
        streams.runtime_snapshot.return_value = [{"stage": "speaking", "settings": DEFAULT_SETTINGS["vad"]}]
        with (
            patch("Programs.ServerSide.dashboard.server._VOICE_SESSIONS", manager),
            patch("Programs.ServerSide.dashboard.server._AUDIO_STREAMS", streams),
            patch("Programs.ServerSide.dashboard.server.get_dashboard_settings", return_value=copy.deepcopy(DEFAULT_SETTINGS)),
            patch("Programs.ServerSide.dashboard.runtime_status.get_runtime_status", return_value={"components": {}}) as status,
        ):
            handler.do_GET()
        status.assert_called_once_with(manager)
        streams.runtime_snapshot.assert_called_once_with({"active"})
        manager.get_active_session.assert_not_called()
        streams.discard.assert_not_called()
        self.assertEqual(handler._send_json.call_args.args[1]["vad"]["active"][0]["stage"], "speaking")

    def test_configuration_failure_does_not_expose_private_error_details(self):
        handler = self.handler()
        with patch("Programs.ServerSide.dashboard.runtime_status.get_runtime_status", side_effect=ValueError("secret api key")):
            handler.do_GET()
        code, body = handler._send_json.call_args.args
        self.assertEqual(code, 503)
        self.assertNotIn("secret", str(body))


if __name__ == "__main__":
    unittest.main()
