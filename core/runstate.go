package core

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

// TaskStatus describes the lifecycle state of a task in a run.
type TaskStatus string

const (
	TaskStatusPending   TaskStatus = "pending"
	TaskStatusRunning   TaskStatus = "running"
	TaskStatusDone      TaskStatus = "done"
	TaskStatusFailed    TaskStatus = "failed"
	TaskStatusCancelled TaskStatus = "cancelled"
)

// TaskState records the result of a single task execution.
type TaskState struct {
	Status       TaskStatus `json:"status"`
	Output       string     `json:"output,omitempty"`
	Files        []string   `json:"files,omitempty"`
	CompletedAt  time.Time  `json:"completed_at,omitempty"`
	Error        string     `json:"error,omitempty"`
	PartialFiles []string   `json:"partial_files,omitempty"`
}

// RunState is the complete persisted state of a workflow run.
type RunState struct {
	ID           string                `json:"id"`
	WorkflowFile string                `json:"workflow_file"`
	Workflow     string                `json:"workflow"`
	Inputs       map[string]string     `json:"inputs"`
	Tasks        map[string]*TaskState `json:"tasks"`
}

// NewRunState creates a RunState for a new run.
func NewRunState(runID, workflowFile, workflowName string, inputs map[string]string) *RunState {
	return &RunState{
		ID:           runID,
		WorkflowFile: workflowFile,
		Workflow:     workflowName,
		Inputs:       inputs,
		Tasks:        make(map[string]*TaskState),
	}
}

// Save writes the RunState as JSON to <directory>/<runID>.json.
func Save(state *RunState, directory string) error {
	if err := os.MkdirAll(directory, 0755); err != nil {
		return fmt.Errorf("runstate: create directory: %w", err)
	}
	data, err := json.MarshalIndent(state, "", "  ")
	if err != nil {
		return fmt.Errorf("runstate: marshal: %w", err)
	}
	path := filepath.Join(directory, state.ID+".json")
	if err := os.WriteFile(path, data, 0644); err != nil {
		return fmt.Errorf("runstate: write %s: %w", path, err)
	}
	return nil
}

// Load reads a RunState from <directory>/<runID>.json.
func Load(runID, directory string) (*RunState, error) {
	path := filepath.Join(directory, runID+".json")
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("runstate: read %s: %w", path, err)
	}
	var state RunState
	if err := json.Unmarshal(data, &state); err != nil {
		return nil, fmt.Errorf("runstate: unmarshal %s: %w", path, err)
	}
	return &state, nil
}

// BuildRunState assembles a RunState from the outcome of a scheduler run.
// Tasks present in runContext.AllResults() are marked done; all others are
// marked cancelled when runErr is non-nil, or pending when it is nil.
func BuildRunState(runID string, workflowFile string, workflow *Workflow, inputs map[string]string, runContext *RunContext, runErr error) *RunState {
	state := NewRunState(runID, workflowFile, workflow.Name, inputs)
	completedResults := runContext.AllResults()

	for taskID := range workflow.Tasks {
		if result, ok := completedResults[taskID]; ok {
			state.Tasks[taskID] = &TaskState{
				Status: TaskStatusDone,
				Output: result.Output,
				Files:  result.Files,
			}
		} else if runErr != nil {
			state.Tasks[taskID] = &TaskState{Status: TaskStatusCancelled}
		} else {
			state.Tasks[taskID] = &TaskState{Status: TaskStatusPending}
		}
	}

	return state
}
func GenerateRunID() string {
	now := time.Now().UTC()
	suffix := fmt.Sprintf("%04x", now.UnixNano()%0xffff)
	return fmt.Sprintf("sh-%s-%s", now.Format("20060102"), suffix)
}
