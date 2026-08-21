"""Hidden loopback C2 submit endpoint contract."""

from unittest.mock import patch


def _payload():
    return {
        "logical_turn_id": 41,
        "document": {
            "format": "v1",
            "summary": "complete",
            "body_markdown": "done",
            "changed_files": [],
            "checks": [{"command": "pytest", "outcome": "passed"}],
            "risks": [],
            "blockers": [],
        },
    }


def test_hidden_submit_requires_bearer_token_only(client):
    response = client.post("/_internal/delegation-results/handoff-v1", json=_payload())
    assert response.status_code == 401
    response = client.post(
        "/_internal/delegation-results/handoff-v1",
        json=_payload(),
        headers={"Authorization": "Bearer token"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_terminal_auth"


def test_hidden_submit_derives_terminal_from_bearer_and_accepts_only_document_and_turn(client):
    expected = {
        "accepted": True,
        "duplicate": False,
        "result_id": "result-1",
        "result_status": "awaiting",
        "submission_status": "recorded",
        "schema_version": 1,
        "content_sha256": "a" * 64,
    }
    with patch(
        "cli_agent_orchestrator.api.main.submit_handoff_result_v1", return_value=expected
    ) as submitted:
        response = client.post(
            "/_internal/delegation-results/handoff-v1",
            json=_payload(),
            headers={"Authorization": "Bearer token", "X-CAO-Terminal-ID": "forged-child"},
        )
    assert response.status_code == 200
    assert response.json() == expected
    args = submitted.call_args.args
    assert args[:2] == ("token", 41)
    assert args[2].format == "v1"


def test_hidden_submit_rejects_caller_selected_relation_identifiers(client):
    body = _payload()
    body["result_id"] = "forbidden"
    response = client.post(
        "/_internal/delegation-results/handoff-v1",
        json=body,
        headers={"Authorization": "Bearer token"},
    )
    assert response.status_code == 422
