from __future__ import annotations

from typing import Optional

from stagehand.ports.cache import ResultCache
from stagehand.ports.executor import ExecutionResult


class InMemoryCache(ResultCache):
    """Process-local cache backed by a dict.

    Entries live only for the lifetime of the instance. Useful for tests and for
    deduplicating identical work within a single run (for example fan-out
    children that resolve to the same prompt). For reuse across processes use
    :class:`~stagehand.adapters.cache.filesystem.FilesystemCache`.
    """

    def __init__(self) -> None:
        self._store: dict[str, ExecutionResult] = {}

    async def get(self, key: str) -> Optional[ExecutionResult]:
        return self._store.get(key)

    async def set(self, key: str, result: ExecutionResult) -> None:
        self._store[key] = result
