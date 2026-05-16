import pytest

from stagehand.core.graph import build_graph
from stagehand.core.workflow import AgentConfig, Task, Workflow


def _make_workflow(tasks: dict) -> Workflow:
    return Workflow(
        name="test",
        agents={"a": AgentConfig(executor="ollama")},
        tasks=tasks,
    )


def test_single_task():
    wf = _make_workflow({"t1": Task(agent_id="a")})
    g = build_graph(wf)
    assert g.topological_order() == ["t1"]


def test_sequential():
    wf = _make_workflow({
        "t1": Task(agent_id="a"),
        "t2": Task(agent_id="a", depends_on=["t1"]),
    })
    g = build_graph(wf)
    order = g.topological_order()
    assert order.index("t1") < order.index("t2")


def test_parallel_then_merge():
    wf = _make_workflow({
        "t1": Task(agent_id="a"),
        "t2": Task(agent_id="a"),
        "t3": Task(agent_id="a", depends_on=["t1", "t2"]),
    })
    g = build_graph(wf)
    order = g.topological_order()
    assert order.index("t1") < order.index("t3")
    assert order.index("t2") < order.index("t3")


def test_cycle_detected():
    wf = _make_workflow({
        "t1": Task(agent_id="a", depends_on=["t2"]),
        "t2": Task(agent_id="a", depends_on=["t1"]),
    })
    with pytest.raises(ValueError, match="cycle"):
        build_graph(wf)


def test_unknown_dependency():
    wf = _make_workflow({
        "t1": Task(agent_id="a", depends_on=["nonexistent"]),
    })
    with pytest.raises(ValueError, match="unknown dependency"):
        build_graph(wf)


def test_dependents():
    wf = _make_workflow({
        "t1": Task(agent_id="a"),
        "t2": Task(agent_id="a", depends_on=["t1"]),
        "t3": Task(agent_id="a", depends_on=["t1"]),
    })
    g = build_graph(wf)
    assert set(g.dependents("t1")) == {"t2", "t3"}
    assert g.dependents("t2") == []


def test_downstream_set():
    wf = _make_workflow({
        "t1": Task(agent_id="a"),
        "t2": Task(agent_id="a", depends_on=["t1"]),
        "t3": Task(agent_id="a", depends_on=["t2"]),
    })
    g = build_graph(wf)
    assert g.downstream_set("t1") == {"t1", "t2", "t3"}
    assert g.downstream_set("t2") == {"t2", "t3"}
    assert g.downstream_set("t3") == {"t3"}
