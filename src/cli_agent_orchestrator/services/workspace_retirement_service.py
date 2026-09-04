"""Conservative, restart-safe retirement of managed Session workspaces."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from cli_agent_orchestrator.clients.database import (
    claim_session_workspace_retirement,
    get_session_workspace_retirement_snapshot,
    list_session_workspace_retirement_snapshots,
    transition_writable_work_context,
)
from cli_agent_orchestrator.services.housekeeping.models import (
    HousekeepingCandidate,
    resource_fingerprint,
)
from cli_agent_orchestrator.services.managed_worktree_service import (
    managed_worktree_status,
    remove_managed_worktree,
)


def _tree_size(path: Path) -> int:
    """Measure reclaimable bytes with the host's bounded filesystem walker."""
    if not path.exists() or path.is_symlink():
        return 0
    import subprocess

    measured = subprocess.run(
        ["du", "-sb", "--one-file-system", str(path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if measured.returncode != 0:
        raise RuntimeError("WORKTREE_MEASUREMENT_FAILED")
    try:
        return int(measured.stdout.split(maxsplit=1)[0])
    except (IndexError, ValueError):
        raise RuntimeError("WORKTREE_MEASUREMENT_FAILED")


def _managed_terminals(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for raw in snapshot.get("terminals", []):
        terminal = dict(raw)
        path = terminal.get("launch_worktree")
        if not terminal.get("managed_worktree_kind") or not isinstance(path, str) or path in seen:
            continue
        seen.add(path)
        result.append(terminal)
    return result


def candidate_from_snapshot(
    snapshot: Mapping[str, Any], *, allow_dirty: bool = False
) -> HousekeepingCandidate:
    """Project one durable Session as a truthful Housekeeping candidate."""
    context = dict(snapshot["context"])
    durable_allow_dirty = bool(
        context.get("state") == "retiring" and context.get("retirement_allow_dirty")
    )
    if durable_allow_dirty:
        allow_dirty = True
    elif context.get("state") == "retiring" and allow_dirty:
        # A caller cannot add destructive authority after another retirement
        # generation has already been claimed without that authority.
        snapshot = {**snapshot, "reason_code": "WORKSPACE_AUTHORITY_CHANGED"}
    reason = snapshot.get("reason_code")
    statuses: list[dict[str, Any]] = []
    total_bytes = 0
    modified_files = 0
    untracked_files = 0
    managed_terminals = _managed_terminals(snapshot)
    if reason is None and not managed_terminals:
        reason = "MANAGED_WORKTREE_METADATA_INVALID"
    # Durable activity authority is sufficient to preserve an active Session.
    # Avoid touching Git or walking its worktree until the Session has passed
    # that cheap database-only boundary.
    if reason is None:
        for terminal in managed_terminals:
            try:
                status = managed_worktree_status(terminal)
            except Exception:
                status = {
                    "path": terminal.get("launch_worktree"),
                    "safe": False,
                    "reason_code": "WORKTREE_INSPECTION_FAILED",
                }
            statuses.append(status)
            if not status.get("safe"):
                reason = status.get("reason_code") or "WORKTREE_UNVERIFIED"
                break
            modified_files += int(status.get("modified_files") or 0)
            untracked_files += int(status.get("untracked_files") or 0)
            if not status.get("clean") and not allow_dirty:
                reason = "WORKTREE_DIRTY"
                break
            path = Path(str(status["path"]))
            try:
                total_bytes += _tree_size(path)
            except RuntimeError as exc:
                reason = str(exc)
                break

    fingerprint = resource_fingerprint(
        {
            "authority": snapshot["authority_fingerprint"],
            "allow_dirty": allow_dirty,
            "worktrees": [
                {
                    "path": status.get("path"),
                    "source": status.get("source"),
                    "kind": status.get("kind"),
                    "commit": status.get("commit"),
                    "branch": status.get("branch"),
                    "expected_commit": status.get("expected_commit"),
                    "expected_branch": status.get("expected_branch"),
                    "clean": status.get("clean"),
                    "absent": bool(status.get("absent")),
                    "modified_files": int(status.get("modified_files") or 0),
                    "untracked_files": int(status.get("untracked_files") or 0),
                    "content_fingerprint": status.get("content_fingerprint"),
                    "reason_code": status.get("reason_code"),
                }
                for status in statuses
            ],
        }
    )
    action = "retire" if reason is None else "preserve"
    return HousekeepingCandidate(
        category="session_workspaces",
        path=str(context["canonical_worktree"]),
        canonical_identity=f"session-workspace:{context['id']}",
        fingerprint=fingerprint,
        bytes=total_bytes,
        estimated_reclaim_bytes=total_bytes if action == "retire" else 0,
        action=action,
        retention_reason=(
            "explicit_inactive_dirty_workspace_retirement"
            if action == "retire" and allow_dirty and (modified_files or untracked_files)
            else "inactive_clean_session_workspace" if action == "retire" else str(reason)
        ),
        protection_reason=str(reason) if reason is not None else None,
        resource_kind="session_workspace",
        attributes=(
            ("allow_dirty", "true" if allow_dirty else "false"),
            ("context_id", str(context["id"])),
            ("session_id", str(context["session_id"])),
            ("modified_files", str(modified_files)),
            ("untracked_files", str(untracked_files)),
        ),
    )


def plan_session_workspaces(*, allow_dirty: bool = False) -> list[HousekeepingCandidate]:
    """Plan managed Session retirement without inferring authority from age."""
    return [
        candidate_from_snapshot(snapshot, allow_dirty=allow_dirty)
        for snapshot in list_session_workspace_retirement_snapshots()
    ]


def revalidate_session_workspace_candidate(
    candidate: HousekeepingCandidate,
) -> HousekeepingCandidate | None:
    attributes = dict(candidate.attributes)
    context_id = attributes.get("context_id")
    if not context_id:
        return None
    snapshot = get_session_workspace_retirement_snapshot(context_id)
    if snapshot is None:
        return None
    return candidate_from_snapshot(snapshot, allow_dirty=attributes.get("allow_dirty") == "true")


def retire_session_workspace(candidate: HousekeepingCandidate) -> int:
    """Claim, revalidate, and canonically remove one Session's worktrees."""
    attributes = dict(candidate.attributes)
    context_id = attributes.get("context_id")
    allow_dirty = attributes.get("allow_dirty") == "true"
    if not context_id or candidate.canonical_identity != f"session-workspace:{context_id}":
        raise RuntimeError("WORKSPACE_CANDIDATE_INVALID")
    current = revalidate_session_workspace_candidate(candidate)
    if current is None:
        raise RuntimeError("WORKSPACE_NOT_FOUND")
    if current.action != "retire" or current.protection_reason:
        raise RuntimeError(current.protection_reason or "WORKSPACE_NOT_RETIRABLE")
    if current.fingerprint != candidate.fingerprint:
        raise RuntimeError("WORKSPACE_AUTHORITY_CHANGED")
    snapshot = get_session_workspace_retirement_snapshot(context_id)
    assert snapshot is not None
    claim = claim_session_workspace_retirement(
        context_id,
        str(snapshot["authority_fingerprint"]),
        allow_dirty=allow_dirty,
    )
    if not claim.get("claimed"):
        raise RuntimeError(str(claim.get("reason_code") or "WORKSPACE_AUTHORITY_CHANGED"))

    # The durable claim closes new workflow/writer admission. Re-read Git and
    # database state immediately before the first irreversible removal.
    claimed = revalidate_session_workspace_candidate(candidate)
    if claimed is None or claimed.action != "retire" or claimed.protection_reason:
        raise RuntimeError(
            str(claimed.protection_reason if claimed is not None else "WORKSPACE_NOT_FOUND")
        )
    if claimed.fingerprint != candidate.fingerprint:
        raise RuntimeError("WORKSPACE_AUTHORITY_CHANGED")
    claimed_snapshot = get_session_workspace_retirement_snapshot(context_id)
    assert claimed_snapshot is not None
    if bool(claimed_snapshot["context"].get("retirement_allow_dirty")) != allow_dirty:
        raise RuntimeError("WORKSPACE_AUTHORITY_CHANGED")
    for terminal in _managed_terminals(claimed_snapshot):
        cleanup = remove_managed_worktree(terminal, allow_dirty=allow_dirty)
        if not cleanup.get("removed"):
            raise RuntimeError(str(cleanup.get("reason_code") or "WORKTREE_UNVERIFIED"))
    if not transition_writable_work_context(
        context_id,
        expected_states=("retiring",),
        state="retired",
        event_type="workspace_retired",
    ):
        raise RuntimeError("WORKSPACE_RETIREMENT_STATE_CHANGED")
    # Directory sizes must be captured before deletion. The immutable plan is
    # the conservative reclaimed-byte authority for a successful retirement.
    return int(candidate.bytes)


def reconcile_retiring_session_workspaces() -> int:
    """Finish crash-interrupted retirements; never expands dirty authority."""
    from cli_agent_orchestrator.services.operations_service import context_lifecycle_fence

    reconciled = 0
    with context_lifecycle_fence(nonblocking=True) as acquired:
        if not acquired:
            return 0
        for snapshot in list_session_workspace_retirement_snapshots():
            if snapshot["context"]["state"] != "retiring":
                continue
            candidate = candidate_from_snapshot(snapshot, allow_dirty=False)
            if candidate.action != "retire":
                continue
            retire_session_workspace(candidate)
            reconciled += 1
    return reconciled
