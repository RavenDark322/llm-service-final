"""Сборка приложения: создаём компоненты pipeline и связываем их."""

import logging
import time
from typing import Any, Callable, Dict, Optional

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

from api.routes import create_blueprint
from cache.ttl_cache import TTLCache
from llm.factory import create_client
from llm.prompt_builder import PromptBuilder
from observability.logging_setup import log_event
from services.chat_service import ChatService

log = logging.getLogger("app")


def create_app(
    settings: Dict[str, Any], client: Optional[Any] = None, sleep: Callable[[float], None] = time.sleep
) -> Flask:
    """client и sleep можно подменить в тестах, в рабочем режиме создаются автоматически."""
    llm_cfg = settings["llm"]
    if client is None:
        client = create_client(llm_cfg)

    cache = TTLCache(settings["cache"]["ttl_seconds"], settings["cache"]["max_entries"])
    prompts = PromptBuilder(settings["scenarios"], settings.get("format_reminder", ""))
    service = ChatService(settings, client, cache, prompts, sleep=sleep)

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = settings["server"]["max_body_bytes"]
    app.json.ensure_ascii = False  # кириллица в ответах без \uXXXX
    app.register_blueprint(create_blueprint(service, settings))
    app.extensions["chat_service"] = service

    @app.errorhandler(HTTPException)
    def http_error(exc: HTTPException):
        # 404, 405, 413 и т. п. — тоже в JSON, а не HTML-страницей.
        return jsonify({"error": {"code": exc.name.lower().replace(" ", "_"), "message": exc.description}}), exc.code

    @app.errorhandler(Exception)
    def unexpected_error(exc: Exception):
        # Последний рубеж: любая непредвиденная ошибка логируется, сервис не падает.
        log.exception("unhandled_error", extra={"fields": {"error_class": type(exc).__name__}})
        return jsonify(
            {"error": {"code": "internal_error", "message": "Внутренняя ошибка сервиса. Попробуйте ещё раз позже."}}
        ), 500

    provider = llm_cfg["provider"]
    secret_configured = bool(
        llm_cfg["gigachat"]["credentials"] if provider == "gigachat" else llm_cfg["openai"]["api_key"]
    )
    endpoint = llm_cfg["gigachat"]["base_url"] if provider == "gigachat" else llm_cfg["openai"]["base_url"]
    # Секреты не логируем — только признак, задан ли ключ.
    log_event(
        log,
        "service_configured",
        env=settings["env"],
        provider=provider,
        endpoint=endpoint,
        model=llm_cfg["model"],
        temperature=llm_cfg["temperature"],
        timeout_s=llm_cfg["timeout_seconds"],
        secret_configured=secret_configured,
        cache_ttl_s=settings["cache"]["ttl_seconds"],
        retry_max_attempts=settings["retry"]["max_attempts"],
        log_payloads=settings["logging"]["log_payloads"],
    )

    is_local = any(h in endpoint for h in ("127.0.0.1", "localhost"))
    if not secret_configured and not is_local:
        hint = "GIGACHAT_CREDENTIALS" if provider == "gigachat" else "LLM_API_KEY"
        log_event(
            log, "secret_missing", logging.WARNING, hint=f"Задайте {hint} в .env, иначе каждый запрос получит fallback"
        )
    return app
