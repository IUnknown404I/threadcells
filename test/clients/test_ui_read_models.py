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
    ProviderExecutionLeaseModel,
    SessionDeletionReceiptModel,
    TerminalModel,
    WorkflowModel,
    WorkflowTurnModel,
    WorkflowTurnReceiptModel,
    WorktreeWriterLeaseModel,
)
from cli_agent_orchestrator.services import ui_read_model_service


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
            if terminal_id == "processing":
                turn = WorkflowTurnModel(
                    workflow_id=workflow.id,
                    kind="continuation",
                    dedupe_key="processing-turn",
                    state="claimed",
                )
                db.add(turn)
                db.flush()
                workflow.active_turn_id = turn.id
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

    assert overview["waiting"] == 1
    assert overview["owner_gate"] == 1
    assert overview["cancelled"] == 1
    assert overview["completed"] == 1
    assert [item["id"] for item in waiting["items"]] == ["ready"]
    assert [item["id"] for item in owner["items"]] == ["owner"]
    assert [item["id"] for item in cancelled["items"]] == ["cancelled"]
    assert [item["id"] for item in completed["items"]] == ["completed"]


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
            dedupe_key="active-resume",
            state="sent",
            created_at=now,
            updated_at=now,
        )
        db.add(active)
        db.flush()
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
    assert item["activity"] == "processing"


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

    # An admitted in-flight workflow turn remains Processing even if a live
    # provider observation is momentarily Ready while it performs MCP work.
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
    assert items["continuation"]["activity"] == "processing"
    assert items["continuation"]["execution_state"] == "processing"

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
