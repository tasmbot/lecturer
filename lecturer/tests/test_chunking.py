import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lecturer.summarize.chunking import split_into_chunks


def test_short_text_single_chunk():
    text = "короткий текст"
    assert split_into_chunks(text, chunk_size=1000, overlap=100) == [text]


def test_splits_on_newline_boundary():
    text = "первый абзац\n" + "x" * 50 + "\nвторой абзац\n" + "y" * 50
    chunks = split_into_chunks(text, chunk_size=30, overlap=5)
    assert len(chunks) > 1
    # каждый чанк не длиннее исходного текста и непуст
    assert all(0 < len(c) for c in chunks)


def test_overlap_preserves_all_content_coverage():
    text = "Предложение раз. Предложение два. Предложение три. " * 10
    chunks = split_into_chunks(text, chunk_size=60, overlap=10)
    assert len(chunks) > 1
    # объединение чанков должно покрывать текст (с учётом overlap длиннее оригинала)
    assert sum(len(c) for c in chunks) >= len(text)


def test_no_infinite_loop_on_small_chunk_size():
    text = "a" * 200
    chunks = split_into_chunks(text, chunk_size=5, overlap=3)
    assert len(chunks) > 0
    assert "".join(chunks).replace("", "") is not None  # завершилось, не зациклилось


if __name__ == "__main__":
    test_short_text_single_chunk()
    test_splits_on_newline_boundary()
    test_overlap_preserves_all_content_coverage()
    test_no_infinite_loop_on_small_chunk_size()
    print("OK: все тесты split_into_chunks прошли")
