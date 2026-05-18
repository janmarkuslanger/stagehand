# Stagehand — development guide for Claude

## README rule

**Always update `README.md` when a feature is added, changed, or removed.**

This includes but is not limited to:
- New parameters on `WorkflowBuilder`, `Task`, or any public dataclass
- New or changed behaviour in the scheduler or executors
- New public classes or functions exported from `stagehand/__init__.py`
- Changed defaults

The README is the only user-facing documentation. Keep it in sync with the code.

## Architecture

Stagehand uses ports-and-adapters (hexagonal) architecture. Dependency rule is strict:

```
core/     →  nothing external (stdlib only)
ports/    →  nothing (interfaces only)
adapters/ →  ports/ only
builder   →  core/ + ports/
```

Never import from `adapters/` inside `core/` or `ports/`.

## Public API

`stagehand/__init__.py` is the public surface. Every new user-facing class or function must be added to `__all__` there.

## Tests

Run with `python -m pytest tests/ -q`. All tests must pass before pushing.

Add tests for new behaviour in the relevant `tests/test_*.py` file.
