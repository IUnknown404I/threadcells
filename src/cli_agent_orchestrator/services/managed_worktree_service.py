"""Deterministic Git worktree isolation for managed delegated contexts."""

from __future__ import annotations

import hashlib
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


def _managed_path(source: Path, terminal_id: str, kind: str) -> Path:
    repository_key = hashlib.sha256(str(source).encode("utf-8", "strict")).hexdigest()[:16]
    return MANAGED_WORKTREE_DIR / repository_key / f"{kind}-{terminal_id}"


def _repository_root(path: Path) -> Path | None:
    completed = _git("rev-parse", "--show-toplevel", cwd=path, check=False)
    if completed.returncode != 0:
        return None
    return Path(completed.stdout.strip()).resolve(strict=True)


def create_managed_worktree(
    source_worktree: str, terminal_id: str, kind: str
) -> ManagedWorktree | None:
    """Create one cheap isolated worktree, or return None outside a Git repository."""
    if kind not in {"task", "reviewer"}:
        raise ValueError("managed worktree kind must be task or reviewer")
    source = Path(source_worktree).resolve(strict=True)
    repository = _repository_root(source)
    if repository is None:
        return None
    commit_result = _git("rev-parse", "--verify", "HEAD", cwd=repository)
    commit = commit_result.stdout.strip()
    if not commit:
        raise ManagedWorktreeError("source repository HEAD is unavailable")
    target = _managed_path(repository, terminal_id, kind)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        raise ManagedWorktreeError(f"managed worktree target already exists: {target}")

    branch = None if kind == "reviewer" else f"cao/task/{terminal_id}"
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
    terminal_id = metadata.get("id")
    path_value = metadata.get("launch_worktree")
    source_value = metadata.get("managed_worktree_source")
    if kind not in {"task", "reviewer"}:
        return {"managed": False}
    if (
        not isinstance(terminal_id, str)
        or not terminal_id
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
    expected_path = _managed_path(resolved_source, terminal_id, kind).resolve(strict=False)
    expected_branch = None if kind == "reviewer" else f"cao/task/{terminal_id}"
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
    if status["kind"] == "task" and status.get("branch") != status.get("expected_branch"):
        return {
            "removed": False,
            **status,
            "reason_code": "TASK_WORKTREE_AUTHORITY_CHANGED",
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
