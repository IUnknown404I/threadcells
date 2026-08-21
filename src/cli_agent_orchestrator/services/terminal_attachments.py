"""Safe short-lived local image attachments for CAO terminal input."""

import json
import os
import re
import secrets
import stat
import time
from pathlib import Path

from cli_agent_orchestrator.constants import CAO_HOME_DIR

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_ARCHIVE_BYTES = 25 * 1024 * 1024
ATTACHMENT_MAX_AGE_SECONDS = 24 * 60 * 60
TERMINAL_ATTACHMENTS_DIR = CAO_HOME_DIR / "runtime" / "terminal-attachments"

_TERMINAL_ID_RE = re.compile(r"^[a-f0-9]{8}$")
_IMAGE_TYPES = {
    "image/png": ("png", b"\x89PNG\r\n\x1a\n"),
    "image/jpeg": ("jpg", b"\xff\xd8\xff"),
    "image/webp": ("webp", None),
}
_TEXT_EXTENSIONS = {".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".log"}
_OPAQUE_FILE_EXTENSIONS = {".zip"}


class UnsupportedTerminalImage(ValueError):
    """Raised when an upload is not a supported image with matching bytes."""


class TerminalImageTooLarge(ValueError):
    """Raised when an upload exceeds the terminal attachment size limit."""


class UnsupportedTerminalFile(ValueError):
    """Raised when a terminal text attachment is unsafe or unsupported."""


def _ensure_owned_directory(path: Path) -> None:
    """Create a private runtime directory without following a symlink."""
    path.mkdir(mode=0o750, parents=True, exist_ok=True)
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise RuntimeError(f"Attachment runtime path is not a directory: {path}")
    if metadata.st_uid != os.geteuid():
        raise RuntimeError(f"Attachment runtime path is not owned by the CAO user: {path}")
    os.chmod(path, 0o750)


def _matches_magic(mime_type: str, content: bytes) -> bool:
    if mime_type == "image/webp":
        return len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    expected_magic = _IMAGE_TYPES[mime_type][1]
    return expected_magic is not None and content.startswith(expected_magic)


def validate_terminal_image(mime_type: str, content: bytes) -> tuple[str, str]:
    """Validate a browser-provided MIME type against strict image magic bytes."""
    normalized_mime = mime_type.split(";", 1)[0].strip().lower()
    if normalized_mime not in _IMAGE_TYPES:
        raise UnsupportedTerminalImage("Only PNG, JPEG, and WebP images are supported")
    if not content:
        raise UnsupportedTerminalImage("Image attachment is empty")
    if len(content) > MAX_IMAGE_BYTES:
        raise TerminalImageTooLarge("Image attachment exceeds the 10 MiB limit")
    if not _matches_magic(normalized_mime, content):
        raise UnsupportedTerminalImage("Image content does not match its declared MIME type")
    return normalized_mime, _IMAGE_TYPES[normalized_mime][0]


def validate_terminal_file(filename: str, content: bytes) -> str:
    """Validate a small text or opaque ZIP attachment before assigning a generated path."""
    extension = Path(filename).suffix.lower()
    if extension not in _TEXT_EXTENSIONS | _OPAQUE_FILE_EXTENSIONS:
        raise UnsupportedTerminalFile(
            "Only MD, TXT, JSON, YAML, CSV, LOG, and ZIP files are supported"
        )
    if not content:
        raise UnsupportedTerminalFile("File attachment is empty")
    if extension in _OPAQUE_FILE_EXTENSIONS:
        if len(content) > MAX_ARCHIVE_BYTES:
            raise TerminalImageTooLarge("Archive attachment exceeds the 25 MiB limit")
        return extension
    if len(content) > MAX_IMAGE_BYTES:
        raise TerminalImageTooLarge("File attachment exceeds the 10 MiB limit")
    if b"\x00" in content:
        raise UnsupportedTerminalFile("Text attachment contains binary data")
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise UnsupportedTerminalFile("Text attachment must be UTF-8") from error
    if extension == ".json":
        try:
            json.loads(decoded)
        except json.JSONDecodeError as error:
            raise UnsupportedTerminalFile("JSON attachment is invalid") from error
    return extension


def cleanup_expired_terminal_attachments(now: float | None = None) -> int:
    """Best-effort removal of regular attachment files older than 24 hours."""
    if not TERMINAL_ATTACHMENTS_DIR.exists():
        return 0

    root_metadata = TERMINAL_ATTACHMENTS_DIR.lstat()
    if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_ISLNK(root_metadata.st_mode):
        return 0

    cutoff = (time.time() if now is None else now) - ATTACHMENT_MAX_AGE_SECONDS
    deleted = 0
    for terminal_dir in TERMINAL_ATTACHMENTS_DIR.iterdir():
        try:
            directory_metadata = terminal_dir.lstat()
            if not stat.S_ISDIR(directory_metadata.st_mode) or stat.S_ISLNK(
                directory_metadata.st_mode
            ):
                continue
            for candidate in terminal_dir.iterdir():
                try:
                    candidate_metadata = candidate.lstat()
                    if (
                        stat.S_ISREG(candidate_metadata.st_mode)
                        and candidate_metadata.st_mtime < cutoff
                    ):
                        candidate.unlink()
                        deleted += 1
                except FileNotFoundError:
                    continue
            try:
                terminal_dir.rmdir()
            except OSError:
                pass
        except FileNotFoundError:
            continue
    return deleted


def store_terminal_image(terminal_id: str, mime_type: str, content: bytes) -> Path:
    """Persist a validated image in a generated per-terminal CAO runtime path."""
    if not _TERMINAL_ID_RE.fullmatch(terminal_id):
        raise ValueError("Invalid terminal identifier")
    _, extension = validate_terminal_image(mime_type, content)

    _ensure_owned_directory(TERMINAL_ATTACHMENTS_DIR)
    cleanup_expired_terminal_attachments()
    terminal_dir = TERMINAL_ATTACHMENTS_DIR / terminal_id
    _ensure_owned_directory(terminal_dir)

    while True:
        destination = terminal_dir / f"{secrets.token_urlsafe(18)}.{extension}"
        try:
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
            break
        except FileExistsError:
            continue

    try:
        with os.fdopen(fd, "wb") as attachment:
            attachment.write(content)
        os.chmod(destination, 0o640)
    except Exception:
        try:
            destination.unlink()
        except FileNotFoundError:
            pass
        raise

    return destination.resolve()


def store_terminal_file(terminal_id: str, filename: str, content: bytes) -> Path:
    """Persist a validated file under a generated terminal-owned path."""
    if not _TERMINAL_ID_RE.fullmatch(terminal_id):
        raise ValueError("Invalid terminal identifier")
    extension = validate_terminal_file(filename, content)

    _ensure_owned_directory(TERMINAL_ATTACHMENTS_DIR)
    cleanup_expired_terminal_attachments()
    terminal_dir = TERMINAL_ATTACHMENTS_DIR / terminal_id
    _ensure_owned_directory(terminal_dir)

    while True:
        destination = terminal_dir / f"{secrets.token_urlsafe(18)}{extension}"
        try:
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
            break
        except FileExistsError:
            continue

    try:
        with os.fdopen(fd, "wb") as attachment:
            attachment.write(content)
        os.chmod(destination, 0o640)
    except Exception:
        try:
            destination.unlink()
        except FileNotFoundError:
            pass
        raise
    return destination.resolve()
