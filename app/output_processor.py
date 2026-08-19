"""
Output parser for audiobook-dl
Handles parsing and processing of audiobook-dl command output
"""

import asyncio
import json
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Constants
AUDIO_EXTENSIONS = [".m4b", ".mp3", ".m4a"]

MP4_TITLE_TAG = "\xa9nam"
MP4_ALBUM_TAG = "\xa9alb"
MP4_TRACK_TAG = "trkn"
MP4_SERIES_TAG = "----:com.apple.iTunes:series"
MP4_SERIES_PART_TAG = "----:com.apple.iTunes:series-part"


def normalize_audiobookshelf_metadata(file_path: str | Path) -> dict[str, str] | None:
    """Write Audiobookshelf-compatible title and series tags to an MP4 audiobook.

    audiobook-dl may store the series name in the MP4 album field. Audiobookshelf
    can then import that album value as the book title. Preserve the old album as
    the series, copy the actual title to album, and derive the series part from
    the track number when necessary.
    """
    path = Path(file_path)
    if path.suffix.lower() not in {".m4b", ".m4a", ".mp4"}:
        return None

    try:
        from mutagen.mp4 import MP4

        audio = MP4(path)
        if audio.tags is None:
            logger.warning("Cannot normalize MP4 metadata without tags: %s", path)
            return None

        title_values = audio.tags.get(MP4_TITLE_TAG, [])
        title = str(title_values[0]).strip() if title_values else ""
        if not title:
            logger.warning("Cannot normalize MP4 metadata without a title: %s", path)
            return None

        album_values = audio.tags.get(MP4_ALBUM_TAG, [])
        album = str(album_values[0]).strip() if album_values else ""

        if album and album != title and not audio.tags.get(MP4_SERIES_TAG):
            audio.tags[MP4_SERIES_TAG] = [album.encode("utf-8")]

        track_values = audio.tags.get(MP4_TRACK_TAG, [])
        if track_values and not audio.tags.get(MP4_SERIES_PART_TAG):
            track = track_values[0]
            track_number = track[0] if isinstance(track, tuple) else track
            if track_number:
                audio.tags[MP4_SERIES_PART_TAG] = [str(track_number).encode("utf-8")]

        audio.tags[MP4_TITLE_TAG] = [title]
        audio.tags[MP4_ALBUM_TAG] = [title]
        audio.save()

        normalized = {"title": title, "album": title}
        for output_key, tag_key in (
            ("series", MP4_SERIES_TAG),
            ("series_part", MP4_SERIES_PART_TAG),
        ):
            values = audio.tags.get(tag_key, [])
            if values:
                value = values[0]
                normalized[output_key] = (
                    value.decode("utf-8", errors="replace")
                    if isinstance(value, bytes)
                    else str(value)
                )

        logger.info("Normalized Audiobookshelf metadata for %s: %s", path, normalized)
        return normalized
    except Exception:
        logger.exception("Failed to normalize Audiobookshelf metadata for %s", path)
        return None


SAVE_KEYWORDS = [
    "saved to",
    "written to",
    "output:",
    "saved:",
    "file:",
    "downloading to",
    "writing",
    "created",
    "merged to",
    "combined to",
]


def normalize_path(path: str | Path) -> str:
    """Convert path to forward slashes for consistency"""
    return str(path).replace("\\", "/")


def make_relative_path(file_path: str, base_dir: Path) -> str:
    """Convert absolute path to relative path from base directory"""
    file_path = normalize_path(file_path)
    base_str = normalize_path(base_dir)

    if file_path.startswith(base_str):
        return file_path[len(base_str) :].lstrip("/")
    elif file_path.startswith("/app/downloads/"):
        return file_path[len("/app/downloads/") :]
    return file_path


def strip_ansi_codes(text: str) -> str:
    """
    Remove ANSI escape sequences from text

    Args:
        text: Text containing ANSI codes

    Returns:
        Text with ANSI codes removed
    """
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    return ansi_escape.sub("", text)


def parse_progress_line(line: str, current_progress: int) -> tuple[int, str]:
    """
    Parse progress information from audiobook-dl output line

    Args:
        line: Output line from audiobook-dl
        current_progress: Current progress percentage

    Returns:
        Tuple of (progress_percentage, message)
    """
    line_lower = line.lower()
    progress = current_progress
    message = line[:100] if line else "Processing..."  # Default message

    # Update progress based on download stages
    if "authenticating" in line_lower or "login" in line_lower:
        progress = 10
        message = "Authenticating with service..."
    elif "downloading" in line_lower or "download" in line_lower:
        # If we see download, we're at least 20% through
        if progress < 20:
            progress = 20
        # Gradually increase progress during download phase
        elif progress < 70:
            progress = min(progress + 5, 70)
        message = "Downloading audiobook files..."
    elif "combining" in line_lower or "merge" in line_lower or "concat" in line_lower:
        progress = 75
        message = "Combining audio files..."
    elif "chapter" in line_lower:
        progress = 85
        message = "Adding chapter information..."
    elif "saving" in line_lower or "writing" in line_lower:
        progress = 90
        message = "Saving audiobook..."
    elif "complete" in line_lower or "finished" in line_lower or "done" in line_lower:
        progress = 95
        message = "Finalizing..."

    # Extract percentage if available in the line
    percentage_match = re.search(r"(\d+)%", line)
    if percentage_match:
        progress = int(percentage_match.group(1))

    return progress, message


def find_output_file_in_lines(output_lines: list[str], downloads_dir: Path) -> str | None:
    """
    Try to find the output file path from command output lines

    Args:
        output_lines: Lines from stdout/stderr
        downloads_dir: Base downloads directory

    Returns:
        Relative path to output file or None
    """
    # Common patterns in audiobook-dl output:
    # "Saved to: <path>"
    # "Output file: <path>"
    # "Downloaded: <path>"
    # "Writing to: <path>"
    # "File saved: <path>"
    # "Created: <path>"

    found_paths = []

    for line in reversed(output_lines):
        line_clean = strip_ansi_codes(line)
        line_clean_lower = line_clean.lower()

        # Check for explicit save/output messages first
        for keyword in SAVE_KEYWORDS:
            if keyword in line_clean_lower:
                # Extract everything after the keyword (from the same cleaned string).
                idx = line_clean_lower.index(keyword) + len(keyword)
                potential_path = line_clean[idx:].lstrip(" \t:-").strip().strip("'\"")

                # Check if it has an audio extension
                if any(potential_path.lower().endswith(ext) for ext in AUDIO_EXTENSIONS):
                    logger.debug(f"Found path with keyword '{keyword}': {potential_path}")
                    found_paths.append(make_relative_path(potential_path, downloads_dir))

        # Also look for lines that just contain file paths with audio extensions
        # (some tools just print the path without a prefix)
        if not found_paths and any(ext in line_clean_lower for ext in AUDIO_EXTENSIONS):
            # Fallback: Look for any line containing audio file extensions
            for ext in AUDIO_EXTENSIONS:
                if ext.lower() in line_clean_lower:
                    # Try to extract a file path
                    # Look for patterns like: /path/to/file.mp3 or C:\path\to\file.mp3
                    patterns = [
                        r'([a-zA-Z]:[\\\/](?:[^\\\/\s<>:"|?*]+[\\\/])*[^\\\/\s<>:"|?*]+'
                        + re.escape(ext)
                        + r")",  # Windows absolute
                        r'(\/(?:[^\/\s<>"|?*]+\/)*[^\/\s<>"|?*]+'
                        + re.escape(ext)
                        + r")",  # Unix absolute
                        r'((?:[^\\\/\s<>:"|?*]+[\\\/])*[^\\\/\s<>:"|?*]+'
                        + re.escape(ext)
                        + r")",  # Relative
                    ]

                    for pattern in patterns:
                        match = re.search(pattern, line_clean, re.IGNORECASE)
                        if match:
                            file_path = match.group(1).strip("\"'")
                            logger.debug(f"Found path by pattern '{ext}': {file_path}")
                            found_paths.append(make_relative_path(file_path, downloads_dir))
                            break

    # Return the first found path that actually points to an existing audio file
    for potential_path in found_paths:
        # Convert to Path and check
        full_path = (
            downloads_dir / potential_path
            if not Path(potential_path).is_absolute()
            else Path(potential_path)
        )
        if full_path.exists():
            logger.debug(f"Confirmed existing file: {potential_path}")
            return make_relative_path(str(full_path), downloads_dir)

    if found_paths:
        logger.warning(
            f"Found {len(found_paths)} potential paths but none were valid existing files"
        )
    else:
        logger.debug("No output file paths found in command output")

    return None


def find_latest_audio_file(
    downloads_dir: Path, min_mtime: float = 0.0, search_dir: Path | None = None
) -> str | None:
    """
    Find the most recently modified audio file in downloads directory

    Args:
        downloads_dir: Base downloads directory
        min_mtime: Minimum modification time (Unix timestamp) to filter files
        search_dir: Specific subdirectory to search (if None, searches entire downloads_dir)

    Returns:
        Full absolute path to most recent file or None
    """
    try:
        latest_file = None
        latest_time = 0

        # Use specific search directory if provided, otherwise search all
        base_search = search_dir if search_dir and search_dir.exists() else downloads_dir

        # Walk through downloads directory
        for root, _, files in os.walk(base_search):
            for file in files:
                if any(file.lower().endswith(ext) for ext in AUDIO_EXTENSIONS):
                    file_path = Path(root) / file
                    mtime = file_path.stat().st_mtime
                    # Only consider files created after min_mtime
                    if mtime > max(latest_time, min_mtime):
                        latest_time = mtime
                        latest_file = file_path

        if latest_file:
            return normalize_path(latest_file)
    except Exception as e:
        logger.error(f"Error finding latest file: {e}")
    return None


def find_newest_subdirectory(downloads_dir: Path, min_mtime: float) -> Path | None:
    """
    Find the most recently created subdirectory in downloads directory

    Args:
        downloads_dir: Base downloads directory
        min_mtime: Minimum creation time (Unix timestamp)

    Returns:
        Path to newest subdirectory or None
    """
    try:
        newest_dir = None
        newest_time = 0

        for item in downloads_dir.iterdir():
            if item.is_dir():
                mtime = item.stat().st_mtime
                if mtime > max(newest_time, min_mtime):
                    newest_time = mtime
                    newest_dir = item

        return newest_dir
    except Exception as e:
        logger.error(f"Error finding newest subdirectory: {e}")
    return None


def get_tag_with_fallback(tags: dict, primary: str, secondary: str | None = None) -> str | None:
    """Get tag value with fallback option"""
    if primary in tags:
        return tags[primary]
    if secondary and secondary in tags:
        return tags[secondary]
    return None


def format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


async def extract_audio_metadata(file_path: str) -> dict | None:
    """
    Extract metadata from audio file using ffprobe

    Args:
        file_path: Full path to the audio file

    Returns:
        Dictionary with metadata or None
    """
    try:
        # Run ffprobe to extract metadata
        process = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            file_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        if process.returncode == 0 and stdout:
            data = json.loads(stdout.decode("utf-8"))

            if "format" in data:
                fmt = data["format"]
                tags = fmt.get("tags", {}) or {}
                metadata: dict[str, str] = {}

                # Tags (may be missing)
                title = tags.get("title")
                if title:
                    metadata["title"] = title

                author = get_tag_with_fallback(tags, "artist", "album_artist")
                if author:
                    metadata["author"] = author

                narrator = get_tag_with_fallback(tags, "composer", "PERFORMER")
                if narrator:
                    metadata["narrator"] = narrator

                year = get_tag_with_fallback(tags, "date", "year")
                if year:
                    metadata["year"] = year

                # Duration (available even without tags)
                if "duration" in fmt:
                    duration_sec = float(fmt["duration"])
                    hours = int(duration_sec // 3600)
                    minutes = int((duration_sec % 3600) // 60)
                    metadata["duration"] = f"{hours}h {minutes}m"

                # File size (available even without tags)
                if "size" in fmt:
                    metadata["size"] = format_file_size(int(fmt["size"]))

                return metadata if metadata else None

    except FileNotFoundError:
        logger.warning("ffprobe command not found - install FFmpeg to enable metadata extraction")
        return None
    except Exception as e:
        logger.error(f"Error extracting metadata: {e}")
    return None


async def read_process_stream(
    stream: asyncio.StreamReader, lines_list: list[str], on_line_callback=None
) -> None:
    """
    Read from a process stream and capture output lines
    Handles both newline and carriage return line endings

    Args:
        stream: The stream to read from (stdout or stderr)
        lines_list: List to append lines to
        on_line_callback: Optional callback function called for each line
    """
    buffer = b""
    while True:
        # Read in small chunks to catch progress updates
        chunk = await stream.read(256)
        if not chunk:
            # End of stream, process any remaining buffer
            if buffer:
                line_text = buffer.decode("utf-8", errors="replace").strip()
                line_text = strip_ansi_codes(line_text)
                if line_text:
                    lines_list.append(line_text)
                    if on_line_callback:
                        on_line_callback(line_text)
            break

        buffer += chunk

        # Process all complete lines (terminated by \n or \r)
        while b"\n" in buffer or b"\r" in buffer:
            # Find the first line ending
            nl_pos = buffer.find(b"\n")
            cr_pos = buffer.find(b"\r")

            if nl_pos == -1:
                split_pos = cr_pos
            elif cr_pos == -1:
                split_pos = nl_pos
            else:
                split_pos = min(nl_pos, cr_pos)

            line_bytes = buffer[:split_pos]
            buffer = buffer[split_pos + 1 :]

            line_text = line_bytes.decode("utf-8", errors="replace").strip()
            line_text = strip_ansi_codes(line_text)
            if line_text:
                # Only add unique lines to avoid duplicates from \r updates
                if not lines_list or lines_list[-1] != line_text:
                    lines_list.append(line_text)
                    if on_line_callback:
                        on_line_callback(line_text)


def format_error_messages(stderr_lines: list[str]) -> str:
    """Format error messages from stderr output"""
    if not stderr_lines:
        return "Unknown error"

    formatted_errors = []
    for line in stderr_lines:
        line = line.strip()
        if line and not line.startswith("WARNING:"):
            if "ERROR:" in line:
                parts = line.split("ERROR:")
                for part in parts[1:]:  # Skip first empty part
                    formatted_errors.append(f"ERROR: {part.strip()}")
            elif line:
                formatted_errors.append(line)

    return "\n".join(formatted_errors) if formatted_errors else "Unknown error"
