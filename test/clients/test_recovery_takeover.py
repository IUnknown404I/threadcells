from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database

OLD_ID = "a11ce001"
WRITER_GENERATION = "a" * 32
RUNTIME_GENERATION = "11111111-1111-4111-8111-111111111111"


@pytest.fixture()
def takeover_db(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'takeover.db'}",
        connect_args={"check_same_thread": False},
    )
    database.Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setattr(database, "_ensure_terminal_worktree_authority_schema", lambda: None)
    monkeypatch.setattr(database, "_ensure_provider_execution_schema", lambda: None)
    monkeypatch.setattr(database, "_ensure_child_assignment_schema", lambda: None)
    monkeypatch.setattr(database, "_ensure_workflow_schema", lambda: None)
    monkeypatch.setattr(database, "_ensure_control_plane_schema", lambda: None)
    monkeypatch.setattr(database, "_ensure_usage_schema", lambda: None)
    worktree = str(tmp_path / "repo")
    (tmp_path / "repo").mkdir()
    with sessions() as db:
        db.add(
            database.TerminalModel(
                id=OLD_ID,
                tmux_session="cao-old",
                session_id=str(uuid.uuid4()),
                tmux_window="old-window",
                provider="codex",
                agent_profile="critical_sol_xhigh_owner",
                launch_worktree=worktree,
                write_enabled=True,
                writer_authority_generation=WRITER_GENERATION,
                context_role="supervisor",
                project_id="project-1",
                project_name="Recovery test",
                project_path=worktree,
                runtime_lifecycle="running",
                runtime_pane_id="%1",
                runtime_pane_pid=1234,
                runtime_generation=RUNTIME_GENERATION,
                runtime_generation_origin="launch",
                runtime_process_start_ticks=5678,
                runtime_process_group_id=1234,
                runtime_process_session_id=1234,
                creation_order=1,
                last_active=datetime.now(),
            )
        )
        db.add(
            database.WorktreeWriterLeaseModel(
                canonical_worktree=worktree,
                terminal_id=OLD_ID,
                authority_generation=WRITER_GENERATION,
            )
        )
        db.commit()
    return sessions, worktree


def _scope():
    return {
        "profile_revision_id": "profile-revision",
        "provider_config_revision_id": "provider-revision",
        "project_id": "project-1",
        "launch_mode": "recovery_takeover",
        "delegation_depth": 0,
        "target_terminal_id": OLD_ID,
        "expected_authority_generation": WRITER_GENERATION,
        "expected_runtime_generation": RUNTIME_GENERATION,
    }


def _grant(worktree: str, suffix: str):
    launch_id = f"launch-{suffix}"
    token = database.issue_owner_launch_grant(
        launch_id=launch_id,
        agent_profile="critical_sol_xhigh_owner",
        provider="codex",
        canonical_worktree=worktree,
        requested_session_name=None,
        grant_scope=_scope(),
    )
    return launch_id, token


def _claim_only(worktree: str, *, suffix: str = "one", new_terminal_id: str = "b22ce001"):
    launch_id, token = _grant(worktree, suffix)
    return database.claim_recovery_takeover(
        request_id=str(uuid.uuid4()),
        old_terminal_id=OLD_ID,
        expected_authority_generation=WRITER_GENERATION,
        expected_runtime_generation=RUNTIME_GENERATION,
        agent_profile="critical_sol_xhigh_owner",
        provider="codex",
        profile_revision_id="profile-revision",
        provider_config_revision_id="provider-revision",
        owner_grant_token=token,
        owner_grant_launch_id=launch_id,
        owner_grant_scope=_scope(),
        new_terminal_id=new_terminal_id,
        new_session_name=f"cao-recovery-{suffix}",
        new_session_id=str(uuid.uuid4()),
        new_window_name=f"recovery-{suffix}",
        new_runtime_generation=str(uuid.uuid4()),
    )


def _claim(worktree: str, *, suffix: str = "one", new_terminal_id: str = "b22ce001"):
    claimed = _claim_only(worktree, suffix=suffix, new_terminal_id=new_terminal_id)
    return database.fence_claimed_recovery_takeover(claimed["id"])


def _runtime_authority(terminal_id: str = OLD_ID):
    metadata = database.get_terminal_metadata(terminal_id)
    assert metadata is not None
    return {
        field: metadata.get(field) for field in database.TERMINAL_RUNTIME_DEATH_AUTHORITY_FIELDS
    }


def test_durable_claim_precedes_runtime_and_writer_fencing(takeover_db):
    sessions, worktree = takeover_db
    claimed = _claim_only(worktree, suffix="durable-claim", new_terminal_id="b22ce099")
    assert claimed["state"] == "claimed"
    with sessions() as db:
        old = db.get(database.TerminalModel, OLD_ID)
        lease = db.get(database.WorktreeWriterLeaseModel, worktree)
        assert old.runtime_lifecycle == "running"
        assert old.runtime_operation_kind == "recovery_takeover"
        assert old.runtime_operation_token == claimed["id"]
        assert lease.terminal_id == OLD_ID

    fenced = database.fence_claimed_recovery_takeover(claimed["id"])
    assert fenced["state"] == "fenced"


def test_runtime_death_enters_non_writable_recovery_without_leaking_writer(takeover_db):
    sessions, worktree = takeover_db

    state, workflows = database.mark_terminal_runtime_recovery_required_with_workflow_ids(
        OLD_ID,
        expected_runtime_authority=_runtime_authority(),
    )

    assert state == "recovery_required"
    assert workflows == []
    assert database.recovery_takeover_durable_eligibility(OLD_ID)["eligible"] is True
    assert database.acquire_terminal_runtime_transport(OLD_ID) is None
    assert database.mark_terminal_runtime_running(OLD_ID) is False
    with sessions() as db:
        old = db.get(database.TerminalModel, OLD_ID)
        assert old.runtime_lifecycle == "recovery_required"
        assert old.writer_authority_generation == WRITER_GENERATION
        assert db.get(database.WorktreeWriterLeaseModel, worktree) is None


def test_recovery_required_takeover_claim_survives_reconciliation_and_recreates_lease(
    takeover_db,
):
    sessions, worktree = takeover_db
    observed = _runtime_authority()
    state, _workflows = database.mark_terminal_runtime_recovery_required_with_workflow_ids(
        OLD_ID,
        expected_runtime_authority=observed,
    )
    assert state == "recovery_required"

    claimed = _claim_only(
        worktree,
        suffix="after-runtime-death",
        new_terminal_id="b22ce097",
    )
    assert claimed["state"] == "claimed"
    # The stale death observation loses to the takeover-owned terminal CAS.
    exited, _ = database.mark_terminal_runtime_exited_with_workflow_ids(
        OLD_ID,
        expected_runtime_authority=observed,
    )
    assert exited is False

    fenced = database.fence_claimed_recovery_takeover(claimed["id"])
    assert fenced["state"] == "fenced"
    with sessions() as db:
        old = db.get(database.TerminalModel, OLD_ID)
        lease = db.get(database.WorktreeWriterLeaseModel, worktree)
        assert old.runtime_lifecycle == "recovery_fenced"
        assert lease.terminal_id == "b22ce097"
        assert lease.authority_generation == fenced["new_authority_generation"]


def test_runtime_death_and_takeover_claim_converge_without_split_brain(takeover_db):
    sessions, worktree = takeover_db
    observed = _runtime_authority()

    def runtime_death():
        return database.mark_terminal_runtime_recovery_required_with_workflow_ids(
            OLD_ID,
            expected_runtime_authority=observed,
        )[0]

    def takeover():
        return _claim_only(
            worktree,
            suffix="death-race",
            new_terminal_id="b22ce096",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        death_future = pool.submit(runtime_death)
        claim_future = pool.submit(takeover)
        death_state = death_future.result()
        claimed = claim_future.result()

    assert death_state in {"recovery_required", "stale"}
    assert claimed["state"] == "claimed"
    fenced = database.fence_claimed_recovery_takeover(claimed["id"])
    assert fenced["state"] == "fenced"
    with sessions() as db:
        old = db.get(database.TerminalModel, OLD_ID)
        leases = db.query(database.WorktreeWriterLeaseModel).all()
        assert old.runtime_lifecycle == "recovery_fenced"
        assert len(leases) == 1
        assert leases[0].terminal_id == "b22ce096"


def test_unclaimed_recovery_can_converge_to_idempotent_exit(takeover_db):
    sessions, worktree = takeover_db
    state, _workflows = database.mark_terminal_runtime_recovery_required_with_workflow_ids(
        OLD_ID,
        expected_runtime_authority=_runtime_authority(),
    )
    assert state == "recovery_required"

    assert database.abandon_terminal_runtime_recovery(OLD_ID)[0] == "exited"
    assert database.abandon_terminal_runtime_recovery(OLD_ID)[0] == "exited"
    with sessions() as db:
        assert db.get(database.TerminalModel, OLD_ID).runtime_lifecycle == "exited"
        assert db.get(database.WorktreeWriterLeaseModel, worktree) is None


def test_generation_change_after_claim_fails_without_transferring_writer(takeover_db):
    sessions, worktree = takeover_db
    claimed = _claim_only(worktree, suffix="stale-after-claim", new_terminal_id="b22ce098")
    with sessions() as db:
        old = db.get(database.TerminalModel, OLD_ID)
        old.runtime_generation = str(uuid.uuid4())
        db.commit()

    failed = database.fence_claimed_recovery_takeover(claimed["id"])
    assert failed["state"] == "failed"
    assert failed["failure_reason"] == "RECOVERY_RUNTIME_GENERATION_STALE"
    with sessions() as db:
        old = db.get(database.TerminalModel, OLD_ID)
        lease = db.get(database.WorktreeWriterLeaseModel, worktree)
        assert old.runtime_lifecycle == "running"
        assert lease.terminal_id == OLD_ID


def test_pre_fence_failed_claim_does_not_permanently_block_recovery(takeover_db):
    sessions, worktree = takeover_db
    first = _claim_only(worktree, suffix="failed-first", new_terminal_id="b22ce091")
    failed = database.record_recovery_takeover_claim_wait(
        first["id"], "RECOVERY_RUNTIME_PROCESS_TREE_ACTIVE", terminal=True
    )
    assert failed["state"] == "failed"
    assert failed["fenced_at"] is None
    assert database.recovery_takeover_durable_eligibility(OLD_ID)["eligible"] is True

    second = _claim_only(worktree, suffix="replacement", new_terminal_id="b22ce092")
    assert second["state"] == "claimed"
    with sessions() as db:
        rows = (
            db.query(database.RecoveryTakeoverModel)
            .filter(database.RecoveryTakeoverModel.old_terminal_id == OLD_ID)
            .order_by(database.RecoveryTakeoverModel.created_at.asc())
            .all()
        )
        assert [row.state for row in rows] == ["failed", "claimed"]


def test_fence_and_writer_transfer_are_one_transaction(takeover_db):
    sessions, worktree = takeover_db
    takeover = _claim(worktree)

    with sessions() as db:
        old = db.get(database.TerminalModel, OLD_ID)
        lease = db.get(database.WorktreeWriterLeaseModel, worktree)
        assert old.runtime_lifecycle == "recovery_fenced"
        assert old.replaced_by_terminal_id == takeover["new_terminal_id"]
        assert lease.terminal_id == takeover["new_terminal_id"]
        assert lease.authority_generation == takeover["new_authority_generation"]
        assert db.get(database.TerminalModel, takeover["new_terminal_id"]) is None
        assert db.get(database.RecoveryTakeoverModel, takeover["id"]).state == "fenced"


def test_old_workflow_effect_and_transport_are_rejected_after_fence(takeover_db):
    sessions, worktree = takeover_db
    with sessions() as db:
        workflow = database.WorkflowModel(root_terminal_id=OLD_ID, status="open")
        db.add(workflow)
        db.flush()
        turn = database.WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="external_input",
            state="sent",
            dedupe_key="old-turn",
        )
        db.add(turn)
        db.flush()
        workflow.active_turn_id = turn.id
        db.add(
            database.WorkflowTurnReceiptModel(
                workflow_turn_id=turn.id,
                receiver_terminal_id=OLD_ID,
            )
        )
        db.commit()
        turn_id = turn.id

    _claim(worktree)

    assert database.claim_workflow_effect(OLD_ID, turn_id, "send_message", "late") is None
    assert database.acquire_terminal_runtime_transport(OLD_ID) is None
    assert database.mark_terminal_runtime_running(OLD_ID) is False
    with pytest.raises(ValueError):
        database.create_inbox_message("sender", OLD_ID, "late message")
    with sessions() as db:
        assert db.query(database.WorkflowModel).one().status == "cancelled"


def test_processing_and_genuine_owner_gate_fail_closed(takeover_db):
    sessions, _worktree = takeover_db
    with sessions() as db:
        db.add(
            database.ProviderExecutionLeaseModel(
                terminal_id=OLD_ID,
                workflow_turn_id=991,
            )
        )
        db.commit()
    assert (
        database.recovery_takeover_durable_eligibility(OLD_ID)["reason_code"]
        == "RECOVERY_PROVIDER_EXECUTION_ACTIVE"
    )
    with sessions() as db:
        db.query(database.ProviderExecutionLeaseModel).delete()
        db.add(database.WorkflowModel(root_terminal_id=OLD_ID, status="owner_gate"))
        db.commit()
    assert (
        database.recovery_takeover_durable_eligibility(OLD_ID)["reason_code"]
        == "RECOVERY_GENUINE_OWNER_GATE"
    )


def test_claimed_privileged_effect_blocks_takeover(takeover_db):
    sessions, _worktree = takeover_db
    with sessions() as db:
        workflow = database.WorkflowModel(root_terminal_id=OLD_ID, status="open")
        db.add(workflow)
        db.flush()
        turn = database.WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="external_input",
            state="sent",
            dedupe_key="effect-turn",
        )
        db.add(turn)
        db.flush()
        workflow.active_turn_id = turn.id
        db.add(
            database.WorkflowTurnReceiptModel(
                workflow_turn_id=turn.id,
                receiver_terminal_id=OLD_ID,
            )
        )
        db.commit()
        turn_id = turn.id
    assert database.claim_workflow_effect(OLD_ID, turn_id, "send_message", "once")
    assert (
        database.recovery_takeover_durable_eligibility(OLD_ID)["reason_code"]
        == "RECOVERY_PRIVILEGED_EFFECT_UNRESOLVED"
    )


def test_stale_generation_fails_closed(takeover_db):
    assert (
        database.recovery_takeover_durable_eligibility(
            OLD_ID, expected_authority_generation="b" * 32
        )["reason_code"]
        == "RECOVERY_AUTHORITY_GENERATION_STALE"
    )


def test_non_supervisor_or_projectless_target_fails_closed(takeover_db):
    sessions, _worktree = takeover_db
    with sessions() as db:
        terminal = db.get(database.TerminalModel, OLD_ID)
        terminal.context_role = "work"
        db.commit()
    assert (
        database.recovery_takeover_durable_eligibility(OLD_ID)["reason_code"]
        == "RECOVERY_TARGET_IDENTITY_MISMATCH"
    )


def test_concurrent_takeovers_have_one_winner(takeover_db):
    _sessions, worktree = takeover_db

    def attempt(index: int):
        try:
            return _claim(
                worktree,
                suffix=f"concurrent-{index}",
                new_terminal_id=f"b22ce00{index}",
            )["id"]
        except database.RecoveryTakeoverRejected as exc:
            return exc.reason_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, (1, 2)))
    assert sum(len(value) == 32 for value in results) == 1
    assert "RECOVERY_TAKEOVER_ALREADY_CLAIMED" in results


def test_dispatch_claim_is_exactly_once_and_retry_rotates_runtime_generation(takeover_db):
    _sessions, worktree = takeover_db
    takeover = _claim(worktree)
    first = database.claim_recovery_takeover_dispatch(takeover["id"])
    second = database.claim_recovery_takeover_dispatch(takeover["id"])
    assert first["state"] == second["state"] == "dispatching"
    assert first["dispatch_attempt_count"] == second["dispatch_attempt_count"] == 1
    old_generation = first["new_runtime_generation"]
    assert database.reset_recovery_takeover_after_confirmed_prestart_failure(takeover["id"])
    retry = database.get_recovery_takeover(takeover["id"])
    assert retry["state"] == "fenced"
    assert retry["new_runtime_generation"] != old_generation


def test_reserved_successor_is_admitted_and_completed_on_same_writer_epoch(takeover_db):
    sessions, worktree = takeover_db
    context_id = "recovery-context"
    branch = f"cao/session/{context_id}"
    base_revision = "b" * 40
    with sessions() as db:
        old = db.get(database.TerminalModel, OLD_ID)
        old.managed_worktree_kind = "supervisor"
        old.managed_worktree_source = worktree
        old.managed_worktree_branch = branch
        old.managed_worktree_commit = base_revision
        old.writable_work_context_id = context_id
        old.workspace_classification = "managed_isolated"
        db.add(
            database.WritableWorkContextModel(
                id=context_id,
                request_id="00000000-0000-4000-8000-000000000095",
                project_id="project-1",
                session_id=old.session_id,
                terminal_id=OLD_ID,
                canonical_source=worktree,
                canonical_worktree=worktree,
                branch=branch,
                base_revision=base_revision,
                state="admitted",
                writer_authority_generation=WRITER_GENERATION,
            )
        )
        db.commit()
    takeover = _claim(worktree)
    dispatched = database.claim_recovery_takeover_dispatch(takeover["id"])

    created = database.create_terminal(
        takeover["new_terminal_id"],
        takeover["new_session_name"],
        takeover["new_window_name"],
        "codex",
        agent_profile="critical_sol_xhigh_owner",
        session_lifetime_id=takeover["new_session_id"],
        launch_worktree=worktree,
        write_enabled=True,
        context_role="supervisor",
        managed_worktree_kind="supervisor",
        managed_worktree_source=worktree,
        managed_worktree_branch=branch,
        managed_worktree_commit=base_revision,
        writable_work_context_id=context_id,
        workspace_classification="managed_isolated",
        project_id="project-1",
        runtime_pane_id="%2",
        runtime_pane_pid=4321,
        runtime_generation=dispatched["new_runtime_generation"],
        runtime_process_start_ticks=8765,
        runtime_process_group_id=4321,
        runtime_process_session_id=4321,
        recovery_takeover_id=takeover["id"],
    )
    assert created["writer_authority_generation"] == takeover["new_authority_generation"]
    assert database.get_recovery_takeover(takeover["id"])["state"] == "admitted"
    assert database.mark_terminal_runtime_running(takeover["new_terminal_id"])
    assert database.transition_writable_work_context(
        context_id,
        expected_states=("launching",),
        state="admitted",
        event_type="recovery_supervisor_admitted",
    )
    assert database.mark_recovery_takeover_completed(takeover["id"])

    with sessions() as db:
        lease = db.get(database.WorktreeWriterLeaseModel, worktree)
        assert lease.terminal_id == takeover["new_terminal_id"]
        assert lease.authority_generation == takeover["new_authority_generation"]
        assert db.query(database.WorktreeWriterLeaseModel).count() == 1
        context = db.get(database.WritableWorkContextModel, context_id)
        assert context.terminal_id == takeover["new_terminal_id"]
        assert context.session_id == takeover["new_session_id"]
        assert context.state == "admitted"
        assert context.writer_authority_generation == takeover["new_authority_generation"]
        audit = {row.event_type for row in db.query(database.RecoveryTakeoverAuditModel).all()}
        assert {
            "takeover_requested",
            "takeover_claim_acquired",
            "old_authority_fenced",
            "recovery_provider_dispatch_claimed",
            "new_recovery_supervisor_admitted",
            "takeover_completed",
        } <= audit


def test_uncertain_dispatch_is_never_reclaimed(takeover_db):
    _sessions, worktree = takeover_db
    takeover = _claim(worktree)
    database.claim_recovery_takeover_dispatch(takeover["id"])
    assert database.mark_recovery_takeover_dispatch_uncertain(
        takeover["id"], "RECOVERY_PROVIDER_DISPATCH_UNCERTAIN"
    )
    assert (
        database.claim_recovery_takeover_dispatch(takeover["id"])["state"] == "dispatch_uncertain"
    )


def test_completed_results_are_preserved_not_replayed(takeover_db):
    sessions, worktree = takeover_db
    with sessions() as db:
        workflow = database.WorkflowModel(
            root_terminal_id=OLD_ID,
            status="terminal",
            terminal_reason="completed before recovery",
        )
        db.add(workflow)
        db.commit()
        workflow_id = workflow.id
    _claim(worktree)
    with sessions() as db:
        preserved = db.get(database.WorkflowModel, workflow_id)
        assert preserved.status == "terminal"
        assert preserved.terminal_reason == "completed before recovery"


def test_queued_old_workflow_turn_is_explicitly_cancelled_not_replayed(takeover_db):
    sessions, worktree = takeover_db
    with sessions() as db:
        workflow = database.WorkflowModel(root_terminal_id=OLD_ID, status="open")
        db.add(workflow)
        db.flush()
        turn = database.WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="external_input",
            state="queued",
            dedupe_key="queued-before-takeover",
        )
        db.add(turn)
        db.commit()
        workflow_id = workflow.id
        turn_id = turn.id

    _claim(worktree)

    with sessions() as db:
        workflow = db.get(database.WorkflowModel, workflow_id)
        turn = db.get(database.WorkflowTurnModel, turn_id)
        assert workflow.status == "cancelled"
        assert workflow.terminal_reason == (
            "supervisor replaced by owner-authorized recovery takeover"
        )
        assert turn.state == "cancelled"
