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

import json as _json
import logging
import time

import requests

from lecturer.config import OllamaConfig, settings
from lecturer.summarize.chunking import split_into_chunks

logger = logging.getLogger(__name__)


def build_summary_prompt(text: str, final: bool = False) -> str:
    """
    final=False — конспект отдельного чанка лекции.
    final=True — итоговый конспект всей лекции.
    """

    
    if final:
        task = (
            "Составь итоговый конспект лекции на основе промежуточных конспектов ниже.\n\n"
            "Объедини материал по смысловым темам, убери повторы и восстанови "
            "логическую структуру лекции.\n"
            "Сохрани основные концепции, определения, факты, формулы, причинно-следственные "
            "связи, примеры и выводы.\n"
            "Сделай конспект удобным для последующего изучения и повторения материала."
        )
    else:
        task = (
            "Составь конспект данного фрагмента лекции.\n\n"
            "Выдели основные понятия, определения, факты, объяснения, "
            "причинно-следственные связи, примеры и выводы.\n"
            "Сохраняй порядок и логику изложения материала."
        )

    return f"""{task}
    

    Правила:

    * Используй только информацию из исходного текста.
    * Не добавляй знания из своей модели.
    * Не делай предположений и не исправляй содержание лектора от себя.
    * Сохраняй точные термины, названия, числа, даты и формулы, если они присутствуют.
    * Если термин или утверждение объясняется в тексте, сохрани это объяснение.
    * Не удаляй пример, если он помогает понять основную концепцию.
    * Удаляй приветствия, речевые повторы, слова-паразиты и несущественные отступления.
    * Не превращай каждую фразу в отдельный пункт.
    * Объединяй связанные мысли в один смысловой блок.
    * Используй Markdown.
    * Не добавляй разделы, для которых в тексте нет материала.
    * Если информация в тексте неполная или неясная, не додумывай её.
    * Верни только готовый конспект.

    Текст:

    {text}
    """



class OllamaClient:
    """Тонкая обёртка над /api/generate с ретраями и разумными таймаутами."""

    def __init__(self, cfg: OllamaConfig = settings.ollama):
        self.cfg = cfg

    def generate(self, prompt: str, model_name: str | None = None) -> str:
        """
        Запрос к Ollama со стримингом ответа в консоль (как было в исходном
        ноутбуке) — по мере генерации токены печатаются в stdout, а полный
        текст всё равно собирается и возвращается целиком. Стриминг можно
        выключить через LECTURER_OLLAMA_STREAM_OUTPUT=false.

        При сетевых ошибках/таймауте делает до cfg.max_retries повторов с
        экспоненциальной паузой; если все попытки исчерпаны — пробрасывает
        исключение (вызывающий код сам решает, чем заменить результат).
        """

        model = model_name or self.cfg.model_name
        last_error: Exception | None = None

        for attempt in range(1, self.cfg.max_retries + 2):
            try:
                text = self._generate_once(prompt, model)
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

        logger.error("Ollama недоступна после %s попыток.", self.cfg.max_retries + 1)
        raise last_error or RuntimeError("Ollama request failed")

    def _generate_once(self, prompt: str, model: str) -> str:
        if not self.cfg.stream_output:
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
            return response.json().get("response", "").strip()

        # Стриминговый режим: сервер шлёт по одной JSON-строке на токен/чанк
        # (формат Ollama /api/generate с stream=true). Печатаем в консоль
        # по мере поступления и одновременно собираем полный текст.
        response = requests.post(
            self.cfg.url,
            json={
                "model": model,
                "prompt": prompt,
                "stream": True,
                "think": self.cfg.think,
                "options": {"temperature": self.cfg.temperature},
            },
            timeout=self.cfg.timeout,
            stream=True,
        )
        response.raise_for_status()

        chunks: list[str] = []
        for line in response.iter_lines():
            if not line:
                continue
            piece = _json.loads(line)
            token = piece.get("response", "")
            if token:
                print(token, end="", flush=True)
                chunks.append(token)
            if piece.get("done"):
                break

        print()  # перевод строки после стрима одного чанка
        return "".join(chunks).strip()


def summarize_text(
    text: str,
    client: OllamaClient | None = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
    output_file: str | None = None,
) -> str:
    """
    Суммаризирует произвольно длинный текст.

    Алгоритм:
    1. Разбивает текст на чанки.
    2. Делает отдельную суммаризацию каждого чанка.
    3. Если чанков больше одного — объединяет их промежуточные
       конспекты и делает один финальный проход.
    """

    if not text or not text.strip():
        raise ValueError("Переданный текст пуст.")

    client = client or OllamaClient()

    # Используем значения из конфигурации только если параметры
    # не были переданы явно.
    if chunk_size is None:
        chunk_size = client.cfg.chunk_size

    if chunk_overlap is None:
        chunk_overlap = client.cfg.chunk_overlap

    if chunk_size <= 0:
        raise ValueError(
            f"chunk_size должен быть больше 0, получено: {chunk_size}"
        )

    if chunk_overlap < 0:
        raise ValueError(
            f"chunk_overlap не может быть отрицательным, "
            f"получено: {chunk_overlap}"
        )

    if chunk_overlap >= chunk_size:
        raise ValueError(
            f"chunk_overlap ({chunk_overlap}) должен быть меньше "
            f"chunk_size ({chunk_size})"
        )

    chunks = split_into_chunks(
        text,
        chunk_size=chunk_size,
        overlap=chunk_overlap,
    )

    logger.info(
        "Суммаризация: %s символов, %s чанк(ов), "
        "chunk_size=%s, overlap=%s",
        len(text),
        len(chunks),
        chunk_size,
        chunk_overlap,
    )

    chunk_summaries: list[str] = []

    for i, chunk in enumerate(chunks, 1):
        logger.info(
            "Чанк %s/%s (%s символов)",
            i,
            len(chunks),
            len(chunk),
        )

        prompt = build_summary_prompt(
            chunk,
            final=False,
        )

        try:
            summary = client.generate(prompt)

        except Exception as e:
            logger.error(
                "Ошибка суммаризации чанка %s/%s: %s",
                i,
                len(chunks),
                e,
            )

            # При сбое Ollama не теряем исходный текст.
            summary = chunk

        chunk_summaries.append(summary)

    # Один чанк — финальный проход не нужен.
    if len(chunk_summaries) == 1:
        logger.info(
            "Обработан один чанк. Финальная суммаризация не требуется."
        )

        final_summary = chunk_summaries[0]

    else:
        # Объединяем промежуточные конспекты.
        combined = "\n\n".join(chunk_summaries)

        logger.info(
            "Все %s чанка обработаны. "
            "Размер промежуточных конспектов: %s символов.",
            len(chunk_summaries),
            len(combined),
        )

        logger.info(
            "Запуск финальной суммаризации."
        )

        final_prompt = build_summary_prompt(
            combined,
            final=True,
        )

        try:
            final_summary = client.generate(final_prompt)

        except Exception as e:
            logger.error(
                "Ошибка финальной суммаризации: %s",
                e,
            )

            # Если финальный проход не удался,
            # возвращаем объединённые промежуточные конспекты.
            final_summary = combined

        logger.info(
            "Финальная суммаризация завершена."
        )

    if output_file:
        with open(
            output_file,
            "w",
            encoding="utf-8",
        ) as f:
            f.write(final_summary)

        logger.info(
            "Конспект сохранён: %s",
            output_file,
        )

    return final_summary