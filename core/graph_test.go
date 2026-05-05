package core

import (
	"testing"
)

func TestBuildGraph_NoDependencies(t *testing.T) {
	workflow := &Workflow{
		Agents: map[string]AgentConfig{"x": {}},
		Tasks: map[string]*Task{
			"a": {AgentID: "x"},
			"b": {AgentID: "x"},
		},
	}
	graph, err := BuildGraph(workflow)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(graph.TopologicalOrder()) != 2 {
		t.Errorf("expected 2 tasks in order, got %d", len(graph.TopologicalOrder()))
	}
}

func TestBuildGraph_DetectsCycle(t *testing.T) {
	workflow := &Workflow{
		Agents: map[string]AgentConfig{"x": {}},
		Tasks: map[string]*Task{
			"a": {AgentID: "x", DependsOn: []string{"b"}},
			"b": {AgentID: "x", DependsOn: []string{"a"}},
		},
	}
	_, err := BuildGraph(workflow)
	if err == nil {
		t.Fatal("expected error for cycle, got nil")
	}
}

func TestBuildGraph_UnknownDependency(t *testing.T) {
	workflow := &Workflow{
		Agents: map[string]AgentConfig{"x": {}},
		Tasks: map[string]*Task{
			"a": {AgentID: "x", DependsOn: []string{"missing"}},
		},
	}
	_, err := BuildGraph(workflow)
	if err == nil {
		t.Fatal("expected error for unknown dependency, got nil")
	}
}

func TestBuildGraph_TopologicalOrder(t *testing.T) {
	workflow := &Workflow{
		Agents: map[string]AgentConfig{"x": {}},
		Tasks: map[string]*Task{
			"a": {AgentID: "x"},
			"b": {AgentID: "x", DependsOn: []string{"a"}},
			"c": {AgentID: "x", DependsOn: []string{"b"}},
		},
	}
	graph, err := BuildGraph(workflow)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	order := graph.TopologicalOrder()
	if len(order) != 3 {
		t.Fatalf("expected 3 tasks in order, got %d", len(order))
	}
	position := make(map[string]int, len(order))
	for i, id := range order {
		position[id] = i
	}
	if position["a"] >= position["b"] {
		t.Errorf("expected a before b in topological order")
	}
	if position["b"] >= position["c"] {
		t.Errorf("expected b before c in topological order")
	}
}

func TestBuildGraph_Dependents(t *testing.T) {
	workflow := &Workflow{
		Agents: map[string]AgentConfig{"x": {}},
		Tasks: map[string]*Task{
			"a": {AgentID: "x"},
			"b": {AgentID: "x", DependsOn: []string{"a"}},
			"c": {AgentID: "x", DependsOn: []string{"a"}},
		},
	}
	graph, err := BuildGraph(workflow)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	dependents := graph.Dependents("a")
	if len(dependents) != 2 {
		t.Errorf("expected 2 dependents of a, got %d", len(dependents))
	}
}

func TestGraph_DownstreamSet_IncludesSelf(t *testing.T) {
	workflow := &Workflow{
		Agents: map[string]AgentConfig{"x": {}},
		Tasks: map[string]*Task{
			"a": {AgentID: "x"},
			"b": {AgentID: "x", DependsOn: []string{"a"}},
			"c": {AgentID: "x", DependsOn: []string{"b"}},
		},
	}
	graph, err := BuildGraph(workflow)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	set := graph.DownstreamSet("b")
	if !set["b"] {
		t.Error("expected b to be in its own downstream set")
	}
	if !set["c"] {
		t.Error("expected c (dependent of b) to be in downstream set of b")
	}
	if set["a"] {
		t.Error("expected a (upstream of b) not to be in downstream set of b")
	}
}

func TestGraph_DownstreamSet_TransitiveClosure(t *testing.T) {
	workflow := &Workflow{
		Agents: map[string]AgentConfig{"x": {}},
		Tasks: map[string]*Task{
			"a": {AgentID: "x"},
			"b": {AgentID: "x", DependsOn: []string{"a"}},
			"c": {AgentID: "x", DependsOn: []string{"b"}},
			"d": {AgentID: "x", DependsOn: []string{"c"}},
		},
	}
	graph, err := BuildGraph(workflow)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	set := graph.DownstreamSet("b")
	for _, expected := range []string{"b", "c", "d"} {
		if !set[expected] {
			t.Errorf("expected %q in downstream set of b", expected)
		}
	}
	if set["a"] {
		t.Error("expected a not to be in downstream set of b")
	}
}

func TestGraph_DownstreamSet_LeafTaskContainsOnlySelf(t *testing.T) {
	workflow := &Workflow{
		Agents: map[string]AgentConfig{"x": {}},
		Tasks: map[string]*Task{
			"a": {AgentID: "x"},
			"b": {AgentID: "x", DependsOn: []string{"a"}},
		},
	}
	graph, err := BuildGraph(workflow)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	set := graph.DownstreamSet("b")
	if len(set) != 1 || !set["b"] {
		t.Errorf("expected downstream set of leaf b to contain only b, got %v", set)
	}
}

func TestBuildGraph_EmptyWorkflow(t *testing.T) {
	workflow := &Workflow{
		Agents: map[string]AgentConfig{},
		Tasks:  map[string]*Task{},
	}
	graph, err := BuildGraph(workflow)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(graph.TopologicalOrder()) != 0 {
		t.Errorf("expected empty topological order")
	}
}
