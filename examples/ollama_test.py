"""
End-to-end example using a local Ollama instance.

Run with:
    python examples/ollama_test.py

Output files land in ./output/
"""
import asyncio

from stagehand import WorkflowBuilder
from stagehand.adapters.executor import OllamaExecutor
from stagehand.adapters.storage.filesystem import FilesystemStorage

OUTPUT_DIR = "output"
STATE_DIR = ".stagehand/runs"


async def main() -> None:
    storage = FilesystemStorage(OUTPUT_DIR)
    executor = OllamaExecutor(storage=storage)

    print("=== Sequential: Haiku Pipeline ===")
    run_id = await (
        WorkflowBuilder("haiku-pipeline")
        .agent(
            "writer",
            executor,
            model="qwen2.5",
            system_prompt=(
                "You are a concise haiku writer. "
                "Write exactly one haiku (5-7-5 syllables). "
                "Always use the write_file tool to save your work."
            ),
            tools=["write_file"],
        )
        .task("draft", agent="writer", prompt="Write a haiku about the ocean at dawn. Save it to draft.md.")
        .task(
            "refine",
            agent="writer",
            prompt=(
                "Here is the draft haiku:\n\n"
                "{{ tasks.draft }}\n\n"
                "Refine it to be more vivid. Save the final version to final.md."
            ),
            after=["draft"],
        )
        .state_dir(STATE_DIR)
        .run()
    )
    print(f"run_id : {run_id}")
    print(f"output : {OUTPUT_DIR}/draft/draft.md")
    print(f"         {OUTPUT_DIR}/refine/final.md\n")

    print("=== Parallel: Pros / Cons Report ===")
    run_id2 = await (
        WorkflowBuilder("parallel-report")
        .agent(
            "writer",
            executor,
            model="qwen2.5",
            system_prompt=(
                "You are a technical writer. Write clear, concise content. "
                "Always use the write_file tool to save your output. "
                "Keep each section to 2-3 sentences maximum."
            ),
            tools=["write_file", "read_file"],
        )
        .task("pros", agent="writer", prompt="Write 2-3 sentences about the pros of remote work. Save to pros.md.")
        .task("cons", agent="writer", prompt="Write 2-3 sentences about the cons of remote work. Save to cons.md.")
        .task(
            "summary",
            agent="writer",
            prompt=(
                "Combine the following two sections into a balanced summary report.\n"
                "Save the result to summary.md.\n\n"
                "PROS:\n{{ tasks.pros }}\n\n"
                "CONS:\n{{ tasks.cons }}"
            ),
            after=["pros", "cons"],
        )
        .state_dir(STATE_DIR)
        .run()
    )
    print(f"run_id : {run_id2}")
    print(f"output : {OUTPUT_DIR}/pros/pros.md")
    print(f"         {OUTPUT_DIR}/cons/cons.md")
    print(f"         {OUTPUT_DIR}/summary/summary.md")


if __name__ == "__main__":
    asyncio.run(main())
