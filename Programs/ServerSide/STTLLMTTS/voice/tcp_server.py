"""Native voice transport: uint32 network-order length + UTF-8 JSON, no HTTP."""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import io
import socket
import socketserver
import struct
import threading
from pathlib import Path

from dotenv import load_dotenv
from .sessions import VoiceAPIError, VoiceSessions
from .pcm_stream import ServerAudioStreams
from .audio_frames import PCM_STREAM_PROFILE, iter_audio_frames

MAX_PACKET = 24 * 1024 * 1024
MAX_COMMAND = 48_000


def receive(sock: socket.socket) -> dict:
    def exact(size):
        data = bytearray()
        while len(data) < size:
            part = sock.recv(size - len(data))
            if not part:
                raise EOFError
            data.extend(part)
        return bytes(data)
    size = struct.unpack('!I', exact(4))[0]
    if not 0 < size <= MAX_COMMAND:
        raise ValueError('invalid packet size')
    value = json.loads(exact(size))
    if not isinstance(value, dict):
        raise ValueError('object required')
    return value


def esp32_audio_event(event: dict) -> dict:
    """Convert an existing audio event without adding or splitting TTS segments."""
    if event.get('type') not in {'audio_chunk', 'acknowledgement'}:
        return event
    import av
    pcm = bytearray()
    try:
        audio = base64.b64decode(event['audio_base64'], validate=True)
        with av.open(io.BytesIO(audio)) as container:
            resampler = av.AudioResampler(format='s16', layout='mono', rate=16000)
            def append(frames):
                for frame in frames:
                    pcm.extend(frame.to_ndarray().tobytes())
                    if len(pcm) > 512 * 1024:
                        raise VoiceAPIError(413, 'device_audio_too_large', 'ESP32の文節音声上限を超えました。')
            for frame in container.decode(audio=0):
                append(resampler.resample(frame))
            append(resampler.resample(None))
        if not pcm:
            raise ValueError('empty audio')
    except VoiceAPIError:
        raise
    except Exception as exc:
        raise VoiceAPIError(500, 'device_audio_conversion_failed', 'ESP32向け音声へ変換できません。') from exc
    return {**event, 'audio_base64': base64.b64encode(pcm).decode('ascii'),
            'media_type': 'audio/pcm', 'sample_rate': 16000, 'channels': 1, 'format': 's16le'}


class VoiceTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, sessions=None, streams=None):
        self.sessions = sessions if sessions is not None else VoiceSessions()
        self.streams = streams if streams is not None else ServerAudioStreams(self.sessions)
        self.slots = threading.BoundedSemaphore(4)
        super().__init__(address, VoiceConnection)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class VoiceConnection(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(15)
        self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.write_lock = threading.Lock()
        self.done = threading.Event()
        self.session_id = None
        self.esp32 = False
        self.pcm_streaming = False
        self.interrupted_turns = set()
        self.max_packet = MAX_PACKET
        pump = None
        try:
            hello = receive(self.request)
            if (hello.get('type') != 'start' or type(hello.get('version')) is not int
                    or hello['version'] != 1 or set(hello) - {'type', 'version', 'audio_profile'}):
                raise ValueError('start/version required')
            profile = hello.get('audio_profile')
            if profile not in (None, 'pcm_s16le_16000', PCM_STREAM_PROFILE):
                raise ValueError('unsupported audio profile')
            self.pcm_streaming = profile == PCM_STREAM_PROFILE
            self.esp32 = profile is not None
            self.max_packet = 1024 * 1024 if self.esp32 else MAX_PACKET
            session = self.server.sessions.create_session()
            self.session_id = session['session_id']
            self.generation = session['generation']
            self.send({'type': 'session', **session, 'audio_profile': profile or 'original'})
            pump = threading.Thread(target=self.events, daemon=True)
            pump.start()
            while not self.done.is_set():
                message = receive(self.request)
                kind = message.get('type')
                if kind == 'stop':
                    break
                if kind == 'pcm':
                    sequence = message.get('sequence')
                    if type(sequence) is not int or sequence < 0:
                        raise ValueError('invalid sequence')
                    pcm = base64.b64decode(message['audio_base64'], validate=True)
                    if not 0 < len(pcm) <= 32000 or len(pcm) % 2:
                        raise ValueError('invalid PCM frame')
                    state = self.server.streams.push_audio(self.session_id, self.generation, sequence, pcm)
                    self.remember_interruption(state)
                    self.send({'type': 'state', **state})
                elif kind == 'playback':
                    turn, event = message.get('turn_id'), message.get('event')
                    if not isinstance(turn, str) or event not in ('started', 'ended', 'failed'):
                        raise ValueError('invalid playback')
                    try:
                        self.server.streams.playback(self.session_id, self.generation, turn, event)
                    except VoiceAPIError as exc:
                        if exc.code != 'playback_turn_mismatch' or turn not in self.interrupted_turns:
                            raise
                else:
                    raise ValueError('unknown command')
        except VoiceAPIError as exc:
            self.error(exc.code, exc.message)
        except (ValueError, KeyError, TypeError, binascii.Error):
            self.error('invalid_command', '通信形式を確認してください。')
        except (OSError, EOFError):
            pass
        finally:
            self.done.set()
            if self.session_id:
                try:
                    self.server.sessions.stop_session(self.session_id)
                except VoiceAPIError:
                    pass
                self.server.streams.discard(self.session_id)
            try:
                self.request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            if pump:
                pump.join(timeout=2)

    def send(self, value):
        payload = json.dumps(value, ensure_ascii=False).encode('utf-8')
        if len(payload) > self.max_packet:
            raise ValueError('reply too large')
        with self.write_lock:
            if not self.done.is_set():
                self.request.sendall(struct.pack('!I', len(payload)) + payload)

    def error(self, code, message):
        try:
            self.send({'type': 'error', 'error': code, 'message': message})
        except OSError:
            pass

    def remember_interruption(self, value):
        interruption = value if value.get('type') == 'interruption' else value.get('interruption')
        if isinstance(interruption, dict):
            turn = interruption.get('interrupted_turn_id')
            if isinstance(turn, str) and turn:
                self.interrupted_turns.add(turn)

    def events(self):
        cursor = 0
        try:
            while not self.done.is_set():
                for event in self.server.streams.read_events(self.session_id, self.generation, cursor, timeout=1):
                    self.remember_interruption(event)
                    if self.pcm_streaming:
                        for frame in iter_audio_frames(event):
                            self.send(frame)
                    else:
                        self.send(esp32_audio_event(event) if self.esp32 else event)
                    cursor = event['cursor']
        except VoiceAPIError as exc:
            self.error(exc.code, exc.message)
            self.done.set()
            try:
                self.request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        except (OSError, ValueError):
            self.done.set()
            try:
                self.request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def main():
    load_dotenv(Path(__file__).resolve().parents[2] / '.env', override=False)
    parser = argparse.ArgumentParser(description='Native TCP voice server')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    with VoiceTCPServer((args.host, args.port)) as server:
        print(f'Voice TCP listening on {args.host}:{args.port}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.streams.close()


if __name__ == '__main__':
    main()
