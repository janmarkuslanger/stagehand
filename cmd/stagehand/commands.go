package main

import (
	"fmt"
	"os"
	"strings"

	"github.com/janmarkuslanger/stagehand/core"
	"github.com/janmarkuslanger/stagehand/loader"
	"github.com/spf13/cobra"
)

func buildPlanCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "plan <workflow.yaml>",
		Short: "Validate and print the execution plan without running",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			workflow, err := loader.Load(args[0])
			if err != nil {
				return err
			}
			graph, err := core.BuildGraph(workflow)
			if err != nil {
				return err
			}
			fmt.Printf("Workflow: %s\n\n", workflow.Name)
			fmt.Println("Execution order:")
			for _, id := range graph.TopologicalOrder() {
				task := workflow.Tasks[id]
				deps := "none"
				if len(task.DependsOn) > 0 {
					deps = strings.Join(task.DependsOn, ", ")
				}
				fmt.Printf("  %-20s depends on: %s\n", id, deps)
			}
			return nil
		},
	}
}

func buildGraphCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "graph <workflow.yaml>",
		Short: "Print the task dependency graph",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			workflow, err := loader.Load(args[0])
			if err != nil {
				return err
			}
			fmt.Printf("Workflow: %s\n\n", workflow.Name)
			fmt.Println("Tasks:")
			for id, task := range workflow.Tasks {
				fmt.Printf("  %s\n", id)
				fmt.Printf("    agent:      %s\n", task.AgentID)
				if len(task.DependsOn) > 0 {
					fmt.Printf("    depends_on: %s\n", strings.Join(task.DependsOn, ", "))
				}
				switch spec := task.Outputs.(type) {
				case core.StaticOutputs:
					fmt.Printf("    outputs:    %s\n", strings.Join(spec, ", "))
				case core.DynamicOutputs:
					fmt.Printf("    outputs:    dynamic\n")
				case core.PatternOutputs:
					fmt.Printf("    outputs:    pattern: %s\n", spec.Pattern)
				}
			}
			return nil
		},
	}
}

func buildStatusCommand() *cobra.Command {
	return &cobra.Command{
		Use:   "status <run-id>",
		Short: "Show the status of a previous run",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			state, err := core.Load(args[0], runStateDirectory)
			if err != nil {
				return err
			}
			fmt.Printf("Run:      %s\n", state.ID)
			fmt.Printf("Workflow: %s\n\n", state.Workflow)
			for id, task := range state.Tasks {
				fmt.Printf("  %-20s %s\n", id, task.Status)
				if task.Error != "" {
					fmt.Printf("    error: %s\n", task.Error)
				}
			}
			return nil
		},
	}
}


func parseInputs(raw []string) (map[string]string, error) {
	result := make(map[string]string, len(raw))
	for _, item := range raw {
		key, value, found := strings.Cut(item, "=")
		if !found {
			return nil, fmt.Errorf("invalid input %q: expected key=value or key=@file", item)
		}
		if strings.HasPrefix(value, "@") {
			data, err := os.ReadFile(strings.TrimPrefix(value, "@"))
			if err != nil {
				return nil, fmt.Errorf("input %s: read file: %w", key, err)
			}
			value = string(data)
		}
		result[key] = value
	}
	return result, nil
}
