"""
Единая точка конфигурации.

Раньше константы (DEFAULT_WHISPER_DEVICE и т.п.) были раскиданы по ячейкам
ноутбука в непредсказуемом порядке выполнения, из-за чего падал NameError.
Теперь всё собрано в одном месте и читается один раз при импорте.

Значения по умолчанию можно переопределить через переменные окружения
или файл .env в корне проекта (см. .env.example).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    # python-dotenv не обязателен: если его нет, просто читаем os.environ
    pass


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class AudioConfig:
    # Источник звука: "screen" | "mic" | "both"
    source_mode: str = _env("LECTURER_AUDIO_SOURCE", "screen")

    # Имена входных устройств sounddevice.
    screen_device_name: str = _env("LECTURER_SCREEN_DEVICE", "BlackHole 2ch")
    mic_device_name: str = _env("LECTURER_MIC_DEVICE", "MacBook Pro Microphone")
    # Устройство для режима "both" — Aggregate Device, настраивается один раз
    # в Audio MIDI Setup.app (BlackHole 2ch + микрофон в одном виртуальном устройстве).
    aggregate_device_name: str = _env("LECTURER_AGGREGATE_DEVICE", "Lecturer Aggregate")

    sample_rate: int = _env_int("LECTURER_SAMPLE_RATE", 16000)
    channels: int = _env_int("LECTURER_CHANNELS", 1)


@dataclass(frozen=True)
class WhisperConfig:
    model_path: str = _env("LECTURER_WHISPER_MODEL_PATH", "~/Models/faster-whisper-small")
    device: str = _env("LECTURER_WHISPER_DEVICE", "cpu")
    compute_type: str = _env("LECTURER_WHISPER_COMPUTE_TYPE", "int8")
    language: str = _env("LECTURER_WHISPER_LANGUAGE", "ru")
    beam_size: int = _env_int("LECTURER_WHISPER_BEAM_SIZE", 1)
    temperature: float = _env_float("LECTURER_WHISPER_TEMPERATURE", 0.0)

    # Параметры сегментации речи (VAD на основе громкости)
    block_duration: float = _env_float("LECTURER_BLOCK_DURATION", 0.1)
    max_segment_duration: float = _env_float("LECTURER_MAX_SEGMENT_DURATION", 8.0)
    min_speech_duration: float = _env_float("LECTURER_MIN_SPEECH_DURATION", 0.3)
    silence_duration: float = _env_float("LECTURER_SILENCE_DURATION", 0.7)
    silence_threshold: float = _env_float("LECTURER_SILENCE_THRESHOLD", 0.01)


@dataclass(frozen=True)
class OllamaConfig:
    url: str = _env("LECTURER_OLLAMA_URL", "http://localhost:11434/api/generate")
    model_name: str = _env("LECTURER_OLLAMA_MODEL", "qwen3.5:4b-mlx")
    temperature: float = _env_float("LECTURER_OLLAMA_TEMPERATURE", 0.0)
    think: bool = _env_bool("LECTURER_OLLAMA_THINK", False)
    timeout: int = _env_int("LECTURER_OLLAMA_TIMEOUT", 180)
    max_retries: int = _env_int("LECTURER_OLLAMA_MAX_RETRIES", 2)

    chunk_size: int = _env_int("LECTURER_CHUNK_SIZE", 12000)
    chunk_overlap: int = _env_int("LECTURER_CHUNK_OVERLAP", 500)

    # Печатать токены суммаризации в консоль по мере генерации (как было в
    # исходном ноутбуке) — удобно видеть прогресс, особенно на длинных чанках.
    stream_output: bool = _env_bool("LECTURER_OLLAMA_STREAM_OUTPUT", True)


@dataclass(frozen=True)
class AnytypeConfig:
    enabled: bool = _env_bool("LECTURER_ANYTYPE_ENABLED", True)
    api_key: str = _env("ANYTYPE_API_KEY", "")
    space_id: str = _env("ANYTYPE_SPACE_ID", "")
    api_version: str = _env("ANYTYPE_API_VERSION", "2025-11-08")
    # "page" работает всегда из коробки. "lecture_note" — кастомный тип,
    # который нужно один раз создать в приложении Anytype (Settings -> Types).
    type_key: str = _env("ANYTYPE_TYPE_KEY", "page")

    # Ключи (key, не название!) созданных вами свойств типа Lecture Note.
    # ВАЖНО: Anytype генерирует key автоматически из названия свойства при
    # создании — эти значения могут не совпасть с вашими один в один.
    # Проверить реальные ключи: GET /v1/spaces/{space_id}/properties
    # (или через MCP-инструмент API-list-properties) и поправить в .env.
    prop_source_type: str = _env("ANYTYPE_PROP_SOURCE_TYPE", "source_type")
    prop_audio_source: str = _env("ANYTYPE_PROP_AUDIO_SOURCE", "audio_source")
    prop_duration: str = _env("ANYTYPE_PROP_DURATION", "duration")
    prop_recorded_at: str = _env("ANYTYPE_PROP_RECORDED_AT", "recorded_at")
    prop_transcript: str = _env("ANYTYPE_PROP_TRANSCRIPT", "transcript")

    # Прикреплять ли сырой transcript.md как файл в свойство "Transcript"
    # (требует лишний запрос на загрузку файла — можно выключить).
    attach_transcript_file: bool = _env_bool("LECTURER_ANYTYPE_ATTACH_TRANSCRIPT", True)

    # Куда девать болтливые stderr-логи процесса `npx @anyproto/anytype-mcp`
    # (он при старте печатает полный реестр инструментов) — по умолчанию в
    # файл рядом с сессией, а не в терминал.
    quiet_mcp_logs: bool = _env_bool("LECTURER_ANYTYPE_QUIET_MCP_LOGS", True)


@dataclass(frozen=True)
class SessionConfig:
    # Где хранить сессии (аудио, транскрипт, конспект, метаданные)
    sessions_root: Path = field(
        default_factory=lambda: Path(_env("LECTURER_SESSIONS_ROOT", "~/Lectures/sessions")).expanduser()
    )
    # Куда пишется pid текущей запущенной сессии — используется командой stop
    pid_dir: Path = field(default_factory=lambda: Path(_env("LECTURER_STATE_DIR", "~/.lecturer")).expanduser())

    # Плоская папка с копиями transcript/summary всех сессий с датой в
    # имени файла — удобно смотреть/грепать всё сразу, не заходя в
    # подпапки сессий. Копии, не замена — оригиналы остаются в session.root.
    archive_dir: Path = field(
        default_factory=lambda: Path(_env("LECTURER_ARCHIVE_DIR", "~/Lectures/archive")).expanduser()
    )


@dataclass(frozen=True)
class Settings:
    audio: AudioConfig = field(default_factory=AudioConfig)
    whisper: WhisperConfig = field(default_factory=WhisperConfig)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    anytype: AnytypeConfig = field(default_factory=AnytypeConfig)
    session: SessionConfig = field(default_factory=SessionConfig)


settings = Settings()
