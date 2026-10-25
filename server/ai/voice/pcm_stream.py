"""ESP32/browser-neutral continuous PCM ingestion and server-owned turns."""

from __future__ import annotations

import json
import math
import tempfile
import threading
import time
import uuid
import wave
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from numbers import Real
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable

import numpy as np

from .server_vad import SAMPLE_RATE, ServerVADSettings, ServerVoiceActivity
from .sessions import MAX_ACTIVE_SESSIONS, TURN_TIMEOUT_SECONDS, VoiceAPIError, VoiceSessions


MAX_PCM_FRAME_BYTES = 32_000
PREVIEW_INTERVAL_BYTES = SAMPLE_RATE * 2
COOLDOWN_SECONDS = 0.6
MAX_STREAM_EVENTS = 256
MAX_STREAM_EVENT_BYTES = 16 * 1024 * 1024
_REPLY_STAGES = {"stt", "llm", "tts", "reply_ready", "playing"}


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
    event_cursor: int = 0
    events: deque[tuple[dict[str, Any], int]] = field(default_factory=deque)
    event_bytes: int = 0
    source_future: Future[Any] | None = None
    turn_complete: bool = False
    has_audio: bool = False
    playback_started: bool = False
    playback_failed: bool = False
    cancelled: bool = False
    interruption: dict[str, Any] | None = None
    vad_failed: bool = False


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
        self._events_ready = threading.Condition(self._lock)
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
                self._discard_locked(state)

    def _state_locked(self, session_id: str, generation: int) -> _StreamState:
        self._purge_locked()
        session = self.manager.get_active_session(session_id, generation)
        state = self._states.get(session_id)
        if state is None:
            saved = getattr(getattr(session, "pipeline", None), "vad_settings", None)
            vad = (
                ServerVoiceActivity(ServerVADSettings(**saved))
                if self._vad_factory is ServerVoiceActivity and isinstance(saved, dict)
                else self._vad_factory()
            )
            state = _StreamState(session_id, generation, vad)
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
            "output_streaming": True,
            "turn_complete": state.turn_complete,
            "event_cursor": state.event_cursor,
        }
        if state.response:
            result["response_text"] = state.response.get("response_text", "")
            if state.response.get("speech") is not None:
                result["speech"] = state.response["speech"]
        if state.error:
            result.update(state.error)
        if state.interruption is not None:
            # Also reach a receiver paused by audio-output backpressure. Its
            # durable event cursor still advances through the event stream only.
            result["interruption"] = dict(state.interruption)
        return result

    def push_audio(
        self, session_id: str, generation: int, sequence: int, pcm_bytes: bytes,
        *, input_gain: float = 1.0,
    ) -> dict[str, Any]:
        try:
            if isinstance(input_gain, bool) or not isinstance(input_gain, Real):
                raise ValueError
            gain = float(input_gain)
            if not math.isfinite(gain) or not 0.25 <= gain <= 2.0:
                raise ValueError
        except (TypeError, ValueError, OverflowError) as exc:
            raise VoiceAPIError(400, "invalid_input_gain", "入力音量は0.25〜2.0の範囲で指定してください。") from exc
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
            raise VoiceAPIError(400, "invalid_sequence", "音声の連番を指定してください。")
        if not pcm_bytes or len(pcm_bytes) > MAX_PCM_FRAME_BYTES or len(pcm_bytes) % 2:
            raise VoiceAPIError(413 if len(pcm_bytes) > MAX_PCM_FRAME_BYTES else 400,
                                "invalid_pcm", "PCM16LE音声を1秒以下で送信してください。")
        with self._lock:
            state = self._state_locked(session_id, generation)
            if state.vad_failed:
                raise VoiceAPIError(503, "vad_unavailable", state.error["message"], False)
            if sequence < state.next_sequence:
                return self._snapshot(state, sequence)
            if sequence != state.next_sequence:
                error = VoiceAPIError(409, "audio_sequence_gap", "音声の順番を確認してください。", True)
                error.expected_sequence = state.next_sequence
                raise error
            self.manager.get_active_session(session_id, generation, touch=True)
            try:
                update = state.vad.feed(self._apply_input_gain(pcm_bytes, gain))
            except Exception as exc:
                # A failed feed may already have changed the recurrent VAD state.
                # Do not acknowledge it or permit a retry to append it a second time.
                self.manager.stop_tail_for_session(session_id, generation)
                state.vad_failed = True
                state.stage = "failed"
                state.error = {
                    "error": "vad_unavailable", "message": "サーバーの音声判定を利用できません。会話を開始し直してください。",
                    "fatal": True,
                }
                try:
                    self._interrupt_manager_turn(state)
                except VoiceAPIError:
                    pass  # Stop/expiry must not mask the original VAD failure.
                self._events_ready.notify_all()
                raise VoiceAPIError(503, "vad_unavailable", state.error["message"], False) from exc
            state.next_sequence += 1
            if update.started:
                state = self._begin_utterance_locked(state)
            if update.completed is not None:
                self._start_final_locked(state, update.completed)
            elif state.vad.speaking:
                state.stage = "speaking"
                self._start_preview_locked(state)
            return self._snapshot(state, sequence)

    def _interrupt_manager_turn(self, state: _StreamState) -> None:
        if state.turn_id is not None:
            self.manager.interrupt_turn(state.session_id, state.generation, state.turn_id)

    def _begin_utterance_locked(self, previous: _StreamState) -> _StreamState:
        """Detach old workers while keeping the continuous VAD and input order."""
        turn_id = str(uuid.uuid4())
        interruption = previous.interruption
        if previous.stage in _REPLY_STAGES and previous.turn_id is not None:
            self._interrupt_manager_turn(previous)
            self._append_event_locked(previous, {
                "type": "interruption", "session_id": previous.session_id,
                "generation": previous.generation, "turn_id": previous.turn_id,
                "interrupted_turn_id": previous.turn_id, "new_turn_id": turn_id,
                "cancellation_requested": True, "native_work_may_continue": True,
            })
            interruption = dict(previous.events[-1][0])
        elif previous.final_future is not None and not previous.final_future.done():
            self._interrupt_manager_turn(previous)
        previous.cancelled = True
        state = _StreamState(
            previous.session_id, previous.generation, previous.vad,
            next_sequence=previous.next_sequence, stage="speaking", turn_id=turn_id,
            utterance=previous.utterance + 1, event_cursor=previous.event_cursor,
            events=previous.events, event_bytes=previous.event_bytes,
            interruption=interruption,
        )
        self._states[state.session_id] = state
        self._events_ready.notify_all()
        return state

    @staticmethod
    def _apply_input_gain(pcm: bytes, gain: float) -> bytes:
        if gain == 1.0:
            return bytes(pcm)
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        return np.clip(samples * gain, -32768, 32767).astype("<i2").tobytes()

    def get_status(self, session_id: str, generation: int) -> dict[str, Any]:
        with self._lock:
            return self._snapshot(self._state_locked(session_id, generation))

    def runtime_snapshot(self, active_session_ids: set[str] | None = None) -> list[dict[str, Any]]:
        """Observe VAD settings without consuming events or resetting live input."""
        with self._lock:
            return [
                {"stage": state.stage, "settings": asdict(state.vad.settings)}
                for state in self._states.values()
                if hasattr(state.vad, "settings")
                and (active_session_ids is None or state.session_id in active_session_ids)
            ]

    def read_events(
        self, session_id: str, generation: int, after: int, *, timeout: float = 10.0,
    ) -> list[dict[str, Any]]:
        if type(after) is not int or after < 0:
            raise VoiceAPIError(400, "invalid_event_cursor", "イベントの連番を確認してください。")
        if not math.isfinite(timeout) or not 0 <= timeout <= 30:
            raise VoiceAPIError(400, "invalid_event_timeout", "イベントの待機時間を確認してください。")
        deadline = time.monotonic() + timeout
        with self._events_ready:
            while True:
                state = self._state_locked(session_id, generation)
                if state.vad_failed:
                    raise VoiceAPIError(503, "vad_unavailable", state.error["message"], False)
                if after > state.event_cursor:
                    raise VoiceAPIError(409, "invalid_event_cursor", "まだ存在しないイベントです。")
                if state.events and after < state.events[0][0]["cursor"] - 1:
                    raise VoiceAPIError(409, "events_expired", "返答イベントの保持期限を超えました。会話を開始し直してください。")
                available = [dict(event) for event, _ in state.events if event["cursor"] > after]
                if available:
                    return available
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                # Revalidate the manager's expiry/generation even if it was stopped
                # through an existing endpoint that did not notify this condition.
                self._events_ready.wait(timeout=min(remaining, 0.25))

    def _append_event_locked(self, state: _StreamState, event: dict[str, Any]) -> bool:
        if self._states.get(state.session_id) is not state or state.cancelled or state.vad_failed:
            return False
        self.manager.get_active_session(state.session_id, state.generation)
        if (event.get("session_id") != state.session_id or event.get("generation") != state.generation
                or event.get("turn_id") != state.turn_id):
            return False
        payload = dict(event)
        payload["cursor"] = state.event_cursor + 1
        size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        if size > MAX_STREAM_EVENT_BYTES:
            payload = {
                "type": "error", "error": "stream_event_too_large", "retryable": False,
                "message": "返答イベントの上限に達しました。", "session_id": state.session_id,
                "generation": state.generation, "turn_id": state.turn_id,
                "cursor": state.event_cursor + 1,
            }
            size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            self.manager.cancel_stream_turn(state.session_id, state.generation)
        state.event_cursor += 1
        state.events.append((payload, size))
        state.event_bytes += size
        while len(state.events) > MAX_STREAM_EVENTS or state.event_bytes > MAX_STREAM_EVENT_BYTES:
            _, expired_size = state.events.popleft()
            state.event_bytes -= expired_size
        kind = payload.get("type")
        if kind == "transcript":
            state.transcript = str(payload.get("text", ""))[:16_384]
            state.stage = "llm"
        elif kind == "text_delta":
            if state.response is None:
                state.response = {}
            state.response["response_text"] = (state.response.get("response_text", "") + str(payload.get("text", "")))[:1000]
        elif kind in {"audio_chunk", "acknowledgement"}:
            state.has_audio = True
            if kind == "audio_chunk":
                state.stage = "tts"
        elif kind == "complete":
            state.turn_complete = True
            state.transcript = str(payload.get("transcript", state.transcript))[:16_384]
            state.response = dict(payload)
            state.stage = "playing" if state.playback_started and not state.playback_failed else "reply_ready"
        elif kind == "error":
            state.error = {
                "error": str(payload.get("error", "voice_processing_failed")),
                "message": str(payload.get("message", "音声を処理できませんでした。")),
            }
            if state.error["error"] == "no_speech":
                state.stage = "cooldown"
                state.cooldown_until = self._clock() + COOLDOWN_SECONDS
                state.transcript = ""
            else:
                state.stage = "failed"
        self._events_ready.notify_all()
        return kind != "error"

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
        state.turn_id = state.turn_id or str(uuid.uuid4())
        if not self._final_capacity.acquire(blocking=False):
            self._final_start_error(state, VoiceAPIError(503, "server_busy", "音声処理が混み合っています。", True))
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
            source: Future[Any] | None = None
            complete: dict[str, Any] | None = None
            terminal_error: VoiceAPIError | None = None
            try:
                with self._lock:
                    if state.cancelled or self._states.get(state.session_id) is not state:
                        self._interrupt_manager_turn(state)
                        raise VoiceAPIError(409, "session_stopped", "会話は停止されました。")
                path = self._write_wav(pcm)
                events: Queue[dict[str, Any] | None] = Queue()
                source = self.manager.stream_turn(session, generation, turn_id, path, events)
                with self._lock:
                    state.source_future = source
                deadline = time.monotonic() + TURN_TIMEOUT_SECONDS
                timed_out = False
                while True:
                    try:
                        event = events.get(timeout=0.1)
                    except Empty:
                        if (not timed_out and time.monotonic() >= deadline
                                and not state.cancelled and self._states.get(state.session_id) is state):
                            timed_out = True
                            self.manager.timeout_stream_turn(session, generation=generation, turn_id=turn_id)
                        continue
                    if event is None:
                        break
                    with self._lock:
                        if state.cancelled or self._states.get(state.session_id) is not state:
                            continue
                        if terminal_error is not None:
                            continue
                        try:
                            published = self._append_event_locked(state, event)
                        except VoiceAPIError:
                            self.manager.cancel_stream_turn(state.session_id, generation)
                            continue
                        if not published:
                            details = state.error or event
                            code = str(details.get("error", "voice_processing_failed"))
                            terminal_error = VoiceAPIError(422 if code == "no_speech" else 502, code,
                                                           str(details.get("message", "音声を処理できませんでした。")))
                        elif event.get("type") == "complete":
                            complete = dict(event)
                # The manager sends the sentinel only after native STT releases the
                # input WAV. Never use future cancellation as a file-cleanup signal.
                if source is not None:
                    source.result(timeout=5)
                if terminal_error is not None:
                    raise terminal_error
                if complete is None:
                    raise VoiceAPIError(502, "voice_processing_failed", "返答の生成完了を確認できませんでした。", True)
                return complete
            except BaseException:
                if source is None:
                    self._interrupt_manager_turn(state)
                raise
            finally:
                if path is not None:
                    path.unlink(missing_ok=True)
                self._final_capacity.release()

        try:
            state.final_future = self._final_workers.submit(final)
        except Exception as exc:
            self._interrupt_manager_turn(state)
            self._final_capacity.release()
            error = self._final_start_error(state, exc)
            if error is exc:
                raise
            raise error from exc

    def _final_start_error(self, state: _StreamState, cause: Exception) -> VoiceAPIError:
        error = cause if isinstance(cause, VoiceAPIError) else VoiceAPIError(
            503, "voice_unavailable", "音声処理を開始できません。", True,
        )
        state.stage = "failed"
        state.final_future = None
        state.error = {"error": error.code, "message": error.message}
        self._append_event_locked(state, {
            "type": "error", "session_id": state.session_id, "generation": state.generation,
            "turn_id": state.turn_id, "error": error.code, "message": error.message,
            "retryable": error.retryable,
        })
        return error

    def _refresh_locked(self, state: _StreamState) -> None:
        if state.vad_failed:
            return  # Late preview/reply completion cannot revive partial VAD state.
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
                    if not state.turn_complete:
                        self._append_event_locked(state, {**result, "type": "complete"})
                except Exception as exc:
                    if state.error is not None:
                        pass  # The terminal event already set failed/cooldown state.
                    elif isinstance(exc, VoiceAPIError) and exc.code == "no_speech":
                        state.stage = "cooldown"
                        state.cooldown_until = self._clock() + COOLDOWN_SECONDS
                        state.transcript = ""
                    else:
                        self._final_start_error(state, exc)
            elif state.turn_id and not state.turn_complete:
                try:
                    progress = self.manager.get_turn_status(state.session_id, state.turn_id)
                    if progress["stage"] in {"stt", "llm", "tts"}:
                        state.stage = progress["stage"]
                    state.transcript = progress.get("transcript", state.transcript)
                except VoiceAPIError:
                    pass
        if state.stage == "cooldown" and self._clock() >= state.cooldown_until:
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
            if not state.has_audio or state.stage not in {"stt", "llm", "tts", "reply_ready", "playing"}:
                raise VoiceAPIError(409, "playback_not_ready", "返答音声を準備しています。", True)
            if event == "started":
                self.manager.start_tail_for_playback(session_id, generation, turn_id)
                state.playback_started = True
                state.playback_failed = False
                if state.turn_complete:
                    state.stage = "playing"
                state.error = None
            elif event == "ended":
                if not state.turn_complete:
                    raise VoiceAPIError(409, "playback_not_complete", "返答音声の生成が続いています。", True)
                self.manager.stop_tail_for_session(session_id, generation, turn_id)
                state.stage = "cooldown"
                state.cooldown_until = self._clock() + COOLDOWN_SECONDS
                state.error = None
            else:
                self.manager.stop_tail_for_session(session_id, generation, turn_id)
                state.playback_failed = True
                if state.turn_complete:
                    state.stage = "reply_ready"
                state.error = {"error": "playback_failed", "message": "返答音声の再生を確認してください。"}
            return self._snapshot(state)

    def discard(self, session_id: str) -> None:
        with self._lock:
            state = self._states.pop(session_id, None)
            if state is not None:
                self._discard_locked(state)

    def _discard_locked(self, state: _StreamState) -> None:
        state.cancelled = True
        state.vad.reset()
        state.events.clear()
        state.event_bytes = 0
        state.transcript = ""
        state.response = None
        self.manager.cancel_stream_turn(state.session_id, state.generation)
        self._events_ready.notify_all()

    def close(self) -> None:
        with self._lock:
            for state in self._states.values():
                self._discard_locked(state)
            self._states.clear()
        self._preview_workers.shutdown(wait=True)
        self._final_workers.shutdown(wait=True)
