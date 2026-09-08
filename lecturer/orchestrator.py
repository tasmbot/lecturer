"""
Оркестратор: один прогон пайплайна от старта записи до (опционального)
экспорта в Anytype. Управляется извне через RealtimeTranscriber.stop(),
которую CLI дёргает из обработчика сигнала SIGINT.

Состояния: RECORDING -> TRANSCRIBING (идёт параллельно с RECORDING,
т.к. транскрибация потоковая) -> SUMMARIZING -> EXPORTING -> DONE.
Каждый этап сразу пишет результат на диск (transcript.md, summary.md) —
если что-то упадёt на суммаризации или экспорте, транскрипт не теряется
и его можно досуммаризировать вручную через summarize_text().
"""

from __future__ import annotations

import asyncio
import logging

from lecturer.audio.capture import AudioSourceMode, device_name_for_mode
from lecturer.config import settings
from lecturer.export.anytype_client import AnytypeExporter, LectureNoteProperties
from lecturer.session import Session, SessionLock
from lecturer.summarize.ollama_client import OllamaClient, summarize_text
from lecturer.transcribe.whisper_engine import RealtimeTranscriber

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, source_mode: AudioSourceMode = AudioSourceMode.SCREEN):
        self.source_mode = source_mode
        self.session = Session.create(source_mode=source_mode.value)
        self.lock = SessionLock()
        self.transcriber: RealtimeTranscriber | None = None

    def request_stop(self) -> None:
        """Вызывается из обработчика сигнала — прерывает запись/транскрипцию,
        после чего run() сам доходит до суммаризации и экспорта."""

        if self.transcriber is not None:
            self.transcriber.stop()

    def run(self) -> Session:
        self.lock.acquire(self.session.root)
        try:
            self._run_transcription()
            self._run_summarization()
            self._run_export()
        finally:
            self.session.save_meta()
            self.lock.release()

        logger.info("Готово. Результаты: %s", self.session.root)
        return self.session

    def _run_transcription(self) -> None:
        device_name = device_name_for_mode(self.source_mode)
        self.transcriber = RealtimeTranscriber(device_name=device_name)

        result = self.transcriber.start()  # блокирует до stop()

        self.session.transcript_path.write_text(result.text, encoding="utf-8")
        self.session.meta.duration_sec = result.duration_seconds
        self.session.meta.segments_count = len(result.segments)
        logger.info("Транскрипт сохранён: %s", self.session.transcript_path)

    def _run_summarization(self) -> None:
        text = self.session.transcript_path.read_text(encoding="utf-8")
        if not text.strip():
            logger.warning("Транскрипт пуст — суммаризация пропущена.")
            return

        client = OllamaClient()
        summarize_text(text, client=client, output_file=str(self.session.summary_path))

    def _run_export(self) -> None:
        if not settings.anytype.enabled:
            logger.info("Экспорт в Anytype выключен (LECTURER_ANYTYPE_ENABLED=false).")
            return

        if not self.session.summary_path.exists():
            logger.warning("Нет summary.md — экспорт пропущен.")
            return

        properties = LectureNoteProperties(
            source_type="Lecture",
            audio_source=self.source_mode.value,
            duration_sec=int(self.session.meta.duration_sec),
            recorded_at=self.session.meta.started_at,
        )

        exporter = AnytypeExporter()
        try:
            asyncio.run(exporter.export_markdown(self.session.summary_path, properties=properties))
        except Exception as e:
            # Экспорт — последний шаг: если он падает (нет сети, не настроен
            # Anytype), локальный summary.md всё равно остаётся на диске.
            logger.error("Экспорт в Anytype не удался: %s. summary.md сохранён локально: %s", e, self.session.summary_path)
