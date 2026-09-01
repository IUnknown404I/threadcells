from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from cli_agent_orchestrator.services.recovery_takeover_service import RecoveryTakeoverError

PAYLOAD = {
    "request_id": "00000000-0000-4000-8000-000000000001",
    "expected_authority_generation": "a" * 32,
    "expected_runtime_generation": "11111111-1111-4111-8111-111111111111",
    "agent_profile": "critical_sol_xhigh_owner",
    "provider": "codex",
    "owner_grant_launch_id": "launch-1",
}


def test_preview_requires_operator_authority(client):
    with patch(
        "cli_agent_orchestrator.api.main._require_operator",
        side_effect=HTTPException(status_code=401, detail="operator required"),
    ):
        response = client.get("/terminals/a11ce001/recovery-takeover/preview")
    assert response.status_code == 401


def test_preview_returns_exact_authority_and_dirty_state(client):
    preview = {
        "eligible": True,
        "reason_code": None,
        "runtime_absent": True,
        "terminal": {
            "id": "a11ce001",
            "writer_authority_generation": "a" * 32,
            "runtime_generation": "11111111-1111-4111-8111-111111111111",
        },
        "worktree": {"state": "dirty", "dirty": True, "reason_code": None},
        "consequence": "OLD_SUPERVISOR_PERMANENTLY_LOSES_WRITER_AUTHORITY",
    }
    with (
        patch("cli_agent_orchestrator.api.main._require_operator", return_value="owner"),
        patch(
            "cli_agent_orchestrator.api.main.recovery_takeover_service.preview_recovery_takeover",
            return_value=preview,
        ) as inspect,
    ):
        response = client.get("/terminals/a11ce001/recovery-takeover/preview")
    assert response.status_code == 200
    assert response.json() == preview
    inspect.assert_called_once_with(
        "a11ce001",
        expected_authority_generation=None,
        expected_runtime_generation=None,
    )


def test_create_takeover_passes_one_use_owner_grant(client):
    result = {
        "id": "takeover",
        "request_id": PAYLOAD["request_id"],
        "old_terminal_id": "a11ce001",
        "new_terminal_id": "b22ce001",
        "state": "completed",
        "failure_reason": None,
    }
    with (
        patch("cli_agent_orchestrator.api.main._require_operator", return_value="owner"),
        patch(
            "cli_agent_orchestrator.api.main.recovery_takeover_service.request_recovery_takeover",
            return_value=result,
        ) as takeover,
    ):
        response = client.post(
            "/terminals/a11ce001/recovery-takeover",
            headers={"X-ThreadCells-Owner-Grant": "secret-capability"},
            json=PAYLOAD,
        )
    assert response.status_code == 201
    assert response.json() == result
    assert takeover.call_args.kwargs["owner_grant_token"] == "secret-capability"
    assert takeover.call_args.kwargs["old_terminal_id"] == "a11ce001"


def test_create_takeover_maps_healthy_rejection(client):
    with (
        patch("cli_agent_orchestrator.api.main._require_operator", return_value="owner"),
        patch(
            "cli_agent_orchestrator.api.main.recovery_takeover_service.request_recovery_takeover",
            side_effect=RecoveryTakeoverError("RECOVERY_HEALTHY_RUNTIME_ACTIVE"),
        ),
    ):
        response = client.post(
            "/terminals/a11ce001/recovery-takeover",
            headers={"X-ThreadCells-Owner-Grant": "secret-capability"},
            json=PAYLOAD,
        )
    assert response.status_code == 409
    assert response.json()["detail"]["reason_code"] == "RECOVERY_HEALTHY_RUNTIME_ACTIVE"


def test_recovery_grant_is_bound_to_exact_target_and_generations(client):
    preview = {
        "eligible": True,
        "reason_code": None,
        "terminal": {
            "id": "a11ce001",
            "project_id": "project-1",
            "launch_worktree": "/repo",
        },
    }
    resolution = SimpleNamespace(
        provider_adapter_id="codex",
        owner_grant_required=True,
        profile_revision_id="profile-revision",
        provider_config_revision_id="provider-revision",
    )
    with (
        patch("cli_agent_orchestrator.api.main._require_operator", return_value="owner"),
        patch(
            "cli_agent_orchestrator.api.main.provider_manager.adapter_registry.get",
            return_value=object(),
        ),
        patch(
            "cli_agent_orchestrator.api.main.recovery_takeover_service.preview_recovery_takeover",
            return_value=preview,
        ),
        patch(
            "cli_agent_orchestrator.services.control_plane_registry.resolve_launch",
            return_value=resolution,
        ),
        patch(
            "cli_agent_orchestrator.services.operator_auth_service.mint_xhigh_launch_grant",
            return_value={
                "grant": "one-use-grant",
                "launch_id": "launch-1",
                "expires_in_seconds": 60,
            },
        ) as mint,
    ):
        response = client.post(
            "/operator/xhigh-grants",
            json={
                "agent_profile": "critical_sol_xhigh_owner",
                "provider": "codex",
                "launch_mode": "recovery_takeover",
                "target_terminal_id": "a11ce001",
                "expected_authority_generation": "a" * 32,
                "expected_runtime_generation": ("11111111-1111-4111-8111-111111111111"),
                "confirmed": True,
            },
        )
    assert response.status_code == 200
    assert mint.call_args.kwargs["expected_confirmation"] == ("RECOVERY TAKEOVER a11ce001")
    assert mint.call_args.kwargs["grant_scope"] == {
        "profile_revision_id": "profile-revision",
        "provider_config_revision_id": "provider-revision",
        "project_id": "project-1",
        "launch_mode": "recovery_takeover",
        "delegation_depth": 0,
        "target_terminal_id": "a11ce001",
        "expected_authority_generation": "a" * 32,
        "expected_runtime_generation": "11111111-1111-4111-8111-111111111111",
    }


def test_takeover_status_requires_operator_and_returns_durable_state(client):
    takeover_id = "b" * 32
    durable = {
        "id": takeover_id,
        "state": "dispatch_uncertain",
        "failure_reason": "RECOVERY_PROVIDER_DISPATCH_UNCERTAIN",
    }
    with (
        patch("cli_agent_orchestrator.api.main._require_operator", return_value="owner"),
        patch(
            "cli_agent_orchestrator.clients.database.get_recovery_takeover",
            return_value=durable,
        ),
    ):
        response = client.get(f"/recovery-takeovers/{takeover_id}")
    assert response.status_code == 200
    assert response.json() == durable

    with patch(
        "cli_agent_orchestrator.api.main._require_operator",
        side_effect=HTTPException(status_code=401, detail="operator required"),
    ):
        rejected = client.get(f"/recovery-takeovers/{takeover_id}")
    assert rejected.status_code == 401
