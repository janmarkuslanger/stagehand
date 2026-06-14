from __future__ import annotations

import time
from typing import Optional

from stagehand.ports.cache import ResultCache
from stagehand.ports.executor import ExecutionResult


class InMemoryCache(ResultCache):
    """Process-local cache backed by a dict.

    Entries live only for the lifetime of the instance. Useful for tests and for
    deduplicating identical work within a single run (for example fan-out
    children that resolve to the same prompt). For reuse across processes use
    :class:`~stagehand.adapters.cache.filesystem.FilesystemCache`.

    Parameters
    ----------
    ttl:
        If set, entries older than ``ttl`` seconds are treated as a miss (lazy
        expiry — checked on read, never evicted in the background). ``None``
        (the default) means entries never expire.
    """

    def __init__(self, ttl: Optional[float] = None) -> None:
        self._store: dict[str, tuple[float, ExecutionResult]] = {}
        self._ttl = ttl

    async def get(self, key: str) -> Optional[ExecutionResult]:
        entry = self._store.get(key)
        if entry is None:
            return None
        stored_at, result = entry
        if self._ttl is not None and time.time() - stored_at > self._ttl:
            del self._store[key]
            return None
        return result

    async def set(self, key: str, result: ExecutionResult) -> None:
        self._store[key] = (time.time(), result)
