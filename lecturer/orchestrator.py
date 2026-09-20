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
import time
from datetime import datetime, timezone

from lecturer.audio.capture import AudioSourceMode, device_name_for_mode
from lecturer.config import settings
from lecturer.export.anytype_client import AnytypeExporter, LectureNoteProperties
from lecturer.session import Session, SessionLock
from lecturer.summarize.ollama_client import OllamaClient, summarize_text
from lecturer.transcribe.whisper_engine import RealtimeTranscriber

logger = logging.getLogger(__name__)


def _log_full_exception(exc: BaseException, prefix: str = "") -> None:
    """
    anyio/MCP заворачивают реальную ошибку в TaskGroup -> ExceptionGroup,
    из-за чего в логе видно только бесполезное "unhandled errors in a
    TaskGroup (N sub-exceptions)". Разворачиваем рекурсивно и печатаем
    каждую настоящую причину с полным traceback.
    """

    import traceback

    # ExceptionGroup / BaseExceptionGroup появились в Python 3.11.
    sub_exceptions = getattr(exc, "exceptions", None)
    if sub_exceptions:
        for i, sub in enumerate(sub_exceptions, 1):
            _log_full_exception(sub, prefix=f"{prefix}[{i}] ")
        return

    logger.error(
        "%sПричина: %s: %s\n%s",
        prefix,
        type(exc).__name__,
        exc,
        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
    )


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
            self.session.archive_outputs()
            self._run_export()
        finally:
            self.session.meta.finished_at = datetime.now(timezone.utc).isoformat()
            self.session.save_meta()
            self.session.append_to_stats()
            self.lock.release()

        logger.info("Готово. Результаты: %s", self.session.root)
        return self.session

    def run_from_existing_transcript(
        self,
        transcript_text: str,
        duration_sec: float = 0.0,
        skip_export: bool = False,
    ) -> Session:
        """
        Для отладки: пропускает запись и транскрибацию, сразу суммаризирует
        готовый текст и (если не skip_export) шлёт в Anytype.
        """

        self.session.transcript_path.write_text(transcript_text, encoding="utf-8")
        self.session.meta.duration_sec = duration_sec
        self.session.meta.segments_count = transcript_text.count("\n") + 1

        self._run_summarization()
        self.session.archive_outputs()

        if not skip_export:
            self._run_export()

        self.session.meta.finished_at = datetime.now(timezone.utc).isoformat()
        self.session.save_meta()
        self.session.append_to_stats()
        logger.info("Готово. Результаты: %s", self.session.root)
        return self.session

    def _run_transcription(self) -> None:
        device_name = device_name_for_mode(self.source_mode)
        self.transcriber = RealtimeTranscriber(device_name=device_name)

        t0 = time.monotonic()
        result = self.transcriber.start()  # блокирует до stop()
        transcription_real_time = time.monotonic() - t0

        self.session.transcript_path.write_text(result.text, encoding="utf-8")

        # --- метрики транскрибации ---
        transcript_chars = len(result.text)
        word_count = len(result.text.split())
        duration = result.duration_seconds or 1e-6  # защита от деления на 0

        m = self.session.meta
        m.duration_sec = result.duration_seconds
        m.segments_count = len(result.segments)
        m.transcript_chars = transcript_chars
        m.words_per_minute = round(word_count / (duration / 60), 1)
        m.transcription_real_time_sec = round(transcription_real_time, 1)
        m.rtf = round(transcription_real_time / duration, 3)
        m.auto_stopped = self.transcriber._stop_event.is_set() and not any(
            # авто-стоп — stop_event взведён, но не через SIGINT:
            # проверяем, что причина — тишина, а не внешний сигнал
            # (упрощение: если stop вызван из _run_transcription сам по себе — авто-стоп)
            True for _ in []
        )
        # Более надёжный способ: флаг выставляется самим транскрайбером
        m.auto_stopped = getattr(self.transcriber, "_auto_stopped", False)

        logger.info(
            "Транскрипт: %.1f сек. записи | RTF=%.2f | %d слов | %.0f слов/мин",
            m.duration_sec, m.rtf, word_count, m.words_per_minute,
        )

    def _run_summarization(self) -> None:
        text = self.session.transcript_path.read_text(encoding="utf-8")
        if not text.strip():
            logger.warning("Транскрипт пуст — суммаризация пропущена.")
            return

        t0 = time.monotonic()
        client = OllamaClient()
        summarize_text(text, client=client, output_file=str(self.session.summary_path))
        summarization_sec = time.monotonic() - t0

        summary_chars = len(self.session.summary_path.read_text(encoding="utf-8")) if self.session.summary_path.exists() else 0
        transcript_chars = len(text)

        m = self.session.meta
        m.summarization_sec = round(summarization_sec, 1)
        m.summary_chars = summary_chars
        m.transcript_chars = transcript_chars  # на случай run_from_existing_transcript
        m.compression_ratio = round(summary_chars / transcript_chars, 3) if transcript_chars else 0.0

        logger.info(
            "Суммаризация: %.1f сек. | %d → %d символов (сжатие %.1f%%)",
            summarization_sec,
            transcript_chars,
            summary_chars,
            (1 - m.compression_ratio) * 100,
        )

    def _run_export(self) -> None:
        if not settings.anytype.enabled:
            logger.info("Экспорт в Anytype выключен (LECTURER_ANYTYPE_ENABLED=false).")
            return

        if not self.session.summary_path.exists():
            logger.warning("Нет summary.md — экспорт пропущен.")
            return

        _AUDIO_SOURCE_TAG = {"screen": "screen", "mic": "mic", "both": "screen+mic"}

        properties = LectureNoteProperties(
            source_type="lecture",
            audio_source=_AUDIO_SOURCE_TAG.get(self.source_mode.value, self.source_mode.value),
            duration_sec=int(self.session.meta.duration_sec),
            recorded_at=self.session.meta.started_at,
        )

        exporter = AnytypeExporter()
        t0 = time.monotonic()
        try:
            result = asyncio.run(
                exporter.export_markdown(
                    self.session.summary_path,
                    transcript_path=self.session.transcript_path,
                    properties=properties,
                    log_file=self.session.mcp_log_path,
                )
            )
            self.session.meta.export_sec = round(time.monotonic() - t0, 1)
            self.session.meta.anytype_object_id = (
                result.get("object", {}).get("id") or result.get("id", "")
            )
            self.session.meta.anytype_transcript_file_id = (
                result.get("transcript_file_id", "")
            )
            logger.info("Экспорт завершён за %.1f сек.", self.session.meta.export_sec)
        except Exception as e:
            self.session.meta.export_sec = round(time.monotonic() - t0, 1)
            logger.error("Экспорт в Anytype не удался. summary.md сохранён локально: %s", self.session.summary_path)
            _log_full_exception(e)