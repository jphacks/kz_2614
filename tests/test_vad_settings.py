"""Persisted controls reach new PCM streams without resetting ongoing input."""

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from Programs.ServerSide.dashboard.settings_store import (
    DEFAULT_SETTINGS, get_dashboard_settings, get_settings_defaults,
    save_settings, validate_settings,
)
from Programs.ServerSide.STTLLMTTS.voice.pcm_stream import ServerAudioStreams
from Programs.ServerSide.STTLLMTTS.voice.server_vad import (
    FRAME_BYTES, ServerVADSettings, ServerVoiceActivity,
)


class VADSettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "settings.json"
        environment = patch.dict(os.environ, {"NENGITSUNE_SETTINGS_PATH": str(self.path)}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def test_save_roundtrip_overrides_environment_for_new_streams(self):
        with patch.dict(os.environ, {"VOICE_VAD_SILENCE_MS": "1500", "VOICE_VAD_THRESHOLD": "0.8"}):
            self.assertEqual(ServerVADSettings.from_env().silence_ms, 1500)
            payload = get_dashboard_settings()
            payload["vad"].update(threshold=0.5, end_threshold=0.25, silence_ms=640)
            result = save_settings(payload)
            self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["vad"], result["vad"])
            active = ServerVADSettings.from_env()
            self.assertEqual(active.silence_ms, 640)
            self.assertEqual(active.threshold, 0.5)

    def test_legacy_file_uses_vad_environment_until_first_explicit_save(self):
        legacy = copy.deepcopy(DEFAULT_SETTINGS)
        del legacy["vad"]
        self.path.write_text(json.dumps(legacy), encoding="utf-8")
        with patch.dict(os.environ, {"VOICE_VAD_SILENCE_MS": "1600"}):
            self.assertEqual(get_dashboard_settings()["vad"]["silence_ms"], 1600)
            self.assertEqual(ServerVADSettings.from_env().silence_ms, 1600)

    def test_legacy_client_save_keeps_existing_vad_values(self):
        settings = get_dashboard_settings()
        settings["vad"]["silence_ms"] = 1728
        save_settings(settings)
        del settings["vad"]
        with patch.dict(os.environ, {"VOICE_VAD_SILENCE_MS": "320"}):
            saved = save_settings(settings)
        self.assertEqual(saved["vad"]["silence_ms"], 1728)
        self.assertEqual(ServerVADSettings.from_env().silence_ms, 1728)

    def test_invalid_types_nonfinite_bounds_and_relations_are_rejected(self):
        cases = [
            {"threshold": True}, {"threshold": "0.5"}, {"threshold": float("nan")},
            {"threshold": float("inf")}, {"threshold": 10 ** 1000},
            {"end_threshold": 0.7}, {"threshold": 1}, {"end_threshold": 0},
            {"silence_ms": 319}, {"silence_ms": 2001}, {"silence_ms": 896.5},
            {"min_speech_ms": False}, {"min_speech_ms": 1001},
            {"preroll_ms": 511}, {"min_speech_ms": 640, "preroll_ms": 640},
            {"max_speech_seconds": 0}, {"max_speech_seconds": 31},
        ]
        for invalid in cases:
            with self.subTest(invalid=invalid):
                payload = copy.deepcopy(DEFAULT_SETTINGS)
                payload["vad"].update(invalid)
                with self.assertRaises(ValueError):
                    save_settings(payload)
                self.assertFalse(self.path.exists())

    def test_unknown_vad_fields_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_settings({"vad": {"browser_vad": True}})

    def test_backend_aware_tts_defaults_match_local_configuration(self):
        with patch.dict(os.environ, {"VOICE_TTS_BACKEND": "local"}):
            self.assertEqual(get_settings_defaults()["tts"]["checkpoint"], "Aratako/Irodori-TTS-v4.1-Small")
            self.assertEqual(get_dashboard_settings()["tts"]["checkpoint"], "Aratako/Irodori-TTS-v4.1-Small")
        self.assertEqual(get_settings_defaults()["tts"]["checkpoint"], DEFAULT_SETTINGS["tts"]["checkpoint"])

    def test_existing_input_keeps_its_vad_and_snapshot_does_not_reset_it(self):
        model = Mock()
        model.probability.return_value = 0.9
        controller = ServerAudioStreams(Mock(), vad_factory=lambda: ServerVoiceActivity(model=model))
        self.addCleanup(controller.close)
        controller.get_status("existing", 0)
        first = controller._states["existing"].vad
        first.feed(bytes(FRAME_BYTES) * 12)
        self.assertTrue(first.speaking)
        buffered = first.buffered_bytes
        settings = get_dashboard_settings()
        settings["vad"].update(silence_ms=1600, threshold=0.8, end_threshold=0.5)
        save_settings(settings)
        controller.get_status("new", 0)
        second = controller._states["new"].vad
        self.assertEqual(first.settings.silence_ms, 896)
        self.assertEqual(second.settings.silence_ms, 1600)
        before_reset_count = model.reset.call_count
        rows = controller.runtime_snapshot({"existing"})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["settings"]["silence_ms"], 896)
        self.assertTrue(first.speaking)
        self.assertEqual(first.buffered_bytes, buffered)
        self.assertEqual(model.reset.call_count, before_reset_count)

    def test_first_pcm_packet_uses_settings_captured_when_conversation_started(self):
        original = get_dashboard_settings()["vad"]
        manager = Mock()
        manager.get_active_session.return_value = SimpleNamespace(
            pipeline=SimpleNamespace(vad_settings=original),
        )
        controller = ServerAudioStreams(manager)
        self.addCleanup(controller.close)
        changed = get_dashboard_settings()
        changed["vad"]["silence_ms"] = 1920
        save_settings(changed)
        controller.get_status("started-before-save", 0)
        self.assertEqual(controller.runtime_snapshot()[0]["settings"]["silence_ms"], 896)


if __name__ == "__main__":
    unittest.main()
