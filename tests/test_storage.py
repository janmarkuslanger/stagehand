import os
import tempfile

import pytest

from stagehand.adapters.storage.filesystem import FilesystemStorage


@pytest.mark.asyncio
async def test_write_and_read():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = FilesystemStorage(tmpdir)
        await storage.write("task1/result.txt", b"hello world")
        data = await storage.read("task1/result.txt")
        assert data == b"hello world"


@pytest.mark.asyncio
async def test_creates_parent_directories():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = FilesystemStorage(tmpdir)
        await storage.write("a/b/c/file.md", b"content")
        assert os.path.exists(os.path.join(tmpdir, "a/b/c/file.md"))


@pytest.mark.asyncio
async def test_list_glob():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = FilesystemStorage(tmpdir)
        await storage.write("task1/a.md", b"a")
        await storage.write("task1/b.md", b"b")
        await storage.write("task1/c.txt", b"c")

        md_files = await storage.list("task1/*.md")
        assert set(md_files) == {"task1/a.md", "task1/b.md"}


@pytest.mark.asyncio
async def test_read_missing_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = FilesystemStorage(tmpdir)
        with pytest.raises(FileNotFoundError):
            await storage.read("nonexistent.txt")
