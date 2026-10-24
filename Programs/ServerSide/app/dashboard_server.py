"""Serve the local settings dashboard and its small JSON settings API."""

from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import logging
import mimetypes
import os
import re
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from dotenv import load_dotenv

from .settings_store import (
    DEFAULT_SETTINGS,
    MAX_REFERENCE_WAV_BYTES,
    delete_reference_voice,
    get_dashboard_settings,
    get_reference_voice_info,
    save_reference_voice,
    save_settings,
)
from .voice_sessions import MAX_AUDIO_BYTES, VoiceAPIError, VoiceSessions


WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
MAX_REQUEST_BYTES = 16_384
LOGGER = logging.getLogger("nen_gitsune.dashboard")
_VOICE_SESSIONS: VoiceSessions | None = None
_VOICE_SESSIONS_LOCK = threading.Lock()
_SESSION_TURN_ROUTE = re.compile(
    r"^/api/v1/voice/sessions/([0-9a-fA-F-]{36})/turns/([0-9a-fA-F-]{36})$"
)
_SESSION_AUDIO_ROUTE = re.compile(
    r"^/api/v1/voice/sessions/([0-9a-fA-F-]{36})/turns/([0-9a-fA-F-]{36})/audio$"
)
_SESSION_ROUTE = re.compile(r"^/api/v1/voice/sessions/([0-9a-fA-F-]{36})$")


def _voice_sessions() -> VoiceSessions:
    global _VOICE_SESSIONS
    with _VOICE_SESSIONS_LOCK:
        if _VOICE_SESSIONS is None:
            _VOICE_SESSIONS = VoiceSessions()
        return _VOICE_SESSIONS


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

    @property
    def dashboard_server(self) -> "DashboardHTTPServer":
        return self.server  # type: ignore[return-value]

    def _authorized(self) -> bool:
        expected = self.dashboard_server.dashboard_token
        if not expected:
            return True
        supplied = self.headers.get("Authorization", "")
        if not supplied.startswith("Bearer "):
            return False
        return hmac.compare_digest(supplied[7:], expected)

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; "
            "font-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, value: object) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _api_auth_or_reject(self) -> bool:
        if self._authorized():
            return True
        self._send_json(401, {"error": "unauthorized", "message": "管理トークンを確認してください。"})
        return False

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        route = urlsplit(self.path).path
        if route == "/api/v1/dashboard/settings":
            if not self._api_auth_or_reject():
                return
            try:
                self._send_json(
                    200,
                    {
                        "settings": get_dashboard_settings(),
                        "defaults": DEFAULT_SETTINGS,
                        "applies": "new_pipeline_instance",
                    },
                )
            except (RuntimeError, ValueError):
                LOGGER.exception("Unable to load dashboard settings")
                self._send_json(500, {"error": "settings_unavailable", "message": "設定を読み込めません。"})
            return

        if route == "/api/v1/dashboard/health":
            if not self._api_auth_or_reject():
                return
            self._send_json(200, {"status": "ok", "settings_api": "ready"})
            return

        if route == "/api/v1/dashboard/reference-audio":
            if not self._api_auth_or_reject():
                return
            try:
                self._send_json(200, get_reference_voice_info())
            except (RuntimeError, ValueError):
                LOGGER.exception("Unable to read reference WAV status")
                self._send_json(500, {"error": "reference_audio_unavailable", "message": "参照音声を読み取れません。"})
            return

        if route == "/api/v1/voice/status":
            if not self._api_auth_or_reject():
                return
            try:
                self._send_json(200, _voice_sessions().status())
            except VoiceAPIError as exc:
                self._send_voice_error(exc)
            return

        audio_match = _SESSION_AUDIO_ROUTE.fullmatch(route)
        if audio_match:
            if not self._api_auth_or_reject():
                return
            try:
                audio, media_type = _voice_sessions().get_speech(*audio_match.groups())
                self._send_bytes(200, audio, media_type)
            except VoiceAPIError as exc:
                self._send_voice_error(exc)
            return

        self._serve_static(route)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        route = urlsplit(self.path).path
        if route == "/api/v1/voice/sessions":
            if not self._api_auth_or_reject():
                return
            try:
                self._send_json(201, _voice_sessions().create_session())
            except VoiceAPIError as exc:
                self._send_voice_error(exc)
            return

        turn_match = _SESSION_TURN_ROUTE.fullmatch(route)
        if turn_match:
            self._receive_voice_turn(*turn_match.groups())
            return

        self._send_json(404, {"error": "not_found"})

    def _receive_voice_turn(self, session_id: str, turn_id: str) -> None:
        if not self._api_auth_or_reject():
            return
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
            response = manager.process_turn(session, generation, turn_id, temporary_path)
            self._send_json(200, response)
        except VoiceAPIError as exc:
            self._send_voice_error(exc)
        except OSError:
            manager.abandon_turn(session)
            LOGGER.warning("Voice request could not use a temporary audio file")
            self._send_json(500, {"error": "temporary_audio_unavailable", "message": "音声を一時処理できません。"})
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    LOGGER.warning("Temporary voice audio cleanup failed")

    def do_DELETE(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        route = urlsplit(self.path).path
        session_match = _SESSION_ROUTE.fullmatch(route)
        if session_match:
            if not self._api_auth_or_reject():
                return
            try:
                self._send_json(200, _voice_sessions().stop_session(session_match.group(1)))
            except VoiceAPIError as exc:
                self._send_voice_error(exc)
            return
        if route != "/api/v1/dashboard/reference-audio":
            self._send_json(404, {"error": "not_found"})
            return
        if not self._api_auth_or_reject():
            return
        try:
            delete_reference_voice()
            self._send_json(200, {"uploaded": False})
        except RuntimeError:
            LOGGER.exception("Unable to delete reference WAV")
            self._send_json(500, {"error": "reference_audio_unavailable", "message": "参照音声を削除できません。"})

    def _send_voice_error(self, error: VoiceAPIError) -> None:
        self._send_json(
            error.status,
            {
                "error": error.code,
                "message": error.message,
                "retryable": error.retryable,
            },
        )

    def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        route = urlsplit(self.path).path
        if route == "/api/v1/dashboard/reference-audio":
            self._upload_reference_audio()
            return
        if route != "/api/v1/dashboard/settings":
            self._send_json(404, {"error": "not_found"})
            return
        if not self._api_auth_or_reject():
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

    def _upload_reference_audio(self) -> None:
        if not self._api_auth_or_reject():
            return
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

    def __init__(self, address: tuple[str, int], dashboard_token: str) -> None:
        self.dashboard_token = dashboard_token
        super().__init__(address, DashboardHandler)


def main() -> None:
    load_dotenv(Path(__file__).with_name(".env"), override=False)
    parser = argparse.ArgumentParser(description="Start the NenGitsune settings dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: loopback only)")
    parser.add_argument("--port", type=int, default=8765, help="HTTP port (default: 8765)")
    args = parser.parse_args()
    token = os.getenv("NENGITSUNE_DASHBOARD_TOKEN", "").strip()
    if not _is_loopback(args.host) and not token:
        parser.error("NENGITSUNE_DASHBOARD_TOKEN is required when binding outside loopback")
    if not _is_loopback(args.host) and len(token) < 32:
        parser.error("NENGITSUNE_DASHBOARD_TOKEN must be at least 32 characters outside loopback")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = DashboardHTTPServer((args.host, args.port), token)
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
