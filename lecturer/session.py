"""
Управление сессией записи: папка сессии на диске + pid-файл, по которому
команда `lecturer stop` находит запущенный процесс `lecturer start`.

Структура папки сессии (LECTURER_SESSIONS_ROOT/<timestamp>/):
    audio.wav        — (опционально) сырой звук, если включено сохранение
    transcript.md     — транскрипт с таймкодами
    summary.md         — итоговый конспект
    meta.json           — метаданные: источник звука, длительность, время записи и т.п.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from lecturer.config import SessionConfig, settings


@dataclass
class SessionMeta:
    source_mode: str
    started_at: str
    duration_sec: float = 0.0
    segments_count: int = 0
    finished_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "source_mode": self.source_mode,
            "started_at": self.started_at,
            "duration_sec": self.duration_sec,
            "segments_count": self.segments_count,
            "finished_at": self.finished_at,
        }


@dataclass
class Session:
    root: Path
    meta: SessionMeta
    timestamp: str
    cfg: SessionConfig = field(default_factory=lambda: settings.session)

    @property
    def transcript_path(self) -> Path:
        # Дата в самом имени файла — не только в имени папки — чтобы файл
        # оставался однозначно идентифицируемым, если его скопируют или
        # прикрепят отдельно (например к конспекту в Anytype).
        return self.root / f"transcript_{self.timestamp}.md"

    @property
    def summary_path(self) -> Path:
        return self.root / f"summary_{self.timestamp}.md"

    @property
    def meta_path(self) -> Path:
        return self.root / "meta.json"

    @property
    def mcp_log_path(self) -> Path:
        return self.root / "anytype_mcp.log"

    def save_meta(self) -> None:
        self.meta_path.write_text(json.dumps(self.meta.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def archive_outputs(self) -> None:
        """
        Копирует transcript/summary в плоскую папку LECTURER_ARCHIVE_DIR —
        оригиналы в session.root остаются, это именно копии для удобного
        просмотра всех сессий в одном месте.
        """

        self.cfg.archive_dir.mkdir(parents=True, exist_ok=True)
        for src in (self.transcript_path, self.summary_path):
            if src.exists():
                shutil.copy2(src, self.cfg.archive_dir / src.name)

    @classmethod
    def create(cls, source_mode: str, cfg: SessionConfig = settings.session) -> "Session":
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        root = cfg.sessions_root / timestamp
        root.mkdir(parents=True, exist_ok=True)

        meta = SessionMeta(source_mode=source_mode, started_at=datetime.now(timezone.utc).isoformat())
        session = cls(root=root, meta=meta, timestamp=timestamp, cfg=cfg)
        session.save_meta()
        return session


class SessionLock:
    """
    Pid-файл + путь к текущей сессии — так `lecturer stop` знает, какой
    процесс останавливать и куда после этого писать результат.
    """

    def __init__(self, cfg: SessionConfig = settings.session):
        cfg.pid_dir.mkdir(parents=True, exist_ok=True)
        self.pid_file = cfg.pid_dir / "session.pid"
        self.pointer_file = cfg.pid_dir / "current_session"

    def acquire(self, session_root: Path) -> None:
        if self.pid_file.exists():
            existing_pid = int(self.pid_file.read_text().strip())
            if _process_alive(existing_pid):
                raise RuntimeError(
                    f"Уже идёт запись (pid={existing_pid}). "
                    f"Сначала остановите её: lecturer stop"
                )
        self.pid_file.write_text(str(os.getpid()))
        self.pointer_file.write_text(str(session_root))

    def release(self) -> None:
        for f in (self.pid_file, self.pointer_file):
            if f.exists():
                f.unlink()

    def read_pid(self) -> int | None:
        if not self.pid_file.exists():
            return None
        return int(self.pid_file.read_text().strip())

    def read_current_session_root(self) -> Path | None:
        if not self.pointer_file.exists():
            return None
        return Path(self.pointer_file.read_text().strip())


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def send_stop_signal(cfg: SessionConfig = settings.session) -> bool:
    """Используется командой `lecturer stop`. Возвращает True, если сигнал отправлен."""

    lock = SessionLock(cfg)
    pid = lock.read_pid()
    if pid is None:
        return False
    if not _process_alive(pid):
        lock.release()
        return False
    os.kill(pid, signal.SIGINT)
    return True
