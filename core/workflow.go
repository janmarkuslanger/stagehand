package core

// Workflow is the top-level unit of execution.
type Workflow struct {
	Name    string
	Version string
	Output  OutputConfig
	Agents  map[string]AgentConfig
	Tasks   map[string]*Task
}

// AgentConfig defines an agent's role, model, executor backend, and available tools.
type AgentConfig struct {
	Role         string
	SystemPrompt string
	Model        string
	Executor     string
	Tools        []string
}

// Task is a single node in the workflow DAG.
type Task struct {
	AgentID   string
	DependsOn []string
	Prompt    string
	Outputs   OutputSpec
	Secrets   []string
}

// OutputConfig specifies where artifacts are stored.
type OutputConfig struct {
	Backend string
	Path    string
}

// OutputSpec is implemented by static, dynamic, and pattern output declarations.
type OutputSpec interface{ isOutputSpec() }

// StaticOutputs declares a fixed list of output file names.
type StaticOutputs []string

// DynamicOutputs indicates the agent decides which files to produce at runtime.
type DynamicOutputs struct{}

// PatternOutputs declares output files via a glob pattern.
type PatternOutputs struct{ Pattern string }

func (StaticOutputs) isOutputSpec()  {}
func (DynamicOutputs) isOutputSpec() {}
func (PatternOutputs) isOutputSpec() {}
