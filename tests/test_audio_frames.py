"""Raw PCM transport framing without altering speech synthesis boundaries."""

import base64
import io
import json
import socket
import struct
import threading
import unittest
import wave
from email.message import Message
from unittest.mock import Mock, patch

from Programs.ServerSide.STTLLMTTS.voice.audio_frames import (
    PCM_STREAM_PROFILE, iter_audio_frames,
)
from Programs.ServerSide.STTLLMTTS.voice.sessions import VoiceAPIError
from Programs.ServerSide.STTLLMTTS.voice.tcp_server import VoiceTCPServer
from Programs.ServerSide.dashboard.server import DashboardHandler


def event(samples=3500, rate=16000, channels=1):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(struct.pack("<h", 2048) * samples * channels)
    return {"type": "audio_chunk", "cursor": 3, "index": 4,
            "session_id": "session", "generation": 0, "turn_id": "turn",
            "media_type": "audio/wav", "text": "合成済みの文節",
            "audio_base64": base64.b64encode(buffer.getvalue()).decode()}


class AudioFramesTests(unittest.TestCase):
    def test_frames_are_pcm_preserve_parent_identity_and_finish_exactly_once(self):
        source = event()
        frames = list(iter_audio_frames(source))
        self.assertEqual([frame["frame_index"] for frame in frames], [0, 1, 2])
        self.assertEqual([frame["sample_offset"] for frame in frames], [0, 1600, 3200])
        self.assertEqual([frame["is_last"] for frame in frames], [False, False, True])
        self.assertEqual([len(base64.b64decode(frame["audio_base64"])) for frame in frames], [3200, 3200, 600])
        self.assertEqual(b"".join(base64.b64decode(frame["audio_base64"]) for frame in frames), struct.pack("<h", 2048) * 3500)
        for frame in frames:
            self.assertEqual((frame["type"], frame["source_type"], frame["cursor"], frame["index"]), ("audio_frame", "audio_chunk", 3, 4))
            self.assertEqual((frame["sample_rate"], frame["channels"], frame["format"]), (16000, 1, "s16le"))
            self.assertEqual((frame["frame_count"], frame["total_samples"]), (3, 3500))
            self.assertNotIn("text", frame)
        self.assertEqual(source["type"], "audio_chunk")
        self.assertEqual(source["media_type"], "audio/wav")

    def test_resample_downmix_and_acknowledgement_use_same_transport(self):
        source = event(samples=7200, rate=24000, channels=2)
        source["type"] = "acknowledgement"
        source.pop("index")
        frames = list(iter_audio_frames(source))
        self.assertEqual(len(frames), 3)
        self.assertEqual(sum(len(base64.b64decode(frame["audio_base64"])) for frame in frames), 9600)
        self.assertTrue(all(frame["source_type"] == "acknowledgement" and frame["index"] == 0 for frame in frames))

    def test_controls_and_interruption_are_forwarded_unchanged(self):
        for kind in ("text_delta", "interruption", "complete", "error", "heartbeat"):
            source = {"type": kind, "cursor": 9}
            self.assertIs(list(iter_audio_frames(source))[0], source)

    def test_invalid_data_and_excessive_pcm_are_explicit_errors(self):
        for changes in ({"audio_base64": "invalid"}, {"media_type": "audio/pcm", "format": "s16le",
                        "sample_rate": 16000, "channels": 1, "audio_base64": "AQ=="}):
            with self.subTest(changes=changes), self.assertRaises(VoiceAPIError) as error:
                list(iter_audio_frames({**event(), **changes}))
            self.assertEqual(error.exception.code, "audio_frame_conversion_failed")
        with patch("Programs.ServerSide.STTLLMTTS.voice.audio_frames.MAX_PCM_BYTES", 100):
            with self.assertRaises(VoiceAPIError) as error:
                list(iter_audio_frames(event()))
            self.assertEqual(error.exception.code, "audio_frame_too_large")


class AudioFrameHTTPTests(unittest.TestCase):
    def handler(self, profile):
        handler = object.__new__(DashboardHandler)
        handler.headers = Message()
        handler.headers["X-Voice-Generation"] = "0"
        if profile is not None:
            handler.headers["X-Voice-Audio-Profile"] = profile
        handler.path = "/api/v1/voice/sessions/session/audio-stream/events?after=0"
        handler.connection = Mock()
        handler._begin_ndjson_response = Mock()
        handler._finish_ndjson_response = Mock()
        handler._write_ndjson_event = Mock()
        handler._send_voice_error = Mock()
        return handler

    def test_negotiated_frames_share_one_durable_cursor_and_legacy_stays_wav(self):
        for profile in (PCM_STREAM_PROFILE, None):
            with self.subTest(profile=profile):
                handler = self.handler(profile)
                streams = Mock()
                source = event()
                streams.read_events.return_value = [source]
                with patch("Programs.ServerSide.dashboard.server._audio_streams", return_value=streams), \
                     patch("Programs.ServerSide.dashboard.server.AUDIO_EVENT_STREAM_SECONDS", 0):
                    handler._stream_audio_events("session")
                sent = [call.args[0] for call in handler._write_ndjson_event.call_args_list]
                self.assertEqual([item["type"] for item in sent], ["audio_frame"] * 3 if profile else ["audio_chunk"])
                self.assertTrue(all(item["cursor"] == 3 for item in sent))
                self.assertEqual(streams.read_events.call_count, 1)
                handler._finish_ndjson_response.assert_called_once()

    def test_bad_profile_fails_before_streaming_begins(self):
        handler = self.handler("unknown")
        handler._stream_audio_events("session")
        self.assertEqual(handler._send_voice_error.call_args.args[0].code, "invalid_audio_profile")
        handler._begin_ndjson_response.assert_not_called()


class AudioFrameTCPTests(unittest.TestCase):
    def test_new_profile_streams_frames_then_interruption_and_keeps_pcm_input(self):
        from test_voice_tcp import send, reply

        sessions, streams = Mock(), Mock()
        sessions.create_session.return_value = {"session_id": "session", "generation": 0}
        delivered = threading.Event()
        def events(*args, **kwargs):
            if not delivered.is_set():
                delivered.set()
                return [event(), {"type": "interruption", "cursor": 4, "turn_id": "turn", "interrupted_turn_id": "turn"}]
            threading.Event().wait(.01)
            return []
        streams.read_events.side_effect = events
        streams.push_audio.return_value = {"next_sequence": 1}
        server = VoiceTCPServer(("127.0.0.1", 0), sessions, streams)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with socket.create_connection(server.server_address, timeout=3) as client:
                send(client, {"type": "start", "version": 1, "audio_profile": PCM_STREAM_PROFILE})
                self.assertEqual(reply(client)["audio_profile"], PCM_STREAM_PROFILE)
                frames = [reply(client) for _ in range(3)]
                self.assertEqual([frame["frame_index"] for frame in frames], [0, 1, 2])
                self.assertEqual(reply(client)["type"], "interruption")
                streams.playback.side_effect = VoiceAPIError(409, "playback_turn_mismatch", "Old turn")
                send(client, {"type": "playback", "turn_id": "turn", "event": "ended"})
                send(client, {"type": "pcm", "sequence": 0, "audio_base64": "AAA="})
                self.assertEqual(reply(client)["next_sequence"], 1)
                send(client, {"type": "playback", "turn_id": "unrecognized", "event": "ended"})
                self.assertEqual(reply(client)["error"], "playback_turn_mismatch")
                self.assertEqual(client.recv(1), b"")
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)
        streams.push_audio.assert_called_once_with("session", 0, 0, b"\0\0")


if __name__ == "__main__":
    unittest.main()
