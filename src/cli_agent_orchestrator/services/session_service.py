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
    cancel_workflows_for_terminal,
    delete_terminals_by_session_lifetime,
    mark_terminal_runtime_exited,
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
    cleanup_managed_worktree,
    create_terminal,
    prepare_terminal_for_destruction,
    prove_live_session_runtime_authority,
    retire_exited_terminal_runtime,
    validate_managed_worktree_cleanup,
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
    deleted: bool
    runtime_exists: bool | None

    @property
    def has_live_runtime_owner(self) -> bool:
        return any(terminal.get("runtime_lifecycle") != "exited" for terminal in self.terminals)


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
        deleted=bool(durable["deleted"]),
        runtime_exists=runtime_exists,
    )
    if not require_live:
        return authority
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
) -> Terminal:
    """Create a new session by creating its initial terminal."""

    create_terminal_kwargs = {
        "provider": provider,
        "agent_profile": agent_profile,
        "session_name": session_name,
        "new_session": True,
        "working_directory": working_directory,
        "allowed_tools": allowed_tools,
        "registry": registry,
        "project_context": project_context,
        "owner_grant_token": owner_grant_token,
        "owner_grant_launch_id": owner_grant_launch_id,
    }
    terminal = create_terminal(
        **create_terminal_kwargs,
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


def delete_session(session_name: str, registry: PluginRegistry | None = None) -> Dict:
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
                return result
            terminals = authority.terminals

            # Worktree safety and exact runtime authority are read-only
            # preconditions. Do not cancel workflows or clean providers until
            # every destructive boundary is known to be eligible.
            for terminal in terminals:
                validate_managed_worktree_cleanup(terminal)
            if authority.has_live_runtime_owner:
                proof = prove_live_session_runtime_authority(
                    authority.session_name, terminals, runtime_client=tmux_client
                )
                if not proof.proven:
                    raise SessionLifecycleError(
                        proof.reason_code or "SESSION_RUNTIME_AUTHORITY_DIVERGED",
                        f"{proof.message}; deletion is protected",
                        inventory_uncertain=proof.inventory_uncertain,
                    )

            # Capture every child result before cancelling workflows, cleaning
            # providers, or killing the session. Any failure aborts while live
            # panes remain intact.
            for terminal in terminals:
                prepare_terminal_for_destruction(terminal["id"])

            if not authority.has_live_runtime_owner:
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

            if authority.has_live_runtime_owner:
                # Revalidate immediately before killing the reusable tmux
                # name; the first proof may have become stale meanwhile.
                proof = prove_live_session_runtime_authority(
                    authority.session_name, terminals, runtime_client=tmux_client
                )
                if not proof.proven:
                    raise SessionLifecycleError(
                        proof.reason_code or "SESSION_RUNTIME_AUTHORITY_DIVERGED",
                        f"{proof.message}; deletion is protected",
                        inventory_uncertain=proof.inventory_uncertain,
                    )
                tmux_client.kill_session(authority.session_name)
                death = tmux_client.session_exists(authority.session_name)
                if death is not False:
                    raise SessionLifecycleError(
                        "SESSION_DEATH_UNCONFIRMED",
                        "Session death was not confirmed; metadata and writer leases remain protected",
                        inventory_uncertain=death is None,
                    )
                for terminal in terminals:
                    mark_terminal_runtime_exited(terminal["id"])

            # Every managed worktree was validated before runtime mutation.
            for terminal in terminals:
                cleanup_managed_worktree(terminal)

            try:
                deletion = delete_terminals_by_session_lifetime(
                    authority.session_id,
                    authority.session_name,
                    expected_terminal_ids=[terminal["id"] for terminal in terminals],
                )
            except AmbiguousSessionIdentity as exc:
                raise SessionLifecycleError(
                    "SESSION_IDENTITY_CHANGED",
                    "Session identity changed during deletion; retry after reconciliation",
                    inventory_uncertain=True,
                ) from exc
            if not deletion["already_deleted"] and deletion["deleted"] != len(terminals):
                raise SessionLifecycleError(
                    "SESSION_IDENTITY_CHANGED",
                    "Session identity changed during deletion; retry after reconciliation",
                    inventory_uncertain=True,
                )

        result["deleted"].append(authority.session_name)
        result["already_deleted"] = bool(deletion["already_deleted"])
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
