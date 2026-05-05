package main

import (
	"path/filepath"
	"testing"

	"github.com/janmarkuslanger/stagehand/core"
)

func TestResolveArtifactRoot_EmptyPathUsesDefault(t *testing.T) {
	result := resolveArtifactRoot("/some/path/workflow.yaml", "", "sh-run-001")
	expected := filepath.Join(".stagehand", "runs", "sh-run-001")
	if result != expected {
		t.Errorf("expected %q, got %q", expected, result)
	}
}

func TestResolveArtifactRoot_AbsolutePathPassedThrough(t *testing.T) {
	result := resolveArtifactRoot("/some/path/workflow.yaml", "/data/outputs", "sh-run-001")
	if result != "/data/outputs" {
		t.Errorf("expected %q, got %q", "/data/outputs", result)
	}
}

func TestResolveArtifactRoot_RelativePathResolvedFromWorkflowDir(t *testing.T) {
	result := resolveArtifactRoot("/projects/myflow/workflow.yaml", "./output", "sh-run-001")
	expected := filepath.Join("/projects", "myflow", "output")
	if result != expected {
		t.Errorf("expected %q, got %q", expected, result)
	}
}

func TestComputePreCompletedTasks_NoCacheReturnsEmpty(t *testing.T) {
	workflow := &core.Workflow{
		Tasks: map[string]*core.Task{
			"a": {AgentID: "x"},
			"b": {AgentID: "x"},
		},
	}
	state := &core.RunState{
		Tasks: map[string]*core.TaskState{
			"a": {Status: core.TaskStatusDone},
			"b": {Status: core.TaskStatusDone},
		},
	}
	result, err := computePreCompletedTasks(workflow, state, "", true)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(result) != 0 {
		t.Errorf("expected empty map with --no-cache, got %v", result)
	}
}

func TestComputePreCompletedTasks_NoFromTaskReturnsDoneTasks(t *testing.T) {
	workflow := &core.Workflow{
		Tasks: map[string]*core.Task{
			"a": {AgentID: "x"},
			"b": {AgentID: "x"},
			"c": {AgentID: "x"},
		},
	}
	state := &core.RunState{
		Tasks: map[string]*core.TaskState{
			"a": {Status: core.TaskStatusDone},
			"b": {Status: core.TaskStatusFailed},
			"c": {Status: core.TaskStatusCancelled},
		},
	}
	result, err := computePreCompletedTasks(workflow, state, "", false)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !result["a"] {
		t.Error("expected done task a to be pre-completed")
	}
	if result["b"] || result["c"] {
		t.Error("expected failed/cancelled tasks to not be pre-completed")
	}
}

func TestComputePreCompletedTasks_FromTaskExcludesDownstream(t *testing.T) {
	workflow := &core.Workflow{
		Agents: map[string]core.AgentConfig{"x": {}},
		Tasks: map[string]*core.Task{
			"a": {AgentID: "x"},
			"b": {AgentID: "x", DependsOn: []string{"a"}},
			"c": {AgentID: "x", DependsOn: []string{"b"}},
		},
	}
	state := &core.RunState{
		Tasks: map[string]*core.TaskState{
			"a": {Status: core.TaskStatusDone},
			"b": {Status: core.TaskStatusDone},
			"c": {Status: core.TaskStatusDone},
		},
	}
	result, err := computePreCompletedTasks(workflow, state, "b", false)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !result["a"] {
		t.Error("expected upstream task a to be pre-completed")
	}
	if result["b"] {
		t.Error("expected from-task b to NOT be pre-completed (it should re-run)")
	}
	if result["c"] {
		t.Error("expected downstream task c to NOT be pre-completed")
	}
}

func TestComputePreCompletedTasks_FromTaskErrorsIfUpstreamNotDone(t *testing.T) {
	workflow := &core.Workflow{
		Agents: map[string]core.AgentConfig{"x": {}},
		Tasks: map[string]*core.Task{
			"a": {AgentID: "x"},
			"b": {AgentID: "x", DependsOn: []string{"a"}},
		},
	}
	state := &core.RunState{
		Tasks: map[string]*core.TaskState{
			"a": {Status: core.TaskStatusFailed},
			"b": {Status: core.TaskStatusCancelled},
		},
	}
	_, err := computePreCompletedTasks(workflow, state, "b", false)
	if err == nil {
		t.Fatal("expected error when upstream task is not done, got nil")
	}
}

func TestComputePreCompletedTasks_UnknownFromTaskReturnsError(t *testing.T) {
	workflow := &core.Workflow{
		Agents: map[string]core.AgentConfig{"x": {}},
		Tasks: map[string]*core.Task{
			"a": {AgentID: "x"},
		},
	}
	state := &core.RunState{
		Tasks: map[string]*core.TaskState{
			"a": {Status: core.TaskStatusDone},
		},
	}
	_, err := computePreCompletedTasks(workflow, state, "does-not-exist", false)
	if err == nil {
		t.Fatal("expected error for unknown from-task, got nil")
	}
}
