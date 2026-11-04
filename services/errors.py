"""Ошибки уровня API/бизнес-логики."""


class ValidationError(Exception):
    """Пользовательская ошибка ввода. Отвечаем 400 и объясняем, что исправить."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message
