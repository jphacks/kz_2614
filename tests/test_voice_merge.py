"""Incoming NDJSON output and local progress/PCM session interfaces coexist."""

from __future__ import annotations

import asyncio
import copy
import io
import threading
import unittest
import uuid
import wave
from concurrent.futures import CancelledError as FutureCancelledError
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from Programs.ServerSide.dashboard.settings_store import DEFAULT_SETTINGS
from Programs.ServerSide.STTLLMTTS.llm.lm_studio import VoiceLLMReply
from Programs.ServerSide.STTLLMTTS.stt.whisper import Transcription
from Programs.ServerSide.STTLLMTTS.tts.irodori import SynthesizedSpeech
from Programs.ServerSide.STTLLMTTS.voice.pipeline import VoicePipeline
from Programs.ServerSide.STTLLMTTS.voice.sessions import VoiceAPIError, VoiceSessions


def make_pipeline(deltas, *, pause_after_first=None):
    async def stream(*args, **kwargs):
        for index, delta in enumerate(deltas):
            yield delta
            if index == 0 and pause_after_first is not None:
                while not pause_after_first.is_set():
                    await asyncio.sleep(0.005)

    async def transcribe(path, *, on_worker_finished=None):
        try:
            return Transcription("今回の発話", "ja", 0.99, 1.2,
                                 vocal_analysis={"pitch": "中くらい", "delivery": "穏やか"})
        finally:
            if on_worker_finished is not None:
                on_worker_finished()

    stt = SimpleNamespace(transcribe_file_async=AsyncMock(side_effect=transcribe))
    llm = SimpleNamespace(
        config=SimpleNamespace(max_history_messages=8),
        generate_response=AsyncMock(return_value=VoiceLLMReply("返答です。", (), {})),
        stream_response=stream, close=AsyncMock(), last_tool_calls=(), last_stats={},
    )
    audio = io.BytesIO()
    with wave.open(audio, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(48_000)
        writer.writeframes(bytes(960))
    tts = SimpleNamespace(synthesize_async=AsyncMock(return_value=SynthesizedSpeech(
        audio.getvalue(), "audio/wav", ".wav",
    )))
    acknowledgements = Mock()
    acknowledgements.ready = True
    acknowledgements.delay_seconds = 0.01
    acknowledgements.is_goodnight_or_stop_request.return_value = False
    acknowledgements.prepare = AsyncMock()
    with patch(
        "Programs.ServerSide.STTLLMTTS.voice.pipeline.get_dashboard_settings",
        return_value=copy.deepcopy(DEFAULT_SETTINGS),
    ):
        return VoicePipeline(stt=stt, llm=llm, tts=tts, acknowledgements=acknowledgements)


class PipelineMergeTests(unittest.IsolatedAsyncioTestCase):
    async def test_nonstream_progress_and_incoming_vocal_context_are_both_preserved(self):
        pipeline = make_pipeline([])
        progress = []
        result = await pipeline.process_audio_file(
            Path("recording.wav"), on_progress=lambda *event: progress.append(event),
        )
        self.assertEqual([event[0] for event in progress], ["stt", "llm", "tts"])
        self.assertEqual(progress[1][1], "今回の発話")
        self.assertEqual(pipeline.llm.generate_response.call_args.kwargs["speech_context"], {
            "pitch": "中くらい", "delivery": "穏やか",
        })
        self.assertEqual(result.response.text, "返答です。")
        await pipeline.close()

    async def test_stream_keeps_text_deltas_phrase_audio_quality_and_progress(self):
        pipeline = make_pipeline(["最初の返答。", "続きです。"])
        events, progress = [], []
        result = await pipeline.process_audio_file_stream(
            Path("recording.wav"), events.append,
            on_progress=lambda *event: progress.append(event),
        )
        self.assertEqual(events[0]["type"], "transcript")
        self.assertIn("avg_logprob", events[0]["quality"])
        self.assertEqual("".join(event["text"] for event in events if event["type"] == "text_delta"), "最初の返答。続きです。")
        self.assertEqual([event["index"] for event in events if event["type"] == "audio_chunk"], [0, 1])
        self.assertEqual(result.response.text, "最初の返答。続きです。")
        self.assertEqual([event[0] for event in progress], ["stt", "llm", "tts", "tts"])
        self.assertEqual(len(pipeline._history), 2)
        await pipeline.close()

    async def test_empty_stream_fails_without_waiting_for_an_acknowledgement(self):
        pipeline = make_pipeline([])
        pipeline.acknowledgements.ready = False

        async def never_ready():
            await asyncio.Event().wait()

        pipeline.acknowledgements.wait_until_ready = never_ready
        with self.assertRaisesRegex(RuntimeError, "no spoken response"):
            await asyncio.wait_for(pipeline.process_audio_file_stream(Path("recording.wav"), lambda event: None), timeout=0.3)
        await pipeline.close()

    async def test_plain_session_creation_does_not_prewarm_tts(self):
        manager = object.__new__(VoiceSessions)
        pipeline = Mock()
        with patch("Programs.ServerSide.STTLLMTTS.voice.sessions.VoicePipeline", return_value=pipeline):
            self.assertIs(await manager._create_pipeline(), pipeline)
        pipeline.start_acknowledgement_preparation.assert_not_called()


class StreamSessionMergeTests(unittest.TestCase):
    def setUp(self):
        self.release = threading.Event()
        self.manager = VoiceSessions()

    def tearDown(self):
        self.release.set()
        with self.manager._lock:
            for session in tuple(self.manager._sessions.values()):
                self.manager._retire_locked(session)
        self.manager._loop.call_soon_threadsafe(self.manager._loop.stop)
        self.manager._loop_thread.join(timeout=3)
        self.assertFalse(self.manager._loop_thread.is_alive())

    def start(self, *, pause=False):
        pipeline = make_pipeline(["最初の返答。", "続きです。"], pause_after_first=self.release if pause else None)
        with patch.object(self.manager, "_create_pipeline", AsyncMock(return_value=pipeline)):
            created = self.manager.create_session()
        turn_id = str(uuid.uuid4())
        session, generation = self.manager.begin_turn(created["session_id"], turn_id)
        events = Queue()
        future = self.manager.stream_turn(session, generation, turn_id, Path("recording.wav"), events)
        return session, generation, turn_id, events, future

    def test_ndjson_ready_events_complete_and_shared_progress_interface(self):
        session, generation, turn_id, queue, future = self.start()
        future.result(timeout=3)
        events = []
        while (event := queue.get(timeout=3)) is not None:
            events.append(event)
        self.assertEqual(events[-1]["type"], "complete")
        audio = [event for event in events if event["type"] == "audio_chunk"]
        self.assertEqual(len(audio), 2)
        self.assertTrue(all(event["audio_base64"] and "speech" not in event for event in audio))
        self.assertTrue(all(event["session_id"] == session.session_id and event["generation"] == generation for event in events))
        self.assertEqual(self.manager.get_turn_status(session.session_id, turn_id)["stage"], "complete")
        self.assertIs(self.manager.get_active_session(session.session_id, generation), session)
        self.assertTrue(events[-1]["speech"]["url"].endswith("/audio"))
        fallback, media_type = self.manager.get_speech(session.session_id, turn_id)
        with wave.open(io.BytesIO(fallback)) as audio:
            self.assertEqual(audio.getnframes(), 960)
        self.assertEqual(media_type, "audio/wav")

    def test_stop_invalidates_stream_generation_and_prevents_later_audio(self):
        session, generation, turn_id, queue, future = self.start(pause=True)
        while True:
            event = queue.get(timeout=3)
            if event["type"] == "audio_chunk":
                break
        self.manager.stop_session(session.session_id)
        self.release.set()
        with self.assertRaises(FutureCancelledError):
            future.result(timeout=3)
        remaining = []
        while (event := queue.get(timeout=3)) is not None:
            remaining.append(event)
        self.assertFalse(any(event["type"] in {"audio_chunk", "complete"} for event in remaining))
        with self.assertRaises(VoiceAPIError) as error:
            self.manager.get_active_session(session.session_id, generation)
        self.assertEqual(error.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
