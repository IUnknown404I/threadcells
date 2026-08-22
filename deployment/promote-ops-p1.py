#!/usr/bin/env python3
"""Atomically promote one verified ThreadCells candidate under the trusted release anchor."""

from __future__ import annotations

import argparse
import fcntl
import grp
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

RELEASE_ADMIN_GROUP = "threadcells-release-admin"


def fail(reason: str) -> None:
    raise SystemExit(f"OPS_P1_PROMOTE_FAILED reason_code={reason}")


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _open_json(path: Path, *, owner: tuple[int, int]) -> dict[str, Any]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError:
        fail("RELEASE_CONTROL_FILE_INVALID")
    try:
        file_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(file_stat.st_mode)
            or (file_stat.st_uid, file_stat.st_gid) != owner
            or file_stat.st_mode & 0o022
        ):
            fail("RELEASE_CONTROL_FILE_INVALID")
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            try:
                value = json.load(handle)
            except (OSError, ValueError, json.JSONDecodeError):
                fail("RELEASE_CONTROL_FILE_INVALID")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(value, dict):
        fail("RELEASE_CONTROL_FILE_INVALID")
    return value


def _atomic_json(path: Path, value: dict[str, Any], *, owner: tuple[int, int]) -> None:
    if path.is_symlink():
        fail("RELEASE_CONTROL_FILE_INVALID")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o644)
            os.fchown(handle.fileno(), *owner)
        os.replace(temporary, path)
        parent_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_anchor(
    system_root: Path,
    *,
    trusted_owner: tuple[int, int],
    release_owner: tuple[int, int],
) -> tuple[Path, Path]:
    state_root = system_root / "var/lib/threadcells"
    release_root = state_root / "releases"
    for path, owner, mode in (
        (state_root, trusted_owner, 0o755),
        (release_root, release_owner, 0o775),
    ):
        if path.is_symlink() or not path.is_dir():
            fail("RELEASE_CONTROL_ROOT_INVALID")
        path_stat = path.stat()
        if (path_stat.st_uid, path_stat.st_gid) != owner or (path_stat.st_mode & 0o777) != mode:
            fail("RELEASE_CONTROL_ROOT_UNTRUSTED")
    return state_root, release_root


def _validate_release(
    path: Path,
    release_root: Path,
    *,
    release_owner: tuple[int, int],
    expected_commit: str | None,
    allowed_states: set[str],
) -> dict[str, Any]:
    if (
        not path.is_absolute()
        or path.parent != release_root
        or path.is_symlink()
        or not path.is_dir()
    ):
        fail("RELEASE_IDENTITY_INVALID")
    for item in (path, *path.rglob("*")):
        item_stat = item.lstat()
        if (item_stat.st_uid, item_stat.st_gid) != release_owner:
            fail("RELEASE_OWNERSHIP_INVALID")
        if item.is_symlink():
            continue
        if item.is_dir():
            if (item_stat.st_mode & 0o777) != 0o775:
                fail("RELEASE_MODE_INVALID")
            continue
        if not item.is_file() or (item_stat.st_mode & 0o777) not in {0o644, 0o755}:
            fail("RELEASE_MODE_INVALID")
    marker = _open_json(path / ".threadcells-release.json", owner=release_owner)
    if (
        marker.get("schema_version") != 1
        or marker.get("release_id") != path.name
        or marker.get("state") not in allowed_states
        or not isinstance(marker.get("source_commit"), str)
        or (expected_commit is not None and marker.get("source_commit") != expected_commit)
    ):
        fail("RELEASE_MARKER_INVALID")
    for executable in ("cao-server", "cao-housekeeping"):
        target = path / "runtime/bin" / executable
        if not target.is_file() or not os.access(target, os.X_OK):
            fail("RELEASE_RUNTIME_INVALID")
    return marker


def _validate_metadata(metadata: dict[str, Any], release_root: Path) -> None:
    if (
        metadata.get("schema_version") != 1
        or not isinstance(metadata.get("rollback_releases"), list)
        or not isinstance(metadata.get("candidate_releases"), list)
    ):
        fail("RELEASE_METADATA_INVALID")
    for value in (
        metadata.get("active_release"),
        *metadata["rollback_releases"],
        *metadata["candidate_releases"],
    ):
        if value is None:
            continue
        if not isinstance(value, str):
            fail("RELEASE_METADATA_INVALID")
        candidate = Path(value)
        if not candidate.is_absolute() or candidate.parent != release_root:
            fail("RELEASE_METADATA_INVALID")


def _active_target(active_link: Path, release_root: Path, *, owner: tuple[int, int]) -> Path | None:
    if not active_link.exists() and not active_link.is_symlink():
        return None
    if not active_link.is_symlink():
        fail("ACTIVE_RELEASE_LINK_INVALID")
    link_stat = active_link.lstat()
    if (link_stat.st_uid, link_stat.st_gid) != owner:
        fail("ACTIVE_RELEASE_LINK_INVALID")
    try:
        target = active_link.resolve(strict=True)
    except (OSError, RuntimeError):
        fail("ACTIVE_RELEASE_LINK_INVALID")
    if target.parent != release_root or not target.is_dir():
        fail("ACTIVE_RELEASE_LINK_INVALID")
    return target


def _replace_active(active_link: Path, candidate: Path, *, owner: tuple[int, int]) -> None:
    temporary = active_link.with_name(f".{active_link.name}.{os.getpid()}")
    if temporary.exists() or temporary.is_symlink():
        fail("ACTIVE_RELEASE_TEMPORARY_EXISTS")
    try:
        temporary.symlink_to(candidate, target_is_directory=True)
        os.lchown(temporary, *owner)
        os.replace(temporary, active_link)
        parent_descriptor = os.open(active_link.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--system-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--rollback-root", type=Path)
    parser.add_argument(
        "--test-unprivileged-promotion", action="store_true", help=argparse.SUPPRESS
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    system_root = args.system_root.resolve()
    production = system_root == Path("/")
    if args.test_unprivileged_promotion and production:
        fail("TEST_PROMOTION_OVERRIDE_FORBIDDEN")
    if production and os.geteuid() != 0:
        fail("PROMOTION_PRIVILEGE_REQUIRED")
    if os.geteuid() != 0 and not args.test_unprivileged_promotion:
        fail("PROMOTION_PRIVILEGE_REQUIRED")
    try:
        release_gid = grp.getgrnam(RELEASE_ADMIN_GROUP).gr_gid if production else os.getegid()
    except KeyError:
        fail("RELEASE_ADMIN_GROUP_UNAVAILABLE")
    trusted_owner = (0, 0) if production else (os.geteuid(), os.getegid())
    release_owner = (0, release_gid) if production else trusted_owner
    state_root, release_root = _validate_anchor(
        system_root,
        trusted_owner=trusted_owner,
        release_owner=release_owner,
    )
    candidate = args.candidate_root
    candidate_marker = _validate_release(
        candidate,
        release_root,
        release_owner=release_owner,
        expected_commit=args.expected_commit,
        allowed_states={"candidate", "active"},
    )
    rollback = args.rollback_root
    if rollback is not None:
        _validate_release(
            rollback,
            release_root,
            release_owner=release_owner,
            expected_commit=None,
            allowed_states={"active", "rollback"},
        )
    lock_path = state_root / "release-staging.lock"
    metadata_path = state_root / "release-metadata.json"
    active_link = state_root / "active"
    try:
        lock_descriptor = os.open(lock_path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError:
        fail("RELEASE_LOCK_INVALID")
    with os.fdopen(lock_descriptor, "a+") as lock_handle:
        lock_stat = os.fstat(lock_handle.fileno())
        if (
            not stat.S_ISREG(lock_stat.st_mode)
            or (lock_stat.st_uid, lock_stat.st_gid) != release_owner
            or lock_stat.st_mode & 0o007
        ):
            fail("RELEASE_LOCK_INVALID")
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fail("RELEASE_STAGING_BUSY")
        metadata = _open_json(metadata_path, owner=release_owner)
        _validate_metadata(metadata, release_root)
        for value in metadata["candidate_releases"]:
            candidate_path = Path(value)
            if candidate_path == candidate:
                continue
            _validate_release(
                candidate_path,
                release_root,
                release_owner=release_owner,
                expected_commit=None,
                allowed_states={"candidate"},
            )
        candidate_value = str(candidate)
        if (
            metadata.get("active_release") != candidate_value
            and candidate_value not in metadata["candidate_releases"]
        ):
            fail("CANDIDATE_NOT_STAGED")
        active = _active_target(active_link, release_root, owner=release_owner)
        recorded_active = metadata.get("active_release")
        if active is not None and active != candidate and recorded_active != str(active):
            fail("ACTIVE_RELEASE_IDENTITY_MISMATCH")
        previous = Path(recorded_active) if isinstance(recorded_active, str) else None
        if previous is not None and previous != candidate:
            _validate_release(
                previous,
                release_root,
                release_owner=release_owner,
                expected_commit=None,
                allowed_states={"active", "rollback"},
            )
        rollback_values: list[str] = []
        for value in (
            str(rollback) if rollback is not None else None,
            str(previous) if previous is not None and previous != candidate else None,
            *metadata["rollback_releases"],
        ):
            if value is None or value == candidate_value or value in rollback_values:
                continue
            rollback_path = Path(value)
            _validate_release(
                rollback_path,
                release_root,
                release_owner=release_owner,
                expected_commit=None,
                allowed_states={"active", "rollback"},
            )
            rollback_values.append(value)
        if args.dry_run:
            print(
                f"OPS_P1_PROMOTE_DRY_RUN candidate={candidate_value} "
                f"rollback={','.join(rollback_values[:2]) or 'none'}"
            )
            return 0
        if candidate_marker.get("state") != "active":
            candidate_marker["state"] = "active"
            _atomic_json(
                candidate / ".threadcells-release.json", candidate_marker, owner=release_owner
            )
        for value in rollback_values[:2]:
            rollback_path = Path(value)
            marker = _open_json(rollback_path / ".threadcells-release.json", owner=release_owner)
            if marker.get("state") != "rollback":
                marker["state"] = "rollback"
                _atomic_json(
                    rollback_path / ".threadcells-release.json", marker, owner=release_owner
                )
        if active != candidate:
            _replace_active(active_link, candidate, owner=release_owner)
        metadata["active_release"] = candidate_value
        metadata["rollback_releases"] = rollback_values[:2]
        metadata["candidate_releases"] = [
            value for value in metadata["candidate_releases"] if value != candidate_value
        ][:2]
        _atomic_json(metadata_path, metadata, owner=release_owner)
    print(
        f"OPS_P1_PROMOTED commit={args.expected_commit} active={candidate} "
        f"rollback={','.join(rollback_values[:2]) or 'none'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
