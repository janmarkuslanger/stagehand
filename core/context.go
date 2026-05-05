package core

import "sync"

// TaskResult holds the output produced by a completed task.
type TaskResult struct {
	Output string
	Files  []string
}

// RunContext holds the shared state for a single workflow run.
type RunContext struct {
	RunID   string
	inputs  map[string]string
	results map[string]TaskResult
	mu      sync.RWMutex
}

// NewRunContext creates a RunContext for a new run.
func NewRunContext(runID string, inputs map[string]string) *RunContext {
	copied := make(map[string]string, len(inputs))
	for k, v := range inputs {
		copied[k] = v
	}
	return &RunContext{
		RunID:   runID,
		inputs:  copied,
		results: make(map[string]TaskResult),
	}
}

// SetTaskResult records the result of a completed task.
func (c *RunContext) SetTaskResult(taskID string, result TaskResult) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.results[taskID] = result
}

// GetTaskResult returns the result of a completed task.
func (c *RunContext) GetTaskResult(taskID string) (TaskResult, bool) {
	c.mu.RLock()
	defer c.mu.RUnlock()
	result, ok := c.results[taskID]
	return result, ok
}

// GetInput returns the value of a runtime input.
func (c *RunContext) GetInput(key string) (string, bool) {
	value, ok := c.inputs[key]
	return value, ok
}

// AllResults returns a snapshot of all completed task results.
func (c *RunContext) AllResults() map[string]TaskResult {
	c.mu.RLock()
	defer c.mu.RUnlock()
	snapshot := make(map[string]TaskResult, len(c.results))
	for k, v := range c.results {
		snapshot[k] = v
	}
	return snapshot
}
