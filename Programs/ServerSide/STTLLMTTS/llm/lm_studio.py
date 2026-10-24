"""短い添い寝会話を LM Studio へ問い合わせる音声用クライアント。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from dataclasses import dataclass
from typing import Any, AsyncIterator, Sequence

import httpx
from dotenv import load_dotenv

from ..runtime import VoiceStageTimeout
from ...dashboard.settings_store import get_dashboard_settings
from ...dashboard.runtime_status import RuntimeObservation, component_configuration

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)


COMPANION_SYSTEM_PROMPT = """あなたは、寝る前の寂しさに寄り添う短い会話相手です。
日本語で、落ち着いた温かい言葉を1〜2文で返してください。相手の気持ちを決めつけず、眠ることを急かさず、会話を続けるためだけの質問を毎回しないでください。
ユーザーがトントンを明確に希望したときだけ tap_tail を使い、止めてほしいと言われたら stop_tail を使ってください。寂しさを表明しただけで、動作を勝手に始めないでください。
動作ツールの結果が成功したと確認できたときだけ、しっぽが動いたと伝えてください。サーボ角度や生のハードウェア制御は指定しないでください。
"""


@dataclass(frozen=True)
class VoiceLLMConfig:
    base_url: str = "http://127.0.0.1:1234"
    api_key: str = "lm-studio"
    model: str = "google/gemma-4-26b-a4b-qat"
    max_output_tokens: int = 160
    context_length: int = 4096
    max_history_messages: int = 8
    temperature: float = 0.7
    timeout_seconds: float = 45.0
    tail_mcp_enabled: bool = False
    tail_mcp_plugin_id: str = "mcp/nen-gitsune-tail"

    @classmethod
    def from_env(cls) -> "VoiceLLMConfig":
        settings = get_dashboard_settings()["llm"]
        return cls(
            base_url=os.getenv("LM_STUDIO_BASE_URL", cls.base_url).rstrip("/"),
            api_key=os.getenv("LM_STUDIO_API_KEY", cls.api_key),
            model=settings["model"],
            max_output_tokens=settings["max_output_tokens"],
            context_length=settings["context_length"],
            max_history_messages=settings["max_history_messages"],
            temperature=settings["temperature"],
            timeout_seconds=settings["timeout_seconds"],
            tail_mcp_enabled=os.getenv("TAIL_MCP_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"},
            tail_mcp_plugin_id=os.getenv("TAIL_MCP_PLUGIN_ID", cls.tail_mcp_plugin_id),
        )


@dataclass(frozen=True)
class VoiceLLMReply:
    text: str
    tool_calls: tuple[str, ...]
    stats: dict[str, Any]


class VoiceLLMClient:
    """LM Studio native REST APIを使う。ツールを有効にする場合も尾端用MCPだけに限定する。"""

    def __init__(self, config: VoiceLLMConfig | None = None) -> None:
        self.config = config or VoiceLLMConfig.from_env()
        self._runtime_observation = RuntimeObservation(
            component_configuration(self.config.model, backend="lm_studio"), external=True,
        )
        headers = {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else {}
        self.client = httpx.AsyncClient(
            base_url=self.config.base_url,
            headers=headers,
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
        if self.config.tail_mcp_enabled:
            payload["integrations"] = [
                {
                    "type": "plugin",
                    "id": self.config.tail_mcp_plugin_id,
                    "allowed_tools": ["tap_tail", "stop_tail", "get_tail_status"],
                }
            ]
        return payload

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
            async for delta in self._stream_response_impl(
                user_text, history, system_prompt, speech_context,
            ):
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
