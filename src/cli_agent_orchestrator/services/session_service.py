"""Session service for session-level operations.

This module provides session management functionality for CAO, where a "session"
corresponds to a tmux session that may contain multiple terminal windows (agents).

Session Hierarchy:
- Session: A tmux session (e.g., "cao-my-project")
  - Terminal: A tmux window within the session (e.g., "developer-abc123")
    - Provider: The CLI agent running in the terminal (e.g., KiroCliProvider)

Key Operations:
- list_sessions(): Get all CAO-managed sessions (filtered by SESSION_PREFIX)
- get_session(): Get session details including all terminal metadata
- delete_session(): Clean up session, providers, database records, and tmux session

Session Lifecycle:
1. create_terminal() with new_session=True creates a new tmux session
2. Additional terminals are added via create_terminal() with new_session=False
3. delete_session() removes the entire session and all contained terminals
"""

import logging
from dataclasses import dataclass
from typing import Dict, List

from cli_agent_orchestrator.clients.database import (
    AmbiguousSessionIdentity,
    cancel_session_work_for_deletion,
    cancel_workflows_for_terminal,
    claim_session_workspace_retirement,
    delete_terminals_by_session_lifetime,
    get_session_unresolved_work_plan,
    get_session_workspace_retirement_snapshot,
    get_writable_work_context_by_session,
    resolve_session_lifetime,
)
from cli_agent_orchestrator.clients.tmux import tmux_client
from cli_agent_orchestrator.constants import SESSION_PREFIX
from cli_agent_orchestrator.models.terminal import Terminal
from cli_agent_orchestrator.plugins import (
    PluginRegistry,
    PostCreateSessionEvent,
    PostKillSessionEvent,
)
from cli_agent_orchestrator.providers.manager import provider_manager
from cli_agent_orchestrator.services.plugin_dispatch import dispatch_plugin_event
from cli_agent_orchestrator.services.terminal_service import (
    ManagedWorktreeCleanupError,
    cleanup_managed_worktree,
    create_terminal,
    prepare_terminal_for_destruction,
    prove_live_session_runtime_authority,
    retire_exited_terminal_runtime,
)

logger = logging.getLogger(__name__)


class SessionNotFoundError(ValueError):
    """No durable or live session authority matches the supplied identifier."""


class SessionLifecycleError(RuntimeError):
    """A session exists but the requested action is not lifecycle-safe."""

    def __init__(self, reason_code: str, message: str, *, inventory_uncertain: bool = False):
        super().__init__(message)
        self.reason_code = reason_code
        self.inventory_uncertain = inventory_uncertain


@dataclass(frozen=True)
class SessionAuthority:
    session_id: str
    session_name: str
    terminals: List[Dict]
    retained_resources: List[Dict]
    deleted: bool
    runtime_exists: bool | None

    @property
    def has_live_runtime_owner(self) -> bool:
        return any(
            terminal.get("runtime_lifecycle")
            not in {"exited", "recovery_fenced", "recovery_required"}
            for terminal in self.terminals
        )

    @property
    def has_recovery_fenced_history(self) -> bool:
        return any(
            terminal.get("runtime_lifecycle") == "recovery_fenced" for terminal in self.terminals
        )


def resolve_session_authority(identifier: str, *, require_live: bool = False) -> SessionAuthority:
    """Resolve one stable lifetime and its current tmux authority."""
    try:
        durable = resolve_session_lifetime(identifier)
    except AmbiguousSessionIdentity as exc:
        raise SessionLifecycleError(
            "SESSION_IDENTITY_AMBIGUOUS",
            "Session identity is ambiguous; retry with the stable session ID shown by ThreadCells",
        ) from exc
    if durable is None:
        raise SessionNotFoundError(f"Session '{identifier}' not found")
    runtime_exists = tmux_client.session_exists(str(durable["session_name"]))
    authority = SessionAuthority(
        session_id=str(durable["session_id"]),
        session_name=str(durable["session_name"]),
        terminals=list(durable["terminals"]),
        retained_resources=list(durable.get("retained_resources", [])),
        deleted=bool(durable["deleted"]),
        runtime_exists=runtime_exists,
    )
    if not require_live:
        return authority
    workspace = get_writable_work_context_by_session(authority.session_id)
    if workspace is not None and workspace.get("state") in {"retiring", "retired"}:
        raise SessionLifecycleError(
            "WORKSPACE_RETIRED",
            "This Session's writable workspace was retired; create a new Session to continue",
        )
    if authority.deleted or not authority.has_live_runtime_owner:
        raise SessionLifecycleError(
            "SESSION_HISTORY_INELIGIBLE",
            "This session is historical; Add Agent is available only for a live session",
        )
    proof = prove_live_session_runtime_authority(
        authority.session_name, authority.terminals, runtime_client=tmux_client
    )
    if not proof.proven:
        raise SessionLifecycleError(
            proof.reason_code or "SESSION_RUNTIME_AUTHORITY_DIVERGED",
            f"{proof.message}; retry after reconciliation",
            inventory_uncertain=proof.inventory_uncertain,
        )
    return authority


def create_session(
    provider: str,
    agent_profile: str,
    session_name: str | None = None,
    working_directory: str | None = None,
    allowed_tools: list[str] | None = None,
    registry: PluginRegistry | None = None,
    project_context: dict[str, str] | None = None,
    owner_grant_token: str | None = None,
    owner_grant_launch_id: str | None = None,
    work_context_request_id: str | None = None,
) -> Terminal:
    """Create a new session by creating its initial terminal."""

    terminal = create_terminal(
        provider=provider,
        agent_profile=agent_profile,
        session_name=session_name,
        new_session=True,
        working_directory=working_directory,
        allowed_tools=allowed_tools,
        registry=registry,
        project_context=project_context,
        owner_grant_token=owner_grant_token,
        owner_grant_launch_id=owner_grant_launch_id,
        work_context_request_id=work_context_request_id,
    )
    dispatch_plugin_event(
        registry,
        "post_create_session",
        PostCreateSessionEvent(
            session_id=terminal.session_name,
            session_name=terminal.session_name,
        ),
    )
    return terminal


def list_sessions() -> List[Dict]:
    """List all sessions from tmux, failing rather than inventing an empty inventory."""
    try:
        tmux_sessions = tmux_client.list_sessions()
    except Exception as e:
        logger.error(f"Failed to list sessions: {e}")
        raise RuntimeError("Could not inventory tmux sessions") from e
    if tmux_sessions is None:
        raise RuntimeError("Could not inventory tmux sessions")

    sessions = [s for s in tmux_sessions if (s.get("id") or "").startswith(SESSION_PREFIX)]

    def sort_key(session: Dict) -> tuple[float, str]:
        """Sort by tmux creation timestamp, with a stable ID tie-breaker."""
        created_at = session.get("created_at")
        try:
            return (float(str(created_at)), str(session.get("id") or ""))
        except (TypeError, ValueError):
            # Older/unavailable tmux metadata remains compatible while
            # still producing a deterministic order.
            return (float("-inf"), str(session.get("id") or ""))

    return sorted(sessions, key=sort_key, reverse=True)


def get_session(session_name: str) -> Dict:
    """Get session with terminals."""
    try:
        authority = resolve_session_authority(session_name, require_live=True)

        tmux_sessions = tmux_client.list_sessions()
        if tmux_sessions is None:
            raise RuntimeError("Could not inventory tmux sessions")
        session_data = next((s for s in tmux_sessions if s["id"] == authority.session_name), None)

        if not session_data:
            raise SessionLifecycleError(
                "SESSION_RUNTIME_INVENTORY_DIVERGED",
                "The live session changed during inventory; retry after reconciliation",
                inventory_uncertain=True,
            )

        return {"session": session_data, "terminals": authority.terminals}

    except Exception as e:
        logger.error(f"Failed to get session {session_name}: {e}")
        raise


def get_session_root_working_directory(session_name: str) -> str | None:
    """Return the initial tmux window's directory as the session root."""
    authority = resolve_session_authority(session_name, require_live=True)
    return tmux_client.get_session_root_working_directory(authority.session_name)


def _session_deletion_preflight(authority: SessionAuthority) -> Dict[str, object]:
    """Inspect destructive Session deletion without changing durable state."""
    empty_plan: Dict[str, object] = {
        "eligible": True,
        "deletion_mode": "eligible_normal",
        "cancellable": False,
        "can_resolve_and_delete": False,
        "requires_cancellation_confirmation": False,
        "requires_historical_indeterminate_confirmation": False,
        "plan_token": None,
        "blockers": [],
        "cancellable_count": 0,
        "historical_indeterminate_count": 0,
        "unsafe_count": 0,
        "live_unsafe_count": 0,
        "plan_limit": 500,
        "reason_codes": [],
    }
    if authority.deleted:
        return {
            **empty_plan,
            "eligible": True,
            "already_deleted": True,
            "requires_dirty_confirmation": False,
            "modified_files": 0,
            "untracked_files": 0,
            "reason_code": None,
            "current_queue_count": 0,
            "cancellation_plan": {"count": 0, "categories": []},
            "historical_indeterminate_plan": {"count": 0, "categories": []},
            "unsafe_blockers": [],
            "cancellable_blockers": [],
            "historical_indeterminate_blockers": [],
            "active_runtime_count": 0,
            "active_execution_count": 0,
        }

    plan = get_session_unresolved_work_plan(
        authority.session_id,
        expected_terminal_ids=[terminal["id"] for terminal in authority.terminals],
    )
    blockers = [dict(item) for item in plan.get("blockers", [])]

    def add_unsafe(category: str, reason: str) -> None:
        blockers.append(
            {
                "category": category,
                "count": 1,
                "disposition": "unsafe",
                "reason_codes": [reason],
            }
        )

    authority_reason: str | None = None
    if authority.has_recovery_fenced_history:
        authority_reason = "SESSION_RECOVERY_EVIDENCE_PROTECTED"
    elif authority.runtime_exists is None:
        authority_reason = "SESSION_RUNTIME_AUTHORITY_UNPROVEN"
    elif authority.runtime_exists and not authority.has_live_runtime_owner:
        authority_reason = "SESSION_RUNTIME_AUTHORITY_UNPROVEN"
    elif authority.runtime_exists or authority.has_live_runtime_owner:
        authority_reason = "SESSION_RUNTIME_ACTIVE"
    if authority_reason is not None and not any(
        authority_reason in item.get("reason_codes", []) for item in blockers
    ):
        add_unsafe("runtime_authority", authority_reason)

    context = get_writable_work_context_by_session(authority.session_id)
    if context is not None and context.get("state") not in {"admitted", "retiring", "retired"}:
        add_unsafe("workspace_authority", "WORKSPACE_STATE_NOT_RETIRABLE")

    unsafe_blockers = [item for item in blockers if item["disposition"] == "unsafe"]
    cancellable_blockers = [item for item in blockers if item["disposition"] == "cancellable"]
    retirement_blockers = [
        item for item in blockers if item["disposition"] == "historical_indeterminate"
    ]

    modified_files = 0
    untracked_files = 0
    if not unsafe_blockers:
        from cli_agent_orchestrator.services.managed_worktree_service import (
            managed_worktree_status,
        )

        for terminal in authority.terminals:
            if not terminal.get("managed_worktree_kind"):
                continue
            worktree = managed_worktree_status(terminal)
            if not worktree.get("safe"):
                add_unsafe(
                    "workspace_authority",
                    str(worktree.get("reason_code") or "MANAGED_WORKTREE_UNVERIFIED"),
                )
                break
            modified_files += int(worktree.get("modified_files") or 0)
            untracked_files += int(worktree.get("untracked_files") or 0)
    unsafe_blockers = [item for item in blockers if item["disposition"] == "unsafe"]
    cancellable_blockers = [item for item in blockers if item["disposition"] == "cancellable"]
    retirement_blockers = [
        item for item in blockers if item["disposition"] == "historical_indeterminate"
    ]
    eligible = not unsafe_blockers and not cancellable_blockers and not retirement_blockers
    cancellable = not unsafe_blockers and bool(cancellable_blockers)
    retirement_available = not unsafe_blockers and bool(retirement_blockers)
    can_resolve_and_delete = not unsafe_blockers and bool(
        cancellable_blockers or retirement_blockers
    )
    if unsafe_blockers:
        deletion_mode = "blocked_live_or_unsafe_authority"
    elif retirement_blockers:
        deletion_mode = "eligible_with_historical_indeterminate_retirement"
    elif cancellable_blockers:
        deletion_mode = "eligible_with_cancellable_work"
    else:
        deletion_mode = "eligible_normal"
    reason_codes = sorted(
        {str(reason) for item in blockers for reason in item.get("reason_codes", [])}
    )
    if authority_reason is not None:
        reason_code = authority_reason
    elif unsafe_blockers:
        reason_code = str(unsafe_blockers[0]["reason_codes"][0])
    elif retirement_blockers:
        reason_code = "HISTORICAL_EFFECT_OUTCOME_UNKNOWN"
    elif cancellable_blockers:
        if "OWNER_GATE" in reason_codes:
            reason_code = "OWNER_GATE"
        elif "WORKFLOW_OPEN" in reason_codes:
            reason_code = "WORKFLOW_OPEN"
        else:
            reason_code = "QUEUED_WORK"
    else:
        reason_code = None

    from cli_agent_orchestrator.services.interaction_read_model_service import (
        list_session_current_queue_counts,
    )

    current_queue_count = list_session_current_queue_counts([authority.session_id]).get(
        authority.session_id, 0
    )
    dirty = modified_files > 0 or untracked_files > 0
    active_runtime_count = sum(
        int(item["count"]) for item in unsafe_blockers if item["category"] == "runtime_authority"
    )
    active_execution_count = sum(
        int(item["count"]) for item in unsafe_blockers if item["category"] == "provider_execution"
    )
    return {
        "eligible": eligible,
        "deletion_mode": deletion_mode,
        "cancellable": cancellable,
        "can_resolve_and_delete": can_resolve_and_delete,
        "requires_cancellation_confirmation": (bool(cancellable_blockers) and not unsafe_blockers),
        "requires_historical_indeterminate_confirmation": retirement_available,
        "plan_token": plan.get("plan_token") if can_resolve_and_delete else None,
        "blockers": blockers,
        "cancellable_count": sum(int(item["count"]) for item in cancellable_blockers),
        "historical_indeterminate_count": sum(int(item["count"]) for item in retirement_blockers),
        "unsafe_count": sum(int(item["count"]) for item in unsafe_blockers),
        "live_unsafe_count": sum(int(item["count"]) for item in unsafe_blockers),
        "plan_limit": int(plan.get("plan_limit") or 500),
        "reason_codes": reason_codes,
        "already_deleted": False,
        "requires_dirty_confirmation": (eligible or can_resolve_and_delete) and dirty,
        "modified_files": modified_files,
        "untracked_files": untracked_files,
        "reason_code": reason_code,
        "current_queue_count": int(current_queue_count),
        "cancellation_plan": {
            "count": sum(int(item["count"]) for item in cancellable_blockers),
            "categories": cancellable_blockers,
        },
        "historical_indeterminate_plan": {
            "count": sum(int(item["count"]) for item in retirement_blockers),
            "categories": retirement_blockers,
        },
        "unsafe_blockers": unsafe_blockers,
        "cancellable_blockers": cancellable_blockers,
        "historical_indeterminate_blockers": retirement_blockers,
        "active_runtime_count": active_runtime_count,
        "active_execution_count": active_execution_count,
    }


def get_session_deletion_preflight(session_name: str) -> Dict[str, object]:
    """Return the exact confirmation and active-authority state for deletion."""
    return _session_deletion_preflight(resolve_session_authority(session_name))


def delete_session(
    session_name: str,
    registry: PluginRegistry | None = None,
    *,
    confirm_dirty_workspace: bool = False,
    cancel_unresolved_work: bool = False,
    retire_historical_indeterminate: bool = False,
    cancellation_plan_token: str | None = None,
) -> Dict:
    """Delete session and cleanup.

    Returns:
        Dict with 'deleted' (list of deleted session names) and 'errors' (list of error dicts).
    """
    result: Dict = {"deleted": [], "errors": [], "already_deleted": False}
    try:
        from cli_agent_orchestrator.services.operations_service import context_lifecycle_fence

        with context_lifecycle_fence():
            authority = resolve_session_authority(session_name)
            if authority.deleted:
                result["already_deleted"] = True
                result["retained_resources"] = authority.retained_resources
                return result
            terminals = authority.terminals
            preflight = _session_deletion_preflight(authority)
            if not preflight["eligible"]:
                resolution_requested = cancel_unresolved_work or retire_historical_indeterminate
                if resolution_requested:
                    if (
                        not preflight.get("can_resolve_and_delete")
                        or not cancellation_plan_token
                        or cancellation_plan_token != preflight.get("plan_token")
                    ):
                        raise SessionLifecycleError(
                            "SESSION_DELETE_PLAN_CHANGED",
                            "Session deletion authority changed; inspect the current blockers and confirm again",
                        )
                    cancellation = cancel_session_work_for_deletion(
                        authority.session_id,
                        expected_plan_token=cancellation_plan_token,
                        expected_terminal_ids=[terminal["id"] for terminal in terminals],
                        cancel_unresolved_work=cancel_unresolved_work,
                        retire_historical_indeterminate=retire_historical_indeterminate,
                    )
                    if not cancellation.get("cancelled"):
                        raise SessionLifecycleError(
                            str(
                                cancellation.get("reason_code")
                                or "SESSION_DELETE_CANCELLATION_FAILED"
                            ),
                            "Session-owned work could not be resolved safely; inspect the current blockers",
                        )
                    preflight = _session_deletion_preflight(authority)
                    if not preflight["eligible"]:
                        raise SessionLifecycleError(
                            str(preflight.get("reason_code") or "SESSION_DELETE_AUTHORITY_UNSAFE"),
                            "Cancellation completed only in part; the Session remains protected",
                        )
                else:
                    reason = str(preflight["reason_code"])
                    if preflight.get("requires_historical_indeterminate_confirmation"):
                        raise SessionLifecycleError(
                            "SESSION_HISTORICAL_INDETERMINATE_CONFIRMATION_REQUIRED",
                            "This Session has historical operations with unknown outcomes; explicit operator retirement confirmation is required",
                        )
                    if preflight.get("cancellable"):
                        raise SessionLifecycleError(
                            "SESSION_CANCELLATION_CONFIRMATION_REQUIRED",
                            "This Session has cancellable unfinished work; explicit cancellation confirmation is required",
                        )
                    messages = {
                        "SESSION_RECOVERY_EVIDENCE_PROTECTED": (
                            "This session contains recovery-takeover evidence and must be retained"
                        ),
                        "SESSION_RUNTIME_ACTIVE": (
                            "Every agent must be durably exited before deleting this session"
                        ),
                    }
                    raise SessionLifecycleError(
                        reason,
                        messages.get(
                            reason, "Session authority is still active; deletion is blocked"
                        ),
                    )
            if preflight["requires_dirty_confirmation"] and not confirm_dirty_workspace:
                raise SessionLifecycleError(
                    "SESSION_DIRTY_CONFIRMATION_REQUIRED",
                    "The workspace has uncommitted changes; explicit destructive confirmation is required",
                )

            # Capture every child result before cancelling workflows, cleaning
            # providers, or killing the session. Any failure aborts while live
            # panes remain intact.
            for terminal in terminals:
                prepare_terminal_for_destruction(terminal["id"])

            # Historical rows remain deletable without a tmux session, but
            # each exact terminal identity must independently prove that no
            # runtime can still write through its lease. This happens only
            # after any required result snapshot has been made durable.
            for terminal in terminals:
                if retire_exited_terminal_runtime(terminal["id"]) is not True:
                    raise SessionLifecycleError(
                        "SESSION_RUNTIME_AUTHORITY_UNPROVEN",
                        "Historical session runtime authority is ambiguous; metadata and writer leases remain protected",
                    )

            for terminal in terminals:
                cancel_workflows_for_terminal(terminal["id"])
            from cli_agent_orchestrator.services.inbox_service import (
                wake_provider_execution_queue,
            )

            wake_provider_execution_queue(registry)

            # Cleanup providers (non-blocking — don't let failures stop deletion)
            for terminal in terminals:
                try:
                    provider_manager.cleanup_provider(terminal["id"])
                except Exception as e:
                    logger.warning(f"Provider cleanup failed for {terminal['id']}: {e}")

            # The destructive confirmation covers dirty bytes, never active
            # authority. Any Git identity change still aborts fail-closed.
            claimed_contexts: dict[str, bool] = {}
            for terminal in terminals:
                try:
                    work_context_id = terminal.get("writable_work_context_id")
                    if work_context_id and str(work_context_id) not in claimed_contexts:
                        snapshot = get_session_workspace_retirement_snapshot(str(work_context_id))
                        if snapshot is None:
                            raise SessionLifecycleError(
                                "WORKSPACE_NOT_FOUND",
                                "Managed workspace authority disappeared during deletion",
                            )
                        durable_allow_dirty = bool(
                            snapshot["context"].get("state") == "retiring"
                            and snapshot["context"].get("retirement_allow_dirty")
                        )
                        allow_dirty = bool(confirm_dirty_workspace or durable_allow_dirty)
                        claim = claim_session_workspace_retirement(
                            str(work_context_id),
                            str(snapshot["authority_fingerprint"]),
                            allow_dirty=allow_dirty,
                        )
                        if not claim.get("claimed"):
                            raise SessionLifecycleError(
                                str(claim.get("reason_code") or "WORKSPACE_AUTHORITY_CHANGED"),
                                "Managed workspace authority changed during deletion",
                            )
                        claimed_contexts[str(work_context_id)] = allow_dirty
                    cleanup_managed_worktree(
                        terminal,
                        allow_dirty=claimed_contexts.get(
                            str(work_context_id), bool(confirm_dirty_workspace)
                        ),
                    )
                    if work_context_id:
                        from cli_agent_orchestrator.clients.database import (
                            transition_writable_work_context,
                        )

                        transition_writable_work_context(
                            str(work_context_id),
                            expected_states=("admitted", "preserved", "retiring"),
                            state="retired",
                            event_type="managed_worktree_retired",
                        )
                except ManagedWorktreeCleanupError as exc:
                    raise SessionLifecycleError(
                        exc.reason_code,
                        "Managed workspace identity changed during deletion; history was preserved",
                    ) from exc

            try:
                deletion = delete_terminals_by_session_lifetime(
                    authority.session_id,
                    authority.session_name,
                    expected_terminal_ids=[terminal["id"] for terminal in terminals],
                    retained_resources=[],
                )
            except AmbiguousSessionIdentity as exc:
                raise SessionLifecycleError(
                    "SESSION_IDENTITY_CHANGED",
                    "Session identity changed during deletion; retry after reconciliation",
                    inventory_uncertain=True,
                ) from exc
            if not deletion["already_deleted"] and deletion["logical_deleted"] != len(terminals):
                raise SessionLifecycleError(
                    "SESSION_IDENTITY_CHANGED",
                    "Session identity changed during deletion; retry after reconciliation",
                    inventory_uncertain=True,
                )

        result["deleted"].append(authority.session_name)
        result["already_deleted"] = bool(deletion["already_deleted"])
        result["retained_resources"] = list(deletion["retained_resources"])
        logger.info(f"Deleted session lifetime: {authority.session_id}")
        dispatch_plugin_event(
            registry,
            "post_kill_session",
            PostKillSessionEvent(
                session_id=authority.session_id, session_name=authority.session_name
            ),
        )
        return result

    except Exception as e:
        logger.error(f"Failed to delete session {session_name}: {e}")
        raise
