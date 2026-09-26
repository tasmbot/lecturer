"""
Экспорт конспекта в Anytype через REST API v2.

API v2 работает на том же порту что и v1 (127.0.0.1:31009), но маршруты
начинаются с /v2/ и НЕ требуют заголовка Anytype-Version.

Ключевые отличия v2 от старого MCP-подхода:
  - Прямые HTTP-запросы вместо MCP-инструмента API-create-object
  - Тело запроса: {type, name, properties, markdown} — шорткат без blocks
  - Поле называется "type" (не "type_key"), "markdown" (не "body")
  - Шаблон: параметр "template" (не "template_id")
  - Properties: плоский dict {"ключ": значение}, не список объектов
  - Загрузка файлов: тот же эндпоинт /v1/spaces/{id}/files что и раньше
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import requests

from lecturer.config import AnytypeConfig, settings

logger = logging.getLogger(__name__)

_CANDIDATE_BASES = [
    "http://127.0.0.1:31009",
    "http://localhost:31009",
    "http://127.0.0.1:31007",
    "http://localhost:31007",
]


@dataclass
class LectureNoteProperties:
    source_type: str = "Lecture"    # key тега, не name
    audio_source: str = "Screen"    # key тега: screen | mic | screen+mic
    duration_sec: int = 0
    recorded_at: str = ""           # ISO 8601


class AnytypeClient:
    """
    Тонкий HTTP-клиент к локальному Anytype API v2.
    Находит живой порт автоматически при первом запросе.
    """

    def __init__(self, cfg: AnytypeConfig = settings.anytype):
        self.cfg = cfg
        self._base_url: str | None = cfg.api_base_url or None
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        })

    def _resolve_base(self) -> str:
        if self._base_url:
            return self._base_url

        candidates = [self.cfg.api_base_url] if self.cfg.api_base_url else []
        candidates += _CANDIDATE_BASES

        for base in candidates:
            if not base:
                continue
            try:
                r = self._session.get(f"{base}/v2/spaces", timeout=3)
                if r.status_code in (200, 400, 401, 403):
                    logger.info("Anytype API найден: %s", base)
                    self._base_url = base
                    return base
            except Exception:
                continue

        raise RuntimeError(
            f"Anytype API не отвечает ни на одном из портов {_CANDIDATE_BASES}. "
            "Убедитесь, что Anytype Desktop запущен. "
            "Или укажите ANYTYPE_API_BASE_URL в .env явно."
        )

    def _get(self, path: str, **kwargs) -> requests.Response:
        base = self._resolve_base()
        return self._session.get(f"{base}{path}", timeout=30, **kwargs)

    def _post(self, path: str, json: dict, **kwargs) -> requests.Response:
        base = self._resolve_base()
        return self._session.post(f"{base}{path}", json=json, timeout=60, **kwargs)

    def _post_multipart(self, path: str, files: dict, **kwargs) -> requests.Response:
        base = self._resolve_base()
        # multipart не должен иметь Content-Type: application/json
        headers = {k: v for k, v in self._session.headers.items() if k != "Content-Type"}
        return requests.post(f"{base}{path}", files=files, headers=headers, timeout=30, **kwargs)

    def create_object(
        self,
        name: str,
        markdown: str,
        type_key: str = "page",
        properties: dict | None = None,
        template_id: str | None = None,
    ) -> dict:
        """
        POST /v2/spaces/{space_id}/objects
        Шорткат-форма: {type, name, markdown, properties, template}
        """

        body: dict = {
            "type": type_key,
            "name": name,
            "markdown": markdown,
        }
        if properties:
            body["properties"] = properties
        if template_id:
            body["template"] = template_id

        # create_missing_options=true — создаёт отсутствующие опции select/multi_select
        # вместо ошибки "option does not exist"
        r = self._post(
            f"/v2/spaces/{self.cfg.space_id}/objects",
            json=body,
            params={"create_missing_options": "true"},
        )

        try:
            data = r.json()
        except Exception:
            raise RuntimeError(
                f"Anytype вернул не-JSON ответ (статус {r.status_code}): {r.text[:300]}"
            )

        if r.status_code not in (200, 201):
            # issues содержит точное поле и опцию которая не прошла
            issues = data.get("issues", [])
            raise RuntimeError(
                f"Ошибка создания объекта: {r.status_code} {data.get('message', data)}"
                + (f" | issues: {issues}" if issues else "")
            )

        return data

    def upload_file(self, file_path: Path) -> str | None:
        """
        POST /v1/spaces/{space_id}/files — загрузка файла.
        Эндпоинт файлов остался в v1, работает на том же порту.
        """

        try:
            with open(file_path, "rb") as f:
                r = self._post_multipart(
                    f"/v1/spaces/{self.cfg.space_id}/files",
                    files={"file": (file_path.name, f, "text/markdown")},
                )

            logger.debug("Upload %s -> %s: %r", file_path.name, r.status_code, r.text[:200])

            if not r.text.strip():
                logger.warning("Upload вернул пустой ответ (статус %s).", r.status_code)
                return None

            data = r.json()
            if r.status_code not in (200, 201):
                logger.warning(
                    "Ошибка загрузки файла: %s %s", r.status_code, data.get("message", data)
                )
                return None

            file_id = (
                data.get("object_id")
                or data.get("object", {}).get("id")
                or data.get("id")
            )
            if file_id:
                logger.info("Файл загружен: %s -> %s", file_path.name, file_id)
            else:
                logger.warning("Файл загружен, но id не найден в ответе: %s", data)
            return file_id

        except Exception as e:
            logger.warning(
                "Не удалось загрузить файл транскрипта: %s: %s", type(e).__name__, e
            )
            return None


class AnytypeExporter:
    def __init__(self, cfg: AnytypeConfig = settings.anytype):
        self.cfg = cfg
        self._client: AnytypeClient | None = None

    @property
    def client(self) -> AnytypeClient:
        if self._client is None:
            self._client = AnytypeClient(self.cfg)
        return self._client

    @staticmethod
    def _extract_title(body: str, fallback: str) -> str:
        match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        return match.group(1).strip() if match else fallback

    def _build_properties(self, props: LectureNoteProperties | None) -> dict | None:
        """
        API v2 принимает properties как плоский dict:
          {"source_type": ["lecture"], "audio_source": ["screen"], "duration": 1800}
        В отличие от v1 где это был список {"key": ..., "multi_select": [...]}
        """

        if not props or self.cfg.type_key == "page":
            return None

        result: dict = {
            self.cfg.prop_source_type: [props.source_type],
            self.cfg.prop_audio_source: [props.audio_source],
            self.cfg.prop_duration: props.duration_sec,
        }
        if props.recorded_at:
            result[self.cfg.prop_recorded_at] = props.recorded_at

        return result

    def export_markdown(
        self,
        summary_path: str | Path,
        transcript_path: str | Path | None = None,
        title: str | None = None,
        properties: LectureNoteProperties | None = None,
        log_file: str | Path | None = None,  # оставлен для совместимости сигнатуры
    ) -> dict:
        """
        Создаёт объект в Anytype через API v2.
        Синхронный — asyncio больше не нужен (убран MCP).
        """

        if not self.cfg.api_key or not self.cfg.space_id:
            raise RuntimeError(
                "ANYTYPE_API_KEY / ANYTYPE_SPACE_ID не заданы. Проверьте .env."
            )

        summary_path = Path(summary_path)
        if not summary_path.exists():
            raise FileNotFoundError(f"Markdown-файл не найден: {summary_path}")

        markdown = summary_path.read_text(encoding="utf-8")
        if not markdown.strip():
            raise ValueError(f"Markdown-файл пустой: {summary_path}")

        title = title or self._extract_title(markdown, fallback=summary_path.stem)
        api_properties = self._build_properties(properties)

        # Загружаем транскрипт и добавляем id в properties
        if (
            transcript_path
            and self.cfg.attach_transcript_file
            and self.cfg.type_key != "page"
            and api_properties is not None
        ):
            file_id = self.client.upload_file(Path(transcript_path))
            if file_id:
                api_properties[self.cfg.prop_transcript] = [file_id]
            else:
                logger.warning("Transcript не прикреплён — продолжаем без него.")

        # Создаём объект
        try:
            result = self.client.create_object(
                name=title,
                markdown=markdown,
                type_key=self.cfg.type_key,
                properties=api_properties,
                template_id=self.cfg.template_id or None,
            )
            logger.info("Объект создан в Anytype: %s", title)
            return result
        except Exception as e:
            if self.cfg.type_key == "page":
                raise
            logger.warning(
                "Создание объекта типа '%s' не удалось (%s). "
                "Повторяю с дефолтным типом 'page'.",
                self.cfg.type_key,
                e,
            )
            result = self.client.create_object(
                name=title,
                markdown=markdown,
                type_key="page",
            )
            logger.info("Объект создан как page: %s", title)
            return result