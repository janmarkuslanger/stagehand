import asyncio
import os
import tempfile

import pytest

from stagehand.core.context import RunContext
from stagehand.core.runstate import (
    RunState,
    TaskState,
    TaskStatus,
    build_run_state,
    generate_run_id,
    load_state,
    save,
)
from stagehand.core.workflow import AgentConfig, Task, TaskResult, Workflow


def test_generate_run_id():
    run_id = generate_run_id()
    assert run_id.startswith("sh-")
    assert len(run_id) > 5


def test_save_and_load():
    state = RunState(
        id="sh-20260101-abcd",
        workflow_file="workflow.yaml",
        workflow="Test",
        inputs={"k": "v"},
        tasks={"t1": TaskState(status=TaskStatus.DONE, output="out", files=["f.txt"])},
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        save(state, tmpdir)
        loaded = load_state("sh-20260101-abcd", tmpdir)

    assert loaded.id == state.id
    assert loaded.workflow == state.workflow
    assert loaded.inputs == state.inputs
    assert loaded.tasks["t1"].status == TaskStatus.DONE
    assert loaded.tasks["t1"].output == "out"
    assert loaded.tasks["t1"].files == ["f.txt"]


def test_build_run_state_success():
    wf = Workflow(
        name="Test",
        agents={"a": AgentConfig(executor="ollama")},
        tasks={"t1": Task(agent_id="a"), "t2": Task(agent_id="a")},
    )
    ctx = RunContext(run_id="test", inputs={})
    loop = asyncio.new_event_loop()
    loop.run_until_complete(ctx.set_task_result("t1", TaskResult(output="hello", files=[])))
    loop.close()

    state = build_run_state("test", "wf.yaml", wf, {}, ctx, None)
    assert state.tasks["t1"].status == TaskStatus.DONE
    assert state.tasks["t2"].status == TaskStatus.PENDING


def test_build_run_state_error():
    wf = Workflow(
        name="Test",
        agents={"a": AgentConfig(executor="ollama")},
        tasks={"t1": Task(agent_id="a"), "t2": Task(agent_id="a")},
    )
    ctx = RunContext(run_id="test", inputs={})

    state = build_run_state("test", "wf.yaml", wf, {}, ctx, RuntimeError("oops"))
    assert state.tasks["t1"].status == TaskStatus.CANCELLED
    assert state.tasks["t2"].status == TaskStatus.CANCELLED
