"""ESP32 MCP chat loop: SSE fragments, authorization, limits and lifecycle."""

from __future__ import annotations

import asyncio
from contextlib import aclosing, asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from mcp import types

from server.ai.llm import tail_tools as tail
from server.ai.llm import tail_control
from server.ai.runtime import VoiceStageTimeout


def event(delta=None, *, finish=None, usage=None):
    result = {"choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}
    if usage is not None:
        result["usage"] = usage
    return result


def sse(events, *, done=True):
    wire = ": keepalive\n\n" + "".join("data: " + json.dumps(item, ensure_ascii=False) + "\n\n" for item in events)
    if done:
        wire += "data: [DONE]\n\n"
    return httpx.Response(200, text=wire, headers={"Content-Type": "text/event-stream"})


def tool_event(name="tap_tail", arguments='{"duration_seconds":5}', identifier="call-1", *, index=0):
    return event({"tool_calls": [{"index": index, "id": identifier, "type": "function", "function": {"name": name, "arguments": arguments}}]})


def final(text="ESP32へ動作指令を送りました。物理動作は未確認です。"):
    return sse([event({"content": text}), event(finish="stop", usage={"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14})])


def device_result(name="tap_tail", **changes):
    from server.ai.llm.tail_v2 import LIMITS
    return {"protocol_version": 2, "capabilities": ["revision_fence"], "control_mode": "v2_exclusive",
            "device_epoch": "a" * 32, "revision": 2, "session_id": "00000000-0000-0000-0000-000000000001",
            "phase": "open", "motion": "tapping" if name == "tap_tail" else "idle",
            "start_enabled": True, "calibrated": True, "feedback": "command_only", "limits": dict(LIMITS),
            "command_id": "00000000-0000-0000-0000-000000000002", **changes}


def stop_confirmation():
    return {"status": "command_stopped", "device_epoch": "a" * 32, "stop_revision": 3, "feedback": "command_only"}


class FakeLease:
    def __init__(self, *, stale=False):
        self.stale = stale
        self.used = False
        self.stops = 0

    async def call_tap(self, callback, duration):
        if self.stale or self.used:
            raise tail.TailControlError("stale or previously used lease")
        self.used = True
        return await callback()

    async def stop(self):
        self.stale = True
        self.stops += 1
        return stop_confirmation()


_DEFAULT_LEASE = object()


class FakeMCP:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []
        self.owner = None
        self.closed_by = None
        self.closed = False
        self.extra_names = {"set_tail_frequency", "get_tail_frequency", "arbitrary_angle"}

    @asynccontextmanager
    async def open(self, timeout):
        self.owner = asyncio.current_task()
        try:
            yield self
        finally:
            self.closed_by = asyncio.current_task()
            self.closed = True
            if self.closed_by is not self.owner:
                raise AssertionError("MCP context must close in its owning task")

    async def list_tools(self, **kwargs):
        names = set(tail.ALLOWED_TOOLS) | self.extra_names
        return SimpleNamespace(tools=[SimpleNamespace(
            name=name, description=f"tool {name}",
            input_schema={"type": "object", "properties": {"rate_hz": {"type": "number", "minimum": 0.3, "maximum": 1.0}} if name == "set_tail_frequency" else {},
                          **({"required": ["rate_hz"]} if name == "set_tail_frequency" else {})},
        ) for name in sorted(names)], next_cursor=None)

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self.error:
            raise self.error
        return self.result if self.result is not None else types.CallToolResult(
            content=[], structured_content={"result": device_result(name)})


class TailToolChatTests(unittest.IsolatedAsyncioTestCase):
    async def test_voice_shortcut_starts_before_model_and_passes_result_without_repeating(self):
        mcp = FakeMCP()
        async def model(request):
            self.assertEqual(mcp.calls, [("tap_tail", {"duration_seconds": 1.0})])
            body = json.loads(request.content)
            self.assertEqual(body["tool_choice"], "none")
            self.assertIn('"motion": "tapping"', body["messages"][-1]["content"])
            return final("動作指令を送りました。")
        async with httpx.AsyncClient(base_url="http://model.test", transport=httpx.MockTransport(model)) as client:
            with patch.object(tail, "_open_tail_session", mcp.open), patch.object(tail, "current_tail_lease", return_value=FakeLease()):
                result = [event async for event in tail.stream_tail_chat(client, **self.options(
                    current_user_text="1秒こんこんして", execute_confirmed_tap=True))]
        self.assertEqual(result[-1]["tool_calls"], ("tap_tail",))
        self.assertEqual(result[-1]["stats"]["rounds"], 1)
        self.assertFalse(result[-1]["stats"]["tail_command_unfulfilled"])

    async def test_voice_shortcut_keeps_authorization_duration_and_negation_guards(self):
        for phrase, lease in [("こんこん", None), ("こんこん", FakeLease(stale=True)),
                              ("6秒こんこん", FakeLease()), ("1秒こんこん2秒", FakeLease())]:
            with self.subTest(phrase=phrase, lease=lease):
                result, requests, mcp = await self.exercise([final("動かしました。")], lease=lease,
                    options={"current_user_text": phrase, "execute_confirmed_tap": True})
                self.assertEqual(mcp.calls, [])
                self.assertTrue(result[-1]["stats"]["tail_command_unfulfilled"])
                self.assertNotIn("動かしました", "".join(x.get("text", "") for x in result))
                self.assertEqual(requests[0][1]["tool_choice"], "none")
        result, _, mcp = await self.exercise([final("開始しません。")],
            options={"current_user_text": "こんこんしないで", "execute_confirmed_tap": True})
        self.assertEqual(mcp.calls, [])

    async def test_voice_shortcut_rejects_invalid_ack_and_never_retries_after_model_failure(self):
        mcp = FakeMCP(result=types.CallToolResult(content=[], structured_content={"result": device_result(motion="idle")}))
        result, _, mcp = await self.exercise([final("動かしました。")], mcp=mcp,
            options={"execute_confirmed_tap": True})
        self.assertEqual(len(mcp.calls), 1)
        self.assertEqual(mcp.lease.stops, 1)
        self.assertTrue(result[-1]["stats"]["tail_command_unfulfilled"])
        mcp = FakeMCP()
        with self.assertRaises(VoiceStageTimeout):
            await self.exercise([httpx.ReadTimeout("model unavailable")], mcp=mcp,
                options={"execute_confirmed_tap": True})
        self.assertEqual(len(mcp.calls), 1)

    def options(self, **changes):
        return {"model": "test-model", "user_input": "今回のユーザー発話（質問・依頼）:\nトントンして", "current_user_text": "トントンして", "system_prompt": "短く返答してください。",
                "max_output_tokens": 160, "context_length": 4096, "timeout_seconds": 5.0, **changes}

    async def exercise(self, responses, *, mcp=None, options=None, lease=_DEFAULT_LEASE):
        mcp = mcp or FakeMCP()
        requests = []
        lease = FakeLease() if lease is _DEFAULT_LEASE else lease
        mcp.lease = lease
        async def handle(request):
            requests.append((request, json.loads(request.content)))
            item = responses[len(requests) - 1]
            if isinstance(item, Exception):
                raise item
            return item
        async with httpx.AsyncClient(base_url="http://model.test", transport=httpx.MockTransport(handle)) as client:
            with patch.object(tail, "_open_tail_session", mcp.open), \
                    patch.object(tail, "current_tail_lease", return_value=lease):
                result = [item async for item in tail.stream_tail_chat(client, **self.options(**(options or {})))]
        self.assertTrue(mcp.closed)
        self.assertIs(mcp.owner, mcp.closed_by)
        return result, requests, mcp

    async def test_fragmented_parallel_calls_are_rebuilt_filtered_and_followed_by_results(self):
        first = sse([
            event({"content": "変更しておきます。"}),
            event({"tool_calls": [
                {"index": 1, "id": "read-", "type": "function", "function": {"name": "get_tail_", "arguments": "{"}},
                {"index": 0, "id": "set-", "type": "function", "function": {"name": "tap_ta", "arguments": '{"duration_'}},
            ]}),
            event({"tool_calls": [
                {"index": 0, "id": "1", "function": {"name": "il", "arguments": 'seconds":5}'}},
                {"index": 1, "id": "1", "function": {"name": "status", "arguments": "}"}},
            ]}), event(finish="tool_calls"),
        ])
        result, requests, mcp = await self.exercise([first, final()])
        self.assertEqual(mcp.calls, [("tap_tail", {"duration_seconds": 5.0}), ("get_tail_status", {})])
        self.assertNotIn("変更しておきます", "".join(item.get("text", "") for item in result))
        body = requests[0][1]
        self.assertEqual({item["function"]["name"] for item in body["tools"]}, {"tap_tail"})
        self.assertEqual({item["function"]["name"] for item in requests[1][1]["tools"]}, tail.ALLOWED_TOOLS)
        definitions = {item["function"]["name"]: item["function"]["parameters"] for item in body["tools"]}
        self.assertEqual(set(definitions["tap_tail"]["properties"]), {"duration_seconds"})
        self.assertFalse(definitions["tap_tail"]["additionalProperties"])
        self.assertEqual(body["tool_choice"], "required")
        self.assertEqual(body["reasoning_effort"], "none")
        self.assertNotIn("integrations", body)
        self.assertNotIn("reasoning", body)
        self.assertNotIn("authorization", requests[0][0].headers)
        self.assertEqual(requests[0][0].url.path, "/v1/chat/completions")
        followup = requests[1][1]["messages"]
        assistant = followup[-3]
        self.assertEqual([call["id"] for call in assistant["tool_calls"]], ["set-1", "read-1"])
        self.assertEqual([item["tool_call_id"] for item in followup[-2:]], ["set-1", "read-1"])
        for item in followup[-2:]:
            outcome = json.loads(item["content"])
            self.assertFalse(outcome["isError"])
            self.assertFalse(outcome["result"]["device_confirmed"])
        self.assertEqual(result[-1]["tool_calls"], ("tap_tail", "get_tail_status"))
        self.assertEqual(result[-1]["stats"]["rounds"], 2)

    async def test_non_tool_content_is_yielded_before_the_rest_of_stream_finishes(self):
        gate = asyncio.Event()
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield ("data: " + json.dumps(event({"content": "こんにちは。"})) + "\n\n").encode()
                await gate.wait()
                yield ("data: " + json.dumps(event({"content": "聞いているよ。"})) + "\n\n" + "data: " + json.dumps(event(finish="stop")) + "\n\ndata: [DONE]\n\n").encode()
        mcp = FakeMCP()
        async with httpx.AsyncClient(base_url="http://model.test", transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Stream()))) as client:
            with patch.object(tail, "_open_tail_session", mcp.open):
                async with aclosing(tail.stream_tail_chat(client, **self.options(
                        user_input="今日は楽しかった", current_user_text="今日は楽しかった"))) as stream:
                    self.assertEqual(await anext(stream), {"type": "text_delta", "text": "こんにちは。"})
                    self.assertFalse(mcp.closed)
                    gate.set()
                    rest = [item async for item in stream]
        self.assertEqual(rest[0]["text"], "聞いているよ。")
        self.assertEqual(rest[-1]["tool_calls"], ())
        self.assertTrue(mcp.closed)

    async def test_early_close_releases_mcp_in_the_same_task(self):
        mcp = FakeMCP()
        async with httpx.AsyncClient(base_url="http://model.test", transport=httpx.MockTransport(lambda request: final("こんにちは。"))) as client:
            with patch.object(tail, "_open_tail_session", mcp.open):
                async with aclosing(tail.stream_tail_chat(client, **self.options(
                        user_input="こんにちは", current_user_text="こんにちは"))) as stream:
                    await anext(stream)
                self.assertTrue(mcp.closed)
                self.assertIs(mcp.owner, mcp.closed_by)

    async def test_disallowed_tools_and_invalid_arguments_become_error_results_without_mcp_calls(self):
        invalid = [
            ("set_tail_frequency", '{}'), ("get_tail_frequency", '{}'),
            ("tap_tail", '{"duration_seconds":true}'),
            ("tap_tail", '{"duration_seconds":"1"}'), ("tap_tail", '{"duration_seconds":0.49}'),
            ("tap_tail", '{"duration_seconds":5.01}'), ("tap_tail", '{"duration_seconds":NaN}'),
            ("tap_tail", '{"duration_seconds":Infinity}'), ("tap_tail", '{"duration_seconds":1,"url":"x"}'),
            ("tap_tail", '{"duration_seconds":1,"duration_seconds":2}'), ("tap_tail", '[1]'),
            ("tap_tail", '{"duration_seconds":'), ("get_tail_status", '{"duration_seconds":1}'),
            ("tap_tail", '{"duration_seconds":1,"taps_per_second":0.6}'),
            ("tap_tail", '{"rate_hz":0.3}'), ("stop_tail", '{"duration_seconds":1}'),
            ("tap_tail", '{"duration_seconds":' + "9" * 4000 + '}'),
        ]
        for name, arguments in invalid:
            with self.subTest(name=name, arguments=arguments[:80]):
                result, requests, mcp = await self.exercise([sse([tool_event(name, arguments), event(finish="tool_calls")]), final("設定を変更できませんでした。")])
                self.assertEqual(mcp.calls, [])
                outcome = json.loads(requests[1][1]["messages"][-1]["content"])
                self.assertTrue(outcome["isError"])
                self.assertFalse(outcome["device_confirmed"])
                self.assertEqual(result[-1]["tool_calls"], ())
                self.assertEqual(result[-1]["stats"]["tool_calls_failed"], 1)

    async def test_mcp_error_exception_and_invalid_success_confirmation_are_failures(self):
        cases = [
            FakeMCP(result=types.CallToolResult(is_error=True, content=[types.TextContent(type="text", text="設定APIへ接続できませんでした")])),
            FakeMCP(error=RuntimeError("Unreachable")),
            FakeMCP(result=types.CallToolResult(content=[], structured_content={"ok": True, "state": "unknown", "feedback": "command_only"})),
            FakeMCP(result=types.CallToolResult(content=[], structured_content=device_result(device_confirmed=True))),
            FakeMCP(result=types.CallToolResult(content=[], structured_content=device_result(motion="idle"))),
            FakeMCP(result=types.CallToolResult(content=[], structured_content={"result": "not JSON"})),
            FakeMCP(result=types.CallToolResult(content=[], structured_content=device_result(revision=True))),
            FakeMCP(result=types.CallToolResult(content=[], structured_content=device_result(command_id="invalid"))),
        ]
        for mcp in cases:
            with self.subTest(mcp=mcp):
                result, requests, _ = await self.exercise([sse([tool_event(), event(finish="tool_calls")]), final("設定に失敗しました。")], mcp=mcp)
                outcome = json.loads(requests[1][1]["messages"][-1]["content"])
                self.assertTrue(outcome["isError"])
                self.assertFalse(outcome["device_confirmed"])
                self.assertEqual(result[-1]["stats"]["tool_calls_failed"], 1)

    async def test_duration_boundaries_default_and_text_only_mcp_result_are_valid(self):
        for duration in (0.5, 5.0, None):
            with self.subTest(duration=duration):
                mcp = FakeMCP(result=types.CallToolResult(content=[types.TextContent(
                    type="text", text=json.dumps(device_result()))]))
                arguments = {} if duration is None else {"duration_seconds": duration}
                result, _, _ = await self.exercise([sse([tool_event(arguments=json.dumps(arguments)),
                                                        event(finish="tool_calls")]), final()], mcp=mcp)
                self.assertEqual(mcp.calls[0][1], {"duration_seconds": 5.0 if duration is None else duration})
                self.assertEqual(result[-1]["stats"]["tool_calls_failed"], 0)

    async def test_explicit_current_duration_overrides_model_default_and_different_values(self):
        cases = (("0.5秒トントンして", {}, 0.5),
                 ("1秒トントンして", {"duration_seconds": 5}, 1.0),
                 ("しっぽをトントン三秒だけしてください", {}, 3.0),
                 ("四秒トントンして", {"duration_seconds": 0.5}, 4.0),
                 ("please tap my tail for 1.5 seconds", {}, 1.5),
                 ("トントンして", {}, 5.0))
        for current, arguments, expected in cases:
            with self.subTest(current=current, arguments=arguments):
                result, _, mcp = await self.exercise([
                    sse([tool_event(arguments=json.dumps(arguments)), event(finish="tool_calls")]), final()],
                    options={"current_user_text": current})
                self.assertEqual(mcp.calls, [("tap_tail", {"duration_seconds": expected})])
                self.assertEqual(result[-1]["stats"]["tool_calls_failed"], 0)

    async def test_out_of_bounds_or_ambiguous_current_duration_never_starts(self):
        for current in ("20秒トントンして", "0.1秒トントンして", "0秒トントンして",
                        "1秒トントン2秒して", "please tap my tail for 20 seconds"):
            with self.subTest(current=current):
                result, _, mcp = await self.exercise([
                    sse([tool_event(arguments="{}"), event(finish="tool_calls")]), final()],
                    options={"current_user_text": current})
                self.assertEqual(mcp.calls, [])
                self.assertEqual(result[-1]["stats"]["tool_calls_failed"], 1)

    async def test_explicit_duration_does_not_bypass_extra_model_argument_rejection(self):
        for arguments in ({"duration_seconds": 5, "rate_hz": 0.3},
                          {"duration_seconds": 5, "url": "http://localhost"}):
            result, _, mcp = await self.exercise([
                sse([tool_event(arguments=json.dumps(arguments)), event(finish="tool_calls")]), final()],
                options={"current_user_text": "1秒トントンして"})
            self.assertEqual(mcp.calls, [])
            self.assertEqual(result[-1]["stats"]["tool_calls_failed"], 1)

    async def test_short_aliases_and_everyday_direct_requests_select_and_execute_one_tap(self):
        phrases = ("トントン", "とんとん", "こんこん", "コンコン", "ﾄﾝﾄﾝ", "トンとんして。",
                   "こんこんしてみて。", "とんとんしてよ。", "コンコンしてくださいね。",
                   "こんこんしてくれませんか？", "こんこんお願い", "こんこんをお願いします。",
                   "軽く叩いて。", "軽くたたいてみて。", "しっぽで優しく叩いてください。",
                   "ねえ、こんこんして。", "ねんぎつねちゃん、トントンしてくれる？",
                   "お願い、軽くこんこんして。")
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertEqual(tail.tail_request_intent(phrase), "tap")
                result, requests, mcp = await self.exercise([
                    sse([tool_event(arguments="{}"), event(finish="tool_calls")]), final()],
                    options={"current_user_text": phrase, "user_input": phrase})
                self.assertEqual(mcp.calls, [("tap_tail", {"duration_seconds": 5.0})])
                self.assertEqual(requests[0][1]["tool_choice"], "required")
                self.assertEqual([item["function"]["name"] for item in requests[0][1]["tools"]], ["tap_tail"])
                self.assertEqual(requests[1][1]["tool_choice"], "auto")
                self.assertFalse(result[-1]["stats"]["tail_command_unfulfilled"])

    async def test_new_aliases_keep_negation_history_quotation_indefinite_and_compound_out(self):
        for phrase in ("こんこんしないで", "コンコンしなくていい", "昨日こんこんしてもらった",
                       "『こんこん』", "こんこんって言った", "とんとんとは", "こんこんする方法は？",
                       "ずっとこんこんして", "こんこんし続けて", "こんこんしてからトントンして",
                       "こんこんして、説明もして", "こんこんを", "寂しいです", "悲しいから", "肩を叩いて"):
            with self.subTest(phrase=phrase):
                self.assertIsNone(tail.tail_request_intent(phrase))
                result, requests, mcp = await self.exercise([
                    sse([tool_event(), event(finish="tool_calls")]), final()],
                    options={"current_user_text": phrase, "user_input": phrase})
                self.assertEqual(mcp.calls, [])
                self.assertEqual(requests[0][1]["tool_choice"], "auto")
                self.assertEqual(result[-1]["stats"]["tool_calls_failed"], 1)
        for phrase in ("コンコンを止めてね。", "止めてお願い", "止めてほしい", "しっぽを止めてください"):
            with self.subTest(stop=phrase):
                self.assertEqual(tail.tail_request_intent(phrase), "stop")

    async def test_bare_alias_ignored_required_tool_never_speaks_an_unverified_success(self):
        result, requests, mcp = await self.exercise([final("わかった、今からこんこんするね。")],
                                                   options={"current_user_text": "こんこん", "user_input": "こんこん"})
        self.assertEqual(mcp.calls, [])
        self.assertEqual(requests[0][1]["tool_choice"], "required")
        text = "".join(item.get("text", "") for item in result)
        self.assertNotIn("今からこんこんするね", text)
        self.assertIn("受付を確認できませんでした", text)
        self.assertTrue(result[-1]["stats"]["tail_command_unfulfilled"])

    async def test_rejected_start_never_speaks_the_models_unverified_success(self):
        mcp = FakeMCP(result=types.CallToolResult(is_error=True, content=[types.TextContent(type="text", text="rejected")]))
        result, _, _ = await self.exercise([
            sse([tool_event(), event(finish="tool_calls")]), final("トントンできたよ。")], mcp=mcp)
        text = "".join(item.get("text", "") for item in result)
        self.assertNotIn("トントンできたよ", text)
        self.assertIn("受付を確認できませんでした", text)
        self.assertTrue(result[-1]["stats"]["tail_command_unfulfilled"])

    async def test_command_logs_include_only_intent_tool_and_result(self):
        phrase = "ねえ、こんこんしてみて。"
        with self.assertLogs("nen_gitsune.tail_tools", level="INFO") as logs:
            await self.exercise([sse([tool_event(), event(finish="tool_calls")]), final()],
                                options={"current_user_text": phrase, "user_input": phrase})
        lines = "\n".join(logs.output)
        self.assertIn("intent=tap", lines)
        self.assertIn("tool=tap_tail result=accepted", lines)
        self.assertIn("attempted=1 failed=0", lines)
        self.assertNotIn(phrase, lines)

    async def test_negated_quoted_historical_and_missing_raw_request_never_authorize_start(self):
        for current in ("", "こんにちは", "トントンしないで", "トントンしてと言われた", "『トントンして』",
                        "昨日はトントンしてもらった", "トントンする方法は？", "トントンして、説明もして"):
            with self.subTest(current=current):
                result, requests, mcp = await self.exercise([
                    sse([tool_event(), event(finish="tool_calls")]), final("開始していません。")], options={
                        "current_user_text": current,
                        "user_input": "履歴のユーザー発話: トントンして\n今回のユーザー発話（質問・依頼）:\n" + current,
                    })
                self.assertEqual(mcp.calls, [])
                self.assertEqual(result[-1]["tool_calls"], ())
                self.assertTrue(json.loads(requests[-1][1]["messages"][-1]["content"])["isError"])

    async def test_missing_or_stale_session_lease_rejects_even_a_direct_request(self):
        for lease in (None, FakeLease(stale=True)):
            with self.subTest(lease=lease):
                result, _, mcp = await self.exercise([
                    sse([tool_event(), event(finish="tool_calls")]), final("開始できませんでした。")], lease=lease)
                self.assertEqual(mcp.calls, [])
                self.assertEqual(result[-1]["stats"]["tool_calls_failed"], 1)

    async def test_same_turn_repeated_start_is_not_executed_twice(self):
        result, _, mcp = await self.exercise([
            sse([tool_event(identifier="first"), event(finish="tool_calls")]),
            sse([tool_event(identifier="second"), event(finish="tool_calls")]), final()])
        self.assertEqual(mcp.calls, [("tap_tail", {"duration_seconds": 5.0})])
        self.assertEqual(result[-1]["stats"]["tool_calls_failed"], 1)

    async def test_explicit_stop_runs_before_model_even_if_model_times_out(self):
        lease, mcp = FakeLease(), FakeMCP()
        with self.assertRaises(VoiceStageTimeout):
            await self.exercise([httpx.ReadTimeout("LLM is unavailable")], lease=lease, mcp=mcp,
                                options={"current_user_text": "止めて"})
        self.assertEqual(lease.stops, 1)
        self.assertEqual(mcp.calls, [])
        self.assertTrue(mcp.closed)

    async def test_model_stop_reuses_independent_stop_instead_of_calling_stdio(self):
        lease = FakeLease()
        result, requests, mcp = await self.exercise([
            sse([tool_event("stop_tail", "{}"), event(finish="tool_calls")]), final("停止指令を受け付けました。")],
            lease=lease, options={"current_user_text": "しっぽを止めて"})
        self.assertEqual(lease.stops, 1)
        self.assertEqual(mcp.calls, [])
        self.assertEqual(result[-1]["tool_calls"], ("stop_tail",))
        outcome = json.loads(requests[-1][1]["messages"][-1]["content"])
        self.assertFalse(outcome["isError"])
        self.assertEqual(outcome["result"]["status"], "command_stopped")

    async def test_stop_confirmation_requires_valid_fence(self):
        for changes in ({"stop_revision": None}, {"device_epoch": None}, {"stop_revision": True}, {"feedback": "physical"}):
            self.assertTrue(tail._device_result({**stop_confirmation(), **changes}, "stop_tail")["isError"])
        partial = tail._device_result({**stop_confirmation(), "status": "unconfirmed"}, "stop_tail")
        self.assertTrue(partial["isError"])
        self.assertEqual(partial["result"]["status"], "unconfirmed")

    async def test_status_requires_boolean_readiness_and_command_only_feedback(self):
        for bad in (device_result("get_tail_status", start_enabled="yes"),
                    device_result("get_tail_status", feedback="physical"),
                    device_result("get_tail_status", device_confirmed=True)):
            mcp = FakeMCP(result=types.CallToolResult(content=[], structured_content=bad))
            result, _, _ = await self.exercise([
                sse([tool_event("get_tail_status", "{}"), event(finish="tool_calls")]), final()], mcp=mcp)
            self.assertEqual(result[-1]["stats"]["tool_calls_failed"], 1)

    async def test_four_round_limit_forces_a_non_tool_final_round(self):
        rounds = [sse([tool_event("get_tail_status", "{}", f"call-{index}"), event(finish="tool_calls")]) for index in range(3)]
        result, requests, mcp = await self.exercise(rounds + [final()])
        self.assertEqual(len(requests), 4)
        self.assertEqual(len(mcp.calls), 3)
        self.assertEqual(requests[-1][1]["tool_choice"], "none")
        self.assertEqual(result[-1]["stats"]["rounds"], 4)

    async def test_eight_call_limit_prevents_additional_execution(self):
        delta = {"tool_calls": [{"index": index, "id": f"call-{index}", "type": "function", "function": {"name": "get_tail_status", "arguments": "{}"}} for index in range(10)]}
        result, requests, mcp = await self.exercise([sse([event(delta), event(finish="tool_calls")]), final()])
        self.assertEqual(len(mcp.calls), 8)
        self.assertEqual(requests[1][1]["tool_choice"], "none")
        self.assertEqual(result[-1]["stats"]["tool_calls_failed"], 2)

    async def test_reasoning_truncation_and_marker_without_calls_fail_and_close(self):
        responses = [
            sse([event({"reasoning_content": "private reasoning"}), event(finish="stop")]),
            sse([event({"content": "本文"})], done=False),
            sse([event({"content": "toolなし"}), event(finish="tool_calls")]),
            sse([event({"content": "本文"}), event(finish="stop", usage={"completion_tokens_details": {"reasoning_tokens": 1}})]),
            sse([event({"tool_calls": [{"index": 0, "id": "id", "type": "custom", "function": {"name": "get_tail_status", "arguments": "{}"}}]}), event(finish="tool_calls")]),
        ]
        for response in responses:
            mcp = FakeMCP()
            with self.subTest(response=response), self.assertRaises(RuntimeError):
                await self.exercise([response], mcp=mcp)
            self.assertTrue(mcp.closed)
            self.assertEqual(mcp.calls, [])

    async def test_timeout_maps_to_voice_timeout_and_closes_context(self):
        mcp = FakeMCP()
        with self.assertRaises(VoiceStageTimeout):
            await self.exercise([httpx.ReadTimeout("timed out")], mcp=mcp)
        self.assertTrue(mcp.closed)

    async def test_missing_tools_fail_before_model_request(self):
        mcp = FakeMCP()
        original = mcp.list_tools
        async def missing(**kwargs):
            result = await original(**kwargs)
            result.tools = [tool for tool in result.tools if tool.name != "tap_tail"]
            return result
        mcp.list_tools = missing
        with self.assertRaisesRegex(RuntimeError, "three ESP32 tail tools"):
            await self.exercise([], mcp=mcp)
        self.assertTrue(mcp.closed)

    async def test_real_mcp_listing_lifecycle_with_mocked_model_and_no_device_calls(self):
        requests = []
        def handle(request):
            requests.append(json.loads(request.content))
            return final("こんばんは。")
        async with httpx.AsyncClient(base_url="http://model.test", transport=httpx.MockTransport(handle)) as client:
            result = [item async for item in tail.stream_tail_chat(client, **self.options(
                user_input="こんばんは", current_user_text="こんばんは", timeout_seconds=15.0))]
        self.assertEqual({item["function"]["name"] for item in requests[0]["tools"]}, tail.ALLOWED_TOOLS)
        self.assertEqual(result[-1]["tool_calls"], ())
        self.assertEqual(result[-1]["stats"]["reasoning_effort"], "none")

    # End-to-end v2 HTTP/MCP tests live in test_tail_v2.py.


if __name__ == "__main__":
    unittest.main()
