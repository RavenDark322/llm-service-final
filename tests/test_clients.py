"""Клиенты LLM через настоящий HTTP к фейковому серверу: коды ответов → классы ошибок, токен GigaChat."""

import json
import ssl

import certifi
import pytest

from api.app import create_app
from config.settings import load_settings
from llm.errors import PermanentLLMError, TransientLLMError
from llm.gigachat_client import GigaChatClient, build_verify
from llm.openai_client import OpenAICompatibleClient

MESSAGES = [{"role": "system", "content": "s"}, {"role": "user", "content": "Привет"}]


def llm_cfg(url, mode="ok", provider="openai", credentials="fake-key", timeout=1.0, ca_bundle=""):
    cfg = load_settings()["llm"]
    cfg.update(provider=provider, timeout_seconds=timeout, connect_timeout_seconds=1.0)
    cfg["openai"]["base_url"] = f"{url}/{mode}/v1"
    cfg["gigachat"].update(
        auth_url=f"{url}/{mode}/api/v2/oauth", base_url=f"{url}/{mode}/v1", credentials=credentials, ca_bundle=ca_bundle
    )
    return cfg


# ---------- OpenAI-совместимый клиент ----------
def test_openai_ok(fake):
    url, _ = fake
    content = OpenAICompatibleClient(llm_cfg(url)).complete(MESSAGES)
    assert json.loads(content)["answer"].startswith("[fake LLM]")


@pytest.mark.parametrize("mode, error", [("error", TransientLLMError), ("unauthorized", PermanentLLMError)])
def test_openai_status_codes_map_to_error_classes(fake, mode, error):
    url, _ = fake
    with pytest.raises(error):
        OpenAICompatibleClient(llm_cfg(url, mode)).complete(MESSAGES)


def test_openai_timeout_is_transient(fake):
    url, _ = fake
    with pytest.raises(TransientLLMError, match="timeout"):
        OpenAICompatibleClient(llm_cfg(url, "slow", timeout=0.5)).complete(MESSAGES)


def test_network_down_is_transient():
    # Linux сразу отвечает «connection refused», Windows повторяет SYN и упирается в connect timeout.
    # Оба случая — временная ошибка, текст сообщения зависит от ОС.
    cfg = llm_cfg("http://127.0.0.1:9")
    with pytest.raises(TransientLLMError, match="connection|timeout"):
        OpenAICompatibleClient(cfg).complete(MESSAGES)


# ---------- GigaChat ----------
def test_gigachat_ok_and_token_is_cached(fake):
    url, state = fake
    client = GigaChatClient(llm_cfg(url, provider="gigachat"))
    client.complete(MESSAGES)
    client.complete(MESSAGES)
    assert state["oauth_calls"] == 1 and state["chat_calls"] == 2


def test_gigachat_wrong_key_is_permanent(fake):
    url, state = fake
    with pytest.raises(PermanentLLMError):
        GigaChatClient(llm_cfg(url, "unauthorized", provider="gigachat")).complete(MESSAGES)
    assert state["chat_calls"] == 0


def test_gigachat_missing_key_does_not_call_api(fake):
    url, state = fake
    with pytest.raises(PermanentLLMError, match="GIGACHAT_CREDENTIALS"):
        GigaChatClient(llm_cfg(url, provider="gigachat", credentials="")).complete(MESSAGES)
    assert state["calls"] == 0


def test_gigachat_missing_ca_file_does_not_crash_on_start(fake):
    url, state = fake
    client = GigaChatClient(llm_cfg(url, provider="gigachat", ca_bundle="certs/nope.cer"))
    with pytest.raises(PermanentLLMError, match="CA bundle"):
        client.complete(MESSAGES)
    assert state["calls"] == 0


@pytest.mark.parametrize("as_der", [False, True])
def test_ca_bundle_accepts_pem_and_der(tmp_path, as_der):
    body = certifi.contents().split("-----END CERTIFICATE-----")[0]
    pem = "-----BEGIN" + body.split("-----BEGIN", 1)[1] + "-----END CERTIFICATE-----\n"
    cert = tmp_path / "russian_trusted_root_ca.cer"
    cert.write_bytes(ssl.PEM_cert_to_DER_cert(pem) if as_der else pem.encode())
    bundle = build_verify(str(cert))
    with open(bundle, encoding="utf-8") as f:
        assert f.read().count("BEGIN CERTIFICATE") > 100  # certifi + наш сертификат


# ---------- сквозной сценарий через HTTP API и GigaChat-клиент ----------
def make_app(url, mode, provider="gigachat"):
    settings = load_settings()
    settings["llm"] = llm_cfg(url, mode, provider=provider)
    return create_app(settings, sleep=lambda _: None).test_client()


def test_end_to_end_success_then_cache(fake):
    url, state = fake
    client = make_app(url, "ok")
    first = client.post("/chat", json={"message": "Что такое CI?"})
    second = client.post("/chat", json={"message": "Что такое CI?"})
    assert first.status_code == 200 and first.get_json()["source"] == "llm"
    assert second.get_json()["source"] == "cache"
    assert state["chat_calls"] == 1


def test_end_to_end_provider_down_gives_fallback(fake):
    url, state = fake
    resp = make_app(url, "error").post("/chat", json={"message": "Привет"})
    assert resp.status_code == 503 and resp.get_json()["source"] == "fallback"
    assert state["chat_calls"] == 3  # 1 попытка + 2 ретрая


def test_end_to_end_wrong_key_gives_fallback_without_retries(fake):
    url, state = fake
    resp = make_app(url, "unauthorized").post("/chat", json={"message": "Привет"})
    assert resp.status_code == 503 and resp.get_json()["error"]["code"] == "llm_unavailable"
    assert state["oauth_calls"] == 1 and state["chat_calls"] == 0


def test_different_ca_bundles_do_not_overwrite_each_other(tmp_path):
    """Регрессия: раньше все бандлы писались в один файл, и тесты подменяли его у работающего сервиса."""
    certs = certifi.contents().split("-----END CERTIFICATE-----")
    pems = ["-----BEGIN" + c.split("-----BEGIN", 1)[1] + "-----END CERTIFICATE-----\n" for c in certs[:2]]
    paths = []
    for i, pem in enumerate(pems):
        cert = tmp_path / f"ca{i}.pem"
        cert.write_text(pem, encoding="utf-8")
        paths.append(build_verify(str(cert)))
    assert paths[0] != paths[1]
    with open(paths[0], encoding="utf-8") as f:
        assert pems[0].strip() in f.read()  # первый файл не перезаписан вторым
    assert build_verify(str(tmp_path / "ca0.pem")) == paths[0]  # тот же сертификат — тот же файл
