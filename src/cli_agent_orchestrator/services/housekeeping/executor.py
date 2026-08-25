"""Revalidating executor for immutable Housekeeping.P2 plans."""

from __future__ import annotations

import fcntl
import grp
import gzip
import hashlib
import json
import os
import pwd
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from .models import HousekeepingCandidate, HousekeepingPlan, candidate_fingerprint, default_settings
from .planner import revalidate_runtime_candidate
from .protected_set import ProtectedSet, resolve_protected_set


@dataclass
class ExecutionReport:
    plan_id: str
    ok: bool = True
    freed_bytes: int = 0
    reclaimed_bytes_by_class: dict[str, int] = field(default_factory=dict)
    executed_count_by_class: dict[str, int] = field(default_factory=dict)
    executed: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)
    active_release: str | None = None
    rollback_available: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _rename_noreplace(
    source_name: str,
    destination_name: str,
    *,
    source_dir_fd: int,
    destination_dir_fd: int,
) -> None:
    """Linux renameat2(RENAME_NOREPLACE) without a shell or path fallback."""
    import ctypes
    import errno

    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(errno.ENOSYS, "renameat2 is unavailable")
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    if (
        renameat2(
            source_dir_fd,
            os.fsencode(source_name),
            destination_dir_fd,
            os.fsencode(destination_name),
            1,  # RENAME_NOREPLACE
        )
        != 0
    ):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination_name)


def _descriptor_fingerprint(
    descriptor: int,
) -> tuple[str, int, dict[str, tuple[int, int, int, int, int]]]:
    """Fingerprint one open filesystem object without reopening its root path."""
    entries: list[dict[str, Any]] = []
    manifest: dict[str, tuple[int, int, int, int, int]] = {}
    total = 0

    def walk(current: int, relative: str) -> None:
        nonlocal total
        metadata = os.fstat(current)
        entries.append(
            {
                "relative": relative,
                "device": metadata.st_dev,
                "inode": metadata.st_ino,
                "mode": metadata.st_mode,
                "size": metadata.st_size,
                "mtime_ns": metadata.st_mtime_ns,
            }
        )
        manifest[relative] = (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_size,
            metadata.st_mtime_ns,
        )
        if stat.S_ISREG(metadata.st_mode):
            total += metadata.st_size
            return
        if not stat.S_ISDIR(metadata.st_mode):
            return
        for name in sorted(os.listdir(current)):
            child_metadata = os.stat(name, dir_fd=current, follow_symlinks=False)
            child_relative = name if relative == "." else f"{relative}/{name}"
            if stat.S_ISDIR(child_metadata.st_mode):
                child = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=current,
                )
                try:
                    opened = os.fstat(child)
                    if (opened.st_dev, opened.st_ino) != (
                        child_metadata.st_dev,
                        child_metadata.st_ino,
                    ):
                        raise RuntimeError("candidate child identity changed")
                    walk(child, child_relative)
                finally:
                    os.close(child)
            else:
                entries.append(
                    {
                        "relative": child_relative,
                        "device": child_metadata.st_dev,
                        "inode": child_metadata.st_ino,
                        "mode": child_metadata.st_mode,
                        "size": child_metadata.st_size,
                        "mtime_ns": child_metadata.st_mtime_ns,
                    }
                )
                manifest[child_relative] = (
                    child_metadata.st_dev,
                    child_metadata.st_ino,
                    child_metadata.st_mode,
                    child_metadata.st_size,
                    child_metadata.st_mtime_ns,
                )
                if stat.S_ISREG(child_metadata.st_mode):
                    total += child_metadata.st_size

    walk(descriptor, ".")
    entries.sort(key=lambda item: str(item["relative"]))
    serialized = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest(), total, manifest


def _require_descriptor_name(parent: int, name: str, descriptor: int) -> os.stat_result:
    """Require a directory entry to still name one already-open exact inode."""
    opened = os.fstat(descriptor)
    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if (opened.st_dev, opened.st_ino, opened.st_mode) != (
        current.st_dev,
        current.st_ino,
        current.st_mode,
    ):
        raise RuntimeError("candidate changed after quarantine")
    return opened


def _require_descriptor_manifest(
    descriptor: int,
    expected: tuple[int, int, int, int, int],
    *,
    root_exclusive_directory: bool = False,
) -> os.stat_result:
    """Require one open object to retain its complete fingerprint metadata."""
    metadata = os.fstat(descriptor)
    actual = (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
    )
    if root_exclusive_directory and stat.S_ISDIR(expected[2]):
        valid = (
            actual[0] == expected[0]
            and actual[1] == expected[1]
            and stat.S_IFMT(actual[2]) == stat.S_IFMT(expected[2])
            and stat.S_IMODE(actual[2]) == 0o700
            and actual[3:] == expected[3:]
            and metadata.st_uid == 0
            and metadata.st_gid == 0
        )
    else:
        valid = actual == expected
    if not valid:
        raise RuntimeError("candidate identity changed after fingerprint")
    return metadata


def _lock_directory_tree_for_root(descriptor: int) -> None:
    """Deny runtime-UID mutation through paths or already-open directory FDs."""
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        return
    os.fchown(descriptor, 0, 0)
    os.fchmod(descriptor, 0o700)
    for name in sorted(os.listdir(descriptor)):
        child_metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if not stat.S_ISDIR(child_metadata.st_mode):
            continue
        child = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=descriptor,
        )
        try:
            _require_descriptor_name(descriptor, name, child)
            _lock_directory_tree_for_root(child)
        finally:
            os.close(child)


def _remove_descriptor_contents(
    descriptor: int,
    manifest: dict[str, tuple[int, int, int, int, int]],
    relative: str = ".",
    *,
    root_exclusive_directories: bool = False,
) -> None:
    """Remove only exact fingerprinted children relative to an open parent."""

    prefix = "" if relative == "." else f"{relative}/"
    expected_names = sorted(
        path[len(prefix) :]
        for path in manifest
        if path.startswith(prefix) and path != relative and "/" not in path[len(prefix) :]
    )
    if sorted(os.listdir(descriptor)) != expected_names:
        raise RuntimeError("candidate contents changed after fingerprint")
    for name in expected_names:
        child_relative = name if relative == "." else f"{relative}/{name}"
        expected = manifest[child_relative]
        flags = os.O_CLOEXEC | os.O_NOFOLLOW
        if stat.S_ISDIR(expected[2]):
            flags |= os.O_RDONLY | os.O_DIRECTORY | os.O_NONBLOCK
        else:
            flags |= os.O_PATH
        child = os.open(name, flags, dir_fd=descriptor)
        try:
            metadata = _require_descriptor_manifest(
                child,
                expected,
                root_exclusive_directory=root_exclusive_directories,
            )
            _require_descriptor_name(descriptor, name, child)
            if stat.S_ISDIR(metadata.st_mode):
                _remove_descriptor_contents(
                    child,
                    manifest,
                    child_relative,
                    root_exclusive_directories=root_exclusive_directories,
                )
                _require_descriptor_name(descriptor, name, child)
                os.rmdir(name, dir_fd=descriptor)
            else:
                _require_descriptor_name(descriptor, name, child)
                os.unlink(name, dir_fd=descriptor)
        finally:
            os.close(child)


def _quarantine_and_delete(
    path: Path,
    expected_fingerprint: str,
    *,
    before_delete: Callable[[], None] | None = None,
    exclusive_untrusted_uid: int | None = None,
) -> int:
    """Atomically capture, verify, then delete one exact filesystem identity.

    Validation followed by ``rmtree(path)`` leaves a pathname-replacement
    window.  Moving the entry into a fresh same-filesystem quarantine first
    means a concurrent replacement can at worst be captured and rejected; it
    is never deleted.  Rejection restores with ``RENAME_NOREPLACE`` so a new
    source entry is never overwritten.
    """
    parent = path.parent
    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    quarantine: Path | None = None
    quarantine_fd = -1
    captured_fd = -1
    captured = False
    try:
        opened_parent = Path(f"/proc/self/fd/{parent_fd}").resolve(strict=True)
        if opened_parent != parent:
            raise RuntimeError("candidate parent identity changed")
        quarantine = Path(tempfile.mkdtemp(prefix=".threadcells-housekeeping-", dir=parent))
        os.chmod(quarantine, 0o700)
        quarantine_fd = os.open(
            quarantine,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        captured_name = "candidate"
        _rename_noreplace(
            path.name,
            captured_name,
            source_dir_fd=parent_fd,
            destination_dir_fd=quarantine_fd,
        )
        captured = True
        captured_fd = os.open(
            captured_name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=quarantine_fd,
        )
        _require_descriptor_name(quarantine_fd, captured_name, captured_fd)
        current_fingerprint, before, manifest = _descriptor_fingerprint(captured_fd)
        if current_fingerprint != expected_fingerprint:
            try:
                _rename_noreplace(
                    captured_name,
                    path.name,
                    source_dir_fd=quarantine_fd,
                    destination_dir_fd=parent_fd,
                )
                captured = False
            except OSError as restore_error:
                raise RuntimeError(
                    "candidate changed during quarantine; captured replacement retained"
                ) from restore_error
            raise RuntimeError("candidate changed during quarantine")
        if exclusive_untrusted_uid is not None:
            if os.geteuid() != 0 or exclusive_untrusted_uid == 0:
                raise RuntimeError("privileged quarantine authority is unavailable")
            quarantine_metadata = os.fstat(quarantine_fd)
            if (
                quarantine_metadata.st_uid != 0
                or quarantine_metadata.st_gid != 0
                or stat.S_IMODE(quarantine_metadata.st_mode) != 0o700
            ):
                raise RuntimeError("privileged quarantine authority is untrusted")
            _lock_directory_tree_for_root(captured_fd)
        if before_delete is not None:
            try:
                before_delete()
            except Exception:
                try:
                    _rename_noreplace(
                        captured_name,
                        path.name,
                        source_dir_fd=quarantine_fd,
                        destination_dir_fd=parent_fd,
                    )
                    captured = False
                except OSError as restore_error:
                    raise RuntimeError(
                        "replacement failed; captured source retained"
                    ) from restore_error
                raise
        metadata = _require_descriptor_manifest(
            captured_fd,
            manifest["."],
            root_exclusive_directory=exclusive_untrusted_uid is not None,
        )
        _require_descriptor_name(quarantine_fd, captured_name, captured_fd)
        if stat.S_ISDIR(metadata.st_mode):
            _remove_descriptor_contents(
                captured_fd,
                manifest,
                root_exclusive_directories=exclusive_untrusted_uid is not None,
            )
            _require_descriptor_name(quarantine_fd, captured_name, captured_fd)
            os.rmdir(captured_name, dir_fd=quarantine_fd)
        else:
            os.unlink(captured_name, dir_fd=quarantine_fd)
        captured = False
        return before
    finally:
        if captured_fd >= 0:
            os.close(captured_fd)
        if quarantine_fd >= 0:
            os.close(quarantine_fd)
        if quarantine is not None and not captured:
            try:
                quarantine.rmdir()
            except (FileNotFoundError, OSError):
                pass
        os.close(parent_fd)


def _compress(
    path: Path,
    candidate: HousekeepingCandidate,
    *,
    exclusive_untrusted_uid: int | None = None,
) -> int:
    source = path.lstat()
    destination = path.with_suffix(path.suffix + ".gz")
    if destination.exists():
        raise RuntimeError("compression destination already exists")
    temporary: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(name)
        with (
            os.fdopen(fd, "wb") as raw,
            gzip.GzipFile(
                filename=path.name, mode="wb", fileobj=raw, mtime=int(source.st_mtime)
            ) as target,
            path.open("rb") as origin,
        ):
            shutil.copyfileobj(origin, target)
        if candidate_fingerprint(path)[0] != candidate.fingerprint:
            raise RuntimeError("candidate changed during compression")
        os.chmod(temporary, source.st_mode & 0o777)
        os.utime(temporary, ns=(source.st_atime_ns, source.st_mtime_ns))
        destination_parent_fd = os.open(
            destination.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        temporary_entry_name = temporary.name
        try:
            reclaimed = _quarantine_and_delete(
                path,
                candidate.fingerprint,
                exclusive_untrusted_uid=exclusive_untrusted_uid,
                before_delete=lambda: _rename_noreplace(
                    temporary_entry_name,
                    destination.name,
                    source_dir_fd=destination_parent_fd,
                    destination_dir_fd=destination_parent_fd,
                ),
            )
        finally:
            os.close(destination_parent_fd)
        temporary = None
        return max(0, reclaimed - destination.lstat().st_size)
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _execute_candidate(
    path: Path,
    candidate: HousekeepingCandidate,
    *,
    exclusive_untrusted_uid: int | None = None,
) -> int:
    if candidate.action == "compress":
        return _compress(
            path,
            candidate,
            exclusive_untrusted_uid=exclusive_untrusted_uid,
        )
    return _quarantine_and_delete(
        path,
        candidate.fingerprint,
        exclusive_untrusted_uid=exclusive_untrusted_uid,
    )


def privileged_full_cleanup_candidate(candidate: HousekeepingCandidate) -> bool:
    """Return whether Full Cleanup must execute this candidate as root."""
    return candidate.resource_kind in {
        "path",
        "reproducible_cache",
        "browser_process_group",
    }


def _attributes(candidate: HousekeepingCandidate) -> dict[str, str]:
    return dict(candidate.attributes)


def _reconcile_full_cleanup_release_metadata(
    root: Path,
    *,
    config: Mapping[str, Any],
    open_inventory: Callable[[], tuple[set[Path], bool]],
    protection_resolver: Callable[[], ProtectedSet] | None = None,
) -> tuple[str | None, bool | None, str | None]:
    """Remove stale release references only after their targets are gone.

    The caller owns the canonical release-staging lock.  Unknown or divergent
    authority remains untouched and is reported as a protected skip.
    """
    protection = (
        protection_resolver()
        if protection_resolver is not None
        else resolve_protected_set(
            root,
            config,
            open_inventory=open_inventory,
            full_cleanup=True,
        )
    )
    if not protection.release_metadata_certain:
        return None, None, "RELEASE_METADATA_UNKNOWN"
    active = sorted(
        {
            path
            for path, reason in protection.protected_release_reasons
            if reason == "ACTIVE_RELEASE"
        },
        key=str,
    )
    if len(active) != 1:
        return None, None, "ACTIVE_RELEASE_AUTHORITY_AMBIGUOUS"
    active_release = active[0]
    if not active_release.is_dir() or active_release.is_symlink():
        return None, None, "ACTIVE_RELEASE_IDENTITY_CHANGED"
    remaining = [
        path
        for path, reason in protection.release_reference_reasons
        if reason != "ACTIVE_RELEASE" and (path.exists() or path.is_symlink())
    ]
    if remaining:
        return str(active_release), True, "PROTECTED_RELEASE_REFERENCES_REMAIN"

    metadata_path = Path(str(config["release_metadata"]))
    active_link = Path(str(config["active_release_link"]))
    try:
        if active_link.resolve(strict=True) != active_release:
            return str(active_release), None, "ACTIVE_RELEASE_AUTHORITY_CHANGED"
        descriptor = os.open(
            metadata_path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        try:
            metadata_stat = os.fstat(descriptor)
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                descriptor = -1
                metadata = json.load(handle)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if (
            not isinstance(metadata, dict)
            or metadata.get("schema_version") != 1
            or not isinstance(metadata.get("rollback_releases"), list)
            or not isinstance(metadata.get("candidate_releases"), list)
        ):
            return str(active_release), None, "RELEASE_METADATA_CHANGED"
        try:
            release_group = grp.getgrnam(str(config["release_admin_group"]))
            release_control_uid = int(config["release_control_uid"])
            if (
                not stat.S_ISREG(metadata_stat.st_mode)
                or metadata_stat.st_uid != release_control_uid
                or metadata_stat.st_gid != release_group.gr_gid
                or metadata_stat.st_mode & 0o022
            ):
                return str(active_release), None, "RELEASE_METADATA_CHANGED"
            recorded_active = metadata.get("active_release")
            if recorded_active is not None and (
                not isinstance(recorded_active, str)
                or not Path(recorded_active).is_absolute()
                or Path(recorded_active).resolve() != active_release
            ):
                return str(active_release), None, "ACTIVE_RELEASE_AUTHORITY_CHANGED"
            release_roots = set(protection.release_roots)
            for value in [
                *metadata["rollback_releases"],
                *metadata["candidate_releases"],
            ]:
                if (
                    not isinstance(value, str)
                    or not Path(value).is_absolute()
                    or Path(value).resolve().parent not in release_roots
                ):
                    return str(active_release), None, "RELEASE_METADATA_CHANGED"
                referenced = Path(value).resolve()
                if referenced.exists() or referenced.is_symlink():
                    return (
                        str(active_release),
                        True,
                        "PROTECTED_RELEASE_REFERENCES_REMAIN",
                    )
        except (KeyError, OSError, TypeError, ValueError):
            return str(active_release), None, "RELEASE_METADATA_CHANGED"
        metadata["active_release"] = str(active_release)
        metadata["rollback_releases"] = []
        metadata["candidate_releases"] = []
        temporary: Path | None = None
        try:
            temporary_descriptor, name = tempfile.mkstemp(
                prefix=f".{metadata_path.name}.", dir=metadata_path.parent
            )
            temporary = Path(name)
            with os.fdopen(temporary_descriptor, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
                os.fchmod(handle.fileno(), metadata_stat.st_mode & 0o777)
                os.fchown(handle.fileno(), metadata_stat.st_uid, metadata_stat.st_gid)
            os.replace(temporary, metadata_path)
            temporary = None
            parent_descriptor = os.open(metadata_path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent_descriptor)
            finally:
                os.close(parent_descriptor)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError):
        return str(active_release), None, "RELEASE_METADATA_RECONCILE_FAILED"
    return str(active_release), False, None


def _process_group_pidfds(proc_root: Path, process_group: int) -> dict[int, int]:
    """Open stable handles for the exact current members of one process group."""
    if proc_root != Path("/proc") or not hasattr(os, "pidfd_open"):
        raise RuntimeError("stable process identity is unavailable")
    handles: dict[int, int] = {}
    try:
        for process in proc_root.iterdir():
            if not process.name.isdigit():
                continue
            try:
                fields = (process / "stat").read_text(encoding="utf-8").split()
                if int(fields[4]) != process_group:
                    continue
                pid = int(process.name)
                handles[pid] = os.pidfd_open(pid)
            except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError, IndexError):
                continue
        return handles
    except Exception:
        for descriptor in handles.values():
            os.close(descriptor)
        raise


def _pidfd_alive(descriptor: int) -> bool:
    try:
        signal.pidfd_send_signal(descriptor, 0)
        return True
    except ProcessLookupError:
        return False


def _execute_resource(
    candidate: HousekeepingCandidate,
    *,
    config: Mapping[str, Any],
    proc_root: Path,
    runner: Callable[..., Any],
    sleeper: Callable[[float], None],
    exclusive_untrusted_uid: int | None = None,
) -> int:
    attributes = _attributes(candidate)
    timeout = float(config.get("subprocess_timeout_seconds", 20))
    if candidate.resource_kind == "browser_process_group":
        pid = int(attributes["pid"])
        process_group = int(candidate.path.removeprefix("process-group:"))
        handles = _process_group_pidfds(proc_root, process_group)
        if pid not in handles:
            for descriptor in handles.values():
                os.close(descriptor)
            raise RuntimeError("browser process leader identity disappeared")
        try:
            for descriptor in handles.values():
                signal.pidfd_send_signal(descriptor, signal.SIGTERM)
            sleeper(5)
            for descriptor in handles.values():
                if _pidfd_alive(descriptor):
                    signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            sleeper(1)
            if any(_pidfd_alive(descriptor) for descriptor in handles.values()):
                raise RuntimeError("browser process group remained alive")
            profile = Path(attributes["profile"])
            if profile.is_symlink():
                raise RuntimeError("browser profile became a symlink")
            if profile.exists():
                if not profile.is_dir():
                    raise RuntimeError("browser profile identity changed")
                if candidate_fingerprint(profile)[0] != attributes["profile_fingerprint"]:
                    raise RuntimeError("browser profile fingerprint changed")
                _quarantine_and_delete(
                    profile,
                    attributes["profile_fingerprint"],
                    exclusive_untrusted_uid=exclusive_untrusted_uid,
                )
            return candidate.bytes
        finally:
            for descriptor in handles.values():
                os.close(descriptor)
    if candidate.resource_kind in {"docker_container", "docker_volume"}:
        docker = shutil.which("docker")
        if docker is None:
            raise RuntimeError("docker executable disappeared")
        identifier = attributes["identifier"]
        command = (
            [docker, "rm", identifier]
            if candidate.resource_kind == "docker_container"
            else [docker, "volume", "rm", identifier]
        )
        completed = runner(command, capture_output=True, text=True, check=False, timeout=timeout)
        if completed.returncode:
            raise RuntimeError("docker resource removal failed")
        return 0
    if candidate.resource_kind == "package_cache":
        name = attributes["name"]
        entry = next(
            (
                item
                for item in config.get("package_caches", [])
                if str(item.get("name", "")) == name
                and str(Path(str(item.get("path", ""))).resolve()) == candidate.path
            ),
            None,
        )
        cache_command = entry.get("command") if isinstance(entry, Mapping) else None
        path_argument = entry.get("path_argument") if isinstance(entry, Mapping) else None
        from .planner import package_command_bound_to_path

        if not package_command_bound_to_path(
            cache_command,
            path_argument=path_argument,
            path=Path(candidate.path),
        ):
            raise RuntimeError("package cache command authority disappeared")
        assert isinstance(cache_command, list)
        executable = shutil.which(str(cache_command[0]))
        if executable is None:
            raise RuntimeError("package cache executable disappeared")
        from .planner import package_command_running

        try:
            runtime_uid = pwd.getpwnam(str(config["runtime_user"])).pw_uid
        except (KeyError, TypeError) as exc:
            raise RuntimeError("package cache owner is unavailable") from exc
        running = package_command_running(Path(executable).name, proc_root, runtime_uid)
        if running is None:
            raise RuntimeError("package process inventory is unavailable")
        if running:
            raise RuntimeError("package cache command is already running")
        before = candidate_fingerprint(Path(candidate.path))[1]
        completed = runner(
            [executable, *map(str, cache_command[1:])],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        if completed.returncode:
            raise RuntimeError("package cache prune failed")
        path = Path(candidate.path)
        after = candidate_fingerprint(path)[1] if path.exists() else 0
        return max(0, before - after)
    if candidate.resource_kind == "reproducible_cache":
        path = Path(candidate.path)
        configured_roots = {
            Path(str(value)).resolve()
            for value in config.get("reproducible_cache_roots", [])
            if isinstance(value, str) and Path(value).is_absolute()
        }
        planned_root = Path(attributes["root"])
        if (
            planned_root not in configured_roots
            or path.parent != planned_root
            or path.is_symlink()
            or not path.is_dir()
            or candidate_fingerprint(path)[0] != candidate.fingerprint
        ):
            raise RuntimeError("reproducible cache authority changed")
        return _quarantine_and_delete(
            path,
            candidate.fingerprint,
            exclusive_untrusted_uid=exclusive_untrusted_uid,
        )
    if candidate.resource_kind == "terminal_runtime":
        from cli_agent_orchestrator.services.terminal_service import (
            retire_exited_terminal_runtime,
        )

        terminal_id = attributes["terminal_id"]
        if retire_exited_terminal_runtime(terminal_id, proc_root=proc_root) is not True:
            raise RuntimeError("terminal runtime retirement was not confirmed")
        return 0
    if candidate.resource_kind == "workflow_authority":
        import json

        from cli_agent_orchestrator.clients.database import (
            reconcile_orphaned_protected_workflow_authority,
        )

        root_terminal_id = attributes["root_terminal_id"]
        workflow_ids = json.loads(attributes["workflow_ids"])
        direct_assignment_ids = json.loads(attributes.get("direct_assignment_ids", "[]"))
        if (
            not isinstance(workflow_ids, list)
            or not workflow_ids
            or any(not isinstance(value, int) for value in workflow_ids)
            or not isinstance(direct_assignment_ids, list)
            or any(not isinstance(value, int) for value in direct_assignment_ids)
        ):
            raise RuntimeError("workflow authority identity is unavailable")
        result = reconcile_orphaned_protected_workflow_authority(
            root_terminal_id,
            workflow_ids,
            candidate.fingerprint,
            attributes.get("writer_lease_path", ""),
            direct_assignment_ids,
        )
        if not result.get("reconciled") and not result.get("already_reconciled"):
            raise RuntimeError("workflow authority reconciliation was rejected")
        return 0
    if candidate.resource_kind == "retirement_cleanup":
        import json

        from cli_agent_orchestrator.clients.database import (
            claim_completed_child_retirement,
            complete_child_retirement,
            get_child_retirement_cleanup_intent,
        )
        from cli_agent_orchestrator.services.terminal_service import (
            cleanup_managed_worktree,
            validate_managed_worktree_cleanup,
        )

        child = attributes["child_terminal_id"]
        delegation_kind = attributes["delegation_kind"]
        planned_intent = json.loads(attributes["intent"])
        token = attributes.get("claim_token") or None
        if attributes["stage"] in {"legacy", "unclaimed"}:
            claimed = claim_completed_child_retirement(
                attributes["parent_terminal_id"],
                child,
                delegation_kind,
                require_exited_runtime=True,
            )
            if not claimed.get("eligible"):
                raise RuntimeError("retirement cleanup claim was rejected")
            token = claimed.get("claim_token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("retirement cleanup claim identity is unavailable")
        current = get_child_retirement_cleanup_intent(child, token)
        if current is None or current.get("cleanup_completed"):
            raise RuntimeError("retirement cleanup intent is no longer pending")
        if current.get("intent") != planned_intent:
            raise RuntimeError("retirement cleanup intent changed")
        validate_managed_worktree_cleanup(planned_intent)
        cleanup_managed_worktree(planned_intent)
        if not complete_child_retirement(child, token, planned_intent, delegation_kind):
            raise RuntimeError("retirement cleanup finalization raced")
        return candidate.bytes
    raise RuntimeError("unsupported housekeeping resource kind")


def execute_plan(
    plan: HousekeepingPlan,
    *,
    config: Mapping[str, Any],
    open_inventory: Callable[[], tuple[set[Path], bool]],
    settings: Mapping[str, Any] | None = None,
    proc_root: Path = Path("/proc"),
    runner: Callable[..., Any] = subprocess.run,
    sleeper: Callable[[float], None] = time.sleep,
    full_cleanup: bool = False,
    lifecycle_fence_held: bool = False,
    reconcile_releases: bool = True,
    protection_resolver: Callable[[], ProtectedSet] | None = None,
    privileged_path_deletion: bool = False,
) -> ExecutionReport:
    """Execute planned actions while rechecking identity and protection per candidate."""
    report = ExecutionReport(plan_id=plan.plan_id)
    root = Path(plan.root).resolve()
    effective_settings = settings or default_settings(config)
    release_roots = tuple(Path(str(value)).resolve() for value in config.get("release_roots", ()))
    release_actions = any(
        item.category == "releases" and item.action == "delete" for item in plan.candidates
    )
    release_lock_required = release_actions or (full_cleanup and reconcile_releases)
    release_handle = None
    release_lock_acquired = not release_lock_required
    release_authorized = True
    release_authority_reason = "RELEASE_STAGING_BUSY"
    release_group = None
    release_control_uid = None
    exclusive_untrusted_uid: int | None = None
    if privileged_path_deletion:
        if not full_cleanup or os.geteuid() != 0:
            raise RuntimeError("FULL_CLEANUP_PRIVILEGED_EXECUTOR_REQUIRED")
        try:
            exclusive_untrusted_uid = pwd.getpwnam(str(config["runtime_user"])).pw_uid
        except (KeyError, TypeError) as exc:
            raise RuntimeError("FULL_CLEANUP_RUNTIME_OWNER_UNAVAILABLE") from exc
        if exclusive_untrusted_uid == 0:
            raise RuntimeError("FULL_CLEANUP_RUNTIME_OWNER_INVALID")

    def _current_protection() -> ProtectedSet:
        if protection_resolver is not None:
            return protection_resolver()
        return resolve_protected_set(
            root,
            config,
            open_inventory=open_inventory,
            full_cleanup=full_cleanup,
        )

    if release_lock_required:
        try:
            release_group = grp.getgrnam(str(config["release_admin_group"]))
            release_control_uid = int(config["release_control_uid"])
        except (KeyError, TypeError, ValueError):
            release_authorized = False
            release_authority_reason = "RELEASE_CONTROL_CONFIG_INVALID"
        if (
            release_authorized
            and os.geteuid() != 0
            and release_group is not None
            and release_group.gr_gid not in {os.getegid(), *os.getgroups()}
        ):
            release_authorized = False
            release_authority_reason = "RELEASE_ADMIN_GROUP_REQUIRED"
    try:
        if release_lock_required and release_authorized:
            assert release_group is not None and release_control_uid is not None
            lock_path = Path(
                str(
                    config.get(
                        "release_staging_lock",
                        Path(str(config["lock_dir"])) / "release-staging.lock",
                    )
                )
            )
            lock_descriptor = -1
            try:
                lock_descriptor = os.open(
                    lock_path,
                    os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
                    0o660,
                )
                lock_stat = os.fstat(lock_descriptor)
                if (
                    not stat.S_ISREG(lock_stat.st_mode)
                    or lock_stat.st_uid != release_control_uid
                    or lock_stat.st_gid != release_group.gr_gid
                    or lock_stat.st_mode & 0o007
                ):
                    raise OSError("release staging lock ownership is invalid")
                release_handle = os.fdopen(lock_descriptor, "a+")
                lock_descriptor = -1
                try:
                    fcntl.flock(release_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    release_lock_acquired = True
                except BlockingIOError:
                    release_authority_reason = "RELEASE_STAGING_BUSY"
                    report.ok = False
                    report.failures.append(
                        {"candidate": "releases", "reason_code": release_authority_reason}
                    )
            except OSError:
                release_authority_reason = "RELEASE_STAGING_LOCK_INVALID"
                report.ok = False
                report.failures.append(
                    {"candidate": "releases", "reason_code": release_authority_reason}
                )
            finally:
                if lock_descriptor >= 0:
                    os.close(lock_descriptor)
        elif release_lock_required:
            report.ok = False
            report.failures.append(
                {"candidate": "releases", "reason_code": release_authority_reason}
            )
        for candidate in plan.candidates:
            if candidate.action == "preserve":
                continue
            if candidate.category == "releases" and not release_lock_acquired:
                report.skipped.append(
                    {
                        "candidate": candidate.canonical_identity,
                        "reason_code": release_authority_reason,
                    }
                )
                continue
            try:
                if candidate.resource_kind == "workflow_authority":
                    from cli_agent_orchestrator.services.operations_service import (
                        context_lifecycle_fence,
                    )

                    fence = (
                        nullcontext(True)
                        if lifecycle_fence_held
                        else context_lifecycle_fence(config, nonblocking=True)
                    )
                    with fence as acquired:
                        if not acquired:
                            report.skipped.append(
                                {
                                    "candidate": candidate.canonical_identity,
                                    "reason_code": "WORKTREE_LIFECYCLE_BUSY",
                                }
                            )
                            continue
                        current = revalidate_runtime_candidate(
                            candidate,
                            root=root,
                            config=config,
                            settings=effective_settings,
                            now=plan.generated_at,
                            open_inventory=open_inventory,
                            proc_root=proc_root,
                            runner=runner,
                            full_cleanup=full_cleanup,
                        )
                        # A concurrent explicit workflow close removes this
                        # identity from the OPEN/OWNER_GATE planner inventory
                        # while its writer lease can still require exact
                        # reconciliation. Let only that absent-candidate case
                        # reach the transactional database verifier below. It
                        # binds the planned workflow IDs, direct claims, writer
                        # path, terminal absence, and all remaining authority
                        # edges before releasing anything. A candidate which
                        # still exists but changed remains fail-closed here.
                        if current is not None and (
                            current.action == "preserve"
                            or current.protection_reason
                            or current.fingerprint != candidate.fingerprint
                        ):
                            raise RuntimeError("candidate fingerprint changed")
                        _execute_resource(
                            candidate,
                            config=config,
                            proc_root=proc_root,
                            runner=runner,
                            sleeper=sleeper,
                            exclusive_untrusted_uid=exclusive_untrusted_uid,
                        )
                        report.executed_count_by_class[candidate.category] = (
                            report.executed_count_by_class.get(candidate.category, 0) + 1
                        )
                        report.executed.append(candidate.canonical_identity)
                    continue
                if candidate.resource_kind == "git_worktree":
                    from cli_agent_orchestrator.services.operations_service import (
                        context_lifecycle_fence,
                    )

                    fence = (
                        nullcontext(True)
                        if lifecycle_fence_held
                        else context_lifecycle_fence(config, nonblocking=True)
                    )
                    with fence as acquired:
                        if not acquired:
                            report.skipped.append(
                                {
                                    "candidate": candidate.canonical_identity,
                                    "reason_code": "WORKTREE_LIFECYCLE_BUSY",
                                }
                            )
                            continue
                        current_protection = _current_protection()
                        from .worktrees import (
                            remove_worktree,
                            revalidate_worktree_candidate,
                        )

                        current = revalidate_worktree_candidate(
                            candidate,
                            config=config,
                            protection=current_protection,
                            runner=runner,
                        )
                        if current is None:
                            report.skipped.append(
                                {
                                    "candidate": candidate.canonical_identity,
                                    "reason_code": "CANDIDATE_NO_LONGER_ELIGIBLE",
                                }
                            )
                            continue
                        if current.action != "retire" or current.protection_reason:
                            report.skipped.append(
                                {
                                    "candidate": candidate.canonical_identity,
                                    "reason_code": current.protection_reason
                                    or "CANDIDATE_NO_LONGER_ELIGIBLE",
                                }
                            )
                            continue
                        if current.fingerprint != candidate.fingerprint:
                            raise RuntimeError("candidate fingerprint changed")
                        reclaimed = remove_worktree(
                            candidate,
                            runner=runner,
                            timeout=float(config.get("subprocess_timeout_seconds", 20)),
                        )
                        report.freed_bytes += reclaimed
                        report.reclaimed_bytes_by_class[candidate.category] = (
                            report.reclaimed_bytes_by_class.get(candidate.category, 0) + reclaimed
                        )
                        report.executed_count_by_class[candidate.category] = (
                            report.executed_count_by_class.get(candidate.category, 0) + 1
                        )
                        report.executed.append(candidate.canonical_identity)
                    continue
                if candidate.category in {
                    "build_artifact",
                    "ephemeral",
                    "browser_cache",
                    "package_cache",
                    "terminal_runtime",
                    "retirement_cleanup",
                    "reproducible_cache",
                }:
                    current = revalidate_runtime_candidate(
                        candidate,
                        root=root,
                        config=config,
                        settings=effective_settings,
                        now=plan.generated_at,
                        open_inventory=open_inventory,
                        proc_root=proc_root,
                        runner=runner,
                        full_cleanup=full_cleanup,
                    )
                    if current is None:
                        report.skipped.append(
                            {
                                "candidate": candidate.canonical_identity,
                                "reason_code": "CANDIDATE_NO_LONGER_ELIGIBLE",
                            }
                        )
                        continue
                    if current.action == "preserve" or current.protection_reason:
                        report.skipped.append(
                            {
                                "candidate": candidate.canonical_identity,
                                "reason_code": current.protection_reason
                                or "CANDIDATE_NO_LONGER_ELIGIBLE",
                            }
                        )
                        continue
                    if current.fingerprint != candidate.fingerprint:
                        raise RuntimeError("candidate fingerprint changed")
                    if candidate.resource_kind != "path":
                        reclaimed = _execute_resource(
                            candidate,
                            config=config,
                            proc_root=proc_root,
                            runner=runner,
                            sleeper=sleeper,
                            exclusive_untrusted_uid=exclusive_untrusted_uid,
                        )
                        report.freed_bytes += reclaimed
                        report.reclaimed_bytes_by_class[candidate.category] = (
                            report.reclaimed_bytes_by_class.get(candidate.category, 0) + reclaimed
                        )
                        report.executed_count_by_class[candidate.category] = (
                            report.executed_count_by_class.get(candidate.category, 0) + 1
                        )
                        report.executed.append(candidate.canonical_identity)
                        continue
                path = Path(candidate.path)
                resolved = path.resolve(strict=True)
                if candidate.category == "releases":
                    allowed_roots = release_roots
                elif candidate.category == "browser_cache":
                    configured_browser_roots = config.get("playwright_browser_caches") or [
                        config["playwright_browser_cache"]
                    ]
                    allowed_roots = tuple(
                        Path(str(value)).resolve() for value in configured_browser_roots
                    )
                elif candidate.category == "package_cache":
                    allowed_roots = tuple(
                        Path(str(entry.get("path", ""))).resolve()
                        for entry in config.get("package_caches", [])
                    )
                elif candidate.category == "build_artifact":
                    allowed_roots = tuple(
                        Path(str(entry.get("path", ""))).resolve()
                        for entry in config.get("full_cleanup_artifact_roots", [])
                        if isinstance(entry, Mapping)
                        and Path(str(entry.get("path", ""))).is_absolute()
                    )
                else:
                    allowed_roots = (root,)
                if (
                    resolved != path
                    or not any(_within(resolved, allowed) for allowed in allowed_roots)
                    or resolved in allowed_roots
                ):
                    raise RuntimeError("candidate canonical identity changed")
                if candidate.category == "releases":
                    assert release_group is not None and release_control_uid is not None
                    release_stat = path.lstat()
                    if (
                        release_stat.st_uid != release_control_uid
                        or release_stat.st_gid != release_group.gr_gid
                        or release_stat.st_mode & 0o002
                    ):
                        raise RuntimeError("release candidate ownership changed")
                current_fingerprint, _size = candidate_fingerprint(path)
                if current_fingerprint != candidate.fingerprint:
                    raise RuntimeError("candidate fingerprint changed")
                protection = _current_protection()
                reason = protection.reason(path, candidate.category)
                if reason:
                    report.skipped.append(
                        {"candidate": candidate.canonical_identity, "reason_code": reason}
                    )
                    continue
                reclaimed = _execute_candidate(
                    path,
                    candidate,
                    exclusive_untrusted_uid=exclusive_untrusted_uid,
                )
                report.freed_bytes += reclaimed
                report.reclaimed_bytes_by_class[candidate.category] = (
                    report.reclaimed_bytes_by_class.get(candidate.category, 0) + reclaimed
                )
                report.executed_count_by_class[candidate.category] = (
                    report.executed_count_by_class.get(candidate.category, 0) + 1
                )
                report.executed.append(candidate.canonical_identity)
            except FileNotFoundError:
                report.skipped.append(
                    {
                        "candidate": candidate.canonical_identity,
                        "reason_code": "CANDIDATE_DISAPPEARED",
                    }
                )
            except Exception as error:
                report.ok = False
                report.failures.append(
                    {
                        "candidate": candidate.canonical_identity,
                        "reason_code": type(error).__name__,
                    }
                )
        if full_cleanup and reconcile_releases and release_lock_acquired:
            active_release, rollback_available, reason = _reconcile_full_cleanup_release_metadata(
                root,
                config=config,
                open_inventory=open_inventory,
                protection_resolver=protection_resolver,
            )
            report.active_release = active_release
            report.rollback_available = rollback_available
            if reason:
                outcome = {"candidate": "release-metadata", "reason_code": reason}
                if reason in {
                    "ACTIVE_RELEASE_AUTHORITY_CHANGED",
                    "ACTIVE_RELEASE_IDENTITY_CHANGED",
                    "RELEASE_METADATA_CHANGED",
                    "RELEASE_METADATA_RECONCILE_FAILED",
                }:
                    report.ok = False
                    report.failures.append(outcome)
                else:
                    report.skipped.append(outcome)
        return report
    finally:
        if release_handle is not None:
            fcntl.flock(release_handle, fcntl.LOCK_UN)
            release_handle.close()


def merge_execution_reports(first: ExecutionReport, second: ExecutionReport) -> ExecutionReport:
    """Combine two disjoint subplans of one immutable Housekeeping plan."""
    if first.plan_id != second.plan_id:
        raise RuntimeError("HOUSEKEEPING_REPORT_PLAN_MISMATCH")
    merged = ExecutionReport(plan_id=first.plan_id)
    merged.ok = first.ok and second.ok
    merged.freed_bytes = first.freed_bytes + second.freed_bytes
    for source in (first, second):
        for category, value in source.reclaimed_bytes_by_class.items():
            merged.reclaimed_bytes_by_class[category] = (
                merged.reclaimed_bytes_by_class.get(category, 0) + value
            )
        for category, value in source.executed_count_by_class.items():
            merged.executed_count_by_class[category] = (
                merged.executed_count_by_class.get(category, 0) + value
            )
        merged.executed.extend(source.executed)
        merged.skipped.extend(source.skipped)
        merged.failures.extend(source.failures)
    merged.active_release = second.active_release or first.active_release
    merged.rollback_available = (
        second.rollback_available
        if second.rollback_available is not None
        else first.rollback_available
    )
    return merged
