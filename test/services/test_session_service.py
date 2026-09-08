"""Tests for the session service."""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cli_agent_orchestrator.services.session_service import (
    SessionAuthority,
    SessionLifecycleError,
    _session_deletion_preflight,
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
    def test_recovery_fenced_history_is_not_a_live_runtime_owner(self, mock_tmux, resolve):
        resolve.return_value = _durable_session(lifecycle="recovery_fenced")
        mock_tmux.session_exists.return_value = False

        with pytest.raises(SessionLifecycleError) as error:
            resolve_session_authority("session-lifetime-1", require_live=True)

        assert error.value.reason_code == "SESSION_HISTORY_INELIGIBLE"
        mock_tmux.get_session_windows.assert_not_called()

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
    @pytest.fixture(autouse=True)
    def _stub_empty_unresolved_work_plan(self):
        """Keep service-unit deletion tests independent from ambient durable state."""
        with (
            patch(
                "cli_agent_orchestrator.services.session_service.get_session_unresolved_work_plan",
                return_value={
                    "eligible": True,
                    "cancellable": False,
                    "requires_cancellation_confirmation": False,
                    "plan_token": None,
                    "blockers": [],
                    "cancellable_count": 0,
                    "unsafe_count": 0,
                    "plan_limit": 500,
                    "reason_codes": [],
                },
            ),
            patch(
                "cli_agent_orchestrator.services.session_service.get_session_hard_deletion_operation",
                return_value=None,
            ),
            patch(
                "cli_agent_orchestrator.services.session_service.begin_session_hard_deletion",
                side_effect=lambda session_id, session_name, expected_terminal_ids, allow_dirty_workspace: {
                    "started": True,
                    "session_id": session_id,
                    "session_name": session_name,
                    "state": "fenced",
                    "terminal_ids": list(expected_terminal_ids),
                    "allow_dirty_workspace": allow_dirty_workspace,
                },
            ),
            patch(
                "cli_agent_orchestrator.services.session_service.mark_session_hard_deletion_workspace_retired",
                return_value={"marked": True},
            ),
            patch(
                "cli_agent_orchestrator.services.session_service.get_writable_work_context_by_session",
                return_value=None,
            ),
            patch(
                "cli_agent_orchestrator.services.session_service.housekeeping_mutation_fence",
                side_effect=lambda: nullcontext(),
            ),
            patch(
                "cli_agent_orchestrator.services.session_service.purge_session_terminal_artifacts",
                return_value={"runtime_artifacts_absent": True, "terminals": []},
            ),
            patch(
                "cli_agent_orchestrator.services.session_service.revalidate_session_hard_deletion",
                return_value={
                    "valid": True,
                    "terminal_ids": ["terminal1", "terminal2"],
                },
            ),
        ):
            yield

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
                "cli_agent_orchestrator.services.session_service.validate_managed_worktree_cleanup",
                create=True,
            ),
            patch(
                "cli_agent_orchestrator.services.session_service.purge_managed_worktree",
                return_value={
                    "removed": False,
                    "managed": False,
                    "path_absent": True,
                    "git_unregistered": True,
                    "branch_absent": True,
                },
            ),
            patch("cli_agent_orchestrator.services.session_service.provider_manager"),
            patch(
                "cli_agent_orchestrator.services.session_service.complete_session_hard_deletion",
                return_value={
                    "completed": True,
                    "already_deleted": False,
                    "before_counts": {"terminals": len(durable["terminals"])},
                    "after_counts": {"terminals": 0},
                    "tombstone_count": 1,
                },
            ),
            patch("cli_agent_orchestrator.services.inbox_service.wake_provider_execution_queue"),
        )

    def test_retired_absent_workspace_with_already_removed_branch_is_preflight_eligible(self):
        durable = _durable_session(lifecycle="exited")
        durable["terminals"][0].update(
            {
                "managed_worktree_kind": "supervisor",
                "writable_work_context_id": "context-1",
            }
        )
        authority = SessionAuthority(
            session_id="session-lifetime-1",
            session_name="cao-test",
            terminals=durable["terminals"],
            retained_resources=[],
            deleted=False,
            runtime_exists=False,
        )
        with (
            patch(
                "cli_agent_orchestrator.services.session_service.get_writable_work_context_by_session",
                return_value={"id": "context-1", "state": "retired"},
            ),
            patch(
                "cli_agent_orchestrator.services.managed_worktree_service.managed_worktree_status",
                return_value={
                    "managed": True,
                    "safe": False,
                    "absent": True,
                    "reason_code": "TASK_WORKTREE_BRANCH_MISSING",
                },
            ),
            patch(
                "cli_agent_orchestrator.services.interaction_read_model_service.list_session_current_queue_counts",
                return_value={"session-lifetime-1": 0},
            ),
        ):
            preflight = _session_deletion_preflight(authority)

        assert preflight["eligible"] is True
        assert preflight["deletion_mode"] == "eligible_normal"

    def test_retired_context_with_live_managed_worktree_is_preflight_unsafe(self):
        durable = _durable_session(lifecycle="exited")
        durable["terminals"][0].update(
            {
                "managed_worktree_kind": "supervisor",
                "writable_work_context_id": "context-1",
            }
        )
        authority = SessionAuthority(
            session_id="session-lifetime-1",
            session_name="cao-test",
            terminals=durable["terminals"],
            retained_resources=[],
            deleted=False,
            runtime_exists=False,
        )
        with (
            patch(
                "cli_agent_orchestrator.services.session_service.get_writable_work_context_by_session",
                return_value={"id": "context-1", "state": "retired"},
            ),
            patch(
                "cli_agent_orchestrator.services.managed_worktree_service.managed_worktree_status",
                return_value={
                    "managed": True,
                    "safe": True,
                    "absent": False,
                    "modified_files": 0,
                    "untracked_files": 0,
                },
            ),
            patch(
                "cli_agent_orchestrator.services.interaction_read_model_service.list_session_current_queue_counts",
                return_value={"session-lifetime-1": 0},
            ),
        ):
            preflight = _session_deletion_preflight(authority)

        assert preflight["eligible"] is False
        assert preflight["deletion_mode"] == "blocked_live_or_unsafe_authority"
        assert preflight["reason_code"] == "WORKSPACE_RETIREMENT_STATE_CONFLICT"

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

    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_recovery_fenced_session_is_retained_as_takeover_evidence(self, mock_tmux):
        mock_tmux.session_exists.return_value = False
        contexts = self._patch_common(_durable_session(lifecycle="recovery_fenced"))
        with (
            contexts[0],
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

        assert error.value.reason_code == "SESSION_RECOVERY_EVIDENCE_PROTECTED"
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
        delete.assert_called_once_with("session-lifetime-1", "cao-test")

    @patch("cli_agent_orchestrator.services.session_service.retire_exited_terminal_runtime")
    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_changed_authority_is_rejected_before_any_physical_cleanup(self, mock_tmux, retire):
        mock_tmux.session_exists.return_value = False
        retire.return_value = True
        contexts = self._patch_common(_durable_session(lifecycle="exited"))
        with (
            contexts[0],
            contexts[1],
            contexts[2],
            contexts[3],
            contexts[4] as worktrees,
            contexts[5] as providers,
            contexts[6] as complete,
            contexts[7],
            patch(
                "cli_agent_orchestrator.services.session_service.revalidate_session_hard_deletion",
                return_value={
                    "valid": False,
                    "reason_code": "SESSION_DELETE_PLAN_CHANGED",
                },
            ),
            patch(
                "cli_agent_orchestrator.services.session_service.purge_session_terminal_artifacts"
            ) as artifacts,
        ):
            with pytest.raises(SessionLifecycleError) as error:
                delete_session("session-lifetime-1")

        assert error.value.reason_code == "SESSION_DELETE_PLAN_CHANGED"
        providers.cleanup_provider.assert_not_called()
        worktrees.assert_not_called()
        artifacts.assert_not_called()
        complete.assert_not_called()

    @patch("cli_agent_orchestrator.services.session_service.retire_exited_terminal_runtime")
    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_already_retired_workspace_is_satisfied_when_physical_absence_is_proven(
        self, mock_tmux, retire
    ):
        mock_tmux.session_exists.return_value = False
        retire.return_value = True
        durable = _durable_session(lifecycle="exited")
        contexts = self._patch_common(durable)
        operation = {
            "session_id": "session-lifetime-1",
            "session_name": "cao-test",
            "state": "fenced",
            "terminal_ids": ["terminal1", "terminal2"],
            "allow_dirty_workspace": False,
        }
        with (
            contexts[0],
            contexts[1] as prepare,
            contexts[2] as cancel,
            contexts[3],
            contexts[4] as cleanup,
            contexts[5],
            contexts[6] as complete,
            contexts[7],
            patch(
                "cli_agent_orchestrator.services.session_service.get_session_hard_deletion_operation",
                return_value=operation,
            ),
            patch(
                "cli_agent_orchestrator.services.session_service.get_writable_work_context_by_session",
                return_value={"id": "context-1", "state": "retired"},
            ),
            patch(
                "cli_agent_orchestrator.services.session_service.claim_session_workspace_retirement"
            ) as claim,
            patch(
                "cli_agent_orchestrator.services.session_service.get_session_workspace_retirement_snapshot"
            ) as snapshot,
            patch(
                "cli_agent_orchestrator.services.session_service.purge_session_terminal_artifacts",
                return_value={
                    "runtime_artifacts_absent": True,
                    "terminals": [
                        {
                            "terminal_id": "terminal1",
                            "logs_removed": 1,
                            "attachments_removed": 1,
                        },
                        {
                            "terminal_id": "terminal2",
                            "logs_removed": 1,
                            "attachments_removed": 0,
                        },
                    ],
                },
            ) as artifacts,
        ):
            result = delete_session("session-lifetime-1")

        assert result["deleted"] == ["cao-test"]
        prepare.assert_not_called()
        cancel.assert_not_called()
        claim.assert_not_called()
        snapshot.assert_not_called()
        assert cleanup.call_count == 2
        for terminal in durable["terminals"]:
            cleanup.assert_any_call(
                terminal,
                allow_dirty=False,
                require_already_absent=True,
            )
        artifacts.assert_called_once_with(["terminal1", "terminal2"])
        assert result["terminal_artifacts"]["runtime_artifacts_absent"] is True
        complete.assert_called_once_with("session-lifetime-1", "cao-test")

    @patch("cli_agent_orchestrator.services.session_service.retire_exited_terminal_runtime")
    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_hard_delete_cleans_artifacts_for_previously_retired_session_terminal(
        self, mock_tmux, retire
    ):
        mock_tmux.session_exists.return_value = False
        retire.return_value = True
        durable = _durable_session(lifecycle="exited")
        contexts = self._patch_common(durable)
        graph_ids = ["terminal1", "terminal2", "retired-child"]
        with (
            contexts[0],
            contexts[1],
            contexts[2],
            contexts[3],
            contexts[4],
            contexts[5],
            contexts[6],
            contexts[7],
            patch(
                "cli_agent_orchestrator.services.session_service.revalidate_session_hard_deletion",
                return_value={
                    "valid": True,
                    "terminal_ids": ["terminal1", "terminal2"],
                    "graph_terminal_ids": graph_ids,
                },
            ),
            patch(
                "cli_agent_orchestrator.services.session_service.purge_session_terminal_artifacts",
                return_value={"runtime_artifacts_absent": True, "terminals": []},
            ) as artifacts,
            patch(
                "cli_agent_orchestrator.services.session_service."
                "mark_session_hard_deletion_workspace_retired",
                return_value={"marked": True},
            ) as mark,
        ):
            result = delete_session("session-lifetime-1")

        artifacts.assert_called_once_with(graph_ids)
        evidence = mark.call_args.kwargs["workspace_evidence"]
        assert [item["terminal_id"] for item in evidence] == graph_ids
        assert evidence[-1] == {
            "terminal_id": "retired-child",
            "managed": False,
            "path_absent": True,
            "git_unregistered": True,
            "branch_absent": True,
            "runtime_artifacts_absent": True,
        }
        assert result["deleted"] == ["cao-test"]

    @patch("cli_agent_orchestrator.services.session_service.retire_exited_terminal_runtime")
    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_explicit_plan_cancels_session_owned_work_then_rechecks_and_deletes(
        self, mock_tmux, retire
    ):
        mock_tmux.session_exists.return_value = False
        retire.return_value = True
        contexts = self._patch_common(_durable_session(lifecycle="exited"))
        cancellable = {
            "eligible": False,
            "deletion_mode": "eligible_with_cancellable_work",
            "cancellable": True,
            "can_resolve_and_delete": True,
            "plan_token": "a" * 64,
            "requires_dirty_confirmation": False,
            "reason_code": "QUEUED_WORK",
        }
        eligible = {
            "eligible": True,
            "deletion_mode": "eligible_normal",
            "cancellable": False,
            "can_resolve_and_delete": False,
            "plan_token": None,
            "requires_dirty_confirmation": False,
            "reason_code": None,
        }
        with (
            contexts[0],
            contexts[1],
            contexts[2],
            contexts[3],
            contexts[4],
            contexts[5],
            contexts[6] as delete,
            contexts[7],
            patch(
                "cli_agent_orchestrator.services.session_service._session_deletion_preflight",
                side_effect=[cancellable, eligible],
            ) as preflight,
            patch(
                "cli_agent_orchestrator.services.session_service.cancel_session_work_for_deletion",
                return_value={
                    "cancelled": True,
                    "cancelled_count": 2,
                    "residual": {"eligible": True},
                },
            ) as cancel_work,
        ):
            result = delete_session(
                "session-lifetime-1",
                cancel_unresolved_work=True,
                cancellation_plan_token="a" * 64,
            )

        assert result["deleted"] == ["cao-test"]
        assert preflight.call_count == 2
        cancel_work.assert_called_once_with(
            "session-lifetime-1",
            expected_plan_token="a" * 64,
            expected_terminal_ids=["terminal1", "terminal2"],
            cancel_unresolved_work=True,
            retire_historical_indeterminate=False,
        )
        delete.assert_called_once()

    @patch("cli_agent_orchestrator.services.session_service.retire_exited_terminal_runtime")
    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_explicit_historical_unknown_retirement_then_rechecks_and_deletes(
        self, mock_tmux, retire
    ):
        mock_tmux.session_exists.return_value = False
        retire.return_value = True
        contexts = self._patch_common(_durable_session(lifecycle="exited"))
        retirement = {
            "eligible": False,
            "deletion_mode": "eligible_with_historical_indeterminate_retirement",
            "can_resolve_and_delete": True,
            "cancellable": False,
            "requires_historical_indeterminate_confirmation": True,
            "plan_token": "r" * 64,
            "requires_dirty_confirmation": False,
            "reason_code": "HISTORICAL_EFFECT_OUTCOME_UNKNOWN",
        }
        eligible = {
            "eligible": True,
            "deletion_mode": "eligible_normal",
            "can_resolve_and_delete": False,
            "cancellable": False,
            "plan_token": None,
            "requires_dirty_confirmation": False,
            "reason_code": None,
        }
        with (
            contexts[0],
            contexts[1],
            contexts[2],
            contexts[3],
            contexts[4],
            contexts[5],
            contexts[6] as delete,
            contexts[7],
            patch(
                "cli_agent_orchestrator.services.session_service._session_deletion_preflight",
                side_effect=[retirement, eligible],
            ),
            patch(
                "cli_agent_orchestrator.services.session_service.cancel_session_work_for_deletion",
                return_value={
                    "cancelled": True,
                    "cancelled_count": 0,
                    "retired_indeterminate_count": 4,
                    "residual": {"eligible": True},
                },
            ) as resolve_work,
        ):
            result = delete_session(
                "session-lifetime-1",
                retire_historical_indeterminate=True,
                cancellation_plan_token="r" * 64,
            )

        assert result["deleted"] == ["cao-test"]
        resolve_work.assert_called_once_with(
            "session-lifetime-1",
            expected_plan_token="r" * 64,
            expected_terminal_ids=["terminal1", "terminal2"],
            cancel_unresolved_work=False,
            retire_historical_indeterminate=True,
        )
        delete.assert_called_once()

    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_stale_or_unsafe_cancellation_intent_mutates_nothing(self, mock_tmux):
        mock_tmux.session_exists.return_value = False
        contexts = self._patch_common(_durable_session(lifecycle="exited"))
        with (
            contexts[0],
            contexts[1] as prepare,
            contexts[2],
            contexts[3],
            contexts[4],
            contexts[5],
            contexts[6] as delete,
            contexts[7],
            patch(
                "cli_agent_orchestrator.services.session_service._session_deletion_preflight",
                return_value={
                    "eligible": False,
                    "cancellable": False,
                    "plan_token": None,
                    "requires_dirty_confirmation": False,
                    "reason_code": "INDETERMINATE_EFFECT",
                },
            ),
            patch(
                "cli_agent_orchestrator.services.session_service.cancel_session_work_for_deletion"
            ) as cancel_work,
        ):
            with pytest.raises(SessionLifecycleError) as error:
                delete_session(
                    "session-lifetime-1",
                    cancel_unresolved_work=True,
                    cancellation_plan_token="b" * 64,
                )

        assert error.value.reason_code == "SESSION_DELETE_PLAN_CHANGED"
        cancel_work.assert_not_called()
        prepare.assert_not_called()
        delete.assert_not_called()

    @patch("cli_agent_orchestrator.services.session_service.retire_exited_terminal_runtime")
    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_historical_dirty_session_requires_and_honors_explicit_confirmation(
        self, mock_tmux, retire
    ):
        mock_tmux.session_exists.return_value = False
        retire.return_value = True
        durable = _durable_session(lifecycle="exited")
        for terminal in durable["terminals"]:
            terminal["writable_work_context_id"] = "context-1"
        contexts = self._patch_common(durable)
        with (
            contexts[0],
            contexts[1],
            contexts[2],
            contexts[3],
            contexts[4] as cleanup,
            contexts[5],
            contexts[6] as delete,
            contexts[7],
            patch(
                "cli_agent_orchestrator.services.session_service._session_deletion_preflight",
                return_value={
                    "eligible": True,
                    "already_deleted": False,
                    "requires_dirty_confirmation": True,
                    "modified_files": 1,
                    "untracked_files": 1,
                    "reason_code": None,
                },
            ),
            patch(
                "cli_agent_orchestrator.services.session_service.get_session_workspace_retirement_snapshot",
                return_value={
                    "authority_fingerprint": "exact-session-workspace",
                    "context": {"state": "admitted", "retirement_allow_dirty": False},
                },
            ),
            patch(
                "cli_agent_orchestrator.services.session_service.claim_session_workspace_retirement",
                return_value={"claimed": True},
            ) as claim,
            patch(
                "cli_agent_orchestrator.services.session_service.get_writable_work_context_by_session",
                return_value={"id": "context-1", "state": "admitted"},
            ),
            patch(
                "cli_agent_orchestrator.services.session_service.transition_writable_work_context",
                return_value=True,
            ),
        ):
            with pytest.raises(SessionLifecycleError) as error:
                delete_session("session-lifetime-1")
            assert error.value.reason_code == "SESSION_DIRTY_CONFIRMATION_REQUIRED"
            cleanup.assert_not_called()
            delete.assert_not_called()

            result = delete_session("session-lifetime-1", confirm_dirty_workspace=True)

        assert result["deleted"] == ["cao-test"]
        assert result["retained_resources"] == []
        claim.assert_called_once_with("context-1", "exact-session-workspace", allow_dirty=True)
        assert cleanup.call_count == 2
        for terminal in durable["terminals"]:
            cleanup.assert_any_call(
                terminal,
                allow_dirty=True,
                require_already_absent=False,
            )
        delete.assert_called_once_with("session-lifetime-1", "cao-test")

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
