"""Загрузка конфигурации: config.yaml → профиль окружения (APP_ENV) → переменные окружения.

Приоритет (от слабого к сильному):
  1. config/config.yaml        — значения по умолчанию;
  2. config/<APP_ENV>.yaml     — профиль окружения: dev (по умолчанию) или prod;
  3. переменные окружения/.env — точечные переопределения и секреты.
"""

import copy
import os
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
ALLOWED_ENVS = ("dev", "prod")

# переменная окружения -> (путь в конфиге, приведение типа)
ENV_OVERRIDES: Dict[str, Tuple[Tuple[str, ...], Callable[[str], Any]]] = {
    "LLM_PROVIDER": (("llm", "provider"), str),
    "LLM_MODEL": (("llm", "model"), str),
    "LLM_TEMPERATURE": (("llm", "temperature"), float),
    "LLM_TIMEOUT": (("llm", "timeout_seconds"), float),
    "LLM_BASE_URL": (("llm", "openai", "base_url"), str),
    "GIGACHAT_AUTH_URL": (("llm", "gigachat", "auth_url"), str),
    "GIGACHAT_BASE_URL": (("llm", "gigachat", "base_url"), str),
    "GIGACHAT_SCOPE": (("llm", "gigachat", "scope"), str),
    "CACHE_TTL": (("cache", "ttl_seconds"), float),
    "HOST": (("server", "host"), str),
    "PORT": (("server", "port"), int),
    "LOG_LEVEL": (("logging", "level"), str),
}


class ConfigError(Exception):
    """Некорректная конфигурация — сервис не должен стартовать."""


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Рекурсивно накладывает override на base, не меняя исходные словари."""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _read_yaml(path: Path) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _set_path(settings: Dict[str, Any], path: Tuple[str, ...], value: Any) -> None:
    node = settings
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value


def load_settings(env: Optional[str] = None, load_env_file: bool = True) -> Dict[str, Any]:
    if load_env_file:
        load_dotenv(PROJECT_ROOT / ".env")

    env = (env or os.getenv("APP_ENV") or "dev").strip().lower()
    if env not in ALLOWED_ENVS:
        raise ConfigError(f"APP_ENV={env!r}: допустимы {', '.join(ALLOWED_ENVS)}")

    settings = _read_yaml(CONFIG_DIR / "config.yaml")
    settings = deep_merge(settings, _read_yaml(CONFIG_DIR / f"{env}.yaml"))
    settings["env"] = env

    for env_name, (path, cast) in ENV_OVERRIDES.items():
        raw = os.getenv(env_name)
        if raw not in (None, ""):
            try:
                _set_path(settings, path, cast(raw))
            except ValueError as exc:
                raise ConfigError(f"{env_name}={raw!r}: неверный формат") from exc

    # Секреты — только из окружения, в YAML их нет и в логи они не попадают.
    settings["llm"]["openai"]["api_key"] = os.getenv("LLM_API_KEY", "")
    settings["llm"]["gigachat"]["credentials"] = os.getenv("GIGACHAT_CREDENTIALS", "")
    settings["llm"]["gigachat"]["ca_bundle"] = os.getenv("GIGACHAT_CA_BUNDLE", "")

    provider = settings["llm"]["provider"]
    if provider not in ("gigachat", "openai"):
        raise ConfigError(f"LLM_PROVIDER={provider!r}: допустимы gigachat, openai")

    log_file = settings["logging"].get("file")
    if log_file and not os.path.isabs(log_file):
        settings["logging"]["file"] = str(PROJECT_ROOT / log_file)

    return settings
