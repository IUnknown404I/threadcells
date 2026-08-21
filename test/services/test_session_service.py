"""Tests for the session service."""

from unittest.mock import MagicMock, patch

import pytest

from cli_agent_orchestrator.services.session_service import (
    delete_session,
    get_session,
    get_session_root_working_directory,
    list_sessions,
)


class TestListSessions:
    """Tests for list_sessions function."""

    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_list_sessions_success(self, mock_tmux):
        """Test listing sessions successfully."""
        mock_tmux.list_sessions.return_value = [
            {"id": "cao-session1", "name": "Session 1", "created_at": "100"},
            {"id": "cao-session2", "name": "Session 2", "created_at": "200"},
            {"id": "other-session", "name": "Other"},
        ]

        result = list_sessions()

        assert len(result) == 2
        assert all(s["id"].startswith("cao-") for s in result)
        assert [s["id"] for s in result] == ["cao-session2", "cao-session1"]

    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_list_sessions_sorts_equal_creation_times_by_id_descending(self, mock_tmux):
        mock_tmux.list_sessions.return_value = [
            {"id": "cao-alpha", "name": "alpha", "created_at": "200"},
            {"id": "cao-zeta", "name": "zeta", "created_at": "200"},
        ]

        assert [s["id"] for s in list_sessions()] == ["cao-zeta", "cao-alpha"]

    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_list_sessions_status_does_not_affect_order(self, mock_tmux):
        mock_tmux.list_sessions.return_value = [
            {"id": "cao-older", "name": "older", "created_at": "100", "status": "active"},
            {"id": "cao-newer", "name": "newer", "created_at": "200", "status": "detached"},
        ]

        before = [s["id"] for s in list_sessions()]
        mock_tmux.list_sessions.return_value[0]["status"] = "detached"
        mock_tmux.list_sessions.return_value[1]["status"] = "active"

        assert [s["id"] for s in list_sessions()] == before

    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_list_sessions_keeps_generated_names(self, mock_tmux):
        mock_tmux.list_sessions.return_value = [
            {"id": "cao-0123abcd", "name": "cao-0123abcd", "created_at": "100"},
        ]

        assert list_sessions()[0]["id"] == "cao-0123abcd"

    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_list_sessions_empty(self, mock_tmux):
        """Test listing sessions when none exist."""
        mock_tmux.list_sessions.return_value = []

        result = list_sessions()

        assert result == []

    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_list_sessions_no_cao_sessions(self, mock_tmux):
        """Test listing sessions when no CAO sessions exist."""
        mock_tmux.list_sessions.return_value = [
            {"id": "other-session1", "name": "Other 1"},
            {"id": "other-session2", "name": "Other 2"},
        ]

        result = list_sessions()

        assert result == []

    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_list_sessions_error(self, mock_tmux):
        """Test listing sessions with error."""
        mock_tmux.list_sessions.side_effect = Exception("Tmux error")

        with pytest.raises(RuntimeError, match="Could not inventory tmux sessions"):
            list_sessions()

    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_list_sessions_uncertain_inventory_does_not_become_empty(self, mock_tmux):
        mock_tmux.list_sessions.return_value = None

        with pytest.raises(RuntimeError, match="Could not inventory tmux sessions"):
            list_sessions()


class TestGetSession:
    """Tests for get_session function."""

    @patch("cli_agent_orchestrator.services.session_service.list_terminals_by_session")
    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_get_session_success(self, mock_tmux, mock_list_terminals):
        """Test getting session successfully."""
        mock_tmux.session_exists.return_value = True
        mock_tmux.list_sessions.return_value = [{"id": "cao-test", "name": "Test Session"}]
        mock_list_terminals.return_value = [{"id": "terminal1", "session": "cao-test"}]

        result = get_session("cao-test")

        assert result["session"]["id"] == "cao-test"
        assert len(result["terminals"]) == 1
        mock_tmux.session_exists.assert_called_once_with("cao-test")

    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_get_session_not_found(self, mock_tmux):
        """Test getting non-existent session."""
        mock_tmux.session_exists.return_value = False

        with pytest.raises(ValueError, match="Session 'cao-nonexistent' not found"):
            get_session("cao-nonexistent")

    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_get_session_inventory_uncertain(self, mock_tmux):
        mock_tmux.session_exists.return_value = None

        with pytest.raises(RuntimeError, match="Could not determine whether session"):
            get_session("cao-uncertain")

    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_get_session_not_in_list(self, mock_tmux):
        """Test getting session that exists but not in list."""
        mock_tmux.session_exists.return_value = True
        mock_tmux.list_sessions.return_value = []

        with pytest.raises(ValueError, match="Session 'cao-test' not found"):
            get_session("cao-test")

    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_get_session_error(self, mock_tmux):
        """Test getting session with error."""
        mock_tmux.session_exists.side_effect = Exception("Tmux error")

        with pytest.raises(Exception, match="Tmux error"):
            get_session("cao-test")


class TestGetSessionRootWorkingDirectory:
    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_inventory_uncertain_does_not_read_root(self, mock_tmux):
        mock_tmux.session_exists.return_value = None

        with pytest.raises(RuntimeError, match="Could not determine whether session"):
            get_session_root_working_directory("cao-uncertain")

        mock_tmux.get_session_root_working_directory.assert_not_called()


class TestDeleteSession:
    """Tests for delete_session function."""

    @patch("cli_agent_orchestrator.services.session_service.delete_terminals_by_session")
    @patch("cli_agent_orchestrator.services.session_service.provider_manager")
    @patch("cli_agent_orchestrator.services.session_service.cancel_workflows_for_terminal")
    @patch("cli_agent_orchestrator.services.session_service.list_terminals_by_session")
    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_delete_session_success(
        self,
        mock_tmux,
        mock_list_terminals,
        mock_cancel_workflows,
        mock_provider_manager,
        mock_delete_terminals,
    ):
        """Test deleting session successfully."""
        mock_tmux.session_exists.return_value = True
        mock_list_terminals.return_value = [
            {"id": "terminal1"},
            {"id": "terminal2"},
        ]

        result = delete_session("cao-test")

        assert result == {"deleted": ["cao-test"], "errors": []}
        mock_tmux.kill_session.assert_called_once_with("cao-test")
        mock_delete_terminals.assert_called_once_with("cao-test")
        assert mock_provider_manager.cleanup_provider.call_count == 2
        mock_cancel_workflows.assert_any_call("terminal1")
        mock_cancel_workflows.assert_any_call("terminal2")

    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_delete_session_not_found(self, mock_tmux):
        """Test deleting non-existent session."""
        mock_tmux.session_exists.return_value = False

        with pytest.raises(ValueError, match="Session 'cao-nonexistent' not found"):
            delete_session("cao-nonexistent")

    @patch("cli_agent_orchestrator.services.session_service.list_terminals_by_session")
    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_delete_session_inventory_uncertain_stops_before_cleanup(
        self, mock_tmux, mock_list_terminals
    ):
        mock_tmux.session_exists.return_value = None

        with pytest.raises(RuntimeError, match="Could not determine whether session"):
            delete_session("cao-uncertain")

        mock_list_terminals.assert_not_called()
        mock_tmux.kill_session.assert_not_called()

    @patch("cli_agent_orchestrator.services.session_service.delete_terminals_by_session")
    @patch("cli_agent_orchestrator.services.session_service.provider_manager")
    @patch("cli_agent_orchestrator.services.session_service.list_terminals_by_session")
    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_delete_session_retains_metadata_and_leases_when_kill_is_uncertain(
        self, mock_tmux, mock_list_terminals, mock_provider_manager, mock_delete_terminals
    ):
        mock_tmux.session_exists.return_value = True
        mock_tmux.kill_session.return_value = False
        mock_list_terminals.return_value = []

        with pytest.raises(RuntimeError, match="writer leases retained"):
            delete_session("cao-test")

        mock_delete_terminals.assert_not_called()

    @patch("cli_agent_orchestrator.services.session_service.delete_terminals_by_session")
    @patch("cli_agent_orchestrator.services.session_service.provider_manager")
    @patch("cli_agent_orchestrator.services.session_service.list_terminals_by_session")
    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_delete_session_no_terminals(
        self, mock_tmux, mock_list_terminals, mock_provider_manager, mock_delete_terminals
    ):
        """Test deleting session with no terminals."""
        mock_tmux.session_exists.return_value = True
        mock_list_terminals.return_value = []

        result = delete_session("cao-test")

        assert result == {"deleted": ["cao-test"], "errors": []}
        mock_provider_manager.cleanup_provider.assert_not_called()

    @patch("cli_agent_orchestrator.services.session_service.list_terminals_by_session")
    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_delete_session_error(self, mock_tmux, mock_list_terminals):
        """Test deleting session with error."""
        mock_tmux.session_exists.return_value = True
        mock_list_terminals.side_effect = Exception("Database error")

        with pytest.raises(Exception, match="Database error"):
            delete_session("cao-test")

    @patch("cli_agent_orchestrator.services.session_service.delete_terminals_by_session")
    @patch("cli_agent_orchestrator.services.session_service.provider_manager")
    @patch("cli_agent_orchestrator.services.session_service.list_terminals_by_session")
    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_delete_session_continues_when_provider_cleanup_fails(
        self, mock_tmux, mock_list_terminals, mock_provider_manager, mock_delete_terminals
    ):
        """Test that delete_session continues even when provider cleanup fails for some terminals."""
        mock_tmux.session_exists.return_value = True
        mock_list_terminals.return_value = [
            {"id": "terminal1"},
            {"id": "terminal2"},
            {"id": "terminal3"},
        ]

        # First terminal cleanup fails, others succeed
        mock_provider_manager.cleanup_provider.side_effect = [
            Exception("Provider cleanup error for terminal1"),
            None,  # terminal2 succeeds
            None,  # terminal3 succeeds
        ]

        result = delete_session("cao-test")

        # Session should still be deleted despite provider cleanup failure
        assert result == {"deleted": ["cao-test"], "errors": []}
        mock_tmux.kill_session.assert_called_once_with("cao-test")
        mock_delete_terminals.assert_called_once_with("cao-test")
        # All three provider cleanups were attempted
        assert mock_provider_manager.cleanup_provider.call_count == 3

    @patch("cli_agent_orchestrator.services.session_service.delete_terminals_by_session")
    @patch("cli_agent_orchestrator.services.session_service.provider_manager")
    @patch("cli_agent_orchestrator.services.session_service.list_terminals_by_session")
    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_delete_session_cleans_up_provider_for_each_terminal(
        self, mock_tmux, mock_list_terminals, mock_provider_manager, mock_delete_terminals
    ):
        """Test that delete_session calls cleanup_provider for every terminal in the session."""
        mock_tmux.session_exists.return_value = True
        mock_list_terminals.return_value = [
            {"id": "term-aaa"},
            {"id": "term-bbb"},
            {"id": "term-ccc"},
            {"id": "term-ddd"},
        ]

        result = delete_session("cao-multi-terminal")

        assert result == {"deleted": ["cao-multi-terminal"], "errors": []}
        # Verify cleanup_provider was called for each terminal with the correct ID
        expected_calls = [
            (("term-aaa",),),
            (("term-bbb",),),
            (("term-ccc",),),
            (("term-ddd",),),
        ]
        assert mock_provider_manager.cleanup_provider.call_count == 4
        mock_provider_manager.cleanup_provider.assert_any_call("term-aaa")
        mock_provider_manager.cleanup_provider.assert_any_call("term-bbb")
        mock_provider_manager.cleanup_provider.assert_any_call("term-ccc")
        mock_provider_manager.cleanup_provider.assert_any_call("term-ddd")
