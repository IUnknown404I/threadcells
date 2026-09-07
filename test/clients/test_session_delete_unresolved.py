"""Safe Session deletion planning, cancellation, and audit regressions."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.database import (
    Base,
    ChildAssignmentModel,
    DelegationResultModel,
    InboxModel,
    ProviderExecutionLeaseModel,
    RecoveryTakeoverModel,
    SessionDeletionCancellationAuditModel,
    TerminalModel,
    WorkflowEffectModel,
    WorkflowModel,
    WorkflowTurnModel,
    WorkflowTurnReceiptModel,
    WorktreeWriterLeaseModel,
)
from cli_agent_orchestrator.models.inbox import ChildAssignmentStatus, MessageStatus
from cli_agent_orchestrator.services import interaction_read_model_service, session_service


def _install_database(monkeypatch, url: str = "sqlite:///:memory:"):
    engine = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    for name in (
        "_ensure_terminal_worktree_authority_schema",
        "_ensure_workflow_schema",
        "_ensure_child_assignment_schema",
        "_ensure_delegation_result_schema",
        "_ensure_provider_execution_schema",
        "_ensure_session_deletion_receipt_schema",
        "_ensure_terminal_ui_projection_schema",
    ):
        monkeypatch.setattr(database, name, lambda: None)
    return engine


def _terminal(terminal_id: str = "owner", session_id: str = "session") -> TerminalModel:
    return TerminalModel(
        id=terminal_id,
        tmux_session=f"cao-{session_id}",
        session_id=session_id,
        tmux_window=terminal_id,
        provider="codex",
        runtime_lifecycle="exited",
        last_active=datetime(2026, 9, 7, 10, 0, 0),
    )


def _plan(session_id: str = "session"):
    return database.get_session_unresolved_work_plan(session_id)


def test_historical_lifecycle_axes_do_not_block_or_enter_current_queue(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add_all([_terminal(), _terminal("child")])
        workflow = WorkflowModel(root_terminal_id="owner", status="terminal")
        db.add(workflow)
        db.flush()
        turn = WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="external_input",
            dedupe_key="historical",
            state="sent",
        )
        db.add(turn)
        db.flush()
        db.add_all(
            [
                WorkflowEffectModel(
                    workflow_id=workflow.id,
                    workflow_turn_id=turn.id,
                    effect_kind="complete_workflow",
                    effect_key="done",
                    state="completed",
                    claim_token="claim",
                ),
                ChildAssignmentModel(
                    parent_terminal_id="owner",
                    child_terminal_id="child",
                    status=ChildAssignmentStatus.RESULT_ACKNOWLEDGED.value,
                ),
            ]
        )
        db.commit()

    assert _plan()["eligible"] is True
    assert interaction_read_model_service.list_session_current_queue_counts(["session"]) == {
        "session": 0
    }

    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).one()
        workflow.status = "cancelled"
        turn = db.query(WorkflowTurnModel).one()
        turn.state = "queued"
        turn.superseded_by_turn_id = 999
        db.commit()
    assert _plan()["eligible"] is True
    assert (
        interaction_read_model_service.list_session_current_queue_counts(["session"])["session"]
        == 0
    )


def test_cancelled_workflow_queued_turn_is_historical_without_supersession(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add(_terminal())
        workflow = WorkflowModel(root_terminal_id="owner", status="cancelled")
        db.add(workflow)
        db.flush()
        db.add(
            WorkflowTurnModel(
                workflow_id=workflow.id,
                kind="external_input",
                dedupe_key="cancelled-workflow",
                state="queued",
            )
        )
        db.commit()

    assert _plan()["eligible"] is True
    assert interaction_read_model_service.list_session_current_queue_counts(["session"]) == {
        "session": 0
    }


def test_superseded_turn_and_completed_effect_add_no_open_workflow_blocker(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add(_terminal())
        workflow = WorkflowModel(root_terminal_id="owner", status="open")
        db.add(workflow)
        db.flush()
        turn = WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="external_input",
            dedupe_key="superseded",
            state="queued",
            superseded_by_turn_id=999,
        )
        db.add(turn)
        db.flush()
        db.add(
            WorkflowEffectModel(
                workflow_id=workflow.id,
                workflow_turn_id=turn.id,
                effect_kind="send_message",
                effect_key="completed",
                state="completed",
                claim_token="claim",
            )
        )
        db.commit()

    plan = _plan()
    assert plan["cancellable"] is True
    assert [blocker["category"] for blocker in plan["blockers"]] == ["unfinished_workflows"]


def test_legacy_session_identity_cannot_hide_unresolved_work(monkeypatch):
    _install_database(monkeypatch)
    legacy = _terminal()
    legacy.session_id = None
    legacy.tmux_session = "cao-legacy"
    with database.SessionLocal() as db:
        db.add(legacy)
        workflow = WorkflowModel(root_terminal_id="owner", status="open")
        db.add(workflow)
        db.commit()

    plan = database.get_session_unresolved_work_plan("legacy:cao-legacy")
    assert plan["cancellable"] is True
    assert plan["reason_codes"] == ["WORKFLOW_OPEN"]


def test_queued_unadmitted_input_is_cancellable_audited_and_idempotent(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add(_terminal())
        workflow = WorkflowModel(root_terminal_id="owner", status="open")
        db.add(workflow)
        db.flush()
        db.add(
            WorkflowTurnModel(
                workflow_id=workflow.id,
                kind="external_input",
                dedupe_key="queued",
                state="queued",
            )
        )
        db.commit()

    before = _plan()
    assert before["cancellable"] is True
    assert before["unsafe_count"] == 0
    assert (
        interaction_read_model_service.list_session_current_queue_counts(["session"])["session"]
        == 1
    )

    first = database.cancel_session_work_for_deletion(
        "session", expected_plan_token=before["plan_token"]
    )
    second = database.cancel_session_work_for_deletion(
        "session", expected_plan_token=before["plan_token"]
    )

    assert first["cancelled"] is True and first["residual"]["eligible"] is True
    assert second["cancelled"] is True and second["already_cancelled"] is True
    assert _plan()["eligible"] is True
    with database.SessionLocal() as db:
        assert db.query(WorkflowModel).one().status == "cancelled"
        assert db.query(WorkflowTurnModel).one().state == "cancelled"
        audits = db.query(SessionDeletionCancellationAuditModel).all()
        assert {(row.item_kind, row.previous_state, row.final_state) for row in audits} == {
            ("workflow", "open", "cancelled"),
            ("workflow_turn", "queued", "cancelled"),
        }


def test_sent_turn_requires_exact_receiver_receipt_to_be_historical(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add_all([_terminal(), _terminal("wrong")])
        workflow = WorkflowModel(root_terminal_id="owner", status="open")
        db.add(workflow)
        db.flush()
        turn = WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="external_input",
            dedupe_key="sent",
            state="sent",
        )
        db.add(turn)
        db.flush()
        db.add(
            WorkflowTurnReceiptModel(
                workflow_turn_id=turn.id,
                receiver_terminal_id="wrong",
            )
        )
        db.commit()

    assert any(item["category"] == "queued_work" for item in _plan()["blockers"])
    with database.SessionLocal() as db:
        turn = db.query(WorkflowTurnModel).one()
        db.add(
            WorkflowTurnReceiptModel(
                workflow_turn_id=turn.id,
                receiver_terminal_id="owner",
            )
        )
        db.commit()
    plan = _plan()
    assert not any(item["category"] == "queued_work" for item in plan["blockers"])
    assert plan["cancellable"] is True  # the OPEN workflow shell remains explicit


def test_pending_inbox_cancellation_preserves_canonical_result(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add_all([_terminal(), _terminal("child")])
        assignment = ChildAssignmentModel(
            parent_terminal_id="owner",
            child_terminal_id="child",
            status=ChildAssignmentStatus.RESULT_ACKNOWLEDGED.value,
        )
        db.add(assignment)
        db.flush()
        result = DelegationResultModel(
            id="durable-result",
            child_assignment_id=assignment.id,
            delegation_kind="assign",
            parent_terminal_id="owner",
            child_terminal_id="child",
            authorship="child",
            status="complete",
            document_json='{"summary":"preserved"}',
        )
        db.add(result)
        notice = InboxModel(
            sender_id="child",
            receiver_id="owner",
            message="result available",
            status=MessageStatus.PENDING.value,
            result_id=result.id,
            kind="delegation_result_notice",
        )
        db.add(notice)
        db.commit()

    plan = _plan()
    assert plan["cancellable"] is True
    assert {row["category"] for row in plan["blockers"]} == {"pending_delivery"}
    database.cancel_session_work_for_deletion("session", expected_plan_token=plan["plan_token"])
    with database.SessionLocal() as db:
        assert db.query(InboxModel).one().status == MessageStatus.SUPERSEDED.value
        preserved = db.get(DelegationResultModel, "durable-result")
        assert preserved is not None
        assert preserved.status == "complete"
        assert preserved.document_json == '{"summary":"preserved"}'

    deleted = database.delete_terminals_by_session_lifetime(
        "session",
        "cao-session",
        expected_terminal_ids=["owner", "child"],
    )
    assert deleted["logical_deleted"] == 2
    with database.SessionLocal() as db:
        assert db.get(DelegationResultModel, "durable-result") is not None
        assert db.query(InboxModel).one().status == MessageStatus.SUPERSEDED.value
        assert db.query(SessionDeletionCancellationAuditModel).count() == 1


def test_session_owned_child_assignment_is_cancelled_with_result_history(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add_all([_terminal(), _terminal("child")])
        db.add(
            ChildAssignmentModel(
                parent_terminal_id="owner",
                child_terminal_id="child",
                status=ChildAssignmentStatus.AWAITING_RESULT.value,
            )
        )
        db.commit()

    plan = _plan()
    assert plan["cancellable"] is True
    database.cancel_session_work_for_deletion("session", expected_plan_token=plan["plan_token"])
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).one()
        result = db.query(DelegationResultModel).one()
        assert assignment.status == ChildAssignmentStatus.CANCELLED.value
        assert result.status == "cancelled"
        assert result.child_assignment_id == assignment.id


def test_cancellation_mutates_and_audits_only_exact_planned_rows(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add_all([_terminal(), _terminal("child")])
        workflow = WorkflowModel(root_terminal_id="owner", status="open")
        db.add(workflow)
        db.flush()
        superseded_turn = WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="external_input",
            dedupe_key="superseded-history",
            state="queued",
            superseded_by_turn_id=999,
            superseded_at=datetime(2026, 9, 7, 9, 0, 0),
        )
        superseded_assignment = ChildAssignmentModel(
            parent_terminal_id="owner",
            child_terminal_id="child",
            status=ChildAssignmentStatus.AWAITING_RESULT.value,
            review_superseded_at=datetime(2026, 9, 7, 9, 0, 0),
        )
        db.add_all([superseded_turn, superseded_assignment])
        db.commit()
        workflow_id = int(workflow.id)
        turn_id = int(superseded_turn.id)
        assignment_id = int(superseded_assignment.id)

    plan = _plan()
    assert plan["cancellable_count"] == 1
    assert plan["blockers"] == [
        {
            "category": "unfinished_workflows",
            "count": 1,
            "disposition": "cancellable",
            "reason_codes": ["WORKFLOW_OPEN"],
        }
    ]

    result = database.cancel_session_work_for_deletion(
        "session", expected_plan_token=plan["plan_token"]
    )
    # The existing terminal-cleanup phase runs after the exact cancellation
    # transaction during Session deletion and must preserve the same history.
    database.cancel_workflows_for_terminal("owner")

    assert result["cancelled"] is True
    assert result["cancelled_count"] == 1
    with database.SessionLocal() as db:
        assert db.get(WorkflowModel, workflow_id).status == "cancelled"
        assert db.get(WorkflowTurnModel, turn_id).state == "queued"
        assert db.get(ChildAssignmentModel, assignment_id).status == (
            ChildAssignmentStatus.AWAITING_RESULT.value
        )
        assert db.query(DelegationResultModel).count() == 0
        audits = db.query(SessionDeletionCancellationAuditModel).all()
        assert [(row.item_kind, row.item_id, row.final_state) for row in audits] == [
            ("workflow", str(workflow_id), "cancelled")
        ]


def test_unsafe_authority_is_fail_closed_and_safe_rows_are_not_partially_cancelled(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add(_terminal())
        workflow = WorkflowModel(root_terminal_id="owner", status="open")
        db.add(workflow)
        db.flush()
        turn = WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="external_input",
            dedupe_key="mixed",
            state="queued",
        )
        db.add(turn)
        db.flush()
        db.add(
            WorkflowEffectModel(
                workflow_id=workflow.id,
                workflow_turn_id=turn.id,
                effect_kind="send_message",
                effect_key="unsafe",
                state="indeterminate",
                claim_token="claim",
            )
        )
        db.commit()

    plan = _plan()
    assert plan["cancellable"] is False
    assert "INDETERMINATE_EFFECT" in plan["reason_codes"]
    rejected = database.cancel_session_work_for_deletion("session", expected_plan_token="0" * 64)
    assert rejected["cancelled"] is False
    with database.SessionLocal() as db:
        assert db.query(WorkflowModel).one().status == "open"
        assert db.query(WorkflowTurnModel).one().state == "queued"
        assert db.query(SessionDeletionCancellationAuditModel).count() == 0


def test_provider_writer_recovery_and_cross_session_authority_are_unsafe(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add_all([_terminal(), _terminal("external", "other")])
        workflow = WorkflowModel(root_terminal_id="owner", status="terminal")
        db.add(workflow)
        db.flush()
        turn = WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="external_input",
            dedupe_key="provider",
            state="sent",
        )
        db.add(turn)
        db.flush()
        db.add_all(
            [
                ProviderExecutionLeaseModel(terminal_id="owner", workflow_turn_id=turn.id),
                WorktreeWriterLeaseModel(canonical_worktree="/tmp/session", terminal_id="owner"),
                RecoveryTakeoverModel(
                    id="recovery",
                    request_id="request",
                    old_terminal_id="owner",
                    new_terminal_id="external",
                    old_session_id="session",
                    expected_authority_generation="old-writer",
                    expected_runtime_generation="old-runtime",
                    new_authority_generation="new-writer",
                    canonical_worktree="/tmp/session",
                    project_id="project",
                    agent_profile="developer",
                    provider="codex",
                    owner_grant_id="grant",
                    new_session_name="cao-other",
                    new_session_id="other",
                    new_window_name="developer",
                    new_runtime_generation="new-runtime",
                    state="claimed",
                ),
                InboxModel(
                    sender_id="owner",
                    receiver_id="external",
                    message="outbound",
                    status=MessageStatus.PENDING.value,
                ),
                ChildAssignmentModel(
                    parent_terminal_id="owner",
                    child_terminal_id="external",
                    status=ChildAssignmentStatus.AWAITING_RESULT.value,
                ),
            ]
        )
        db.commit()

    plan = _plan()
    assert plan["cancellable"] is False
    assert {
        "PROVIDER_EXECUTION_ACTIVE",
        "WRITER_LEASE_ACTIVE",
        "RECOVERY_TAKEOVER_ACTIVE",
        "CROSS_SESSION_DELIVERY",
        "CROSS_SESSION_ASSIGNMENT",
    }.issubset(set(plan["reason_codes"]))


def test_recovery_lifecycle_states_remain_structured_unsafe_authority(monkeypatch):
    _install_database(monkeypatch)
    terminal = _terminal()
    terminal.runtime_lifecycle = "recovery_required"
    with database.SessionLocal() as db:
        db.add(terminal)
        db.commit()

    plan = _plan()
    assert plan["cancellable"] is False
    assert plan["blockers"] == [
        {
            "category": "recovery_authority",
            "count": 1,
            "disposition": "unsafe",
            "reason_codes": ["RECOVERY_RECONCILIATION_REQUIRED"],
        }
    ]


def test_provider_execution_leaves_queue_independently_from_runtime_lifecycle(monkeypatch):
    _install_database(monkeypatch)
    terminal = _terminal()
    terminal.runtime_lifecycle = "running"
    with database.SessionLocal() as db:
        db.add(terminal)
        workflow = WorkflowModel(root_terminal_id="owner", status="terminal")
        db.add(workflow)
        db.flush()
        turn = WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="external_input",
            dedupe_key="provider-active",
            state="sent",
        )
        db.add(turn)
        db.flush()
        db.add(ProviderExecutionLeaseModel(terminal_id="owner", workflow_turn_id=turn.id))
        db.commit()

    assert (
        interaction_read_model_service.list_session_current_queue_counts(["session"])["session"]
        == 1
    )
    assert {
        "PROVIDER_EXECUTION_ACTIVE",
        "RUNTIME_DEATH_UNCONFIRMED",
    }.issubset(set(_plan()["reason_codes"]))

    with database.SessionLocal() as db:
        db.query(ProviderExecutionLeaseModel).delete()
        db.commit()
    assert (
        interaction_read_model_service.list_session_current_queue_counts(["session"])["session"]
        == 0
    )
    assert _plan()["reason_codes"] == ["RUNTIME_DEATH_UNCONFIRMED"]

    with database.SessionLocal() as db:
        db.query(TerminalModel).one().runtime_lifecycle = "exited"
        db.commit()
    assert _plan()["eligible"] is True


def test_mixed_processed_history_plus_one_unresolved_turn_has_exact_queue_count(monkeypatch):
    engine = _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add(_terminal())
        workflow = WorkflowModel(root_terminal_id="owner", status="open")
        db.add(workflow)
        db.flush()
        processed = WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="external_input",
            dedupe_key="processed",
            state="sent",
        )
        unresolved = WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="external_input",
            dedupe_key="unresolved",
            state="claimed",
        )
        db.add_all([processed, unresolved])
        db.flush()
        db.add(
            WorkflowTurnReceiptModel(
                workflow_turn_id=processed.id,
                receiver_terminal_id="owner",
            )
        )
        db.commit()

    statements: list[str] = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        authority = session_service.SessionAuthority(
            session_id="session",
            session_name="cao-session",
            terminals=[{"id": "owner", "runtime_lifecycle": "exited"}],
            retained_resources=[],
            deleted=False,
            runtime_exists=False,
        )
        preflight = session_service._session_deletion_preflight(authority)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    current = interaction_read_model_service.list_interactions("session", mode="current", limit=20)
    assert preflight["current_queue_count"] == 1
    assert current["total"] == 1
    assert [item["workflow"]["turn_id"] for item in current["items"]] == [2]
    assert preflight["cancellable"] is True
    # Fixed query shape: no per-terminal or per-blocker fetch loop.
    assert len(statements) == 11


def test_plan_token_revalidation_fails_closed_after_state_change(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add(_terminal())
        workflow = WorkflowModel(root_terminal_id="owner", status="open")
        db.add(workflow)
        db.commit()
    original = _plan()
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).one()
        workflow.status = "owner_gate"
        db.commit()

    result = database.cancel_session_work_for_deletion(
        "session", expected_plan_token=original["plan_token"]
    )
    assert result == {"cancelled": False, "reason_code": "SESSION_DELETE_PLAN_CHANGED"}


def test_cancellation_fails_closed_when_session_terminal_membership_changes(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add(_terminal())
        workflow = WorkflowModel(root_terminal_id="owner", status="open")
        db.add(workflow)
        db.commit()
    original = _plan()
    with database.SessionLocal() as db:
        db.add(_terminal("late-terminal"))
        db.commit()

    result = database.cancel_session_work_for_deletion(
        "session",
        expected_plan_token=original["plan_token"],
        expected_terminal_ids=["owner"],
    )
    assert result == {"cancelled": False, "reason_code": "SESSION_IDENTITY_CHANGED"}
    with database.SessionLocal() as db:
        assert db.query(WorkflowModel).one().status == "open"
        assert db.query(SessionDeletionCancellationAuditModel).count() == 0


def test_cancellation_plan_is_bounded_and_overflow_fails_closed(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add(_terminal())
        db.add_all(
            [WorkflowModel(root_terminal_id="owner", status="open") for _index in range(501)]
        )
        db.commit()

    plan = _plan()
    assert plan["cancellable"] is False
    assert plan["plan_limit"] == 500
    assert plan["cancellable_count"] == 500
    assert "CANCELLATION_PLAN_TOO_LARGE" in plan["reason_codes"]


def test_terminal_membership_scan_is_bounded_and_overflow_fails_closed(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add_all([_terminal(f"owner-{index}") for index in range(501)])
        db.commit()

    plan = _plan()
    assert plan["cancellable"] is False
    assert plan["plan_limit"] == 500
    assert plan["cancellable_count"] == 0
    assert plan["unsafe_count"] == 1
    assert plan["reason_codes"] == ["CANCELLATION_PLAN_TOO_LARGE"]


def test_concurrent_cancellation_attempts_have_one_effective_transition(monkeypatch, tmp_path):
    engine = _install_database(monkeypatch, f"sqlite:///{tmp_path / 'concurrent.sqlite'}")
    with database.SessionLocal() as db:
        db.add(_terminal())
        workflow = WorkflowModel(root_terminal_id="owner", status="open")
        db.add(workflow)
        db.flush()
        db.add(
            WorkflowTurnModel(
                workflow_id=workflow.id,
                kind="external_input",
                dedupe_key="concurrent",
                state="queued",
            )
        )
        db.commit()
    plan = _plan()

    def cancel():
        return database.cancel_session_work_for_deletion(
            "session", expected_plan_token=plan["plan_token"]
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: cancel(), range(2)))

    assert all(result["cancelled"] is True for result in results)
    assert sorted(bool(result.get("already_cancelled")) for result in results) == [False, True]
    with database.SessionLocal() as db:
        assert db.query(SessionDeletionCancellationAuditModel).count() == 2
        assert db.query(WorkflowModel).one().status == "cancelled"
    engine.dispose()


def test_concurrent_cancel_and_delete_has_one_effective_deletion_lifecycle(monkeypatch, tmp_path):
    engine = _install_database(monkeypatch, f"sqlite:///{tmp_path / 'delete-race.sqlite'}")
    with database.SessionLocal() as db:
        db.add(_terminal())
        workflow = WorkflowModel(root_terminal_id="owner", status="open")
        db.add(workflow)
        db.flush()
        db.add(
            WorkflowTurnModel(
                workflow_id=workflow.id,
                kind="external_input",
                dedupe_key="delete-race",
                state="queued",
            )
        )
        db.commit()
    plan = _plan()

    def cancel_and_delete():
        cancellation = database.cancel_session_work_for_deletion(
            "session",
            expected_plan_token=plan["plan_token"],
            expected_terminal_ids=["owner"],
        )
        assert cancellation["cancelled"] is True
        return database.delete_terminals_by_session_lifetime(
            "session",
            "cao-session",
            expected_terminal_ids=["owner"],
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: cancel_and_delete(), range(2)))

    assert sum(int(result["logical_deleted"]) for result in results) == 1
    assert sorted(bool(result["already_deleted"]) for result in results) == [False, True]
    with database.SessionLocal() as db:
        assert db.query(TerminalModel).count() == 0
        assert db.query(SessionDeletionCancellationAuditModel).count() == 2
    engine.dispose()
