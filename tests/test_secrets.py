import os

import pytest

from stagehand.adapters.secrets.env import EnvSecretProvider


@pytest.mark.asyncio
async def test_get_existing_env(monkeypatch):
    monkeypatch.setenv("MY_SECRET", "super-secret")
    provider = EnvSecretProvider()
    value = await provider.get("MY_SECRET")
    assert value == "super-secret"


@pytest.mark.asyncio
async def test_get_missing_env(monkeypatch):
    monkeypatch.delenv("MY_SECRET", raising=False)
    provider = EnvSecretProvider()
    with pytest.raises(ValueError, match="is not set"):
        await provider.get("MY_SECRET")
