"""Deterministic Git worktree isolation for writable and review contexts."""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from cli_agent_orchestrator.constants import MANAGED_WORKTREE_DIR


class ManagedWorktreeError(RuntimeError):
    """A managed worktree operation could not be proven safe."""


@dataclass(frozen=True)
class ManagedWorktree:
    kind: str
    source: str
    path: str
    branch: str | None
    commit: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _git(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=check,
        capture_output=True,
        text=True,
        timeout=30,
    )


_MANAGED_KINDS = frozenset({"supervisor", "task", "reviewer"})


def _branch_for(kind: str, identity: str) -> str | None:
    if kind == "reviewer":
        return None
    namespace = "session" if kind == "supervisor" else "task"
    return f"cao/{namespace}/{identity}"


def _managed_path(source: Path, identity: str, kind: str) -> Path:
    repository_key = hashlib.sha256(str(source).encode("utf-8", "strict")).hexdigest()[:16]
    return MANAGED_WORKTREE_DIR / repository_key / f"{kind}-{identity}"


def _repository_root(path: Path) -> Path | None:
    completed = _git("rev-parse", "--show-toplevel", cwd=path, check=False)
    if completed.returncode != 0:
        return None
    return Path(completed.stdout.strip()).resolve(strict=True)


def plan_managed_worktree(source_worktree: str, identity: str, kind: str) -> ManagedWorktree | None:
    """Resolve one immutable managed-worktree plan without changing Git or disk."""
    if kind not in _MANAGED_KINDS:
        raise ValueError("managed worktree kind must be supervisor, task, or reviewer")
    source = Path(source_worktree).resolve(strict=True)
    repository = _repository_root(source)
    if repository is None:
        return None
    commit_result = _git("rev-parse", "--verify", "HEAD", cwd=repository)
    commit = commit_result.stdout.strip()
    if not commit:
        raise ManagedWorktreeError("source repository HEAD is unavailable")
    target = _managed_path(repository, identity, kind)
    branch = _branch_for(kind, identity)
    return ManagedWorktree(
        kind=kind,
        source=str(repository),
        path=str(target.resolve(strict=False)),
        branch=branch,
        commit=commit,
    )


def create_managed_worktree(
    source_worktree: str,
    identity: str,
    kind: str,
    *,
    expected_commit: str | None = None,
    allow_existing: bool = False,
) -> ManagedWorktree | None:
    """Create one isolated worktree, idempotently when durable authority requests it."""
    planned = plan_managed_worktree(source_worktree, identity, kind)
    if planned is None:
        return None
    repository = Path(planned.source)
    target = Path(planned.path)
    commit = expected_commit or planned.commit
    if expected_commit is not None and not re.fullmatch(r"[0-9a-f]{40,64}", expected_commit):
        raise ManagedWorktreeError("managed worktree base revision is invalid")
    branch = planned.branch
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        if not allow_existing:
            raise ManagedWorktreeError(f"managed worktree target already exists: {target}")
        metadata = {
            "id": identity,
            "writable_work_context_id": identity,
            "managed_worktree_origin_terminal_id": identity,
            "launch_worktree": str(target),
            "managed_worktree_kind": kind,
            "managed_worktree_source": str(repository),
            "managed_worktree_branch": branch,
            "managed_worktree_commit": commit,
        }
        status = managed_worktree_status(metadata)
        if not status.get("safe") or status.get("absent"):
            raise ManagedWorktreeError(
                f"managed worktree existing identity is unsafe: {status.get('reason_code')}"
            )
        if status.get("commit") != commit or status.get("branch") != branch:
            raise ManagedWorktreeError("managed worktree existing revision or branch changed")
        return ManagedWorktree(
            kind, str(repository), str(target.resolve(strict=True)), branch, commit
        )

    command = ["worktree", "add"]
    if branch is None:
        command.extend(["--detach", str(target), commit])
    else:
        command.extend(["-b", branch, str(target), commit])
    completed = _git(*command, cwd=repository, check=False)
    if completed.returncode != 0:
        raise ManagedWorktreeError(
            f"git worktree add failed: {(completed.stderr or completed.stdout).strip()}"
        )
    return ManagedWorktree(
        kind=kind,
        source=str(repository),
        path=str(target.resolve(strict=True)),
        branch=branch,
        commit=commit,
    )


def managed_worktree_status(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Return verified lifecycle state without mutating Git or filesystem state."""
    kind = metadata.get("managed_worktree_kind")
    identity = (
        metadata.get("writable_work_context_id")
        or metadata.get("managed_worktree_origin_terminal_id")
        or metadata.get("id")
    )
    path_value = metadata.get("launch_worktree")
    source_value = metadata.get("managed_worktree_source")
    if kind not in _MANAGED_KINDS:
        return {"managed": False}
    if (
        not isinstance(identity, str)
        or not identity
        or not isinstance(path_value, str)
        or not isinstance(source_value, str)
    ):
        return {"managed": True, "safe": False, "reason_code": "MANAGED_WORKTREE_METADATA_INVALID"}
    path = Path(path_value)
    source = Path(source_value)
    if not source.exists():
        return {"managed": True, "safe": False, "reason_code": "MANAGED_WORKTREE_MISSING"}
    resolved_path = path.resolve(strict=False)
    resolved_source = source.resolve(strict=True)
    repository = _repository_root(source)
    expected_path = _managed_path(resolved_source, identity, kind).resolve(strict=False)
    expected_branch = _branch_for(kind, identity)
    if (
        repository != resolved_source
        or resolved_path != expected_path
        or metadata.get("managed_worktree_branch") != expected_branch
    ):
        return {
            "managed": True,
            "safe": False,
            "reason_code": "MANAGED_WORKTREE_IDENTITY_MISMATCH",
        }
    registered = _git("worktree", "list", "--porcelain", cwd=source)
    registered_paths = {
        Path(line.removeprefix("worktree ")).resolve(strict=False)
        for line in registered.stdout.splitlines()
        if line.startswith("worktree ")
    }
    if not path.exists() and not path.is_symlink():
        if resolved_path in registered_paths:
            return {
                "managed": True,
                "safe": False,
                "reason_code": "MANAGED_WORKTREE_MISSING",
            }
        result = {
            "managed": True,
            "safe": True,
            "kind": kind,
            "path": str(resolved_path),
            "source": str(resolved_source),
            "clean": True,
            "absent": True,
            # An absent, unregistered worktree has already crossed the exact
            # cleanup boundary.  Preserve its durable launch commit in the
            # status document so reviewer validation and idempotent replay do
            # not contradict the positive absence proof.
            "commit": metadata.get("managed_worktree_commit"),
            "expected_commit": metadata.get("managed_worktree_commit"),
            "expected_branch": expected_branch,
            "branch": expected_branch,
        }
        if expected_branch is not None:
            branch = _git(
                "show-ref", "--verify", f"refs/heads/{expected_branch}", cwd=source, check=False
            )
            if branch.returncode != 0:
                return {
                    **result,
                    "safe": False,
                    "reason_code": "TASK_WORKTREE_BRANCH_MISSING",
                }
        return result
    resolved_path = path.resolve(strict=True)
    root = _repository_root(path)
    if root != resolved_path:
        return {"managed": True, "safe": False, "reason_code": "MANAGED_WORKTREE_IDENTITY_MISMATCH"}
    if resolved_path not in registered_paths:
        return {
            "managed": True,
            "safe": False,
            "reason_code": "MANAGED_WORKTREE_NOT_REGISTERED",
        }
    status = _git("status", "--porcelain", "--untracked-files=all", cwd=path)
    head = _git("rev-parse", "HEAD", cwd=path).stdout.strip()
    branch = _git("symbolic-ref", "--quiet", "--short", "HEAD", cwd=path, check=False)
    return {
        "managed": True,
        "safe": True,
        "kind": kind,
        "path": str(resolved_path),
        "source": str(resolved_source),
        "commit": head,
        "branch": branch.stdout.strip() if branch.returncode == 0 else None,
        "clean": not bool(status.stdout),
        "expected_commit": metadata.get("managed_worktree_commit"),
        "expected_branch": expected_branch,
    }


def remove_managed_worktree(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Remove a clean managed worktree without deleting its task branch.

    Dirty or unverifiable worktrees are retained fail-closed. A task branch is
    intentionally preserved so committed but not-yet-integrated work remains
    recoverable after terminal retirement.
    """
    status = managed_worktree_status(metadata)
    if not status.get("managed"):
        return {"removed": False, "managed": False}
    if not status.get("safe"):
        return {"removed": False, **status}
    if not status.get("clean"):
        return {
            "removed": False,
            **status,
            "reason_code": "MANAGED_WORKTREE_DIRTY",
        }
    if status.get("absent"):
        return {"removed": True, "already_removed": True, **status}
    if status["kind"] in {"task", "supervisor"} and status.get("branch") != status.get(
        "expected_branch"
    ):
        return {
            "removed": False,
            **status,
            "reason_code": "WRITABLE_WORKTREE_AUTHORITY_CHANGED",
        }
    if status["kind"] == "reviewer":
        if status.get("branch") is not None or status.get("commit") != status.get(
            "expected_commit"
        ):
            return {
                "removed": False,
                **status,
                "reason_code": "REVIEW_WORKTREE_AUTHORITY_CHANGED",
            }
    source = Path(str(status["source"]))
    completed = _git("worktree", "remove", str(status["path"]), cwd=source, check=False)
    if completed.returncode != 0:
        return {
            "removed": False,
            **status,
            "reason_code": "MANAGED_WORKTREE_REMOVE_FAILED",
            "detail": (completed.stderr or completed.stdout).strip(),
        }
    return {"removed": True, **status}


def reconcile_writable_work_context_provisioning() -> int:
    """Reconcile pre-dispatch work and fence ambiguous provider launches.

    A provisioned row intentionally does not launch a provider: process
    dispatch needs its original launch/owner authority. Clean, unclaimed
    pre-dispatch worktrees are removed conservatively. Once the writer lease
    exists, an uncertain provider outcome is preserved for explicit #95
    recovery and is never blindly dispatched again.
    """
    from cli_agent_orchestrator.clients.database import (
        get_terminal_metadata,
        list_writable_work_contexts,
        transition_writable_work_context,
    )
    from cli_agent_orchestrator.services.operations_service import context_lifecycle_fence

    reconciled = 0
    with context_lifecycle_fence(nonblocking=True) as acquired:
        if not acquired:
            return 0
        for row in list_writable_work_contexts(states=("reserved",)):
            try:
                managed = create_managed_worktree(
                    str(row["canonical_source"]),
                    str(row["id"]),
                    "supervisor",
                    expected_commit=str(row["base_revision"]),
                    allow_existing=True,
                )
                if (
                    managed is None
                    or managed.path != row["canonical_worktree"]
                    or managed.branch != row["branch"]
                ):
                    raise ManagedWorktreeError("durable work-context identity changed")
                reconciled += int(
                    transition_writable_work_context(
                        str(row["id"]),
                        expected_states=("reserved",),
                        state="provisioned",
                        event_type="provisioning_recovered_after_restart",
                    )
                )
            except Exception as exc:
                transition_writable_work_context(
                    str(row["id"]),
                    expected_states=("reserved",),
                    state="preserved",
                    event_type="provisioning_preserved",
                    reason_code=type(exc).__name__,
                )

        for row in list_writable_work_contexts(states=("provisioned",)):
            terminal = get_terminal_metadata(str(row["terminal_id"]))
            if terminal is not None:
                reconciled += int(
                    transition_writable_work_context(
                        str(row["id"]),
                        expected_states=("provisioned",),
                        state="preserved",
                        event_type="provisioning_preserved",
                        reason_code="PROVIDER_LAUNCH_OUTCOME_UNCERTAIN",
                    )
                )
                continue
            cleanup = remove_managed_worktree(
                {
                    "id": row["terminal_id"],
                    "writable_work_context_id": row["id"],
                    "managed_worktree_kind": "supervisor",
                    "managed_worktree_source": row["canonical_source"],
                    "managed_worktree_branch": row["branch"],
                    "managed_worktree_commit": row["base_revision"],
                    "launch_worktree": row["canonical_worktree"],
                }
            )
            target_state = "abandoned" if cleanup.get("removed") else "preserved"
            reason = (
                "PROVISIONING_INTERRUPTED_BEFORE_ADMISSION"
                if cleanup.get("removed")
                else str(cleanup.get("reason_code") or "PROVISIONING_CLEANUP_UNCERTAIN")
            )
            reconciled += int(
                transition_writable_work_context(
                    str(row["id"]),
                    expected_states=("provisioned",),
                    state=target_state,
                    event_type=(
                        "provisioning_abandoned"
                        if target_state == "abandoned"
                        else "provisioning_preserved"
                    ),
                    reason_code=reason,
                )
            )

        for row in list_writable_work_contexts(states=("launching",)):
            terminal = get_terminal_metadata(str(row["terminal_id"]))
            running = bool(terminal and terminal.get("runtime_lifecycle") == "running")
            recovery = bool(terminal and terminal.get("recovery_takeover_id"))
            reconciled += int(
                transition_writable_work_context(
                    str(row["id"]),
                    expected_states=("launching",),
                    state="admitted" if running else "preserved",
                    event_type=(
                        ("recovery_supervisor_admitted" if recovery else "supervisor_admitted")
                        if running
                        else "provisioning_preserved"
                    ),
                    reason_code=(None if running else "PROVIDER_LAUNCH_OUTCOME_UNCERTAIN"),
                )
            )
    return reconciled
