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


# ---------------------------------------------------------------------------
# validate_path tests
# ---------------------------------------------------------------------------

def test_absolute_path_rejected():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = FilesystemStorage(tmpdir)
        with pytest.raises(ValueError, match="path traversal"):
            storage.validate_path("/etc/passwd")


def test_path_traversal_simple():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = FilesystemStorage(tmpdir)
        with pytest.raises(ValueError, match="path traversal"):
            storage.validate_path("../escape.txt")


def test_path_traversal_nested():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = FilesystemStorage(tmpdir)
        with pytest.raises(ValueError, match="path traversal"):
            storage.validate_path("task1/../../etc/passwd")


def test_normal_path_passes():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = FilesystemStorage(tmpdir)
        storage.validate_path("task1/result.txt")  # must not raise


def test_allowed_extensions_accepts_matching():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = FilesystemStorage(tmpdir, allowed_extensions=[".txt"])
        storage.validate_path("task1/result.txt")  # must not raise


def test_allowed_extensions_rejects_other():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = FilesystemStorage(tmpdir, allowed_extensions=[".txt"])
        with pytest.raises(ValueError):
            storage.validate_path("task1/script.py")


def test_allowed_extensions_none_allows_any():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = FilesystemStorage(tmpdir, allowed_extensions=None)
        storage.validate_path("task1/anything.xyz")  # must not raise


def test_allowed_extensions_case_insensitive():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = FilesystemStorage(tmpdir, allowed_extensions=[".txt"])
        storage.validate_path("task1/RESULT.TXT")  # must not raise


@pytest.mark.asyncio
async def test_write_calls_validate_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = FilesystemStorage(tmpdir)
        with pytest.raises(ValueError, match="path traversal"):
            await storage.write("../escape.txt", b"bad")


@pytest.mark.asyncio
async def test_write_rejects_absolute_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = FilesystemStorage(tmpdir)
        with pytest.raises(ValueError, match="path traversal"):
            await storage.write("/tmp/escape.txt", b"bad")
