"""Валидация входных данных API-слоя: обязательные поля, типы, ограничения длины."""

from typing import Any, Iterable, Tuple

from services.errors import ValidationError


def validate_chat_request(
    payload: Any, max_length: int, scenarios: Iterable[str], default_scenario: str
) -> Tuple[str, str]:
    """Возвращает (message, scenario) или бросает ValidationError с понятным текстом."""
    if not isinstance(payload, dict):
        raise ValidationError("invalid_body", 'Тело запроса должно быть JSON-объектом, например {"message": "..."}.')

    if "message" not in payload:
        raise ValidationError("missing_field", 'Поле "message" обязательно.')

    message = payload["message"]
    if not isinstance(message, str):
        raise ValidationError("invalid_type", 'Поле "message" должно быть строкой.')

    message = message.strip()
    if not message:
        raise ValidationError("empty_message", 'Поле "message" не должно быть пустым.')

    if len(message) > max_length:
        raise ValidationError(
            "message_too_long",
            f"Текст слишком длинный: {len(message)} из допустимых {max_length} символов. Сократите сообщение.",
        )

    scenario = payload.get("scenario", default_scenario)
    allowed = sorted(scenarios)
    if scenario not in allowed:
        raise ValidationError(
            "unknown_scenario",
            f'Неизвестный сценарий "{scenario}". Доступны: {", ".join(allowed)}.',
        )

    return message, scenario
