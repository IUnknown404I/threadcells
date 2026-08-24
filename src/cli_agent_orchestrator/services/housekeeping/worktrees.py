"""First-class, fail-closed Git worktree retirement for Housekeeping."""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .models import HousekeepingCandidate, resource_fingerprint
from .protected_set import ProtectedSet


@dataclass(frozen=True)
class WorktreeAuthority:
    """Durable lifecycle references which make a worktree non-retirable."""

    active_terminal_paths: frozenset[Path]
    managed_source_paths: frozenset[Path]
    workflow_paths: frozenset[Path]
    writer_lease_paths: frozenset[Path]
    project_paths: frozenset[Path]
    repository_seeds: frozenset[Path]
    certain: bool
    warnings: tuple[str, ...]

    def reason(self, path: Path) -> str | None:
        if not self.certain:
            return "WORKTREE_AUTHORITY_INVENTORY_UNKNOWN"
        inventories = (
            (self.active_terminal_paths, "ACTIVE_TERMINAL_WORKTREE"),
            (self.managed_source_paths, "ACTIVE_MANAGED_WORKTREE_SOURCE"),
            (self.workflow_paths, "ACTIVE_OR_RECOVERY_WORKFLOW"),
            (self.writer_lease_paths, "WRITER_LEASE_WORKTREE"),
            (self.project_paths, "PROJECT_SOURCE_AUTHORITY"),
        )
        for paths, reason in inventories:
            if any(_overlaps(path, protected) for protected in paths):
                return reason
        return None


def _overlaps(left: Path, right: Path) -> bool:
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False


def _canonical_absolute(value: Any) -> Path | None:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        return None
    return Path(value).resolve(strict=False)


def _registered_absolute(value: Any) -> Path | None:
    """Keep a registered worktree's lexical path so symlinks remain detectable."""
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        return None
    path = Path(value)
    normalized = Path(os.path.normpath(value))
    return path if normalized == path else None


def authority_inventory() -> WorktreeAuthority:
    """Read terminal, workflow, writer-lease, and project authorities atomically enough.

    Any malformed or unavailable path-bearing catalog makes every candidate
    protected. A workflow whose root terminal record no longer exists is
    reported, but cannot claim an arbitrary filesystem path: workflow
    ownership is derived from the root terminal's canonical worktree.

    The execute path repeats this inventory while holding the terminal-launch
    lifecycle lock.
    """
    try:
        from cli_agent_orchestrator.clients.database import (
            get_protected_workflow_root_terminal_ids,
            list_all_terminals,
            list_projects,
            list_worktree_writer_leases,
        )

        terminals = list_all_terminals()
        leases = list_worktree_writer_leases()
        projects = list_projects()
        workflow_terminal_ids = set(get_protected_workflow_root_terminal_ids())
    except Exception:
        return WorktreeAuthority(
            active_terminal_paths=frozenset(),
            managed_source_paths=frozenset(),
            workflow_paths=frozenset(),
            writer_lease_paths=frozenset(),
            project_paths=frozenset(),
            repository_seeds=frozenset(),
            certain=False,
            warnings=("worktree_authority_inventory_uncertain",),
        )

    terminals_by_id: dict[str, Path] = {}
    managed_sources_by_id: dict[str, Path] = {}
    active: set[Path] = set()
    managed_sources: set[Path] = set()
    seeds: set[Path] = set()
    certain = True
    for terminal in terminals:
        terminal_id = terminal.get("id")
        path = _canonical_absolute(terminal.get("launch_worktree"))
        managed_source = _canonical_absolute(terminal.get("managed_worktree_source"))
        managed_kind = terminal.get("managed_worktree_kind")
        if isinstance(terminal_id, str) and terminal_id and path is not None:
            terminals_by_id[terminal_id] = path
            seeds.add(path)
            if terminal.get("runtime_lifecycle") != "exited":
                active.add(path)
        elif terminal.get("runtime_lifecycle") != "exited":
            certain = False
        if isinstance(terminal_id, str) and terminal_id and managed_source is not None:
            managed_sources_by_id[terminal_id] = managed_source
            seeds.add(managed_source)
            if terminal.get("runtime_lifecycle") != "exited":
                managed_sources.add(managed_source)
        elif managed_kind is not None and (
            terminal.get("runtime_lifecycle") != "exited" or terminal_id in workflow_terminal_ids
        ):
            certain = False

    workflows: set[Path] = set()
    orphan_workflows = 0
    for terminal_id in workflow_terminal_ids:
        path = terminals_by_id.get(str(terminal_id))
        if path is None:
            orphan_workflows += 1
        else:
            workflows.add(path)
            managed_source = managed_sources_by_id.get(str(terminal_id))
            if managed_source is not None:
                workflows.add(managed_source)

    lease_paths: set[Path] = set()
    for lease in leases:
        path = _canonical_absolute(lease.get("canonical_worktree"))
        if path is None:
            certain = False
        else:
            lease_paths.add(path)
            seeds.add(path)

    project_paths: set[Path] = set()
    for project in projects:
        path = _canonical_absolute(getattr(project, "path", None))
        if path is None:
            certain = False
        else:
            project_paths.add(path)
            seeds.add(path)

    warnings: list[str] = []
    if orphan_workflows:
        # A protected workflow whose root terminal/path cannot be resolved is
        # ambiguous lifecycle authority.  It cannot safely name one worktree,
        # so fail closed for the entire retirement inventory until the durable
        # relationship is reconciled.
        certain = False
    if not certain:
        warnings.append("worktree_authority_inventory_uncertain")
    if orphan_workflows:
        warnings.append(f"worktree_orphan_workflow_authority:{orphan_workflows}")
    return WorktreeAuthority(
        active_terminal_paths=frozenset(active),
        managed_source_paths=frozenset(managed_sources),
        workflow_paths=frozenset(workflows),
        writer_lease_paths=frozenset(lease_paths),
        project_paths=frozenset(project_paths),
        repository_seeds=frozenset(seeds),
        certain=certain,
        warnings=tuple(warnings),
    )


def _run_git(
    runner: Callable[..., Any],
    arguments: Sequence[str],
    *,
    timeout: float,
) -> Any:
    return runner(
        ["git", *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _tree_size(path: Path, *, timeout: float) -> tuple[int, bool]:
    """Measure one bounded worktree without following links."""
    try:
        completed = subprocess.run(
            ["du", "-sb", "--apparent-size", "--", str(path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0, False
    first_line = completed.stdout.splitlines()[0] if completed.stdout else ""
    raw_size, separator, _name = first_line.partition("\t")
    try:
        size = int(raw_size) if separator else 0
    except ValueError:
        size = 0
    return size, completed.returncode == 0 and bool(separator)


def _repository_seeds(
    config: Mapping[str, Any], authority: WorktreeAuthority
) -> tuple[set[Path], list[str]]:
    seeds = set(authority.repository_seeds)
    warnings: list[str] = []
    for value in config.get("worktree_repository_paths", []):
        raw = Path(value) if isinstance(value, str) else None
        path = _canonical_absolute(value)
        if raw is None or raw.is_symlink() or path is None:
            warnings.append("worktree_repository_path_invalid")
        else:
            seeds.add(path)
    for value in config.get("worktree_repository_collections", []):
        raw = Path(value) if isinstance(value, str) else None
        root = _canonical_absolute(value)
        if raw is None or raw.is_symlink() or root is None or not root.is_dir():
            warnings.append("worktree_repository_collection_invalid")
            continue
        try:
            children = sorted(root.iterdir())
        except OSError:
            warnings.append("worktree_repository_collection_unreadable")
            continue
        for child in children:
            try:
                if child.is_symlink() or not child.is_dir() or not (child / ".git").exists():
                    continue
                seeds.add(child.resolve(strict=True))
            except OSError:
                warnings.append("worktree_repository_seed_unreadable")
    return seeds, warnings


def _common_dirs(
    seeds: set[Path], runner: Callable[..., Any], *, timeout: float
) -> tuple[set[Path], list[str]]:
    common_dirs: set[Path] = set()
    warnings: list[str] = []

    def resolve(seed: Path) -> tuple[Path | None, str | None]:
        if not seed.is_dir() or seed.is_symlink():
            return None, None
        try:
            completed = _run_git(
                runner,
                [
                    "-C",
                    str(seed),
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-common-dir",
                ],
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None, "git_common_dir_inventory_failed"
        if completed.returncode:
            return None, None
        value = completed.stdout.strip()
        raw = Path(value) if value else None
        path = _canonical_absolute(value)
        if raw is None or raw.is_symlink() or path is None or not path.is_dir():
            return None, "git_common_dir_invalid"
        return path, None

    workers = max(1, min(8, len(seeds)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for path, warning in pool.map(resolve, sorted(seeds)):
            if path is not None:
                common_dirs.add(path)
            if warning is not None:
                warnings.append(warning)
    return common_dirs, warnings


def _registered_worktrees(output: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in [*output.splitlines(), ""]:
        if not line:
            if current:
                rows.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    return rows


def _inside_allowed_root(path: Path, roots: tuple[Path, ...]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
        except ValueError:
            continue
        return path != root
    return False


def _durable_ref(
    *,
    path: Path,
    head: str,
    refs: tuple[str, ...],
    runner: Callable[..., Any],
    timeout: float,
) -> tuple[str | None, dict[str, str]]:
    observed: dict[str, str] = {}
    for reference in refs:
        resolved = _run_git(
            runner,
            ["-C", str(path), "rev-parse", "--verify", f"{reference}^{{commit}}"],
            timeout=timeout,
        )
        if resolved.returncode or not resolved.stdout.strip():
            continue
        target = resolved.stdout.strip()
        observed[reference] = target
        contained = _run_git(
            runner,
            ["-C", str(path), "merge-base", "--is-ancestor", head, target],
            timeout=timeout,
        )
        if contained.returncode == 0:
            return reference, observed
    return None, observed


def _classify_registered_worktree(
    *,
    common_dir: Path,
    row: Mapping[str, str],
    roots: tuple[Path, ...],
    refs: tuple[str, ...],
    authority: WorktreeAuthority,
    protection: ProtectedSet,
    runner: Callable[..., Any],
    timeout: float,
) -> HousekeepingCandidate | None:
    raw_path = row.get("worktree")
    path = _registered_absolute(raw_path)
    if path is None or not _inside_allowed_root(path, roots):
        return None

    head = row.get("HEAD", "")
    branch = row.get("branch", "detached")
    reason: str | None = None
    retention_reason = "clean_inactive_head_durable"
    status_text = ""
    durable_reference: str | None = None
    ref_targets: dict[str, str] = {}
    size = 0
    size_certain = False
    try:
        if not authority.certain:
            reason = "WORKTREE_AUTHORITY_INVENTORY_UNKNOWN"
        elif "locked" in row:
            reason = "WORKTREE_GIT_LOCKED"
        elif "prunable" in row or "bare" in row:
            reason = "WORKTREE_GIT_IDENTITY_UNKNOWN"
        elif not path.exists():
            reason = "WORKTREE_PATH_MISSING"
        elif path.is_symlink() or path.resolve(strict=False) != path or not path.is_dir():
            reason = "WORKTREE_PATH_INVALID"
        elif (path / ".git").is_symlink() or not (path / ".git").is_file():
            reason = "GIT_COMMON_DIR_AUTHORITY"
        if reason is None:
            reason = authority.reason(path) or protection.reason(path, "worktrees")

        if path.exists() and path.is_dir() and not path.is_symlink():
            size, size_certain = _tree_size(path, timeout=timeout)
        if not size_certain and reason is None:
            reason = "WORKTREE_SIZE_INVENTORY_UNKNOWN"
        if reason is None:
            top = _run_git(
                runner,
                ["-C", str(path), "rev-parse", "--show-toplevel"],
                timeout=timeout,
            )
            current_head = _run_git(
                runner,
                ["-C", str(path), "rev-parse", "--verify", "HEAD^{commit}"],
                timeout=timeout,
            )
            status = _run_git(
                runner,
                ["-C", str(path), "status", "--porcelain", "--untracked-files=normal"],
                timeout=timeout,
            )
            if (
                top.returncode
                or current_head.returncode
                or status.returncode
                or Path(top.stdout.strip()).resolve(strict=False) != path
                or current_head.stdout.strip() != head
            ):
                reason = "WORKTREE_GIT_IDENTITY_UNKNOWN"
            else:
                status_text = status.stdout
                if status_text:
                    reason = "WORKTREE_DIRTY"
        if reason is None:
            durable_reference, ref_targets = _durable_ref(
                path=path,
                head=head,
                refs=refs,
                runner=runner,
                timeout=timeout,
            )
            if durable_reference is None:
                reason = "WORKTREE_HEAD_NOT_DURABLE"
    except (OSError, subprocess.TimeoutExpired):
        reason = "WORKTREE_INVENTORY_UNKNOWN"

    payload = {
        "path": str(path),
        "common_dir": str(common_dir),
        "head": head,
        "branch": branch,
        "status": status_text,
        "durable_ref": durable_reference or "",
        "durable_ref_targets": ref_targets,
        "size": size,
    }
    action = "preserve" if reason else "retire"
    return HousekeepingCandidate(
        category="worktrees",
        path=str(path),
        canonical_identity=f"worktrees:{path}",
        fingerprint=resource_fingerprint(payload),
        bytes=size,
        estimated_reclaim_bytes=size if action == "retire" else 0,
        action=action,  # type: ignore[arg-type]
        retention_reason=retention_reason,
        protection_reason=reason,
        resource_kind="git_worktree",
        attributes=tuple(
            sorted(
                {
                    "common_dir": str(common_dir),
                    "head": head,
                    "branch": branch,
                    "durable_ref": durable_reference or "",
                    "durable_ref_target": ref_targets.get(durable_reference or "", ""),
                }.items()
            )
        ),
    )


def plan_worktrees(
    *,
    config: Mapping[str, Any],
    protection: ProtectedSet,
    runner: Callable[..., Any] = subprocess.run,
) -> tuple[list[HousekeepingCandidate], list[str]]:
    """Inventory registered linked worktrees and classify retirement safety."""
    timeout = float(config.get("subprocess_timeout_seconds", 20))
    warnings: list[str] = []
    roots: list[Path] = []
    configured_roots = config.get("worktree_roots", [])
    if not configured_roots:
        return [], []
    for value in configured_roots:
        raw = Path(value) if isinstance(value, str) else None
        root = _canonical_absolute(value)
        if raw is None or raw.is_symlink() or root is None or not root.is_dir():
            warnings.append("worktree_root_invalid")
        else:
            roots.append(root)
    if not roots:
        return [], [*warnings, "worktree_roots_unavailable"]
    authority = authority_inventory()
    warnings.extend(authority.warnings)
    refs_raw = config.get("worktree_durable_refs", [])
    refs = tuple(
        str(value) for value in refs_raw if isinstance(value, str) and value.startswith("refs/")
    )
    if len(refs) != len(refs_raw):
        warnings.append("worktree_durable_ref_invalid")
        refs = ()

    seeds, seed_warnings = _repository_seeds(config, authority)
    common_dirs, common_warnings = _common_dirs(seeds, runner, timeout=timeout)
    warnings.extend(seed_warnings)
    warnings.extend(common_warnings)
    registered: list[tuple[Path, dict[str, str]]] = []
    seen: set[Path] = set()
    for common_dir in sorted(common_dirs):
        try:
            listed = _run_git(
                runner,
                ["--git-dir", str(common_dir), "worktree", "list", "--porcelain"],
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired):
            warnings.append("git_worktree_inventory_failed")
            continue
        if listed.returncode:
            warnings.append("git_worktree_inventory_failed")
            continue
        for row in _registered_worktrees(listed.stdout):
            raw_path = row.get("worktree")
            path = _registered_absolute(raw_path)
            if path is None or path in seen or not _inside_allowed_root(path, tuple(roots)):
                continue
            seen.add(path)
            registered.append((common_dir, row))

    workers_raw = config.get("worktree_inventory_workers", 8)
    if isinstance(workers_raw, bool) or not isinstance(workers_raw, int):
        warnings.append("worktree_inventory_workers_invalid")
        workers_raw = 8
    workers = max(1, min(16, workers_raw, len(registered) or 1))

    def classify(item: tuple[Path, dict[str, str]]) -> HousekeepingCandidate | None:
        common_dir, row = item
        return _classify_registered_worktree(
            common_dir=common_dir,
            row=row,
            roots=tuple(roots),
            refs=refs,
            authority=authority,
            protection=protection,
            runner=runner,
            timeout=timeout,
        )

    candidates: list[HousekeepingCandidate] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for candidate in pool.map(classify, registered):
            if candidate is not None:
                candidates.append(candidate)
    return sorted(candidates, key=lambda item: item.canonical_identity), warnings


def revalidate_worktree_candidate(
    candidate: HousekeepingCandidate,
    *,
    config: Mapping[str, Any],
    protection: ProtectedSet,
    runner: Callable[..., Any] = subprocess.run,
) -> HousekeepingCandidate | None:
    """Reclassify exactly one planned worktree under the lifecycle lock."""
    if candidate.resource_kind != "git_worktree":
        return None
    timeout = float(config.get("subprocess_timeout_seconds", 20))
    path = _registered_absolute(candidate.path)
    common_dir = _canonical_absolute(dict(candidate.attributes).get("common_dir"))
    if path is None or common_dir is None:
        return None
    roots_list: list[Path] = []
    for value in config.get("worktree_roots", []):
        raw = Path(value) if isinstance(value, str) else None
        root = _canonical_absolute(value)
        if raw is None or raw.is_symlink() or root is None or not root.is_dir():
            return None
        roots_list.append(root)
    roots = tuple(roots_list)
    refs_raw = config.get("worktree_durable_refs", [])
    refs = tuple(
        str(value) for value in refs_raw if isinstance(value, str) and value.startswith("refs/")
    )
    if not roots or len(refs) != len(refs_raw):
        return None
    try:
        listed = _run_git(
            runner,
            ["--git-dir", str(common_dir), "worktree", "list", "--porcelain"],
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if listed.returncode:
        return None
    row = next(
        (
            item
            for item in _registered_worktrees(listed.stdout)
            if _registered_absolute(item.get("worktree")) == path
        ),
        None,
    )
    if row is None:
        return None
    return _classify_registered_worktree(
        common_dir=common_dir,
        row=row,
        roots=roots,
        refs=refs,
        authority=authority_inventory(),
        protection=protection,
        runner=runner,
        timeout=timeout,
    )


def remove_worktree(
    candidate: HousekeepingCandidate,
    *,
    runner: Callable[..., Any] = subprocess.run,
    timeout: float = 20,
) -> int:
    """Retire exactly one revalidated linked worktree using Git semantics."""
    attributes = dict(candidate.attributes)
    path = Path(candidate.path)
    common_dir = Path(attributes["common_dir"])
    head = attributes.get("head", "")
    durable_ref = attributes.get("durable_ref", "")
    durable_ref_target = attributes.get("durable_ref_target", "")
    if (
        not path.is_absolute()
        or not common_dir.is_absolute()
        or path.is_symlink()
        or not path.is_dir()
        or (path / ".git").is_symlink()
        or not (path / ".git").is_file()
        or common_dir.is_symlink()
        or not common_dir.is_dir()
        or not durable_ref.startswith("refs/")
        or len(head) not in {40, 64}
        or len(durable_ref_target) != len(head)
        or any(character not in "0123456789abcdef" for character in head)
        or any(character not in "0123456789abcdef" for character in durable_ref_target)
    ):
        raise RuntimeError("worktree identity changed")
    pin_digest = hashlib.sha256(
        f"{candidate.canonical_identity}\0{head}\0{os.getpid()}\0{time.time_ns()}".encode()
    ).hexdigest()
    pin_ref = f"refs/threadcells/housekeeping-pins/{pin_digest}"
    transaction = "\n".join(
        (
            "start",
            f"verify {durable_ref} {durable_ref_target}",
            f"create {pin_ref} {head}",
            "prepare",
            "commit",
            "",
        )
    )
    pinned = runner(
        ["git", "--git-dir", str(common_dir), "update-ref", "--stdin"],
        input=transaction,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    if pinned.returncode:
        raise RuntimeError("worktree durable reference changed")
    removed = False
    try:
        completed = _run_git(
            runner,
            ["--git-dir", str(common_dir), "worktree", "remove", "--", str(path)],
            timeout=timeout,
        )
        if completed.returncode:
            raise RuntimeError("git worktree remove failed")
        removed = True
        pruned = _run_git(
            runner,
            ["--git-dir", str(common_dir), "worktree", "prune"],
            timeout=timeout,
        )
        if pruned.returncode:
            raise RuntimeError("git worktree prune failed")
        return candidate.bytes
    finally:
        durable = _run_git(
            runner,
            ["--git-dir", str(common_dir), "merge-base", "--is-ancestor", head, durable_ref],
            timeout=timeout,
        )
        # If removal raced with loss of the sole proven durable ref, retain the
        # pin as recovery authority. Otherwise delete it conditionally. A
        # failed removal keeps the worktree itself and never needs the pin.
        if not removed or durable.returncode == 0:
            _run_git(
                runner,
                ["--git-dir", str(common_dir), "update-ref", "-d", pin_ref, head],
                timeout=timeout,
            )
