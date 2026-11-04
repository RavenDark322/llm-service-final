"""Кеш в памяти с TTL и ограничением размера + построение ключа кеша."""

import hashlib
import json
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple


class TTLCache:
    def __init__(self, ttl_seconds: float, max_entries: int = 1000, clock: Callable[[], float] = time.monotonic):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._clock = clock
        self._data: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.Lock()  # Flask обрабатывает запросы в разных потоках
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                self.misses += 1
                return None
            expires_at, value = item
            if self._clock() >= expires_at:  # запись протухла — считаем промахом и удаляем
                del self._data[key]
                self.misses += 1
                return None
            self.hits += 1
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            if key not in self._data and len(self._data) >= self.max_entries:
                # Вытесняем запись, которая истекает раньше всех.
                oldest = min(self._data, key=lambda k: self._data[k][0])
                del self._data[oldest]
            self._data[key] = (self._clock() + self.ttl_seconds, value)

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            total = self.hits + self.misses
            return {
                "entries": len(self._data),
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 3) if total else 0.0,
                "ttl_seconds": self.ttl_seconds,
            }


def make_cache_key(version: str, **parts: Any) -> str:
    """Ключ = версия + хеш всех параметров, влияющих на ответ.

    Передавайте сюда текст запроса, модель, temperature, max_tokens и системный промпт:
    если забыть что-то из них, кеш начнёт возвращать ответы на «чужие» запросы.
    """
    canonical = json.dumps(parts, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{version}:{digest}"
