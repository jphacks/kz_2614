"""短い添い寝会話を LM Studio へ問い合わせる音声用クライアント。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Sequence

import httpx
from dotenv import load_dotenv

from ..runtime import VoiceStageTimeout

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
    model: str = "google/gemma-4-26b-a4b"
    max_output_tokens: int = 160
    context_length: int = 4096
    max_history_messages: int = 8
    timeout_seconds: float = 45.0
    tail_mcp_enabled: bool = False
    tail_mcp_plugin_id: str = "mcp/nen-gitsune-tail"

    @classmethod
    def from_env(cls) -> "VoiceLLMConfig":
        return cls(
            base_url=os.getenv("LM_STUDIO_BASE_URL", cls.base_url).rstrip("/"),
            api_key=os.getenv("LM_STUDIO_API_KEY", cls.api_key),
            model=os.getenv("VOICE_LLM_MODEL", cls.model),
            max_output_tokens=max(32, int(os.getenv("VOICE_LLM_MAX_TOKENS", "160"))),
            context_length=max(512, int(os.getenv("VOICE_LLM_CONTEXT_LENGTH", "4096"))),
            max_history_messages=max(0, int(os.getenv("VOICE_LLM_HISTORY_MESSAGES", "8"))),
            timeout_seconds=max(5.0, float(os.getenv("VOICE_LLM_TIMEOUT_SECONDS", "45"))),
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
            "temperature": 0.7,
            "store": False,
            "stream": False,
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
    ) -> VoiceLLMReply:
        """STTで得た現在のユーザー発話を質問・依頼としてLM Studioへ送る。"""
        if not user_text.strip():
            raise ValueError("user_text must not be empty")

        try:
            response = await self.client.post(
                "/api/v1/chat",
                json=self._build_payload(user_text, history, system_prompt),
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
        return VoiceLLMReply(text=text, tool_calls=tool_calls, stats=dict(self.last_stats))

    async def close(self) -> None:
        await self.client.aclose()
