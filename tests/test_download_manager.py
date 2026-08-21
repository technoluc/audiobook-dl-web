import asyncio
import shutil
from pathlib import Path

import pytest

from app.download_manager import (
    DownloadManager,
    DownloadStatus,
    DownloadTask,
    sanitize_template_literal,
)


def test_sanitize_template_literal_preserves_spaces_and_hyphen():
    # Keeps spaces/hyphen, but removes Windows-invalid characters and control chars
    assert sanitize_template_literal(" - ") == " - "
    assert sanitize_template_literal("a:b") == "a_b"
    assert sanitize_template_literal("x\u0000y") == "xy"


def _write_config(config_dir: Path, toml_text: str) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "audiobook-dl.toml").write_text(toml_text, encoding="utf-8")


def test_build_download_command_uses_default_template_and_group_by_author(tmp_path: Path):
    config_dir = tmp_path / "config"
    downloads_dir = tmp_path / "downloads"
    _write_config(
        config_dir,
        """
output_template = "{title} - {author}"
create_folder = true
group_by_author = true
max_concurrent_downloads = 2
""".lstrip(),
    )

    dm = DownloadManager(str(config_dir), str(downloads_dir))

    base_dir = downloads_dir / "__task_123__"
    cmd = dm._build_download_command(
        url="https://example.invalid/book",
        output_template=None,
        combine=False,
        no_chapters=False,
        output_format=None,
        base_dir=base_dir,
    )

    assert "-o" in cmd
    out_path = cmd[cmd.index("-o") + 1].replace("\\", "/")

    # create_folder=true => <base>/{author}/<template>/<template>
    assert out_path.endswith("/__task_123__/{author}/{title} - {author}/{title} - {author}")


def test_build_ebook_command_reuses_credentials_and_masks_password(tmp_path: Path):
    config_dir = tmp_path / "config"
    downloads_dir = tmp_path / "downloads"
    _write_config(
        config_dir,
        """
ebook_output_template = "{authors}/{title}.{ext}"

[sources.nextory]
username = "reader@example.com"
password = "top-secret"
""".lstrip(),
    )
    dm = DownloadManager(str(config_dir), str(downloads_dir))

    cmd = dm._build_ebook_command(
        "https://nextory.com/book/example",
        output_template=None,
        output_format="epub",
        base_dir=downloads_dir / "__task_ebook__",
    )

    assert cmd[0] == "grawlix"
    assert cmd[cmd.index("--username") + 1] == "reader@example.com"
    assert cmd[cmd.index("--password") + 1] == "top-secret"
    assert cmd[cmd.index("--output") + 1].endswith("/{authors}/{title}.epub")
    assert "top-secret" not in dm._redact_command(cmd)


def test_publish_ebook_outputs_handles_multiple_files_and_conflicts(tmp_path: Path):
    config_dir = tmp_path / "config"
    downloads_dir = tmp_path / "downloads"
    config_dir.mkdir()
    dm = DownloadManager(str(config_dir), str(downloads_dir))

    existing = downloads_dir / "Author" / "Book.epub"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"old")
    staging = downloads_dir / "__task_books__"
    (staging / "Author").mkdir(parents=True)
    (staging / "Author" / "Book.epub").write_bytes(b"new")
    (staging / "Other.cbz").write_bytes(b"comic")

    published = [Path(path) for path in dm._publish_ebook_outputs(staging)]

    assert len(published) == 2
    assert existing.read_bytes() == b"old"
    assert (downloads_dir / "Author" / "Book (2).epub").read_bytes() == b"new"
    assert (downloads_dir / "Other.cbz").read_bytes() == b"comic"
    assert not staging.exists()


def test_download_task_serializes_media_type_and_multiple_outputs():
    task = DownloadTask("https://example.invalid/book", "ebook", media_type="ebook")
    task.output_files = ["/downloads/one.epub", "/downloads/two.epub"]

    serialized = task.to_dict()

    assert serialized["media_type"] == "ebook"
    assert serialized["output_files"] == task.output_files


def test_ebook_slot_does_not_wait_for_full_audiobook_pool(tmp_path: Path):
    config_dir = tmp_path / "config"
    downloads_dir = tmp_path / "downloads"
    _write_config(config_dir, "max_concurrent_downloads = 1\n")
    dm = DownloadManager(str(config_dir), str(downloads_dir))
    dm.active_downloads = 1
    dm.active_audiobook_downloads = 1

    asyncio.run(asyncio.wait_for(dm._acquire_download_slot("ebook"), timeout=0.05))

    assert dm.active_audiobook_downloads == 1
    assert dm.active_ebook_downloads == 1
    assert dm.active_downloads == 2
    dm._release_download_slot("ebook")
    assert dm.active_downloads == 1


def test_unwrap_staging_dir_moves_single_dir_and_updates_output_file(tmp_path: Path):
    config_dir = tmp_path / "config"
    downloads_dir = tmp_path / "downloads"
    config_dir.mkdir(parents=True, exist_ok=True)

    dm = DownloadManager(str(config_dir), str(downloads_dir))

    staging_dir = downloads_dir / "__task_abc__"
    book_dir = staging_dir / "Book"
    book_dir.mkdir(parents=True)

    audio = book_dir / "audio.m4b"
    audio.write_bytes(b"dummy")

    output_file = str(audio)
    new_output, search_root = dm._unwrap_staging_dir(staging_dir, output_file)

    assert search_root == downloads_dir / "Book"
    assert new_output is not None
    assert Path(new_output).exists()
    assert Path(new_output).name == "audio.m4b"
    assert (downloads_dir / "Book" / "audio.m4b").exists()
    assert not staging_dir.exists()


def test_unwrap_staging_dir_merges_into_existing_dir_and_renames_on_conflict(tmp_path: Path):
    config_dir = tmp_path / "config"
    downloads_dir = tmp_path / "downloads"
    config_dir.mkdir(parents=True, exist_ok=True)

    dm = DownloadManager(str(config_dir), str(downloads_dir))

    # Existing target structure
    target_book_dir = downloads_dir / "Book"
    target_book_dir.mkdir(parents=True)
    (target_book_dir / "audio.m4b").write_bytes(b"existing")

    # Staging contains the same filename -> must be renamed
    staging_dir = downloads_dir / "__task_def__"
    staged_book_dir = staging_dir / "Book"
    staged_book_dir.mkdir(parents=True)
    staged_audio = staged_book_dir / "audio.m4b"
    staged_audio.write_bytes(b"new")

    new_output, search_root = dm._unwrap_staging_dir(staging_dir, str(staged_audio))

    assert search_root == target_book_dir
    assert new_output is not None
    new_path = Path(new_output)
    assert new_path.exists()
    assert new_path.parent == target_book_dir
    assert new_path.name.startswith("audio")
    assert new_path.suffix == ".m4b"

    # Both files must exist (original + renamed)
    assert (target_book_dir / "audio.m4b").exists()
    assert len(list(target_book_dir.glob("audio*.m4b"))) == 2
    assert not staging_dir.exists()


def test_unwrap_staging_dir_noop_when_multiple_entries(tmp_path: Path):
    config_dir = tmp_path / "config"
    downloads_dir = tmp_path / "downloads"
    config_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir.mkdir(parents=True, exist_ok=True)

    dm = DownloadManager(str(config_dir), str(downloads_dir))

    staging_dir = downloads_dir / "__task_multi__"
    (staging_dir / "A").mkdir(parents=True)
    (staging_dir / "B").mkdir(parents=True)

    output_file = None
    new_output, search_root = dm._unwrap_staging_dir(staging_dir, output_file)
    assert new_output is None
    assert search_root == staging_dir
    assert staging_dir.exists()


def test_unwrap_staging_dir_noop_when_target_is_file(tmp_path: Path):
    config_dir = tmp_path / "config"
    downloads_dir = tmp_path / "downloads"
    config_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir.mkdir(parents=True, exist_ok=True)

    dm = DownloadManager(str(config_dir), str(downloads_dir))

    # Create a file where a directory would be expected
    (downloads_dir / "Book").write_bytes(b"not a dir")

    staging_dir = downloads_dir / "__task_target_file__"
    staged_book_dir = staging_dir / "Book"
    staged_book_dir.mkdir(parents=True)
    staged_audio = staged_book_dir / "audio.m4b"
    staged_audio.write_bytes(b"x")

    new_output, search_root = dm._unwrap_staging_dir(staging_dir, str(staged_audio))
    assert new_output == str(staged_audio)
    assert search_root == staging_dir
    assert staging_dir.exists()


def test_unwrap_staging_dir_moves_single_file(tmp_path: Path):
    config_dir = tmp_path / "config"
    downloads_dir = tmp_path / "downloads"
    config_dir.mkdir(parents=True, exist_ok=True)

    dm = DownloadManager(str(config_dir), str(downloads_dir))

    staging_dir = downloads_dir / "__task_file__"
    staging_dir.mkdir(parents=True)
    staged_audio = staging_dir / "out.m4b"
    staged_audio.write_bytes(b"x")

    new_output, search_root = dm._unwrap_staging_dir(staging_dir, str(staged_audio))
    assert search_root == downloads_dir
    assert new_output is not None
    assert Path(new_output).exists()
    assert (downloads_dir / "out.m4b").exists()
    assert not staging_dir.exists()


def test_parse_transfer_progress_uses_latest_valid_percentage():
    assert DownloadManager._parse_transfer_progress(" 12%\r 67%", 3) == 67
    assert DownloadManager._parse_transfer_progress("no progress here", 42) == 42
    assert DownloadManager._parse_transfer_progress("999%", 42) == 42


class _FakeStream:
    def __init__(self, chunks: list[bytes]):
        self.chunks = iter(chunks)

    async def read(self, _size: int) -> bytes:
        return next(self.chunks, b"")


class _FakeProcess:
    def __init__(self, returncode: int, stdout: list[bytes], stderr: list[bytes]):
        self.returncode = returncode
        self.stdout = _FakeStream(stdout)
        self.stderr = _FakeStream(stderr)

    async def wait(self) -> int:
        return self.returncode


@pytest.mark.asyncio
async def test_transfer_removes_source_only_after_success(tmp_path: Path, monkeypatch):
    config_dir = tmp_path / "config"
    downloads_dir = tmp_path / "downloads"
    destination = tmp_path / "nas"
    config_dir.mkdir()
    destination.mkdir()
    dm = DownloadManager(str(config_dir), str(downloads_dir))
    dm.destination_path = str(destination)

    source = downloads_dir / "book.m4b"
    source.write_bytes(b"audiobook")
    task = DownloadTask("https://example.invalid/book", "success")
    task.output_file = str(source)

    async def fake_rsync(*cmd, **_kwargs):
        shutil.copyfile(Path(cmd[-2]), Path(cmd[-1]) / Path(cmd[-2]).name)
        return _FakeProcess(0, [b" 10%\r", b" 100%\n"], [])

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_rsync)
    await dm._transfer_completed_output(task)

    assert task.status is DownloadStatus.TRANSFERRING
    assert task.progress == 100
    assert not source.exists()
    assert (destination / "book.m4b").read_bytes() == b"audiobook"
    assert task.output_file == str(destination / "book.m4b")


@pytest.mark.asyncio
async def test_transfer_failure_keeps_local_source(tmp_path: Path, monkeypatch):
    config_dir = tmp_path / "config"
    downloads_dir = tmp_path / "downloads"
    destination = tmp_path / "nas"
    config_dir.mkdir()
    destination.mkdir()
    dm = DownloadManager(str(config_dir), str(downloads_dir))
    dm.destination_path = str(destination)

    source = downloads_dir / "book.m4b"
    source.write_bytes(b"audiobook")
    task = DownloadTask("https://example.invalid/book", "failure")
    task.output_file = str(source)

    async def fake_rsync(*_cmd, **_kwargs):
        return _FakeProcess(23, [b" 40%\r"], [b"NAS disconnected"])

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_rsync)

    with pytest.raises(RuntimeError, match="code 23"):
        await dm._transfer_completed_output(task)

    assert source.read_bytes() == b"audiobook"
    assert task.output_file == str(source)


@pytest.mark.asyncio
async def test_ebook_transfer_uses_separate_destination_and_preserves_folders(
    tmp_path: Path, monkeypatch
):
    config_dir = tmp_path / "config"
    downloads_dir = tmp_path / "downloads"
    destination = tmp_path / "ebooks"
    config_dir.mkdir()
    destination.mkdir()
    dm = DownloadManager(str(config_dir), str(downloads_dir))
    dm.ebook_destination_path = str(destination)

    source = downloads_dir / "Author" / "book.epub"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"ebook")
    task = DownloadTask("https://example.invalid/book", "ebook-transfer", "ebook")
    task.output_file = str(source)
    task.output_files = [str(source)]

    async def fake_rsync(*cmd, **_kwargs):
        shutil.copyfile(Path(cmd[-2]), Path(cmd[-1]) / Path(cmd[-2]).name)
        return _FakeProcess(0, [b" 100%\n"], [])

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_rsync)
    await dm._transfer_completed_output(task)

    expected = destination / "Author" / "book.epub"
    assert expected.read_bytes() == b"ebook"
    assert task.output_file == str(expected)
    assert task.output_files == [str(expected)]
