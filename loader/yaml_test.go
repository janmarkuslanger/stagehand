package loader

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/janmarkuslanger/stagehand/core"
)

var validWorkflowYAML = []byte(`
name: "Test Workflow"
version: "1"

output:
  backend: filesystem
  path: ./artifacts

agents:
  writer:
    role: "Writer"
    system_prompt: "You are a writer."
    model: claude-sonnet-4-20250514
    executor: claude
    tools:
      - write_file

tasks:
  draft:
    agent: writer
    prompt: "Write a draft."
    outputs:
      - draft.md

  review:
    agent: writer
    depends_on: [draft]
    prompt: "Review: {{ tasks.draft }}"
    outputs:
      - review.md
`)

func TestParse_ValidWorkflow(t *testing.T) {
	workflow, err := Parse(validWorkflowYAML)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if workflow.Name != "Test Workflow" {
		t.Errorf("expected name %q, got %q", "Test Workflow", workflow.Name)
	}
	if len(workflow.Tasks) != 2 {
		t.Errorf("expected 2 tasks, got %d", len(workflow.Tasks))
	}
	if len(workflow.Agents) != 1 {
		t.Errorf("expected 1 agent, got %d", len(workflow.Agents))
	}
}

func TestParse_StaticOutputs(t *testing.T) {
	workflow, err := Parse(validWorkflowYAML)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	draft := workflow.Tasks["draft"]
	static, ok := draft.Outputs.(core.StaticOutputs)
	if !ok {
		t.Fatalf("expected StaticOutputs, got %T", draft.Outputs)
	}
	if len(static) != 1 || static[0] != "draft.md" {
		t.Errorf("expected [draft.md], got %v", static)
	}
}

func TestParse_DynamicOutput(t *testing.T) {
	data := []byte(`
name: "Test"
agents:
  a:
    role: "X"
    executor: claude
tasks:
  t:
    agent: a
    prompt: "p"
    outputs: dynamic
`)
	workflow, err := Parse(data)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	_, ok := workflow.Tasks["t"].Outputs.(core.DynamicOutputs)
	if !ok {
		t.Errorf("expected DynamicOutputs, got %T", workflow.Tasks["t"].Outputs)
	}
}

func TestParse_PatternOutput(t *testing.T) {
	data := []byte(`
name: "Test"
agents:
  a:
    role: "X"
    executor: claude
tasks:
  t:
    agent: a
    prompt: "p"
    outputs:
      pattern: "pages/**/*.html"
`)
	workflow, err := Parse(data)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	pattern, ok := workflow.Tasks["t"].Outputs.(core.PatternOutputs)
	if !ok {
		t.Fatalf("expected PatternOutputs, got %T", workflow.Tasks["t"].Outputs)
	}
	if pattern.Pattern != "pages/**/*.html" {
		t.Errorf("expected pattern %q, got %q", "pages/**/*.html", pattern.Pattern)
	}
}

func TestParse_UnknownAgent(t *testing.T) {
	data := []byte(`
name: "Test"
agents:
  a:
    role: "X"
    executor: claude
tasks:
  t:
    agent: missing_agent
    prompt: "p"
    outputs: dynamic
`)
	_, err := Parse(data)
	if err == nil {
		t.Fatal("expected error for unknown agent, got nil")
	}
}

func TestParse_UnknownDependency(t *testing.T) {
	data := []byte(`
name: "Test"
agents:
  a:
    role: "X"
    executor: claude
tasks:
  t:
    agent: a
    depends_on: [missing_task]
    prompt: "p"
    outputs: dynamic
`)
	_, err := Parse(data)
	if err == nil {
		t.Fatal("expected error for unknown dependency, got nil")
	}
}

func TestParse_CycleDetection(t *testing.T) {
	data := []byte(`
name: "Test"
agents:
  a:
    role: "X"
    executor: claude
tasks:
  t1:
    agent: a
    depends_on: [t2]
    prompt: "p"
  t2:
    agent: a
    depends_on: [t1]
    prompt: "p"
`)
	_, err := Parse(data)
	if err == nil {
		t.Fatal("expected error for cycle, got nil")
	}
}

func TestParse_MissingWorkflowName(t *testing.T) {
	data := []byte(`
agents:
  a:
    role: "X"
    executor: claude
tasks:
  t:
    agent: a
    prompt: "p"
`)
	_, err := Parse(data)
	if err == nil {
		t.Fatal("expected error for missing workflow name, got nil")
	}
}

func TestLoad_ValidFile(t *testing.T) {
	filePath := filepath.Join(t.TempDir(), "workflow.yaml")
	if err := os.WriteFile(filePath, validWorkflowYAML, 0644); err != nil {
		t.Fatalf("write file: %v", err)
	}
	workflow, err := Load(filePath)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if workflow.Name != "Test Workflow" {
		t.Errorf("expected name %q, got %q", "Test Workflow", workflow.Name)
	}
}

func TestParse_UnknownScalarOutput(t *testing.T) {
	data := []byte(`
name: "Test"
agents:
  a:
    role: "X"
    executor: claude
tasks:
  t:
    agent: a
    prompt: "p"
    outputs: unknown_value
`)
	_, err := Parse(data)
	if err == nil {
		t.Fatal("expected error for unknown scalar output spec, got nil")
	}
}

func TestParse_ExecutorFieldIsPropagated(t *testing.T) {
	data := []byte(`
name: "Test"
agents:
  a:
    role: "X"
    executor: ollama
tasks:
  t:
    agent: a
    prompt: "p"
`)
	workflow, err := Parse(data)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if workflow.Agents["a"].Executor != "ollama" {
		t.Errorf("expected executor %q, got %q", "ollama", workflow.Agents["a"].Executor)
	}
}

func TestParse_MissingExecutorReturnsError(t *testing.T) {
	data := []byte(`
name: "Test"
agents:
  a:
    role: "X"
tasks:
  t:
    agent: a
    prompt: "p"
`)
	_, err := Parse(data)
	if err == nil {
		t.Fatal("expected error for missing executor, got nil")
	}
}
