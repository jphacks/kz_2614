"""PCM transport frames; this does not split text or alter TTS generation."""

from __future__ import annotations

import base64
import io
from typing import Any, Iterator

from .sessions import VoiceAPIError


PCM_STREAM_PROFILE = "pcm_s16le_16000_stream"
PCM_SAMPLE_RATE = 16_000
PCM_FRAME_SAMPLES = 1_600  # 100 ms of mono PCM16LE per transport message.
MAX_PCM_BYTES = PCM_SAMPLE_RATE * 2 * 60
MAX_ENCODED_AUDIO_CHARS = 34_000_000


def _decode_pcm(event: dict[str, Any]) -> bytes:
    import av

    try:
        encoded = event.get("audio_base64")
        if not isinstance(encoded, str) or len(encoded) > MAX_ENCODED_AUDIO_CHARS:
            raise ValueError("Invalid audio payload")
        audio = base64.b64decode(encoded, validate=True)
        pcm = bytearray()

        def append(frames):
            for frame in frames:
                pcm.extend(frame.to_ndarray().tobytes())
                if len(pcm) > MAX_PCM_BYTES:
                    raise VoiceAPIError(413, "audio_frame_too_large", "返答音声の送信上限を超えました。")

        if event.get("media_type") == "audio/pcm":
            if (event.get("sample_rate") != PCM_SAMPLE_RATE or event.get("channels") != 1
                    or event.get("format") != "s16le" or not audio or len(audio) % 2):
                raise ValueError("Invalid PCM format")
            pcm.extend(audio)
        else:
            with av.open(io.BytesIO(audio)) as container:
                resampler = av.AudioResampler(format="s16", layout="mono", rate=PCM_SAMPLE_RATE)
                for frame in container.decode(audio=0):
                    append(resampler.resample(frame))
                append(resampler.resample(None))
        if not pcm or len(pcm) % 2:
            raise ValueError("Empty PCM output")
        if len(pcm) > MAX_PCM_BYTES:
            raise VoiceAPIError(413, "audio_frame_too_large", "返答音声の送信上限を超えました。")
        return bytes(pcm)
    except VoiceAPIError:
        raise
    except Exception as exc:
        raise VoiceAPIError(502, "audio_frame_conversion_failed", "返答音声を連続データへ変換できませんでした。") from exc


def iter_audio_frames(event: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Keep one durable cursor per source event, advanced after its final frame.

    A reconnect replays the parent event. Clients skip received frame_index values
    before accepting its remainder; generating hundreds of transport frames does
    not consume hundreds of entries in the durable event log.
    """
    if event.get("type") not in {"audio_chunk", "acknowledgement"}:
        yield event
        return
    pcm = _decode_pcm(event)
    frame_bytes = PCM_FRAME_SAMPLES * 2
    count = (len(pcm) + frame_bytes - 1) // frame_bytes
    # Text already travels on transcript/text_delta; avoid repeating it per frame.
    metadata = {key: value for key, value in event.items() if key not in {"audio_base64", "text"}}
    for index in range(count):
        part = pcm[index * frame_bytes:(index + 1) * frame_bytes]
        yield {
            **metadata,
            "type": "audio_frame", "source_type": event["type"],
            "index": event.get("index", 0),
            "media_type": "audio/pcm", "sample_rate": PCM_SAMPLE_RATE,
            "channels": 1, "format": "s16le",
            "frame_index": index, "frame_count": count,
            "sample_offset": index * PCM_FRAME_SAMPLES,
            "total_samples": len(pcm) // 2, "is_last": index == count - 1,
            "audio_base64": base64.b64encode(part).decode("ascii"),
        }
