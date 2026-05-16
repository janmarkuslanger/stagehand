from __future__ import annotations

import re

_TEMPLATE_PATTERN = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")


def resolve(template: str, context: "RunContext") -> str:  # type: ignore[name-defined]
    """Replaces all {{ }} template expressions using values from the RunContext.

    Supported expressions:
      {{ input.key }}         → runtime input value
      {{ tasks.id }}          → primary output of a completed task
      {{ tasks.id.files }}    → newline-separated list of files produced by a task
      {{ tasks.id.slug }}     → path of a specific output file matched by slug
    """
    errors: list[str] = []

    def replacer(match: re.Match) -> str:
        expression = match.group(1).strip()
        try:
            return _resolve_expression(expression, context)
        except ValueError as exc:
            errors.append(str(exc))
            return match.group(0)

    result = _TEMPLATE_PATTERN.sub(replacer, template)
    if errors:
        raise ValueError(errors[0])
    return result


def _resolve_expression(expression: str, context: "RunContext") -> str:  # type: ignore[name-defined]
    parts = expression.split(".", 2)
    namespace = parts[0]

    if namespace == "input":
        if len(parts) < 2:
            raise ValueError(f"template: invalid input reference {expression!r}")
        value = context.get_input(parts[1])
        if value is None:
            raise ValueError(f"template: input {parts[1]!r} not found")
        return value

    if namespace == "tasks":
        if len(parts) < 2:
            raise ValueError(f"template: invalid task reference {expression!r}")
        task_id = parts[1]
        result = context.get_task_result(task_id)
        if result is None:
            raise ValueError(f"template: task {task_id!r} result not available")
        if len(parts) == 2:
            return result.output
        sub = parts[2]
        if sub == "files":
            return "\n".join(result.files)
        slug = sub
        for f in result.files:
            if _file_slug(f) == slug:
                return f
        raise ValueError(f"template: task {task_id!r} has no file matching slug {slug!r}")

    raise ValueError(f"template: unknown reference type {namespace!r} in {expression!r}")


def _file_slug(filename: str) -> str:
    """Converts a file path's basename to a slug for template references.
    Example: "tokens.css" → "tokens_css", "design-system.md" → "design_system_md"
    """
    base = filename.split("/")[-1]
    base = base.replace(".", "_").replace("-", "_")
    return base
