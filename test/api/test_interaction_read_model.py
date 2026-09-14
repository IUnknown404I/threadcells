"""API contract for the bounded interaction queue/history read model."""

from unittest.mock import patch

from cli_agent_orchestrator.services.interaction_read_model_service import (
    SessionInteractionsDeleted,
)


def _page(mode: str):
    return {
        "items": [],
        "total": 0,
        "limit": 12,
        "next_cursor": None,
        "snapshot_at": "2026-09-07T10:00:00",
        "mode": mode,
    }


def test_interaction_endpoint_forwards_cursor_scope(client):
    with patch(
        "cli_agent_orchestrator.api.main.interaction_read_model_service.list_interactions",
        return_value=_page("history"),
    ) as listed:
        response = client.get(
            "/ui/interactions?session_id=session-1&terminal_id=agent-1"
            "&mode=history&limit=12&cursor=opaque"
        )

    assert response.status_code == 200
    assert response.json()["snapshot_at"] == "2026-09-07T10:00:00"
    listed.assert_called_once_with(
        "session-1",
        mode="history",
        terminal_id="agent-1",
        limit=12,
        cursor="opaque",
    )


def test_interaction_endpoint_rejects_service_cursor_error(client):
    with patch(
        "cli_agent_orchestrator.api.main.interaction_read_model_service.list_interactions",
        side_effect=ValueError("cursor is invalid"),
    ):
        response = client.get("/ui/interactions?session_id=session-1&mode=history&cursor=invalid")

    assert response.status_code == 400
    assert response.json()["detail"] == "cursor is invalid"


def test_interaction_endpoint_bounds_page_size(client):
    response = client.get("/ui/interactions?session_id=session-1&limit=51")
    assert response.status_code == 422


def test_interaction_endpoint_returns_deleted_semantics_after_hard_purge(client):
    with patch(
        "cli_agent_orchestrator.api.main.interaction_read_model_service.list_interactions",
        side_effect=SessionInteractionsDeleted("SESSION_DELETED"),
    ):
        response = client.get("/ui/interactions?session_id=session-1&mode=history")

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "SESSION_DELETED",
        "message": "Session was permanently deleted",
    }
