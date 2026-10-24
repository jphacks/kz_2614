"""Frequency-only MCP chat loop: SSE fragments, validation, limits and lifecycle."""

from __future__ import annotations

import asyncio
from contextlib import aclosing, asynccontextmanager
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from mcp import types

from server.ai.llm import tail_tools as tail
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


def tool_event(name="set_tail_frequency", arguments='{"rate_hz":0.8}', identifier="call-1", *, index=0):
    return event({"tool_calls": [{"index": index, "id": identifier, "type": "function", "function": {"name": name, "arguments": arguments}}]})


def final(text="PC側の設定を保存しました。実機への反映は未確認です。"):
    return sse([event({"content": text}), event(finish="stop", usage={"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14})])


def configuration(rate=0.8):
    return {"rate_hz": rate, "device_confirmed": False, "applies_to": "configuration"}


class FakeMCP:
    def __init__(self, result=None, error=None):
        self.result = result or types.CallToolResult(content=[], structured_content={"result": configuration()})
        self.error = error
        self.calls = []
        self.owner = None
        self.closed_by = None
        self.closed = False
        self.extra_names = {"tap_tail", "stop_tail", "get_tail_status"}

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
        return self.result


class TailToolChatTests(unittest.IsolatedAsyncioTestCase):
    def options(self, **changes):
        return {"model": "test-model", "user_input": "今回のユーザー発話（質問・依頼）:\nトントンを0.8Hzにして", "system_prompt": "短く返答してください。",
                "max_output_tokens": 160, "context_length": 4096, "timeout_seconds": 5.0, **changes}

    async def exercise(self, responses, *, mcp=None, options=None):
        mcp = mcp or FakeMCP()
        requests = []
        async def handle(request):
            requests.append((request, json.loads(request.content)))
            item = responses[len(requests) - 1]
            if isinstance(item, Exception):
                raise item
            return item
        async with httpx.AsyncClient(base_url="http://model.test", transport=httpx.MockTransport(handle)) as client:
            with patch.object(tail, "_open_tail_session", mcp.open):
                result = [item async for item in tail.stream_tail_chat(client, **self.options(**(options or {})))]
        self.assertTrue(mcp.closed)
        self.assertIs(mcp.owner, mcp.closed_by)
        return result, requests, mcp

    async def test_fragmented_parallel_calls_are_rebuilt_filtered_and_followed_by_results(self):
        first = sse([
            event({"content": "変更しておきます。"}),
            event({"tool_calls": [
                {"index": 1, "id": "read-", "type": "function", "function": {"name": "get_tail_", "arguments": "{"}},
                {"index": 0, "id": "set-", "type": "function", "function": {"name": "set_tail_fre", "arguments": '{"rate_'}},
            ]}),
            event({"tool_calls": [
                {"index": 0, "id": "1", "function": {"name": "quency", "arguments": 'hz":0.8}'}},
                {"index": 1, "id": "1", "function": {"name": "frequency", "arguments": "}"}},
            ]}), event(finish="tool_calls"),
        ])
        result, requests, mcp = await self.exercise([first, final()])
        self.assertEqual(mcp.calls, [("set_tail_frequency", {"rate_hz": 0.8}), ("get_tail_frequency", {})])
        self.assertNotIn("変更しておきます", "".join(item.get("text", "") for item in result))
        body = requests[0][1]
        self.assertEqual({item["function"]["name"] for item in body["tools"]}, tail.ALLOWED_TOOLS)
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
        self.assertEqual(result[-1]["tool_calls"], ("set_tail_frequency", "get_tail_frequency"))
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
                async with aclosing(tail.stream_tail_chat(client, **self.options(user_input="今日は楽しかった"))) as stream:
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
                async with aclosing(tail.stream_tail_chat(client, **self.options(user_input="こんにちは"))) as stream:
                    await anext(stream)
                self.assertTrue(mcp.closed)
                self.assertIs(mcp.owner, mcp.closed_by)

    async def test_disallowed_tools_and_invalid_arguments_become_error_results_without_mcp_calls(self):
        invalid = [
            ("tap_tail", "{}"), ("set_tail_frequency", '{"rate_hz":true}'),
            ("set_tail_frequency", '{"rate_hz":"0.8"}'), ("set_tail_frequency", '{"rate_hz":0.2}'),
            ("set_tail_frequency", '{"rate_hz":1.1}'), ("set_tail_frequency", '{"rate_hz":NaN}'),
            ("set_tail_frequency", '{"rate_hz":Infinity}'), ("set_tail_frequency", '{"rate_hz":0.8,"url":"x"}'),
            ("set_tail_frequency", '{"rate_hz":0.3,"rate_hz":0.8}'), ("set_tail_frequency", "[0.8]"),
            ("set_tail_frequency", '{"rate_hz":'), ("get_tail_frequency", '{"rate_hz":0.8}'),
            ("set_tail_frequency", '{"rate_hz":' + "9" * 4000 + "}"),
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
            FakeMCP(result=types.CallToolResult(content=[], structured_content=configuration(10 ** 1000))),
            FakeMCP(result=types.CallToolResult(content=[], structured_content={**configuration(), "device_confirmed": True})),
            FakeMCP(result=types.CallToolResult(content=[], structured_content=configuration(0.6))),
        ]
        for mcp in cases:
            with self.subTest(mcp=mcp):
                result, requests, _ = await self.exercise([sse([tool_event(), event(finish="tool_calls")]), final("設定に失敗しました。")], mcp=mcp)
                outcome = json.loads(requests[1][1]["messages"][-1]["content"])
                self.assertTrue(outcome["isError"])
                self.assertFalse(outcome["device_confirmed"])
                self.assertEqual(result[-1]["stats"]["tool_calls_failed"], 1)

    async def test_rate_boundaries_and_text_only_mcp_result_are_valid(self):
        for rate in (0.3, 1.0):
            with self.subTest(rate=rate):
                mcp = FakeMCP(result=types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(configuration(rate)))]))
                result, _, _ = await self.exercise([sse([tool_event(arguments=json.dumps({"rate_hz": rate})), event(finish="tool_calls")]), final()], mcp=mcp)
                self.assertEqual(mcp.calls[0][1], {"rate_hz": rate})
                self.assertEqual(result[-1]["stats"]["tool_calls_failed"], 0)

    async def test_four_round_limit_forces_a_non_tool_final_round(self):
        rounds = [sse([tool_event("get_tail_frequency", "{}", f"call-{index}"), event(finish="tool_calls")]) for index in range(3)]
        result, requests, mcp = await self.exercise(rounds + [final()])
        self.assertEqual(len(requests), 4)
        self.assertEqual(len(mcp.calls), 3)
        self.assertEqual(requests[-1][1]["tool_choice"], "none")
        self.assertEqual(result[-1]["stats"]["rounds"], 4)

    async def test_eight_call_limit_prevents_additional_execution(self):
        delta = {"tool_calls": [{"index": index, "id": f"call-{index}", "type": "function", "function": {"name": "get_tail_frequency", "arguments": "{}"}} for index in range(10)]}
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
            sse([event({"tool_calls": [{"index": 0, "id": "id", "type": "custom", "function": {"name": "get_tail_frequency", "arguments": "{}"}}]}), event(finish="tool_calls")]),
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
            result.tools = [tool for tool in result.tools if tool.name != "set_tail_frequency"]
            return result
        mcp.list_tools = missing
        with self.assertRaisesRegex(RuntimeError, "two tail frequency tools"):
            await self.exercise([], mcp=mcp)
        self.assertTrue(mcp.closed)

    async def test_real_stdio_listing_lifecycle_with_mocked_model_and_no_device_calls(self):
        requests = []
        def handle(request):
            requests.append(json.loads(request.content))
            return final("こんばんは。")
        async with httpx.AsyncClient(base_url="http://model.test", transport=httpx.MockTransport(handle)) as client:
            result = [item async for item in tail.stream_tail_chat(client, **self.options(user_input="こんばんは", timeout_seconds=15.0))]
        self.assertEqual({item["function"]["name"] for item in requests[0]["tools"]}, tail.ALLOWED_TOOLS)
        self.assertEqual(result[-1]["tool_calls"], ())
        self.assertEqual(result[-1]["stats"]["reasoning_effort"], "none")


if __name__ == "__main__":
    unittest.main()
