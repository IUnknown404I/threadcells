"""Hidden Codex SessionStart identity-binding endpoint tests."""

from unittest.mock import patch

from cli_agent_orchestrator.runtime_generation import (
    ACTIVE_RUNTIME_GENERATION,
    RUNTIME_GENERATION_HEADER,
)


def _payload():
    return {
        "session_id": "01234567-89ab-cdef-0123-456789abcdef",
        "transcript_path": "/tmp/codex/sessions/rollout.jsonl",
        "cwd": "/tmp/project",
        "source": "startup",
        "runtime_generation": "a" * 64,
    }


def _headers(generation=ACTIVE_RUNTIME_GENERATION):
    return {
        "Authorization": "Bearer terminal-secret",
        RUNTIME_GENERATION_HEADER: generation,
    }


def test_codex_session_identity_requires_exact_terminal_bearer(client):
    with patch("cli_agent_orchestrator.api.main.terminal_auth_token_matches", return_value=False):
        response = client.post(
            "/_internal/terminals/abcdef12/codex-session-identity",
            json=_payload(),
            headers=_headers(),
        )
    assert response.status_code == 401


def test_codex_session_identity_rejects_stale_api_generation_before_proof(client):
    with (
        patch("cli_agent_orchestrator.api.main.terminal_auth_token_matches", return_value=True),
        patch(
            "cli_agent_orchestrator.api.main.terminal_service.bind_provider_runtime_session_identity"
        ) as bind,
    ):
        response = client.post(
            "/_internal/terminals/abcdef12/codex-session-identity",
            json=_payload(),
            headers=_headers("b" * 64),
        )
    assert response.status_code == 409
    assert response.json()["detail"] == "stale_runtime_generation"
    bind.assert_not_called()


def test_codex_session_identity_binds_only_server_proven_identity(client):
    identity = _payload()["session_id"]
    with (
        patch(
            "cli_agent_orchestrator.api.main.terminal_auth_token_matches", return_value=True
        ) as auth,
        patch(
            "cli_agent_orchestrator.api.main.terminal_service.bind_provider_runtime_session_identity",
            return_value=identity,
        ) as bind,
    ):
        response = client.post(
            "/_internal/terminals/abcdef12/codex-session-identity",
            json=_payload(),
            headers=_headers(),
        )
    assert response.status_code == 200
    assert response.json() == {"session_id": identity}
    auth.assert_called_once_with("abcdef12", "terminal-secret")
    assert bind.call_args.kwargs == {
        "resume_identity": identity,
        "transcript_path": "/tmp/codex/sessions/rollout.jsonl",
        "working_directory": "/tmp/project",
        "source": "startup",
        "runtime_generation": "a" * 64,
    }


def test_codex_session_identity_fails_closed_when_foreground_proof_fails(client):
    with (
        patch("cli_agent_orchestrator.api.main.terminal_auth_token_matches", return_value=True),
        patch(
            "cli_agent_orchestrator.api.main.terminal_service.bind_provider_runtime_session_identity",
            side_effect=RuntimeError("wrong rollout"),
        ),
    ):
        response = client.post(
            "/_internal/terminals/abcdef12/codex-session-identity",
            json=_payload(),
            headers=_headers(),
        )
    assert response.status_code == 409
    assert response.json()["detail"] == "identity_not_proven"
