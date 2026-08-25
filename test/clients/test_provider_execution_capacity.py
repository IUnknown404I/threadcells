import threading
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.database import (
    Base,
    InboxModel,
    TerminalModel,
    WorkflowTurnModel,
    acquire_provider_execution,
    acquire_provider_execution_decision,
    list_provider_execution_leases,
    mark_terminal_runtime_exited,
    release_provider_execution,
)
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.services import (
    inbox_service,
    operations_service,
    terminal_service,
    workflow_service,
)


@pytest.fixture
def capacity_db(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'capacity.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    sessions = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    with sessions() as db:
        for index in range(5):
            db.add(
                TerminalModel(
                    id=f"term-{index}",
                    tmux_session="capacity",
                    tmux_window=f"window-{index}",
                    provider="codex",
                    agent_profile="supervisor",
                    context_role="supervisor",
                    project_id=f"project-{index}",
                    runtime_lifecycle="running",
                )
            )
        db.commit()
    yield tmp_path
    engine.dispose()


def test_provider_execution_admission_is_atomic_and_wakes_after_release(capacity_db):
    """F: five contenders admit three; one exact release admits the next."""
    barrier = threading.Barrier(5)
    outcomes: dict[str, bool] = {}

    def contend(index: int) -> None:
        barrier.wait()
        outcomes[f"term-{index}"] = acquire_provider_execution(f"term-{index}", 100 + index, 3)

    threads = [threading.Thread(target=contend, args=(index,)) for index in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3)

    assert len(outcomes) == 5
    admitted = [terminal_id for terminal_id, acquired in outcomes.items() if acquired]
    waiting = [terminal_id for terminal_id, acquired in outcomes.items() if not acquired]
    assert len(admitted) == 3
    assert len(waiting) == 2
    assert len(list_provider_execution_leases()) == 3

    assert release_provider_execution(admitted[0]) is True
    waiting_index = int(waiting[0].split("-")[1])
    assert acquire_provider_execution(waiting[0], 100 + waiting_index, 3) is True
    assert len(list_provider_execution_leases()) == 3


def test_provider_admission_reports_runtime_busy_with_available_capacity(capacity_db):
    """A pane-operation conflict is not mislabeled as slot exhaustion."""
    turn_id = database.start_workflow_input("term-0")
    assert turn_id is not None
    with database.SessionLocal() as db:
        terminal = db.get(TerminalModel, "term-0")
        terminal.runtime_operation_kind = "transport"
        terminal.runtime_operation_token = "busy-token"
        terminal.runtime_operation_expires_at = datetime.now() + timedelta(minutes=1)
        db.commit()

    decision = acquire_provider_execution_decision("term-0", turn_id, 3)

    assert decision == {
        "acquired": False,
        "reason_code": "TERMINAL_RUNTIME_OPERATION_BUSY",
        "active": 0,
        "limit": 3,
        "available": 3,
        "draining": False,
        "certain": True,
    }
    assert list_provider_execution_leases() == []


def test_provider_admission_reports_only_true_saturation_as_capacity(capacity_db):
    for index in range(3):
        assert acquire_provider_execution(f"term-{index}", 500 + index, 3)

    decision = acquire_provider_execution_decision("term-3", 503, 3)

    assert decision["acquired"] is False
    assert decision["reason_code"] == "PROVIDER_EXECUTION_CAPACITY_EXHAUSTED"
    assert decision["active"] == 3
    assert decision["limit"] == 3
    assert decision["available"] == 0


def test_operations_admission_uses_same_atomic_capacity_snapshot(capacity_db, monkeypatch):
    turn_id = database.start_workflow_input("term-0")
    assert turn_id is not None
    with database.SessionLocal() as db:
        terminal = db.get(TerminalModel, "term-0")
        terminal.runtime_operation_kind = "transport"
        terminal.runtime_operation_token = "busy-token"
        terminal.runtime_operation_expires_at = datetime.now() + timedelta(minutes=1)
        db.commit()
    monkeypatch.setattr(
        operations_service,
        "require_resource_admission",
        lambda _config: {
            "provider_executions": {
                "active": 0,
                "limit": 3,
                "available": 3,
                "draining": False,
                "certain": True,
            }
        },
    )

    with pytest.raises(operations_service.AdmissionDenied) as denied:
        operations_service.acquire_provider_execution_slot(
            "term-0",
            turn_id,
            config={
                "max_provider_executions": 3,
                "lock_dir": str(capacity_db / "locks"),
                "context_launch_lock_timeout_seconds": 1,
            },
        )

    assert denied.value.reason_code == "TERMINAL_RUNTIME_OPERATION_BUSY"
    assert denied.value.status["provider_executions"] == {
        "active": 0,
        "limit": 3,
        "available": 3,
        "draining": False,
        "certain": True,
    }


def test_runtime_release_wakes_queued_input_once_without_owner_resend(capacity_db, monkeypatch):
    message = database.create_inbox_message("owner", "term-0", "continue once")
    provider = SimpleNamespace(
        paste_enter_count=1,
        get_status=lambda: TerminalStatus.IDLE,
        is_process_alive=lambda: True,
        mark_input_received=lambda: None,
    )
    monkeypatch.setattr(inbox_service.provider_manager, "get_provider", lambda *_: provider)
    monkeypatch.setattr(terminal_service.provider_manager, "get_provider", lambda *_: provider)
    monkeypatch.setattr(
        operations_service,
        "load_operations_config",
        lambda: {
            "max_resident_supervisors": 5,
            "max_provider_executions": 3,
            "max_work_contexts": 2,
            "max_heavy_execution_slots": 1,
            "lock_dir": str(capacity_db / "locks"),
            "context_launch_lock_timeout_seconds": 1,
        },
    )
    monkeypatch.setattr(operations_service, "require_resource_admission", lambda *_: {})
    monkeypatch.setattr(workflow_service, "reconcile_open_workflows", lambda *_args, **_kw: 0)
    sent: list[str] = []
    monkeypatch.setattr(
        terminal_service.tmux_client,
        "send_keys",
        lambda _session, _window, payload, **_kwargs: sent.append(payload),
    )
    monkeypatch.setattr(terminal_service.tmux_client, "send_special_key", lambda *_args: None)

    # The control-key transport owns the pane until its finally block. Its
    # committed release immediately wakes the durable owner input.
    assert terminal_service.send_special_key("term-0", "C-c") is True
    assert len(sent) == 1
    assert "continue once" in sent[0]
    with database.SessionLocal() as db:
        assert db.get(InboxModel, message.id).status == "delivered"

    assert inbox_service.reconcile_provider_execution_queue() == 0
    assert len(sent) == 1


def test_provider_execution_release_is_exactly_once_on_failure_and_exit(capacity_db):
    """H: retries cannot leak, double-release, go negative, or release another turn."""
    assert acquire_provider_execution("term-0", 200, 3) is True
    assert acquire_provider_execution("term-0", 200, 3) is True
    assert release_provider_execution("term-0", 201) is False
    assert release_provider_execution("term-0", 200) is True
    assert release_provider_execution("term-0", 200) is False
    assert list_provider_execution_leases() == []

    assert acquire_provider_execution("term-1", 201, 3) is True
    assert mark_terminal_runtime_exited("term-1") is True
    assert list_provider_execution_leases() == []
    assert release_provider_execution("term-1", 201) is False


def test_stale_ready_observer_cannot_release_successor_lease(capacity_db):
    """A status observer carries the exact lease seen before its external probe."""
    assert acquire_provider_execution("term-0", 300, 3)
    observed_turn = database.get_provider_execution_turn("term-0")
    assert observed_turn == 300

    assert release_provider_execution("term-0", 300)
    assert acquire_provider_execution("term-0", 301, 3)

    assert release_provider_execution("term-0", observed_turn) is False
    assert [
        (row["terminal_id"], row["workflow_turn_id"]) for row in list_provider_execution_leases()
    ] == [("term-0", 301)]


def test_workflow_closure_atomically_releases_and_fences_provider_lease(capacity_db):
    turn_id = database.start_workflow_input("term-0")
    assert turn_id is not None
    assert acquire_provider_execution("term-0", turn_id, 3)

    assert database.set_workflow_terminal_state("term-0", "terminal")
    assert list_provider_execution_leases() == []
    assert acquire_provider_execution("term-0", turn_id, 3) is False
    assert list_provider_execution_leases() == []


def test_workflow_closure_commit_failure_retains_open_root_and_lease(capacity_db):
    turn_id = database.start_workflow_input("term-0")
    assert turn_id is not None
    assert acquire_provider_execution("term-0", turn_id, 3)

    def fail_commit(_session):
        raise RuntimeError("injected commit failure")

    event.listen(database.SessionLocal.class_, "before_commit", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="injected commit failure"):
            database.set_workflow_terminal_state("term-0", "terminal")
    finally:
        event.remove(database.SessionLocal.class_, "before_commit", fail_commit)

    assert database.get_workflow_status("term-0") == "open"
    assert [
        (row["terminal_id"], row["workflow_turn_id"]) for row in list_provider_execution_leases()
    ] == [("term-0", turn_id)]


def test_runtime_exit_commit_failure_retains_lifecycle_and_lease(capacity_db):
    assert acquire_provider_execution("term-1", 401, 3)

    def fail_commit(_session):
        raise RuntimeError("injected exit commit failure")

    event.listen(database.SessionLocal.class_, "before_commit", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="injected exit commit failure"):
            mark_terminal_runtime_exited("term-1")
    finally:
        event.remove(database.SessionLocal.class_, "before_commit", fail_commit)

    assert database.get_terminal_metadata("term-1")["runtime_lifecycle"] == "running"
    assert [
        (row["terminal_id"], row["workflow_turn_id"]) for row in list_provider_execution_leases()
    ] == [("term-1", 401)]


def test_provider_admission_queue_merges_sources_by_durable_age(capacity_db):
    base = datetime(2026, 1, 1, 12, 0, 0)
    workflow_turns = []
    for terminal_id in ("term-0", "term-2"):
        turn_id = database.start_workflow_input(terminal_id)
        assert turn_id is not None
        assert database.queue_workflow_input_for_provider(terminal_id, turn_id, "queued")
        workflow_turns.append(turn_id)
    inbox_rows = [
        database.create_inbox_message("sender", "term-1", "first inbox"),
        database.create_inbox_message("sender", "term-3", "second inbox"),
    ]
    with database.SessionLocal() as db:
        db.query(WorkflowTurnModel).filter_by(id=workflow_turns[0]).update(
            {WorkflowTurnModel.created_at: base}
        )
        db.query(InboxModel).filter_by(id=inbox_rows[0].id).update(
            {InboxModel.created_at: base + timedelta(seconds=1)}
        )
        db.query(InboxModel).filter_by(id=inbox_rows[1].id).update(
            {InboxModel.created_at: base + timedelta(seconds=2)}
        )
        db.query(WorkflowTurnModel).filter_by(id=workflow_turns[1]).update(
            {WorkflowTurnModel.created_at: base + timedelta(seconds=3)}
        )
        db.commit()

    assert [
        (item["source"], item["terminal_id"])
        for item in database.get_provider_execution_admission_queue()
    ] == [
        ("workflow", "term-0"),
        ("inbox", "term-1"),
        ("inbox", "term-3"),
        ("workflow", "term-2"),
    ]


def test_provider_admission_queue_prefers_explicit_inbox_for_same_resident(capacity_db):
    """A stale synthetic continuation never outranks newer semantic input."""
    terminal_id = "term-0"
    active = database.start_workflow_input(terminal_id)
    assert active is not None
    assert database.claim_workflow_turn_receipt(terminal_id, active)
    stale = database.observe_workflow_final(terminal_id)
    assert isinstance(stale, int)
    message = database.create_inbox_message("owner", terminal_id, "new owner input")

    candidates = [
        item
        for item in database.get_provider_execution_admission_queue()
        if item["terminal_id"] == terminal_id
    ]
    assert candidates == [
        {
            "source": "inbox",
            "terminal_id": terminal_id,
            "created_at": message.created_at,
            "source_id": message.id,
        }
    ]


def test_provider_release_wakeup_dispatches_merged_sources_without_starvation(monkeypatch):
    order: list[tuple[str, str]] = []
    monkeypatch.setattr(
        inbox_service,
        "get_provider_execution_admission_queue",
        lambda: [
            {"source": "workflow", "terminal_id": "workflow-old"},
            {"source": "inbox", "terminal_id": "inbox-next"},
            {"source": "workflow", "terminal_id": "workflow-last"},
        ],
    )
    monkeypatch.setattr(
        inbox_service,
        "_dispatch_pending_messages_with_admission",
        lambda terminal_id, **_: order.append(("inbox", terminal_id)) or True,
    )
    monkeypatch.setattr(
        workflow_service,
        "reconcile_root_workflow",
        lambda terminal_id, **_: order.append(("workflow", terminal_id)) or True,
    )
    monkeypatch.setattr(workflow_service, "reconcile_open_workflows", lambda *_args, **_kw: 0)

    assert inbox_service.reconcile_provider_execution_queue() == 3
    assert order == [
        ("workflow", "workflow-old"),
        ("inbox", "inbox-next"),
        ("workflow", "workflow-last"),
    ]


def test_direct_inbox_wake_uses_real_fifo_leases_under_saturation(capacity_db, monkeypatch):
    """API/MCP/watchdog entry cannot privilege its resident or exceed three turns."""
    base = datetime(2026, 1, 1, 12, 0, 0)
    messages = [
        database.create_inbox_message("sender", f"term-{index}", f"message-{index}")
        for index in range(5)
    ]
    with database.SessionLocal() as db:
        for index, message in enumerate(messages):
            db.query(InboxModel).filter_by(id=message.id).update(
                {InboxModel.created_at: base + timedelta(seconds=index)}
            )
        db.commit()

    provider = SimpleNamespace(
        paste_enter_count=1,
        get_status=lambda: TerminalStatus.IDLE,
        is_process_alive=lambda: True,
        mark_input_received=lambda: None,
    )
    monkeypatch.setattr(inbox_service.provider_manager, "get_provider", lambda *_: provider)
    monkeypatch.setattr(terminal_service.provider_manager, "get_provider", lambda *_: provider)
    monkeypatch.setattr(
        operations_service,
        "load_operations_config",
        lambda: {
            "max_resident_supervisors": 5,
            "max_provider_executions": 3,
            "max_work_contexts": 2,
            "max_heavy_execution_slots": 1,
            "lock_dir": str(capacity_db / "locks"),
            "context_launch_lock_timeout_seconds": 1,
        },
    )
    monkeypatch.setattr(operations_service, "require_resource_admission", lambda *_: {})
    sent: list[str] = []
    monkeypatch.setattr(
        terminal_service.tmux_client,
        "send_keys",
        lambda _session, window, _message, **_kwargs: sent.append(window),
    )
    monkeypatch.setattr(workflow_service, "reconcile_open_workflows", lambda *_args, **_kw: 0)

    # The youngest resident triggers the wake, but the shared durable FIFO
    # still admits the three oldest rows and real lease CAS rejects the rest.
    assert inbox_service.check_and_send_pending_messages("term-4") is True
    assert sent == ["window-0", "window-1", "window-2"]
    assert [row["terminal_id"] for row in list_provider_execution_leases()] == [
        "term-0",
        "term-1",
        "term-2",
    ]
    with database.SessionLocal() as db:
        statuses = [
            db.query(InboxModel).filter_by(id=message.id).one().status for message in messages
        ]
    assert statuses == ["delivered", "delivered", "delivered", "pending", "pending"]


def test_generic_workflow_reconciler_cannot_claim_inbox_transport(capacity_db):
    message = database.create_inbox_message("sender", "term-0", "owned by Inbox")
    turn_id = database.ensure_workflow_turn_for_inbox(message.id)
    assert turn_id is not None

    assert database.claim_workflow_turn("term-0") is None
    claim = database.claim_workflow_turn("term-0", inbox_message_id=message.id)
    assert claim is not None
    assert claim["id"] == turn_id


def test_committed_release_survives_immediate_wake_failure(monkeypatch):
    monkeypatch.setattr(
        inbox_service,
        "reconcile_provider_execution_queue",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("injected wake failure")),
    )

    assert inbox_service.wake_provider_execution_queue() == 0


def test_provider_release_wakeup_prioritizes_existing_durable_queue(monkeypatch):
    """F: released capacity reaches older queued turns before new F13 work."""
    order: list[tuple[str, bool]] = []
    monkeypatch.setattr(workflow_service, "requeue_expired_workflow_turn_claims", lambda **_: 0)
    monkeypatch.setattr(
        workflow_service,
        "get_queued_workflow_root_terminal_ids",
        lambda: ["queued-oldest", "queued-next"],
    )
    monkeypatch.setattr(
        workflow_service,
        "get_open_workflow_root_terminal_ids",
        lambda: ["hot-completing", "queued-next", "idle-open"],
    )
    pending_receivers = MagicMock(return_value=["queued-next"])
    monkeypatch.setattr(
        workflow_service,
        "get_pending_message_receiver_ids",
        pending_receivers,
    )
    monkeypatch.setattr(
        workflow_service,
        "reconcile_root_workflow",
        lambda terminal_id, **kwargs: order.append((terminal_id, kwargs["pending_inbox"])) or True,
    )

    assert workflow_service.reconcile_open_workflows() == 4
    assert order == [
        ("queued-oldest", False),
        ("queued-next", True),
        ("hot-completing", False),
        ("idle-open", False),
    ]
    pending_receivers.assert_called_once_with()
