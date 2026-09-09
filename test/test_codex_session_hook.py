"""Managed Codex SessionStart hook contract tests."""

import json
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
    monkeypatch.setenv("CAO_RUNTIME_GENERATION", "11111111-2222-4333-8444-555555555555")


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
    assert kwargs["json"]["runtime_generation"] == "11111111-2222-4333-8444-555555555555"
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


def test_compaction_hook_rehydrates_exact_admitted_authority(monkeypatch, capsys):
    token = "terminal-secret-never-print"
    resume_token = "r" * 43
    _hook_env(monkeypatch, token)
    payload = {**_hook_payload(), "source": "compact"}
    monkeypatch.setattr(codex_session_hook, "_payload", lambda: payload)
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "session_id": payload["session_id"],
        "continuation_authority": {
            "authority_version": 1,
            "workflow_id": 71,
            "logical_turn_id": 73,
            "resume_token": resume_token,
            "receiver_terminal_id": "abcdef12",
            "runtime_generation": "11111111-2222-4333-8444-555555555555",
        },
    }
    monkeypatch.setattr(
        codex_session_hook.requests,
        "post",
        MagicMock(return_value=response),
    )

    assert codex_session_hook.main() == 0
    output = json.loads(capsys.readouterr().out)
    context = output["hookSpecificOutput"]["additionalContext"]
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    authority_line = next(
        line for line in context.splitlines() if line.startswith("CAO_WORKFLOW_CONTINUATION_V1=")
    )
    authority = json.loads(authority_line.split("=", 1)[1])
    assert authority == {
        "authority_version": 1,
        "claim_required": False,
        "logical_turn_id": 73,
        "resume_token": resume_token,
        "workflow_id": 71,
    }
    assert "current logical_turn_id is 73" in context
    assert "do not make a fresh claim_workflow_turn_receipt" in context
    assert context.count(resume_token) == 1
    assert "must not be replayed" in context
    assert token not in context


def test_compaction_hook_fails_closed_on_mismatched_authority(monkeypatch, capsys):
    terminal_token = "terminal-secret-never-print"
    leaked_resume_token = "s" * 43
    _hook_env(monkeypatch, terminal_token)
    payload = {**_hook_payload(), "source": "compact"}
    monkeypatch.setattr(codex_session_hook, "_payload", lambda: payload)
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "session_id": payload["session_id"],
        "continuation_authority": {
            "authority_version": 1,
            "workflow_id": 71,
            "logical_turn_id": 73,
            "resume_token": leaked_resume_token,
            "receiver_terminal_id": "foreign1",
            "runtime_generation": "11111111-2222-4333-8444-555555555555",
        },
    }
    monkeypatch.setattr(
        codex_session_hook.requests,
        "post",
        MagicMock(return_value=response),
    )

    assert codex_session_hook.main() == 0
    output = capsys.readouterr().out
    assert '"continue":false' in output
    assert terminal_token not in output
    assert leaked_resume_token not in output


def test_compaction_hook_has_no_context_to_restore_for_terminal_workflow(monkeypatch, capsys):
    _hook_env(monkeypatch)
    payload = {**_hook_payload(), "source": "compact"}
    monkeypatch.setattr(codex_session_hook, "_payload", lambda: payload)
    response = MagicMock(status_code=200)
    response.json.return_value = {"session_id": payload["session_id"]}
    monkeypatch.setattr(
        codex_session_hook.requests,
        "post",
        MagicMock(return_value=response),
    )

    assert codex_session_hook.main() == 0
    assert capsys.readouterr().out == ""
