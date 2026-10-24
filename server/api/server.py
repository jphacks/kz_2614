"""Serve the local settings dashboard and its small JSON settings API."""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import math
import mimetypes
import re
import tempfile
import threading
import time
from concurrent.futures import CancelledError as FutureCancelledError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Empty, Queue
from urllib.parse import parse_qs, unquote, urlsplit

from dotenv import load_dotenv

from .settings_store import (
    MAX_REFERENCE_WAV_BYTES,
    delete_reference_voice,
    get_dashboard_settings,
    get_reference_voice_info,
    get_settings_defaults,
    save_reference_voice,
    save_settings,
)
from .tail_settings import get_tail_frequency, set_tail_frequency
from ..ai.voice.sessions import (
    MAX_AUDIO_BYTES,
    TURN_TIMEOUT_SECONDS,
    VoiceAPIError,
    VoiceSessions,
)


WEB_ROOT = Path(__file__).resolve().parents[2] / "apps" / "web"
MAX_REQUEST_BYTES = 16_384
LOGGER = logging.getLogger("nen_gitsune.dashboard")
_VOICE_SESSIONS: VoiceSessions | None = None
_VOICE_SESSIONS_LOCK = threading.Lock()
_AUDIO_STREAMS = None
_AUDIO_STREAMS_LOCK = threading.Lock()
_AUDIO_EVENT_READERS = threading.BoundedSemaphore(8)
AUDIO_EVENT_STREAM_SECONDS = 25.0
AUDIO_EVENT_HEARTBEAT_SECONDS = 10.0
MAX_PCM_FRAME_BYTES = 32_000
_SESSION_STREAM_ROUTE = re.compile(r"^/api/v1/voice/sessions/([0-9a-fA-F-]{36})/audio-stream$")
_SESSION_EVENTS_ROUTE = re.compile(r"^/api/v1/voice/sessions/([0-9a-fA-F-]{36})/audio-stream/events$")
_SESSION_PLAYBACK_ROUTE = re.compile(r"^/api/v1/voice/sessions/([0-9a-fA-F-]{36})/audio-stream/playback$")
_SESSION_TURN_ROUTE = re.compile(
    r"^/api/v1/voice/sessions/([0-9a-fA-F-]{36})/turns/([0-9a-fA-F-]{36})$"
)
_SESSION_AUDIO_ROUTE = re.compile(
    r"^/api/v1/voice/sessions/([0-9a-fA-F-]{36})/turns/([0-9a-fA-F-]{36})/audio$"
)
_SESSION_PROGRESS_ROUTE = re.compile(
    r"^/api/v1/voice/sessions/([0-9a-fA-F-]{36})/turns/([0-9a-fA-F-]{36})/status$"
)
_SESSION_TRANSCRIPTION_ROUTE = re.compile(
    r"^/api/v1/voice/sessions/([0-9a-fA-F-]{36})/transcription$"
)
_SESSION_ROUTE = re.compile(r"^/api/v1/voice/sessions/([0-9a-fA-F-]{36})$")


def _voice_sessions() -> VoiceSessions:
    global _VOICE_SESSIONS
    with _VOICE_SESSIONS_LOCK:
        if _VOICE_SESSIONS is None:
            _VOICE_SESSIONS = VoiceSessions()
        return _VOICE_SESSIONS


def _audio_streams():
    global _AUDIO_STREAMS
    with _AUDIO_STREAMS_LOCK:
        if _AUDIO_STREAMS is None:
            from ..ai.voice.pcm_stream import ServerAudioStreams

            _AUDIO_STREAMS = ServerAudioStreams(_voice_sessions())
        return _AUDIO_STREAMS


def _audio_upload_format(content_type: str) -> tuple[str, str] | None:
    mime_type = content_type.split(";", 1)[0].strip().lower()
    formats = {
        "audio/webm": ("audio/webm", ".webm"),
        "audio/ogg": ("audio/ogg", ".ogg"),
        "audio/wav": ("audio/wav", ".wav"),
        "audio/x-wav": ("audio/wav", ".wav"),
        "audio/mp4": ("audio/mp4", ".m4a"),
    }
    return formats.get(mime_type)


def _has_audio_signature(audio: bytes, media_type: str) -> bool:
    if media_type == "audio/webm":
        return audio.startswith(b"\x1a\x45\xdf\xa3")
    if media_type == "audio/ogg":
        return audio.startswith(b"OggS")
    if media_type == "audio/wav":
        return len(audio) >= 12 and audio[:4] == b"RIFF" and audio[8:12] == b"WAVE"
    if media_type == "audio/mp4":
        return len(audio) >= 12 and audio[4:8] == b"ftyp"
    return False


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "NenGitsuneDashboard/1.0"
    protocol_version = "HTTP/1.1"

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        # HTTP/1.1 errors may reject an upload before its body is consumed.
        # Closing prevents those remaining bytes becoming the next request.
        if status >= 400:
            self.close_connection = True
            self.send_header("Connection", "close")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; "
            "font-src 'self'; media-src 'self' blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, value: object) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        route = urlsplit(self.path).path
        if route == "/api/v1/tail/frequency":
            try:
                self._send_json(200, get_tail_frequency())
            except (OSError, RuntimeError, ValueError):
                self._send_json(500, {"error": "tail_settings_unavailable", "message": "トントンの頻度を取得できません。"})
            return
        events_match = _SESSION_EVENTS_ROUTE.fullmatch(route)
        if events_match:
            self._stream_audio_events(events_match.group(1))
            return
        stream_match = _SESSION_STREAM_ROUTE.fullmatch(route)
        if stream_match:
            try:
                generation = self._stream_header_integer("X-Voice-Generation")
                self._send_json(200, _audio_streams().get_status(stream_match.group(1), generation))
            except VoiceAPIError as exc:
                self._send_voice_error(exc)
            return
        if route == "/api/v1/dashboard/settings":
            try:
                self._send_json(
                    200,
                    {
                        "settings": get_dashboard_settings(),
                        "defaults": get_settings_defaults(),
                        "applies": "new_pipeline_instance",
                    },
                )
            except (RuntimeError, ValueError):
                LOGGER.exception("Unable to load dashboard settings")
                self._send_json(500, {"error": "settings_unavailable", "message": "設定を読み込めません。"})
            return

        if route == "/api/v1/dashboard/health":
            self._send_json(200, {"status": "ok", "settings_api": "ready"})
            return

        if route == "/api/v1/dashboard/runtime":
            try:
                from .runtime_status import get_runtime_status

                # Observing status must not create a session, load a model, expire
                # a conversation, or reset any microphone/VAD state.
                with _VOICE_SESSIONS_LOCK:
                    manager = _VOICE_SESSIONS
                with _AUDIO_STREAMS_LOCK:
                    streams = _AUDIO_STREAMS
                status = get_runtime_status(manager)
                live_ids = manager.runtime_session_ids() if manager is not None else set()
                status["vad"] = {
                    "configured": get_dashboard_settings()["vad"],
                    "active": streams.runtime_snapshot(live_ids) if streams is not None else [],
                }
                self._send_json(200, status)
            except (RuntimeError, ValueError, OSError):
                LOGGER.warning("Runtime status could not be read")
                self._send_json(503, {"error": "runtime_unavailable", "message": "稼働状態を取得できません。設定を確認してください。"})
            return

        if route == "/api/v1/dashboard/reference-audio":
            try:
                self._send_json(200, get_reference_voice_info())
            except (RuntimeError, ValueError):
                LOGGER.exception("Unable to read reference WAV status")
                self._send_json(500, {"error": "reference_audio_unavailable", "message": "参照音声を読み取れません。"})
            return

        if route == "/api/v1/voice/status":
            try:
                self._send_json(200, _voice_sessions().status())
            except VoiceAPIError as exc:
                self._send_voice_error(exc)
            return

        progress_match = _SESSION_PROGRESS_ROUTE.fullmatch(route)
        if progress_match:
            try:
                self._send_json(200, _voice_sessions().get_turn_status(*progress_match.groups()))
            except VoiceAPIError as exc:
                self._send_voice_error(exc)
            return

        audio_match = _SESSION_AUDIO_ROUTE.fullmatch(route)
        if audio_match:
            try:
                audio, media_type = _voice_sessions().get_speech(*audio_match.groups())
                self._send_bytes(200, audio, media_type)
            except VoiceAPIError as exc:
                self._send_voice_error(exc)
            return

        self._serve_static(route)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        route = urlsplit(self.path).path
        stream_match = _SESSION_STREAM_ROUTE.fullmatch(route)
        if stream_match:
            self._receive_pcm_frame(stream_match.group(1))
            return
        playback_match = _SESSION_PLAYBACK_ROUTE.fullmatch(route)
        if playback_match:
            self._receive_playback_event(playback_match.group(1))
            return
        if route == "/api/v1/voice/sessions":
            try:
                self._send_json(201, _voice_sessions().create_session())
            except VoiceAPIError as exc:
                self._send_voice_error(exc)
            return

        transcription_match = _SESSION_TRANSCRIPTION_ROUTE.fullmatch(route)
        if transcription_match:
            self._receive_transcription(transcription_match.group(1))
            return

        turn_match = _SESSION_TURN_ROUTE.fullmatch(route)
        if turn_match:
            self._receive_voice_turn(*turn_match.groups())
            return

        self._send_json(404, {"error": "not_found"})

    def _stream_header_integer(self, name: str) -> int:
        try:
            value = int(self.headers.get(name, ""))
            if not 0 <= value <= 9_007_199_254_740_991:
                raise ValueError
            return value
        except (TypeError, ValueError) as exc:
            raise VoiceAPIError(400, "invalid_stream_header", f"{name}に0以上の整数を指定してください。") from exc

    def _stream_body(self, maximum: int) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise VoiceAPIError(400, "invalid_content_length", "本文の長さを確認してください。") from exc
        if length <= 0 or length > maximum:
            raise VoiceAPIError(413, "stream_frame_too_large", f"本文は1〜{maximum}バイトにしてください。")
        try:
            self.connection.settimeout(10.0)
            body = self.rfile.read(length)
            if len(body) != length:
                raise VoiceAPIError(400, "incomplete_stream_frame", "音声データが途中で終了しました。")
            return body
        except OSError as exc:
            raise VoiceAPIError(408, "stream_upload_timeout", "音声の受信がタイムアウトしました。", True) from exc
        finally:
            self.connection.settimeout(None)

    def _stream_audio_events(self, session_id: str) -> None:
        """Receive reply audio independently of the continuously uploaded PCM."""
        acquired = False
        started = False
        cursor = 0
        generation = 0
        try:
            from ..ai.voice.audio_frames import PCM_STREAM_PROFILE, iter_audio_frames
            audio_profile = self.headers.get("X-Voice-Audio-Profile", "")
            if audio_profile not in {"", PCM_STREAM_PROFILE}:
                raise VoiceAPIError(400, "invalid_audio_profile", "返答音声の送信形式を確認してください。")
            generation = self._stream_header_integer("X-Voice-Generation")
            values = parse_qs(urlsplit(self.path).query, keep_blank_values=True).get("after", ["0"])
            try:
                if len(values) != 1 or not values[0].isascii() or not values[0].isdecimal():
                    raise ValueError
                cursor = int(values[0])
                if cursor > 9_007_199_254_740_991:
                    raise ValueError
            except ValueError as exc:
                raise VoiceAPIError(400, "invalid_event_cursor", "返答の受信位置を確認してください。") from exc
            acquired = _AUDIO_EVENT_READERS.acquire(blocking=False)
            if not acquired:
                raise VoiceAPIError(503, "event_readers_busy", "返答の接続が混み合っています。", True)
            streams = _audio_streams()
            pending = streams.read_events(session_id, generation, cursor, timeout=0)
            self.connection.settimeout(15.0)
            self._begin_ndjson_response()
            started = True
            deadline = time.monotonic() + AUDIO_EVENT_STREAM_SECONDS
            while True:
                if pending:
                    for event in pending:
                        outgoing = iter_audio_frames(event) if audio_profile else (event,)
                        for frame in outgoing:
                            self._write_ndjson_event(frame)
                        cursor = int(event["cursor"])
                else:
                    self._write_ndjson_event({
                        "type": "heartbeat", "session_id": session_id,
                        "generation": generation, "turn_id": None, "cursor": cursor,
                    })
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                pending = streams.read_events(
                    session_id, generation, cursor,
                    timeout=min(AUDIO_EVENT_HEARTBEAT_SECONDS, remaining),
                )
        except VoiceAPIError as exc:
            if not started:
                self._send_voice_error(exc)
            else:
                try:
                    self._write_ndjson_event({
                        "type": "error", "session_id": session_id,
                        "generation": generation, "turn_id": None, "cursor": cursor,
                        "error": exc.code, "message": exc.message, "retryable": exc.retryable,
                    })
                except OSError:
                    pass
        except OSError:
            # The input connection and model work survive a disconnected output reader.
            LOGGER.info("PCM reply receiver disconnected")
        finally:
            if started:
                self._finish_ndjson_response()
            if acquired:
                _AUDIO_EVENT_READERS.release()

    def _begin_ndjson_response(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-store, no-transform")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True

    def _write_ndjson_event(self, event: dict[str, object]) -> None:
        body = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        self.wfile.write(f"{len(body):X}\r\n".encode("ascii"))
        self.wfile.write(body)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def _finish_ndjson_response(self) -> None:
        try:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except OSError:
            pass

    def _receive_pcm_frame(self, session_id: str) -> None:
        try:
            parts = [part.strip().lower() for part in self.headers.get("Content-Type", "").split(";")]
            parameters = dict(part.split("=", 1) for part in parts[1:] if "=" in part)
            if (parts[0] != "audio/pcm" or parameters.get("rate") != "16000"
                    or parameters.get("channels") != "1" or parameters.get("format") != "s16le"):
                raise VoiceAPIError(415, "unsupported_pcm_format", "PCM16LE、16kHz、モノラルを指定してください。")
            generation = self._stream_header_integer("X-Voice-Generation")
            sequence = self._stream_header_integer("X-Audio-Sequence")
            try:
                input_gain = float(self.headers.get("X-Audio-Input-Gain", "1"))
                if not math.isfinite(input_gain) or not 0.25 <= input_gain <= 2:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise VoiceAPIError(400, "invalid_input_gain", "入力音量は25〜200%で指定してください。") from exc
            body = self._stream_body(MAX_PCM_FRAME_BYTES)
            if len(body) % 2:
                raise VoiceAPIError(400, "invalid_pcm_frame", "PCM16LEの音声は偶数バイトにしてください。")
            self._send_json(200, _audio_streams().push_audio(
                session_id, generation, sequence, body, input_gain=input_gain,
            ))
        except VoiceAPIError as exc:
            self._send_voice_error(exc)

    def _receive_playback_event(self, session_id: str) -> None:
        try:
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise VoiceAPIError(415, "unsupported_playback_format", "再生通知はapplication/jsonで送信してください。")
            try:
                payload = json.loads(self._stream_body(MAX_REQUEST_BYTES))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise VoiceAPIError(400, "invalid_playback_event", "再生通知のJSONを確認してください。") from exc
            if not isinstance(payload, dict):
                raise VoiceAPIError(400, "invalid_playback_event", "再生通知はJSONオブジェクトにしてください。")
            generation, turn_id, event = payload.get("generation"), payload.get("turn_id"), payload.get("event")
            if (type(generation) is not int or generation < 0 or not isinstance(turn_id, str)
                    or not isinstance(event, str) or event not in {"started", "ended", "failed"}):
                raise VoiceAPIError(400, "invalid_playback_event", "世代・発話ID・再生イベントを確認してください。")
            self._send_json(200, _audio_streams().playback(session_id, generation, turn_id, event))
        except VoiceAPIError as exc:
            self._send_voice_error(exc)

    def _receive_transcription(self, session_id: str) -> None:
        try:
            raw_generation = self.headers.get("X-Voice-Generation")
            if raw_generation is None:
                raise ValueError
            generation = int(raw_generation)
            if generation < 0:
                raise ValueError
        except ValueError:
            self._send_json(400, {"error": "invalid_generation", "message": "X-Voice-Generationを指定してください。"})
            return
        audio_format = _audio_upload_format(self.headers.get("Content-Type", ""))
        if audio_format is None:
            self._send_json(415, {"error": "unsupported_audio_format"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "invalid_content_length"})
            return
        if length <= 0 or length > MAX_AUDIO_BYTES:
            self._send_json(413, {"error": "audio_too_large", "max_bytes": MAX_AUDIO_BYTES})
            return
        try:
            self.connection.settimeout(30.0)
            audio = self.rfile.read(length)
            if len(audio) != length:
                self._send_json(400, {"error": "incomplete_audio_upload"})
                return
        except OSError:
            self._send_json(408, {"error": "audio_upload_timeout", "retryable": True})
            return
        finally:
            self.connection.settimeout(None)
        media_type, suffix = audio_format
        if not _has_audio_signature(audio, media_type):
            self._send_json(400, {"error": "invalid_audio", "retryable": True})
            return
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix="nen-gitsune-preview-", suffix=suffix, delete=False) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(audio)
            # Ownership passes to the manager, including on HTTP timeout: its native
            # worker may not have decoded this file yet, so the handler cannot unlink it.
            manager = _voice_sessions()
            owned_path = temporary_path
            temporary_path = None
            response = manager.transcribe_preview(session_id, generation, owned_path)
            self._send_json(200, response)
        except VoiceAPIError as exc:
            self._send_voice_error(exc)
        except OSError:
            self._send_json(500, {"error": "temporary_audio_unavailable"})
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _receive_voice_turn(self, session_id: str, turn_id: str) -> None:
        audio_format = _audio_upload_format(self.headers.get("Content-Type", ""))
        if audio_format is None:
            self._send_json(
                415,
                {"error": "unsupported_audio_format", "message": "WebM、Ogg、WAV、MP4音声を送信してください。"},
            )
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "invalid_content_length"})
            return
        if length <= 0 or length > MAX_AUDIO_BYTES:
            self._send_json(
                413,
                {"error": "audio_too_large", "message": "音声は25MB以下にしてください。", "max_bytes": MAX_AUDIO_BYTES},
            )
            return

        try:
            self.connection.settimeout(30.0)
            audio = self.rfile.read(length)
            if len(audio) != length:
                self._send_json(400, {"error": "incomplete_audio_upload"})
                return
        except OSError:
            self._send_json(408, {"error": "audio_upload_timeout", "retryable": True})
            return
        finally:
            self.connection.settimeout(None)

        media_type, suffix = audio_format
        if not _has_audio_signature(audio, media_type):
            self._send_json(400, {"error": "invalid_audio", "message": "音声ファイルを読み取れません。"})
            return

        manager = _voice_sessions()
        try:
            session, generation = manager.begin_turn(session_id, turn_id)
        except VoiceAPIError as exc:
            self._send_voice_error(exc)
            return

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix="nen-gitsune-voice-", suffix=suffix, delete=False) as temporary_file:
                temporary_file.write(audio)
                temporary_path = Path(temporary_file.name)
            if "application/x-ndjson" in self.headers.get("Accept", "").lower():
                self._stream_voice_turn(manager, session, generation, turn_id, temporary_path)
                return
            response = manager.process_turn(session, generation, turn_id, temporary_path)
            self._send_json(200, response)
        except VoiceAPIError as exc:
            self._send_voice_error(exc)
        except OSError:
            manager.abandon_turn(session, turn_id=turn_id)
            LOGGER.warning("Voice request could not use a temporary audio file")
            self._send_json(500, {"error": "temporary_audio_unavailable", "message": "音声を一時処理できません。"})
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    LOGGER.warning("Temporary voice audio cleanup failed")

    def _stream_voice_turn(self, manager, session, generation: int, turn_id: str, audio_path: Path) -> None:
        events: Queue[dict[str, object] | None] = Queue()
        future = manager.stream_turn(session, generation, turn_id, audio_path, events)
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-store, no-transform")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True

        def write_event(event: dict[str, object]) -> None:
            body = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
            self.wfile.write(f"{len(body):X}\r\n".encode("ascii"))
            self.wfile.write(body)
            self.wfile.write(b"\r\n")
            self.wfile.flush()

        deadline = time.monotonic() + TURN_TIMEOUT_SECONDS
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    manager.timeout_stream_turn(session, generation=generation, turn_id=turn_id)
                    write_event(
                        {
                            "type": "error",
                            "session_id": session.session_id,
                            "turn_id": turn_id,
                            "generation": generation,
                            "error": "turn_timeout",
                            "message": "音声処理がタイムアウトしました。もう一度お試しください。",
                            "retryable": True,
                        }
                    )
                    break
                try:
                    event = events.get(timeout=min(0.25, remaining))
                except Empty:
                    if future is not None and future.done() and events.empty():
                        try:
                            future.result()
                        except FutureCancelledError:
                            write_event(
                                {
                                    "type": "error",
                                    "session_id": session.session_id,
                                    "turn_id": turn_id,
                                    "generation": generation,
                                    "error": "session_stopped",
                                    "message": "会話は停止されました。",
                                    "retryable": False,
                                }
                            )
                        except Exception:
                            write_event(
                                {
                                    "type": "error",
                                    "session_id": session.session_id,
                                    "turn_id": turn_id,
                                    "generation": generation,
                                    "error": "voice_processing_failed",
                                    "message": "音声を処理できませんでした。設定と音声サービスを確認してください。",
                                    "retryable": True,
                                }
                            )
                        break
                    continue
                if event is None:
                    if future is not None:
                        try:
                            future.result()
                        except FutureCancelledError:
                            write_event(
                                {
                                    "type": "error",
                                    "session_id": session.session_id,
                                    "turn_id": turn_id,
                                    "generation": generation,
                                    "error": "session_stopped",
                                    "message": "会話は停止されました。",
                                    "retryable": False,
                                }
                            )
                        except Exception:
                            write_event(
                                {
                                    "type": "error",
                                    "session_id": session.session_id,
                                    "turn_id": turn_id,
                                    "generation": generation,
                                    "error": "voice_processing_failed",
                                    "message": "音声を処理できませんでした。設定と音声サービスを確認してください。",
                                    "retryable": True,
                                }
                            )
                    break
                write_event(event)
        except (BrokenPipeError, ConnectionResetError, OSError):
            if future is not None:
                future.cancel()
            LOGGER.info("Voice stream client disconnected")
        finally:
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except OSError:
                pass

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        route = urlsplit(self.path).path
        session_match = _SESSION_ROUTE.fullmatch(route)
        if session_match:
            try:
                stopped = _voice_sessions().stop_session(session_match.group(1))
                if _AUDIO_STREAMS is not None:
                    _AUDIO_STREAMS.discard(session_match.group(1))
                self._send_json(200, stopped)
            except VoiceAPIError as exc:
                self._send_voice_error(exc)
            return
        if route != "/api/v1/dashboard/reference-audio":
            self._send_json(404, {"error": "not_found"})
            return
        try:
            delete_reference_voice()
            self._send_json(200, {"uploaded": False})
        except RuntimeError:
            LOGGER.exception("Unable to delete reference WAV")
            self._send_json(500, {"error": "reference_audio_unavailable", "message": "参照音声を削除できません。"})

    def _send_voice_error(self, error: VoiceAPIError) -> None:
        value = {"error": error.code, "message": error.message, "retryable": error.retryable}
        expected = getattr(error, "expected_sequence", None)
        if type(expected) is int:
            value["expected_sequence"] = expected
        self._send_json(error.status, value)

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        route = urlsplit(self.path).path
        if route == "/api/v1/tail/frequency":
            self._receive_tail_frequency()
            return
        if route == "/api/v1/dashboard/reference-audio":
            self._upload_reference_audio()
            return
        if route != "/api/v1/dashboard/settings":
            self._send_json(404, {"error": "not_found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "invalid_content_length"})
            return
        if length <= 0 or length > MAX_REQUEST_BYTES:
            self._send_json(413, {"error": "request_too_large", "max_bytes": MAX_REQUEST_BYTES})
            return

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            settings = save_settings(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self._send_json(400, {"error": "invalid_settings", "message": str(exc)})
            return
        except RuntimeError:
            LOGGER.exception("Unable to save dashboard settings")
            self._send_json(500, {"error": "settings_unavailable", "message": "設定を保存できません。"})
            return

        self._send_json(200, {"settings": settings, "applies": "new_pipeline_instance"})

    def _receive_tail_frequency(self) -> None:
        try:
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise VoiceAPIError(415, "unsupported_media_type", "頻度はapplication/jsonで送信してください。")
            try:
                payload = json.loads(self._stream_body(MAX_REQUEST_BYTES))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise VoiceAPIError(400, "invalid_tail_frequency", "頻度のJSONを確認してください。") from exc
            if not isinstance(payload, dict) or set(payload) != {"rate_hz"}:
                raise VoiceAPIError(400, "invalid_tail_frequency", "rate_hzだけを指定してください。")
            try:
                result = set_tail_frequency(payload["rate_hz"])
            except ValueError as exc:
                raise VoiceAPIError(400, "invalid_tail_frequency", "頻度は0.3〜1.0Hzで指定してください。") from exc
            self._send_json(200, result)
        except VoiceAPIError as exc:
            self._send_voice_error(exc)
        except (OSError, RuntimeError):
            self._send_json(500, {"error": "tail_settings_unavailable", "message": "トントンの頻度を保存できません。"})

    def _upload_reference_audio(self) -> None:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type not in {"audio/wav", "audio/x-wav", "application/octet-stream"}:
            self._send_json(415, {"error": "unsupported_media_type", "message": "WAVファイルを選択してください。"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "invalid_content_length"})
            return
        if length <= 0 or length > MAX_REFERENCE_WAV_BYTES:
            self._send_json(
                413,
                {"error": "reference_audio_too_large", "max_bytes": MAX_REFERENCE_WAV_BYTES},
            )
            return
        try:
            self.connection.settimeout(60.0)
            audio = self.rfile.read(length)
            if len(audio) != length:
                raise ValueError("アップロードが途中で終了しました。")
            info = save_reference_voice(audio)
        except OSError:
            self._send_json(408, {"error": "upload_timeout", "message": "アップロードがタイムアウトしました。"})
            return
        except ValueError as exc:
            self._send_json(400, {"error": "invalid_reference_audio", "message": str(exc)})
            return
        except RuntimeError:
            LOGGER.exception("Unable to save reference WAV")
            self._send_json(500, {"error": "reference_audio_unavailable", "message": "参照音声を保存できません。"})
            return
        finally:
            self.connection.settimeout(None)
        self._send_json(200, info)

    def _serve_static(self, route: str) -> None:
        relative = unquote(route.lstrip("/")) or "index.html"
        target = (WEB_ROOT / relative).resolve()
        if not target.is_relative_to(WEB_ROOT.resolve()) or not target.is_file():
            self._send_json(404, {"error": "not_found"})
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type == "application/javascript":
            content_type += "; charset=utf-8"
        try:
            self._send_bytes(200, target.read_bytes(), content_type)
        except OSError:
            LOGGER.exception("Unable to serve dashboard asset")
            self._send_json(500, {"error": "asset_unavailable"})

    def log_message(self, format: str, *args: object) -> None:
        LOGGER.info("%s - %s", self.address_string(), format % args)


class DashboardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int]) -> None:
        super().__init__(address, DashboardHandler)


def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    parser = argparse.ArgumentParser(description="Start the NenGitsune settings dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: loopback only)")
    parser.add_argument("--port", type=int, default=8765, help="HTTP port (default: 8765)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = DashboardHTTPServer((args.host, args.port))
    LOGGER.info("Dashboard listening at http://%s:%d/", args.host, args.port)
    if not _is_loopback(args.host):
        LOGGER.warning("Use a trusted network and HTTPS reverse proxy for remote browser access")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Dashboard stopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
