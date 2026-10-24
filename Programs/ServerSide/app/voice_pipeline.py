"""Whisper文字起こしとLM Studio音声会話をつなぐ最小パイプライン。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

from .voice_llm import VoiceLLMClient, VoiceLLMReply
from .voice_stt import Transcription, WhisperSTT


class NoSpeechDetected(RuntimeError):
    """音声ファイルから発話を認識できなかった。"""


@dataclass(frozen=True)
class VoiceTurnResult:
    transcription: Transcription
    response: VoiceLLMReply
    timings_seconds: dict[str, float]


class VoicePipeline:
    """一つの会話セッション用。履歴はインスタンス内にだけ保持する。"""

    def __init__(
        self,
        stt: WhisperSTT | None = None,
        llm: VoiceLLMClient | None = None,
        history_messages: int | None = None,
    ) -> None:
        self.stt = stt or WhisperSTT()
        self.llm = llm or VoiceLLMClient()
        if history_messages is None:
            history_messages = self.llm.config.max_history_messages
        self.history_messages = max(0, history_messages)
        self._history: list[dict[str, str]] = []
        self._turn_lock = asyncio.Lock()

    async def process_audio_file(self, audio_path: str | Path) -> VoiceTurnResult:
        """音声ファイルを文字起こししてLM Studioへ送り、結果と段階時間を返す。"""
        async with self._turn_lock:
            stt_started = time.perf_counter()
            transcription = await asyncio.to_thread(self.stt.transcribe_file, audio_path)
            stt_seconds = time.perf_counter() - stt_started
            if not transcription.text.strip():
                raise NoSpeechDetected("Whisper did not detect speech in the audio file")

            llm_started = time.perf_counter()
            response = await self.llm.generate_response(transcription.text, self._history)
            llm_seconds = time.perf_counter() - llm_started

            self._history.extend(
                [
                    {"role": "user", "content": transcription.text},
                    {"role": "assistant", "content": response.text},
                ]
            )
            if self.history_messages:
                self._history = self._history[-self.history_messages :]
            else:
                self._history.clear()

            return VoiceTurnResult(
                transcription=transcription,
                response=response,
                timings_seconds={
                    "stt": round(stt_seconds, 3),
                    "llm": round(llm_seconds, 3),
                    "total": round(stt_seconds + llm_seconds, 3),
                },
            )

    async def clear_history(self) -> None:
        async with self._turn_lock:
            self._history.clear()

    async def close(self) -> None:
        await self.llm.close()
