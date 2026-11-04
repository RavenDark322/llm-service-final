"""Постобработка: проверка ответа модели по контракту {"answer": "..."} и очистка текста."""

import json
import re

from llm.errors import LLMResponseError

# Модели часто оборачивают JSON в ```json ... ``` — это восстанавливаемое отклонение.
CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def parse_and_clean(raw: str, max_chars: int) -> str:
    if raw is None or not raw.strip():
        raise LLMResponseError("empty response from model")

    text = raw.strip()
    fenced = CODE_FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMResponseError(f"invalid JSON from model: {exc.msg}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("answer"), str):
        raise LLMResponseError('missing or invalid field "answer"')

    answer = clean_text(data["answer"])
    if not answer:
        raise LLMResponseError('field "answer" is empty')

    if len(answer) > max_chars:
        answer = answer[:max_chars].rstrip() + "…"
    return answer


def clean_text(text: str) -> str:
    """Убирает лишние пробелы и пустые строки, сохраняя абзацы."""
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
