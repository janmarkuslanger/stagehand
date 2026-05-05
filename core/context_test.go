package core

import (
	"fmt"
	"sync"
	"testing"
)

func TestRunContext_GetInput(t *testing.T) {
	ctx := NewRunContext("run-1", map[string]string{"key": "value"})
	value, ok := ctx.GetInput("key")
	if !ok {
		t.Fatal("expected input to exist")
	}
	if value != "value" {
		t.Errorf("expected %q, got %q", "value", value)
	}
}

func TestRunContext_GetInput_Missing(t *testing.T) {
	ctx := NewRunContext("run-1", nil)
	_, ok := ctx.GetInput("missing")
	if ok {
		t.Fatal("expected missing input to return false")
	}
}

func TestRunContext_InputsAreCopied(t *testing.T) {
	original := map[string]string{"key": "value"}
	ctx := NewRunContext("run-1", original)
	original["key"] = "mutated"
	value, _ := ctx.GetInput("key")
	if value != "value" {
		t.Errorf("input should be isolated from original map, got %q", value)
	}
}

func TestRunContext_SetAndGetTaskResult(t *testing.T) {
	ctx := NewRunContext("run-1", nil)
	result := TaskResult{Output: "output text", Files: []string{"file.md"}}
	ctx.SetTaskResult("task-a", result)
	got, ok := ctx.GetTaskResult("task-a")
	if !ok {
		t.Fatal("expected task result to exist")
	}
	if got.Output != result.Output {
		t.Errorf("expected output %q, got %q", result.Output, got.Output)
	}
}

func TestRunContext_GetTaskResult_Missing(t *testing.T) {
	ctx := NewRunContext("run-1", nil)
	_, ok := ctx.GetTaskResult("missing")
	if ok {
		t.Fatal("expected missing task result to return false")
	}
}

func TestRunContext_AllResults_ReturnsSnapshot(t *testing.T) {
	ctx := NewRunContext("run-1", nil)
	ctx.SetTaskResult("a", TaskResult{Output: "out-a"})
	ctx.SetTaskResult("b", TaskResult{Output: "out-b"})

	snapshot := ctx.AllResults()
	if len(snapshot) != 2 {
		t.Fatalf("expected 2 results, got %d", len(snapshot))
	}
	if snapshot["a"].Output != "out-a" || snapshot["b"].Output != "out-b" {
		t.Errorf("unexpected snapshot contents: %v", snapshot)
	}

	// Mutating the snapshot must not affect the RunContext.
	snapshot["c"] = TaskResult{Output: "injected"}
	if _, ok := ctx.GetTaskResult("c"); ok {
		t.Error("snapshot mutation leaked into RunContext")
	}
}

func TestRunContext_ConcurrentAccess(t *testing.T) {
	ctx := NewRunContext("run-1", nil)
	var wg sync.WaitGroup
	for i := 0; i < 100; i++ {
		wg.Add(1)
		go func(n int) {
			defer wg.Done()
			id := fmt.Sprintf("task-%d", n)
			ctx.SetTaskResult(id, TaskResult{Output: id})
			ctx.GetTaskResult(id)
		}(i)
	}
	wg.Wait()
}
