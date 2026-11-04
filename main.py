"""Точка входа: python main.py"""

import logging
import sys

import flask.cli

from api.app import create_app
from config.settings import ConfigError, load_settings
from observability.logging_setup import log_event, setup_logging


def main() -> None:
    try:
        settings = load_settings()
    except ConfigError as exc:
        # Битая конфигурация — понятное сообщение и код возврата 1, без traceback.
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        sys.exit(1)
    log_cfg = settings["logging"]
    setup_logging(log_cfg["level"], log_cfg["file"], log_cfg["max_field_chars"])

    app = create_app(settings)
    host, port = settings["server"]["host"], settings["server"]["port"]
    log_event(logging.getLogger("main"), "service_started", env=settings["env"], url=f"http://{host}:{port}")
    flask.cli.show_server_banner = lambda *args, **kwargs: None  # без баннера — логи остаются чистым JSON
    # threaded=True: несколько запросов обрабатываются параллельно (поэтому кеш потокобезопасный).
    app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    main()
