"""Read-only service boundary for durable delegation result artifacts."""

from typing import Any, Dict, List, Optional

from cli_agent_orchestrator.clients.database import (
    get_delegation_result,
    list_delegation_results,
)


def read_result(result_id: str) -> Optional[Dict[str, Any]]:
    """Return one immutable result artifact by its opaque server-minted ID."""
    return get_delegation_result(result_id)


def list_results(
    terminal_id: Optional[str] = None,
    session_name: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    cursor: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List retained artifacts without exposing prompts or workflow bindings."""
    return list_delegation_results(terminal_id, session_name, status, limit, cursor)
