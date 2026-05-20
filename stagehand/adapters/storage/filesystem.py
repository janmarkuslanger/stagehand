from __future__ import annotations

import glob as glob_module
import os
from typing import Optional

from stagehand.ports.storage import ArtifactStorage


class FilesystemStorage(ArtifactStorage):
    """Stores artifacts on the local filesystem under a root directory.

    Parameters
    ----------
    root:
        Base directory under which all artifacts are stored.
    allowed_extensions:
        If set, only paths whose extension (case-insensitive) is in this list
        are accepted. ``ValueError`` is raised for any other extension.
        Example: ``[".txt", ".md"]``
    """

    def __init__(self, root: str, allowed_extensions: Optional[list[str]] = None) -> None:
        self.root = root
        self._allowed_extensions: Optional[list[str]] = (
            [ext.lower() for ext in allowed_extensions] if allowed_extensions is not None else None
        )

    def validate_path(self, path: str) -> None:
        full = os.path.normpath(os.path.join(self.root, path))
        root = os.path.abspath(self.root)
        if os.path.commonpath([root, os.path.abspath(full)]) != root:
            raise ValueError(f"path traversal detected in path: {path!r}")
        if self._allowed_extensions is not None:
            _, ext = os.path.splitext(path)
            if ext.lower() not in self._allowed_extensions:
                raise ValueError(
                    f"extension {ext!r} is not allowed; allowed: {self._allowed_extensions}"
                )

    async def write(self, path: str, content: bytes) -> None:
        self.validate_path(path)
        full_path = os.path.join(self.root, path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as f:
            f.write(content)

    async def read(self, path: str) -> bytes:
        full_path = os.path.join(self.root, path)
        with open(full_path, "rb") as f:
            return f.read()

    async def list(self, pattern: str) -> list[str]:
        full_pattern = os.path.join(self.root, pattern)
        matches = glob_module.glob(full_pattern)
        result = []
        for match in matches:
            rel = os.path.relpath(match, self.root)
            result.append(rel)
        return result
