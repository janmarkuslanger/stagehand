# stagehand

Stagehand orchestrates multi-agent AI workflows in pure Python. Each workflow is a directed acyclic graph (DAG) of tasks. Tasks with no dependencies run in parallel; tasks with dependencies wait until their upstream tasks complete.

---

## Installation

```bash
pip install stagehand
```

Requires Python 3.11+.

---

## Quickstart

```python
import asyncio
from stagehand import WorkflowBuilder
from stagehand.adapters.executor import OllamaExecutor

async def main():
    run_id = await (
        WorkflowBuilder("haiku-pipeline")
        .agent("writer", OllamaExecutor(), model="qwen2.5",
               system_prompt="You are a haiku writer.",
               tools=["write_file"])
        .task("draft",  agent="writer", prompt="Write a haiku about the ocean at dawn.")
        .task("refine", agent="writer",
              prompt="Refine this haiku:\n\n{{ tasks.draft }}",
              after=["draft"])
        .run()
    )
    print(f"Done — run {run_id}")

asyncio.run(main())
```

---

## Concepts

### Builder

`WorkflowBuilder` is the primary API. It lets you define agents and tasks in code, then run the workflow with a single `await`.

```python
WorkflowBuilder(name, version="1")
  .agent(agent_id, executor, *, model, system_prompt, role, tools)
  .task(task_id, *, agent, prompt, after, outputs, secrets)
  .state_dir(directory)   # where run state is persisted (default: .stagehand/runs)
  .run(inputs={})         # returns run_id
```

`.build()` returns a `Workflow` object without running it, useful if you want to pass it to a `Scheduler` directly.

---

### Agents

An agent is a named AI persona with an executor, a model, and a system prompt.

```python
.agent(
    "writer",
    OllamaExecutor(),
    model="qwen2.5",
    system_prompt="You are a concise technical writer.",
    tools=["write_file", "read_file"],
)
```

Different agents in the same workflow can use different executors:

```python
WorkflowBuilder("pipeline")
.agent("drafter", OllamaExecutor(), model="qwen2.5")
.agent("reviewer", ClaudeExecutor(api_key="..."), model="claude-opus-4-5")
```

---

### Tasks

A task is a single unit of work assigned to an agent. Tasks form the nodes of the DAG.

```python
.task("draft",  agent="writer", prompt="Write a short intro to Python.")
.task("review", agent="reviewer",
      prompt="Review this draft:\n\n{{ tasks.draft }}",
      after=["draft"])
```

| Parameter | Required | Description |
|---|---|---|
| `agent` | yes | ID of the agent that runs this task |
| `prompt` | yes | Message sent to the agent (supports `{{ }}` expressions) |
| `after` | no | List of task IDs this task waits for |
| `outputs` | no | `StaticOutputs`, `DynamicOutputs`, or `PatternOutputs` |
| `secrets` | no | List of secret names to resolve at runtime |

Tasks with no `after` (or whose dependencies are all complete) start immediately. Multiple ready tasks run in parallel.

---

### Template expressions

Prompts support `{{ }}` expressions to inject values from previous tasks or runtime inputs.

| Expression | Resolves to |
|---|---|
| `{{ input.key }}` | A value passed via `inputs={"key": "..."}` |
| `{{ tasks.id }}` | The text output of a completed task |
| `{{ tasks.id.files }}` | Newline-separated list of file paths produced by a task |
| `{{ tasks.id.filename_md }}` | Path of a specific file, identified by its slug (`filename.md` → `filename_md`) |

---

### Outputs

The `outputs` parameter of `.task()` declares what files a task produces.

```python
from stagehand import StaticOutputs, DynamicOutputs, PatternOutputs

# Exact file names known upfront
.task("t1", agent="a", prompt="...", outputs=StaticOutputs(["report.md"]))

# Agent decides at runtime (default)
.task("t2", agent="a", prompt="...", outputs=DynamicOutputs())

# Collect by glob after the task finishes
.task("t3", agent="a", prompt="...", outputs=PatternOutputs(pattern="**/*.md"))
```

---

## Executors

An executor is the AI backend that drives a task. Stagehand ships two:

### `OllamaExecutor`

Runs models locally via [Ollama](https://ollama.com). No API key required.

```python
from stagehand.adapters.executor import OllamaExecutor
from stagehand.adapters.storage.filesystem import FilesystemStorage

executor = OllamaExecutor(
    host="http://localhost:11434",   # default
    storage=FilesystemStorage("./output"),
)
```

```bash
ollama pull qwen2.5
ollama serve
```

Models with reliable tool use: `qwen2.5`, `llama3.1`, `llama3.2`, `mistral-nemo`.

### `ClaudeExecutor`

Uses the [Anthropic Messages API](https://docs.anthropic.com/en/api/messages).

```python
from stagehand.adapters.executor import ClaudeExecutor
from stagehand.adapters.storage.filesystem import FilesystemStorage

executor = ClaudeExecutor(
    api_key="sk-ant-...",            # or set ANTHROPIC_API_KEY
    storage=FilesystemStorage("./output"),
)
```

The default model is `claude-opus-4-5`.

### Custom tools

Pass extra tools to `ClaudeExecutor` via `extra_tools`:

```python
from stagehand import ToolDefinition
from stagehand.adapters.executor import ClaudeExecutor

my_tool = ToolDefinition(
    name="fetch_ticket",
    description="Fetch a Linear ticket by ID.",
    input_schema={"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
    handler=lambda args: fetch_from_linear(args["id"]),
)

executor = ClaudeExecutor(api_key="...", extra_tools=[my_tool])
```

### Custom executor

Implement `AgentExecutor` to add any backend:

```python
from stagehand.ports.executor import AgentExecutor, ExecutionRequest, ExecutionResult

class MyExecutor(AgentExecutor):
    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        output = call_my_model(request.prompt, request.system_prompt)
        return ExecutionResult(output=output)
```

---

## Resume

A failed or interrupted run can be resumed. Completed tasks are skipped.

```python
from stagehand import Scheduler

scheduler = Scheduler(run_state_directory=".stagehand/runs")
workflow = builder.build()

run_id = await scheduler.run(workflow)
# ... if it fails or you want to retry:
await scheduler.resume(run_id, workflow)
```

---

## Examples

### Sequential

```python
run_id = await (
    WorkflowBuilder("sequential")
    .agent("writer", OllamaExecutor(), model="qwen2.5",
           system_prompt="You write haiku.", tools=["write_file"])
    .task("draft",  agent="writer", prompt="Write a haiku about the ocean. Save to draft.md.")
    .task("refine", agent="writer",
          prompt="Refine this:\n\n{{ tasks.draft }}\n\nSave to final.md.",
          after=["draft"])
    .run()
)
```

```
draft  →  refine
```

### Parallel

```python
run_id = await (
    WorkflowBuilder("parallel")
    .agent("writer", OllamaExecutor(), model="qwen2.5",
           system_prompt="You write clearly.", tools=["write_file"])
    .task("pros",    agent="writer", prompt="Write pros of remote work. Save to pros.md.")
    .task("cons",    agent="writer", prompt="Write cons of remote work. Save to cons.md.")
    .task("summary", agent="writer",
          prompt="Combine:\n\nPROS:\n{{ tasks.pros }}\n\nCONS:\n{{ tasks.cons }}\n\nSave to summary.md.",
          after=["pros", "cons"])
    .run()
)
```

```
pros  ─┐
       ├→  summary
cons  ─┘
```

---

## Architecture

Stagehand uses a **ports-and-adapters** (hexagonal) architecture. The dependency rule is strict:

```
core/     →  nothing external (stdlib only)
ports/    →  nothing (interfaces only)
adapters/ →  ports/ only
builder   →  core/ + ports/
```

| Package | Responsibility |
|---|---|
| `stagehand/core/` | Domain types, DAG, scheduler, run state, template engine |
| `stagehand/ports/` | ABCs: `AgentExecutor`, `ArtifactStorage`, `SecretProvider` |
| `stagehand/adapters/executor/` | `ClaudeExecutor`, `OllamaExecutor` |
| `stagehand/adapters/storage/` | `FilesystemStorage` |
| `stagehand/adapters/secrets/` | `EnvSecretProvider` |
| `stagehand/builder.py` | `WorkflowBuilder` — primary public API |
