package core

import "fmt"

// Graph holds the adjacency structure of the task DAG.
type Graph struct {
	// dependents maps a task ID to the IDs of tasks that depend on it.
	dependents map[string][]string
	// inDegree maps each task ID to its number of dependencies.
	inDegree map[string]int
	// order is a topologically sorted list of task IDs.
	order []string
}

// BuildGraph constructs a DAG from a Workflow and validates it has no cycles.
func BuildGraph(workflow *Workflow) (*Graph, error) {
	g := &Graph{
		dependents: make(map[string][]string, len(workflow.Tasks)),
		inDegree:   make(map[string]int, len(workflow.Tasks)),
	}

	for id := range workflow.Tasks {
		g.inDegree[id] = 0
		if _, exists := g.dependents[id]; !exists {
			g.dependents[id] = nil
		}
	}

	for id, task := range workflow.Tasks {
		for _, dependency := range task.DependsOn {
			if _, exists := workflow.Tasks[dependency]; !exists {
				return nil, fmt.Errorf("graph: task %s: unknown dependency %q", id, dependency)
			}
			g.dependents[dependency] = append(g.dependents[dependency], id)
			g.inDegree[id]++
		}
	}

	order, err := topologicalSort(g.inDegree, g.dependents)
	if err != nil {
		return nil, err
	}
	g.order = order
	return g, nil
}

// Dependents returns the IDs of tasks that directly depend on the given task.
func (g *Graph) Dependents(taskID string) []string {
	return g.dependents[taskID]
}

// TopologicalOrder returns tasks in an order where all dependencies come before their dependents.
func (g *Graph) TopologicalOrder() []string {
	return g.order
}

// DownstreamSet returns the set of task IDs that are transitively downstream
// of taskID, including taskID itself.
func (g *Graph) DownstreamSet(taskID string) map[string]bool {
	result := make(map[string]bool)
	g.collectDownstream(taskID, result)
	return result
}

func (g *Graph) collectDownstream(taskID string, visited map[string]bool) {
	if visited[taskID] {
		return
	}
	visited[taskID] = true
	for _, dependent := range g.dependents[taskID] {
		g.collectDownstream(dependent, visited)
	}
}

// topologicalSort uses Kahn's algorithm to compute the topological order and detect cycles.
func topologicalSort(inDegree map[string]int, dependents map[string][]string) ([]string, error) {
	degree := make(map[string]int, len(inDegree))
	for id, d := range inDegree {
		degree[id] = d
	}

	queue := make([]string, 0, len(degree))
	for id, d := range degree {
		if d == 0 {
			queue = append(queue, id)
		}
	}

	order := make([]string, 0, len(inDegree))
	for len(queue) > 0 {
		current := queue[0]
		queue = queue[1:]
		order = append(order, current)

		for _, dependent := range dependents[current] {
			degree[dependent]--
			if degree[dependent] == 0 {
				queue = append(queue, dependent)
			}
		}
	}

	if len(order) != len(inDegree) {
		return nil, fmt.Errorf("graph: workflow contains a dependency cycle")
	}
	return order, nil
}
