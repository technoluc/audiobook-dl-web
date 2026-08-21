"""
Download manager for audiobook-dl-web
Handles audiobook downloads with progress tracking
"""

import asyncio
import logging
import re
import shutil
from datetime import datetime
from enum import Enum
from pathlib import Path
from urllib.parse import urlparse

from app import output_processor

logger = logging.getLogger(__name__)

# Constants
REMOVABLE_STATUSES = ["completed", "failed", "cancelled"]


def sanitize_path_component(path: str) -> str:
    r"""
    Sanitize path component by removing/replacing invalid filesystem characters.

    Windows doesn't allow: < > : " / \ | ? *
    Also removes leading/trailing spaces and dots
    """
    # Replace invalid characters with underscores
    sanitized = re.sub(r'[<>:"/\\|?*]', "_", path)
    # Remove control characters (0-31)
    sanitized = re.sub(r"[\x00-\x1f]", "", sanitized)
    # Remove leading/trailing spaces and dots
    sanitized = sanitized.strip(". ")
    # Collapse multiple spaces/underscores
    sanitized = re.sub(r"[_\s]+", " ", sanitized)
    return sanitized or "unnamed"


def sanitize_template_literal(literal: str) -> str:
    """Sanitize only the literal parts of the output template.

    This is intentionally less aggressive than `sanitize_path_component`:
    - Keeps spaces/hyphens exactly as written (e.g. " - ")
    - Only removes characters that are invalid in Windows paths and control chars

    The `{variables}` are expanded by audiobook-dl.
    """
    sanitized = re.sub(r'[<>:"/\\|?*]', "_", literal)
    sanitized = re.sub(r"[\x00-\x1f]", "", sanitized)
    return sanitized


class DownloadStatus(Enum):
    """Download status enumeration"""

    PENDING = "pending"
    DOWNLOADING = "downloading"
    TRANSFERRING = "transferring"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DownloadTask:
    """Represents a single download task"""

    def __init__(self, url: str, task_id: str, media_type: str = "audiobook"):
        self.url = url
        self.task_id = task_id
        self.media_type = media_type
        self.status = DownloadStatus.PENDING
        self.progress = 0
        self.message = "Waiting to start..."
        self.started_at: datetime | None = None
        self.completed_at: datetime | None = None
        self.error: str | None = None
        self.output_file: str | None = None
        self.output_files: list[str] = []
        self.transfer_destination: str | None = None
        self.metadata: dict | None = None
        self.expected_output_dir: Path | None = None  # Track expected output directory

    @property
    def duration(self) -> float | None:
        """Calculate task duration in seconds"""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    def to_dict(self) -> dict:
        """Convert task to dictionary for JSON serialization"""
        return {
            "task_id": self.task_id,
            "url": self.url,
            "media_type": self.media_type,
            "status": self.status.value,
            "progress": self.progress,
            "message": self.message,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (self.completed_at.isoformat() if self.completed_at else None),
            "error": self.error,
            "output_file": self.output_file,
            "output_files": self.output_files,
            "transfer_destination": self.transfer_destination,
            "metadata": self.metadata,
        }


class DownloadManager:
    """Manages audiobook downloads"""

    def __init__(self, config_dir: str, downloads_dir: str):
        """
        Initialize download manager

        Args:
            config_dir: Directory containing audiobook-dl.toml
            downloads_dir: Directory where audiobooks will be downloaded
        """
        self.config_dir = Path(config_dir).resolve()
        self.downloads_dir = Path(downloads_dir).resolve()
        self.downloads_dir.mkdir(parents=True, exist_ok=True)

        self.tasks: dict[str, DownloadTask] = {}
        self.active_downloads = 0
        self.active_audiobook_downloads = 0
        self.active_ebook_downloads = 0
        self._load_config()

    def _read_config(self) -> dict:
        """Read configuration from audiobook-dl.toml file

        Returns:
            Configuration dictionary or empty dict if file doesn't exist or can't be read
        """
        try:
            import tomllib

            config_file = self.config_dir / "audiobook-dl.toml"
            if config_file.exists():
                with open(config_file, "rb") as f:
                    return tomllib.load(f)
        except Exception as e:
            logger.warning(f"Failed to read config file: {e}")
        return {}

    def _load_config(self):
        """Load configuration settings from config file"""
        config = self._read_config()

        self.max_concurrent_downloads = config.get("max_concurrent_downloads", 2)
        self.max_concurrent_ebook_downloads = config.get("max_concurrent_ebook_downloads", 2)
        self.create_folder = config.get("create_folder", False)
        self.group_by_author = config.get("group_by_author", False)
        self.default_output_template = config.get("output_template", "{title}")
        self.move_after_completion = config.get("move_after_completion", False)
        self.destination_path = config.get("destination_path", "")
        self.ebook_output_template = config.get(
            "ebook_output_template", "{authors}/{title}.{ext}"
        )
        self.move_ebooks_after_completion = config.get(
            "move_ebooks_after_completion", False
        )
        self.ebook_destination_path = config.get("ebook_destination_path", "")

        # Ensure valid range (1-10)
        self.max_concurrent_downloads = max(1, min(10, self.max_concurrent_downloads))
        self.max_concurrent_ebook_downloads = max(
            1, min(10, self.max_concurrent_ebook_downloads)
        )

    def reload_config(self):
        """Reload configuration settings from config file"""
        self._load_config()

    async def add_download(
        self,
        url: str,
        task_id: str,
        output_template: str | None = None,
        combine: bool = False,
        no_chapters: bool = False,
        output_format: str | None = None,
        media_type: str = "audiobook",
    ) -> DownloadTask:
        """
        Add a download task

        Args:
            url: URL of the audiobook listening page
            task_id: Unique identifier for this task
            output_template: Custom output template
            combine: Whether to combine files into a single file
            no_chapters: Whether to exclude chapters
            output_format: Output file format

        Returns:
            DownloadTask object
        """
        if media_type not in {"audiobook", "ebook"}:
            raise ValueError(f"Unsupported media type: {media_type}")

        task = DownloadTask(url, task_id, media_type)
        self.tasks[task_id] = task

        # Start download in background
        asyncio.create_task(
            self._download(task, output_template, combine, no_chapters, output_format)
        )

        return task

    async def _download(
        self,
        task: DownloadTask,
        output_template: str | None,
        combine: bool,
        no_chapters: bool,
        output_format: str | None,
    ):
        """
        Execute the audiobook download

        Args:
            task: DownloadTask to execute
            output_template: Custom output template
            combine: Whether to combine files
            no_chapters: Whether to exclude chapters
            output_format: Output file format
        """
        await self._acquire_download_slot(task.media_type)
        task.status = DownloadStatus.DOWNLOADING
        task.started_at = datetime.now()
        task.message = "Starting download..."

        try:
            # Use a per-task staging directory so concurrent tasks can never
            # claim each other's output by "newest file" heuristics.
            task_base_dir = self.downloads_dir / f"__task_{task.task_id}__"
            task_base_dir.mkdir(parents=True, exist_ok=True)
            task.expected_output_dir = task_base_dir

            if task.media_type == "ebook":
                cmd = self._build_ebook_command(
                    task.url,
                    output_template,
                    output_format,
                    base_dir=task_base_dir,
                )
            else:
                cmd = self._build_download_command(
                    task.url,
                    output_template,
                    combine,
                    no_chapters,
                    output_format,
                    base_dir=task_base_dir,
                )

            logger.info(
                f"Download started - Task: {task.task_id}, Type: {task.media_type}, "
                f"URL: {task.url}, Command: {' '.join(self._redact_command(cmd))}"
            )

            # Execute command with unbuffered output
            import os

            env = {
                "PYTHONUNBUFFERED": "1",  # Force Python unbuffered output
                "FORCE_COLOR": "0",  # Disable ANSI colors that might interfere
            }
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.downloads_dir),
                env={**os.environ, **env},  # Merge with existing env
            )

            stdout = process.stdout
            stderr = process.stderr
            if stdout is None or stderr is None:
                raise RuntimeError("Failed to capture subprocess output streams")

            # Read output and update progress in real-time from both stdout and stderr
            stdout_lines = []
            stderr_lines = []

            # Callback to update task progress as lines come in
            def on_line(line: str):
                progress, message = output_processor.parse_progress_line(line, task.progress)
                task.progress = progress
                if message != task.message:
                    task.message = message

            # Run both readers concurrently
            await asyncio.gather(
                output_processor.read_process_stream(stdout, stdout_lines, on_line),
                output_processor.read_process_stream(stderr, stderr_lines, on_line),
            )

            # Wait for process to complete
            await process.wait()

            if process.returncode == 0:
                # Keep search root within this task's staging directory for deterministic
                # file detection, then unwrap/move after we've identified the output.
                search_root = task.expected_output_dir or self.downloads_dir

                # Try to find audiobook output from captured output. Grawlix uses
                # Rich progress rendering, so ebook results are detected in the
                # task-specific staging directory instead.
                logger.info(
                    f"Task {task.task_id}: Captured {len(stdout_lines)} stdout lines, "
                    f"{len(stderr_lines)} stderr lines"
                )

                # First try to parse the exact file path from tool output.
                if task.media_type == "audiobook":
                    task.output_file = output_processor.find_output_file_in_lines(
                        stdout_lines + stderr_lines, self.downloads_dir
                    )

                if task.output_file:
                    logger.info(f"Task {task.task_id}: Found file in output: {task.output_file}")

                # If not found in output, search for files created after task started
                if task.media_type == "ebook":
                    task.output_files = self._find_ebook_outputs(search_root)
                    task.output_file = task.output_files[0] if task.output_files else None
                elif not task.output_file and task.started_at:
                    min_time = task.started_at.timestamp()

                    # With per-task staging dirs, we can deterministically search only within the
                    # task's own output tree (staging dir or the moved-to target).
                    # This works for both "single file" (file directly in a folder) and multi-file.
                    if (
                        isinstance(search_root, Path)
                        and search_root.exists()
                        and search_root.is_file()
                    ):
                        task.output_file = output_processor.normalize_path(search_root)
                    else:
                        task.output_file = output_processor.find_latest_audio_file(
                            self.downloads_dir,
                            min_mtime=min_time,
                            search_dir=search_root
                            if isinstance(search_root, Path)
                            else self.downloads_dir,
                        )

                    if not task.output_file:
                        logger.warning(
                            f"Task {task.task_id}: No audio file found under search root: {search_root}"
                        )

                    if task.output_file:
                        logger.info(
                            f"Task {task.task_id}: Found file by timestamp search: {task.output_file}"
                        )

                # Unwrap staging directory into the main downloads folder (merge-safe).
                # Also update `task.output_file` to the new location when possible.
                if (
                    task.media_type == "ebook"
                    and task.expected_output_dir
                    and task.expected_output_dir.exists()
                ):
                    task.output_files = self._publish_ebook_outputs(task.expected_output_dir)
                    task.output_file = task.output_files[0] if task.output_files else None
                    if not task.output_files:
                        raise RuntimeError("Grawlix completed without producing an e-book file")
                elif task.expected_output_dir and task.expected_output_dir.exists():
                    try:
                        task.output_file, search_root = self._unwrap_staging_dir(
                            task.expected_output_dir, task.output_file
                        )
                    except Exception as e:
                        logger.warning(f"Task {task.task_id}: Failed to unwrap staging dir: {e}")

                # Normalize the finished local file before metadata extraction and
                # before rsync transfers it to the final Audiobookshelf location.
                if task.media_type == "audiobook" and task.output_file:
                    output_processor.normalize_audiobookshelf_metadata(task.output_file)

                # Extract metadata from the file
                if task.media_type == "audiobook" and task.output_file:
                    logger.info(f"Extracting metadata for: {task.output_file}")
                    task.metadata = await output_processor.extract_audio_metadata(task.output_file)
                    if task.metadata:
                        logger.info(f"Metadata extracted: {list(task.metadata.keys())}")
                    else:
                        logger.warning(f"No metadata extracted for: {task.output_file}")

                should_transfer = (
                    self.move_ebooks_after_completion
                    if task.media_type == "ebook"
                    else self.move_after_completion
                )
                if should_transfer:
                    try:
                        await self._transfer_completed_output(task)
                    except Exception as e:
                        task.status = DownloadStatus.FAILED
                        task.message = "Transfer to final destination failed; local file retained"
                        task.error = str(e)
                        task.completed_at = datetime.now()
                        logger.error(
                            f"Transfer failed - Task: {task.task_id}, Error: {e}, "
                            f"Local file: {task.output_file}"
                        )
                        return

                task.status = DownloadStatus.COMPLETED
                task.progress = 100
                task.message = (
                    "Transfer completed successfully!"
                    if should_transfer
                    else "Download completed successfully!"
                )
                task.completed_at = datetime.now()

                log_msg = (
                    f"Download completed - Task: {task.task_id}, Duration: {task.duration:.1f}s"
                )
                if task.output_file:
                    log_msg += f", File: {task.output_file}"
                logger.info(log_msg)
            else:
                task.status = DownloadStatus.FAILED
                task.progress = 0
                task.message = "Download failed"
                task.completed_at = datetime.now()

                logger.error(
                    f"Download failed - Task: {task.task_id}, Return code: {process.returncode}, "
                    f"Duration: {task.duration:.1f}s"
                )

                # Log stderr output for debugging
                if stderr_lines:
                    logger.error(f"Task {task.task_id} stderr output:")
                    for line in stderr_lines[-20:]:  # Log last 20 lines
                        if line.strip():
                            logger.error(f"  {line}")

                task.error = output_processor.format_error_messages(stderr_lines)

        except Exception as e:
            task.status = DownloadStatus.FAILED
            task.message = "Download failed with exception"
            task.error = str(e)
            task.completed_at = datetime.now()

            duration_msg = f", Duration: {task.duration:.1f}s" if task.duration else ""
            logger.error(
                f"Download failed with exception - Task: {task.task_id}, Error: {str(e)}{duration_msg}"
            )
        finally:
            self._release_download_slot(task.media_type)

    async def _acquire_download_slot(self, media_type: str) -> None:
        """Wait for a slot in the media-specific concurrency pool.

        The user-facing concurrent-download setting applies to audiobooks.
        E-books use their own small pool so a fast EPUB download never waits
        for long-running audio downloading or conversion.
        """
        if media_type == "ebook":
            while self.active_ebook_downloads >= self.max_concurrent_ebook_downloads:
                await asyncio.sleep(0.1)
            self.active_ebook_downloads += 1
        else:
            while self.active_audiobook_downloads >= self.max_concurrent_downloads:
                await asyncio.sleep(0.1)
            self.active_audiobook_downloads += 1
        self.active_downloads += 1

    def _release_download_slot(self, media_type: str) -> None:
        """Return a slot to the matching concurrency pool."""
        if media_type == "ebook":
            self.active_ebook_downloads = max(0, self.active_ebook_downloads - 1)
        else:
            self.active_audiobook_downloads = max(0, self.active_audiobook_downloads - 1)
        self.active_downloads = max(0, self.active_downloads - 1)

    def get_task(self, task_id: str) -> DownloadTask | None:
        """
        Get a download task by ID

        Args:
            task_id: Task identifier

        Returns:
            DownloadTask or None if not found
        """
        return self.tasks.get(task_id)

    def get_all_tasks(self) -> list[DownloadTask]:
        """
        Get all download tasks

        Returns:
            List of all DownloadTask objects
        """
        return list(self.tasks.values())

    def cancel_task(self, task_id: str) -> bool:
        """
        Cancel a download task (if possible)

        Args:
            task_id: Task identifier

        Returns:
            True if cancelled, False otherwise
        """
        task = self.tasks.get(task_id)
        if task and task.status in [DownloadStatus.PENDING, DownloadStatus.DOWNLOADING]:
            task.status = DownloadStatus.CANCELLED
            task.message = "Download cancelled by user"
            task.completed_at = datetime.now()
            return True
        return False

    def remove_task(self, task_id: str) -> bool:
        """
        Remove a specific task from the task list

        Args:
            task_id: Task identifier

        Returns:
            True if removed, False if task not found or still active
        """
        task = self.tasks.get(task_id)
        if task and task.status.value in REMOVABLE_STATUSES:
            del self.tasks[task_id]
            logger.info(f"Task removed - ID: {task_id}, Status: {task.status.value}")
            return True
        return False

    def clear_completed(self):
        """Clear all completed, failed, and cancelled tasks"""
        to_remove = [
            task_id
            for task_id, task in self.tasks.items()
            if task.status.value in REMOVABLE_STATUSES
        ]
        for task_id in to_remove:
            del self.tasks[task_id]

    def _resolve_local_output(self, output_file: str | None) -> Path:
        """Resolve a task output and ensure it is a local file under DOWNLOADS_DIR."""
        if not output_file:
            raise RuntimeError("No completed local file was found to transfer")

        source = Path(output_file)
        if not source.is_absolute():
            source = self.downloads_dir / source
        source = source.resolve()

        if not source.is_relative_to(self.downloads_dir):
            raise RuntimeError("Refusing to transfer a source outside DOWNLOADS_DIR")
        if not source.is_file():
            raise RuntimeError(f"Local source file does not exist: {source}")
        return source

    @staticmethod
    def _parse_transfer_progress(text: str, current_progress: int) -> int:
        """Return the last valid rsync percentage found in a stream chunk."""
        percentages = [int(value) for value in re.findall(r"(?<!\d)(\d{1,3})%", text)]
        valid = [value for value in percentages if 0 <= value <= 100]
        return valid[-1] if valid else current_progress

    async def _transfer_completed_output(self, task: DownloadTask) -> None:
        """Copy completed outputs with rsync and remove sources after verification."""
        configured_outputs = (
            task.output_files if task.media_type == "ebook" else [task.output_file]
        )
        sources = [self._resolve_local_output(output) for output in configured_outputs]
        destination_path = (
            self.ebook_destination_path
            if task.media_type == "ebook"
            else self.destination_path
        )
        if not destination_path:
            raise RuntimeError("No final destination path is configured")

        destination = Path(destination_path).expanduser()
        if not destination.is_absolute():
            raise RuntimeError("The final destination path must be absolute")
        destination = destination.resolve()
        if not destination.exists() or not destination.is_dir():
            raise RuntimeError(f"Final destination is not an available directory: {destination}")
        if destination == self.downloads_dir or destination.is_relative_to(self.downloads_dir):
            raise RuntimeError("The final destination must be outside DOWNLOADS_DIR")

        transfers: list[tuple[Path, Path]] = []
        for source in sources:
            relative = (
                source.relative_to(self.downloads_dir)
                if task.media_type == "ebook"
                else Path(source.name)
            )
            final_file = destination / relative
            if final_file.resolve() == source:
                raise RuntimeError("The final destination resolves to the local source file")
            final_file.parent.mkdir(parents=True, exist_ok=True)
            transfers.append((source, final_file))

        task.status = DownloadStatus.TRANSFERRING
        task.progress = 0
        task.transfer_destination = output_processor.normalize_path(destination)
        task.message = f"Transferring to {destination}..."

        for source, final_file in transfers:
            cmd = [
                "rsync",
                "--archive",
                "--human-readable",
                "--progress",
                str(source),
                f"{final_file.parent}/",
            ]
            logger.info(
                f"Transfer started - Task: {task.task_id}, Source: {source}, "
                f"Destination: {final_file.parent}"
            )

            try:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as e:
                raise RuntimeError("rsync is not installed or not available on PATH") from e

            async def read_stream(stream, captured: list[str]) -> None:
                while True:
                    chunk = await stream.read(4096)
                    if not chunk:
                        break
                    text = chunk.decode("utf-8", errors="replace")
                    captured.append(text)
                    task.progress = self._parse_transfer_progress(text, task.progress)
                    task.message = f"Transferring to {destination}... {task.progress}%"

            stdout_chunks: list[str] = []
            stderr_chunks: list[str] = []
            if process.stdout is None or process.stderr is None:
                raise RuntimeError("Failed to capture rsync output streams")
            await asyncio.gather(
                read_stream(process.stdout, stdout_chunks),
                read_stream(process.stderr, stderr_chunks),
            )
            await process.wait()

            if process.returncode != 0:
                detail = "".join(stderr_chunks).strip() or "".join(stdout_chunks).strip()
                detail = re.sub(r"\s+", " ", detail)[-1000:]
                raise RuntimeError(
                    f"rsync exited with code {process.returncode}"
                    + (f": {detail}" if detail else "")
                )

            if not final_file.is_file() or final_file.stat().st_size != source.stat().st_size:
                raise RuntimeError("Transferred file could not be verified; local file retained")

        for source, _ in transfers:
            source.unlink()
        final_outputs = [output_processor.normalize_path(final) for _, final in transfers]
        task.output_files = final_outputs if task.media_type == "ebook" else []
        task.output_file = final_outputs[0]
        task.progress = 100
        logger.info(
            f"Transfer completed - Task: {task.task_id}, Destination: {destination}; "
            f"{len(transfers)} local source(s) removed"
        )

    @staticmethod
    def _redact_command(cmd: list[str]) -> list[str]:
        """Mask command-line secrets before writing a command to the log."""
        redacted = list(cmd)
        for index, value in enumerate(redacted[:-1]):
            if value in {"-p", "--password"}:
                redacted[index + 1] = "********"
        return redacted

    @staticmethod
    def _source_key_for_url(url: str) -> str | None:
        hostname = (urlparse(url).hostname or "").lower()
        if "storytel" in hostname or "mofibo" in hostname:
            return "storytel"
        if "nextory" in hostname:
            return "nextory"
        if hostname in {"saxo.com", "saxo.dk"} or hostname.endswith((".saxo.com", ".saxo.dk")):
            return "saxo"
        if "/reader" in url and "orderid=" in url:
            return "ereolen"
        return None

    def _build_ebook_command(
        self,
        url: str,
        output_template: str | None,
        output_format: str | None,
        base_dir: Path,
    ) -> list[str]:
        """Build a Grawlix command, reusing configured service credentials."""
        template = output_template or self.ebook_output_template
        self._validate_output_template(template)
        if "{ext}" not in template and Path(template).suffix.lower() not in {
            ".epub",
            ".cbz",
            ".acsm",
        }:
            template = f"{template}.{{ext}}"
        if output_format:
            if "{ext}" in template:
                template = template.replace("{ext}", output_format)
            elif Path(template).suffix.lower() in {".epub", ".cbz", ".acsm"}:
                template = str(Path(template).with_suffix(f".{output_format}"))

        cmd = ["grawlix", "--output", str(base_dir / template)]
        source_key = self._source_key_for_url(url)
        source_config = self._read_config().get("sources", {}).get(source_key or "", {})
        if source_config.get("username"):
            cmd.extend(["--username", str(source_config["username"])])
        if source_config.get("password"):
            cmd.extend(["--password", str(source_config["password"])])
        if source_config.get("cookie_file"):
            cookie_file = Path(str(source_config["cookie_file"]))
            if not cookie_file.is_absolute():
                cookie_file = self.config_dir / cookie_file
            cmd.extend(["--cookies", str(cookie_file)])
        cmd.append(url)
        return cmd

    @staticmethod
    def _find_ebook_outputs(search_root: Path) -> list[str]:
        """Return all ebook files produced by Grawlix in deterministic order."""
        extensions = {".epub", ".cbz", ".acsm", ".pdf"}
        return [
            output_processor.normalize_path(path)
            for path in sorted(search_root.rglob("*"))
            if path.is_file() and path.suffix.lower() in extensions
        ]

    def _publish_ebook_outputs(self, staging_dir: Path) -> list[str]:
        """Move all Grawlix results out of the per-task staging directory."""
        published: list[Path] = []
        for source_file_text in self._find_ebook_outputs(staging_dir):
            source_file = Path(source_file_text)
            relative = source_file.relative_to(staging_dir)
            destination = self.downloads_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                destination = self._unique_destination(destination)
            shutil.move(str(source_file), str(destination))
            published.append(destination)
        shutil.rmtree(staging_dir, ignore_errors=True)
        return [output_processor.normalize_path(path) for path in published]

    def _build_download_command(
        self,
        url: str,
        output_template: str | None,
        combine: bool,
        no_chapters: bool,
        output_format: str | None,
        base_dir: Path,
    ) -> list[str]:
        """Build audiobook-dl command with options"""
        cmd = ["audiobook-dl", "--config", str(self.config_dir / "audiobook-dl.toml")]

        # Determine output path - use provided template or fall back to config default
        template = output_template if output_template is not None else self.default_output_template
        self._validate_output_template(template)

        # Sanitize template parts that are not variables (outside of {})
        # Variables like {title}, {author} are handled by audiobook-dl
        # Split into parts, keeping {...} patterns intact
        parts = re.split(r"(\{[^}]+\})", template)
        sanitized_template = "".join(
            part if (not part or part.startswith("{")) else sanitize_template_literal(part)
            for part in parts
        )

        effective_base_dir = base_dir
        if getattr(self, "group_by_author", False):
            # Let audiobook-dl expand {author} and handle its own sanitization.
            effective_base_dir = base_dir / "{author}"

        if self.create_folder:
            output_path = str(effective_base_dir / sanitized_template / sanitized_template)
        else:
            output_path = str(effective_base_dir / sanitized_template)

        cmd.extend(["-o", output_path])

        # Add optional flags
        if combine:
            cmd.append("--combine")
        if no_chapters:
            cmd.append("--no-chapters")
        if output_format:
            cmd.extend(["--output-format", output_format])

        cmd.append(url)
        return cmd

    @staticmethod
    def _validate_output_template(template: str) -> None:
        """Reject absolute and parent-traversing templates while allowing subfolders."""
        path = Path(template)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Output templates must stay inside the downloads directory")

    def _unique_destination(self, dest: Path) -> Path:
        """Return a non-existing destination path by appending a numeric suffix."""
        if not dest.exists():
            return dest

        stem = dest.stem
        suffix = dest.suffix
        parent = dest.parent

        for i in range(2, 10_000):
            candidate = parent / f"{stem} ({i}){suffix}"
            if not candidate.exists():
                return candidate
        # Fallback (should be extremely unlikely)
        return parent / f"{stem} (copy){suffix}"

    def _merge_dir_contents(self, src_dir: Path, dest_dir: Path) -> dict[Path, Path]:
        """Move top-level children from src_dir into dest_dir. Returns mapping of moved children."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        moved: dict[Path, Path] = {}

        for child in src_dir.iterdir():
            target_child = dest_dir / child.name
            if target_child.exists():
                target_child = self._unique_destination(target_child)
            shutil.move(str(child), str(target_child))
            moved[child] = target_child

        return moved

    def _unwrap_staging_dir(
        self, staging_dir: Path, output_file: str | None
    ) -> tuple[str | None, Path]:
        """Move results from staging_dir into downloads_dir, merging if necessary.

        Returns updated (output_file, search_root).
        """
        entries = list(staging_dir.iterdir())
        if len(entries) != 1:
            return output_file, staging_dir

        entry = entries[0]
        target = self.downloads_dir / entry.name

        old_output_path: Path | None = None
        if output_file:
            try:
                old_output_path = Path(output_file)
            except Exception:
                old_output_path = None

        if entry.is_dir():
            if not target.exists():
                shutil.move(str(entry), str(target))
                shutil.rmtree(staging_dir, ignore_errors=True)

                if old_output_path and old_output_path.is_relative_to(entry):
                    new_output = target / old_output_path.relative_to(entry)
                    return output_processor.normalize_path(new_output), target
                return output_file, target

            if not target.is_dir():
                # Destination exists but isn't a directory; keep staging
                return output_file, staging_dir

            moved_map = self._merge_dir_contents(entry, target)
            shutil.rmtree(staging_dir, ignore_errors=True)

            if old_output_path and old_output_path.is_relative_to(entry):
                rel = old_output_path.relative_to(entry)
                top = entry / rel.parts[0]
                new_top = moved_map.get(top)
                if new_top:
                    new_output = new_top.joinpath(*rel.parts[1:])
                    return output_processor.normalize_path(new_output), target
            return output_file, target

        # entry is a file
        if target.exists():
            target = self._unique_destination(target)
        shutil.move(str(entry), str(target))
        shutil.rmtree(staging_dir, ignore_errors=True)

        if old_output_path and old_output_path == entry:
            return output_processor.normalize_path(target), target.parent
        return output_file, target.parent
