"""Ограниченные ретраи с экспоненциальной задержкой — только для временных ошибок."""

import logging
import random
import time
from typing import Callable, Tuple, TypeVar

from llm.errors import TransientLLMError
from observability.logging_setup import log_event

T = TypeVar("T")
log = logging.getLogger("llm.retry")


def call_with_retry(
    fn: Callable[[], T],
    *,
    max_attempts: int,
    base_delay: float,
    max_delay: float,
    request_id: str,
    sleep: Callable[[float], None] = time.sleep,
) -> Tuple[T, int]:
    """Вызывает fn() до max_attempts раз. Возвращает (результат, число попыток).

    - TransientLLMError (таймаут, обрыв, 5xx, 429) -> пауза base_delay * 2^(n-1) + jitter и повтор;
    - любые другие ошибки (PermanentLLMError, LLMResponseError) пробрасываются сразу — повтор не поможет.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn(), attempt
        except TransientLLMError as exc:
            if attempt >= max_attempts:
                log_event(
                    log, "llm_retries_exhausted", logging.ERROR, request_id=request_id, attempts=attempt, error=str(exc)
                )
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay += random.uniform(0, delay * 0.1)  # jitter: клиенты не ретраят синхронно
            log_event(
                log,
                "llm_retry",
                logging.WARNING,
                request_id=request_id,
                attempt=attempt,
                next_delay_s=round(delay, 2),
                error=str(exc),
            )
            sleep(delay)
