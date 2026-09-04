"""Hidden Codex SessionStart identity-binding endpoint tests."""

from unittest.mock import MagicMock, patch

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
        "runtime_generation": "11111111-2222-4333-8444-555555555555",
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


def test_codex_session_identity_allows_stale_api_generation_only_as_exact_rebind(
    client, monkeypatch
):
    identity = _payload()["session_id"]
    generation = _payload()["runtime_generation"]
    provider = MagicMock()
    provider.runtime_sidecar_resume_identity.return_value = identity
    manager = MagicMock()
    manager.get_provider.return_value = provider
    tmux = MagicMock()
    tmux.get_pane_working_directory.return_value = _payload()["cwd"]
    bind = MagicMock(return_value=True)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.get_terminal_metadata",
        lambda _terminal_id: {
            "provider": "codex",
            "runtime_lifecycle": "running",
            "runtime_generation": generation,
            "provider_resume_identity": identity,
            "provider_resume_runtime_generation": generation,
            "managed_worktree_kind": "supervisor",
            "launch_worktree": _payload()["cwd"],
            "tmux_session": "cao-managed",
            "tmux_window": "managed",
        },
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.provider_manager", manager
    )
    monkeypatch.setattr("cli_agent_orchestrator.services.terminal_service.tmux_client", tmux)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.bind_terminal_provider_resume_identity",
        bind,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.get_workflow_turn_provider_outcome_cursor_bootstrap",
        lambda *_args: None,
    )
    with patch("cli_agent_orchestrator.api.main.terminal_auth_token_matches", return_value=True):
        response = client.post(
            "/_internal/terminals/abcdef12/codex-session-identity",
            json=_payload(),
            headers=_headers("b" * 64),
        )
    assert response.status_code == 200
    assert response.json() == {"session_id": identity}
    provider.runtime_sidecar_resume_identity.assert_called_once_with(
        expected_identity=identity,
        expected_rollout_path=_payload()["transcript_path"],
    )
    bind.assert_called_once_with(
        "abcdef12",
        provider="codex",
        resume_identity=identity,
        runtime_generation=generation,
        require_existing_binding=True,
    )


def test_codex_session_identity_rejects_malformed_api_generation_before_proof(client):
    with (
        patch("cli_agent_orchestrator.api.main.terminal_auth_token_matches", return_value=True),
        patch(
            "cli_agent_orchestrator.api.main.terminal_service.bind_provider_runtime_session_identity"
        ) as bind,
    ):
        response = client.post(
            "/_internal/terminals/abcdef12/codex-session-identity",
            json=_payload(),
            headers=_headers("not-a-runtime-generation"),
        )
    assert response.status_code == 409
    assert response.json()["detail"] == "stale_runtime_generation"
    bind.assert_not_called()


def test_codex_session_identity_rejects_unproven_stale_rebind(client, monkeypatch):
    manager = MagicMock()
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.get_terminal_metadata",
        lambda _terminal_id: {
            "provider": "codex",
            "runtime_lifecycle": "running",
            "runtime_generation": _payload()["runtime_generation"],
            "provider_resume_identity": None,
            "provider_resume_runtime_generation": None,
        },
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.provider_manager", manager
    )
    with patch("cli_agent_orchestrator.api.main.terminal_auth_token_matches", return_value=True):
        response = client.post(
            "/_internal/terminals/abcdef12/codex-session-identity",
            json=_payload(),
            headers=_headers("b" * 64),
        )
    assert response.status_code == 409
    assert response.json()["detail"] == "stale_identity_rebind_not_proven"
    manager.get_provider.assert_not_called()


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
        "runtime_generation": "11111111-2222-4333-8444-555555555555",
        "require_existing_binding": False,
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
