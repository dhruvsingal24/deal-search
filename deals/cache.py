"""Search-result cache.

Wraps Django's cache framework (LocMemCache by default, which already does
TTL + LRU culling) with hit/miss counters so `/stats` can report them.

Scope note: this caches the *ranking result* for a (brand, amount) pair. It
never short-circuits the search-history write, so a cached response is still
recorded — a cache hit is a performance detail, not a change in behaviour.
Point CACHES at Redis to share it across replicas; nothing else changes.
"""

import threading

from django.core.cache import cache as django_cache

_KEY_PREFIX = "search"


class CacheStats:
    def __init__(self):
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def record(self, hit: bool) -> None:
        with self._lock:
            if hit:
                self.hits += 1
            else:
                self.misses += 1

    def reset(self) -> None:
        with self._lock:
            self.hits = self.misses = 0

    def as_dict(self) -> dict:
        with self._lock:
            total = self.hits + self.misses
            return {
                "hits": self.hits,
                "misses": self.misses,
                "hitRate": round(self.hits / total, 4) if total else 0.0,
            }


stats = CacheStats()


def make_key(brand_key: str, amount) -> str:
    return f"{_KEY_PREFIX}:{brand_key}:{amount:.2f}"


def get(key: str):
    value = django_cache.get(key)
    stats.record(value is not None)
    return value


def set(key: str, value) -> None:  # noqa: A001 - mirrors the cache API
    django_cache.set(key, value)


def clear() -> None:
    """Called on any write that changes what a search would return."""
    django_cache.clear()


def flush_all() -> None:
    clear()
    stats.reset()
