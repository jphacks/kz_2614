"""Cooperative TTS cancellation with fake native gates; no model is loaded."""

from __future__ import annotations

import asyncio
import tempfile
import threading
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from server.ai.runtime import VoiceStageTimeout
from server.ai.tts.cancellation import SynthesisCancelled
from server.ai.tts.irodori import IrodoriTTS, TTSConfig
from server.ai.tts.local_backend import LocalIrodoriBackend


WAV = b"RIFF\x00\x00\x00\x00WAVE\x00\x00"


class ObservedLock:
    def __init__(self):
        self.inner = threading.Lock()
        self.waited = threading.Event()

    def acquire(self, blocking=True, timeout=-1):
        if self.inner.locked():
            self.waited.set()
        return self.inner.acquire(blocking, timeout)

    def release(self):
        self.inner.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()


class TTSCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = TTSConfig(
            backend="local", model_device="cpu", codec_device="cpu",
            model_precision="fp32", codec_precision="fp32", timeout_seconds=2,
        )
        self.lock = ObservedLock()
        self.native_started = threading.Event()
        self.native_release = threading.Event()
        self.native_calls = []
        self.reference_paths = {}
        self.created_paths = []
        self.finished = {name: threading.Event() for name in ("owner", "stale", "latest")}
        self.cancel_events = {}
        self.runtime = types.SimpleNamespace(
            key=types.SimpleNamespace(model_device="cpu", codec_device="cpu"),
            synthesize=Mock(side_effect=self.native),
        )
        self.api = types.SimpleNamespace(SamplingRequest=lambda **kwargs: types.SimpleNamespace(**kwargs))
        self.encoder = Mock(return_value=WAV)
        original_temporary = tempfile.NamedTemporaryFile

        def temporary(*args, **kwargs):
            result = original_temporary(*args, **kwargs)
            self.created_paths.append(Path(result.name))
            return result

        self.patchers = [
            patch.object(LocalIrodoriBackend, "_load", return_value=(self.api, self.runtime, self.lock)),
            patch.object(LocalIrodoriBackend, "_encode_wav", self.encoder),
            patch("server.ai.tts.local_backend.tempfile.NamedTemporaryFile", side_effect=temporary),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.tts = self.trace(IrodoriTTS(self.config))
        self.tts._reference_audio_bytes = b"reference snapshot"

    async def asyncTearDown(self):
        self.native_release.set()
        for name in tuple(self.cancel_events):
            await self.wait_event(self.finished[name])
        self.assertTrue(all(not path.exists() for path in self.created_paths))

    def trace(self, tts):
        original = tts.synthesize

        def synthesize(text, *, cancel_event=None):
            self.cancel_events[text] = cancel_event
            try:
                return original(text, cancel_event=cancel_event)
            finally:
                self.finished[text].set()

        tts.synthesize = synthesize
        return tts

    async def wait_event(self, event, timeout=1):
        deadline = asyncio.get_running_loop().time() + timeout
        while not event.is_set():
            if asyncio.get_running_loop().time() >= deadline:
                self.fail("fake worker did not reach its expected state")
            await asyncio.sleep(0.005)

    def native(self, request):
        self.native_calls.append(request.text)
        path = Path(request.ref_wav)
        self.reference_paths[request.text] = path
        self.assertEqual(path.read_bytes(), b"reference snapshot")
        if request.text == "owner":
            self.native_started.set()
            if not self.native_release.wait(timeout=2):
                raise RuntimeError("fake native gate was not released")
            # Canceling its async caller must not delete an in-use native input.
            self.assertTrue(path.exists())
        return types.SimpleNamespace(audio=object(), sample_rate=48000)

    async def test_cancelled_waiter_exits_before_owner_and_latest_job_uses_slot(self):
        owner = asyncio.create_task(self.tts.synthesize_async("owner"))
        try:
            await self.wait_event(self.native_started)
            stale = asyncio.create_task(self.tts.synthesize_async("stale"))
            await self.wait_event(self.lock.waited)
            stale.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await stale
            await self.wait_event(self.finished["stale"])
            self.assertTrue(self.cancel_events["stale"].is_set())
            self.assertEqual(self.native_calls, ["owner"])
            self.assertFalse(self.finished["owner"].is_set())
            self.assertEqual(sum(path.exists() for path in self.created_paths), 1)
            latest = asyncio.create_task(self.tts.synthesize_async("latest"))
            self.native_release.set()
            results = await asyncio.gather(owner, latest)
            self.assertEqual([result.audio_bytes for result in results], [WAV, WAV])
            self.assertEqual(self.native_calls, ["owner", "latest"])
        finally:
            self.native_release.set()
            await asyncio.gather(owner, return_exceptions=True)

    async def test_timed_out_waiter_never_starts_native_after_owner_releases_lock(self):
        tts = self.trace(IrodoriTTS(replace(self.config, timeout_seconds=0.03)))
        tts._reference_audio_bytes = b"reference snapshot"
        self.lock.acquire()
        try:
            with self.assertRaises(VoiceStageTimeout):
                await tts.synthesize_async("stale")
            await self.wait_event(self.finished["stale"])
            self.assertTrue(self.cancel_events["stale"].is_set())
            self.runtime.synthesize.assert_not_called()
        finally:
            self.lock.release()
        await self.tts.synthesize_async("latest")
        self.assertEqual(self.native_calls, ["latest"])

    async def test_running_native_retains_lock_and_files_then_discards_cancelled_result(self):
        owner = asyncio.create_task(self.tts.synthesize_async("owner"))
        try:
            await self.wait_event(self.native_started)
            owner.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await owner
            self.assertFalse(self.finished["owner"].is_set())
            self.assertTrue(self.reference_paths["owner"].exists())
            latest = asyncio.create_task(self.tts.synthesize_async("latest"))
            await self.wait_event(self.lock.waited)
            self.assertEqual(self.native_calls, ["owner"])
            self.native_release.set()
            self.assertEqual((await latest).audio_bytes, WAV)
            await self.wait_event(self.finished["owner"])
            self.assertEqual(self.native_calls, ["owner", "latest"])
            # Only the latest result was encoded; native owner was allowed to finish.
            self.assertEqual(self.encoder.call_count, 1)
        finally:
            self.native_release.set()
            await asyncio.gather(owner, return_exceptions=True)

    async def test_cancelled_gradio_waiter_skips_predict_and_keeps_client(self):
        tts = self.trace(IrodoriTTS(replace(self.config, backend="gradio")))
        client = types.SimpleNamespace(predict=Mock(return_value=WAV))
        tts._client = client
        tts._synthesis_lock = self.lock
        self.lock.acquire()
        try:
            stale = asyncio.create_task(tts.synthesize_async("stale"))
            await self.wait_event(self.lock.waited)
            stale.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await stale
            await self.wait_event(self.finished["stale"])
            client.predict.assert_not_called()
            self.assertIs(tts._client, client)
        finally:
            self.lock.release()
        self.assertEqual((await tts.synthesize_async("latest")).audio_bytes, WAV)
        self.assertEqual(client.predict.call_count, 1)

    async def test_running_gradio_worker_removes_download_after_cancel(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "generated.wav"

            def predict(**kwargs):
                self.native_started.set()
                if not self.native_release.wait(timeout=2):
                    raise RuntimeError("fake native gate was not released")
                output.write_bytes(WAV)
                return str(output)

            tts = self.trace(IrodoriTTS(replace(self.config, backend="gradio")))
            tts._client = types.SimpleNamespace(predict=Mock(side_effect=predict))
            owner = asyncio.create_task(tts.synthesize_async("owner"))
            try:
                await self.wait_event(self.native_started)
                owner.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await owner
                self.assertFalse(self.finished["owner"].is_set())
                self.native_release.set()
                await self.wait_event(self.finished["owner"])
                self.assertFalse(output.exists())
            finally:
                self.native_release.set()
                await asyncio.gather(owner, return_exceptions=True)

    async def test_precancelled_direct_call_does_no_work_and_old_direct_call_still_works(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(SynthesisCancelled):
            self.tts.synthesize("stale", cancel_event=cancelled)
        self.assertEqual(self.created_paths, [])
        self.runtime.synthesize.assert_not_called()
        self.assertEqual(self.tts.synthesize("latest").audio_bytes, WAV)
        self.assertEqual(self.native_calls, ["latest"])

    async def test_cancel_after_reference_preparation_skips_native_and_deletes_latent(self):
        def prepare(backend, api, runtime, request, audio, path):
            with tempfile.NamedTemporaryFile(suffix=".pt", delete=False) as temporary:
                latent = Path(temporary.name)
            self.cancel_events["stale"].set()
            return latent

        with patch.object(LocalIrodoriBackend, "_cached_reference_path", prepare):
            with self.assertRaises(SynthesisCancelled):
                self.tts.synthesize("stale", cancel_event=threading.Event())
        self.runtime.synthesize.assert_not_called()
        self.assertTrue(any(path.suffix == ".pt" for path in self.created_paths))
        self.assertTrue(all(not path.exists() for path in self.created_paths))


if __name__ == "__main__":
    unittest.main()
