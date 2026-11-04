"""Prompt Builder: собирает сообщения для модели из системной инструкции сценария и запроса."""

from typing import Dict, List


class PromptBuilder:
    def __init__(self, scenarios: Dict[str, Dict[str, str]], format_reminder: str = ""):
        self._scenarios = scenarios
        # Напоминание о формате ответа в конце сообщения пользователя: модели сильнее всего
        # следуют последней инструкции, и фраза пользователя вроде «ответь одним предложением»
        # иначе перебивает требование JSON из системного промпта.
        self._format_reminder = format_reminder.strip()

    @property
    def scenarios(self) -> List[str]:
        return sorted(self._scenarios)

    def system_prompt(self, scenario: str) -> str:
        return self._scenarios[scenario]["system_prompt"].strip()

    def build(self, scenario: str, message: str) -> List[Dict[str, str]]:
        """Формат messages, который принимают OpenAI-совместимые API и GigaChat."""
        user_content = f"{message}\n\n{self._format_reminder}" if self._format_reminder else message
        return [
            {"role": "system", "content": self.system_prompt(scenario)},
            {"role": "user", "content": user_content},
        ]
