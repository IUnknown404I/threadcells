import json
from datetime import datetime, timedelta

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.database import (
    Base,
    CapacitySettingsModel,
    ChildAssignmentModel,
    DelegationResultModel,
    InboxModel,
    ProviderExecutionLeaseModel,
    RecoveryTakeoverModel,
    SessionDeletionReceiptModel,
    TerminalModel,
    WorkflowEffectModel,
    WorkflowModel,
    WorkflowTurnModel,
    WorkflowTurnReceiptModel,
    WorktreeWriterLeaseModel,
    WritableWorkContextModel,
)
from cli_agent_orchestrator.services import (
    interaction_read_model_service,
    ui_read_model_service,
)


def _install_database(monkeypatch, url="sqlite:///:memory:"):
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(database, "_terminal_ui_projection_schema_ready", False)
    monkeypatch.setattr(database, "_terminal_ui_projection_schema_engine_identity", None)
    for name in (
        "_ensure_terminal_worktree_authority_schema",
        "_ensure_provider_execution_schema",
        "_ensure_workflow_schema",
        "_ensure_child_assignment_schema",
        "_ensure_delegation_result_schema",
    ):
        monkeypatch.setattr(database, name, lambda: None)
    return engine


def _interaction_terminal(
    terminal_id: str = "owner", *, session_id: str = "interaction-session"
) -> TerminalModel:
    return TerminalModel(
        id=terminal_id,
        tmux_session="cao-interactions",
        session_id=session_id,
        tmux_window=terminal_id,
        provider="codex",
        runtime_lifecycle="running",
        creation_order=1,
        last_active=datetime(2026, 9, 7, 9, 0, 0),
    )


def _seed_retirement_projection(*, parent: str, children: tuple[tuple[str, str], ...]) -> None:
    """Persist completed history with cleanup claims at distinct runtime boundaries."""
    now = datetime(2026, 8, 25, 10, 0, 0)
    with database.SessionLocal() as db:
        db.add(
            TerminalModel(
                id=parent,
                tmux_session="cao-retirement",
                session_id="lifetime-retirement",
                tmux_window=parent,
                provider="codex",
                runtime_lifecycle="running",
                last_active=now,
            )
        )
        db.add(
            WorkflowModel(
                root_terminal_id=parent,
                status="terminal",
                terminal_reason="completed parent",
                created_at=now,
                updated_at=now,
            )
        )
        for index, (child, lifecycle) in enumerate(children):
            worktree = f"/protected/history/{child}"
            db.add(
                TerminalModel(
                    id=child,
                    tmux_session="cao-retirement",
                    session_id="lifetime-retirement",
                    tmux_window=child,
                    provider="codex",
                    runtime_lifecycle=lifecycle,
                    runtime_exited_at=now if lifecycle == "exited" else None,
                    launch_worktree=worktree,
                    managed_worktree_kind="task",
                    managed_worktree_source="/protected/source",
                    managed_worktree_branch=f"cao/task/{child}",
                    managed_worktree_commit=f"{index + 1:040x}",
                    last_active=now,
                )
            )
            db.add(
                WorkflowModel(
                    root_terminal_id=child,
                    status="terminal",
                    terminal_reason="completed child",
                    created_at=now,
                    updated_at=now,
                )
            )
            intent = {
                "version": 1,
                "terminal_id": child,
                "managed": True,
                "id": child,
                "launch_worktree": worktree,
                "managed_worktree_kind": "task",
                "managed_worktree_source": "/protected/source",
                "managed_worktree_branch": f"cao/task/{child}",
                "managed_worktree_commit": f"{index + 1:040x}",
            }
            assignment = ChildAssignmentModel(
                parent_terminal_id=parent,
                child_terminal_id=child,
                status="result_acknowledged",
                retirement_claim_token=f"claim-{child}",
                retirement_claimed_at=now,
                retirement_exit_dispatched_at=now,
                retirement_cleanup_intent=json.dumps(intent, sort_keys=True, separators=(",", ":")),
                retirement_cleanup_completed_at=None,
                retirement_completed_at=None,
                created_at=now,
                updated_at=now,
            )
            db.add(assignment)
            db.flush()
            db.add(
                DelegationResultModel(
                    id=f"result-{child}",
                    child_assignment_id=assignment.id,
                    schema_version=1,
                    delegation_kind="assign",
                    parent_terminal_id=parent,
                    child_terminal_id=child,
                    authorship="child_terminal_capture",
                    status="complete",
                    created_at=now,
                    finalized_at=now,
                    updated_at=now,
                )
            )
        db.commit()


def _seed_history(session_count: int, terminal_count: int) -> None:
    now = datetime(2026, 8, 21, 8, 0, 0)
    with database.SessionLocal() as db:
        for index in range(terminal_count):
            terminal_id = f"agent-{index:04d}"
            session_index = index % session_count
            running = index % 9 == 0
            db.add(
                TerminalModel(
                    id=terminal_id,
                    tmux_session=f"cao-session-{session_index:03d}",
                    session_id=f"lifetime-{session_index:03d}",
                    tmux_window=f"window-{index:04d}",
                    provider="codex",
                    agent_profile="reviewer" if index % 3 == 0 else "developer",
                    context_role="work",
                    launch_worktree=f"/synthetic/project-{index % 4}",
                    project_id=f"project-{index % 4}",
                    project_name=f"Project {index % 4}",
                    project_path=f"/synthetic/project-{index % 4}",
                    runtime_lifecycle="running" if running else "exited",
                    last_active=now - timedelta(seconds=index),
                )
            )
            if index % 5 == 0:
                workflow_status = ("open", "owner_gate", "terminal", "cancelled")[(index // 5) % 4]
                workflow = WorkflowModel(
                    root_terminal_id=terminal_id,
                    status=workflow_status,
                    created_at=now,
                    updated_at=now,
                )
                db.add(workflow)
                db.flush()
                if workflow_status == "open":
                    turn = WorkflowTurnModel(
                        workflow_id=workflow.id,
                        kind="continuation",
                        dedupe_key=f"turn-{index}",
                        state="queued",
                    )
                    db.add(turn)
                    db.flush()
                    if running:
                        db.add(
                            ProviderExecutionLeaseModel(
                                terminal_id=terminal_id,
                                workflow_turn_id=turn.id,
                            )
                        )
        db.commit()


def test_large_history_projection_is_one_bounded_query(monkeypatch):
    engine = _install_database(monkeypatch)
    _seed_history(session_count=100, terminal_count=1000)
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        page = database.list_terminal_ui_summary_page(limit=40)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert len(page["items"]) == 40
    assert page["total"] == 1000
    assert len(statements) == 1
    normalized = " ".join(statements).lower()
    assert "limit ? offset ?" in normalized
    assert "projected as materialized" in normalized
    assert "launch_snapshot_json" not in normalized
    assert "document_json" not in normalized


def test_history_sessions_survive_runtime_retirement(monkeypatch):
    _install_database(monkeypatch)
    _seed_history(session_count=25, terminal_count=180)

    overview = ui_read_model_service.get_overview()
    sessions = ui_read_model_service.list_session_summaries(limit=10)
    agents = ui_read_model_service.list_agent_summaries(limit=40)

    assert overview["sessions"] == 25
    assert overview["agents"] == 180
    assert sessions["total"] == 25
    assert len(sessions["items"]) == 10
    assert agents["total"] == 180
    assert len(agents["items"]) == 40
    assert any(item["status"] == "history" for item in sessions["items"])
    all_agents = ui_read_model_service.list_agent_summaries(limit=100)["items"]
    all_agents += ui_read_model_service.list_agent_summaries(limit=100, offset=100)["items"]
    assert overview["waiting"] == sum(
        item["activity"] in {"ready", "queued"} for item in all_agents
    )


def test_session_summary_search_uses_aggregated_session_columns(monkeypatch):
    _install_database(monkeypatch)
    _seed_history(session_count=180, terminal_count=180)

    by_name = ui_read_model_service.list_session_summaries(query="SESSION-007")
    by_id = ui_read_model_service.list_session_summaries(query="lifetime-007")
    by_project = ui_read_model_service.list_session_summaries(query="project 3")

    assert by_name["total"] == 1
    assert by_name["items"][0]["name"] == "cao-session-007"
    assert by_id["total"] == 1
    assert by_id["items"][0]["id"] == "lifetime-007"
    assert by_project["total"] > 1
    assert {item["project_name"] for item in by_project["items"]} == {"Project 3"}


def test_home_lifecycle_counts_and_filters_are_mutually_truthful(monkeypatch):
    _install_database(monkeypatch)
    now = datetime(2026, 8, 21, 8, 0, 0)
    with database.SessionLocal() as db:
        for terminal_id, workflow_status, lifecycle in (
            ("ready", "open", "running"),
            ("processing", "open", "running"),
            ("queued", "open", "running"),
            ("owner", "owner_gate", "running"),
            ("cancelled", "cancelled", "running"),
            ("completed", "terminal", "running"),
            ("exited", "open", "exited"),
        ):
            db.add(
                TerminalModel(
                    id=terminal_id,
                    tmux_session="cao-lifecycle",
                    session_id="lifetime-lifecycle",
                    tmux_window=terminal_id,
                    provider="codex",
                    runtime_lifecycle=lifecycle,
                    last_active=now,
                )
            )
            workflow = WorkflowModel(
                root_terminal_id=terminal_id,
                status=workflow_status,
                terminal_reason=("owner approval required" if terminal_id == "owner" else None),
                created_at=now,
                updated_at=now,
            )
            db.add(workflow)
            db.flush()
            if terminal_id in {"processing", "queued"}:
                turn = WorkflowTurnModel(
                    workflow_id=workflow.id,
                    kind="continuation",
                    dedupe_key=f"{terminal_id}-turn",
                    state="claimed" if terminal_id == "processing" else "queued",
                )
                db.add(turn)
                db.flush()
                workflow.active_turn_id = turn.id
                if terminal_id == "processing":
                    db.add(
                        ProviderExecutionLeaseModel(
                            terminal_id=terminal_id,
                            workflow_turn_id=turn.id,
                        )
                    )
        db.commit()

    overview = ui_read_model_service.get_overview()
    waiting = ui_read_model_service.list_agent_summaries(limit=20, home_filter="waiting")
    owner = ui_read_model_service.list_agent_summaries(limit=20, home_filter="owner_gate")
    cancelled = ui_read_model_service.list_agent_summaries(limit=20, home_filter="cancelled")
    completed = ui_read_model_service.list_agent_summaries(limit=20, home_filter="completed")
    session = ui_read_model_service.list_session_summaries(limit=10)["items"][0]

    assert overview["active"] == 6
    assert overview["waiting"] == 5
    assert overview["owner_gate"] == 1
    assert overview["cancelled"] == 1
    assert overview["completed"] == 1
    assert waiting["total"] == 5
    assert {item["id"] for item in waiting["items"]} == {
        "ready",
        "queued",
        "owner",
        "cancelled",
        "completed",
    }
    assert [item["id"] for item in owner["items"]] == ["owner"]
    assert [item["id"] for item in cancelled["items"]] == ["cancelled"]
    assert [item["id"] for item in completed["items"]] == ["completed"]
    assert session["activity_counts"] == {
        "exited": 1,
        "processing": 1,
        "queued": 1,
        "ready": 4,
    }
    assert overview["waiting"] == (
        session["activity_counts"]["ready"] + session["activity_counts"]["queued"]
    )
    assert session["workflow_counts"] == {
        "active": 4,
        "cancelled": 1,
        "completed": 1,
        "owner_gate": 1,
    }
    assert session["active_agent_count"] == 6


def test_open_workflow_projects_queued_composer_count(monkeypatch):
    _install_database(monkeypatch)
    now = datetime(2026, 9, 4, 6, 0, 0)
    with database.SessionLocal() as db:
        db.add(
            TerminalModel(
                id="queued-composer",
                tmux_session="cao-queued-composer",
                session_id="lifetime-queued-composer",
                tmux_window="conductor",
                provider="codex",
                runtime_lifecycle="running",
                last_active=now,
            )
        )
        workflow = WorkflowModel(
            root_terminal_id="queued-composer",
            status="open",
            created_at=now,
            updated_at=now,
        )
        db.add(workflow)
        db.flush()
        active = WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="execution_resume",
            dedupe_key="provider-reconnect-execution:1",
            state="sent",
            created_at=now,
            updated_at=now,
        )
        db.add(active)
        db.flush()
        active_turn_id = active.id
        workflow.active_turn_id = active.id
        db.add(
            WorkflowTurnReceiptModel(
                workflow_turn_id=active.id,
                receiver_terminal_id="queued-composer",
            )
        )
        for index in range(2):
            db.add(
                WorkflowTurnModel(
                    workflow_id=workflow.id,
                    kind="external_input",
                    dedupe_key=f"external_request:queued-{index}",
                    payload=f"queued payload {index}",
                    state="queued",
                    created_at=now + timedelta(seconds=index + 1),
                    updated_at=now + timedelta(seconds=index + 1),
                )
            )
        db.commit()

    item = ui_read_model_service.list_agent_summaries(limit=10)["items"][0]
    assert item["workflow_status"] == "open"
    assert item["queued_task_count"] == 2
    assert item["workflow_recovery_pending"] is True
    assert item["activity"] == "queued"
    assert item["execution_state"] == "waiting_workflow_continuation"

    # A future Composer item remains secondary while an exact provider
    # execution lease is active.
    with database.SessionLocal() as db:
        db.add(
            ProviderExecutionLeaseModel(
                terminal_id="queued-composer",
                workflow_turn_id=active_turn_id,
            )
        )
        db.commit()
    item = ui_read_model_service.list_agent_summaries(limit=10)["items"][0]
    assert item["activity"] == "processing"
    assert item["execution_state"] == "processing"
    assert item["queued_task_count"] == 2


def test_late_receipt_restores_same_physical_turn_and_keeps_future_queue_secondary(
    monkeypatch,
):
    _install_database(monkeypatch)
    now = datetime(2026, 9, 4, 16, 0, 0)
    with database.SessionLocal() as db:
        db.add(
            TerminalModel(
                id="receipt-race",
                tmux_session="cao-receipt-race",
                session_id="lifetime-receipt-race",
                tmux_window="owner",
                provider="codex",
                runtime_lifecycle="running",
                last_active=now,
            )
        )
        workflow = WorkflowModel(
            root_terminal_id="receipt-race",
            status="open",
            created_at=now,
            updated_at=now,
        )
        db.add(workflow)
        db.flush()
        active = WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="external_input",
            dedupe_key="external_request:active",
            payload="already delivered exact input",
            state="queued",
            queue_reason="PROVIDER_SETTLED_BEFORE_RECEIPT",
            created_at=now,
            updated_at=now,
        )
        db.add(active)
        db.flush()
        workflow.active_turn_id = active.id
        db.add(
            WorkflowTurnModel(
                workflow_id=workflow.id,
                kind="external_input",
                dedupe_key="external_request:future",
                payload="future Composer input",
                state="queued",
                queue_reason="WORKFLOW_CONTINUATION_PENDING",
                created_at=now + timedelta(seconds=1),
                updated_at=now + timedelta(seconds=1),
            )
        )
        active_id = active.id
        db.commit()

    assert database.claim_workflow_turn_receipt("receipt-race", active_id)
    assert not database.claim_workflow_turn_receipt("receipt-race", active_id)
    item = ui_read_model_service.list_agent_summaries(limit=10)["items"][0]
    assert item["activity"] == "processing"
    assert item["execution_state"] == "processing"
    assert item["workflow_state"] == "active"
    assert item["queued_task_count"] == 1
    with database.SessionLocal() as db:
        active = db.get(WorkflowTurnModel, active_id)
        assert active.state == "sent"
        assert active.queue_reason is None
        assert db.query(WorkflowTurnReceiptModel).count() == 1
        lease = db.get(ProviderExecutionLeaseModel, "receipt-race")
        assert lease is not None and lease.workflow_turn_id == active_id


def test_never_dispatched_queued_turn_cannot_fabricate_physical_receipt(monkeypatch):
    _install_database(monkeypatch)
    now = datetime(2026, 9, 4, 16, 5, 0)
    with database.SessionLocal() as db:
        db.add(
            TerminalModel(
                id="never-dispatched",
                tmux_session="cao-never-dispatched",
                session_id="lifetime-never-dispatched",
                tmux_window="owner",
                provider="codex",
                runtime_lifecycle="running",
                last_active=now,
            )
        )
        workflow = WorkflowModel(
            root_terminal_id="never-dispatched",
            status="open",
            created_at=now,
            updated_at=now,
        )
        db.add(workflow)
        db.flush()
        turn = WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="external_input",
            dedupe_key="external_request:waiting",
            payload="not yet delivered",
            state="queued",
            queue_reason="RESOURCE_HEALTH_REJECTED",
            created_at=now,
            updated_at=now,
        )
        db.add(turn)
        db.flush()
        workflow.active_turn_id = turn.id
        turn_id = turn.id
        db.commit()

    assert not database.claim_workflow_turn_receipt("never-dispatched", turn_id)
    with database.SessionLocal() as db:
        assert db.query(WorkflowTurnReceiptModel).count() == 0
        assert db.query(ProviderExecutionLeaseModel).count() == 0


def test_execution_wait_labels_and_owner_reason_are_exact_durable_mappings(monkeypatch):
    _install_database(monkeypatch)
    now = datetime(2026, 8, 21, 8, 0, 0)
    with database.SessionLocal() as db:
        db.add(
            CapacitySettingsModel(
                id=1,
                max_resident_supervisors=1,
                max_provider_executions=1,
                max_work_contexts=1,
                max_heavy_execution_slots=1,
            )
        )
        for terminal_id, operation in (
            ("capacity-holder", None),
            ("provider-slot", None),
            ("continuation", None),
            ("retirement", "retire"),
            ("owner", None),
        ):
            db.add(
                TerminalModel(
                    id=terminal_id,
                    tmux_session="cao-waits",
                    session_id="lifetime-waits",
                    tmux_window=terminal_id,
                    provider="codex",
                    runtime_lifecycle="running",
                    runtime_operation_kind=operation,
                    last_active=now,
                )
            )
            workflow = WorkflowModel(
                root_terminal_id=terminal_id,
                status="owner_gate" if terminal_id == "owner" else "open",
                terminal_reason=(
                    "provider reconnect recovery exhausted after 3 attempts"
                    if terminal_id == "owner"
                    else None
                ),
                created_at=now,
                updated_at=now,
            )
            db.add(workflow)
            db.flush()
            if terminal_id in {"provider-slot", "continuation"}:
                db.add(
                    WorkflowTurnModel(
                        workflow_id=workflow.id,
                        kind="continuation",
                        dedupe_key=f"{terminal_id}-turn",
                        state="queued",
                    )
                )
            if terminal_id == "capacity-holder":
                turn = WorkflowTurnModel(
                    workflow_id=workflow.id,
                    kind="continuation",
                    dedupe_key="holder-turn",
                    state="claimed",
                )
                db.add(turn)
                db.flush()
                db.add(
                    ProviderExecutionLeaseModel(
                        terminal_id=terminal_id,
                        workflow_turn_id=turn.id,
                    )
                )
        db.commit()

    # Capacity is full for the first snapshot.
    items = {
        item["id"]: item for item in ui_read_model_service.list_agent_summaries(limit=20)["items"]
    }
    assert items["provider-slot"]["execution_state"] == "queued_provider_execution"
    assert items["retirement"]["execution_state"] == "waiting_child_retirement"
    assert items["owner"]["workflow_reason"] == (
        "provider reconnect recovery exhausted after 3 attempts"
    )

    # Once the exact provider-capacity barrier clears, queued work is waiting
    # for workflow continuation, not a provider slot.
    with database.SessionLocal() as db:
        db.query(ProviderExecutionLeaseModel).delete()
        db.commit()
    items = {
        item["id"]: item for item in ui_read_model_service.list_agent_summaries(limit=20)["items"]
    }
    assert items["provider-slot"]["execution_state"] == "waiting_workflow_continuation"
    assert items["continuation"]["execution_state"] == "waiting_workflow_continuation"

    # A receipt is durable workflow authority, not physical execution truth.
    # Without an execution lease, the runtime is Ready while the workflow
    # remains independently Active.
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id="continuation").one()
        turn = db.query(WorkflowTurnModel).filter_by(workflow_id=workflow.id).one()
        turn.state = "sent"
        workflow.active_turn_id = turn.id
        db.add(
            WorkflowTurnReceiptModel(
                workflow_turn_id=turn.id,
                receiver_terminal_id="continuation",
            )
        )
        db.commit()
    items = {
        item["id"]: item for item in ui_read_model_service.list_agent_summaries(limit=20)["items"]
    }
    assert items["continuation"]["activity"] == "ready"
    assert items["continuation"]["execution_state"] == "ready"
    assert items["continuation"]["workflow_state"] == "active"

    # New semantic input can advance the receiver capability during the
    # narrow post-paste/pre-ack race. The old provider lease still means a
    # model invocation is active, while receipt/effect authority remains with
    # the newer turn.
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id="continuation").one()
        old_turn = db.get(WorkflowTurnModel, workflow.active_turn_id)
        assert old_turn is not None
        db.add(
            ProviderExecutionLeaseModel(
                terminal_id="continuation",
                workflow_turn_id=old_turn.id,
            )
        )
        newer = WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="inbox_message",
            dedupe_key="newer-during-old-execution",
            state="queued",
        )
        db.add(newer)
        db.flush()
        workflow.active_turn_id = newer.id
        db.commit()
    items = {
        item["id"]: item for item in ui_read_model_service.list_agent_summaries(limit=20)["items"]
    }
    assert items["continuation"]["activity"] == "processing"
    assert items["continuation"]["execution_state"] == "processing"
    assert database.get_terminal_execution_projection("continuation")["active_turn"] is True

    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id="continuation").one()
        old_turn = (
            db.query(WorkflowTurnModel)
            .filter_by(workflow_id=workflow.id, dedupe_key="continuation-turn")
            .one()
        )
        newer = (
            db.query(WorkflowTurnModel)
            .filter_by(workflow_id=workflow.id, dedupe_key="newer-during-old-execution")
            .one()
        )
        db.query(ProviderExecutionLeaseModel).filter_by(terminal_id="continuation").delete()
        workflow.active_turn_id = old_turn.id
        db.delete(newer)
        db.commit()

    # A physically sent turn that never reached SessionStart/model admission
    # is not Processing once its execution lease is gone. It remains an
    # explicit continuation wait until restart recovery retries or supersedes
    # that same logical turn.
    with database.SessionLocal() as db:
        db.query(WorkflowTurnReceiptModel).filter_by(receiver_terminal_id="continuation").delete()
        db.commit()
    items = {
        item["id"]: item for item in ui_read_model_service.list_agent_summaries(limit=20)["items"]
    }
    assert items["continuation"]["activity"] == "queued"
    assert items["continuation"]["execution_state"] == "waiting_workflow_continuation"

    # A stale-sidecar fence is itself durable continuation work. Even with a
    # receiver receipt, it is Queued once no exact execution lease exists;
    # reacquiring that same turn's lease makes it Processing again.
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id="continuation").one()
        turn = db.query(WorkflowTurnModel).filter_by(workflow_id=workflow.id).one()
        turn.provider_reconnect_requested_at = now
        db.add(
            WorkflowTurnReceiptModel(
                workflow_turn_id=turn.id,
                receiver_terminal_id="continuation",
            )
        )
        db.commit()
    items = {
        item["id"]: item for item in ui_read_model_service.list_agent_summaries(limit=20)["items"]
    }
    assert items["continuation"]["activity"] == "queued"
    assert items["continuation"]["execution_state"] == "waiting_workflow_continuation"
    assert items["continuation"]["workflow_recovery_pending"] is True

    with database.SessionLocal() as db:
        turn = (
            db.query(WorkflowTurnModel)
            .join(WorkflowModel, WorkflowTurnModel.workflow_id == WorkflowModel.id)
            .filter(WorkflowModel.root_terminal_id == "continuation")
            .one()
        )
        db.add(
            ProviderExecutionLeaseModel(
                terminal_id="continuation",
                workflow_turn_id=turn.id,
            )
        )
        db.commit()
    items = {
        item["id"]: item for item in ui_read_model_service.list_agent_summaries(limit=20)["items"]
    }
    assert items["continuation"]["activity"] == "processing"
    assert items["continuation"]["execution_state"] == "processing"


def test_completed_parent_does_not_wait_for_post_exit_resource_cleanup_after_restart(
    monkeypatch, tmp_path
):
    state_path = tmp_path / "retirement-projection.db"
    engine = _install_database(monkeypatch, f"sqlite:///{state_path}")
    parent = "completed-parent"
    children = (("exited-child-a", "exited"), ("exited-child-b", "exited"))
    _seed_retirement_projection(parent=parent, children=children)

    assert database.get_parent_completion_barrier(parent) == (0, 0)
    assert database.get_terminal_execution_projection(parent) == {
        "active_turn": False,
        "wait_reason": None,
    }
    before_restart = {
        item["id"]: item for item in ui_read_model_service.list_agent_summaries(limit=20)["items"]
    }
    assert before_restart[parent]["activity"] == "ready"
    assert before_restart[parent]["execution_state"] == "ready"
    assert before_restart[parent]["workflow_state"] == "completed"
    assert {
        item["child_terminal_id"] for item in database.list_pending_child_retirement_cleanups()
    } == {child for child, _lifecycle in children}

    # A same-state restart must deterministically rebuild the same operational
    # projection without consuming cleanup authority or historical evidence.
    engine.dispose()
    restarted_engine = _install_database(monkeypatch, f"sqlite:///{state_path}")
    try:
        for _attempt in range(2):
            assert database.get_terminal_execution_projection(parent)["wait_reason"] is None
            after_restart = {
                item["id"]: item
                for item in ui_read_model_service.list_agent_summaries(limit=20)["items"]
            }
            assert after_restart[parent]["execution_state"] == "ready"
            assert after_restart[parent]["workflow_state"] == "completed"
        with database.SessionLocal() as db:
            assert db.query(ChildAssignmentModel).filter_by(parent_terminal_id=parent).count() == 2
            assert db.query(DelegationResultModel).filter_by(parent_terminal_id=parent).count() == 2
            assert (
                db.query(ChildAssignmentModel)
                .filter(
                    ChildAssignmentModel.parent_terminal_id == parent,
                    ChildAssignmentModel.retirement_claim_token.is_not(None),
                    ChildAssignmentModel.retirement_cleanup_completed_at.is_(None),
                )
                .count()
                == 2
            )
            assert (
                db.query(TerminalModel)
                .filter(TerminalModel.id.in_([child for child, _lifecycle in children]))
                .count()
                == 2
            )
    finally:
        restarted_engine.dispose()


def test_real_child_retirement_dependency_clears_only_after_durable_exit(monkeypatch):
    _install_database(monkeypatch)
    parent, child = "waiting-parent", "running-child"
    _seed_retirement_projection(parent=parent, children=((child, "running"),))

    assert database.get_parent_completion_barrier(parent) == (0, 0)
    assert database.get_terminal_execution_projection(parent)["wait_reason"] == ("child_retirement")
    waiting = {
        item["id"]: item for item in ui_read_model_service.list_agent_summaries(limit=20)["items"]
    }
    assert waiting[parent]["execution_state"] == "waiting_child_retirement"
    assert waiting[parent]["workflow_state"] == "completed"

    # This is the late-exit race: the parent workflow is already terminal, but
    # runtime reconciliation now durably proves that the child no longer owns
    # execution. Resource cleanup remains fail-closed and independently retryable.
    with database.SessionLocal() as db:
        terminal = db.get(TerminalModel, child)
        assert terminal is not None
        terminal.runtime_lifecycle = "exited"
        terminal.runtime_exited_at = datetime(2026, 8, 25, 10, 1, 0)
        db.add(ProviderExecutionLeaseModel(terminal_id=child, workflow_turn_id=999_999))
        db.add(
            WorktreeWriterLeaseModel(
                canonical_worktree=f"/protected/history/{child}", terminal_id=child
            )
        )
        db.commit()

    # An exited label alone cannot override a still-live provider/writer
    # authority. Canonical runtime reconciliation releases both atomically.
    assert database.get_terminal_execution_projection(parent)["wait_reason"] == ("child_retirement")
    lease_blocked = {
        item["id"]: item for item in ui_read_model_service.list_agent_summaries(limit=20)["items"]
    }
    assert lease_blocked[parent]["execution_state"] == "waiting_child_retirement"
    with database.SessionLocal() as db:
        db.query(ProviderExecutionLeaseModel).filter_by(terminal_id=child).delete()
        db.query(WorktreeWriterLeaseModel).filter_by(terminal_id=child).delete()
        db.commit()

    for _attempt in range(2):
        assert database.get_terminal_execution_projection(parent)["wait_reason"] is None
        reconciled = {
            item["id"]: item
            for item in ui_read_model_service.list_agent_summaries(limit=20)["items"]
        }
        assert reconciled[parent]["execution_state"] == "ready"
        assert reconciled[parent]["workflow_state"] == "completed"
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        result = db.query(DelegationResultModel).filter_by(child_assignment_id=assignment.id).one()
        assert assignment.retirement_claim_token == f"claim-{child}"
        assert assignment.retirement_cleanup_completed_at is None
        assert result.status == "complete"
        assert db.get(TerminalModel, child) is not None


def test_session_lifetime_filter_never_coalesces_reused_tmux_name(monkeypatch):
    _install_database(monkeypatch)
    now = datetime(2026, 8, 21, 8, 0, 0)
    with database.SessionLocal() as db:
        for terminal_id, lifetime, lifecycle in (
            ("old", "lifetime-old", "exited"),
            ("new", "lifetime-new", "running"),
        ):
            db.add(
                TerminalModel(
                    id=terminal_id,
                    tmux_session="cao-reused",
                    session_id=lifetime,
                    tmux_window=terminal_id,
                    provider="codex",
                    runtime_lifecycle=lifecycle,
                    last_active=now,
                )
            )
        db.commit()

    sessions = ui_read_model_service.list_session_summaries(limit=10)
    old_agents = ui_read_model_service.list_agent_summaries(limit=10, session_id="lifetime-old")
    new_agents = ui_read_model_service.list_agent_summaries(limit=10, session_id="lifetime-new")

    assert sessions["total"] == 2
    assert {item["id"] for item in sessions["items"]} == {"lifetime-old", "lifetime-new"}
    assert [item["id"] for item in old_agents["items"]] == ["old"]
    assert [item["id"] for item in new_agents["items"]] == ["new"]


def test_projection_lazily_creates_session_receipt_table_for_older_schema(monkeypatch):
    engine = _install_database(monkeypatch)
    SessionDeletionReceiptModel.__table__.drop(bind=engine)
    with database.SessionLocal() as db:
        db.add(
            TerminalModel(
                id="legacy-projection",
                tmux_session="cao-legacy-projection",
                session_id=None,
                tmux_window="legacy-projection",
                provider="codex",
                runtime_lifecycle="exited",
            )
        )
        db.commit()

    agents = ui_read_model_service.list_agent_summaries(limit=10)

    assert [item["id"] for item in agents["items"]] == ["legacy-projection"]
    assert inspect(engine).has_table("session_deletion_receipts")


def test_real_terminal_creation_assigns_durable_append_order(monkeypatch):
    _install_database(monkeypatch)
    lifetime = "lifetime-created-order"
    session_name = "cao-created-order"
    for terminal_id, provider, profile in (
        ("z-first", "codex", "developer_terra_medium"),
        ("a-second", "claude_code", "developer_terra_high"),
        ("m-third", "codex", "developer_sol_medium"),
    ):
        database.create_terminal(
            terminal_id,
            session_name,
            terminal_id,
            provider,
            agent_profile=profile,
            session_lifetime_id=lifetime,
        )
    now = datetime(2026, 8, 25, 12, 0, 0)
    with database.SessionLocal() as db:
        rows = {row.id: row for row in db.query(TerminalModel).all()}
        rows["z-first"].runtime_lifecycle = "exited"
        rows["z-first"].last_active = now + timedelta(days=3)
        rows["a-second"].runtime_lifecycle = "running"
        rows["a-second"].last_active = now + timedelta(hours=7)
        rows["m-third"].runtime_lifecycle = "exited"
        rows["m-third"].last_active = now
        db.commit()

    first_page = ui_read_model_service.list_agent_summaries(limit=2, session_id=lifetime)
    second_page = ui_read_model_service.list_agent_summaries(limit=2, offset=2, session_id=lifetime)
    assert [item["id"] for item in first_page["items"]] == ["z-first", "a-second"]
    assert [item["id"] for item in second_page["items"]] == ["m-third"]

    database.create_terminal(
        "b-fourth",
        session_name,
        "b-fourth",
        "claude_code",
        agent_profile="developer_terra_medium",
        session_lifetime_id=lifetime,
    )
    with database.SessionLocal() as db:
        fourth = db.get(TerminalModel, "b-fourth")
        fourth.runtime_lifecycle = "running"
        fourth.last_active = now - timedelta(days=2)
        db.commit()

    refreshed = ui_read_model_service.list_agent_summaries(limit=10, session_id=lifetime)
    assert [item["id"] for item in refreshed["items"]] == [
        "z-first",
        "a-second",
        "m-third",
        "b-fourth",
    ]


def test_session_agents_and_boundaries_use_durable_creation_order(monkeypatch):
    _install_database(monkeypatch)
    now = datetime(2026, 8, 21, 8, 0, 0)
    with database.SessionLocal() as db:
        for terminal_id, creation_order, last_active, lifecycle in (
            ("z-created-first", 10, now, "exited"),
            ("a-created-second", 20, now + timedelta(hours=5), "running"),
            ("m-created-last", 30, now + timedelta(hours=1), "exited"),
        ):
            db.add(
                TerminalModel(
                    id=terminal_id,
                    tmux_session="cao-natural-order",
                    session_id="lifetime-natural-order",
                    tmux_window=terminal_id,
                    provider="codex",
                    runtime_lifecycle=lifecycle,
                    creation_order=creation_order,
                    last_active=last_active,
                )
            )
        db.commit()

    first_page = ui_read_model_service.list_agent_summaries(
        limit=2, session_id="lifetime-natural-order"
    )
    second_page = ui_read_model_service.list_agent_summaries(
        limit=2, offset=2, session_id="lifetime-natural-order"
    )
    sessions = ui_read_model_service.list_session_summaries(limit=10)

    assert [item["id"] for item in first_page["items"]] == [
        "z-created-first",
        "a-created-second",
    ]
    assert [item["id"] for item in second_page["items"]] == ["m-created-last"]
    summary = sessions["items"][0]
    assert summary["first_agent"]["id"] == "z-created-first"
    assert summary["first_agent"]["lifecycle"] == "exited"
    assert summary["last_agent"]["id"] == "m-created-last"
    assert summary["last_agent"]["lifecycle"] == "exited"
    assert summary["activity_counts"] == {"exited": 2, "ready": 1}

    with database.SessionLocal() as db:
        first = db.query(TerminalModel).filter_by(id="z-created-first").one()
        first.last_active = now + timedelta(days=2)
        db.commit()

    refreshed = ui_read_model_service.list_agent_summaries(
        limit=10, session_id="lifetime-natural-order"
    )
    refreshed_session = ui_read_model_service.list_session_summaries(limit=10)["items"][0]
    assert [item["id"] for item in refreshed["items"]] == [
        "z-created-first",
        "a-created-second",
        "m-created-last",
    ]
    assert refreshed_session["first_agent"]["id"] == "z-created-first"
    assert refreshed_session["last_agent"]["id"] == "m-created-last"

    with database.SessionLocal() as db:
        db.add(
            TerminalModel(
                id="b-created-fourth",
                tmux_session="cao-natural-order",
                session_id="lifetime-natural-order",
                tmux_window="fourth",
                provider="claude_code",
                agent_profile="developer_terra_high",
                runtime_lifecycle="running",
                creation_order=40,
                last_active=now - timedelta(days=1),
            )
        )
        db.commit()

    appended = ui_read_model_service.list_agent_summaries(
        limit=10, session_id="lifetime-natural-order"
    )
    assert [item["id"] for item in appended["items"]] == [
        "z-created-first",
        "a-created-second",
        "m-created-last",
        "b-created-fourth",
    ]


def test_session_summary_keeps_known_recovery_lifecycle_separate_from_workflow(monkeypatch):
    _install_database(monkeypatch)
    now = datetime(2026, 9, 2, 12, 0, 0)
    with database.SessionLocal() as db:
        for terminal_id, creation_order, lifecycle in (
            ("recovery-predecessor", 1, "recovery_fenced"),
            ("historical-peer", 2, "exited"),
        ):
            db.add(
                TerminalModel(
                    id=terminal_id,
                    tmux_session="cao-recovery-history",
                    session_id="lifetime-recovery-history",
                    tmux_window=terminal_id,
                    provider="codex",
                    runtime_lifecycle=lifecycle,
                    creation_order=creation_order,
                    last_active=now + timedelta(seconds=creation_order),
                )
            )
        db.commit()

    summary = ui_read_model_service.list_session_summaries(limit=10)["items"][0]

    assert summary["status"] == "history"
    assert summary["active_agent_count"] == 0
    assert summary["activity_counts"] == {"exited": 1, "recovery_fenced": 1}
    assert summary["workflow_counts"] == {"untracked": 2}
    assert summary["first_agent"]["lifecycle"] == "recovery_fenced"
    assert summary["first_agent"]["activity"] == "recovery_fenced"
    assert summary["first_agent"]["workflow_state"] is None


def test_recovery_required_projects_known_runtime_wait_instead_of_ready(monkeypatch):
    _install_database(monkeypatch)
    now = datetime(2026, 9, 2, 18, 30, 0)
    with database.SessionLocal() as db:
        db.add(
            TerminalModel(
                id="recovery-required",
                tmux_session="cao-recovery-required",
                session_id="lifetime-recovery-required",
                tmux_window="recovery-required",
                provider="codex",
                context_role="supervisor",
                runtime_lifecycle="recovery_required",
                creation_order=1,
                last_active=now,
            )
        )
        db.commit()

    agent = ui_read_model_service.list_agent_summaries(limit=10)["items"][0]
    session = ui_read_model_service.list_session_summaries(limit=10)["items"][0]

    assert agent["lifecycle"] == "recovery_required"
    assert agent["activity"] == "queued"
    assert agent["execution_state"] == "waiting_runtime_recovery"
    assert session["activity_counts"] == {"queued": 1}
    assert session["workflow_counts"] == {"untracked": 1}


def test_provider_content_unavailable_projects_recoverable_without_processing(monkeypatch):
    _install_database(monkeypatch)
    now = datetime(2026, 8, 28, 12, 0, 0)
    with database.SessionLocal() as db:
        db.add(
            TerminalModel(
                id="provider-policy",
                tmux_session="cao-provider-policy",
                session_id="lifetime-provider-policy",
                tmux_window="provider-policy",
                provider="codex",
                runtime_lifecycle="running",
                last_active=now,
            )
        )
        workflow = WorkflowModel(
            root_terminal_id="provider-policy",
            status="open",
            created_at=now,
            updated_at=now,
        )
        db.add(workflow)
        db.flush()
        turn = WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="external_input",
            dedupe_key="provider-policy-turn",
            state="finished",
            provider_outcome_code="PROVIDER_CONTENT_UNAVAILABLE",
            provider_outcome_detail="cyber_policy",
            provider_outcome_observed_at=now,
        )
        db.add(turn)
        db.flush()
        workflow.active_turn_id = turn.id
        db.commit()

    item = ui_read_model_service.list_agent_summaries(limit=10)["items"][0]

    assert item["activity"] == "ready"
    assert item["execution_state"] == "ready"
    assert item["workflow_state"] == "recoverable"
    assert item["provider_outcome_code"] == "PROVIDER_CONTENT_UNAVAILABLE"
    assert item["provider_outcome_detail"] == "cyber_policy"


def test_recovery_fenced_supervisor_is_historical_while_successor_is_active(monkeypatch):
    _install_database(monkeypatch)
    now = datetime(2026, 9, 1, 12, 0, 0)
    with database.SessionLocal() as db:
        db.add_all(
            [
                TerminalModel(
                    id="old-owner",
                    tmux_session="cao-old-owner",
                    session_id="lifetime-old-owner",
                    tmux_window="old-owner",
                    provider="codex",
                    context_role="supervisor",
                    project_id="project-1",
                    runtime_lifecycle="recovery_fenced",
                    recovery_fenced_at=now,
                    replaced_by_terminal_id="new-owner",
                    last_active=now,
                ),
                TerminalModel(
                    id="new-owner",
                    tmux_session="cao-new-owner",
                    session_id="lifetime-new-owner",
                    tmux_window="new-owner",
                    provider="codex",
                    context_role="supervisor",
                    project_id="project-1",
                    runtime_lifecycle="running",
                    last_active=now,
                ),
            ]
        )
        db.commit()

    agents = {
        item["id"]: item for item in ui_read_model_service.list_agent_summaries(limit=10)["items"]
    }
    sessions = {
        item["id"]: item for item in ui_read_model_service.list_session_summaries(limit=10)["items"]
    }

    assert agents["old-owner"]["activity"] == "recovery_fenced"
    assert agents["old-owner"]["execution_state"] == "recovery_fenced"
    assert agents["new-owner"]["activity"] == "ready"
    assert sessions["lifetime-old-owner"]["active_agent_count"] == 0
    assert sessions["lifetime-new-owner"]["active_agent_count"] == 1


def test_interaction_projection_preserves_current_and_history_axes(monkeypatch):
    _install_database(monkeypatch)
    now = datetime(2026, 9, 6, 9, 0, 0)
    with database.SessionLocal() as db:
        db.add_all(
            [
                _interaction_terminal(),
                _interaction_terminal("child"),
            ]
        )
        open_workflow = WorkflowModel(
            root_terminal_id="owner", status="open", created_at=now, updated_at=now
        )
        terminal_workflow = WorkflowModel(
            root_terminal_id="owner",
            status="terminal",
            terminal_reason="complete",
            created_at=now + timedelta(seconds=1),
            updated_at=now + timedelta(seconds=1),
        )
        cancelled_workflow = WorkflowModel(
            root_terminal_id="owner",
            status="cancelled",
            created_at=now + timedelta(seconds=2),
            updated_at=now + timedelta(seconds=2),
        )
        superseded_workflow = WorkflowModel(
            root_terminal_id="owner",
            status="open",
            created_at=now + timedelta(seconds=3),
            updated_at=now + timedelta(seconds=3),
        )
        owner_gate_workflow = WorkflowModel(
            root_terminal_id="owner",
            status="owner_gate",
            terminal_reason="owner decision",
            created_at=now + timedelta(seconds=4),
            updated_at=now + timedelta(seconds=4),
        )
        effect_workflow = WorkflowModel(
            root_terminal_id="owner",
            status="terminal",
            created_at=now + timedelta(seconds=5),
            updated_at=now + timedelta(seconds=5),
        )
        db.add_all(
            [
                open_workflow,
                terminal_workflow,
                cancelled_workflow,
                superseded_workflow,
                owner_gate_workflow,
                effect_workflow,
            ]
        )
        db.flush()

        turns = []
        for index, (workflow, state, superseded) in enumerate(
            (
                (open_workflow, "queued", False),
                (terminal_workflow, "sent", False),
                (cancelled_workflow, "queued", False),
                (superseded_workflow, "queued", True),
                (effect_workflow, "queued", False),
            )
        ):
            turn = WorkflowTurnModel(
                workflow_id=workflow.id,
                kind="external_input",
                dedupe_key=f"interaction-{index}",
                payload=f"durable input {index}",
                state=state,
                superseded_by_turn_id=999 if superseded else None,
                superseded_at=now if superseded else None,
                created_at=now + timedelta(seconds=10 + index),
                updated_at=now + timedelta(seconds=10 + index),
            )
            db.add(turn)
            db.flush()
            turns.append(turn)
        open_workflow.active_turn_id = turns[0].id
        db.add(
            WorkflowEffectModel(
                workflow_id=effect_workflow.id,
                workflow_turn_id=turns[-1].id,
                effect_kind="handoff",
                effect_key="effect-current",
                state="claimed",
                claim_token="claim",
                created_at=now + timedelta(seconds=20),
                updated_at=now + timedelta(seconds=20),
            )
        )

        active_assignment = ChildAssignmentModel(
            parent_terminal_id="owner",
            child_terminal_id="child",
            status="result_delivered",
            request_workflow_id=open_workflow.id,
            request_workflow_turn_id=turns[0].id,
            created_at=now + timedelta(seconds=21),
            updated_at=now + timedelta(seconds=21),
        )
        acknowledged_assignment = ChildAssignmentModel(
            parent_terminal_id="owner",
            child_terminal_id="child",
            status="result_acknowledged",
            created_at=now + timedelta(seconds=22),
            updated_at=now + timedelta(seconds=22),
        )
        cancelled_assignment = ChildAssignmentModel(
            parent_terminal_id="owner",
            child_terminal_id="child",
            status="cancelled",
            created_at=now + timedelta(seconds=23),
            updated_at=now + timedelta(seconds=23),
        )
        db.add_all([active_assignment, acknowledged_assignment, cancelled_assignment])
        db.flush()
        for assignment, result_id in (
            (active_assignment, "result-current"),
            (acknowledged_assignment, "result-history"),
        ):
            db.add(
                DelegationResultModel(
                    id=result_id,
                    child_assignment_id=assignment.id,
                    schema_version=1,
                    delegation_kind="assign",
                    parent_terminal_id="owner",
                    child_terminal_id="child",
                    authorship="child_submission",
                    status="complete",
                    document_json=json.dumps(
                        {"format": "v1", "summary": result_id, "body_markdown": "done"}
                    ),
                    created_at=assignment.created_at,
                    finalized_at=assignment.created_at,
                    updated_at=assignment.updated_at,
                )
            )
        db.add_all(
            [
                InboxModel(
                    sender_id="ui",
                    receiver_id="owner",
                    message="pending input",
                    status="pending",
                    kind="message",
                    created_at=now + timedelta(seconds=24),
                ),
                InboxModel(
                    sender_id="ui",
                    receiver_id="owner",
                    message="delivered input",
                    status="delivered",
                    kind="message",
                    created_at=now + timedelta(seconds=25),
                ),
            ]
        )
        db.commit()

    current = interaction_read_model_service.list_interactions(
        "interaction-session", mode="current", limit=20
    )
    history = interaction_read_model_service.list_interactions(
        "interaction-session", mode="history", limit=20
    )
    current_by_type = {}
    for item in current["items"]:
        current_by_type.setdefault(item["interaction_type"], []).append(item)

    assert current["total"] == 6
    assert len(current_by_type["workflow_turn"]) == 1
    assert current_by_type["workflow_turn"][0]["queue"]["wait_reason"] == ("workflow_continuation")
    assert current_by_type["effect"][0]["queue"]["wait_reason"] == "claimed_effect"
    assert current_by_type["effect"][0]["workflow"]["status"] == "terminal"
    assert {item["queue"]["wait_reason"] for item in current_by_type["workflow"]} == {
        "owner_gate",
        "workflow_continuation",
    }
    assert not any(
        item["final_disposition"] == "superseded" for item in current_by_type["workflow_turn"]
    )
    assert current_by_type["delegation"][0]["result"] == {
        "id": "result-current",
        "status": "complete",
        "summary": "result-current",
        "available": True,
    }
    assert current_by_type["delegation"][0]["delivery"]["pending"] is True
    assert current_by_type["inbox"][0]["queue"]["admission_pending"] is True
    assert (
        current["total"]
        == interaction_read_model_service.list_session_current_queue_counts(
            ["interaction-session"]
        )["interaction-session"]
    )

    history_dispositions = {item["final_disposition"] for item in history["items"]}
    assert {"completed", "cancelled", "superseded", "acknowledged", "delivered"}.issubset(
        history_dispositions
    )
    assert all(not item["current"] for item in history["items"])
    assert not any(
        item["workflow"]["status"] in {"terminal", "cancelled"}
        and item["workflow"]["effect_state"] not in {"claimed", "indeterminate"}
        for item in current["items"]
    )

    session = ui_read_model_service.list_session_summaries(limit=10)["items"][0]
    owner = ui_read_model_service.list_agent_summaries(limit=10, session_id="interaction-session")[
        "items"
    ][0]
    assert session["current_queue_count"] == current["total"]
    assert owner["activity"] == "ready"


def test_open_workflow_projects_consumed_turns_as_history_and_only_unresolved_as_current(
    monkeypatch,
):
    _install_database(monkeypatch)
    now = datetime(2026, 9, 7, 8, 0, 0)
    with database.SessionLocal() as db:
        db.add_all([_interaction_terminal(), _interaction_terminal("reviewer")])
        workflow = WorkflowModel(
            root_terminal_id="owner", status="open", created_at=now, updated_at=now
        )
        db.add(workflow)
        db.flush()

        processed_operator = WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="external_input",
            dedupe_key="processed-operator",
            payload="Already processed operator input",
            state="sent",
            provider_processing_observed_at=now + timedelta(seconds=1),
            created_at=now,
            updated_at=now + timedelta(seconds=1),
        )
        db.add(processed_operator)
        db.flush()
        db.add(
            WorkflowTurnReceiptModel(
                workflow_turn_id=processed_operator.id,
                receiver_terminal_id="owner",
                consumed_at=now + timedelta(seconds=1),
            )
        )
        db.add(
            WorkflowEffectModel(
                workflow_id=workflow.id,
                workflow_turn_id=processed_operator.id,
                effect_kind="assign",
                effect_key="processed-effect",
                state="completed",
                claim_token="processed-effect-token",
                created_at=now + timedelta(seconds=1),
                updated_at=now + timedelta(seconds=2),
            )
        )

        callback_specs = (
            (
                "superseded-result",
                "result-processed-superseded",
                "result_acknowledged",
                now + timedelta(seconds=5),
            ),
            (
                "acknowledged-result",
                "result-processed-acknowledged",
                "result_acknowledged",
                None,
            ),
        )
        processed_callbacks = []
        for offset, (dedupe_key, result_id, assignment_status, superseded_at) in enumerate(
            callback_specs, start=3
        ):
            inbox = InboxModel(
                sender_id="reviewer",
                receiver_id="owner",
                message=f"Canonical result {result_id}",
                status="delivered",
                result_id=result_id,
                kind="delegation_result_notice",
                superseded_at=superseded_at,
                created_at=now + timedelta(seconds=offset),
            )
            db.add(inbox)
            db.flush()
            callback = WorkflowTurnModel(
                workflow_id=workflow.id,
                kind="assigned_result",
                dedupe_key=dedupe_key,
                payload=inbox.message,
                state="sent",
                inbox_message_id=inbox.id,
                provider_processing_observed_at=now + timedelta(seconds=offset + 1),
                created_at=now + timedelta(seconds=offset),
                updated_at=now + timedelta(seconds=offset + 1),
            )
            db.add(callback)
            db.flush()
            db.add(
                WorkflowTurnReceiptModel(
                    workflow_turn_id=callback.id,
                    receiver_terminal_id="owner",
                    consumed_at=now + timedelta(seconds=offset + 1),
                )
            )
            assignment = ChildAssignmentModel(
                parent_terminal_id="owner",
                child_terminal_id="reviewer",
                status=assignment_status,
                result_message_id=inbox.id,
                request_workflow_id=workflow.id,
                request_workflow_turn_id=processed_operator.id,
                review_superseded_at=superseded_at,
                created_at=now + timedelta(seconds=offset - 2),
                updated_at=now + timedelta(seconds=offset + 1),
            )
            db.add(assignment)
            db.flush()
            db.add(
                DelegationResultModel(
                    id=result_id,
                    child_assignment_id=assignment.id,
                    schema_version=1,
                    delegation_kind="assign",
                    parent_terminal_id="owner",
                    child_terminal_id="reviewer",
                    authorship="child_submission",
                    status="complete",
                    document_json=json.dumps(
                        {
                            "format": "v1",
                            "summary": f"Reviewed {result_id}",
                            "body_markdown": "Durable canonical result",
                        }
                    ),
                    created_at=assignment.created_at,
                    finalized_at=assignment.updated_at,
                    updated_at=assignment.updated_at,
                )
            )
            processed_callbacks.append(callback)

        unresolved = WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="external_input",
            dedupe_key="genuinely-unresolved",
            payload="Not admitted yet",
            state="sent",
            created_at=now + timedelta(seconds=10),
            updated_at=now + timedelta(seconds=10),
        )
        db.add(unresolved)
        db.flush()
        workflow.active_turn_id = unresolved.id
        processed_turn_ids = [processed_operator.id, *(turn.id for turn in processed_callbacks)]
        processed_operator_id = processed_operator.id
        unresolved_id = unresolved.id
        db.commit()

    current = interaction_read_model_service.list_interactions(
        "interaction-session", mode="current", limit=20
    )
    current_ids = [item["workflow"]["turn_id"] for item in current["items"]]
    assert current["total"] == 1
    assert current_ids == [unresolved_id]
    assert current["items"][0]["queue"] == {
        "state": "sent",
        "wait_reason": "admission",
        "admission_pending": True,
    }
    assert interaction_read_model_service.list_session_current_queue_counts(
        ["interaction-session"]
    ) == {"interaction-session": 1}
    assert (
        ui_read_model_service.list_session_summaries(limit=10)["items"][0]["current_queue_count"]
        == 1
    )

    history = interaction_read_model_service.list_interactions(
        "interaction-session", mode="history", limit=20
    )
    history_by_turn = {
        item["workflow"]["turn_id"]: item
        for item in history["items"]
        if item["interaction_type"] == "workflow_turn"
    }
    for turn_id in processed_turn_ids:
        assert history_by_turn[turn_id]["final_disposition"] == "processed"
        assert history_by_turn[turn_id]["queue"]["wait_reason"] is None
        assert history_by_turn[turn_id]["queue"]["admission_pending"] is False
    processed_operator_item = history_by_turn[processed_operator_id]
    assert processed_operator_item["workflow"]["effect_state"] == "completed"
    assert not any(item["interaction_type"] == "effect" for item in current["items"])
    assert {
        item["final_disposition"]
        for item in history["items"]
        if item["interaction_type"] == "delegation"
    } == {"superseded", "acknowledged"}

    with database.SessionLocal() as db:
        unresolved_row = db.get(WorkflowTurnModel, unresolved_id)
        unresolved_row.provider_processing_observed_at = now + timedelta(seconds=11)
        db.add(
            WorkflowTurnReceiptModel(
                workflow_turn_id=unresolved_id,
                receiver_terminal_id="owner",
                consumed_at=now + timedelta(seconds=11),
            )
        )
        db.commit()

    current = interaction_read_model_service.list_interactions(
        "interaction-session", mode="current", limit=20
    )
    assert current["total"] == 1
    assert current["items"][0]["interaction_type"] == "workflow"
    assert current["items"][0]["queue"]["wait_reason"] == "workflow_continuation"
    owner = ui_read_model_service.list_agent_summaries(limit=10, session_id="interaction-session")[
        "items"
    ][0]
    assert owner["activity"] == "ready"
    assert owner["lifecycle"] == "running"
    assert owner["workflow_state"] == "active"

    with database.SessionLocal() as db:
        unresolved_row = db.get(WorkflowTurnModel, unresolved_id)
        unresolved_row.provider_reconnect_requested_at = now + timedelta(seconds=12)
        db.commit()

    current = interaction_read_model_service.list_interactions(
        "interaction-session", mode="current", limit=20
    )
    assert current["total"] == 1
    assert current["items"][0]["workflow"]["turn_id"] == unresolved_id
    assert current["items"][0]["queue"]["wait_reason"] == "reconnect"

    with database.SessionLocal() as db:
        unresolved_row = db.get(WorkflowTurnModel, unresolved_id)
        unresolved_row.provider_reconnect_requested_at = None
        db.add(
            ProviderExecutionLeaseModel(
                terminal_id="owner",
                workflow_turn_id=unresolved_id,
                acquired_at=now + timedelta(seconds=13),
            )
        )
        db.commit()

    current = interaction_read_model_service.list_interactions(
        "interaction-session", mode="current", limit=20
    )
    assert current["total"] == 1
    assert current["items"][0]["workflow"]["turn_id"] == unresolved_id
    assert current["items"][0]["queue"]["wait_reason"] == "current_provider_turn"
    owner = ui_read_model_service.list_agent_summaries(limit=10, session_id="interaction-session")[
        "items"
    ][0]
    assert owner["activity"] == "processing"
    assert owner["lifecycle"] == "running"
    assert owner["workflow_state"] == "active"

    with database.SessionLocal() as db:
        db.query(ProviderExecutionLeaseModel).delete()
        db.commit()

    current = interaction_read_model_service.list_interactions(
        "interaction-session", mode="current", limit=20
    )
    assert current["total"] == 1
    assert current["items"][0]["interaction_type"] == "workflow"
    assert not any(item["task_type"] == "provider_execution" for item in current["items"])
    assert interaction_read_model_service.list_session_current_queue_counts(
        ["interaction-session"]
    ) == {"interaction-session": 1}
    owner = ui_read_model_service.list_agent_summaries(limit=10, session_id="interaction-session")[
        "items"
    ][0]
    assert owner["activity"] == "ready"
    assert owner["lifecycle"] == "running"

    with database.SessionLocal() as db:
        workflow_row = db.query(WorkflowModel).one()
        workflow_row.status = "terminal"
        workflow_row.terminal_reason = "completed"
        db.commit()
    current = interaction_read_model_service.list_interactions(
        "interaction-session", mode="current", limit=20
    )
    assert current["total"] == 0
    assert interaction_read_model_service.list_session_current_queue_counts(
        ["interaction-session"]
    ) == {"interaction-session": 0}


def test_wait_timeout_is_history_while_open_workflow_and_provider_axes_stay_independent(
    monkeypatch,
):
    _install_database(monkeypatch)
    now = datetime(2026, 9, 7, 12, 0, 0)
    with database.SessionLocal() as db:
        db.add(_interaction_terminal())
        workflows = [
            WorkflowModel(root_terminal_id="owner", status=status, created_at=now, updated_at=now)
            for status in ("open", "terminal")
        ]
        db.add_all(workflows)
        db.flush()
        turns = []
        for index, workflow in enumerate(workflows):
            turn = WorkflowTurnModel(
                workflow_id=workflow.id,
                kind="external_input",
                dedupe_key=f"await-timeout-{index}",
                state="sent",
                provider_processing_observed_at=now,
                created_at=now,
                updated_at=now,
            )
            db.add(turn)
            db.flush()
            db.add(
                WorkflowTurnReceiptModel(
                    workflow_turn_id=turn.id,
                    receiver_terminal_id="owner",
                    consumed_at=now,
                )
            )
            db.add(
                WorkflowEffectModel(
                    workflow_id=workflow.id,
                    workflow_turn_id=turn.id,
                    effect_kind="await_handoff",
                    effect_key=f"wait-slice-{index}",
                    state="wait_timeout",
                    claim_token=f"claim-{index}",
                    created_at=now,
                    updated_at=now + timedelta(seconds=30),
                )
            )
            turns.append(turn)
        db.add(
            WorkflowEffectModel(
                workflow_id=workflows[0].id,
                workflow_turn_id=turns[0].id,
                effect_kind="await_handoff",
                effect_key="wait-slice-open-completed",
                state="completed",
                claim_token="claim-open-completed",
                created_at=now + timedelta(seconds=31),
                updated_at=now + timedelta(seconds=32),
            )
        )
        db.add(
            WorkflowEffectModel(
                workflow_id=workflows[0].id,
                workflow_turn_id=turns[0].id,
                effect_kind="handoff",
                effect_key="initial-handoff-known-timeout",
                state="wait_timeout",
                claim_token="claim-initial-handoff",
                created_at=now + timedelta(seconds=33),
                updated_at=now + timedelta(seconds=34),
            )
        )
        workflows[0].active_turn_id = turns[0].id
        open_turn_id, terminal_turn_id = (turn.id for turn in turns)
        db.commit()

    current = interaction_read_model_service.list_interactions(
        "interaction-session", mode="current", limit=20
    )
    assert current["total"] == 1
    assert current["items"][0]["interaction_type"] == "workflow"
    assert current["items"][0]["queue"]["wait_reason"] == "workflow_continuation"
    assert not any(item["workflow"]["effect_state"] == "wait_timeout" for item in current["items"])
    assert interaction_read_model_service.list_session_current_queue_counts(
        ["interaction-session"]
    ) == {"interaction-session": 1}

    history = interaction_read_model_service.list_interactions(
        "interaction-session", mode="history", limit=20
    )
    history_by_turn = {
        item["workflow"]["turn_id"]: item
        for item in history["items"]
        if item["interaction_type"] == "workflow_turn"
    }
    assert history_by_turn[open_turn_id]["final_disposition"] == "processed"
    assert history_by_turn[open_turn_id]["workflow"]["effect_kind"] == "handoff"
    assert history_by_turn[open_turn_id]["workflow"]["effect_state"] == "wait_timeout"
    assert history_by_turn[terminal_turn_id]["final_disposition"] == "completed"
    known_waits = [
        item
        for item in history["items"]
        if item["interaction_type"] == "effect"
        and item["workflow"]["effect_kind"] == "await_handoff"
    ]
    assert len(known_waits) == 3
    assert len({item["id"] for item in known_waits}) == 3
    assert sorted(item["workflow"]["effect_state"] for item in known_waits) == [
        "completed",
        "wait_timeout",
        "wait_timeout",
    ]
    assert sorted(item["final_disposition"] for item in known_waits) == [
        "completed",
        "wait_slice_expired",
        "wait_slice_expired",
    ]
    initial_handoff_waits = [
        item
        for item in history["items"]
        if item["interaction_type"] == "effect" and item["workflow"]["effect_kind"] == "handoff"
    ]
    assert len(initial_handoff_waits) == 1
    assert initial_handoff_waits[0]["final_disposition"] == "wait_slice_expired"

    with database.SessionLocal() as db:
        db.add(
            ProviderExecutionLeaseModel(
                terminal_id="owner", workflow_turn_id=open_turn_id, acquired_at=now
            )
        )
        db.commit()
    executing = interaction_read_model_service.list_interactions(
        "interaction-session", mode="current", limit=20
    )
    assert executing["total"] == 1
    assert executing["items"][0]["queue"]["wait_reason"] == "current_provider_turn"
    assert not any(
        item["workflow"]["effect_state"] == "wait_timeout" for item in executing["items"]
    )


def test_wait_history_collapses_only_explicit_reconnect_mirrors(monkeypatch):
    _install_database(monkeypatch)
    now = datetime(2026, 9, 7, 12, 30, 0)
    with database.SessionLocal() as db:
        db.add(_interaction_terminal())
        workflow = WorkflowModel(
            root_terminal_id="owner",
            status="terminal",
            terminal_reason="completed",
            created_at=now,
            updated_at=now,
        )
        db.add(workflow)
        db.flush()
        turns = []
        for index in range(3):
            turn = WorkflowTurnModel(
                workflow_id=workflow.id,
                kind="external_input" if index != 1 else "execution_resume",
                dedupe_key=f"wait-operation-{index}",
                state="finished",
                created_at=now + timedelta(seconds=index),
                updated_at=now + timedelta(seconds=index),
            )
            db.add(turn)
            db.flush()
            turns.append(turn)
        first = WorkflowEffectModel(
            workflow_id=workflow.id,
            workflow_turn_id=turns[0].id,
            effect_kind="await_handoff",
            effect_key="same-child-slice-zero",
            state="wait_timeout",
            claim_token="first",
            created_at=now,
            updated_at=now,
        )
        db.add(first)
        db.flush()
        db.add_all(
            [
                WorkflowEffectModel(
                    workflow_id=workflow.id,
                    workflow_turn_id=turns[1].id,
                    effect_kind="await_handoff",
                    effect_key="same-child-slice-zero",
                    state="wait_timeout",
                    claim_token="mirror",
                    mirrored_from_effect_id=first.id,
                    created_at=now + timedelta(seconds=1),
                    updated_at=now + timedelta(seconds=1),
                ),
                WorkflowEffectModel(
                    workflow_id=workflow.id,
                    workflow_turn_id=turns[2].id,
                    effect_kind="await_handoff",
                    effect_key="same-child-slice-zero",
                    state="wait_timeout",
                    claim_token="independent",
                    created_at=now + timedelta(seconds=2),
                    updated_at=now + timedelta(seconds=2),
                ),
            ]
        )
        turn_ids = [int(turn.id) for turn in turns]
        db.commit()

    history = interaction_read_model_service.list_interactions(
        "interaction-session", mode="history", limit=20
    )
    wait_turn_ids = {
        item["workflow"]["turn_id"]
        for item in history["items"]
        if item["interaction_type"] == "effect"
    }
    assert wait_turn_ids == {turn_ids[0], turn_ids[2]}


def test_open_workflow_unadmitted_transport_states_remain_current(monkeypatch):
    _install_database(monkeypatch)
    now = datetime(2026, 9, 7, 8, 30, 0)
    session_ids = []
    with database.SessionLocal() as db:
        for index, state in enumerate(("queued", "claimed", "sent")):
            session_id = f"unadmitted-{state}"
            terminal_id = f"owner-{state}"
            session_ids.append(session_id)
            db.add(_interaction_terminal(terminal_id, session_id=session_id))
            workflow = WorkflowModel(
                root_terminal_id=terminal_id,
                status="open",
                created_at=now + timedelta(seconds=index),
                updated_at=now + timedelta(seconds=index),
            )
            db.add(workflow)
            db.flush()
            turn = WorkflowTurnModel(
                workflow_id=workflow.id,
                kind="external_input",
                dedupe_key=f"unadmitted-{state}",
                state=state,
                created_at=now + timedelta(seconds=index),
                updated_at=now + timedelta(seconds=index),
            )
            db.add(turn)
            db.flush()
            workflow.active_turn_id = turn.id
        db.commit()

    assert interaction_read_model_service.list_session_current_queue_counts(session_ids) == {
        session_id: 1 for session_id in session_ids
    }
    for state, session_id in zip(("queued", "claimed", "sent"), session_ids):
        page = interaction_read_model_service.list_interactions(
            session_id, mode="current", limit=20
        )
        assert page["total"] == 1
        assert page["items"][0]["workflow"]["turn_state"] == state
        assert page["items"][0]["queue"]["admission_pending"] is True


def test_interaction_history_cursor_is_stable_bounded_and_query_constant(monkeypatch):
    engine = _install_database(monkeypatch)
    now = datetime(2026, 9, 5, 8, 0, 0)
    with database.SessionLocal() as db:
        db.add(_interaction_terminal())
        for index in range(9):
            db.add(
                InboxModel(
                    sender_id="ui",
                    receiver_id="owner",
                    message=f"history {index}",
                    status="delivered",
                    kind="message",
                    created_at=now + timedelta(seconds=index),
                )
            )
        db.commit()

    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        first = interaction_read_model_service.list_interactions(
            "interaction-session", mode="history", limit=4
        )
        repeated = interaction_read_model_service.list_interactions(
            "interaction-session", mode="history", limit=4
        )
        with database.SessionLocal() as db:
            db.add(
                InboxModel(
                    sender_id="ui",
                    receiver_id="owner",
                    message="newer than cursor snapshot",
                    status="delivered",
                    kind="message",
                    # Keep this on the snapshot's date to prove SQLite's
                    # text ordering cannot leak a post-snapshot insert.
                    created_at=datetime.fromisoformat(first["snapshot_at"]) + timedelta(seconds=1),
                )
            )
            db.commit()
        second = interaction_read_model_service.list_interactions(
            "interaction-session", mode="history", limit=4, cursor=first["next_cursor"]
        )
        third = interaction_read_model_service.list_interactions(
            "interaction-session", mode="history", limit=4, cursor=second["next_cursor"]
        )
    finally:
        event.remove(engine, "before_cursor_execute", record)

    ids = [item["id"] for page in (first, second, third) for item in page["items"]]
    assert [item["id"] for item in repeated["items"]] == [item["id"] for item in first["items"]]
    assert len(ids) == len(set(ids)) == 9
    assert first["total"] is second["total"] is third["total"] is None
    assert len(statements) == 4
    assert all("LIMIT ?" in statement for statement in statements)
    assert all(statement.count("LIMIT ?") >= 10 for statement in statements)
    assert all(
        "COUNT(*) AS total_count FROM base_page" not in statement for statement in statements
    )
    assert first["limit"] == 4
    assert third["next_cursor"] is None


def test_terminal_and_superseded_work_stays_history_while_authority_is_current(
    monkeypatch,
):
    _install_database(monkeypatch)
    now = datetime(2026, 9, 6, 14, 0, 0)
    terminal = _interaction_terminal()
    terminal.runtime_lifecycle = "exited"
    terminal.launch_worktree = "/tmp/interaction-authority"
    terminal.writer_authority_generation = "writer-generation"
    terminal.writable_work_context_id = "authority-context"
    with database.SessionLocal() as db:
        db.add(terminal)
        db.add(
            WritableWorkContextModel(
                id="authority-context",
                request_id="authority-request",
                project_id="project-1",
                session_id="interaction-session",
                terminal_id="owner",
                canonical_source="/tmp/source",
                canonical_worktree="/tmp/interaction-authority",
                branch="feat/authority",
                base_revision="2" * 40,
                state="admitted",
                writer_authority_generation="writer-generation",
                created_at=now,
                updated_at=now,
            )
        )
        workflow = WorkflowModel(
            root_terminal_id="owner",
            status="terminal",
            terminal_reason="workflow complete",
            created_at=now,
            updated_at=now,
        )
        db.add(workflow)
        db.flush()
        linked_inbox = InboxModel(
            sender_id="ui",
            receiver_id="owner",
            message="pending transport after terminal state",
            status="pending",
            kind="message",
            created_at=now + timedelta(seconds=1),
        )
        db.add(linked_inbox)
        db.flush()
        turn = WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="external_input",
            dedupe_key="terminal-authority-turn",
            state="sent",
            inbox_message_id=linked_inbox.id,
            superseded_by_turn_id=999,
            superseded_at=now,
            created_at=now + timedelta(seconds=2),
            updated_at=now + timedelta(seconds=2),
        )
        db.add(turn)
        db.flush()
        db.add(
            ProviderExecutionLeaseModel(
                terminal_id="owner", workflow_turn_id=turn.id, acquired_at=now
            )
        )
        db.commit()

    current = interaction_read_model_service.list_interactions(
        "interaction-session", mode="current", limit=20
    )
    history = interaction_read_model_service.list_interactions(
        "interaction-session", mode="history", limit=20
    )
    assert {item["interaction_type"] for item in current["items"]} == {
        "inbox",
        "runtime_authority",
    }
    provider_item = next(
        item for item in current["items"] if item["interaction_type"] == "runtime_authority"
    )
    assert provider_item["queue"]["wait_reason"] == "current_provider_turn"
    assert provider_item["workflow"]["status"] == "terminal"
    historical_turn = next(
        item for item in history["items"] if item["interaction_type"] == "workflow_turn"
    )
    assert historical_turn["final_disposition"] == "superseded"
    assert not historical_turn["current"]

    with database.SessionLocal() as db:
        inbox = db.get(InboxModel, 1)
        inbox.status = "delivered"
        db.commit()

    current = interaction_read_model_service.list_interactions(
        "interaction-session", mode="current", limit=20
    )
    assert [item["interaction_type"] for item in current["items"]] == ["runtime_authority"]
    snapshot = database.get_session_workspace_retirement_snapshot("authority-context")
    assert snapshot is not None and snapshot["reason_code"] == "PROVIDER_EXECUTION_ACTIVE"

    with database.SessionLocal() as db:
        db.query(ProviderExecutionLeaseModel).delete()
        db.add(
            WorktreeWriterLeaseModel(
                canonical_worktree="/tmp/interaction-authority",
                terminal_id="owner",
                authority_generation="writer-generation",
                created_at=now,
            )
        )
        db.commit()

    current = interaction_read_model_service.list_interactions(
        "interaction-session", mode="current", limit=20
    )
    assert [(item["interaction_type"], item["task_type"]) for item in current["items"]] == [
        ("runtime_authority", "writer_authority")
    ]
    snapshot = database.get_session_workspace_retirement_snapshot("authority-context")
    assert snapshot is not None and snapshot["reason_code"] == "WRITER_LEASE_ACTIVE"


def test_interaction_projection_reconstructs_from_durable_state_after_restart(
    monkeypatch, tmp_path
):
    state_path = tmp_path / "interaction-read-model.db"
    engine = _install_database(monkeypatch, f"sqlite:///{state_path}")
    now = datetime(2026, 9, 6, 16, 0, 0)
    with database.SessionLocal() as db:
        db.add_all([_interaction_terminal(), _interaction_terminal("child")])
        workflow = WorkflowModel(
            root_terminal_id="owner", status="open", created_at=now, updated_at=now
        )
        db.add(workflow)
        db.flush()
        db.add(
            WorkflowTurnModel(
                workflow_id=workflow.id,
                kind="external_input",
                dedupe_key="restart-current",
                payload="durable restart input",
                state="queued",
                created_at=now,
                updated_at=now,
            )
        )
        assignment = ChildAssignmentModel(
            parent_terminal_id="owner",
            child_terminal_id="child",
            status="result_acknowledged",
            created_at=now + timedelta(seconds=1),
            updated_at=now + timedelta(seconds=1),
        )
        db.add(assignment)
        db.flush()
        db.add(
            DelegationResultModel(
                id="durable-restart-result",
                child_assignment_id=assignment.id,
                schema_version=1,
                delegation_kind="assign",
                parent_terminal_id="owner",
                child_terminal_id="child",
                authorship="child_submission",
                status="complete",
                document_json=json.dumps(
                    {
                        "format": "v1",
                        "summary": "survives restart",
                        "body_markdown": "durable body",
                    }
                ),
                created_at=assignment.created_at,
                finalized_at=assignment.created_at,
                updated_at=assignment.updated_at,
            )
        )
        db.commit()

    before = {
        mode: interaction_read_model_service.list_interactions(
            "interaction-session", mode=mode, limit=20
        )["items"]
        for mode in ("current", "history")
    }
    engine.dispose()
    restarted_engine = _install_database(monkeypatch, f"sqlite:///{state_path}")
    try:
        after = {
            mode: interaction_read_model_service.list_interactions(
                "interaction-session", mode=mode, limit=20
            )["items"]
            for mode in ("current", "history")
        }
        assert after == before
        assert after["history"][0]["result"]["summary"] == "survives restart"
    finally:
        restarted_engine.dispose()


def test_session_page_adds_one_bounded_current_only_count_query(monkeypatch):
    engine = _install_database(monkeypatch)
    _seed_history(session_count=40, terminal_count=400)
    ui_read_model_service.list_session_summaries(limit=10)
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        page = ui_read_model_service.list_session_summaries(limit=10)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert len(page["items"]) == 10
    assert len(statements) == 2
    assert "LIMIT ? OFFSET ?" in statements[0]
    assert "FROM interaction_items WHERE is_current = 1" in statements[1]
    assert statements[1].count("WHERE is_current = 1") >= 9
    indexes = {
        index["name"]
        for table in ("workflow_turns", "inbox", "child_assignments", "workflow_effects")
        for index in inspect(engine).get_indexes(table)
    }
    assert {
        "ix_workflow_turns_workflow_created",
        "ix_inbox_receiver_status_created",
        "ix_inbox_sender_status_created",
        "ix_child_assignments_parent_status_created",
        "ix_child_assignments_child_status_created",
        "ix_workflow_effects_workflow_turn_state",
    }.issubset(indexes)


def test_current_queue_matches_workspace_retirement_safety_boundary(monkeypatch):
    _install_database(monkeypatch)
    now = datetime(2026, 9, 5, 8, 0, 0)
    terminal = _interaction_terminal()
    terminal.runtime_lifecycle = "exited"
    terminal.launch_worktree = "/tmp/interaction-worktree"
    terminal.writer_authority_generation = "writer-1"
    terminal.writable_work_context_id = "context-1"
    with database.SessionLocal() as db:
        db.add(terminal)
        db.add(
            WritableWorkContextModel(
                id="context-1",
                request_id="request-1",
                project_id="project-1",
                session_id="interaction-session",
                terminal_id="owner",
                canonical_source="/tmp/source",
                canonical_worktree="/tmp/interaction-worktree",
                branch="feat/interaction-test",
                base_revision="1" * 40,
                state="admitted",
                writer_authority_generation="writer-1",
                created_at=now,
                updated_at=now,
            )
        )
        workflow = WorkflowModel(
            root_terminal_id="owner", status="terminal", created_at=now, updated_at=now
        )
        db.add(workflow)
        db.flush()
        db.add(
            WorkflowTurnModel(
                workflow_id=workflow.id,
                kind="external_input",
                dedupe_key="historical-sent",
                state="sent",
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()

    assert (
        interaction_read_model_service.list_session_current_queue_counts(["interaction-session"])[
            "interaction-session"
        ]
        == 0
    )
    snapshot = database.get_session_workspace_retirement_snapshot("context-1")
    assert snapshot is not None and snapshot["reason_code"] is None

    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).one()
        workflow.status = "open"
        db.commit()
    assert (
        interaction_read_model_service.list_session_current_queue_counts(["interaction-session"])[
            "interaction-session"
        ]
        == 1
    )
    snapshot = database.get_session_workspace_retirement_snapshot("context-1")
    assert snapshot is not None and snapshot["reason_code"] == "WORKFLOW_OPEN"

    with database.SessionLocal() as db:
        turn = db.query(WorkflowTurnModel).one()
        turn.provider_processing_observed_at = now + timedelta(seconds=1)
        db.add(
            WorkflowTurnReceiptModel(
                workflow_turn_id=turn.id,
                receiver_terminal_id="owner",
                consumed_at=now + timedelta(seconds=1),
            )
        )
        db.commit()
    assert (
        interaction_read_model_service.list_session_current_queue_counts(["interaction-session"])[
            "interaction-session"
        ]
        == 1
    )
    current = interaction_read_model_service.list_interactions(
        "interaction-session", mode="current", limit=20
    )
    assert [
        (item["interaction_type"], item["queue"]["wait_reason"]) for item in current["items"]
    ] == [("workflow", "workflow_continuation")]
    snapshot = database.get_session_workspace_retirement_snapshot("context-1")
    assert snapshot is not None and snapshot["reason_code"] == "WORKFLOW_OPEN"

    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).one()
        workflow.status = "terminal"
        db.commit()
    assert (
        interaction_read_model_service.list_session_current_queue_counts(["interaction-session"])[
            "interaction-session"
        ]
        == 0
    )
    snapshot = database.get_session_workspace_retirement_snapshot("context-1")
    assert snapshot is not None and snapshot["reason_code"] is None


def test_superseded_review_attempts_are_history_not_current_or_retirement_authority(
    monkeypatch,
):
    _install_database(monkeypatch)
    now = datetime(2026, 9, 6, 12, 0, 0)
    terminal = _interaction_terminal()
    terminal.runtime_lifecycle = "exited"
    terminal.launch_worktree = "/tmp/superseded-review-worktree"
    terminal.writer_authority_generation = "writer-superseded-review"
    terminal.writable_work_context_id = "superseded-review-context"
    with database.SessionLocal() as db:
        db.add(terminal)
        db.add(
            WritableWorkContextModel(
                id="superseded-review-context",
                request_id="superseded-review-request",
                project_id="project-1",
                session_id="interaction-session",
                terminal_id="owner",
                canonical_source="/tmp/source",
                canonical_worktree="/tmp/superseded-review-worktree",
                branch="feat/superseded-review",
                base_revision="3" * 40,
                state="admitted",
                writer_authority_generation="writer-superseded-review",
                created_at=now,
                updated_at=now,
            )
        )
        attempts = [
            ChildAssignmentModel(
                parent_terminal_id="owner",
                child_terminal_id="reviewer",
                status="result_acknowledged",
                attempt_id="superseded-acknowledged-attempt",
                review_subject_kind="git_commit",
                review_subject_revision="a" * 40,
                review_subject_revision_source="explicit",
                review_superseded_at=now + timedelta(seconds=2),
                created_at=now,
                updated_at=now + timedelta(seconds=2),
            ),
            # Bounded rereview supersession can intentionally leave a handoff
            # attempt's transport-looking status intact. The durable review
            # authority marker, not that old status, is canonical.
            ChildAssignmentModel(
                parent_terminal_id="owner",
                child_terminal_id="reviewer-handoff",
                status="handoff_result_delivered",
                attempt_id="superseded-handoff-attempt",
                review_subject_kind="git_commit",
                review_subject_revision="b" * 40,
                review_subject_revision_source="explicit",
                review_superseded_at=now + timedelta(seconds=3),
                created_at=now + timedelta(seconds=1),
                updated_at=now + timedelta(seconds=3),
            ),
        ]
        db.add_all(attempts)
        db.flush()
        for attempt, result_id, kind in (
            (attempts[0], "superseded-ack-result", "assign"),
            (attempts[1], "superseded-handoff-result", "handoff"),
        ):
            db.add(
                DelegationResultModel(
                    id=result_id,
                    child_assignment_id=attempt.id,
                    schema_version=1,
                    delegation_kind=kind,
                    parent_terminal_id=attempt.parent_terminal_id,
                    child_terminal_id=attempt.child_terminal_id,
                    authorship="child_submission",
                    status="complete",
                    document_json=json.dumps(
                        {
                            "format": "v1",
                            "summary": f"historical {kind} review",
                            "body_markdown": "durable historical result",
                        }
                    ),
                    created_at=attempt.created_at,
                    finalized_at=attempt.updated_at,
                    updated_at=attempt.updated_at,
                )
            )
        db.commit()

    current = interaction_read_model_service.list_interactions(
        "interaction-session", mode="current", limit=20
    )
    history = interaction_read_model_service.list_interactions(
        "interaction-session", mode="history", limit=20
    )

    assert current["total"] == 0
    assert current["items"] == []
    assert interaction_read_model_service.list_session_current_queue_counts(
        ["interaction-session"]
    ) == {"interaction-session": 0}
    assert len(history["items"]) == 2
    assert {item["final_disposition"] for item in history["items"]} == {"superseded"}
    assert all(item["queue"]["wait_reason"] is None for item in history["items"])
    assert all(item["delivery"]["pending"] is False for item in history["items"])
    assert database.get_parent_completion_barrier("owner") == (0, 0)
    snapshot = database.get_session_workspace_retirement_snapshot("superseded-review-context")
    assert snapshot is not None and snapshot["reason_code"] is None
