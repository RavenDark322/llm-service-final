"""Клиент для любого OpenAI-совместимого API (/chat/completions): OpenAI, OpenRouter, Ollama.

Один вызов с таймаутом; сбои переводятся в классы ошибок из llm/errors.py.
Повторы делает llm/retry.py.
"""

from typing import Any, Dict, List

import requests

from llm.errors import LLMResponseError, PermanentLLMError, TransientLLMError
from llm.http import post_json


class OpenAICompatibleClient:
    name = "openai"

    def __init__(self, llm_cfg: Dict[str, Any]):
        self.base_url = llm_cfg["openai"]["base_url"].rstrip("/")
        self.api_key = llm_cfg["openai"]["api_key"]
        self.model = llm_cfg["model"]
        self.temperature = llm_cfg["temperature"]
        self.max_tokens = llm_cfg["max_tokens"]
        self.timeout = (llm_cfg["connect_timeout_seconds"], llm_cfg["timeout_seconds"])

    def complete(self, messages: List[Dict[str, str]]) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        response = post_json(f"{self.base_url}/chat/completions", self.timeout, headers=headers, json=payload)
        return extract_content(response)


def raise_for_status(response: requests.Response) -> None:
    """429 и 5xx — временные ошибки (ретраим), остальные 4xx — постоянные (не ретраим)."""
    status = response.status_code
    if status == 429 or status >= 500:
        raise TransientLLMError(f"HTTP {status} from LLM provider")
    if status >= 400:
        # Тело ответа провайдера помогает понять причину (неверный ключ, модель), но обрезаем его.
        raise PermanentLLMError(f"HTTP {status} from LLM provider: {response.text[:200]}")


def extract_content(response: requests.Response) -> str:
    """Ответ в формате OpenAI: choices[0].message.content. Общий для OpenAI и GigaChat."""
    raise_for_status(response)
    try:
        return response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise LLMResponseError("unexpected response format from LLM API") from exc
