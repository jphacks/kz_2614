"""Voice client routing and metadata for application-owned MCP tool calls."""

import asyncio
import unittest
from unittest.mock import patch

import httpx

from server.ai.llm import lm_studio
from server.ai.runtime import VoiceStageTimeout


class TailLLMBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.llm = lm_studio.VoiceLLMClient(lm_studio.VoiceLLMConfig(tail_mcp_enabled=True))

    async def asyncTearDown(self):
        await self.llm.close()

    async def test_generate_preserves_current_request_context_and_tool_result_metadata(self):
        received = {}

        async def helper(client, **kwargs):
            received.update(kwargs)
            self.assertIs(client, self.llm.client)
            yield {"type": "text_delta", "text": "動作指令を"}
            yield {"type": "text_delta", "text": "送りました。"}
            yield {"type": "complete", "tool_calls": ("tap_tail",), "stats": {"rounds": 2}}

        with patch.object(lm_studio, "stream_tail_chat", helper), \
                patch.object(self.llm.client, "post", side_effect=AssertionError("native integration must not run")):
            result = await self.llm.generate_response(
                "トントンして", [{"role": "user", "content": "以前の発話"}],
                speech_context={"pitch": "低め", "delivery": "ゆっくり"},
            )
        self.assertEqual(result.text, "動作指令を送りました。")
        self.assertEqual(result.tool_calls, ("tap_tail",))
        self.assertEqual(result.stats["rounds"], 2)
        self.assertEqual(result.stats["reasoning"], "off")
        self.assertIn("今回のユーザー発話（質問・依頼）:\nトントンして", received["user_input"])
        self.assertEqual(received["current_user_text"], "トントンして")
        self.assertTrue(received["execute_confirmed_tap"])
        self.assertIn("以前の発話", received["user_input"])
        self.assertIn("声の高さ: 低め", received["user_input"])
        self.assertNotIn("integrations", self.llm._build_payload("test"))

    async def test_stream_yields_before_completion_and_replaces_previous_metadata(self):
        self.llm.last_tool_calls = ("stale",)
        self.llm.last_stats = {"stale": True}
        progressed = []

        async def helper(*args, **kwargs):
            yield {"type": "text_delta", "text": "はい。"}
            progressed.append("continued")
            yield {"type": "complete", "tool_calls": (), "stats": {"rounds": 1}}

        with patch.object(lm_studio, "stream_tail_chat", helper):
            stream = self.llm.stream_response("こんばんは")
            self.assertEqual(await anext(stream), "はい。")
            self.assertEqual(progressed, [])
            self.assertEqual([part async for part in stream], [])
        self.assertEqual(self.llm.last_tool_calls, ())
        self.assertNotIn("stale", self.llm.last_stats)

    async def test_timeout_and_missing_spoken_response_are_failures(self):
        async def timeout(*args, **kwargs):
            raise TimeoutError("MCP or model deadline")
            yield

        async def empty(*args, **kwargs):
            yield {"type": "complete", "tool_calls": (), "stats": {}}

        with patch.object(lm_studio, "stream_tail_chat", timeout):
            with self.assertRaises(VoiceStageTimeout):
                await self.llm.generate_response("test")
        with patch.object(lm_studio, "stream_tail_chat", empty):
            with self.assertRaisesRegex(RuntimeError, "no spoken response"):
                await self.llm.generate_response("test")

    async def test_closing_outer_voice_stream_closes_mcp_owner_in_same_task(self):
        owners = []

        async def helper(*args, **kwargs):
            owners.append(asyncio.current_task())
            try:
                yield {"type": "text_delta", "text": "こんばんは。"}
                await asyncio.Event().wait()
            finally:
                owners.append(asyncio.current_task())

        with patch.object(lm_studio, "stream_tail_chat", helper):
            stream = self.llm.stream_response("こんばんは")
            await anext(stream)
            await stream.aclose()
        self.assertEqual(owners, [asyncio.current_task(), asyncio.current_task()])

    async def test_disabled_mcp_keeps_native_api_and_does_not_start_helper(self):
        await self.llm.close()
        self.llm = lm_studio.VoiceLLMClient(lm_studio.VoiceLLMConfig(tail_mcp_enabled=False))
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(200, json={"output": [{"type": "message", "content": "こんばんは。"}]})

        await self.llm.client.aclose()
        self.llm.client = httpx.AsyncClient(base_url="http://localhost", transport=httpx.MockTransport(handle))
        with patch.object(lm_studio, "stream_tail_chat", side_effect=AssertionError("MCP is disabled")):
            self.assertEqual((await self.llm.generate_response("こんばんは")).text, "こんばんは。")
        self.assertEqual(requests[0].url.path, "/api/v1/chat")
        self.assertNotIn("Authorization", requests[0].headers)


if __name__ == "__main__":
    unittest.main()
