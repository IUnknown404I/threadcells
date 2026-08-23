from datetime import datetime, timedelta

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.database import (
    Base,
    CapacitySettingsModel,
    ProviderExecutionLeaseModel,
    TerminalModel,
    WorkflowModel,
    WorkflowTurnModel,
)
from cli_agent_orchestrator.services import ui_read_model_service


def _install_database(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
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
        db.commit()
    items = {
        item["id"]: item for item in ui_read_model_service.list_agent_summaries(limit=20)["items"]
    }
    assert items["continuation"]["activity"] == "processing"
    assert items["continuation"]["execution_state"] == "processing"


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
