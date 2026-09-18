from unittest.mock import patch

from fastapi import HTTPException

PAYLOAD = {
    "expected_workflow_id": 1186,
    "expected_workflow_turn_id": 3590,
    "expected_root_terminal_id": "33a70c20",
    "expected_effect_kind": "send_message",
    "confirmed": True,
}


def test_owner_retirement_requires_auth_and_forwards_exact_target(client):
    with patch(
        "cli_agent_orchestrator.api.main._require_operator",
        side_effect=HTTPException(status_code=401, detail="operator required"),
    ):
        denied = client.post("/api/v1/workflow-effects/2965/retire-indeterminate", json=PAYLOAD)
    assert denied.status_code == 401

    result = {
        "retired": True,
        "already_retired": False,
        "outcome": "unknown_preserved",
        "workflow_status": "cancelled",
    }
    with (
        patch(
            "cli_agent_orchestrator.api.main._require_operator",
            return_value="operator:test",
        ),
        patch(
            "cli_agent_orchestrator.clients.database.retire_indeterminate_workflow_effect",
            return_value=result,
        ) as retire,
    ):
        accepted = client.post("/api/v1/workflow-effects/2965/retire-indeterminate", json=PAYLOAD)
    assert accepted.status_code == 200
    assert accepted.json() == result
    retire.assert_called_once_with(
        2965,
        expected_workflow_id=1186,
        expected_workflow_turn_id=3590,
        expected_root_terminal_id="33a70c20",
        expected_effect_kind="send_message",
    )


def test_owner_retirement_requires_explicit_confirmation(client):
    with patch(
        "cli_agent_orchestrator.api.main._require_operator",
        side_effect=AssertionError("validation must reject before authorization"),
    ):
        response = client.post(
            "/api/v1/workflow-effects/2965/retire-indeterminate",
            json={**PAYLOAD, "confirmed": False},
        )
    assert response.status_code == 422
