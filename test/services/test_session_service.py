"""Tests for the session service."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cli_agent_orchestrator.services.session_service import (
    SessionLifecycleError,
    delete_session,
    get_session,
    get_session_root_working_directory,
    list_sessions,
    resolve_session_authority,
)
from cli_agent_orchestrator.services.terminal_service import ManagedWorktreeCleanupError


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


def _durable_session(
    *,
    lifecycle: str = "running",
    deleted: bool = False,
    retained_resources: list[dict[str, str]] | None = None,
):
    return {
        "session_id": "session-lifetime-1",
        "session_name": "cao-test",
        "deleted": deleted,
        "retained_resources": retained_resources or [],
        "terminals": (
            []
            if deleted
            else [
                {
                    "id": "terminal1",
                    "tmux_session": "cao-test",
                    "session_id": "session-lifetime-1",
                    "tmux_window": "developer-one",
                    "runtime_lifecycle": lifecycle,
                    "runtime_pane_id": "%1",
                    "runtime_pane_pid": 1001,
                    "runtime_generation": "generation-one",
                    "runtime_generation_origin": "launch",
                    "runtime_process_start_ticks": 2001,
                },
                {
                    "id": "terminal2",
                    "tmux_session": "cao-test",
                    "session_id": "session-lifetime-1",
                    "tmux_window": "developer-two",
                    "runtime_lifecycle": lifecycle,
                    "runtime_pane_id": "%2",
                    "runtime_pane_pid": 1002,
                    "runtime_generation": "generation-two",
                    "runtime_generation_origin": "launch",
                    "runtime_process_start_ticks": 2002,
                },
            ]
        ),
    }


def _runtime_target(_session_name, window_name):
    index = 1 if window_name == "developer-one" else 2
    return SimpleNamespace(
        pane_id=f"%{index}",
        pane_pid=1000 + index,
        terminal_id=f"terminal{index}",
        runtime_generation=f"generation-{'one' if index == 1 else 'two'}",
        process_start_ticks=2000 + index,
        generation_inherited=True,
    )


def _runtime_windows():
    return [
        {"name": "developer-one", "index": "0"},
        {"name": "developer-two", "index": "1"},
    ]


class TestSessionAuthority:
    @patch("cli_agent_orchestrator.services.session_service.resolve_session_lifetime")
    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_get_session_resolves_stable_lifetime(self, mock_tmux, resolve):
        resolve.return_value = _durable_session()
        mock_tmux.session_exists.return_value = True
        mock_tmux.get_session_windows.return_value = _runtime_windows()
        mock_tmux.exact_runtime_target.side_effect = _runtime_target
        mock_tmux.list_sessions.return_value = [{"id": "cao-test", "name": "cao-test"}]

        result = get_session("session-lifetime-1")

        assert result["session"]["id"] == "cao-test"
        assert [row["id"] for row in result["terminals"]] == ["terminal1", "terminal2"]
        resolve.assert_called_once_with("session-lifetime-1")

    @patch("cli_agent_orchestrator.services.session_service.resolve_session_lifetime")
    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_history_is_truthful_conflict_for_live_actions(self, mock_tmux, resolve):
        resolve.return_value = _durable_session(lifecycle="exited")
        mock_tmux.session_exists.return_value = False

        with pytest.raises(SessionLifecycleError) as error:
            resolve_session_authority("session-lifetime-1", require_live=True)

        assert error.value.reason_code == "SESSION_HISTORY_INELIGIBLE"

    @patch("cli_agent_orchestrator.services.session_service.resolve_session_lifetime")
    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_inventory_uncertainty_remains_fail_closed(self, mock_tmux, resolve):
        resolve.return_value = _durable_session()
        mock_tmux.session_exists.return_value = None

        with pytest.raises(SessionLifecycleError) as error:
            get_session_root_working_directory("session-lifetime-1")

        assert error.value.reason_code == "SESSION_RUNTIME_INVENTORY_UNCERTAIN"
        assert error.value.inventory_uncertain is True
        mock_tmux.get_session_root_working_directory.assert_not_called()


class TestDeleteSession:
    def _patch_common(self, durable):
        return (
            patch(
                "cli_agent_orchestrator.services.session_service.resolve_session_lifetime",
                return_value=durable,
            ),
            patch(
                "cli_agent_orchestrator.services.session_service.prepare_terminal_for_destruction"
            ),
            patch("cli_agent_orchestrator.services.session_service.cancel_workflows_for_terminal"),
            patch(
                "cli_agent_orchestrator.services.session_service.validate_managed_worktree_cleanup"
            ),
            patch("cli_agent_orchestrator.services.session_service.cleanup_managed_worktree"),
            patch("cli_agent_orchestrator.services.session_service.provider_manager"),
            patch(
                "cli_agent_orchestrator.services.session_service.delete_terminals_by_session_lifetime",
                return_value={
                    "deleted": len(durable["terminals"]),
                    "logical_deleted": len(durable["terminals"]),
                    "retained": 0,
                    "retained_resources": [],
                    "already_deleted": False,
                },
            ),
            patch("cli_agent_orchestrator.services.inbox_service.wake_provider_execution_queue"),
        )

    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_live_session_is_truthfully_blocked_before_mutation(self, mock_tmux):
        mock_tmux.session_exists.return_value = True
        contexts = self._patch_common(_durable_session())
        with (
            contexts[0] as resolve,
            contexts[1] as prepare,
            contexts[2] as cancel,
            contexts[3] as validate,
            contexts[4] as cleanup,
            contexts[5] as providers,
            contexts[6] as delete,
            contexts[7],
        ):
            with pytest.raises(SessionLifecycleError) as error:
                delete_session("session-lifetime-1")

        assert error.value.reason_code == "SESSION_RUNTIME_ACTIVE"
        resolve.assert_called_once_with("session-lifetime-1")
        prepare.assert_not_called()
        cancel.assert_not_called()
        validate.assert_not_called()
        cleanup.assert_not_called()
        providers.cleanup_provider.assert_not_called()
        delete.assert_not_called()

    @patch("cli_agent_orchestrator.services.session_service.retire_exited_terminal_runtime")
    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_historical_session_deletes_without_false_not_found(self, mock_tmux, retire):
        mock_tmux.session_exists.return_value = False
        retire.return_value = True
        contexts = self._patch_common(_durable_session(lifecycle="exited"))
        with (
            contexts[0],
            contexts[1],
            contexts[2],
            contexts[3],
            contexts[4],
            contexts[5],
            contexts[6] as delete,
            contexts[7],
        ):
            result = delete_session("session-lifetime-1")

        assert result["deleted"] == ["cao-test"]
        assert retire.call_count == 2
        mock_tmux.kill_session.assert_not_called()
        delete.assert_called_once_with(
            "session-lifetime-1",
            "cao-test",
            expected_terminal_ids=["terminal1", "terminal2"],
            retained_resources=[],
        )

    @patch("cli_agent_orchestrator.services.session_service.retire_exited_terminal_runtime")
    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_historical_session_tombstones_while_retaining_protected_worktree(
        self, mock_tmux, retire
    ):
        mock_tmux.session_exists.return_value = False
        retire.return_value = True
        contexts = self._patch_common(_durable_session(lifecycle="exited"))
        with (
            contexts[0],
            contexts[1],
            contexts[2],
            contexts[3] as validate,
            contexts[4] as cleanup,
            contexts[5],
            contexts[6] as delete,
            contexts[7],
        ):
            validate.side_effect = [
                None,
                ManagedWorktreeCleanupError("MANAGED_WORKTREE_DIRTY"),
            ]
            delete.return_value = {
                "deleted": 1,
                "logical_deleted": 2,
                "retained": 1,
                "retained_resources": [
                    {"terminal_id": "terminal2", "reason_code": "MANAGED_WORKTREE_DIRTY"}
                ],
                "already_deleted": False,
            }
            result = delete_session("session-lifetime-1")

        assert result["deleted"] == ["cao-test"]
        assert result["retained_resources"] == [
            {"terminal_id": "terminal2", "reason_code": "MANAGED_WORKTREE_DIRTY"}
        ]
        cleanup.assert_called_once_with(_durable_session(lifecycle="exited")["terminals"][0])
        delete.assert_called_once_with(
            "session-lifetime-1",
            "cao-test",
            expected_terminal_ids=["terminal1", "terminal2"],
            retained_resources=[
                {"terminal_id": "terminal2", "reason_code": "MANAGED_WORKTREE_DIRTY"}
            ],
        )

    @patch("cli_agent_orchestrator.services.session_service.resolve_session_lifetime")
    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_repeated_deletion_uses_durable_receipt(self, mock_tmux, resolve):
        retained = [{"terminal_id": "terminal2", "reason_code": "MANAGED_WORKTREE_DIRTY"}]
        resolve.return_value = _durable_session(deleted=True, retained_resources=retained)
        mock_tmux.session_exists.return_value = False

        result = delete_session("session-lifetime-1")

        assert result == {
            "deleted": [],
            "errors": [],
            "already_deleted": True,
            "retained_resources": retained,
        }

    @patch("cli_agent_orchestrator.services.session_service.retire_exited_terminal_runtime")
    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_ambiguous_historical_runtime_remains_protected(self, mock_tmux, retire):
        mock_tmux.session_exists.return_value = True
        retire.return_value = None
        contexts = self._patch_common(_durable_session(lifecycle="exited"))
        with (
            contexts[0],
            contexts[1],
            contexts[2],
            contexts[3],
            contexts[4],
            contexts[5],
            contexts[6] as delete,
            contexts[7],
        ):
            with pytest.raises(SessionLifecycleError) as error:
                delete_session("session-lifetime-1")

        assert error.value.reason_code == "SESSION_RUNTIME_AUTHORITY_UNPROVEN"
        delete.assert_not_called()

    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_reused_live_name_is_protected_before_any_mutation(self, mock_tmux):
        mock_tmux.session_exists.return_value = True
        mock_tmux.get_session_windows.return_value = _runtime_windows()
        mismatched = _runtime_target("cao-test", "developer-one")
        mismatched = SimpleNamespace(**{**mismatched.__dict__, "terminal_id": "replacement"})
        mock_tmux.exact_runtime_target.return_value = mismatched
        contexts = self._patch_common(_durable_session())
        with (
            contexts[0],
            contexts[1] as prepare,
            contexts[2] as cancel,
            contexts[3],
            contexts[4] as cleanup,
            contexts[5] as providers,
            contexts[6] as delete,
            contexts[7],
        ):
            with pytest.raises(SessionLifecycleError) as error:
                delete_session("session-lifetime-1")

        assert error.value.reason_code == "SESSION_RUNTIME_ACTIVE"
        prepare.assert_not_called()
        cancel.assert_not_called()
        cleanup.assert_not_called()
        providers.cleanup_provider.assert_not_called()
        mock_tmux.kill_session.assert_not_called()
        delete.assert_not_called()

    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_untracked_live_window_is_protected_before_any_mutation(self, mock_tmux):
        mock_tmux.session_exists.return_value = True
        mock_tmux.get_session_windows.return_value = [
            *_runtime_windows(),
            {"name": "foreign-window", "index": "2"},
        ]
        mock_tmux.exact_runtime_target.side_effect = _runtime_target
        contexts = self._patch_common(_durable_session())
        with (
            contexts[0],
            contexts[1] as prepare,
            contexts[2] as cancel,
            contexts[3],
            contexts[4] as cleanup,
            contexts[5] as providers,
            contexts[6] as delete,
            contexts[7],
        ):
            with pytest.raises(SessionLifecycleError) as error:
                delete_session("session-lifetime-1")

        assert error.value.reason_code == "SESSION_RUNTIME_ACTIVE"
        prepare.assert_not_called()
        cancel.assert_not_called()
        cleanup.assert_not_called()
        providers.cleanup_provider.assert_not_called()
        mock_tmux.kill_session.assert_not_called()
        delete.assert_not_called()
