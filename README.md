# lecturer

Локальный пайплайн транскрибации и суммаризации записей экрана и микрофона на macOS.
Поддерживает лекции, рабочие встречи, любые видео со звуком.

```
Звук (экран / микрофон)
    │
    ▼
faster-whisper-small          — транскрибация в реальном времени с таймкодами
    │
    ▼
qwen3.5:4b (Ollama)           — суммаризация транскрипта в Markdown-конспект
    │
    ▼
Anytype (локально)            — создание объекта типа Lecture Note со свойствами
```

Работает полностью офлайн — ни транскрибация, ни суммаризация не требуют интернета.
Anytype тоже локальный. Единственное внешнее соединение — npm при первом запуске
MCP-сервера (кешируется).

---

## Требования

- macOS 12+
- Python 3.10+
- [Homebrew](https://brew.sh)
- [Ollama](https://ollama.ai) с моделью `qwen3.5:4b-mlx`
- [Anytype](https://anytype.io) (десктопное приложение)
- Node.js 18+ (для MCP-сервера Anytype)

---

## Установка

### 1. Homebrew и внешние зависимости

```bash
# Homebrew (если не установлен)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# BlackHole — виртуальное аудиоустройство для захвата системного звука
brew install blackhole-2ch

# Node.js — нужен для MCP-сервера Anytype
brew install node

# Ollama — локальный LLM-сервер
brew install ollama

# Скачать модель суммаризации
ollama pull qwen3.5:4b-mlx
```

### 2. Клонирование репозитория

```bash
git clone https://github.com/tasmbot/lecturer.git
cd lecturer
```

### 3. Запуск setup.sh

Скрипт создаёт виртуальное окружение, устанавливает зависимости, делает
`bin/lecturer` исполняемым и копирует `.env.example` → `.env`:

```bash
./setup.sh
```

После завершения скрипт выведет итоговый статус — в том числе предупреждения
если Ollama или BlackHole не найдены.

### 4. Скачивание модели Whisper

```bash
source .venv/bin/activate
python3 -c "
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id='guillaumekln/faster-whisper-small',
    local_dir='$HOME/Models/faster-whisper-small'
)
"
```

Доступные модели по убыванию скорости / возрастанию качества:
`tiny` → `base` → `small` → `medium` → `large-v3`.
Для русского языка `small` даёт приемлемое качество при обработке быстрее реального времени.

### 5. Настройка конфигурации

Откройте `.env` (создан автоматически на шаге 3) и заполните обязательные поля:

```env
LECTURER_WHISPER_MODEL_PATH=~/Models/faster-whisper-small
LECTURER_WHISPER_LANGUAGE=ru

ANYTYPE_API_KEY=your_key_here
ANYTYPE_SPACE_ID=your_space_id_here
```

Как получить `ANYTYPE_API_KEY` и `ANYTYPE_SPACE_ID` — см. раздел [Настройка Anytype](#настройка-anytype).

---

## Настройка захвата звука

### Режим `screen` — только системный звук

Нужен BlackHole (установлен на шаге 1). Один раз настройте Multi-Output Device,
чтобы звук шёл одновременно в колонки и в BlackHole:

1. Откройте **Audio MIDI Setup.app** (Finder → Программы → Утилиты)
2. Нажмите `+` внизу слева → **Create Multi-Output Device**
3. Поставьте галочки напротив **BlackHole 2ch** и вашего динамика/наушников
4. Назовите устройство, например `Lecturer Output`
5. В **Системных настройках → Звук → Выход** выберите `Lecturer Output`

Теперь звук идёт и в наушники, и через BlackHole в пайплайн.

### Режим `mic` — только микрофон

Ничего дополнительно настраивать не нужно. Проверьте имя устройства:

```bash
./bin/lecturer list-devices
```

И при необходимости поправьте в `.env`:

```env
LECTURER_MIC_DEVICE=MacBook Pro Microphone
```

### Режим `both` — экран + микрофон одновременно

Нужен Aggregate Device — объединяет BlackHole и микрофон в одно устройство:

1. В **Audio MIDI Setup.app** нажмите `+` → **Create Aggregate Device**
2. Поставьте галочки напротив **BlackHole 2ch** и вашего микрофона
3. Назовите устройство и укажите это имя в `.env`:

```env
LECTURER_AGGREGATE_DEVICE=Lecturer Aggregate
```

---

## Настройка Anytype

### Получение API-ключа

1. Откройте Anytype → Settings → **API**
2. Создайте новый ключ, скопируйте в `.env` как `ANYTYPE_API_KEY`
3. Скопируйте ID пространства в `ANYTYPE_SPACE_ID`

### Быстрый старт — тип Page

По умолчанию (`ANYTYPE_TYPE_KEY=page`) конспект создаётся как обычная страница.
Работает сразу без дополнительной настройки.

### Кастомный тип Lecture Note (рекомендуется)

Позволяет автоматически заполнять структурированные свойства: тип записи, источник
звука, длительность, дату, файл транскрипта.

**Шаг 1.** Создайте тип в Anytype: Settings → Types → New Type → назовите `Lecture Note`.

**Шаг 2.** Добавьте свойства:

| Название      | Формат       | Опции (для мультивыбора)               |
|---------------|--------------|----------------------------------------|
| Source Type   | Multi-select | `lecture`, `meeting`, `video`, `other` |
| Audio Source  | Multi-select | `screen`, `mic`, `screen+mic`          |
| Duration      | Number       | —                                      |
| Recorded at   | Date         | —                                      |
| Transcript    | Files        | —                                      |

> Названия опций мультивыбора должны совпадать с тем, что шлёт пайплайн (строчные буквы).

**Шаг 3.** Проверьте реальные ключи свойств — Anytype генерирует их автоматически
и они могут отличаться от названий:

```bash
./bin/lecturer list-tags
```

При расхождении поправьте в `.env`:

```env
ANYTYPE_PROP_SOURCE_TYPE=source_type
ANYTYPE_PROP_AUDIO_SOURCE=audio_source
ANYTYPE_PROP_DURATION=duration
ANYTYPE_PROP_RECORDED_AT=recorded_at
ANYTYPE_PROP_TRANSCRIPT=transcript
```

**Шаг 4.** Включите кастомный тип:

```env
ANYTYPE_TYPE_KEY=lecture_note
```

### Шаблон (опционально)

Если хотите, чтобы конспект создавался по заранее настроенному шаблону
(с нужным расположением свойств, заголовков и т.п.):

1. В Anytype: откройте тип Lecture Note → Templates → New Template, настройте
2. Узнайте ID шаблона:

```bash
./bin/lecturer list-templates
```

3. Добавьте в `.env`:

```env
ANYTYPE_TEMPLATE_ID=bafyrei...
```

---

## Запуск

Убедитесь, что Ollama запущена перед стартом:

```bash
ollama serve &
```

**Старт записи:**

```bash
./bin/lecturer start --source screen     # системный звук
./bin/lecturer start --source mic        # микрофон
./bin/lecturer start --source both       # экран + микрофон
```

Запись идёт до Ctrl+C или до авто-стопа (30 секунд тишины по умолчанию).
После остановки пайплайн автоматически суммаризирует транскрипт и создаёт
объект в Anytype.

**Стоп (из другого окна терминала):**

```bash
./bin/lecturer stop
```

---

## Запуск через быструю команду macOS (AppleScript)

Удобнее всего запускать через **Script Menu** в строке меню macOS или через
горячую клавишу.

### Настройка Script Menu

1. Откройте **Script Editor.app**
2. Меню Script Editor → Settings → General → включите **Show Script Menu in menu bar**
3. Скрипты из `~/Library/Scripts/` появятся в меню 📜 в строке меню

### Скрипт старта записи

Создайте файл `~/Library/Scripts/Lecturer Start.scpt`:

```applescript
set projectPath to "/путь/к/lecturer"

do shell script projectPath & "/bin/lecturer start --source screen > /tmp/lecturer.log 2>&1 &"

display notification "Запись началась" with title "Lecturer" subtitle "Источник: экран"
```

### Скрипт остановки

Создайте файл `~/Library/Scripts/Lecturer Stop.scpt`:

```applescript
set projectPath to "/путь/к/lecturer"

do shell script projectPath & "/bin/lecturer stop"

display notification "Запись остановлена" with title "Lecturer" subtitle "Суммаризация запущена..."
```

`bin/lecturer` сам находит venv внутри проекта — активировать окружение
вручную или указывать полный путь к Python не нужно.

### Назначение горячих клавиш

System Settings → Keyboard → Keyboard Shortcuts → Services → найдите скрипты
в разделе General и назначьте клавиши, например `⌃⌥R` для старта и `⌃⌥S` для стопа.

---

## Результаты

После каждой сессии создаётся папка `~/Lectures/sessions/<дата>/`:

```
2026-09-20_14-00-00/
  transcript_2026-09-20_14-00-00.md   — транскрипт с таймкодами
  summary_2026-09-20_14-00-00.md       — Markdown-конспект
  meta.json                             — метрики сессии
  anytype_mcp.log                       — лог экспорта
```

Копии всех файлов также появляются в `~/Lectures/archive/` — плоская папка
со всеми сессиями, удобна для поиска и анализа.

### Структура meta.json

```json
{
  "source_mode": "screen",
  "started_at": "2026-09-20T14:00:00Z",
  "finished_at": "2026-09-20T14:32:00Z",
  "transcription": {
    "duration_sec": 1800.0,
    "segments_count": 42,
    "transcript_chars": 8500,
    "words_per_minute": 112.3,
    "real_time_sec": 95.2,
    "rtf": 0.053,
    "auto_stopped": false
  },
  "summarization": {
    "chunks_count": 1,
    "duration_sec": 34.7,
    "transcript_chars": 8500,
    "summary_chars": 2100,
    "compression_ratio": 0.247
  },
  "export": {
    "duration_sec": 3.1,
    "anytype_object_id": "bafyrei..."
  }
}
```

`rtf` — real-time factor: отношение времени транскрибации к длине записи.
`rtf < 1` означает, что Whisper успевает быстрее реального времени.

Агрегированная статистика по всем сессиям пишется в `~/Lectures/archive/stats.jsonl`
(одна строка JSON на сессию).

---

## Вспомогательные команды

```bash
# Список всех входных аудиоустройств
./bin/lecturer list-devices

# Список свойств Anytype-пространства с форматами
./bin/lecturer list-tags

# Теги конкретного multi_select свойства
./bin/lecturer list-tags source_type
./bin/lecturer list-tags audio_source

# Список шаблонов типа Lecture Note
./bin/lecturer list-templates

# Тест суммаризации + экспорта без записи (встроенный тестовый текст)
./bin/lecturer test-export

# Тест на своём транскрипте, без отправки в Anytype
./bin/lecturer test-export --transcript-file ~/path/to/transcript.md --skip-anytype

# Тест с явными значениями свойств
./bin/lecturer test-export --source both --duration 1800
```

---

## Все переменные окружения

| Переменная | По умолчанию | Описание |
|---|---|---|
| `LECTURER_AUDIO_SOURCE` | `screen` | Источник звука: `screen` / `mic` / `both` |
| `LECTURER_SCREEN_DEVICE` | `BlackHole 2ch` | Имя устройства для системного звука |
| `LECTURER_MIC_DEVICE` | `MacBook Pro Microphone` | Имя микрофона |
| `LECTURER_AGGREGATE_DEVICE` | `Lecturer Aggregate` | Имя Aggregate Device для режима `both` |
| `LECTURER_WHISPER_MODEL_PATH` | `~/Models/faster-whisper-small` | Путь к модели Whisper |
| `LECTURER_WHISPER_DEVICE` | `cpu` | Устройство: `cpu` или `cuda` |
| `LECTURER_WHISPER_LANGUAGE` | `ru` | Язык транскрибации |
| `LECTURER_SILENCE_THRESHOLD` | `0.01` | Порог громкости для определения речи |
| `LECTURER_AUTO_STOP_SILENCE_SEC` | `30.0` | Авто-стоп после N секунд тишины (0 = выкл) |
| `LECTURER_OLLAMA_URL` | `http://localhost:11434/api/generate` | URL Ollama API |
| `LECTURER_OLLAMA_MODEL` | `qwen3.5:4b-mlx` | Модель суммаризации |
| `LECTURER_OLLAMA_TIMEOUT` | `180` | Таймаут запроса к Ollama (сек) |
| `LECTURER_OLLAMA_STREAM_OUTPUT` | `true` | Стриминг токенов в консоль |
| `LECTURER_ANYTYPE_ENABLED` | `true` | Включить экспорт в Anytype |
| `ANYTYPE_API_KEY` | — | API-ключ Anytype |
| `ANYTYPE_SPACE_ID` | — | ID пространства Anytype |
| `ANYTYPE_TYPE_KEY` | `page` | Тип объекта: `page` или `lecture_note` |
| `ANYTYPE_TEMPLATE_ID` | — | ID шаблона (пусто = без шаблона) |
| `ANYTYPE_API_BASE_URL` | — | URL локального API (авто если пусто) |
| `ANYTYPE_PROP_SOURCE_TYPE` | `source_type` | Ключ свойства Source Type |
| `ANYTYPE_PROP_AUDIO_SOURCE` | `audio_source` | Ключ свойства Audio Source |
| `ANYTYPE_PROP_DURATION` | `duration` | Ключ свойства Duration |
| `ANYTYPE_PROP_RECORDED_AT` | `recorded_at` | Ключ свойства Recorded at |
| `ANYTYPE_PROP_TRANSCRIPT` | `transcript` | Ключ свойства Transcript |
| `LECTURER_ANYTYPE_ATTACH_TRANSCRIPT` | `true` | Прикреплять файл транскрипта |
| `LECTURER_ANYTYPE_QUIET_MCP_LOGS` | `true` | Писать логи MCP в файл, не в терминал |
| `LECTURER_SESSIONS_ROOT` | `~/Lectures/sessions` | Папка сессий |
| `LECTURER_ARCHIVE_DIR` | `~/Lectures/archive` | Папка архива |

---

## Структура проекта

```
lecturer/
  config.py                      — все настройки, читает .env
  session.py                     — папка сессии, метрики, pid-файл
  orchestrator.py                — связывает все этапы в один прогон
  cli.py                         — команды: start / stop / test-export / list-*
  audio/
    capture.py                   — поиск устройств, запись WAV
  transcribe/
    whisper_engine.py            — потоковая транскрибация, авто-стоп
  summarize/
    chunking.py                  — разбивка длинного текста на чанки
    ollama_client.py             — клиент Ollama, стриминг, суммаризация
  export/
    anytype_client.py            — MCP-экспорт, загрузка файла, свойства
bin/
  lecturer                       — лаунчер (находит venv автоматически)
setup.sh                         — установка одной командой
tests/
  test_chunking.py
  test_prompts.py
```

---

## Тесты

Чистые функции тестируются без внешних зависимостей:

```bash
python3 tests/test_chunking.py
python3 tests/test_prompts.py
```

---

## Известные ограничения

**VAD по громкости (RMS).** Детектор речи работает по порогу громкости — простой
и быстрый, но может ложно срабатывать на фоновый шум. Если авто-стоп не работает
при тишине — поднимите `LECTURER_SILENCE_THRESHOLD` (например до `0.03`–`0.05`).

**Диаризация (кто говорит) не реализована.** Для встреч с несколькими участниками
все реплики идут в транскрипт без разделения по спикерам. Планируемое улучшение
на основе `pyannote.audio` — потребует ~1.6 GB дополнительной памяти и увеличит
время обработки 30-минутной записи примерно втрое.

**Только русский язык по умолчанию.** Сменить: `LECTURER_WHISPER_LANGUAGE=en`.
Whisper поддерживает мультиязычный режим (`language=` пусто), но он медленнее.