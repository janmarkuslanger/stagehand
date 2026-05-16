from __future__ import annotations

from abc import ABC, abstractmethod


class SecretProvider(ABC):
    """Port: resolves named secrets at runtime."""

    @abstractmethod
    async def get(self, key: str) -> str: ...
