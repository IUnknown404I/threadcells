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
from typing import Dict, List

from cli_agent_orchestrator.clients.database import (
    cancel_workflows_for_terminal,
    delete_terminals_by_session,
    list_terminals_by_session,
    mark_terminal_runtime_exited,
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
    validate_managed_worktree_cleanup,
)

logger = logging.getLogger(__name__)


def _require_session(session_name: str) -> None:
    """Require positive session inventory without treating uncertainty as absence."""
    exists = tmux_client.session_exists(session_name)
    if exists is None:
        raise RuntimeError(f"Could not determine whether session '{session_name}' exists")
    if exists is False:
        raise ValueError(f"Session '{session_name}' not found")


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
        _require_session(session_name)

        tmux_sessions = tmux_client.list_sessions()
        if tmux_sessions is None:
            raise RuntimeError("Could not inventory tmux sessions")
        session_data = next((s for s in tmux_sessions if s["id"] == session_name), None)

        if not session_data:
            raise ValueError(f"Session '{session_name}' not found")

        terminals = list_terminals_by_session(session_name)
        return {"session": session_data, "terminals": terminals}

    except Exception as e:
        logger.error(f"Failed to get session {session_name}: {e}")
        raise


def get_session_root_working_directory(session_name: str) -> str | None:
    """Return the initial tmux window's directory as the session root."""
    _require_session(session_name)
    return tmux_client.get_session_root_working_directory(session_name)


def delete_session(session_name: str, registry: PluginRegistry | None = None) -> Dict:
    """Delete session and cleanup.

    Returns:
        Dict with 'deleted' (list of deleted session names) and 'errors' (list of error dicts).
    """
    result: Dict = {"deleted": [], "errors": []}
    try:
        _require_session(session_name)

        terminals = list_terminals_by_session(session_name)

        # Capture every child result before cancelling workflows, cleaning
        # providers, or killing the session.  Any failure aborts the whole
        # destructive operation while all panes remain intact.
        for terminal in terminals:
            prepare_terminal_for_destruction(terminal["id"])

        for terminal in terminals:
            cancel_workflows_for_terminal(terminal["id"])
        from cli_agent_orchestrator.services.inbox_service import wake_provider_execution_queue

        wake_provider_execution_queue(registry)

        # Cleanup providers (non-blocking — don't let failures stop deletion)
        for terminal in terminals:
            try:
                provider_manager.cleanup_provider(terminal["id"])
            except Exception as e:
                logger.warning(f"Provider cleanup failed for {terminal['id']}: {e}")

        # Metadata (and any writer lease) may be removed only after tmux
        # positively reports that the session was killed.  A false/uncertain
        # result leaves the durable owner fenced for later reconciliation.
        if not tmux_client.kill_session(session_name):
            raise RuntimeError(
                f"Session death not confirmed for {session_name}; metadata and writer leases retained"
            )

        # Positive session death retires runtime authority immediately.  Keep
        # this independent from later optional history/worktree cleanup.
        for terminal in terminals:
            mark_terminal_runtime_exited(terminal["id"])

        # The tmux/process boundary is now positively dead. Remove only clean,
        # verifiable managed worktrees before deleting historical metadata.
        # Any dirty/unverifiable worktree preserves every metadata row, but the
        # already-dead runtimes no longer own writer leases.
        for terminal in terminals:
            validate_managed_worktree_cleanup(terminal)
        for terminal in terminals:
            cleanup_managed_worktree(terminal)

        # Delete terminal metadata
        delete_terminals_by_session(session_name)

        result["deleted"].append(session_name)
        logger.info(f"Deleted session: {session_name}")
        dispatch_plugin_event(
            registry,
            "post_kill_session",
            PostKillSessionEvent(session_id=session_name, session_name=session_name),
        )
        return result

    except Exception as e:
        logger.error(f"Failed to delete session {session_name}: {e}")
        raise
