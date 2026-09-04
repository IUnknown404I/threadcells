"""Bounded durable read models for operational Web surfaces."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, Optional

from cli_agent_orchestrator.clients.database import (
    get_terminal_ui_overview_counts,
    list_terminal_ui_session_page,
    list_terminal_ui_summary_page,
)

DEFAULT_SESSION_PAGE_SIZE = 10
DEFAULT_AGENT_PAGE_SIZE = 40
MAX_PAGE_SIZE = 100


def _validate_page(limit: int, offset: int) -> None:
    if isinstance(limit, bool) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
    if isinstance(offset, bool) or offset < 0:
        raise ValueError("offset must be non-negative")


def _iso(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value else None


def _page(items: list[Dict[str, Any]], *, total: int, limit: int, offset: int) -> Dict[str, Any]:
    next_offset = offset + len(items)
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "next_offset": next_offset if next_offset < total else None,
    }


def get_overview() -> Dict[str, Any]:
    """Return authoritative counters without observing provider processes."""
    return get_terminal_ui_overview_counts()


def list_session_summaries(
    *, limit: int = DEFAULT_SESSION_PAGE_SIZE, offset: int = 0, query: str = ""
) -> Dict[str, Any]:
    """Return one page of stable session lifetimes, including history."""
    _validate_page(limit, offset)
    projection = list_terminal_ui_session_page(
        limit=limit, offset=offset, query=query.strip().lower()
    )
    items = []
    for row in projection["items"]:
        item = dict(row)
        item["created_at"] = _iso(item.get("created_at"))
        item["last_active"] = _iso(item.get("last_active"))
        item["agent_count"] = int(item.get("agent_count") or 0)
        item["active_agent_count"] = int(item.get("active_agent_count") or 0)
        items.append(item)
    return _page(items, total=int(projection["total"]), limit=limit, offset=offset)


def _public_agent_summary(terminal: Dict[str, Any]) -> Dict[str, Any]:
    keys = (
        "id",
        "name",
        "provider",
        "session_id",
        "session_name",
        "agent_profile",
        "activity",
        "execution_state",
        "lifecycle",
        "workflow_state",
        "workflow_status",
        "workflow_reason",
        "queued_task_count",
        "workflow_recovery_pending",
        "provider_outcome_code",
        "provider_outcome_detail",
        "assignment_status",
        "result_status",
        "delivery_status",
        "context_role",
        "launch_worktree",
        "managed_worktree_kind",
        "managed_worktree_commit",
        "managed_worktree_branch",
        "writable_work_context_id",
        "writer_authority_generation",
        "workspace_classification",
        "workspace_state",
        "projectId",
        "project_name",
        "project_path",
        "creation_order",
    )
    result = {key: terminal.get(key) for key in keys}
    result["queued_task_count"] = int(result.get("queued_task_count") or 0)
    result["workflow_recovery_pending"] = bool(result.get("workflow_recovery_pending"))
    result["last_active"] = _iso(terminal.get("last_active"))
    return result


def list_agent_summaries(
    *,
    limit: int = DEFAULT_AGENT_PAGE_SIZE,
    offset: int = 0,
    session_id: Optional[str] = None,
    query: str = "",
    activities: Iterable[str] = (),
    workflow_states: Iterable[str] = (),
    profiles: Iterable[str] = (),
    home_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """Return one filtered page of lightweight durable agent metadata."""
    _validate_page(limit, offset)
    projection = list_terminal_ui_summary_page(
        limit=limit,
        offset=offset,
        session_id=session_id,
        query=query.strip().lower(),
        activities=list(activities),
        workflow_states=list(workflow_states),
        profiles=list(profiles),
        home_filter=home_filter,
    )
    result = _page(
        [_public_agent_summary(item) for item in projection["items"]],
        total=int(projection["total"]),
        limit=limit,
        offset=offset,
    )
    result["facets"] = projection["facets"]
    return result
