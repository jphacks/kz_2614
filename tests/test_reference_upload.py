"""Reference WAV persistence through the actual HTTP handler, isolated from live data."""
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import wave
from Programs.ServerSide.dashboard.server import DashboardHTTPServer
from Programs.ServerSide.dashboard.settings_store import save_reference_voice


def wav(seconds=1, rate=16000, channels=1):
    data=io.BytesIO()
    with wave.open(data,'wb') as output:
        output.setnchannels(channels); output.setsampwidth(2); output.setframerate(rate)
        output.writeframes(b'\0\0'*(seconds*rate*channels))
    return data.getvalue()


class ReferenceUploadTests(unittest.TestCase):
    def test_save_reload_and_reject_without_losing_previous_file(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'NENGITSUNE_SETTINGS_PATH':str(Path(directory)/'data'/'settings.json')}):
            server=DashboardHTTPServer(('127.0.0.1',0))
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            url=f'http://127.0.0.1:{server.server_port}/api/v1/dashboard/reference-audio'
            try:
                audio=wav()
                with urlopen(Request(url,data=audio,method='PUT',headers={'Content-Type':'audio/wav'}),timeout=3) as response:
                    self.assertTrue(json.load(response)['uploaded'])
                saved=Path(directory)/'data'/'reference_voice.wav'
                self.assertEqual(saved.read_bytes(),audio)
                with urlopen(url,timeout=3) as response:
                    self.assertEqual(json.load(response)['duration_seconds'],1)
                with self.assertRaises(HTTPError) as caught:
                    urlopen(Request(url,data=b'invalid',method='PUT',headers={'Content-Type':'audio/wav'}),timeout=3)
                self.assertEqual(caught.exception.code,400)
                self.assertEqual(saved.read_bytes(),audio)
            finally:
                server.shutdown();server.server_close();worker.join(2)

    def test_directory_failure_is_reported_as_storage_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            blocked=Path(directory)/'file';blocked.write_text('not a directory')
            with patch.dict(os.environ,{'NENGITSUNE_SETTINGS_PATH':str(blocked/'settings.json')}):
                with self.assertRaises(RuntimeError): save_reference_voice(wav())


class ReferenceSizeLimitTests(unittest.TestCase):
    def test_over_10mb_and_over_30seconds_save_successfully(self):
        from Programs.ServerSide.dashboard.settings_store import get_reference_voice_info
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'NENGITSUNE_SETTINGS_PATH': str(Path(directory)/'settings.json')}):
            audio = wav(seconds=60, rate=48000, channels=2)
            self.assertGreater(len(audio), 10*1024*1024)
            self.assertTrue(save_reference_voice(audio)['uploaded'])
            self.assertEqual(get_reference_voice_info()['duration_seconds'], 60)
            self.assertEqual((Path(directory)/'reference_voice.wav').read_bytes(), audio)

    def test_upload_and_tts_limits_are_consistent(self):
        from Programs.ServerSide.dashboard.settings_store import MAX_REFERENCE_WAV_BYTES
        from Programs.ServerSide.STTLLMTTS.tts.irodori import MAX_REFERENCE_AUDIO_BYTES
        from Programs.ServerSide.STTLLMTTS.tts.local_backend import MAX_REFERENCE_WAV_BYTES as local_limit
        self.assertEqual(MAX_REFERENCE_WAV_BYTES, 100*1024*1024)
        self.assertEqual(MAX_REFERENCE_AUDIO_BYTES, local_limit)
