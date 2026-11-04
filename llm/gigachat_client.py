"""Клиент GigaChat API.

Авторизация в два шага:
  1. ключ авторизации (GIGACHAT_CREDENTIALS) → POST {auth_url} → access token на 30 минут;
  2. access token → POST {base_url}/chat/completions (формат ответа как у OpenAI).
Токен кешируется и обновляется заранее; если он всё же истёк (401) — обновляем один раз.

Сертификаты серверов GigaChat выпущены НУЦ Минцифры — его корневой сертификат
«Russian Trusted Root CA» добавляется к стандартному набору certifi (GIGACHAT_CA_BUNDLE).
Проверка TLS не отключается.
"""

import hashlib
import logging
import ssl
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

import certifi

from llm.errors import PermanentLLMError
from llm.http import Verify, post_json
from llm.openai_client import extract_content, raise_for_status
from observability.logging_setup import log_event

log = logging.getLogger("llm.gigachat")
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class GigaChatClient:
    name = "gigachat"

    def __init__(self, llm_cfg: Dict[str, Any]):
        cfg = llm_cfg["gigachat"]
        self.auth_url = cfg["auth_url"]
        self.base_url = cfg["base_url"].rstrip("/")
        self.scope = cfg["scope"]
        self.credentials = cfg["credentials"]
        self.refresh_margin = cfg["token_refresh_margin_seconds"]
        self.model = llm_cfg["model"]
        self.temperature = llm_cfg["temperature"]
        self.max_tokens = llm_cfg["max_tokens"]
        self.timeout = (llm_cfg["connect_timeout_seconds"], llm_cfg["timeout_seconds"])
        # Ошибка сертификата не должна ронять сервис при старте: пишем в лог,
        # а каждый вызов модели получит PermanentLLMError → fallback-ответ.
        self._verify: Verify = True
        self._config_error = ""
        try:
            self._verify = build_verify(cfg["ca_bundle"])
        except PermanentLLMError as exc:
            self._config_error = str(exc)
            log_event(log, "gigachat_config_error", logging.ERROR, error=self._config_error)
        self._token = ""
        self._expires_at = 0.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ public
    def complete(self, messages: List[Dict[str, str]]) -> str:
        if self._config_error:
            raise PermanentLLMError(self._config_error)
        response = self._chat(messages, self._get_token())
        if response.status_code == 401:  # токен истёк между проверкой и запросом
            log_event(log, "gigachat_token_expired_retry")
            response = self._chat(messages, self._get_token(force=True))
        return extract_content(response)

    # ----------------------------------------------------------------- private
    def _chat(self, messages: List[Dict[str, str]], token: str):
        return post_json(
            f"{self.base_url}/chat/completions",
            self.timeout,
            verify=self._verify,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            json={
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            },
        )

    def _get_token(self, force: bool = False) -> str:
        if not self.credentials:
            raise PermanentLLMError("GIGACHAT_CREDENTIALS is not set")

        with self._lock:
            if not force and self._token and time.time() < self._expires_at - self.refresh_margin:
                return self._token

            response = post_json(
                self.auth_url,
                self.timeout,
                verify=self._verify,
                headers={
                    "Authorization": f"Basic {self.credentials}",
                    "RqUID": str(uuid.uuid4()),
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                data={"scope": self.scope},
            )
            raise_for_status(response)
            try:
                payload = response.json()
                token = payload["access_token"]
                expires_at = float(payload.get("expires_at", 0))
            except (ValueError, KeyError, TypeError) as exc:
                raise PermanentLLMError("unexpected OAuth response format") from exc

            if expires_at > 1e12:  # API отдаёт время в миллисекундах
                expires_at /= 1000
            self._token = token
            self._expires_at = expires_at or time.time() + 25 * 60
            log_event(log, "gigachat_token_refreshed")
            return token


def build_verify(ca_bundle: str) -> Verify:
    """Без GIGACHAT_CA_BUNDLE — стандартная проверка. С ним — certifi + сертификат НУЦ (PEM или DER)."""
    if not ca_bundle:
        return True
    path = Path(ca_bundle)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.exists():
        raise PermanentLLMError(f"CA bundle not found: {path.name}")
    raw = path.read_bytes()
    pem = raw.decode("ascii", "ignore") if b"-----BEGIN" in raw else ssl.DER_cert_to_PEM_cert(raw)
    content = Path(certifi.where()).read_text(encoding="utf-8") + "\n" + pem
    # Имя файла — по хешу содержимого: разные наборы сертификатов не перезаписывают друг друга
    # (иначе тесты или второй экземпляр сервиса могли бы подменить файл у работающего процесса).
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    bundle = Path(tempfile.gettempdir()) / f"gigachat_ca_bundle_{digest}.pem"
    if not bundle.exists():
        tmp = bundle.with_suffix(f".{uuid.uuid4().hex}.tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(bundle)  # атомарная замена: читатель никогда не увидит недописанный файл
    return str(bundle)
