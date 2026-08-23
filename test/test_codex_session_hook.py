"""Managed Codex SessionStart hook contract tests."""

from unittest.mock import MagicMock

from cli_agent_orchestrator import codex_session_hook
from cli_agent_orchestrator.runtime_generation import (
    ACTIVE_RUNTIME_GENERATION,
    RUNTIME_GENERATION_HEADER,
)


def _hook_payload():
    return {
        "hook_event_name": "SessionStart",
        "session_id": "01234567-89ab-cdef-0123-456789abcdef",
        "transcript_path": "/tmp/codex/sessions/rollout.jsonl",
        "cwd": "/tmp/project",
        "source": "startup",
    }


def _hook_env(monkeypatch, token="terminal-secret"):
    monkeypatch.setenv("CAO_TERMINAL_ID", "abcdef12")
    monkeypatch.setenv("CAO_TERMINAL_AUTH_TOKEN", token)
    monkeypatch.setenv("CAO_RUNTIME_GENERATION", "a" * 64)


def test_hook_binds_exact_identity_without_printing_capability(monkeypatch, capsys):
    token = "terminal-secret-never-print"
    _hook_env(monkeypatch, token)
    monkeypatch.setattr(codex_session_hook, "_payload", _hook_payload)
    response = MagicMock(status_code=200)
    response.json.return_value = {"session_id": "01234567-89ab-cdef-0123-456789abcdef"}
    post = MagicMock(return_value=response)
    monkeypatch.setattr(codex_session_hook.requests, "post", post)

    assert codex_session_hook.main() == 0
    assert capsys.readouterr().out == ""
    _, kwargs = post.call_args
    assert kwargs["json"]["runtime_generation"] == "a" * 64
    assert kwargs["headers"]["Authorization"] == f"Bearer {token}"
    assert kwargs["headers"][RUNTIME_GENERATION_HEADER] == ACTIVE_RUNTIME_GENERATION


def test_hook_blocks_before_provider_dispatch_when_binding_is_rejected(monkeypatch, capsys):
    token = "terminal-secret-never-print"
    _hook_env(monkeypatch, token)
    monkeypatch.setattr(codex_session_hook, "_payload", _hook_payload)
    monkeypatch.setattr(
        codex_session_hook.requests,
        "post",
        MagicMock(return_value=MagicMock(status_code=409)),
    )

    assert codex_session_hook.main() == 0
    output = capsys.readouterr().out
    assert '"continue":false' in output
    assert "ThreadCells could not bind" in output
    assert token not in output


def test_hook_rejects_malformed_or_stale_local_authority_without_network(monkeypatch, capsys):
    _hook_env(monkeypatch)
    monkeypatch.setenv("CAO_RUNTIME_GENERATION", "stale")
    monkeypatch.setattr(codex_session_hook, "_payload", _hook_payload)
    post = MagicMock()
    monkeypatch.setattr(codex_session_hook.requests, "post", post)

    assert codex_session_hook.main() == 0
    assert '"continue":false' in capsys.readouterr().out
    post.assert_not_called()
