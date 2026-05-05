package core

import (
	"context"
	"errors"
	"testing"
)

type mockExecutor struct {
	results map[string]ExecutionResult
	errors  map[string]error
	called  []string
}

func (m *mockExecutor) Execute(_ context.Context, request ExecutionRequest) (ExecutionResult, error) {
	m.called = append(m.called, request.TaskID)
	if err, ok := m.errors[request.TaskID]; ok {
		return ExecutionResult{}, err
	}
	if result, ok := m.results[request.TaskID]; ok {
		return result, nil
	}
	return ExecutionResult{Output: request.TaskID + "-output"}, nil
}

func TestScheduler_RunsTasksWithNoDependenciesImmediately(t *testing.T) {
	workflow := &Workflow{
		Agents: map[string]AgentConfig{"x": {}},
		Tasks: map[string]*Task{
			"a": {AgentID: "x"},
			"b": {AgentID: "x"},
		},
	}
	mock := &mockExecutor{}
	scheduler := NewScheduler(mock)
	runContext := NewRunContext("run-1", nil)

	if err := scheduler.Run(context.Background(), workflow, runContext); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(mock.called) != 2 {
		t.Errorf("expected 2 tasks executed, got %d", len(mock.called))
	}
}

func TestScheduler_RespectsDependencyOrder(t *testing.T) {
	workflow := &Workflow{
		Agents: map[string]AgentConfig{"x": {}},
		Tasks: map[string]*Task{
			"a": {AgentID: "x"},
			"b": {AgentID: "x", DependsOn: []string{"a"}},
		},
	}
	mock := &mockExecutor{
		results: map[string]ExecutionResult{
			"a": {Output: "a-result"},
		},
	}
	scheduler := NewScheduler(mock)
	runContext := NewRunContext("run-1", nil)

	if err := scheduler.Run(context.Background(), workflow, runContext); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	calledSet := make(map[string]bool, len(mock.called))
	for _, id := range mock.called {
		calledSet[id] = true
	}
	if !calledSet["a"] || !calledSet["b"] {
		t.Errorf("expected both a and b to run, got: %v", mock.called)
	}
}

func TestScheduler_DownstreamTaskReceivesUpstreamOutput(t *testing.T) {
	workflow := &Workflow{
		Agents: map[string]AgentConfig{"x": {}},
		Tasks: map[string]*Task{
			"upstream":   {AgentID: "x", Prompt: "produce output"},
			"downstream": {AgentID: "x", DependsOn: []string{"upstream"}, Prompt: "use {{ tasks.upstream }}"},
		},
	}
	mock := &mockExecutor{
		results: map[string]ExecutionResult{
			"upstream": {Output: "upstream-result"},
		},
	}
	scheduler := NewScheduler(mock)
	runContext := NewRunContext("run-1", nil)

	if err := scheduler.Run(context.Background(), workflow, runContext); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	result, ok := runContext.GetTaskResult("upstream")
	if !ok {
		t.Fatal("expected upstream result in run context")
	}
	if result.Output != "upstream-result" {
		t.Errorf("expected upstream output %q, got %q", "upstream-result", result.Output)
	}
}

func TestScheduler_FailureCancelsDownstreamTasks(t *testing.T) {
	workflow := &Workflow{
		Agents: map[string]AgentConfig{"x": {}},
		Tasks: map[string]*Task{
			"a": {AgentID: "x"},
			"b": {AgentID: "x", DependsOn: []string{"a"}},
			"c": {AgentID: "x", DependsOn: []string{"b"}},
		},
	}
	mock := &mockExecutor{
		errors: map[string]error{"a": errors.New("a failed")},
	}
	scheduler := NewScheduler(mock)
	runContext := NewRunContext("run-1", nil)

	err := scheduler.Run(context.Background(), workflow, runContext)
	if err == nil {
		t.Fatal("expected error from failing task, got nil")
	}

	for _, id := range mock.called {
		if id == "b" || id == "c" {
			t.Errorf("expected task %s to be cancelled, but it was executed", id)
		}
	}
}

func TestScheduler_SiblingTaskContinuesAfterFailure(t *testing.T) {
	workflow := &Workflow{
		Agents: map[string]AgentConfig{"x": {}},
		Tasks: map[string]*Task{
			"a": {AgentID: "x"},
			"b": {AgentID: "x"},
			"c": {AgentID: "x", DependsOn: []string{"a", "b"}},
		},
	}
	mock := &mockExecutor{
		errors: map[string]error{"a": errors.New("a failed")},
	}
	scheduler := NewScheduler(mock)
	runContext := NewRunContext("run-1", nil)

	_ = scheduler.Run(context.Background(), workflow, runContext)

	calledSet := make(map[string]bool, len(mock.called))
	for _, id := range mock.called {
		calledSet[id] = true
	}
	// b has no dependency on a and should run regardless
	if !calledSet["b"] {
		t.Errorf("expected sibling task b to run even though a failed")
	}
	// c depends on both a and b; since a failed, c is cancelled
	if calledSet["c"] {
		t.Errorf("expected task c to be cancelled, but it was executed")
	}
}

func TestScheduler_SkipsTasksWithPrePopulatedResults(t *testing.T) {
	workflow := &Workflow{
		Agents: map[string]AgentConfig{"x": {}},
		Tasks: map[string]*Task{
			"a": {AgentID: "x"},
			"b": {AgentID: "x", DependsOn: []string{"a"}},
		},
	}
	mock := &mockExecutor{}
	scheduler := NewScheduler(mock)
	runContext := NewRunContext("run-1", nil)
	// Pre-populate task a as if it was completed in a previous run.
	runContext.SetTaskResult("a", TaskResult{Output: "cached-output"})

	if err := scheduler.Run(context.Background(), workflow, runContext); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	for _, calledID := range mock.called {
		if calledID == "a" {
			t.Error("expected pre-completed task a to be skipped, but it was executed")
		}
	}
	calledSet := make(map[string]bool, len(mock.called))
	for _, id := range mock.called {
		calledSet[id] = true
	}
	if !calledSet["b"] {
		t.Error("expected downstream task b to be executed")
	}
}

func TestScheduler_CycleInWorkflowReturnsError(t *testing.T) {
	workflow := &Workflow{
		Agents: map[string]AgentConfig{"x": {}},
		Tasks: map[string]*Task{
			"a": {AgentID: "x", DependsOn: []string{"b"}},
			"b": {AgentID: "x", DependsOn: []string{"a"}},
		},
	}
	mock := &mockExecutor{}
	scheduler := NewScheduler(mock)
	runContext := NewRunContext("run-1", nil)

	err := scheduler.Run(context.Background(), workflow, runContext)
	if err == nil {
		t.Fatal("expected error for cycle, got nil")
	}
}

func TestScheduler_ContextCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel() // cancel immediately

	workflow := &Workflow{
		Agents: map[string]AgentConfig{"x": {}},
		Tasks: map[string]*Task{
			"a": {AgentID: "x"},
		},
	}
	// Executor blocks until context is done
	blocking := &blockingExecutor{done: ctx.Done()}
	scheduler := NewScheduler(blocking)
	runContext := NewRunContext("run-1", nil)

	err := scheduler.Run(ctx, workflow, runContext)
	if err == nil {
		t.Fatal("expected context cancellation error, got nil")
	}
}

// blockingExecutor blocks until its done channel is closed, then returns a result.
type blockingExecutor struct {
	done <-chan struct{}
}

func (e *blockingExecutor) Execute(ctx context.Context, request ExecutionRequest) (ExecutionResult, error) {
	select {
	case <-e.done:
		return ExecutionResult{}, context.Canceled
	case <-ctx.Done():
		return ExecutionResult{}, ctx.Err()
	}
}
