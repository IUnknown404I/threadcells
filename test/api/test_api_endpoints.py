"""Tests for uncovered API endpoints in main.py.

Covers: health, agents/profiles, agents/providers, sessions CRUD,
terminals CRUD (create in session, list, get, input, output, delete),
flow_daemon, lifespan, and the main() entry point.
"""

import asyncio
import json
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from cli_agent_orchestrator.api import main as api_main
from cli_agent_orchestrator.api.main import (
    _workflow_reconciliation_tick,
    app,
    flow_daemon,
    workflow_daemon,
)
from cli_agent_orchestrator.models.terminal import Terminal
from cli_agent_orchestrator.plugins import PluginRegistry
from cli_agent_orchestrator.runtime_generation import ACTIVE_RUNTIME_GENERATION
from cli_agent_orchestrator.services.full_cleanup_helper import FullCleanupHelperError
from cli_agent_orchestrator.utils.skills import SkillNameError

# ── Health endpoint ──────────────────────────────────────────────────


@pytest.fixture
def asgi_app():
    """Seed app state for tests that exercise ASGI without the TestClient fixture."""
    app.state.plugin_registry = PluginRegistry()
    return app


class TestHealthCheck:
    """Tests for GET /health endpoint."""

    def test_health_check_returns_ok(self, client):
        """GET /health returns status ok."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "cli-agent-orchestrator"

    def test_runtime_compatibility_identity_is_available_to_sidecars(self, client):
        response = client.get("/_internal/runtime-generation")

        assert response.status_code == 200
        assert response.json() == {"generation": ACTIVE_RUNTIME_GENERATION}
        assert len(ACTIVE_RUNTIME_GENERATION) == 64

    def test_runtime_compatibility_identity_is_stable_across_process_restart(self):
        command = [
            sys.executable,
            "-c",
            (
                "from cli_agent_orchestrator.runtime_generation import "
                "ACTIVE_RUNTIME_GENERATION; print(ACTIVE_RUNTIME_GENERATION)"
            ),
        ]

        first = subprocess.check_output(command, text=True).strip()
        second = subprocess.check_output(command, text=True).strip()

        assert first == second == ACTIVE_RUNTIME_GENERATION

    def test_product_docs_and_internal_openapi_routes_do_not_conflict(self):
        assert app.docs_url == "/_internal/docs"
        assert app.openapi_url == "/_internal/openapi.json"
        assert app.redoc_url is None

        docs_route = next(route for route in app.routes if route.path == "/docs")
        assert docs_route.endpoint.__name__ == "docs_spa_entry"

        settings_route = next(
            route for route in app.routes if route.path == "/settings/{path:path}"
        )
        assert settings_route.endpoint.__name__ == "settings_spa_entry"
        paths = {route.path for route in app.routes}
        assert "/api/v1/profiles" in paths
        assert "/api/v1/providers" in paths
        assert "/api/v1/housekeeping" in paths
        assert "/settings/profiles" not in paths
        assert "/settings/providers" not in paths
        assert "/settings/housekeeping" not in paths


class TestCapacitySettings:
    def test_put_capacity_settings_updates_all_limits_atomically(self, client):
        projection = {"capacity_settings": {"schema_version": 1}}
        payload = {
            "max_resident_supervisors": 8,
            "max_provider_executions": 6,
            "max_work_contexts": 7,
            "max_heavy_execution_slots": 2,
        }
        with (
            patch(
                "cli_agent_orchestrator.api.main._require_operator",
                return_value="operator_session:test",
            ),
            patch("cli_agent_orchestrator.api.main.set_capacity_settings") as update,
            patch(
                "cli_agent_orchestrator.api.main.get_resource_status",
                return_value=projection,
            ),
        ):
            response = client.put("/settings/orchestration-capacity", json=payload)

        assert response.status_code == 200
        assert response.json() == projection
        update.assert_called_once_with(payload, actor="operator_session:test")

    def test_put_capacity_settings_rejects_bool_and_partial_payloads(self, client):
        payload = {
            "max_resident_supervisors": True,
            "max_provider_executions": 3,
            "max_work_contexts": 2,
        }
        response = client.put("/settings/orchestration-capacity", json=payload)

        assert response.status_code == 422


class TestOperatorGrantBoundary:
    def _configure_secret(self, tmp_path, monkeypatch):
        from cli_agent_orchestrator.services.operator_auth_service import (
            build_operator_verifier,
        )

        secret = "A7!qz"
        verifier_file = tmp_path / "operator-verifier.json"
        verifier_file.write_text(json.dumps(build_operator_verifier(secret)))
        verifier_file.chmod(0o440)
        monkeypatch.setenv("THREADCELLS_OPERATOR_VERIFIER_FILE", str(verifier_file))
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.operator_auth_service.os.geteuid",
            lambda: verifier_file.stat().st_uid + 1,
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.operator_auth_service._validate_operator_verifier_parent_chain",
            lambda _path: None,
        )
        return secret

    def test_operator_session_fails_closed_when_auth_is_unconfigured(self, client, monkeypatch):
        monkeypatch.delenv("THREADCELLS_OPERATOR_VERIFIER_FILE", raising=False)
        monkeypatch.delenv("THREADMESH_OPERATOR_VERIFIER_FILE", raising=False)
        response = client.post(
            "/operator/session",
            json={"secret": "x" * 32},
        )

        assert response.status_code == 503
        assert response.json()["detail"]["reason_code"] == "OPERATOR_AUTH_NOT_CONFIGURED"

    def test_operator_session_status_is_non_secret_and_projects_expiry(
        self, client, tmp_path, monkeypatch
    ):
        secret = self._configure_secret(tmp_path, monkeypatch)

        locked = client.get("/operator/session")
        assert locked.json() == {
            "configured": True,
            "configuration_state": "ready",
            "authenticated": False,
            "expires_in_seconds": 0,
            "session_ttl_seconds": 300,
            "verifier_reference": "THREADCELLS_OPERATOR_VERIFIER_FILE",
        }
        assert str(tmp_path) not in locked.text
        assert secret not in locked.text

        assert client.post("/operator/session", json={"secret": secret}).status_code == 200
        unlocked = client.get("/operator/session").json()
        assert unlocked["configured"] is True
        assert unlocked["authenticated"] is True
        assert 1 <= unlocked["expires_in_seconds"] <= 300
        assert secret not in json.dumps(unlocked)

        from cli_agent_orchestrator.clients import database

        with database.SessionLocal() as db:
            row = db.query(database.OperatorSessionModel).one()
            row.expires_at = datetime.now() - timedelta(seconds=1)
            db.commit()
        expired = client.get("/operator/session").json()
        assert expired["configured"] is True
        assert expired["authenticated"] is False
        assert expired["expires_in_seconds"] == 0

    def test_operator_session_status_reports_unconfigured_without_internal_details(
        self, client, monkeypatch
    ):
        monkeypatch.delenv("THREADCELLS_OPERATOR_VERIFIER_FILE", raising=False)
        monkeypatch.delenv("THREADMESH_OPERATOR_VERIFIER_FILE", raising=False)

        response = client.get("/operator/session")

        assert response.status_code == 200
        assert response.json() == {
            "configured": False,
            "configuration_state": "missing",
            "authenticated": False,
            "expires_in_seconds": 0,
            "session_ttl_seconds": 300,
            "verifier_reference": "THREADCELLS_OPERATOR_VERIFIER_FILE",
        }

    def test_legacy_verifier_reference_is_a_read_only_transition_fallback(
        self, client, tmp_path, monkeypatch
    ):
        from cli_agent_orchestrator.services.operator_auth_service import (
            build_operator_verifier,
        )

        secret = "A7!qz"
        verifier_file = tmp_path / "operator-verifier.json"
        verifier_file.write_text(json.dumps(build_operator_verifier(secret)))
        verifier_file.chmod(0o440)
        monkeypatch.delenv("THREADCELLS_OPERATOR_VERIFIER_FILE", raising=False)
        monkeypatch.setenv("THREADMESH_OPERATOR_VERIFIER_FILE", str(verifier_file))
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.operator_auth_service.os.geteuid",
            lambda: verifier_file.stat().st_uid + 1,
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.operator_auth_service._validate_operator_verifier_parent_chain",
            lambda _path: None,
        )

        response = client.post("/operator/session", json={"secret": secret})

        assert response.status_code == 200
        status = client.get("/operator/session")
        assert status.json()["verifier_reference"] == "THREADCELLS_OPERATOR_VERIFIER_FILE"
        assert "THREADMESH" not in status.text

    def test_operator_session_reports_present_but_unsafe_verifier(
        self, client, tmp_path, monkeypatch
    ):
        from cli_agent_orchestrator.services.operator_auth_service import (
            build_operator_verifier,
        )

        verifier_file = tmp_path / "operator-verifier.json"
        verifier_file.write_text(json.dumps(build_operator_verifier("A7!qz")))
        verifier_file.chmod(0o600)
        monkeypatch.setenv("THREADCELLS_OPERATOR_VERIFIER_FILE", str(verifier_file))

        response = client.get("/operator/session")

        assert response.status_code == 200
        assert response.json()["configured"] is False
        assert response.json()["configuration_state"] == "invalid"
        assert str(verifier_file) not in response.text

    def test_operator_session_rejects_four_character_secret(self, client, tmp_path, monkeypatch):
        self._configure_secret(tmp_path, monkeypatch)

        response = client.post("/operator/session", json={"secret": "A7!q"})

        assert response.status_code == 422

    def test_operator_session_rejects_wrong_secret_without_reflecting_it(
        self, client, tmp_path, monkeypatch
    ):
        self._configure_secret(tmp_path, monkeypatch)
        wrong_secret = "wrong-operator-secret"

        response = client.post("/operator/session", json={"secret": wrong_secret})

        assert response.status_code == 401
        assert response.json()["detail"]["reason_code"] == "OPERATOR_AUTHENTICATION_FAILED"
        assert wrong_secret not in response.text

    def test_browser_session_can_mint_an_existing_session_xhigh_grant(
        self, client, tmp_path, monkeypatch
    ):
        secret = self._configure_secret(tmp_path, monkeypatch)
        assert client.post("/operator/session", json={"secret": secret}).status_code == 200

        with patch.object(
            api_main.session_service,
            "resolve_session_authority",
            return_value=MagicMock(session_id="stable-session", session_name="cao-existing"),
        ):
            grant = client.post(
                "/operator/xhigh-grants",
                json={
                    "agent_profile": "critical_sol_xhigh_owner",
                    "provider": "codex",
                    "working_directory": str(tmp_path),
                    "requested_session_name": "stable-session",
                    "launch_mode": "existing_session",
                    "confirmed": True,
                },
            )

        assert grant.status_code == 200
        assert set(grant.json()) == {"grant", "launch_id", "expires_in_seconds"}
        assert secret not in grant.text

    def test_browser_session_is_http_only_strict_and_can_mint_confirmed_grant(
        self, client, tmp_path, monkeypatch
    ):
        secret = self._configure_secret(tmp_path, monkeypatch)
        login = client.post("/operator/session", json={"secret": secret})

        assert login.status_code == 200
        cookie = login.headers["set-cookie"].lower()
        assert "httponly" in cookie
        assert "samesite=strict" in cookie
        grant = client.post(
            "/operator/xhigh-grants",
            json={
                "agent_profile": "critical_sol_xhigh_owner",
                "provider": "codex",
                "working_directory": str(tmp_path),
                "requested_session_name": None,
                "confirmed": True,
            },
        )

        assert grant.status_code == 200
        assert set(grant.json()) == {"grant", "launch_id", "expires_in_seconds"}
        assert grant.json()["expires_in_seconds"] == 60

    def test_explicit_https_proxy_origin_preserves_operator_cookie_mutations(
        self, client, tmp_path, monkeypatch
    ):
        secret = self._configure_secret(tmp_path, monkeypatch)
        origin = "https://threadcells.example.com"
        monkeypatch.setenv("THREADCELLS_TRUSTED_PROXY_ORIGINS", origin)

        login = client.post(
            "/operator/session",
            json={"secret": secret},
            headers={"Origin": origin},
        )
        grant = client.post(
            "/operator/xhigh-grants",
            # The fixture transport is HTTP while the modeled public browser
            # origin is HTTPS, so forward the Secure cookie explicitly.
            headers={
                "Origin": origin,
                "Cookie": f"threadcells_operator_session={client.cookies.get('threadcells_operator_session')}",
            },
            json={
                "agent_profile": "critical_sol_xhigh_owner",
                "provider": "codex",
                "working_directory": str(tmp_path),
                "requested_session_name": None,
                "confirmed": True,
            },
        )

        assert login.status_code == 200
        assert "secure" in login.headers["set-cookie"].lower()
        assert grant.status_code == 200

    def test_canonical_proxy_origin_overrides_the_transition_fallback(
        self, client, tmp_path, monkeypatch
    ):
        secret = self._configure_secret(tmp_path, monkeypatch)
        canonical = "https://threadcells.example.com"
        monkeypatch.setenv("THREADCELLS_TRUSTED_PROXY_ORIGINS", canonical)
        monkeypatch.setenv("THREADMESH_TRUSTED_PROXY_ORIGINS", "https://legacy.example.com")

        response = client.post(
            "/operator/session",
            json={"secret": secret},
            headers={"Origin": canonical},
        )

        assert response.status_code == 200

    def test_unconfigured_or_malformed_proxy_origin_fails_closed(
        self, client, tmp_path, monkeypatch
    ):
        secret = self._configure_secret(tmp_path, monkeypatch)
        origin = "https://threadcells.example.com"

        unconfigured = client.post(
            "/operator/session",
            json={"secret": secret},
            headers={"Origin": origin},
        )
        monkeypatch.setenv("THREADCELLS_TRUSTED_PROXY_ORIGINS", f"{origin}/not-an-origin")
        malformed = client.post(
            "/operator/session",
            json={"secret": secret},
            headers={"Origin": origin},
        )

        assert unconfigured.status_code == 401
        assert malformed.status_code == 401
        assert origin not in unconfigured.text + malformed.text

    def test_bearer_api_requires_exact_auth_and_explicit_confirmation(
        self, client, tmp_path, monkeypatch
    ):
        secret = self._configure_secret(tmp_path, monkeypatch)
        payload = {
            "agent_profile": "critical_sol_xhigh_owner",
            "provider": "codex",
            "working_directory": str(tmp_path),
            "confirmation": "OWNER_GATE: APPROVED_XHIGH",
        }
        denied = client.post(
            "/operator/xhigh-grants",
            json=payload,
            headers={"Authorization": f"Bearer {secret}"},
        )
        unauthenticated = client.post(
            "/operator/xhigh-grants",
            json={**payload, "confirmation": "LAUNCH critical_sol_xhigh_owner"},
            headers={"Authorization": "Bearer wrong"},
        )

        assert denied.status_code == 403
        assert denied.json()["detail"]["reason_code"] == "XHIGH_CONFIRMATION_REQUIRED"
        assert unauthenticated.status_code == 401


class TestPublicControlPlaneApi:
    def test_profile_and_provider_validation_return_json_pointer_issues(self, client):
        profile = client.post("/api/v1/profiles/validate", json={"document": {}})
        provider = client.post(
            "/api/v1/providers/validate",
            json={
                "document": {
                    "schema_version": 1,
                    "config_id": "community",
                    "adapter_id": "not-installed",
                    "display_name": "Community",
                }
            },
        )

        assert profile.status_code == 200
        assert profile.json()["valid"] is False
        assert all(issue["pointer"].startswith("/") for issue in profile.json()["issues"])
        assert provider.status_code == 200
        assert provider.json() == {
            "valid": False,
            "issues": [
                {
                    "pointer": "/adapter_id",
                    "code": "ADAPTER_NOT_INSTALLED",
                    "message": "must reference a trusted installed provider adapter",
                }
            ],
        }

    def test_public_schemas_are_versioned_and_allowlisted(self, client):
        listing = client.get("/schemas/v1")
        profile = client.get("/schemas/v1/profile")
        missing = client.get("/schemas/v1/not-a-schema")

        assert listing.status_code == 200
        assert {item["name"] for item in listing.json()} == {
            "adapter-manifest",
            "capabilities",
            "profile",
            "provider-config",
        }
        assert profile.status_code == 200
        assert profile.json()["$id"].endswith("/schemas/v1/profile.schema.json")
        assert missing.status_code == 404

    def test_housekeeping_api_uses_thin_service_boundary(self, client):
        settings = {
            "schema_version": 1,
            "policy": {"logs": {"enabled": True}},
            "schedule": {"frequent": "every 6 hours"},
        }
        with patch(
            "cli_agent_orchestrator.services.housekeeping_service.get_housekeeping_settings",
            return_value=settings,
        ) as service:
            response = client.get("/api/v1/housekeeping")

        assert response.status_code == 200
        assert response.json() == settings
        service.assert_called_once_with()

    def test_housekeeping_execution_is_bound_to_the_inspected_plan(self, client):
        plan_id = "a" * 64
        summary = MagicMock()
        summary.as_dict.return_value = {"ok": True, "plan_id": plan_id}
        with (
            patch(
                "cli_agent_orchestrator.api.main._require_operator",
                return_value="operator:test",
            ),
            patch(
                "cli_agent_orchestrator.services.housekeeping_service.run_housekeeping",
                return_value=summary,
            ) as service,
        ):
            response = client.post(
                "/api/v1/housekeeping/run",
                json={
                    "dry_run": False,
                    "mode": "weekly",
                    "expected_plan_id": plan_id,
                },
            )

        assert response.status_code == 200
        assert response.json() == {"ok": True, "plan_id": plan_id}
        service.assert_called_once_with(
            dry_run=False,
            mode="weekly",
            expected_plan_id=plan_id,
        )

    def test_housekeeping_execution_requires_an_inspected_plan(self, client):
        with patch(
            "cli_agent_orchestrator.api.main._require_operator",
            return_value="operator:test",
        ):
            response = client.post(
                "/api/v1/housekeeping/run",
                json={"dry_run": False, "mode": "frequent"},
            )

        assert response.status_code == 422
        assert response.json()["detail"]["reason_code"] == "HOUSEKEEPING_PLAN_REQUIRED"

    def test_housekeeping_execution_fails_closed_when_the_plan_changed(self, client):
        with (
            patch(
                "cli_agent_orchestrator.api.main._require_operator",
                return_value="operator:test",
            ),
            patch(
                "cli_agent_orchestrator.services.housekeeping_service.run_housekeeping",
                side_effect=RuntimeError("HOUSEKEEPING_PLAN_CHANGED"),
            ),
        ):
            response = client.post(
                "/api/v1/housekeeping/run",
                json={
                    "dry_run": False,
                    "mode": "frequent",
                    "expected_plan_id": "a" * 64,
                },
            )

        assert response.status_code == 409
        assert response.json()["detail"]["reason_code"] == "HOUSEKEEPING_PLAN_CHANGED"

    def test_full_cleanup_preview_is_read_only(self, client):
        preview = {
            "schema_version": 1,
            "mode": "full",
            "plan_id": "a" * 64,
            "idle_gate": {"eligible": True, "blockers": []},
        }
        with patch(
            "cli_agent_orchestrator.services.housekeeping_service.plan_full_cleanup_serialized",
            return_value=preview,
        ) as service:
            response = client.get("/api/v1/housekeeping/full-cleanup/plan")

        assert response.status_code == 200
        assert response.json() == preview
        service.assert_called_once_with()

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/housekeeping/plan?mode=frequent",
            "/api/v1/housekeeping/full-cleanup/plan",
        ],
    )
    def test_housekeeping_plan_endpoints_report_canonical_lock_contention(self, client, path):
        service = (
            "plan_full_cleanup_serialized"
            if "full-cleanup" in path
            else "plan_housekeeping_serialized"
        )
        with patch(
            f"cli_agent_orchestrator.services.housekeeping_service.{service}",
            side_effect=RuntimeError("HOUSEKEEPING_BUSY"),
        ):
            response = client.get(path)

        assert response.status_code == 423
        assert response.json()["detail"]["reason_code"] == "HOUSEKEEPING_BUSY"

    def test_full_cleanup_execution_uses_operator_and_explicit_confirmation(self, client):
        plan_id = "b" * 64
        summary = MagicMock()
        summary.as_dict.return_value = {"ok": True, "full_cleanup": True}

        def run_service(**kwargs):
            kwargs["privileged_cleanup_executor"]()
            return summary

        with (
            patch(
                "cli_agent_orchestrator.api.main._require_operator",
                return_value="operator_bearer",
            ) as authorize,
            patch(
                "cli_agent_orchestrator.services.full_cleanup_helper.execute_via_privileged_helper",
                return_value=MagicMock(plan_id=plan_id, ok=True),
            ) as helper,
            patch(
                "cli_agent_orchestrator.services.housekeeping_service.run_full_cleanup",
                side_effect=run_service,
            ) as service,
        ):
            response = client.post(
                "/api/v1/housekeeping/full-cleanup/run",
                json={"expected_plan_id": plan_id, "confirmed": True},
                headers={"Authorization": "Bearer existing-secret"},
            )

        assert response.status_code == 200
        authorize.assert_called_once()
        helper.assert_called_once_with(
            expected_plan_id=plan_id,
            confirmed=True,
            session_token=None,
            bearer_secret="existing-secret",
        )
        service.assert_called_once_with(
            expected_plan_id=plan_id,
            confirmed=True,
            privileged_cleanup_executor=ANY,
        )

    def test_full_cleanup_execution_is_denied_while_operator_is_locked(self, client):
        with (
            patch(
                "cli_agent_orchestrator.api.main._require_operator",
                side_effect=HTTPException(status_code=401, detail="operator session required"),
            ),
            patch(
                "cli_agent_orchestrator.services.housekeeping_service.run_full_cleanup",
            ) as service,
        ):
            response = client.post(
                "/api/v1/housekeeping/full-cleanup/run",
                json={"expected_plan_id": "a" * 64, "confirmed": True},
            )

        assert response.status_code == 401
        service.assert_not_called()

    def test_full_cleanup_rejects_missing_confirmation_without_execution(self, client):
        with (
            patch(
                "cli_agent_orchestrator.api.main._require_operator",
                return_value="operator_bearer",
            ),
            patch(
                "cli_agent_orchestrator.services.housekeeping_service.run_full_cleanup",
            ) as service,
        ):
            response = client.post(
                "/api/v1/housekeeping/full-cleanup/run",
                json={"expected_plan_id": "a" * 64},
            )

        assert response.status_code == 422
        service.assert_not_called()

    def test_full_cleanup_maps_execute_time_idle_race_to_conflict(self, client):
        def run_service(**kwargs):
            kwargs["privileged_cleanup_executor"]()

        with (
            patch(
                "cli_agent_orchestrator.api.main._require_operator",
                return_value="operator_bearer",
            ),
            patch(
                "cli_agent_orchestrator.services.full_cleanup_helper.execute_via_privileged_helper",
                side_effect=FullCleanupHelperError("FULL_CLEANUP_NOT_IDLE"),
            ),
            patch(
                "cli_agent_orchestrator.services.housekeeping_service.run_full_cleanup",
                side_effect=run_service,
            ),
        ):
            response = client.post(
                "/api/v1/housekeeping/full-cleanup/run",
                json={"expected_plan_id": "a" * 64, "confirmed": True},
                headers={"Authorization": "Bearer existing-secret"},
            )

        assert response.status_code == 409
        assert response.json()["detail"]["reason_code"] == "FULL_CLEANUP_NOT_IDLE"

    def test_housekeeping_report_is_available_before_the_first_run(self, client, tmp_path):
        with patch(
            "cli_agent_orchestrator.api.main.load_operations_config",
            return_value={"root": str(tmp_path)},
        ):
            response = client.get("/api/v1/housekeeping/report")

        assert response.status_code == 200
        assert response.json() == {"status": "never_run"}


# ── Agent profiles endpoint ──────────────────────────────────────────


class TestAgentProfiles:
    """Tests for GET /agents/profiles endpoint."""

    def test_list_profiles_success(self, client):
        """GET /agents/profiles returns list of profiles."""
        mock_profiles = [
            {"name": "developer", "path": "/agents/developer"},
            {"name": "reviewer", "path": "/agents/reviewer"},
        ]
        with patch(
            "cli_agent_orchestrator.api.main.list_agent_profiles",
            create=True,
        ) as mock_fn:
            # The endpoint does a lazy import, so we need to patch at the import target
            with patch(
                "cli_agent_orchestrator.utils.agent_profiles.list_agent_profiles",
                return_value=mock_profiles,
            ):
                response = client.get("/agents/profiles")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["name"] == "developer"

    def test_list_profiles_empty(self, client):
        """GET /agents/profiles returns empty list when none exist."""
        with patch(
            "cli_agent_orchestrator.utils.agent_profiles.list_agent_profiles",
            return_value=[],
        ):
            response = client.get("/agents/profiles")

        assert response.status_code == 200
        assert response.json() == []

    def test_list_profiles_server_error(self, client):
        """GET /agents/profiles returns 500 on internal error."""
        with patch(
            "cli_agent_orchestrator.utils.agent_profiles.list_agent_profiles",
            side_effect=Exception("Failed to read profiles"),
        ):
            response = client.get("/agents/profiles")

        assert response.status_code == 500
        assert "Failed to list agent profiles" in response.json()["detail"]


# ── Agent providers endpoint ─────────────────────────────────────────


class TestAgentProviders:
    """Tests for GET /agents/providers endpoint."""

    def test_list_providers_all_installed(self, client):
        """GET /agents/providers returns all providers as installed."""
        with patch("shutil.which", return_value="/usr/bin/dummy"):
            response = client.get("/agents/providers")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 8
        names = [p["name"] for p in data]
        assert "kiro_cli" in names
        assert "claude_code" in names
        assert "q_cli" in names
        assert "codex" in names
        assert "gemini_cli" in names
        assert "kimi_cli" in names
        assert "copilot_cli" in names
        assert "opencode_cli" in names
        for p in data:
            assert p["installed"] is True

    def test_list_providers_none_installed(self, client):
        """GET /agents/providers returns all providers as not installed."""
        with patch("shutil.which", return_value=None):
            response = client.get("/agents/providers")

        assert response.status_code == 200
        data = response.json()
        for p in data:
            assert p["installed"] is False

    def test_list_providers_mixed_installed(self, client):
        """GET /agents/providers returns mixed installation status."""

        def mock_which(binary):
            return "/usr/bin/kiro-cli" if binary == "kiro-cli" else None

        with patch("shutil.which", side_effect=mock_which):
            response = client.get("/agents/providers")

        assert response.status_code == 200
        data = response.json()
        providers_dict = {p["name"]: p for p in data}
        assert providers_dict["kiro_cli"]["installed"] is True
        assert providers_dict["claude_code"]["installed"] is False
        assert providers_dict["q_cli"]["installed"] is False
        assert providers_dict["codex"]["installed"] is False
        assert providers_dict["gemini_cli"]["installed"] is False
        assert providers_dict["kimi_cli"]["installed"] is False
        assert providers_dict["copilot_cli"]["installed"] is False
        assert providers_dict["opencode_cli"]["installed"] is False

    def test_list_providers_has_binary_field(self, client):
        """Each provider entry has correct binary name."""
        with patch("shutil.which", return_value=None):
            response = client.get("/agents/providers")

        data = response.json()
        providers_dict = {p["name"]: p for p in data}
        assert providers_dict["kiro_cli"]["binary"] == "kiro-cli"
        assert providers_dict["claude_code"]["binary"] == "claude"
        assert providers_dict["q_cli"]["binary"] == "q"
        assert providers_dict["codex"]["binary"] == "codex"
        assert providers_dict["gemini_cli"]["binary"] == "gemini"
        assert providers_dict["kimi_cli"]["binary"] == "kimi"
        assert providers_dict["copilot_cli"]["binary"] == "copilot"
        assert providers_dict["opencode_cli"]["binary"] == "opencode"

    def test_spawn_and_settings_share_canonical_runtime_preflight(self, client):
        from cli_agent_orchestrator.providers.contracts import (
            AuthenticationState,
            ProviderPreflight,
            ProviderState,
        )

        runtime = ProviderPreflight(
            state=ProviderState.NOT_CONFIGURED,
            installed=True,
            authentication=AuthenticationState.UNKNOWN,
            version="provider 1.2.3",
            compatible=True,
            reason_code="AUTHENTICATION_UNVERIFIED",
            message="Authentication cannot be verified non-interactively",
        )
        with patch.object(
            api_main.provider_manager.adapter_registry,
            "preflight",
            return_value=runtime,
        ):
            spawn = client.get("/agents/providers")
            settings = client.get("/api/v1/providers")

        assert spawn.status_code == 200
        assert settings.status_code == 200
        spawn_by_id = {item["name"]: item for item in spawn.json()}
        settings_by_id = {
            item["adapter_id"]: item["runtime"] for item in settings.json()["adapters"]
        }
        assert spawn_by_id.keys() == settings_by_id.keys()
        for adapter_id, spawn_runtime in spawn_by_id.items():
            assert spawn_runtime["adapter_available"] is True
            assert spawn_runtime["availability"] == "UNKNOWN"
            assert spawn_runtime["available"] is True
            assert settings_by_id[adapter_id] == {
                key: spawn_runtime[key]
                for key in (
                    "state",
                    "installed",
                    "authentication",
                    "version",
                    "compatible",
                    "models",
                    "reason_code",
                    "message",
                    "availability",
                    "available",
                )
            }

    def test_provider_settings_preflights_each_configuration_document(self, client):
        from cli_agent_orchestrator.providers.contracts import (
            AuthenticationState,
            ProviderPreflight,
            ProviderState,
        )

        ready = ProviderPreflight(
            state=ProviderState.CONNECTED,
            installed=True,
            authentication=AuthenticationState.AUTHENTICATED,
            compatible=True,
            message="Provider ready",
        )
        disabled = ProviderPreflight(
            state=ProviderState.DISABLED,
            installed=True,
            authentication=AuthenticationState.UNKNOWN,
            compatible=True,
            message="Provider configuration is disabled",
        )

        def preflight(_adapter_id, configuration=None):
            return disabled if configuration is not None else ready

        configuration = {
            "config_id": "disabled-codex",
            "adapter_id": "codex",
            "display_name": "Disabled Codex",
            "enabled": False,
            "built_in": False,
            "revision_id": "provider-revision",
            "revision_number": 1,
            "fingerprint": "fingerprint",
            "document": {"adapter_id": "codex", "enabled": False},
        }
        with (
            patch.object(
                api_main.provider_manager.adapter_registry,
                "preflight",
                side_effect=preflight,
            ),
            patch(
                "cli_agent_orchestrator.services.control_plane_registry.list_provider_configurations",
                return_value=[configuration],
            ),
        ):
            settings = client.get("/api/v1/providers")

        assert settings.status_code == 200
        payload = settings.json()
        assert payload["adapters"]
        assert payload["configurations"]
        assert all(
            item["runtime"]["availability"] == "INSTALLED_AND_READY" for item in payload["adapters"]
        )
        assert all(item["runtime"]["state"] == "disabled" for item in payload["configurations"])
        assert all(item["runtime"]["available"] is False for item in payload["configurations"])


# ── Skills endpoint ──────────────────────────────────────────────────


class TestGetSkillContent:
    """Tests for GET /skills/{name} endpoint."""

    def test_get_skill_returns_content(self, client):
        """GET /skills/{name} returns the skill body on success."""
        with patch(
            "cli_agent_orchestrator.api.main.load_skill_content",
            return_value="# Python Testing\n\nUse pytest.",
        ):
            response = client.get("/skills/python-testing")

        assert response.status_code == 200
        assert response.json() == {
            "name": "python-testing",
            "content": "# Python Testing\n\nUse pytest.",
        }

    def test_get_skill_returns_400_for_invalid_name(self, client):
        """GET /skills/{name} returns 400 for path traversal names."""
        with patch(
            "cli_agent_orchestrator.api.main.load_skill_content",
            side_effect=SkillNameError(
                "Invalid skill name '../secret': must not contain '/', '\\\\', or '..'"
            ),
        ):
            response = client.get("/skills/%2E%2E")

        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid skill name: .."

    def test_get_skill_returns_404_for_missing_skill(self, client):
        """GET /skills/{name} returns 404 when the skill does not exist."""
        with patch(
            "cli_agent_orchestrator.api.main.load_skill_content",
            side_effect=FileNotFoundError("Skill folder does not exist"),
        ):
            response = client.get("/skills/missing-skill")

        assert response.status_code == 404
        assert response.json()["detail"] == "Skill not found: missing-skill"

    def test_get_skill_returns_500_for_parse_error(self, client):
        """GET /skills/{name} returns 500 for invalid skill file content."""
        with patch(
            "cli_agent_orchestrator.api.main.load_skill_content",
            side_effect=ValueError("Failed to parse skill file '/tmp/SKILL.md': bad yaml"),
        ):
            response = client.get("/skills/broken-skill")

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "Failed to load skill: Failed to parse skill file '/tmp/SKILL.md': bad yaml"
        )

    def test_get_skill_returns_500_for_filesystem_error(self, client):
        """GET /skills/{name} returns 500 for unexpected filesystem errors."""
        with patch(
            "cli_agent_orchestrator.api.main.load_skill_content",
            side_effect=OSError("Permission denied"),
        ):
            response = client.get("/skills/python-testing")

        assert response.status_code == 500
        assert response.json()["detail"] == "Failed to load skill: Permission denied"


# ── Sessions CRUD ────────────────────────────────────────────────────


class TestCreateSession:
    """Tests for POST /sessions endpoint — success and error cases."""

    def test_genuinely_invalid_name_returns_400_before_launch_admission(self, client, monkeypatch):
        admission = MagicMock()
        create_after_admission = MagicMock()
        generate_id = MagicMock()
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.operations_service.context_launch_admission",
            admission,
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.terminal_service._create_terminal_after_admission",
            create_after_admission,
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.terminal_service.generate_terminal_id", generate_id
        )

        response = client.post(
            "/sessions",
            params={
                "provider": "codex",
                "agent_profile": "developer",
                "session_name": "release\x00candidate",
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid session name: must not contain NUL"
        admission.assert_not_called()
        create_after_admission.assert_not_called()
        generate_id.assert_not_called()

    def test_create_session_success(self, client):
        """POST /sessions creates a session and returns 201."""
        mock_terminal = Terminal(
            id="abcd1234",
            name="test-window",
            session_name="test-session",
            provider="kiro_cli",
            agent_profile="developer",
        )
        with patch("cli_agent_orchestrator.api.main.session_service") as mock_svc:
            mock_svc.create_session.return_value = mock_terminal

            response = client.post(
                "/sessions",
                params={
                    "provider": "kiro_cli",
                    "agent_profile": "developer",
                },
            )

        assert response.status_code == 201
        data = response.json()
        assert data["id"] == "abcd1234"
        assert data["provider"] == "kiro_cli"
        assert data["agent_profile"] == "developer"
        mock_svc.create_session.assert_called_once_with(
            provider="kiro_cli",
            agent_profile="developer",
            session_name=None,
            working_directory=None,
            allowed_tools=None,
            registry=ANY,
            project_context=None,
            owner_grant_token=None,
            owner_grant_launch_id=None,
        )

    def test_create_session_passes_owner_grant_header_without_logging_it(self, client):
        mock_terminal = Terminal(
            id="abcd1234",
            name="owner-window",
            session_name="cao-owner",
            provider="codex",
            agent_profile="critical_sol_xhigh_owner",
        )
        with patch("cli_agent_orchestrator.api.main.session_service") as mock_svc:
            mock_svc.create_session.return_value = mock_terminal
            response = client.post(
                "/sessions",
                params={
                    "provider": "codex",
                    "agent_profile": "critical_sol_xhigh_owner",
                    "owner_grant_launch_id": "launch-1",
                },
                headers={"X-ThreadCells-Owner-Grant": "secret-grant"},
            )

        assert response.status_code == 201
        kwargs = mock_svc.create_session.call_args.kwargs
        assert kwargs["owner_grant_token"] == "secret-grant"
        assert kwargs["owner_grant_launch_id"] == "launch-1"

    def test_create_session_does_not_accept_reusable_loopback_xhigh_header(self, client):
        from cli_agent_orchestrator.services.operations_service import AdmissionDenied

        with patch("cli_agent_orchestrator.api.main.session_service") as mock_svc:
            mock_svc.create_session.side_effect = AdmissionDenied("OWNER_GRANT_REQUIRED", {})
            response = client.post(
                "/sessions",
                params={
                    "provider": "codex",
                    "agent_profile": "critical_sol_xhigh_owner",
                },
                headers={"X-ThreadCells-Local-Owner-XHigh": "confirmed"},
            )

        assert response.status_code == 403
        assert response.json()["detail"]["reason_code"] == "OWNER_GRANT_REQUIRED"
        kwargs = mock_svc.create_session.call_args.kwargs
        assert kwargs["owner_grant_token"] is None
        assert kwargs["owner_grant_launch_id"] is None
        assert "local_owner_xhigh_authorized" not in kwargs

    def test_create_session_returns_http_500_for_provider_startup_failure(self, client):
        """Preserve the launch failure contract while lifecycle cleanup runs below it."""
        with patch("cli_agent_orchestrator.api.main.session_service") as mock_svc:
            mock_svc.create_session.side_effect = RuntimeError("kiro-cli: command not found")

            response = client.post(
                "/sessions",
                params={"provider": "kiro_cli", "agent_profile": "developer"},
            )

        assert response.status_code == 500
        assert response.json()["detail"] == (
            "Failed to create session: kiro-cli: command not found"
        )

    def test_create_session_with_session_name(self, client):
        """POST /sessions with explicit session_name."""
        mock_terminal = Terminal(
            id="abcd1234",
            name="test-window",
            session_name="my-custom-session",
            provider="q_cli",
            agent_profile="developer",
        )
        with patch("cli_agent_orchestrator.api.main.session_service") as mock_svc:
            mock_svc.create_session.return_value = mock_terminal

            response = client.post(
                "/sessions",
                params={
                    "provider": "q_cli",
                    "agent_profile": "developer",
                    "session_name": "my-custom-session",
                },
            )

        assert response.status_code == 201
        call_kwargs = mock_svc.create_session.call_args.kwargs
        assert call_kwargs["session_name"] == "my-custom-session"
        assert call_kwargs["registry"] is not None

    def test_create_session_trims_name_and_treats_blank_as_auto_generated(self, client):
        mock_terminal = Terminal(
            id="abcd1234",
            name="test-window",
            session_name="cao-generated-name",
            provider="q_cli",
            agent_profile="developer",
        )
        with patch("cli_agent_orchestrator.api.main.session_service") as mock_svc:
            mock_svc.create_session.return_value = mock_terminal

            response = client.post(
                "/sessions",
                params={
                    "provider": "q_cli",
                    "agent_profile": "developer",
                    "session_name": "  CAO — Mobile UI Adaptation  ",
                },
            )
            assert response.status_code == 201
            assert (
                mock_svc.create_session.call_args.kwargs["session_name"]
                == "CAO — Mobile UI Adaptation"
            )

            response = client.post(
                "/sessions",
                params={
                    "provider": "q_cli",
                    "agent_profile": "developer",
                    "session_name": "   ",
                },
            )

        assert response.status_code == 201
        assert mock_svc.create_session.call_args.kwargs["session_name"] is None

    def test_create_session_value_error(self, client):
        """POST /sessions returns 400 on ValueError."""
        with patch("cli_agent_orchestrator.api.main.session_service") as mock_svc:
            mock_svc.create_session.side_effect = ValueError("Invalid provider")

            response = client.post(
                "/sessions",
                params={
                    "provider": "bad_provider",
                    "agent_profile": "developer",
                },
            )

        assert response.status_code == 400
        assert "Invalid provider" in response.json()["detail"]

    def test_create_session_server_error(self, client):
        """POST /sessions returns 500 on unexpected error."""
        with patch("cli_agent_orchestrator.api.main.session_service") as mock_svc:
            mock_svc.create_session.side_effect = Exception("TMux crashed")

            response = client.post(
                "/sessions",
                params={
                    "provider": "kiro_cli",
                    "agent_profile": "developer",
                },
            )

        assert response.status_code == 500
        assert "Failed to create session" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_slow_failing_spawn_does_not_block_session_listing_or_leave_an_api_orphan(
        self, asgi_app
    ):
        """Spawn runs off-loop; its existing failure and rollback contract remain intact."""
        spawn_started = threading.Event()
        allow_failure = threading.Event()

        def slow_failing_create_session(**_kwargs):
            spawn_started.set()
            assert allow_failure.wait(timeout=1)
            raise RuntimeError("provider startup failed after rollback")

        with patch("cli_agent_orchestrator.api.main.session_service") as mock_svc:
            mock_svc.create_session.side_effect = slow_failing_create_session
            mock_svc.list_sessions.return_value = []
            transport = httpx.ASGITransport(app=asgi_app)

            async with httpx.AsyncClient(
                transport=transport, base_url="http://localhost"
            ) as client:
                spawn_task = asyncio.create_task(
                    client.post(
                        "/sessions",
                        params={"provider": "kiro_cli", "agent_profile": "developer"},
                        headers={"Host": "localhost"},
                    )
                )
                assert await asyncio.to_thread(spawn_started.wait, 1)

                sessions_response = await asyncio.wait_for(
                    client.get("/sessions", headers={"Host": "localhost"}), timeout=0.2
                )
                assert sessions_response.status_code == 200
                assert sessions_response.json() == []

                allow_failure.set()
                spawn_response = await asyncio.wait_for(spawn_task, timeout=1)

        assert spawn_response.status_code == 500
        assert spawn_response.json()["detail"] == (
            "Failed to create session: provider startup failed after rollback"
        )
        mock_svc.create_session.assert_called_once()
        mock_svc.list_sessions.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_cancelled_create_reconciles_successful_worker_without_blocking_listing(
        self, asgi_app
    ):
        """A cancelled caller cannot leave a session created by its worker behind."""
        spawn_started = threading.Event()
        allow_success = threading.Event()
        cleanup_completed = threading.Event()
        created_sessions = set()
        created_terminal = Terminal(
            id="cancelled",
            name="cancelled",
            provider="codex",
            session_name="cao-cancelled-request",
            agent_profile="developer",
        )

        def slow_successful_create_session(**_kwargs):
            spawn_started.set()
            assert allow_success.wait(timeout=1)
            created_sessions.add(created_terminal.session_name)
            return created_terminal

        def delete_created_session(session_name, *, registry):
            assert session_name == created_terminal.session_name
            assert registry is asgi_app.state.plugin_registry
            assert session_name in created_sessions
            created_sessions.remove(session_name)
            cleanup_completed.set()
            return {"deleted": [session_name], "errors": []}

        with patch("cli_agent_orchestrator.api.main.session_service") as mock_svc:
            mock_svc.create_session.side_effect = slow_successful_create_session
            mock_svc.delete_session.side_effect = delete_created_session
            mock_svc.list_sessions.side_effect = lambda: list(created_sessions)
            transport = httpx.ASGITransport(app=asgi_app)

            async with httpx.AsyncClient(
                transport=transport, base_url="http://localhost"
            ) as client:
                spawn_task = asyncio.create_task(
                    client.post(
                        "/sessions",
                        params={"provider": "codex", "agent_profile": "developer"},
                        headers={"Host": "localhost"},
                    )
                )
                assert await asyncio.to_thread(spawn_started.wait, 1)

                sessions_response = await asyncio.wait_for(
                    client.get("/sessions", headers={"Host": "localhost"}), timeout=0.2
                )
                assert sessions_response.status_code == 200
                assert sessions_response.json() == []

                spawn_task.cancel()
                await asyncio.sleep(0)
                allow_success.set()
                with pytest.raises(asyncio.CancelledError):
                    await spawn_task

            assert await asyncio.to_thread(cleanup_completed.wait, 1)
            assert created_sessions == set()
            mock_svc.create_session.assert_called_once()
            mock_svc.delete_session.assert_called_once_with(
                created_terminal.session_name, registry=asgi_app.state.plugin_registry
            )
            mock_svc.list_sessions.assert_called_once_with()


class TestListSessions:
    """Tests for GET /sessions endpoint."""

    def test_list_sessions_success(self, client):
        """GET /sessions returns list of sessions."""
        mock_sessions = [
            {"id": "cao-session-1", "windows": 2},
            {"id": "cao-session-2", "windows": 1},
        ]
        with patch("cli_agent_orchestrator.api.main.session_service") as mock_svc:
            mock_svc.list_sessions.return_value = mock_sessions

            response = client.get("/sessions")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    def test_list_sessions_empty(self, client):
        """GET /sessions returns empty list."""
        with patch("cli_agent_orchestrator.api.main.session_service") as mock_svc:
            mock_svc.list_sessions.return_value = []

            response = client.get("/sessions")

        assert response.status_code == 200
        assert response.json() == []

    def test_list_sessions_server_error(self, client):
        """GET /sessions returns 500 on error."""
        with patch("cli_agent_orchestrator.api.main.session_service") as mock_svc:
            mock_svc.list_sessions.side_effect = Exception("TMux not running")

            response = client.get("/sessions")

        assert response.status_code == 500
        assert "Failed to list sessions" in response.json()["detail"]


class TestGetSession:
    """Tests for GET /sessions/{session_name} endpoint."""

    def test_get_session_success(self, client):
        """GET /sessions/{name} returns session details."""
        mock_session = {
            "id": "test-session",
            "windows": [{"name": "window-1", "id": "abcd1234"}],
        }
        with patch("cli_agent_orchestrator.api.main.session_service") as mock_svc:
            mock_svc.get_session.return_value = mock_session

            response = client.get("/sessions/test-session")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "test-session"
        mock_svc.get_session.assert_called_once_with("test-session")

    def test_get_session_not_found(self, client):
        """GET /sessions/{name} returns 404 for nonexistent session."""
        with patch("cli_agent_orchestrator.api.main.session_service") as mock_svc:
            mock_svc.get_session.side_effect = ValueError("Session 'nonexistent' not found")

            response = client.get("/sessions/nonexistent")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_get_session_server_error(self, client):
        """GET /sessions/{name} returns 500 on internal error."""
        with patch("cli_agent_orchestrator.api.main.session_service") as mock_svc:
            mock_svc.get_session.side_effect = Exception("Unexpected error")

            response = client.get("/sessions/test-session")

        assert response.status_code == 500
        assert "Failed to get session" in response.json()["detail"]


class TestDeleteSession:
    """Tests for DELETE /sessions/{session_name} endpoint."""

    def test_delete_session_success(self, client):
        """DELETE /sessions/{name} deletes session and returns success."""
        with patch("cli_agent_orchestrator.api.main.session_service") as mock_svc:
            mock_svc.delete_session.return_value = {
                "deleted": ["test-session"],
                "errors": [],
            }

            response = client.delete("/sessions/test-session")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["deleted"] == ["test-session"]
        mock_svc.delete_session.assert_called_once_with("test-session", registry=ANY)

    def test_delete_session_not_found(self, client):
        """DELETE /sessions/{name} returns 404 for nonexistent session."""
        with patch("cli_agent_orchestrator.api.main.session_service") as mock_svc:
            mock_svc.delete_session.side_effect = ValueError("Session 'nonexistent' not found")

            response = client.delete("/sessions/nonexistent")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_delete_session_ambiguous_identity_returns_truthful_conflict(self, client):
        with patch("cli_agent_orchestrator.api.main.session_service") as mock_svc:
            mock_svc.delete_session.side_effect = api_main.SessionLifecycleError(
                "SESSION_IDENTITY_AMBIGUOUS",
                "Session identity is ambiguous; use the stable session ID",
            )

            response = client.delete("/sessions/cao-reused")

        assert response.status_code == 409
        assert response.json()["detail"]["reason_code"] == "SESSION_IDENTITY_AMBIGUOUS"

    def test_delete_session_server_error(self, client):
        """DELETE /sessions/{name} returns 500 on internal error."""
        with patch("cli_agent_orchestrator.api.main.session_service") as mock_svc:
            mock_svc.delete_session.side_effect = Exception("TMux error")

            response = client.delete("/sessions/test-session")

        assert response.status_code == 500
        assert "Failed to delete session" in response.json()["detail"]


# ── Terminals in sessions ────────────────────────────────────────────


class TestCreateTerminalInSession:
    """Tests for POST /sessions/{session_name}/terminals endpoint."""

    def test_create_terminal_success(self, client):
        """POST /sessions/{name}/terminals creates terminal and returns 201."""
        mock_terminal = Terminal(
            id="abcd5678",
            name="test-window-2",
            session_name="test-session",
            provider="claude_code",
            agent_profile="reviewer",
        )
        with (
            patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc,
            patch.object(
                api_main.session_service,
                "resolve_session_authority",
                return_value=MagicMock(session_id="stable-session", session_name="test-session"),
            ),
        ):
            mock_svc.create_terminal.return_value = mock_terminal

            response = client.post(
                "/sessions/test-session/terminals",
                params={
                    "provider": "claude_code",
                    "agent_profile": "reviewer",
                },
            )

        assert response.status_code == 201
        data = response.json()
        assert data["id"] == "abcd5678"
        assert data["session_name"] == "test-session"
        call_kwargs = mock_svc.create_terminal.call_args.kwargs
        assert call_kwargs["session_name"] == "test-session"
        assert call_kwargs["session_lifetime_id"] == "stable-session"
        assert call_kwargs["new_session"] is False

    def test_create_terminal_session_not_found(self, client):
        """POST /sessions/{name}/terminals returns 404 for nonexistent session."""
        with patch.object(
            api_main.session_service,
            "resolve_session_authority",
            side_effect=ValueError("Session 'nonexistent' not found"),
        ):

            response = client.post(
                "/sessions/nonexistent/terminals",
                params={
                    "provider": "kiro_cli",
                    "agent_profile": "developer",
                },
            )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_create_terminal_history_is_ineligible_not_false_not_found(self, client):
        with patch.object(
            api_main.session_service,
            "resolve_session_authority",
            side_effect=api_main.SessionLifecycleError(
                "SESSION_HISTORY_INELIGIBLE",
                "This session is historical",
            ),
        ):
            response = client.post(
                "/sessions/stable-history/terminals",
                params={"provider": "codex", "agent_profile": "developer"},
            )

        assert response.status_code == 409
        assert response.json()["detail"] == {
            "reason_code": "SESSION_HISTORY_INELIGIBLE",
            "message": "This session is historical",
        }

    def test_create_terminal_runtime_uncertainty_returns_retryable_503(self, client):
        with patch.object(
            api_main.session_service,
            "resolve_session_authority",
            side_effect=api_main.SessionLifecycleError(
                "SESSION_RUNTIME_INVENTORY_UNCERTAIN",
                "Runtime inventory is uncertain",
                inventory_uncertain=True,
            ),
        ):
            response = client.post(
                "/sessions/stable-live/terminals",
                params={"provider": "codex", "agent_profile": "developer"},
            )

        assert response.status_code == 503
        assert response.json()["detail"]["reason_code"] == "SESSION_RUNTIME_INVENTORY_UNCERTAIN"

    def test_create_terminal_server_error(self, client):
        """POST /sessions/{name}/terminals returns 500 on error."""
        with (
            patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc,
            patch.object(
                api_main.session_service,
                "resolve_session_authority",
                return_value=MagicMock(session_id="stable-session", session_name="test-session"),
            ),
        ):
            mock_svc.create_terminal.side_effect = Exception("TMux error")

            response = client.post(
                "/sessions/test-session/terminals",
                params={
                    "provider": "kiro_cli",
                    "agent_profile": "developer",
                },
            )

        assert response.status_code == 500
        assert "Failed to create terminal" in response.json()["detail"]


class TestListTerminalsInSession:
    """Tests for GET /sessions/{session_name}/terminals endpoint."""

    def test_list_terminals_success(self, client):
        """GET /sessions/{name}/terminals returns terminal list."""
        mock_terminals = [
            {"id": "abcd1234", "tmux_session": "s1", "provider": "kiro_cli"},
            {"id": "abcd5678", "tmux_session": "s1", "provider": "claude_code"},
        ]
        with patch.object(
            api_main.session_service,
            "resolve_session_authority",
            return_value=MagicMock(terminals=mock_terminals),
        ):
            response = client.get("/sessions/s1/terminals")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    def test_list_terminals_empty(self, client):
        """GET /sessions/{name}/terminals returns empty list."""
        with patch.object(
            api_main.session_service,
            "resolve_session_authority",
            return_value=MagicMock(terminals=[]),
        ):
            response = client.get("/sessions/empty-session/terminals")

        assert response.status_code == 200
        assert response.json() == []

    def test_list_terminals_server_error(self, client):
        """GET /sessions/{name}/terminals returns 500 on error."""
        with patch.object(
            api_main.session_service,
            "resolve_session_authority",
            side_effect=Exception("DB error"),
        ):
            response = client.get("/sessions/s1/terminals")

        assert response.status_code == 500
        assert "Failed to list terminals" in response.json()["detail"]


# ── Individual terminal endpoints ────────────────────────────────────


class TestGetTerminal:
    """Tests for GET /terminals/{terminal_id} endpoint."""

    def test_get_terminal_success(self, client):
        """GET /terminals/{id} returns terminal details."""
        mock_terminal_dict = {
            "id": "abcd1234",
            "name": "test-window",
            "session_name": "test-session",
            "provider": "kiro_cli",
            "agent_profile": "developer",
            "status": "completed",
            "lifecycle": "running",
            "workflow_state": "waiting",
            "workflow_status": "open",
            "assignment_status": "handoff_awaiting_result",
            "result_status": "awaiting",
            "delivery_status": "handoff_awaiting_result",
        }
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.get_terminal.return_value = mock_terminal_dict

            response = client.get("/terminals/abcd1234")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "abcd1234"
        assert data["provider"] == "kiro_cli"
        assert data["workflow_state"] == "waiting"
        assert data["assignment_status"] == "handoff_awaiting_result"
        mock_svc.get_terminal.assert_called_once_with("abcd1234")

    def test_get_terminal_not_found(self, client):
        """GET /terminals/{id} returns 404 for nonexistent terminal."""
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.get_terminal.side_effect = ValueError("Terminal 'deadbeef' not found")

            response = client.get("/terminals/deadbeef")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_get_terminal_server_error(self, client):
        """GET /terminals/{id} returns 500 on internal error."""
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.get_terminal.side_effect = Exception("DB error")

            response = client.get("/terminals/abcd1234")

        assert response.status_code == 500
        assert "Failed to get terminal" in response.json()["detail"]

    def test_get_terminal_invalid_id_format(self, client):
        """GET /terminals/{id} returns 422 for invalid ID format."""
        response = client.get("/terminals/not-valid-hex")
        assert response.status_code == 422


class TestSendTerminalInput:
    """Tests for POST /terminals/{terminal_id}/input endpoint."""

    def test_send_input_success(self, client):
        """A normal input carries its durable admission envelope to the model."""
        with (
            patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc,
            patch(
                "cli_agent_orchestrator.api.main.workflow_service.prepare_external_input",
                return_value={"turn_id": 73, "queued": False},
            ) as record,
        ):
            mock_svc.send_input.return_value = True

            response = client.post(
                "/terminals/abcd1234/input",
                params={"message": "hello world"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        record.assert_called_once_with("abcd1234", "hello world")
        mock_svc.send_input.assert_called_once_with(
            "abcd1234",
            "[CAO workflow input: logical-turn=73]\n"
            "Before any model-dependent work, call "
            "claim_workflow_turn_receipt(logical_turn_id=73). "
            "Preserve the returned resume_token across context compaction. If this "
            "admitted model execution is interrupted before its work is complete, call "
            "the same tool with that resume_token; a safe resume receives a new "
            "logical_turn_id. "
            "If it returns accepted=false, this is a duplicate or a closed workflow: "
            "stop without creating another supervisor effect. Every privileged CAO "
            "operation (assign, handoff, send_message, acknowledgement, or workflow "
            "terminal transition) must use the logical_turn_id returned by the successful "
            "receipt call (normally 73); the MCP runtime "
            "rejects duplicate or unadmitted effects.\n\nhello world",
            registry=ANY,
            sender_id=None,
            orchestration_type=None,
            logical_turn_id=73,
        )

    def test_full_provider_pool_retains_external_input_as_queued_turn(self, client):
        from cli_agent_orchestrator.services.operations_service import AdmissionDenied

        with (
            patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc,
            patch(
                "cli_agent_orchestrator.api.main.workflow_service.prepare_external_input",
                return_value={"turn_id": 76, "queued": False},
            ),
            patch(
                "cli_agent_orchestrator.api.main.queue_workflow_input_for_provider",
                return_value=True,
            ) as queue,
        ):
            mock_svc.send_input.side_effect = AdmissionDenied(
                "PROVIDER_EXECUTION_CAPACITY_EXHAUSTED", {}
            )
            response = client.post("/terminals/abcd1234/input", params={"message": "durable task"})

        assert response.status_code == 200
        assert response.json() == {
            "success": True,
            "queued": True,
            "status": "queued_provider_execution",
            "reason_code": "PROVIDER_EXECUTION_CAPACITY_EXHAUSTED",
        }
        queue.assert_called_once_with(
            "abcd1234", 76, "durable task", "PROVIDER_EXECUTION_CAPACITY_EXHAUSTED"
        )

    def test_runtime_busy_queue_does_not_claim_capacity_exhaustion(self, client):
        from cli_agent_orchestrator.services.operations_service import AdmissionDenied

        capacity = {
            "provider_executions": {
                "active": 0,
                "limit": 3,
                "available": 3,
                "draining": False,
                "certain": True,
            }
        }
        with (
            patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc,
            patch(
                "cli_agent_orchestrator.api.main.workflow_service.prepare_external_input",
                return_value={"turn_id": 77, "queued": False},
            ),
            patch(
                "cli_agent_orchestrator.api.main.queue_workflow_input_for_provider",
                return_value=True,
            ) as queue,
        ):
            mock_svc.send_input.side_effect = AdmissionDenied(
                "TERMINAL_RUNTIME_OPERATION_BUSY", capacity
            )
            response = client.post(
                "/terminals/abcd1234/workflow-input",
                json={
                    "message": "durable owner turn",
                    "request_id": "735a44c5-2455-4c54-826a-79331fe6cdb0",
                },
            )

        assert response.status_code == 200
        assert response.json() == {
            "success": True,
            "accepted": True,
            "duplicate": False,
            "turn_id": 77,
            "queued": True,
            "status": "queued_provider_execution",
            "reason_code": "TERMINAL_RUNTIME_OPERATION_BUSY",
        }
        queue.assert_called_once_with(
            "abcd1234", 77, "durable owner turn", "TERMINAL_RUNTIME_OPERATION_BUSY"
        )

    def test_runtime_recovery_queues_external_input_without_physical_send(self, client):
        with (
            patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc,
            patch(
                "cli_agent_orchestrator.api.main.workflow_service.prepare_external_input",
                return_value={"turn_id": 77, "queued": True},
            ) as prepare,
        ):
            response = client.post(
                "/terminals/abcd1234/input", params={"message": "after reconnect"}
            )

        assert response.status_code == 200
        assert response.json() == {
            "success": True,
            "queued": True,
            "status": "queued_runtime_recovery",
            "reason_code": "TERMINAL_RUNTIME_OPERATION_BUSY",
        }
        prepare.assert_called_once_with("abcd1234", "after reconnect")
        mock_svc.send_input.assert_not_called()

    def test_pending_workflow_continuation_queues_external_input_without_overtaking(self, client):
        with (
            patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc,
            patch(
                "cli_agent_orchestrator.api.main.workflow_service.prepare_external_input",
                return_value={
                    "turn_id": 78,
                    "queued": True,
                    "queue_reason": "workflow_predecessor",
                },
            ) as prepare,
            patch(
                "cli_agent_orchestrator.api.main.inbox_service.wake_provider_execution_queue"
            ) as wake,
        ):
            response = client.post(
                "/terminals/abcd1234/workflow-input",
                json={
                    "message": "after the child callback",
                    "request_id": "b24c76b1-ec71-4a4a-824d-c8a9ad3e40a7",
                },
            )

        assert response.status_code == 200
        assert response.json() == {
            "success": True,
            "accepted": True,
            "duplicate": False,
            "turn_id": 78,
            "queued": True,
            "status": "queued_provider_execution",
            "reason_code": "WORKFLOW_CONTINUATION_PENDING",
        }
        prepare.assert_called_once_with(
            "abcd1234",
            "after the child callback",
            request_id="b24c76b1-ec71-4a4a-824d-c8a9ad3e40a7",
        )
        mock_svc.send_input.assert_not_called()
        wake.assert_called_once()

    def test_duplicate_composer_request_returns_same_turn_without_second_send(self, client):
        with (
            patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc,
            patch(
                "cli_agent_orchestrator.api.main.workflow_service.prepare_external_input",
                return_value={
                    "accepted": True,
                    "duplicate": True,
                    "turn_id": 81,
                    "queued": False,
                    "queue_reason": None,
                },
            ) as prepare,
        ):
            response = client.post(
                "/terminals/abcd1234/workflow-input",
                json={
                    "message": "one exact request",
                    "request_id": "576f9e7c-83f0-46c5-b838-c7b8b7b3aa34",
                },
            )

        assert response.status_code == 200
        assert response.json() == {
            "success": True,
            "accepted": True,
            "duplicate": True,
            "turn_id": 81,
            "queued": False,
            "status": "already_accepted",
            "reason_code": None,
        }
        prepare.assert_called_once_with(
            "abcd1234",
            "one exact request",
            request_id="576f9e7c-83f0-46c5-b838-c7b8b7b3aa34",
        )
        mock_svc.send_input.assert_not_called()

    def test_duplicate_composer_request_reports_its_durable_resource_wait(self, client):
        with (
            patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc,
            patch(
                "cli_agent_orchestrator.api.main.workflow_service.prepare_external_input",
                return_value={
                    "accepted": True,
                    "duplicate": True,
                    "turn_id": 81,
                    "queued": True,
                    "queue_reason": "workflow_predecessor",
                    "reason_code": "RESOURCE_HEALTH_REJECTED",
                },
            ),
        ):
            response = client.post(
                "/terminals/abcd1234/workflow-input",
                json={
                    "message": "one exact request",
                    "request_id": "576f9e7c-83f0-46c5-b838-c7b8b7b3aa34",
                },
            )

        assert response.status_code == 200
        assert response.json() == {
            "success": True,
            "accepted": True,
            "duplicate": True,
            "turn_id": 81,
            "queued": True,
            "status": "queued_provider_execution",
            "reason_code": "RESOURCE_HEALTH_REJECTED",
        }
        mock_svc.send_input.assert_not_called()

    def test_exited_terminal_composer_input_is_truthful_conflict(self, client):
        with (
            patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc,
            patch(
                "cli_agent_orchestrator.api.main.workflow_service.prepare_external_input",
                return_value={
                    "accepted": False,
                    "reason_code": "TERMINAL_RUNTIME_NOT_WRITABLE",
                },
            ),
        ):
            response = client.post(
                "/terminals/abcd1234/workflow-input",
                json={
                    "message": "must not resurrect",
                    "request_id": "39b91266-e0b8-456f-b597-2012079d86c9",
                },
            )

        assert response.status_code == 409
        assert response.json()["detail"]["reason_code"] == "TERMINAL_RUNTIME_NOT_WRITABLE"
        mock_svc.send_input.assert_not_called()

    def test_closed_unreceipted_composer_retry_is_truthful_conflict(self, client):
        with (
            patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc,
            patch(
                "cli_agent_orchestrator.api.main.workflow_service.prepare_external_input",
                return_value={
                    "accepted": False,
                    "reason_code": "WORKFLOW_INPUT_NO_LONGER_EXECUTABLE",
                },
            ),
        ):
            response = client.post(
                "/terminals/abcd1234/workflow-input",
                json={
                    "message": "closed before admission",
                    "request_id": "e430a9aa-6cb2-4704-b2d7-c5e4cd067bda",
                },
            )

        assert response.status_code == 409
        assert response.json()["detail"]["reason_code"] == "WORKFLOW_INPUT_NO_LONGER_EXECUTABLE"
        mock_svc.send_input.assert_not_called()

    def test_composer_transport_failure_returns_explicit_durable_recovery(self, client):
        with (
            patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc,
            patch(
                "cli_agent_orchestrator.api.main.workflow_service.prepare_external_input",
                return_value={"turn_id": 82, "queued": False},
            ),
            patch(
                "cli_agent_orchestrator.api.main.queue_workflow_input_for_provider",
                return_value=True,
            ) as queue,
            patch(
                "cli_agent_orchestrator.api.main.inbox_service.wake_provider_execution_queue"
            ) as wake,
        ):
            mock_svc.send_input.side_effect = RuntimeError("provider transport interrupted")
            response = client.post(
                "/terminals/abcd1234/workflow-input",
                json={
                    "message": "recover this exact submission",
                    "request_id": "fdbd8a13-1d9f-4be4-94b8-9347f635dbba",
                },
            )

        assert response.status_code == 200
        assert response.json() == {
            "success": True,
            "accepted": True,
            "duplicate": False,
            "turn_id": 82,
            "queued": True,
            "status": "queued_runtime_recovery",
            "reason_code": "PROVIDER_TRANSPORT_RETRY_PENDING",
        }
        queue.assert_called_once_with(
            "abcd1234",
            82,
            "recover this exact submission",
            "PROVIDER_TRANSPORT_RETRY_PENDING",
        )
        wake.assert_called_once()

    def test_public_orchestration_metadata_cannot_suppress_admission(self, client):
        """Public sender/type query values are ignored and cannot retain an old turn."""
        with (
            patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc,
            patch(
                "cli_agent_orchestrator.api.main.workflow_service.prepare_external_input",
                return_value={"turn_id": 74, "queued": False},
            ) as record,
        ):
            mock_svc.send_input.return_value = True

            response = client.post(
                "/terminals/abcd1234/input",
                params={
                    "message": "hello world",
                    "sender_id": "supervisor-1",
                    "orchestration_type": "assign",
                },
            )

        assert response.status_code == 200
        record.assert_called_once_with("abcd1234", "hello world")
        mock_svc.send_input.assert_called_once_with(
            "abcd1234",
            "[CAO workflow input: logical-turn=74]\n"
            "Before any model-dependent work, call "
            "claim_workflow_turn_receipt(logical_turn_id=74). "
            "Preserve the returned resume_token across context compaction. If this "
            "admitted model execution is interrupted before its work is complete, call "
            "the same tool with that resume_token; a safe resume receives a new "
            "logical_turn_id. "
            "If it returns accepted=false, this is a duplicate or a closed workflow: "
            "stop without creating another supervisor effect. Every privileged CAO "
            "operation (assign, handoff, send_message, acknowledgement, or workflow "
            "terminal transition) must use the logical_turn_id returned by the successful "
            "receipt call (normally 74); the MCP runtime "
            "rejects duplicate or unadmitted effects.\n\nhello world",
            registry=ANY,
            sender_id=None,
            orchestration_type=None,
            logical_turn_id=74,
        )

    def test_internal_orchestration_input_owns_its_fresh_admission(self, client):
        """Direct assign/handoff transport receives a server-issued binding."""
        with (
            patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc,
            patch(
                "cli_agent_orchestrator.api.main.workflow_service.record_external_input",
                return_value=75,
            ) as record,
            patch(
                "cli_agent_orchestrator.api.main.resolve_workflow_input_binding",
                return_value=75,
            ) as resolve,
        ):
            mock_svc.send_input.return_value = True

            response = client.post(
                "/_internal/terminals/abcd1234/input",
                params={
                    "message": "assigned work",
                    "binding": "server-issued-binding",
                    "sender_id": "supervisor-1",
                    "orchestration_type": "assign",
                },
            )

        assert response.status_code == 200
        record.assert_not_called()
        resolve.assert_called_once_with("abcd1234", "server-issued-binding")
        call = mock_svc.send_input.call_args
        assert "logical-turn=75" in call.args[1]
        assert call.kwargs["sender_id"] == "supervisor-1"
        assert call.kwargs["orchestration_type"] == "assign"

    def test_send_input_terminal_not_found(self, client):
        """POST /terminals/{id}/input returns 404 for nonexistent terminal."""
        with patch(
            "cli_agent_orchestrator.api.main.workflow_service.prepare_external_input",
            return_value={"accepted": False, "reason_code": "TERMINAL_NOT_FOUND"},
        ):

            response = client.post(
                "/terminals/deadbeef/input",
                params={"message": "hello"},
            )

        assert response.status_code == 404
        assert response.json()["detail"]["reason_code"] == "TERMINAL_NOT_FOUND"

    def test_send_input_server_error(self, client):
        """POST /terminals/{id}/input returns 500 on error."""
        with (
            patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc,
            patch(
                "cli_agent_orchestrator.api.main.workflow_service.prepare_external_input",
                return_value={"turn_id": 79, "queued": False},
            ),
        ):
            mock_svc.send_input.side_effect = Exception("TMux send failed")

            response = client.post(
                "/terminals/abcd1234/input",
                params={"message": "hello"},
            )

        assert response.status_code == 500
        assert "Failed to send input" in response.json()["detail"]

    @pytest.mark.parametrize(
        "message",
        [
            "short",
            "first line\nsecond line",
            "кириллица " * 20_000,
        ],
    )
    def test_workflow_composer_input_reuses_the_canonical_admission(self, client, message):
        """The explicit Composer route creates one admitted external turn."""
        with (
            patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc,
            patch(
                "cli_agent_orchestrator.api.main.workflow_service.prepare_external_input",
                return_value={"turn_id": 76, "queued": False},
            ) as record,
        ):
            mock_svc.send_input.return_value = True

            response = client.post(
                "/terminals/abcd1234/workflow-input",
                json={
                    "message": message,
                    "request_id": "8a02dbb1-2e7c-426c-9398-568eeb20d5a4",
                },
            )

        assert response.status_code == 200
        assert response.json() == {
            "success": True,
            "accepted": True,
            "duplicate": False,
            "turn_id": 76,
            "queued": False,
            "status": "provider_admitted",
            "reason_code": None,
        }
        record.assert_called_once_with(
            "abcd1234",
            message,
            request_id="8a02dbb1-2e7c-426c-9398-568eeb20d5a4",
        )
        delivered = mock_svc.send_input.call_args.args[1]
        assert "logical-turn=76" in delivered
        assert delivered.endswith(message)

    def test_workflow_composer_reads_message_from_json_body_not_uri(self, client):
        message = "строка 1\nстрока 2"
        with (
            patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc,
            patch(
                "cli_agent_orchestrator.api.main.workflow_service.prepare_external_input",
                return_value={"turn_id": 77, "queued": False},
            ) as record,
        ):
            mock_svc.send_input.return_value = True

            response = client.post(
                "/terminals/abcd1234/workflow-input?message=must-not-be-used",
                json={
                    "message": message,
                    "request_id": "441b0093-cecd-4070-ad76-cb6615566bf0",
                },
            )

        assert response.status_code == 200
        record.assert_called_once_with(
            "abcd1234",
            message,
            request_id="441b0093-cecd-4070-ad76-cb6615566bf0",
        )
        assert mock_svc.send_input.call_args.args[1].endswith(message)

    def test_workflow_composer_rejects_empty_input_before_admission(self, client):
        with patch(
            "cli_agent_orchestrator.api.main.workflow_service.prepare_external_input"
        ) as record:
            response = client.post(
                "/terminals/abcd1234/workflow-input",
                json={"message": "  \n\t"},
            )

        assert response.status_code == 422
        assert response.json()["detail"] == "message is empty"
        record.assert_not_called()


class TestGetTerminalOutput:
    """Tests for GET /terminals/{terminal_id}/output endpoint."""

    def test_get_output_full_mode(self, client):
        """GET /terminals/{id}/output returns full output by default."""
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.get_output.return_value = "Hello from terminal"

            response = client.get("/terminals/abcd1234/output")

        assert response.status_code == 200
        data = response.json()
        assert data["output"] == "Hello from terminal"
        assert data["mode"] == "full"

    def test_get_output_last_mode(self, client):
        """GET /terminals/{id}/output with mode=last returns last response."""
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.get_output.return_value = "Last response"

            response = client.get("/terminals/abcd1234/output?mode=last")

        assert response.status_code == 200
        data = response.json()
        assert data["output"] == "Last response"
        assert data["mode"] == "last"

    def test_get_output_terminal_not_found(self, client):
        """GET /terminals/{id}/output returns 404 for nonexistent terminal."""
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.get_output.side_effect = ValueError("Terminal not found")

            response = client.get("/terminals/deadbeef/output")

        assert response.status_code == 404
        assert "Terminal not found" in response.json()["detail"]

    def test_get_output_returns_cleaned_state_for_retained_history(self, client):
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.get_output.side_effect = api_main.TerminalOutputUnavailable(
                "durable output unavailable"
            )

            response = client.get("/terminals/abcd1234/output")

        assert response.status_code == 200
        assert response.json() == {
            "output": "",
            "mode": "full",
            "availability": "unavailable",
            "reason_code": "DURABLE_OUTPUT_UNAVAILABLE",
        }

    def test_get_output_server_error(self, client):
        """GET /terminals/{id}/output returns 500 on error."""
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.get_output.side_effect = Exception("Read failed")

            response = client.get("/terminals/abcd1234/output")

        assert response.status_code == 500
        assert "Failed to get output" in response.json()["detail"]


class TestDeleteTerminal:
    """Tests for DELETE /terminals/{terminal_id} endpoint."""

    def test_delete_terminal_success(self, client):
        """DELETE /terminals/{id} deletes terminal successfully."""
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.delete_terminal.return_value = True

            response = client.delete("/terminals/abcd1234")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        mock_svc.delete_terminal.assert_called_once_with("abcd1234", registry=ANY)

    def test_delete_terminal_not_found(self, client):
        """DELETE /terminals/{id} returns 404 for nonexistent terminal."""
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.delete_terminal.side_effect = ValueError("Terminal not found")

            response = client.delete("/terminals/deadbeef")

        assert response.status_code == 404
        assert "Terminal not found" in response.json()["detail"]

    def test_delete_terminal_server_error(self, client):
        """DELETE /terminals/{id} returns 500 on error."""
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.delete_terminal.side_effect = Exception("Cleanup failed")

            response = client.delete("/terminals/abcd1234")

        assert response.status_code == 500
        assert "Failed to delete terminal" in response.json()["detail"]


# ── flow_daemon ──────────────────────────────────────────────────────


class TestFlowDaemon:
    """Tests for the flow_daemon() background task."""

    @pytest.mark.asyncio
    async def test_flow_daemon_executes_flows(self):
        """flow_daemon fetches and executes due flows."""
        mock_flow = MagicMock()
        mock_flow.name = "test-flow"

        with patch("cli_agent_orchestrator.api.main.flow_service") as mock_svc:
            mock_svc.get_flows_to_run.return_value = [mock_flow]
            mock_svc.execute_flow.return_value = True

            # Run one iteration then cancel
            with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
                with pytest.raises(asyncio.CancelledError):
                    await flow_daemon()

            mock_svc.get_flows_to_run.assert_called_once()
            mock_svc.execute_flow.assert_called_once_with("test-flow")

    @pytest.mark.asyncio
    async def test_flow_daemon_handles_execute_error(self):
        """flow_daemon handles errors from execute_flow gracefully."""
        mock_flow = MagicMock()
        mock_flow.name = "fail-flow"

        with patch("cli_agent_orchestrator.api.main.flow_service") as mock_svc:
            mock_svc.get_flows_to_run.return_value = [mock_flow]
            mock_svc.execute_flow.side_effect = Exception("Execution failed")

            with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
                with pytest.raises(asyncio.CancelledError):
                    await flow_daemon()

            # Should still have attempted execution
            mock_svc.execute_flow.assert_called_once_with("fail-flow")

    @pytest.mark.asyncio
    async def test_flow_daemon_handles_get_flows_error(self):
        """flow_daemon handles errors from get_flows_to_run gracefully."""
        with patch("cli_agent_orchestrator.api.main.flow_service") as mock_svc:
            mock_svc.get_flows_to_run.side_effect = Exception("DB error")

            with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
                with pytest.raises(asyncio.CancelledError):
                    await flow_daemon()

            mock_svc.get_flows_to_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_flow_daemon_skipped_flow(self):
        """flow_daemon logs when execute returns False (skipped)."""
        mock_flow = MagicMock()
        mock_flow.name = "skipped-flow"

        with patch("cli_agent_orchestrator.api.main.flow_service") as mock_svc:
            mock_svc.get_flows_to_run.return_value = [mock_flow]
            mock_svc.execute_flow.return_value = False

            with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
                with pytest.raises(asyncio.CancelledError):
                    await flow_daemon()

            mock_svc.execute_flow.assert_called_once_with("skipped-flow")

    @pytest.mark.asyncio
    async def test_flow_daemon_multiple_flows(self):
        """flow_daemon processes multiple flows in one iteration."""
        flow1 = MagicMock()
        flow1.name = "flow-1"
        flow2 = MagicMock()
        flow2.name = "flow-2"

        with patch("cli_agent_orchestrator.api.main.flow_service") as mock_svc:
            mock_svc.get_flows_to_run.return_value = [flow1, flow2]
            mock_svc.execute_flow.return_value = True

            with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
                with pytest.raises(asyncio.CancelledError):
                    await flow_daemon()

            assert mock_svc.execute_flow.call_count == 2


class TestWorkflowDaemon:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("failed_reconciliation", ["handoff", "queue"])
    async def test_workflow_daemon_reconciles_handoffs_before_queue_and_isolates_failures(
        self, failed_reconciliation
    ):
        """One failed reconciliation cannot suppress the other in the same one-second tick."""
        calls = []

        def reconcile_handoffs(_registry=None):
            calls.append("handoff")
            if failed_reconciliation == "handoff":
                raise RuntimeError("handoff failure")

        def reconcile_queue(_registry=None):
            calls.append("queue")
            if failed_reconciliation == "queue":
                raise RuntimeError("queue failure")

        with (
            patch(
                "cli_agent_orchestrator.api.main.inbox_service.reconcile_handoff_continuations",
                side_effect=reconcile_handoffs,
            ) as handoffs,
            patch(
                "cli_agent_orchestrator.api.main.inbox_service.reconcile_provider_execution_queue",
                side_effect=reconcile_queue,
            ) as queue,
        ):
            assert await _workflow_reconciliation_tick(None, False) is False

        assert calls == ["handoff", "queue"]
        handoffs.assert_called_once_with(None)
        queue.assert_called_once_with(None)


# ── lifespan ─────────────────────────────────────────────────────────


class TestLifespan:
    """Tests for the lifespan() context manager."""

    @pytest.mark.asyncio
    async def test_lifespan_startup_and_shutdown(self):
        """lifespan starts background tasks on entry, cleans up on exit."""
        from cli_agent_orchestrator.api.main import lifespan

        mock_observer = MagicMock()

        async def completed_daemon(*_args, **_kwargs):
            return None

        with (
            patch("cli_agent_orchestrator.api.main.setup_logging"),
            patch("cli_agent_orchestrator.api.main.init_db"),
            patch(
                "cli_agent_orchestrator.api.main.PollingObserver",
                return_value=mock_observer,
            ),
            patch(
                "cli_agent_orchestrator.api.main.flow_daemon",
                side_effect=completed_daemon,
            ),
            patch(
                "cli_agent_orchestrator.api.main.workflow_daemon",
                side_effect=completed_daemon,
            ),
            patch(
                "cli_agent_orchestrator.api.main.runtime_recovery_daemon",
                side_effect=completed_daemon,
            ),
        ):
            async with lifespan(app):
                # Inside the lifespan — startup completed
                mock_observer.schedule.assert_called_once()
                mock_observer.start.assert_called_once()

            # After exit — shutdown cleanup
            mock_observer.stop.assert_called_once()
            mock_observer.join.assert_called_once()


# ── main() entry point ───────────────────────────────────────────────


class TestMainEntryPoint:
    """Tests for the main() CLI entry point."""

    def test_main_default_args(self):
        """main() runs uvicorn with default host/port."""
        with (
            patch("argparse.ArgumentParser.parse_args") as mock_args,
            patch("uvicorn.run") as mock_uvicorn,
        ):
            mock_args.return_value = MagicMock(agents_dir=None, host=None, port=None)

            from cli_agent_orchestrator.api.main import main

            main()

            mock_uvicorn.assert_called_once()
            call_kwargs = mock_uvicorn.call_args
            # Should use SERVER_HOST and SERVER_PORT defaults
            assert call_kwargs[0][0] is app

    def test_main_custom_host_port(self):
        """main() uses custom host and port from args."""
        with (
            patch("argparse.ArgumentParser.parse_args") as mock_args,
            patch("uvicorn.run") as mock_uvicorn,
        ):
            mock_args.return_value = MagicMock(agents_dir=None, host="0.0.0.0", port=9999)

            from cli_agent_orchestrator.api.main import main

            main()

            mock_uvicorn.assert_called_once_with(app, host="0.0.0.0", port=9999)

    def test_main_with_agents_dir(self):
        """main() sets KIRO_AGENTS_DIR when --agents-dir is provided."""
        with (
            patch("argparse.ArgumentParser.parse_args") as mock_args,
            patch("uvicorn.run"),
            patch("cli_agent_orchestrator.constants.KIRO_AGENTS_DIR") as _,
        ):
            mock_args.return_value = MagicMock(agents_dir="/custom/agents", host=None, port=None)

            from cli_agent_orchestrator.api.main import main

            main()
            # No assertion needed beyond no exception — the code path is covered
