"""App-owned in-process MCP tools for LM Studio's unauthenticated compatible API.

Iterate and close the generator in the same task so the MCP client's task group
is also closed by its owner. Hardware starts require a trusted live conversation lease.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
import logging
import math
from numbers import Real
import re
import unicodedata
from typing import Any, AsyncIterator

import httpx
from mcp import Client

from ..runtime import VoiceStageTimeout
from .tail_control import TailControlError, current_tail_lease, get_tail_coordinator


ALLOWED_TOOLS = frozenset({"tap_tail", "stop_tail", "get_tail_status"})
logger = logging.getLogger("nen_gitsune.tail_tools")
MAX_ROUNDS = 4
MAX_TOOL_CALLS = 8
MAX_ARGUMENT_CHARS = 8_192
MAX_EVENT_CHARS = 262_144
_TOOL_GUIDANCE = (
    "\n\nしっぽの開始は今回のユーザーの明確な『トントンして』等の要求がある時だけです。"
    "tap_tailは0.5〜5秒、速度0.3回/秒固定です。同じ発話で繰り返して延長しないでください。"
    "停止はstop_tail、状態はget_tail_statusを使います。中立・脱力を解除する操作はありません。"
    "feedback=command_onlyは機器の指令状態です。成功時も『動作指令を送りました』等、受付の範囲で伝え、"
    "物理的に動いた・止まったと確認したとは答えないでください。isError=trueは失敗です。"
)


@dataclass
class _Call:
    identifier: str = ""
    name: str = ""
    arguments: str = ""


@asynccontextmanager
async def _open_tail_session(timeout_seconds: float):
    from ..tail_mcp_server import create_tail_server
    async with Client(create_tail_server(current_tail_lease()),
                      read_timeout_seconds=min(10.0, timeout_seconds)) as session:
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
        raise ValueError("Only tap_tail, stop_tail and get_tail_status are allowed")
    if len(raw) > MAX_ARGUMENT_CHARS:
        raise ValueError("Tool arguments are too large")
    parsed = json.loads(raw or "{}", object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    if not isinstance(parsed, dict):
        raise ValueError("Tool arguments must be an object")
    if name != "tap_tail":
        if parsed:
            raise ValueError(name + " takes no arguments")
        return {}
    if set(parsed) - {"duration_seconds"}:
        raise ValueError("tap_tail accepts only duration_seconds; speed is fixed")
    return {"duration_seconds": _duration_number(parsed.get("duration_seconds", 5.0))}


def _duration_number(rate: Any) -> float:
    if isinstance(rate, bool) or not isinstance(rate, Real):
        raise ValueError("duration_seconds must be a finite number from 0.5 to 5.0")
    try:
        number = float(rate)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("duration_seconds must be a finite number from 0.5 to 5.0") from exc
    if not math.isfinite(number) or not 0.5 <= number <= 5.0:
        raise ValueError("duration_seconds must be a finite number from 0.5 to 5.0")
    return number


def _failure(message: str) -> dict[str, Any]:
    return {"isError": True, "error": message, "device_confirmed": False}


def _tool_result(result: Any, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    wire = result.model_dump(mode="json", by_alias=True, exclude_none=True)
    if wire.get("isError"):
        messages = [item.get("text", "") for item in wire.get("content", []) if item.get("type") == "text"]
        return _failure("MCP tool failed: " + " ".join(messages)[:1_200])
    value = wire.get("structuredContent")
    if isinstance(value, dict) and "result" in value:
        value = value["result"]
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                value = None
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
    return _device_result(value, name)


def _device_result(value: Any, name: str) -> dict[str, Any]:
    from .tail_v2 import validate_state, valid_uuid
    if name == "stop_tail":
        if (not isinstance(value, dict) or set(value) != {"status", "device_epoch", "stop_revision", "feedback"}
                or value.get("feedback") != "command_only"
                or value.get("status") not in {"command_stopped", "unconfirmed", "disabled"}):
            return _failure("Invalid v2 stop result")
        epoch, revision = value["device_epoch"], value["stop_revision"]
        if (epoch is not None and (not isinstance(epoch, str) or re.fullmatch(r"[0-9a-f]{32}", epoch) is None)
                or revision is not None and (type(revision) is not int or not 0 <= revision <= 9007199254740991)
                or value["status"] == "command_stopped" and (epoch is None or revision is None)):
            return _failure("Invalid v2 stop fence")
        return {"isError": value["status"] != "command_stopped", "result": value}
    try:
        if isinstance(value, dict) and value.get("device_confirmed", False) is not False:
            raise ValueError("No physical feedback is available")
        state = validate_state(value)
        if name == "tap_tail" and (state["phase"] != "open" or state["motion"] != "tapping"
                                   or not valid_uuid(state.get("command_id"))):
            raise ValueError("Missing tap acknowledgement")
    except (ValueError, TailControlError):
        return _failure("Invalid v2 device command state")
    return {"isError": False, "result": {**state, "device_confirmed": False}}


async def _discover_tools(session: Any) -> tuple[list[dict[str, Any]], set[str]]:
    definitions: dict[str, dict[str, Any]] = {}
    cursor = None
    seen: set[str] = set()
    for _ in range(16):
        result = await session.list_tools(**({"cursor": cursor} if cursor else {}))
        for tool in result.tools:
            if tool.name in ALLOWED_TOOLS:
                parameters = {"type": "object", "additionalProperties": False, "properties": {}}
                if tool.name == "tap_tail":
                    parameters["properties"] = {"duration_seconds": {
                        "type": "number", "minimum": 0.5, "maximum": 5.0, "default": 5.0}}
                definitions[tool.name] = {"type": "function", "function": {
                    "name": tool.name, "description": tool.description or "ESP32 tail command status only",
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
        raise RuntimeError("The three ESP32 tail tools are not available from MCP")
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


def _buffer_tail_reply(user_input: str) -> bool:
    current = user_input.rsplit("今回のユーザー発話（質問・依頼）:\n", 1)[-1]
    return bool(re.search(r"トントン|とんとん|しっぽ|尻尾|頻度|毎秒|毎分|ヘルツ|\bhz\b|\btail\b|\bfrequency\b", current, re.IGNORECASE))


def _normalized_tail_request(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).strip().lower()
    text = re.sub(r"[\s、,。!！?？]+", "", text).rstrip(".")
    return text.translate(str.maketrans("トンコ", "とんこ"))


def _tail_intent(text: str) -> str | None:
    """Recognize a whole current command, including short everyday requests."""
    text = _normalized_tail_request(text)
    call = r"(?:(?:ねえねえ|ねえ|ねぇ|あのね|あの|すみません|ねんぎつね(?:ちゃん|さん)?|お願い)[ー〜～]?){0,2}"
    text = re.sub(r"^" + call, "", text)
    action = r"(?:とんとん|こんこん)"
    ending = r"(?:ください(?:ね)?|下さい(?:ね)?|くれる(?:かな)?|くれませんか|くれ|もらえる(?:かな)?|もらえますか|もらえませんか|ほしい(?:です)?|欲しい(?:です)?|みて(?:ください(?:ね)?)?|お願い(?:します)?|よ|ね)?"
    stop = r"(?:(?:しっぽ|尻尾|とんとん|こんこん)(?:を)?)?(?:止めて|停止して|やめて|ストップ)" + ending
    if re.fullmatch(stop, text) or re.fullmatch(r"(?:please)?stop(?:the|my)?(?:tail|tapping)?(?:please)?", text):
        return "stop"
    # Whole-command matching keeps negation, quotations, historical references,
    # open-ended movement and multiple actions outside this short-command route.
    duration = r"(?:(?:\d+(?:\.\d+)?|[一二三四五])秒(?:間)?(?:だけ)?)?"
    gentle = r"(?:少し|ちょっと|軽く|優しく|やさしく)?"
    prefix = gentle + r"(?:(?:しっぽ|尻尾)(?:で|を|に)?)?" + gentle + duration
    request = r"(?:して" + ending + r"|お願い(?:します)?|頼む|たのむ)"
    if re.fullmatch(prefix + action + r"(?:" + duration + r"|(?:を)?" + duration + request + r")", text):
        return "tap"
    if re.fullmatch(prefix + r"(?:叩いて|たたいて)" + duration + ending, text):
        return "tap"
    if re.fullmatch(r"(?:please)?tap(?:the|my)?tail(?:for\d+(?:\.\d+)?seconds?)?(?:please)?", text):
        return "tap"
    return None


def tail_request_intent(text: str) -> str | None:
    """Classify the current final transcript for command handling and acknowledgements."""
    return _tail_intent(text)


def _requested_tap_duration(text: str) -> float | None:
    """Keep an explicit duration in the trusted current request authoritative."""
    compact = _normalized_tail_request(text)
    matches = re.findall(r"(\d+(?:\.\d+)?|[一二三四五])秒|for(\d+(?:\.\d+)?)seconds?", compact)
    if not matches:
        return None
    if len(matches) != 1:
        raise TailControlError("The current request contains multiple tapping durations")
    value = next(part for part in matches[0] if part)
    japanese = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}
    try:
        return _duration_number(japanese[value] if value in japanese else float(value))
    except (ValueError, OverflowError) as exc:
        raise TailControlError("The requested tapping duration must be between 0.5 and 5.0 seconds") from exc


async def stream_tail_chat(
    client: httpx.AsyncClient,
    *,
    model: str,
    user_input: str,
    system_prompt: str,
    max_output_tokens: int,
    context_length: int,
    timeout_seconds: float,
    current_user_text: str = "",
    execute_confirmed_tap: bool = False,
) -> AsyncIterator[dict[str, Any]]:
    """Resolve ESP32 tools, with starts authorized by the current trusted session."""
    if not model.strip() or not user_input.strip():
        raise ValueError("model and user_input must not be empty")
    if (isinstance(timeout_seconds, bool) or not math.isfinite(float(timeout_seconds)) or timeout_seconds <= 0
            or isinstance(max_output_tokens, bool) or max_output_tokens < 1
            or isinstance(context_length, bool) or context_length < 1):
        raise ValueError("Invalid voice model limits")
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt.strip() + (
            "\nしっぽの開始は実際の音声再生に合わせてサーバーが管理します。tap_tailを呼ばず、開始済みと答えないでください。停止はstop_tail、状態はget_tail_statusです。"
            if current_tail_lease() and current_tail_lease().playback_owned else _TOOL_GUIDANCE)},
        {"role": "user", "content": user_input},
    ]
    actual_calls: list[str] = []
    tap_accepted = False
    attempted_calls = 0
    failed_calls = 0
    usage: dict[str, int] = {}
    intent = _tail_intent(current_user_text)
    buffer_tail = intent is not None or _buffer_tail_reply(user_input)
    lease = current_tail_lease()
    logger.info("tail_request intent=%s active_lease=%s", intent or "none", lease is not None)
    stop_result = None
    # Stop is dispatched before contacting the model, including when it never
    # chooses a tool or is unavailable. No explicit current request, no motion.
    if intent == "stop":
        try:
            stopped = await (lease.stop() if lease is not None else get_tail_coordinator().stop())
            stop_result = _device_result(stopped, "stop_tail")
        except Exception as exc:
            stop_result = _failure(f"Independent tail stop failed ({type(exc).__name__}); stop is unconfirmed")
        messages.append({"role": "system", "content": "今回の停止要求はモデルを待たず処理しました。結果: "
                         + json.dumps(stop_result, ensure_ascii=False, allow_nan=False)})
    try:
        async with asyncio.timeout(timeout_seconds):
            async with _open_tail_session(timeout_seconds) as session:
                tools, available = await _discover_tools(session)
                # A confirmed voice shortcut does not need a model round trip
                # to choose its sole allowed action. Keep the same lease and
                # device validation, then let the model explain the result.
                shortcut = execute_confirmed_tap and intent == "tap" and not (lease and lease.playback_owned)
                if shortcut:
                    attempted_calls += 1
                    try:
                        if lease is None:
                            raise TailControlError("Start requires an active conversation")
                        duration = _requested_tap_duration(current_user_text)
                        arguments = {"duration_seconds": 5.0 if duration is None else duration}
                        wire = await lease.call_tap(
                            lambda: session.call_tool("tap_tail", arguments), arguments["duration_seconds"])
                        actual_calls.append("tap_tail")
                        result = _tool_result(wire, "tap_tail", arguments)
                        if result["isError"]:
                            result["stop"] = await lease.stop()
                        else:
                            tap_accepted = True
                    except TailControlError as exc:
                        result = _failure(str(exc))
                    except Exception as exc:
                        result = _failure(f"Tail call failed ({type(exc).__name__}); device application is unconfirmed")
                    failed_calls += int(result["isError"])
                    logger.info("tail_tool_call intent=tap tool=tap_tail route=confirmed_voice result=%s",
                                "accepted" if tap_accepted else "failed")
                    messages.append({"role": "system", "content":
                        "サーバーが今回の確定したトントン要求を処理しました。再実行せず、次の受付結果だけを短く伝えてください。結果: "
                        + json.dumps(result, ensure_ascii=False, allow_nan=False)})
                for round_index in range(MAX_ROUNDS):
                    final_only = shortcut or round_index == MAX_ROUNDS - 1 or attempted_calls >= MAX_TOOL_CALLS
                    force_tap = (round_index == 0 and intent == "tap" and lease is not None
                                 and not lease.playback_owned and not final_only)
                    payload = {
                        "model": model, "messages": messages,
                        "tools": ([tool for tool in tools if tool["function"]["name"] != "tap_tail"]
                                  if lease and lease.playback_owned else
                                  ([tool for tool in tools if tool["function"]["name"] == "tap_tail"]
                                   if force_tap else tools)),
                        "tool_choice": "required" if force_tap else "none" if final_only else "auto",
                        "parallel_tool_calls": False,
                        "stream": True, "stream_options": {"include_usage": True},
                        "max_tokens": max_output_tokens, "context_length": context_length,
                        "temperature": 0.7, "reasoning_effort": "none", "store": False,
                    }
                    calls: dict[int, _Call] = {}
                    content: list[str] = []
                    finish_reason: str | None = None
                    buffered = buffer_tail and (not final_only or (intent == "tap" and not tap_accepted))
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
                        unfulfilled_tap = intent == "tap" and not tap_accepted and not (lease and lease.playback_owned)
                        if unfulfilled_tap:
                            # A model may ignore required tool_choice or describe a
                            # success despite a rejected call. Do not speak that
                            # unverified success or dispatch a fallback movement.
                            content = ["トントンの動作指令の受付を確認できませんでした。"]
                            buffered = True
                        elif intent == "stop" and stop_result is not None and stop_result["isError"]:
                            content = ["機器の停止を確認できませんでした。"]
                            buffered = True
                        elif not "".join(content).strip():
                            raise RuntimeError("LM Studio returned no spoken response")
                        if buffered:
                            for text in content:
                                yield {"type": "text_delta", "text": text}
                        logger.info("tail_tools_completed intent=%s attempted=%d failed=%d result=%s",
                                    intent or "none", attempted_calls, failed_calls,
                                    "unconfirmed" if unfulfilled_tap else "accepted" if tap_accepted else "complete")
                        yield {"type": "complete", "tool_calls": tuple(actual_calls), "stats": {
                            "model": model, "reasoning": "off", "reasoning_effort": "none", "rounds": round_index + 1,
                            "tool_calls_attempted": attempted_calls, "tool_calls_failed": failed_calls, "usage": usage,
                            "tail_intent": intent, "tail_command_unfulfilled": unfulfilled_tap,
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
                                try:
                                    if name == "tap_tail":
                                        if lease is not None and lease.playback_owned:
                                            raise TailControlError("Voice starts are owned by actual audio playback")
                                        if intent != "tap" or lease is None:
                                            raise TailControlError("Start requires an explicit current request and an active conversation")
                                        requested_duration = _requested_tap_duration(current_user_text)
                                        if requested_duration is not None:
                                            arguments["duration_seconds"] = requested_duration
                                        wire = await lease.call_tap(
                                            lambda: session.call_tool(name, arguments), arguments["duration_seconds"])
                                        actual_calls.append(name)
                                        result = _tool_result(wire, name, arguments)
                                        if result["isError"]:
                                            cleanup = await lease.stop()
                                            result["stop"] = cleanup
                                        else:
                                            tap_accepted = True
                                    elif name == "stop_tail":
                                        stopped = (await lease.stop() if lease is not None
                                                   else await get_tail_coordinator().stop()) if stop_result is None else None
                                        actual_calls.append(name)
                                        result = stop_result if stop_result is not None else _device_result(stopped, name)
                                    else:
                                        actual_calls.append(name)
                                        result = _tool_result(await session.call_tool(name, arguments), name, arguments)
                                except TailControlError as exc:
                                    result = _failure(str(exc))
                                except Exception as exc:
                                    result = _failure(f"Tail call failed ({type(exc).__name__}); device application is unconfirmed")
                        failed_calls += int(result["isError"])
                        logger.info("tail_tool_call intent=%s tool=%s result=%s",
                                    intent or "none", name if name in ALLOWED_TOOLS else "unsupported",
                                    "failed" if result["isError"] else "accepted")
                        messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result, ensure_ascii=False, allow_nan=False)})
                    buffer_tail = True
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise VoiceStageTimeout("llm", timeout_seconds) from exc
