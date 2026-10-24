"""The same PCM transport contract serves browsers and embedded clients."""

import io
import json
import unittest
import uuid
from unittest.mock import Mock, patch

from Programs.ServerSide.dashboard.server import DashboardHandler, MAX_PCM_FRAME_BYTES
from Programs.ServerSide.STTLLMTTS.voice.sessions import VoiceAPIError


class PCMHTTPTests(unittest.TestCase):
    session_id = str(uuid.uuid4())
    pcm_type = "audio/pcm;rate=16000;channels=1;format=s16le"

    def handler(self, body=b"\x00\x00" * 4096, *, headers=None, authorized=True):
        handler = object.__new__(DashboardHandler)
        handler.headers = {
            "Content-Type": self.pcm_type, "Content-Length": str(len(body)),
            "X-Voice-Generation": "0", "X-Audio-Sequence": "0",
            **(headers or {}),
        }
        handler.rfile = io.BytesIO(body)
        handler.connection = Mock()
        handler._api_auth_or_reject = Mock(return_value=authorized)
        handler._send_json = Mock()
        return handler

    def test_pcm_samples_sequence_and_generation_reach_shared_server_controller(self):
        body = b"\x01\x00\xff\x7f" * 2048
        handler = self.handler(body)
        manager = Mock()
        manager.push_audio.return_value = {"stage": "listening", "next_sequence": 1}
        with patch("Programs.ServerSide.dashboard.server._audio_streams", return_value=manager):
            handler._receive_pcm_frame(self.session_id)
        manager.push_audio.assert_called_once_with(self.session_id, 0, 0, body)
        handler._send_json.assert_called_once_with(200, manager.push_audio.return_value)

    def test_authentication_precedes_audio_read_and_controller_access(self):
        handler = self.handler(authorized=False)
        with patch("Programs.ServerSide.dashboard.server._audio_streams") as factory:
            handler._receive_pcm_frame(self.session_id)
        self.assertEqual(handler.rfile.tell(), 0)
        factory.assert_not_called()

    def test_incompatible_endian_rate_size_and_headers_are_rejected(self):
        cases = [
            ({"Content-Type": "audio/L16;rate=16000;channels=1"}, b"\x00\x00", 415),
            ({"Content-Type": "audio/pcm;rate=48000;channels=1;format=s16le"}, b"\x00\x00", 415),
            ({"X-Voice-Generation": "-1"}, b"\x00\x00", 400),
            ({"X-Audio-Sequence": ""}, b"\x00\x00", 400),
            ({}, b"\x00", 400),
            ({}, bytes(MAX_PCM_FRAME_BYTES + 2), 413),
            ({"Content-Length": "5"}, b"\x00\x00", 400),
        ]
        with patch("Programs.ServerSide.dashboard.server._audio_streams") as factory:
            for headers, body, status in cases:
                with self.subTest(headers=headers, bytes=len(body)):
                    handler = self.handler(body, headers=headers)
                    handler._receive_pcm_frame(self.session_id)
                    self.assertEqual(handler._send_json.call_args.args[0], status)
            factory.assert_not_called()

    def test_rejected_http11_upload_closes_connection_before_unread_body_is_reused(self):
        cases = [
            ({"Content-Type": "audio/L16"}, 415),
            ({"X-Audio-Sequence": ""}, 400),
            ({"Content-Length": str(MAX_PCM_FRAME_BYTES + 2)}, 413),
            ({"Authorization": "invalid"}, 401),
        ]
        for headers, status in cases:
            with self.subTest(status=status):
                handler = self.handler(headers=headers)
                del handler._send_json
                handler.wfile = io.BytesIO()
                handler.send_response = Mock()
                handler.send_header = Mock()
                handler.end_headers = Mock()
                if status == 401:
                    del handler._api_auth_or_reject
                    handler._authorized = Mock(return_value=False)
                handler._receive_pcm_frame(self.session_id)
                handler.send_response.assert_called_once_with(status)
                self.assertEqual(handler.rfile.tell(), 0)
                self.assertTrue(handler.close_connection)
                self.assertEqual(dict(call.args for call in handler.send_header.call_args_list)["Connection"], "close")

    def test_sequence_gap_exposes_expected_sequence_for_idempotent_retry(self):
        error = VoiceAPIError(409, "audio_sequence_gap", "Retry the missing packet", True)
        error.expected_sequence = 3
        manager = Mock()
        manager.push_audio.side_effect = error
        handler = self.handler(headers={"X-Audio-Sequence": "4"})
        with patch("Programs.ServerSide.dashboard.server._audio_streams", return_value=manager):
            handler._receive_pcm_frame(self.session_id)
        status, payload = handler._send_json.call_args.args
        self.assertEqual((status, payload["expected_sequence"]), (409, 3))

    def test_get_and_playback_notify_server_without_client_speech_decisions(self):
        manager = Mock()
        handler = self.handler()
        handler.path = f"/api/v1/voice/sessions/{self.session_id}/audio-stream"
        with patch("Programs.ServerSide.dashboard.server._audio_streams", return_value=manager):
            handler.do_GET()
        manager.get_status.assert_called_once_with(self.session_id, 0)

        turn_id = str(uuid.uuid4())
        body = json.dumps({"generation": 0, "turn_id": turn_id, "event": "ended"}).encode()
        handler = self.handler(body, headers={"Content-Type": "application/json"})
        with patch("Programs.ServerSide.dashboard.server._audio_streams", return_value=manager):
            handler._receive_playback_event(self.session_id)
        manager.playback.assert_called_once_with(self.session_id, 0, turn_id, "ended")

    def test_malformed_playback_event_does_not_resume_server_listening(self):
        for payload in ([], {"generation": 0, "turn_id": "id", "event": []},
                        {"generation": True, "turn_id": "id", "event": "ended"}):
            with self.subTest(payload=payload):
                handler = self.handler(json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
                with patch("Programs.ServerSide.dashboard.server._audio_streams") as factory:
                    handler._receive_playback_event(self.session_id)
                factory.assert_not_called()
                self.assertEqual(handler._send_json.call_args.args[0], 400)


if __name__ == "__main__":
    unittest.main()
