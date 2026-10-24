"""Keep the pulled NDJSON reply API alongside local PCM and JSON clients."""

from concurrent.futures import Future
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from Programs.ServerSide.dashboard.server import DashboardHandler


class TurnHTTPCompatibilityTests(unittest.TestCase):
    def handler(self, accept):
        handler = object.__new__(DashboardHandler)
        audio = b'RIFF' + bytes(4) + b'WAVE' + bytes(32)
        handler.headers = {'Content-Type': 'audio/wav', 'Content-Length': str(len(audio)), 'Accept': accept}
        handler.rfile = io.BytesIO(audio)
        handler.wfile = io.BytesIO()
        handler.connection = Mock()
        handler._send_json = Mock()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        return handler

    def test_json_and_ndjson_uploads_both_keep_temporary_audio_until_processing(self):
        session_id, turn_id = str(uuid.uuid4()), str(uuid.uuid4())
        session = SimpleNamespace(session_id=session_id)
        paths = []
        for accept in ('application/json', 'application/x-ndjson'):
            with self.subTest(accept=accept):
                handler = self.handler(accept)
                manager = Mock()
                manager.begin_turn.return_value = (session, 2)
                def process(session, generation, turn_id, audio_path):
                    self.assertTrue(audio_path.exists())
                    paths.append(audio_path)
                    return {'response_text': 'reply', 'speech': {'url': '/audio'}}
                manager.process_turn.side_effect = process
                def stream(session, generation, turn_id, audio_path, events):
                    self.assertTrue(audio_path.exists())
                    paths.append(audio_path)
                    events.put({'type': 'transcript', 'generation': generation, 'text': '途中の文字'})
                    events.put({'type': 'audio_chunk', 'generation': generation, 'audio_base64': 'AA=='})
                    events.put({'type': 'complete', 'generation': generation})
                    events.put(None)
                    future = Future()
                    future.set_result(None)
                    return future
                manager.stream_turn.side_effect = stream
                with patch('Programs.ServerSide.dashboard.server._voice_sessions', return_value=manager):
                    handler._receive_voice_turn(session_id, turn_id)
                manager.begin_turn.assert_called_once_with(session_id, turn_id)
                if accept == 'application/json':
                    manager.stream_turn.assert_not_called()
                    self.assertEqual(handler._send_json.call_args.args[0], 200)
                    self.assertEqual(handler._send_json.call_args.args[1]['speech']['url'], '/audio')
                else:
                    manager.process_turn.assert_not_called()
                    headers = dict(call.args for call in handler.send_header.call_args_list)
                    self.assertEqual(headers['Transfer-Encoding'], 'chunked')
                    self.assertTrue(headers['Content-Type'].startswith('application/x-ndjson'))
                    wire = io.BytesIO(handler.wfile.getvalue())
                    events = []
                    while size := int(wire.readline().strip(), 16):
                        events.append(json.loads(wire.read(size)))
                        self.assertEqual(wire.read(2), b'\r\n')
                    self.assertEqual([event['type'] for event in events], ['transcript', 'audio_chunk', 'complete'])
                    self.assertEqual(events[0]['text'], '途中の文字')
                    self.assertTrue(all(event['generation'] == 2 for event in events))
        self.assertTrue(all(not path.exists() for path in paths))


if __name__ == '__main__':
    unittest.main()
