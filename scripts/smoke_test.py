"""Ручная проверка запущенного сервиса: успех, кеш, ошибки ввода, /stats (Windows, macOS, Linux).

Запуск:  python scripts/smoke_test.py              (сервис должен быть запущен: python main.py)
         python scripts/smoke_test.py --url http://127.0.0.1:8000
"""

import argparse
import json
import time

import requests

QUESTION = "Что такое CI/CD? Ответь в одном предложении."

CASES = [
    ("1. Корректный запрос", {"message": QUESTION}),
    ("2. Повторный идентичный запрос (ожидаем source=cache)", {"message": QUESTION}),
    (
        "3. Сценарий summarize",
        {
            "message": "Docker упаковывает приложение вместе с зависимостями в образ. "
            "Из образа запускаются контейнеры, которые изолированы друг от друга.",
            "scenario": "summarize",
        },
    ),
    ("4. Нет поля message", {"text": "Привет"}),
    ("5. Пустое сообщение", {"message": "   "}),
    ("6. Слишком длинный текст (1001 символ)", {"message": "x" * 1001}),
    ("7. Неизвестный сценарий", {"message": "Привет", "scenario": "poem"}),
]


def show(title: str, response: requests.Response, elapsed: float) -> None:
    print(f"\n=== {title}")
    print(f"HTTP {response.status_code}  ({elapsed:.0f} ms)")
    try:
        body = response.json()
        body.get("meta", {}).pop("timings_ms", None)
        print(json.dumps(body, ensure_ascii=False, indent=2))
    except ValueError:
        print(response.text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    url = parser.parse_args().url.rstrip("/")

    for title, payload in CASES:
        started = time.perf_counter()
        resp = requests.post(f"{url}/chat", json=payload, timeout=120)
        show(title, resp, (time.perf_counter() - started) * 1000)

    started = time.perf_counter()
    resp = requests.post(
        f"{url}/chat",
        data='{"message": "Привет" "user_id": 123}',
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    show("8. Битый JSON в теле запроса", resp, (time.perf_counter() - started) * 1000)

    print("\n=== /stats")
    print(json.dumps(requests.get(f"{url}/stats", timeout=10).json(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
