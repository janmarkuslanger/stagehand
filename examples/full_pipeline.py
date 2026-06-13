"""
Kitchen-sink example exercising every core capability in one workflow,
running locally against Ollama.

It builds a tiny "ocean-at-dawn" content pipeline:

    topics ──▶ sections (fan-out)        ──┐
       │                                    ├─▶ assemble
       └─────▶ editor_note (conditional) ──┘        │
                                                     ▼
                                                  polish (loop)

Features shown:
  * deterministic ``fn`` task returning structured ``data``      (topics)
  * fan-out with ``over`` + ``{{ item }}``                       (sections)
  * conditional task with ``when``                              (editor_note)
  * structured-data template navigation ``{{ tasks.x.data.0 }}``
  * iterative refinement with ``loop_until`` + ``{{ loop.* }}``  (polish)
  * parallelism cap + structured logging

Run with:
    ollama pull qwen2.5 && ollama serve
    python examples/full_pipeline.py

Output files land in ./output/
"""
import asyncio
import logging

from stagehand import RetryPolicy, StdlibLogger, TaskResult, WorkflowBuilder
from stagehand.adapters.executor import OllamaExecutor
from stagehand.adapters.storage.filesystem import FilesystemStorage

OUTPUT_DIR = "output"
STATE_DIR = ".stagehand/runs"
MODEL = "qwen2.5"


def fetch_topics(ctx) -> TaskResult:
    """Deterministic source task — no LLM. Returns a structured list via ``data``."""
    topics = ["tide pools", "the rising sun", "circling seabirds"]
    return TaskResult(output=f"{len(topics)} topics", data=topics)


def build_pipeline(executor) -> WorkflowBuilder:
    """Defines the workflow. Kept separate from ``main`` so it can be unit-built
    with any executor (see the ``__main__`` smoke check at the bottom)."""
    return (
        WorkflowBuilder("ocean-at-dawn")
        .agent(
            "writer",
            executor,
            model=MODEL,
            system_prompt=(
                "You are a vivid but concise nature writer. "
                "Write 2-3 sentences. Always save your work with the write_file tool."
            ),
            tools=["write_file", "read_file"],
        )
        # 1. Deterministic fn task: produces structured data (a list of topics).
        .task("topics", fn=fetch_topics)
        # 2. Fan-out: one child per topic. Each child sees its item via {{ item }}.
        #    The map task's result aggregates all children.
        .task(
            "sections",
            agent="writer",
            prompt=(
                "Write a short, vivid paragraph about '{{ item }}' at the ocean at dawn. "
                "Save it to section.md."
            ),
            over=lambda ctx: ctx.get_task_result("topics").data,
            after=["topics"],
        )
        # 3. Conditional task: only runs when there are enough topics. Note the
        #    structured-data navigation {{ tasks.topics.data.0 }} in the prompt.
        .task(
            "editor_note",
            agent="writer",
            prompt=(
                "Write a one-line editor's note teasing a piece that opens with "
                "'{{ tasks.topics.data.0 }}'. Save it to note.md."
            ),
            after=["topics"],
            when=lambda ctx: len(ctx.get_task_result("topics").data) >= 3,
        )
        # 4. Assemble everything (fan-out aggregate + the conditional note).
        .task(
            "assemble",
            agent="writer",
            prompt=(
                "Combine these sections into one flowing piece, prefixed by the editor's note.\n\n"
                "NOTE:\n{{ tasks.editor_note }}\n\n"
                "SECTIONS:\n{{ tasks.sections }}\n\n"
                "Save the result to assembled.md."
            ),
            after=["sections", "editor_note"],
            retry=RetryPolicy(max_attempts=2, delay=1.0),
        )
        # 5. Loop: iteratively polish until it looks done, capped at 3 passes.
        #    The prompt references the previous iteration via {{ loop.* }}.
        .task(
            "polish",
            agent="writer",
            prompt=(
                "Polish pass #{{ loop.iteration }}.\n"
                "Previous version:\n{{ loop.previous }}\n\n"
                "If it is already publishable, reply with the text and the word DONE. "
                "Otherwise tighten the prose. Save the current version to polished.md."
            ),
            after=["assemble"],
            loop_until=lambda ctx, result: "DONE" in result.output,
            max_iterations=3,
        )
        .concurrency(3)
        .logger(StdlibLogger())
        .state_dir(STATE_DIR)
    )


async def main() -> None:
    storage = FilesystemStorage(OUTPUT_DIR)
    executor = OllamaExecutor(storage=storage)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    run_id = await build_pipeline(executor).run()

    print(f"\nrun_id : {run_id}")
    print(f"output : {OUTPUT_DIR}/sections#*/section.md  (one per topic)")
    print(f"         {OUTPUT_DIR}/editor_note/note.md")
    print(f"         {OUTPUT_DIR}/assemble/assembled.md")
    print(f"         {OUTPUT_DIR}/polish/polished.md")


if __name__ == "__main__":
    asyncio.run(main())
