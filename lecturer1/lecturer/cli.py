"""
CLI-обёртка для macOS Shortcuts.

Использование:
    python -m lecturer start --source screen   # запускает запись в текущем процессе
    python -m lecturer start --source both &   # то же самое, но в фоне (как вызывает Shortcut)
    python -m lecturer stop                    # шлёт SIGINT фоновому процессу

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

from lecturer.audio.capture import AudioSourceMode
from lecturer.orchestrator import Pipeline
from lecturer.session import send_stop_signal


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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
