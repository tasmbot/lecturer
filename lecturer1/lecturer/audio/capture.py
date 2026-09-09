"""
Работа с аудиоустройствами и запись в WAV.

Поддерживает три режима источника (см. AudioSourceMode):
  - screen: только системный звук через BlackHole
  - mic:    только микрофон
  - both:   экран + микрофон одновременно через Aggregate Device

Для режима "both" нужно один раз настроить Aggregate Device в
Audio MIDI Setup.app: добавить BlackHole 2ch и встроенный микрофон в
одно виртуальное устройство и указать его имя в LECTURER_AGGREGATE_DEVICE.
Без этого шага sounddevice видит BlackHole и микрофон как два раздельных
устройства и свести их в один поток нельзя.
"""

from __future__ import annotations

import enum
import logging
import os
import queue
import sys
import wave
from dataclasses import dataclass

import sounddevice as sd

from lecturer.config import AudioConfig, settings

logger = logging.getLogger(__name__)


class AudioSourceMode(str, enum.Enum):
    SCREEN = "screen"
    MIC = "mic"
    BOTH = "both"


def device_name_for_mode(mode: AudioSourceMode, audio_cfg: AudioConfig = settings.audio) -> str:
    """Возвращает имя sounddevice-устройства для выбранного режима записи."""

    if mode is AudioSourceMode.SCREEN:
        return audio_cfg.screen_device_name
    if mode is AudioSourceMode.MIC:
        return audio_cfg.mic_device_name
    if mode is AudioSourceMode.BOTH:
        return audio_cfg.aggregate_device_name
    raise ValueError(f"Неизвестный режим источника звука: {mode}")


def list_input_devices() -> list[dict]:
    """Список всех входных аудиоустройств, видимых sounddevice."""

    devices = sd.query_devices()
    return [
        {"index": idx, "name": d["name"], "max_input_channels": d["max_input_channels"]}
        for idx, d in enumerate(devices)
        if d["max_input_channels"] > 0
    ]


def find_audio_device(device_name: str) -> int | None:
    """
    Ищет входное устройство по (под)строке имени. Регистронезависимо,
    совпадение по вхождению — так что "blackhole" найдёт "BlackHole 2ch".
    """

    devices = sd.query_devices()

    for idx, device in enumerate(devices):
        if device_name.lower() in device["name"].lower() and device["max_input_channels"] > 0:
            logger.info("Найдено устройство [%s]: %s", idx, device["name"])
            return idx

    logger.warning("Устройство '%s' не найдено.", device_name)
    for d in list_input_devices():
        logger.warning("  [%s] %s (inputs: %s)", d["index"], d["name"], d["max_input_channels"])

    return None


@dataclass
class RecordedFile:
    path: str
    size_bytes: int


def record_audio(
    output_file: str,
    device_name: str,
    sample_rate: int = 16000,
    channels: int = 1,
    stop_event=None,
) -> RecordedFile | None:
    """
    Синхронная запись в WAV до Ctrl+C или до срабатывания stop_event
    (threading.Event) — используется, когда запись нужно остановить
    программно (например по сигналу от CLI-команды stop).
    """

    device_id = find_audio_device(device_name)
    if device_id is None:
        return None

    audio_queue: queue.Queue = queue.Queue()

    def callback(indata, frames, time_info, status):
        if status:
            print(status, file=sys.stderr)
        audio_queue.put(indata.copy())

    logger.info("Начало записи: устройство=%s, sample_rate=%s, каналы=%s", device_name, sample_rate, channels)

    try:
        with wave.open(output_file, "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)

            with sd.InputStream(
                samplerate=sample_rate,
                device=device_id,
                channels=channels,
                dtype="int16",
                callback=callback,
            ):
                while stop_event is None or not stop_event.is_set():
                    try:
                        data = audio_queue.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    wav_file.writeframes(data.tobytes())

    except KeyboardInterrupt:
        logger.info("Запись остановлена (Ctrl+C).")
    except Exception as e:
        logger.error("Ошибка записи: %s", e)
        return None

    if not os.path.exists(output_file):
        logger.error("WAV-файл не создан.")
        return None

    file_size = os.path.getsize(output_file)
    logger.info("Файл сохранён: %s (%.2f MB)", output_file, file_size / 1024 / 1024)

    return RecordedFile(path=output_file, size_bytes=file_size)
