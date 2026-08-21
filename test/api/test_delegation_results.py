"""Local API contract for retained F14 result history."""

from unittest.mock import patch


def _result(result_id="result-1"):
    return {
        "id": result_id,
        "delegation_kind": "assign",
        "status": "complete",
        "authorship": "child_submission",
        "document": {"body_markdown": "done", "format": "legacy_text"},
    }


def test_get_delegation_result_returns_artifact(client):
    with patch(
        "cli_agent_orchestrator.api.main.result_service.read_result", return_value=_result()
    ):
        response = client.get("/delegation-results/result-1")
    assert response.status_code == 200
    assert response.json()["id"] == "result-1"


def test_get_missing_delegation_result_is_not_found(client):
    with patch("cli_agent_orchestrator.api.main.result_service.read_result", return_value=None):
        response = client.get("/delegation-results/missing")
    assert response.status_code == 404


def test_list_delegation_results_forwards_filters(client):
    with patch(
        "cli_agent_orchestrator.api.main.result_service.list_results", return_value=[_result()]
    ) as listed:
        response = client.get("/delegation-results?terminal_id=parent&status=complete&limit=2")
    assert response.status_code == 200
    assert response.json()[0]["status"] == "complete"
    listed.assert_called_once_with("parent", None, "complete", 2, None)
