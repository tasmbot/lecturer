# lecturer

Локальный пайплайн: запись экрана/микрофона → транскрибация (faster-whisper) →
суммаризация (Ollama / qwen3.5) → экспорт конспекта в Anytype.

Полностью офлайн, кроме шага экспорта (локальный Ollama-сервер тоже должен
быть запущен: `ollama serve`).

## Установка

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# отредактируйте .env: пути к моделям, устройства, ключ Anytype
```

Модель faster-whisper должна быть скачана заранее и лежать по пути из
`LECTURER_WHISPER_MODEL_PATH` (по умолчанию `~/Models/faster-whisper-small`).

## Настройка звука на macOS

- **screen** — системный звук через [BlackHole](https://github.com/ExistentialAudio/BlackHole)
  (`brew install blackhole-2ch`). Один раз: Audio MIDI Setup → создать
  Multi-Output Device, чтобы звук был слышен одновременно с BlackHole.
- **mic** — обычный микрофон, ничего дополнительно настраивать не нужно.
- **both** — экран + микрофон одновременно. Один раз в Audio MIDI Setup.app:
  создать **Aggregate Device**, добавить в него BlackHole 2ch и микрофон,
  назвать так же, как в `LECTURER_AGGREGATE_DEVICE`.

## Настройка Anytype

По умолчанию (`ANYTYPE_TYPE_KEY=page`) экспорт создаёт обычную страницу —
работает сразу, ничего настраивать не нужно.

Чтобы получить структурированные свойства (тег, длительность, источник
записи и т.п.), один раз создайте в приложении Anytype кастомный тип
**Lecture Note** (Settings → Types → New Type) со свойствами:

| Свойство | Ключ | Формат |
|---|---|---|
| Тип записи | `source_type` | select: Lecture / Meeting / Video / Other |
| Источник звука | `audio_source` | select: screen / mic / both |
| Длительность, сек | `duration_sec` | number |
| Дата записи | `recorded_at` | date |
| Тег | `tag` | multi_select |

После этого поставьте `ANYTYPE_TYPE_KEY=lecture_note` в `.env`. Если при
экспорте что-то не совпадёт со схемой, пайплайн автоматически откатится
на дефолтный `page` без свойств — конспект в любом случае не потеряется.

## Запуск

Ручной сквозной прогон из терминала:

```bash
python -m lecturer start --source screen
# ... говорите/показывайте лекцию ...
# Ctrl+C — останавливает запись, дальше пайплайн сам
# засуммаризирует транскрипт и экспортирует конспект
```

Через macOS Shortcuts (два отдельных Shortcut'а):

- **Start Lecture** — действие "Run Shell Script":
  `/usr/bin/env python3 -m lecturer start --source screen &`
- **Stop Lecture** — действие "Run Shell Script":
  `/usr/bin/env python3 -m lecturer stop`

Результаты сессии — в `~/Lectures/sessions/<дата>/`:
`transcript.md`, `summary.md`, `meta.json`.

## Тесты

```bash
python3 tests/test_chunking.py
python3 tests/test_prompts.py
```

(чистые функции без внешних зависимостей — тестируются без установки
sounddevice/faster-whisper/mcp)

## Структура проекта

```
lecturer/
  config.py               # все настройки в одном месте, читает .env
  session.py               # папка сессии, pid-файл для start/stop
  audio/capture.py          # поиск устройств, запись WAV
  transcribe/whisper_engine.py  # потоковая транскрибация faster-whisper
  summarize/
    chunking.py              # разбивка длинного текста на чанки
    ollama_client.py          # клиент Ollama + суммаризация в Markdown
  export/anytype_client.py    # экспорт в Anytype через MCP
  orchestrator.py             # связывает все этапы в один прогон
  cli.py                      # `lecturer start` / `lecturer stop`
```

## Известные ограничения / что дальше

- VAD на пороге громкости (RMS) — простой и быстрый, но чувствителен к
  шуму. План: заменить на silero-vad (см. план разработки).
- Суммаризация не имеет отдельной стадии очистки ASR-ошибок — сознательное
  решение ради скорости; модель сглаживает мелкие огрехи сама по инструкции
  в промпте.
- Формат `properties` в запросе к Anytype API — предположение по аналогии,
  нужно свериться с документацией перед первым реальным запуском.
