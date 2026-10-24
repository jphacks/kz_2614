"""Real sockets and fake models verify the native voice transport."""
import base64
import json
import io
import wave
import socket
import struct
import threading
import unittest
from unittest.mock import Mock
from server.ai.voice.tcp_server import VoiceTCPServer, receive


def send(sock, value):
    data = json.dumps(value).encode()
    sock.sendall(struct.pack('!I', len(data)) + data)


def reply(sock):
    def exact(n):
        data = b''
        while len(data) < n:
            part = sock.recv(n - len(data))
            if not part:
                raise EOFError
            data += part
        return data
    return json.loads(exact(struct.unpack('!I', exact(4))[0]))


class VoiceTCPTests(unittest.TestCase):
    def setUp(self):
        self.sessions = Mock()
        self.sessions.create_session.return_value = {'session_id': 'session', 'generation': 0}
        self.streams = Mock()
        self.wait = threading.Event()
        self.streams.read_events.side_effect = lambda *a, **k: (self.wait.wait(.02) and []) or []
        self.server = VoiceTCPServer(('127.0.0.1', 0), self.sessions, self.streams)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.sock = socket.create_connection(self.server.server_address, timeout=2)

    def tearDown(self):
        self.sock.close()
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(2)

    def start(self):
        send(self.sock, {'type': 'start', 'version': 1})
        self.assertEqual(reply(self.sock)['type'], 'session')

    def test_pcm_playback_stop(self):
        self.start()
        self.streams.push_audio.return_value = {'next_sequence': 1}
        pcm = b'\x01\x00' * 512
        send(self.sock, {'type': 'pcm', 'sequence': 0, 'audio_base64': base64.b64encode(pcm).decode()})
        self.assertEqual(reply(self.sock)['next_sequence'], 1)
        self.streams.push_audio.assert_called_once_with('session', 0, 0, pcm)
        send(self.sock, {'type': 'playback', 'turn_id': 'turn', 'event': 'started'})
        send(self.sock, {'type': 'stop'})
        self.assertEqual(self.sock.recv(1), b'')
        self.sessions.stop_session.assert_called_once_with('session')
        self.streams.discard.assert_called_once_with('session')
        self.streams.playback.assert_called_once_with('session', 0, 'turn', 'started')

    def test_disconnect_retires_session(self):
        self.start()
        self.sock.shutdown(socket.SHUT_WR)
        self.assertEqual(self.sock.recv(1), b'')
        self.sessions.stop_session.assert_called_once_with('session')

    def test_bad_pcm(self):
        self.start()
        send(self.sock, {'type': 'pcm', 'sequence': 0, 'audio_base64': 'AQ=='})
        self.assertEqual(reply(self.sock)['error'], 'invalid_command')
        self.assertEqual(self.sock.recv(1), b'')
        self.streams.push_audio.assert_not_called()
        self.sessions.stop_session.assert_called_once()

    def test_version(self):
        send(self.sock, {'type': 'start', 'version': 2})
        self.assertEqual(reply(self.sock)['type'], 'error')
        self.sessions.create_session.assert_not_called()

    def test_size_bound(self):
        self.sock.sendall(struct.pack('!I', 48001))
        self.assertEqual(reply(self.sock)['error'], 'invalid_command')
        self.sessions.create_session.assert_not_called()

    def test_fragmented_frame(self):
        left, right = socket.socketpair()
        try:
            data = json.dumps({'type': 'stop'}).encode()
            packet = struct.pack('!I', len(data)) + data
            def fragment():
                for value in packet:
                    left.sendall(bytes([value]))
            worker = threading.Thread(target=fragment)
            worker.start()
            self.assertEqual(receive(right), {'type': 'stop'})
            worker.join()
        finally:
            left.close()
            right.close()

    def test_reply_without_pcm(self):
        sent = threading.Event()
        def events(*a, **k):
            if not sent.is_set():
                sent.set()
                return [{'type': 'audio_chunk', 'cursor': 1}]
            self.wait.wait(.02)
            return []
        self.streams.read_events.side_effect = events
        self.start()
        self.assertEqual(reply(self.sock)['type'], 'audio_chunk')
        self.streams.push_audio.assert_not_called()


class ESP32AudioProfileTests(unittest.TestCase):
    @staticmethod
    def wav(rate=24000, channels=2, samples=2400):
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as audio:
            audio.setnchannels(channels)
            audio.setsampwidth(2)
            audio.setframerate(rate)
            audio.writeframes(struct.pack('<h', 2048) * samples * channels)
        return buffer.getvalue()

    def test_downmix_and_resample_preserve_event_identity(self):
        from server.ai.voice.tcp_server import esp32_audio_event
        value = {'type': 'audio_chunk', 'turn_id': 'turn', 'cursor': 5,
                 'audio_base64': base64.b64encode(self.wav()).decode()}
        result = esp32_audio_event(value)
        self.assertEqual(result['sample_rate'], 16000)
        self.assertEqual(result['channels'], 1)
        self.assertEqual(result['format'], 's16le')
        self.assertEqual(result['cursor'], 5)
        self.assertEqual(result['turn_id'], 'turn')
        self.assertEqual(len(base64.b64decode(result['audio_base64'])), 3200)
        self.assertNotIn('format', value)  # Android event remains unchanged

    def test_long_segment_fails_instead_of_silent_truncation(self):
        from server.ai.voice.tcp_server import esp32_audio_event
        from server.ai.voice.sessions import VoiceAPIError
        with self.assertRaises(VoiceAPIError) as caught:
            esp32_audio_event({'type': 'acknowledgement', 'audio_base64': base64.b64encode(self.wav(16000, 1, 270000)).decode()})
        self.assertEqual(caught.exception.code, 'device_audio_too_large')

    def test_malformed_audio_and_non_audio_events(self):
        from server.ai.voice.tcp_server import esp32_audio_event
        from server.ai.voice.sessions import VoiceAPIError
        with self.assertRaises(VoiceAPIError) as caught:
            esp32_audio_event({'type': 'audio_chunk', 'audio_base64': 'invalid'})
        self.assertEqual(caught.exception.code, 'device_audio_conversion_failed')
        complete = {'type': 'complete', 'cursor': 6}
        self.assertIs(esp32_audio_event(complete), complete)


class ESP32TransportTests(unittest.TestCase):
    setUp = VoiceTCPTests.setUp
    tearDown = VoiceTCPTests.tearDown
    def test_profile_handshake_and_pcm_reply(self):
        audio = ESP32AudioProfileTests.wav()
        first = threading.Event()
        def events(*a, **k):
            if not first.is_set():
                first.set()
                return [{'type': 'audio_chunk', 'session_id': 'session', 'generation': 0,
                         'turn_id': 'turn', 'cursor': 1, 'media_type': 'audio/wav',
                         'audio_base64': base64.b64encode(audio).decode()}]
            self.wait.wait(.02)
            return []
        self.streams.read_events.side_effect = events
        send(self.sock, {'type': 'start', 'version': 1, 'audio_profile': 'pcm_s16le_16000'})
        self.assertEqual(reply(self.sock)['audio_profile'], 'pcm_s16le_16000')
        event = reply(self.sock)
        self.assertEqual(event['media_type'], 'audio/pcm')
        self.assertEqual(len(base64.b64decode(event['audio_base64'])), 3200)
        send(self.sock, {'type': 'stop'})
        self.assertEqual(self.sock.recv(1), b'')
        self.sessions.stop_session.assert_called_once()

    def test_unknown_profile_rejected(self):
        send(self.sock, {'type': 'start', 'version': 1, 'audio_profile': 'unknown'})
        self.assertEqual(reply(self.sock)['error'], 'invalid_command')
        self.sessions.create_session.assert_not_called()

    def test_audio_conversion_error_closes_session(self):
        first = threading.Event()
        def events(*a, **k):
            if not first.is_set():
                first.set()
                return [{'type': 'audio_chunk', 'audio_base64': 'invalid', 'cursor': 1}]
            return []
        self.streams.read_events.side_effect = events
        send(self.sock, {'type': 'start', 'version': 1, 'audio_profile': 'pcm_s16le_16000'})
        self.assertEqual(reply(self.sock)['type'], 'session')
        self.assertEqual(reply(self.sock)['error'], 'device_audio_conversion_failed')
        self.assertEqual(self.sock.recv(1), b'')
