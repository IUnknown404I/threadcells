import asyncio
import os
from unittest.mock import MagicMock, patch

from cli_agent_orchestrator.mcp_server.server import (
    TerminalAdmissionError,
    _assign_impl,
    _create_terminal,
    _delegation_effect_outcome,
    _handoff_impl,
)


def _response(payload, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def test_default_delegation_requests_managed_reviewer_worktree_but_explicit_directory_wins():
    metadata = _response(
        {
            "provider": "codex",
            "session_name": "cao-session",
            "session_id": "stable-session-lifetime",
            "allowed_tools": ["fs_read"],
        }
    )
    directory = _response({"working_directory": "/project"})
    created = _response({"id": "child"}, 201)
    with (
        patch.dict(os.environ, {"CAO_TERMINAL_ID": "parent"}),
        patch(
            "cli_agent_orchestrator.mcp_server.server.requests.get",
            side_effect=[metadata, directory],
        ),
        patch(
            "cli_agent_orchestrator.mcp_server.server.requests.post", return_value=created
        ) as post,
    ):
        assert _create_terminal("reviewer") == ("child", "codex")
    assert post.call_args.args[0].endswith("/sessions/stable-session-lifetime/terminals")
    assert post.call_args.kwargs["params"]["managed_worktree_kind"] == "reviewer"
    assert post.call_args.kwargs["params"]["working_directory"] == "/project"

    with (
        patch.dict(os.environ, {"CAO_TERMINAL_ID": "parent"}),
        patch("cli_agent_orchestrator.mcp_server.server.requests.get", return_value=metadata),
        patch(
            "cli_agent_orchestrator.mcp_server.server.requests.post", return_value=created
        ) as explicit_post,
    ):
        _create_terminal("reviewer", "/explicit-review")
    assert "managed_worktree_kind" not in explicit_post.call_args.kwargs["params"]
    assert explicit_post.call_args.kwargs["params"]["working_directory"] == "/explicit-review"


def test_delegation_fails_before_child_creation_without_canonical_session_identity():
    metadata = _response(
        {
            "provider": "codex",
            "session_name": "cao-reused",
            "allowed_tools": ["fs_read"],
        }
    )
    with (
        patch.dict(os.environ, {"CAO_TERMINAL_ID": "parent"}),
        patch("cli_agent_orchestrator.mcp_server.server.requests.get", return_value=metadata),
        patch("cli_agent_orchestrator.mcp_server.server.requests.post") as post,
    ):
        try:
            _create_terminal("reviewer")
        except TerminalAdmissionError as error:
            assert error.reason_code == "SESSION_IDENTITY_UNAVAILABLE"
        else:
            raise AssertionError("delegation must fail closed without canonical session identity")
    post.assert_not_called()


def test_delegation_preserves_canonical_legacy_session_identity():
    metadata = _response(
        {
            "provider": "codex",
            "session_name": "cao-legacy",
            "session_id": "legacy:cao-legacy",
            "allowed_tools": ["fs_read"],
        }
    )
    created = _response({"id": "child"}, 201)
    with (
        patch.dict(os.environ, {"CAO_TERMINAL_ID": "parent"}),
        patch("cli_agent_orchestrator.mcp_server.server.requests.get", return_value=metadata),
        patch(
            "cli_agent_orchestrator.mcp_server.server.requests.post", return_value=created
        ) as post,
    ):
        assert _create_terminal("reviewer", "/project") == ("child", "codex")

    assert post.call_args.args[0].endswith("/sessions/legacy%3Acao-legacy/terminals")


def test_handoff_preserves_terminal_admission_reason_code():
    with patch(
        "cli_agent_orchestrator.mcp_server.server._create_terminal",
        side_effect=TerminalAdmissionError(
            "TOTAL_PROVIDER_CAPACITY_EXHAUSTED", "terminal admission denied"
        ),
    ):
        result = asyncio.run(_handoff_impl("developer", "task"))
    assert result.success is False
    assert result.reason_code == "TOTAL_PROVIDER_CAPACITY_EXHAUSTED"


def test_assign_preserves_terminal_admission_reason_code():
    with patch(
        "cli_agent_orchestrator.mcp_server.server._create_terminal",
        side_effect=TerminalAdmissionError(
            "WORK_CONTEXT_CAPACITY_EXHAUSTED", "terminal admission denied"
        ),
    ):
        result = _assign_impl("developer", "task")
    assert result == {
        "success": False,
        "terminal_id": None,
        "message": "terminal admission denied",
        "reason_code": "WORK_CONTEXT_CAPACITY_EXHAUSTED",
    }


def test_session_identity_rejections_are_safe_pre_child_effect_outcomes():
    for reason_code in (
        "SESSION_IDENTITY_AMBIGUOUS",
        "SESSION_IDENTITY_UNAVAILABLE",
    ):
        assert (
            _delegation_effect_outcome(
                {
                    "success": False,
                    "terminal_id": None,
                    "reason_code": reason_code,
                }
            )
            == "not_admitted"
        )
