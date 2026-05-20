# stagehand

Stagehand orchestrates multi-agent AI workflows in pure Python. Each workflow is a directed acyclic graph (DAG) of tasks. Tasks with no dependencies run in parallel; tasks with dependencies wait until their upstream tasks complete.

---

## Installation

```bash
pip install stagehand-ai
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

A task is a single unit of work in the DAG. There are two kinds:

**Agent task** — runs a prompt against an AI agent:

```python
.task("draft",  agent="writer", prompt="Write a short intro to Python.")
.task("review", agent="reviewer",
      prompt="Review this draft:\n\n{{ tasks.draft }}",
      after=["draft"])
```

**Deterministic task** — runs a plain Python callable (sync or async). No agent or prompt needed:

```python
async def fetch_tickets(ctx):
    return await linear_client.get_tickets()  # returns str or TaskResult

.task("fetch", fn=fetch_tickets)
.task("analyze", agent="analyst",
      prompt="Tickets:\n\n{{ tasks.fetch }}\n\nSummarise.",
      after=["fetch"])
```

The callable receives a `RunContext` (access to `inputs` and previous task results) and must return a `str` or `TaskResult`.

| Parameter | Description |
|---|---|
| `agent` | ID of the agent that runs this task *(required unless `fn` is set)* |
| `prompt` | Message sent to the agent (supports `{{ }}` expressions) *(required unless `fn` is set)* |
| `fn` | Python callable to run directly *(required unless `agent`/`prompt` are set)* |
| `after` | List of task IDs this task waits for |
| `outputs` | `StaticOutputs`, `DynamicOutputs`, or `PatternOutputs` |
| `secrets` | List of secret names to resolve at runtime |
| `retry` | `RetryPolicy` — how many times to retry on failure |

Tasks with no `after` (or whose dependencies are all complete) start immediately. Multiple ready tasks run in parallel.

---

### Retry

Pass a `RetryPolicy` to a task to retry it automatically on failure. Downstream tasks are only cancelled once all attempts are exhausted.

```python
from stagehand import RetryPolicy

.task(
    "fetch",
    agent="worker",
    prompt="Fetch the latest report.",
    retry=RetryPolicy(max_attempts=3, delay=2.0),
)
```

| Parameter | Default | Description |
|---|---|---|
| `max_attempts` | `1` | Total attempts including the first (1 = no retry) |
| `delay` | `0.0` | Seconds to wait between attempts |

---

### Logging

By default the scheduler is silent. Pass a `Logger` to see workflow and task lifecycle events.

```python
import logging
from stagehand import WorkflowBuilder, StdlibLogger

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

run_id = await (
    WorkflowBuilder("my-pipeline")
    .agent("worker", executor, model="claude-opus-4-5")
    .task("analyse", agent="worker", prompt="...")
    .logger(StdlibLogger())   # <-- add this
    .run()
)
```

`StdlibLogger` wraps Python's stdlib `logging` module and, by default, raises `httpx` and `httpcore` to `WARNING` so their per-request `INFO` lines don't drown out workflow events.

```
INFO  workflow 'my-pipeline' started [run=sh-20260520-a1b2]
INFO  task 'analyse' starting
INFO  task 'analyse' done
INFO  workflow 'my-pipeline' finished [run=sh-20260520-a1b2]
```

| Parameter | Default | Description |
|---|---|---|
| `name` | `"stagehand"` | Logger name used with `logging.getLogger` |
| `suppress_http_logs` | `True` | Raises httpx/httpcore to WARNING to suppress per-request noise |

To silence everything, omit `.logger()` (the default) or pass `NullLogger()` explicitly.

When using `Scheduler` directly, pass the logger to its constructor:

```python
from stagehand import Scheduler, StdlibLogger

scheduler = Scheduler(run_state_directory=".stagehand/runs", logger=StdlibLogger())
```

To write a custom logger — for example to route events to a structured sink — implement the `Logger` port:

```python
from stagehand import Logger

class MyLogger(Logger):
    def debug(self, message: str) -> None: ...
    def info(self, message: str) -> None: ...
    def warning(self, message: str) -> None: ...
    def error(self, message: str) -> None: ...
```

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

#### Rate-limit retries

`ClaudeExecutor` automatically retries `messages.create` calls that receive a 429 response. Only the individual API call is retried — not the entire agentic loop — so a rate-limit mid-task retries only the current step.

| Parameter | Default | Description |
|---|---|---|
| `rate_limit_retries` | `3` | Maximum number of attempts per `messages.create` call (1 = no retries). |
| `rate_limit_delay` | `60.0` | Seconds to wait between attempts. |

```python
executor = ClaudeExecutor(
    api_key="sk-ant-...",
    rate_limit_retries=5,
    rate_limit_delay=30.0,
)
```

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
