"""Runtime ownership retirement must never require history deletion."""

from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import cli_agent_orchestrator.clients.database as database
from cli_agent_orchestrator.clients.database import (
    Base,
    WorktreeWriterLeaseConflict,
    claim_terminal_runtime_exit,
    create_inbox_message,
    create_terminal,
    ensure_open_workflow,
    get_delegation_result_for_assignment,
    get_inbox_messages,
    get_terminal_metadata,
    get_workflow_status,
    list_terminals_by_session,
    list_worktree_writer_leases,
    mark_terminal_runtime_exit_pending,
    mark_terminal_runtime_exited,
    mark_terminal_runtime_running,
    register_handoff_child,
)
from cli_agent_orchestrator.clients.tmux import (
    PaneDeliveryTarget,
    PaneTargetError,
    RuntimePaneTarget,
    TmuxClient,
)
from cli_agent_orchestrator.services import operations_service, terminal_service
from cli_agent_orchestrator.services.housekeeping_service import (
    HousekeepingSummary,
    _reconcile_writer_leases,
)


@pytest.fixture
def lifecycle_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(database, "_terminal_authority_schema_ready", True)
    monkeypatch.setattr(database, "_child_assignment_schema_ready", True)
    monkeypatch.setattr(database, "_delegation_result_schema_ready", True)
    yield
    engine.dispose()


def _terminal(
    terminal_id: str,
    session: str,
    worktree: str,
    *,
    write_enabled: bool,
    context_role: str = "work",
):
    return create_terminal(
        terminal_id,
        session,
        f"window-{terminal_id}",
        "codex",
        "developer",
        launch_worktree=worktree,
        write_enabled=write_enabled,
        context_role=context_role,
    )


def _recoverable_supervisor(terminal_id: str = "recover00"):
    return create_terminal(
        terminal_id,
        "cao-recoverable",
        f"window-{terminal_id}",
        "codex",
        "critical_sol_xhigh_owner",
        launch_worktree=f"/worktree-{terminal_id}",
        write_enabled=True,
        context_role="supervisor",
        project_id="project-recovery",
        project_name="Recovery test",
        project_path="/source-recovery",
        runtime_pane_id="%91",
        runtime_pane_pid=9191,
        runtime_generation="runtime-recovery-a",
        runtime_generation_origin="launch",
        runtime_process_start_ticks=919191,
        runtime_process_group_id=9191,
        runtime_process_session_id=9191,
    )


def test_unexpected_supervisor_runtime_death_preserves_stable_takeover_authority(
    lifecycle_db, monkeypatch
):
    _recoverable_supervisor()
    assert mark_terminal_runtime_running("recover00")
    cleanup = MagicMock()
    retire = MagicMock(return_value=True)
    monkeypatch.setattr(terminal_service, "_runtime_death_observation", lambda *_: True)
    monkeypatch.setattr(
        terminal_service, "_retire_observed_dead_runtime", lambda *_args, **_kwargs: (True, None)
    )
    monkeypatch.setattr(terminal_service.provider_manager, "cleanup_provider", cleanup)
    monkeypatch.setattr(
        terminal_service,
        "_retire_recovery_required_terminal_runtime",
        retire,
    )

    assert terminal_service.reconcile_terminal_runtime("recover00") is False
    first = get_terminal_metadata("recover00")
    assert first["runtime_lifecycle"] == "recovery_required"
    assert first["writer_authority_generation"]
    assert list_worktree_writer_leases() == []
    assert database.recovery_takeover_durable_eligibility("recover00")["eligible"] is True

    # Arbitrarily many daemon/status ticks preserve the same exact recovery
    # generation; there is no UI race window and no writer lease leak.
    assert terminal_service.reconcile_terminal_runtime("recover00") is False
    second = get_terminal_metadata("recover00")
    assert second["runtime_lifecycle"] == "recovery_required"
    assert second["runtime_generation"] == first["runtime_generation"]
    assert second["writer_authority_generation"] == first["writer_authority_generation"]
    cleanup.assert_called_once_with("recover00")
    assert retire.call_count == 2


def test_recovery_required_graceful_exit_is_idempotent_and_never_sends_input(
    lifecycle_db, monkeypatch
):
    _recoverable_supervisor()
    assert mark_terminal_runtime_running("recover00")
    observed = {
        field: get_terminal_metadata("recover00").get(field)
        for field in database.TERMINAL_RUNTIME_DEATH_AUTHORITY_FIELDS
    }
    state, _ = database.mark_terminal_runtime_recovery_required_with_workflow_ids(
        "recover00", expected_runtime_authority=observed
    )
    assert state == "recovery_required"
    monkeypatch.setattr(terminal_service.provider_manager, "cleanup_provider", lambda *_: None)
    monkeypatch.setattr(terminal_service, "_retire_exited_terminal_runtime", lambda *_: True)
    monkeypatch.setattr(terminal_service, "_wake_queued_provider_execution", lambda *_: None)
    send = MagicMock()
    monkeypatch.setattr(terminal_service.tmux_client, "send_keys", send)

    first = terminal_service.exit_terminal("recover00")
    second = terminal_service.exit_terminal("recover00")

    assert first.success is second.success is True
    assert first.command_delivered is second.command_delivered is False
    assert get_terminal_metadata("recover00")["runtime_lifecycle"] == "exited"
    send.assert_not_called()


def test_positive_death_releases_runtime_ownership_but_preserves_history(lifecycle_db, monkeypatch):
    _terminal(
        "parent00",
        "cao-parent",
        "/worktree-parent",
        write_enabled=False,
        context_role="supervisor",
    )
    _terminal("writer00", "cao-historical", "/worktree-a", write_enabled=True)
    assert mark_terminal_runtime_running("writer00")
    assert ensure_open_workflow("writer00") is not None
    assert register_handoff_child("parent00", "writer00")
    create_inbox_message("parent00", "writer00", "durable inbox history")
    monkeypatch.setattr(
        terminal_service.tmux_client,
        "get_history",
        lambda *_args, **_kwargs: "durable terminal output",
    )

    assert list_worktree_writer_leases()[0]["terminal_id"] == "writer00"
    assert mark_terminal_runtime_exit_pending("writer00")
    with pytest.raises(WorktreeWriterLeaseConflict):
        _terminal("blocked0", "cao-new", "/worktree-a", write_enabled=True)
    assert get_terminal_metadata("writer00")["runtime_lifecycle"] == "exit_pending"
    assert list_worktree_writer_leases()[0]["terminal_id"] == "writer00"

    assert mark_terminal_runtime_exited("writer00")
    assert list_worktree_writer_leases() == []
    historical = get_terminal_metadata("writer00")
    assert historical is not None
    assert historical["runtime_lifecycle"] == "exited"
    assert [row["id"] for row in list_terminals_by_session("cao-historical")] == ["writer00"]
    assert terminal_service.get_output("writer00") == "durable terminal output"
    assert get_inbox_messages("writer00")[0].message == "durable inbox history"
    assert get_delegation_result_for_assignment("writer00") is not None
    assert get_workflow_status("writer00") == "cancelled"

    _terminal("writer01", "cao-new", "/worktree-a", write_enabled=True)
    assert get_terminal_metadata("writer00") is not None
    assert list_worktree_writer_leases()[0]["terminal_id"] == "writer01"

    # Housekeeping is not history cleanup.  A valid exited record survives.
    summary = HousekeepingSummary()
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.tmux.tmux_client.window_exists", lambda *_: True
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.tmux.tmux_client.get_pane_current_command",
        lambda *_: "codex",
    )
    _reconcile_writer_leases(summary)
    assert get_terminal_metadata("writer00") is not None


def test_exited_runtime_retirement_requires_exact_terminal_identity(monkeypatch):
    metadata = {
        "id": "closed00",
        "tmux_session": "cao-history",
        "tmux_window": "agent",
        "runtime_lifecycle": "exited",
        "runtime_pane_id": "%41",
        "runtime_pane_pid": 4242,
        "runtime_generation": "gen-1",
        "runtime_generation_origin": "launch",
        "runtime_process_start_ticks": 777,
        "runtime_process_group_id": 4242,
        "runtime_process_session_id": 4242,
    }
    monkeypatch.setattr(terminal_service, "get_terminal_metadata", lambda *_: metadata)
    monkeypatch.setattr(
        terminal_service.tmux_client,
        "exact_runtime_target",
        lambda *_args, **_kwargs: RuntimePaneTarget(
            "%41",
            4242,
            "bash",
            "closed00",
            "gen-1",
            777,
            process_group_id=4242,
            process_session_id=4242,
        ),
    )
    retired = []
    monkeypatch.setattr(
        terminal_service.tmux_client,
        "retire_runtime_pane",
        lambda target, **_kwargs: retired.append(target) or True,
    )
    assert terminal_service.retire_exited_terminal_runtime("closed00") is True
    assert retired[0].pane_pid == 4242

    monkeypatch.setattr(
        terminal_service.tmux_client,
        "exact_runtime_target",
        lambda *_args, **_kwargs: RuntimePaneTarget(
            "%42", 4343, "bash", "replacement", "gen-2", 888
        ),
    )
    assert terminal_service.retire_exited_terminal_runtime("closed00") is None
    assert len(retired) == 1


def test_startup_reconciles_intact_legacy_runtime_identity_once(lifecycle_db, monkeypatch):
    _terminal("legacy00", "cao-legacy", "/worktree-legacy", write_enabled=False)
    target = RuntimePaneTarget(
        "%71",
        7171,
        "codex",
        "legacy00",
        "reconciled-gen",
        999,
        False,
        7171,
        7171,
    )
    bind = MagicMock(return_value=target)
    monkeypatch.setattr(terminal_service.tmux_client, "bind_legacy_runtime_generation", bind)

    assert terminal_service.reconcile_legacy_runtime_identities() == 1
    metadata = get_terminal_metadata("legacy00")
    assert metadata is not None
    assert metadata["runtime_pane_id"] == "%71"
    assert metadata["runtime_pane_pid"] == 7171
    assert metadata["runtime_generation"] == "reconciled-gen"
    assert metadata["runtime_generation_origin"] == "reconciled"
    assert metadata["runtime_process_start_ticks"] == 999
    assert metadata["runtime_process_group_id"] == 7171
    assert metadata["runtime_process_session_id"] == 7171

    assert terminal_service.reconcile_legacy_runtime_identities() == 0
    assert bind.call_count == 1


def test_startup_backfills_process_tree_fence_for_exact_existing_runtime(lifecycle_db, monkeypatch):
    create_terminal(
        "rolling00",
        "cao-rolling",
        "agent",
        "codex",
        runtime_pane_id="%72",
        runtime_pane_pid=7272,
        runtime_generation="rolling-gen",
        runtime_process_start_ticks=1001,
    )
    target = RuntimePaneTarget(
        "%72",
        7272,
        "bash",
        "rolling00",
        "rolling-gen",
        1001,
        True,
        7272,
        7272,
    )
    exact = MagicMock(return_value=target)
    monkeypatch.setattr(terminal_service.tmux_client, "exact_runtime_target", exact)

    assert terminal_service.reconcile_legacy_runtime_identities() == 1
    metadata = get_terminal_metadata("rolling00")
    assert metadata["runtime_process_group_id"] == 7272
    assert metadata["runtime_process_session_id"] == 7272
    assert terminal_service.reconcile_legacy_runtime_identities() == 0
    assert exact.call_count == 1


def test_startup_runtime_reconciliation_never_polls_durable_history(monkeypatch):
    bind = MagicMock()
    monkeypatch.setattr(
        terminal_service,
        "list_all_terminals",
        lambda: [
            {
                "id": "historical00",
                "tmux_session": "cao-history",
                "tmux_window": "agent",
                "runtime_lifecycle": "exited",
                "runtime_generation": None,
            }
        ],
    )
    monkeypatch.setattr(terminal_service.tmux_client, "bind_legacy_runtime_generation", bind)

    assert terminal_service.reconcile_legacy_runtime_identities() == 0
    bind.assert_not_called()


def test_new_terminal_persists_launch_generation_and_process_start(lifecycle_db):
    create_terminal(
        "launch00",
        "cao-launch",
        "agent",
        "codex",
        runtime_pane_id="%81",
        runtime_pane_pid=8181,
        runtime_generation="launch-gen",
        runtime_process_start_ticks=1234,
        runtime_process_group_id=8181,
        runtime_process_session_id=8181,
    )
    metadata = get_terminal_metadata("launch00")
    assert metadata is not None
    assert metadata["runtime_pane_id"] == "%81"
    assert metadata["runtime_pane_pid"] == 8181
    assert metadata["runtime_generation"] == "launch-gen"
    assert metadata["runtime_generation_origin"] == "launch"
    assert metadata["runtime_process_start_ticks"] == 1234
    assert metadata["runtime_process_group_id"] == 8181
    assert metadata["runtime_process_session_id"] == 8181


def test_exited_terminal_output_falls_back_to_durable_log(tmp_path, monkeypatch):
    metadata = {
        "id": "closed00",
        "tmux_session": "cao-history",
        "tmux_window": "agent",
        "runtime_lifecycle": "exited",
    }
    (tmp_path / "closed00.log").write_text("durable output", encoding="utf-8")
    monkeypatch.setattr(terminal_service, "TERMINAL_LOG_DIR", tmp_path)
    monkeypatch.setattr(terminal_service, "get_terminal_metadata", lambda *_: metadata)
    monkeypatch.setattr(
        terminal_service.tmux_client,
        "get_history",
        MagicMock(side_effect=ValueError("pane retired")),
    )
    assert terminal_service.get_output("closed00") == "durable output"


def test_recovery_fenced_terminal_history_remains_available(tmp_path, monkeypatch):
    metadata = {
        "id": "fenced00",
        "tmux_session": "cao-history",
        "tmux_window": "old-owner",
        "runtime_lifecycle": "recovery_fenced",
    }
    (tmp_path / "fenced00.log").write_text("preserved recovery history", encoding="utf-8")
    monkeypatch.setattr(terminal_service, "TERMINAL_LOG_DIR", tmp_path)
    monkeypatch.setattr(terminal_service, "get_terminal_metadata", lambda *_: metadata)
    monkeypatch.setattr(
        terminal_service.tmux_client,
        "get_history",
        MagicMock(side_effect=ValueError("old pane retired")),
    )
    assert terminal_service.get_output("fenced00") == "preserved recovery history"


def test_exited_terminal_output_reads_housekeeping_compressed_log(tmp_path, monkeypatch):
    import gzip

    metadata = {
        "id": "closed00",
        "tmux_session": "cao-history",
        "tmux_window": "agent",
        "runtime_lifecycle": "exited",
    }
    with gzip.open(tmp_path / "closed00.log.gz", "wt", encoding="utf-8") as stream:
        stream.write("compressed durable output")
    monkeypatch.setattr(terminal_service, "TERMINAL_LOG_DIR", tmp_path)
    monkeypatch.setattr(terminal_service, "get_terminal_metadata", lambda *_: metadata)
    monkeypatch.setattr(
        terminal_service.tmux_client,
        "get_history",
        MagicMock(side_effect=ValueError("pane retired")),
    )
    assert terminal_service.get_output("closed00") == "compressed durable output"


def test_housekeeping_restart_recovery_releases_only_positive_death(lifecycle_db, monkeypatch):
    _terminal("uncert00", "cao-uncertain", "/worktree-u", write_enabled=True)
    assert mark_terminal_runtime_running("uncert00")
    assert mark_terminal_runtime_exit_pending("uncert00")

    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.tmux.tmux_client.window_exists", lambda *_: None
    )
    uncertain = HousekeepingSummary()
    _reconcile_writer_leases(uncertain)
    assert list_worktree_writer_leases()[0]["terminal_id"] == "uncert00"
    assert get_terminal_metadata("uncert00")["runtime_lifecycle"] == "exit_pending"

    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.tmux.tmux_client.window_exists", lambda *_: False
    )
    monkeypatch.setattr(
        terminal_service, "_retire_observed_dead_runtime", lambda *_args, **_kwargs: (True, None)
    )
    dead = HousekeepingSummary()
    _reconcile_writer_leases(dead)
    assert list_worktree_writer_leases() == []
    assert get_terminal_metadata("uncert00")["runtime_lifecycle"] == "exited"
    assert dead.writer_leases_reconciled == 1


def test_housekeeping_preserves_recoverable_supervisor_for_takeover(
    lifecycle_db, monkeypatch, tmp_path
):
    _recoverable_supervisor()
    assert mark_terminal_runtime_running("recover00")
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.tmux.tmux_client.window_exists", lambda *_: False
    )
    monkeypatch.setattr(
        terminal_service.tmux_client,
        "exact_runtime_target",
        MagicMock(side_effect=PaneTargetError("EXIT_SESSION_MISSING", "session absent")),
    )
    monkeypatch.setattr(terminal_service.provider_manager, "cleanup_provider", lambda *_: None)

    summary = HousekeepingSummary()
    _reconcile_writer_leases(summary, proc_root=tmp_path)

    metadata = get_terminal_metadata("recover00")
    assert metadata["runtime_lifecycle"] == "recovery_required"
    assert list_worktree_writer_leases() == []
    assert database.recovery_takeover_durable_eligibility("recover00")["eligible"] is True
    assert summary.writer_leases_reconciled == 1
    assert summary.skipped_unknown == 0


def test_housekeeping_missing_tmux_preserves_live_orphan_writer_authority(
    lifecycle_db, monkeypatch, tmp_path
):
    _recoverable_supervisor()
    assert mark_terminal_runtime_running("recover00")
    process = tmp_path / "9191"
    process.mkdir()
    fields = ["S", "1", "9191", "9191", *(["0"] * 15), "919191"]
    (process / "stat").write_text(
        f"9191 (orphaned provider) {' '.join(fields)}\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.tmux.tmux_client.window_exists", lambda *_: False
    )

    summary = HousekeepingSummary()
    _reconcile_writer_leases(summary, proc_root=tmp_path)

    metadata = get_terminal_metadata("recover00")
    assert metadata["runtime_lifecycle"] == "running"
    assert list_worktree_writer_leases()[0]["terminal_id"] == "recover00"
    with pytest.raises(WorktreeWriterLeaseConflict):
        _terminal("replacement", "cao-replacement", "/worktree-recover00", write_enabled=True)
    assert summary.writer_leases_reconciled == 0


def test_read_only_legacy_runtime_missing_tmux_still_converges_under_issue_58(
    lifecycle_db, monkeypatch
):
    _terminal("readonly", "cao-readonly", "/legacy-readonly", write_enabled=False)
    assert mark_terminal_runtime_running("readonly")
    monkeypatch.setattr(terminal_service, "_runtime_death_observation", lambda *_: True)
    monkeypatch.setattr(terminal_service.provider_manager, "cleanup_provider", lambda *_: None)
    monkeypatch.setattr(terminal_service, "_wake_queued_provider_execution", lambda *_: None)

    assert terminal_service.reconcile_terminal_runtime("readonly") is True
    assert get_terminal_metadata("readonly")["runtime_lifecycle"] == "exited"
    assert list_worktree_writer_leases() == []


def test_completed_historical_contexts_do_not_consume_capacity(monkeypatch):
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.list_all_terminals",
        lambda: [
            {
                "id": "historical",
                "tmux_session": "s",
                "tmux_window": "history",
                "runtime_lifecycle": "exited",
                "context_role": "work",
            },
            {
                "id": "active",
                "tmux_session": "s",
                "tmux_window": "active",
                "runtime_lifecycle": "running",
                "context_role": "work",
            },
        ],
    )
    calls = []

    def pane_status(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="0 codex\n")

    monkeypatch.setattr(operations_service.subprocess, "run", pane_status)
    assert operations_service._active_context_ids() == ["active"]
    assert len(calls) == 1


def test_graceful_exit_releases_only_after_positive_runtime_death(monkeypatch):
    metadata = {
        "id": "writer00",
        "tmux_session": "cao-session",
        "tmux_window": "writer",
        "runtime_lifecycle": "running",
    }
    provider = MagicMock()
    provider.terminal_id = "writer00"
    provider.session_name = "cao-session"
    provider.window_name = "writer"
    provider.exit_cli.return_value = "/exit"
    provider.is_process_alive.return_value = True
    monkeypatch.setattr(terminal_service, "get_terminal_metadata", lambda *_: metadata)
    monkeypatch.setattr(terminal_service.provider_manager, "get_provider", lambda *_: provider)
    monkeypatch.setattr(terminal_service, "prepare_terminal_for_destruction", lambda *_: None)
    monkeypatch.setattr(terminal_service, "_wake_queued_provider_execution", lambda *_: None)
    sent = []
    monkeypatch.setattr(
        terminal_service.tmux_client,
        "send_keys",
        lambda *args, **kwargs: sent.append((args, kwargs)),
    )
    monkeypatch.setattr(terminal_service, "cancel_child_assignments_for_terminal", lambda *_: None)
    monkeypatch.setattr(terminal_service, "cancel_workflows_for_terminal", lambda *_: None)
    exited = []
    monkeypatch.setattr(
        terminal_service,
        "claim_terminal_runtime_exit",
        lambda terminal_id: "dispatch",
    )
    monkeypatch.setattr(
        terminal_service,
        "mark_terminal_runtime_exited_with_workflow_ids",
        lambda terminal_id, **_kwargs: (exited.append(terminal_id) or True, []),
    )
    monkeypatch.setattr(
        terminal_service,
        "mark_terminal_runtime_recovery_required_with_workflow_ids",
        lambda *_args, **_kwargs: ("ineligible", []),
    )
    monkeypatch.setattr(terminal_service.tmux_client, "window_exists", lambda *_: True)
    monkeypatch.setattr(terminal_service.tmux_client, "get_pane_current_command", lambda *_: "bash")
    monkeypatch.setattr(
        terminal_service.tmux_client,
        "exact_pane_target",
        lambda *_: PaneDeliveryTarget("%41", "codex"),
    )

    assert terminal_service.exit_terminal("writer00").success is True
    assert sent == [(("cao-session", "writer", "/exit"), {"enter_count": 1, "pane_id": "%41"})]
    assert exited == ["writer00"]


def test_missing_tmux_session_converges_running_runtime_and_repeated_exit(
    lifecycle_db, monkeypatch
):
    _terminal("writer00", "cao-session", "/worktree-a", write_enabled=True)
    assert mark_terminal_runtime_running("writer00")
    provider = MagicMock()
    provider.terminal_id = "writer00"
    provider.session_name = "cao-session"
    provider.window_name = "window-writer00"
    provider.is_process_alive.return_value = True
    monkeypatch.setattr(terminal_service.provider_manager, "get_provider", lambda *_: provider)
    monkeypatch.setattr(terminal_service.provider_manager, "cleanup_provider", lambda *_: None)
    monkeypatch.setattr(terminal_service, "cancel_child_assignments_for_terminal", lambda *_: None)
    monkeypatch.setattr(terminal_service, "_wake_queued_provider_execution", lambda *_: None)
    monkeypatch.setattr(
        terminal_service, "_retire_observed_dead_runtime", lambda *_args, **_kwargs: (True, None)
    )
    from libtmux._internal.query_list import ObjectDoesNotExist

    presence = TmuxClient()
    presence.server = MagicMock()
    presence.server.sessions.get.side_effect = ObjectDoesNotExist("cao-session")
    presence.server.cmd.return_value.returncode = 0
    presence.server.cmd.return_value.stderr = []
    monkeypatch.setattr(terminal_service.tmux_client, "window_exists", presence.window_exists)
    monkeypatch.setattr(
        terminal_service.tmux_client,
        "exact_pane_target",
        MagicMock(side_effect=PaneTargetError("EXIT_SESSION_MISSING", "session absent")),
    )
    monkeypatch.setattr(
        terminal_service.tmux_client,
        "exact_runtime_target",
        MagicMock(side_effect=PaneTargetError("EXIT_SESSION_MISSING", "session absent")),
    )
    send = MagicMock()
    monkeypatch.setattr(terminal_service.tmux_client, "send_keys", send)

    first = terminal_service.exit_terminal("writer00")
    second = terminal_service.exit_terminal("writer00")

    assert first.success is True
    assert second.success is True
    assert first.command_delivered is False
    assert second.command_delivered is False
    assert get_terminal_metadata("writer00")["runtime_lifecycle"] == "exited"
    assert list_worktree_writer_leases() == []
    send.assert_not_called()


def test_stale_death_observation_cannot_overwrite_recovery_fence(lifecycle_db, monkeypatch):
    _terminal("writer00", "cao-session", "/worktree-a", write_enabled=True)
    with database.SessionLocal() as db:
        terminal = db.get(database.TerminalModel, "writer00")
        terminal.runtime_lifecycle = "running"
        terminal.runtime_generation = "runtime-generation-a"
        terminal.runtime_generation_origin = "launch"
        terminal.runtime_pane_id = "%41"
        terminal.runtime_pane_pid = 4141
        terminal.runtime_process_start_ticks = 111
        terminal.runtime_process_group_id = 4141
        terminal.runtime_process_session_id = 4141
        db.commit()

    cleanup = MagicMock()
    cancel = MagicMock()
    wake = MagicMock()
    monkeypatch.setattr(terminal_service, "_runtime_death_observation", lambda *_: True)
    monkeypatch.setattr(
        terminal_service, "_retire_observed_dead_runtime", lambda *_args, **_kwargs: (True, None)
    )
    monkeypatch.setattr(terminal_service.provider_manager, "cleanup_provider", cleanup)
    monkeypatch.setattr(terminal_service, "cancel_child_assignments_for_terminal", cancel)
    monkeypatch.setattr(terminal_service, "_wake_queued_provider_execution", wake)
    original_mark = terminal_service.mark_terminal_runtime_exited_with_workflow_ids

    def fence_then_compare(terminal_id, **kwargs):
        with database.SessionLocal() as db:
            terminal = db.get(database.TerminalModel, terminal_id)
            lease = db.get(database.WorktreeWriterLeaseModel, "/worktree-a")
            terminal.runtime_lifecycle = "recovery_fenced"
            terminal.writer_authority_generation = "successor-writer-generation"
            terminal.recovery_fenced_reason = "owner_authorized_recovery_takeover"
            terminal.replaced_by_terminal_id = "successor00"
            lease.terminal_id = "successor00"
            lease.authority_generation = "successor-writer-generation"
            db.commit()
        return original_mark(terminal_id, **kwargs)

    monkeypatch.setattr(
        terminal_service,
        "mark_terminal_runtime_exited_with_workflow_ids",
        fence_then_compare,
    )

    assert terminal_service.reconcile_terminal_runtime("writer00") is None
    current = get_terminal_metadata("writer00")
    assert current["runtime_lifecycle"] == "recovery_fenced"
    assert current["replaced_by_terminal_id"] == "successor00"
    leases = list_worktree_writer_leases()
    assert len(leases) == 1
    assert leases[0]["canonical_worktree"] == "/worktree-a"
    assert leases[0]["terminal_id"] == "successor00"
    assert leases[0]["authority_generation"] == "successor-writer-generation"
    cleanup.assert_not_called()
    cancel.assert_not_called()
    wake.assert_not_called()


def test_stale_death_observation_cannot_terminalize_new_runtime_generation(
    lifecycle_db, monkeypatch
):
    _terminal("writer00", "cao-session", "/worktree-a", write_enabled=True)
    with database.SessionLocal() as db:
        terminal = db.get(database.TerminalModel, "writer00")
        terminal.runtime_lifecycle = "running"
        terminal.runtime_generation = "runtime-generation-a"
        terminal.runtime_generation_origin = "launch"
        terminal.runtime_pane_id = "%41"
        terminal.runtime_pane_pid = 4141
        terminal.runtime_process_start_ticks = 111
        terminal.runtime_process_group_id = 4141
        terminal.runtime_process_session_id = 4141
        db.commit()

    cleanup = MagicMock()
    cancel = MagicMock()
    wake = MagicMock()
    monkeypatch.setattr(terminal_service, "_runtime_death_observation", lambda *_: True)
    monkeypatch.setattr(
        terminal_service, "_retire_observed_dead_runtime", lambda *_args, **_kwargs: (True, None)
    )
    monkeypatch.setattr(terminal_service.provider_manager, "cleanup_provider", cleanup)
    monkeypatch.setattr(terminal_service, "cancel_child_assignments_for_terminal", cancel)
    monkeypatch.setattr(terminal_service, "_wake_queued_provider_execution", wake)
    original_mark = terminal_service.mark_terminal_runtime_exited_with_workflow_ids

    def reconnect_then_compare(terminal_id, **kwargs):
        with database.SessionLocal() as db:
            terminal = db.get(database.TerminalModel, terminal_id)
            terminal.runtime_generation = "runtime-generation-b"
            terminal.runtime_pane_id = "%42"
            terminal.runtime_pane_pid = 4242
            terminal.runtime_process_start_ticks = 222
            terminal.runtime_process_group_id = 4242
            terminal.runtime_process_session_id = 4242
            db.commit()
        return original_mark(terminal_id, **kwargs)

    monkeypatch.setattr(
        terminal_service,
        "mark_terminal_runtime_exited_with_workflow_ids",
        reconnect_then_compare,
    )

    assert terminal_service.reconcile_terminal_runtime("writer00") is None
    current = get_terminal_metadata("writer00")
    assert current["runtime_lifecycle"] == "running"
    assert current["runtime_generation"] == "runtime-generation-b"
    assert list_worktree_writer_leases()[0]["terminal_id"] == "writer00"
    cleanup.assert_not_called()
    cancel.assert_not_called()
    wake.assert_not_called()


def test_graceful_exit_uncertainty_keeps_pending_ownership(monkeypatch):
    metadata = {
        "id": "writer00",
        "tmux_session": "cao-session",
        "tmux_window": "writer",
        "runtime_lifecycle": "running",
    }
    provider = MagicMock()
    provider.terminal_id = "writer00"
    provider.session_name = "cao-session"
    provider.window_name = "writer"
    provider.exit_cli.return_value = "/exit"
    provider.is_process_alive.return_value = True
    monkeypatch.setattr(terminal_service, "get_terminal_metadata", lambda *_: metadata)
    monkeypatch.setattr(terminal_service.provider_manager, "get_provider", lambda *_: provider)
    monkeypatch.setattr(terminal_service, "prepare_terminal_for_destruction", lambda *_: None)
    monkeypatch.setattr(terminal_service.tmux_client, "send_keys", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(terminal_service, "cancel_child_assignments_for_terminal", lambda *_: None)
    monkeypatch.setattr(terminal_service, "cancel_workflows_for_terminal", lambda *_: None)
    monkeypatch.setattr(terminal_service, "claim_terminal_runtime_exit", lambda *_: "dispatch")
    exited = []
    monkeypatch.setattr(
        terminal_service,
        "mark_terminal_runtime_exited_with_workflow_ids",
        lambda terminal_id, **_kwargs: (exited.append(terminal_id) or True, []),
    )
    monkeypatch.setattr(terminal_service.tmux_client, "window_exists", lambda *_: None)
    monkeypatch.setattr(
        terminal_service.tmux_client,
        "exact_pane_target",
        lambda *_: PaneDeliveryTarget("%41", "codex"),
    )
    monkeypatch.setattr(terminal_service, "EXIT_CONFIRMATION_TIMEOUT_SECONDS", 0.0)

    assert terminal_service.exit_terminal("writer00").success is False
    assert exited == []


def test_exit_claim_is_idempotent_and_preserves_capacity_until_death(lifecycle_db):
    _terminal("writer00", "cao-session", "/worktree-a", write_enabled=True)
    assert mark_terminal_runtime_running("writer00")

    assert claim_terminal_runtime_exit("writer00") == "dispatch"
    requested_at = get_terminal_metadata("writer00")["runtime_exit_requested_at"]
    assert claim_terminal_runtime_exit("writer00") == "observe"
    assert get_terminal_metadata("writer00")["runtime_exit_requested_at"] == requested_at
    assert list_worktree_writer_leases()[0]["terminal_id"] == "writer00"

    assert mark_terminal_runtime_exited("writer00")
    assert claim_terminal_runtime_exit("writer00") == "exited"
    assert list_worktree_writer_leases() == []
    _terminal("writer01", "cao-replacement", "/worktree-a", write_enabled=True)
    assert list_worktree_writer_leases()[0]["terminal_id"] == "writer01"


def test_codex_exit_uses_one_tmux_submit_and_pending_retry_only_observes(lifecycle_db, monkeypatch):
    _terminal("writer00", "cao-session", "/worktree-a", write_enabled=True)
    assert mark_terminal_runtime_running("writer00")
    provider = MagicMock()
    provider.terminal_id = "writer00"
    provider.session_name = "cao-session"
    provider.window_name = "window-writer00"
    provider.exit_cli.return_value = "/exit"
    provider.is_process_alive.return_value = True
    monkeypatch.setattr(terminal_service.provider_manager, "get_provider", lambda *_: provider)
    monkeypatch.setattr(terminal_service, "prepare_terminal_for_destruction", lambda *_: None)
    monkeypatch.setattr(terminal_service, "cancel_child_assignments_for_terminal", lambda *_: None)
    monkeypatch.setattr(terminal_service, "cancel_workflows_for_terminal", lambda *_: None)
    monkeypatch.setattr(terminal_service.tmux_client, "window_exists", lambda *_: None)
    monkeypatch.setattr(
        terminal_service.tmux_client,
        "exact_pane_target",
        lambda *_: PaneDeliveryTarget("%41", "codex"),
    )
    monkeypatch.setattr(terminal_service, "EXIT_CONFIRMATION_TIMEOUT_SECONDS", 0.0)

    with (
        patch("cli_agent_orchestrator.clients.tmux.uuid") as mock_uuid,
        patch("cli_agent_orchestrator.clients.tmux.time.sleep"),
        patch("cli_agent_orchestrator.clients.tmux.subprocess.run") as run,
    ):
        mock_uuid.uuid4.return_value.hex = "abcd1234efgh"
        assert terminal_service.exit_terminal("writer00").success is False
        assert terminal_service.exit_terminal("writer00").success is False

    assert provider.exit_cli.call_count == 1
    assert run.call_args_list == [
        call(
            ["tmux", "load-buffer", "-b", "cao_abcd1234", "-"],
            input=b"/exit",
            check=True,
            timeout=10.0,
        ),
        call(
            [
                "tmux",
                "paste-buffer",
                "-p",
                "-b",
                "cao_abcd1234",
                "-t",
                "%41",
            ],
            check=True,
            timeout=10.0,
        ),
        call(
            ["tmux", "send-keys", "-t", "%41", "Enter"],
            check=True,
            timeout=10.0,
        ),
        call(
            ["tmux", "delete-buffer", "-b", "cao_abcd1234"],
            check=False,
            timeout=10.0,
        ),
    ]
    assert get_terminal_metadata("writer00")["runtime_lifecycle"] == "exit_pending"
    assert list_worktree_writer_leases()[0]["terminal_id"] == "writer00"
