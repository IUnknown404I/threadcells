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

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List

from cli_agent_orchestrator.clients.database import (
    AmbiguousSessionIdentity,
    SessionLifetimeAuthorityError,
    begin_session_hard_deletion,
    bind_session_hard_deletion_workspace_authority,
    cancel_session_work_for_deletion,
    cancel_workflows_for_terminal,
    claim_session_workspace_retirement,
    complete_session_hard_deletion,
    get_session_hard_deletion_operation,
    get_session_unresolved_work_plan,
    get_session_workspace_retirement_snapshot,
    get_writable_work_context_by_session,
    list_session_historical_terminal_cleanup_authorities,
    mark_session_hard_deletion_workspace_retired,
    resolve_session_lifetime,
    revalidate_session_hard_deletion,
    transition_writable_work_context,
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
from cli_agent_orchestrator.services.housekeeping_service import housekeeping_mutation_fence
from cli_agent_orchestrator.services.managed_worktree_service import (
    capture_session_worktree_retirement_authority,
    purge_managed_worktree,
    purge_session_managed_worktrees,
)
from cli_agent_orchestrator.services.plugin_dispatch import dispatch_plugin_event
from cli_agent_orchestrator.services.terminal_artifact_service import (
    TerminalArtifactCleanupError,
    purge_session_terminal_artifacts,
)
from cli_agent_orchestrator.services.terminal_service import (
    create_terminal,
    prepare_terminal_for_destruction,
    prove_live_session_runtime_authority,
    retire_exited_terminal_runtime,
)

logger = logging.getLogger(__name__)
_WORKSPACE_CONTEXT_NOT_LOADED = object()


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
    deletion_in_progress: bool = False

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
    except SessionLifetimeAuthorityError as exc:
        raise SessionLifecycleError(
            exc.reason_code,
            "Session lifetime authority is incomplete or conflicting; reconciliation is required",
            inventory_uncertain=True,
        ) from exc
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
        deletion_in_progress=bool(durable.get("deletion_in_progress")),
    )
    if not require_live:
        return authority
    if authority.deletion_in_progress:
        raise SessionLifecycleError(
            "SESSION_DELETION_IN_PROGRESS",
            "This Session is being permanently deleted and cannot accept new work",
        )
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


def _capture_session_workspace_retirement_authority(
    authority: SessionAuthority,
    *,
    context: Dict[str, Any] | None | object = _WORKSPACE_CONTEXT_NOT_LOADED,
) -> Dict[str, Any]:
    """Bind current Git state to the exact durable writable-context state."""
    captured = capture_session_worktree_retirement_authority(
        authority.terminals,
        session_id=authority.session_id,
    )
    if not captured.get("safe"):
        return captured
    if context is _WORKSPACE_CONTEXT_NOT_LOADED:
        context = get_writable_work_context_by_session(authority.session_id)
    context_authority = None
    if context is not None:
        if not isinstance(context, dict):
            return {"safe": False, "reason_code": "WORKSPACE_AUTHORITY_CHANGED"}
        if context.get("session_id") != authority.session_id:
            return {"safe": False, "reason_code": "WORKSPACE_AUTHORITY_CHANGED"}
        context_authority = {
            key: context.get(key)
            for key in (
                "id",
                "session_id",
                "terminal_id",
                "project_id",
                "canonical_source",
                "canonical_worktree",
                "branch",
                "base_revision",
                "state",
                "writer_authority_generation",
                "retirement_allow_dirty",
                "retirement_authority_fingerprint",
            )
        }
    current_authority = {
        **dict(captured["authority"]),
        "work_context": context_authority,
    }
    encoded = json.dumps(current_authority, sort_keys=True, separators=(",", ":"))
    return {
        **captured,
        "authority": current_authority,
        "authority_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
    }


def _workspace_context_matches_retirement_progress(
    expected: object,
    current: Dict[str, Any] | None,
) -> bool:
    """Accept only exact context authority or this delete's forward progress."""
    if expected is None:
        return current is None
    if not isinstance(expected, dict) or current is None:
        return False
    immutable_keys = {
        "id",
        "session_id",
        "terminal_id",
        "project_id",
        "canonical_source",
        "canonical_worktree",
        "branch",
        "base_revision",
        "writer_authority_generation",
    }
    if any(expected.get(key) != current.get(key) for key in immutable_keys):
        return False
    expected_state = expected.get("state")
    current_state = current.get("state")
    allowed_progress = {
        "admitted": {"admitted", "retiring", "retired"},
        "retiring": {"retiring", "retired"},
        "retired": {"retired"},
    }
    if current_state not in allowed_progress.get(str(expected_state), set()):
        return False
    if current_state == expected_state:
        return all(
            expected.get(key) == current.get(key)
            for key in ("retirement_allow_dirty", "retirement_authority_fingerprint")
        )
    return bool(
        current_state == "retired"
        or (
            current_state == "retiring"
            and isinstance(current.get("retirement_authority_fingerprint"), str)
            and current.get("retirement_authority_fingerprint")
        )
    )


def _session_deletion_preflight(
    authority: SessionAuthority,
    *,
    include_workspace_authority: bool = False,
) -> Dict[str, object]:
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
    if authority.deletion_in_progress:
        return {
            **empty_plan,
            "eligible": True,
            "deletion_mode": "deletion_in_progress",
            "deletion_in_progress": True,
            "already_deleted": False,
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
    workspace_capture: dict[str, Any] | None = None
    if not unsafe_blockers:
        captured = _capture_session_workspace_retirement_authority(
            authority,
            context=context,
        )
        workspace_capture = dict(captured)
        if not captured.get("safe"):
            add_unsafe(
                "workspace_authority",
                str(captured.get("reason_code") or "MANAGED_WORKTREE_UNVERIFIED"),
            )
        else:
            context_retired = bool(context and context.get("state") == "retired")
            worktrees = captured["authority"]["worktrees"]
            if context_retired and any(item.get("present") for item in worktrees):
                add_unsafe(
                    "workspace_authority",
                    "WORKSPACE_RETIREMENT_STATE_CONFLICT",
                )
            elif not context_retired and any(
                item.get("managed") and not item.get("present") for item in worktrees
            ):
                # Before a durable deletion/retirement fence exists, an absent
                # launch path could be a moved registration or external loss.
                # Only the retired context (or an already-fenced retry, which
                # bypasses fresh preflight) makes that absence authoritative.
                add_unsafe(
                    "workspace_authority",
                    "WORKSPACE_RETIREMENT_STATE_CONFLICT",
                )
            modified_files = int(captured.get("modified_files") or 0)
            untracked_files = int(captured.get("untracked_files") or 0)
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
    workspace_authority_sha256 = (
        str(workspace_capture["authority_sha256"])
        if workspace_capture is not None and workspace_capture.get("safe")
        else None
    )
    deletion_plan_token = None
    if workspace_authority_sha256 is not None and (eligible or can_resolve_and_delete):
        deletion_plan_token = hashlib.sha256(
            json.dumps(
                {
                    "version": 1,
                    "session_id": authority.session_id,
                    "unresolved_plan_token": plan.get("plan_token"),
                    "workspace_authority_sha256": workspace_authority_sha256,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    result = {
        "eligible": eligible,
        "deletion_mode": deletion_mode,
        "cancellable": cancellable,
        "can_resolve_and_delete": can_resolve_and_delete,
        "requires_cancellation_confirmation": (bool(cancellable_blockers) and not unsafe_blockers),
        "requires_historical_indeterminate_confirmation": retirement_available,
        "plan_token": deletion_plan_token,
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
    if include_workspace_authority and workspace_capture is not None:
        result["_workspace_authority"] = workspace_capture.get("authority")
        result["_workspace_authority_sha256"] = workspace_authority_sha256
        result["_unresolved_plan_token"] = plan.get("plan_token")
    return result


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
    """Permanently delete one Session after fencing every live authority.

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
            terminal_ids = [str(terminal["id"]) for terminal in terminals]
            operation = get_session_hard_deletion_operation(authority.session_id)
            deletion: Dict | None = None
            planned_workspace_authority: dict[str, Any] | None = None
            if operation is None:
                preflight = _session_deletion_preflight(authority, include_workspace_authority=True)
                if cancellation_plan_token is not None and cancellation_plan_token != (
                    preflight.get("plan_token")
                ):
                    raise SessionLifecycleError(
                        "SESSION_DELETE_PLAN_CHANGED",
                        "Session deletion authority changed; inspect and confirm again",
                    )
                confirmed_workspace_sha256 = preflight.get("_workspace_authority_sha256")
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
                                "Session deletion authority changed; inspect and confirm again",
                            )
                        cancellation = cancel_session_work_for_deletion(
                            authority.session_id,
                            expected_plan_token=str(preflight.get("_unresolved_plan_token") or ""),
                            expected_terminal_ids=terminal_ids,
                            cancel_unresolved_work=cancel_unresolved_work,
                            retire_historical_indeterminate=retire_historical_indeterminate,
                        )
                        if not cancellation.get("cancelled"):
                            raise SessionLifecycleError(
                                str(
                                    cancellation.get("reason_code")
                                    or "SESSION_DELETE_CANCELLATION_FAILED"
                                ),
                                "Session-owned work could not be resolved safely",
                            )
                        preflight = _session_deletion_preflight(
                            authority, include_workspace_authority=True
                        )
                        if confirmed_workspace_sha256 != preflight.get(
                            "_workspace_authority_sha256"
                        ):
                            raise SessionLifecycleError(
                                "WRITABLE_WORKTREE_AUTHORITY_CHANGED",
                                "Workspace authority changed after deletion confirmation",
                            )
                        if not preflight["eligible"]:
                            raise SessionLifecycleError(
                                str(
                                    preflight.get("reason_code")
                                    or "SESSION_DELETE_AUTHORITY_UNSAFE"
                                ),
                                "Cancellation was incomplete; the Session remains protected",
                            )
                    else:
                        reason = str(preflight["reason_code"])
                        if preflight.get("requires_historical_indeterminate_confirmation"):
                            raise SessionLifecycleError(
                                "SESSION_HISTORICAL_INDETERMINATE_CONFIRMATION_REQUIRED",
                                "Historical unknown outcomes require explicit deletion confirmation",
                            )
                        if preflight.get("cancellable"):
                            raise SessionLifecycleError(
                                "SESSION_CANCELLATION_CONFIRMATION_REQUIRED",
                                "Unfinished Session work requires explicit cancellation confirmation",
                            )
                        raise SessionLifecycleError(
                            reason,
                            "Session authority is still active; deletion is blocked",
                        )
                workspace_authority = preflight.get("_workspace_authority")
                if not isinstance(workspace_authority, dict):
                    raise SessionLifecycleError(
                        "WORKSPACE_AUTHORITY_CHANGED",
                        "Current workspace authority could not be captured",
                    )
                planned_workspace_authority = workspace_authority
                if preflight["requires_dirty_confirmation"] and not confirm_dirty_workspace:
                    raise SessionLifecycleError(
                        "SESSION_DIRTY_CONFIRMATION_REQUIRED",
                        "Uncommitted workspace changes require explicit destructive confirmation",
                    )

                for terminal in terminals:
                    prepare_terminal_for_destruction(terminal["id"])
                for terminal in terminals:
                    if retire_exited_terminal_runtime(terminal["id"]) is not True:
                        raise SessionLifecycleError(
                            "SESSION_RUNTIME_AUTHORITY_UNPROVEN",
                            "Historical runtime authority remains ambiguous",
                        )
                for terminal in terminals:
                    cancel_workflows_for_terminal(terminal["id"])
                from cli_agent_orchestrator.services.inbox_service import (
                    wake_provider_execution_queue,
                )

                wake_provider_execution_queue(registry)
                try:
                    started = begin_session_hard_deletion(
                        authority.session_id,
                        authority.session_name,
                        expected_terminal_ids=terminal_ids,
                        allow_dirty_workspace=bool(confirm_dirty_workspace),
                    )
                except AmbiguousSessionIdentity as exc:
                    raise SessionLifecycleError(
                        "SESSION_IDENTITY_CHANGED",
                        "Session identity changed; retry after reconciliation",
                        inventory_uncertain=True,
                    ) from exc
                if not started.get("started"):
                    raise SessionLifecycleError(
                        str(started.get("reason_code") or "SESSION_DELETE_PLAN_CHANGED"),
                        "Session deletion authority changed before the durable fence",
                    )
                if started.get("already_deleted"):
                    deletion = complete_session_hard_deletion(
                        authority.session_id, authority.session_name
                    )
                    operation = None
                else:
                    operation = started
            if operation is None and deletion is not None and deletion.get("completed"):
                pass
            elif operation is None:
                raise SessionLifecycleError(
                    "SESSION_DELETE_FENCE_MISSING",
                    "The durable Session deletion fence could not be established",
                )
            if operation is not None and set(operation["terminal_ids"]) != set(terminal_ids):
                raise SessionLifecycleError(
                    "SESSION_IDENTITY_CHANGED",
                    "Session terminal identity changed during deletion",
                    inventory_uncertain=True,
                )
            if operation is not None:
                revalidated = revalidate_session_hard_deletion(
                    authority.session_id,
                    authority.session_name,
                )
                if not revalidated.get("valid") or set(revalidated.get("terminal_ids", ())) != set(
                    terminal_ids
                ):
                    raise SessionLifecycleError(
                        str(revalidated.get("reason_code") or "SESSION_DELETE_PLAN_CHANGED"),
                        "Session deletion authority changed before physical cleanup",
                    )
                graph_terminal_ids = [
                    str(value) for value in revalidated.get("graph_terminal_ids", terminal_ids)
                ]
                historical_terminal_cleanup = list_session_historical_terminal_cleanup_authorities(
                    authority.session_id
                )
                if operation.get("workspace_authority") is None:
                    if planned_workspace_authority is None:
                        captured = _capture_session_workspace_retirement_authority(authority)
                        if not captured.get("safe"):
                            raise SessionLifecycleError(
                                str(
                                    captured.get("reason_code")
                                    or "WRITABLE_WORKTREE_AUTHORITY_CHANGED"
                                ),
                                "Current workspace authority could not be captured",
                            )
                        if (
                            int(captured.get("modified_files") or 0)
                            or int(captured.get("untracked_files") or 0)
                        ) and not bool(operation["allow_dirty_workspace"]):
                            raise SessionLifecycleError(
                                "SESSION_DIRTY_CONFIRMATION_REQUIRED",
                                "Uncommitted workspace changes require explicit confirmation",
                            )
                        planned_workspace_authority = captured["authority"]
                    if planned_workspace_authority is None:
                        raise SessionLifecycleError(
                            "WORKSPACE_CLEANUP_UNPROVEN",
                            "Current workspace authority could not be bound",
                        )
                    bound = bind_session_hard_deletion_workspace_authority(
                        authority.session_id,
                        authority.session_name,
                        workspace_authority=planned_workspace_authority,
                    )
                    if not bound.get("bound"):
                        raise SessionLifecycleError(
                            str(bound.get("reason_code") or "WRITABLE_WORKTREE_AUTHORITY_CHANGED"),
                            "Current workspace authority changed before cleanup",
                        )
                    # The binding transaction has already normalized, hashed,
                    # and durably committed this exact document.  Carry that
                    # admitted authority through the current request; later
                    # retries read the same document from the operation row.
                    operation = dict(operation)
                    operation["workspace_authority"] = planned_workspace_authority
                    operation["workspace_authority_sha256"] = bound.get(
                        "workspace_authority_sha256"
                    )
            else:
                graph_terminal_ids = terminal_ids
                historical_terminal_cleanup = []

            for terminal in terminals if operation is not None else ():
                try:
                    provider_manager.cleanup_provider(terminal["id"])
                except Exception as error:
                    logger.warning("Provider cleanup failed for %s: %s", terminal["id"], error)

            artifact_cleanup: dict[str, object] = {
                "runtime_artifacts_absent": True,
                "terminals": [],
            }
            if operation is not None:
                try:
                    with housekeeping_mutation_fence():
                        context = get_writable_work_context_by_session(authority.session_id)
                        context_already_retired = bool(
                            context and context.get("state") == "retired"
                        )
                        if not _workspace_context_matches_retirement_progress(
                            operation["workspace_authority"].get("work_context"),
                            context,
                        ):
                            raise SessionLifecycleError(
                                "WRITABLE_WORKTREE_AUTHORITY_CHANGED",
                                "Writable workspace context changed during deletion",
                            )
                        allow_dirty = bool(operation["allow_dirty_workspace"])
                        if context is not None and not context_already_retired:
                            snapshot = get_session_workspace_retirement_snapshot(str(context["id"]))
                            if snapshot is None:
                                raise SessionLifecycleError(
                                    "WORKSPACE_NOT_FOUND",
                                    "Managed workspace authority disappeared during deletion",
                                )
                            durable_allow_dirty = bool(
                                snapshot["context"].get("state") == "retiring"
                                and snapshot["context"].get("retirement_allow_dirty")
                            )
                            if (
                                durable_allow_dirty != allow_dirty
                                and snapshot["context"].get("state") == "retiring"
                            ):
                                raise SessionLifecycleError(
                                    "WORKSPACE_AUTHORITY_CHANGED",
                                    "Workspace destructive authority changed during retry",
                                )
                            claim = claim_session_workspace_retirement(
                                str(context["id"]),
                                str(snapshot["authority_fingerprint"]),
                                allow_dirty=allow_dirty,
                            )
                            if not claim.get("claimed"):
                                raise SessionLifecycleError(
                                    str(claim.get("reason_code") or "WORKSPACE_AUTHORITY_CHANGED"),
                                    "Managed workspace authority changed during deletion",
                                )

                        cleanup = purge_session_managed_worktrees(
                            terminals,
                            operation["workspace_authority"],
                            allow_dirty=allow_dirty,
                            require_already_absent=context_already_retired,
                        )
                        if not cleanup.get("removed"):
                            raise SessionLifecycleError(
                                str(cleanup.get("reason_code") or "MANAGED_WORKTREE_UNVERIFIED"),
                                "Managed worktree cleanup could not be proven",
                            )
                        workspace_evidence: list[dict[str, object]] = list(
                            cleanup.get("evidence") or []
                        )
                        artifact_cleanup = purge_session_terminal_artifacts(graph_terminal_ids)
                        if not artifact_cleanup.get("runtime_artifacts_absent"):
                            raise SessionLifecycleError(
                                "TERMINAL_ARTIFACT_CLEANUP_UNPROVEN",
                                "Terminal output or attachment cleanup could not be proven",
                            )
                        # Individual terminal retirement removes the worktree
                        # but deliberately preserves its private task branch.
                        # Consume the exact receipt-bound Git identity here;
                        # legacy receipts without that authority remain unsafe
                        # in preflight rather than receiving fabricated proof.
                        for historical in historical_terminal_cleanup:
                            cleanup = purge_managed_worktree(
                                historical,
                                require_already_absent=True,
                            )
                            if not cleanup.get("removed") and cleanup.get("managed"):
                                raise SessionLifecycleError(
                                    str(
                                        cleanup.get("reason_code") or "MANAGED_WORKTREE_UNVERIFIED"
                                    ),
                                    "Historical managed-worktree cleanup could not be proven",
                                )
                            workspace_evidence.append(
                                {
                                    "terminal_id": str(historical["terminal_id"]),
                                    "managed": bool(cleanup.get("managed")),
                                    "path_absent": bool(cleanup.get("path_absent", True)),
                                    "git_unregistered": bool(cleanup.get("git_unregistered", True)),
                                    "branch_absent": bool(cleanup.get("branch_absent", True)),
                                    "runtime_artifacts_absent": True,
                                }
                            )
                        if context is not None and not context_already_retired:
                            if not transition_writable_work_context(
                                str(context["id"]),
                                expected_states=("admitted", "retiring"),
                                state="retired",
                                event_type="managed_worktree_hard_deleted",
                            ):
                                raise SessionLifecycleError(
                                    "WORKSPACE_AUTHORITY_CHANGED",
                                    "Workspace state changed before cleanup completion",
                                )
                        marked = mark_session_hard_deletion_workspace_retired(
                            authority.session_id, workspace_evidence=workspace_evidence
                        )
                        if not marked.get("marked"):
                            raise SessionLifecycleError(
                                str(marked.get("reason_code") or "WORKSPACE_CLEANUP_UNPROVEN"),
                                "Physical workspace cleanup could not be recorded",
                            )
                except TerminalArtifactCleanupError as error:
                    raise SessionLifecycleError(
                        error.reason_code,
                        "Terminal output or attachment cleanup could not be proven",
                    ) from error
                except RuntimeError as error:
                    if str(error) != "HOUSEKEEPING_BUSY":
                        raise
                    raise SessionLifecycleError(
                        "HOUSEKEEPING_BUSY",
                        "Physical cleanup is busy; retry Session deletion",
                    ) from error
            if operation is not None:
                try:
                    deletion = complete_session_hard_deletion(
                        authority.session_id, authority.session_name
                    )
                except AmbiguousSessionIdentity as exc:
                    raise SessionLifecycleError(
                        "SESSION_IDENTITY_CHANGED",
                        "Session identity changed during final purge",
                        inventory_uncertain=True,
                    ) from exc
            if deletion is None or not deletion.get("completed"):
                reason_code = (
                    str(deletion.get("reason_code"))
                    if deletion is not None and deletion.get("reason_code")
                    else "SESSION_PURGE_INCOMPLETE"
                )
                raise SessionLifecycleError(
                    reason_code,
                    "The Session remains fenced; retry deletion to converge",
                )

        result["deleted"].append(authority.session_name)
        result["already_deleted"] = bool(deletion["already_deleted"])
        result["retained_resources"] = []
        result["purged_rows"] = dict(deletion.get("before_counts", {}))
        result["remaining_rows"] = dict(deletion.get("after_counts", {}))
        result["tombstone_count"] = int(deletion.get("tombstone_count", 1))
        result["terminal_artifacts"] = artifact_cleanup
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
