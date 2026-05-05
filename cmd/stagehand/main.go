package main

import (
	"context"
	"fmt"
	"os"
	"path/filepath"

	"github.com/anthropics/anthropic-sdk-go"
	"github.com/anthropics/anthropic-sdk-go/option"
	"github.com/janmarkuslanger/stagehand/adapters/executor"
	"github.com/janmarkuslanger/stagehand/adapters/storage"
	"github.com/janmarkuslanger/stagehand/core"
	"github.com/janmarkuslanger/stagehand/loader"
	"github.com/janmarkuslanger/stagehand/ports"
	"github.com/spf13/cobra"
)

const runStateDirectory = ".stagehand/runs"

func main() {
	if err := buildRootCommand().Execute(); err != nil {
		os.Exit(1)
	}
}

func buildRootCommand() *cobra.Command {
	root := &cobra.Command{
		Use:   "stagehand",
		Short: "Orchestrate multi-agent AI workflows",
	}
	root.AddCommand(
		buildRunCommand(),
		buildPlanCommand(),
		buildGraphCommand(),
		buildStatusCommand(),
		buildResumeCommand(),
	)
	return root
}

func buildRunCommand() *cobra.Command {
	var inputs []string
	cmd := &cobra.Command{
		Use:   "run <workflow.yaml>",
		Short: "Execute a workflow",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			parsedInputs, err := parseInputs(inputs)
			if err != nil {
				return err
			}
			workflowFile, err := filepath.Abs(args[0])
			if err != nil {
				return fmt.Errorf("resolve workflow path: %w", err)
			}
			workflow, err := loader.Load(workflowFile)
			if err != nil {
				return err
			}
			runID := core.GenerateRunID()
			runContext := core.NewRunContext(runID, parsedInputs)

			registry, err := buildExecutorRegistry(workflowFile, workflow, runID)
			if err != nil {
				return err
			}

			bridge := &executorBridge{executors: registry, workflow: workflow}
			scheduler := core.NewScheduler(bridge)
			fmt.Printf("Run ID: %s\n", runID)
			runErr := scheduler.Run(cmd.Context(), workflow, runContext)

			state := core.BuildRunState(runID, workflowFile, workflow, parsedInputs, runContext, runErr)
			if saveErr := core.Save(state, runStateDirectory); saveErr != nil {
				fmt.Fprintf(os.Stderr, "warning: could not save run state: %v\n", saveErr)
			}

			return runErr
		},
	}
	cmd.Flags().StringArrayVarP(&inputs, "input", "i", nil, "Input values as key=value or key=@file")
	return cmd
}

// buildExecutorRegistry constructs one ports.AgentExecutor for every distinct
// executor name declared across the workflow's agents.
func buildExecutorRegistry(workflowFile string, workflow *core.Workflow, runID string) (map[string]ports.AgentExecutor, error) {
	needed := make(map[string]struct{})
	for _, agent := range workflow.Agents {
		needed[agent.Executor] = struct{}{}
	}
	registry := make(map[string]ports.AgentExecutor, len(needed))
	for name := range needed {
		agentExecutor, err := buildSingleExecutor(name, workflowFile, workflow, runID)
		if err != nil {
			return nil, err
		}
		registry[name] = agentExecutor
	}
	return registry, nil
}

// buildSingleExecutor constructs a ports.AgentExecutor for the given executor name.
func buildSingleExecutor(executorName string, workflowFile string, workflow *core.Workflow, runID string) (ports.AgentExecutor, error) {
	switch executorName {
	case "claude":
		apiKey := os.Getenv("ANTHROPIC_API_KEY")
		if apiKey == "" {
			return nil, fmt.Errorf("ANTHROPIC_API_KEY environment variable is not set")
		}
		artifactRoot := resolveArtifactRoot(workflowFile, workflow.Output.Path, runID)
		artifactStorage := storage.NewFilesystemStorage(artifactRoot)
		client := anthropic.NewClient(option.WithAPIKey(apiKey))
		return executor.NewClaudeExecutor(&client, artifactStorage), nil
	case "ollama":
		host := os.Getenv("OLLAMA_HOST")
		if host == "" {
			host = "http://localhost:11434"
		}
		artifactRoot := resolveArtifactRoot(workflowFile, workflow.Output.Path, runID)
		artifactStorage := storage.NewFilesystemStorage(artifactRoot)
		return executor.NewOllamaExecutor(host, artifactStorage), nil
	default:
		return nil, fmt.Errorf("unknown executor %q: supported values are: claude, ollama", executorName)
	}
}

// resolveArtifactRoot returns the absolute path for artifact storage.
// If the workflow declares an output path it is resolved relative to the workflow file's directory.
// Otherwise a default path under .stagehand/runs is used, relative to CWD.
func resolveArtifactRoot(workflowFile string, outputPath string, runID string) string {
	if outputPath == "" {
		return filepath.Join(".stagehand", "runs", runID)
	}
	if filepath.IsAbs(outputPath) {
		return outputPath
	}
	return filepath.Join(filepath.Dir(workflowFile), outputPath)
}

// executorBridge adapts ports.AgentExecutor to core.TaskExecutor.
// It holds the workflow so it can resolve AgentID to the full agent config,
// and routes each task to the executor declared on its agent.
type executorBridge struct {
	executors map[string]ports.AgentExecutor
	workflow  *core.Workflow
}

func (b *executorBridge) Execute(ctx context.Context, request core.ExecutionRequest) (core.ExecutionResult, error) {
	agent := b.workflow.Agents[request.AgentID]
	agentExecutor, ok := b.executors[agent.Executor]
	if !ok {
		return core.ExecutionResult{}, fmt.Errorf("executor bridge: agent %q: unknown executor %q", request.AgentID, agent.Executor)
	}
	portsRequest := ports.ExecutionRequest{
		SystemPrompt: agent.SystemPrompt,
		Model:        agent.Model,
		Tools:        agent.Tools,
		Prompt:       request.Prompt,
		RunID:        request.RunID,
		TaskID:       request.TaskID,
	}
	result, err := agentExecutor.Execute(ctx, portsRequest)
	if err != nil {
		return core.ExecutionResult{}, err
	}
	return core.ExecutionResult{Output: result.Output, Files: result.Files}, nil
}

// Compile-time check that executorBridge satisfies core.TaskExecutor.
var _ core.TaskExecutor = (*executorBridge)(nil)
