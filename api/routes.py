"""API-слой: HTTP-эндпоинты, валидация, коды ответов, request_id."""

import logging
import time
import uuid

from flask import Blueprint, jsonify, request

from api.validation import validate_chat_request
from observability.logging_setup import log_event
from services.errors import ValidationError

log = logging.getLogger("api")


def create_blueprint(service, settings) -> Blueprint:
    bp = Blueprint("api", __name__)
    max_length = settings["validation"]["max_message_length"]
    default_scenario = settings["default_scenario"]

    @bp.post("/chat")
    def chat():
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        started = time.perf_counter()
        log_event(
            log,
            "request_received",
            request_id=request_id,
            method=request.method,
            path=request.path,
            remote_addr=request.remote_addr,
            content_length=request.content_length,
        )

        def finish(status, body):
            duration = round((time.perf_counter() - started) * 1000, 1)
            log_event(
                log,
                "request_completed",
                request_id=request_id,
                status=status,
                source=body.get("source"),
                error_code=(body.get("error") or {}).get("code"),
                duration_ms=duration,
            )
            response = jsonify(body)
            response.status_code = status
            response.headers["X-Request-ID"] = request_id
            return response

        # force=True: разбираем JSON даже без заголовка Content-Type; silent=True: битый JSON -> None
        payload = request.get_json(force=True, silent=True)
        if payload is None:
            log_event(log, "validation_failed", logging.WARNING, request_id=request_id, code="invalid_json")
            return finish(
                400,
                {
                    "request_id": request_id,
                    "error": {
                        "code": "invalid_json",
                        "message": 'Тело запроса не является корректным JSON. Пример: {"message": "Привет"}.',
                    },
                },
            )

        try:
            message, scenario = validate_chat_request(payload, max_length, service.prompts.scenarios, default_scenario)
        except ValidationError as exc:
            log_event(log, "validation_failed", logging.WARNING, request_id=request_id, code=exc.code)
            return finish(400, {"request_id": request_id, "error": {"code": exc.code, "message": exc.message}})

        text = {"message": message} if settings["logging"]["log_payloads"] else {}
        log_event(
            log, "request_validated", request_id=request_id, scenario=scenario, message_chars=len(message), **text
        )
        status, body = service.handle(message, scenario, request_id)
        return finish(status, body)

    @bp.get("/health")
    def health():
        return jsonify({"status": "ok"})

    @bp.get("/stats")
    def stats():
        """Мини-метрики: число запросов, вызовов LLM, fallback и статистика кеша (hit/miss)."""
        return jsonify(service.stats())

    return bp
