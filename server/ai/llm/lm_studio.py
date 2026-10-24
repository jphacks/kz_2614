"""短い添い寝会話を LM Studio へ問い合わせる音声用クライアント。"""

from __future__ import annotations

import json
import os
from contextlib import aclosing
from pathlib import Path
from dataclasses import dataclass
from typing import Any, AsyncIterator, Sequence

import httpx
from dotenv import load_dotenv

from ...api.settings_store import get_dashboard_settings
from ...api.runtime_status import RuntimeObservation, component_configuration
from ..runtime import VoiceStageTimeout
from .tail_tools import stream_tail_chat

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)


COMPANION_SYSTEM_PROMPT = """あなたは、親しい人がそばで耳を傾けるように、自然な日本語で話を聴く会話相手です。会話の主役は相手です。自分が話すことより、相手が安心して話せることを優先してください。
返答は原則1〜2文の短い話し言葉にします。「うん」「そっか」「それは大変だったね」などを文脈に合わせて使い、同じ相づちや定型句を繰り返さないでください。大げさに褒めたり、過度に丁寧な接客口調になったりしないでください。
まず、相手が話した出来事や言葉の一部を自然に受け止めてください。感情は決めつけず、わからないことは想像で補わないでください。発言を毎回要約したり、そのままオウム返ししたりする必要はありません。
解決策、助言、分析、説教は、相手から求められた時にだけ伝えてください。愚痴や悩みには、すぐに前向きな結論を出さず、そのまま耳を傾けてください。
質問は、相手が続きを話しやすくなる時にだけ、短く一つまでにします。毎回質問で終えず、相づちや受け止めだけの返答も使ってください。話題を勝手に変えたり、自分の話へ持っていったりしないでください。
眠ることや会話の継続を急かさないでください。「おやすみ」「もう終わり」と言われたら、引き留めず短く穏やかに応じてください。
音声で読み上げる返答だけを出力してください。箇条書き、見出し、絵文字、括弧付きの動作説明は使わないでください。人間であるという虚偽の自己紹介や、実際にはない自分の体験談は作らないでください。
トントンの頻度の変更を明確に依頼された時だけ、利用可能な頻度設定ツールを使ってください。寂しさを表明しただけで、設定を変えたり動作を始めたりしないでください。
頻度設定の成功はPC側の設定保存を示します。ESP32の実機反映やしっぽの動作を確認したと伝えないでください。サーボ角度や生のハードウェア制御は指定しないでください。
"""


@dataclass(frozen=True)
class VoiceLLMConfig:
    base_url: str = "http://127.0.0.1:1234"
    model: str = "google/gemma-4-26b-a4b-qat"
    max_output_tokens: int = 160
    context_length: int = 4096
    max_history_messages: int = 8
    temperature: float = 0.7
    timeout_seconds: float = 45.0
    tail_mcp_enabled: bool = False

    @classmethod
    def from_env(cls) -> "VoiceLLMConfig":
        settings = get_dashboard_settings()["llm"]
        return cls(
            base_url=os.getenv("LM_STUDIO_BASE_URL", cls.base_url).rstrip("/"),
            model=settings["model"],
            max_output_tokens=settings["max_output_tokens"],
            context_length=settings["context_length"],
            max_history_messages=settings["max_history_messages"],
            temperature=settings["temperature"],
            timeout_seconds=settings["timeout_seconds"],
            tail_mcp_enabled=os.getenv("TAIL_MCP_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
        )


@dataclass(frozen=True)
class VoiceLLMReply:
    text: str
    tool_calls: tuple[str, ...]
    stats: dict[str, Any]


class VoiceLLMClient:
    """LM Studioと会話し、MCP有効時はPC側で頻度設定ツールを仲介する。"""

    def __init__(self, config: VoiceLLMConfig | None = None) -> None:
        self.config = config or VoiceLLMConfig.from_env()
        self._runtime_observation = RuntimeObservation(
            component_configuration(self.config.model, backend="lm_studio"), external=True,
        )
        self.client = httpx.AsyncClient(
            base_url=self.config.base_url,
            timeout=httpx.Timeout(self.config.timeout_seconds, connect=5.0),
            trust_env=False,
        )
        self.last_stats: dict[str, Any] = {}
        self.last_tool_calls: tuple[str, ...] = ()

    def _build_payload(
        self,
        user_text: str,
        history: Sequence[dict[str, str]] = (),
        system_prompt: str = COMPANION_SYSTEM_PROMPT,
        *,
        stream: bool = False,
        speech_context: dict[str, str | float] | None = None,
    ) -> dict[str, Any]:
        recent_history = [
            {"role": item["role"], "content": str(item["content"])}
            for item in history
            if item.get("role") in {"user", "assistant"} and item.get("content")
        ]
        if self.config.max_history_messages:
            recent_history = recent_history[-self.config.max_history_messages :]
        else:
            recent_history = []

        current_utterance = user_text.strip()
        if speech_context:
            pitch = str(speech_context.get("pitch", "判定できず"))[:100]
            delivery = str(speech_context.get("delivery", "判定できず"))[:120]
            current_utterance += (
                "\n\n今回の声の特徴（参考情報）:\n"
                f"声の高さ: {pitch}\n話し方: {delivery}\n"
                "特徴だけから感情を決めつけず、文字起こしと会話の流れを優先してください。"
            )
        if recent_history:
            user_input = (
                "以前の会話は文脈として参照してください。回答対象は、最後に記載する今回のユーザー発話です。\n"
                "以前の会話:\n"
                + json.dumps(recent_history, ensure_ascii=False)
                + "\n\n今回のユーザー発話（質問・依頼）:\n"
                + current_utterance
            )
        else:
            user_input = "今回のユーザー発話（質問・依頼）:\n" + current_utterance

        payload: dict[str, Any] = {
            "model": self.config.model,
            "input": user_input,
            "system_prompt": system_prompt.strip(),
            "reasoning": "off",
            "max_output_tokens": self.config.max_output_tokens,
            "context_length": self.config.context_length,
            "temperature": self.config.temperature,
            "store": False,
            "stream": stream,
        }
        return payload

    async def _stream_tail_response(self, payload: dict[str, Any]) -> AsyncIterator[str]:
        self.last_stats = {}
        self.last_tool_calls = ()
        has_text = False
        try:
            async with aclosing(stream_tail_chat(
                self.client,
                model=self.config.model,
                user_input=payload["input"],
                system_prompt=payload["system_prompt"],
                max_output_tokens=self.config.max_output_tokens,
                context_length=self.config.context_length,
                timeout_seconds=self.config.timeout_seconds,
            )) as events:
                async for event in events:
                    if event.get("type") == "text_delta":
                        delta = str(event.get("text", ""))
                        has_text = has_text or bool(delta.strip())
                        if delta:
                            yield delta
                    elif event.get("type") == "complete":
                        self.last_tool_calls = tuple(event.get("tool_calls", ()))
                        self.last_stats = {
                            **event.get("stats", {}),
                            "model": self.config.model,
                            "reasoning": "off",
                        }
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise VoiceStageTimeout("llm", self.config.timeout_seconds) from exc
        if not has_text:
            raise RuntimeError("LM Studio returned no spoken response")
        self._runtime_observation.success()

    async def generate_response(
        self,
        user_text: str,
        history: Sequence[dict[str, str]] = (),
        system_prompt: str = COMPANION_SYSTEM_PROMPT,
        speech_context: dict[str, str | float] | None = None,
    ) -> VoiceLLMReply:
        if not user_text.strip():
            raise ValueError("user_text must not be empty")
        self._runtime_observation.begin()
        try:
            return await self._generate_response_impl(
                user_text, history, system_prompt, speech_context,
            )
        except Exception as error:
            self._runtime_observation.failure(error)
            raise
        finally:
            self._runtime_observation.finish()

    def runtime_status(self) -> dict[str, Any]:
        return self._runtime_observation.snapshot()

    async def _generate_response_impl(
        self,
        user_text: str,
        history: Sequence[dict[str, str]] = (),
        system_prompt: str = COMPANION_SYSTEM_PROMPT,
        speech_context: dict[str, str | float] | None = None,
    ) -> VoiceLLMReply:
        """STTで得た現在のユーザー発話を質問・依頼としてLM Studioへ送る。"""
        if not user_text.strip():
            raise ValueError("user_text must not be empty")

        if self.config.tail_mcp_enabled:
            payload = self._build_payload(user_text, history, system_prompt, speech_context=speech_context)
            parts = [part async for part in self._stream_tail_response(payload)]
            return VoiceLLMReply(
                text="".join(parts).strip(),
                tool_calls=self.last_tool_calls,
                stats=dict(self.last_stats),
            )

        try:
            response = await self.client.post(
                "/api/v1/chat",
                json=self._build_payload(
                    user_text,
                    history,
                    system_prompt,
                    speech_context=speech_context,
                ),
            )
        except httpx.TimeoutException as exc:
            raise VoiceStageTimeout("llm", self.config.timeout_seconds) from exc
        response.raise_for_status()
        result = response.json()
        output = result.get("output", [])

        reasoning = [
            item.get("content", "").strip()
            for item in output
            if item.get("type") == "reasoning" and item.get("content", "").strip()
        ]
        if reasoning:
            raise RuntimeError("LM Studio returned reasoning while reasoning=off")

        text = " ".join(
            item.get("content", "").strip()
            for item in output
            if item.get("type") == "message" and item.get("content", "").strip()
        ).strip()
        tool_calls = tuple(
            str(item.get("tool", ""))
            for item in output
            if item.get("type") == "tool_call" and item.get("tool")
        )
        invalid_tool_calls = [item for item in output if item.get("type") == "invalid_tool_call"]
        if invalid_tool_calls:
            raise RuntimeError("LM Studio returned an invalid tail-control tool call")
        if not text:
            raise RuntimeError("LM Studio returned no spoken response")

        self.last_tool_calls = tool_calls
        self.last_stats = {
            **result.get("stats", {}),
            "model": result.get("model_instance_id", self.config.model),
            "reasoning": "off",
        }
        self._runtime_observation.success(model=result.get("model_instance_id"))
        return VoiceLLMReply(text=text, tool_calls=tool_calls, stats=dict(self.last_stats))

    async def stream_response(
        self,
        user_text: str,
        history: Sequence[dict[str, str]] = (),
        system_prompt: str = COMPANION_SYSTEM_PROMPT,
        speech_context: dict[str, str | float] | None = None,
    ) -> AsyncIterator[str]:
        if not user_text.strip():
            raise ValueError("user_text must not be empty")
        self._runtime_observation.begin()
        try:
            async with aclosing(self._stream_response_impl(
                user_text, history, system_prompt, speech_context,
            )) as deltas:
                async for delta in deltas:
                    yield delta
        except Exception as error:
            self._runtime_observation.failure(error)
            raise
        finally:
            self._runtime_observation.finish()

    async def _stream_response_impl(
        self,
        user_text: str,
        history: Sequence[dict[str, str]] = (),
        system_prompt: str = COMPANION_SYSTEM_PROMPT,
        speech_context: dict[str, str | float] | None = None,
    ) -> AsyncIterator[str]:
        """Yield LM Studio SSE text deltas while retaining final stats and tool-call metadata."""
        if not user_text.strip():
            raise ValueError("user_text must not be empty")
        payload = self._build_payload(
            user_text,
            history,
            system_prompt,
            stream=True,
            speech_context=speech_context,
        )
        if self.config.tail_mcp_enabled:
            async with aclosing(self._stream_tail_response(payload)) as deltas:
                async for delta in deltas:
                    yield delta
            return
        event_data: list[str] = []
        response_parts: list[str] = []
        final_result: dict[str, Any] = {}

        async def handle_event(raw_event: str) -> tuple[str | None, dict[str, Any] | None]:
            if not raw_event.strip():
                return None, None
            event = json.loads(raw_event)
            event_type = event.get("type")
            if event_type == "error":
                detail = event.get("error", {})
                raise RuntimeError(detail.get("message", "LM Studio streaming error"))
            if event_type == "message.delta":
                return str(event.get("content", "")), None
            if event_type == "chat.end":
                result = event.get("result", {})
                return None, result if isinstance(result, dict) else {}
            return None, None

        try:
            timeout = httpx.Timeout(self.config.timeout_seconds, connect=5.0)
            async with self.client.stream(
                "POST",
                "/api/v1/chat",
                json=payload,
                timeout=timeout,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        event_data.append(line[5:].strip())
                        continue
                    if line.strip() or not event_data:
                        continue
                    delta, result = await handle_event("\n".join(event_data))
                    event_data.clear()
                    if result is not None:
                        final_result = result
                    if delta:
                        response_parts.append(delta)
                        yield delta
        except httpx.TimeoutException as exc:
            raise VoiceStageTimeout("llm", self.config.timeout_seconds) from exc

        if event_data:
            delta, result = await handle_event("\n".join(event_data))
            if result is not None:
                final_result = result
            if delta:
                response_parts.append(delta)
                yield delta

        text = "".join(response_parts).strip()
        if not text:
            raise RuntimeError("LM Studio returned no spoken response")
        output = final_result.get("output", [])
        if any(
            item.get("type") == "reasoning" and item.get("content", "").strip()
            for item in output
        ):
            raise RuntimeError("LM Studio returned reasoning while reasoning=off")
        if any(item.get("type") == "invalid_tool_call" for item in output):
            raise RuntimeError("LM Studio returned an invalid tail-control tool call")
        self.last_tool_calls = tuple(
            str(item.get("tool", ""))
            for item in output
            if item.get("type") == "tool_call" and item.get("tool")
        )
        self.last_stats = {
            **final_result.get("stats", {}),
            "model": final_result.get("model_instance_id", self.config.model),
            "reasoning": "off",
        }
        if final_result:
            self._runtime_observation.success(model=final_result.get("model_instance_id"))

    async def close(self) -> None:
        await self.client.aclose()
