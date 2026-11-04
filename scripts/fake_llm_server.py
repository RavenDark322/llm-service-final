"""Фейковый LLM-сервер для проверки сценариев без настоящего ключа и без отключения интернета.

Понимает оба протокола сервиса:
  • OpenAI-совместимый:  POST /<режим>/v1/chat/completions
  • GigaChat:            POST /<режим>/api/v2/oauth  (токен) + POST /<режим>/v1/chat/completions

Запуск:  python scripts/fake_llm_server.py           (слушает http://127.0.0.1:8001)

Режим задаётся префиксом пути — чтобы переключить сценарий, достаточно поменять адреса в .env:
  ok            корректный ответ {"answer": ...}
  slow          ответ через 60 секунд (таймаут)
  error         всегда HTTP 503 (ретраи → fallback)
  flaky         2 ответа из 3 — HTTP 503 (ретрай спасает запрос)
  badjson       текст вместо JSON (ошибка постобработки → переспрос → fallback)
  unauthorized  HTTP 401 (неверный ключ, без ретраев)

Пример .env для GigaChat-протокола:
  LLM_PROVIDER=gigachat
  GIGACHAT_CREDENTIALS=fake
  GIGACHAT_AUTH_URL=http://127.0.0.1:8001/ok/api/v2/oauth
  GIGACHAT_BASE_URL=http://127.0.0.1:8001/ok/v1
"""

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Tuple

HOST, PORT = "127.0.0.1", 8001


def completion(content: str) -> Dict[str, Any]:
    return {
        "id": "fake-1",
        "object": "chat.completion",
        "model": "fake-model",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def make_handler(state: Dict[str, Any]):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: Any) -> None:
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):  # noqa: N802 — имя метода задаёт http.server
            mode = self.path.strip("/").split("/")[0]
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0)
            state["calls"] += 1

            if self.path.endswith("/oauth"):
                state["oauth_calls"] += 1
                if mode == "unauthorized" or not self.headers.get("Authorization", "").startswith("Basic "):
                    return self._send(401, {"code": 6, "message": "fake: credentials doesn't match"})
                return self._send(200, {"access_token": "fake-token", "expires_at": int((time.time() + 1800) * 1000)})

            if not self.path.endswith("/chat/completions"):
                return self._send(404, {"error": {"message": "unknown path"}})

            state["chat_calls"] += 1
            request = json.loads(raw or b"{}")
            user_text = request.get("messages", [{}])[-1].get("content", "")

            if mode == "ok":
                answer = f"[fake LLM] Получен запрос из {len(user_text)} символов: «{user_text[:60]}»."
                return self._send(200, completion(json.dumps({"answer": answer}, ensure_ascii=False)))
            if mode == "slow":
                time.sleep(60)
                return self._send(200, completion('{"answer": "слишком поздно"}'))
            if mode == "error":
                return self._send(503, {"error": {"message": "fake: service unavailable"}})
            if mode == "flaky":
                state["flaky"] += 1
                if state["flaky"] % 3 != 0:
                    return self._send(503, {"error": {"message": "fake: temporary failure"}})
                return self._send(200, completion('{"answer": "[fake LLM] ответ с третьей попытки"}'))
            if mode == "badjson":
                return self._send(200, completion("Конечно! Вот ваш ответ, но без JSON :)"))
            if mode == "unauthorized":
                return self._send(401, {"error": {"message": "fake: invalid api key"}})
            return self._send(404, {"error": {"message": "unknown mode"}})

        def log_message(self, fmt: str, *args: Any) -> None:
            if state.get("verbose"):
                print(f"[fake-llm] {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")

    return Handler


def serve(port: int = PORT, verbose: bool = False) -> Tuple[ThreadingHTTPServer, Dict[str, Any]]:
    """Создаёт сервер (port=0 — свободный порт, для тестов). Возвращает (server, state со счётчиками)."""
    state = {"calls": 0, "oauth_calls": 0, "chat_calls": 0, "flaky": 0, "verbose": verbose}
    server = ThreadingHTTPServer((HOST, port), make_handler(state))
    server.daemon_threads = True
    return server, state


if __name__ == "__main__":
    srv, _ = serve(verbose=True)
    print(f"Fake LLM server: http://{HOST}:{PORT}/<mode>/...  (modes: ok, slow, error, flaky, badjson, unauthorized)")
    srv.serve_forever()
