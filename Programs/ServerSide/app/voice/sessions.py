"""In-memory voice sessions exposed to the Android web client."""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .pipeline import NoSpeechDetected, VoicePipeline
from .stt import AudioDurationExceeded


MAX_AUDIO_BYTES = 25_000_000
MAX_ACTIVE_SESSIONS = 4
MAX_TURNS_PER_SESSION = 100
SESSION_TTL_SECONDS = 15 * 60
SPEECH_TTL_SECONDS = 2 * 60
TURN_TIMEOUT_SECONDS = 180


class VoiceAPIError(RuntimeError):
    def __init__(self, status: int, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass
class StoredSpeech:
    audio: bytes
    media_type: str
    generation: int
    expires_at: float


@dataclass
class VoiceSession:
    session_id: str
    pipeline: VoicePipeline | None = None
    generation: int = 0
    active: bool = True
    busy: bool = False
    last_activity: float = field(default_factory=time.monotonic)
    turn_ids: set[str] = field(default_factory=set)
    speeches: dict[str, StoredSpeech] = field(default_factory=dict)
    task: asyncio.Task[Any] | None = None


class VoiceSessions:
    """Keep each conversation in memory and fence results after stop or expiry."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, VoiceSession] = {}
        self._loop_ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread = threading.Thread(
            target=self._run_loop,
            name="nen-gitsune-voice-loop",
            daemon=True,
        )
        self._loop_thread.start()
        if not self._loop_ready.wait(timeout=5):
            raise RuntimeError("音声処理ループを開始できません。")

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._loop_ready.set()
        loop.run_forever()
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()

    def _submit(self, coroutine: Any):
        loop = self._loop
        if loop is None or loop.is_closed():
            raise VoiceAPIError(503, "voice_unavailable", "音声処理を開始できません。", True)
        return asyncio.run_coroutine_threadsafe(coroutine, loop)

    async def _create_pipeline(self) -> VoicePipeline:
        return VoicePipeline()

    def _retire_locked(self, session: VoiceSession) -> None:
        session.active = False
        session.generation += 1
        session.speeches.clear()
        session.busy = False
        task = session.task
        if task is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(task.cancel)
        elif session.pipeline is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(
                lambda: asyncio.create_task(session.pipeline.close())
            )
        self._sessions.pop(session.session_id, None)

    def _expire_locked(self) -> None:
        now = time.monotonic()
        for session in tuple(self._sessions.values()):
            expired_speeches = [
                turn_id
                for turn_id, speech in session.speeches.items()
                if speech.expires_at <= now
            ]
            for turn_id in expired_speeches:
                session.speeches.pop(turn_id, None)
            if not session.busy and now - session.last_activity >= SESSION_TTL_SECONDS:
                self._retire_locked(session)

    def create_session(self) -> dict[str, Any]:
        session_id = str(uuid.uuid4())
        session = VoiceSession(session_id=session_id)
        with self._lock:
            self._expire_locked()
            if len(self._sessions) >= MAX_ACTIVE_SESSIONS:
                raise VoiceAPIError(503, "server_busy", "会話中のセッションが上限に達しています。", True)
            self._sessions[session_id] = session

        try:
            pipeline = self._submit(self._create_pipeline()).result(timeout=10)
        except FutureTimeoutError as exc:
            with self._lock:
                self._retire_locked(session)
            raise VoiceAPIError(503, "voice_unavailable", "音声処理を準備できません。", True) from exc
        except Exception as exc:
            with self._lock:
                self._retire_locked(session)
            raise VoiceAPIError(503, "voice_unavailable", "音声処理を準備できません。", True) from exc

        with self._lock:
            if not session.active:
                raise VoiceAPIError(409, "session_stopped", "会話は停止されました。")
            session.pipeline = pipeline
            session.last_activity = time.monotonic()
        return {
            "session_id": session_id,
            "state": "active",
            "generation": session.generation,
            "expires_in_seconds": SESSION_TTL_SECONDS,
        }

    def begin_turn(self, session_id: str, turn_id: str) -> tuple[VoiceSession, int]:
        with self._lock:
            self._expire_locked()
            session = self._sessions.get(session_id)
            if session is None or not session.active:
                raise VoiceAPIError(404, "session_not_found", "会話が見つからないか、期限切れです。")
            if session.pipeline is None:
                raise VoiceAPIError(503, "voice_unavailable", "音声処理を準備できません。", True)
            if session.busy:
                raise VoiceAPIError(409, "session_busy", "前の発話を処理しています。", True)
            if turn_id in session.turn_ids:
                raise VoiceAPIError(409, "duplicate_turn", "この発話はすでに受け付けています。")
            if len(session.turn_ids) >= MAX_TURNS_PER_SESSION:
                raise VoiceAPIError(409, "session_turn_limit", "この会話の発話数上限に達しました。")
            session.turn_ids.add(turn_id)
            session.busy = True
            session.last_activity = time.monotonic()
            return session, session.generation

    async def _process_turn(
        self,
        session: VoiceSession,
        generation: int,
        turn_id: str,
        audio_path: Path,
    ) -> dict[str, Any]:
        current_task = asyncio.current_task()
        with self._lock:
            if not session.active or session.generation != generation:
                raise VoiceAPIError(409, "session_stopped", "停止後の発話は処理しません。")
            session.task = current_task
            pipeline = session.pipeline
        if pipeline is None:
            raise VoiceAPIError(503, "voice_unavailable", "音声処理を準備できません。", True)

        try:
            result = await pipeline.process_audio_file(audio_path)
            speech_url = f"/api/v1/voice/sessions/{session.session_id}/turns/{turn_id}/audio"
            response = {
                "session_id": session.session_id,
                "turn_id": turn_id,
                "generation": generation,
                "transcript": result.transcription.text,
                "response_text": result.response.text,
                "speech": {
                    "url": speech_url,
                    "media_type": result.speech.media_type,
                    "expires_in_seconds": SPEECH_TTL_SECONDS,
                },
                "timings_seconds": result.timings_seconds,
            }
            with self._lock:
                if not session.active or session.generation != generation:
                    raise VoiceAPIError(409, "session_stopped", "停止後の返答は破棄しました。")
                session.speeches[turn_id] = StoredSpeech(
                    audio=result.speech.audio_bytes,
                    media_type=result.speech.media_type,
                    generation=generation,
                    expires_at=time.monotonic() + SPEECH_TTL_SECONDS,
                )
                session.last_activity = time.monotonic()
            return response
        finally:
            with self._lock:
                session.busy = False
                session.task = None
                session.last_activity = time.monotonic()
                should_close = not session.active
                pipeline_to_close = session.pipeline
            if should_close and pipeline_to_close is not None:
                await pipeline_to_close.close()

    def process_turn(
        self,
        session: VoiceSession,
        generation: int,
        turn_id: str,
        audio_path: Path,
    ) -> dict[str, Any]:
        try:
            future = self._submit(self._process_turn(session, generation, turn_id, audio_path))
        except VoiceAPIError:
            self.abandon_turn(session)
            raise
        try:
            return future.result(timeout=TURN_TIMEOUT_SECONDS)
        except FutureTimeoutError as exc:
            future.cancel()
            with self._lock:
                if session.active:
                    self._retire_locked(session)
            raise VoiceAPIError(504, "turn_timeout", "音声処理がタイムアウトしました。もう一度お試しください。", True) from exc
        except VoiceAPIError:
            raise
        except NoSpeechDetected as exc:
            raise VoiceAPIError(422, "no_speech", "音声から発話を認識できませんでした。") from exc
        except AudioDurationExceeded as exc:
            raise VoiceAPIError(413, "audio_too_long", "音声は60秒以内にしてください。") from exc
        except Exception as exc:
            if not session.active:
                raise VoiceAPIError(409, "session_stopped", "会話は停止されました。") from exc
            raise VoiceAPIError(502, "voice_processing_failed", "音声を処理できませんでした。設定と音声サービスを確認してください。", True) from exc

    def abandon_turn(self, session: VoiceSession) -> None:
        with self._lock:
            session.busy = False
            session.task = None
            session.last_activity = time.monotonic()

    def get_speech(self, session_id: str, turn_id: str) -> tuple[bytes, str]:
        with self._lock:
            self._expire_locked()
            session = self._sessions.get(session_id)
            if session is None or not session.active:
                raise VoiceAPIError(404, "session_not_found", "会話が見つからないか、期限切れです。")
            speech = session.speeches.get(turn_id)
            if speech is None or speech.generation != session.generation or speech.expires_at <= time.monotonic():
                session.speeches.pop(turn_id, None)
                raise VoiceAPIError(404, "speech_expired", "返答音声は期限切れです。")
            session.last_activity = time.monotonic()
            return speech.audio, speech.media_type

    def stop_session(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or not session.active:
                raise VoiceAPIError(404, "session_not_found", "会話が見つからないか、すでに停止されています。")
            old_generation = session.generation
            self._retire_locked(session)
            stopped_generation = session.generation
        return {
            "session_id": session_id,
            "state": "stopped",
            "invalidated_generation": old_generation,
            "generation": stopped_generation,
            "audio_device_stop_confirmed": False,
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._expire_locked()
            active_sessions = sum(1 for session in self._sessions.values() if session.active)
        return {
            "voice_api": "available",
            "models": "checked_on_first_turn",
            "active_sessions": active_sessions,
            "max_active_sessions": MAX_ACTIVE_SESSIONS,
            "session_ttl_seconds": SESSION_TTL_SECONDS,
            "audio": {
                "accepted_content_types": ["audio/webm", "audio/ogg", "audio/wav", "audio/mp4"],
                "max_bytes": MAX_AUDIO_BYTES,
                "saved_to_disk": False,
            },
        }
