"""
briner_agent/infra/metrics.py

In-process metrics store. Thread-safe. No external dependencies.
All values available via metrics.snapshot() for --metrics CLI output.
"""

import threading
import time
from collections import deque
from typing import Any

_MAX_SAMPLES = 100


class _Timer:
    def __init__(self, maxlen: int = _MAX_SAMPLES):
        self._samples: deque[float] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def record(self, seconds: float):
        with self._lock:
            self._samples.append(seconds)

    def snapshot(self) -> dict:
        with self._lock:
            samples = list(self._samples)
        if not samples:
            return {"count": 0, "mean_ms": None, "p95_ms": None, "last_ms": None}
        sorted_s = sorted(samples)
        p95_idx = max(0, int(len(sorted_s) * 0.95) - 1)
        return {
            "count": len(samples),
            "mean_ms": round(sum(samples) / len(samples) * 1000, 1),
            "p95_ms": round(sorted_s[p95_idx] * 1000, 1),
            "last_ms": round(samples[-1] * 1000, 1),
        }


class _Counter:
    def __init__(self):
        self._value = 0
        self._lock = threading.Lock()

    def inc(self, n: int = 1):
        with self._lock:
            self._value += n

    def snapshot(self) -> int:
        with self._lock:
            return self._value


class Metrics:
    """Singleton in-process metrics registry."""

    _instance: "Metrics | None" = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        with cls._instance_lock:
            if cls._instance is None:
                inst = super().__new__(cls)
                inst._counters: dict[str, _Counter] = {}
                inst._timers: dict[str, _Timer] = {}
                inst._lock = threading.Lock()
                cls._instance = inst
        return cls._instance

    def inc(self, name: str, n: int = 1):
        self._get_counter(name).inc(n)

    def _get_counter(self, name: str) -> _Counter:
        with self._lock:
            if name not in self._counters:
                self._counters[name] = _Counter()
            return self._counters[name]

    def record(self, name: str, seconds: float):
        self._get_timer(name).record(seconds)

    def _get_timer(self, name: str) -> _Timer:
        with self._lock:
            if name not in self._timers:
                self._timers[name] = _Timer()
            return self._timers[name]

    class _Span:
        def __init__(self, registry: "Metrics", name: str):
            self._registry = registry
            self._name = name
            self._start: float = 0.0

        def __enter__(self):
            self._start = time.perf_counter()
            return self

        def __exit__(self, *_):
            elapsed = time.perf_counter() - self._start
            self._registry.record(self._name, elapsed)

    def span(self, name: str) -> "_Span":
        return self._Span(self, name)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counter_names = list(self._counters)
            timer_names = list(self._timers)
        return {
            "counters": {n: self._counters[n].snapshot() for n in counter_names},
            "timers": {n: self._timers[n].snapshot() for n in timer_names},
        }


# Module-level singleton
metrics = Metrics()

# Canonical metric name constants
M_STARTUP_LATENCY    = "startup_latency_s"
M_LLM_CALL           = "llm_call_duration_s"
M_PHASE1_DURATION    = "phase1_rules_duration_s"
M_PHASE2_DURATION    = "phase2_batch_duration_s"
M_PHASE3_DURATION    = "phase3_react_duration_s"
M_CYCLE_DURATION     = "cycle_total_duration_s"
M_LLM_CALLS_TOTAL    = "llm_calls_total"
M_LLM_FAILURES_TOTAL = "llm_failures_total"
M_FILES_PROCESSED    = "files_processed_total"
M_FILES_ERRORS       = "files_errors_total"
M_CACHE_HITS         = "cache_hits_total"
M_CACHE_MISSES       = "cache_misses_total"
