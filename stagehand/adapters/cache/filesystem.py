from __future__ import annotations

import json
import os
from typing import Optional

from stagehand.ports.cache import ResultCache
from stagehand.ports.executor import ExecutionResult


class FilesystemCache(ResultCache):
    """Cache that persists results as one JSON file per key under a directory.

    Entries survive process restarts, which is the main payoff during
    development: re-running a workflow reuses the unchanged upstream tasks
    instead of calling the backend again. The default directory is
    ``.stagehand/cache``.

    Only ``output`` and ``files`` are persisted. The structured ``data`` value is
    in-memory only — consistent with persisted run state — so a result restored
    from disk has ``data=None``.
    """

    def __init__(self, root: str = ".stagehand/cache") -> None:
        self.root = root

    def _path(self, key: str) -> str:
        return os.path.join(self.root, f"{key}.json")

    async def get(self, key: str) -> Optional[ExecutionResult]:
        try:
            with open(self._path(key), "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        return ExecutionResult(
            output=payload.get("output", ""),
            files=payload.get("files", []),
        )

    async def set(self, key: str, result: ExecutionResult) -> None:
        os.makedirs(self.root, exist_ok=True)
        payload = {"output": result.output, "files": result.files}
        with open(self._path(key), "w", encoding="utf-8") as f:
            json.dump(payload, f)
