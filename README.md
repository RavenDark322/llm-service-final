# llm-service-final — end-to-end LLM-сервис

[![CI](https://github.com/RavenDark322/llm-service-final/actions/workflows/ci.yml/badge.svg)](https://github.com/RavenDark322/llm-service-final/actions/workflows/ci.yml)

HTTP-сервис на Flask, который отвечает на вопросы и делает краткие резюме текста с помощью LLM.
Работает с **GigaChat API** или любым **OpenAI-совместимым** API (OpenAI, OpenRouter, локальная Ollama).

Production-like элементы: слои с явной ответственностью, валидация, таймауты, ретраи с экспоненциальной задержкой,
fallback при недоступности модели, TTL-кеш, структурированные JSON-логи, конфигурации dev/prod через переменные окружения,
секреты только в `.env`, CI (линтер → тесты → сборка Docker-образа и smoke-тест).

## Сценарий

| | |
|---|---|
| **Вход** | `POST /chat` с JSON `{"message": "<текст до 1000 символов>", "scenario": "answer" \| "summarize"}` |
| **Выход** | JSON с ответом модели (`answer`), источником (`llm` / `cache` / `fallback`) и метаданными |
| **answer** | короткий ответ на вопрос |
| **summarize** | резюме присланного текста в 2–3 предложениях |

## Архитектура

```
 HTTP-клиент
     │  POST /chat
     ▼
┌────────────┐  400 + понятная ошибка
│  api/      │──────────────────────────► невалидный JSON, нет message, пусто, > 1000 символов, неизвестный сценарий
│  Flask     │
└─────┬──────┘
      ▼
┌────────────┐  cache hit ─────────────► 200, source=cache
│ services/  │  бизнес-логика: промпт → кеш → LLM → постобработка → кеш
│ ChatService│
└─────┬──────┘
      ▼
┌────────────┐  ┌──────────────────────┐
│ llm/       │  │ prompt_builder       │ system + user по сценарию + напоминание о формате
│            │  │ retry                │ до 3 попыток, 0.5 → 1 → 2 с + jitter, только временные ошибки
│            │  │ factory → клиент     │ GigaChat (OAuth + токен 30 мин) | OpenAI-совместимый
│            │  │ postprocess          │ контракт {"answer": ...}, очистка, обрезка
└─────┬──────┘  └──────────────────────┘
      ▼
  503 / 502 fallback ◄── модель недоступна / ответ не по контракту после переспроса
```

| Слой | Где | Ответственность |
|---|---|---|
| API | `api/routes.py`, `api/validation.py`, `api/app.py` | HTTP, валидация, коды ответов, `X-Request-ID`, JSON-ошибки вместо HTML |
| Бизнес-логика | `services/chat_service.py` | порядок шагов сценария, кеш, решение о fallback |
| LLM-модуль | `llm/` | промпт, клиенты провайдеров, таймауты, классы ошибок, ретраи |
| Постобработка | `llm/postprocess.py` | проверка контракта ответа, очистка текста |
| Кеш | `cache/ttl_cache.py` | потокобезопасный кеш в памяти с TTL, ключ по всем значимым параметрам |
| Конфигурация | `config/` | `config.yaml` + профиль `dev.yaml`/`prod.yaml` + переменные окружения |
| Наблюдаемость | `observability/` | JSON-логи, маскирование e-mail и телефонов, замер этапов |

**Точки обработки ошибок:** валидация (API, 400) → сетевые сбои и коды провайдера (`llm/http.py`, классы ошибок)
→ ретраи временных ошибок (`llm/retry.py`) → fallback (`services/chat_service.py`, 503/502)
→ последний рубеж для непредвиденных ошибок (`api/app.py`, 500 в JSON, сервис не падает).

## Структура

```
api/            HTTP-слой: маршруты, валидация, сборка приложения
services/       бизнес-логика сценария
llm/            промпты, клиенты GigaChat и OpenAI, ретраи, постобработка, ошибки
cache/          TTL-кеш
config/         config.yaml, dev.yaml, prod.yaml, загрузка настроек
observability/  логирование и замеры
tests/          54 автотеста (pytest)
scripts/        fake_llm_server.py — фейковый LLM для сбоев; smoke_test.py — ручная проверка
certs/          сюда кладётся сертификат НУЦ Минцифры (для GigaChat)
.github/        CI-пайплайн и шаблон pull request
main.py         точка входа
```

## Быстрый старт

Нужен Python 3.10+. Команды для Windows PowerShell; для macOS/Linux — в комментариях.

```powershell
git clone https://github.com/RavenDark322/llm-service-final.git
cd llm-service-final
python -m venv venv
venv\Scripts\Activate.ps1                 # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                    # macOS/Linux: cp .env.example .env
```

Заполните `.env` одним из вариантов:

**GigaChat** (по умолчанию) — ключ авторизации из [личного кабинета](https://developers.sber.ru/studio) и сертификат
«Russian Trusted Root CA» с [gosuslugi.ru/crt](https://www.gosuslugi.ru/crt) в папке `certs/` (см. `certs/README.md`):

```dotenv
LLM_PROVIDER=gigachat
GIGACHAT_CREDENTIALS=<ключ авторизации>
GIGACHAT_CA_BUNDLE=certs/russian_trusted_root_ca.cer
LLM_MODEL=GigaChat-2
```

**Ollama** (локально, бесплатно, без ключа):

```dotenv
LLM_PROVIDER=openai
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen2.5:3b
```

Запуск — одна команда:

```powershell
python main.py
```

Сервис слушает `http://127.0.0.1:8000`, логи — в консоль и `logs/service.log`.

## Конфигурация

Настройки собираются в три слоя: `config/config.yaml` → профиль `config/<APP_ENV>.yaml` → переменные окружения.
В коде нет URL, ключей и таймаутов — всё в конфигурации.

| Профиль | Логи | Тексты промптов и ответов в логах | Адрес |
|---|---|---|---|
| `dev` (по умолчанию) | DEBUG | да, с обрезкой и маскированием | 127.0.0.1 |
| `prod` | INFO | нет, только длины | 0.0.0.0 (контейнер) |

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `APP_ENV` | профиль: `dev` / `prod` | `dev` |
| `LLM_PROVIDER` | `gigachat` / `openai` | `gigachat` |
| `LLM_MODEL` | модель | `GigaChat-2` |
| `GIGACHAT_CREDENTIALS` | 🔒 ключ авторизации GigaChat | — |
| `GIGACHAT_CA_BUNDLE` | путь к сертификату НУЦ Минцифры | — |
| `GIGACHAT_SCOPE` | `GIGACHAT_API_PERS` / `_B2B` / `_CORP` | `GIGACHAT_API_PERS` |
| `LLM_BASE_URL` | адрес OpenAI-совместимого API | `https://api.openai.com/v1` |
| `LLM_API_KEY` | 🔒 ключ OpenAI-совместимого API | — |
| `LLM_TEMPERATURE`, `LLM_TIMEOUT` | параметры генерации, таймаут ответа (с) | `0.2`, `30` |
| `CACHE_TTL` | время жизни записи кеша (с) | `600` |
| `HOST`, `PORT`, `LOG_LEVEL` | сервер и логи | из профиля |

Некорректная конфигурация (неизвестный `APP_ENV` или провайдер, нечисловой `PORT`) останавливает запуск с понятным сообщением и кодом возврата 1.

**Секреты:** ключи только в `.env` (он в `.gitignore`), в репозитории — шаблон `.env.example`. В логи ключи не попадают — только признак `secret_configured: true/false`.

## API

### `POST /chat`

```json
{"message": "Что такое CI/CD?", "scenario": "answer"}
```

`scenario` необязателен (по умолчанию `answer`).

**200 — ответ модели:**

```json
{
  "request_id": "953d80ca1acd",
  "scenario": "answer",
  "answer": "CI/CD — практика автоматической сборки, тестирования и доставки изменений кода.",
  "source": "llm",
  "meta": {"model": "GigaChat-2", "attempts": 1, "timings_ms": {"cache_lookup": 0.0, "prompt_build": 0.0, "llm_call": 812.4, "postprocess": 0.1}}
}
```

Повторный идентичный запрос: тот же ответ, `"source": "cache"`, `"attempts": 0`, время — единицы миллисекунд.

**400 — ошибка ввода:**

```json
{"request_id": "8e7d4cc23b6e", "error": {"code": "empty_message", "message": "Поле \"message\" не должно быть пустым."}}
```

Коды: `invalid_json`, `invalid_body`, `missing_field`, `invalid_type`, `empty_message`, `message_too_long`, `unknown_scenario`.

**503 / 502 — fallback:**

```json
{
  "request_id": "6eb0387deb2d",
  "scenario": "answer",
  "answer": "Сервис временно недоступен, попробуйте позже.",
  "source": "fallback",
  "error": {"code": "llm_unavailable", "message": "Сервис временно недоступен, попробуйте позже."}
}
```

| Статус | Когда |
|---|---|
| 200 | ответ модели или из кеша |
| 400 | ошибка ввода — модель не вызывается |
| 413 | тело запроса больше 64 КБ |
| 502 | модель дважды ответила не по контракту `{"answer": ...}` |
| 503 | модель недоступна: сеть, таймаут, 5xx/429 после ретраев, неверный ключ, проблема с сертификатом |
| 500 | непредвиденная ошибка — в JSON, сервис продолжает работать |

### `GET /health` → `{"status": "ok"}`

### `GET /stats` → счётчики запросов, вызовов модели, fallback и статистика кеша (`hits`, `misses`, `hit_rate`)

### Примеры

```bash
curl -X POST http://127.0.0.1:8000/chat -H "Content-Type: application/json" \
  -d '{"message": "Что такое CI/CD?"}'
```

PowerShell:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/chat -Method Post `
  -ContentType "application/json; charset=utf-8" -Body '{"message": "Что такое CI/CD?"}'
```

Все сценарии разом (успех, кеш, summarize, пять ошибок ввода, `/stats`): `python scripts/smoke_test.py`.

## Устойчивость

| Ситуация | Реакция |
|---|---|
| Таймаут, обрыв сети, HTTP 5xx, 429 | до 3 попыток с паузой 0.5 → 1 → 2 с (+ jitter), затем 503 fallback |
| HTTP 400/401/403/404, нет ключа, ошибка сертификата | без повторов — сразу 503 fallback, причина в логе |
| Ответ модели не JSON / нет поля `answer` | один повторный запрос, затем 502 fallback |
| Access token GigaChat истёк (401) | один раз получаем новый токен и повторяем запрос |

Fallback-ответ в кеш не попадает — временный сбой не «застревает» на 10 минут.

**Контракт с моделью.** Модель обязана вернуть `{"answer": "..."}`. Требование стоит в системном промпте и дублируется
в конце сообщения пользователя (`format_reminder` в `config.yaml`): иначе фраза пользователя вроде «ответь одним
предложением» перебивает инструкцию о формате, и модель отвечает простым текстом.

## Кеш

Ключ — `v1:` + SHA-256 от всех сообщений, уходящих в модель (системный промпт сценария + сообщение пользователя с напоминанием о формате), провайдера, модели, temperature и max_tokens. Изменился любой промпт — изменился ключ.
TTL — 600 с, протухшие записи удаляются при обращении, при переполнении вытесняется самая старая.
В логах — `cache_hit` / `cache_miss` / `cache_store`, статистика — в `GET /stats`.
Смена промптов или логики → повысить `cache.key_version`, старые записи перестанут использоваться.

## Логи

Одна строка — одно событие в JSON, у каждого запроса сквозной `request_id`:

```json
{"ts": "…", "level": "INFO", "logger": "services.chat", "event": "cache_miss", "request_id": "953d80ca1acd", "cache_key": "v1:52757a6b308b27b8"}
{"ts": "…", "level": "INFO", "logger": "llm.gigachat", "event": "gigachat_token_refreshed"}
{"ts": "…", "level": "WARNING", "logger": "llm.retry", "event": "llm_retry", "request_id": "6eb0387deb2d", "attempt": 1, "next_delay_s": 0.5, "error": "HTTP 503 from LLM provider"}
{"ts": "…", "level": "ERROR", "logger": "services.chat", "event": "llm_unavailable", "request_id": "6eb0387deb2d", "error_class": "TransientLLMError"}
```

События: `request_received`, `request_validated` / `validation_failed`, `cache_hit` / `cache_miss`, `prompt_built`,
`llm_response`, `llm_retry`, `postprocess_ok` / `postprocess_failed`, `llm_unavailable`, `cache_store`, `request_completed`.
Вся история одного запроса: `Select-String -Path logs\service.log -Pattern "<request_id>"`.

## Тесты, линтер, CI

```powershell
pip install -r requirements-dev.txt
ruff check .              # линтер
ruff format --check .     # форматирование
python -m pytest -q       # 54 passed
```

Тесты не ходят в настоящий LLM: часть подменяет клиента заглушкой, часть поднимает `scripts/fake_llm_server.py`
и проверяет клиентов GigaChat и OpenAI через настоящий HTTP (коды ответов, таймауты, токен, сертификат, сквозной сценарий через API).

**CI** (`.github/workflows/ci.yml`) запускается на каждый push в `main` и каждый pull request:

1. **Lint** — `ruff check` и `ruff format --check`.
2. **Tests** — установка зависимостей, `pip check`, pytest на Python 3.10 и 3.12.
3. **Docker build + smoke test** — сборка образа, запуск контейнера с заведомо недоступным провайдером, проверки
   `/health` → 200, пустой запрос → 400, запрос к недоступной модели → 503 с `source: fallback`.

Версии зависимостей и actions зафиксированы — сборка воспроизводима.

## Docker

```powershell
docker build -t llm-service .
docker run --rm -p 8000:8000 --env-file .env -v "${PWD}/certs:/app/certs:ro" llm-service
```

Образ запускается в профиле `prod` от непривилегированного пользователя, с `HEALTHCHECK` на `/health`.

## Проверка сбоев без отключения интернета

```powershell
python scripts/fake_llm_server.py        # отдельный терминал, порт 8001
```

В `.env` задайте `GIGACHAT_CREDENTIALS=fake` и адреса с нужным режимом, например для «провайдер упал»:

```dotenv
GIGACHAT_AUTH_URL=http://127.0.0.1:8001/error/api/v2/oauth
GIGACHAT_BASE_URL=http://127.0.0.1:8001/error/v1
```

Режимы: `ok`, `error` (503), `flaky` (2 из 3 — 503), `slow` (таймаут, поставьте `LLM_TIMEOUT=3`), `badjson`, `unauthorized` (401).

Результаты проверки сценариев — в [TESTING.md](TESTING.md).
