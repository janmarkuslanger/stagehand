# stagehand

Stagehand orchestrates multi-agent AI workflows in pure Python. Each workflow is a directed acyclic graph (DAG) of tasks. Tasks with no dependencies run in parallel; tasks with dependencies wait until their upstream tasks complete.

**New here?** Jump to [Get started](#get-started) for a copy-paste first run.
**Looking for details?** [Concepts](#concepts) · [Executors](#executors) · [Resume](#resume) · [Examples](#examples) · [Architecture](#architecture)

Concepts at a glance: [Builder](#builder) · [Agents](#agents) · [Tasks](#tasks) · [Retry](#retry) · [Structured data](#structured-data) · [Conditional tasks](#conditional-tasks) · [Loops](#loops) · [Fan-out / map](#fan-out--map) · [Logging](#logging) · [Caching](#caching) · [Template expressions](#template-expressions) · [Outputs](#outputs)

---

## Get started

**1. Install Stagehand** (Python 3.11+):

```bash
pip install stagehand-ai
```

**2. Get a local model running.** Stagehand runs models locally through
[Ollama](https://ollama.com) — no API key, nothing to sign up for:

```bash
# install Ollama from https://ollama.com, then:
ollama pull qwen2.5     # download a model that's good at following instructions
ollama serve            # start the local server (leave this running)
```

**3. Run your first workflow.** Save this as `haiku.py` and run `python haiku.py`:

```python
import asyncio
from stagehand import WorkflowBuilder, load_state
from stagehand.adapters.executor import OllamaExecutor

async def main():
    # Two tasks: "draft" writes a haiku, "refine" improves it.
    # {{ tasks.draft }} injects the draft's output into the next prompt.
    run_id = await (
        WorkflowBuilder("haiku-pipeline")
        .agent("writer", OllamaExecutor(), model="qwen2.5",
               system_prompt="You are a haiku writer. Reply with only the haiku.")
        .task("draft",  agent="writer", prompt="Write a haiku about the ocean at dawn.")
        .task("refine", agent="writer",
              prompt="Refine this haiku to be more vivid:\n\n{{ tasks.draft }}",
              after=["draft"])
        .run()
    )

    # Read the finished result back from the saved run state.
    final = load_state(run_id, ".stagehand/runs").tasks["refine"].output
    print(final)

asyncio.run(main())
```

That's it — no files, no API keys, no storage to configure. The two tasks run
in order (`refine` waits for `draft`), and you see the refined haiku printed.

> **Want the agent to write files instead?** Give it the `write_file` tool and a
> storage backend — see [Executors](#executors) and [Outputs](#outputs).

---

## Concepts

### Builder

`WorkflowBuilder` is the primary API. It lets you define agents and tasks in code, then run the workflow with a single `await`.

```python
WorkflowBuilder(name, version="1")
  .agent(agent_id, executor, *, model, system_prompt, role, tools)
  .task(task_id, *, agent, prompt, fn, after, outputs, secrets, retry, timeout,
        when, over, loop_until, max_iterations)
  .state_dir(directory)   # where run state is persisted (default: .stagehand/runs)
  .concurrency(n)         # max tasks running simultaneously (default: unlimited)
  .cache(result_cache)    # reuse identical agent work across runs (default: off)
  .run(inputs={})         # returns run_id
```

Beyond a static DAG, tasks support runtime dynamics: **conditionals**
(`when`), **loops** (`loop_until` / `max_iterations`) and **fan-out / map**
(`over`), plus a structured `data` channel between tasks. See
[Structured data](#structured-data), [Conditional tasks](#conditional-tasks),
[Loops](#loops) and [Fan-out / map](#fan-out--map).

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
| `when` | Predicate `(ctx) -> bool`. When falsy the task is skipped (see [Conditional tasks](#conditional-tasks)) |
| `loop_until` | Predicate `(ctx, result) -> bool`. Re-runs the body until truthy (see [Loops](#loops)) |
| `max_iterations` | Maximum loop iterations (default `1` = single run) |
| `over` | Callable `(ctx) -> list`. Fans the task out into one child per item (see [Fan-out / map](#fan-out--map)) |

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

### Structured data

Every task result carries a textual `output` *and* an optional structured
`data` value (any Python object). Deterministic `fn` tasks set it by returning a
`TaskResult`, or implicitly when they return a non-string:

```python
def fetch(ctx):
    return TaskResult(output="3 tickets", data=["T-1", "T-2", "T-3"])

# or simply:
def fetch(ctx):
    return ["T-1", "T-2", "T-3"]   # becomes data=[...], output="['T-1', ...]"
```

`data` is what enables branching and fan-out below, since you can pass typed
values between tasks instead of only text. Templates can reach into it with
`{{ tasks.id.data }}` and dotted paths (see [Template expressions](#template-expressions)).

Runtime `inputs` are no longer limited to strings — any value may be passed via
`inputs={...}` and read with `ctx.get_input("key")`.

> **Note:** `data` is in-memory only. Persisted run state (used by `resume`)
> keeps `output` and `files`, not `data`.

---

### Conditional tasks

Pass `when` — a predicate `(ctx) -> bool` (sync or async) — to skip a task at
runtime. A skipped task produces an empty result and is recorded with status
`skipped`.

```python
.task("draft", agent="writer", prompt="Write a draft.")
.task(
    "publish",
    agent="writer",
    prompt="Publish:\n\n{{ tasks.draft }}",
    after=["draft"],
    when=lambda ctx: "APPROVED" in ctx.get_task_result("draft").output,
)
```

**Skips do not cascade.** Only the task whose `when` is falsy is skipped; its
dependents still become ready (and see an empty `{{ tasks.skipped }}`). To prune
a whole branch, put a `when` on the downstream tasks too.

---

### Loops

Pass `loop_until` — a predicate `(ctx, result) -> bool` — together with
`max_iterations` to re-run a task's body until the predicate returns truthy or
the cap is reached. The final iteration's result becomes the task result.

```python
.task(
    "refine",
    agent="writer",
    prompt="Iteration {{ loop.iteration }}. Improve the previous draft:\n\n{{ loop.previous }}",
    loop_until=lambda ctx, result: "DONE" in result.output,
    max_iterations=5,
)
```

Inside a looping agent prompt, `{{ loop.iteration }}` (0-based) and
`{{ loop.previous }}` (previous iteration's output, empty on the first pass) are
available. Loops are node-internal — the DAG stays acyclic. `loop_until` cannot
be combined with `over`.

---

### Fan-out / map

Pass `over` — a callable `(ctx) -> list` — to fan a task out into one child per
item at runtime. Agent prompts reference the current item with `{{ item }}`;
`fn` callables receive it as a second argument, `fn(ctx, item)`.

```python
.task("fetch", fn=lambda ctx: ["en", "de", "fr"])
.task(
    "translate",
    agent="writer",
    prompt="Translate the homepage into: {{ item }}",
    over=lambda ctx: ctx.get_task_result("fetch").data,
    after=["fetch"],
)
.task("bundle", agent="writer",
      prompt="Combine all translations:\n\n{{ tasks.translate }}",
      after=["translate"])
```

The children run in parallel (subject to `concurrency`). The map task's own
result aggregates them: `data` is the list of child results in order, and
`output` is their outputs joined by newlines — so downstream tasks depending on
the map task see the combined result. If any child fails, the map task (and its
downstream) fails. An empty list completes the map immediately with empty
results.

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

---

### Caching

Attach a `ResultCache` to reuse identical agent work across runs. When a task's
resolved request — `model`, `system_prompt`, `tools` and `prompt` (after
template substitution) — matches a previous one, the stored result is returned
instead of calling the backend again. This is the big lever during development:
re-running a workflow after editing one downstream task no longer recomputes the
unchanged upstream tasks.

```python
from stagehand import WorkflowBuilder, FilesystemCache

run_id = await (
    WorkflowBuilder("pipeline")
    .agent("writer", executor, model="qwen2.5")
    .task("draft",  agent="writer", prompt="Write an intro.")
    .task("review", agent="writer", prompt="Review:\n\n{{ tasks.draft }}", after=["draft"])
    .cache(FilesystemCache())   # <-- opt in
    .run()
)
```

Stagehand ships two caches:

| Cache | Persistence | Use |
|---|---|---|
| `InMemoryCache` | Process-local dict | Tests; dedup within one run |
| `FilesystemCache` | One JSON file per key (default `.stagehand/cache`) | Survives restarts — the dev loop |

The cache key is a SHA-256 of the request's `model`, `system_prompt`, sorted
`tools` and resolved `prompt`. `run_id` and `task_id` are excluded, so the key
is stable across runs. Because the `prompt` is the already-resolved string, the
key changes automatically when an upstream task produces different output — the
cache self-invalidates along the DAG.

> **Caveats.** Caching is opt-in because it assumes "identical input →
> identical output", which is a deliberate choice for non-deterministic LLM
> backends. Only **agent tasks** are cached — deterministic `fn` tasks (often
> used for fetches and side effects) always run. On a cache hit the stored
> `output` and `files` are reused, but tools with external side effects are
> **not** invoked again. Like persisted run state, `FilesystemCache` keeps
> `output` and `files`, not the in-memory `data` value.

To plug in your own backend (Redis, S3, …), implement the `ResultCache` port:

```python
from stagehand import ResultCache, ExecutionResult

class MyCache(ResultCache):
    async def get(self, key: str) -> ExecutionResult | None: ...
    async def set(self, key: str, result: ExecutionResult) -> None: ...
```

When using `Scheduler` directly, pass the cache to its constructor:

```python
from stagehand import Scheduler, FilesystemCache

scheduler = Scheduler(run_state_directory=".stagehand/runs", cache=FilesystemCache())
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
| `{{ input.key.field }}` | A nested field of a structured input value |
| `{{ tasks.id }}` | The text output of a completed task |
| `{{ tasks.id.files }}` | Newline-separated list of file paths produced by a task |
| `{{ tasks.id.filename_md }}` | Path of a specific file, identified by its slug (`filename.md` → `filename_md`) |
| `{{ tasks.id.data }}` | The structured `data` value of a task |
| `{{ tasks.id.data.field }}` | A nested field of a task's `data` (dicts, list indices, attributes) |
| `{{ item }}` / `{{ item.field }}` | The current item inside a fan-out (`over`) task |
| `{{ loop.iteration }}` / `{{ loop.previous }}` | The iteration index / previous output inside a loop task |

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

Runnable scripts live in [`examples/`](examples/):

- [`ollama_test.py`](examples/ollama_test.py) — sequential and parallel pipelines.
- [`full_pipeline.py`](examples/full_pipeline.py) — kitchen-sink demo combining a
  deterministic `fn` task with structured `data`, fan-out (`over`), a conditional
  task (`when`), structured-data templates, and an iterative loop (`loop_until`).

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
| `stagehand/ports/` | ABCs: `AgentExecutor`, `ArtifactStorage`, `SecretProvider`, `ResultCache` |
| `stagehand/adapters/executor/` | `BaseAgentExecutor`, `ClaudeExecutor`, `OllamaExecutor` |
| `stagehand/adapters/storage/` | `FilesystemStorage` |
| `stagehand/adapters/secrets/` | `EnvSecretProvider` |
| `stagehand/adapters/cache/` | `InMemoryCache`, `FilesystemCache` |
| `stagehand/builder.py` | `WorkflowBuilder` — primary public API |
