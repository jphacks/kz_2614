"""faster-whisper を使う日本語音声認識クライアント。"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

from ...api.settings_store import get_saved_settings
from ...api.runtime_status import RuntimeObservation, component_configuration
from ..runtime import run_blocking_stage

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)

# server/whisper is the model-cache folder and is ignored by Git.
WHISPER_MODEL_DIR = Path(__file__).resolve().parents[2] / "whisper"
DEFAULT_MAX_AUDIO_BYTES = 25_000_000
DEFAULT_MAX_AUDIO_SECONDS = 60.0
SAMPLE_RATE = 16_000
VAD_FRAME_SAMPLES = 400  # 25 ms
VAD_HOP_SAMPLES = 160  # 10 ms
VAD_SILENCE_SECONDS = 0.7
VAD_MAX_SEGMENT_SECONDS = 15.0
VAD_MIN_SEGMENT_SECONDS = 0.25


class AudioDurationExceeded(ValueError):
    """Audio decoded by faster-whisper exceeds the accepted turn length."""


class CudaUnavailableError(RuntimeError):
    """CUDA inference was requested but its runtime or device is unavailable."""


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class WhisperConfig:
    model_id: str = "Systran/faster-whisper-small"
    device: str = "cuda"
    compute_type: str = "float16"
    language: str = "ja"
    local_files_only: bool = False
    max_audio_bytes: int = DEFAULT_MAX_AUDIO_BYTES
    max_duration_seconds: float = DEFAULT_MAX_AUDIO_SECONDS
    timeout_seconds: float = 90.0
    vad_filter: bool = True

    @classmethod
    def from_env(cls) -> "WhisperConfig":
        saved = get_saved_settings().get("stt", {})
        return cls(
            model_id=saved.get("model_id", os.getenv("WHISPER_MODEL_ID", cls.model_id)),
            device=saved.get("device", os.getenv("WHISPER_DEVICE", cls.device)),
            compute_type=saved.get("compute_type", os.getenv("WHISPER_COMPUTE_TYPE", cls.compute_type)),
            language=saved.get("language", os.getenv("WHISPER_LANGUAGE", cls.language)),
            local_files_only=_env_bool("WHISPER_LOCAL_FILES_ONLY"),
            max_audio_bytes=max(1_024, int(os.getenv("WHISPER_MAX_AUDIO_BYTES", str(cls.max_audio_bytes)))),
            max_duration_seconds=max(
                1.0,
                float(os.getenv("WHISPER_MAX_AUDIO_SECONDS", str(cls.max_duration_seconds))),
            ),
            timeout_seconds=min(
                180.0,
                max(1.0, float(os.getenv("WHISPER_TIMEOUT_SECONDS", str(cls.timeout_seconds)))),
            ),
            vad_filter=saved.get(
                "vad_filter", _env_bool("WHISPER_VAD_FILTER", default=cls.vad_filter)
            ),
        )


@dataclass(frozen=True)
class Transcription:
    text: str
    language: str | None
    language_probability: float | None
    duration_seconds: float | None
    avg_logprob: float | None = None
    no_speech_probability: float | None = None
    speech_segment_count: int = 0
    vocal_analysis: dict[str, str | float] | None = None


class WhisperSTT:
    """同一設定のモデルをプロセス内で共有し、推論を直列化して文字起こしする。"""

    _shared_models: dict[
        tuple[str, str, str, str],
        tuple[Any, threading.Lock],
    ] = {}
    _shared_models_lock = threading.Lock()

    def __init__(self, config: WhisperConfig | None = None) -> None:
        self.config = config or WhisperConfig.from_env()
        self._runtime_observation = RuntimeObservation(component_configuration(
            self.config.model_id, device=self.config.device,
            precision=self.config.compute_type, backend="faster_whisper",
        ))
        self._model: Any | None = None
        self._load_lock = threading.Lock()
        self._inference_lock: threading.Lock | None = None

    def _load_model(self) -> Any:
        observation = self._runtime_observation
        loading = self._model is None
        observation.begin(loading=loading)
        try:
            model = self._load_model_impl()
            engine = getattr(model, "model", None)
            observation.loaded(
                model=self.config.model_id,
                device=getattr(engine, "device", None),
                precision=getattr(engine, "compute_type", None),
            )
            return model
        except Exception as error:
            observation.failure(error)
            raise
        finally:
            observation.finish(loading=loading)

    def runtime_status(self) -> dict[str, Any]:
        return self._runtime_observation.snapshot()

    def _load_model_impl(self) -> Any:
        with self._load_lock:
            if self._model is None:
                key = (
                    self.config.model_id,
                    self.config.device,
                    self.config.compute_type,
                    str(WHISPER_MODEL_DIR.resolve()),
                )
                with self._shared_models_lock:
                    shared = self._shared_models.get(key)
                    if shared is None:
                        WHISPER_MODEL_DIR.mkdir(parents=True, exist_ok=True)
                        if self.config.device == "cuda":
                            try:
                                from ctranslate2 import get_cuda_device_count

                                cuda_devices = get_cuda_device_count()
                            except Exception as exc:
                                raise CudaUnavailableError(
                                    "Whisper CUDA is unavailable. Check the NVIDIA driver and ensure CUDA 12 cuBLAS and cuDNN 9 runtime DLLs are installed and on PATH."
                                ) from exc
                            if cuda_devices < 1:
                                raise CudaUnavailableError(
                                    "Whisper CUDA was selected, but CTranslate2 found no CUDA device. Check the NVIDIA driver and CUDA runtime, or select CPU in the dashboard."
                                )
                        try:
                            from faster_whisper import WhisperModel
                        except ImportError as exc:
                            raise RuntimeError(
                                "faster-whisper is not installed; install server/requirements.txt"
                            ) from exc

                        try:
                            model = WhisperModel(
                                self.config.model_id,
                                device=self.config.device,
                                compute_type=self.config.compute_type,
                                download_root=str(WHISPER_MODEL_DIR),
                                local_files_only=self.config.local_files_only,
                            )
                        except Exception as exc:
                            if self.config.device == "cuda":
                                raise CudaUnavailableError(
                                    "Whisper could not initialize CUDA. Check CUDA 12, cuBLAS, cuDNN 9, GPU memory, and the selected compute type."
                                ) from exc
                            raise
                        shared = (model, threading.Lock())
                        self._shared_models[key] = shared
                self._model, self._inference_lock = shared
            return self._model

    def transcribe_file(self, audio_path: str | Path) -> Transcription:
        observation = self._runtime_observation
        observation.begin()
        try:
            return self._transcribe_file_impl(audio_path)
        except (ValueError, FileNotFoundError):
            # An invalid upload is not evidence that the inference service failed.
            raise
        except Exception as error:
            observation.failure(error)
            raise
        finally:
            observation.finish()

    def _transcribe_file_impl(self, audio_path: str | Path) -> Transcription:
        """Decode browser audio, split at sustained pauses, and transcribe speech regions."""
        path = Path(audio_path).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"Audio file does not exist: {path}")
        try:
            file_size = path.stat().st_size
        except OSError as exc:
            raise ValueError("Audio file cannot be accessed") from exc
        if file_size <= 0:
            raise ValueError("Audio file is empty")
        if file_size > self.config.max_audio_bytes:
            raise ValueError("Audio file exceeds WHISPER_MAX_AUDIO_BYTES")

        audio = self._decode_audio(path, self.config.max_duration_seconds)
        duration = len(audio) / SAMPLE_RATE
        if duration > self.config.max_duration_seconds:
            raise AudioDurationExceeded("Audio file exceeds WHISPER_MAX_AUDIO_SECONDS")
        speech_regions = self._split_speech_regions(audio)
        vocal_analysis = self._analyze_speaking_style(audio)
        if not speech_regions:
            return Transcription(
                text="",
                language=None,
                language_probability=None,
                duration_seconds=duration,
                vocal_analysis=vocal_analysis,
            )

        model = self._load_model()
        language = self.config.language.strip().lower()
        inference_lock = self._inference_lock
        if inference_lock is None:
            raise RuntimeError("Whisper model lock was not initialized")

        recognized_segments: list[Any] = []
        detected_languages: list[tuple[str, float]] = []
        with inference_lock:
            for start, end in speech_regions:
                segments, info = model.transcribe(
                    audio[start:end],
                    language=None if language in {"", "auto"} else language,
                    beam_size=3,
                    initial_prompt=(
                        "自然な日本語の日常会話です。くだけた話し方も含め、"
                        "聞こえた言葉をそのまま文字起こししてください。"
                    ),
                    condition_on_previous_text=False,
                    vad_filter=self.config.vad_filter,
                )
                recognized_segments.extend(segment for segment in segments if segment.text.strip())
                detected_language = getattr(info, "language", None)
                language_probability = getattr(info, "language_probability", None)
                if detected_language and language_probability is not None:
                    detected_languages.append((str(detected_language), float(language_probability)))

        self._runtime_observation.success()
        text = " ".join(segment.text.strip() for segment in recognized_segments).strip()
        weights = [max(1, len(segment.text.strip())) for segment in recognized_segments]
        avg_logprob = self._weighted_segment_stat(recognized_segments, weights, "avg_logprob")
        no_speech_probability = self._weighted_segment_stat(
            recognized_segments, weights, "no_speech_prob"
        )
        # Whisper may hallucinate short text over silence; match AI_Secretary's joint confidence gate.
        if (
            text
            and avg_logprob is not None
            and no_speech_probability is not None
            and avg_logprob <= -0.45
            and no_speech_probability >= 0.72
        ):
            text = ""

        detected_language, language_probability = max(
            detected_languages,
            key=lambda item: item[1],
            default=(None, None),
        )
        return Transcription(
            text=text,
            language=detected_language,
            language_probability=language_probability,
            duration_seconds=duration,
            avg_logprob=avg_logprob,
            no_speech_probability=no_speech_probability,
            speech_segment_count=len(speech_regions),
            vocal_analysis=vocal_analysis,
        )

    @staticmethod
    def _weighted_segment_stat(segments: list[Any], weights: list[int], attribute: str) -> float | None:
        weighted = [
            (float(value), weight)
            for segment, weight in zip(segments, weights)
            if (value := getattr(segment, attribute, None)) is not None
        ]
        total_weight = sum(weight for _, weight in weighted)
        if not total_weight:
            return None
        return sum(value * weight for value, weight in weighted) / total_weight

    @staticmethod
    def _decode_audio(path: Path, max_duration_seconds: float):
        """Decode WebM/Ogg/WAV/MP4 through the PyAV dependency used by faster-whisper."""
        try:
            import av
            import numpy as np
        except ImportError as exc:
            raise RuntimeError("PyAV and NumPy are required to decode and analyze browser audio") from exc

        chunks = []
        decoded_samples = 0
        max_samples = int(max_duration_seconds * SAMPLE_RATE)

        def append_frame(frame) -> None:
            nonlocal decoded_samples
            samples = frame.to_ndarray().astype(np.float32, copy=False).reshape(-1)
            decoded_samples += len(samples)
            if decoded_samples > max_samples:
                raise AudioDurationExceeded("Audio file exceeds WHISPER_MAX_AUDIO_SECONDS")
            chunks.append(samples)

        try:
            with av.open(str(path)) as container:
                audio_stream = next(
                    (stream for stream in container.streams if stream.type == "audio"),
                    None,
                )
                if audio_stream is None:
                    raise ValueError("Audio file has no audio stream")
                resampler = av.AudioResampler(format="fltp", layout="mono", rate=SAMPLE_RATE)
                for frame in container.decode(audio=audio_stream.index):
                    for converted in resampler.resample(frame):
                        append_frame(converted)
                for converted in resampler.resample(None):
                    append_frame(converted)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("Audio file could not be decoded") from exc

        if not chunks:
            raise ValueError("Audio file contains no decodable samples")
        audio = np.concatenate(chunks)
        if audio.size == 0:
            raise ValueError("Audio file contains no decodable samples")
        return audio

    def _split_speech_regions(self, audio) -> list[tuple[int, int]]:
        """Split sustained low-energy pauses while keeping brief pauses inside one phrase."""
        import numpy as np

        if len(audio) < VAD_FRAME_SAMPLES:
            return []
        frame_count = 1 + (len(audio) - VAD_FRAME_SAMPLES) // VAD_HOP_SAMPLES
        energies = np.empty(frame_count, dtype=np.float32)
        for index in range(frame_count):
            start = index * VAD_HOP_SAMPLES
            frame = audio[start : start + VAD_FRAME_SAMPLES]
            energies[index] = float(np.sqrt(np.mean(frame * frame)))

        # Estimate the room/microphone floor per recording while retaining sensitivity to quiet speech.
        noise_floor = float(np.percentile(energies, 20))
        threshold = max(0.003, min(0.008, noise_floor * 1.3))
        active = energies >= threshold
        max_gap_frames = max(1, int(0.12 * SAMPLE_RATE / VAD_HOP_SAMPLES))
        index = 0
        while index < len(active):
            if active[index]:
                index += 1
                continue
            gap_start = index
            while index < len(active) and not active[index]:
                index += 1
            if gap_start > 0 and index < len(active) and index - gap_start <= max_gap_frames:
                active[gap_start:index] = True

        silence_frames = max(1, int(VAD_SILENCE_SECONDS * SAMPLE_RATE / VAD_HOP_SAMPLES))
        minimum_samples = int(VAD_MIN_SEGMENT_SECONDS * SAMPLE_RATE)
        maximum_samples = int(VAD_MAX_SEGMENT_SECONDS * SAMPLE_RATE)
        regions: list[tuple[int, int]] = []
        speech_start: int | None = None
        last_active_end = 0
        for index, is_active in enumerate(active):
            frame_start = index * VAD_HOP_SAMPLES
            frame_end = min(len(audio), frame_start + VAD_FRAME_SAMPLES)
            if is_active:
                if speech_start is None:
                    speech_start = frame_start
                last_active_end = frame_end
            elif (
                speech_start is not None
                and frame_start - last_active_end >= silence_frames * VAD_HOP_SAMPLES
            ):
                if last_active_end - speech_start >= minimum_samples:
                    regions.extend(self._cap_region(speech_start, last_active_end, maximum_samples))
                speech_start = None

        if speech_start is not None and last_active_end - speech_start >= minimum_samples:
            regions.extend(self._cap_region(speech_start, last_active_end, maximum_samples))
        return regions

    @staticmethod
    def _cap_region(start: int, end: int, maximum_samples: int) -> list[tuple[int, int]]:
        regions = []
        while end - start > maximum_samples:
            regions.append((start, start + maximum_samples))
            start += maximum_samples
        if end > start:
            regions.append((start, end))
        return regions

    @staticmethod
    def _analyze_speaking_style(audio) -> dict[str, str | float]:
        """Estimate pitch, pitch variation, and loudness without assigning an emotion."""
        import numpy as np

        if len(audio) < 800:
            return {"pitch": "判定しにくい", "delivery": "音声が短く判定しにくい"}

        # Downsample to 8 kHz for a light autocorrelation estimate over 70-400 Hz.
        decimated = audio[::2]
        frame_size, hop_size, sample_rate = 240, 480, 8_000
        pitches: list[float] = []
        energies: list[float] = []
        window = np.hanning(frame_size).astype(np.float32)
        min_lag = int(sample_rate / 400)
        max_lag = min(int(sample_rate / 70), frame_size - 2)
        for start in range(0, len(decimated) - frame_size + 1, hop_size):
            frame = decimated[start : start + frame_size].astype(np.float32, copy=True)
            frame -= float(np.mean(frame))
            rms = float(np.sqrt(np.mean(frame * frame)))
            if rms < 0.008:
                continue
            energies.append(rms)
            frame *= window
            correlation = np.correlate(frame, frame, mode="full")[frame_size - 1 :]
            region = correlation[min_lag : max_lag + 1]
            if not len(region):
                continue
            lag = min_lag + int(np.argmax(region))
            confidence = float(correlation[lag]) / max(float(correlation[0]), 1e-8)
            if confidence >= 0.35:
                pitches.append(sample_rate / lag)

        if not energies:
            return {"pitch": "検出できず", "delivery": "声量が小さい、または無音"}

        median_energy = float(np.median(energies))
        energy_spread = float(np.percentile(energies, 85) - np.percentile(energies, 15))
        loudness = "強め" if median_energy >= 0.09 else "控えめ" if median_energy < 0.025 else "普通"
        if len(pitches) >= 3:
            median_pitch = float(np.median(pitches))
            pitch_spread = float(np.percentile(pitches, 85) - np.percentile(pitches, 15))
            pitch_label = "高め" if median_pitch >= 190 else "低め" if median_pitch < 125 else "中くらい"
            intonation = "抑揚が大きめ" if pitch_spread >= 65 else "抑揚は穏やか"
            pitch_summary = f"{pitch_label}（中央値約{median_pitch:.0f}Hz）"
        else:
            median_pitch = 0.0
            pitch_spread = 0.0
            intonation = "抑揚は判定しにくい"
            pitch_summary = "高さは判定しにくい"
        return {
            "pitch": pitch_summary,
            "delivery": f"声量の目安は{loudness}、{intonation}",
            "median_pitch_hz": round(median_pitch, 1) if median_pitch else 0.0,
            "pitch_spread_hz": round(pitch_spread, 1),
            "energy_spread": round(energy_spread, 4),
            "emotion": "音響特徴だけでは断定しない",
        }

    async def transcribe_file_async(
        self,
        audio_path: str | Path,
        *,
        on_worker_finished: Callable[[], None] | None = None,
    ) -> Transcription:
        """STTをイベントループ外で実行し、設定した期限内に結果を返す。"""
        def transcribe() -> Transcription:
            try:
                return self.transcribe_file(audio_path)
            finally:
                if on_worker_finished is not None:
                    on_worker_finished()

        return await run_blocking_stage(
            "stt",
            self.config.timeout_seconds,
            transcribe,
        )
