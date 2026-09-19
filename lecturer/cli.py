"""
CLI-обёртка для macOS Shortcuts.

Использование:
    python -m lecturer start --source screen   # запускает запись в текущем процессе
    python -m lecturer start --source both &   # то же самое, но в фоне (как вызывает Shortcut)
    python -m lecturer stop                    # шлёт SIGINT фоновому процессу
    python -m lecturer test-export             # суммаризация + экспорт без записи (отладка)

Start-shortcut в Shortcuts.app: действие "Run Shell Script"
    /usr/bin/env python3 -m lecturer start --source screen &
Stop-shortcut: действие "Run Shell Script"
    /usr/bin/env python3 -m lecturer stop

Процесс сам доходит до конца пайплайна после получения сигнала —
останавливать его силой (SIGKILL) не нужно и не следует, иначе
транскрипт не будет засуммаризирован и экспортирован.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import asyncio
import json

from lecturer.audio.capture import AudioSourceMode
from lecturer.orchestrator import Pipeline
from lecturer.session import send_stop_signal

_SAMPLE_TRANSCRIPT = """\
[00:00] Сегодня поговорим про алгоритмы сортировки.
[00:05] Начнём с сортировки пузырьком — она простая, но медленная: O(n в квадрате).
[00:12] Быстрая сортировка в среднем работает за O(n log n), но в худшем случае деградирует до O(n в квадрате).
[00:20] Сортировка слиянием стабильна и гарантированно работает за O(n log n), но требует дополнительной памяти.
[00:28] На практике для небольших массивов часто используют insertion sort как базовый случай в гибридных алгоритмах.
"""



def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_start(args: argparse.Namespace) -> int:
    pipeline = Pipeline(source_mode=AudioSourceMode(args.source))

    def _on_signal(signum, _frame):
        logging.getLogger("lecturer.cli").info("Получен сигнал остановки, завершаю запись...")
        pipeline.request_stop()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    try:
        session = pipeline.run()
    except RuntimeError as e:
        # например "уже идёт запись"
        logging.getLogger("lecturer.cli").error(str(e))
        return 1

    print(f"Готово: {session.summary_path}")
    return 0


def cmd_stop(_args: argparse.Namespace) -> int:
    sent = send_stop_signal()
    if not sent:
        print("Активная запись не найдена.")
        return 1
    print("Сигнал остановки отправлен, пайплайн завершает суммаризацию и экспорт...")
    return 0

async def _list_tags_async(prop_key: str) -> None:
    """
    Через MCP запрашиваем список свойств пространства, находим нужное по key,
    затем запрашиваем его теги. Выводит точные id/key/name — именно их нужно
    передавать в multi_select при создании объекта.
    """

    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    from lecturer.config import settings

    cfg = settings.anytype
    if not cfg.api_key or not cfg.space_id:
        print("Ошибка: ANYTYPE_API_KEY / ANYTYPE_SPACE_ID не заданы в .env")
        return

    headers = json.dumps({
        "Authorization": f"Bearer {cfg.api_key}",
        "Anytype-Version": cfg.api_version,
    })
    server_params = StdioServerParameters(
        command="npx",
        args=["-y", "@anyproto/anytype-mcp"],
        env={**__import__("os").environ, "OPENAPI_MCP_HEADERS": headers},
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # 1. Получаем все свойства пространства
            props_result = await session.call_tool(
                "API-list-properties",
                arguments={"space_id": cfg.space_id, "limit": 100},
            )
            raw = props_result.content[0].text if props_result.content else "{}"
            props_data = json.loads(raw)
            properties = props_data.get("data", [])

            if not properties:
                print("Свойства не найдены. Проверьте ANYTYPE_SPACE_ID.")
                return

            if not prop_key:
                # без аргумента — показываем все свойства с форматом
                print(f"\n{'KEY':<30} {'NAME':<30} FORMAT")
                print("-" * 70)
                for p in properties:
                    print(f"{p.get('key',''):<30} {p.get('name',''):<30} {p.get('format','')}")
                return

            # 2. Ищем свойство по key или name (регистронезависимо)
            target = next(
                (p for p in properties
                 if p.get("key", "").lower() == prop_key.lower()
                 or p.get("name", "").lower() == prop_key.lower()),
                None,
            )
            if not target:
                print(f"Свойство '{prop_key}' не найдено. Запустите без аргумента чтобы увидеть все.")
                return

            prop_id = target.get("id") or target.get("key")
            print(f"\nСвойство: {target.get('name')} | key={target.get('key')} | format={target.get('format')}")

            # 3. Получаем теги этого свойства
            tags_result = await session.call_tool(
                "API-list-tags",
                arguments={"space_id": cfg.space_id, "property_id": prop_id, "limit": 100},
            )
            raw_tags = tags_result.content[0].text if tags_result.content else "{}"
            tags_data = json.loads(raw_tags)
            tags = tags_data.get("data", [])

            if not tags:
                print("Теги не найдены — создайте их в Anytype (редактор типа → свойство → + добавить опцию).")
                return

            print(f"\n{'ID':<40} {'KEY':<30} NAME")
            print("-" * 80)
            for t in tags:
                print(f"{t.get('id',''):<40} {t.get('key',''):<30} {t.get('name','')}")

            print(f"\nПередавайте в multi_select: key (3-я колонка) или id (1-я колонка).")


def cmd_list_tags(args: argparse.Namespace) -> int:
    asyncio.run(_list_tags_async(args.property))
    return 0

def cmd_test_export(args: argparse.Namespace) -> int:
    """
    Отладочная команда: пропускает запись/транскрибацию. Берёт готовый
    транскрипт (--transcript-file) или встроенный тестовый текст,
    прогоняет суммаризацию и (если не --skip-anytype) экспорт в Anytype —
    удобно быстро проверить формат Lecture Note без реальной записи.
    """

    if args.transcript_file:
        transcript_text = open(args.transcript_file, encoding="utf-8").read()
    else:
        transcript_text = _SAMPLE_TRANSCRIPT
        print("Используется встроенный тестовый транскрипт (--transcript-file не указан).")

    pipeline = Pipeline(source_mode=AudioSourceMode(args.source))
    session = pipeline.run_from_existing_transcript(
        transcript_text,
        duration_sec=args.duration,
        skip_export=args.skip_anytype,
    )

    print(f"Транскрипт: {session.transcript_path}")
    print(f"Конспект:   {session.summary_path}")
    if not args.skip_anytype:
        print(f"Лог MCP:    {session.mcp_log_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    # -v нужен и до, и после подкоманды: "lecturer -v start" и
    # "lecturer start -v" — оба привычны, поэтому флаг регистрируется
    # на общем родительском парсере и наследуется каждой подкомандой.
    verbose_parent = argparse.ArgumentParser(add_help=False)
    verbose_parent.add_argument("-v", "--verbose", action="store_true")

    parser = argparse.ArgumentParser(prog="lecturer", parents=[verbose_parent])
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="начать запись и запустить пайплайн", parents=[verbose_parent])
    start.add_argument(
        "--source",
        choices=[m.value for m in AudioSourceMode],
        default="screen",
        help="источник звука: screen | mic | both",
    )
    start.set_defaults(func=cmd_start)

    stop = sub.add_parser("stop", help="остановить текущую запись", parents=[verbose_parent])
    stop.set_defaults(func=cmd_stop)

    list_tags = sub.add_parser(
        "list-tags",
        help="показать теги свойства Anytype (нужны для multi_select при создании объекта)",
        parents=[verbose_parent],
    )
    list_tags.add_argument(
        "property",
        nargs="?",
        default="",
        help="key или name свойства (например: source_type). Без аргумента — список всех свойств.",
    )
    list_tags.set_defaults(func=cmd_list_tags)

    test_export = sub.add_parser(
        "test-export",
        help="отладка: суммаризация + экспорт в Anytype без записи/транскрибации",
        parents=[verbose_parent],
    )
    test_export.add_argument(
        "--transcript-file",
        default=None,
        help="путь к готовому транскрипту (.md/.txt); без него — встроенный тестовый текст",
    )
    test_export.add_argument(
        "--source",
        choices=[m.value for m in AudioSourceMode],
        default="screen",
        help="значение для свойства Audio Source (по умолчанию screen)",
    )
    test_export.add_argument(
        "--duration",
        type=float,
        default=300.0,
        help="значение для свойства Duration в секундах (по умолчанию 300)",
    )
    test_export.add_argument(
        "--skip-anytype",
        action="store_true",
        help="только суммаризация, без обращения к Anytype",
    )
    test_export.set_defaults(func=cmd_test_export)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

