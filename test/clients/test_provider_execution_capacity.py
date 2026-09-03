import threading
from contextlib import contextmanager
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

    @contextmanager
    def admitted_workflow_fence(*_args, **_kwargs):
        yield True

    monkeypatch.setattr(
        operations_service,
        "workflow_execution_admission_fence",
        admitted_workflow_fence,
    )
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


def test_owner_gate_composer_resume_is_recovery_safe_for_disk_only_red(capacity_db, monkeypatch):
    initial = database.start_workflow_input("term-0")
    assert initial is not None
    assert database.set_workflow_terminal_state("term-0", "owner_gate", "owner decision")
    resumed = database.prepare_workflow_input(
        "term-0",
        "recover the resident workflow",
        request_id="ffebfd8c-cf1d-41a7-aa0e-0b584f7b9d6c",
        require_live_terminal=True,
    )
    red_disk = {
        "resource_state": "RED",
        "reasons": ["ROOT_DISK_PRESSURE", "root_free_below_green"],
    }
    monkeypatch.setattr(operations_service, "get_resource_status", lambda _config: red_disk)

    operations_service.acquire_provider_execution_slot(
        "term-0",
        resumed["turn_id"],
        config={
            "max_provider_executions": 3,
            "lock_dir": str(capacity_db / "locks"),
            "context_launch_lock_timeout_seconds": 1,
        },
    )

    assert database.get_provider_execution_turn("term-0") == resumed["turn_id"]


def test_owner_gate_open_final_recovery_is_safe_for_disk_only_red(capacity_db, monkeypatch):
    initial = database.start_workflow_input("term-0")
    assert initial is not None
    assert database.set_workflow_terminal_state("term-0", "owner_gate", "owner decision")
    resumed = database.prepare_workflow_input(
        "term-0",
        "recover the resident workflow",
        request_id="628c4e0a-b3f0-4e91-986f-326109581cac",
        require_live_terminal=True,
    )
    with database.SessionLocal() as db:
        workflow = (
            db.query(database.WorkflowModel)
            .filter_by(root_terminal_id="term-0")
            .order_by(database.WorkflowModel.id.desc())
            .first()
        )
        successor = database.WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="open_final",
            dedupe_key=f"open-final:{resumed['turn_id']}",
            state="queued",
            resume_parent_turn_id=resumed["turn_id"],
        )
        db.add(successor)
        db.flush()
        workflow.active_turn_id = successor.id
        successor_id = successor.id
        db.commit()

    monkeypatch.setattr(
        operations_service,
        "get_resource_status",
        lambda _config: {
            "resource_state": "RED",
            "reasons": ["ROOT_DISK_PRESSURE", "root_free_below_green"],
        },
    )

    operations_service.acquire_provider_execution_slot(
        "term-0",
        successor_id,
        config={
            "max_provider_executions": 3,
            "lock_dir": str(capacity_db / "locks"),
            "context_launch_lock_timeout_seconds": 1,
        },
    )

    assert database.get_provider_execution_turn("term-0") == successor_id


def test_owner_gate_composer_resume_still_fails_closed_for_non_disk_red(capacity_db, monkeypatch):
    initial = database.start_workflow_input("term-0")
    assert initial is not None
    assert database.set_workflow_terminal_state("term-0", "owner_gate", "owner decision")
    resumed = database.prepare_workflow_input(
        "term-0",
        "must wait for memory recovery",
        request_id="e7cadb6f-0e76-45a6-9305-c4fef1a539e6",
        require_live_terminal=True,
    )
    red_memory = {
        "resource_state": "RED",
        "reasons": ["critical_memory_pressure"],
    }
    monkeypatch.setattr(operations_service, "get_resource_status", lambda _config: red_memory)

    with pytest.raises(operations_service.AdmissionDenied) as denied:
        operations_service.acquire_provider_execution_slot(
            "term-0",
            resumed["turn_id"],
            config={
                "max_provider_executions": 3,
                "lock_dir": str(capacity_db / "locks"),
                "context_launch_lock_timeout_seconds": 1,
            },
        )

    assert denied.value.reason_code == "RESOURCE_HEALTH_REJECTED"
    assert database.get_provider_execution_turn("term-0") is None


def test_disk_red_recovery_requires_same_terminal_owner_gate_provenance(capacity_db, monkeypatch):
    initial = database.start_workflow_input("term-0")
    assert initial is not None
    assert database.set_workflow_terminal_state("term-0", "owner_gate", "owner decision")
    resumed = database.prepare_workflow_input(
        "term-0",
        "must retain exact owner provenance",
        request_id="282f4ec4-d87f-4679-86f4-6cf239d59ee0",
        require_live_terminal=True,
    )
    with database.SessionLocal() as db:
        unrelated = database.WorkflowModel(root_terminal_id="term-1", status="owner_gate")
        db.add(unrelated)
        db.flush()
        current = (
            db.query(database.WorkflowModel)
            .filter_by(root_terminal_id="term-0")
            .order_by(database.WorkflowModel.id.desc())
            .first()
        )
        current.resumed_from_owner_gate_workflow_id = unrelated.id
        db.commit()

    monkeypatch.setattr(
        operations_service,
        "get_resource_status",
        lambda _config: {
            "resource_state": "RED",
            "reasons": ["ROOT_DISK_PRESSURE", "root_free_below_green"],
        },
    )
    with pytest.raises(operations_service.AdmissionDenied) as denied:
        operations_service.acquire_provider_execution_slot(
            "term-0",
            resumed["turn_id"],
            config={
                "max_provider_executions": 3,
                "lock_dir": str(capacity_db / "locks"),
                "context_launch_lock_timeout_seconds": 1,
            },
        )

    assert denied.value.reason_code == "RESOURCE_HEALTH_REJECTED"


def test_disk_red_owner_resume_provenance_requires_current_active_turn(capacity_db):
    initial = database.start_workflow_input("term-0")
    assert initial is not None
    assert database.set_workflow_terminal_state("term-0", "owner_gate", "owner decision")
    resumed = database.prepare_workflow_input(
        "term-0",
        "the active owner continuation",
        request_id="b47287d0-dd6e-4862-9192-dc942d2ca29b",
        require_live_terminal=True,
    )
    assert database.is_owner_gate_resume_turn("term-0", resumed["turn_id"])

    with database.SessionLocal() as db:
        workflow = (
            db.query(database.WorkflowModel)
            .filter_by(root_terminal_id="term-0")
            .order_by(database.WorkflowModel.id.desc())
            .first()
        )
        later = database.WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="external_input",
            dedupe_key="external_request:later-active-owner-input",
            state="queued",
        )
        db.add(later)
        db.flush()
        workflow.active_turn_id = later.id
        db.commit()

    assert not database.is_owner_gate_resume_turn("term-0", resumed["turn_id"])


def test_resource_deferred_turn_has_truthful_durable_wait_projection(capacity_db):
    turn_id = database.start_workflow_input("term-0")
    assert turn_id is not None
    assert database.queue_workflow_input_for_provider(
        "term-0", turn_id, "wait safely", "RESOURCE_HEALTH_REJECTED"
    )

    assert database.get_terminal_execution_projection("term-0") == {
        "active_turn": False,
        "wait_reason": "resource_health",
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


def test_workflow_closure_fences_successors_but_retains_execution_until_provider_final(
    capacity_db,
):
    turn_id = database.start_workflow_input("term-0")
    assert turn_id is not None
    assert acquire_provider_execution("term-0", turn_id, 3)

    assert database.set_workflow_terminal_state("term-0", "terminal")
    assert [
        (row["terminal_id"], row["workflow_turn_id"]) for row in list_provider_execution_leases()
    ] == [("term-0", turn_id)]
    # An exact retry observes the one still-owned lease; it cannot add a
    # second execution or acquire any successor after workflow closure.
    assert acquire_provider_execution("term-0", turn_id, 3) is True
    assert len(list_provider_execution_leases()) == 1


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


def _seed_completed_assigned_child_execution(
    parent: str,
    child: str,
    *,
    result_complete: bool = True,
    bind_child_workflow: bool = True,
) -> tuple[int, str, int]:
    assert database.register_child_assignment(parent, child)
    child_turn = database.start_workflow_input(child)
    assert child_turn is not None
    assert acquire_provider_execution(child, child_turn, 3)
    with database.SessionLocal() as db:
        child_workflow = db.query(database.WorkflowModel).filter_by(root_terminal_id=child).one()
        assignment = (
            db.query(database.ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        )
        result = (
            db.query(database.DelegationResultModel)
            .filter_by(child_assignment_id=assignment.id)
            .one()
        )
        if bind_child_workflow:
            assignment.child_workflow_id = child_workflow.id
            assignment.child_workflow_turn_id = child_turn
        else:
            assignment.request_workflow_id = None
            assignment.request_workflow_turn_id = None
            assignment.request_workflow_effect_id = None
        if result_complete:
            now = datetime.now()
            result.status = "complete"
            result.finalized_at = now
            result.updated_at = now
            notice = InboxModel(
                sender_id=child,
                receiver_id=parent,
                message=f"result from {child}",
                status="pending",
                result_id=result.id,
                kind="delegation_result_notice",
            )
            db.add(notice)
            db.flush()
            assignment.result_message_id = notice.id
            assignment.status = "result_queued"
            callback = WorkflowTurnModel(
                workflow_id=result.parent_workflow_id,
                kind="assigned_result",
                dedupe_key=f"assigned-result:{assignment.attempt_id}",
                payload=notice.message,
                inbox_message_id=notice.id,
                state="queued",
            )
            db.add(callback)
            db.flush()
            result.workflow_turn_id = callback.id
        child_workflow.status = "terminal"
        child_workflow.terminal_reason = "authoritative delegated result accepted"
        child_workflow.updated_at = datetime.now()
        db.commit()
        return child_turn, result.id, assignment.result_message_id or 0


def _provider_final_observation(monkeypatch, statuses: dict[str, TerminalStatus]) -> None:
    providers: dict[str, MagicMock] = {}
    for terminal_id in statuses:
        provider = MagicMock()
        provider.get_status.side_effect = lambda terminal_id=terminal_id: statuses[terminal_id]
        provider.is_process_alive.return_value = True
        providers[terminal_id] = provider
    monkeypatch.setattr(
        terminal_service.provider_manager,
        "get_provider",
        lambda terminal_id: providers[terminal_id],
    )
    monkeypatch.setattr(terminal_service, "reconcile_terminal_runtime", lambda *_: False)
    monkeypatch.setattr(
        terminal_service,
        "provider_turn_execution_active",
        lambda terminal_id, _provider=None: statuses[terminal_id] == TerminalStatus.PROCESSING,
    )
    monkeypatch.setattr(terminal_service, "_wake_queued_provider_execution", lambda *_: None)
    from cli_agent_orchestrator.services import usage_service

    monkeypatch.setattr(usage_service, "observe_provider_usage", lambda *_args, **_kwargs: None)


def test_three_completed_children_release_capacity_without_ack_or_exit(capacity_db, monkeypatch):
    """#112: three durable completed children cannot deadlock a three-slot parent."""
    parent = "term-4"
    assert database.start_workflow_input(parent) is not None
    results: list[tuple[str, int]] = []
    for child in ("term-0", "term-1", "term-2"):
        _turn, result_id, notice_id = _seed_completed_assigned_child_execution(parent, child)
        results.append((result_id, notice_id))
    assert len(list_provider_execution_leases()) == 3
    before = database.list_terminal_ui_summary_page(limit=10, query="term-0")["items"][0]
    assert before["execution_state"] == "processing"
    assert before["workflow_state"] == "completed"
    assert before["result_status"] == "complete"
    assert before["delivery_status"] == "result_queued"
    _provider_final_observation(
        monkeypatch,
        {
            "term-0": TerminalStatus.IDLE,
            "term-1": TerminalStatus.COMPLETED,
            "term-2": TerminalStatus.IDLE,
        },
    )

    assert inbox_service._reconcile_terminal_workflow_provider_executions_with_admission() == 3
    assert list_provider_execution_leases() == []
    assert inbox_service._reconcile_terminal_workflow_provider_executions_with_admission() == 0

    after = database.list_terminal_ui_summary_page(limit=10, query="term-0")["items"][0]
    assert after["activity"] == "ready"
    assert after["execution_state"] == "ready"
    assert after["workflow_state"] == "completed"
    assert after["result_status"] == "complete"
    assert after["delivery_status"] == "result_queued"

    with database.SessionLocal() as db:
        for result_id, notice_id in results:
            result = db.get(database.DelegationResultModel, result_id)
            notice = db.get(InboxModel, notice_id)
            assignment = db.get(database.ChildAssignmentModel, result.child_assignment_id)
            assert result.status == "complete"
            assert result.finalized_at is not None
            assert notice.status == "pending"
            assert assignment.status == "result_queued"
            child = db.get(TerminalModel, assignment.child_terminal_id)
            assert child.runtime_lifecycle == "running"

    waiting_turn = database.start_workflow_input("term-3")
    assert waiting_turn is not None
    assert acquire_provider_execution("term-3", waiting_turn, 3)


def test_terminal_child_provider_final_before_result_durability_retains_lease(
    capacity_db, monkeypatch
):
    """#112: provider final alone cannot bypass immutable result durability."""
    parent = "term-4"
    assert database.start_workflow_input(parent) is not None
    child_turn, _result_id, _notice_id = _seed_completed_assigned_child_execution(
        parent,
        "term-0",
        result_complete=False,
    )
    _provider_final_observation(monkeypatch, {"term-0": TerminalStatus.COMPLETED})

    assert inbox_service._reconcile_terminal_workflow_provider_executions_with_admission() == 0
    assert database.get_provider_execution_turn("term-0") == child_turn


def test_legacy_unbound_completed_child_releases_only_with_exact_causal_proof(
    capacity_db, monkeypatch
):
    """#112 rolling upgrade: pre-binding results retain a bounded exact proof."""
    parent = "term-4"
    assert database.start_workflow_input(parent) is not None
    child_turn, result_id, notice_id = _seed_completed_assigned_child_execution(
        parent,
        "term-0",
        bind_child_workflow=False,
    )
    _provider_final_observation(monkeypatch, {"term-0": TerminalStatus.IDLE})

    candidates = database.list_terminal_workflow_provider_execution_candidates()
    assert [(row["terminal_id"], row["workflow_turn_id"]) for row in candidates] == [
        ("term-0", child_turn)
    ]
    assert inbox_service._reconcile_terminal_workflow_provider_executions_with_admission() == 1
    assert database.get_provider_execution_turn("term-0") is None
    with database.SessionLocal() as db:
        result = db.get(database.DelegationResultModel, result_id)
        assignment = db.get(database.ChildAssignmentModel, result.child_assignment_id)
        notice = db.get(InboxModel, notice_id)
        assert result.status == "complete"
        assert assignment.status == "result_queued"
        assert assignment.child_workflow_id is None
        assert notice.status == "pending"


def test_legacy_unbound_result_from_before_exact_lease_fails_closed(capacity_db):
    parent = "term-4"
    assert database.start_workflow_input(parent) is not None
    child_turn, result_id, _notice_id = _seed_completed_assigned_child_execution(
        parent,
        "term-0",
        bind_child_workflow=False,
    )
    with database.SessionLocal() as db:
        result = db.get(database.DelegationResultModel, result_id)
        lease = db.get(database.ProviderExecutionLeaseModel, "term-0")
        result.finalized_at = lease.acquired_at - timedelta(seconds=1)
        db.commit()

    assert database.list_terminal_workflow_provider_execution_candidates() == []
    assert database.get_provider_execution_turn("term-0") == child_turn


def test_durable_child_result_while_provider_processing_retains_then_releases_after_restart(
    capacity_db, monkeypatch
):
    """#112: result durability cannot lie about a genuinely active model turn."""
    parent = "term-4"
    assert database.start_workflow_input(parent) is not None
    child_turn, result_id, _notice_id = _seed_completed_assigned_child_execution(
        parent,
        "term-0",
    )
    statuses = {"term-0": TerminalStatus.PROCESSING}
    _provider_final_observation(monkeypatch, statuses)

    assert inbox_service._reconcile_terminal_workflow_provider_executions_with_admission() == 0
    assert database.get_provider_execution_turn("term-0") == child_turn
    assert database.get_delegation_result(result_id)["status"] == "complete"

    # A fresh daemon process observes the same durable candidate after the
    # provider task settles. The exact release is idempotent across restart.
    statuses["term-0"] = TerminalStatus.IDLE
    assert inbox_service._reconcile_terminal_workflow_provider_executions_with_admission() == 1
    assert database.get_provider_execution_turn("term-0") is None
    assert inbox_service._reconcile_terminal_workflow_provider_executions_with_admission() == 0


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


def test_runtime_exit_atomically_cancels_pending_inbox_and_workflow(capacity_db):
    turn_id = database.start_workflow_input("term-0")
    assert turn_id is not None
    message = database.create_inbox_message("owner", "term-0", "must not outlive exit")
    assert acquire_provider_execution("term-0", turn_id, 3)

    assert mark_terminal_runtime_exited("term-0") is True

    assert database.get_terminal_metadata("term-0")["runtime_lifecycle"] == "exited"
    assert database.get_workflow_status("term-0") == "cancelled"
    assert list_provider_execution_leases() == []
    assert database.get_provider_execution_admission_queue() == []
    with database.SessionLocal() as db:
        assert db.get(InboxModel, message.id).status == "failed"


def test_inbox_creation_and_runtime_exit_are_serialized(capacity_db):
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def create_message() -> None:
        barrier.wait()
        try:
            database.create_inbox_message("owner", "term-0", "racing input")
            outcomes.append("created")
        except ValueError:
            outcomes.append("rejected")

    def exit_runtime() -> None:
        barrier.wait()
        outcomes.append("exited" if mark_terminal_runtime_exited("term-0") else "exit_failed")

    creator = threading.Thread(target=create_message)
    exiter = threading.Thread(target=exit_runtime)
    creator.start()
    exiter.start()
    creator.join(3)
    exiter.join(3)

    assert "exited" in outcomes
    assert "exit_failed" not in outcomes
    assert len({"created", "rejected"} & set(outcomes)) == 1
    assert database.get_terminal_metadata("term-0")["runtime_lifecycle"] == "exited"
    assert database.get_pending_messages("term-0") == []
    assert database.get_provider_execution_admission_queue() == []


def test_inbox_creation_rejects_missing_receiver_without_orphan_row(capacity_db):
    with pytest.raises(ValueError, match="not found"):
        database.create_inbox_message("owner", "missing-terminal", "must not orphan")

    with database.SessionLocal() as db:
        assert db.query(InboxModel).count() == 0


def test_reconcile_exited_terminal_workflow_authority_repairs_legacy_race(capacity_db):
    assert mark_terminal_runtime_exited("term-0") is True
    with database.SessionLocal() as db:
        message = InboxModel(
            sender_id="owner",
            receiver_id="term-0",
            message="legacy pending input",
            status="pending",
        )
        workflow = database.WorkflowModel(root_terminal_id="term-0", status="open")
        db.add_all([message, workflow])
        db.flush()
        turn = WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="inbox_message",
            dedupe_key="inbox:legacy-exited",
            inbox_message_id=message.id,
            state="queued",
        )
        db.add(turn)
        db.flush()
        workflow_id = workflow.id
        message_id = message.id
        turn_id = turn.id
        db.commit()

    assert database.get_provider_execution_admission_queue()[0]["terminal_id"] == "term-0"
    assert database.reconcile_exited_terminal_workflow_authorities() == 1

    with database.SessionLocal() as db:
        assert db.get(database.WorkflowModel, workflow_id).status == "cancelled"
        assert db.get(InboxModel, message_id).status == "failed"
        assert db.get(WorkflowTurnModel, turn_id).state == "cancelled"
    assert database.get_provider_execution_admission_queue() == []


def test_composer_request_is_durable_and_idempotent(capacity_db):
    request_id = "33c0ce56-3400-4dc8-97dc-91529e2b6999"
    first = database.prepare_workflow_input(
        "term-0",
        "durable Composer input",
        request_id=request_id,
        require_live_terminal=True,
    )
    duplicate = database.prepare_workflow_input(
        "term-0",
        "durable Composer input",
        request_id=request_id,
        require_live_terminal=True,
    )

    assert first == {
        "accepted": True,
        "duplicate": False,
        "turn_id": first["turn_id"],
        "queued": False,
        "queue_reason": None,
    }
    assert duplicate == {
        "accepted": True,
        "duplicate": True,
        "turn_id": first["turn_id"],
        "queued": False,
        "queue_reason": None,
    }
    with database.SessionLocal() as db:
        turns = db.query(WorkflowTurnModel).all()
        assert len(turns) == 1
        assert turns[0].payload == "durable Composer input"


def test_composer_request_identity_rejects_changed_payload(capacity_db):
    request_id = "6fdba18b-10c2-4406-a169-b14684d395b2"
    assert database.prepare_workflow_input(
        "term-0", "first payload", request_id=request_id, require_live_terminal=True
    )["accepted"]

    conflict = database.prepare_workflow_input(
        "term-0", "changed payload", request_id=request_id, require_live_terminal=True
    )

    assert conflict == {
        "accepted": False,
        "reason_code": "WORKFLOW_INPUT_IDEMPOTENCY_CONFLICT",
    }
    with database.SessionLocal() as db:
        turns = db.query(WorkflowTurnModel).all()
        assert len(turns) == 1
        assert turns[0].payload == "first payload"


def test_composer_retry_rejects_closed_unreceipted_turn(capacity_db):
    request_id = "bc1c06b9-dfb0-4adb-8f72-547bf2aeaf67"
    prepared = database.prepare_workflow_input(
        "term-0",
        "input that closed before admission",
        request_id=request_id,
        require_live_terminal=True,
    )
    assert database.cancel_workflows_for_terminal("term-0") == 1

    retry = database.prepare_workflow_input(
        "term-0",
        "input that closed before admission",
        request_id=request_id,
        require_live_terminal=True,
    )

    assert retry == {
        "accepted": False,
        "reason_code": "WORKFLOW_INPUT_NO_LONGER_EXECUTABLE",
    }
    with database.SessionLocal() as db:
        assert db.query(WorkflowTurnModel).count() == 1
        assert db.get(WorkflowTurnModel, prepared["turn_id"]).state == "sent"


def test_composer_retry_accepts_receipted_historical_turn(capacity_db):
    request_id = "d0abdf4c-6c04-4adb-a3aa-66dd88dac850"
    prepared = database.prepare_workflow_input(
        "term-0",
        "input whose execution was admitted",
        request_id=request_id,
        require_live_terminal=True,
    )
    assert database.claim_workflow_turn_receipt("term-0", prepared["turn_id"])
    assert database.set_workflow_terminal_state("term-0", "terminal")

    retry = database.prepare_workflow_input(
        "term-0",
        "input whose execution was admitted",
        request_id=request_id,
        require_live_terminal=True,
    )

    assert retry == {
        "accepted": True,
        "duplicate": True,
        "turn_id": prepared["turn_id"],
        "queued": False,
        "queue_reason": None,
    }
    with database.SessionLocal() as db:
        assert db.query(WorkflowTurnModel).count() == 1


def test_composer_retry_rejects_cancelled_queued_turn(capacity_db):
    first = database.prepare_workflow_input(
        "term-0",
        "active predecessor",
        request_id="04aa2063-e6cf-4687-889a-f927eb76b1e5",
        require_live_terminal=True,
    )
    request_id = "c89ca989-45ee-4a01-8afc-4c5c133bda89"
    queued = database.prepare_workflow_input(
        "term-0",
        "queued input later cancelled",
        request_id=request_id,
        require_live_terminal=True,
    )
    assert first["queued"] is False
    assert queued["queued"] is True
    assert database.cancel_workflows_for_terminal("term-0") == 1

    retry = database.prepare_workflow_input(
        "term-0",
        "queued input later cancelled",
        request_id=request_id,
        require_live_terminal=True,
    )

    assert retry == {
        "accepted": False,
        "reason_code": "WORKFLOW_INPUT_NO_LONGER_EXECUTABLE",
    }
    with database.SessionLocal() as db:
        assert db.get(WorkflowTurnModel, queued["turn_id"]).state == "cancelled"


def test_distinct_composer_inputs_execute_in_creation_order_once(capacity_db, monkeypatch):
    first = database.prepare_workflow_input(
        "term-0",
        "first sequential input",
        request_id="d05dd16f-0936-456d-88d1-076ce0a8af66",
        require_live_terminal=True,
    )
    second = database.prepare_workflow_input(
        "term-0",
        "second sequential input",
        request_id="0f792d38-c09b-4a38-bf53-0dcc520d3fdf",
        require_live_terminal=True,
    )

    assert first["queued"] is False
    assert second["queued"] is True
    assert second["queue_reason"] == "workflow_predecessor"
    with database.SessionLocal() as db:
        turns = db.query(WorkflowTurnModel).order_by(WorkflowTurnModel.id.asc()).all()
        assert [(turn.id, turn.payload, turn.state) for turn in turns] == [
            (first["turn_id"], "first sequential input", "sent"),
            (second["turn_id"], "second sequential input", "queued"),
        ]

    assert database.claim_workflow_turn_receipt("term-0", first["turn_id"])
    terminal_state = {"lifecycle": "running", "status": TerminalStatus.IDLE.value}
    send = MagicMock(return_value=True)
    monkeypatch.setattr(
        workflow_service.terminal_service, "get_terminal", lambda *_: terminal_state
    )
    monkeypatch.setattr(workflow_service.terminal_service, "send_input", send)

    assert inbox_service.reconcile_provider_execution_queue() == 1
    assert send.call_count == 1
    assert f"logical-turn={second['turn_id']}" in send.call_args.args[1]
    assert send.call_args.args[1].endswith("second sequential input")
    terminal_state["status"] = TerminalStatus.PROCESSING.value
    assert inbox_service.reconcile_provider_execution_queue() == 0
    assert send.call_count == 1


def test_composer_input_after_completed_workflow_starts_one_successor(capacity_db):
    first = database.prepare_workflow_input(
        "term-0",
        "completed workflow input",
        request_id="ace57046-5a8b-43a5-8a0d-58111d6510a2",
        require_live_terminal=True,
    )
    assert database.set_workflow_terminal_state("term-0", "terminal")

    successor = database.prepare_workflow_input(
        "term-0",
        "successor workflow input",
        request_id="45ca7cbe-f8d4-466f-81ae-cc274734810c",
        require_live_terminal=True,
    )

    assert successor["accepted"] is True
    assert successor["queued"] is False
    assert successor["turn_id"] != first["turn_id"]
    with database.SessionLocal() as db:
        assert db.query(database.WorkflowModel).count() == 2


def test_interrupted_composer_send_requeues_same_payload_and_turn(capacity_db):
    prepared = database.prepare_workflow_input(
        "term-0",
        "resume this exact input",
        request_id="256fd05b-bb26-42d0-ad36-4c222177ab81",
        require_live_terminal=True,
    )

    assert database.requeue_unadmitted_workflow_turns_for_restart() == 1

    with database.SessionLocal() as db:
        turn = db.get(WorkflowTurnModel, prepared["turn_id"])
        assert turn.state == "queued"
        assert turn.payload == "resume this exact input"
    assert database.get_provider_execution_admission_queue() == [
        {
            "source": "workflow",
            "terminal_id": "term-0",
            "created_at": database.get_provider_execution_admission_queue()[0]["created_at"],
            "source_id": prepared["turn_id"],
        }
    ]


def test_restart_preserves_uncertain_sent_turn_until_provider_settles(capacity_db):
    prepared = database.prepare_workflow_input(
        "term-0",
        "retain the uncertain dispatch",
        request_id="cebb6f21-d3d6-45c3-975d-a1d0a5d17d42",
        require_live_terminal=True,
    )
    assert database.acquire_provider_execution("term-0", prepared["turn_id"], limit=2)

    assert database.requeue_unadmitted_workflow_turns_for_restart() == 0
    with database.SessionLocal() as db:
        assert db.get(WorkflowTurnModel, prepared["turn_id"]).state == "sent"

    assert database.release_provider_execution("term-0", prepared["turn_id"])
    assert database.requeue_settled_unadmitted_workflow_turn("term-0", prepared["turn_id"])
    assert not database.requeue_settled_unadmitted_workflow_turn("term-0", prepared["turn_id"])
    with database.SessionLocal() as db:
        turn = db.get(WorkflowTurnModel, prepared["turn_id"])
        assert turn.state == "queued"
        assert turn.queue_reason == "PROVIDER_SETTLED_BEFORE_RECEIPT"


def test_composer_input_queued_during_runtime_reconnect_wakes_after_recovery(
    capacity_db, monkeypatch
):
    with database.SessionLocal() as db:
        terminal = db.get(TerminalModel, "term-0")
        terminal.runtime_operation_kind = "reconnect"
        terminal.runtime_operation_token = "runtime-generation-reconnect"
        terminal.runtime_operation_expires_at = datetime.now() + timedelta(minutes=1)
        db.commit()

    prepared = database.prepare_workflow_input(
        "term-0",
        "continue after runtime generation recovery",
        request_id="610621f8-3bb1-420e-b07c-b04bb221e2ed",
        require_live_terminal=True,
    )
    assert prepared["queued"] is True
    assert prepared["queue_reason"] == "runtime_recovery"

    with database.SessionLocal() as db:
        terminal = db.get(TerminalModel, "term-0")
        terminal.runtime_operation_kind = None
        terminal.runtime_operation_token = None
        terminal.runtime_operation_expires_at = None
        db.commit()
    terminal_state = {"lifecycle": "running", "status": TerminalStatus.IDLE.value}
    send = MagicMock(return_value=True)
    monkeypatch.setattr(
        workflow_service.terminal_service, "get_terminal", lambda *_: terminal_state
    )
    monkeypatch.setattr(workflow_service.terminal_service, "send_input", send)

    assert inbox_service.reconcile_provider_execution_queue() == 1
    assert send.call_count == 1
    assert f"logical-turn={prepared['turn_id']}" in send.call_args.args[1]
    assert send.call_args.args[1].endswith("continue after runtime generation recovery")


def test_exited_terminal_rejects_composer_without_workflow_authority(capacity_db):
    assert mark_terminal_runtime_exited("term-0")

    rejected = database.prepare_workflow_input(
        "term-0",
        "must not resurrect",
        request_id="89858954-3df9-48f6-8fb7-6cf7a5fa53f6",
        require_live_terminal=True,
    )

    assert rejected == {
        "accepted": False,
        "reason_code": "TERMINAL_RUNTIME_NOT_WRITABLE",
    }
    assert database.get_provider_execution_admission_queue() == []
    with database.SessionLocal() as db:
        assert db.query(database.WorkflowModel).count() == 0
        assert db.query(WorkflowTurnModel).count() == 0


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


def test_provider_release_wakeup_dispatches_merged_sources_without_starvation(
    capacity_db, monkeypatch
):
    order: list[tuple[str, str]] = []
    monkeypatch.setattr(inbox_service, "reconcile_exited_terminal_workflow_authorities", lambda: 0)
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
