import asyncio
import io
import json
import math
from pathlib import Path
import struct
import tempfile
import types
import unittest
import wave
from unittest.mock import AsyncMock, patch
from Programs.ServerSide.STTLLMTTS.tts.irodori import TTSConfig, SynthesizedSpeech
from Programs.ServerSide.STTLLMTTS.voice.acknowledgements import DEFAULT_ACKNOWLEDGEMENT_PHRASES, VoiceAcknowledgementSystem
from Programs.ServerSide.STTLLMTTS.voice.acknowledgement_assets import input_identity, validate_wav, load_assets


def tone():
    stream=io.BytesIO()
    with wave.open(stream,'wb') as audio:
        audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000)
        audio.writeframes(b''.join(struct.pack('<h',int(1000*math.sin(i*.1))) for i in range(8000)))
    return stream.getvalue()


class AcknowledgementAssetTests(unittest.TestCase):
    def setUp(self):
        self.audio=tone()
        self.tts=types.SimpleNamespace(config=TTSConfig(reference_audio_path='reference.wav'),_reference_audio_bytes=b'reference snapshot',synthesize_async=AsyncMock(return_value=SynthesizedSpeech(self.audio,'audio/wav','.wav')))

    def write_assets(self,root):
        entries=[]
        for index,text in enumerate(DEFAULT_ACKNOWLEDGEMENT_PHRASES):
            name=f'{index}.wav';(root/name).write_bytes(self.audio)
            entries.append({'text':text,'file':name,**validate_wav(self.audio)})
        (root/'manifest.json').write_text(json.dumps({'schema':1,'input':input_identity(self.tts),'assets':entries}))

    def test_requested_phrases_are_exact_tts_inputs(self):
        self.assertEqual(DEFAULT_ACKNOWLEDGEMENT_PHRASES,('うんうん','ふーん','へー'))
        system=VoiceAcknowledgementSystem(self.tts)
        with patch('Programs.ServerSide.STTLLMTTS.voice.acknowledgements.load_assets',return_value=None):
            asyncio.run(system.prepare())
        self.assertEqual([call.args[0] for call in self.tts.synthesize_async.await_args_list],list(DEFAULT_ACKNOWLEDGEMENT_PHRASES))
        self.assertEqual([system.next().text for _ in range(4)],['うんうん','ふーん','へー','うんうん'])

    def test_saved_assets_used_without_regenerating(self):
        with tempfile.TemporaryDirectory() as folder,patch('Programs.ServerSide.STTLLMTTS.voice.acknowledgement_assets.assets_directory',return_value=Path(folder)):
            self.write_assets(Path(folder))
            system=VoiceAcknowledgementSystem(self.tts)
            asyncio.run(system.prepare())
            self.assertTrue(system.ready)
            self.tts.synthesize_async.assert_not_called()
            self.assertEqual(system.next().speech.audio_bytes,self.audio)

    def test_changed_reference_or_corrupt_asset_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder,patch('Programs.ServerSide.STTLLMTTS.voice.acknowledgement_assets.assets_directory',return_value=Path(folder)):
            root=Path(folder);self.write_assets(root)
            self.tts._reference_audio_bytes=b'new reference'
            self.assertIsNone(load_assets(self.tts,DEFAULT_ACKNOWLEDGEMENT_PHRASES))
            self.tts._reference_audio_bytes=b'reference snapshot'
            (root/'0.wav').write_bytes(b'broken')
            self.assertIsNone(load_assets(self.tts,DEFAULT_ACKNOWLEDGEMENT_PHRASES))

    def test_silence_is_not_accepted_as_audio_data(self):
        buffer=io.BytesIO()
        with wave.open(buffer,'wb') as audio:
            audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000);audio.writeframes(b'\0\0'*16000)
        with self.assertRaises(ValueError): validate_wav(buffer.getvalue())


class DirectTTSOnlyTests(unittest.TestCase):
    def test_generator_blocks_tcp_and_llm_calls(self):
        import importlib.util
        import socket
        script=Path(__file__).resolve().parents[1]/"scripts"/"generate_acknowledgements.py"
        spec=importlib.util.spec_from_file_location("generate_ack_direct",script)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        def attempt_llm():
            connection = types.SimpleNamespace(family=socket.AF_INET)
            socket.socket.connect(connection,("127.0.0.1",1234))
        with patch.object(module,"generate",side_effect=attempt_llm):
            with self.assertRaisesRegex(RuntimeError,"direct TTS only"):
                module.main()
