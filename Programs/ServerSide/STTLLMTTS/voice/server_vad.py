"""Stateful, server-side Silero VAD for a continuous 16 kHz PCM stream."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Any

import numpy as np

from ...dashboard.settings_store import get_dashboard_settings, validate_vad_settings


SAMPLE_RATE = 16_000
FRAME_SAMPLES = 512
FRAME_BYTES = FRAME_SAMPLES * 2
FRAME_MS = FRAME_SAMPLES * 1000 / SAMPLE_RATE


class SileroStreamingVAD:
    """Share the ONNX session, while retaining h/c/context separately per stream.

    faster-whisper's public batch wrapper resets h/c on each call. The bundled v6
    ONNX contract is checked explicitly before using its stateful inference API.
    """

    def __init__(self, session: Any | None = None) -> None:
        self._session = session
        self.reset()

    def reset(self) -> None:
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, 64), dtype=np.float32)

    def _get_session(self) -> Any:
        if self._session is None:
            from faster_whisper.vad import get_vad_model

            session = get_vad_model().session
            inputs = {item.name: item.shape for item in session.get_inputs()}
            if (
                set(inputs) != {"input", "h", "c"}
                or inputs["input"][-1] != 576
                or inputs["h"] != [1, 1, 128]
                or inputs["c"] != [1, 1, 128]
            ):
                raise RuntimeError("The bundled Silero ONNX streaming contract has changed")
            self._session = session
        return self._session

    def probability(self, pcm_frame: bytes) -> float:
        if len(pcm_frame) != FRAME_BYTES:
            raise ValueError("Silero requires exactly 512 PCM16 samples per frame")
        samples = np.frombuffer(pcm_frame, dtype="<i2").astype(np.float32) / 32768.0
        model_input = np.concatenate((self._context, samples.reshape(1, -1)), axis=1)
        output, self._h, self._c = self._get_session().run(
            None, {"input": model_input, "h": self._h, "c": self._c},
        )
        self._context = samples[-64:].reshape(1, 64).copy()
        probability = float(np.asarray(output).reshape(-1)[0])
        if not math.isfinite(probability):
            raise RuntimeError("Silero returned an invalid speech probability")
        return probability


@dataclass(frozen=True)
class ServerVADSettings:
    threshold: float = 0.65
    end_threshold: float = 0.35
    min_speech_ms: int = 384
    silence_ms: int = 896
    preroll_ms: int = 640
    max_speech_seconds: float = 20.0

    @classmethod
    def from_env(cls) -> "ServerVADSettings":
        """New streams use saved dashboard values, otherwise the environment."""
        return cls(**get_dashboard_settings()["vad"])

    def __post_init__(self) -> None:
        validate_vad_settings(vars(self))


@dataclass(frozen=True)
class VADUpdate:
    started: bool = False
    completed: bytes | None = None


class ServerVoiceActivity:
    """A bounded idle ring, a single utterance and a sub-frame remainder."""

    def __init__(
        self, settings: ServerVADSettings | None = None, model: Any | None = None,
    ) -> None:
        self.settings = settings or ServerVADSettings.from_env()
        self.model = model or SileroStreamingVAD()
        self._ring_frames = math.ceil(self.settings.preroll_ms / FRAME_MS)
        self._min_frames = math.ceil(self.settings.min_speech_ms / FRAME_MS)
        self._silence_frames = math.ceil(self.settings.silence_ms / FRAME_MS)
        self._max_frames = math.ceil(self.settings.max_speech_seconds * SAMPLE_RATE / FRAME_SAMPLES)
        self.reset()

    def reset(self) -> None:
        self.model.reset()
        self._ring: deque[bytes] = deque(maxlen=self._ring_frames)
        self._remainder = b""
        self._audio = bytearray()
        self._candidate_frames = 0
        self._active_frames = 0
        self._quiet_frames = 0
        self.speaking = False

    @property
    def audio_bytes(self) -> bytes:
        return bytes(self._audio)

    @property
    def buffered_bytes(self) -> int:
        return len(self._remainder) + len(self._audio) + sum(map(len, self._ring))

    def feed(self, pcm: bytes) -> VADUpdate:
        pending = self._remainder + pcm
        self._remainder = b""
        started = False
        complete_length = len(pending) - len(pending) % FRAME_BYTES
        for offset in range(0, complete_length, FRAME_BYTES):
            frame = pending[offset:offset + FRAME_BYTES]
            probability = self.model.probability(frame)
            if not self.speaking:
                self._ring.append(frame)
                self._candidate_frames = self._candidate_frames + 1 if probability >= self.settings.threshold else 0
                if self._candidate_frames >= self._min_frames:
                    self.speaking = True
                    started = True
                    self._audio = bytearray(b"".join(self._ring))
                    self._active_frames = self._candidate_frames
                    self._ring.clear()
            else:
                self._audio.extend(frame)
                self._active_frames += 1
                self._quiet_frames = self._quiet_frames + 1 if probability < self.settings.end_threshold else 0
                if self._quiet_frames >= self._silence_frames or self._active_frames >= self._max_frames:
                    audio = bytes(self._audio)
                    # Remaining samples in this packet belong to reply processing and
                    # are deliberately discarded, just like subsequent PCM packets.
                    self.reset()
                    return VADUpdate(started=started, completed=audio)
        self._remainder = pending[complete_length:]
        return VADUpdate(started=started)
