"""App-owned stdio MCP tools for LM Studio's unauthenticated compatible API.

Iterate and close the generator in the same task so the stdio client's task group
is also closed by its owner. Only the two PC frequency configuration tools are exposed.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
import math
from numbers import Real
import os
from pathlib import Path
import re
import sys
from typing import Any, AsyncIterator

import httpx
from mcp import Client, StdioServerParameters

from ..runtime import VoiceStageTimeout


ALLOWED_TOOLS = frozenset({"set_tail_frequency", "get_tail_frequency"})
MAX_ROUNDS = 4
MAX_TOOL_CALLS = 8
MAX_ARGUMENT_CHARS = 8_192
MAX_EVENT_CHARS = 262_144
_TOOL_GUIDANCE = (
    "\n\n頻度ツールはPC側の希望設定を読む・保存するだけです。ESP32への反映や動作を確認したと答えないでください。"
    "ツール結果のisError=trueは失敗です。失敗を設定完了として伝えないでください。"
)


@dataclass
class _Call:
    identifier: str = ""
    name: str = ""
    arguments: str = ""


@asynccontextmanager
async def _open_tail_session(timeout_seconds: float):
    server = Path(__file__).resolve().parents[1] / "tail_mcp_server.py"
    environment = {"TAIL_CONTROL_ENABLED": "false", "TAIL_MCP_LOG_LEVEL": "WARNING"}
    if os.getenv("TAIL_DASHBOARD_URL"):
        environment["TAIL_DASHBOARD_URL"] = os.environ["TAIL_DASHBOARD_URL"]
    params = StdioServerParameters(command=sys.executable, args=[str(server)], env=environment, cwd=str(server.parent))
    async with Client(params, read_timeout_seconds=min(10.0, timeout_seconds)) as session:
        yield session


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate argument key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON number: {value}")


def _arguments(name: str, raw: str) -> dict[str, Any]:
    if name not in ALLOWED_TOOLS:
        raise ValueError("Only set_tail_frequency and get_tail_frequency are allowed")
    if len(raw) > MAX_ARGUMENT_CHARS:
        raise ValueError("Tool arguments are too large")
    parsed = json.loads(raw or "{}", object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    if not isinstance(parsed, dict):
        raise ValueError("Tool arguments must be an object")
    if name == "get_tail_frequency":
        if parsed:
            raise ValueError("get_tail_frequency takes no arguments")
        return {}
    if set(parsed) != {"rate_hz"}:
        raise ValueError("set_tail_frequency requires only rate_hz")
    return {"rate_hz": _rate_number(parsed["rate_hz"])}


def _rate_number(rate: Any) -> float:
    if isinstance(rate, bool) or not isinstance(rate, Real):
        raise ValueError("rate_hz must be a finite number from 0.3 to 1.0")
    try:
        number = float(rate)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("rate_hz must be a finite number from 0.3 to 1.0") from exc
    if not math.isfinite(number) or not 0.3 <= number <= 1.0:
        raise ValueError("rate_hz must be a finite number from 0.3 to 1.0")
    return number


def _failure(message: str) -> dict[str, Any]:
    return {"isError": True, "error": message, "device_confirmed": False}


def _tool_result(result: Any, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    wire = result.model_dump(mode="json", by_alias=True, exclude_none=True)
    if wire.get("isError"):
        messages = [item.get("text", "") for item in wire.get("content", []) if item.get("type") == "text"]
        return _failure("MCP tool failed: " + " ".join(messages)[:1_200])
    value = wire.get("structuredContent")
    if isinstance(value, dict) and isinstance(value.get("result"), dict):
        value = value["result"]
    if not isinstance(value, dict):
        for item in wire.get("content", []):
            if item.get("type") != "text":
                continue
            try:
                value = json.loads(item.get("text", ""))
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict):
                break
    if not isinstance(value, dict):
        return _failure("MCP returned no configuration confirmation")
    try:
        rate = _rate_number(value.get("rate_hz"))
    except ValueError:
        return _failure("MCP returned an invalid frequency")
    if value.get("device_confirmed") is not False or value.get("applies_to") != "configuration":
        return _failure("MCP returned an invalid PC configuration result; device application is not confirmed")
    if name == "set_tail_frequency" and not math.isclose(float(rate), arguments["rate_hz"], abs_tol=1e-12):
        return _failure("MCP did not confirm the requested frequency")
    return {"isError": False, "result": {"rate_hz": float(rate), "interval_ms": int(round(1000 / rate)),
            "device_confirmed": False, "applies_to": "configuration"}}


async def _discover_tools(session: Any) -> tuple[list[dict[str, Any]], set[str]]:
    definitions: dict[str, dict[str, Any]] = {}
    cursor = None
    seen: set[str] = set()
    for _ in range(16):
        result = await session.list_tools(**({"cursor": cursor} if cursor else {}))
        for tool in result.tools:
            if tool.name in ALLOWED_TOOLS:
                parameters = dict(tool.input_schema)
                parameters["additionalProperties"] = False
                definitions[tool.name] = {"type": "function", "function": {
                    "name": tool.name, "description": tool.description or "PC tail frequency configuration only",
                    "parameters": parameters,
                }}
        cursor = result.next_cursor
        if not cursor:
            break
        if cursor in seen:
            raise RuntimeError("MCP returned a repeated tool-list cursor")
        seen.add(cursor)
    else:
        raise RuntimeError("MCP tool list exceeded its page limit")
    if set(definitions) != ALLOWED_TOOLS:
        raise RuntimeError("The two tail frequency tools are not available from MCP")
    return [definitions[name] for name in sorted(definitions)], set(definitions)


async def _sse_events(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    data: list[str] = []
    length = 0
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            part = line[5:].lstrip()
            length += len(part)
            if length > MAX_EVENT_CHARS:
                raise RuntimeError("LM Studio SSE event exceeded its size limit")
            data.append(part)
        elif not line.strip() and data:
            raw = "\n".join(data)
            data.clear()
            length = 0
            if raw.strip() == "[DONE]":
                return
            event = json.loads(raw)
            if not isinstance(event, dict):
                raise RuntimeError("LM Studio returned an invalid streaming event")
            yield event
    if data:
        raw = "\n".join(data)
        if raw.strip() != "[DONE]":
            event = json.loads(raw)
            if not isinstance(event, dict):
                raise RuntimeError("LM Studio returned an invalid streaming event")
            yield event


def _buffer_frequency_reply(user_input: str) -> bool:
    current = user_input.rsplit("今回のユーザー発話（質問・依頼）:\n", 1)[-1]
    return bool(re.search(r"トントン|とんとん|しっぽ|尻尾|頻度|毎秒|毎分|ヘルツ|\bhz\b|\btail\b|\bfrequency\b", current, re.IGNORECASE))


async def stream_tail_chat(
    client: httpx.AsyncClient,
    *,
    model: str,
    user_input: str,
    system_prompt: str,
    max_output_tokens: int,
    context_length: int,
    timeout_seconds: float,
) -> AsyncIterator[dict[str, Any]]:
    """Stream compatible chat completion text while resolving only frequency MCP tools."""
    if not model.strip() or not user_input.strip():
        raise ValueError("model and user_input must not be empty")
    if (isinstance(timeout_seconds, bool) or not math.isfinite(float(timeout_seconds)) or timeout_seconds <= 0
            or isinstance(max_output_tokens, bool) or max_output_tokens < 1
            or isinstance(context_length, bool) or context_length < 1):
        raise ValueError("Invalid voice model limits")
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt.strip() + _TOOL_GUIDANCE},
        {"role": "user", "content": user_input},
    ]
    actual_calls: list[str] = []
    attempted_calls = 0
    failed_calls = 0
    usage: dict[str, int] = {}
    buffer_frequency = _buffer_frequency_reply(user_input)
    try:
        async with asyncio.timeout(timeout_seconds):
            async with _open_tail_session(timeout_seconds) as session:
                tools, available = await _discover_tools(session)
                for round_index in range(MAX_ROUNDS):
                    final_only = round_index == MAX_ROUNDS - 1 or attempted_calls >= MAX_TOOL_CALLS
                    payload = {
                        "model": model, "messages": messages, "tools": tools,
                        "tool_choice": "none" if final_only else "auto", "parallel_tool_calls": False,
                        "stream": True, "stream_options": {"include_usage": True},
                        "max_tokens": max_output_tokens, "context_length": context_length,
                        "temperature": 0.7, "reasoning_effort": "none", "store": False,
                    }
                    calls: dict[int, _Call] = {}
                    content: list[str] = []
                    finish_reason: str | None = None
                    buffered = buffer_frequency and not final_only
                    async with client.stream("POST", "/v1/chat/completions", json=payload,
                                             timeout=httpx.Timeout(timeout_seconds, connect=min(5.0, timeout_seconds))) as response:
                        response.raise_for_status()
                        async for event in _sse_events(response):
                            if event.get("error"):
                                raise RuntimeError("LM Studio compatible streaming request failed")
                            event_usage = event.get("usage")
                            if isinstance(event_usage, dict):
                                details = event_usage.get("completion_tokens_details") or {}
                                if details.get("reasoning_tokens", 0):
                                    raise RuntimeError("LM Studio returned reasoning while reasoning_effort=none")
                                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                                    value = event_usage.get(key)
                                    if isinstance(value, int) and not isinstance(value, bool):
                                        usage[key] = usage.get(key, 0) + value
                            for choice in event.get("choices", []):
                                if choice.get("index", 0) != 0:
                                    continue
                                delta = choice.get("delta") or {}
                                if delta.get("reasoning") or delta.get("reasoning_content"):
                                    raise RuntimeError("LM Studio returned reasoning while reasoning_effort=none")
                                fragments = delta.get("tool_calls") or []
                                for fragment in fragments:
                                    if fragment.get("type") not in {None, "function"}:
                                        raise RuntimeError("LM Studio returned an unsupported tool-call type")
                                    index = fragment.get("index")
                                    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < 32:
                                        raise RuntimeError("LM Studio returned an invalid tool-call index")
                                    call = calls.setdefault(index, _Call())
                                    function = fragment.get("function") or {}
                                    for value in (fragment.get("id"), function.get("name"), function.get("arguments")):
                                        if value is not None and not isinstance(value, str):
                                            raise RuntimeError("LM Studio returned an invalid tool-call fragment")
                                    call.identifier += fragment.get("id") or ""
                                    call.name += function.get("name") or ""
                                    call.arguments += function.get("arguments") or ""
                                    if len(call.identifier) > 256 or len(call.name) > 128 or len(call.arguments) > MAX_ARGUMENT_CHARS:
                                        raise RuntimeError("LM Studio tool-call fragments exceeded their limit")
                                text = delta.get("content")
                                if text is not None and not isinstance(text, str):
                                    raise RuntimeError("LM Studio returned non-text voice content")
                                if text:
                                    content.append(text)
                                    if sum(map(len, content)) > 8_000:
                                        raise RuntimeError("LM Studio voice response exceeded its limit")
                                    if not buffered and not calls:
                                        yield {"type": "text_delta", "text": text}
                                if choice.get("finish_reason") is not None:
                                    finish_reason = choice["finish_reason"]
                    if finish_reason not in {"stop", "length", "tool_calls"}:
                        raise RuntimeError("LM Studio voice stream ended without a completion marker")
                    if finish_reason == "tool_calls" and not calls:
                        raise RuntimeError("LM Studio marked tool calls without providing a call")
                    if not calls:
                        if not "".join(content).strip():
                            raise RuntimeError("LM Studio returned no spoken response")
                        if buffered:
                            for text in content:
                                yield {"type": "text_delta", "text": text}
                        yield {"type": "complete", "tool_calls": tuple(actual_calls), "stats": {
                            "model": model, "reasoning": "off", "reasoning_effort": "none", "rounds": round_index + 1,
                            "tool_calls_attempted": attempted_calls, "tool_calls_failed": failed_calls, "usage": usage,
                        }}
                        return
                    if final_only:
                        raise RuntimeError("LM Studio exceeded the tail-tool round or call limit")
                    reconstructed: list[dict[str, Any]] = []
                    identifiers: set[str] = set()
                    for _, call in sorted(calls.items()):
                        if not call.identifier or not call.name or call.identifier in identifiers:
                            raise RuntimeError("LM Studio returned an incomplete or duplicate tool call")
                        identifiers.add(call.identifier)
                        reconstructed.append({"id": call.identifier, "type": "function", "function": {"name": call.name, "arguments": call.arguments}})
                    messages.append({"role": "assistant", "content": "".join(content) or None, "tool_calls": reconstructed})
                    for call in reconstructed:
                        name = call["function"]["name"]
                        attempted_calls += 1
                        if attempted_calls > MAX_TOOL_CALLS:
                            result = _failure("The tail-tool call limit has been reached")
                        else:
                            try:
                                arguments = _arguments(name, call["function"]["arguments"])
                                if name not in available:
                                    raise ValueError("The requested tail tool is unavailable")
                            except (TypeError, ValueError) as exc:
                                result = _failure(str(exc))
                            else:
                                actual_calls.append(name)
                                try:
                                    result = _tool_result(await session.call_tool(name, arguments), name, arguments)
                                except Exception as exc:
                                    result = _failure(f"MCP call failed ({type(exc).__name__}); no setting success is confirmed")
                        failed_calls += int(result["isError"])
                        messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result, ensure_ascii=False, allow_nan=False)})
                    buffer_frequency = True
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise VoiceStageTimeout("llm", timeout_seconds) from exc
