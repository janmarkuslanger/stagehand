from __future__ import annotations

import re
from typing import Any, Optional

_TEMPLATE_PATTERN = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")


def resolve(
    template: str,
    context: "RunContext",  # type: ignore[name-defined]
    extra: Optional[dict[str, Any]] = None,
) -> str:
    """Replaces all {{ }} template expressions using values from the RunContext.

    Supported expressions:
      {{ input.key }}         → runtime input value
      {{ tasks.id }}          → primary output of a completed task
      {{ tasks.id.files }}    → newline-separated list of files produced by a task
      {{ tasks.id.data }}     → structured data of a task (dotted path supported)
      {{ tasks.id.data.key }} → nested field of a task's structured data
      {{ tasks.id.slug }}     → path of a specific output file matched by slug

    ``extra`` supplies additional namespaces resolved before the built-in ones.
    The scheduler uses it for ``{{ item }}`` (fan-out) and ``{{ loop.* }}``
    (loops).  Dotted paths navigate into dicts, lists and object attributes.
    """
    errors: list[str] = []
    extra = extra or {}

    def replacer(match: re.Match) -> str:
        expression = match.group(1).strip()
        try:
            return _resolve_expression(expression, context, extra)
        except ValueError as error:
            errors.append(str(error))
            return match.group(0)

    result = _TEMPLATE_PATTERN.sub(replacer, template)
    if errors:
        raise ValueError(errors[0])
    return result


def _resolve_expression(
    expression: str,
    context: "RunContext",  # type: ignore[name-defined]
    extra: dict[str, Any],
) -> str:
    parts = expression.split(".")
    namespace = parts[0]

    if namespace in extra:
        return _stringify(_navigate(extra[namespace], parts[1:], expression))

    if namespace == "input":
        if len(parts) < 2:
            raise ValueError(f"template: invalid input reference {expression!r}")
        value = context.get_input(parts[1])
        if value is None:
            raise ValueError(f"template: input {parts[1]!r} not found")
        return _stringify(_navigate(value, parts[2:], expression))

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
        if sub == "data":
            return _stringify(_navigate(result.data, parts[3:], expression))
        slug = sub
        for f in result.files:
            if _file_slug(f) == slug:
                return f
        raise ValueError(f"template: task {task_id!r} has no file matching slug {slug!r}")

    raise ValueError(f"template: unknown reference type {namespace!r} in {expression!r}")


def _navigate(obj: Any, parts: list[str], expression: str) -> Any:
    """Walks a dotted path into dicts, lists/tuples and object attributes."""
    for part in parts:
        if obj is None:
            raise ValueError(f"template: cannot resolve {expression!r}: intermediate value is None")
        if isinstance(obj, dict):
            if part not in obj:
                raise ValueError(f"template: key {part!r} not found in {expression!r}")
            obj = obj[part]
        elif isinstance(obj, (list, tuple)):
            try:
                index = int(part)
            except ValueError:
                raise ValueError(f"template: invalid list index {part!r} in {expression!r}")
            try:
                obj = obj[index]
            except IndexError:
                raise ValueError(f"template: index {index} out of range in {expression!r}")
        elif hasattr(obj, part):
            obj = getattr(obj, part)
        else:
            raise ValueError(f"template: cannot resolve {part!r} in {expression!r}")
    return obj


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _file_slug(filename: str) -> str:
    """Converts a file path's basename to a slug for template references.
    Example: "tokens.css" → "tokens_css", "design-system.md" → "design_system_md"
    """
    base = filename.split("/")[-1]
    base = base.replace(".", "_").replace("-", "_")
    return base
