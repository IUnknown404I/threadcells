"""Revalidating executor for immutable Housekeeping.P2 plans."""

from __future__ import annotations

import fcntl
import grp
import gzip
import os
import pwd
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from .models import HousekeepingCandidate, HousekeepingPlan, candidate_fingerprint, default_settings
from .planner import revalidate_runtime_candidate
from .protected_set import resolve_protected_set


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
        before = candidate_fingerprint(path)[1]
        shutil.rmtree(path)
        return before
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
    release_group = None
    release_control_uid = None
    if release_actions:
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
        if release_actions and release_authorized:
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
                if candidate.resource_kind == "workflow_authority":
                    from cli_agent_orchestrator.services.operations_service import (
                        context_lifecycle_fence,
                    )

                    with context_lifecycle_fence(config, nonblocking=True) as acquired:
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

                    with context_lifecycle_fence(config, nonblocking=True) as acquired:
                        if not acquired:
                            report.skipped.append(
                                {
                                    "candidate": candidate.canonical_identity,
                                    "reason_code": "WORKTREE_LIFECYCLE_BUSY",
                                }
                            )
                            continue
                        current_protection = resolve_protected_set(
                            root,
                            config=config,
                            open_inventory=open_inventory,
                        )
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
                protection = resolve_protected_set(root, config, open_inventory=open_inventory)
                reason = protection.reason(path, candidate.category)
                if reason:
                    report.skipped.append(
                        {"candidate": candidate.canonical_identity, "reason_code": reason}
                    )
                    continue
                reclaimed = _execute_candidate(path, candidate)
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
        return report
    finally:
        if release_handle is not None:
            fcntl.flock(release_handle, fcntl.LOCK_UN)
            release_handle.close()
