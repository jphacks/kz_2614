"""In-memory voice sessions exposed to the Android web client."""

from __future__ import annotations

import asyncio
import base64
import logging
import threading
import time
import uuid
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue
from typing import Any

from .pipeline import NoSpeechDetected, VoicePipeline
from ..stt.whisper import AudioDurationExceeded, CudaUnavailableError
from ..tts.irodori import SynthesizedSpeech
from ..runtime import VoiceStageTimeout


MAX_AUDIO_BYTES = 25_000_000
MAX_ACTIVE_SESSIONS = 4
MAX_TURNS_PER_SESSION = 100
SESSION_TTL_SECONDS = 15 * 60
SPEECH_TTL_SECONDS = 2 * 60
TURN_TIMEOUT_SECONDS = 180
logger = logging.getLogger(__name__)
PREVIEW_TIMEOUT_SECONDS = 15
MAX_PROGRESS_TRANSCRIPT_CHARS = 16_384


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
class TurnProgress:
    turn_id: str
    generation: int
    stage: str
    transcript: str
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
    progress: TurnProgress | None = None
    preview_busy: bool = False


class VoiceSessions:
    """Keep each conversation in memory and fence results after stop or expiry."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, VoiceSession] = {}
        # One preview worker globally; timed-out native inference retains this slot.
        self._preview_inflight = 0
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

    def _retire_locked(self, session: VoiceSession, *, cancel_task: bool = True) -> None:
        session.active = False
        session.generation += 1
        session.speeches.clear()
        session.progress = None
        session.busy = False
        task = session.task
        if task is not None and self._loop is not None:
            if cancel_task:
                self._loop.call_soon_threadsafe(task.cancel)
        elif session.pipeline is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(
                lambda: asyncio.create_task(session.pipeline.close())
            )
        self._sessions.pop(session.session_id, None)

    def _expire_locked(self) -> None:
        now = time.monotonic()
        for session in tuple(self._sessions.values()):
            if session.progress is not None and session.progress.expires_at <= now:
                session.progress = None
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

    def get_active_session(
        self, session_id: str, generation: int, *, touch: bool = False,
    ) -> VoiceSession:
        """Validate stream ownership without making status polling renew the session."""
        with self._lock:
            self._expire_locked()
            session = self._sessions.get(session_id)
            if session is None or not session.active:
                raise VoiceAPIError(404, "session_not_found", "会話が見つからないか、期限切れです。")
            if session.generation != generation:
                raise VoiceAPIError(409, "generation_mismatch", "会話の状態が変わりました。")
            if touch:
                session.last_activity = time.monotonic()
            return session

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
            session.progress = TurnProgress(
                turn_id=turn_id,
                generation=session.generation,
                stage="stt",
                transcript="",
                expires_at=session.last_activity + TURN_TIMEOUT_SECONDS,
            )
            return session, session.generation

    def transcribe_preview(
        self, session_id: str, generation: int, audio_path: Path,
    ) -> dict[str, Any]:
        """Own a temporary recording until the native STT worker actually finishes."""
        session: VoiceSession | None = None
        dispatched = False
        try:
            with self._lock:
                self._expire_locked()
                candidate = self._sessions.get(session_id)
                if candidate is None or not candidate.active:
                    raise VoiceAPIError(404, "session_not_found", "会話が見つからないか、期限切れです。")
                if candidate.generation != generation:
                    raise VoiceAPIError(409, "generation_mismatch", "会話の状態が変わりました。")
                if candidate.pipeline is None:
                    raise VoiceAPIError(503, "voice_unavailable", "音声処理を準備できません。", True)
                if candidate.busy:
                    raise VoiceAPIError(409, "session_busy", "返答の処理中です。", True)
                if candidate.preview_busy or self._preview_inflight:
                    raise VoiceAPIError(409, "preview_busy", "前の文字起こしを処理しています。", True)
                session = candidate
                session.preview_busy = True
                session.last_activity = time.monotonic()
                self._preview_inflight += 1
                accepted_turn_count = len(session.turn_ids)

            coroutine = self._process_preview(session, generation, accepted_turn_count, audio_path)
            try:
                future = self._submit(coroutine)
            except BaseException:
                coroutine.close()
                raise
            dispatched = True
            try:
                return future.result(timeout=PREVIEW_TIMEOUT_SECONDS)
            except FutureTimeoutError as exc:
                # Do not cancel or release the slot: a native decoder can still be using
                # this file. The worker's completion callback owns the eventual cleanup.
                raise VoiceAPIError(504, "transcription_timeout", "文字起こしを待っています。", True) from exc
        finally:
            if not dispatched:
                try:
                    audio_path.unlink(missing_ok=True)
                finally:
                    if session is not None:
                        with self._lock:
                            session.preview_busy = False
                            self._preview_inflight -= 1

    async def _process_preview(
        self, session: VoiceSession, generation: int, accepted_turn_count: int, audio_path: Path,
    ) -> dict[str, Any]:
        worker_finished = threading.Event()
        worker_dispatched = False
        try:
            with self._lock:
                if (
                    self._sessions.get(session.session_id) is not session
                    or not session.active or session.generation != generation
                ):
                    raise VoiceAPIError(409, "session_stopped", "停止後の文字起こしは破棄しました。")
                pipeline = session.pipeline
            if pipeline is None:
                raise VoiceAPIError(503, "voice_unavailable", "音声処理を準備できません。", True)
            worker_dispatched = True
            try:
                transcription = await pipeline.stt.transcribe_file_async(
                    audio_path, on_worker_finished=worker_finished.set,
                )
                transcript = transcription.text
            except NoSpeechDetected:
                transcript = ""
            with self._lock:
                if (
                    self._sessions.get(session.session_id) is not session
                    or not session.active or session.generation != generation
                ):
                    raise VoiceAPIError(409, "session_stopped", "停止後の文字起こしは破棄しました。")
                if len(session.turn_ids) != accepted_turn_count:
                    raise VoiceAPIError(409, "transcription_superseded", "確定した発話の文字起こしを優先します。")
            return {
                "session_id": session.session_id,
                "generation": generation,
                "transcript": transcript,
            }
        except VoiceAPIError:
            raise
        except Exception as exc:
            with self._lock:
                if not session.active or session.generation != generation:
                    raise VoiceAPIError(409, "session_stopped", "会話は停止されました。") from exc
            if isinstance(exc, AudioDurationExceeded):
                raise VoiceAPIError(413, "audio_too_long", "音声は60秒以内にしてください。") from exc
            if isinstance(exc, CudaUnavailableError):
                raise VoiceAPIError(503, "cuda_unavailable", "WhisperのCUDAを利用できません。", True) from exc
            if isinstance(exc, VoiceStageTimeout):
                raise VoiceAPIError(504, "transcription_timeout", "文字起こしがタイムアウトしました。", True) from exc
            if isinstance(exc, (ValueError, EOFError)) or type(exc).__module__.startswith("av."):
                raise VoiceAPIError(400, "invalid_audio", "音声をまだ読み取れません。次の音声で再試行します。", True) from exc
            raise VoiceAPIError(503, "stt_unavailable", "文字起こしを利用できません。", True) from exc
        finally:
            if worker_dispatched:
                while not worker_finished.is_set():
                    await asyncio.sleep(0.05)
            try:
                audio_path.unlink(missing_ok=True)
            finally:
                with self._lock:
                    session.preview_busy = False
                    self._preview_inflight -= 1

    def _update_turn_progress(
        self,
        session: VoiceSession,
        generation: int,
        turn_id: str,
        stage: str,
        transcript: str | None = None,
    ) -> None:
        """Keep only the current turn; stopped, expired and late callbacks are ignored."""
        with self._lock:
            progress = session.progress
            if (
                self._sessions.get(session.session_id) is not session
                or not session.active
                or not session.busy
                or session.generation != generation
                or progress is None
                or progress.generation != generation
                or progress.turn_id != turn_id
                or progress.expires_at <= time.monotonic()
                or progress.stage in {"complete", "failed"}
            ):
                return
            if stage not in {"stt", "llm", "tts", "complete", "failed"}:
                return
            progress.stage = stage
            if transcript is not None:
                progress.transcript = transcript[:MAX_PROGRESS_TRANSCRIPT_CHARS]
            if stage in {"complete", "failed"}:
                progress.expires_at = time.monotonic() + SPEECH_TTL_SECONDS

    def get_turn_status(self, session_id: str, turn_id: str) -> dict[str, Any]:
        with self._lock:
            self._expire_locked()
            session = self._sessions.get(session_id)
            if session is None or not session.active:
                raise VoiceAPIError(404, "session_not_found", "会話が見つからないか、期限切れです。")
            progress = session.progress
            if (
                progress is None
                or progress.turn_id != turn_id
                or progress.generation != session.generation
            ):
                raise VoiceAPIError(404, "turn_not_found", "発話が見つからないか、期限切れです。")
            # Polling must not keep either the transcript or the session alive.
            return {
                "session_id": session_id,
                "turn_id": turn_id,
                "generation": progress.generation,
                "stage": progress.stage,
                "transcript": progress.transcript,
            }

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
            result = await pipeline.process_audio_file(
                audio_path,
                on_progress=lambda stage, transcript: self._update_turn_progress(
                    session, generation, turn_id, stage, transcript
                ),
            )
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
                self._update_turn_progress(
                    session, generation, turn_id, "complete", result.transcription.text
                )
            return response
        except VoiceStageTimeout as exc:
            with self._lock:
                timed_out = session.active and session.generation == generation
                if timed_out:
                    self._retire_locked(session, cancel_task=False)
            if not timed_out:
                raise VoiceAPIError(
                    409,
                    "session_stopped",
                    "会話は停止されました。",
                ) from exc
            stage_name = {"stt": "STT", "llm": "LLM", "tts": "TTS"}.get(exc.stage, exc.stage)
            raise VoiceAPIError(
                504,
                f"{exc.stage}_timeout",
                f"{stage_name}の処理がタイムアウトしました。",
                True,
            ) from exc
        except BaseException:
            self._update_turn_progress(session, generation, turn_id, "failed")
            raise
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
        except CudaUnavailableError as exc:
            raise VoiceAPIError(
                503,
                "cuda_unavailable",
                "WhisperのCUDAを利用できません。NVIDIAドライバーとCUDA 12、cuBLAS、cuDNN 9を確認するか、ダッシュボードでCPUを選択してください。",
                True,
            ) from exc
        except Exception as exc:
            if not session.active:
                raise VoiceAPIError(409, "session_stopped", "会話は停止されました。") from exc
            raise VoiceAPIError(502, "voice_processing_failed", "音声を処理できませんでした。設定と音声サービスを確認してください。", True) from exc

    async def _process_stream_turn(
        self,
        session: VoiceSession,
        generation: int,
        turn_id: str,
        audio_path: Path,
        events: Queue[dict[str, Any] | None],
    ) -> None:
        current_task = asyncio.current_task()
        with self._lock:
            if not session.active or session.generation != generation:
                events.put({
                    "type": "error",
                    "error": "session_stopped",
                    "message": "停止後の発話は処理しません。",
                    "retryable": False,
                })
                events.put(None)
                return
            session.task = current_task
            pipeline = session.pipeline

        def publish(event: dict[str, object]) -> None:
            with self._lock:
                if not session.active or session.generation != generation:
                    raise asyncio.CancelledError
            payload = dict(event)
            speech = payload.pop("speech", None)
            if isinstance(speech, SynthesizedSpeech):
                payload["media_type"] = speech.media_type
                payload["audio_base64"] = base64.b64encode(speech.audio_bytes).decode("ascii")
            payload.update(
                {
                    "session_id": session.session_id,
                    "turn_id": turn_id,
                    "generation": generation,
                }
            )
            events.put(payload)

        def publish_terminal(event: dict[str, object]) -> None:
            payload = dict(event)
            payload.update(
                {
                    "session_id": session.session_id,
                    "turn_id": turn_id,
                    "generation": generation,
                }
            )
            events.put(payload)

        try:
            if pipeline is None:
                raise VoiceAPIError(503, "voice_unavailable", "音声処理を準備できません。", True)
            result = await pipeline.process_audio_file_stream(
                audio_path,
                publish,
                on_progress=lambda stage, transcript: self._update_turn_progress(
                    session, generation, turn_id, stage, transcript
                ),
            )
            self._update_turn_progress(
                session, generation, turn_id, "complete", result.transcription.text
            )
            publish(
                {
                    "type": "complete",
                    "transcript": result.transcription.text,
                    "response_text": result.response.text,
                    "timings_seconds": result.timings_seconds,
                }
            )
        except asyncio.CancelledError:
            raise
        except VoiceAPIError as exc:
            publish({
                "type": "error",
                "error": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
            })
        except VoiceStageTimeout as exc:
            with self._lock:
                timed_out = session.active and session.generation == generation
                if timed_out:
                    self._retire_locked(session, cancel_task=False)
            if not timed_out:
                publish_terminal({
                    "type": "error",
                    "error": "session_stopped",
                    "message": "会話は停止されました。",
                    "retryable": False,
                })
            else:
                stage_name = {"stt": "STT", "llm": "LLM", "tts": "TTS"}.get(exc.stage, exc.stage)
                publish_terminal({
                    "type": "error",
                    "error": f"{exc.stage}_timeout",
                    "message": f"{stage_name}の処理がタイムアウトしました。",
                    "retryable": True,
                })
        except NoSpeechDetected:
            publish({
                "type": "error",
                "error": "no_speech",
                "message": "音声から発話を認識できませんでした。",
                "retryable": False,
            })
        except AudioDurationExceeded:
            publish({
                "type": "error",
                "error": "audio_too_long",
                "message": "音声は60秒以内にしてください。",
                "retryable": False,
            })
        except CudaUnavailableError:
            publish({
                "type": "error",
                "error": "cuda_unavailable",
                "message": "WhisperのCUDAを利用できません。NVIDIAドライバーとCUDA 12、cuBLAS、cuDNN 9を確認するか、ダッシュボードでCPUを選択してください。",
                "retryable": True,
            })
        except Exception as exc:
            logger.warning("voice_stream_turn_failed error_type=%s", type(exc).__name__)
            with self._lock:
                active = session.active and session.generation == generation
            publish_terminal({
                "type": "error",
                "error": "voice_processing_failed" if active else "session_stopped",
                "message": "音声を処理できませんでした。設定と音声サービスを確認してください。" if active else "会話は停止されました。",
                "retryable": active,
            })
        finally:
            self._update_turn_progress(session, generation, turn_id, "failed")
            with self._lock:
                session.busy = False
                session.task = None
                session.last_activity = time.monotonic()
                should_close = not session.active
                pipeline_to_close = session.pipeline
            if should_close and pipeline_to_close is not None:
                try:
                    await pipeline_to_close.close()
                except Exception as exc:
                    logger.warning("voice_pipeline_close_failed error_type=%s", type(exc).__name__)
            events.put(None)

    def stream_turn(
        self,
        session: VoiceSession,
        generation: int,
        turn_id: str,
        audio_path: Path,
        events: Queue[dict[str, Any] | None],
    ):
        """Schedule a turn that writes NDJSON-ready events to a thread-safe queue."""
        try:
            return self._submit(
                self._process_stream_turn(session, generation, turn_id, audio_path, events)
            )
        except VoiceAPIError:
            self.abandon_turn(session)
            events.put({
                "type": "error",
                "error": "voice_unavailable",
                "message": "音声処理を開始できません。",
                "retryable": True,
            })
            events.put(None)
            return None

    def timeout_stream_turn(self, session: VoiceSession) -> None:
        """Fence and cancel a stream whose whole-turn deadline has elapsed."""
        with self._lock:
            if session.active:
                self._retire_locked(session)

    def abandon_turn(self, session: VoiceSession) -> None:
        with self._lock:
            if session.progress is not None:
                self._update_turn_progress(
                    session, session.progress.generation, session.progress.turn_id, "failed"
                )
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

    def runtime_session_ids(self) -> set[str]:
        """Return live IDs without expiring sessions or cancelling any task."""
        now = time.monotonic()
        with self._lock:
            return {
                session.session_id for session in self._sessions.values()
                if session.active and (
                    session.busy or now - session.last_activity < SESSION_TTL_SECONDS
                )
            }

    def runtime_snapshot(self) -> dict[str, Any]:
        """Read observed component state without model loading or service probes."""
        with self._lock:
            live_ids = self.runtime_session_ids()
            pipelines = [
                session.pipeline for session in self._sessions.values()
                if session.session_id in live_ids and session.pipeline is not None
            ]
        components: dict[str, list[dict[str, Any]]] = {"stt": [], "llm": [], "tts": []}
        for pipeline in pipelines:
            for name in components:
                client = getattr(pipeline, name, None)
                reader = getattr(client, "runtime_status", None)
                if callable(reader):
                    snapshot = reader()
                    if isinstance(snapshot, dict):
                        components[name].append(snapshot)
        return {"active_sessions": len(live_ids), "components": components}

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
