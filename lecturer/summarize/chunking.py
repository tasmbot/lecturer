"""Разбивка длинного текста на чанки для LLM с ограниченным контекстом."""

from __future__ import annotations


def split_into_chunks(
    text: str,
    chunk_size: int,
    overlap: int,
) -> list[str]:
    """
    Разбивает текст на чанки размером до chunk_size символов.

    При возможности граница чанка переносится на конец абзаца
    или предложения. Между соседними чанками сохраняется overlap
    символов контекста.
    """

    if not text or not text.strip():
        return []

    if chunk_size <= 0:
        raise ValueError("chunk_size должен быть больше 0")

    if overlap < 0:
        raise ValueError("overlap не может быть отрицательным")

    if overlap >= chunk_size:
        raise ValueError(
            f"overlap ({overlap}) должен быть меньше "
            f"chunk_size ({chunk_size})"
        )

    text = text.strip()
    text_length = len(text)

    # Весь текст помещается в один чанк.
    if text_length <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0

    while start < text_length:
        target_end = min(start + chunk_size, text_length)
        end = target_end

        # Если это не последний чанк, пытаемся найти
        # естественную границу внутри допустимого диапазона.
        if target_end < text_length:
            # Сначала ищем конец абзаца.
            newline_position = text.rfind(
                "\n",
                start,
                target_end,
            )

            if newline_position > start:
                end = newline_position

            else:
                # Если подходящего переноса строки нет,
                # ищем конец предложения.
                sentence_position = max(
                    text.rfind(". ", start, target_end),
                    text.rfind("? ", start, target_end),
                    text.rfind("! ", start, target_end),
                )

                if sentence_position > start:
                    end = sentence_position + 1

        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        # КРИТИЧЕСКАЯ ПРОВЕРКА:
        # если дошли до конца текста, больше чанков не создаём.
        if end >= text_length:
            break

        # Следующий чанк начинается overlap символов
        # до конца текущего чанка.
        next_start = end - overlap

        # Защита от бесконечного цикла.
        if next_start <= start:
            next_start = end

        start = next_start

    return chunks