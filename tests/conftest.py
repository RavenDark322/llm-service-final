"""Общие фикстуры: изоляция от .env и окружения разработчика, фейковый LLM-сервер."""

import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from fake_llm_server import serve  # noqa: E402

ENV_VARS = (
    "APP_ENV",
    "LLM_PROVIDER",
    "LLM_MODEL",
    "LLM_TEMPERATURE",
    "LLM_TIMEOUT",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "GIGACHAT_CREDENTIALS",
    "GIGACHAT_CA_BUNDLE",
    "GIGACHAT_AUTH_URL",
    "GIGACHAT_BASE_URL",
    "GIGACHAT_SCOPE",
    "CACHE_TTL",
    "HOST",
    "PORT",
    "LOG_LEVEL",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Тесты не должны зависеть от локального .env и переменных окружения разработчика."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("config.settings.load_dotenv", lambda *a, **k: None)


@pytest.fixture(scope="session")
def fake_server():
    server, state = serve(port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", state
    server.shutdown()


@pytest.fixture
def fake(fake_server):
    """URL фейкового сервера и обнулённые счётчики вызовов."""
    url, state = fake_server
    for key in ("calls", "oauth_calls", "chat_calls", "flaky"):
        state[key] = 0
    return url, state
