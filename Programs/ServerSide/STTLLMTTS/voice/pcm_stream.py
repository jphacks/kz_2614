"""ESP32/browser-neutral continuous PCM ingestion and server-owned turns."""

from __future__ import annotations

import tempfile
import threading
import time
import uuid
import wave
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .server_vad import SAMPLE_RATE, ServerVoiceActivity
from .sessions import MAX_ACTIVE_SESSIONS, VoiceAPIError, VoiceSession, VoiceSessions


MAX_PCM_FRAME_BYTES = 32_000
PREVIEW_INTERVAL_BYTES = SAMPLE_RATE * 2
COOLDOWN_SECONDS = 0.6
_BLOCKED_STAGES = {"stt", "llm", "tts", "reply_ready", "playing", "cooldown", "failed"}


@dataclass
class _StreamState:
    session_id: str
    generation: int
    vad: ServerVoiceActivity
    next_sequence: int = 0
    stage: str = "listening"
    turn_id: str | None = None
    transcript: str = ""
    response: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    utterance: int = 0
    preview_size: int = 0
    preview_future: Future[Any] | None = None
    final_future: Future[Any] | None = None
    cooldown_until: float = 0.0


class ServerAudioStreams:
    """Short PCM ACKs; bounded background STT and one reply at a time per session."""

    def __init__(
        self,
        manager: VoiceSessions,
        *,
        vad_factory: Callable[[], ServerVoiceActivity] = ServerVoiceActivity,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.manager = manager
        self._vad_factory = vad_factory
        self._clock = clock
        self._lock = threading.RLock()
        self._states: dict[str, _StreamState] = {}
        self._preview_capacity = threading.BoundedSemaphore(1)
        self._final_capacity = threading.BoundedSemaphore(MAX_ACTIVE_SESSIONS)
        self._preview_workers = ThreadPoolExecutor(max_workers=1, thread_name_prefix="voice-stream-preview")
        self._final_workers = ThreadPoolExecutor(max_workers=MAX_ACTIVE_SESSIONS, thread_name_prefix="voice-stream-turn")

    def _purge_locked(self) -> None:
        for session_id, state in tuple(self._states.items()):
            try:
                self.manager.get_active_session(session_id, state.generation)
            except VoiceAPIError:
                self._states.pop(session_id, None)
                state.vad.reset()

    def _state_locked(self, session_id: str, generation: int) -> _StreamState:
        self._purge_locked()
        self.manager.get_active_session(session_id, generation)
        state = self._states.get(session_id)
        if state is None:
            state = _StreamState(session_id, generation, self._vad_factory())
            self._states[session_id] = state
        if state.generation != generation:
            raise VoiceAPIError(409, "generation_mismatch", "会話の状態が変わりました。")
        self._refresh_locked(state)
        return state

    @staticmethod
    def _snapshot(state: _StreamState, sequence: int | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "session_id": state.session_id,
            "generation": state.generation,
            "sequence": state.next_sequence - 1 if sequence is None else sequence,
            "next_sequence": state.next_sequence,
            "stage": state.stage,
            "turn_id": state.turn_id,
            "transcript": state.transcript,
        }
        if state.response:
            result["response_text"] = state.response.get("response_text", "")
            result["speech"] = state.response.get("speech")
        if state.error:
            result.update(state.error)
        return result

    def push_audio(
        self, session_id: str, generation: int, sequence: int, pcm_bytes: bytes,
    ) -> dict[str, Any]:
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
            raise VoiceAPIError(400, "invalid_sequence", "音声の連番を指定してください。")
        if not pcm_bytes or len(pcm_bytes) > MAX_PCM_FRAME_BYTES or len(pcm_bytes) % 2:
            raise VoiceAPIError(413 if len(pcm_bytes) > MAX_PCM_FRAME_BYTES else 400,
                                "invalid_pcm", "PCM16LE音声を1秒以下で送信してください。")
        with self._lock:
            state = self._state_locked(session_id, generation)
            if sequence < state.next_sequence:
                return self._snapshot(state, sequence)
            if sequence != state.next_sequence:
                error = VoiceAPIError(409, "audio_sequence_gap", "音声の順番を確認してください。", True)
                error.expected_sequence = state.next_sequence
                raise error
            self.manager.get_active_session(session_id, generation, touch=True)
            state.next_sequence += 1
            if state.stage in _BLOCKED_STAGES:
                return self._snapshot(state, sequence)
            try:
                update = state.vad.feed(pcm_bytes)
            except Exception as exc:
                state.stage = "failed"
                state.error = {"error": "vad_unavailable", "message": "サーバーの音声判定を利用できません。"}
                raise VoiceAPIError(503, "vad_unavailable", state.error["message"], True) from exc
            if update.started:
                state.utterance += 1
                state.stage = "speaking"
                state.turn_id = None
                state.transcript = ""
                state.response = None
                state.error = None
                state.preview_size = 0
            if update.completed is not None:
                self._start_final_locked(state, update.completed)
            elif state.vad.speaking:
                state.stage = "speaking"
                self._start_preview_locked(state)
            return self._snapshot(state, sequence)

    def get_status(self, session_id: str, generation: int) -> dict[str, Any]:
        with self._lock:
            return self._snapshot(self._state_locked(session_id, generation))

    @staticmethod
    def _write_wav(pcm: bytes) -> Path:
        path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix="nen-gitsune-stream-", suffix=".wav", delete=False) as file:
                path = Path(file.name)
            with wave.open(str(path), "wb") as file:
                file.setnchannels(1)
                file.setsampwidth(2)
                file.setframerate(SAMPLE_RATE)
                file.writeframes(pcm)
            return path
        except BaseException:
            if path is not None:
                path.unlink(missing_ok=True)
            raise

    def _start_preview_locked(self, state: _StreamState) -> None:
        pcm = state.vad.audio_bytes
        if (
            len(pcm) - state.preview_size < PREVIEW_INTERVAL_BYTES
            or state.preview_future is not None
            or not self._preview_capacity.acquire(blocking=False)
        ):
            return
        state.preview_size = len(pcm)
        utterance = state.utterance

        def preview() -> tuple[int, dict[str, Any]]:
            try:
                path = self._write_wav(pcm)
                # The manager retains its own native-worker gate and file ownership
                # after a preview timeout; no replacement native queue is created here.
                return utterance, self.manager.transcribe_preview(state.session_id, state.generation, path)
            finally:
                self._preview_capacity.release()

        try:
            state.preview_future = self._preview_workers.submit(preview)
        except BaseException:
            self._preview_capacity.release()
            raise

    def _start_final_locked(self, state: _StreamState, pcm: bytes) -> None:
        state.utterance += 1  # invalidate an older in-flight preview immediately
        state.stage = "stt"
        state.turn_id = str(uuid.uuid4())
        if not self._final_capacity.acquire(blocking=False):
            state.stage = "failed"
            state.error = {"error": "server_busy", "message": "音声処理が混み合っています。"}
            return
        try:
            session, generation = self.manager.begin_turn(state.session_id, state.turn_id)
        except Exception as exc:
            self._final_capacity.release()
            error = self._final_start_error(state, exc)
            if error is exc:
                raise
            raise error from exc
        turn_id = state.turn_id

        def final() -> dict[str, Any]:
            path: Path | None = None
            try:
                path = self._write_wav(pcm)
                return self.manager.process_turn(session, generation, turn_id, path)
            except BaseException:
                if path is None:
                    self.manager.abandon_turn(session)
                raise
            finally:
                if path is not None:
                    path.unlink(missing_ok=True)
                self._final_capacity.release()

        try:
            state.final_future = self._final_workers.submit(final)
        except Exception as exc:
            self.manager.abandon_turn(session)
            self._final_capacity.release()
            error = self._final_start_error(state, exc)
            if error is exc:
                raise
            raise error from exc

    @staticmethod
    def _final_start_error(state: _StreamState, cause: Exception) -> VoiceAPIError:
        error = cause if isinstance(cause, VoiceAPIError) else VoiceAPIError(
            503, "voice_unavailable", "音声処理を開始できません。", True,
        )
        state.stage = "failed"
        state.final_future = None
        state.error = {"error": error.code, "message": error.message}
        return error

    def _refresh_locked(self, state: _StreamState) -> None:
        if state.preview_future is not None and state.preview_future.done():
            future, state.preview_future = state.preview_future, None
            try:
                utterance, result = future.result()
                if state.stage == "speaking" and utterance == state.utterance:
                    state.transcript = str(result.get("transcript", ""))[:16_384]
            except Exception:
                pass  # Preview failure cannot replace the authoritative final turn.
        if state.final_future is not None:
            if state.final_future.done():
                future, state.final_future = state.final_future, None
                try:
                    result = future.result()
                    if result.get("generation") != state.generation or result.get("turn_id") != state.turn_id:
                        raise VoiceAPIError(409, "session_stopped", "古い返答は破棄しました。")
                    state.response = result
                    state.transcript = result.get("transcript", "")
                    state.stage = "reply_ready"
                except Exception as exc:
                    if isinstance(exc, VoiceAPIError) and exc.code == "no_speech":
                        state.stage = "cooldown"
                        state.cooldown_until = self._clock() + COOLDOWN_SECONDS
                        state.transcript = ""
                    else:
                        state.stage = "failed"
                        state.error = {
                            "error": exc.code if isinstance(exc, VoiceAPIError) else "voice_processing_failed",
                            "message": exc.message if isinstance(exc, VoiceAPIError) else "音声を処理できませんでした。",
                        }
            elif state.turn_id:
                try:
                    progress = self.manager.get_turn_status(state.session_id, state.turn_id)
                    if progress["stage"] in {"stt", "llm", "tts"}:
                        state.stage = progress["stage"]
                    state.transcript = progress.get("transcript", state.transcript)
                except VoiceAPIError:
                    pass
        if state.stage == "cooldown" and self._clock() >= state.cooldown_until:
            state.vad.reset()
            state.stage = "listening"

    def playback(
        self, session_id: str, generation: int, turn_id: str, event: str,
    ) -> dict[str, Any]:
        if event not in {"started", "ended", "failed"}:
            raise VoiceAPIError(400, "invalid_playback_event", "再生の状態を確認してください。")
        with self._lock:
            state = self._state_locked(session_id, generation)
            if not turn_id or state.turn_id != turn_id:
                raise VoiceAPIError(409, "playback_turn_mismatch", "再生する返答が変わりました。")
            if event == "ended" and state.stage in {"cooldown", "listening"}:
                return self._snapshot(state)
            if state.stage not in {"reply_ready", "playing"}:
                raise VoiceAPIError(409, "playback_not_ready", "返答音声を準備しています。", True)
            if event == "started":
                state.stage = "playing"
                state.error = None
            elif event == "ended":
                state.stage = "cooldown"
                state.cooldown_until = self._clock() + COOLDOWN_SECONDS
                state.vad.reset()
                state.error = None
            else:
                state.stage = "reply_ready"
                state.error = {"error": "playback_failed", "message": "返答音声の再生を確認してください。"}
            return self._snapshot(state)

    def discard(self, session_id: str) -> None:
        with self._lock:
            state = self._states.pop(session_id, None)
            if state is not None:
                state.vad.reset()

    def close(self) -> None:
        with self._lock:
            for state in self._states.values():
                state.vad.reset()
            self._states.clear()
        self._preview_workers.shutdown(wait=True)
        self._final_workers.shutdown(wait=True)
