"""
briner_agent/classifiers/decision_cache.py

LRU + TTL decision cache for LLM classification results.
Keyed by (extension, stem_pattern) where stem_pattern normalizes
digits to '#' so invoice_001 and invoice_042 share the same cache entry.

Thread-safe. No external dependencies.
"""

import logging
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_DIGIT_RE = re.compile(r"\d+")


def _stem_pattern(filename: str) -> str:
    """Normalize digits in the stem to '#' for cache key generalization."""
    stem = filename.rsplit(".", 1)[0].casefold() if "." in filename else filename.casefold()
    return _DIGIT_RE.sub("#", stem)


@dataclass
class CacheEntry:
    category: str
    decision_source: str
    cached_at: float  # time.monotonic()


class DecisionCache:
    """
    LRU + TTL cache for (extension, stem_pattern) → category.

    Parameters
    ----------
    max_size : int
        Maximum number of entries before LRU eviction.
    ttl_seconds : float
        Time-to-live per entry. Expired entries are treated as misses.
    """

    def __init__(self, max_size: int = 200, ttl_seconds: float = 3600.0):
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._cache: OrderedDict[tuple[str, str], CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    @staticmethod
    def make_key(filename: str, extension: str) -> tuple[str, str]:
        ext = extension.casefold() if extension else ""
        pattern = _stem_pattern(filename)
        return (ext, pattern)

    def get(self, filename: str, extension: str) -> str | None:
        key = self.make_key(filename, extension)
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None
            if time.monotonic() - entry.cached_at > self.ttl_seconds:
                del self._cache[key]
                self._misses += 1
                logger.debug("Cache TTL expired for key %s", key)
                return None
            self._cache.move_to_end(key)
            self._hits += 1
            logger.debug("Cache hit for %s → %s", key, entry.category)
            return entry.category

    def put(self, filename: str, extension: str, category: str, decision_source: str = "llm"):
        key = self.make_key(filename, extension)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = CacheEntry(
                category=category,
                decision_source=decision_source,
                cached_at=time.monotonic(),
            )
            if len(self._cache) > self.max_size:
                evicted_key, _ = self._cache.popitem(last=False)
                logger.debug("Cache LRU evicted key %s", evicted_key)

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total, 3) if total > 0 else 0.0,
                "size": len(self._cache),
                "max_size": self.max_size,
                "ttl_seconds": self.ttl_seconds,
            }

    def clear(self):
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0
