"""Автотесты сценариев: python -m pytest -q

Реальный LLM не вызывается: клиент подменяется заглушками, паузы ретраев — no-op.
"""

import json

import pytest

from api.app import create_app
from cache.ttl_cache import TTLCache, make_cache_key
from config.settings import load_settings
from llm.errors import LLMResponseError, PermanentLLMError, TransientLLMError
from llm.postprocess import parse_and_clean


class StubClient:
    """Заглушка LLM: отдаёт ответы/ошибки из списка по очереди (последний элемент повторяется)."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def complete(self, messages):
        self.calls += 1
        outcome = self.outcomes[min(self.calls, len(self.outcomes)) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


OK = json.dumps({"answer": "  Короткий   ответ.  "}, ensure_ascii=False)


@pytest.fixture
def make_client_app():
    def _make(stub):
        settings = load_settings("dev", load_env_file=False)
        app = create_app(settings, client=stub, sleep=lambda _: None)
        return app.test_client()

    return _make


# ---------- корректный запрос ----------
def test_valid_request_returns_clean_answer(make_client_app):
    client = make_client_app(StubClient(OK))
    resp = client.post("/chat", json={"message": "Что такое DevOps?"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["answer"] == "Короткий ответ."  # постобработка убрала лишние пробелы
    assert body["source"] == "llm"
    assert "X-Request-ID" in resp.headers


# ---------- ошибки ввода ----------
@pytest.mark.parametrize(
    "payload, code",
    [
        ({}, "missing_field"),
        ({"message": ""}, "empty_message"),
        ({"message": "   "}, "empty_message"),
        ({"message": 123}, "invalid_type"),
        ({"message": "x" * 1001}, "message_too_long"),
        ({"message": "ok", "scenario": "poem"}, "unknown_scenario"),
        (["message"], "invalid_body"),
    ],
)
def test_validation_errors_return_400(make_client_app, payload, code):
    stub = StubClient(OK)
    client = make_client_app(stub)
    resp = client.post("/chat", json=payload)
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == code
    assert stub.calls == 0  # до модели некорректный запрос не доходит


def test_broken_json_returns_400(make_client_app):
    client = make_client_app(StubClient(OK))
    resp = client.post("/chat", data='{"message": "Привет" "user_id": 123}', content_type="application/json")
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "invalid_json"


def test_message_of_max_length_is_accepted(make_client_app):
    client = make_client_app(StubClient(OK))
    assert client.post("/chat", json={"message": "x" * 1000}).status_code == 200


# ---------- кеш ----------
def test_identical_request_served_from_cache(make_client_app):
    stub = StubClient(OK)
    client = make_client_app(stub)
    first = client.post("/chat", json={"message": "Повтори"}).get_json()
    second = client.post("/chat", json={"message": "Повтори"}).get_json()
    assert first["source"] == "llm" and second["source"] == "cache"
    assert second["answer"] == first["answer"]
    assert stub.calls == 1
    stats = client.get("/stats").get_json()["cache"]
    assert stats["hits"] == 1 and stats["misses"] == 1


def test_different_scenario_is_not_a_cache_hit(make_client_app):
    stub = StubClient(OK)
    client = make_client_app(stub)
    client.post("/chat", json={"message": "Текст"})
    resp = client.post("/chat", json={"message": "Текст", "scenario": "summarize"})
    assert resp.get_json()["source"] == "llm"  # другой системный промпт -> другой ключ
    assert stub.calls == 2


def test_cache_key_depends_on_model_params():
    base = {"message": "a", "system_prompt": "s", "model": "m", "temperature": 0.2, "max_tokens": 100}
    assert make_cache_key("v1", **base) == make_cache_key("v1", **base)
    assert make_cache_key("v1", **base) != make_cache_key("v1", **{**base, "temperature": 0.9})
    assert make_cache_key("v1", **base) != make_cache_key("v1", **{**base, "model": "other"})
    assert make_cache_key("v1", **base) != make_cache_key("v2", **base)


def test_cache_entry_expires_after_ttl():
    now = {"t": 0.0}
    cache = TTLCache(ttl_seconds=600, clock=lambda: now["t"])
    cache.set("k", "v")
    now["t"] = 599
    assert cache.get("k") == "v"
    now["t"] = 600
    assert cache.get("k") is None


def test_fallback_is_not_cached(make_client_app):
    stub = StubClient(TransientLLMError("down"), TransientLLMError("down"), TransientLLMError("down"), OK)
    client = make_client_app(stub)
    assert client.post("/chat", json={"message": "Q"}).get_json()["source"] == "fallback"
    assert client.post("/chat", json={"message": "Q"}).get_json()["source"] == "llm"


# ---------- устойчивость ----------
def test_network_failure_gives_fallback_after_3_attempts(make_client_app):
    stub = StubClient(TransientLLMError("connection error"))
    client = make_client_app(stub)
    resp = client.post("/chat", json={"message": "Привет"})
    assert resp.status_code == 503
    body = resp.get_json()
    assert body["source"] == "fallback"
    assert body["answer"] == "Сервис временно недоступен, попробуйте позже."
    assert stub.calls == 3


def test_transient_error_recovered_by_retry(make_client_app):
    stub = StubClient(TransientLLMError("HTTP 503"), TransientLLMError("timeout"), OK)
    client = make_client_app(stub)
    resp = client.post("/chat", json={"message": "Привет"})
    assert resp.status_code == 200
    assert resp.get_json()["meta"]["attempts"] == 3


def test_permanent_error_is_not_retried(make_client_app):
    stub = StubClient(PermanentLLMError("HTTP 401"))
    client = make_client_app(stub)
    resp = client.post("/chat", json={"message": "Привет"})
    assert resp.status_code == 503
    assert stub.calls == 1  # 401 повтором не лечится


def test_invalid_model_json_is_reasked_then_fallback(make_client_app):
    stub = StubClient("Вот ответ без JSON")
    client = make_client_app(stub)
    resp = client.post("/chat", json={"message": "Привет"})
    assert resp.status_code == 502
    assert resp.get_json()["error"]["code"] == "llm_invalid_response"
    assert stub.calls == 2  # 1 попытка + 1 повторный запрос


def test_invalid_model_json_recovered_on_reask(make_client_app):
    stub = StubClient("не JSON", OK)
    client = make_client_app(stub)
    assert client.post("/chat", json={"message": "Привет"}).status_code == 200


# ---------- постобработка ----------
def test_postprocess_accepts_code_fence():
    assert parse_and_clean('```json\n{"answer": "Да"}\n```', max_chars=100) == "Да"


@pytest.mark.parametrize("raw", ["", "   ", "text", '{"text": "x"}', '{"answer": "  "}', '{"answer": 5}'])
def test_postprocess_rejects_contract_violations(raw):
    with pytest.raises(LLMResponseError):
        parse_and_clean(raw, max_chars=100)


# ---------- служебное ----------
def test_unknown_route_returns_json_404(make_client_app):
    resp = make_client_app(StubClient(OK)).get("/nope")
    assert resp.status_code == 404
    assert resp.is_json


def test_long_answer_is_truncated():
    answer = parse_and_clean(json.dumps({"answer": "слово " * 100}), max_chars=50)
    assert len(answer) <= 51 and answer.endswith("…")


# ---------- промпт ----------
def test_format_reminder_is_last_instruction_for_model():
    from llm.prompt_builder import PromptBuilder

    builder = PromptBuilder({"answer": {"system_prompt": "sys"}}, "Верни JSON.")
    messages = builder.build("answer", "Что такое CI? Ответь одним предложением.")
    assert messages[0] == {"role": "system", "content": "sys"}
    assert messages[1]["content"].startswith("Что такое CI?")
    assert messages[1]["content"].endswith("Верни JSON.")


def test_reminder_from_config_reaches_model(make_client_app):
    seen = []

    class Recorder(StubClient):
        def complete(self, messages):
            seen.append(messages)
            return super().complete(messages)

    make_client_app(Recorder(OK)).post("/chat", json={"message": "Привет"})
    assert '{"answer"' in seen[0][1]["content"]
