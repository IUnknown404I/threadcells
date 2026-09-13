"""Exact physical cleanup for terminal-owned output and attachment artifacts."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Any, Sequence

from cli_agent_orchestrator.constants import TERMINAL_LOG_DIR
from cli_agent_orchestrator.services.terminal_attachments import TERMINAL_ATTACHMENTS_DIR

_TERMINAL_ID_RE = re.compile(r"^[a-f0-9]{8}$")
_LOG_FILE_RE = re.compile(r"^([a-f0-9]{8})\.log(?:\.gz|\.tci|\.tcd|\.output-index\.lock)?$")
_INDEX_TEMP_RE = re.compile(r"^\.([a-f0-9]{8})\.[0-9a-f]{16}\.tc[di]$")
_LOG_COMPRESS_TEMP_RE = re.compile(r"^\.([a-f0-9]{8})\.log\.[A-Za-z0-9_-]+$")
_LOG_INVENTORY_LIMIT = 100_000
_ATTACHMENT_INVENTORY_LIMIT = 10_000


class TerminalArtifactCleanupError(RuntimeError):
    """Terminal-owned physical artifacts could not be proven safely absent."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


def _open_owned_directory(path: Path, *, absent_ok: bool) -> int | None:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        if absent_ok:
            return None
        raise
    except OSError as exc:
        raise TerminalArtifactCleanupError("TERMINAL_ARTIFACT_ROOT_UNSAFE") from exc
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid():
        os.close(descriptor)
        raise TerminalArtifactCleanupError("TERMINAL_ARTIFACT_ROOT_UNSAFE")
    return descriptor


def _terminal_log_owner(name: str, terminal_ids: set[str]) -> str | None:
    match = (
        _LOG_FILE_RE.fullmatch(name)
        or _INDEX_TEMP_RE.fullmatch(name)
        or _LOG_COMPRESS_TEMP_RE.fullmatch(name)
    )
    return match.group(1) if match is not None and match.group(1) in terminal_ids else None


def _purge_logs(terminal_ids: set[str], counts: dict[str, dict[str, int]]) -> None:
    descriptor = _open_owned_directory(TERMINAL_LOG_DIR, absent_ok=True)
    if descriptor is None:
        return
    try:
        names = os.listdir(descriptor)
        if len(names) > _LOG_INVENTORY_LIMIT:
            raise TerminalArtifactCleanupError("TERMINAL_LOG_INVENTORY_UNBOUNDED")
        for name in names:
            terminal_id = _terminal_log_owner(name, terminal_ids)
            if terminal_id is None:
                continue
            try:
                metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
                raise TerminalArtifactCleanupError("TERMINAL_LOG_IDENTITY_UNSAFE")
            os.unlink(name, dir_fd=descriptor)
            counts[terminal_id]["logs_removed"] += 1
        os.fsync(descriptor)
        remaining = os.listdir(descriptor)
        if any(_terminal_log_owner(name, terminal_ids) is not None for name in remaining):
            raise TerminalArtifactCleanupError("TERMINAL_LOG_CLEANUP_UNPROVEN")
    finally:
        os.close(descriptor)


def _purge_attachments(terminal_ids: set[str], counts: dict[str, dict[str, int]]) -> None:
    root_descriptor = _open_owned_directory(TERMINAL_ATTACHMENTS_DIR, absent_ok=True)
    if root_descriptor is None:
        return
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    try:
        for terminal_id in sorted(terminal_ids):
            try:
                terminal_descriptor = os.open(
                    terminal_id,
                    directory_flags,
                    dir_fd=root_descriptor,
                )
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise TerminalArtifactCleanupError("TERMINAL_ATTACHMENT_DIRECTORY_UNSAFE") from exc
            try:
                metadata = os.fstat(terminal_descriptor)
                if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid():
                    raise TerminalArtifactCleanupError("TERMINAL_ATTACHMENT_DIRECTORY_UNSAFE")
                names = os.listdir(terminal_descriptor)
                if len(names) > _ATTACHMENT_INVENTORY_LIMIT:
                    raise TerminalArtifactCleanupError("TERMINAL_ATTACHMENT_INVENTORY_UNBOUNDED")
                for name in names:
                    try:
                        item = os.stat(name, dir_fd=terminal_descriptor, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if not (stat.S_ISREG(item.st_mode) or stat.S_ISLNK(item.st_mode)):
                        raise TerminalArtifactCleanupError("TERMINAL_ATTACHMENT_IDENTITY_UNSAFE")
                    os.unlink(name, dir_fd=terminal_descriptor)
                    counts[terminal_id]["attachments_removed"] += 1
                os.fsync(terminal_descriptor)
                if os.listdir(terminal_descriptor):
                    raise TerminalArtifactCleanupError("TERMINAL_ATTACHMENT_CLEANUP_UNPROVEN")
            finally:
                os.close(terminal_descriptor)
            try:
                os.rmdir(terminal_id, dir_fd=root_descriptor)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise TerminalArtifactCleanupError("TERMINAL_ATTACHMENT_CLEANUP_UNPROVEN") from exc
        os.fsync(root_descriptor)
        for terminal_id in terminal_ids:
            try:
                os.stat(terminal_id, dir_fd=root_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise TerminalArtifactCleanupError("TERMINAL_ATTACHMENT_CLEANUP_UNPROVEN")
    finally:
        os.close(root_descriptor)


def purge_session_terminal_artifacts(terminal_ids: Sequence[str]) -> dict[str, Any]:
    """Remove one bounded Session's exact output/attachment artifacts.

    Callers must hold both the context lifecycle fence and the Housekeeping
    mutation fence.  The cleanup is idempotent, scans the shared log directory
    once, and never follows a Session-controlled symlink.
    """
    normalized = set(terminal_ids)
    if (
        len(normalized) != len(terminal_ids)
        or not normalized
        or any(_TERMINAL_ID_RE.fullmatch(value) is None for value in normalized)
    ):
        raise TerminalArtifactCleanupError("TERMINAL_ARTIFACT_IDENTITY_INVALID")
    counts = {
        terminal_id: {"logs_removed": 0, "attachments_removed": 0} for terminal_id in normalized
    }
    try:
        _purge_logs(normalized, counts)
        _purge_attachments(normalized, counts)
    except TerminalArtifactCleanupError:
        raise
    except OSError as exc:
        raise TerminalArtifactCleanupError("TERMINAL_ARTIFACT_CLEANUP_UNPROVEN") from exc
    return {
        "runtime_artifacts_absent": True,
        "terminals": [
            {"terminal_id": terminal_id, **counts[terminal_id]}
            for terminal_id in sorted(normalized)
        ],
    }
