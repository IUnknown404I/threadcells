"""Revalidating executor for immutable Housekeeping.P2 plans."""

from __future__ import annotations

import fcntl
import grp
import gzip
import os
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from .models import HousekeepingCandidate, HousekeepingPlan, candidate_fingerprint, default_settings
from .planner import build_plan
from .protected_set import resolve_protected_set


@dataclass
class ExecutionReport:
    plan_id: str
    ok: bool = True
    freed_bytes: int = 0
    executed: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _compress(path: Path, candidate: HousekeepingCandidate) -> int:
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
        os.replace(temporary, destination)
        temporary = None
        path.unlink()
        return max(0, source.st_size - destination.lstat().st_size)
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _execute_candidate(path: Path, candidate: HousekeepingCandidate) -> int:
    if candidate.action == "compress":
        return _compress(path, candidate)
    before = candidate_fingerprint(path)[1]
    if path.is_dir():
        if path.is_symlink():
            raise RuntimeError("directory candidate became a symlink")
        shutil.rmtree(path)
    else:
        path.unlink()
    return before


def _attributes(candidate: HousekeepingCandidate) -> dict[str, str]:
    return dict(candidate.attributes)


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


def _command_running(name: str, proc_root: Path) -> bool | None:
    try:
        processes = list(proc_root.iterdir())
    except OSError:
        return None
    for process in processes:
        if not process.name.isdigit():
            continue
        try:
            command = (process / "cmdline").read_bytes().split(b"\0", 1)[0]
            if Path(os.fsdecode(command)).name == name:
                return True
        except FileNotFoundError:
            continue
        except OSError:
            return None
    return False


def _execute_resource(
    candidate: HousekeepingCandidate,
    *,
    config: Mapping[str, Any],
    proc_root: Path,
    runner: Callable[..., Any],
    sleeper: Callable[[float], None],
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
                shutil.rmtree(profile)
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
        if not isinstance(cache_command, list) or not cache_command:
            raise RuntimeError("package cache command authority disappeared")
        executable = shutil.which(str(cache_command[0]))
        if executable is None:
            raise RuntimeError("package cache executable disappeared")
        running = _command_running(Path(executable).name, proc_root)
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
    if candidate.resource_kind == "terminal_runtime":
        from cli_agent_orchestrator.services.terminal_service import (
            retire_exited_terminal_runtime,
        )

        terminal_id = attributes["terminal_id"]
        if retire_exited_terminal_runtime(terminal_id, proc_root=proc_root) is not True:
            raise RuntimeError("terminal runtime retirement was not confirmed")
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
        if attributes["stage"] == "legacy":
            claimed = claim_completed_child_retirement(
                attributes["parent_terminal_id"], child, delegation_kind
            )
            if not claimed.get("eligible"):
                raise RuntimeError("legacy retirement cleanup claim was rejected")
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
) -> ExecutionReport:
    """Execute planned actions while rechecking identity and protection per candidate."""
    report = ExecutionReport(plan_id=plan.plan_id)
    root = Path(plan.root).resolve()
    effective_settings = settings or default_settings(config)
    release_roots = tuple(Path(str(value)).resolve() for value in config.get("release_roots", ()))
    release_actions = any(
        item.category == "releases" and item.action == "delete" for item in plan.candidates
    )
    release_handle = None
    release_lock_acquired = not release_actions
    release_authorized = True
    release_authority_reason = "RELEASE_STAGING_BUSY"
    if release_actions and os.geteuid() != 0:
        try:
            release_group = grp.getgrnam(str(config["release_admin_group"]))
            release_authorized = release_group.gr_gid in {
                os.getegid(),
                *os.getgroups(),
            }
        except (KeyError, TypeError):
            release_authorized = False
        if not release_authorized:
            release_authority_reason = "RELEASE_ADMIN_GROUP_REQUIRED"
    try:
        if release_actions and release_authorized:
            lock_path = Path(
                str(
                    config.get(
                        "release_staging_lock",
                        Path(str(config["lock_dir"])) / "release-staging.lock",
                    )
                )
            )
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            release_handle = lock_path.open("a+")
            try:
                fcntl.flock(release_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                release_lock_acquired = True
            except BlockingIOError:
                report.ok = False
                report.failures.append(
                    {"candidate": "releases", "reason_code": "RELEASE_STAGING_BUSY"}
                )
        elif release_actions:
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
                if candidate.category in {
                    "ephemeral",
                    "browser_cache",
                    "package_cache",
                    "terminal_runtime",
                    "retirement_cleanup",
                }:
                    refreshed = build_plan(
                        root=root,
                        config=config,
                        settings=effective_settings,
                        mode=plan.mode,
                        now=plan.generated_at,
                        open_inventory=open_inventory,
                        proc_root=proc_root,
                        runner=runner,
                    )
                    current = next(
                        (
                            item
                            for item in refreshed.candidates
                            if item.canonical_identity == candidate.canonical_identity
                        ),
                        None,
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
                        report.freed_bytes += _execute_resource(
                            candidate,
                            config=config,
                            proc_root=proc_root,
                            runner=runner,
                            sleeper=sleeper,
                        )
                        report.executed.append(candidate.canonical_identity)
                        continue
                path = Path(candidate.path)
                resolved = path.resolve(strict=True)
                if candidate.category == "releases":
                    allowed_roots = release_roots
                elif candidate.category == "browser_cache":
                    allowed_roots = (Path(str(config["playwright_browser_cache"])).resolve(),)
                elif candidate.category == "package_cache":
                    allowed_roots = tuple(
                        Path(str(entry.get("path", ""))).resolve()
                        for entry in config.get("package_caches", [])
                    )
                else:
                    allowed_roots = (root,)
                if (
                    resolved != path
                    or not any(_within(resolved, allowed) for allowed in allowed_roots)
                    or resolved in allowed_roots
                ):
                    raise RuntimeError("candidate canonical identity changed")
                current_fingerprint, _size = candidate_fingerprint(path)
                if current_fingerprint != candidate.fingerprint:
                    raise RuntimeError("candidate fingerprint changed")
                protection = resolve_protected_set(root, config, open_inventory=open_inventory)
                reason = protection.reason(path, candidate.category)
                if reason:
                    report.skipped.append(
                        {"candidate": candidate.canonical_identity, "reason_code": reason}
                    )
                    continue
                report.freed_bytes += _execute_candidate(path, candidate)
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
        return report
    finally:
        if release_handle is not None:
            fcntl.flock(release_handle, fcntl.LOCK_UN)
            release_handle.close()
