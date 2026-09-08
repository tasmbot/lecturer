"""
Обёртка над faster-whisper.

RealtimeTranscriber — основной путь для пайплайна: слушает аудиопоток,
сегментирует речь по громкости (простой RMS VAD) и прогоняет каждый
сегмент через faster-whisper по мере поступления.

transcribe_audio_file — батчевая транскрибация уже готового WAV-файла.
Не используется в основном пайплайне (там запись и транскрибация идут
параллельно в реальном времени), но полезна для отладки и для ручного
повторного прогона записи другой моделью.

Ключевое отличие от кода в ноутбуке: остановка управляется через
threading.Event, а не через перехват KeyboardInterrupt. Это нужно,
потому что в финальном пайплайне процесс останавливает не сам
пользователь через Ctrl+C, а CLI-команда `lecturer stop`, посылающая
сигнал в фоновый процесс.
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
import time
from dataclasses import dataclass, field

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

from lecturer.config import WhisperConfig, settings

logger = logging.getLogger(__name__)


@dataclass
class TranscriptSegment:
    timestamp_seconds: float
    text: str

    @property
    def timecode(self) -> str:
        minutes = int(self.timestamp_seconds // 60)
        seconds = int(self.timestamp_seconds % 60)
        return f"[{minutes:02d}:{seconds:02d}]"

    def __str__(self) -> str:
        return f"{self.timecode} {self.text}"


@dataclass
class TranscriptResult:
    segments: list[TranscriptSegment] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def text(self) -> str:
        return "\n".join(str(s) for s in self.segments)


class RealtimeTranscriber:
    """
    Near-real-time транскрибация аудиопотока с одного устройства.

    Использование:
        transcriber = RealtimeTranscriber(device_name="BlackHole 2ch")
        transcriber.start()          # блокирует поток до stop() или сигнала
        # ... где-то из другого потока / обработчика сигнала:
        transcriber.stop()
        result = transcriber.result  # TranscriptResult
    """

    def __init__(
        self,
        device_name: str,
        cfg: WhisperConfig = settings.whisper,
        sample_rate: int = settings.audio.sample_rate,
        channels: int = settings.audio.channels,
        on_segment=None,
    ):
        self.device_name = device_name
        self.cfg = cfg
        self.sample_rate = sample_rate
        self.channels = channels
        # опциональный колбэк on_segment(TranscriptSegment) — например для
        # live-вывода в консоль или отправки уведомления
        self.on_segment = on_segment

        self._stop_event = threading.Event()
        self.result = TranscriptResult()
        self._model: WhisperModel | None = None

    def stop(self) -> None:
        self._stop_event.set()

    def _load_model(self) -> WhisperModel:
        import os

        model_path = os.path.expanduser(self.cfg.model_path)
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Локальная модель Whisper не найдена: {model_path}")

        logger.info("Загружаем Whisper: %s (device=%s, compute_type=%s)", model_path, self.cfg.device, self.cfg.compute_type)
        model = WhisperModel(model_path, device=self.cfg.device, compute_type=self.cfg.compute_type)
        logger.info("Whisper загружен.")
        return model

    def _find_device_id(self) -> int:
        devices = sd.query_devices()
        for idx, device in enumerate(devices):
            if self.device_name.lower() in device["name"].lower() and device["max_input_channels"] > 0:
                return idx
        raise ValueError(f"Устройство '{self.device_name}' не найдено.")

    def _process_segment(self, audio: np.ndarray, total_samples: int, min_speech_samples: int) -> None:
        if len(audio) < min_speech_samples:
            return

        segments, _info = self._model.transcribe(
            audio,
            language=self.cfg.language,
            beam_size=self.cfg.beam_size,
            temperature=self.cfg.temperature,
            vad_filter=True,
            condition_on_previous_text=False,
        )

        text = " ".join(s.text.strip() for s in segments if s.text.strip()).strip()
        if not text:
            return

        elapsed = total_samples / self.sample_rate
        segment = TranscriptSegment(timestamp_seconds=elapsed, text=text)
        self.result.segments.append(segment)

        logger.info(str(segment))
        if self.on_segment:
            self.on_segment(segment)

    def start(self) -> TranscriptResult:
        """Блокирует текущий поток до stop() или KeyboardInterrupt."""

        device_id = self._find_device_id()
        self._model = self._load_model()

        block_size = int(self.sample_rate * self.cfg.block_duration)
        min_speech_samples = int(self.sample_rate * self.cfg.min_speech_duration)
        silence_samples = int(self.sample_rate * self.cfg.silence_duration)
        max_segment_samples = int(self.sample_rate * self.cfg.max_segment_duration)

        audio_queue: queue.Queue = queue.Queue()

        def callback(indata, frames, time_info, status):
            if status:
                print(f"Audio status: {status}", file=sys.stderr)
            audio_queue.put(indata[:, 0].copy())

        speech_buffer = np.array([], dtype=np.float32)
        recording_speech = False
        silence_counter = 0
        total_audio_samples = 0
        start_time = time.time()

        logger.info("Слушаем аудио (устройство=%s)...", self.device_name)

        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                blocksize=block_size,
                device=device_id,
                channels=self.channels,
                dtype="float32",
                callback=callback,
            ):
                while not self._stop_event.is_set():
                    try:
                        data = audio_queue.get(timeout=0.2)
                    except queue.Empty:
                        continue

                    total_audio_samples += len(data)
                    rms = np.sqrt(np.mean(np.square(data)))
                    is_speech = rms > self.cfg.silence_threshold

                    if is_speech:
                        speech_buffer = data.copy() if not recording_speech else np.concatenate([speech_buffer, data])
                        recording_speech = True
                        silence_counter = 0

                        if len(speech_buffer) >= max_segment_samples:
                            self._process_segment(speech_buffer, total_audio_samples, min_speech_samples)
                            speech_buffer = np.array([], dtype=np.float32)
                            recording_speech = False
                    else:
                        if recording_speech:
                            speech_buffer = np.concatenate([speech_buffer, data])
                            silence_counter += len(data)

                            if silence_counter >= silence_samples:
                                self._process_segment(speech_buffer, total_audio_samples, min_speech_samples)
                                speech_buffer = np.array([], dtype=np.float32)
                                recording_speech = False
                                silence_counter = 0

        except KeyboardInterrupt:
            logger.info("Остановка по Ctrl+C...")
        finally:
            if len(speech_buffer) > 0:
                logger.info("Обрабатываем последний фрагмент...")
                self._process_segment(speech_buffer, total_audio_samples, min_speech_samples)

        self.result.duration_seconds = total_audio_samples / self.sample_rate
        total_time = time.time() - start_time
        logger.info(
            "Транскрибация завершена: %.1f сек. записи, %.1f сек. работы, %s фрагментов",
            self.result.duration_seconds,
            total_time,
            len(self.result.segments),
        )
        return self.result


def transcribe_audio_file(
    audio_file: str,
    cfg: WhisperConfig = settings.whisper,
    beam_size: int = 5,
) -> TranscriptResult:
    """Батчевая транскрибация готового WAV-файла. Не используется в основном
    пайплайне — вспомогательная функция для отладки/ручного прогона."""

    import os

    model_path = os.path.expanduser(cfg.model_path)
    logger.info("Транскрибируем файл %s моделью %s", audio_file, model_path)

    model = WhisperModel(model_path, device=cfg.device, compute_type=cfg.compute_type)
    raw_segments, _info = model.transcribe(
        audio_file,
        beam_size=beam_size,
        language=cfg.language,
        vad_filter=True,
    )

    result = TranscriptResult()
    for seg in raw_segments:
        text = seg.text.strip()
        if text:
            result.segments.append(TranscriptSegment(timestamp_seconds=seg.start, text=text))

    if result.segments:
        result.duration_seconds = result.segments[-1].timestamp_seconds

    logger.info("Готово: %s сегментов", len(result.segments))
    return result
