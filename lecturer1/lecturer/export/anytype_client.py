"""
Экспорт готового конспекта в Anytype через MCP-сервер @anyproto/anytype-mcp.

Формат properties подтверждён по официальной документации Anytype API
(https://developers.anytype.io/docs/guides/get-started/objects/):
properties — это СПИСОК объектов вида {"key": <ключ свойства>, "<формат>": значение},
например {"key": "done", "checkbox": true} или {"key": "prop_date", "date": "2025-10-13T12:34:56Z"}.
Для select/multi_select значение — строка/список строк с tag key (или tag id)
уже существующих опций свойства (их нужно один раз завести в самом Anytype).

Ключи ваших конкретных свойств (source_type, audio_source, duration,
recorded_at, transcript) Anytype генерирует автоматически из названия при
создании — они настраиваются в config.py (ANYTYPE_PROP_*) и МОГУТ не
совпадать с default-значениями. Проверить реальные ключи:
GET /v1/spaces/{space_id}/properties, либо в самом Anytype: Settings ->
Properties -> клик на свойство показывает его key.
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from lecturer.config import AnytypeConfig, settings

logger = logging.getLogger(__name__)


@dataclass
class LectureNoteProperties:
    source_type: str = "Lecture"       # должен совпадать с key/названием опции select
    audio_source: str = "Screen"       # screen | mic | both -> опция multi_select
    duration_sec: int = 0
    recorded_at: str = ""              # ISO 8601, например "2026-09-08T14:00:00Z"


class AnytypeExporter:
    def __init__(self, cfg: AnytypeConfig = settings.anytype):
        self.cfg = cfg

    @staticmethod
    def _extract_title(body: str, fallback: str) -> str:
        match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        return match.group(1).strip() if match else fallback

    def _build_properties(self, properties: LectureNoteProperties | None) -> list[dict]:
        if not properties or self.cfg.type_key == "page":
            return []

        return [
            {"key": self.cfg.prop_source_type, "select": properties.source_type},
            {"key": self.cfg.prop_audio_source, "multi_select": [properties.audio_source]},
            {"key": self.cfg.prop_duration, "number": properties.duration_sec},
            {"key": self.cfg.prop_recorded_at, "date": properties.recorded_at},
        ]

    async def export_markdown(
        self,
        summary_path: str | Path,
        transcript_path: str | Path | None = None,
        title: str | None = None,
        properties: LectureNoteProperties | None = None,
        log_file: str | Path | None = None,
    ) -> dict:
        """Читает markdown-файл конспекта и создаёт объект в Anytype через MCP.

        Если передан transcript_path и cfg.attach_transcript_file включён,
        файл сначала загружается через Files API, а его file-object-id
        записывается в properties[prop_transcript].
        """

        if not self.cfg.api_key or not self.cfg.space_id:
            raise RuntimeError(
                "ANYTYPE_API_KEY / ANYTYPE_SPACE_ID не заданы. "
                "Экспорт в Anytype пропущен — проверьте .env."
            )

        import json
        import os

        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        summary_path = Path(summary_path)
        if not summary_path.exists():
            raise FileNotFoundError(f"Markdown-файл не найден: {summary_path}")

        body = summary_path.read_text(encoding="utf-8")
        if not body.strip():
            raise ValueError(f"Markdown-файл пустой: {summary_path}")

        title = title or self._extract_title(body, fallback=summary_path.stem)

        headers = json.dumps(
            {
                "Authorization": f"Bearer {self.cfg.api_key}",
                "Anytype-Version": self.cfg.api_version,
            }
        )
        server_params = StdioServerParameters(
            command="npx",
            args=["-y", "@anyproto/anytype-mcp"],
            env={**os.environ, "OPENAPI_MCP_HEADERS": headers},
        )

        # npx/@anyproto/anytype-mcp печатает в stderr полный реестр
        # инструментов при старте — по умолчанию уводим это в лог-файл
        # сессии, чтобы не засорять терминал.
        errlog_path = Path(log_file) if log_file else Path.cwd() / "anytype_mcp.log"
        errlog_file = open(errlog_path, "a", encoding="utf-8") if self.cfg.quiet_mcp_logs else None

        try:
            logger.info("Запускаю Anytype MCP...")
            kwargs = {"errlog": errlog_file} if errlog_file else {}
            async with stdio_client(server_params, **kwargs) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    logger.info("MCP-сессия готова.")

                    api_properties = self._build_properties(properties)

                    if transcript_path and self.cfg.attach_transcript_file and self.cfg.type_key != "page":
                        file_id = await self._upload_file(session, Path(transcript_path))
                        if file_id:
                            api_properties.append({"key": self.cfg.prop_transcript, "files": [file_id]})
                        else:
                            logger.warning(
                                "Не удалось прикрепить transcript-файл (см. %s), конспект всё равно будет создан.",
                                errlog_path,
                            )

                    result = await self._create_object(session, title, body, api_properties)
                    logger.info("Объект создан в Anytype: %s", title)
                    return result
        finally:
            if errlog_file:
                errlog_file.close()

    async def _upload_file(self, session, file_path: Path) -> str | None:
        """
        Загружает файл через сгенерированный из OpenAPI MCP-инструмент.
        Точная схема аргументов этого инструмента не документирована
        публично, поэтому: 1) находим инструмент по имени/пути, 2) логируем
        его inputSchema (для отладки, в тот же файл, что и errlog),
        3) пробуем несколько правдоподобных наборов аргументов по очереди.
        Если ни один не сработал — пропускаем прикрепление файла, не роняя
        весь экспорт целиком.
        """

        try:
            tools_result = await session.list_tools()
        except Exception as e:
            logger.warning("Не удалось получить список инструментов MCP: %s", e)
            return None

        upload_tool = None
        for tool in tools_result.tools:
            name = (tool.name or "").lower()
            desc = (tool.description or "").lower()
            if "upload" in name or ("file" in name and "upload" in desc):
                upload_tool = tool
                break

        if upload_tool is None:
            logger.warning("Инструмент загрузки файла не найден в реестре MCP — Transcript не будет прикреплён.")
            return None

        logger.info("Инструмент загрузки файла: %s, схема: %s", upload_tool.name, upload_tool.inputSchema)

        file_bytes = file_path.read_bytes()
        b64_content = base64.b64encode(file_bytes).decode("ascii")

        candidate_args_list = [
            {"space_id": self.cfg.space_id, "path": str(file_path)},
            {"space_id": self.cfg.space_id, "file_path": str(file_path)},
            {"space_id": self.cfg.space_id, "file": str(file_path)},
            {"space_id": self.cfg.space_id, "file": b64_content, "filename": file_path.name},
            {"space_id": self.cfg.space_id, "content": b64_content, "filename": file_path.name},
        ]

        for args in candidate_args_list:
            try:
                result = await session.call_tool(upload_tool.name, arguments=args)
                data = self._unwrap(result)
                file_id = data.get("object", {}).get("id") or data.get("id") or data.get("file_id")
                if file_id:
                    logger.info("Файл транскрипта загружен, id=%s", file_id)
                    return file_id
            except Exception as e:
                logger.debug("Попытка загрузки файла с args=%s не удалась: %s", args, e)
                continue

        logger.warning(
            "Ни один вариант аргументов не подошёл для %s. Проверьте inputSchema в логе и поправьте _upload_file.",
            upload_tool.name,
        )
        return None

    async def _create_object(self, session, title: str, body: str, api_properties: list[dict]) -> dict:
        args = {
            "space_id": self.cfg.space_id,
            "type_key": self.cfg.type_key,
            "name": title,
            "body": body,
        }
        if api_properties:
            args["properties"] = api_properties

        try:
            result = await session.call_tool("API-create-object", arguments=args)
            return self._unwrap(result)
        except Exception as e:
            if self.cfg.type_key == "page":
                raise
            logger.warning(
                "Создание объекта типа '%s' со свойствами не удалось (%s). "
                "Повторяю с дефолтным типом 'page' без properties.",
                self.cfg.type_key,
                e,
            )
            fallback_args = {
                "space_id": self.cfg.space_id,
                "type_key": "page",
                "name": title,
                "body": body,
            }
            result = await session.call_tool("API-create-object", arguments=fallback_args)
            return self._unwrap(result)

    @staticmethod
    def _unwrap(result) -> dict:
        import json as _json

        for content in result.content:
            if hasattr(content, "text"):
                try:
                    return _json.loads(content.text)
                except _json.JSONDecodeError:
                    return {"raw": content.text}
        return {"raw": repr(result)}
