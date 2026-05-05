package ports

import "context"

// ExecutionRequest is the input passed to an AgentExecutor.
// Agent properties are flattened here; the domain type core.AgentConfig
// is resolved by the bridge in cmd/ before the request reaches an executor.
type ExecutionRequest struct {
	SystemPrompt string
	Model        string
	Tools        []string
	Prompt       string
	RunID        string
	TaskID       string
}

// ExecutionResult is the output produced by an AgentExecutor.
type ExecutionResult struct {
	Output string
	Files  []string
}

// AgentExecutor runs a task against an AI backend and returns the result.
type AgentExecutor interface {
	Execute(ctx context.Context, request ExecutionRequest) (ExecutionResult, error)
}
