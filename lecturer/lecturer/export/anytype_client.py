"""
Экспорт готового конспекта в Anytype через MCP-сервер @anyproto/anytype-mcp.

По умолчанию использует кастомный тип объекта "lecture_note" со свойствами
(tag, source_type, audio_source, duration_sec, recorded_at) — см. README о
том, как один раз создать этот тип в приложении Anytype. Если тип не
настроен (ANYTYPE_TYPE_KEY=page или сервер вернул ошибку по properties),
автоматически откатывается на дефолтный тип "page" без properties, чтобы
пайплайн не падал целиком из-за отсутствующей схемы в Anytype.

ВНИМАНИЕ: формат поля "properties" в теле запроса API-create-object ниже —
разумное предположение по аналогии с остальным API Anytype (см. структуру
properties в ответе create-object в исходном ноутбуке). Перед первым
реальным запуском стоит свериться с актуальной документацией Anytype API
(https://developers.anytype.io) и, при необходимости, поправить именно
этот метод — остальной пайплайн от точного формата не зависит.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from lecturer.config import AnytypeConfig, settings

logger = logging.getLogger(__name__)


@dataclass
class LectureNoteProperties:
    source_type: str = "Lecture"       # Lecture | Meeting | Video | Other
    audio_source: str = "Screen"       # Screen | Mic | Screen+Mic
    duration_sec: int = 0
    recorded_at: str = ""              # ISO 8601, например "2026-09-08T14:00:00Z"
    tags: list[str] | None = None

    def to_api_properties(self) -> dict:
        data = asdict(self)
        tags = data.pop("tags") or []
        return {
            "source_type": data["source_type"],
            "audio_source": data["audio_source"],
            "duration_sec": data["duration_sec"],
            "recorded_at": data["recorded_at"],
            "tag": tags,
        }


class AnytypeExporter:
    def __init__(self, cfg: AnytypeConfig = settings.anytype):
        self.cfg = cfg

    @staticmethod
    def _extract_title(body: str, fallback: str) -> str:
        match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        return match.group(1).strip() if match else fallback

    async def export_markdown(
        self,
        file_path: str | Path,
        title: str | None = None,
        properties: LectureNoteProperties | None = None,
    ) -> dict:
        """Читает markdown-файл и создаёт объект в Anytype через MCP."""

        if not self.cfg.api_key or not self.cfg.space_id:
            raise RuntimeError(
                "ANYTYPE_API_KEY / ANYTYPE_SPACE_ID не заданы. "
                "Экспорт в Anytype пропущен — проверьте .env."
            )

        # ленивый импорт: mcp — опциональная зависимость, не нужна для
        # остальных этапов пайплайна (запись/транскрипция/суммаризация)
        import json
        import os

        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"Markdown-файл не найден: {file_path}")

        body = file_path.read_text(encoding="utf-8")
        if not body.strip():
            raise ValueError(f"Markdown-файл пустой: {file_path}")

        title = title or self._extract_title(body, fallback=file_path.stem)

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

        logger.info("Запускаю Anytype MCP...")
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                logger.info("MCP-сессия готова.")

                result = await self._create_object(session, title, body, properties)
                logger.info("Объект создан в Anytype: %s", title)
                return result

    async def _create_object(self, session, title: str, body: str, properties: LectureNoteProperties | None) -> dict:
        args = {
            "space_id": self.cfg.space_id,
            "type_key": self.cfg.type_key,
            "name": title,
            "body": body,
        }
        if properties and self.cfg.type_key != "page":
            args["properties"] = properties.to_api_properties()

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
