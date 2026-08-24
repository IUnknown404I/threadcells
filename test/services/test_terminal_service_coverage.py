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
    """Exited-only deletion and exact runtime authority."""

    @staticmethod
    def _metadata(lifecycle="exited"):
        return {
            "id": "tid1",
            "tmux_session": "ses",
            "tmux_window": "win",
            "runtime_lifecycle": lifecycle,
        }

    def test_delete_terminal_full_path(self):
        from cli_agent_orchestrator.services.terminal_service import delete_terminal

        metadata = self._metadata()
        with (
            patch(
                "cli_agent_orchestrator.services.terminal_service.get_terminal_metadata",
                return_value=metadata,
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.prepare_terminal_for_destruction"
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.retire_exited_terminal_runtime",
                return_value=True,
            ) as retire,
            patch(
                "cli_agent_orchestrator.services.terminal_service.mark_terminal_runtime_exited",
                return_value=True,
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.db_delete_exited_terminal",
                return_value={"deleted": 1, "already_deleted": False, "missing": False},
            ) as db_delete,
            patch("cli_agent_orchestrator.services.terminal_service.provider_manager") as manager,
        ):
            assert delete_terminal("tid1") is True

        retire.assert_called_once_with("tid1")
        manager.cleanup_provider.assert_called_once_with("tid1")
        db_delete.assert_called_once()

    @pytest.mark.parametrize(
        ("retirement", "reason_code"),
        [
            (False, "TERMINAL_DEATH_UNCONFIRMED"),
            (None, "TERMINAL_RUNTIME_AUTHORITY_UNCERTAIN"),
        ],
    )
    def test_delete_terminal_uncertain_runtime_is_protected(self, retirement, reason_code):
        from cli_agent_orchestrator.services.terminal_service import (
            TerminalDeletionError,
            delete_terminal,
        )

        with (
            patch(
                "cli_agent_orchestrator.services.terminal_service.get_terminal_metadata",
                return_value=self._metadata(),
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.prepare_terminal_for_destruction"
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.retire_exited_terminal_runtime",
                return_value=retirement,
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.db_delete_exited_terminal"
            ) as db_delete,
        ):
            with pytest.raises(TerminalDeletionError) as raised:
                delete_terminal("tid1")

        assert raised.value.reason_code == reason_code
        db_delete.assert_not_called()

    def test_delete_terminal_active_runtime_is_protected_before_snapshot(self):
        from cli_agent_orchestrator.services.terminal_service import (
            TerminalDeletionError,
            delete_terminal,
        )

        with (
            patch(
                "cli_agent_orchestrator.services.terminal_service.get_terminal_metadata",
                return_value=self._metadata("running"),
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.prepare_terminal_for_destruction"
            ) as prepare,
        ):
            with pytest.raises(TerminalDeletionError) as raised:
                delete_terminal("tid1")

        assert raised.value.reason_code == "TERMINAL_RUNTIME_ACTIVE"
        prepare.assert_not_called()

    def test_delete_terminal_identity_change_after_retirement_is_protected(self):
        from cli_agent_orchestrator.services.terminal_service import (
            TerminalDeletionError,
            delete_terminal,
        )

        changed = {**self._metadata(), "runtime_generation": "replacement"}
        with (
            patch(
                "cli_agent_orchestrator.services.terminal_service.get_terminal_metadata",
                side_effect=[self._metadata(), changed],
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.prepare_terminal_for_destruction"
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.retire_exited_terminal_runtime",
                return_value=True,
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.mark_terminal_runtime_exited"
            ) as mark_exited,
            patch(
                "cli_agent_orchestrator.services.terminal_service.db_delete_exited_terminal"
            ) as db_delete,
        ):
            with pytest.raises(TerminalDeletionError) as raised:
                delete_terminal("tid1")

        assert raised.value.reason_code == "TERMINAL_IDENTITY_CHANGED"
        mark_exited.assert_not_called()
        db_delete.assert_not_called()

    def test_delete_terminal_db_failure_raises(self):
        from cli_agent_orchestrator.services.terminal_service import delete_terminal

        with (
            patch(
                "cli_agent_orchestrator.services.terminal_service.get_terminal_metadata",
                return_value=self._metadata(),
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.prepare_terminal_for_destruction"
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.retire_exited_terminal_runtime",
                return_value=True,
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.mark_terminal_runtime_exited",
                return_value=True,
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.db_delete_exited_terminal",
                side_effect=RuntimeError("DB error"),
            ),
        ):
            with pytest.raises(RuntimeError, match="DB error"):
                delete_terminal("tid1")
