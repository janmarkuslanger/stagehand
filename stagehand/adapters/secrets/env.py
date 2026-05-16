from __future__ import annotations

import os

from stagehand.ports.secrets import SecretProvider


class EnvSecretProvider(SecretProvider):
    """Resolves secrets from environment variables."""

    async def get(self, key: str) -> str:
        value = os.environ.get(key, "")
        if not value:
            raise ValueError(f"env secrets: {key!r} is not set")
        return value
