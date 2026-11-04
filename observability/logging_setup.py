"""Структурированное логирование в JSON (консоль + файл)."""

import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any

# Простое маскирование персональных данных в логах: e-mail и телефоны.
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"\+?\d[\d\s()-]{9,}\d")


def mask_pii(text: str) -> str:
    text = EMAIL_RE.sub("<EMAIL>", text)
    return PHONE_RE.sub("<PHONE>", text)


class JsonFormatter(logging.Formatter):
    """Каждая запись — одна JSON-строка: ts, level, event и произвольные поля."""

    def __init__(self, max_field_chars: int = 500):
        super().__init__()
        self.max_field_chars = max_field_chars

    def _prepare(self, value: Any) -> Any:
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        if isinstance(value, str):
            value = mask_pii(value)
            if len(value) > self.max_field_chars:
                extra = len(value) - self.max_field_chars
                value = f"{value[: self.max_field_chars]}...(+{extra} chars)"
        return value

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        fields = getattr(record, "fields", None) or {}
        for key, value in fields.items():
            entry[key] = self._prepare(value)
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO", file_path: str = "", max_field_chars: int = 500) -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers.clear()

    formatter = JsonFormatter(max_field_chars)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    if file_path:
        os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
        file_handler = logging.FileHandler(file_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # Встроенный access-лог Flask/werkzeug не в JSON — приглушаем, свои логи пишем сами.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    """Удобная обёртка: log_event(log, "cache_hit", request_id=..., key=...)."""
    logger.log(level, event, extra={"fields": fields})
