"""Tests for settings API endpoints."""

from unittest.mock import patch

import pytest

from cli_agent_orchestrator.api.main import _admission_http_exception, app
from cli_agent_orchestrator.services.operations_service import AdmissionDenied


@pytest.mark.parametrize(
    ("reason_code", "http_status"),
    [
        ("WORKTREE_WRITER_LEASE_HELD", 423),
        ("WORKTREE_AUTHORITY_UNRECONCILED", 409),
        ("TOTAL_PROVIDER_CAPACITY_EXHAUSTED", 429),
        ("PROVIDER_EXECUTION_CAPACITY_EXHAUSTED", 429),
        ("RESIDENT_SUPERVISOR_CAPACITY_EXHAUSTED", 429),
        ("PROJECT_SUPERVISOR_ALREADY_RESIDENT", 409),
        ("WORK_CONTEXT_CAPACITY_EXHAUSTED", 429),
        ("RESOURCE_HEALTH_REJECTED", 503),
    ],
)
def test_admission_reason_codes_have_semantic_http_contract(reason_code, http_status):
    error = _admission_http_exception(AdmissionDenied(reason_code, {"resource_state": "GREEN"}))
    assert error.status_code == http_status
    assert error.detail == {
        "reason_code": reason_code,
        "status": {"resource_state": "GREEN"},
    }


def test_orchestration_capacity_is_backend_truth(client):
    projected = {
        "resource_state": "GREEN",
        "reasons": [],
        "resident_supervisors": {"active": 5, "limit": 5, "available": 0, "certain": True},
        "provider_executions": {"active": 3, "limit": 3, "available": 0, "certain": True},
        "provider_contexts": {"active": 3, "limit": 3, "available": 0, "certain": True},
        "work_contexts": {"active": 2, "limit": 2, "available": 0, "certain": True},
        "heavy_executions": {"active": 1, "limit": 1, "available": 0, "waiting": None},
        "memory": {"available_mib": 4096, "swap_total_mib": 1024, "swap_free_mib": 1024},
        "root_disk": {"used_percent": 40.0, "free_gib": 50.0},
        "memory_pressure": {"some_avg10": 0.0, "full_avg10": 0.0},
        "cpu_load": {"one_minute": 1.75, "cpu_count": 8},
        "housekeeping": {"ok": True},
    }
    with patch(
        "cli_agent_orchestrator.api.main.get_resource_status", return_value=projected
    ) as probe:
        response = client.get("/settings/orchestration-capacity")
    assert response.status_code == 200
    assert response.json() == projected
    probe.assert_called_once_with()


class TestGetAgentDirsEndpoint:
    """Tests for GET /settings/agent-dirs endpoint."""

    def test_returns_agent_dirs_and_extra_dirs(self, client):
        """GET /settings/agent-dirs returns both agent_dirs and extra_dirs."""
        mock_agent_dirs = {
            "kiro_cli": "/home/user/.kiro/agents",
            "q_cli": "/home/user/.aws/amazonq/cli-agents",
            "claude_code": "/custom/claude",
            "codex": "/custom/codex",
        }
        mock_extra_dirs = ["/extra/dir1", "/extra/dir2"]

        with (
            patch(
                "cli_agent_orchestrator.services.settings_service.get_agent_dirs",
                return_value=mock_agent_dirs,
            ),
            patch(
                "cli_agent_orchestrator.services.settings_service.get_extra_agent_dirs",
                return_value=mock_extra_dirs,
            ),
        ):
            response = client.get("/settings/agent-dirs")

        assert response.status_code == 200
        data = response.json()
        assert "agent_dirs" in data
        assert "extra_dirs" in data
        assert data["agent_dirs"] == mock_agent_dirs
        assert data["extra_dirs"] == mock_extra_dirs

    def test_returns_empty_extra_dirs_when_none(self, client):
        """GET /settings/agent-dirs returns empty extra_dirs when none configured."""
        mock_agent_dirs = {
            "kiro_cli": "/path",
            "q_cli": "/path2",
            "claude_code": "/p3",
            "codex": "/p4",
        }

        with (
            patch(
                "cli_agent_orchestrator.services.settings_service.get_agent_dirs",
                return_value=mock_agent_dirs,
            ),
            patch(
                "cli_agent_orchestrator.services.settings_service.get_extra_agent_dirs",
                return_value=[],
            ),
        ):
            response = client.get("/settings/agent-dirs")

        assert response.status_code == 200
        data = response.json()
        assert data["extra_dirs"] == []


class TestSetAgentDirsEndpoint:
    """Tests for POST /settings/agent-dirs endpoint."""

    def test_updates_agent_dirs_and_returns_result(self, client):
        """POST /settings/agent-dirs updates agent_dirs and returns new settings."""
        updated_dirs = {
            "kiro_cli": "/new/kiro",
            "q_cli": "/default/q",
            "claude_code": "/default/claude",
            "codex": "/default/codex",
        }

        with (
            patch(
                "cli_agent_orchestrator.services.settings_service.set_agent_dirs",
                return_value=updated_dirs,
            ),
            patch(
                "cli_agent_orchestrator.services.settings_service.get_extra_agent_dirs",
                return_value=["/existing/extra"],
            ),
            patch(
                "cli_agent_orchestrator.services.settings_service.get_agent_dirs",
                return_value=updated_dirs,
            ),
        ):
            response = client.post(
                "/settings/agent-dirs",
                json={"agent_dirs": {"kiro_cli": "/new/kiro"}},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["agent_dirs"] == updated_dirs
        assert data["extra_dirs"] == ["/existing/extra"]

    def test_updates_extra_dirs(self, client):
        """POST /settings/agent-dirs can update extra_dirs."""
        with (
            patch(
                "cli_agent_orchestrator.services.settings_service.set_extra_agent_dirs",
                return_value=["/new/extra"],
            ),
            patch(
                "cli_agent_orchestrator.services.settings_service.get_extra_agent_dirs",
                return_value=["/new/extra"],
            ),
            patch(
                "cli_agent_orchestrator.services.settings_service.get_agent_dirs",
                return_value={"codex": "/provider/codex"},
            ),
        ):
            response = client.post(
                "/settings/agent-dirs",
                json={"extra_dirs": ["/new/extra"]},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["extra_dirs"] == ["/new/extra"]

    def test_updates_both_agent_dirs_and_extra_dirs(self, client):
        """POST /settings/agent-dirs can update both in one request."""
        updated_dirs = {
            "kiro_cli": "/updated",
            "q_cli": "/default/q",
            "claude_code": "/default/claude",
            "codex": "/default/codex",
        }

        with (
            patch(
                "cli_agent_orchestrator.services.settings_service.set_agent_dirs",
                return_value=updated_dirs,
            ),
            patch(
                "cli_agent_orchestrator.services.settings_service.set_extra_agent_dirs",
                return_value=["/extra1"],
            ),
            patch(
                "cli_agent_orchestrator.services.settings_service.get_extra_agent_dirs",
                return_value=["/extra1"],
            ),
            patch(
                "cli_agent_orchestrator.services.settings_service.get_agent_dirs",
                return_value=updated_dirs,
            ),
        ):
            response = client.post(
                "/settings/agent-dirs",
                json={
                    "agent_dirs": {"kiro_cli": "/updated"},
                    "extra_dirs": ["/extra1"],
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["agent_dirs"] == updated_dirs
        assert data["extra_dirs"] == ["/extra1"]

    def test_empty_body_returns_defaults(self, client):
        """POST /settings/agent-dirs with empty body returns empty agent_dirs and existing extra."""
        with (
            patch(
                "cli_agent_orchestrator.services.settings_service.get_extra_agent_dirs",
                return_value=[],
            ),
            patch(
                "cli_agent_orchestrator.services.settings_service.get_agent_dirs",
                return_value={},
            ),
        ):
            response = client.post("/settings/agent-dirs", json={})

        assert response.status_code == 200
        data = response.json()
        assert data["agent_dirs"] == {}
        assert data["extra_dirs"] == []

    def test_empty_agent_dirs_is_an_explicit_update(self, client):
        """An empty provider map is not confused with an omitted field."""
        with (
            patch("cli_agent_orchestrator.services.settings_service.set_agent_dirs") as setter,
            patch(
                "cli_agent_orchestrator.services.settings_service.get_agent_dirs",
                return_value={},
            ),
            patch(
                "cli_agent_orchestrator.services.settings_service.get_extra_agent_dirs",
                return_value=[],
            ),
        ):
            response = client.post("/settings/agent-dirs", json={"agent_dirs": {}})

        assert response.status_code == 200
        setter.assert_called_once_with({})
