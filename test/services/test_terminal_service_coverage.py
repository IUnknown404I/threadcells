"""Additional terminal_service tests for coverage gaps.

Covers: create_terminal error cleanup, delete_terminal internals,
and the SESSION_PREFIX branch.
"""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cli_agent_orchestrator.models.agent_profile import AgentProfile


@pytest.fixture(autouse=True)
def isolate_operational_admission(monkeypatch):
    """Construction coverage tests isolate from live host admission state."""
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.context_launch_admission",
        lambda **kwargs: nullcontext({}),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.control_plane_registry.registry_is_initialized",
        lambda: False,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service._capture_created_runtime_identity",
        lambda _session, _window, terminal_id, generation: SimpleNamespace(
            pane_id="%41",
            pane_pid=4242,
            terminal_id=terminal_id,
            runtime_generation=generation,
            process_start_ticks=777,
        ),
    )
    resolved_session_name: dict[str, str | None] = {"value": None}

    def resolve_session(identifier):
        if identifier != "stable-existing-session":
            resolved_session_name["value"] = identifier
        return {
            "session_id": "stable-existing-session",
            "session_name": resolved_session_name["value"] or identifier,
            "deleted": False,
            "terminals": [{"id": "resident", "runtime_lifecycle": "running"}],
        }

    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.resolve_session_lifetime",
        resolve_session,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.prove_live_session_runtime_authority",
        lambda _name, _terminals, **_kwargs: SimpleNamespace(
            proven=True, reason_code=None, inventory_uncertain=False
        ),
    )


class TestCreateTerminalCleanup:
    """Test error cleanup paths in create_terminal."""

    @patch("cli_agent_orchestrator.services.terminal_service.TERMINAL_LOG_DIR")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.db_create_terminal")
    @patch(
        "cli_agent_orchestrator.services.terminal_service.generate_window_name", return_value="w1"
    )
    @patch(
        "cli_agent_orchestrator.services.terminal_service.generate_terminal_id",
        return_value="tid1",
    )
    @patch("cli_agent_orchestrator.services.terminal_service.load_agent_profile")
    def test_cleanup_on_provider_init_failure(
        self,
        mock_load_profile,
        mock_tid,
        mock_wname,
        mock_db_create,
        mock_pm,
        mock_tmux,
        mock_log_dir,
    ):
        """When provider.initialize() fails, cleanup should kill session and cleanup provider."""
        from cli_agent_orchestrator.services.terminal_service import create_terminal

        mock_tmux.session_exists.return_value = False
        mock_tmux.create_session.return_value = "w1"
        mock_load_profile.return_value = AgentProfile(name="dev", description="Dev")

        mock_provider = MagicMock()
        mock_provider.initialize.side_effect = Exception("Provider init failed")
        mock_pm.create_provider.return_value = mock_provider

        with pytest.raises(Exception, match="Provider init failed"):
            create_terminal(
                provider="kiro_cli",
                agent_profile="dev",
                session_name="test-ses",
                new_session=True,
                allowed_tools=["*"],
            )

        mock_pm.cleanup_provider.assert_called_once_with("tid1")
        mock_tmux.kill_session.assert_called_once()

    @patch("cli_agent_orchestrator.services.terminal_service.TERMINAL_LOG_DIR")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.db_create_terminal")
    @patch(
        "cli_agent_orchestrator.services.terminal_service.generate_window_name", return_value="w1"
    )
    @patch(
        "cli_agent_orchestrator.services.terminal_service.generate_terminal_id",
        return_value="tid1",
    )
    @patch("cli_agent_orchestrator.services.terminal_service.load_agent_profile")
    def test_cleanup_on_failure_does_not_kill_session_if_not_new(
        self,
        mock_load_profile,
        mock_tid,
        mock_wname,
        mock_db_create,
        mock_pm,
        mock_tmux,
        mock_log_dir,
    ):
        """When new_session=False, cleanup should NOT kill the session."""
        from cli_agent_orchestrator.services.terminal_service import create_terminal

        mock_tmux.session_exists.return_value = True
        mock_tmux.create_window.return_value = "w1"
        mock_load_profile.return_value = AgentProfile(name="dev", description="Dev")

        mock_provider = MagicMock()
        mock_provider.initialize.side_effect = Exception("fail")
        mock_pm.create_provider.return_value = mock_provider

        with pytest.raises(Exception):
            create_terminal(
                provider="kiro_cli",
                agent_profile="dev",
                session_name="cao-existing",
                new_session=False,
                allowed_tools=["*"],
            )

        mock_pm.cleanup_provider.assert_called_once()
        mock_tmux.kill_session.assert_not_called()

    @patch("cli_agent_orchestrator.services.terminal_service.TERMINAL_LOG_DIR")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.db_create_terminal")
    @patch(
        "cli_agent_orchestrator.services.terminal_service.generate_window_name", return_value="w1"
    )
    @patch(
        "cli_agent_orchestrator.services.terminal_service.generate_terminal_id",
        return_value="tid1",
    )
    @patch("cli_agent_orchestrator.services.terminal_service.load_agent_profile")
    def test_cleanup_ignores_cleanup_errors(
        self,
        mock_load_profile,
        mock_tid,
        mock_wname,
        mock_db_create,
        mock_pm,
        mock_tmux,
        mock_log_dir,
    ):
        """Cleanup errors should be swallowed, original error re-raised."""
        from cli_agent_orchestrator.services.terminal_service import create_terminal

        mock_tmux.session_exists.return_value = False
        mock_tmux.create_session.return_value = "w1"
        mock_load_profile.return_value = AgentProfile(name="dev", description="Dev")

        mock_provider = MagicMock()
        mock_provider.initialize.side_effect = Exception("original error")
        mock_pm.create_provider.return_value = mock_provider
        mock_pm.cleanup_provider.side_effect = Exception("cleanup error")
        mock_tmux.kill_session.side_effect = Exception("kill error")

        with pytest.raises(Exception, match="original error"):
            create_terminal(
                provider="kiro_cli",
                agent_profile="dev",
                session_name="test-ses",
                new_session=True,
                allowed_tools=["*"],
            )

    @patch("cli_agent_orchestrator.services.terminal_service.TERMINAL_LOG_DIR")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.db_create_terminal")
    @patch(
        "cli_agent_orchestrator.services.terminal_service.generate_window_name", return_value="w1"
    )
    @patch(
        "cli_agent_orchestrator.services.terminal_service.generate_terminal_id",
        return_value="tid1",
    )
    @patch("cli_agent_orchestrator.services.terminal_service.load_agent_profile")
    def test_session_prefix_added_for_new_session(
        self,
        mock_load_profile,
        mock_tid,
        mock_wname,
        mock_db_create,
        mock_pm,
        mock_tmux,
        mock_log_dir,
    ):
        """New sessions without the prefix get it added automatically."""
        from cli_agent_orchestrator.services.terminal_service import create_terminal

        mock_tmux.session_exists.return_value = False
        mock_tmux.create_session.return_value = "w1"
        mock_load_profile.return_value = AgentProfile(name="dev", description="Dev")
        mock_provider = MagicMock()
        mock_pm.create_provider.return_value = mock_provider
        mock_log_dir.__truediv__ = MagicMock(return_value=MagicMock())

        result = create_terminal(
            provider="kiro_cli",
            agent_profile="dev",
            session_name="myses",
            new_session=True,
            allowed_tools=["*"],
        )

        # session_name should have been prefixed with "cao-"
        args = mock_tmux.create_session.call_args
        assert args[0][0] == "cao-myses"


class TestCreateTerminalSessionCleanupGuard:
    """Regression tests for session_created guard (fix/terminal-service-session-cleanup).

    Ensures cleanup only kills sessions that THIS call actually created,
    preventing destruction of pre-existing sessions on error.
    """

    @patch("cli_agent_orchestrator.services.terminal_service.TERMINAL_LOG_DIR")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.db_create_terminal")
    @patch(
        "cli_agent_orchestrator.services.terminal_service.generate_window_name", return_value="w1"
    )
    @patch(
        "cli_agent_orchestrator.services.terminal_service.generate_terminal_id",
        return_value="tid1",
    )
    @patch("cli_agent_orchestrator.services.terminal_service.load_agent_profile")
    def test_no_kill_session_when_session_already_exists(
        self,
        mock_load_profile,
        mock_tid,
        mock_wname,
        mock_db_create,
        mock_pm,
        mock_tmux,
        mock_log_dir,
    ):
        """When session already exists, cleanup must NOT kill the pre-existing session."""
        from cli_agent_orchestrator.services.terminal_service import create_terminal

        mock_tmux.session_exists.return_value = True  # session already exists

        with pytest.raises(ValueError, match="already exists"):
            create_terminal(
                provider="kiro_cli",
                agent_profile="dev",
                session_name="cao-foo",
                new_session=True,
                allowed_tools=["*"],
            )

        mock_tmux.kill_session.assert_not_called()

    @patch("cli_agent_orchestrator.services.terminal_service.TERMINAL_LOG_DIR")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.db_create_terminal")
    @patch(
        "cli_agent_orchestrator.services.terminal_service.generate_window_name", return_value="w1"
    )
    @patch(
        "cli_agent_orchestrator.services.terminal_service.generate_terminal_id",
        return_value="tid1",
    )
    @patch("cli_agent_orchestrator.services.terminal_service.load_agent_profile")
    def test_kill_session_when_we_created_it_and_later_step_fails(
        self,
        mock_load_profile,
        mock_tid,
        mock_wname,
        mock_db_create,
        mock_pm,
        mock_tmux,
        mock_log_dir,
    ):
        """When we successfully created the session but a later step fails, cleanup SHOULD kill it."""
        from cli_agent_orchestrator.services.terminal_service import create_terminal

        mock_tmux.session_exists.return_value = False
        mock_tmux.create_session.return_value = "w1"
        mock_load_profile.return_value = AgentProfile(name="dev", description="Dev")

        mock_provider = MagicMock()
        mock_provider.initialize.side_effect = Exception("provider init failed")
        mock_pm.create_provider.return_value = mock_provider

        with pytest.raises(Exception, match="provider init failed"):
            create_terminal(
                provider="kiro_cli",
                agent_profile="dev",
                session_name="test-ses",
                new_session=True,
                allowed_tools=["*"],
            )

        mock_tmux.kill_session.assert_called_once()


class TestDeleteTerminal:
    """Test delete_terminal coverage including pipe-pane and kill_window."""

    @patch("cli_agent_orchestrator.services.terminal_service.db_delete_terminal", return_value=True)
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_metadata")
    def test_delete_terminal_full_path(self, mock_meta, mock_tmux, mock_pm, mock_db_del):
        """Delete should stop pipe-pane, kill window, cleanup provider, delete DB record."""
        from cli_agent_orchestrator.services.terminal_service import delete_terminal

        mock_meta.return_value = {"tmux_session": "ses", "tmux_window": "win"}

        result = delete_terminal("tid1")

        assert result is True
        mock_tmux.stop_pipe_pane.assert_called_once_with("ses", "win")
        mock_tmux.kill_window.assert_called_once_with("ses", "win")
        mock_pm.cleanup_provider.assert_called_once_with("tid1")

    @patch("cli_agent_orchestrator.services.terminal_service.db_delete_terminal", return_value=True)
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_metadata")
    def test_delete_terminal_pipe_pane_failure_continues(
        self, mock_meta, mock_tmux, mock_pm, mock_db_del
    ):
        """Pipe-pane failure should be logged and not block deletion."""
        from cli_agent_orchestrator.services.terminal_service import delete_terminal

        mock_meta.return_value = {"tmux_session": "ses", "tmux_window": "win"}
        mock_tmux.stop_pipe_pane.side_effect = Exception("pipe error")

        result = delete_terminal("tid1")

        assert result is True
        mock_tmux.kill_window.assert_called_once()

    @patch("cli_agent_orchestrator.services.terminal_service.db_delete_terminal", return_value=True)
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_metadata")
    def test_delete_terminal_kill_window_failure_retains_metadata_and_lease(
        self, mock_meta, mock_tmux, mock_pm, mock_db_del
    ):
        """Uncertain kill must not reopen the writer worktree."""
        from cli_agent_orchestrator.services.terminal_service import delete_terminal

        mock_meta.return_value = {"tmux_session": "ses", "tmux_window": "win"}
        mock_tmux.kill_window.side_effect = Exception("kill error")
        mock_tmux.window_exists.return_value = None

        with pytest.raises(RuntimeError, match="metadata and writer lease retained"):
            delete_terminal("tid1")
        mock_pm.cleanup_provider.assert_not_called()
        mock_db_del.assert_not_called()

    @patch("cli_agent_orchestrator.services.terminal_service.db_delete_terminal", return_value=True)
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_metadata")
    def test_delete_terminal_releases_after_positive_absence(
        self, mock_meta, mock_tmux, mock_pm, mock_db_del
    ):
        from cli_agent_orchestrator.services.terminal_service import delete_terminal

        mock_meta.return_value = {"tmux_session": "ses", "tmux_window": "win"}
        mock_tmux.kill_window.return_value = False
        mock_tmux.window_exists.return_value = False

        assert delete_terminal("tid1") is True
        mock_db_del.assert_called_once_with("tid1")

    @patch("cli_agent_orchestrator.services.terminal_service.db_delete_terminal")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_metadata")
    def test_delete_terminal_db_failure_raises(self, mock_meta, mock_tmux, mock_pm, mock_db_del):
        """DB delete failure should propagate."""
        from cli_agent_orchestrator.services.terminal_service import delete_terminal

        mock_meta.return_value = {"tmux_session": "ses", "tmux_window": "win"}
        mock_db_del.side_effect = Exception("DB error")

        with pytest.raises(Exception, match="DB error"):
            delete_terminal("tid1")
