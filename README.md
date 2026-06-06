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
  .task(task_id, *, agent, prompt, fn, after, outputs, secrets, retry, timeout)
  .state_dir(directory)   # where run state is persisted (default: .stagehand/runs)
  .concurrency(n)         # max tasks running simultaneously (default: unlimited)
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
| `timeout` | Seconds before a single attempt is cancelled. `None` = no limit. Sync `fn` tasks cannot be interrupted. |

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

To cap the number of tasks running at the same time, pass `max_concurrency`:

```python
scheduler = Scheduler(run_state_directory=".stagehand/runs", max_concurrency=4)
```

`None` (the default) means unlimited — all ready tasks start immediately. Setting it to `1` makes the scheduler execute one task at a time, regardless of the DAG structure.

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
`OllamaExecutor` and `ClaudeExecutor`. Both are built on the shared
`BaseAgentExecutor` (see [Custom executor](#custom-executor)), which provides
the agentic loop, the built-in storage tools and custom-tool dispatch — each
backend only implements the parts specific to its API.

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
| `logger` | `NullLogger()` | A `Logger` instance (e.g. `StdlibLogger()`) to receive retry warnings. |

```python
from stagehand.adapters.logger import StdlibLogger

executor = ClaudeExecutor(
    api_key="sk-ant-...",
    rate_limit_retries=5,
    rate_limit_delay=30.0,
    logger=StdlibLogger(),
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

### Artifact path validation

`FilesystemStorage` always rejects paths that contain `..` components to prevent path traversal attacks.

Optionally restrict which file extensions are allowed by passing `allowed_extensions`:

```python
from stagehand import FilesystemStorage

# Only .txt and .md files may be written
storage = FilesystemStorage("./output", allowed_extensions=[".txt", ".md"])
```

Extension matching is case-insensitive. Any path that violates a constraint raises `ValueError`
before any I/O is performed. Implement `validate_path` on a custom `ArtifactStorage` subclass
to apply your own rules:

```python
from stagehand import ArtifactStorage

class MyStorage(ArtifactStorage):
    def validate_path(self, path: str) -> None:
        if not path.startswith("safe/"):
            raise ValueError(f"path must be inside safe/: {path!r}")  # raise to block the write
    ...
```

### Custom executor

For a non-agentic backend, implement the `AgentExecutor` port directly:

```python
from stagehand.ports.executor import AgentExecutor, ExecutionRequest, ExecutionResult

class MyExecutor(AgentExecutor):
    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        output = call_my_model(request.prompt, request.system_prompt)
        return ExecutionResult(output=output)
```

To add a new **tool-using** backend, extend `BaseAgentExecutor` instead. It
implements the agent loop, the built-in `write_file` / `read_file` /
`list_files` tools and custom-tool dispatch via a [template method](https://en.wikipedia.org/wiki/Template_method_pattern);
you only fill in the backend-specific hooks:

```python
from stagehand.adapters.executor import BaseAgentExecutor
from stagehand.adapters.executor.base import ParsedTurn, ToolInvocation

class MyExecutor(BaseAgentExecutor):
    _label = "my executor"

    def _default_model(self): ...            # model id when none is requested
    def _init_messages(self, request): ...   # build the initial transcript
    def _serialize_tools(self, tools): ...    # ToolDefinition -> your SDK's format
    async def _call_model(self, model, request, messages, tools): ...
    def _parse_response(self, resp, request, step) -> ParsedTurn: ...
    def _format_tool_result(self, call, content, is_error): ...
    def _append_tool_results(self, messages, tool_results): ...
    # optional: _parse_arguments(raw) if tool args arrive encoded (e.g. JSON)
```

The built-in tool schemas live in one place — `BUILTIN_TOOLS` in
`stagehand/adapters/executor/base.py` — and each backend serialises them into
its own format via `_serialize_tools`.

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
| `stagehand/adapters/executor/` | `BaseAgentExecutor`, `ClaudeExecutor`, `OllamaExecutor` |
| `stagehand/adapters/storage/` | `FilesystemStorage` |
| `stagehand/adapters/secrets/` | `EnvSecretProvider` |
| `stagehand/builder.py` | `WorkflowBuilder` — primary public API |
