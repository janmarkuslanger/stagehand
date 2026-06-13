from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Optional


class TaskStatus:
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


@dataclass
class TaskState:
    status: str = TaskStatus.PENDING
    output: str = ""
    files: list[str] = field(default_factory=list)
    completed_at: str = ""
    error: str = ""
    partial_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict = {"status": self.status}
        if self.output:
            d["output"] = self.output
        if self.files:
            d["files"] = self.files
        if self.completed_at:
            d["completed_at"] = self.completed_at
        if self.error:
            d["error"] = self.error
        if self.partial_files:
            d["partial_files"] = self.partial_files
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "TaskState":
        return cls(
            status=data.get("status", TaskStatus.PENDING),
            output=data.get("output", ""),
            files=data.get("files", []),
            completed_at=data.get("completed_at", ""),
            error=data.get("error", ""),
            partial_files=data.get("partial_files", []),
        )


@dataclass
class RunState:
    id: str
    workflow_file: str
    workflow: str
    inputs: dict[str, str] = field(default_factory=dict)
    tasks: dict[str, TaskState] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "workflow_file": self.workflow_file,
            "workflow": self.workflow,
            "inputs": self.inputs,
            "tasks": {k: v.to_dict() for k, v in self.tasks.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RunState":
        tasks = {
            k: TaskState.from_dict(v) for k, v in data.get("tasks", {}).items()
        }
        return cls(
            id=data["id"],
            workflow_file=data.get("workflow_file", ""),
            workflow=data.get("workflow", ""),
            inputs=data.get("inputs", {}),
            tasks=tasks,
        )


def new_run_state(
    run_id: str,
    workflow_file: str,
    workflow_name: str,
    inputs: dict[str, str],
) -> RunState:
    return RunState(
        id=run_id,
        workflow_file=workflow_file,
        workflow=workflow_name,
        inputs=inputs,
    )


def save(state: RunState, directory: str) -> None:
    """Writes RunState as JSON to <directory>/<run_id>.json."""
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, state.id + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state.to_dict(), f, indent=2)


def load_state(run_id: str, directory: str) -> RunState:
    """Reads a RunState from <directory>/<run_id>.json."""
    path = os.path.join(directory, run_id + ".json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return RunState.from_dict(data)


def build_run_state(
    run_id: str,
    workflow_file: str,
    workflow: "Workflow",  # type: ignore[name-defined]
    inputs: dict[str, str],
    run_context: "RunContext",  # type: ignore[name-defined]
    run_err: Optional[Exception],
) -> RunState:
    """Assembles a RunState from the outcome of a scheduler run."""
    state = new_run_state(run_id, workflow_file, workflow.name, inputs)
    completed = run_context.all_results()
    skipped = run_context.skipped_ids()

    # Dynamically generated fan-out children (ids not in workflow.tasks) carry
    # their results in the run context; persist them so resume can skip them.
    dynamic_ids = [task_id for task_id in completed if task_id not in workflow.tasks]

    for task_id in list(workflow.tasks) + dynamic_ids:
        if task_id in skipped:
            state.tasks[task_id] = TaskState(status=TaskStatus.SKIPPED)
        elif task_id in completed:
            result = completed[task_id]
            state.tasks[task_id] = TaskState(
                status=TaskStatus.DONE,
                output=result.output,
                files=result.files,
            )
        elif run_err is not None:
            state.tasks[task_id] = TaskState(status=TaskStatus.CANCELLED)
        else:
            state.tasks[task_id] = TaskState(status=TaskStatus.PENDING)

    return state


def generate_run_id() -> str:
    now_ns = time.time_ns()
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    suffix = format(now_ns % 0xFFFF, "04x")
    return f"sh-{now.strftime('%Y%m%d')}-{suffix}"
