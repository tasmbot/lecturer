import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lecturer.summarize.ollama_client import build_summary_prompt


def test_chunk_prompt_contains_text_and_constraints():
    prompt = build_summary_prompt("тестовый текст", final=False)
    assert "тестовый текст" in prompt
    assert "Не добавляй информацию" in prompt
    assert "Markdown" in prompt


def test_final_prompt_mentions_merging():
    prompt = build_summary_prompt("объединённый текст", final=True)
    assert "объединённый текст" in prompt
    assert "Объедини" in prompt


if __name__ == "__main__":
    test_chunk_prompt_contains_text_and_constraints()
    test_final_prompt_mentions_merging()
    print("OK: все тесты build_summary_prompt прошли")
