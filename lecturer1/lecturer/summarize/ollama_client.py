"""
Клиент Ollama и суммаризация транскрипта в Markdown-конспект.

По решению из обсуждения архитектуры: отдельной стадии "очистки" ASR-ошибок
перед суммаризацией нет — сырой транскрипт с таймкодами идёт сразу в
build_summary_prompt, а инструкция "не добавляй информацию, не меняй смысл"
внутри промпта достаточна, чтобы модель сама сгладила мелкие огрехи
распознавания при формулировании конспекта.

Старая функция process_chunk_ollama/process_text_with_ollama (чистка текста)
осознанно не портирована в основной пайплайн — при необходимости ручной
чистки транскрипта её несложно восстановить по тому же паттерну, что и
summarize_text ниже.
"""

from __future__ import annotations

import logging
import time

import requests

from lecturer.config import OllamaConfig, settings
from lecturer.summarize.chunking import split_into_chunks

logger = logging.getLogger(__name__)


def build_summary_prompt(text: str, final: bool = False) -> str:
    """
    final=False — конспект отдельного чанка.
    final=True  — объединение промежуточных конспектов в итоговый.
    """

    if final:
        task = (
            "Составь итоговый краткий конспект на основе текста ниже.\n\n"
            "Объедини связанные темы и удали повторы.\n"
            "Сохрани основные идеи, важные факты, определения и выводы.\n"
            "Используй Markdown и логическую структуру."
        )
    else:
        task = (
            "Сделай краткий конспект текста.\n\n"
            "Выдели основные темы и идеи, важные факты, определения и выводы.\n"
            "Используй Markdown.\n"
            "Сохраняй порядок изложения."
        )

    return f"""{task}

Не добавляй информацию, которой нет в тексте.
Не делай предположений.
Не меняй смысл исходного текста.
Не пересказывай несущественные детали.
Верни только готовый конспект.

Текст:

{text}
"""


class OllamaClient:
    """Тонкая обёртка над /api/generate с ретраями и разумными таймаутами."""

    def __init__(self, cfg: OllamaConfig = settings.ollama):
        self.cfg = cfg

    def generate(self, prompt: str, model_name: str | None = None) -> str:
        """
        Один запрос к Ollama (без стриминга в консоль — используется внутри
        пайплайна, где нам нужен только финальный текст). При сетевых
        ошибках/таймауте делает до cfg.max_retries повторов с экспоненциальной
        паузой, и только после исчерпания попыток возвращает исходный prompt
        как деградированный fallback, чтобы не терять данные целиком.
        """

        model = model_name or self.cfg.model_name
        last_error: Exception | None = None

        for attempt in range(1, self.cfg.max_retries + 2):
            try:
                response = requests.post(
                    self.cfg.url,
                    json={
                        "model": model,
                        "prompt": prompt,
                        "stream": False,
                        "think": self.cfg.think,
                        "options": {"temperature": self.cfg.temperature},
                    },
                    timeout=self.cfg.timeout,
                )
                response.raise_for_status()
                data = response.json()
                text = data.get("response", "").strip()

                if not text:
                    logger.warning("Ollama вернула пустой ответ (попытка %s).", attempt)
                    last_error = RuntimeError("empty response")
                else:
                    return text

            except requests.exceptions.ConnectionError as e:
                logger.error("Не удалось подключиться к Ollama (%s). Проверьте: %s", e, self.cfg.url)
                last_error = e
            except requests.exceptions.Timeout as e:
                logger.error("Timeout после %s секунд (попытка %s).", self.cfg.timeout, attempt)
                last_error = e
            except Exception as e:
                logger.error("Ошибка Ollama: %s: %s", type(e).__name__, e)
                last_error = e

            if attempt <= self.cfg.max_retries:
                backoff = min(2 ** attempt, 20)
                logger.info("Повтор через %s сек...", backoff)
                time.sleep(backoff)

        logger.error("Ollama недоступна после %s попыток, возвращаю исходный текст без обработки.", self.cfg.max_retries + 1)
        raise last_error or RuntimeError("Ollama request failed")


def summarize_text(
    text: str,
    client: OllamaClient | None = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    output_file: str | None = None,
) -> str:
    """
    Суммаризирует произвольно длинный текст: бьёт на чанки, конспектирует
    каждый по отдельности, затем (если чанков больше одного) делает
    финальный проход объединения. Для короткого транскрипта одной лекции
    обычно укладывается в один чанк — финальный проход не нужен.
    """

    if not text or not text.strip():
        raise ValueError("Переданный текст пуст.")

    client = client or OllamaClient()
    chunk_size = chunk_size or client.cfg.chunk_size
    chunk_overlap = chunk_overlap or client.cfg.chunk_overlap

    chunks = split_into_chunks(text, chunk_size=chunk_size, overlap=chunk_overlap)
    logger.info("Суммаризация: %s символов, %s чанк(ов)", len(text), len(chunks))

    chunk_summaries: list[str] = []
    for i, chunk in enumerate(chunks, 1):
        logger.info("Чанк %s/%s (%s символов)", i, len(chunks), len(chunk))
        prompt = build_summary_prompt(chunk, final=False)
        try:
            summary = client.generate(prompt)
        except Exception:
            # не теряем данные при сбое Ollama — сохраняем исходный чанк как есть
            summary = chunk
        chunk_summaries.append(summary)

    if len(chunk_summaries) == 1:
        final_summary = chunk_summaries[0]
    else:
        combined = "\n\n".join(chunk_summaries)
        final_prompt = build_summary_prompt(combined, final=True)
        try:
            final_summary = client.generate(final_prompt)
        except Exception:
            final_summary = combined

    if output_file:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(final_summary)
        logger.info("Конспект сохранён: %s", output_file)

    return final_summary
