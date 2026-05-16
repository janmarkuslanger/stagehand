from __future__ import annotations

import glob as glob_module
import os

from stagehand.ports.storage import ArtifactStorage


class FilesystemStorage(ArtifactStorage):
    """Stores artifacts on the local filesystem under a root directory."""

    def __init__(self, root: str) -> None:
        self.root = root

    async def write(self, path: str, content: bytes) -> None:
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
