"""Managed Codex Stop hook contract tests."""

import json
from unittest.mock import MagicMock

from cli_agent_orchestrator import codex_completion_hook
from cli_agent_orchestrator.runtime_generation import (
    ACTIVE_RUNTIME_GENERATION,
    RUNTIME_GENERATION_HEADER,
)


def _hook_payload():
    return {
        "hook_event_name": "Stop",
        "session_id": "01234567-89ab-cdef-0123-456789abcdef",
        "transcript_path": "/tmp/codex/sessions/rollout.jsonl",
        "cwd": "/tmp/project",
        "turn_id": "turn-123",
        "stop_hook_active": False,
        "last_assistant_message": "completed response — ✓",
    }


def _hook_env(monkeypatch, token="terminal-secret"):
    monkeypatch.setenv("CAO_TERMINAL_ID", "abcdef12")
    monkeypatch.setenv("CAO_TERMINAL_AUTH_TOKEN", token)
    monkeypatch.setenv("CAO_RUNTIME_GENERATION", "11111111-2222-4333-8444-555555555555")


def test_stop_hook_persists_exact_completion_and_emits_valid_json(monkeypatch, capsys):
    token = "terminal-secret-never-print"
    _hook_env(monkeypatch, token)
    monkeypatch.setattr(codex_completion_hook, "_payload", _hook_payload)
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "session_id": "01234567-89ab-cdef-0123-456789abcdef",
        "completion_offset": 1234,
    }
    post = MagicMock(return_value=response)
    monkeypatch.setattr(codex_completion_hook.requests, "post", post)

    assert codex_completion_hook.main() == 0
    assert json.loads(capsys.readouterr().out) == {"continue": True}
    _, kwargs = post.call_args
    assert kwargs["json"]["runtime_generation"] == "11111111-2222-4333-8444-555555555555"
    assert kwargs["json"]["last_assistant_message"] == "completed response — ✓"
    assert kwargs["headers"]["Authorization"] == f"Bearer {token}"
    assert kwargs["headers"][RUNTIME_GENERATION_HEADER] == ACTIVE_RUNTIME_GENERATION


def test_stop_hook_fails_closed_without_exposing_capability(monkeypatch, capsys):
    token = "terminal-secret-never-print"
    _hook_env(monkeypatch, token)
    monkeypatch.setattr(codex_completion_hook, "_payload", _hook_payload)
    monkeypatch.setattr(
        codex_completion_hook.requests,
        "post",
        MagicMock(return_value=MagicMock(status_code=409)),
    )

    assert codex_completion_hook.main() == 0
    output = capsys.readouterr().out
    assert json.loads(output)["continue"] is False
    assert "could not persist" in output
    assert token not in output


def test_stop_hook_rejects_malformed_local_authority_without_network(monkeypatch, capsys):
    _hook_env(monkeypatch)
    monkeypatch.setenv("CAO_RUNTIME_GENERATION", "stale")
    monkeypatch.setattr(codex_completion_hook, "_payload", _hook_payload)
    post = MagicMock()
    monkeypatch.setattr(codex_completion_hook.requests, "post", post)

    assert codex_completion_hook.main() == 0
    assert json.loads(capsys.readouterr().out)["continue"] is False
    post.assert_not_called()
