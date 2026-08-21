"""Tests for terminal-related API endpoints including working directory and exit."""

from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from cli_agent_orchestrator.api.main import app
from cli_agent_orchestrator.models.terminal import Terminal
from cli_agent_orchestrator.services import terminal_service


class TestWorkingDirectoryEndpoint:
    """Test GET /terminals/{terminal_id}/working-directory endpoint."""

    def test_get_working_directory_success(self, client):
        """Test successful retrieval of working directory."""
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.get_working_directory.return_value = "/home/user/project"

            response = client.get("/terminals/abcd1234/working-directory")

            assert response.status_code == 200
            data = response.json()
            assert data["working_directory"] == "/home/user/project"
            mock_svc.get_working_directory.assert_called_once_with("abcd1234")

    def test_get_working_directory_returns_none(self, client):
        """Test when working directory is unavailable."""
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.get_working_directory.return_value = None

            response = client.get("/terminals/abcd1234/working-directory")

            assert response.status_code == 200
            assert response.json()["working_directory"] is None

    def test_get_working_directory_terminal_not_found(self, client):
        """Test 404 when terminal doesn't exist."""
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.get_working_directory.side_effect = ValueError("Terminal 'abcd5678' not found")

            response = client.get("/terminals/abcd5678/working-directory")

            assert response.status_code == 404
            assert "not found" in response.json()["detail"].lower()

    def test_get_working_directory_server_error(self, client):
        """Test 500 on internal error."""
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.get_working_directory.side_effect = Exception("TMux error")

            response = client.get("/terminals/abcd1234/working-directory")

            assert response.status_code == 500
            assert "Failed to get working directory" in response.json()["detail"]

    def test_get_working_directory_internal_error(self, client):
        """Test 500 when internal error occurs."""
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.get_working_directory.side_effect = RuntimeError("Internal service error")

            response = client.get("/terminals/abcd1234/working-directory")

            assert response.status_code == 500
            assert "Failed to get working directory" in response.json()["detail"]


class TestSessionRootWorkingDirectoryEndpoint:
    """Test GET /sessions/{name}/working-directory endpoint."""

    def test_get_session_root_working_directory_success(self, client):
        with patch("cli_agent_orchestrator.api.main.session_service") as mock_svc:
            mock_svc.get_session_root_working_directory.return_value = "/srv/session-root"

            response = client.get("/sessions/cao-existing/working-directory")

        assert response.status_code == 200
        assert response.json()["working_directory"] == "/srv/session-root"
        mock_svc.get_session_root_working_directory.assert_called_once_with("cao-existing")


class TestSessionCreationWithWorkingDirectory:
    """Test session creation with working_directory parameter."""

    def test_create_session_passes_working_directory(self, client, tmp_path):
        """Test that working_directory parameter is passed to service."""
        with patch("cli_agent_orchestrator.api.main.session_service") as mock_svc:
            mock_svc.create_session.return_value = Terminal(
                id="abcd1234",
                name="test-window",
                session_name="test-session",
                provider="q_cli",
                agent_profile="developer",
            )

            response = client.post(
                "/sessions",
                params={
                    "provider": "q_cli",
                    "agent_profile": "developer",
                    "working_directory": str(tmp_path),
                },
            )

            assert response.status_code == 201
            # Verify working_directory was passed
            call_kwargs = mock_svc.create_session.call_args.kwargs
            assert call_kwargs.get("working_directory") == str(tmp_path)
            assert call_kwargs.get("registry") is not None

    def test_create_session_with_working_directory(self, client):
        """Test POST /sessions with working_directory parameter."""
        with patch("cli_agent_orchestrator.api.main.session_service") as mock_svc:
            mock_svc.create_session.return_value = Terminal(
                id="abcd1234",
                name="test-window",
                session_name="test-session",
                provider="q_cli",
                agent_profile="developer",
            )

            response = client.post(
                "/sessions",
                params={
                    "provider": "q_cli",
                    "agent_profile": "developer",
                    "working_directory": "/custom/path",
                },
            )

            assert response.status_code == 201
            call_kwargs = mock_svc.create_session.call_args.kwargs
            assert call_kwargs.get("working_directory") == "/custom/path"


class TestTerminalCreationWithWorkingDirectory:
    """Test terminal creation with working_directory parameter."""

    def test_create_terminal_passes_working_directory(self, client, tmp_path):
        """Test that working_directory parameter is passed to service."""
        with (
            patch(
                "cli_agent_orchestrator.api.main.resolve_provider",
                side_effect=lambda _, fallback_provider: fallback_provider,
            ),
            patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc,
        ):
            mock_svc.create_terminal.return_value = Terminal(
                id="abcd5678",
                name="test-window",
                session_name="test-session",
                provider="q_cli",
                agent_profile="analyst",
            )

            response = client.post(
                "/sessions/test-session/terminals",
                params={
                    "provider": "q_cli",
                    "agent_profile": "analyst",
                    "working_directory": str(tmp_path),
                },
            )

            assert response.status_code == 201
            call_kwargs = mock_svc.create_terminal.call_args.kwargs
            assert call_kwargs.get("working_directory") == str(tmp_path)

    def test_create_terminal_in_session_with_working_directory(self, client):
        """Test POST /sessions/{session}/terminals with working_directory."""
        with (
            patch(
                "cli_agent_orchestrator.api.main.resolve_provider",
                side_effect=lambda _, fallback_provider: fallback_provider,
            ),
            patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc,
        ):
            mock_svc.create_terminal.return_value = Terminal(
                id="abcd5678",
                name="test-window",
                session_name="test-session",
                provider="q_cli",
                agent_profile="analyst",
            )

            response = client.post(
                "/sessions/test-session/terminals",
                params={
                    "provider": "q_cli",
                    "agent_profile": "analyst",
                    "working_directory": "/session/path",
                },
            )

            assert response.status_code == 201
            call_kwargs = mock_svc.create_terminal.call_args.kwargs
            assert call_kwargs.get("working_directory") == "/session/path"
            assert "context_role" not in call_kwargs

    def test_create_terminal_in_session_passes_the_one_use_owner_grant(self, client, tmp_path):
        """Authorized Add Agent uses the same protected grant contract as session spawn."""
        with (
            patch(
                "cli_agent_orchestrator.api.main.resolve_provider",
                return_value="codex",
            ),
            patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc,
        ):
            mock_svc.create_terminal.return_value = Terminal(
                id="owner5678",
                name="owner-window",
                session_name="cao-existing",
                provider="codex",
                agent_profile="critical_sol_xhigh_owner",
            )

            response = client.post(
                "/sessions/cao-existing/terminals",
                params={
                    "provider": "codex",
                    "agent_profile": "critical_sol_xhigh_owner",
                    "working_directory": str(tmp_path),
                    "owner_grant_launch_id": "add-launch-1",
                },
                headers={"X-ThreadCells-Owner-Grant": "one-use-add-grant"},
            )

        assert response.status_code == 201
        call_kwargs = mock_svc.create_terminal.call_args.kwargs
        assert call_kwargs["owner_grant_token"] == "one-use-add-grant"
        assert call_kwargs["owner_grant_launch_id"] == "add-launch-1"


class TestExitTerminalEndpoint:
    """Test POST /terminals/{terminal_id}/exit endpoint.

    Verifies that the endpoint exposes the service lifecycle result.
    """

    def test_exit_terminal_text_command(self, client):
        """A positively confirmed graceful exit is exposed as exited."""
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.exit_terminal.return_value = SimpleNamespace(
                success=True,
                lifecycle="exited",
                outcome="command_delivered",
                message="Exit command delivered and provider exit confirmed",
                command_delivered=True,
            )

            response = client.post("/terminals/abcd1234/exit")

            assert response.status_code == 200
            assert response.json() == {
                "success": True,
                "lifecycle": "exited",
                "outcome": "command_delivered",
                "message": "Exit command delivered and provider exit confirmed",
                "command_delivered": True,
            }
            mock_svc.exit_terminal.assert_called_once_with("abcd1234")

    def test_exit_terminal_special_key(self, client):
        """An uncertain graceful-exit outcome remains visibly pending."""
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.exit_terminal.return_value = SimpleNamespace(
                success=False,
                lifecycle="exit_pending",
                outcome="exit_pending",
                message="Exit remains pending",
                command_delivered=False,
            )

            response = client.post("/terminals/abcd1234/exit")

            assert response.status_code == 200
            assert response.json() == {
                "success": False,
                "lifecycle": "exit_pending",
                "outcome": "exit_pending",
                "message": "Exit remains pending",
                "command_delivered": False,
            }

    def test_exit_terminal_meta_key(self, client):
        """The endpoint delegates the lifecycle boundary to the service."""
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.exit_terminal.return_value = SimpleNamespace(
                success=True,
                lifecycle="exited",
                outcome="already_exited",
                message="Terminal was already exited; no command sent",
                command_delivered=False,
            )

            response = client.post("/terminals/abcd1234/exit")

            assert response.status_code == 200
            mock_svc.exit_terminal.assert_called_once_with("abcd1234")

    def test_exit_terminal_authority_conflict_has_reason(self, client):
        error = terminal_service.ExitAuthorityError(
            "EXIT_PANE_AMBIGUOUS", "Tmux window has multiple panes"
        )
        with patch(
            "cli_agent_orchestrator.api.main.terminal_service.exit_terminal",
            side_effect=error,
        ):
            response = client.post("/terminals/abcd1234/exit")

        assert response.status_code == 409
        assert response.json()["detail"] == {
            "reason_code": "EXIT_PANE_AMBIGUOUS",
            "message": "Tmux window has multiple panes",
        }

    def test_exit_terminal_provider_not_found(self, client):
        """Should return 404 when provider is not found."""
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.exit_terminal.side_effect = ValueError("Terminal not found in database")

            response = client.post("/terminals/deadbeef/exit")

            assert response.status_code == 404

    def test_exit_terminal_server_error(self, client):
        """Should return 500 on unexpected errors."""
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.exit_terminal.side_effect = RuntimeError("TMux error")

            response = client.post("/terminals/abcd1234/exit")

            assert response.status_code == 500
            assert "Failed to exit terminal" in response.json()["detail"]

    def test_exit_terminal_provider_returns_none(self, client):
        """A missing historical record is a 404."""
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.exit_terminal.side_effect = ValueError("Terminal 'deadbeef' not found")

            response = client.post("/terminals/deadbeef/exit")

            assert response.status_code == 404
            assert "not found" in response.json()["detail"]


class TestDeleteTerminalEndpoint:
    """Test DELETE /terminals/{terminal_id} endpoint."""

    def test_delete_terminal_success(self, client):
        """DELETE /terminals/{terminal_id} deletes and returns success."""
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.delete_terminal.return_value = True

            response = client.delete("/terminals/abcd1234")

            assert response.status_code == 200
            assert response.json() == {"success": True}
            mock_svc.delete_terminal.assert_called_once_with("abcd1234", registry=ANY)

    def test_delete_terminal_not_found(self, client):
        """DELETE /terminals/{terminal_id} returns 404 for missing terminal."""
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.delete_terminal.side_effect = ValueError("Terminal not found")

            response = client.delete("/terminals/deadbeef")

            assert response.status_code == 404

    def test_delete_terminal_server_error(self, client):
        """DELETE /terminals/{terminal_id} returns 500 on internal error."""
        with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc:
            mock_svc.delete_terminal.side_effect = Exception("TMux error")

            response = client.delete("/terminals/abcd1234")

            assert response.status_code == 500
            assert "Failed to delete terminal" in response.json()["detail"]


class TestCreateInboxMessageEndpoint:
    """Test POST /terminals/{receiver_id}/inbox/messages endpoint."""

    @pytest.mark.parametrize(
        "message",
        [
            "short message",
            "Первая строка\nВторая строка — Unicode ✓",
            "длинное сообщение " * 2000,
        ],
    )
    def test_create_inbox_message_success(self, client, message):
        """POST creates an inbox message and returns success."""
        mock_msg = MagicMock()
        mock_msg.id = 1
        mock_msg.sender_id = "sender1"
        mock_msg.receiver_id = "abcd1234"
        mock_msg.created_at.isoformat.return_value = "2026-03-13T12:00:00"

        with (
            patch("cli_agent_orchestrator.api.main.create_inbox_message") as mock_create,
            patch("cli_agent_orchestrator.api.main.inbox_service") as mock_inbox,
        ):
            mock_create.return_value = mock_msg

            response = client.post(
                "/terminals/abcd1234/inbox/messages",
                json={"sender_id": "sender1", "message": message},
            )

            assert response.status_code == 200
            assert response.request.url.query == b""
            data = response.json()
            assert data["success"] is True
            assert data["message_id"] == 1
            assert data["sender_id"] == "sender1"
            mock_create.assert_called_once_with(
                "sender1",
                "abcd1234",
                message,
            )
            mock_inbox.check_and_send_pending_messages.assert_called_once_with(
                "abcd1234", registry=ANY
            )

    def test_create_inbox_message_delivery_failure_still_succeeds(self, client):
        """Immediate delivery failure should not fail the API response."""
        mock_msg = MagicMock()
        mock_msg.id = 2
        mock_msg.sender_id = "sender1"
        mock_msg.receiver_id = "abcd1234"
        mock_msg.created_at.isoformat.return_value = "2026-03-13T12:00:00"

        with (
            patch("cli_agent_orchestrator.api.main.create_inbox_message") as mock_create,
            patch("cli_agent_orchestrator.api.main.inbox_service") as mock_inbox,
        ):
            mock_create.return_value = mock_msg
            mock_inbox.check_and_send_pending_messages.side_effect = Exception("TMux busy")

            response = client.post(
                "/terminals/abcd1234/inbox/messages",
                json={"sender_id": "sender1", "message": "hello"},
            )

            assert response.status_code == 200
            assert response.json()["success"] is True

    def test_create_inbox_message_not_found(self, client):
        """POST returns 404 when terminal not found."""
        with patch("cli_agent_orchestrator.api.main.create_inbox_message") as mock_create:
            mock_create.side_effect = ValueError("Terminal not found")

            response = client.post(
                "/terminals/deadbeef/inbox/messages",
                json={"sender_id": "sender1", "message": "hello"},
            )

            assert response.status_code == 404

    def test_create_inbox_message_rejects_legacy_query_payload(self, client):
        """Inbox text is accepted only from the JSON body, never from the URL."""
        with patch("cli_agent_orchestrator.api.main.create_inbox_message") as mock_create:
            response = client.post(
                "/terminals/abcd1234/inbox/messages?sender_id=sender1&message=leaked"
            )

        assert response.status_code == 422
        mock_create.assert_not_called()

    def test_create_inbox_message_server_error(self, client):
        """POST returns 500 on internal error."""
        with patch("cli_agent_orchestrator.api.main.create_inbox_message") as mock_create:
            mock_create.side_effect = Exception("DB error")

            response = client.post(
                "/terminals/abcd1234/inbox/messages",
                json={"sender_id": "sender1", "message": "hello"},
            )

            assert response.status_code == 500
            assert "Failed to create inbox message" in response.json()["detail"]


class TestWebSocketLocalhostRestriction:
    """Test that WebSocket endpoint rejects non-loopback clients."""

    def test_websocket_rejects_non_loopback(self, client):
        """WebSocket should close with 4003 for non-localhost clients."""
        # TestClient uses "testclient" as host, which is not in the allowlist
        with pytest.raises(Exception):
            with client.websocket_connect("/terminals/abcd1234/ws"):
                pass


class TestCrossProviderResolution:
    """Test that create_terminal_in_session resolves provider from agent profile
    while create_session always uses the explicit provider parameter."""

    def test_create_terminal_uses_profile_provider(self, client):
        """create_terminal_in_session should resolve provider from agent profile."""
        with (
            patch("cli_agent_orchestrator.api.main.resolve_provider") as mock_resolve,
            patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc,
        ):
            mock_resolve.return_value = "claude_code"
            mock_svc.create_terminal.return_value = Terminal(
                id="abcd1234",
                name="test-window",
                session_name="test-session",
                provider="claude_code",
                agent_profile="developer",
            )

            response = client.post(
                "/sessions/test-session/terminals",
                params={
                    "provider": "kiro_cli",
                    "agent_profile": "developer",
                },
            )

            assert response.status_code == 201
            # Verify resolve_provider was called with the fallback
            mock_resolve.assert_called_once_with("developer", fallback_provider="kiro_cli")
            # Verify terminal_service got the resolved provider
            call_kwargs = mock_svc.create_terminal.call_args.kwargs
            assert call_kwargs["provider"] == "claude_code"

    def test_create_terminal_falls_back_when_no_profile_provider(self, client):
        """create_terminal_in_session should use fallback when profile has no provider."""
        with (
            patch("cli_agent_orchestrator.api.main.resolve_provider") as mock_resolve,
            patch("cli_agent_orchestrator.api.main.terminal_service") as mock_svc,
        ):
            # resolve_provider returns the fallback (no profile provider key)
            mock_resolve.return_value = "kiro_cli"
            mock_svc.create_terminal.return_value = Terminal(
                id="abcd5678",
                name="test-window",
                session_name="test-session",
                provider="kiro_cli",
                agent_profile="reviewer",
            )

            response = client.post(
                "/sessions/test-session/terminals",
                params={
                    "provider": "kiro_cli",
                    "agent_profile": "reviewer",
                },
            )

            assert response.status_code == 201
            call_kwargs = mock_svc.create_terminal.call_args.kwargs
            assert call_kwargs["provider"] == "kiro_cli"

    def test_create_session_does_not_resolve_provider(self, client):
        """create_session should NOT call resolve_provider — CLI flag is the override."""
        with (
            patch("cli_agent_orchestrator.api.main.resolve_provider") as mock_resolve,
            patch("cli_agent_orchestrator.api.main.session_service") as mock_svc,
        ):
            mock_svc.create_session.return_value = Terminal(
                id="abcd1234",
                name="test-window",
                session_name="test-session",
                provider="kiro_cli",
                agent_profile="supervisor",
            )

            response = client.post(
                "/sessions",
                params={
                    "provider": "kiro_cli",
                    "agent_profile": "supervisor",
                },
            )

            assert response.status_code == 201
            # resolve_provider should NOT have been called
            mock_resolve.assert_not_called()
            # session_service should get the raw provider param
            call_kwargs = mock_svc.create_session.call_args.kwargs
            assert call_kwargs["provider"] == "kiro_cli"

    def test_create_terminal_returns_500_on_resolve_error(self, client):
        """Internal errors during provider resolution should return 500."""
        with (
            patch("cli_agent_orchestrator.api.main.resolve_provider") as mock_resolve,
            patch("cli_agent_orchestrator.api.main.terminal_service"),
        ):
            mock_resolve.side_effect = Exception("Unexpected filesystem error")

            response = client.post(
                "/sessions/test-session/terminals",
                params={
                    "provider": "kiro_cli",
                    "agent_profile": "developer",
                },
            )

            assert response.status_code == 500
            assert "Failed to create terminal" in response.json()["detail"]
