"""Reply chunks arrive before completion while PCM continues on another request."""

import http.client
import io
import json
import threading
import unittest
import uuid
from unittest.mock import Mock, patch

from Programs.ServerSide.dashboard.server import DashboardHandler, DashboardHTTPServer
from Programs.ServerSide.STTLLMTTS.voice.sessions import VoiceAPIError


MODULE = "Programs.ServerSide.dashboard.server"


def chunked_events(data):
    source = io.BytesIO(data)
    result = []
    while size := int(source.readline().strip(), 16):
        result.append(json.loads(source.read(size)))
        assert source.read(2) == b"\r\n"
    return result


class PCMEventHTTPTests(unittest.TestCase):
    def handler(self, after="0", authorized=True):
        handler = object.__new__(DashboardHandler)
        handler.path = f"/api/v1/voice/sessions/{uuid.uuid4()}/audio-stream/events?after={after}"
        handler.headers = {"X-Voice-Generation": "0"}
        handler.connection = Mock()
        handler.wfile = io.BytesIO()
        handler._api_auth_or_reject = Mock(return_value=authorized)
        handler._send_json = Mock()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        return handler

    def test_auth_cursor_generation_and_reader_limits_precede_stream_headers(self):
        for after in ("-1", "", "NaN", "1&after=2", "9007199254740992"):
            with self.subTest(after=after):
                handler = self.handler(after)
                with patch(f"{MODULE}._audio_streams") as factory:
                    handler.do_GET()
                factory.assert_not_called()
                self.assertEqual(handler._send_json.call_args.args[0], 400)
                handler.send_response.assert_not_called()
        handler = self.handler(authorized=False)
        with patch(f"{MODULE}._audio_streams") as factory:
            handler.do_GET()
        factory.assert_not_called()

        handler = self.handler()
        handler.headers = {}
        with patch(f"{MODULE}._audio_streams") as factory:
            handler.do_GET()
        factory.assert_not_called()
        self.assertEqual(handler._send_json.call_args.args[0], 400)

        handler = self.handler()
        with patch(f"{MODULE}._AUDIO_EVENT_READERS", threading.BoundedSemaphore(0)):
            handler.do_GET()
        self.assertEqual(handler._send_json.call_args.args[0], 503)

    def test_expired_cursor_is_json_before_headers_and_does_not_leak_reader_slot(self):
        handler = self.handler("7")
        streams = Mock()
        streams.read_events.side_effect = VoiceAPIError(409, "events_expired", "Reconnect is too old")
        slots = threading.BoundedSemaphore(1)
        with patch(f"{MODULE}._audio_streams", return_value=streams), patch(f"{MODULE}._AUDIO_EVENT_READERS", slots):
            handler.do_GET()
        self.assertEqual(handler._send_json.call_args.args[0], 409)
        handler.send_response.assert_not_called()
        self.assertTrue(slots.acquire(blocking=False))

    def test_ndjson_cursor_resume_flushes_unicode_and_stop_error_without_cancelling_turn(self):
        handler = self.handler("7")
        streams = Mock()
        streams.read_events.side_effect = [
            [{"type": "text_delta", "cursor": 8, "text": "返事の途中"},
             {"type": "audio_chunk", "cursor": 9, "audio_base64": "AA=="}],
            VoiceAPIError(404, "session_not_found", "Stopped"),
        ]
        with patch(f"{MODULE}._audio_streams", return_value=streams):
            handler.do_GET()
        events = chunked_events(handler.wfile.getvalue())
        self.assertEqual([event["type"] for event in events], ["text_delta", "audio_chunk", "error"])
        self.assertEqual(events[0]["text"], "返事の途中")
        self.assertEqual(events[-1]["cursor"], 9)
        self.assertEqual(streams.read_events.call_args.args[2], 9)
        self.assertTrue(handler.close_connection)
        streams.discard.assert_not_called()

    def test_heartbeat_has_no_new_cursor_and_normal_connection_rotation_finishes(self):
        handler = self.handler("7")
        streams = Mock()
        streams.read_events.return_value = []
        with patch(f"{MODULE}._audio_streams", return_value=streams), patch(f"{MODULE}.AUDIO_EVENT_STREAM_SECONDS", 0):
            handler.do_GET()
        events = chunked_events(handler.wfile.getvalue())
        self.assertEqual([(event["type"], event["cursor"]) for event in events], [("heartbeat", 7)])
        self.assertTrue(handler.wfile.getvalue().endswith(b"0\r\n\r\n"))

    def test_disconnected_output_releases_reader_slot_without_stopping_input_or_models(self):
        handler = self.handler()
        handler.wfile = Mock()
        handler.wfile.write.side_effect = BrokenPipeError
        streams = Mock()
        streams.read_events.return_value = []
        slots = threading.BoundedSemaphore(1)
        with patch(f"{MODULE}._audio_streams", return_value=streams), patch(f"{MODULE}._AUDIO_EVENT_READERS", slots):
            handler.do_GET()
        self.assertTrue(slots.acquire(blocking=False))
        streams.discard.assert_not_called()
        streams.close.assert_not_called()


class BlockingReply:
    def __init__(self, session_id):
        self.session_id = session_id
        self.condition = threading.Condition()
        self.completed = False
        self.samples_received = 0

    def read_events(self, session_id, generation, after, *, timeout):
        identity = {"session_id": session_id, "generation": generation, "turn_id": "turn"}
        if after == 0:
            return [{**identity, "cursor": 1, "type": "text_delta", "text": "First phrase."},
                    {**identity, "cursor": 2, "type": "audio_chunk", "audio_base64": "AA=="}]
        with self.condition:
            self.condition.wait_for(lambda: self.completed, timeout=timeout)
            if not self.completed:
                return []
        if after == 2:
            return [{**identity, "cursor": 3, "type": "complete"}]
        raise VoiceAPIError(404, "session_not_found", "Test finished")

    def push_audio(self, session_id, generation, sequence, audio, *, input_gain=1.0):
        self.samples_received += len(audio) // 2
        return {"session_id": session_id, "generation": generation, "sequence": sequence,
                "next_sequence": sequence + 1, "stage": "tts"}

    def complete(self):
        with self.condition:
            self.completed = True
            self.condition.notify_all()


class PCMEventConcurrencyTests(unittest.TestCase):
    def test_reply_is_readable_before_completion_and_silence_upload_is_not_blocked(self):
        session_id = str(uuid.uuid4())
        streams = BlockingReply(session_id)
        server = DashboardHTTPServer(("127.0.0.1", 0), "test-token")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        reader = http.client.HTTPConnection(*server.server_address, timeout=3)
        sender = http.client.HTTPConnection(*server.server_address, timeout=3)
        route = f"/api/v1/voice/sessions/{session_id}/audio-stream"
        headers = {"Authorization": "Bearer test-token", "X-Voice-Generation": "0"}
        try:
            with patch(f"{MODULE}._audio_streams", return_value=streams):
                thread.start()
                reader.request("GET", f"{route}/events?after=0", headers=headers)
                response = reader.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.readline())["type"], "text_delta")
                self.assertEqual(json.loads(response.readline())["type"], "audio_chunk")
                self.assertFalse(streams.completed)
                for sequence in range(3):
                    sender.request("POST", route, bytes(8192), headers={
                        **headers, "Content-Type": "audio/pcm;rate=16000;channels=1;format=s16le",
                        "X-Audio-Sequence": str(sequence),
                    })
                    ack = sender.getresponse()
                    self.assertEqual(ack.status, 200)
                    self.assertEqual(json.loads(ack.read())["next_sequence"], sequence + 1)
                self.assertEqual(streams.samples_received, 3 * 4096)
                streams.complete()
                self.assertEqual(json.loads(response.readline())["type"], "complete")
                self.assertEqual(json.loads(response.readline())["type"], "error")
                self.assertEqual(response.read(), b"")
        finally:
            streams.complete()
            reader.close()
            sender.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
