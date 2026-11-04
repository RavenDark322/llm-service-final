"""Выбор клиента LLM по настройке llm.provider (переменная LLM_PROVIDER)."""

from typing import Any, Dict

from llm.gigachat_client import GigaChatClient
from llm.openai_client import OpenAICompatibleClient

PROVIDERS = {
    "gigachat": GigaChatClient,
    "openai": OpenAICompatibleClient,
}


def create_client(llm_cfg: Dict[str, Any]):
    """Оба клиента реализуют один интерфейс: complete(messages) -> str."""
    return PROVIDERS[llm_cfg["provider"]](llm_cfg)
