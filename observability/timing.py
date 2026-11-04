"""Замер времени выполнения этапов pipeline."""

import time
from contextlib import contextmanager
from typing import Dict, Iterator


@contextmanager
def stage(timings: Dict[str, float], name: str) -> Iterator[None]:
    """with stage(timings, "llm_call"): ...  ->  timings["llm_call"] = 123.4 (мс)"""
    started = time.perf_counter()
    try:
        yield
    finally:
        timings[name] = round((time.perf_counter() - started) * 1000, 1)
