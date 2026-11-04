"""Конфигурация: профили dev/prod, переопределения из окружения, секреты, ошибки конфигурации."""

import pytest

from config.settings import ConfigError, deep_merge, load_settings


def test_dev_is_default_profile():
    settings = load_settings()
    assert settings["env"] == "dev"
    assert settings["logging"]["level"] == "DEBUG"
    assert settings["logging"]["log_payloads"] is True


def test_prod_profile_hides_payloads_and_listens_everywhere(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    settings = load_settings()
    assert settings["env"] == "prod"
    assert settings["logging"]["log_payloads"] is False
    assert settings["server"]["host"] == "0.0.0.0"
    assert settings["llm"]["model"] == "GigaChat-2"  # не переопределённое в профиле — из базы


def test_env_variables_override_files(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_TIMEOUT", "7.5")
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    settings = load_settings()
    assert settings["llm"]["provider"] == "openai"
    assert settings["llm"]["timeout_seconds"] == 7.5
    assert settings["server"]["port"] == 9000
    assert settings["llm"]["openai"]["base_url"] == "http://localhost:11434/v1"


def test_secrets_come_only_from_environment(monkeypatch):
    assert load_settings()["llm"]["gigachat"]["credentials"] == ""
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "secret-key")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    settings = load_settings()
    assert settings["llm"]["gigachat"]["credentials"] == "secret-key"
    assert settings["llm"]["openai"]["api_key"] == "sk-test"


@pytest.mark.parametrize("name, value", [("APP_ENV", "staging"), ("LLM_PROVIDER", "yandex"), ("PORT", "abc")])
def test_invalid_configuration_raises_config_error(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError):
        load_settings()


def test_deep_merge_keeps_unrelated_keys():
    base = {"a": {"x": 1, "y": 2}, "b": 3}
    merged = deep_merge(base, {"a": {"y": 20}})
    assert merged == {"a": {"x": 1, "y": 20}, "b": 3}
    assert base["a"]["y"] == 2  # исходный словарь не изменён
