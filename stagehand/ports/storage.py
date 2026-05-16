from __future__ import annotations

from abc import ABC, abstractmethod


class ArtifactStorage(ABC):
    """Port: reads and writes task output files."""

    @abstractmethod
    async def write(self, path: str, content: bytes) -> None: ...

    @abstractmethod
    async def read(self, path: str) -> bytes: ...

    @abstractmethod
    async def list(self, pattern: str) -> list[str]: ...
