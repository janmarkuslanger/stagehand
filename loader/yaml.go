package loader

import (
	"fmt"
	"os"

	"github.com/janmarkuslanger/stagehand/core"
	"gopkg.in/yaml.v3"
)

// yamlWorkflow mirrors the top-level YAML structure for parsing.
type yamlWorkflow struct {
	Name    string                `yaml:"name"`
	Version string                `yaml:"version"`
	Output  yamlOutputConfig      `yaml:"output"`
	Agents  map[string]yamlAgent  `yaml:"agents"`
	Tasks   map[string]yamlTask   `yaml:"tasks"`
}

type yamlOutputConfig struct {
	Backend string `yaml:"backend"`
	Path    string `yaml:"path"`
}

type yamlAgent struct {
	Role         string   `yaml:"role"`
	SystemPrompt string   `yaml:"system_prompt"`
	Model        string   `yaml:"model"`
	Executor     string   `yaml:"executor"`
	Tools        []string `yaml:"tools"`
}

type yamlTask struct {
	Agent     string      `yaml:"agent"`
	DependsOn []string    `yaml:"depends_on"`
	Prompt    string      `yaml:"prompt"`
	Outputs   yamlOutputs `yaml:"outputs"`
	Secrets   []string    `yaml:"secrets"`
}

// yamlOutputs handles the three output spec forms: list, "dynamic", or {pattern: "..."}.
type yamlOutputs struct {
	spec core.OutputSpec
}

func (o *yamlOutputs) UnmarshalYAML(value *yaml.Node) error {
	switch value.Kind {
	case yaml.ScalarNode:
		if value.Value == "dynamic" {
			o.spec = core.DynamicOutputs{}
			return nil
		}
		return fmt.Errorf("loader: unknown scalar output spec %q (expected \"dynamic\")", value.Value)

	case yaml.SequenceNode:
		var files []string
		if err := value.Decode(&files); err != nil {
			return fmt.Errorf("loader: decode static outputs: %w", err)
		}
		o.spec = core.StaticOutputs(files)
		return nil

	case yaml.MappingNode:
		var m map[string]string
		if err := value.Decode(&m); err != nil {
			return fmt.Errorf("loader: decode pattern outputs: %w", err)
		}
		pattern, ok := m["pattern"]
		if !ok {
			return fmt.Errorf("loader: pattern output spec missing \"pattern\" key")
		}
		o.spec = core.PatternOutputs{Pattern: pattern}
		return nil
	}
	return fmt.Errorf("loader: unexpected YAML node kind for outputs field")
}

// Load parses a YAML workflow file and returns a validated core.Workflow.
func Load(path string) (*core.Workflow, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("loader: read %s: %w", path, err)
	}
	return Parse(data)
}

// Parse parses raw YAML bytes and returns a validated core.Workflow.
func Parse(data []byte) (*core.Workflow, error) {
	var raw yamlWorkflow
	if err := yaml.Unmarshal(data, &raw); err != nil {
		return nil, fmt.Errorf("loader: parse YAML: %w", err)
	}
	return convert(raw)
}

func convert(raw yamlWorkflow) (*core.Workflow, error) {
	if raw.Name == "" {
		return nil, fmt.Errorf("loader: workflow name is required")
	}

	agents := make(map[string]core.AgentConfig, len(raw.Agents))
	for id, a := range raw.Agents {
		if a.Executor == "" {
			return nil, fmt.Errorf("loader: agent %q: executor is required", id)
		}
		agents[id] = core.AgentConfig{
			Role:         a.Role,
			SystemPrompt: a.SystemPrompt,
			Model:        a.Model,
			Executor:     a.Executor,
			Tools:        a.Tools,
		}
	}

	tasks := make(map[string]*core.Task, len(raw.Tasks))
	for id, t := range raw.Tasks {
		if _, ok := agents[t.Agent]; !ok {
			return nil, fmt.Errorf("loader: task %s: unknown agent %q", id, t.Agent)
		}
		outputSpec := t.Outputs.spec
		if outputSpec == nil {
			outputSpec = core.DynamicOutputs{}
		}
		tasks[id] = &core.Task{
			AgentID:   t.Agent,
			DependsOn: t.DependsOn,
			Prompt:    t.Prompt,
			Outputs:   outputSpec,
			Secrets:   t.Secrets,
		}
	}

	for id, task := range tasks {
		for _, dependency := range task.DependsOn {
			if _, ok := tasks[dependency]; !ok {
				return nil, fmt.Errorf("loader: task %s: unknown dependency %q", id, dependency)
			}
		}
	}

	workflow := &core.Workflow{
		Name:    raw.Name,
		Version: raw.Version,
		Output: core.OutputConfig{
			Backend: raw.Output.Backend,
			Path:    raw.Output.Path,
		},
		Agents: agents,
		Tasks:  tasks,
	}

	// Validate the graph structure (detects cycles).
	if _, err := core.BuildGraph(workflow); err != nil {
		return nil, fmt.Errorf("loader: %w", err)
	}

	return workflow, nil
}
