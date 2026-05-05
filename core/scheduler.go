package core

import (
	"context"
	"fmt"
)

// TaskExecutor runs a single task and returns its result.
// Implementations live in adapters/executor/ and are bridged in cmd/.
type TaskExecutor interface {
	Execute(ctx context.Context, request ExecutionRequest) (ExecutionResult, error)
}

// ExecutionRequest is the input passed to a TaskExecutor.
type ExecutionRequest struct {
	AgentID string
	Prompt  string
	RunID   string
	TaskID  string
}

// ExecutionResult is the output produced by a TaskExecutor.
type ExecutionResult struct {
	Output string
	Files  []string
}

// Scheduler executes a Workflow's tasks in dependency order, running independent tasks in parallel.
type Scheduler struct {
	executor TaskExecutor
}

// NewScheduler creates a Scheduler backed by the given executor.
func NewScheduler(executor TaskExecutor) *Scheduler {
	return &Scheduler{executor: executor}
}

type taskPhase int

const (
	phaseWaiting   taskPhase = iota
	phaseRunning
	phaseDone
	phaseFailed
	phaseCancelled
)

type taskOutcome struct {
	taskID string
	result TaskResult
	err    error
}

// Run executes all tasks in the workflow.
// Tasks with no unresolved dependencies are started immediately and run in parallel.
// A failing task causes all transitively downstream tasks to be cancelled.
func (s *Scheduler) Run(ctx context.Context, workflow *Workflow, runContext *RunContext) error {
	graph, err := BuildGraph(workflow)
	if err != nil {
		return fmt.Errorf("scheduler: %w", err)
	}

	inDegree := make(map[string]int, len(workflow.Tasks))
	for id, task := range workflow.Tasks {
		inDegree[id] = len(task.DependsOn)
	}

	phases := make(map[string]taskPhase, len(workflow.Tasks))
	for id := range workflow.Tasks {
		phases[id] = phaseWaiting
	}

	// Tasks that already have results in the RunContext (e.g. from a resumed run)
	// are treated as pre-completed: mark them done and reduce dependent in-degrees.
	for id := range workflow.Tasks {
		if _, ok := runContext.GetTaskResult(id); ok {
			phases[id] = phaseDone
			for _, dependentID := range graph.Dependents(id) {
				inDegree[dependentID]--
			}
		}
	}

	// Buffer size equals task count so goroutines never block on send.
	outcomes := make(chan taskOutcome, len(workflow.Tasks))
	runningCount := 0

	launch := func(taskID string) {
		phases[taskID] = phaseRunning
		runningCount++
		go func() {
			task := workflow.Tasks[taskID]
			resolvedPrompt, templateErr := Resolve(task.Prompt, runContext)
			if templateErr != nil {
				outcomes <- taskOutcome{
					taskID: taskID,
					err:    fmt.Errorf("scheduler: task %s: resolve prompt: %w", taskID, templateErr),
				}
				return
			}

			request := ExecutionRequest{
				AgentID: task.AgentID,
				Prompt:  resolvedPrompt,
				RunID:   runContext.RunID,
				TaskID:  taskID,
			}

			executionResult, execErr := s.executor.Execute(ctx, request)
			if execErr != nil {
				outcomes <- taskOutcome{
					taskID: taskID,
					err:    fmt.Errorf("scheduler: task %s: %w", taskID, execErr),
				}
				return
			}

			outcomes <- taskOutcome{
				taskID: taskID,
				result: TaskResult{Output: executionResult.Output, Files: executionResult.Files},
			}
		}()
	}

	for id := range workflow.Tasks {
		if inDegree[id] == 0 && phases[id] == phaseWaiting {
			launch(id)
		}
	}

	var firstError error

	for runningCount > 0 {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case outcome := <-outcomes:
			runningCount--

			if outcome.err != nil {
				if firstError == nil {
					firstError = outcome.err
				}
				phases[outcome.taskID] = phaseFailed
				cancelDownstream(outcome.taskID, graph, phases)
			} else {
				phases[outcome.taskID] = phaseDone
				runContext.SetTaskResult(outcome.taskID, outcome.result)

				for _, dependentID := range graph.Dependents(outcome.taskID) {
					if phases[dependentID] != phaseWaiting {
						continue
					}
					inDegree[dependentID]--
					if inDegree[dependentID] == 0 {
						launch(dependentID)
					}
				}
			}
		}
	}

	return firstError
}

// cancelDownstream marks all transitively downstream waiting tasks as cancelled.
func cancelDownstream(taskID string, graph *Graph, phases map[string]taskPhase) {
	for _, dependentID := range graph.Dependents(taskID) {
		if phases[dependentID] == phaseWaiting {
			phases[dependentID] = phaseCancelled
			cancelDownstream(dependentID, graph, phases)
		}
	}
}
