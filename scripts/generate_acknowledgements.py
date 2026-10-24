"""Explicitly save the three requested acknowledgements using the saved reference voice."""
from __future__ import annotations
import hashlib
import json
import os
import socket
from unittest.mock import patch
from dataclasses import replace
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.ai.tts.irodori import IrodoriTTS, TTSConfig
from server.ai.tts.local_backend import LocalIrodoriBackend
from server.ai.voice.acknowledgements import DEFAULT_ACKNOWLEDGEMENT_PHRASES
from server.ai.voice.acknowledgement_assets import input_identity, assets_directory, validate_wav, load_assets


def generate():
    intended = IrodoriTTS(TTSConfig.from_env())
    identity = input_identity(intended)
    if not identity or not identity['reference_sha256']:
        raise RuntimeError('A saved, readable reference voice is required')
    # No extra GPU model alongside the running conversation service.
    config = replace(intended.config, model_device='cpu', model_precision='fp32', codec_device='cpu', codec_precision='fp32')
    backend = LocalIrodoriBackend(config)
    api, runtime, lock = backend._load()
    original = runtime.synthesize
    checked_inputs = []
    expected = iter(DEFAULT_ACKNOWLEDGEMENT_PHRASES)
    verified_reference_requests = []
    original_reference = backend._cached_reference_path
    def checked_reference(api, runtime, request, audio, path):
        assert hashlib.sha256(audio).hexdigest() == identity["reference_sha256"]
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == identity["reference_sha256"]
        assert request.max_ref_seconds == intended.config.reference_max_seconds
        verified_reference_requests.append(request.text)
        return original_reference(api, runtime, request, audio, path)
    backend._cached_reference_path = checked_reference
    def synthesize(request, **kwargs):
        text = next(expected)
        assert request.text == text
        assert request.no_ref is False and (request.ref_wav or request.ref_latent)
        assert verified_reference_requests[-1] == text
        assert request.max_ref_seconds == intended.config.reference_max_seconds
        if request.ref_wav:
            assert hashlib.sha256(Path(request.ref_wav).read_bytes()).hexdigest() == identity["reference_sha256"]
        checked_inputs.append({'text': request.text, 'no_ref': request.no_ref,
                               'reference_sha256': identity['reference_sha256'],
                               'max_ref_seconds': request.max_ref_seconds})
        return original(request, **kwargs)
    runtime.synthesize = synthesize
    root = assets_directory()
    root.mkdir(parents=True, exist_ok=True)
    names = ('un_un.wav', 'fuun.wav', 'hee.wav')
    entries = []
    try:
        for phrase, name in zip(DEFAULT_ACKNOWLEDGEMENT_PHRASES, names):
            start = time.monotonic()
            audio = backend.synthesize(phrase, reference_audio=intended._reference_audio_bytes)
            info = validate_wav(audio)
            temporary = root / (name + '.tmp')
            temporary.write_bytes(audio)
            os.replace(temporary, root / name)
            entries.append({'text': phrase, 'file': name, **info})
            print(json.dumps({'text': phrase, 'duration_seconds': info['duration_seconds'],
                              'generation_seconds': round(time.monotonic()-start, 2), 'input_verified': True}, ensure_ascii=False), flush=True)
    finally:
        runtime.synthesize = original
        backend._cached_reference_path = original_reference
    assert len(checked_inputs) == 3
    manifest = {'schema': 1, 'input': identity, 'generation': {'model_device': 'cpu', 'model_precision': 'fp32',
                 'codec_device': 'cpu', 'codec_precision': 'fp32', 'num_steps': config.num_steps},
                'llm_used': False, 'network_requests_allowed': False,
                'input_verification': checked_inputs, 'assets': entries}
    temporary = root / 'manifest.json.tmp'
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    os.replace(temporary, root / 'manifest.json')
    assert load_assets(intended, DEFAULT_ACKNOWLEDGEMENT_PHRASES) is not None
    print('ALL_THREE_INPUTS_AND_PERSISTED_ASSETS_VERIFIED', flush=True)


def main():
    original_connect = socket.socket.connect
    def no_tcp(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise RuntimeError("This generator is direct TTS only: network/LLM calls are forbidden")
        return original_connect(sock, address)
    with patch.dict(os.environ, {'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'}), patch.object(socket.socket, 'connect', no_tcp):
        generate()


if __name__ == '__main__':
    main()
