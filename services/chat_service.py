"""Бизнес-логика: связывает кеш, prompt builder, вызов LLM и постобработку в один сценарий."""

import logging
import time
from typing import Any, Callable, Dict, Tuple

from cache.ttl_cache import TTLCache, make_cache_key
from llm.errors import LLMResponseError, PermanentLLMError, TransientLLMError
from llm.postprocess import parse_and_clean
from llm.prompt_builder import PromptBuilder
from llm.retry import call_with_retry
from observability.logging_setup import log_event
from observability.timing import stage

log = logging.getLogger("services.chat")


class ChatService:
    def __init__(
        self,
        settings: Dict[str, Any],
        client: Any,
        cache: TTLCache,
        prompts: PromptBuilder,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.llm_cfg = settings["llm"]
        self.retry_cfg = settings["retry"]
        self.cache_cfg = settings["cache"]
        self.fallback_message = settings["fallback"]["message"]
        # В prod тексты промптов и ответов в лог не пишем — только длины (см. config/prod.yaml).
        self.log_payloads = settings["logging"]["log_payloads"]
        self.client = client
        self.cache = cache
        self.prompts = prompts
        self._sleep = sleep
        self.counters = {"requests": 0, "llm_calls": 0, "fallbacks": 0}

    # ------------------------------------------------------------------ public
    def handle(self, message: str, scenario: str, request_id: str) -> Tuple[int, Dict[str, Any]]:
        """Возвращает (HTTP-статус, тело ответа)."""
        self.counters["requests"] += 1
        timings: Dict[str, float] = {}

        # 1. Prompt Builder: system + user по сценарию.
        with stage(timings, "prompt_build"):
            messages = self.prompts.build(scenario, message)

        # 2. Кеш: ключ по всему, что уходит в модель (сообщения + провайдер + параметры генерации).
        cache_key = make_cache_key(
            self.cache_cfg["key_version"],
            messages=messages,
            provider=self.llm_cfg["provider"],
            model=self.llm_cfg["model"],
            temperature=self.llm_cfg["temperature"],
            max_tokens=self.llm_cfg["max_tokens"],
        )
        with stage(timings, "cache_lookup"):
            cached = self.cache.get(cache_key)
        if cached is not None:
            log_event(
                log, "cache_hit", request_id=request_id, cache_key=cache_key[:19], duration_ms=timings["cache_lookup"]
            )
            return 200, self._ok_body(request_id, scenario, cached, "cache", 0, timings)
        log_event(
            log, "cache_miss", request_id=request_id, cache_key=cache_key[:19], duration_ms=timings["cache_lookup"]
        )

        payload = (
            {"messages": messages} if self.log_payloads else {"prompt_chars": sum(len(m["content"]) for m in messages)}
        )
        log_event(
            log,
            "prompt_built",
            request_id=request_id,
            scenario=scenario,
            provider=self.llm_cfg["provider"],
            model=self.llm_cfg["model"],
            duration_ms=timings["prompt_build"],
            **payload,
        )

        # 3. Вызов LLM (timeout + ретраи) и постобработка с повторным запросом при нарушении формата.
        try:
            answer, attempts = self._ask_model(messages, request_id, timings)
        except (TransientLLMError, PermanentLLMError) as exc:
            log_event(
                log,
                "llm_unavailable",
                logging.ERROR,
                request_id=request_id,
                error_class=type(exc).__name__,
                error=str(exc),
            )
            return 503, self._fallback_body(request_id, scenario, "llm_unavailable", timings)
        except LLMResponseError as exc:
            log_event(log, "llm_invalid_response", logging.ERROR, request_id=request_id, error=str(exc))
            return 502, self._fallback_body(request_id, scenario, "llm_invalid_response", timings)

        # 4. Кешируем только успешные ответы — fallback в кеш не попадает.
        self.cache.set(cache_key, answer)
        log_event(
            log, "cache_store", request_id=request_id, cache_key=cache_key[:19], ttl_seconds=self.cache.ttl_seconds
        )
        return 200, self._ok_body(request_id, scenario, answer, "llm", attempts, timings)

    def stats(self) -> Dict[str, Any]:
        return {"counters": dict(self.counters), "cache": self.cache.stats()}

    # ----------------------------------------------------------------- private
    def _ask_model(self, messages, request_id: str, timings: Dict[str, float]) -> Tuple[str, int]:
        total_attempts = 0
        parse_tries = 1 + int(self.llm_cfg.get("parse_retries", 1))
        last_error: Exception = LLMResponseError("no response")

        for parse_try in range(1, parse_tries + 1):
            with stage(timings, "llm_call"):
                raw, attempts = call_with_retry(
                    lambda: self._call_client(messages),
                    max_attempts=self.retry_cfg["max_attempts"],
                    base_delay=self.retry_cfg["base_delay_seconds"],
                    max_delay=self.retry_cfg["max_delay_seconds"],
                    request_id=request_id,
                    sleep=self._sleep,
                )
            total_attempts += attempts
            payload = {"raw_response": raw} if self.log_payloads else {"response_chars": len(raw or "")}
            log_event(
                log,
                "llm_response",
                request_id=request_id,
                attempts=attempts,
                duration_ms=timings["llm_call"],
                **payload,
            )

            try:
                with stage(timings, "postprocess"):
                    answer = parse_and_clean(raw, max_chars=self.llm_cfg["max_answer_chars"])
                log_event(
                    log,
                    "postprocess_ok",
                    request_id=request_id,
                    answer_chars=len(answer),
                    duration_ms=timings["postprocess"],
                )
                return answer, total_attempts
            except LLMResponseError as exc:
                last_error = exc
                log_event(
                    log,
                    "postprocess_failed",
                    logging.WARNING,
                    request_id=request_id,
                    try_number=parse_try,
                    of=parse_tries,
                    error=str(exc),
                )

        raise last_error

    def _call_client(self, messages) -> str:
        self.counters["llm_calls"] += 1
        return self.client.complete(messages)

    def _ok_body(self, request_id, scenario, answer, source, attempts, timings) -> Dict[str, Any]:
        return {
            "request_id": request_id,
            "scenario": scenario,
            "answer": answer,
            "source": source,  # llm | cache | fallback
            "meta": {"model": self.llm_cfg["model"], "attempts": attempts, "timings_ms": timings},
        }

    def _fallback_body(self, request_id, scenario, code, timings) -> Dict[str, Any]:
        self.counters["fallbacks"] += 1
        return {
            "request_id": request_id,
            "scenario": scenario,
            "answer": self.fallback_message,
            "source": "fallback",
            "error": {"code": code, "message": self.fallback_message},
            "meta": {"model": self.llm_cfg["model"], "timings_ms": timings},
        }
