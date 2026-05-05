package core

import (
	"testing"
)

func TestResolve_InputReference(t *testing.T) {
	ctx := NewRunContext("run-1", map[string]string{"name": "Alice"})
	result, err := Resolve("Hello {{ input.name }}!", ctx)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result != "Hello Alice!" {
		t.Errorf("expected %q, got %q", "Hello Alice!", result)
	}
}

func TestResolve_TaskPrimaryOutput(t *testing.T) {
	ctx := NewRunContext("run-1", nil)
	ctx.SetTaskResult("analysis", TaskResult{Output: "the output", Files: []string{"out.md"}})
	result, err := Resolve("Result: {{ tasks.analysis }}", ctx)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result != "Result: the output" {
		t.Errorf("expected %q, got %q", "Result: the output", result)
	}
}

func TestResolve_TaskFiles(t *testing.T) {
	ctx := NewRunContext("run-1", nil)
	ctx.SetTaskResult("build", TaskResult{Output: "", Files: []string{"a.html", "b.html"}})
	result, err := Resolve("Files: {{ tasks.build.files }}", ctx)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result != "Files: a.html\nb.html" {
		t.Errorf("expected %q, got %q", "Files: a.html\nb.html", result)
	}
}

func TestResolve_TaskFileBySlug(t *testing.T) {
	ctx := NewRunContext("run-1", nil)
	ctx.SetTaskResult("design", TaskResult{Output: "", Files: []string{"tokens.css", "design-system.md"}})
	result, err := Resolve("{{ tasks.design.tokens_css }}", ctx)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result != "tokens.css" {
		t.Errorf("expected %q, got %q", "tokens.css", result)
	}
}

func TestResolve_MissingInput(t *testing.T) {
	ctx := NewRunContext("run-1", nil)
	_, err := Resolve("{{ input.missing }}", ctx)
	if err == nil {
		t.Fatal("expected error for missing input, got nil")
	}
}

func TestResolve_MissingTask(t *testing.T) {
	ctx := NewRunContext("run-1", nil)
	_, err := Resolve("{{ tasks.missing }}", ctx)
	if err == nil {
		t.Fatal("expected error for missing task, got nil")
	}
}

func TestResolve_NoTemplates(t *testing.T) {
	ctx := NewRunContext("run-1", nil)
	result, err := Resolve("plain text with no templates", ctx)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result != "plain text with no templates" {
		t.Errorf("expected unchanged text, got %q", result)
	}
}

var resolveTableTests = []struct {
	name     string
	template string
	inputs   map[string]string
	tasks    map[string]TaskResult
	want     string
	wantErr  bool
}{
	{
		name:     "multiple inputs",
		template: "{{ input.a }} and {{ input.b }}",
		inputs:   map[string]string{"a": "foo", "b": "bar"},
		want:     "foo and bar",
	},
	{
		name:     "unknown reference type",
		template: "{{ unknown.x }}",
		wantErr:  true,
	},
	{
		name:     "empty template string",
		template: "",
		want:     "",
	},
	{
		name:     "input reference with extra whitespace in template",
		template: "{{  input.key  }}",
		inputs:   map[string]string{"key": "value"},
		want:     "value",
	},
}

func TestResolve_Table(t *testing.T) {
	for _, tc := range resolveTableTests {
		t.Run(tc.name, func(t *testing.T) {
			ctx := NewRunContext("run-1", tc.inputs)
			for id, result := range tc.tasks {
				ctx.SetTaskResult(id, result)
			}
			got, err := Resolve(tc.template, ctx)
			if tc.wantErr {
				if err == nil {
					t.Fatalf("expected error, got nil")
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if got != tc.want {
				t.Errorf("expected %q, got %q", tc.want, got)
			}
		})
	}
}
