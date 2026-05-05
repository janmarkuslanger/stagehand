package main

import (
	"fmt"
	"os"

	"github.com/janmarkuslanger/stagehand/core"
	"github.com/janmarkuslanger/stagehand/loader"
	"github.com/spf13/cobra"
)

func buildResumeCommand() *cobra.Command {
	var fromTask string
	var noCache bool
	cmd := &cobra.Command{
		Use:   "resume <run-id>",
		Short: "Re-run a workflow, reusing results from a previous run",
		Long: `Resume re-runs a workflow run, skipping tasks that already completed successfully.

Without --from: failed and cancelled tasks are re-run; completed tasks are reused.
With --from <task>: the named task and all tasks downstream of it are re-run.
With --no-cache: no previous results are reused; all tasks run from scratch.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return resumeRun(cmd, args[0], fromTask, noCache)
		},
	}
	cmd.Flags().StringVar(&fromTask, "from", "", "Re-run from this task and all tasks downstream of it")
	cmd.Flags().BoolVar(&noCache, "no-cache", false, "Ignore all previous results and re-run everything")
	return cmd
}

func resumeRun(cmd *cobra.Command, runID, fromTask string, noCache bool) error {
	previousState, err := core.Load(runID, runStateDirectory)
	if err != nil {
		return fmt.Errorf("resume: %w", err)
	}
	if previousState.WorkflowFile == "" {
		return fmt.Errorf("resume: run %s was created before workflow file tracking was added; re-run the workflow instead", runID)
	}

	workflow, err := loader.Load(previousState.WorkflowFile)
	if err != nil {
		return fmt.Errorf("resume: load workflow %s: %w", previousState.WorkflowFile, err)
	}

	preCompleted, err := computePreCompletedTasks(workflow, previousState, fromTask, noCache)
	if err != nil {
		return err
	}

	newRunID := core.GenerateRunID()
	runContext := core.NewRunContext(newRunID, previousState.Inputs)
	for taskID := range preCompleted {
		taskState := previousState.Tasks[taskID]
		runContext.SetTaskResult(taskID, core.TaskResult{
			Output: taskState.Output,
			Files:  taskState.Files,
		})
	}

	registry, err := buildExecutorRegistry(previousState.WorkflowFile, workflow, newRunID)
	if err != nil {
		return err
	}

	bridge := &executorBridge{executors: registry, workflow: workflow}
	scheduler := core.NewScheduler(bridge)
	fmt.Printf("Run ID: %s (resumed from %s)\n", newRunID, runID)

	runErr := scheduler.Run(cmd.Context(), workflow, runContext)

	state := core.BuildRunState(newRunID, previousState.WorkflowFile, workflow, previousState.Inputs, runContext, runErr)
	if saveErr := core.Save(state, runStateDirectory); saveErr != nil {
		fmt.Fprintf(os.Stderr, "warning: could not save run state: %v\n", saveErr)
	}

	return runErr
}

// computePreCompletedTasks determines which tasks should reuse their saved results.
//
// Without fromTask: all tasks with TaskStatusDone are pre-completed.
// With fromTask: all tasks NOT transitively downstream of fromTask are pre-completed
// (they must have a done status in the previous run, otherwise resume errors).
// With noCache: nothing is pre-completed.
func computePreCompletedTasks(workflow *core.Workflow, state *core.RunState, fromTask string, noCache bool) (map[string]bool, error) {
	if noCache {
		return map[string]bool{}, nil
	}

	if fromTask == "" {
		preCompleted := make(map[string]bool)
		for taskID, taskState := range state.Tasks {
			if taskState.Status == core.TaskStatusDone {
				preCompleted[taskID] = true
			}
		}
		return preCompleted, nil
	}

	if _, exists := workflow.Tasks[fromTask]; !exists {
		return nil, fmt.Errorf("resume: unknown task %q", fromTask)
	}

	graph, err := core.BuildGraph(workflow)
	if err != nil {
		return nil, fmt.Errorf("resume: %w", err)
	}
	downstream := graph.DownstreamSet(fromTask)

	preCompleted := make(map[string]bool)
	for taskID := range workflow.Tasks {
		if downstream[taskID] {
			continue
		}
		taskState, exists := state.Tasks[taskID]
		if !exists || taskState.Status != core.TaskStatusDone {
			status := core.TaskStatusCancelled
			if exists {
				status = taskState.Status
			}
			return nil, fmt.Errorf("resume: cannot resume from %q: task %q has status %q (must be done)", fromTask, taskID, status)
		}
		preCompleted[taskID] = true
	}
	return preCompleted, nil
}
