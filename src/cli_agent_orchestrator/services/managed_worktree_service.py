"""Deterministic Git worktree isolation for writable and review contexts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

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


def _dirty_content_fingerprint(path: Path, *, status: str, head: str) -> str:
    """Bind a dirty worktree candidate to the exact changed file contents.

    This deliberately hashes only Git-reported changed and untracked paths, so
    ordinary Housekeeping does not crawl immutable dependency trees.  Clean
    worktrees are already bound by HEAD and the empty porcelain status.
    """
    changed = _git("diff", "--name-only", "-z", "HEAD", "--", cwd=path).stdout
    untracked = _git("ls-files", "--others", "--exclude-standard", "-z", cwd=path).stdout
    relative_paths = sorted({item for item in f"{changed}\0{untracked}".split("\0") if item})
    digest = hashlib.sha256()
    digest.update(head.encode("utf-8", "surrogateescape"))
    digest.update(b"\0")
    digest.update(status.encode("utf-8", "surrogateescape"))
    for relative in relative_paths:
        digest.update(b"\0path\0")
        digest.update(relative.encode("utf-8", "surrogateescape"))
        candidate = path / relative
        try:
            resolved_parent = candidate.parent.resolve(strict=True)
            resolved_parent.relative_to(path)
            metadata = candidate.lstat()
        except (OSError, ValueError):
            digest.update(b"\0absent")
            continue
        digest.update(f"\0{metadata.st_mode}:{metadata.st_size}".encode())
        if candidate.is_symlink():
            digest.update(b"\0symlink\0")
            digest.update(candidate.readlink().as_posix().encode("utf-8", "surrogateescape"))
        elif candidate.is_file():
            with candidate.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
    return digest.hexdigest()


_MANAGED_KINDS = frozenset({"supervisor", "task", "reviewer"})
_EXACT_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_MAX_WORKTREE_INVENTORY_BYTES = 2 * 1024 * 1024
_MAX_WORKTREE_INVENTORY_ROWS = 4096


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


def _path_identity(path: Path) -> dict[str, int]:
    metadata = path.stat()
    return {"device": int(metadata.st_dev), "inode": int(metadata.st_ino)}


def _bounded_git_output(
    *args: str,
    cwd: Path,
    maximum_bytes: int,
) -> str:
    """Read one Git result without permitting unbounded captured output."""
    process = subprocess.Popen(
        ["git", "-C", str(cwd), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if process.stdout is None:  # pragma: no cover - Popen contract
        process.kill()
        process.wait()
        raise ManagedWorktreeError("MANAGED_WORKTREE_INVENTORY_UNAVAILABLE")
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    output = bytearray()
    deadline = time.monotonic() + 30
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, 30)
            events = selector.select(remaining)
            if not events:
                raise subprocess.TimeoutExpired(process.args, 30)
            for key, _mask in events:
                chunk = os.read(key.fd, min(65536, maximum_bytes + 1 - len(output)))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                output.extend(chunk)
                if len(output) > maximum_bytes:
                    raise ManagedWorktreeError("MANAGED_WORKTREE_INVENTORY_LIMIT")
        return_code = process.wait(timeout=max(0.0, deadline - time.monotonic()))
    except BaseException:
        process.kill()
        process.wait()
        raise
    finally:
        selector.close()
        process.stdout.close()
    decoded = output.decode("utf-8", "surrogateescape")
    if return_code != 0:
        raise subprocess.CalledProcessError(
            return_code,
            process.args,
            output=decoded,
            stderr=decoded,
        )
    return decoded


def _repository_authority(source: Path) -> dict[str, Any]:
    """Read exact live source/common-dir identity without enumerating worktrees."""
    resolved_source = source.resolve(strict=True)
    if _repository_root(resolved_source) != resolved_source:
        raise ManagedWorktreeError("MANAGED_WORKTREE_IDENTITY_MISMATCH")
    common_dir = Path(
        _git(
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
            cwd=resolved_source,
        ).stdout.strip()
    ).resolve(strict=True)
    return {
        "source": str(resolved_source),
        "source_identity": _path_identity(resolved_source),
        "git_common_dir": str(common_dir),
        "git_common_dir_identity": _path_identity(common_dir),
    }


def _repository_authority_matches(
    expected: Mapping[str, Any],
    current: Mapping[str, Any],
) -> bool:
    return all(
        expected.get(key) == current.get(key)
        for key in (
            "source",
            "source_identity",
            "git_common_dir",
            "git_common_dir_identity",
        )
    )


def _worktree_registration_link_matches(path: Path, git_dir: Path) -> bool:
    """Prove the worktree and common-dir administration point at each other."""
    marker = path / ".git"
    backlink = git_dir / "gitdir"
    try:
        if marker.is_symlink() or backlink.is_symlink():
            return False
        marker_stat = marker.stat()
        backlink_stat = backlink.stat()
        if marker_stat.st_size > 4096 or backlink_stat.st_size > 4096:
            return False
        marker_value = marker.read_text(errors="surrogateescape").strip()
        backlink_value = backlink.read_text(errors="surrogateescape").strip()
        prefix = "gitdir: "
        if not marker_value.startswith(prefix):
            return False
        marker_git_dir = Path(marker_value.removeprefix(prefix)).resolve(strict=True)
        backlink_marker = Path(backlink_value).resolve(strict=False)
    except OSError:
        return False
    return marker_git_dir == git_dir and backlink_marker == marker.resolve(strict=False)


def _load_worktree_inventory(source: Path) -> dict[str, Any]:
    """Load one exact repository worktree inventory for bounded reuse."""
    authority = _repository_authority(source)
    resolved_source = Path(str(authority["source"]))
    registrations: dict[str, dict[str, Any]] = {}
    listing = _bounded_git_output(
        "worktree",
        "list",
        "--porcelain",
        cwd=resolved_source,
        maximum_bytes=_MAX_WORKTREE_INVENTORY_BYTES,
    )
    row_count = 0
    for block in listing.split("\n\n"):
        fields: dict[str, str | bool] = {}
        for line in block.splitlines():
            key, _separator, value = line.partition(" ")
            if key in {"bare", "detached", "prunable"}:
                fields[key] = True
            elif key in {"worktree", "HEAD", "branch", "locked"}:
                fields[key] = value
        path_value = fields.get("worktree")
        if not isinstance(path_value, str) or not path_value:
            continue
        row_count += 1
        if row_count > _MAX_WORKTREE_INVENTORY_ROWS:
            raise ManagedWorktreeError("MANAGED_WORKTREE_INVENTORY_LIMIT")
        path = Path(path_value).resolve(strict=False)
        registrations[str(path)] = {
            "path": str(path),
            "head": fields.get("HEAD"),
            "head_ref": fields.get("branch"),
            "detached": bool(fields.get("detached")),
            "locked": fields.get("locked") if "locked" in fields else None,
            "prunable": bool(fields.get("prunable")),
        }
    return {
        **authority,
        "registrations": registrations,
    }


def _owned_ref_object(source: Path, ref_name: str | None) -> str | None:
    if ref_name is None:
        return None
    result = _git("rev-parse", "--verify", ref_name, cwd=source, check=False)
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", value):
        raise ManagedWorktreeError("MANAGED_WORKTREE_BRANCH_CHANGED")
    return value


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


def managed_worktree_status(
    metadata: Mapping[str, Any],
    *,
    inventory: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
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
    if path.is_symlink():
        return {
            "managed": True,
            "safe": False,
            "reason_code": "MANAGED_WORKTREE_IDENTITY_MISMATCH",
        }
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
    try:
        current_inventory = dict(inventory or _load_worktree_inventory(resolved_source))
        live_repository = _repository_authority(resolved_source)
    except ManagedWorktreeError as exc:
        return {
            "managed": True,
            "safe": False,
            "reason_code": str(exc) or "MANAGED_WORKTREE_IDENTITY_MISMATCH",
        }
    except (OSError, subprocess.SubprocessError):
        return {
            "managed": True,
            "safe": False,
            "reason_code": "MANAGED_WORKTREE_IDENTITY_MISMATCH",
        }
    if not _repository_authority_matches(current_inventory, live_repository):
        return {
            "managed": True,
            "safe": False,
            "reason_code": "MANAGED_WORKTREE_IDENTITY_MISMATCH",
        }
    registrations = current_inventory.get("registrations")
    if not isinstance(registrations, Mapping):
        return {
            "managed": True,
            "safe": False,
            "reason_code": "MANAGED_WORKTREE_IDENTITY_MISMATCH",
        }
    registration = registrations.get(str(resolved_path))
    if not path.exists() and not path.is_symlink():
        if registration is not None:
            return {
                "managed": True,
                "safe": False,
                "reason_code": "MANAGED_WORKTREE_MISSING",
            }
        # ``git worktree move`` keeps the administrative registration under
        # the original managed basename.  Its continued existence proves the
        # worktree moved rather than retired; launch-path absence alone is not
        # sufficient deletion authority.
        expected_git_dir = Path(str(current_inventory["git_common_dir"])) / "worktrees" / path.name
        if expected_git_dir.exists() or expected_git_dir.is_symlink():
            return {
                "managed": True,
                "safe": False,
                "reason_code": "MANAGED_WORKTREE_AUTHORITY_CHANGED",
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
            "modified_files": 0,
            "untracked_files": 0,
            "source_identity": current_inventory.get("source_identity"),
            "git_common_dir": current_inventory.get("git_common_dir"),
            "git_common_dir_identity": current_inventory.get("git_common_dir_identity"),
            "registered": False,
        }
        if expected_branch is not None:
            branch_object = _owned_ref_object(resolved_source, f"refs/heads/{expected_branch}")
            if branch_object is not None:
                result["commit"] = branch_object
        return result
    resolved_path = path.resolve(strict=True)
    root = _repository_root(path)
    if root != resolved_path:
        return {"managed": True, "safe": False, "reason_code": "MANAGED_WORKTREE_IDENTITY_MISMATCH"}
    if registration is None:
        return {
            "managed": True,
            "safe": False,
            "reason_code": "MANAGED_WORKTREE_NOT_REGISTERED",
        }
    if registration.get("locked") is not None or registration.get("prunable"):
        return {
            "managed": True,
            "safe": False,
            "reason_code": "MANAGED_WORKTREE_AUTHORITY_CHANGED",
        }
    status = _git("status", "--porcelain", "--untracked-files=all", cwd=path)
    status_lines = [line for line in status.stdout.splitlines() if line]
    head = _git("rev-parse", "HEAD", cwd=path).stdout.strip()
    head_ref = _git("symbolic-ref", "--quiet", "HEAD", cwd=path, check=False)
    branch = (
        head_ref.stdout.strip().removeprefix("refs/heads/") if head_ref.returncode == 0 else None
    )
    detached = head_ref.returncode != 0
    if (
        registration.get("head") != head
        or registration.get("head_ref") != (head_ref.stdout.strip() if not detached else None)
        or bool(registration.get("detached")) != detached
    ):
        return {
            "managed": True,
            "safe": False,
            "reason_code": "MANAGED_WORKTREE_AUTHORITY_CHANGED",
        }
    git_common_dir = Path(
        _git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=path).stdout.strip()
    ).resolve(strict=True)
    if git_common_dir != Path(str(current_inventory["git_common_dir"])):
        return {
            "managed": True,
            "safe": False,
            "reason_code": "MANAGED_WORKTREE_IDENTITY_MISMATCH",
        }
    git_dir = Path(
        _git("rev-parse", "--path-format=absolute", "--git-dir", cwd=path).stdout.strip()
    ).resolve(strict=True)
    try:
        git_dir.relative_to(git_common_dir / "worktrees")
    except ValueError:
        return {
            "managed": True,
            "safe": False,
            "reason_code": "MANAGED_WORKTREE_IDENTITY_MISMATCH",
        }
    if not _worktree_registration_link_matches(resolved_path, git_dir):
        return {
            "managed": True,
            "safe": False,
            "reason_code": "MANAGED_WORKTREE_AUTHORITY_CHANGED",
        }
    content_fingerprint = _dirty_content_fingerprint(
        resolved_path,
        status=status.stdout,
        head=head,
    )
    try:
        final_repository = _repository_authority(resolved_source)
    except (ManagedWorktreeError, OSError, subprocess.SubprocessError):
        return {
            "managed": True,
            "safe": False,
            "reason_code": "MANAGED_WORKTREE_IDENTITY_MISMATCH",
        }
    if not _repository_authority_matches(current_inventory, final_repository):
        return {
            "managed": True,
            "safe": False,
            "reason_code": "MANAGED_WORKTREE_IDENTITY_MISMATCH",
        }
    return {
        "managed": True,
        "safe": True,
        "kind": kind,
        "path": str(resolved_path),
        "source": str(resolved_source),
        "commit": head,
        "branch": branch,
        "head_ref": head_ref.stdout.strip() if not detached else None,
        "detached": detached,
        "clean": not bool(status.stdout),
        "modified_files": sum(not line.startswith("??") for line in status_lines),
        "untracked_files": sum(line.startswith("??") for line in status_lines),
        "content_fingerprint": content_fingerprint,
        "expected_commit": metadata.get("managed_worktree_commit"),
        "expected_branch": expected_branch,
        "source_identity": final_repository.get("source_identity"),
        "git_common_dir": str(git_common_dir),
        "git_common_dir_identity": final_repository.get("git_common_dir_identity"),
        "path_identity": _path_identity(resolved_path),
        "git_dir": str(git_dir),
        "git_dir_identity": _path_identity(git_dir),
        "registered": True,
    }


def prepare_reviewer_worktree_revision(
    metadata: Mapping[str, Any], revision: str
) -> dict[str, Any]:
    """Move one clean, owned reviewer worktree to an exact detached commit.

    The caller must hold the terminal's durable review-preparation runtime
    operation while this function runs. This layer independently proves Git
    source, registration, path, cleanliness, and detached-state authority
    before and after the only mutation. Launch metadata remains provenance;
    the exact review attempt is the current checkout authority.
    """
    exact_revision = revision.strip().lower()
    terminal_id = metadata.get("id")
    if (
        not isinstance(terminal_id, str)
        or metadata.get("managed_worktree_kind") != "reviewer"
        or metadata.get("managed_worktree_origin_terminal_id") != terminal_id
        or metadata.get("managed_worktree_branch") is not None
        or _EXACT_COMMIT_PATTERN.fullmatch(exact_revision) is None
    ):
        raise ManagedWorktreeError("REVIEW_WORKTREE_AUTHORITY_CHANGED")
    before = managed_worktree_status(metadata)
    if (
        not before.get("managed")
        or not before.get("safe")
        or before.get("absent")
        or before.get("kind") != "reviewer"
        or not before.get("clean")
        or before.get("branch") is not None
        or not before.get("detached")
    ):
        raise ManagedWorktreeError(
            str(before.get("reason_code") or "REVIEW_WORKTREE_AUTHORITY_CHANGED")
        )
    path = Path(str(before["path"]))
    available = _git("cat-file", "-e", f"{exact_revision}^{{commit}}", cwd=path, check=False)
    if available.returncode != 0:
        raise ManagedWorktreeError("REVIEW_REVISION_UNAVAILABLE")
    if before.get("commit") != exact_revision:
        switched = _git("switch", "--detach", exact_revision, cwd=path, check=False)
        if switched.returncode != 0:
            raise ManagedWorktreeError("REVIEW_WORKTREE_PREPARATION_FAILED")
    after = managed_worktree_status(metadata)
    stable_identity_fields = (
        "source",
        "source_identity",
        "git_common_dir",
        "git_common_dir_identity",
        "path",
        "path_identity",
        "git_dir",
        "git_dir_identity",
    )
    if (
        not after.get("safe")
        or after.get("absent")
        or after.get("kind") != "reviewer"
        or not after.get("clean")
        or after.get("branch") is not None
        or not after.get("detached")
        or after.get("commit") != exact_revision
        or any(before.get(field) != after.get(field) for field in stable_identity_fields)
    ):
        raise ManagedWorktreeError("REVIEW_WORKTREE_AUTHORITY_CHANGED")
    return after


def reviewer_worktree_matches_revision(metadata: Mapping[str, Any], revision: str) -> bool:
    """Prove exact detached reviewer state immediately before task transport."""
    exact_revision = revision.strip().lower()
    if _EXACT_COMMIT_PATTERN.fullmatch(exact_revision) is None:
        return False
    status = managed_worktree_status(metadata)
    return bool(
        status.get("managed")
        and status.get("safe")
        and not status.get("absent")
        and status.get("kind") == "reviewer"
        and status.get("clean")
        and status.get("branch") is None
        and status.get("detached")
        and status.get("commit") == exact_revision
    )


def _retirement_authority_document(
    metadata: Mapping[str, Any],
    status: Mapping[str, Any],
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    terminal_id = metadata.get("id") or metadata.get("terminal_id")
    if not isinstance(terminal_id, str) or not terminal_id:
        raise ManagedWorktreeError("MANAGED_WORKTREE_METADATA_INVALID")
    metadata_session_id = metadata.get("session_id")
    if session_id is not None and metadata_session_id not in {None, session_id}:
        raise ManagedWorktreeError("MANAGED_WORKTREE_METADATA_INVALID")
    bound_session_id = session_id or metadata_session_id
    if not status.get("managed"):
        return {
            "version": 1,
            "terminal_id": terminal_id,
            "session_id": bound_session_id,
            "managed": False,
        }
    kind = metadata.get("managed_worktree_kind")
    identity = (
        metadata.get("writable_work_context_id")
        or metadata.get("managed_worktree_origin_terminal_id")
        or terminal_id
    )
    if kind not in _MANAGED_KINDS or not isinstance(identity, str) or not identity:
        raise ManagedWorktreeError("MANAGED_WORKTREE_METADATA_INVALID")
    expected_branch = _branch_for(str(kind), identity)
    current_branch = status.get("branch")
    if kind == "reviewer" and not status.get("absent") and current_branch is not None:
        raise ManagedWorktreeError("REVIEW_WORKTREE_AUTHORITY_CHANGED")
    if (
        kind in {"supervisor", "task"}
        and isinstance(current_branch, str)
        and current_branch.startswith("cao/")
        and current_branch != expected_branch
    ):
        raise ManagedWorktreeError("WRITABLE_WORKTREE_AUTHORITY_CHANGED")
    source = Path(str(status["source"]))
    owned_ref = f"refs/heads/{expected_branch}" if expected_branch is not None else None
    owned_ref_object_id = _owned_ref_object(source, owned_ref)
    absent = bool(status.get("absent"))
    return {
        "version": 1,
        "terminal_id": terminal_id,
        "session_id": bound_session_id,
        "managed": True,
        "project_id": metadata.get("project_id"),
        "kind": kind,
        "identity": identity,
        "source": str(status["source"]),
        "source_identity": status.get("source_identity"),
        "git_common_dir": status.get("git_common_dir"),
        "git_common_dir_identity": status.get("git_common_dir_identity"),
        "path": str(status["path"]),
        "present": not absent,
        "registered": bool(status.get("registered")),
        "path_identity": None if absent else status.get("path_identity"),
        "git_dir": None if absent else status.get("git_dir"),
        "git_dir_identity": None if absent else status.get("git_dir_identity"),
        "head": None if absent else status.get("commit"),
        "branch": None if absent else current_branch,
        "head_ref": None if absent else status.get("head_ref"),
        "detached": None if absent else bool(status.get("detached")),
        "clean": bool(status.get("clean")),
        "modified_files": int(status.get("modified_files") or 0),
        "untracked_files": int(status.get("untracked_files") or 0),
        "content_fingerprint": None if absent else status.get("content_fingerprint"),
        "writer_authority_generation": metadata.get("writer_authority_generation"),
        "writable_work_context_id": metadata.get("writable_work_context_id"),
        "owned_ref": owned_ref,
        "owned_ref_object_id": owned_ref_object_id,
    }


def capture_session_worktree_retirement_authority(
    terminals: Sequence[Mapping[str, Any]],
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Capture exact current Git authority for one bounded Session inventory."""
    inventories: dict[str, dict[str, Any]] = {}
    documents: list[dict[str, Any]] = []
    try:
        for metadata in terminals:
            source_value = metadata.get("managed_worktree_source")
            inventory = None
            if metadata.get("managed_worktree_kind") in _MANAGED_KINDS:
                if not isinstance(source_value, str) or not source_value:
                    raise ManagedWorktreeError("MANAGED_WORKTREE_METADATA_INVALID")
                resolved_source = str(Path(source_value).resolve(strict=True))
                inventory = inventories.get(resolved_source)
                if inventory is None:
                    inventory = _load_worktree_inventory(Path(resolved_source))
                    inventories[resolved_source] = inventory
            status = managed_worktree_status(metadata, inventory=inventory)
            if status.get("managed") and not status.get("safe"):
                raise ManagedWorktreeError(
                    str(status.get("reason_code") or "MANAGED_WORKTREE_UNVERIFIED")
                )
            documents.append(
                _retirement_authority_document(
                    metadata,
                    status,
                    session_id=session_id,
                )
            )
    except (ManagedWorktreeError, OSError, subprocess.SubprocessError) as exc:
        return {
            "safe": False,
            "reason_code": str(exc) or "MANAGED_WORKTREE_UNVERIFIED",
        }
    authority = {
        "version": 1,
        "worktrees": sorted(documents, key=lambda item: str(item["terminal_id"])),
    }
    encoded = json.dumps(authority, sort_keys=True, separators=(",", ":"))
    return {
        "safe": True,
        "authority": authority,
        "authority_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
        "modified_files": sum(int(item.get("modified_files") or 0) for item in documents),
        "untracked_files": sum(int(item.get("untracked_files") or 0) for item in documents),
        # Internal execution aid: the same bounded repository inventory can be
        # reused for the immediate per-row recheck without another full scan.
        "_inventories": inventories,
    }


def _retirement_authority_matches(
    expected: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    require_already_absent: bool,
) -> bool:
    if expected == current:
        return not (require_already_absent and bool(current.get("present")))
    if not expected.get("managed") or not current.get("managed"):
        return False
    if current.get("present") or current.get("registered"):
        return False
    expected_git_dir = expected.get("git_dir")
    if isinstance(expected_git_dir, str):
        git_dir_path = Path(expected_git_dir)
        if git_dir_path.exists() or git_dir_path.is_symlink():
            return False
    if not expected.get("present") and expected.get("registered"):
        return False
    immutable_keys = {
        "version",
        "terminal_id",
        "session_id",
        "managed",
        "kind",
        "identity",
        "source",
        "source_identity",
        "git_common_dir",
        "git_common_dir_identity",
        "path",
        "writer_authority_generation",
        "writable_work_context_id",
        "owned_ref",
    }
    if any(expected.get(key) != current.get(key) for key in immutable_keys):
        return False
    expected_ref = expected.get("owned_ref_object_id")
    current_ref = current.get("owned_ref_object_id")
    return current_ref == expected_ref or (expected_ref is not None and current_ref is None)


def purge_session_managed_worktrees(
    terminals: Sequence[Mapping[str, Any]],
    retirement_authority: Mapping[str, Any],
    *,
    allow_dirty: bool = False,
    require_already_absent: bool = False,
) -> dict[str, Any]:
    """Revalidate then retire a bounded Session worktree set and owned refs."""
    expected_rows = retirement_authority.get("worktrees")
    if retirement_authority.get("version") != 1 or not isinstance(expected_rows, list):
        return {"removed": False, "reason_code": "WORKSPACE_AUTHORITY_CHANGED"}
    expected = {
        str(item.get("terminal_id")): item
        for item in expected_rows
        if isinstance(item, Mapping) and isinstance(item.get("terminal_id"), str)
    }
    expected_session_ids = {
        str(item.get("session_id"))
        for item in expected_rows
        if isinstance(item, Mapping) and isinstance(item.get("session_id"), str)
    }
    if len(expected_session_ids) > 1:
        return {"removed": False, "reason_code": "WORKSPACE_AUTHORITY_CHANGED"}
    expected_session_id = next(iter(expected_session_ids), None)
    terminal_metadata = {
        str(item.get("id") or item.get("terminal_id")): item
        for item in terminals
        if isinstance(item.get("id") or item.get("terminal_id"), str)
    }
    terminal_ids = set(terminal_metadata)
    if (
        len(expected) != len(expected_rows)
        or len(terminal_metadata) != len(terminals)
        or set(expected) != terminal_ids
    ):
        return {"removed": False, "reason_code": "WORKSPACE_AUTHORITY_CHANGED"}

    captured = capture_session_worktree_retirement_authority(
        terminals,
        session_id=expected_session_id,
    )
    if not captured.get("safe"):
        return {
            "removed": False,
            "reason_code": str(
                captured.get("reason_code") or "WRITABLE_WORKTREE_AUTHORITY_CHANGED"
            ),
        }
    current_rows = captured["authority"]["worktrees"]
    current = {str(item["terminal_id"]): item for item in current_rows}
    for terminal_id in sorted(expected):
        if not _retirement_authority_matches(
            expected[terminal_id],
            current[terminal_id],
            require_already_absent=require_already_absent,
        ):
            return {
                "removed": False,
                "terminal_id": terminal_id,
                "reason_code": "WRITABLE_WORKTREE_AUTHORITY_CHANGED",
            }
        if (
            expected[terminal_id].get("managed")
            and not bool(expected[terminal_id].get("clean"))
            and not allow_dirty
        ):
            return {
                "removed": False,
                "terminal_id": terminal_id,
                "reason_code": "MANAGED_WORKTREE_DIRTY",
            }

    for terminal_id in sorted(current):
        row = current[terminal_id]
        if not row.get("managed") or not row.get("present"):
            continue
        # Re-read all mutable per-worktree axes immediately before this exact
        # removal.  The repository inventory captured above remains a stable
        # registration fence, while status/HEAD/ref/content and inode probes
        # are live.  Earlier rows may already be absent during this loop.
        try:
            inventory = captured["_inventories"][str(row["source"])]
            status = managed_worktree_status(
                terminal_metadata[terminal_id],
                inventory=inventory,
            )
            if status.get("managed") and not status.get("safe"):
                raise ManagedWorktreeError(
                    str(status.get("reason_code") or "MANAGED_WORKTREE_UNVERIFIED")
                )
            immediate = _retirement_authority_document(
                terminal_metadata[terminal_id],
                status,
                session_id=expected_session_id,
            )
        except (KeyError, ManagedWorktreeError, OSError, subprocess.SubprocessError) as exc:
            return {
                "removed": False,
                "terminal_id": terminal_id,
                "reason_code": str(exc) or "WRITABLE_WORKTREE_AUTHORITY_CHANGED",
            }
        if immediate != expected[terminal_id]:
            return {
                "removed": False,
                "terminal_id": terminal_id,
                "reason_code": "WRITABLE_WORKTREE_AUTHORITY_CHANGED",
            }
        arguments = ["worktree", "remove"]
        if allow_dirty:
            arguments.append("--force")
        arguments.append(str(row["path"]))
        removed = _git(*arguments, cwd=Path(str(row["source"])), check=False)
        if removed.returncode != 0:
            return {
                "removed": False,
                "terminal_id": terminal_id,
                "reason_code": "MANAGED_WORKTREE_REMOVE_FAILED",
                "detail": (removed.stderr or removed.stdout).strip(),
            }

    sources = {
        str(row["source"])
        for row in current.values()
        if row.get("managed") and isinstance(row.get("source"), str)
    }
    try:
        final_inventories = {
            source: _load_worktree_inventory(Path(source)) for source in sorted(sources)
        }
    except (ManagedWorktreeError, OSError, subprocess.SubprocessError) as exc:
        return {
            "removed": False,
            "reason_code": str(exc) or "WRITABLE_WORKTREE_AUTHORITY_CHANGED",
        }
    for terminal_id in sorted(current):
        row = current[terminal_id]
        if not row.get("managed"):
            continue
        path = Path(str(row["path"]))
        final_inventory = final_inventories[str(row["source"])]
        if not _repository_authority_matches(row, final_inventory):
            return {
                "removed": False,
                "terminal_id": terminal_id,
                "reason_code": "MANAGED_WORKTREE_IDENTITY_MISMATCH",
            }
        registrations = final_inventory["registrations"]
        if path.exists() or path.is_symlink() or str(path) in registrations:
            return {
                "removed": False,
                "terminal_id": terminal_id,
                "reason_code": "MANAGED_WORKTREE_REMOVE_FAILED",
            }

    for terminal_id in sorted(expected):
        row = expected[terminal_id]
        ref_name = row.get("owned_ref")
        if not isinstance(ref_name, str):
            continue
        source = Path(str(row["source"]))
        expected_object = row.get("owned_ref_object_id")
        try:
            final_inventory = final_inventories[str(row["source"])]
            live_repository = _repository_authority(source)
        except (KeyError, ManagedWorktreeError, OSError, subprocess.SubprocessError) as exc:
            return {
                "removed": False,
                "terminal_id": terminal_id,
                "reason_code": str(exc) or "MANAGED_WORKTREE_IDENTITY_MISMATCH",
            }
        if not _repository_authority_matches(row, live_repository) or not (
            _repository_authority_matches(final_inventory, live_repository)
        ):
            return {
                "removed": False,
                "terminal_id": terminal_id,
                "reason_code": "MANAGED_WORKTREE_IDENTITY_MISMATCH",
            }
        if any(
            registration.get("head_ref") == ref_name
            for registration in final_inventory["registrations"].values()
        ):
            return {
                "removed": False,
                "terminal_id": terminal_id,
                "reason_code": "MANAGED_WORKTREE_BRANCH_CHANGED",
            }
        current_object = _owned_ref_object(source, ref_name)
        if current_object is None:
            if expected_object is None or not Path(str(row["path"])).exists():
                continue
            return {
                "removed": False,
                "terminal_id": terminal_id,
                "reason_code": "MANAGED_WORKTREE_BRANCH_CHANGED",
            }
        if current_object != expected_object:
            return {
                "removed": False,
                "terminal_id": terminal_id,
                "reason_code": "MANAGED_WORKTREE_BRANCH_CHANGED",
            }
        try:
            before_delete_repository = _repository_authority(source)
        except (ManagedWorktreeError, OSError, subprocess.SubprocessError) as exc:
            return {
                "removed": False,
                "terminal_id": terminal_id,
                "reason_code": str(exc) or "MANAGED_WORKTREE_IDENTITY_MISMATCH",
            }
        if not _repository_authority_matches(row, before_delete_repository):
            return {
                "removed": False,
                "terminal_id": terminal_id,
                "reason_code": "MANAGED_WORKTREE_IDENTITY_MISMATCH",
            }
        deleted = _git("update-ref", "-d", ref_name, current_object, cwd=source, check=False)
        try:
            after_delete_repository = _repository_authority(source)
        except (ManagedWorktreeError, OSError, subprocess.SubprocessError):
            after_delete_repository = {}
        if (
            deleted.returncode != 0
            or not _repository_authority_matches(row, after_delete_repository)
            or _owned_ref_object(source, ref_name) is not None
        ):
            return {
                "removed": False,
                "terminal_id": terminal_id,
                "reason_code": "MANAGED_WORKTREE_BRANCH_CHANGED",
            }

    return {
        "removed": True,
        "evidence": [
            {
                "terminal_id": terminal_id,
                "managed": bool(expected[terminal_id].get("managed")),
                "path_absent": True,
                "git_unregistered": True,
                "branch_absent": True,
                "runtime_artifacts_absent": True,
            }
            for terminal_id in sorted(expected)
        ],
    }


def remove_managed_worktree(
    metadata: Mapping[str, Any], *, allow_dirty: bool = False
) -> dict[str, Any]:
    """Remove an authorized managed worktree without deleting its task branch.

    Dirty worktrees require the caller's explicit destructive authority;
    unverifiable worktrees are always retained fail-closed. A task branch is
    intentionally preserved so committed but not-yet-integrated work remains
    recoverable after terminal retirement.
    """
    status = managed_worktree_status(metadata)
    if not status.get("managed"):
        return {"removed": False, "managed": False}
    if not status.get("safe"):
        return {"removed": False, **status}
    if not status.get("clean") and not allow_dirty:
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
    arguments = ["worktree", "remove"]
    if allow_dirty:
        arguments.append("--force")
    arguments.append(str(status["path"]))
    completed = _git(*arguments, cwd=source, check=False)
    if completed.returncode != 0:
        return {
            "removed": False,
            **status,
            "reason_code": "MANAGED_WORKTREE_REMOVE_FAILED",
            "detail": (completed.stderr or completed.stdout).strip(),
        }
    return {"removed": True, **status}


def purge_managed_worktree(
    metadata: Mapping[str, Any],
    *,
    allow_dirty: bool = False,
    require_already_absent: bool = False,
) -> dict[str, Any]:
    """Permanently remove one exact Session-owned worktree and private branch.

    A durable ``retired`` Session context is accepted only when both the path
    and Git worktree registration are already absent.  A leftover private
    Session branch is then removed with an exact old-object CAS.  This keeps a
    contradictory live worktree fail-closed while allowing crash/retry to
    converge after either filesystem or ref cleanup completed first.
    """
    kind = metadata.get("managed_worktree_kind")
    if kind not in _MANAGED_KINDS:
        return {
            "removed": False,
            "managed": False,
            "path_absent": True,
            "git_unregistered": True,
            "branch_absent": True,
        }
    identity = (
        metadata.get("managed_worktree_identity")
        or metadata.get("writable_work_context_id")
        or metadata.get("managed_worktree_origin_terminal_id")
        or metadata.get("id")
    )
    path_value = metadata.get("launch_worktree")
    source_value = metadata.get("managed_worktree_source")
    if not all(isinstance(value, str) and value for value in (identity, path_value, source_value)):
        return {
            "removed": False,
            "managed": True,
            "reason_code": "MANAGED_WORKTREE_METADATA_INVALID",
        }
    path = Path(str(path_value))
    source = Path(str(source_value))
    if not source.exists():
        return {"removed": False, "managed": True, "reason_code": "MANAGED_WORKTREE_MISSING"}
    resolved_source = source.resolve(strict=True)
    resolved_path = path.resolve(strict=False)
    if (
        _repository_root(source) != resolved_source
        or resolved_path
        != _managed_path(resolved_source, str(identity), str(kind)).resolve(strict=False)
        or metadata.get("managed_worktree_branch") != _branch_for(str(kind), str(identity))
    ):
        return {
            "removed": False,
            "managed": True,
            "reason_code": "MANAGED_WORKTREE_IDENTITY_MISMATCH",
        }

    registered = _git("worktree", "list", "--porcelain", cwd=source)
    registered_paths = {
        Path(line.removeprefix("worktree ")).resolve(strict=False)
        for line in registered.stdout.splitlines()
        if line.startswith("worktree ")
    }
    path_present = path.exists() or path.is_symlink()
    registered_present = resolved_path in registered_paths
    if require_already_absent and (path_present or registered_present):
        return {
            "removed": False,
            "managed": True,
            "reason_code": "WORKSPACE_RETIREMENT_STATE_CONFLICT",
        }
    if not require_already_absent and (path_present or registered_present):
        removed = remove_managed_worktree(metadata, allow_dirty=allow_dirty)
        if not removed.get("removed"):
            return removed

    registered_after = _git("worktree", "list", "--porcelain", cwd=source)
    still_registered = any(
        line.startswith("worktree ")
        and Path(line.removeprefix("worktree ")).resolve(strict=False) == resolved_path
        for line in registered_after.stdout.splitlines()
    )
    if path.exists() or path.is_symlink() or still_registered:
        return {
            "removed": False,
            "managed": True,
            "reason_code": "MANAGED_WORKTREE_REMOVE_FAILED",
        }

    expected_branch = _branch_for(str(kind), str(identity))
    if expected_branch is not None:
        ref_name = f"refs/heads/{expected_branch}"
        branch = _git("rev-parse", "--verify", ref_name, cwd=source, check=False)
        if branch.returncode == 0:
            old_object = branch.stdout.strip()
            expected_object = metadata.get("managed_worktree_branch_object_id")
            if expected_object is not None and old_object != expected_object:
                return {
                    "removed": False,
                    "managed": True,
                    "reason_code": "MANAGED_WORKTREE_BRANCH_CHANGED",
                }
            removed_ref = _git("update-ref", "-d", ref_name, old_object, cwd=source, check=False)
            if removed_ref.returncode != 0:
                return {
                    "removed": False,
                    "managed": True,
                    "reason_code": "MANAGED_WORKTREE_BRANCH_CHANGED",
                }
        branch_after = _git("show-ref", "--verify", ref_name, cwd=source, check=False)
        if branch_after.returncode == 0:
            return {
                "removed": False,
                "managed": True,
                "reason_code": "MANAGED_WORKTREE_BRANCH_REMOVE_FAILED",
            }

    return {
        "removed": True,
        "managed": True,
        "already_removed": not path_present and not registered_present,
        "path_absent": True,
        "git_unregistered": True,
        "branch_absent": True,
    }


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
