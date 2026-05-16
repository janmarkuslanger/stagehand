from __future__ import annotations

from stagehand.core.workflow import Workflow


class Graph:
    """Holds the adjacency structure of the task DAG."""

    def __init__(
        self,
        dependents: dict[str, list[str]],
        in_degree: dict[str, int],
        order: list[str],
    ) -> None:
        self._dependents = dependents
        self._in_degree = in_degree
        self._order = order

    def dependents(self, task_id: str) -> list[str]:
        """Returns the IDs of tasks that directly depend on the given task."""
        return self._dependents.get(task_id, [])

    def topological_order(self) -> list[str]:
        """Returns tasks in an order where all dependencies come before their dependents."""
        return self._order

    def downstream_set(self, task_id: str) -> set[str]:
        """Returns the set of task IDs that are transitively downstream of task_id, including itself."""
        result: set[str] = set()
        self._collect_downstream(task_id, result)
        return result

    def _collect_downstream(self, task_id: str, visited: set[str]) -> None:
        if task_id in visited:
            return
        visited.add(task_id)
        for dependent in self._dependents.get(task_id, []):
            self._collect_downstream(dependent, visited)


def build_graph(workflow: Workflow) -> Graph:
    """Constructs a DAG from a Workflow and validates it has no cycles."""
    dependents: dict[str, list[str]] = {task_id: [] for task_id in workflow.tasks}
    in_degree: dict[str, int] = {task_id: 0 for task_id in workflow.tasks}

    for task_id, task in workflow.tasks.items():
        for dependency in task.depends_on:
            if dependency not in workflow.tasks:
                raise ValueError(f"graph: task {task_id}: unknown dependency {dependency!r}")
            dependents[dependency].append(task_id)
            in_degree[task_id] += 1

    order = _topological_sort(in_degree, dependents)
    return Graph(dependents, in_degree, order)


def _topological_sort(
    in_degree: dict[str, int],
    dependents: dict[str, list[str]],
) -> list[str]:
    """Kahn's algorithm — computes topological order and detects cycles."""
    degree = dict(in_degree)
    queue = [task_id for task_id, d in degree.items() if d == 0]
    order: list[str] = []

    while queue:
        current = queue.pop(0)
        order.append(current)
        for dependent in dependents.get(current, []):
            degree[dependent] -= 1
            if degree[dependent] == 0:
                queue.append(dependent)

    if len(order) != len(in_degree):
        raise ValueError("graph: workflow contains a dependency cycle")

    return order
