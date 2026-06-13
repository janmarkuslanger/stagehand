from __future__ import annotations

import asyncio
from typing import Any, Optional

from stagehand.core.workflow import TaskResult


class RunContext:
    """Holds the shared state for a single workflow run."""

    def __init__(self, run_id: str, inputs: dict[str, Any]) -> None:
        self.run_id = run_id
        self._inputs: dict[str, Any] = dict(inputs)
        self._results: dict[str, TaskResult] = {}
        self._skipped: set[str] = set()
        self._lock = asyncio.Lock()

    async def set_task_result(self, task_id: str, result: TaskResult) -> None:
        async with self._lock:
            self._results[task_id] = result

    def get_task_result(self, task_id: str) -> Optional[TaskResult]:
        return self._results.get(task_id)

    def get_input(self, key: str) -> Optional[Any]:
        return self._inputs.get(key)

    def all_results(self) -> dict[str, TaskResult]:
        return dict(self._results)

    def mark_skipped(self, task_id: str) -> None:
        self._skipped.add(task_id)

    def is_skipped(self, task_id: str) -> bool:
        return task_id in self._skipped

    def skipped_ids(self) -> set[str]:
        return set(self._skipped)
