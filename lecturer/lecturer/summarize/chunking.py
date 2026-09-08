"""Разбивка длинного текста на чанки для LLM с ограниченным контекстом."""

from __future__ import annotations


def split_into_chunks(text: str, chunk_size: int, overlap: int) -> list[str]:
    """
    Разбивает текст на чанки размером до chunk_size символов, стараясь
    не резать посередине абзаца/предложения, с overlap символов
    перекрытия между соседними чанками для сохранения контекста.
    """

    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    text_length = len(text)

    while start < text_length:
        end = min(start + chunk_size, text_length)

        if end < text_length:
            newline_position = text.rfind("\n", start, end)

            if newline_position > start:
                end = newline_position
            else:
                sentence_position = max(
                    text.rfind(". ", start, end),
                    text.rfind("? ", start, end),
                    text.rfind("! ", start, end),
                )
                if sentence_position > start:
                    end = sentence_position + 1

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        start = max(end - overlap, start + 1)

    return chunks
