from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from typing import Optional

from stagehand.ports.executor import ExecutionRequest, ExecutionResult


class ResultCache(ABC):
    """Port: stores and retrieves agent execution results keyed by request signature.

    A cache lets identical agent work be reused across runs: when a task's
    resolved request (model, system prompt, tools and prompt) matches a previous
    one, the stored :class:`ExecutionResult` is returned instead of calling the
    backend again. Caching is opt-in — pass an implementation to the scheduler or
    via ``WorkflowBuilder.cache``.

    Caching assumes "identical input → identical output", which is a deliberate
    choice for non-deterministic LLM backends. Only agent tasks are cached;
    deterministic ``fn`` tasks are never routed through the cache. On a cache hit
    the stored ``files`` are reused, but tools with external side effects are not
    invoked again.
    """

    @abstractmethod
    async def get(self, key: str) -> Optional[ExecutionResult]:
        """Return the cached result for ``key``, or ``None`` on a miss."""

    @abstractmethod
    async def set(self, key: str, result: ExecutionResult) -> None:
        """Store ``result`` under ``key``."""


def cache_key(request: ExecutionRequest) -> str:
    """Derive a stable cache key from the result-determining fields of a request.

    ``run_id`` and ``task_id`` are deliberately excluded — they change every run
    and would prevent any cache hit. ``tools`` is sorted so ordering does not
    affect the key. Because ``prompt`` is the already-resolved template string,
    the key changes automatically when an upstream task produces a different
    output, so the cache self-invalidates along the DAG.
    """
    parts = [
        request.model,
        request.system_prompt,
        "\x00".join(sorted(request.tools)),
        request.prompt,
    ]
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8"))
    return digest.hexdigest()
