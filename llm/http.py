"""Общий HTTP-вызов для клиентов LLM: таймауты, проверка TLS, перевод сетевых сбоев в классы ошибок."""

from typing import Any, Tuple, Union

import requests

from llm.errors import PermanentLLMError, TransientLLMError

Verify = Union[bool, str]


def post_json(url: str, timeout: Tuple[float, float], verify: Verify = True, **kwargs: Any) -> requests.Response:
    try:
        return requests.post(url, timeout=timeout, verify=verify, **kwargs)
    except requests.exceptions.SSLError as exc:  # подкласс ConnectionError — ловим первым
        # Проблема с сертификатом — это настройка, а не временный сбой: не ретраим.
        raise PermanentLLMError("TLS certificate verification failed (check GIGACHAT_CA_BUNDLE)") from exc
    except requests.Timeout as exc:  # ConnectTimeout тоже подкласс ConnectionError — ловим раньше
        raise TransientLLMError(f"timeout after {timeout[1]}s") from exc
    except requests.ConnectionError as exc:
        raise TransientLLMError(f"connection error: {type(exc).__name__}") from exc
    except requests.RequestException as exc:
        raise PermanentLLMError(f"request failed: {type(exc).__name__}") from exc
