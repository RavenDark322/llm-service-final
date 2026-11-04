# Сборка:  docker build -t llm-service .
# Запуск:  docker run --rm -p 8000:8000 --env-file .env -v "${PWD}/certs:/app/certs:ro" llm-service
FROM python:3.12.8-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=prod

WORKDIR /app

# Сначала зависимости — этот слой кешируется и не пересобирается при правке кода.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Сервис работает не от root.
RUN useradd --create-home --uid 10001 app && mkdir -p /app/logs && chown -R app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

CMD ["python", "main.py"]
