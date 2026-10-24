"""返答待ち中に一度だけ使う、短い相づち音声のメモリ内キャッシュ。"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

from ..tts.irodori import IrodoriTTS, SynthesizedSpeech
from .acknowledgement_assets import load_assets


DEFAULT_ACKNOWLEDGEMENT_PHRASES = (
    "うんうん",
    "ふーん",
    "へー",
)


@dataclass(frozen=True)
class VoiceAcknowledgement:
    text: str
    speech: SynthesizedSpeech


class VoiceAcknowledgementSystem:
    """同じTTS設定で相づち音声を事前生成し、会話ターンごとに交互に返す。"""

    def __init__(
        self,
        tts: IrodoriTTS,
        phrases: tuple[str, ...] = DEFAULT_ACKNOWLEDGEMENT_PHRASES,
        delay_seconds: float | None = None,
    ) -> None:
        if not phrases or any(not phrase.strip() for phrase in phrases):
            raise ValueError("At least one non-empty acknowledgement phrase is required")
        if len(set(phrases)) != len(phrases):
            raise ValueError("Acknowledgement phrases must be unique")

        self.tts = tts
        self.phrases = phrases
        if delay_seconds is None:
            delay_seconds = float(os.getenv("VOICE_ACK_DELAY_SECONDS", "0.9"))
        self.delay_seconds = min(10.0, max(0.0, delay_seconds))
        self._cache: tuple[VoiceAcknowledgement, ...] = ()
        self._next_index = 0
        self._prepare_lock = asyncio.Lock()
        self._ready_event = asyncio.Event()

    @property
    def ready(self) -> bool:
        return len(self._cache) == len(self.phrases)

    async def prepare(self) -> None:
        """事前に音声を作ってメモリに保持する。通常は会話開始前に呼び出す。"""
        if self.ready:
            return
        async with self._prepare_lock:
            if self.ready:
                return
            self._ready_event.clear()
            try:
                assets = load_assets(self.tts, self.phrases)
                if assets is not None:
                    self._cache = tuple(VoiceAcknowledgement(text=text, speech=speech) for text, speech in assets)
                    self._next_index = 0
                    return
                generated = []
                for phrase in self.phrases:
                    speech = await self.tts.synthesize_async(phrase)
                    generated.append(VoiceAcknowledgement(text=phrase, speech=speech))
                self._cache = tuple(generated)
                self._next_index = 0
            finally:
                self._ready_event.set()

    async def wait_until_ready(self) -> bool:
        """Wait for cache preparation to finish, whether it succeeds or fails."""
        await self._ready_event.wait()
        return self.ready

    def next(self) -> VoiceAcknowledgement:
        if not self.ready:
            raise RuntimeError("Acknowledgement audio has not been prepared")
        result = self._cache[self._next_index]
        self._next_index = (self._next_index + 1) % len(self._cache)
        return result

    @staticmethod
    def is_goodnight_or_stop_request(text: str) -> bool:
        normalized = text.strip().lower()
        return any(
            phrase in normalized
            for phrase in ("おやすみ", "もう寝る", "寝るね", "止めて", "ストップ")
        )

    def clear(self) -> None:
        """セッション終了時にキャッシュした音声を解放する。"""
        self._cache = ()
        self._next_index = 0
        self._ready_event.set()
