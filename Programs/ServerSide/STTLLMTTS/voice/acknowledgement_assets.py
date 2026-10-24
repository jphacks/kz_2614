"""Validate explicitly generated acknowledgement assets against current TTS inputs."""
from __future__ import annotations
import hashlib
import io
import json
import struct
import wave
from pathlib import Path
from ..tts.irodori import TTSConfig, SynthesizedSpeech
from ...dashboard.settings_store import reference_voice_path


def assets_directory() -> Path:
    return reference_voice_path().parent / 'acknowledgements'


def input_identity(tts) -> dict | None:
    config = getattr(tts, 'config', None)
    if not isinstance(config, TTSConfig):
        return None
    reference = getattr(tts, '_reference_audio_bytes', None)
    if config.reference_audio_path and reference is None:
        return None  # Do not approve an unknown/unreadable reference snapshot.
    return {
        'checkpoint': config.checkpoint, 'caption': config.caption, 'seed': config.seed,
        'num_steps': config.num_steps, 'max_seconds': config.max_seconds,
        'reference_max_seconds': config.reference_max_seconds, 'codec_repo': config.codec_repo,
        'reference_sha256': hashlib.sha256(reference).hexdigest() if reference else None,
    }


def validate_wav(audio: bytes) -> dict:
    if len(audio) > 4 * 1024 * 1024:
        raise ValueError('Acknowledgement exceeds 4MiB')
    with wave.open(io.BytesIO(audio), 'rb') as stream:
        channels, width, rate, frames = stream.getnchannels(), stream.getsampwidth(), stream.getframerate(), stream.getnframes()
        if channels not in (1, 2) or width != 2 or not 8000 <= rate <= 96000 or not 0.1 <= frames / rate <= 10:
            raise ValueError('Invalid acknowledgement WAV format/duration')
        pcm = stream.readframes(frames)
    if len(pcm) != frames * channels * width:
        raise ValueError('Truncated acknowledgement WAV')
    samples = [value[0] for value in struct.iter_unpack('<h', pcm)]
    peak = max(abs(value) for value in samples)
    rms = (sum(value * value for value in samples) / len(samples)) ** .5
    if peak <= 64 or rms <= 8:
        raise ValueError('Acknowledgement contains no usable audio signal')
    return {'duration_seconds': round(frames / rate, 3), 'sample_rate': rate, 'channels': channels,
            'sample_width_bits': 16, 'peak': peak, 'rms': round(rms, 2),
            'sha256': hashlib.sha256(audio).hexdigest(), 'size_bytes': len(audio)}


def load_assets(tts, phrases: tuple[str, ...]):
    identity = input_identity(tts)
    if identity is None:
        return None
    root = assets_directory()
    try:
        manifest = json.loads((root / 'manifest.json').read_text())
        if manifest['schema'] != 1 or manifest['input'] != identity:
            return None
        entries = manifest['assets']
        if [item['text'] for item in entries] != list(phrases):
            return None
        result = []
        for item in entries:
            name = item['file']
            if not isinstance(name, str) or Path(name).name != name or not name.endswith('.wav'):
                return None
            path = root / name
            if path.stat().st_size > 4 * 1024 * 1024:
                return None
            audio = path.read_bytes()
            info = validate_wav(audio)
            if info['sha256'] != item['sha256']:
                return None
            result.append((item['text'], SynthesizedSpeech(audio, 'audio/wav', '.wav')))
        return result
    except (OSError, ValueError, KeyError, TypeError, wave.Error, EOFError):
        return None
