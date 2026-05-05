package core

import (
	"fmt"
	"strings"
	"testing"
)

func TestSaveAndLoad_RoundTrip(t *testing.T) {
	directory := t.TempDir()
	state := NewRunState("sh-test-0001", "workflow.yaml", "My Workflow", map[string]string{"key": "value"})
	state.Tasks["task-a"] = &TaskState{
		Status: TaskStatusDone,
		Output: "result text",
		Files:  []string{"out.md"},
	}

	if err := Save(state, directory); err != nil {
		t.Fatalf("Save: %v", err)
	}

	loaded, err := Load("sh-test-0001", directory)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}

	if loaded.ID != state.ID {
		t.Errorf("ID: expected %q, got %q", state.ID, loaded.ID)
	}
	if loaded.Workflow != state.Workflow {
		t.Errorf("Workflow: expected %q, got %q", state.Workflow, loaded.Workflow)
	}
	if loaded.Tasks["task-a"].Status != TaskStatusDone {
		t.Errorf("task status: expected %q, got %q", TaskStatusDone, loaded.Tasks["task-a"].Status)
	}
	if loaded.Tasks["task-a"].Output != "result text" {
		t.Errorf("task output: expected %q, got %q", "result text", loaded.Tasks["task-a"].Output)
	}
}

func TestLoad_MissingFile(t *testing.T) {
	directory := t.TempDir()
	_, err := Load("sh-does-not-exist", directory)
	if err == nil {
		t.Fatal("expected error for missing file, got nil")
	}
}

func TestBuildRunState_CompletedTasksMarkedDone(t *testing.T) {
	workflow := &Workflow{
		Name: "Test Workflow",
		Tasks: map[string]*Task{
			"a": {AgentID: "x"},
			"b": {AgentID: "x"},
		},
	}
	runContext := NewRunContext("run-1", nil)
	runContext.SetTaskResult("a", TaskResult{Output: "out-a", Files: []string{"a.md"}})

	state := BuildRunState("run-1", "workflow.yaml", workflow, nil, runContext, nil)

	if state.Tasks["a"].Status != TaskStatusDone {
		t.Errorf("expected task a to be done, got %s", state.Tasks["a"].Status)
	}
	if state.Tasks["a"].Output != "out-a" {
		t.Errorf("expected output %q, got %q", "out-a", state.Tasks["a"].Output)
	}
	if state.Tasks["b"].Status != TaskStatusPending {
		t.Errorf("expected task b to be pending, got %s", state.Tasks["b"].Status)
	}
}

func TestBuildRunState_FailedRunMarksMissingTasksCancelled(t *testing.T) {
	workflow := &Workflow{
		Name: "Test Workflow",
		Tasks: map[string]*Task{
			"a": {AgentID: "x"},
			"b": {AgentID: "x"},
		},
	}
	runContext := NewRunContext("run-1", nil)
	runContext.SetTaskResult("a", TaskResult{Output: "out-a"})

	state := BuildRunState("run-1", "workflow.yaml", workflow, nil, runContext, fmt.Errorf("something failed"))

	if state.Tasks["a"].Status != TaskStatusDone {
		t.Errorf("expected completed task a to be done, got %s", state.Tasks["a"].Status)
	}
	if state.Tasks["b"].Status != TaskStatusCancelled {
		t.Errorf("expected uncompleted task b to be cancelled on failed run, got %s", state.Tasks["b"].Status)
	}
}

func TestGenerateRunID_Format(t *testing.T) {
	id := GenerateRunID()
	if !strings.HasPrefix(id, "sh-") {
		t.Errorf("run ID should start with sh-, got %q", id)
	}
	// Format: sh-YYYYMMDD-XXXX — minimum length is 14 chars
	if len(id) < 14 {
		t.Errorf("run ID too short: %q", id)
	}
}

func TestGenerateRunID_Uniqueness(t *testing.T) {
	seen := make(map[string]bool, 100)
	for i := 0; i < 100; i++ {
		id := GenerateRunID()
		seen[id] = true
	}
	// Not strictly guaranteed but highly likely — if all 100 are identical something is wrong.
	if len(seen) < 2 {
		t.Errorf("expected multiple unique run IDs, got only %d distinct values", len(seen))
	}
}
