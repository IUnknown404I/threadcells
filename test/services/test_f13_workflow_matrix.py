"""Focused F13 matrix: durable top-level workflow/run-loop semantics."""

import asyncio
import hashlib
import inspect
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
from threading import Barrier
from unittest.mock import ANY, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import cli_agent_orchestrator.clients.database as database
from cli_agent_orchestrator import constants
from cli_agent_orchestrator.api import main as api_main
from cli_agent_orchestrator.clients.database import (
    DEFER_STABLE_READY,
    DEFER_UNADMITTED,
    Base,
    InboxModel,
    TerminalModel,
    WorkflowEffectModel,
    WorkflowModel,
    WorkflowProviderReconnectAttemptModel,
    WorkflowTurnModel,
    WorkflowTurnReceiptModel,
    acknowledge_child_assignment_result,
    acknowledge_child_assignment_result_outcome,
    acknowledge_handoff_child_result_direct,
    activate_workflow_turn,
    activate_workflow_turn_for_inbox,
    arm_handoff_continuations_for_restart,
    bind_child_assignment_input_turn,
    bind_workflow_turn_provider_outcome_cursor,
    cancel_workflows_for_terminal,
    claim_handoff_child_result_direct,
    claim_handoff_result_batch_for_inbox,
    claim_or_resume_workflow_turn_receipt,
    claim_workflow_effect,
    claim_workflow_turn,
    claim_workflow_turn_receipt,
    create_child_assignment_result_message,
    create_handoff_child_result_message,
    create_inbox_message,
    finish_workflow_effect,
    get_delegation_result_for_assignment,
    get_handoff_child_result_message,
    get_parent_completion_barrier,
    get_pending_handoff_child_terminal_ids,
    get_workflow_provider_outcome,
    get_workflow_provider_outcome_observation,
    get_workflow_status,
    get_workflow_turn_for_inbox,
    issue_workflow_input_binding,
    mark_child_assignment_result_delivered,
    mark_workflow_turn_sent,
    mark_workflow_turn_sent_for_inbox,
    materialize_deferred_handoff_result_turn_for_inbox,
    observe_workflow_final,
    observe_workflow_processing,
    observe_workflow_provider_outcome,
    observe_workflow_ready,
    queue_workflow_turn,
    register_child_assignment,
    register_handoff_child,
    renew_workflow_turn_claim,
    requeue_expired_workflow_turn_claims,
    requeue_unacknowledged_child_assignment_results,
    requeue_unadmitted_workflow_turns_for_restart,
    requeue_workflow_turn,
    resolve_workflow_input_binding,
    set_workflow_terminal_state,
    start_workflow_input,
    submit_handoff_result_v1,
)
from cli_agent_orchestrator.mcp_server import server as mcp_server
from cli_agent_orchestrator.models.provider import ProviderTurnOutcome
from cli_agent_orchestrator.models.result import HandoffResultDocumentV1
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.plugins import PluginRegistry
from cli_agent_orchestrator.providers.codex import CodexProvider
from cli_agent_orchestrator.runtime_generation import ACTIVE_RUNTIME_GENERATION
from cli_agent_orchestrator.services import (
    inbox_service,
    operations_service,
    terminal_service,
    workflow_service,
)
from cli_agent_orchestrator.services.inbox_service import (
    LogFileHandler,
    _dispatch_pending_messages_with_admission,
    check_and_send_pending_messages,
    reconcile_handoff_continuations,
    reconcile_pending_messages,
)
from cli_agent_orchestrator.services.terminal_service import ExitTerminalResult

TEST_CODEX_RESUME_IDENTITY = "01234567-89ab-cdef-0123-456789abcdef"
TEST_TERMINAL_RUNTIME_GENERATION = "11111111-2222-4333-8444-555555555555"
TEST_PROVIDER_OUTCOME_CURSOR = "codex-jsonl-v1:1:2:3:1"


@pytest.fixture
def workflow_db(monkeypatch, tmp_path):
    database_path = tmp_path / "workflow.db"
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(database, "_child_assignment_schema_ready", True)
    monkeypatch.setattr(mcp_server, "_SIDECAR_RUNTIME_GENERATION", TEST_TERMINAL_RUNTIME_GENERATION)
    monkeypatch.setattr(
        mcp_server,
        "_active_runtime_generation",
        lambda: TEST_TERMINAL_RUNTIME_GENERATION,
    )

    @contextmanager
    def admitted_workflow_fence(*_args, **_kwargs):
        yield True

    monkeypatch.setattr(
        operations_service,
        "workflow_execution_admission_fence",
        admitted_workflow_fence,
    )
    # Provider-native rollout evidence belongs to the terminal-service unit
    # contour. Workflow matrix cases opt into active/unavailable boundaries
    # explicitly; their synthetic terminals have no real tmux rollout.
    monkeypatch.setattr(
        workflow_service.terminal_service,
        "provider_turn_execution_active",
        lambda _terminal_id: False,
    )
    yield engine
    engine.dispose()


def _authorized_callback(child_id: str):
    turn_id = _start_admitted_input(child_id)
    effect = claim_workflow_effect(child_id, turn_id, "send_message", "f13-callback")
    assert effect is not None
    return {"workflow_effect_id": effect["id"], "workflow_turn_id": turn_id}


def _confirmed_exit_result() -> ExitTerminalResult:
    return ExitTerminalResult(
        success=True,
        lifecycle="exited",
        outcome="command_delivered",
        message="exit confirmed",
        command_delivered=True,
    )


def _ensure_running_test_terminal(root: str) -> None:
    """Give workflow-only test roots the live receiver required in production."""
    with database.SessionLocal() as db:
        if db.get(TerminalModel, root) is None:
            db.add(
                TerminalModel(
                    id=root,
                    tmux_session=f"cao-{root}",
                    tmux_window="owner-0000",
                    provider="codex",
                    runtime_lifecycle="running",
                    runtime_generation=TEST_TERMINAL_RUNTIME_GENERATION,
                    provider_resume_identity=TEST_CODEX_RESUME_IDENTITY,
                    provider_resume_runtime_generation=TEST_TERMINAL_RUNTIME_GENERATION,
                )
            )
            db.commit()


def _start_admitted_input(root: str) -> int:
    """Create the initial provider turn and model its mandatory receipt."""
    _ensure_running_test_terminal(root)
    turn_id = start_workflow_input(root)
    assert turn_id is not None
    assert claim_workflow_turn_receipt(root, turn_id)
    return turn_id


def _bind_policy_cursor(root: str, turn_id: int) -> str:
    assert bind_workflow_turn_provider_outcome_cursor(root, turn_id, TEST_PROVIDER_OUTCOME_CURSOR)
    return TEST_PROVIDER_OUTCOME_CURSOR


def test_fresh_codex_turn_reserves_and_binds_session_start_outcome_cursor(workflow_db):
    root = "root-fresh-cursor-bootstrap"
    with database.SessionLocal() as db:
        db.add(
            TerminalModel(
                id=root,
                tmux_session=f"cao-{root}",
                tmux_window="owner-0000",
                provider="codex",
                runtime_lifecycle="running",
                runtime_generation=TEST_TERMINAL_RUNTIME_GENERATION,
            )
        )
        db.commit()
    turn_id = start_workflow_input(root)
    assert turn_id is not None

    assert database.reserve_workflow_turn_provider_outcome_cursor_bootstrap(
        root, turn_id, TEST_TERMINAL_RUNTIME_GENERATION
    )
    assert (
        database.get_workflow_turn_provider_outcome_cursor_bootstrap(
            root, TEST_TERMINAL_RUNTIME_GENERATION
        )
        == turn_id
    )
    with database.SessionLocal() as db:
        terminal = db.get(TerminalModel, root)
        terminal.provider_resume_identity = TEST_CODEX_RESUME_IDENTITY
        terminal.provider_resume_runtime_generation = TEST_TERMINAL_RUNTIME_GENERATION
        db.commit()
    assert bind_workflow_turn_provider_outcome_cursor(root, turn_id, TEST_PROVIDER_OUTCOME_CURSOR)
    assert (
        database.get_workflow_turn_provider_outcome_cursor_bootstrap(
            root, TEST_TERMINAL_RUNTIME_GENERATION
        )
        is None
    )
    assert get_workflow_provider_outcome_observation(root) == {
        "turn_id": turn_id,
        "cursor": TEST_PROVIDER_OUTCOME_CURSOR,
    }


def test_persisted_resume_metadata_reaches_real_cursor_capture_path(workflow_db):
    root = "root-real-resume-metadata-projection"
    _ensure_running_test_terminal(root)
    provider = MagicMock()
    provider.capture_turn_outcome_cursor.return_value = TEST_PROVIDER_OUTCOME_CURSOR

    assert (
        terminal_service.capture_provider_turn_outcome_cursor(root, provider)
        == TEST_PROVIDER_OUTCOME_CURSOR
    )
    provider.capture_turn_outcome_cursor.assert_called_once_with(
        provider_session_id=TEST_CODEX_RESUME_IDENTITY
    )


def test_retry_exhausted_canonical_input_recovers_once_but_genuine_owner_gate_does_not(
    workflow_db,
):
    recovered_root = "root-pretransport-recovery"
    _ensure_running_test_terminal(recovered_root)
    prepared = database.prepare_workflow_input(
        recovered_root,
        "pre-existing canonical task",
        request_id="pre-existing-canonical-task",
        require_live_terminal=True,
    )
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=recovered_root).one()
        turn = db.get(WorkflowTurnModel, prepared["turn_id"])
        workflow.status = "owner_gate"
        workflow.terminal_reason = database.BOUNDED_TRANSPORT_RETRY_GUARD_REASON
        turn.state = "cancelled"
        turn.attempt_count = 3
        turn.queue_reason = "PROVIDER_TRANSPORT_RETRY_EXHAUSTED"
        db.commit()

    assert database.reconcile_owner_gated_workflow_successors() == [recovered_root]
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=recovered_root).one()
        turn = db.get(WorkflowTurnModel, prepared["turn_id"])
        assert workflow.status == "open"
        assert turn.state == "queued"
        assert turn.dispatch_recovery_count == 1
        workflow.status = "owner_gate"
        workflow.terminal_reason = database.BOUNDED_TRANSPORT_RETRY_GUARD_REASON
        turn.state = "cancelled"
        turn.queue_reason = "PROVIDER_TRANSPORT_RETRY_EXHAUSTED"
        db.commit()

    assert database.reconcile_owner_gated_workflow_successors() == []

    genuine_root = "root-genuine-owner-gate"
    _ensure_running_test_terminal(genuine_root)
    genuine_turn = start_workflow_input(genuine_root)
    assert genuine_turn is not None
    assert set_workflow_terminal_state(genuine_root, "owner_gate", "owner decision required")
    assert database.reconcile_owner_gated_workflow_successors() == []
    assert get_workflow_status(genuine_root) == "owner_gate"


def test_post_fix_transport_retry_gate_never_enters_upgrade_recovery(workflow_db):
    root = "root-post-fix-transport-gate"
    _ensure_running_test_terminal(root)
    prepared = database.prepare_workflow_input(
        root,
        "new dispatcher failure",
        request_id="post-fix-transport-gate",
        require_live_terminal=True,
    )
    assert database.queue_workflow_input_for_provider(
        root,
        prepared["turn_id"],
        "new dispatcher failure",
        "PROVIDER_TRANSPORT_RETRY_PENDING",
    )
    now = datetime(2026, 8, 31, 12, 0, 0)
    for offset in (0, 2, 5):
        claimed = claim_workflow_turn(root, now=now + timedelta(seconds=offset))
        assert claimed is not None
        assert requeue_workflow_turn(
            claimed["id"],
            claimed["claim_token"],
            claimed["claim_generation"],
            now=now + timedelta(seconds=offset),
        )

    assert get_workflow_status(root) == "owner_gate"
    with database.SessionLocal() as db:
        turn = db.get(WorkflowTurnModel, prepared["turn_id"])
        assert turn.dispatch_recovery_count == 1
        assert turn.queue_reason == "PROVIDER_TRANSPORT_RETRY_EXHAUSTED"
    assert database.reconcile_owner_gated_workflow_successors() == []


def test_historical_pre_fix_composer_turn_survives_newer_bootstrap_transport_gates(
    workflow_db,
):
    root = "root-production-owner-gate-replay"
    _ensure_running_test_terminal(root)
    now = datetime(2026, 8, 31, 13, 41, 0)
    with database.SessionLocal() as db:
        original_workflow = WorkflowModel(
            root_terminal_id=root,
            status="owner_gate",
            terminal_reason=database.BOUNDED_TRANSPORT_RETRY_GUARD_REASON,
        )
        db.add(original_workflow)
        db.flush()
        original_turn = WorkflowTurnModel(
            workflow_id=original_workflow.id,
            kind="external_input",
            dedupe_key="external_request:pre-existing-production-task",
            payload="same pre-existing canonical Composer task",
            state="cancelled",
            attempt_count=3,
            queue_reason="PROVIDER_TRANSPORT_RETRY_EXHAUSTED",
            dispatch_recovery_count=0,
        )
        db.add(original_turn)
        db.flush()
        original_workflow.active_turn_id = original_turn.id

        newer_workflows = []
        newer_turns = []
        for index in range(2):
            newer = WorkflowModel(
                root_terminal_id=root,
                status="owner_gate",
                terminal_reason=database.BOUNDED_TRANSPORT_RETRY_GUARD_REASON,
                resumed_from_owner_gate_workflow_id=(
                    original_workflow.id if index == 0 else newer_workflows[-1].id
                ),
            )
            db.add(newer)
            db.flush()
            newer_turn = WorkflowTurnModel(
                workflow_id=newer.id,
                kind="external_input",
                dedupe_key=f"external_request:bootstrap-{index}",
                payload=f"bootstrap {index}",
                state="cancelled",
                attempt_count=3,
                queue_reason="PROVIDER_TRANSPORT_RETRY_EXHAUSTED",
                dispatch_recovery_count=1,
            )
            db.add(newer_turn)
            db.flush()
            newer.active_turn_id = newer_turn.id
            newer_workflows.append(newer)
            newer_turns.append(newer_turn)
        original_workflow_id = original_workflow.id
        original_turn_id = original_turn.id
        latest_workflow_id = newer_workflows[-1].id
        newer_turn_ids = [turn.id for turn in newer_turns]
        db.commit()

    barrier = Barrier(2)

    def reconcile(_index):
        barrier.wait()
        return database.reconcile_owner_gated_workflow_successors(now=now)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reconcile, range(2)))

    assert sorted(results, key=len) == [[], [root]]
    assert database.reconcile_owner_gated_workflow_successors(now=now) == []
    with database.SessionLocal() as db:
        original = db.get(WorkflowModel, original_workflow_id)
        latest = db.get(WorkflowModel, latest_workflow_id)
        recovered = db.get(WorkflowTurnModel, original_turn_id)
        assert original.status == "owner_gate"
        assert original.active_turn_id is None
        assert latest.status == "open"
        assert latest.active_turn_id == original_turn_id
        assert latest.resumed_from_owner_gate_workflow_id == original_workflow_id
        assert recovered.workflow_id == latest_workflow_id
        assert recovered.state == "queued"
        assert recovered.payload == "same pre-existing canonical Composer task"
        assert recovered.dedupe_key == "external_request:pre-existing-production-task"
        assert recovered.dispatch_recovery_count == 1
        assert [db.get(WorkflowTurnModel, turn_id).state for turn_id in newer_turn_ids] == [
            "cancelled",
            "cancelled",
        ]

    claim = claim_workflow_turn(root, now=now)
    assert claim is not None and claim["id"] == original_turn_id
    assert claim_workflow_turn(root, now=now) is None


def test_historical_transport_replay_does_not_cross_newer_genuine_owner_gate(workflow_db):
    root = "root-production-genuine-gate"
    _ensure_running_test_terminal(root)
    with database.SessionLocal() as db:
        historical = WorkflowModel(
            root_terminal_id=root,
            status="owner_gate",
            terminal_reason=database.BOUNDED_TRANSPORT_RETRY_GUARD_REASON,
        )
        db.add(historical)
        db.flush()
        turn = WorkflowTurnModel(
            workflow_id=historical.id,
            kind="external_input",
            dedupe_key="external_request:historical",
            payload="must remain gated",
            state="cancelled",
            attempt_count=3,
            queue_reason="PROVIDER_TRANSPORT_RETRY_EXHAUSTED",
            dispatch_recovery_count=0,
        )
        db.add(turn)
        db.flush()
        historical.active_turn_id = turn.id
        genuine = WorkflowModel(
            root_terminal_id=root,
            status="owner_gate",
            terminal_reason="owner decision required",
        )
        db.add(genuine)
        db.commit()

    assert database.reconcile_owner_gated_workflow_successors() == []
    assert get_workflow_status(root) == "owner_gate"


@pytest.mark.parametrize("semantic_history", ["receipt", "effect", "queued_turn"])
def test_historical_transport_replay_does_not_cross_newer_semantic_history(
    workflow_db, semantic_history
):
    root = f"root-production-history-{semantic_history}"
    _ensure_running_test_terminal(root)
    with database.SessionLocal() as db:
        historical = WorkflowModel(
            root_terminal_id=root,
            status="owner_gate",
            terminal_reason=database.BOUNDED_TRANSPORT_RETRY_GUARD_REASON,
        )
        db.add(historical)
        db.flush()
        original = WorkflowTurnModel(
            workflow_id=historical.id,
            kind="external_input",
            dedupe_key="external_request:historical",
            payload="historical payload",
            state="cancelled",
            attempt_count=3,
            queue_reason="PROVIDER_TRANSPORT_RETRY_EXHAUSTED",
            dispatch_recovery_count=0,
        )
        db.add(original)
        db.flush()
        historical.active_turn_id = original.id

        newer = WorkflowModel(
            root_terminal_id=root,
            status="owner_gate",
            terminal_reason=database.BOUNDED_TRANSPORT_RETRY_GUARD_REASON,
            resumed_from_owner_gate_workflow_id=historical.id,
        )
        db.add(newer)
        db.flush()
        active = WorkflowTurnModel(
            workflow_id=newer.id,
            kind="external_input",
            dedupe_key="external_request:newer-active",
            payload="newer bootstrap payload",
            state="cancelled",
            attempt_count=3,
            queue_reason="PROVIDER_TRANSPORT_RETRY_EXHAUSTED",
            dispatch_recovery_count=1,
        )
        db.add(active)
        db.flush()
        newer.active_turn_id = active.id

        queued_turn_id = None
        if semantic_history in {"receipt", "queued_turn"}:
            earlier = WorkflowTurnModel(
                workflow_id=newer.id,
                kind="external_input",
                dedupe_key=f"external_request:{semantic_history}",
                payload="newer semantic authority",
                state="sent" if semantic_history == "receipt" else "queued",
            )
            db.add(earlier)
            db.flush()
            queued_turn_id = earlier.id
            if semantic_history == "receipt":
                db.add(
                    WorkflowTurnReceiptModel(
                        workflow_turn_id=earlier.id,
                        receiver_terminal_id=root,
                    )
                )
        else:
            db.add(
                WorkflowEffectModel(
                    workflow_id=newer.id,
                    workflow_turn_id=active.id,
                    effect_kind="send_message",
                    effect_key="newer-semantic-effect",
                    state="finished",
                    claim_token="effect-claim",
                )
            )
        historical_id = historical.id
        original_id = original.id
        newer_id = newer.id
        active_id = active.id
        db.commit()

    expected = [root] if semantic_history == "queued_turn" else []
    assert database.reconcile_owner_gated_workflow_successors() == expected
    with database.SessionLocal() as db:
        historical = db.get(WorkflowModel, historical_id)
        newer = db.get(WorkflowModel, newer_id)
        assert historical.status == "owner_gate"
        assert historical.active_turn_id == original_id
        if semantic_history == "queued_turn":
            assert newer.status == "open"
            assert newer.active_turn_id == queued_turn_id
        else:
            assert newer.status == "owner_gate"
            assert newer.active_turn_id == active_id
        assert db.get(WorkflowTurnModel, original_id).workflow_id == historical_id


@patch("cli_agent_orchestrator.services.workflow_service.terminal_service")
def test_production_owner_gate_replay_dispatches_and_admits_same_turn_once(
    mock_terminal, workflow_db
):
    root = "root-production-live-replay"
    _ensure_running_test_terminal(root)
    now = datetime(2026, 8, 31, 13, 55, 0)
    with database.SessionLocal() as db:
        stranded = WorkflowModel(
            root_terminal_id=root,
            status="owner_gate",
            terminal_reason=database.BOUNDED_TRANSPORT_RETRY_GUARD_REASON,
        )
        db.add(stranded)
        db.flush()
        original = WorkflowTurnModel(
            workflow_id=stranded.id,
            kind="external_input",
            dedupe_key="external_request:same-live-payload",
            payload="same live production payload",
            state="cancelled",
            attempt_count=3,
            queue_reason="PROVIDER_TRANSPORT_RETRY_EXHAUSTED",
            dispatch_recovery_count=0,
        )
        db.add(original)
        db.flush()
        stranded.active_turn_id = original.id
        newer = WorkflowModel(
            root_terminal_id=root,
            status="owner_gate",
            terminal_reason=database.BOUNDED_TRANSPORT_RETRY_GUARD_REASON,
            resumed_from_owner_gate_workflow_id=stranded.id,
        )
        db.add(newer)
        db.flush()
        bootstrap = WorkflowTurnModel(
            workflow_id=newer.id,
            kind="external_input",
            dedupe_key="external_request:bootstrap",
            payload="already handled bootstrap",
            state="cancelled",
            attempt_count=3,
            queue_reason="PROVIDER_TRANSPORT_RETRY_EXHAUSTED",
            dispatch_recovery_count=1,
        )
        db.add(bootstrap)
        db.flush()
        newer.active_turn_id = bootstrap.id
        original_turn_id = original.id
        db.commit()

    mock_terminal.get_terminal.return_value = {
        "status": TerminalStatus.IDLE.value,
        "lifecycle": "running",
    }
    mock_terminal.provider_runtime_sidecar_reconnect_required.return_value = False
    mock_terminal.provider_turn_execution_active.return_value = False
    mock_terminal.provider_turn_outcome.return_value = None
    mock_terminal.send_input.return_value = True

    assert inbox_service.reconcile_provider_execution_queue(observe_open_workflows=False) == 1
    assert inbox_service.reconcile_provider_execution_queue(observe_open_workflows=False) == 0
    mock_terminal.send_input.assert_called_once()
    assert mock_terminal.send_input.call_args.kwargs["logical_turn_id"] == original_turn_id
    assert f"[CAO workflow input: logical-turn={original_turn_id}]" in (
        mock_terminal.send_input.call_args.args[1]
    )
    with database.SessionLocal() as db:
        turn = db.get(WorkflowTurnModel, original_turn_id)
        assert turn.state == "sent"
        assert turn.payload == "same live production payload"
        assert db.query(WorkflowTurnReceiptModel).count() == 0

    assert claim_workflow_turn_receipt(root, original_turn_id) is True
    assert claim_workflow_turn_receipt(root, original_turn_id) is False
    assert inbox_service.reconcile_provider_execution_queue(observe_open_workflows=False) == 0
    assert mock_terminal.send_input.call_count == 1


@patch("cli_agent_orchestrator.services.workflow_service.terminal_service")
def test_provider_native_active_task_fences_ready_projection_before_dispatch(
    mock_terminal, workflow_db
):
    root = "root-ready-projection-active-provider"
    _ensure_running_test_terminal(root)
    prepared = database.prepare_workflow_input(
        root,
        "wait for provider task completion",
        request_id="provider-active-ready-projection",
        require_live_terminal=True,
    )
    assert database.queue_workflow_input_for_provider(
        root,
        prepared["turn_id"],
        "wait for provider task completion",
        "WORKFLOW_CONTINUATION_PENDING",
    )
    mock_terminal.get_terminal.return_value = {
        "status": TerminalStatus.IDLE.value,
        "lifecycle": "running",
    }
    mock_terminal.provider_runtime_sidecar_reconnect_required.return_value = False
    mock_terminal.provider_turn_execution_active.side_effect = [True, False]
    mock_terminal.provider_turn_outcome.return_value = None
    mock_terminal.send_input.return_value = True

    assert workflow_service.reconcile_root_workflow(root) is False
    mock_terminal.send_input.assert_not_called()
    with database.SessionLocal() as db:
        assert db.get(WorkflowTurnModel, prepared["turn_id"]).state == "queued"

    assert workflow_service.reconcile_root_workflow(root) is True
    mock_terminal.send_input.assert_called_once()


def test_composer_successor_waits_for_reconnect_then_delivers_without_second_reconnect(
    workflow_db, monkeypatch
):
    root = "root-reconnect-composer-successor"
    predecessor = _start_admitted_input(root)
    reconnect_turn = observe_workflow_final(root)
    assert isinstance(reconnect_turn, int) and reconnect_turn != predecessor
    assert database.request_workflow_provider_reconnect(root)

    prepared = database.prepare_workflow_input(
        root,
        "deliver after exact reconnect",
        request_id="reconnect-composer-successor",
        require_live_terminal=True,
    )
    assert prepared["queued"] is True
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=root).one()
        assert workflow.active_turn_id == predecessor
        assert db.get(WorkflowTurnModel, reconnect_turn).state == "cancelled"

    reconnect = database.claim_workflow_provider_reconnect(root)
    assert reconnect is not None and reconnect.get("exhausted") is not True
    with database.SessionLocal() as db:
        attempt = (
            db.query(WorkflowProviderReconnectAttemptModel)
            .filter_by(attempt_token=reconnect["attempt_token"])
            .one()
        )
        attempt.state = database.PROVIDER_RECONNECT_READY
        attempt.output_log_device = 1
        attempt.output_log_inode = 2
        attempt.output_log_offset = 3
        attempt.output_boundary_at = datetime.now()
        db.commit()
    assert database.complete_workflow_provider_reconnect(
        root,
        predecessor,
        reconnect["claim_token"],
        reconnect["attempt_token"],
    )
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=root).one()
        assert workflow.active_turn_id == prepared["turn_id"]
        assert db.get(WorkflowTurnModel, reconnect_turn).state == "cancelled"
        assert db.get(WorkflowTurnModel, prepared["turn_id"]).state == "queued"

    send = MagicMock(return_value=True)
    monkeypatch.setattr(
        workflow_service.terminal_service,
        "get_terminal",
        lambda *_args: {"lifecycle": "running", "status": TerminalStatus.IDLE.value},
    )
    monkeypatch.setattr(workflow_service.terminal_service, "send_input", send)
    assert inbox_service.reconcile_provider_execution_queue() == 1
    assert send.call_count == 1
    assert send.call_args.args[1].endswith("deliver after exact reconnect")


def _pending_inbox_turn(root: str, message: str) -> tuple[database.InboxMessage, int]:
    """Bind one ordinary PENDING transport to the root's current OPEN workflow."""
    inbox = create_inbox_message("synthetic-sender", root, message)
    turn_id = database.ensure_workflow_turn_for_inbox(inbox.id)
    assert turn_id is not None
    return inbox, turn_id


def _complete_ready_test_reconnect(root: str, turn_id: int) -> None:
    reconnect = database.claim_workflow_provider_reconnect(root)
    assert reconnect is not None and reconnect.get("exhausted") is not True
    with database.SessionLocal() as db:
        attempt = (
            db.query(WorkflowProviderReconnectAttemptModel)
            .filter_by(attempt_token=reconnect["attempt_token"])
            .one()
        )
        attempt.state = database.PROVIDER_RECONNECT_READY
        attempt.output_log_device = 1
        attempt.output_log_inode = 2
        attempt.output_log_offset = 3
        attempt.output_boundary_at = datetime.now()
        db.commit()
    assert database.complete_workflow_provider_reconnect(
        root,
        turn_id,
        reconnect["claim_token"],
        reconnect["attempt_token"],
    )


def _queued_authoritative_handoff_result(
    parent: str,
    child: str,
    body: str = "immutable reviewer verdict",
    *,
    resumed_child: bool = False,
) -> tuple[int, database.InboxMessage]:
    """Build the exact current queued callback authority used by #116."""
    parent_turn = _start_admitted_input(parent)
    request_effect = claim_workflow_effect(parent, parent_turn, "handoff", child)
    assert request_effect is not None
    _ensure_running_test_terminal(child)
    auth_token = f"{child}-authenticated-v1-token"
    if resumed_child:
        with database.SessionLocal() as db:
            terminal = db.get(TerminalModel, child)
            terminal.auth_token_sha256 = hashlib.sha256(auth_token.encode()).hexdigest()
            db.commit()
    assert register_handoff_child(
        parent,
        child,
        workflow_turn_id=parent_turn,
        workflow_effect_id=request_effect["id"],
        request_message=f"review {child}",
    )
    child_binding = issue_workflow_input_binding(child)
    assert child_binding is not None
    child_turn = resolve_workflow_input_binding(child, child_binding)
    assert child_turn is not None
    assert bind_child_assignment_input_turn(child, child_binding)
    child_receipt = claim_or_resume_workflow_turn_receipt(child, child_turn)
    assert child_receipt["accepted"] is True
    if resumed_child:
        resumed = claim_or_resume_workflow_turn_receipt(
            child,
            child_turn,
            resume_token=child_receipt["resume_token"],
        )
        assert resumed["accepted"] is True and resumed["resumed"] is True
        submission = submit_handoff_result_v1(
            auth_token,
            resumed["logical_turn_id"],
            HandoffResultDocumentV1(
                format="v1",
                summary="resumed child complete",
                body_markdown=body,
                changed_files=[],
                checks=[],
                risks=[],
                blockers=[],
            ),
        )
        assert submission["accepted"] is True and submission["duplicate"] is False
        notice = get_handoff_child_result_message(child)
        assert notice is not None and notice.result_id == submission["result_id"]
    else:
        notice, duplicate = create_handoff_child_result_message(child, body)
        assert notice is not None and duplicate is False and notice.result_id is not None
    assert finish_workflow_effect(
        parent,
        request_effect["id"],
        request_effect["claim_token"],
        "completed",
    )
    callback = get_workflow_turn_for_inbox(notice.id)
    assert callback is not None
    now = datetime(2026, 9, 3, 12, 0, 0)
    claim = claim_handoff_result_batch_for_inbox(notice.id, now=now)
    assert isinstance(claim, dict)
    assert requeue_workflow_turn(
        claim["id"],
        claim["claim_token"],
        claim["claim_generation"],
        now=now,
        admission_reason_code="TERMINAL_RUNTIME_RECONNECT_PENDING",
    )
    return claim["id"], notice


@pytest.mark.parametrize("resumed_child", [False, True], ids=["bound-child", "resumed-child-v1"])
@patch("cli_agent_orchestrator.services.workflow_service.terminal_service")
def test_f13_active_queued_handoff_result_reconnects_then_delivers_same_turn_once(
    mock_terminal, workflow_db, monkeypatch, resumed_child
):
    """Provider 0/N recovers one exact callback without fabricated admission."""
    root, child = "root-queued-result-reconnect", "child-queued-result-reconnect"
    turn_id, notice = _queued_authoritative_handoff_result(root, child, resumed_child=resumed_child)
    with database.SessionLocal() as db:
        db.add(
            database.CapacitySettingsModel(
                id=1,
                max_resident_supervisors=6,
                max_provider_executions=3,
                max_work_contexts=6,
                max_heavy_execution_slots=1,
            )
        )
        result = db.get(database.DelegationResultModel, notice.result_id)
        immutable_before = (
            result.id,
            result.document_json,
            result.content_sha256,
            result.content_bytes,
        )
        initial_turn_count = db.query(WorkflowTurnModel).count()
        assert db.query(database.ProviderExecutionLeaseModel).count() == 0
        db.commit()

    provider = MagicMock()
    provider.get_status.return_value = TerminalStatus.IDLE
    provider.is_process_alive.return_value = True
    provider.runtime_sidecar_reconnect_required.side_effect = [True, False]
    monkeypatch.setattr(inbox_service.provider_manager, "get_provider", lambda *_: provider)
    send = MagicMock(return_value=True)
    monkeypatch.setattr(inbox_service.terminal_service, "send_input", send)
    mock_terminal.get_terminal.return_value = {
        "status": TerminalStatus.COMPLETED.value,
        "lifecycle": "running",
    }
    mock_terminal.provider_runtime_sidecar_reconnect_required.return_value = False
    mock_terminal.provider_turn_execution_active.return_value = False
    mock_terminal.provider_turn_outcome.return_value = None

    def reconnect_request(
        terminal_id,
        requested_turn_id,
        resume_identity,
        *,
        registry,
        claim_token,
        attempt_token,
        attempt_state,
        side_effect_guard,
    ):
        assert terminal_id == root
        assert requested_turn_id == turn_id
        assert resume_identity == TEST_CODEX_RESUME_IDENTITY
        assert registry is None
        assert attempt_state == "reserved"
        assert side_effect_guard()
        assert database.mark_workflow_provider_reconnect_launch_dispatched(
            root, turn_id, claim_token, attempt_token
        )
        assert database.record_workflow_provider_reconnect_runtime_ready(
            root, attempt_token, ACTIVE_RUNTIME_GENERATION, 4321, 987654
        )
        assert database.record_workflow_provider_reconnect_output_boundary(
            root, attempt_token, 11, 22, 333
        )

    mock_terminal.request_provider_runtime_sidecar_reconnect.side_effect = reconnect_request

    assert inbox_service.reconcile_provider_execution_queue() == 0
    assert mock_terminal.request_provider_runtime_sidecar_reconnect.call_count == 1
    send.assert_not_called()
    with database.SessionLocal() as db:
        turn = db.get(WorkflowTurnModel, turn_id)
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=root).one()
        attempt = db.query(WorkflowProviderReconnectAttemptModel).one()
        assert workflow.active_turn_id == turn_id
        assert turn.state == "queued"
        assert turn.provider_reconnect_requested_at is None
        assert attempt.workflow_turn_id == turn_id
        assert attempt.state == "succeeded"
        assert db.query(WorkflowTurnReceiptModel).filter_by(workflow_turn_id=turn_id).count() == 0

    assert inbox_service.reconcile_provider_execution_queue() == 1
    assert send.call_count == 1
    assert f"[CAO workflow input: logical-turn={turn_id}]" in send.call_args.args[1]
    assert claim_workflow_turn_receipt(root, turn_id)
    assert not claim_workflow_turn_receipt(root, turn_id)
    assert inbox_service.reconcile_provider_execution_queue() == 0
    with database.SessionLocal() as db:
        result = db.get(database.DelegationResultModel, notice.result_id)
        immutable_after = (
            result.id,
            result.document_json,
            result.content_sha256,
            result.content_bytes,
        )
        assert immutable_after == immutable_before
        assert db.query(WorkflowTurnModel).count() == initial_turn_count
        assert db.get(WorkflowTurnModel, turn_id).state == "sent"
        assert db.get(InboxModel, notice.id).status == database.MessageStatus.DELIVERED.value
        assert db.query(database.ProviderExecutionLeaseModel).count() == 0


def test_f13_queued_result_reconnect_provenance_fails_closed(workflow_db):
    roots = {
        "ordinary": ("root-reconnect-ordinary", "child-reconnect-ordinary"),
        "wrong_result": ("root-reconnect-wrong-result", "child-reconnect-wrong-result"),
        "superseded": ("root-reconnect-superseded", "child-reconnect-superseded"),
        "newer_receipt": ("root-reconnect-newer-receipt", "child-reconnect-newer-receipt"),
        "unrelated_child": (
            "root-reconnect-unrelated-child",
            "child-reconnect-unrelated-child",
        ),
        "closed": ("root-reconnect-closed", "child-reconnect-closed"),
    }

    ordinary_root, _ = roots["ordinary"]
    _start_admitted_input(ordinary_root)
    _inbox, ordinary_turn = _pending_inbox_turn(ordinary_root, "arbitrary queued input")
    assert activate_workflow_turn_for_inbox(_inbox.id) == ordinary_turn
    ordinary_claim = claim_workflow_turn(ordinary_root, inbox_message_id=_inbox.id)
    assert ordinary_claim is not None
    assert requeue_workflow_turn(
        ordinary_turn,
        ordinary_claim["claim_token"],
        ordinary_claim["claim_generation"],
        admission_reason_code="TERMINAL_RUNTIME_RECONNECT_PENDING",
    )

    prepared = {}
    for case in ("wrong_result", "superseded", "newer_receipt", "unrelated_child", "closed"):
        root, child = roots[case]
        prepared[case] = _queued_authoritative_handoff_result(root, child)
    with database.SessionLocal() as db:
        wrong_turn, wrong_notice = prepared["wrong_result"]
        wrong_result = db.get(database.DelegationResultModel, wrong_notice.result_id)
        wrong_result.parent_workflow_id = None
        superseded_turn, _ = prepared["superseded"]
        db.get(WorkflowTurnModel, superseded_turn).superseded_at = datetime.now()
        newer_turn, _ = prepared["newer_receipt"]
        newer_workflow = (
            db.query(WorkflowModel).filter_by(root_terminal_id=roots["newer_receipt"][0]).one()
        )
        newer_authority = WorkflowTurnModel(
            workflow_id=newer_workflow.id,
            kind="execution_resume",
            dedupe_key="newer-reconnect-authority",
            state="sent",
        )
        db.add(newer_authority)
        db.flush()
        db.add(
            WorkflowTurnReceiptModel(
                workflow_turn_id=newer_authority.id,
                receiver_terminal_id=roots["newer_receipt"][0],
            )
        )
        assert newer_authority.id > newer_turn
        unrelated_turn, unrelated_notice = prepared["unrelated_child"]
        unrelated_result = db.get(database.DelegationResultModel, unrelated_notice.result_id)
        unrelated_assignment = db.get(
            database.ChildAssignmentModel, unrelated_result.child_assignment_id
        )
        child_workflow = db.get(WorkflowModel, unrelated_assignment.child_workflow_id)
        unrelated_authority = WorkflowTurnModel(
            workflow_id=child_workflow.id,
            kind="external_input",
            dedupe_key="unrelated-child-current-authority",
            state="sent",
        )
        db.add(unrelated_authority)
        db.flush()
        db.add(
            WorkflowTurnReceiptModel(
                workflow_turn_id=unrelated_authority.id,
                receiver_terminal_id=roots["unrelated_child"][1],
            )
        )
        child_workflow.active_turn_id = unrelated_authority.id
        assert unrelated_authority.id != unrelated_turn
        closed_root, _ = roots["closed"]
        db.query(WorkflowModel).filter_by(root_terminal_id=closed_root).one().status = "terminal"
        db.commit()

    assert not database.request_workflow_provider_reconnect(ordinary_root)
    assert not database.request_workflow_provider_reconnect(roots["wrong_result"][0])
    assert not database.request_workflow_provider_reconnect(roots["superseded"][0])
    assert not database.request_workflow_provider_reconnect(roots["newer_receipt"][0])
    assert not database.request_workflow_provider_reconnect(roots["unrelated_child"][0])
    assert not database.request_workflow_provider_reconnect(roots["closed"][0])
    with database.SessionLocal() as db:
        for case, (root, _child) in roots.items():
            workflow = db.query(WorkflowModel).filter_by(root_terminal_id=root).one()
            turn = db.get(WorkflowTurnModel, workflow.active_turn_id)
            assert turn.provider_reconnect_requested_at is None


def test_f13_queued_result_reconnect_claim_is_single_and_processing_fenced(workflow_db):
    root, child = "root-reconnect-queued-race", "child-reconnect-queued-race"
    turn_id, _notice = _queued_authoritative_handoff_result(root, child)
    assert database.acquire_provider_execution(root, turn_id, 3)
    assert database.request_workflow_provider_reconnect(root)
    assert database.claim_workflow_provider_reconnect(root) is None
    assert database.release_provider_execution(root, turn_id)
    assert database.get_terminal_execution_wait_reason(root) == "runtime_recovery"

    now = datetime(2026, 9, 3, 12, 30, 0)
    barrier = Barrier(2)

    def claim_reconnect():
        barrier.wait()
        return database.claim_workflow_provider_reconnect(root, now=now)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(lambda _index: claim_reconnect(), range(2)))

    admitted = [claim for claim in claims if claim is not None]
    assert len(admitted) == 1
    assert admitted[0]["turn_id"] == turn_id
    first_attempt_token = admitted[0]["attempt_token"]
    workflow_db.dispose()
    assert (
        database.claim_workflow_provider_reconnect(
            root,
            now=now + timedelta(seconds=database.WORKFLOW_PROVIDER_RECONNECT_LEASE_SECONDS - 1),
        )
        is None
    )
    reclaimed = database.claim_workflow_provider_reconnect(
        root,
        now=now + timedelta(seconds=database.WORKFLOW_PROVIDER_RECONNECT_LEASE_SECONDS + 1),
    )
    assert reclaimed is not None
    assert reclaimed["attempt_token"] == first_attempt_token
    assert reclaimed["attempt_state"] == "reserved"
    with database.SessionLocal() as db:
        attempts = db.query(WorkflowProviderReconnectAttemptModel).all()
        assert len(attempts) == 1
        assert attempts[0].workflow_turn_id == turn_id
        assert attempts[0].state == "reserved"


def test_f13_newer_input_does_not_overtake_reconnecting_queued_result(workflow_db):
    root, child = "root-reconnect-result-fifo", "child-reconnect-result-fifo"
    turn_id, notice = _queued_authoritative_handoff_result(root, child)
    assert database.request_workflow_provider_reconnect(root)
    reconnect = database.claim_workflow_provider_reconnect(root)
    assert reconnect is not None and reconnect.get("exhausted") is not True

    prepared = database.prepare_workflow_input(
        root,
        "newer input must remain behind immutable callback",
        request_id="queued-result-reconnect-fifo",
        require_live_terminal=True,
    )
    assert prepared["queued"] is True
    assert database.mark_workflow_provider_reconnect_launch_dispatched(
        root,
        turn_id,
        reconnect["claim_token"],
        reconnect["attempt_token"],
    )
    assert database.record_workflow_provider_reconnect_runtime_ready(
        root, reconnect["attempt_token"], ACTIVE_RUNTIME_GENERATION, 4321, 987654
    )
    assert database.record_workflow_provider_reconnect_output_boundary(
        root, reconnect["attempt_token"], 11, 22, 333
    )
    assert database.complete_workflow_provider_reconnect(
        root,
        turn_id,
        reconnect["claim_token"],
        reconnect["attempt_token"],
    )
    assert database.get_terminal_execution_wait_reason(root) == "workflow_continuation"

    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=root).one()
        callback = db.get(WorkflowTurnModel, turn_id)
        successor = db.get(WorkflowTurnModel, prepared["turn_id"])
        assert workflow.active_turn_id == turn_id
        assert callback.state == "queued"
        assert successor.state == "queued"
        assert db.get(InboxModel, notice.id).status == database.MessageStatus.PENDING.value
        assert db.query(WorkflowTurnReceiptModel).filter_by(workflow_turn_id=turn_id).count() == 0

    claim = claim_handoff_result_batch_for_inbox(notice.id)
    assert isinstance(claim, dict)
    assert claim["id"] == turn_id


@pytest.mark.parametrize("deferred_relation", ["assigned_child", "handoff_child"])
def test_reconnect_fifo_external_head_is_not_hidden_by_later_inbox(
    workflow_db, monkeypatch, deferred_relation
):
    root = "root-reconnect-external-before-inbox"
    predecessor = _start_admitted_input(root)
    assert isinstance(observe_workflow_final(root), int)
    assert database.request_workflow_provider_reconnect(root)
    external = database.prepare_workflow_input(
        root,
        "Composer FIFO head",
        request_id="reconnect-external-before-inbox",
        require_live_terminal=True,
    )
    inbox, inbox_turn = _pending_inbox_turn(root, "later Inbox input")

    _complete_ready_test_reconnect(root, predecessor)

    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=root).one()
        assert workflow.active_turn_id == external["turn_id"]
    assert database.get_provider_execution_admission_queue() == [
        {
            "source": "workflow",
            "terminal_id": root,
            "created_at": database.get_provider_execution_admission_queue()[0]["created_at"],
            "source_id": external["turn_id"],
        }
    ]

    send = MagicMock(return_value=True)
    monkeypatch.setattr(
        workflow_service.terminal_service,
        "get_terminal",
        lambda *_args: {"lifecycle": "running", "status": TerminalStatus.IDLE.value},
    )
    monkeypatch.setattr(workflow_service.terminal_service, "send_input", send)
    if deferred_relation == "assigned_child":
        monkeypatch.setattr(
            workflow_service, "get_parent_completion_barrier", lambda *_args: (1, 0)
        )
    else:
        monkeypatch.setattr(
            workflow_service, "get_parent_completion_barrier", lambda *_args: (0, 0)
        )
        monkeypatch.setattr(
            workflow_service,
            "get_handoff_child_status",
            lambda *_args: "handoff_awaiting_result",
        )
    assert inbox_service.reconcile_provider_execution_queue() == 1
    assert send.call_count == 1
    assert send.call_args.args[1].endswith("Composer FIFO head")
    assert claim_workflow_turn_receipt(root, external["turn_id"])
    assert activate_workflow_turn_for_inbox(inbox.id) == inbox_turn


def test_reconnect_fifo_inbox_head_remains_ahead_of_later_external(workflow_db):
    root = "root-reconnect-inbox-before-external"
    predecessor = _start_admitted_input(root)
    assert isinstance(observe_workflow_final(root), int)
    assert database.request_workflow_provider_reconnect(root)
    inbox = create_inbox_message("synthetic-sender", root, "Inbox FIFO head")
    assert get_workflow_turn_for_inbox(inbox.id) is None
    database.prepare_workflow_input(
        root,
        "later Composer input",
        request_id="reconnect-inbox-before-external",
        require_live_terminal=True,
    )

    _complete_ready_test_reconnect(root, predecessor)

    materialized = get_workflow_turn_for_inbox(inbox.id)
    assert materialized is not None
    inbox_turn = materialized["turn_id"]
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=root).one()
        assert workflow.active_turn_id == inbox_turn
    assert database.get_provider_execution_admission_queue()[0] == {
        "source": "inbox",
        "terminal_id": root,
        "created_at": database.get_provider_execution_admission_queue()[0]["created_at"],
        "source_id": inbox.id,
    }


def _admit_sent_continuation(root: str, turn: dict, now: datetime) -> None:
    """Model transport activation, receiver admission, and sender ack in order."""
    assert activate_workflow_turn(root, turn["id"])
    assert claim_workflow_turn_receipt(root, turn["id"], now=now)
    assert mark_workflow_turn_sent(
        turn["id"], turn["claim_token"], turn["claim_generation"], now=now
    )


def _workflow_state_bytes(root: str) -> bytes:
    """Stable test-only state image for the no-mutation defer invariant."""
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=root).one()
        turns = (
            db.query(WorkflowTurnModel)
            .filter_by(workflow_id=workflow.id)
            .order_by(WorkflowTurnModel.id)
            .all()
        )
        receipts = (
            db.query(WorkflowTurnReceiptModel)
            .filter(WorkflowTurnReceiptModel.workflow_turn_id.in_([turn.id for turn in turns]))
            .order_by(WorkflowTurnReceiptModel.id)
            .all()
        )
        image = {
            "workflow": {
                "status": workflow.status,
                "active_turn_id": workflow.active_turn_id,
                "no_progress_count": workflow.no_progress_count,
                "terminal_reason": workflow.terminal_reason,
                "updated_at": str(workflow.updated_at),
            },
            "turns": [
                {
                    "id": turn.id,
                    "state": turn.state,
                    "dedupe_key": turn.dedupe_key,
                    "attempt_count": turn.attempt_count,
                    "claim_generation": turn.claim_generation,
                    "claim_token": turn.claim_token,
                    "claim_expires_at": str(turn.claim_expires_at),
                    "not_before": str(turn.not_before),
                    "updated_at": str(turn.updated_at),
                }
                for turn in turns
            ],
            "receipts": [
                {
                    "workflow_turn_id": receipt.workflow_turn_id,
                    "receiver_terminal_id": receipt.receiver_terminal_id,
                    "consumed_at": str(receipt.consumed_at),
                }
                for receipt in receipts
            ],
        }
    return json.dumps(image, sort_keys=True, separators=(",", ":")).encode()


def test_f13_ready_observation_columns_migrate_additively(tmp_path, monkeypatch):
    database_file = tmp_path / "legacy-workflow.db"
    with sqlite3.connect(database_file) as connection:
        connection.execute("CREATE TABLE workflows (id INTEGER PRIMARY KEY)")
        connection.execute(
            "CREATE TABLE workflow_turns ("
            "id INTEGER PRIMARY KEY, claim_generation INTEGER NOT NULL DEFAULT 0, "
            "claim_token TEXT, claim_expires_at DATETIME, transport_binding TEXT)"
        )
        connection.execute(
            "CREATE TABLE workflow_turn_receipts ("
            "id INTEGER PRIMARY KEY, workflow_turn_id INTEGER NOT NULL, "
            "receiver_terminal_id TEXT NOT NULL, consumed_at DATETIME NOT NULL)"
        )
        connection.execute("CREATE TABLE inbox (id INTEGER PRIMARY KEY)")
    monkeypatch.setattr(constants, "DATABASE_FILE", database_file)

    database._migrate_workflow_turn_columns()
    database._migrate_inbox_result_columns()

    with sqlite3.connect(database_file) as connection:
        workflow_columns = {row[1] for row in connection.execute("PRAGMA table_info(workflows)")}
        columns = {row[1] for row in connection.execute("PRAGMA table_info(workflow_turns)")}
        receipt_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(workflow_turn_receipts)")
        }
        inbox_columns = {row[1] for row in connection.execute("PRAGMA table_info(inbox)")}
    assert "resumed_from_owner_gate_workflow_id" in workflow_columns
    assert "queue_reason" in columns
    assert "provider_processing_observed_at" in columns
    assert "provider_ready_observed_at" in columns
    assert "provider_outcome_code" in columns
    assert "provider_outcome_detail" in columns
    assert "provider_outcome_observed_at" in columns
    assert "provider_outcome_cursor" in columns
    assert "provider_outcome_cursor_bootstrap_generation" in columns
    assert "dispatch_recovery_count" in columns
    assert "provider_reconnect_requested_at" in columns
    assert "provider_reconnect_claim_token" in columns
    assert "provider_reconnect_resume_identity" in columns
    assert "resume_parent_turn_id" in columns
    assert "resume_token_sha256" in receipt_columns
    assert "resumed_by_turn_id" in receipt_columns
    assert "resumed_at" in receipt_columns
    assert "superseded_by_turn_id" in columns
    assert "superseded_at" in columns
    assert "callback_reconciled_at" in inbox_columns
    assert "callback_reconciled_from_turn_id" in inbox_columns


def _queue_inbox_workflow_turn(root: str, key: str = "inbox-result") -> tuple[int, int]:
    """Build one real PENDING Inbox row paired to one queued workflow turn."""
    _ensure_running_test_terminal(root)
    inbox = create_inbox_message("child-inbox", root, "durable Inbox B")
    turn_id, duplicate = queue_workflow_turn(
        root,
        "assigned_result",
        key,
        payload=inbox.message,
        inbox_message_id=inbox.id,
    )
    assert turn_id is not None and duplicate is False
    return inbox.id, turn_id


def test_f13_no_child_open_final_queues_one_safe_continuation(workflow_db):
    _start_admitted_input("root-no-child")
    now = datetime(2026, 8, 9, 12, 0, 0)

    first = observe_workflow_final("root-no-child", now=now)
    second = observe_workflow_final("root-no-child", now=now)
    claimed = claim_workflow_turn("root-no-child", now=now)

    assert first == second
    assert claimed is not None and claimed["kind"] == "open_final"
    with database.SessionLocal() as db:
        assert db.query(WorkflowTurnModel).count() == 2


def test_f13_provider_content_unavailable_is_durable_without_automatic_successor(workflow_db):
    root = "root-provider-content-unavailable"
    turn_id = _start_admitted_input(root)
    cursor = _bind_policy_cursor(root, turn_id)
    effect = claim_workflow_effect(root, turn_id, "send_message", "committed-before-policy")
    assert effect is not None
    assert finish_workflow_effect(root, effect["id"], effect["claim_token"], "completed")
    now = datetime(2026, 8, 28, 12, 0, 0)

    assert observe_workflow_provider_outcome(
        root,
        turn_id,
        cursor,
        "PROVIDER_CONTENT_UNAVAILABLE",
        "cyber_policy",
        now=now,
    )
    assert observe_workflow_provider_outcome(
        root,
        turn_id,
        cursor,
        "PROVIDER_CONTENT_UNAVAILABLE",
        "cyber_policy",
        now=now,
    )

    assert get_workflow_provider_outcome(root) == {
        "code": "PROVIDER_CONTENT_UNAVAILABLE",
        "detail_code": "cyber_policy",
    }
    assert claim_workflow_turn(root, now=now) is None
    projection = database.get_terminal_workflow_projection(root)
    assert projection["state"] == "recoverable"
    assert projection["provider_outcome_code"] == "PROVIDER_CONTENT_UNAVAILABLE"
    with database.SessionLocal() as db:
        turn = db.get(WorkflowTurnModel, turn_id)
        assert turn is not None and turn.state == "finished"
        assert db.query(WorkflowTurnModel).count() == 1
        assert db.query(WorkflowEffectModel).count() == 1


def test_f13_policy_before_receipt_finishes_without_granting_effect_authority(workflow_db):
    root = "root-policy-before-receipt"
    _ensure_running_test_terminal(root)
    turn_id = start_workflow_input(root)
    assert turn_id is not None
    cursor = _bind_policy_cursor(root, turn_id)

    assert observe_workflow_provider_outcome(
        root,
        turn_id,
        cursor,
        "PROVIDER_CONTENT_UNAVAILABLE",
        "cyber_policy",
    )
    assert claim_workflow_effect(root, turn_id, "send_message", "never-admitted") is None
    assert claim_workflow_turn(root) is None
    with database.SessionLocal() as db:
        assert db.query(WorkflowTurnReceiptModel).count() == 0
        assert db.query(WorkflowEffectModel).count() == 0
        assert db.query(WorkflowTurnModel).count() == 1

    first = database.prepare_workflow_input(
        root,
        "owner-authored continuation",
        request_id="policy-continuation-1",
        require_live_terminal=True,
    )
    duplicate = database.prepare_workflow_input(
        root,
        "owner-authored continuation",
        request_id="policy-continuation-1",
        require_live_terminal=True,
    )
    assert first is not None and first["accepted"] is True and first["duplicate"] is False
    assert duplicate is not None and duplicate["turn_id"] == first["turn_id"]
    assert duplicate["duplicate"] is True
    assert get_workflow_provider_outcome(root) is None
    assert claim_workflow_turn_receipt(root, first["turn_id"])
    assert not claim_workflow_turn_receipt(root, first["turn_id"])
    continuation_effect = claim_workflow_effect(
        root, first["turn_id"], "send_message", "continuation-effect"
    )
    assert continuation_effect is not None
    assert (
        claim_workflow_effect(root, first["turn_id"], "send_message", "continuation-effect") is None
    )
    with database.SessionLocal() as db:
        assert db.query(WorkflowTurnModel).count() == 2
        assert db.query(WorkflowEffectModel).count() == 1


@patch("cli_agent_orchestrator.services.workflow_service.terminal_service")
def test_f13_reconciler_persists_policy_outcome_and_never_sends_automatic_retry(
    mock_terminal, workflow_db
):
    root = "root-policy-reconcile"
    turn_id = _start_admitted_input(root)
    _bind_policy_cursor(root, turn_id)
    mock_terminal.get_terminal.return_value = {
        "status": TerminalStatus.COMPLETED.value,
        "lifecycle": "running",
    }
    mock_terminal.provider_runtime_sidecar_reconnect_required.return_value = False
    mock_terminal.provider_turn_outcome.return_value = ProviderTurnOutcome(
        code="PROVIDER_CONTENT_UNAVAILABLE", detail_code="cyber_policy"
    )

    assert workflow_service.reconcile_root_workflow(root) is False
    assert workflow_service.reconcile_root_workflow(root) is False

    assert get_workflow_provider_outcome(root) is not None
    mock_terminal.send_input.assert_not_called()
    with database.SessionLocal() as db:
        assert db.query(WorkflowTurnModel).count() == 1


@patch("cli_agent_orchestrator.services.workflow_service.terminal_service")
def test_f13_stale_policy_cannot_finish_composer_turn_before_physical_transport(
    mock_terminal, workflow_db
):
    root = "root-policy-composer-interleave"
    prior_turn_id = _start_admitted_input(root)
    prior_cursor = _bind_policy_cursor(root, prior_turn_id)
    assert observe_workflow_provider_outcome(
        root,
        prior_turn_id,
        prior_cursor,
        "PROVIDER_CONTENT_UNAVAILABLE",
        "cyber_policy",
    )

    prepared = database.prepare_workflow_input(
        root,
        "deliberate owner continuation",
        request_id="policy-interleave-1",
        require_live_terminal=True,
    )
    assert prepared is not None and prepared["queued"] is False
    continuation_turn_id = prepared["turn_id"]
    mock_terminal.get_terminal.return_value = {
        "status": TerminalStatus.COMPLETED.value,
        "lifecycle": "running",
    }
    mock_terminal.provider_runtime_sidecar_reconnect_required.return_value = False
    mock_terminal.provider_turn_outcome.return_value = ProviderTurnOutcome(
        code="PROVIDER_CONTENT_UNAVAILABLE", detail_code="cyber_policy"
    )

    assert workflow_service.reconcile_root_workflow(root) is False
    mock_terminal.provider_turn_outcome.assert_not_called()
    assert get_workflow_provider_outcome(root) is None
    assert claim_workflow_turn_receipt(root, continuation_turn_id)
    assert not claim_workflow_turn_receipt(root, continuation_turn_id)
    with database.SessionLocal() as db:
        turn = db.get(WorkflowTurnModel, continuation_turn_id)
        assert turn is not None
        assert turn.state == "sent"
        assert turn.provider_outcome_code is None
        assert turn.provider_outcome_cursor is None


def test_f13_provider_outcome_cas_rejects_newer_active_turn(workflow_db):
    root = "root-provider-outcome-cas"
    prior_turn_id = _start_admitted_input(root)
    prior_cursor = _bind_policy_cursor(root, prior_turn_id)
    observation = get_workflow_provider_outcome_observation(root)
    assert observation == {"turn_id": prior_turn_id, "cursor": prior_cursor}
    prepared = database.prepare_workflow_input(
        root, "new turn", request_id="provider-outcome-cas-1", require_live_terminal=True
    )
    assert prepared is not None and prepared["turn_id"] != prior_turn_id

    assert not observe_workflow_provider_outcome(
        root,
        observation["turn_id"],
        observation["cursor"],
        "PROVIDER_CONTENT_UNAVAILABLE",
        "cyber_policy",
    )
    assert get_workflow_provider_outcome(root) is None


def test_f13_provider_outcome_cursor_is_rebound_after_transport_retry(workflow_db):
    root = "root-provider-outcome-retry"
    _ensure_running_test_terminal(root)
    turn_id = start_workflow_input(root)
    assert turn_id is not None
    _bind_policy_cursor(root, turn_id)
    assert database.queue_workflow_input_for_provider(root, turn_id, "retry payload")
    with database.SessionLocal() as db:
        assert db.get(WorkflowTurnModel, turn_id).provider_outcome_cursor is None

    claimed = claim_workflow_turn(root)
    assert claimed is not None and claimed["id"] == turn_id
    assert bind_workflow_turn_provider_outcome_cursor(root, turn_id, "codex-jsonl-v1:4:5:6:1")
    assert requeue_workflow_turn(turn_id, claimed["claim_token"], claimed["claim_generation"])
    with database.SessionLocal() as db:
        turn = db.get(WorkflowTurnModel, turn_id)
        assert turn.state == "queued"
        assert turn.provider_outcome_cursor is None


def test_f13_stale_compacted_turn_rejection_keeps_open_workflow_moving_once(workflow_db):
    """A compacted provider replay is inert; CAO owns the one fresh successor."""
    root = "root-compacted-stale-turn"
    stale_turn_id = _start_admitted_input(root)
    now = datetime(2026, 8, 22, 18, 0, 0)

    assert claim_workflow_turn_receipt(root, stale_turn_id, now=now) is False
    assert get_workflow_status(root) == "open"
    successor_id = observe_workflow_final(root, now=now)
    claimed = claim_workflow_turn(root, now=now)

    assert successor_id is not None
    assert claimed is not None and claimed["id"] == successor_id
    _admit_sent_continuation(root, claimed, now)
    assert claim_workflow_turn_receipt(root, successor_id, now=now) is False
    assert get_workflow_status(root) == "open"
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=root).one()
        assert (
            db.query(WorkflowTurnModel)
            .filter_by(workflow_id=workflow.id, kind="open_final")
            .count()
            == 1
        )


def test_f13_unadmitted_active_final_is_byte_stable_and_cannot_cascade(workflow_db):
    """Repeated stale finals do not rewrite the active SENT turn or queue work."""
    root = "root-unadmitted-final"
    start_workflow_input(root)
    now = datetime(2026, 8, 11, 12, 0, 0)
    before = _workflow_state_bytes(root)

    assert observe_workflow_final(root, now=now) == DEFER_UNADMITTED
    assert observe_workflow_final(root, now=now + timedelta(seconds=1)) == DEFER_UNADMITTED
    assert claim_workflow_turn(root, now=now + timedelta(seconds=2)) is None
    assert _workflow_state_bytes(root) == before


@patch("cli_agent_orchestrator.services.inbox_service.terminal_service.send_input")
@patch("cli_agent_orchestrator.services.inbox_service.provider_manager.get_provider")
def test_f13_unadmitted_active_turn_defers_queued_inbox_without_mutation_then_sends_once(
    mock_provider, mock_send, workflow_db
):
    """A queued Inbox B cannot steal an unadmitted SENT A, then reuses B once."""
    root = "root-inbox-unadmitted-fence"
    active_a = start_workflow_input(root)
    assert active_a is not None
    inbox_b, turn_b = _queue_inbox_workflow_turn(root)
    provider = MagicMock()
    provider.get_status.return_value = TerminalStatus.IDLE
    mock_provider.return_value = provider
    before = _workflow_state_bytes(root)

    assert check_and_send_pending_messages(root) is False
    assert check_and_send_pending_messages(root) is False
    assert mock_send.call_count == 0
    assert _workflow_state_bytes(root) == before
    with database.SessionLocal() as db:
        assert db.query(InboxModel).filter_by(id=inbox_b).one().status == "pending"
        assert db.query(WorkflowTurnModel).filter_by(id=turn_b).one().state == "queued"
        assert db.query(WorkflowEffectModel).count() == 0

    assert claim_workflow_turn_receipt(root, active_a)
    assert isinstance(observe_workflow_final(root), int)
    assert check_and_send_pending_messages(root) is True
    assert check_and_send_pending_messages(root) is False
    assert mock_send.call_count == 1
    with database.SessionLocal() as db:
        assert db.query(InboxModel).filter_by(id=inbox_b).one().status == "delivered"
        assert db.query(WorkflowTurnModel).filter_by(id=turn_b).one().state == "sent"
    assert claim_workflow_turn_receipt(root, turn_b)
    assert not claim_workflow_turn_receipt(root, turn_b)


def test_f13_inbox_activation_receipt_race_never_rebinds_without_a_receipt(tmp_path, monkeypatch):
    """Receipt-vs-activation leaves B queued on defer or admits exactly that B."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'inbox-receipt-race.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(database, "_child_assignment_schema_ready", True)
    root = "root-inbox-receipt-race"
    active_a = start_workflow_input(root)
    assert active_a is not None
    inbox_b, turn_b = _queue_inbox_workflow_turn(root)

    with ThreadPoolExecutor(max_workers=2) as executor:
        activation, admitted = list(
            executor.map(
                lambda operation: (
                    activate_workflow_turn_for_inbox(inbox_b)
                    if operation == "activate"
                    else claim_workflow_turn_receipt(root, active_a)
                ),
                ("activate", "receipt"),
            )
        )

    assert admitted is True
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=root).one()
        inbox_turn = db.query(WorkflowTurnModel).filter_by(id=turn_b).one()
        if activation == DEFER_UNADMITTED:
            assert workflow.active_turn_id == active_a
            assert inbox_turn.state == "queued"
        else:
            assert activation == turn_b
            assert workflow.active_turn_id == turn_b
            assert inbox_turn.state == "queued"


@patch("cli_agent_orchestrator.services.inbox_service.terminal_service.send_input")
@patch("cli_agent_orchestrator.services.inbox_service.provider_manager.get_provider")
def test_f13_concurrent_inbox_ticks_claim_and_send_one_turn_once(
    mock_provider, mock_send, tmp_path, monkeypatch
):
    """Two ready watchers share the one queued Inbox turn and one transport send."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'inbox-concurrent-ticks.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(database, "_child_assignment_schema_ready", True)
    root = "root-inbox-concurrent-ticks"
    _start_admitted_input(root)
    inbox_b, turn_b = _queue_inbox_workflow_turn(root)
    assert isinstance(observe_workflow_final(root), int)
    provider = MagicMock()
    ready_barrier = Barrier(2)
    provider.get_status.side_effect = lambda: (
        ready_barrier.wait(),
        TerminalStatus.IDLE,
    )[1]
    mock_provider.return_value = provider

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(lambda _tick: _dispatch_pending_messages_with_admission(root), range(2))
        )

    assert outcomes.count(True) == 1
    assert mock_send.call_count == 1
    with database.SessionLocal() as db:
        assert db.query(InboxModel).filter_by(id=inbox_b).one().status == "delivered"
        assert db.query(WorkflowTurnModel).filter_by(id=turn_b).one().state == "sent"
        assert (
            db.query(WorkflowModel).filter_by(root_terminal_id=root).one().active_turn_id == turn_b
        )


@pytest.mark.parametrize("status", ("terminal", "owner_gate", "cancelled"))
def test_f13_closed_workflow_fences_queued_inbox_activation(workflow_db, status):
    """Owner/cancel terminal transitions leave no active Inbox delivery path."""
    root = "root-inbox-closed-fence"
    start_workflow_input(root)
    inbox_b, turn_b = _queue_inbox_workflow_turn(root)
    assert set_workflow_terminal_state(root, status, "closure decided")

    assert activate_workflow_turn_for_inbox(inbox_b) is None
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=root).one()
        assert workflow.status == status
        assert db.query(WorkflowTurnModel).filter_by(id=turn_b).one().state == "cancelled"


def test_f13_concurrent_admitted_final_creates_one_successor(tmp_path, monkeypatch):
    """The active SENT->FINISHED CAS is the single successor owner."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'workflow-final-cas.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(database, "_child_assignment_schema_ready", True)
    root = "root-concurrent-final"
    _start_admitted_input(root)
    now = datetime(2026, 8, 11, 12, 0, 0)

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _tick: observe_workflow_final(root, now=now), range(2)))

    successor_ids = {outcome for outcome in outcomes if isinstance(outcome, int)}
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=root).one()
        successors = (
            db.query(WorkflowTurnModel)
            .filter_by(workflow_id=workflow.id, dedupe_key=f"open-final:{workflow.active_turn_id}")
            .all()
        )
        active = db.query(WorkflowTurnModel).filter_by(id=workflow.active_turn_id).one()
        assert active.state == "finished"
        assert workflow.no_progress_count == 1
        assert len(successors) == 1
        assert successors[0].id in successor_ids


def test_f13_unadmitted_successor_final_cannot_create_a_second_successor(workflow_db):
    root = "root-unadmitted-successor"
    now = datetime(2026, 8, 11, 12, 0, 0)
    _start_admitted_input(root)
    first_successor = observe_workflow_final(root, now=now)
    assert isinstance(first_successor, int)
    claimed = claim_workflow_turn(root, now=now)
    assert claimed is not None and claimed["id"] == first_successor
    assert activate_workflow_turn(root, first_successor)
    assert mark_workflow_turn_sent(
        claimed["id"], claimed["claim_token"], claimed["claim_generation"], now=now
    )
    before = _workflow_state_bytes(root)

    assert observe_workflow_final(root, now=now + timedelta(seconds=1)) == DEFER_UNADMITTED
    assert claim_workflow_turn(root, now=now + timedelta(seconds=2)) is None
    assert _workflow_state_bytes(root) == before


def test_f13_finalizes_only_active_admitted_sent_turn(workflow_db):
    """An earlier inactive SENT row remains immutable workflow history."""
    root = "root-inactive-sent-history"
    historical = start_workflow_input(root)
    assert historical is not None
    active = start_workflow_input(root)
    assert active is not None and active != historical
    assert claim_workflow_turn_receipt(root, active)

    successor = observe_workflow_final(root, now=datetime(2026, 8, 11, 12, 0, 0))
    assert isinstance(successor, int)
    with database.SessionLocal() as db:
        assert db.query(WorkflowTurnModel).filter_by(id=historical).one().state == "sent"
        assert db.query(WorkflowTurnModel).filter_by(id=active).one().state == "finished"


def test_f13_restart_windows_keep_one_logical_successor_id_without_sleep(workflow_db):
    """Queued/claimed recovery and post-send receipt paths remain ID-stable."""
    root = "root-restart-windows"
    now = datetime(2026, 8, 11, 12, 0, 0)
    _start_admitted_input(root)
    successor_id = observe_workflow_final(root, now=now)
    assert isinstance(successor_id, int)

    # Queued restart: the finished active turn still points to this successor.
    assert observe_workflow_final(root, now=now) == successor_id
    queued_claim = claim_workflow_turn(root, now=now)
    assert queued_claim is not None and queued_claim["id"] == successor_id
    assert activate_workflow_turn(root, successor_id)

    # A live post-send/pre-ack claim cannot be displaced.  If its process dies,
    # fake-clock expiry returns the very same ID, not a new continuation.
    assert observe_workflow_final(root, now=now + timedelta(seconds=1)) == successor_id
    assert claim_workflow_turn(root, now=now + timedelta(seconds=1)) is None
    assert requeue_expired_workflow_turn_claims(now=now + timedelta(seconds=30)) == 1
    recovered = claim_workflow_turn(root, now=now + timedelta(seconds=30))
    assert recovered is not None and recovered["id"] == successor_id

    # Receiver admission before the late sender ack survives restart/replay;
    # after the ack its final produces exactly one next successor.
    assert claim_workflow_turn_receipt(root, successor_id, now=now + timedelta(seconds=30))
    assert mark_workflow_turn_sent(
        recovered["id"],
        recovered["claim_token"],
        recovered["claim_generation"],
        now=now + timedelta(seconds=30),
    )
    next_successor = observe_workflow_final(root, now=now + timedelta(seconds=31))
    assert isinstance(next_successor, int) and next_successor != successor_id

    # A later post-send ACK loss is an unadmitted SENT turn: it must defer and
    # fail closed rather than manufacture a third turn.
    next_claim = claim_workflow_turn(root, now=now + timedelta(seconds=32))
    assert next_claim is not None and next_claim["id"] == next_successor
    assert activate_workflow_turn(root, next_successor)
    assert mark_workflow_turn_sent(
        next_claim["id"],
        next_claim["claim_token"],
        next_claim["claim_generation"],
        now=now + timedelta(seconds=32),
    )
    assert observe_workflow_final(root, now=now + timedelta(seconds=33)) == DEFER_UNADMITTED
    assert claim_workflow_turn(root, now=now + timedelta(seconds=33)) is None


def test_f13_normal_boundary_handoff_result_is_one_inbox_and_one_turn(workflow_db):
    start_workflow_input("parent-boundary")
    register_handoff_child("parent-boundary", "child-boundary")

    first, duplicate = create_handoff_child_result_message("child-boundary", "child result")
    retry, retry_duplicate = create_handoff_child_result_message("child-boundary", "retry")

    assert first is not None and duplicate is False
    assert retry is not None and retry.id == first.id and retry_duplicate is True
    with database.SessionLocal() as db:
        assert db.query(InboxModel).count() == 1
        turns = db.query(WorkflowTurnModel).filter(WorkflowTurnModel.kind == "handoff_result").all()
        assert len(turns) == 1
        assert turns[0].inbox_message_id == first.id


def test_f13_two_queued_handoffs_share_one_safe_boundary_and_clear_barrier(
    workflow_db, monkeypatch
):
    """A busy parent receives all boundary-ready direct results in one successor."""
    parent, first_child, second_child = "parent-batched", "child-batched-one", "child-batched-two"
    active_turn = start_workflow_input(parent)
    assert active_turn is not None
    assert register_handoff_child(parent, first_child)
    assert register_handoff_child(parent, second_child)

    first, first_duplicate = create_handoff_child_result_message(first_child, "first handoff")
    second, second_duplicate = create_handoff_child_result_message(second_child, "second handoff")
    assert first is not None and second is not None
    assert first_duplicate is False and second_duplicate is False
    retry, retry_duplicate = create_handoff_child_result_message(first_child, "first retry")
    assert retry is not None and retry.id == first.id and retry_duplicate is True
    assert get_parent_completion_barrier(parent) == (2, 0)

    with database.SessionLocal() as db:
        turns = db.query(WorkflowTurnModel).filter_by(kind="handoff_result").all()
        assert len(turns) == 1
        callback_turn = turns[0]
        assert callback_turn.inbox_message_id == first.id

    provider = MagicMock()
    provider.get_status.return_value = TerminalStatus.IDLE
    provider.is_process_alive.return_value = True
    with (
        patch(
            "cli_agent_orchestrator.services.inbox_service.provider_manager.get_provider",
            return_value=provider,
        ),
        patch("cli_agent_orchestrator.services.inbox_service.terminal_service.send_input") as send,
    ):
        # The parent is still in its active turn, so no mid-turn injection occurs.
        assert check_and_send_pending_messages(parent) is False
        send.assert_not_called()
        assert claim_workflow_turn_receipt(parent, active_turn)
        assert check_and_send_pending_messages(parent) is True

    assert send.call_count == 1
    delivered_payload = send.call_args.args[1]
    assert f"result_id={first.result_id}" in delivered_payload
    assert f"result_id={second.result_id}" in delivered_payload
    with database.SessionLocal() as db:
        callback_turn = db.query(WorkflowTurnModel).filter_by(kind="handoff_result").one()
        assert callback_turn.state == "sent"
        assert db.query(InboxModel).filter_by(status="delivered").count() == 2

    monkeypatch.setenv("CAO_TERMINAL_ID", parent)
    assert claim_workflow_turn_receipt(parent, callback_turn.id)
    for result_id in (first.result_id, second.result_id):
        acknowledged = asyncio.run(
            mcp_server.acknowledge_assigned_result(callback_turn.id, result_id=result_id)
        )
        assert acknowledged["success"] is True
    assert get_parent_completion_barrier(parent) == (0, 0)
    completed = asyncio.run(mcp_server.complete_workflow(callback_turn.id, "both incorporated"))
    assert completed["success"] is True
    assert get_workflow_status(parent) == "terminal"


def test_f13_restart_resumes_two_results_before_queued_owner_input_once(workflow_db, monkeypatch):
    """A queued owner turn cannot fence the callback that clears its barrier."""
    parent = "parent-result-owner-order"
    children = ("child-result-owner-one", "child-result-owner-two")
    active_turn = _start_admitted_input(parent)
    with database.SessionLocal() as db:
        for index, child in enumerate(children):
            db.add(
                TerminalModel(
                    id=child,
                    tmux_session=f"cao-{child}",
                    tmux_window=f"worker-{index}",
                    provider="codex",
                    runtime_lifecycle="exited",
                )
            )
        db.commit()

    notices = []
    for child in children:
        assert register_handoff_child(parent, child)
        notice, duplicate = create_handoff_child_result_message(child, f"result from {child}")
        assert notice is not None and duplicate is False and notice.result_id is not None
        notices.append(notice)
    with database.SessionLocal() as db:
        db.query(database.ChildAssignmentModel).update({"cleanup_acknowledged": True})
        db.commit()

    monkeypatch.setenv("CAO_TERMINAL_ID", parent)
    monkeypatch.setattr(mcp_server, "_SIDECAR_RUNTIME_GENERATION", "test-generation")
    monkeypatch.setattr(mcp_server, "_active_runtime_generation", lambda: "test-generation")
    blocked = asyncio.run(mcp_server.complete_workflow(active_turn, "children incorporated"))
    assert blocked["error"] == "active child completion barrier"
    assert blocked["active_children"] == 2

    owner_payload = "perform the queued owner landing correction"
    prepared = database.prepare_workflow_input(parent, owner_payload)
    assert prepared == {
        "turn_id": prepared["turn_id"],
        "queued": True,
        "queue_reason": "workflow_predecessor",
    }
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=parent).one()
        assert workflow.active_turn_id == active_turn
        owner_turn = db.get(WorkflowTurnModel, prepared["turn_id"])
        assert owner_turn is not None
        assert owner_turn.state == "queued"
        assert owner_turn.payload == owner_payload

    provider = MagicMock()
    provider.get_status.return_value = TerminalStatus.IDLE
    provider.is_process_alive.return_value = True
    terminal_state = {"lifecycle": "running", "status": TerminalStatus.COMPLETED.value}
    with (
        patch(
            "cli_agent_orchestrator.services.inbox_service.provider_manager.get_provider",
            return_value=provider,
        ),
        patch(
            "cli_agent_orchestrator.services.inbox_service.terminal_service.get_terminal",
            return_value=terminal_state,
        ),
        patch(
            "cli_agent_orchestrator.services.workflow_service.terminal_service.get_terminal",
            return_value=terminal_state,
        ),
        patch("cli_agent_orchestrator.services.inbox_service.terminal_service.send_input") as send,
    ):
        # Startup reconciliation owns the persisted queue even without a new
        # terminal-log event. Both results share one exact callback turn.
        assert reconcile_pending_messages() == 1
        assert send.call_count == 1
        callback_payload = send.call_args.args[1]
        assert owner_payload not in callback_payload
        for notice in notices:
            assert f"result_id={notice.result_id}" in callback_payload

        callback = get_workflow_turn_for_inbox(notices[0].id)
        assert callback is not None
        assert claim_workflow_turn_receipt(parent, callback["turn_id"])
        for notice in notices:
            read = asyncio.run(
                mcp_server.read_delegation_result(callback["turn_id"], notice.result_id)
            )
            assert read["success"] is True
            acknowledged = asyncio.run(
                mcp_server.acknowledge_assigned_result(
                    callback["turn_id"], result_id=notice.result_id
                )
            )
            replay = asyncio.run(
                mcp_server.acknowledge_assigned_result(
                    callback["turn_id"], result_id=notice.result_id
                )
            )
            assert acknowledged["success"] is True
            assert replay["reason_code"] == "RESULT_ALREADY_ACKNOWLEDGED"

        assert get_parent_completion_barrier(parent) == (0, 0)
        assert workflow_service.reconcile_root_workflow(parent) is True
        assert send.call_count == 2
        owner_delivery = send.call_args.args[1]
        assert owner_delivery.endswith(owner_payload)
        assert f"logical-turn={prepared['turn_id']}" in owner_delivery

    assert claim_workflow_turn_receipt(parent, prepared["turn_id"])
    with database.SessionLocal() as db:
        assert db.query(InboxModel).filter_by(status="delivered").count() == 2
        assert (
            db.query(WorkflowTurnModel)
            .filter_by(workflow_id=callback["workflow_id"], kind="handoff_result")
            .count()
            == 1
        )
        owner_turns = (
            db.query(WorkflowTurnModel)
            .filter_by(workflow_id=callback["workflow_id"], kind="external_input")
            .all()
        )
        assert len([turn for turn in owner_turns if turn.payload == owner_payload]) == 1


def test_f13_resume_reconciles_older_result_callbacks_before_composer_once(
    workflow_db, monkeypatch
):
    """A newer execution-resume rematerializes the complete queued FIFO suffix."""
    parent = "parent-resume-result-suffix"
    children = tuple(f"child-resume-result-{index}" for index in range(3))
    _ensure_running_test_terminal(parent)
    interrupted_turn = start_workflow_input(parent)
    assert interrupted_turn is not None
    initial_receipt = claim_or_resume_workflow_turn_receipt(parent, interrupted_turn)
    assert initial_receipt["accepted"] is True

    notices = []
    for child in children:
        assert register_child_assignment(parent, child)
        notice, duplicate = create_child_assignment_result_message(
            child,
            parent,
            f"durable result from {child}",
            **_authorized_callback(child),
        )
        assert notice is not None and notice.result_id is not None
        assert duplicate is False
        notices.append(notice)

    with database.SessionLocal() as db:
        callback_turn_ids = [
            db.query(WorkflowTurnModel).filter_by(inbox_message_id=notice.id).one().id
            for notice in notices
        ]
        result_identity = {
            result.id: (
                result.child_assignment_id,
                db.get(database.ChildAssignmentModel, result.child_assignment_id).attempt_id,
                db.get(
                    database.ChildAssignmentModel, result.child_assignment_id
                ).review_subject_revision,
            )
            for result in db.query(database.DelegationResultModel).filter(
                database.DelegationResultModel.id.in_([notice.result_id for notice in notices])
            )
        }

    resumed = claim_or_resume_workflow_turn_receipt(
        parent,
        interrupted_turn,
        resume_token=initial_receipt["resume_token"],
    )
    assert resumed["accepted"] is True and resumed["resumed"] is True
    resume_turn = resumed["logical_turn_id"]
    composer_payload = "continue after every historical callback"
    composer = database.prepare_workflow_input(
        parent,
        composer_payload,
        request_id="resume-result-suffix-composer",
    )
    assert composer is not None and composer["queued"] is True
    composer_turn = composer["turn_id"]
    assert callback_turn_ids[-1] < resume_turn < composer_turn

    # A server restart after the newer receipt sees only durable state.
    workflow_db.dispose()
    assert database.reconcile_result_callbacks_superseded_by_resume() == 4
    assert database.reconcile_result_callbacks_superseded_by_resume() == 0

    with database.SessionLocal() as db:
        old_callbacks = [db.get(WorkflowTurnModel, turn_id) for turn_id in callback_turn_ids]
        old_composer = db.get(WorkflowTurnModel, composer_turn)
        assert all(turn is not None and turn.state == "cancelled" for turn in old_callbacks)
        assert old_composer is not None and old_composer.state == "cancelled"
        successor_ids = [turn.superseded_by_turn_id for turn in old_callbacks]
        composer_successor_id = old_composer.superseded_by_turn_id
        assert all(isinstance(turn_id, int) for turn_id in successor_ids)
        assert successor_ids == sorted(successor_ids)
        assert successor_ids[-1] < composer_successor_id
        assert all(turn_id > composer_turn for turn_id in successor_ids)
        assert composer_successor_id > composer_turn
        assert db.get(WorkflowTurnModel, composer_successor_id).payload == composer_payload
        for notice, successor_id in zip(notices, successor_ids, strict=True):
            inbox = db.get(InboxModel, notice.id)
            result = db.get(database.DelegationResultModel, notice.result_id)
            assignment = db.get(database.ChildAssignmentModel, result.child_assignment_id)
            assert inbox.status == "pending"
            assert inbox.callback_reconciled_from_turn_id in callback_turn_ids
            assert inbox.callback_reconciled_at is not None
            assert result.workflow_turn_id == successor_id
            assert (
                result.child_assignment_id,
                assignment.attempt_id,
                assignment.review_subject_revision,
            ) == result_identity[result.id]
        assert (
            db.query(database.DelegationResultEventModel)
            .filter_by(event_type="callback_transport_reconciled")
            .count()
            == 3
        )

    provider = MagicMock()
    provider.get_status.return_value = TerminalStatus.IDLE
    provider.is_process_alive.return_value = True
    terminal_state = {"lifecycle": "running", "status": TerminalStatus.COMPLETED.value}
    monkeypatch.setenv("CAO_TERMINAL_ID", parent)
    with (
        patch(
            "cli_agent_orchestrator.services.inbox_service.provider_manager.get_provider",
            return_value=provider,
        ),
        patch(
            "cli_agent_orchestrator.services.inbox_service.terminal_service.provider_runtime_sidecar_reconnect_required",
            return_value=False,
        ),
        patch(
            "cli_agent_orchestrator.services.workflow_service.terminal_service.get_terminal",
            return_value=terminal_state,
        ),
        patch("cli_agent_orchestrator.services.terminal_service.send_input") as send,
    ):
        for notice, successor_id in zip(notices, successor_ids, strict=True):
            assert check_and_send_pending_messages(parent) is True
            assert f"logical-turn={successor_id}" in send.call_args.args[1]
            assert claim_workflow_turn_receipt(parent, successor_id)
            read = asyncio.run(mcp_server.read_delegation_result(successor_id, notice.result_id))
            assert read["success"] is True
            acknowledged = asyncio.run(
                mcp_server.acknowledge_assigned_result(
                    successor_id,
                    result_id=notice.result_id,
                    child_terminal_id=notice.sender_id,
                )
            )
            assert acknowledged["success"] is True

        assert inbox_service.reconcile_provider_execution_queue() >= 1
        assert send.call_count == 4
        assert f"logical-turn={composer_successor_id}" in send.call_args.args[1]
        assert send.call_args.args[1].endswith(composer_payload)

    assert claim_workflow_turn_receipt(parent, composer_successor_id)
    duplicate_composer = database.prepare_workflow_input(
        parent,
        composer_payload,
        request_id="resume-result-suffix-composer",
    )
    assert duplicate_composer is not None
    assert duplicate_composer["accepted"] is True
    assert duplicate_composer["duplicate"] is True
    assert duplicate_composer["turn_id"] == composer_successor_id
    assert get_parent_completion_barrier(parent) == (0, 0)
    with database.SessionLocal() as db:
        assert db.query(InboxModel).filter_by(status="delivered").count() == 3
        assert db.query(database.DelegationResultModel).count() == 3


def test_f13_resume_callback_reconciliation_is_atomic_across_restart(
    workflow_db,
):
    parent = "parent-resume-callback-atomic"
    child = "child-resume-callback-atomic"
    _ensure_running_test_terminal(parent)
    interrupted_turn = start_workflow_input(parent)
    receipt = claim_or_resume_workflow_turn_receipt(parent, interrupted_turn)
    assert receipt["accepted"] is True
    assert register_child_assignment(parent, child)
    notice, duplicate = create_child_assignment_result_message(
        child,
        parent,
        "atomic durable result",
        **_authorized_callback(child),
    )
    assert notice is not None and duplicate is False
    with database.SessionLocal() as db:
        old_turn_id = db.query(WorkflowTurnModel).filter_by(inbox_message_id=notice.id).one().id

    resumed = claim_or_resume_workflow_turn_receipt(
        parent, interrupted_turn, resume_token=receipt["resume_token"]
    )
    assert resumed["accepted"] is True

    def fail_commit(_session):
        raise RuntimeError("injected reconciliation commit failure")

    event.listen(database.SessionLocal.class_, "before_commit", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="injected reconciliation commit failure"):
            database.reconcile_result_callbacks_superseded_by_resume()
    finally:
        event.remove(database.SessionLocal.class_, "before_commit", fail_commit)

    workflow_db.dispose()
    with database.SessionLocal() as db:
        old_turn = db.get(WorkflowTurnModel, old_turn_id)
        result = db.get(database.DelegationResultModel, notice.result_id)
        inbox = db.get(InboxModel, notice.id)
        assert old_turn.state == "queued"
        assert old_turn.superseded_by_turn_id is None
        assert result.workflow_turn_id == old_turn_id
        assert inbox.status == "pending"
        assert inbox.callback_reconciled_at is None
        assert (
            db.query(database.DelegationResultEventModel)
            .filter_by(event_type="callback_transport_reconciled")
            .count()
            == 0
        )

    assert database.reconcile_result_callbacks_superseded_by_resume() == 1
    assert database.reconcile_result_callbacks_superseded_by_resume() == 0


def test_f13_resume_terminalizes_acknowledged_superseded_and_wrong_callbacks(
    workflow_db,
):
    parent = "parent-resume-mixed-callbacks"
    children = tuple(f"child-resume-mixed-{index}" for index in range(4))
    _ensure_running_test_terminal(parent)
    interrupted_turn = start_workflow_input(parent)
    receipt = claim_or_resume_workflow_turn_receipt(parent, interrupted_turn)
    assert receipt["accepted"] is True
    notices = []
    for child in children:
        assert register_child_assignment(parent, child)
        notice, duplicate = create_child_assignment_result_message(
            child,
            parent,
            f"mixed durable result {child}",
            **_authorized_callback(child),
        )
        assert notice is not None and duplicate is False
        notices.append(notice)

    with database.SessionLocal() as db:
        assignments = [
            db.query(database.ChildAssignmentModel).filter_by(child_terminal_id=child).one()
            for child in children
        ]
        assignments[0].status = "result_acknowledged"
        assignments[1].status = "result_superseded"
        assignments[1].review_superseded_at = datetime.now()
        assignments[3].review_scope_sha256 = "exact-scope"
        assignments[3].review_subject_id = "exact-subject"
        assignments[3].review_subject_kind = "git_commit"
        assignments[3].review_subject_revision = "a" * 40
        review_authority = (
            assignments[3].attempt_id,
            assignments[3].review_scope_sha256,
            assignments[3].review_subject_id,
            assignments[3].review_subject_revision,
        )
        wrong = db.get(database.DelegationResultModel, notices[2].result_id)
        assert wrong.parent_workflow_id is not None
        wrong.parent_workflow_id += 1000
        valid_result_id = notices[3].result_id
        db.commit()

    resumed = claim_or_resume_workflow_turn_receipt(
        parent, interrupted_turn, resume_token=receipt["resume_token"]
    )
    assert resumed["accepted"] is True
    assert database.reconcile_result_callbacks_superseded_by_resume() == 4

    with database.SessionLocal() as db:
        assert db.get(InboxModel, notices[0].id).status == "superseded"
        assert db.get(InboxModel, notices[1].id).status == "superseded"
        assert db.get(InboxModel, notices[2].id).status == "failed"
        valid_inbox = db.get(InboxModel, notices[3].id)
        valid_result = db.get(database.DelegationResultModel, valid_result_id)
        valid_assignment = db.get(database.ChildAssignmentModel, valid_result.child_assignment_id)
        valid_turn = db.get(WorkflowTurnModel, valid_result.workflow_turn_id)
        assert valid_inbox.status == "pending"
        assert valid_inbox.callback_reconciled_at is not None
        assert valid_turn.state == "queued"
        assert valid_turn.id > resumed["logical_turn_id"]
        assert (
            valid_assignment.attempt_id,
            valid_assignment.review_scope_sha256,
            valid_assignment.review_subject_id,
            valid_assignment.review_subject_revision,
        ) == review_authority
        assert db.query(database.DelegationResultModel).count() == 4


def test_f13_resume_reconciles_one_handoff_batch_without_splitting_results(
    workflow_db,
):
    parent = "parent-resume-handoff-batch"
    children = ("child-resume-handoff-one", "child-resume-handoff-two")
    _ensure_running_test_terminal(parent)
    interrupted_turn = start_workflow_input(parent)
    receipt = claim_or_resume_workflow_turn_receipt(parent, interrupted_turn)
    assert receipt["accepted"] is True
    notices = []
    for child in children:
        assert register_handoff_child(parent, child)
        notice, duplicate = create_handoff_child_result_message(child, f"handoff from {child}")
        assert notice is not None and duplicate is False
        notices.append(notice)
    with database.SessionLocal() as db:
        result_ids = [notice.result_id for notice in notices]
        old_turn_ids = {
            result.workflow_turn_id
            for result in db.query(database.DelegationResultModel).filter(
                database.DelegationResultModel.id.in_(result_ids)
            )
        }
        assert len(old_turn_ids) == 1

    resumed = claim_or_resume_workflow_turn_receipt(
        parent, interrupted_turn, resume_token=receipt["resume_token"]
    )
    assert resumed["accepted"] is True
    assert database.reconcile_result_callbacks_superseded_by_resume() == 1

    with database.SessionLocal() as db:
        results = (
            db.query(database.DelegationResultModel)
            .filter(database.DelegationResultModel.id.in_(result_ids))
            .all()
        )
        successor_ids = {result.workflow_turn_id for result in results}
        assert len(successor_ids) == 1
        successor_id = successor_ids.pop()
        assert successor_id > resumed["logical_turn_id"]
        successor = db.get(WorkflowTurnModel, successor_id)
        assert successor.kind == "handoff_result"
        assert successor.state == "queued"
        assert {result.id for result in results} == set(result_ids)
        assert all(db.get(InboxModel, notice.id).status == "pending" for notice in notices)


def test_f13_stale_handoff_claim_after_resume_never_rolls_active_authority_back(
    workflow_db,
):
    """A stale FIFO scan yields to one post-resume callback successor."""
    parent = "parent-resume-handoff-claim-race"
    child = "child-resume-handoff-claim-race"
    _ensure_running_test_terminal(parent)
    interrupted_turn = start_workflow_input(parent)
    assert interrupted_turn is not None
    receipt = claim_or_resume_workflow_turn_receipt(parent, interrupted_turn)
    assert receipt["accepted"] is True
    assert register_handoff_child(parent, child)
    notice, duplicate = create_handoff_child_result_message(child, "durable raced callback")
    assert notice is not None and notice.result_id is not None and duplicate is False
    callback = get_workflow_turn_for_inbox(notice.id)
    assert callback is not None
    callback_turn = callback["turn_id"]

    # The dispatcher selected this Inbox head before the concurrent resume
    # committed. Its later transactional batch claim must re-check monotonic
    # receiver authority rather than trusting the stale scan.
    stale_queue = database.get_provider_execution_admission_queue()
    assert stale_queue[0]["source"] == "inbox"
    assert stale_queue[0]["source_id"] == notice.id

    resumed = claim_or_resume_workflow_turn_receipt(
        parent,
        interrupted_turn,
        resume_token=receipt["resume_token"],
    )
    assert resumed["accepted"] is True and resumed["resumed"] is True
    resume_turn = resumed["logical_turn_id"]
    assert callback_turn < resume_turn
    assert database.acquire_provider_execution(parent, resume_turn, 3)

    assert claim_handoff_result_batch_for_inbox(notice.id) is None
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=parent).one()
        assert workflow.active_turn_id == resume_turn
        assert db.get(WorkflowTurnModel, callback_turn).state == "queued"
        assert db.get(database.ProviderExecutionLeaseModel, parent).workflow_turn_id == resume_turn

    assert database.reconcile_result_callbacks_superseded_by_resume() == 1
    assert database.reconcile_result_callbacks_superseded_by_resume() == 0
    with database.SessionLocal() as db:
        stale = db.get(WorkflowTurnModel, callback_turn)
        assert stale.state == "cancelled"
        assert stale.superseded_by_turn_id is not None
        successor_turn = stale.superseded_by_turn_id
        successor = db.get(WorkflowTurnModel, successor_turn)
        result = db.get(database.DelegationResultModel, notice.result_id)
        immutable_result = (
            result.id,
            result.document_json,
            result.content_sha256,
            result.content_bytes,
        )
        assert successor_turn > resume_turn
        assert successor.state == "queued"
        assert result.workflow_turn_id == successor_turn
        assert (
            db.query(WorkflowTurnModel)
            .filter_by(
                workflow_id=workflow.id,
                dedupe_key=f"resume-reconciled:{resume_turn}:{callback_turn}",
            )
            .count()
            == 1
        )

    # A stale Ready projection can claim the correct successor while the
    # resumed provider execution still owns capacity. The admission failure
    # requeues that successor without restoring either the old callback or R.
    successor_claim = claim_handoff_result_batch_for_inbox(notice.id)
    assert isinstance(successor_claim, dict)
    assert successor_claim["id"] == successor_turn
    assert requeue_workflow_turn(
        successor_turn,
        successor_claim["claim_token"],
        successor_claim["claim_generation"],
        admission_reason_code="PROVIDER_EXECUTION_TERMINAL_BUSY",
    )
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=parent).one()
        assert workflow.active_turn_id == successor_turn
        assert db.get(WorkflowTurnModel, callback_turn).state == "cancelled"
        assert db.get(WorkflowTurnModel, successor_turn).state == "queued"
        assert db.get(database.ProviderExecutionLeaseModel, parent).workflow_turn_id == resume_turn
        assert (
            db.query(WorkflowTurnReceiptModel)
            .filter_by(workflow_turn_id=successor_turn, receiver_terminal_id=parent)
            .count()
            == 0
        )

    assert database.release_provider_execution(parent, resume_turn)
    delivered = claim_handoff_result_batch_for_inbox(notice.id)
    assert isinstance(delivered, dict) and delivered["id"] == successor_turn
    assert mark_workflow_turn_sent(
        successor_turn,
        delivered["claim_token"],
        delivered["claim_generation"],
    )
    assert database.update_pending_message_status(notice.id, database.MessageStatus.DELIVERED)
    assert mark_child_assignment_result_delivered(notice.id)
    assert claim_workflow_turn_receipt(parent, successor_turn)
    assert not claim_workflow_turn_receipt(parent, successor_turn)

    with database.SessionLocal() as db:
        result = db.get(database.DelegationResultModel, notice.result_id)
        assert (
            result.id,
            result.document_json,
            result.content_sha256,
            result.content_bytes,
        ) == immutable_result
        turns = (
            db.query(WorkflowTurnModel)
            .filter_by(workflow_id=result.parent_workflow_id, kind="handoff_result")
            .all()
        )
        assert len(turns) == 2
        assert {turn.id for turn in turns} == {callback_turn, successor_turn}


def test_f13_closed_ordinary_inbox_is_not_a_new_owner_input_predecessor(workflow_db):
    """A terminal workflow's transport row cannot defer a new semantic workflow."""
    parent = "parent-closed-inbox-submit"
    _start_admitted_input(parent)
    stale = create_inbox_message("sender", parent, "stale closed-workflow input")
    stale_turn = database.ensure_workflow_turn_for_inbox(stale.id)
    assert stale_turn is not None
    assert set_workflow_terminal_state(parent, "terminal", "completed before Inbox send")
    with database.SessionLocal() as db:
        assert db.get(InboxModel, stale.id).status == "failed"
        # Model a row persisted by a pre-fix runtime before the owner submits
        # the deliberate replacement workflow.
        db.get(InboxModel, stale.id).status = "pending"
        db.commit()

    owner_payload = "begin the deliberate replacement workflow"
    prepared = database.prepare_workflow_input(parent, owner_payload)
    assert prepared == {
        "turn_id": prepared["turn_id"],
        "queued": False,
        "queue_reason": None,
    }

    with database.SessionLocal() as db:
        assert db.get(InboxModel, stale.id).status == "failed"
        assert db.get(WorkflowTurnModel, stale_turn).state == "cancelled"
        owner_turn = db.get(WorkflowTurnModel, prepared["turn_id"])
        assert owner_turn is not None
        assert owner_turn.state == "sent"
        assert owner_turn.payload == owner_payload
        owner_workflow = db.get(WorkflowModel, owner_turn.workflow_id)
        assert owner_workflow is not None
        assert owner_workflow.status == "open"
        assert owner_workflow.active_turn_id == owner_turn.id


def test_f13_lifecycle_cancellation_fails_closed_workflow_inbox_transport(workflow_db):
    """The central terminal/session cancellation path clears its stale FIFO head."""
    parent = "parent-closed-inbox-lifecycle"
    _start_admitted_input(parent)
    stale = create_inbox_message("sender", parent, "stale lifecycle input")
    stale_turn = database.ensure_workflow_turn_for_inbox(stale.id)
    assert stale_turn is not None
    with database.SessionLocal() as db:
        stale_workflow_id = db.get(WorkflowTurnModel, stale_turn).workflow_id

    assert cancel_workflows_for_terminal(parent) == 1

    with database.SessionLocal() as db:
        assert db.get(InboxModel, stale.id).status == "failed"
        assert db.get(WorkflowTurnModel, stale_turn).state == "cancelled"
        workflow = db.get(WorkflowModel, stale_workflow_id)
        assert workflow is not None
        assert workflow.status == "cancelled"


def test_f13_closed_inbox_dispatch_cas_preserves_concurrent_delivery(workflow_db):
    """A stale close observer cannot overwrite a transport outcome that already won."""
    parent = "parent-closed-inbox-delivery-race"
    _start_admitted_input(parent)
    stale, _stale_turn = _pending_inbox_turn(parent, "concurrently delivered input")
    assert set_workflow_terminal_state(parent, "terminal", "closed during delivery")
    with database.SessionLocal() as db:
        db.get(InboxModel, stale.id).status = "delivered"
        db.commit()

    with patch(
        "cli_agent_orchestrator.services.inbox_service.get_pending_messages",
        side_effect=[[stale], []],
    ):
        assert _dispatch_pending_messages_with_admission(parent) is False

    with database.SessionLocal() as db:
        assert db.get(InboxModel, stale.id).status == "delivered"


def test_f13_transport_retry_owner_gate_fences_its_pending_inbox(workflow_db):
    """The bounded pre-send retry guard closes its exact Inbox transport atomically."""
    root = "root-inbox-transport-retry-gate"
    _start_admitted_input(root)
    stale, stale_turn = _pending_inbox_turn(root, "retry until owner gate")
    now = datetime(2026, 8, 24, 21, 0, 0)
    observe_workflow_final(root, now=now)

    claim = claim_workflow_turn(root, now=now, inbox_message_id=stale.id)
    assert claim is not None and claim["id"] == stale_turn
    assert requeue_workflow_turn(
        claim["id"], claim["claim_token"], claim["claim_generation"], now=now
    )
    claim = claim_workflow_turn(root, now=now + timedelta(seconds=2), inbox_message_id=stale.id)
    assert claim is not None and claim["id"] == stale_turn
    assert requeue_workflow_turn(
        claim["id"],
        claim["claim_token"],
        claim["claim_generation"],
        now=now + timedelta(seconds=2),
    )
    claim = claim_workflow_turn(root, now=now + timedelta(seconds=5), inbox_message_id=stale.id)
    assert claim is not None and claim["id"] == stale_turn
    assert requeue_workflow_turn(
        claim["id"],
        claim["claim_token"],
        claim["claim_generation"],
        now=now + timedelta(seconds=5),
    )

    with database.SessionLocal() as db:
        assert db.get(WorkflowTurnModel, stale_turn).state == "cancelled"
        assert db.get(InboxModel, stale.id).status == "failed"
        assert db.get(WorkflowModel, db.get(WorkflowTurnModel, stale_turn).workflow_id).status == (
            "owner_gate"
        )


def test_owner_gate_composer_successor_survives_predecessor_retry_exhaustion(workflow_db):
    """A committed owner decision wins the race with its predecessor's third failure."""
    root = "root-owner-composer-successor"
    _start_admitted_input(root)
    assert set_workflow_terminal_state(root, "owner_gate", "owner decision")

    first = database.prepare_workflow_input(
        root,
        "first owner continuation",
        request_id="06b70814-681a-42e4-b207-5f195b5cac26",
        require_live_terminal=True,
    )
    assert first["queued"] is False
    assert database.queue_workflow_input_for_provider(
        root, first["turn_id"], "first owner continuation", "PROVIDER_TRANSPORT_RETRY_PENDING"
    )
    now = datetime(2026, 8, 26, 12, 15, 52)
    for attempt_at in (now, now + timedelta(seconds=1)):
        claim = claim_workflow_turn(root, now=attempt_at)
        assert claim is not None and claim["id"] == first["turn_id"]
        assert requeue_workflow_turn(
            claim["id"], claim["claim_token"], claim["claim_generation"], now=attempt_at
        )

    third_at = now + timedelta(seconds=3)
    third = claim_workflow_turn(root, now=third_at)
    assert third is not None and third["id"] == first["turn_id"]
    successor = database.prepare_workflow_input(
        root,
        "the owner decision that raced the third failure",
        request_id="b1fda06a-a5af-49d1-b039-c93b919071fd",
        require_live_terminal=True,
    )
    assert successor["queued"] is True
    assert successor["queue_reason"] == "workflow_predecessor"

    assert requeue_workflow_turn(
        third["id"],
        third["claim_token"],
        third["claim_generation"],
        now=third_at,
    )
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).order_by(WorkflowModel.id.desc()).first()
        assert workflow.status == "open"
        assert workflow.active_turn_id == successor["turn_id"]
        assert workflow.resumed_from_owner_gate_workflow_id is not None
        assert db.get(WorkflowTurnModel, first["turn_id"]).state == "cancelled"
        queued = db.get(WorkflowTurnModel, successor["turn_id"])
        assert queued.state == "queued"
        assert queued.attempt_count == 0

    claimed_successor = claim_workflow_turn(root, now=third_at)
    assert claimed_successor is not None
    assert claimed_successor["id"] == successor["turn_id"]
    assert claim_workflow_turn(root, now=third_at) is None


def test_resource_admission_deferral_never_consumes_transport_retry_budget(workflow_db):
    root = "root-red-admission-deferral"
    _start_admitted_input(root)
    assert set_workflow_terminal_state(root, "owner_gate", "owner decision")
    prepared = database.prepare_workflow_input(
        root,
        "recover while disk pressure is red",
        request_id="53918e86-02af-4b8f-869a-20b68a8ee4dc",
        require_live_terminal=True,
    )
    assert database.queue_workflow_input_for_provider(
        root, prepared["turn_id"], "recover while disk pressure is red"
    )
    now = datetime(2026, 8, 26, 12, 30, 0)

    for attempt in range(5):
        attempt_at = now + timedelta(seconds=attempt * 2)
        claim = claim_workflow_turn(root, now=attempt_at)
        assert claim is not None and claim["id"] == prepared["turn_id"]
        assert requeue_workflow_turn(
            claim["id"],
            claim["claim_token"],
            claim["claim_generation"],
            now=attempt_at,
            admission_reason_code="RESOURCE_HEALTH_REJECTED",
        )

    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).order_by(WorkflowModel.id.desc()).first()
        turn = db.get(WorkflowTurnModel, prepared["turn_id"])
        assert workflow.status == "open"
        assert workflow.active_turn_id == prepared["turn_id"]
        assert turn.state == "queued"
        assert turn.attempt_count == 0
        assert turn.claim_generation == 5
        assert turn.queue_reason == "RESOURCE_HEALTH_REJECTED"


def test_restart_reopens_only_exact_stranded_owner_composer_successor(workflow_db):
    root = "root-restart-owner-successor"
    _ensure_running_test_terminal(root)
    now = datetime(2026, 8, 26, 12, 45, 0)
    with database.SessionLocal() as db:
        prior = WorkflowModel(root_terminal_id=root, status="owner_gate")
        stranded = WorkflowModel(
            root_terminal_id=root,
            status="owner_gate",
            terminal_reason="bounded continuation transport retry guard",
        )
        db.add_all([prior, stranded])
        db.flush()
        cancelled = WorkflowTurnModel(
            workflow_id=stranded.id,
            kind="external_input",
            dedupe_key="external_request:cancelled-head",
            state="cancelled",
            attempt_count=3,
        )
        queued = WorkflowTurnModel(
            workflow_id=stranded.id,
            kind="external_input",
            dedupe_key="external_request:exact-successor",
            payload="recover me once",
            state="queued",
        )
        db.add_all([cancelled, queued])
        db.flush()
        stranded.active_turn_id = cancelled.id
        prior_id = prior.id
        queued_id = queued.id
        db.commit()

    assert database.reconcile_owner_gated_workflow_successors(now=now) == [root]
    assert database.reconcile_owner_gated_workflow_successors(now=now) == []
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).order_by(WorkflowModel.id.desc()).first()
        assert workflow.status == "open"
        assert workflow.active_turn_id == queued_id
        assert workflow.resumed_from_owner_gate_workflow_id == prior_id
        assert workflow.terminal_reason is None
        assert db.get(WorkflowTurnModel, queued_id).state == "queued"

    claim = claim_workflow_turn(root, now=now)
    assert claim is not None and claim["id"] == queued_id
    assert claim_workflow_turn(root, now=now) is None


def test_new_composer_input_cannot_overtake_owner_successor_before_recovery_tick(
    workflow_db,
):
    root = "root-owner-successor-before-recovery"
    _ensure_running_test_terminal(root)
    now = datetime(2026, 8, 26, 12, 46, 0)
    with database.SessionLocal() as db:
        prior = WorkflowModel(root_terminal_id=root, status="owner_gate")
        stranded = WorkflowModel(
            root_terminal_id=root,
            status="owner_gate",
            terminal_reason="bounded continuation transport retry guard",
        )
        db.add_all([prior, stranded])
        db.flush()
        cancelled = WorkflowTurnModel(
            workflow_id=stranded.id,
            kind="external_input",
            dedupe_key="external_request:cancelled-before-new-input",
            state="cancelled",
            attempt_count=3,
        )
        accepted_first = WorkflowTurnModel(
            workflow_id=stranded.id,
            kind="external_input",
            dedupe_key="external_request:accepted-before-recovery",
            payload="execute me first",
            state="queued",
        )
        db.add_all([cancelled, accepted_first])
        db.flush()
        stranded.active_turn_id = cancelled.id
        stranded_id = stranded.id
        accepted_first_id = accepted_first.id
        db.commit()

    accepted_second = database.prepare_workflow_input(
        root,
        "execute me second",
        request_id="87ae188d-50fe-4abe-a3c5-15fc0d2df705",
        require_live_terminal=True,
    )

    assert accepted_second["queued"] is True
    assert accepted_second["queue_reason"] == "workflow_predecessor"
    with database.SessionLocal() as db:
        workflows = db.query(WorkflowModel).filter_by(root_terminal_id=root).all()
        assert len(workflows) == 2
        current = db.get(WorkflowModel, stranded_id)
        assert current.status == "open"
        assert current.active_turn_id == accepted_first_id
        second = db.get(WorkflowTurnModel, accepted_second["turn_id"])
        assert second.workflow_id == stranded_id
        assert second.state == "queued"
        assert second.queue_reason == "WORKFLOW_CONTINUATION_PENDING"

    first_claim = claim_workflow_turn(root, now=now)
    assert first_claim is not None and first_claim["id"] == accepted_first_id
    assert mark_workflow_turn_sent(
        first_claim["id"],
        first_claim["claim_token"],
        first_claim["claim_generation"],
        now=now,
    )
    assert claim_workflow_turn_receipt(root, accepted_first_id, now=now)
    assert observe_workflow_final(root, now=now + timedelta(seconds=1)) is not None

    second_claim = claim_workflow_turn(root, now=now + timedelta(seconds=1))
    assert second_claim is not None and second_claim["id"] == accepted_second["turn_id"]
    assert activate_workflow_turn(root, second_claim["id"])
    assert claim_workflow_turn(root, now=now + timedelta(seconds=1)) is None
    assert mark_workflow_turn_sent(
        second_claim["id"],
        second_claim["claim_token"],
        second_claim["claim_generation"],
        now=now + timedelta(seconds=1),
    )
    assert claim_workflow_turn_receipt(
        root, accepted_second["turn_id"], now=now + timedelta(seconds=1)
    )
    assert not claim_workflow_turn_receipt(
        root, accepted_second["turn_id"], now=now + timedelta(seconds=1)
    )
    assert observe_workflow_final(root, now=now + timedelta(seconds=2)) is not None
    with database.SessionLocal() as db:
        assert db.get(WorkflowTurnModel, accepted_first_id).state == "finished"
        assert db.get(WorkflowTurnModel, accepted_second["turn_id"]).state == "finished"
        receipts = (
            db.query(WorkflowTurnReceiptModel)
            .filter(
                WorkflowTurnReceiptModel.receiver_terminal_id == root,
                WorkflowTurnReceiptModel.workflow_turn_id.in_(
                    (accepted_first_id, accepted_second["turn_id"])
                ),
            )
            .count()
        )
        assert receipts == 2


def test_exact_composer_retry_reopens_same_owner_successor_before_recovery_tick(
    workflow_db,
):
    root = "root-owner-successor-duplicate-before-recovery"
    request_id = "bb971146-c08e-4ec6-966d-c8193629c58c"
    payload = "execute this accepted request exactly once"
    _ensure_running_test_terminal(root)
    with database.SessionLocal() as db:
        prior = WorkflowModel(root_terminal_id=root, status="owner_gate")
        stranded = WorkflowModel(
            root_terminal_id=root,
            status="owner_gate",
            terminal_reason="bounded continuation transport retry guard",
        )
        db.add_all([prior, stranded])
        db.flush()
        cancelled = WorkflowTurnModel(
            workflow_id=stranded.id,
            kind="external_input",
            dedupe_key="external_request:cancelled-before-duplicate",
            state="cancelled",
            attempt_count=3,
        )
        accepted = WorkflowTurnModel(
            workflow_id=stranded.id,
            kind="external_input",
            dedupe_key=f"external_request:{request_id}",
            payload=payload,
            state="queued",
        )
        db.add_all([cancelled, accepted])
        db.flush()
        stranded.active_turn_id = cancelled.id
        stranded_id = stranded.id
        accepted_id = accepted.id
        turn_count = db.query(WorkflowTurnModel).count()
        db.commit()

    conflict = database.prepare_workflow_input(
        root,
        "different payload must not borrow the stable request",
        request_id=request_id,
        require_live_terminal=True,
    )
    assert conflict == {
        "accepted": False,
        "reason_code": "WORKFLOW_INPUT_IDEMPOTENCY_CONFLICT",
    }
    with database.SessionLocal() as db:
        unchanged = db.get(WorkflowModel, stranded_id)
        assert unchanged.status == "owner_gate"
        assert unchanged.active_turn_id != accepted_id
        assert db.query(WorkflowTurnModel).count() == turn_count

    duplicate = database.prepare_workflow_input(
        root,
        payload,
        request_id=request_id,
        require_live_terminal=True,
    )

    assert duplicate == {
        "accepted": True,
        "turn_id": accepted_id,
        "queued": True,
        "queue_reason": "workflow_predecessor",
        "duplicate": True,
    }
    with database.SessionLocal() as db:
        current = db.get(WorkflowModel, stranded_id)
        assert current.status == "open"
        assert current.active_turn_id == accepted_id
        assert db.get(WorkflowTurnModel, accepted_id).state == "queued"
        assert db.query(WorkflowTurnModel).count() == turn_count

    claim = claim_workflow_turn(root)
    assert claim is not None and claim["id"] == accepted_id
    assert claim_workflow_turn(root) is None


def test_historical_owner_successor_retry_cannot_reopen_beside_newer_workflow(
    workflow_db,
):
    root = "root-historical-owner-successor-duplicate"
    request_id = "a34c1158-c653-45fa-bf8b-03d1bbfbf8ee"
    payload = "historical accepted request"
    _ensure_running_test_terminal(root)
    with database.SessionLocal() as db:
        prior = WorkflowModel(root_terminal_id=root, status="owner_gate")
        stranded = WorkflowModel(
            root_terminal_id=root,
            status="owner_gate",
            terminal_reason="bounded continuation transport retry guard",
        )
        newer = WorkflowModel(root_terminal_id=root, status="open")
        db.add_all([prior, stranded, newer])
        db.flush()
        cancelled = WorkflowTurnModel(
            workflow_id=stranded.id,
            kind="external_input",
            dedupe_key="external_request:historical-cancelled-head",
            state="cancelled",
            attempt_count=3,
        )
        historical = WorkflowTurnModel(
            workflow_id=stranded.id,
            kind="external_input",
            dedupe_key=f"external_request:{request_id}",
            payload=payload,
            state="queued",
        )
        current = WorkflowTurnModel(
            workflow_id=newer.id,
            kind="external_input",
            dedupe_key="external_request:newer-current-authority",
            payload="newer authority",
            state="sent",
        )
        db.add_all([cancelled, historical, current])
        db.flush()
        stranded.active_turn_id = cancelled.id
        newer.active_turn_id = current.id
        stranded_id = stranded.id
        historical_id = historical.id
        newer_id = newer.id
        current_id = current.id
        turn_count = db.query(WorkflowTurnModel).count()
        db.commit()

    duplicate = database.prepare_workflow_input(
        root,
        payload,
        request_id=request_id,
        require_live_terminal=True,
    )

    assert duplicate == {
        "accepted": False,
        "reason_code": "WORKFLOW_INPUT_NO_LONGER_EXECUTABLE",
    }
    with database.SessionLocal() as db:
        historical_workflow = db.get(WorkflowModel, stranded_id)
        current_workflow = db.get(WorkflowModel, newer_id)
        assert historical_workflow.status == "owner_gate"
        assert historical_workflow.active_turn_id != historical_id
        assert db.get(WorkflowTurnModel, historical_id).state == "queued"
        assert current_workflow.status == "open"
        assert current_workflow.active_turn_id == current_id
        assert db.query(WorkflowModel).filter_by(root_terminal_id=root, status="open").count() == 1
        assert db.query(WorkflowTurnModel).count() == turn_count


def test_provider_queue_restart_recovery_sends_stranded_owner_successor_once(
    workflow_db, monkeypatch
):
    root = "root-restart-owner-successor-send"
    _ensure_running_test_terminal(root)
    with database.SessionLocal() as db:
        prior = WorkflowModel(root_terminal_id=root, status="owner_gate")
        stranded = WorkflowModel(
            root_terminal_id=root,
            status="owner_gate",
            terminal_reason="bounded continuation transport retry guard",
        )
        db.add_all([prior, stranded])
        db.flush()
        cancelled = WorkflowTurnModel(
            workflow_id=stranded.id,
            kind="external_input",
            dedupe_key="external_request:cancelled-restart-head",
            state="cancelled",
            attempt_count=3,
        )
        queued = WorkflowTurnModel(
            workflow_id=stranded.id,
            kind="external_input",
            dedupe_key="external_request:queued-restart-successor",
            payload="deliver the existing owner continuation",
            state="queued",
        )
        db.add_all([cancelled, queued])
        db.flush()
        stranded.active_turn_id = cancelled.id
        queued_id = queued.id
        db.commit()

    send = MagicMock(return_value=True)
    monkeypatch.setattr(
        workflow_service.terminal_service,
        "get_terminal",
        lambda *_: {"lifecycle": "running", "status": TerminalStatus.IDLE.value},
    )
    monkeypatch.setattr(
        workflow_service.terminal_service,
        "provider_runtime_sidecar_reconnect_required",
        lambda *_: False,
    )
    monkeypatch.setattr(workflow_service.terminal_service, "send_input", send)

    assert inbox_service.reconcile_provider_execution_queue() == 1
    assert inbox_service.reconcile_provider_execution_queue() == 0
    assert send.call_count == 1
    assert f"logical-turn={queued_id}" in send.call_args.args[1]
    assert send.call_args.args[1].endswith("deliver the existing owner continuation")
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).order_by(WorkflowModel.id.desc()).first()
        turn = db.get(WorkflowTurnModel, queued_id)
        assert workflow.status == "open"
        assert workflow.active_turn_id == queued_id
        assert turn.state == "sent"
        assert turn.attempt_count == 1


def test_f13_authoritative_child_terminalization_fences_pending_inbox(workflow_db):
    """Result finalization closes the child and its exact transport together."""
    parent = "parent-child-terminal-inbox-fence"
    child = "child-terminal-inbox-fence"
    _start_admitted_input(parent)
    assert register_child_assignment(parent, child)
    callback = _authorized_callback(child)
    stale, stale_turn = _pending_inbox_turn(child, "late child transport")

    notice, duplicate = create_child_assignment_result_message(
        child, parent, "authoritative child result", **callback
    )
    assert notice is not None and duplicate is False

    with database.SessionLocal() as db:
        child_workflow = db.query(WorkflowModel).filter_by(root_terminal_id=child).one()
        assert child_workflow.status == "terminal"
        assert db.get(WorkflowTurnModel, stale_turn).state == "cancelled"
        assert db.get(InboxModel, stale.id).status == "failed"


def test_f13_restart_reconciles_closed_inbox_before_queued_owner_input_once(
    workflow_db, monkeypatch
):
    """Pre-fix persisted FIFO state advances automatically and exactly once."""
    parent = "parent-closed-inbox-restart"
    _start_admitted_input(parent)
    stale = create_inbox_message("sender", parent, "stale closed-workflow input")
    stale_turn = database.ensure_workflow_turn_for_inbox(stale.id)
    assert stale_turn is not None
    assert set_workflow_terminal_state(parent, "terminal", "closed before restart")

    owner_payload = "resume the queued owner mission"
    with database.SessionLocal() as db:
        # Reconstruct the durable state left by a pre-fix process at restart.
        db.get(InboxModel, stale.id).status = "pending"
        replacement = WorkflowModel(root_terminal_id=parent, status="open")
        db.add(replacement)
        db.flush()
        owner_turn = WorkflowTurnModel(
            workflow_id=replacement.id,
            kind="external_input",
            dedupe_key="external:pre-fix-persisted-owner",
            payload=owner_payload,
            state="queued",
        )
        db.add(owner_turn)
        db.commit()
        owner_turn_id = owner_turn.id

    provider = MagicMock()
    provider.get_status.return_value = TerminalStatus.IDLE
    provider.is_process_alive.return_value = True
    terminal_state = {"lifecycle": "running", "status": TerminalStatus.COMPLETED.value}
    with (
        patch(
            "cli_agent_orchestrator.services.inbox_service.provider_manager.get_provider",
            return_value=provider,
        ),
        patch(
            "cli_agent_orchestrator.services.workflow_service.terminal_service.get_terminal",
            return_value=terminal_state,
        ),
        patch.object(workflow_service, "reconcile_open_workflows", return_value=0),
        patch("cli_agent_orchestrator.services.inbox_service.terminal_service.send_input") as send,
    ):
        assert reconcile_pending_messages() == 1
        assert claim_workflow_turn_receipt(parent, owner_turn_id)
        assert reconcile_pending_messages() == 0

    assert send.call_count == 1
    assert send.call_args.args[1].endswith(owner_payload)
    assert f"logical-turn={owner_turn_id}" in send.call_args.args[1]
    with database.SessionLocal() as db:
        assert db.get(InboxModel, stale.id).status == "failed"
        persisted_owner = db.get(WorkflowTurnModel, owner_turn_id)
        assert persisted_owner is not None
        assert persisted_owner.state == "sent"


def test_f13_cancelled_result_notice_cannot_strand_next_open_handoff_boundary(workflow_db):
    """A fenced old callback must not occupy FIFO ahead of a recoverable batch.

    This is the live-regression shape: the parent starts a new admitted
    workflow after an earlier owner gate, three managed handoff results are
    durable and queued, restart rehydration requeues their notices, and one
    safe boundary delivers exactly one batch.  Replays leave one result per
    child, one callback turn, and one acknowledgement per child.
    """
    parent = "parent-cancelled-head"
    stale_children = (
        "child-cancelled-head-one",
        "child-cancelled-head-two",
        "child-cancelled-head-three",
    )
    stale_turn = _start_admitted_input(parent)
    stale_notices = []
    for child in stale_children:
        assert register_handoff_child(parent, child)
        stale_notice, stale_duplicate = create_handoff_child_result_message(
            child, f"old owner-gated result {child}"
        )
        assert stale_notice is not None and stale_duplicate is False
        stale_notices.append(stale_notice)
    assert set_workflow_terminal_state(parent, "owner_gate", "owner decision") is True

    active_turn = start_workflow_input(parent)
    assert active_turn is not None and active_turn != stale_turn
    children = ("child-current-one", "child-current-two", "child-current-three")
    notices = []
    for child in children:
        assert register_handoff_child(parent, child)
        notice, duplicate = create_handoff_child_result_message(child, f"current result {child}")
        assert notice is not None and duplicate is False and notice.result_id is not None
        notices.append(notice)

    # This models restart rehydration of an already durable queued batch. It
    # cannot recreate the cancelled relation, logical results, or a callback.
    assert requeue_unacknowledged_child_assignment_results() == len(notices)

    provider = MagicMock()
    provider.get_status.return_value = TerminalStatus.IDLE
    provider.is_process_alive.return_value = True
    with (
        patch(
            "cli_agent_orchestrator.services.inbox_service.provider_manager.get_provider",
            return_value=provider,
        ),
        patch("cli_agent_orchestrator.services.inbox_service.terminal_service.send_input") as send,
    ):
        # The obsolete notice is terminalized locally, then the live batch
        # remains fenced until the current parent turn has genuinely admitted.
        assert check_and_send_pending_messages(parent) is False
        send.assert_not_called()
        assert claim_workflow_turn_receipt(parent, active_turn)
        assert check_and_send_pending_messages(parent) is True
        assert check_and_send_pending_messages(parent) is False

    assert send.call_count == 1
    payload = send.call_args.args[1]
    for notice in notices:
        assert f"result_id={notice.result_id}" in payload

    callback = get_workflow_turn_for_inbox(notices[0].id)
    assert callback is not None
    assert claim_workflow_turn_receipt(parent, callback["turn_id"])
    for child, notice in zip(children, notices):
        duplicate_notice, duplicate = create_handoff_child_result_message(child, "replayed")
        assert (
            duplicate is True and duplicate_notice is not None and duplicate_notice.id == notice.id
        )
        acknowledged = acknowledge_child_assignment_result_outcome(
            parent, child, result_id=notice.result_id
        )
        assert acknowledged["accepted"] is True
        replay = acknowledge_child_assignment_result_outcome(
            parent, child, result_id=notice.result_id
        )
        assert replay["reason_code"] == "RESULT_ALREADY_ACKNOWLEDGED"

    assert get_parent_completion_barrier(parent) == (0, 0)
    assert workflow_service.complete_workflow(parent, "all results incorporated") is True
    assert get_workflow_status(parent) == "terminal"
    with database.SessionLocal() as db:
        assert {
            db.query(InboxModel).filter_by(id=stale_notice.id).one().status
            for stale_notice in stale_notices
        } == {"failed"}
        assert db.query(InboxModel).filter_by(status="delivered").count() == len(notices)
        assert (
            db.query(WorkflowTurnModel)
            .filter_by(kind="handoff_result", workflow_id=callback["workflow_id"])
            .count()
            == 1
        )


@pytest.mark.parametrize(
    "close",
    [
        lambda root: set_workflow_terminal_state(root, "terminal", "done"),
        lambda root: set_workflow_terminal_state(root, "owner_gate", "owner"),
        cancel_workflows_for_terminal,
    ],
)
def test_f13_closed_parent_suppresses_queued_handoff_notice_without_a_callback(workflow_db, close):
    """A queued result never revives a terminal, owner-gated, or cancelled parent."""
    parent = f"parent-ineligible-{close.__name__}"
    child = f"child-ineligible-{close.__name__}"
    start_workflow_input(parent)
    assert register_handoff_child(parent, child)
    notice, duplicate = create_handoff_child_result_message(child, "queued before parent close")
    assert notice is not None and duplicate is False
    assert close(parent)

    provider = MagicMock()
    provider.get_status.return_value = TerminalStatus.IDLE
    provider.is_process_alive.return_value = True
    with (
        patch(
            "cli_agent_orchestrator.services.inbox_service.provider_manager.get_provider",
            return_value=provider,
        ),
        patch("cli_agent_orchestrator.services.inbox_service.terminal_service.send_input") as send,
    ):
        assert check_and_send_pending_messages(parent) is False
        send.assert_not_called()

    with database.SessionLocal() as db:
        assert db.query(InboxModel).filter_by(id=notice.id).one().status == "failed"
        assert (
            db.query(WorkflowTurnModel).filter_by(inbox_message_id=notice.id).one().state
            == "cancelled"
        )


def test_f13_handoff_batch_fences_late_result_until_first_receipt(workflow_db):
    """A claimed transport owns the parent until its receiver admits it."""
    parent, first_child, late_child = "parent-sealed", "child-sealed-one", "child-sealed-late"
    active_turn = _start_admitted_input(parent)
    assert register_handoff_child(parent, first_child)
    assert register_handoff_child(parent, late_child)
    first, duplicate = create_handoff_child_result_message(first_child, "first handoff")
    assert first is not None and duplicate is False

    provider = MagicMock()
    provider.get_status.return_value = TerminalStatus.IDLE
    provider.is_process_alive.return_value = True
    late = None

    def finalize_late_result(*_args, **_kwargs):
        nonlocal late
        if late is not None:
            return
        late, duplicate_late = create_handoff_child_result_message(late_child, "late handoff")
        assert late is not None and duplicate_late is False
        # Exercise the review's concurrent ready tick while the first batch
        # remains CLAIMED inside send_input(). It may not emit a successor.
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(check_and_send_pending_messages, parent).result() is False

    with (
        patch(
            "cli_agent_orchestrator.services.inbox_service.provider_manager.get_provider",
            return_value=provider,
        ),
        patch(
            "cli_agent_orchestrator.services.inbox_service.terminal_service.send_input",
            side_effect=finalize_late_result,
        ) as send,
    ):
        assert check_and_send_pending_messages(parent) is True
        assert late is not None
        first_payload = send.call_args.args[1]
        assert f"result_id={first.result_id}" in first_payload
        assert f"result_id={late.result_id}" not in first_payload

        first_callback = get_workflow_turn_for_inbox(first.id)
        late_callback = get_workflow_turn_for_inbox(late.id)
        assert first_callback is not None
        assert late_callback is None
        # The first callback has not yet been admitted, so the late result is
        # deferred without minting a successor or sending plain Inbox text.
        assert check_and_send_pending_messages(parent) is False
        assert send.call_count == 1

        assert claim_workflow_turn_receipt(parent, first_callback["turn_id"])
        assert acknowledge_child_assignment_result(parent, first_child, result_id=first.result_id)
        assert materialize_deferred_handoff_result_turn_for_inbox(late.id) is True
        assert check_and_send_pending_messages(parent) is True
        assert send.call_count == 2
        late_callback = get_workflow_turn_for_inbox(late.id)
        assert late_callback is not None
        assert first_callback["turn_id"] != late_callback["turn_id"]
        late_payload = send.call_args.args[1]
        assert f"result_id={late.result_id}" in late_payload
        assert f"logical-turn={late_callback['turn_id']}" in late_payload

    assert claim_workflow_turn_receipt(parent, late_callback["turn_id"])
    acknowledged = acknowledge_child_assignment_result_outcome(
        parent, late_child, result_id=late.result_id
    )
    repeated = acknowledge_child_assignment_result_outcome(
        parent, late_child, result_id=late.result_id
    )
    assert acknowledged["accepted"] is True
    assert repeated["accepted"] is False
    assert repeated["reason_code"] == "RESULT_ALREADY_ACKNOWLEDGED"
    assert get_parent_completion_barrier(parent) == (0, 0)
    with database.SessionLocal() as db:
        callbacks = db.query(WorkflowTurnModel).filter_by(kind="handoff_result").all()
        assert len(callbacks) == 2
        assert db.query(InboxModel).filter_by(status="delivered").count() == 2


def test_f13_owner_gate_fences_late_sealed_handoff_result(workflow_db):
    """Closing after the first send cannot wake a late result's next turn."""
    parent, first_child, late_child = (
        "parent-sealed-gate",
        "child-sealed-gate-one",
        "child-sealed-gate-late",
    )
    _start_admitted_input(parent)
    assert register_handoff_child(parent, first_child)
    assert register_handoff_child(parent, late_child)
    first, duplicate = create_handoff_child_result_message(first_child, "first handoff")
    assert first is not None and duplicate is False

    provider = MagicMock()
    provider.get_status.return_value = TerminalStatus.IDLE
    provider.is_process_alive.return_value = True

    late = None

    def finalize_late_result(*_args, **_kwargs):
        nonlocal late
        if late is not None:
            return
        late, duplicate_late = create_handoff_child_result_message(late_child, "late handoff")
        assert late is not None and duplicate_late is False

    with (
        patch(
            "cli_agent_orchestrator.services.inbox_service.provider_manager.get_provider",
            return_value=provider,
        ),
        patch(
            "cli_agent_orchestrator.services.inbox_service.terminal_service.send_input",
            side_effect=finalize_late_result,
        ) as send,
    ):
        assert check_and_send_pending_messages(parent) is True
        assert send.call_count == 1
        assert late is not None and get_workflow_turn_for_inbox(late.id) is None
        assert set_workflow_terminal_state(parent, "owner_gate", "review race fence")
        assert check_and_send_pending_messages(parent) is False
        assert send.call_count == 1

    with database.SessionLocal() as db:
        assert (
            db.query(WorkflowTurnModel).filter_by(kind="handoff_result", state="cancelled").count()
            == 0
        )
        assert db.query(InboxModel).filter_by(status="failed").count() == 1


def test_f13_deferred_handoff_result_recovery_retries_same_unadmitted_turn(workflow_db):
    """Lease recovery retries the in-flight turn before a deferred successor."""
    parent, first_child, late_child = (
        "parent-deferred-recovery",
        "child-deferred-one",
        "child-deferred-late",
    )
    _start_admitted_input(parent)
    assert register_handoff_child(parent, first_child)
    assert register_handoff_child(parent, late_child)
    first, duplicate = create_handoff_child_result_message(first_child, "first handoff")
    assert first is not None and duplicate is False
    now = datetime(2026, 8, 15, 12, 0, 0)

    first_claim = claim_handoff_result_batch_for_inbox(first.id, now=now)
    assert isinstance(first_claim, dict)
    late, duplicate_late = create_handoff_child_result_message(late_child, "late handoff")
    assert late is not None and duplicate_late is False
    assert get_workflow_turn_for_inbox(late.id) is None

    assert mark_workflow_turn_sent(
        first_claim["id"],
        first_claim["claim_token"],
        first_claim["claim_generation"],
        now=now,
    )
    assert database.update_message_status(first.id, database.MessageStatus.DELIVERED)
    assert requeue_unadmitted_workflow_turns_for_restart(now=now + timedelta(seconds=30)) == 1
    assert database.requeue_unacknowledged_child_assignment_results() == 2

    provider = MagicMock()
    provider.get_status.return_value = TerminalStatus.IDLE
    provider.is_process_alive.return_value = True
    with (
        patch(
            "cli_agent_orchestrator.services.inbox_service.provider_manager.get_provider",
            return_value=provider,
        ),
        patch("cli_agent_orchestrator.services.inbox_service.terminal_service.send_input") as send,
    ):
        # Restart recovery transports the same unadmitted first callback,
        # never a successor for the late result.
        assert check_and_send_pending_messages(parent) is True
        assert send.call_count == 1
        first_payload = send.call_args.args[1]
        assert f"logical-turn={first_claim['id']}" in first_payload
        assert claim_workflow_turn_receipt(
            parent, first_claim["id"], now=now + timedelta(seconds=30)
        )
        assert acknowledge_child_assignment_result(parent, first_child, result_id=first.result_id)
        assert check_and_send_pending_messages(parent) is True
    assert send.call_count == 2
    late_callback = get_workflow_turn_for_inbox(late.id)
    assert late_callback is not None and late_callback["turn_id"] != first_claim["id"]


@patch("cli_agent_orchestrator.services.inbox_service.workflow_service.reconcile_root_workflow")
@patch("cli_agent_orchestrator.services.inbox_service.check_and_send_pending_messages")
@patch(
    "cli_agent_orchestrator.services.inbox_service.handoff_child_cleanup_acknowledged",
    return_value=True,
)
@patch(
    "cli_agent_orchestrator.services.inbox_service.handoff_child_cleanup_is_acknowledged",
    side_effect=[False, True],
)
@patch("cli_agent_orchestrator.services.inbox_service.terminal_service")
def test_f13_normal_no_restart_boundary_captures_once_then_wakes_same_parent(
    mock_terminal, _cleanup_done, _cleanup_ack, mock_deliver, _workflow_reconcile, workflow_db
):
    start_workflow_input("parent-live-boundary")
    register_handoff_child("parent-live-boundary", "child-live-boundary")
    complete = {"status": TerminalStatus.COMPLETED.value, "lifecycle": "running"}
    mock_terminal.get_terminal.side_effect = [complete, complete, complete]
    mock_terminal.get_output.return_value = "stable child result"
    mock_terminal.exit_terminal.return_value = _confirmed_exit_result()

    assert reconcile_handoff_continuations() == 1
    mock_deliver.assert_called_once_with("parent-live-boundary", registry=None)
    with database.SessionLocal() as db:
        assert db.query(InboxModel).count() == 1
        assert (
            db.query(WorkflowTurnModel).filter(WorkflowTurnModel.kind == "handoff_result").count()
            == 1
        )


def test_f14_parent_completion_captures_v1_child_before_successor_replaces_mode_last(
    workflow_db,
):
    """A parent's automatic successor must not replace its child's V1 result."""
    parent, child = "parent-capture-fence", "child-capture-fence"
    document = {
        "summary": "captured before successor",
        "body_markdown": "The completed child report.",
        "changed_files": [],
        "checks": [{"command": "pytest", "result": "passed"}],
        "risks": [],
        "blockers": [],
        "format": "v1",
    }
    completed_child_output = f"CAO_RESULT_V1\n{json.dumps(document)}"
    successor_legacy_prose = "The automatic successor is now the last output."
    start_workflow_input(parent)
    assert register_handoff_child(parent, child)

    complete = {"status": TerminalStatus.COMPLETED.value, "lifecycle": "running"}
    captured_result_ids = []
    with (
        patch("cli_agent_orchestrator.services.inbox_service.terminal_service") as mock_terminal,
        patch("cli_agent_orchestrator.services.inbox_service.check_and_send_pending_messages"),
        patch(
            "cli_agent_orchestrator.services.inbox_service.workflow_service.reconcile_root_workflow"
        ) as mock_reconcile_root,
        patch(
            "cli_agent_orchestrator.services.inbox_service._has_idle_pattern", return_value=False
        ),
    ):
        mock_terminal.get_terminal.return_value = complete
        mock_terminal.get_output.return_value = completed_child_output

        def automatic_successor(root_terminal_id, **_kwargs):
            assert root_terminal_id == parent
            result = get_delegation_result_for_assignment(child)
            assert result is not None
            assert result["document"] == document
            captured_result_ids.append(result["id"])
            # This is what a later live mode=last read would observe if the
            # capture fence had allowed the successor to run first.
            mock_terminal.get_output.return_value = successor_legacy_prose
            return True

        mock_reconcile_root.side_effect = automatic_successor
        LogFileHandler()._handle_log_change(parent)

        captured = get_delegation_result_for_assignment(child)
        assert captured is not None
        assert captured["document"] == document
        first_result_id = captured["id"]
        assert reconcile_handoff_continuations() == 0
        assert get_delegation_result_for_assignment(child)["id"] == first_result_id

    assert captured_result_ids
    assert set(captured_result_ids) == {first_result_id}
    with database.SessionLocal() as db:
        assert db.query(InboxModel).count() == 1
        assert db.query(database.DelegationResultModel).count() == 1
        assert (
            db.query(WorkflowTurnModel).filter(WorkflowTurnModel.kind == "handoff_result").count()
            == 1
        )


def test_f14_unresolved_handoff_child_reconcile_defers_successor_until_v1_capture(workflow_db):
    """A child's own F13 reconcile cannot overwrite output F14 still needs."""
    parent, child = "parent-child-reconcile", "child-child-reconcile"
    document = {
        "summary": "captured before child successor",
        "body_markdown": "The completed child report.",
        "changed_files": [],
        "checks": [{"command": "pytest", "result": "passed"}],
        "risks": [],
        "blockers": [],
        "format": "v1",
    }
    output = f"CAO_RESULT_V1\n{json.dumps(document)}"
    start_workflow_input(parent)
    start_workflow_input(child)
    assert register_handoff_child(parent, child)
    complete = {"status": TerminalStatus.COMPLETED.value, "lifecycle": "running"}

    with (
        patch(
            "cli_agent_orchestrator.services.workflow_service.terminal_service"
        ) as workflow_terminal,
        patch("cli_agent_orchestrator.services.inbox_service.terminal_service") as inbox_terminal,
        patch("cli_agent_orchestrator.services.inbox_service.check_and_send_pending_messages"),
    ):
        workflow_terminal.get_terminal.return_value = complete
        # Exercise the real child-root F13 path.  Before F14 captures the
        # result, it must not send the child's automatic successor.
        assert workflow_service.reconcile_root_workflow(child) is False
        workflow_terminal.send_input.assert_not_called()

        inbox_terminal.get_terminal.return_value = complete
        inbox_terminal.get_output.return_value = output
        assert reconcile_handoff_continuations(child_terminal_id=child) == 1

    captured = get_delegation_result_for_assignment(child)
    assert captured is not None
    assert captured["document"] == document
    with database.SessionLocal() as db:
        assert (
            db.query(WorkflowTurnModel).filter(WorkflowTurnModel.kind == "open_final").count() == 0
        )


def test_f552_expired_live_handoff_defers_f13_until_recovery_queues_one_stable_v1(
    workflow_db,
):
    """A slice expiry cannot cancel or advance a live direct handoff.

    The later recovery capture owns the valid V1 and creates exactly one Inbox
    continuation; F13 never reads, claims, or hides the child's last output.
    """
    parent, child = "parent-f552", "child-f552"
    document = {
        "summary": "recovered after expired wait slice",
        "body_markdown": "The later stable V1 is the one durable result.",
        "changed_files": [],
        "checks": [{"command": "pytest f552", "result": "passed"}],
        "risks": [],
        "blockers": [],
        "format": "v1",
    }
    stable_v1 = f"• CAO_RESULT_V1\n  {json.dumps(document)}\n{'─' * 80}"

    start_workflow_input(parent)
    assert start_workflow_input(child) is not None
    assert register_handoff_child(parent, child)

    # F11's live slice expires while the child is still live.  Its registered
    # result remains awaiting and is recoverable by the same child ID.
    with (
        patch.dict(os.environ, {"CAO_TERMINAL_ID": parent}, clear=False),
        patch(
            "cli_agent_orchestrator.mcp_server.server._read_handoff_terminal",
            return_value=(TerminalStatus.PROCESSING.value, "running"),
        ),
    ):
        wait = asyncio.run(mcp_server._await_handoff_impl(child, timeout=0))
    assert wait.state.value == "waiting"
    assert wait.terminal_id == child
    assert get_delegation_result_for_assignment(child)["status"] == "awaiting"

    # F13 sees a completed child but must not take ownership of its output or
    # create an open_final successor while the direct relation is unresolved.
    completed = {"status": TerminalStatus.COMPLETED.value, "lifecycle": "running"}
    with patch(
        "cli_agent_orchestrator.services.workflow_service.terminal_service"
    ) as mock_terminal:
        mock_terminal.get_terminal.return_value = completed
        assert workflow_service.reconcile_root_workflow(child) is False
        mock_terminal.get_output.assert_not_called()

    # Once recovery obtains two matching valid captures, it alone creates the
    # one durable result and the one parent continuation wake.
    with (
        patch("cli_agent_orchestrator.services.inbox_service.terminal_service") as mock_terminal,
        patch(
            "cli_agent_orchestrator.services.inbox_service.check_and_send_pending_messages"
        ) as deliver,
        patch(
            "cli_agent_orchestrator.services.inbox_service.workflow_service.reconcile_root_workflow"
        ) as reconcile_parent,
    ):
        mock_terminal.get_terminal.side_effect = [completed, completed, completed]
        mock_terminal.get_output.side_effect = [stable_v1, stable_v1]
        mock_terminal.exit_terminal.return_value = _confirmed_exit_result()
        assert reconcile_handoff_continuations(child_terminal_id=child) == 1
        assert reconcile_handoff_continuations(child_terminal_id=child) == 0
        mock_terminal.exit_terminal.assert_called_once_with(child)
        deliver.assert_called_once_with(parent, registry=None)
        reconcile_parent.assert_called_once_with(parent, registry=None)

    recovered = get_delegation_result_for_assignment(child)
    assert recovered is not None
    assert recovered["status"] == "complete"
    assert recovered["document"] == document
    with database.SessionLocal() as db:
        child_workflow = db.query(WorkflowModel).filter_by(root_terminal_id=child).one()
        assert (
            db.query(WorkflowTurnModel)
            .filter(WorkflowTurnModel.workflow_id == child_workflow.id)
            .filter(WorkflowTurnModel.kind == "open_final")
            .count()
            == 0
        )
        assert (
            db.query(database.DelegationResultEventModel)
            .filter_by(result_id=recovered["id"], event_type="completed")
            .count()
            == 1
        )
        assert (
            db.query(database.DelegationResultEventModel)
            .filter_by(result_id=recovered["id"], event_type="cancelled")
            .count()
            == 0
        )
        assert db.query(InboxModel).count() == 1
        assert (
            db.query(WorkflowTurnModel).filter(WorkflowTurnModel.kind == "handoff_result").count()
            == 1
        )


@pytest.mark.parametrize(
    "output",
    [
        'CAO_RESULT_V1\n{"summary":',
        "ordinary completion prose",
        '```json\nCAO_RESULT_V1\n{"summary": "fenced"}\n```',
        "• Working (11s • esc to interrupt)",
    ],
)
@patch("cli_agent_orchestrator.services.workflow_service.terminal_service")
def test_d9_invalid_or_restart_before_claim_never_survives_parent_cancellation(
    mock_terminal, workflow_db, output
):
    """No pre-cancel direct claim means no result can be recreated later."""
    parent, child = f"parent-d9-invalid-{hash(output)}", f"child-d9-invalid-{hash(output)}"
    start_workflow_input(parent)
    start_workflow_input(child)
    assert register_handoff_child(parent, child)
    assert arm_handoff_continuations_for_restart() == 1
    mock_terminal.get_terminal.return_value = {
        "status": TerminalStatus.COMPLETED.value,
        "lifecycle": "running",
    }
    mock_terminal.get_output.return_value = output

    # Recovery status cannot use the live direct-claim path, and malformed,
    # prose, fenced, and progress output never satisfies the exact V1 gate.
    assert workflow_service.reconcile_root_workflow(child) is False
    assert get_delegation_result_for_assignment(child)["status"] == "awaiting"
    assert cancel_workflows_for_terminal(parent) == 1

    result = get_delegation_result_for_assignment(child)
    assert result is not None and result["status"] == "cancelled"
    assert reconcile_handoff_continuations(child_terminal_id=child) == 0
    with database.SessionLocal() as db:
        assert db.query(InboxModel).count() == 0
        assert (
            db.query(database.DelegationResultEventModel)
            .filter_by(result_id=result["id"], event_type="cancelled")
            .count()
            == 1
        )


@patch("cli_agent_orchestrator.services.inbox_service.workflow_service.reconcile_root_workflow")
@patch("cli_agent_orchestrator.services.inbox_service.check_and_send_pending_messages")
@patch("cli_agent_orchestrator.services.inbox_service.terminal_service")
def test_f14_restart_recovery_repolls_completed_provider_and_finalizes_v1_once(
    mock_terminal, mock_deliver, _workflow_reconcile, workflow_db
):
    """A later daemon tick captures delayed completion exactly once after P, P, P."""
    parent, child = "parent-restart-v1", "child-restart-v1"
    document = {
        "summary": "recovered",
        "body_markdown": "Recovered after restart.",
        "changed_files": [],
        "checks": [{"command": "pytest", "result": "passed"}],
        "risks": [],
        "blockers": [],
        "format": "v1",
    }
    output = f"CAO_RESULT_V1\n{json.dumps(document)}"
    start_workflow_input(parent)
    assert register_handoff_child(parent, child)
    assert arm_handoff_continuations_for_restart() == 1
    mock_terminal.get_terminal.side_effect = [
        {"status": TerminalStatus.PROCESSING.value, "lifecycle": "running"},
        {"status": TerminalStatus.PROCESSING.value, "lifecycle": "running"},
        {"status": TerminalStatus.PROCESSING.value, "lifecycle": "running"},
        {"status": TerminalStatus.COMPLETED.value, "lifecycle": "running"},
        {"status": TerminalStatus.COMPLETED.value, "lifecycle": "running"},
    ]
    mock_terminal.get_output.side_effect = [output, output]
    mock_terminal.exit_terminal.return_value = _confirmed_exit_result()

    assert reconcile_handoff_continuations() == 0
    assert reconcile_handoff_continuations() == 1

    recovered = get_delegation_result_for_assignment(child)
    assert recovered is not None
    assert recovered["status"] == "complete"
    assert recovered["document"] == document
    first_result_id = recovered["id"]
    assert reconcile_handoff_continuations() == 0
    assert get_delegation_result_for_assignment(child)["id"] == first_result_id
    assert mock_terminal.get_terminal.call_count == 5
    mock_terminal.exit_terminal.assert_called_once_with(child)
    mock_deliver.assert_called_once_with(parent, registry=None)
    with database.SessionLocal() as db:
        assert db.query(InboxModel).count() == 1
        assert (
            db.query(WorkflowTurnModel).filter(WorkflowTurnModel.kind == "handoff_result").count()
            == 1
        )


@pytest.mark.parametrize(
    "close",
    [
        lambda parent: set_workflow_terminal_state(parent, "owner_gate", "owner"),
        cancel_workflows_for_terminal,
    ],
)
@patch("cli_agent_orchestrator.services.inbox_service.workflow_service.reconcile_root_workflow")
@patch("cli_agent_orchestrator.services.inbox_service.check_and_send_pending_messages")
@patch("cli_agent_orchestrator.services.inbox_service.terminal_service")
def test_f14_delayed_restart_completion_cannot_revive_cancelled_or_owner_gated_parent(
    mock_terminal, mock_deliver, _workflow_reconcile, workflow_db, close
):
    """A P, P, P restart tick followed by closure fences the later completed child."""
    parent, child = "parent-delayed-close", "child-delayed-close"
    start_workflow_input(parent)
    assert register_handoff_child(parent, child)
    assert arm_handoff_continuations_for_restart() == 1
    mock_terminal.get_terminal.side_effect = [
        {"status": TerminalStatus.PROCESSING.value, "lifecycle": "running"},
        {"status": TerminalStatus.PROCESSING.value, "lifecycle": "running"},
        {"status": TerminalStatus.PROCESSING.value, "lifecycle": "running"},
    ]

    assert reconcile_handoff_continuations() == 0
    assert close(parent)
    assert reconcile_handoff_continuations() == 0

    result = get_delegation_result_for_assignment(child)
    assert result is not None and result["status"] == "cancelled"
    mock_deliver.assert_not_called()
    with database.SessionLocal() as db:
        assert db.query(InboxModel).count() == 0
        assert (
            db.query(WorkflowTurnModel).filter(WorkflowTurnModel.kind == "handoff_result").count()
            == 0
        )


@patch("cli_agent_orchestrator.services.inbox_service.workflow_service.reconcile_root_workflow")
@patch("cli_agent_orchestrator.services.inbox_service.check_and_send_pending_messages")
@patch("cli_agent_orchestrator.services.inbox_service.terminal_service")
def test_f14_restart_recovery_stays_processing_after_two_rereads_without_effect(
    mock_terminal, mock_deliver, _workflow_reconcile, workflow_db
):
    """P, P, P recovery consumes no output and creates no durable effect."""
    parent, child = "parent-restart-pending", "child-restart-pending"
    start_workflow_input(parent)
    assert register_handoff_child(parent, child)
    assert arm_handoff_continuations_for_restart() == 1
    mock_terminal.get_terminal.side_effect = [
        {"status": TerminalStatus.PROCESSING.value, "lifecycle": "running"},
        {"status": TerminalStatus.PROCESSING.value, "lifecycle": "running"},
        {"status": TerminalStatus.PROCESSING.value, "lifecycle": "running"},
    ]

    assert reconcile_handoff_continuations() == 0

    assert get_delegation_result_for_assignment(child)["status"] == "awaiting"
    assert mock_terminal.get_terminal.call_count == 3
    mock_terminal.get_output.assert_not_called()
    mock_terminal.exit_terminal.assert_not_called()
    mock_deliver.assert_not_called()
    with database.SessionLocal() as db:
        assert db.query(InboxModel).count() == 0
        assert (
            db.query(WorkflowTurnModel).filter(WorkflowTurnModel.kind == "handoff_result").count()
            == 0
        )


@patch("cli_agent_orchestrator.services.inbox_service.workflow_service.reconcile_root_workflow")
@patch("cli_agent_orchestrator.services.inbox_service.check_and_send_pending_messages")
@patch("cli_agent_orchestrator.services.inbox_service.terminal_service")
def test_f14_restart_recovery_captures_completed_exited_final_once(
    mock_terminal, mock_deliver, _workflow_reconcile, workflow_db
):
    """An exited child with a stable final must not remain recovery-awaiting."""
    parent, child = "parent-exited-final", "child-exited-final"
    start_workflow_input(parent)
    assert register_handoff_child(parent, child)
    assert arm_handoff_continuations_for_restart() == 1
    mock_terminal.get_terminal.return_value = {
        "status": TerminalStatus.COMPLETED.value,
        "lifecycle": "exited",
    }
    mock_terminal.get_output.side_effect = ["exited child final", "exited child final"]

    assert reconcile_handoff_continuations() == 1
    assert reconcile_handoff_continuations() == 0

    result = get_delegation_result_for_assignment(child)
    assert result is not None
    assert result["status"] == "complete"
    assert result["document"]["body_markdown"] == "exited child final"
    assert get_parent_completion_barrier(parent) == (1, 0)
    mock_terminal.exit_terminal.assert_not_called()
    mock_deliver.assert_called_once_with(parent, registry=None)
    with database.SessionLocal() as db:
        assert db.query(InboxModel).count() == 1
        assert (
            db.query(WorkflowTurnModel).filter(WorkflowTurnModel.kind == "handoff_result").count()
            == 1
        )


@pytest.mark.parametrize("output", [None, "Working (11s • esc to interrupt)"])
@patch("cli_agent_orchestrator.services.inbox_service.check_and_send_pending_messages")
@patch("cli_agent_orchestrator.services.inbox_service.terminal_service")
def test_f14_restart_recovery_terminalizes_exited_child_without_valid_final(
    mock_terminal, mock_deliver, workflow_db, output
):
    """An exited child without a usable final has one explicit incomplete result."""
    parent, child = "parent-exited-invalid", "child-exited-invalid"
    start_workflow_input(parent)
    assert register_handoff_child(parent, child)
    assert arm_handoff_continuations_for_restart() == 1
    mock_terminal.get_terminal.return_value = {
        "status": TerminalStatus.COMPLETED.value,
        "lifecycle": "exited",
    }
    mock_terminal.get_output.return_value = output

    assert reconcile_handoff_continuations() == 0
    assert reconcile_handoff_continuations() == 0

    result = get_delegation_result_for_assignment(child)
    assert result is not None
    assert result["status"] == "incomplete"
    assert result["reason_code"] == "child_exited"
    assert get_parent_completion_barrier(parent) == (0, 0)
    mock_terminal.exit_terminal.assert_not_called()
    mock_deliver.assert_not_called()
    with database.SessionLocal() as db:
        assert db.query(InboxModel).count() == 0


def test_f13_restart_rehydrates_open_handoff_without_a_second_effect(workflow_db):
    start_workflow_input("parent-restart")
    register_handoff_child("parent-restart", "child-restart")
    assert get_pending_handoff_child_terminal_ids() == ["child-restart"]
    assert arm_handoff_continuations_for_restart() == 1

    result, duplicate = create_handoff_child_result_message("child-restart", "durable result")

    assert result is not None and duplicate is False
    assert get_pending_handoff_child_terminal_ids() == ["child-restart"]
    with database.SessionLocal() as db:
        assert db.query(InboxModel).count() == 1
        assert (
            db.query(WorkflowTurnModel).filter(WorkflowTurnModel.kind == "handoff_result").count()
            == 1
        )


@patch("cli_agent_orchestrator.services.workflow_service.terminal_service")
def test_f13_dispatches_once_only_after_ready_root(mock_terminal, workflow_db):
    _start_admitted_input("root-ready")
    now = datetime(2026, 8, 9, 12, 0, 0)
    observe_workflow_final("root-ready", now=now)
    mock_terminal.get_terminal.side_effect = [
        {"status": TerminalStatus.COMPLETED.value, "lifecycle": "running"},
        {"status": TerminalStatus.PROCESSING.value, "lifecycle": "running"},
    ]

    assert workflow_service.reconcile_root_workflow("root-ready", now=now) is True
    assert workflow_service.reconcile_root_workflow("root-ready", now=now) is False
    assert mock_terminal.send_input.call_count == 1


@patch("cli_agent_orchestrator.services.workflow_service.terminal_service")
def test_f13_context_compaction_stale_receipt_open_workflow_gets_one_fresh_turn(
    mock_terminal, workflow_db
):
    """A duplicate compacted delivery is inert, then Ready advances exactly once."""
    root = "root-context-compaction-duplicate"
    turn_id = _start_admitted_input(root)
    now = datetime(2026, 8, 22, 12, 0, 0)
    assert claim_workflow_turn_receipt(root, turn_id) is False
    assert get_workflow_status(root) == "open"
    mock_terminal.get_terminal.return_value = {
        "status": TerminalStatus.COMPLETED.value,
        "lifecycle": "running",
    }
    mock_terminal.provider_runtime_sidecar_reconnect_required.return_value = False

    assert workflow_service.reconcile_root_workflow(root, now=now) is True
    assert workflow_service.reconcile_root_workflow(root, now=now + timedelta(seconds=1)) is False
    assert mock_terminal.send_input.call_count == 1

    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=root).one()
        assert workflow.status == "open"
        successor = (
            db.query(WorkflowTurnModel).filter_by(workflow_id=workflow.id, kind="open_final").one()
        )
        assert successor.state == "sent"
        successor_id = successor.id
    assert claim_workflow_turn_receipt(root, successor_id) is True
    assert claim_workflow_turn_receipt(root, successor_id) is False


@patch("cli_agent_orchestrator.services.workflow_service.terminal_service")
def test_f13_stale_sidecar_requests_one_retryable_context_reinitialization(
    mock_terminal, workflow_db
):
    root = "root-sidecar-reconnect"
    reconnect_turn_id = _start_admitted_input(root)
    now = datetime(2026, 8, 22, 12, 0, 0)
    mock_terminal.get_terminal.return_value = {
        "status": TerminalStatus.COMPLETED.value,
        "lifecycle": "running",
    }
    mock_terminal.provider_runtime_sidecar_reconnect_required.side_effect = [
        True,
        False,
        False,
        False,
    ]
    request_attempts = []

    def reconnect_request(
        terminal_id,
        turn_id,
        resume_identity,
        *,
        registry,
        claim_token,
        attempt_token,
        attempt_state,
        side_effect_guard,
    ):
        request_attempts.append(attempt_token)
        assert terminal_id == root
        assert turn_id == reconnect_turn_id
        assert resume_identity == TEST_CODEX_RESUME_IDENTITY
        assert registry is None
        assert attempt_state == "reserved"
        if len(request_attempts) == 1:
            raise RuntimeError("service stopped after provider exit")
        assert database.mark_workflow_provider_reconnect_launch_dispatched(
            root, turn_id, claim_token, attempt_token, now=now + timedelta(seconds=1)
        )
        assert database.record_workflow_provider_reconnect_runtime_ready(
            root,
            attempt_token,
            ACTIVE_RUNTIME_GENERATION,
            4321,
            987654,
            now=now + timedelta(seconds=1),
        )
        assert database.record_workflow_provider_reconnect_output_boundary(
            root,
            attempt_token,
            11,
            22,
            333,
            now=now + timedelta(seconds=1),
        )

    mock_terminal.request_provider_runtime_sidecar_reconnect.side_effect = reconnect_request

    assert workflow_service.reconcile_root_workflow(root, now=now) is False
    # The failed attempt has an outcome; the next scheduler consumes one new
    # bounded attempt even though the volatile provider signal disappeared.
    assert workflow_service.reconcile_root_workflow(root, now=now + timedelta(seconds=1)) is False
    assert mock_terminal.request_provider_runtime_sidecar_reconnect.call_count == 2
    assert request_attempts[0] != request_attempts[1]
    assert mock_terminal.verify_provider_runtime_sidecar_resume_identity.call_count == 2
    mock_terminal.verify_provider_runtime_sidecar_resume_identity.assert_called_with(
        root, TEST_CODEX_RESUME_IDENTITY
    )
    assert get_workflow_status(root) == "open"
    with database.SessionLocal() as db:
        attempts = (
            db.query(WorkflowProviderReconnectAttemptModel)
            .order_by(WorkflowProviderReconnectAttemptModel.attempt_number)
            .all()
        )
        assert [(attempt.attempt_number, attempt.state) for attempt in attempts] == [
            (1, "failed"),
            (2, "succeeded"),
        ]

    # Once the exact runtime is back, the same OPEN workflow advances normally
    # without an owner wake or a duplicate reconnect.
    assert workflow_service.reconcile_root_workflow(root, now=now + timedelta(seconds=2)) is True
    assert workflow_service.reconcile_root_workflow(root, now=now + timedelta(seconds=3)) is False
    assert mock_terminal.request_provider_runtime_sidecar_reconnect.call_count == 2
    assert mock_terminal.send_input.call_count == 1


@patch("cli_agent_orchestrator.services.workflow_service.terminal_service")
def test_f13_processing_sidecar_fence_persists_intent_before_any_new_transport(
    mock_terminal, workflow_db
):
    """A tool-time fence survives the PROCESSING early return and restart."""
    root = "root-sidecar-processing-intent"
    active_turn_id = _start_admitted_input(root)
    now = datetime(2026, 8, 23, 12, 0, 0)
    mock_terminal.get_terminal.return_value = {
        "status": TerminalStatus.PROCESSING.value,
        "lifecycle": "running",
    }
    mock_terminal.provider_runtime_sidecar_reconnect_required.return_value = True

    assert workflow_service.reconcile_root_workflow(root, now=now) is False
    with database.SessionLocal() as db:
        turn = db.get(WorkflowTurnModel, active_turn_id)
        assert turn is not None
        assert turn.provider_reconnect_requested_at == now
        assert turn.provider_reconnect_claim_token is None
        assert db.query(WorkflowProviderReconnectAttemptModel).count() == 0
        assert db.query(WorkflowEffectModel).count() == 0

    workflow_db.dispose()
    assert database.workflow_provider_reconnect_pending(root) is True


@patch("cli_agent_orchestrator.services.workflow_service.terminal_service")
def test_f13_restart_recovers_fence_after_final_before_successor_admission(
    mock_terminal, workflow_db
):
    """A parser reload cannot deadlock behind its queued OPEN successor."""
    root = "root-sidecar-post-final-restart"
    reconnect_turn_id = _start_admitted_input(root)
    now = datetime(2026, 8, 23, 12, 30, 0)
    successor_id = observe_workflow_final(root, now=now)
    assert isinstance(successor_id, int)
    with database.SessionLocal() as db:
        reconnect_turn = db.get(WorkflowTurnModel, reconnect_turn_id)
        successor = db.get(WorkflowTurnModel, successor_id)
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=root).one()
        assert reconnect_turn is not None and reconnect_turn.state == "finished"
        assert successor is not None and successor.state == "queued"
        assert workflow.active_turn_id == reconnect_turn_id

    mock_terminal.get_terminal.return_value = {
        "status": TerminalStatus.COMPLETED.value,
        "lifecycle": "running",
    }
    mock_terminal.provider_runtime_sidecar_reconnect_required.side_effect = [True, False]

    def reconnect_request(
        terminal_id,
        turn_id,
        resume_identity,
        *,
        registry,
        claim_token,
        attempt_token,
        attempt_state,
        side_effect_guard,
    ):
        assert terminal_id == root
        assert turn_id == reconnect_turn_id
        assert resume_identity == TEST_CODEX_RESUME_IDENTITY
        assert registry is None
        assert attempt_state == "reserved"
        assert claim_workflow_turn(root, now=now) is None
        assert database.mark_workflow_provider_reconnect_launch_dispatched(
            root, turn_id, claim_token, attempt_token, now=now
        )
        assert database.record_workflow_provider_reconnect_runtime_ready(
            root,
            attempt_token,
            ACTIVE_RUNTIME_GENERATION,
            4321,
            987654,
            now=now,
        )
        assert database.record_workflow_provider_reconnect_output_boundary(
            root,
            attempt_token,
            11,
            22,
            333,
            now=now,
        )

    mock_terminal.request_provider_runtime_sidecar_reconnect.side_effect = reconnect_request

    assert workflow_service.reconcile_root_workflow(root, now=now) is False
    with database.SessionLocal() as db:
        reconnect_turn = db.get(WorkflowTurnModel, reconnect_turn_id)
        successor = db.get(WorkflowTurnModel, successor_id)
        attempt = db.query(WorkflowProviderReconnectAttemptModel).one()
        assert reconnect_turn is not None
        assert reconnect_turn.provider_reconnect_requested_at is None
        assert successor is not None and successor.state == "queued"
        assert attempt.workflow_turn_id == reconnect_turn_id
        assert attempt.state == "succeeded"

    assert workflow_service.reconcile_root_workflow(root, now=now + timedelta(seconds=1)) is True
    assert mock_terminal.request_provider_runtime_sidecar_reconnect.call_count == 1
    assert mock_terminal.send_input.call_count == 1
    with database.SessionLocal() as db:
        successor = db.get(WorkflowTurnModel, successor_id)
        assert successor is not None and successor.state == "sent"


@patch("cli_agent_orchestrator.services.workflow_service.terminal_service")
def test_f13_reconnects_after_successor_was_resource_requeued_without_duplicate(
    mock_terminal, workflow_db
):
    """A late stale-sidecar marker binds to the exact queued successor."""
    root = "root-sidecar-post-resource-requeue"
    predecessor_id = _start_admitted_input(root)
    now = datetime(2026, 8, 26, 13, 44, 0)
    successor_id = observe_workflow_final(root, now=now)
    assert isinstance(successor_id, int)
    claimed = claim_workflow_turn(root, now=now)
    assert claimed is not None and claimed["id"] == successor_id
    assert activate_workflow_turn(root, successor_id)
    assert requeue_workflow_turn(
        successor_id,
        claimed["claim_token"],
        claimed["claim_generation"],
        now=now,
        admission_reason_code="RESOURCE_HEALTH_REJECTED",
    )
    # Model the already-durable pre-fix row: its canonical dedupe edge is
    # present, but the explicit audit parent was not populated yet.
    with database.SessionLocal() as db:
        successor = db.get(WorkflowTurnModel, successor_id)
        assert successor is not None
        successor.resume_parent_turn_id = None
        db.commit()

    mock_terminal.get_terminal.return_value = {
        "status": TerminalStatus.COMPLETED.value,
        "lifecycle": "running",
    }
    mock_terminal.provider_runtime_sidecar_reconnect_required.side_effect = [
        True,
        False,
        False,
    ]

    def reconnect_request(
        terminal_id,
        turn_id,
        resume_identity,
        *,
        registry,
        claim_token,
        attempt_token,
        attempt_state,
        side_effect_guard,
    ):
        assert terminal_id == root
        assert turn_id == successor_id
        assert resume_identity == TEST_CODEX_RESUME_IDENTITY
        assert registry is None
        assert attempt_state == "reserved"
        assert claim_workflow_turn(root, now=now) is None
        assert database.mark_workflow_provider_reconnect_launch_dispatched(
            root, turn_id, claim_token, attempt_token, now=now
        )
        assert database.record_workflow_provider_reconnect_runtime_ready(
            root,
            attempt_token,
            ACTIVE_RUNTIME_GENERATION,
            4321,
            987654,
            now=now,
        )
        assert database.record_workflow_provider_reconnect_output_boundary(
            root,
            attempt_token,
            11,
            22,
            333,
            now=now,
        )

    mock_terminal.request_provider_runtime_sidecar_reconnect.side_effect = reconnect_request

    assert workflow_service.reconcile_root_workflow(root, now=now) is False
    assert workflow_service.reconcile_root_workflow(root, now=now + timedelta(seconds=31)) is True
    assert workflow_service.reconcile_root_workflow(root, now=now + timedelta(seconds=32)) is False

    assert mock_terminal.request_provider_runtime_sidecar_reconnect.call_count == 1
    assert mock_terminal.send_input.call_count == 1
    with database.SessionLocal() as db:
        predecessor = db.get(WorkflowTurnModel, predecessor_id)
        successor = db.get(WorkflowTurnModel, successor_id)
        attempts = db.query(WorkflowProviderReconnectAttemptModel).all()
        assert predecessor is not None and predecessor.state == "finished"
        assert successor is not None and successor.state == "sent"
        assert successor.resume_parent_turn_id == predecessor_id
        assert len(attempts) == 1
        assert attempts[0].workflow_turn_id == successor_id
        assert attempts[0].state == "succeeded"


def test_f13_restart_repairs_backward_open_final_and_admits_new_input_once(workflow_db):
    """2687-like stale authority cannot replay after a newer admitted turn."""
    root = "root-backward-compaction-repair"
    original = _start_admitted_input(root)
    stale_open_final = observe_workflow_final(root)
    assert isinstance(stale_open_final, int) and stale_open_final > original

    newer = start_workflow_input(root)
    assert newer is not None and newer > stale_open_final
    assert claim_workflow_turn_receipt(root, newer)
    assert isinstance(observe_workflow_final(root), int)
    pending = create_inbox_message("owner", root, "newest owner continuation")
    newest = database.ensure_workflow_turn_for_inbox(pending.id)
    assert newest is not None and newest > newer

    # Recreate the exact historical corruption observed after compaction: an
    # older synthetic turn is SENT without a receipt and owns active_turn_id
    # even though a newer turn already has a durable receiver receipt.
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=root).one()
        stale = db.get(WorkflowTurnModel, stale_open_final)
        assert stale is not None
        stale.state = "sent"
        workflow.active_turn_id = stale_open_final
        db.commit()

    repaired = database.reconcile_superseded_workflow_turns_for_restart()
    assert repaired >= 1
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=root).one()
        assert workflow.active_turn_id == newer
        assert db.get(WorkflowTurnModel, stale_open_final).state == "cancelled"

    assert activate_workflow_turn(root, stale_open_final) is False
    assert activate_workflow_turn_for_inbox(pending.id) == newest
    claim = claim_workflow_turn(root, inbox_message_id=pending.id)
    assert claim is not None and claim["id"] == newest
    assert mark_workflow_turn_sent(newest, claim["claim_token"], claim["claim_generation"])
    assert claim_workflow_turn_receipt(root, newest)
    assert not claim_workflow_turn_receipt(root, newest)

    effect = claim_workflow_effect(root, newest, "handoff", "one-live-child")
    assert effect is not None
    assert claim_workflow_effect(root, newest, "handoff", "one-live-child") is None
    assert register_handoff_child(root, "one-live-child")
    first, duplicate = create_handoff_child_result_message("one-live-child", "accepted once")
    replay, replay_duplicate = create_handoff_child_result_message(
        "one-live-child", "duplicate callback"
    )
    assert first is not None and duplicate is False
    assert replay is not None and replay.id == first.id and replay_duplicate is True
    with database.SessionLocal() as db:
        assert db.query(WorkflowEffectModel).filter_by(effect_kind="handoff").count() == 1
        assert db.query(database.DelegationResultModel).count() == 1


def test_f13_new_inbox_fences_claimed_synthetic_turn_before_receiver_admission(workflow_db):
    """An irreversible late paste cannot restore superseded old authority."""
    root = "root-claimed-open-final-race"
    admitted = _start_admitted_input(root)
    stale = observe_workflow_final(root)
    assert isinstance(stale, int) and stale > admitted
    claim = claim_workflow_turn(root)
    assert claim is not None and claim["id"] == stale
    assert activate_workflow_turn(root, stale)

    pending = create_inbox_message("owner", root, "new semantic authority")
    newest = database.ensure_workflow_turn_for_inbox(pending.id)
    assert newest is not None and newest > stale
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=root).one()
        assert workflow.active_turn_id == newest
        assert db.get(WorkflowTurnModel, stale).state == "cancelled"

    assert not claim_workflow_turn_receipt(root, stale)
    assert not mark_workflow_turn_sent(stale, claim["claim_token"], claim["claim_generation"])
    assert activate_workflow_turn_for_inbox(pending.id) == newest


def test_f13_concurrent_sidecar_reconnect_claims_admit_one_control_input(workflow_db):
    root = "root-sidecar-reconnect-concurrent"
    reconnect_turn_id = _start_admitted_input(root)
    now = datetime(2026, 8, 22, 12, 30, 0)
    barrier = Barrier(2)

    def claim(_attempt):
        barrier.wait()
        return database.claim_workflow_provider_reconnect(root, now=now)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(executor.map(claim, range(2)))

    admitted = [claim for claim in claims if claim is not None]
    assert len(admitted) == 1
    assert admitted[0]["turn_id"] == reconnect_turn_id
    assert admitted[0]["claimed_at"] == now
    assert admitted[0]["resume_identity"] == TEST_CODEX_RESUME_IDENTITY
    assert admitted[0]["resume_identity_authoritative"] is True
    assert len(admitted[0]["claim_token"]) == 32
    assert len(admitted[0]["attempt_token"]) == 32
    assert admitted[0]["attempt_number"] == 1
    assert admitted[0]["attempt_state"] == "reserved"


def test_f13_service_restart_preserves_authoritative_reconnect_identity(workflow_db):
    root = "root-sidecar-resume-identity"
    reconnect_turn_id = _start_admitted_input(root)
    now = datetime(2026, 8, 22, 12, 45, 0)
    claimed = database.claim_workflow_provider_reconnect(root, now=now)
    assert claimed is not None
    assert claimed["turn_id"] == reconnect_turn_id
    assert claimed["claimed_at"] == now
    assert claimed["resume_identity"] == TEST_CODEX_RESUME_IDENTITY
    assert claimed["resume_identity_authoritative"] is True
    original_token = claimed["claim_token"]
    attempt_token = claimed["attempt_token"]
    identity = TEST_CODEX_RESUME_IDENTITY
    assert database.persist_workflow_provider_reconnect_identity(
        root, reconnect_turn_id, original_token, attempt_token, identity
    )
    assert not database.persist_workflow_provider_reconnect_identity(
        root,
        reconnect_turn_id,
        original_token,
        attempt_token,
        "fedcba98-7654-3210-fedc-ba9876543210",
    )
    assert database.renew_workflow_provider_reconnect(
        root,
        reconnect_turn_id,
        original_token,
        now=now + timedelta(seconds=10),
    )
    assert database.claim_workflow_provider_reconnect(root, now=now + timedelta(seconds=31)) is None
    # A failed side effect leaves the lease and identity intact. Once its
    # bounded lease expires, a service restart opens a fresh DB connection and
    # reclaims the exact launch-bound ID.
    workflow_db.dispose()
    reclaimed = database.claim_workflow_provider_reconnect(root, now=now + timedelta(seconds=41))
    assert reclaimed is not None
    assert reclaimed["turn_id"] == reconnect_turn_id
    assert reclaimed["claimed_at"] == now + timedelta(seconds=41)
    assert reclaimed["resume_identity"] == identity
    assert reclaimed["claim_token"] != original_token
    assert reclaimed["attempt_token"] == attempt_token
    assert reclaimed["attempt_number"] == 1
    assert not database.renew_workflow_provider_reconnect(
        root,
        reconnect_turn_id,
        original_token,
        now=now + timedelta(seconds=42),
    )
    assert database.mark_workflow_provider_reconnect_launch_dispatched(
        root,
        reconnect_turn_id,
        reclaimed["claim_token"],
        attempt_token,
        now=now + timedelta(seconds=42),
    )
    assert database.record_workflow_provider_reconnect_runtime_ready(
        root,
        attempt_token,
        ACTIVE_RUNTIME_GENERATION,
        4321,
        987654,
        now=now + timedelta(seconds=42),
    )
    assert database.record_workflow_provider_reconnect_output_boundary(
        root,
        attempt_token,
        11,
        22,
        333,
        now=now + timedelta(seconds=42),
    )
    assert not database.record_workflow_provider_reconnect_output_boundary(
        root,
        attempt_token,
        11,
        22,
        334,
        now=now + timedelta(seconds=43),
    )
    boundary = database.get_latest_workflow_provider_reconnect_output_boundary(root)
    assert boundary is not None
    assert boundary["attempt_token"] == attempt_token
    assert boundary["output_log_offset"] == 333
    assert database.complete_workflow_provider_reconnect(
        root, reconnect_turn_id, reclaimed["claim_token"], attempt_token
    )
    assert not database.workflow_provider_reconnect_pending(root)


@patch("cli_agent_orchestrator.services.workflow_service.terminal_service")
def test_f13_wrong_persisted_resume_identity_fails_before_provider_dispatch(
    mock_terminal, workflow_db
):
    root = "root-sidecar-wrong-persisted-identity"
    _start_admitted_input(root)
    wrong_identity = "fedcba98-7654-3210-fedc-ba9876543210"
    with database.SessionLocal() as db:
        terminal = db.get(TerminalModel, root)
        terminal.provider_resume_identity = wrong_identity
        db.commit()
    mock_terminal.get_terminal.return_value = {
        "status": TerminalStatus.COMPLETED.value,
        "lifecycle": "running",
    }
    mock_terminal.provider_runtime_sidecar_reconnect_required.return_value = True
    mock_terminal.verify_provider_runtime_sidecar_resume_identity.side_effect = RuntimeError(
        "persisted identity is not owned by the foreground runtime"
    )

    assert workflow_service.reconcile_root_workflow(root) is False

    mock_terminal.verify_provider_runtime_sidecar_resume_identity.assert_called_once_with(
        root, wrong_identity
    )
    mock_terminal.request_provider_runtime_sidecar_reconnect.assert_not_called()
    with database.SessionLocal() as db:
        attempt = db.query(WorkflowProviderReconnectAttemptModel).one()
        assert attempt.resume_identity == wrong_identity
        assert attempt.state == "failed"
        assert attempt.launched_at is None


@patch("cli_agent_orchestrator.services.workflow_service.terminal_service")
def test_f13_missing_launch_identity_exhausts_without_any_provider_launch(
    mock_terminal, workflow_db
):
    root = "root-sidecar-missing-launch-identity"
    _start_admitted_input(root)
    with database.SessionLocal() as db:
        terminal = db.get(TerminalModel, root)
        terminal.provider_resume_identity = None
        terminal.provider_resume_runtime_generation = None
        db.commit()
    mock_terminal.get_terminal.return_value = {
        "status": TerminalStatus.COMPLETED.value,
        "lifecycle": "running",
    }
    mock_terminal.provider_runtime_sidecar_reconnect_required.return_value = True

    for offset in range(3):
        assert (
            workflow_service.reconcile_root_workflow(root, now=datetime(2026, 8, 22, 13, 0, offset))
            is False
        )

    assert get_workflow_status(root) == "owner_gate"
    mock_terminal.verify_provider_runtime_sidecar_resume_identity.assert_not_called()
    mock_terminal.request_provider_runtime_sidecar_reconnect.assert_not_called()
    with database.SessionLocal() as db:
        attempts = db.query(WorkflowProviderReconnectAttemptModel).all()
        assert len(attempts) == 3
        assert all(attempt.state == "failed" for attempt in attempts)
        assert all(attempt.launched_at is None for attempt in attempts)


def test_f13_reconnect_runtime_generation_mismatch_fails_closed(workflow_db):
    root = "root-sidecar-generation-fence"
    turn_id = _start_admitted_input(root)
    reconnect = database.claim_workflow_provider_reconnect(root)
    assert reconnect is not None
    assert database.mark_workflow_provider_reconnect_launch_dispatched(
        root,
        turn_id,
        reconnect["claim_token"],
        reconnect["attempt_token"],
    )
    wrong_generation = "0" * 64 if ACTIVE_RUNTIME_GENERATION != "0" * 64 else "1" * 64
    assert not database.record_workflow_provider_reconnect_runtime_ready(
        root,
        reconnect["attempt_token"],
        wrong_generation,
        4321,
        987654,
    )
    assert (
        database.get_workflow_provider_reconnect_runtime_ready(root, reconnect["attempt_token"])
        is None
    )
    assert database.record_workflow_provider_reconnect_runtime_ready(
        root,
        reconnect["attempt_token"],
        ACTIVE_RUNTIME_GENERATION,
        4321,
        987654,
    )


@patch("cli_agent_orchestrator.services.workflow_service.terminal_service")
def test_f13_reconnect_attempt_budget_survives_restart_and_stops_launches(
    mock_terminal, workflow_db
):
    root = "root-sidecar-attempt-exhaustion"
    turn_id = _start_admitted_input(root)
    stale, stale_turn = _pending_inbox_turn(root, "closed by reconnect exhaustion")
    now = datetime(2026, 8, 22, 14, 0, 0)

    tokens = []
    for attempt_number in range(1, 4):
        reconnect = database.claim_workflow_provider_reconnect(
            root, now=now + timedelta(seconds=attempt_number)
        )
        assert reconnect is not None and reconnect.get("exhausted") is not True
        assert reconnect["attempt_number"] == attempt_number
        tokens.append(reconnect["attempt_token"])
        assert database.mark_workflow_provider_reconnect_launch_dispatched(
            root,
            turn_id,
            reconnect["claim_token"],
            reconnect["attempt_token"],
            now=now + timedelta(seconds=attempt_number),
        )
        assert database.fail_workflow_provider_reconnect_attempt(
            root,
            turn_id,
            reconnect["claim_token"],
            reconnect["attempt_token"],
            "process_exited_before_runtime_ready",
            now=now + timedelta(seconds=attempt_number),
        )
        # Dispose every pooled connection to model an API/service restart; the
        # next attempt must come from the same durable budget.
        database.engine.dispose()
        assert get_workflow_status(root) == ("open" if attempt_number < 3 else "owner_gate")

    assert len(set(tokens)) == 3
    assert database.claim_workflow_provider_reconnect(root, now=now + timedelta(seconds=10)) is None
    with database.SessionLocal() as db:
        attempts = (
            db.query(WorkflowProviderReconnectAttemptModel)
            .filter_by(root_terminal_id=root)
            .order_by(WorkflowProviderReconnectAttemptModel.attempt_number)
            .all()
        )
        assert [attempt.state for attempt in attempts] == ["failed", "failed", "failed"]
        assert all(attempt.finished_at is not None for attempt in attempts)
        assert db.get(WorkflowTurnModel, stale_turn).state == "cancelled"
        assert db.get(InboxModel, stale.id).status == "failed"

    mock_terminal.get_terminal.return_value = {
        "status": TerminalStatus.COMPLETED.value,
        "lifecycle": "running",
    }
    mock_terminal.provider_runtime_sidecar_reconnect_required.return_value = True
    assert workflow_service.reconcile_root_workflow(root, now=now + timedelta(seconds=11)) is False
    mock_terminal.request_provider_runtime_sidecar_reconnect.assert_not_called()


def test_f13_historical_reconnect_exhaustion_fences_pending_inbox(workflow_db):
    """Recovered exhausted attempt history closes transport in the same transaction."""
    root = "root-historical-reconnect-exhaustion"
    active_turn = _start_admitted_input(root)
    stale, stale_turn = _pending_inbox_turn(root, "pre-restart reconnect transport")
    now = datetime(2026, 8, 24, 20, 0, 0)
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=root).one()
        for attempt_number in range(1, 4):
            db.add(
                WorkflowProviderReconnectAttemptModel(
                    workflow_id=workflow.id,
                    workflow_turn_id=active_turn,
                    root_terminal_id=root,
                    attempt_number=attempt_number,
                    attempt_token=f"historical-attempt-{attempt_number}",
                    state="failed",
                    outcome_code="process_exited_before_runtime_ready",
                    created_at=now,
                    finished_at=now,
                    updated_at=now,
                )
            )
        db.commit()

    exhausted = database.claim_workflow_provider_reconnect(root, now=now + timedelta(seconds=1))
    assert exhausted == {"exhausted": True, "turn_id": active_turn, "attempt_count": 3}
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=root).one()
        assert workflow.status == "owner_gate"
        assert db.get(WorkflowTurnModel, stale_turn).state == "cancelled"
        assert db.get(InboxModel, stale.id).status == "failed"


def test_f13_reconnect_and_input_transport_have_one_terminal_mutation_owner(workflow_db):
    root = "root-sidecar-input-race"
    _start_admitted_input(root)
    now = datetime.now()
    barrier = Barrier(2)

    def claim_reconnect():
        barrier.wait()
        return database.claim_workflow_provider_reconnect(root, now=now)

    def claim_transport():
        barrier.wait()
        return database.acquire_terminal_runtime_transport(root, now=now)

    with ThreadPoolExecutor(max_workers=2) as executor:
        reconnect_future = executor.submit(claim_reconnect)
        transport_future = executor.submit(claim_transport)
        reconnect = reconnect_future.result()
        transport = transport_future.result()

    assert (reconnect is None) != (transport is None)
    with database.SessionLocal() as db:
        terminal = db.get(TerminalModel, root)
        assert terminal is not None
        assert terminal.runtime_operation_kind in {"reconnect", "transport"}


def test_f13_external_input_queues_without_replacing_reconnect_owned_turn(workflow_db):
    root = "root-sidecar-queued-input"
    active_turn_id = _start_admitted_input(root)
    reconnect = database.claim_workflow_provider_reconnect(root, now=datetime.now())
    assert reconnect is not None

    prepared = database.prepare_workflow_input(root, "continue after recovery")

    assert prepared is not None and prepared["queued"] is True
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=root).one()
        queued = db.get(WorkflowTurnModel, prepared["turn_id"])
        assert workflow.active_turn_id == active_turn_id
        assert queued is not None
        assert queued.state == "queued"
        assert queued.payload == "continue after recovery"
    assert database.mark_workflow_provider_reconnect_launch_dispatched(
        root,
        active_turn_id,
        reconnect["claim_token"],
        reconnect["attempt_token"],
    )
    assert database.record_workflow_provider_reconnect_runtime_ready(
        root, reconnect["attempt_token"], ACTIVE_RUNTIME_GENERATION, 4321, 987654
    )
    assert database.record_workflow_provider_reconnect_output_boundary(
        root,
        reconnect["attempt_token"],
        11,
        22,
        333,
    )
    assert database.complete_workflow_provider_reconnect(
        root,
        active_turn_id,
        reconnect["claim_token"],
        reconnect["attempt_token"],
    )
    claimed = claim_workflow_turn(root)
    assert claimed is not None and claimed["id"] == prepared["turn_id"]


def test_f13_retirement_fences_stale_reconnect_before_resume(workflow_db):
    root = "root-sidecar-retirement-race"
    turn_id = _start_admitted_input(root)
    reconnect = database.claim_workflow_provider_reconnect(root, now=datetime.now())
    assert reconnect is not None
    assert database.claim_terminal_runtime_exit(root) == "busy"

    with database.SessionLocal() as db:
        terminal = db.get(TerminalModel, root)
        turn = db.get(WorkflowTurnModel, turn_id)
        assert terminal is not None and turn is not None
        terminal.runtime_operation_expires_at = datetime(2000, 1, 1)
        turn.provider_reconnect_requested_at = datetime(2000, 1, 1)
        db.commit()

    assert database.claim_terminal_runtime_exit(root) == "dispatch"
    assert not database.renew_workflow_provider_reconnect(root, turn_id, reconnect["claim_token"])
    with database.SessionLocal() as db:
        terminal = db.get(TerminalModel, root)
        assert terminal is not None
        assert terminal.runtime_lifecycle == "exit_pending"
        assert terminal.runtime_operation_kind == "retire"


def test_f13_reconnect_heartbeat_exception_is_observable_and_loses_claim(monkeypatch):
    reconnect = {"turn_id": 7, "claim_token": "claim-token"}
    heartbeat = workflow_service._ProviderReconnectClaimHeartbeat("root-heartbeat", reconnect)
    monkeypatch.setattr(
        workflow_service,
        "renew_workflow_provider_reconnect",
        MagicMock(side_effect=RuntimeError("database unavailable")),
    )
    heartbeat._stop = MagicMock()
    heartbeat._stop.wait.return_value = False

    heartbeat._run()

    assert heartbeat.lost is True
    assert isinstance(heartbeat.error, RuntimeError)


@patch("cli_agent_orchestrator.services.inbox_service.check_and_send_pending_messages")
@patch("cli_agent_orchestrator.services.workflow_service.terminal_service")
def test_f13_ready_workflow_prioritizes_existing_inbox_turn_over_open_final(
    mock_terminal, mock_check_inbox, workflow_db
):
    root = "root-ready-pending-inbox"
    _start_admitted_input(root)
    _queue_inbox_workflow_turn(root, key="pending-after-provider-final")
    mock_terminal.get_terminal.return_value = {
        "status": TerminalStatus.COMPLETED.value,
        "lifecycle": "running",
    }
    mock_terminal.provider_runtime_sidecar_reconnect_required.return_value = False

    assert workflow_service.reconcile_root_workflow(root) is False
    mock_check_inbox.assert_called_once_with(root, registry=None)
    mock_terminal.send_input.assert_not_called()
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=root).one()
        assert (
            db.query(WorkflowTurnModel)
            .filter_by(workflow_id=workflow.id, kind="open_final")
            .count()
            == 0
        )


@patch("cli_agent_orchestrator.services.workflow_service.terminal_service")
@patch("cli_agent_orchestrator.providers.codex.tmux_client")
def test_p0_stale_spinner_rehydration_continues_same_parent_once_without_duplicate(
    mock_tmux, mock_terminal, workflow_db
):
    """A restarted provider repairs P/P/C without another workflow continuation."""
    root = "root-p0-rehydrated"
    now = datetime(2026, 8, 11, 12, 0, 0)
    output = (
        "› [CAO Handoff] Produce the report.\n"
        "• Called status probe\n"
        '  └ {"output":"• Working (15s • esc to interrupt)"}\n'
        "• Summary\n"
        "The final report is complete.\n"
        "\n› Continue working\n"
        "  gpt-5.6-terra high · 96% left · ~/project\n"
    )
    mock_tmux.get_history.return_value = output
    _start_admitted_input(root)

    persisted_provider = CodexProvider(root, "test-session", "window-0")
    restarted_provider = CodexProvider(root, "test-session", "window-0")
    statuses = [
        persisted_provider.get_status(),
        restarted_provider.get_status(),
        restarted_provider.get_status(),
        restarted_provider.get_status(),
    ]
    assert statuses == [
        TerminalStatus.PROCESSING,
        TerminalStatus.PROCESSING,
        TerminalStatus.PROCESSING,
        TerminalStatus.COMPLETED,
    ]
    mock_terminal.get_terminal.side_effect = [
        *[{"status": status.value, "lifecycle": "running"} for status in statuses],
        {"status": TerminalStatus.PROCESSING.value, "lifecycle": "running"},
    ]

    assert workflow_service.reconcile_root_workflow(root, now=now) is False
    assert workflow_service.reconcile_root_workflow(root, now=now) is False
    assert workflow_service.reconcile_root_workflow(root, now=now) is False
    assert workflow_service.reconcile_root_workflow(root, now=now) is True
    assert workflow_service.reconcile_root_workflow(root, now=now) is False
    assert mock_terminal.send_input.call_count == 1

    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=root).one()
        continuations = (
            db.query(WorkflowTurnModel)
            .filter(WorkflowTurnModel.workflow_id == workflow.id)
            .filter(WorkflowTurnModel.kind == "open_final")
            .all()
        )
        assert len(continuations) == 1
        assert continuations[0].state == "sent"
        assert continuations[0].attempt_count == 1
        continuation_id = continuations[0].id

    assert claim_workflow_turn_receipt(root, continuation_id, now=now) is True
    assert claim_workflow_turn_receipt(root, continuation_id, now=now) is False


@patch("cli_agent_orchestrator.services.inbox_service.check_and_send_pending_messages")
@patch("cli_agent_orchestrator.services.workflow_service.terminal_service")
def test_f13_active_child_barrier_retries_persisted_inbox_without_log_wake(
    mock_terminal, mock_inbox_delivery, workflow_db
):
    start_workflow_input("root-assigned-inbox")
    register_child_assignment("root-assigned-inbox", "child-assigned-inbox")
    result, duplicate = create_child_assignment_result_message(
        "child-assigned-inbox",
        "root-assigned-inbox",
        "durable callback",
        **_authorized_callback("child-assigned-inbox"),
    )
    assert result is not None and duplicate is False
    mock_terminal.get_terminal.return_value = {
        "status": TerminalStatus.COMPLETED.value,
        "lifecycle": "running",
    }

    assert workflow_service.reconcile_root_workflow("root-assigned-inbox") is False
    mock_inbox_delivery.assert_called_once_with("root-assigned-inbox", registry=None)


@patch("cli_agent_orchestrator.services.workflow_service.terminal_service")
@patch("cli_agent_orchestrator.services.inbox_service.terminal_service")
@patch("cli_agent_orchestrator.services.inbox_service.provider_manager.get_provider")
def test_f14_nested_handoff_services_assigned_callback_before_direct_handoff_defer(
    mock_provider, inbox_terminal, workflow_terminal, workflow_db
):
    """S can consume C's callback before its unresolved P handoff hard-defer.

    The sequence is P handoff -> S assign -> C result -> S callback/read/ack
    -> S direct completion -> one P handoff callback.  In particular, the
    first retry must retain the C callback behind S's unadmitted SENT turn.
    """
    parent, supervisor, child = "parent-nested", "supervisor-nested", "child-nested"
    parent_turn = _start_admitted_input(parent)
    supervisor_turn = start_workflow_input(supervisor)
    assert supervisor_turn is not None
    assert register_handoff_child(parent, supervisor)
    assert register_child_assignment(supervisor, child)
    child_notice, duplicate = create_child_assignment_result_message(
        child,
        supervisor,
        "C completed the dependency.",
        **_authorized_callback(child),
    )
    assert child_notice is not None and duplicate is False and child_notice.result_id is not None

    completed = {"status": TerminalStatus.COMPLETED.value, "lifecycle": "running"}
    provider = MagicMock()
    provider.get_status.return_value = TerminalStatus.COMPLETED
    mock_provider.return_value = provider
    workflow_terminal.get_terminal.return_value = completed

    # The active SENT turn has no receipt, so retrying the child barrier must
    # not rebind or transport C's callback.
    assert workflow_service.reconcile_root_workflow(supervisor) is False
    inbox_terminal.send_input.assert_not_called()
    assert get_parent_completion_barrier(supervisor) == (1, 0)

    # Once S has admitted its current turn, the same durable callback is
    # delivered once.  A repeated reconcile neither creates a second wake nor
    # bypasses the acknowledgement barrier.
    assert claim_workflow_turn_receipt(supervisor, supervisor_turn)
    assert workflow_service.reconcile_root_workflow(supervisor) is False
    assert workflow_service.reconcile_root_workflow(supervisor) is False
    assert inbox_terminal.send_input.call_count == 1
    with database.SessionLocal() as db:
        callback_turn = (
            db.query(WorkflowTurnModel).filter_by(inbox_message_id=child_notice.id).one()
        )
        assert callback_turn.kind == "assigned_result"
        assert callback_turn.state == "sent"

    # Model S's admitted callback turn: read C's immutable result, incorporate
    # it, then acknowledge it.  The dependent completion releases S's own
    # assigned-child barrier but not S's direct P handoff relation.
    assert claim_workflow_turn_receipt(supervisor, callback_turn.id)
    child_result = get_delegation_result_for_assignment(child)
    assert child_result is not None and child_result["id"] == child_notice.result_id
    assert child_result["document"]["body_markdown"] == "C completed the dependency."
    assert acknowledge_child_assignment_result(supervisor, child, result_id=child_notice.result_id)
    assert get_parent_completion_barrier(supervisor) == (0, 0)

    # The direct-handoff fence remains before provider-final/open_final work.
    assert workflow_service.reconcile_root_workflow(supervisor) is False
    workflow_terminal.send_input.assert_not_called()
    with database.SessionLocal() as db:
        supervisor_workflow = db.query(WorkflowModel).filter_by(root_terminal_id=supervisor).one()
        assert (
            db.query(WorkflowTurnModel)
            .filter_by(workflow_id=supervisor_workflow.id, kind="open_final")
            .count()
            == 0
        )

    document = {
        "summary": "S completed after C",
        "body_markdown": "S's dependent handoff result.",
        "changed_files": [],
        "checks": [{"command": "pytest nested", "outcome": "passed"}],
        "risks": [],
        "blockers": [],
        "format": "v1",
    }
    stable_output = f"CAO_RESULT_V1\n{json.dumps(document)}"
    inbox_terminal.get_terminal.return_value = completed
    inbox_terminal.get_output.side_effect = [stable_output, stable_output]
    inbox_terminal.exit_terminal.return_value = _confirmed_exit_result()
    assert reconcile_handoff_continuations(child_terminal_id=supervisor) == 1
    assert reconcile_handoff_continuations(child_terminal_id=supervisor) == 0
    assert inbox_terminal.send_input.call_count == 2

    parent_result = get_delegation_result_for_assignment(supervisor)
    assert parent_result is not None and parent_result["document"] == document
    with database.SessionLocal() as db:
        parent_notice = (
            db.query(InboxModel).filter_by(receiver_id=parent, result_id=parent_result["id"]).one()
        )
        parent_callback = (
            db.query(WorkflowTurnModel).filter_by(inbox_message_id=parent_notice.id).one()
        )
        assert parent_callback.kind == "handoff_result"
        assert (
            db.query(InboxModel)
            .filter_by(receiver_id=parent, result_id=parent_result["id"])
            .count()
            == 1
        )

    # P receives and acknowledges that one handoff result; repeated recovery
    # did not produce another wake or effect.
    assert claim_workflow_turn_receipt(parent, parent_callback.id)
    assert acknowledge_child_assignment_result(parent, supervisor, result_id=parent_result["id"])
    assert get_parent_completion_barrier(parent) == (0, 0)
    assert parent_turn != parent_callback.id


def test_f13_owner_gate_and_cancel_suppress_late_wakes(workflow_db):
    start_workflow_input("root-owner")
    register_handoff_child("root-owner", "child-owner")
    assert set_workflow_terminal_state("root-owner", "owner_gate", "needs owner") is True
    assert create_handoff_child_result_message("child-owner", "late") == (None, True)
    assert queue_workflow_turn("root-owner", "open_final", "late") == (None, True)

    start_workflow_input("root-cancel")
    register_handoff_child("root-cancel", "child-cancel")
    assert cancel_workflows_for_terminal("root-cancel") == 1
    assert create_handoff_child_result_message("child-cancel", "late") == (None, True)
    assert get_workflow_status("root-cancel") == "cancelled"


@pytest.mark.parametrize(
    ("root", "close"),
    [
        ("root-complete", lambda root: set_workflow_terminal_state(root, "terminal", "done")),
        ("root-owner-fence", lambda root: set_workflow_terminal_state(root, "owner_gate", "gate")),
        ("root-delete-fence", cancel_workflows_for_terminal),
    ],
)
def test_f13_closed_parent_atomically_fences_assignments_and_late_callbacks(
    workflow_db, root, close
):
    start_workflow_input(root)
    register_child_assignment(root, f"{root}-assign")
    register_handoff_child(root, f"{root}-handoff")
    register_handoff_child(root, f"{root}-handoff-second")

    assert close(root)
    # Both child relations are cancelled in the same close operation, so an
    # interleaved result cannot create an Inbox row or a continuation turn.
    assert create_child_assignment_result_message(f"{root}-assign", root, "late") == (None, True)
    assert create_handoff_child_result_message(f"{root}-handoff", "late") == (None, True)
    assert create_handoff_child_result_message(f"{root}-handoff-second", "late") == (None, True)
    with database.SessionLocal() as db:
        assignments = db.query(database.ChildAssignmentModel).all()
        assert {assignment.status for assignment in assignments} == {"cancelled"}
        assert db.query(InboxModel).count() == 0
        assert (
            db.query(WorkflowTurnModel).filter(WorkflowTurnModel.kind == "handoff_result").count()
            == 0
        )


def test_f13_crash_before_send_retries_only_after_its_lease(workflow_db):
    _start_admitted_input("root-claim-retry")
    now = datetime(2026, 8, 9, 12, 0, 0)
    observe_workflow_final("root-claim-retry", now=now)

    first = claim_workflow_turn("root-claim-retry", now=now)
    assert first is not None
    # A second contender sees the persisted claim, not another sendable row.
    assert claim_workflow_turn("root-claim-retry", now=now) is None
    assert requeue_expired_workflow_turn_claims(now=now + timedelta(seconds=29)) == 0
    assert requeue_expired_workflow_turn_claims(now=now + timedelta(seconds=30)) == 1

    retry = claim_workflow_turn("root-claim-retry", now=now + timedelta(seconds=30))
    assert retry is not None and retry["id"] == first["id"]
    with database.SessionLocal() as db:
        turn = db.query(WorkflowTurnModel).filter(WorkflowTurnModel.id == first["id"]).one()
        assert turn.state == "claimed"
        assert turn.attempt_count == 2
    # No physical delivery happened before the crash, so the retried receiver
    # is still admitted exactly once when it reaches its model-work gate.
    assert activate_workflow_turn("root-claim-retry", retry["id"])
    assert claim_workflow_turn_receipt(
        "root-claim-retry", retry["id"], now=now + timedelta(seconds=30)
    )


def test_f13_stale_a_b_claim_token_fences_ack_requeue_and_dependent_effects(workflow_db):
    root = "root-stale-a-b"
    now = datetime(2026, 8, 9, 12, 0, 0)
    _start_admitted_input(root)
    observe_workflow_final(root, now=now)

    stale_a = claim_workflow_turn(root, now=now)
    assert stale_a is not None
    assert requeue_expired_workflow_turn_claims(now=now + timedelta(seconds=30)) == 1
    live_b = claim_workflow_turn(root, now=now + timedelta(seconds=30))
    assert live_b is not None
    assert live_b["claim_generation"] == stale_a["claim_generation"] + 1
    assert live_b["claim_token"] != stale_a["claim_token"]

    # A cannot renew (the pre-send fence), acknowledge, or return B's claim.
    assert not renew_workflow_turn_claim(
        stale_a["id"],
        stale_a["claim_token"],
        stale_a["claim_generation"],
        now=now + timedelta(seconds=30),
    )
    assert not mark_workflow_turn_sent(
        stale_a["id"],
        stale_a["claim_token"],
        stale_a["claim_generation"],
        now=now + timedelta(seconds=30),
    )
    assert not requeue_workflow_turn(
        stale_a["id"],
        stale_a["claim_token"],
        stale_a["claim_generation"],
        now=now + timedelta(seconds=30),
    )
    assert mark_workflow_turn_sent(
        live_b["id"],
        live_b["claim_token"],
        live_b["claim_generation"],
        now=now + timedelta(seconds=30),
    )
    with database.SessionLocal() as db:
        turn = db.query(WorkflowTurnModel).filter(WorkflowTurnModel.id == live_b["id"]).one()
        assert turn.state == "sent"
        assert turn.claim_generation == live_b["claim_generation"]


def test_f13_slow_transport_renews_before_nominal_lease_expires(workflow_db):
    root = "root-slow-transport"
    now = datetime(2026, 8, 9, 12, 0, 0)
    _start_admitted_input(root)
    observe_workflow_final(root, now=now)

    claim = claim_workflow_turn(root, now=now)
    assert claim is not None
    # A heartbeat at 29 seconds extends ownership beyond the original 30-second
    # lease, so a slow but live transport cannot be stolen at 31 seconds.
    assert renew_workflow_turn_claim(
        claim["id"],
        claim["claim_token"],
        claim["claim_generation"],
        now=now + timedelta(seconds=29),
    )
    assert requeue_expired_workflow_turn_claims(now=now + timedelta(seconds=31)) == 0
    assert claim_workflow_turn(root, now=now + timedelta(seconds=31)) is None
    assert mark_workflow_turn_sent(
        claim["id"],
        claim["claim_token"],
        claim["claim_generation"],
        now=now + timedelta(seconds=31),
    )


def test_f13_crash_after_actual_send_reuses_one_idempotent_logical_turn(workflow_db):
    root = "root-after-send-crash"
    now = datetime(2026, 8, 9, 12, 0, 0)
    _start_admitted_input(root)
    observe_workflow_final(root, now=now)

    first = claim_workflow_turn(root, now=now)
    assert first is not None
    # The process dies after tmux accepted the input but before its sender-side
    # receipt. The receiver had already admitted model work under the stable
    # logical turn. Recovery can retry physical transport, but that duplicate
    # cannot create another supervisor effect.
    actual_transport_idempotency_key = first["id"]
    assert activate_workflow_turn(root, actual_transport_idempotency_key)
    assert claim_workflow_turn_receipt(root, actual_transport_idempotency_key, now=now)
    assert requeue_expired_workflow_turn_claims(now=now + timedelta(seconds=30)) == 1
    retry = claim_workflow_turn(root, now=now + timedelta(seconds=30))
    assert retry is not None
    assert retry["id"] == actual_transport_idempotency_key
    assert retry["claim_generation"] == first["claim_generation"] + 1
    assert workflow_service._continuation_message(
        retry["kind"], retry["payload"], retry["id"]
    ) == workflow_service._continuation_message(first["kind"], first["payload"], first["id"])
    assert not claim_workflow_turn_receipt(root, retry["id"], now=now + timedelta(seconds=30))
    assert mark_workflow_turn_sent(
        retry["id"],
        retry["claim_token"],
        retry["claim_generation"],
        now=now + timedelta(seconds=30),
    )
    with database.SessionLocal() as db:
        assert (
            db.query(WorkflowTurnModel).filter(WorkflowTurnModel.kind == "open_final").count() == 1
        )
        assert db.query(WorkflowTurnReceiptModel).count() == 2


def test_f13_concurrent_duplicate_receiver_receipts_admit_one_effect(tmp_path, monkeypatch):
    """Concurrent duplicate physical arrivals have one durable consumer."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'workflow-receipt.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(database, "_child_assignment_schema_ready", True)

    root = "root-concurrent-receipt"
    start_workflow_input(root)
    turn_id, duplicate = queue_workflow_turn(root, "test", "concurrent-receipt")
    assert turn_id is not None and duplicate is False
    assert activate_workflow_turn(root, turn_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        admitted = list(
            executor.map(lambda _attempt: claim_workflow_turn_receipt(root, turn_id), range(2))
        )

    assert admitted.count(True) == 1
    assert admitted.count(False) == 1
    with database.SessionLocal() as db:
        assert db.query(WorkflowTurnReceiptModel).count() == 1


def test_f13_receiver_receipt_restart_suppression_and_distinct_turns(workflow_db):
    root = "root-receipt-restart"
    start_workflow_input(root)
    first_id, first_duplicate = queue_workflow_turn(root, "test", "receipt-first")
    second_id, second_duplicate = queue_workflow_turn(root, "test", "receipt-second")
    assert first_id is not None and first_duplicate is False
    assert second_id is not None and second_duplicate is False

    assert activate_workflow_turn(root, first_id)
    assert claim_workflow_turn_receipt(root, first_id)
    # Restart/replay of the same envelope does not create a second semantic
    # consume, whereas an independently stable logical turn remains valid.
    assert not claim_workflow_turn_receipt(root, first_id)
    assert activate_workflow_turn(root, second_id)
    assert claim_workflow_turn_receipt(root, second_id)


def test_f13_interrupted_assigned_result_resumes_under_fresh_admitted_turn(
    workflow_db, monkeypatch
):
    """A post-read interruption transfers authority and acknowledges once."""
    parent, child = "parent-interrupted-result", "child-interrupted-result"
    _start_admitted_input(parent)
    assert register_child_assignment(parent, child)
    notice, duplicate = create_child_assignment_result_message(
        child,
        parent,
        "completed child result",
        **_authorized_callback(child),
    )
    assert notice is not None and duplicate is False and notice.result_id is not None
    callback = get_workflow_turn_for_inbox(notice.id)
    assert callback is not None
    assert activate_workflow_turn_for_inbox(notice.id) == callback["turn_id"]
    assert mark_workflow_turn_sent_for_inbox(notice.id)
    assert mark_child_assignment_result_delivered(notice.id)
    assert database.acquire_provider_execution(parent, callback["turn_id"], 1)

    monkeypatch.setenv("CAO_TERMINAL_ID", parent)
    admitted = asyncio.run(mcp_server.claim_workflow_turn_receipt(callback["turn_id"]))
    assert admitted["accepted"] is True
    assert admitted["resumed"] is False
    assert asyncio.run(mcp_server.read_delegation_result(callback["turn_id"], notice.result_id))[
        "success"
    ]

    # A provider/model restart after the read retains the returned opaque
    # capability. It does not reuse the old receipt or old logical authority.
    workflow_db.dispose()
    resumed = asyncio.run(
        mcp_server.claim_workflow_turn_receipt(
            callback["turn_id"], resume_token=admitted["resume_token"]
        )
    )
    assert resumed["accepted"] is True
    assert resumed["resumed"] is True
    resumed_turn = resumed["logical_turn_id"]
    assert resumed_turn != callback["turn_id"]
    assert resumed["resumed_from_logical_turn_id"] == callback["turn_id"]
    assert not asyncio.run(mcp_server.claim_workflow_turn_receipt(callback["turn_id"]))["accepted"]
    assert asyncio.run(mcp_server.read_delegation_result(resumed_turn, notice.result_id))["success"]
    assert database.get_provider_execution_turn(parent) == resumed_turn
    assert not database.release_provider_execution(parent, callback["turn_id"])

    # The real blocking-handoff boundary can release the transferred lease,
    # admit a child at capacity one, and reacquire for the fresh execution.
    with patch.object(mcp_server.inbox_service, "wake_provider_execution_queue", return_value=0):
        execution_terminal, suspended = mcp_server._suspend_provider_execution(resumed_turn)
    assert (execution_terminal, suspended) == (parent, True)
    waiter_turn = _start_admitted_input("resume-capacity-waiter")
    assert database.acquire_provider_execution("resume-capacity-waiter", waiter_turn, 1)
    assert database.release_provider_execution("resume-capacity-waiter", waiter_turn)

    def acquire_resumed_execution(terminal_id: str, logical_turn_id: int) -> None:
        assert database.acquire_provider_execution(terminal_id, logical_turn_id, 1)

    monkeypatch.setattr(
        operations_service,
        "acquire_provider_execution_slot",
        acquire_resumed_execution,
    )
    asyncio.run(mcp_server._resume_provider_execution(parent, resumed_turn, suspended))
    assert database.get_provider_execution_turn(parent) == resumed_turn

    acknowledged = asyncio.run(
        mcp_server.acknowledge_assigned_result(
            resumed_turn, result_id=notice.result_id, child_terminal_id=child
        )
    )
    assert acknowledged["success"] is True
    assert acknowledged["child_terminal_id"] == child
    assert acknowledged["result_id"] == notice.result_id
    replay = asyncio.run(
        mcp_server.acknowledge_assigned_result(
            resumed_turn, result_id=notice.result_id, child_terminal_id=child
        )
    )
    assert replay["success"] is False
    assert replay["reason_code"] == "RESULT_ALREADY_ACKNOWLEDGED"
    assert get_parent_completion_barrier(parent) == (0, 0)

    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=parent).one()
        old_turn = db.get(WorkflowTurnModel, callback["turn_id"])
        new_turn = db.get(WorkflowTurnModel, resumed_turn)
        assert workflow.active_turn_id == resumed_turn
        assert old_turn.state == "finished"
        assert new_turn.kind == "execution_resume"
        assert new_turn.resume_parent_turn_id == callback["turn_id"]
        assert (
            db.query(WorkflowTurnReceiptModel)
            .filter(
                WorkflowTurnReceiptModel.workflow_turn_id.in_([callback["turn_id"], resumed_turn])
            )
            .count()
            == 2
        )
        acknowledgement_effects = (
            db.query(WorkflowEffectModel)
            .filter(WorkflowEffectModel.effect_kind == "acknowledge_assignment")
            .all()
        )
        assert len(acknowledgement_effects) == 1
        assert acknowledgement_effects[0].state == "completed"
        assert acknowledgement_effects[0].effect_key == mcp_server._workflow_effect_key(
            "acknowledge_assignment", notice.result_id
        )
        assert db.query(database.DelegationResultModel).filter_by(id=notice.result_id).count() == 1


def test_f13_resumed_acknowledgement_rejects_cross_child_identity_without_effect(
    workflow_db, monkeypatch
):
    """A result and a different child cannot cross assignment identity."""
    parent = "parent-resumed-identity-mismatch"
    child_a = "child-resumed-identity-a"
    child_b = "child-resumed-identity-b"
    _start_admitted_input(parent)
    assert register_child_assignment(parent, child_a)
    assert register_child_assignment(parent, child_b)
    notice, duplicate = create_child_assignment_result_message(
        child_a,
        parent,
        "completed child A",
        **_authorized_callback(child_a),
    )
    assert notice is not None and duplicate is False and notice.result_id is not None
    callback = get_workflow_turn_for_inbox(notice.id)
    assert callback is not None
    assert activate_workflow_turn_for_inbox(notice.id) == callback["turn_id"]
    assert mark_workflow_turn_sent_for_inbox(notice.id)
    assert mark_child_assignment_result_delivered(notice.id)

    monkeypatch.setenv("CAO_TERMINAL_ID", parent)
    admitted = asyncio.run(mcp_server.claim_workflow_turn_receipt(callback["turn_id"]))
    resumed = asyncio.run(
        mcp_server.claim_workflow_turn_receipt(
            callback["turn_id"], resume_token=admitted["resume_token"]
        )
    )
    assert resumed["accepted"] is True and resumed["resumed"] is True
    resumed_turn = resumed["logical_turn_id"]
    barrier_before = get_parent_completion_barrier(parent)

    mismatch = asyncio.run(
        mcp_server.acknowledge_assigned_result(
            resumed_turn,
            result_id=notice.result_id,
            child_terminal_id=child_b,
        )
    )
    assert mismatch["success"] is False
    assert mismatch["reason_code"] == "RESULT_IDENTITY_MISMATCH"
    assert get_parent_completion_barrier(parent) == barrier_before
    with database.SessionLocal() as db:
        assignments = {
            row.child_terminal_id: row.status
            for row in db.query(database.ChildAssignmentModel).filter_by(parent_terminal_id=parent)
        }
        assert assignments == {
            child_a: "result_delivered",
            child_b: "awaiting_result",
        }
        assert (
            db.query(WorkflowEffectModel).filter_by(effect_kind="acknowledge_assignment").count()
            == 0
        )

    canonical = asyncio.run(
        mcp_server.acknowledge_assigned_result(
            resumed_turn,
            result_id=notice.result_id,
            child_terminal_id=child_a,
        )
    )
    assert canonical["success"] is True
    assert canonical["child_terminal_id"] == child_a
    assert canonical["result_id"] == notice.result_id


def test_f13_resume_commit_failure_retains_turn_receipt_and_provider_lease(
    workflow_db, monkeypatch
):
    """The active turn and its capacity lease transfer in one transaction."""
    root = "root-resume-commit-failure"
    with database.SessionLocal() as db:
        db.add(
            TerminalModel(
                id=root,
                tmux_session=f"cao-{root}",
                tmux_window="owner-0000",
                provider="codex",
                runtime_lifecycle="running",
            )
        )
        db.commit()
    turn_id = start_workflow_input(root)
    assert turn_id is not None
    monkeypatch.setenv("CAO_TERMINAL_ID", root)
    admitted = asyncio.run(mcp_server.claim_workflow_turn_receipt(turn_id))
    assert admitted["accepted"] is True
    assert database.acquire_provider_execution(root, turn_id, 1)

    def fail_commit(_session):
        raise RuntimeError("injected resume commit failure")

    event.listen(database.SessionLocal.class_, "before_commit", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="injected resume commit failure"):
            claim_or_resume_workflow_turn_receipt(
                root,
                turn_id,
                resume_token=admitted["resume_token"],
            )
    finally:
        event.remove(database.SessionLocal.class_, "before_commit", fail_commit)

    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=root).one()
        receipt = db.query(WorkflowTurnReceiptModel).one()
        assert workflow.active_turn_id == turn_id
        assert receipt.resumed_by_turn_id is None
        assert db.query(WorkflowTurnModel).filter_by(kind="execution_resume").count() == 0
        assert db.get(database.ProviderExecutionLeaseModel, root).workflow_turn_id == turn_id

    resumed = claim_or_resume_workflow_turn_receipt(
        root,
        turn_id,
        resume_token=admitted["resume_token"],
    )
    assert resumed["accepted"] is True
    assert database.get_provider_execution_turn(root) == resumed["logical_turn_id"]


def test_f13_interrupted_owner_input_resume_fences_old_effects_and_concurrent_replay(
    workflow_db, monkeypatch
):
    """Owner input uses the same one-use transfer without duplicating effects."""
    root = "root-interrupted-owner-input"
    turn_id = start_workflow_input(root)
    assert turn_id is not None
    monkeypatch.setenv("CAO_TERMINAL_ID", root)
    admitted = asyncio.run(mcp_server.claim_workflow_turn_receipt(turn_id))
    assert admitted["accepted"] is True
    assert not claim_or_resume_workflow_turn_receipt(
        root, turn_id, resume_token="not-a-valid-resume-capability"
    )["accepted"]

    # Split provider authority is not silently adopted.  The valid token is
    # left usable after this fail-closed attempt because the transaction made
    # no receipt, turn, workflow, or lease mutation.
    with database.SessionLocal() as db:
        db.add(
            database.ProviderExecutionLeaseModel(
                terminal_id=root,
                workflow_turn_id=turn_id + 1000,
            )
        )
        db.commit()
    conflict = claim_or_resume_workflow_turn_receipt(
        root, turn_id, resume_token=admitted["resume_token"]
    )
    assert conflict == {
        "accepted": False,
        "reason": "provider_execution_lease_conflict",
    }
    with database.SessionLocal() as db:
        assert db.query(WorkflowTurnModel).filter_by(kind="execution_resume").count() == 0
        assert db.query(WorkflowTurnReceiptModel).count() == 1
        db.query(database.ProviderExecutionLeaseModel).filter_by(terminal_id=root).delete()
        db.commit()

    with patch.object(mcp_server, "_send_message_impl", return_value={"success": True}):
        assert asyncio.run(mcp_server.send_message(turn_id, "target", "payload"))["success"]

    barrier = Barrier(2)

    def resume():
        barrier.wait()
        return claim_or_resume_workflow_turn_receipt(
            root, turn_id, resume_token=admitted["resume_token"]
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        attempts = list(executor.map(lambda _attempt: resume(), range(2)))
    accepted = [attempt for attempt in attempts if attempt["accepted"]]
    rejected = [attempt for attempt in attempts if not attempt["accepted"]]
    assert len(accepted) == 1
    assert len(rejected) == 1
    resumed_turn = accepted[0]["logical_turn_id"]

    # The completed send effect was mirrored into the new execution scope;
    # neither the interrupted old turn nor the resumed turn can send it again.
    with patch.object(mcp_server, "_send_message_impl", return_value={"success": True}) as send:
        old = asyncio.run(mcp_server.send_message(turn_id, "target", "payload"))
        duplicate = asyncio.run(mcp_server.send_message(resumed_turn, "target", "payload"))
        distinct = asyncio.run(mcp_server.send_message(resumed_turn, "target-2", "new payload"))
    assert old["success"] is False
    assert old["reason_code"] == "DUPLICATE_EFFECT"
    assert duplicate["success"] is False
    assert duplicate["reason_code"] == "DUPLICATE_EFFECT"
    assert distinct["success"] is True
    assert send.call_count == 1

    with database.SessionLocal() as db:
        assert db.query(WorkflowTurnReceiptModel).count() == 2
        assert db.query(WorkflowTurnModel).filter_by(kind="execution_resume").count() == 1
        original_receipt = (
            db.query(WorkflowTurnReceiptModel).filter_by(workflow_turn_id=turn_id).one()
        )
        assert original_receipt.resume_token_sha256 != admitted["resume_token"]
        assert len(original_receipt.resume_token_sha256) == 64


def test_f13_effect_ledger_requires_admitted_logical_turn_and_dedupes_restart(workflow_db):
    root = "root-effect-ledger"
    start_workflow_input(root)
    turn_id, duplicate = queue_workflow_turn(root, "test", "effect-ledger")
    assert turn_id is not None and duplicate is False
    # Prompt text cannot acquire an effect capability; only the durable
    # receiver admission can.  This is the server-side isolation boundary.
    assert claim_workflow_effect(root, turn_id, "assign", "task-a") is None
    assert activate_workflow_turn(root, turn_id)
    assert claim_workflow_turn_receipt(root, turn_id)

    first = claim_workflow_effect(root, turn_id, "assign", "task-a")
    assert first is not None
    # A duplicate physical delivery and a post-crash process restart observe
    # the claimed row.  They do not create or submit a second child task.
    assert claim_workflow_effect(root, turn_id, "assign", "task-a") is None
    with database.SessionLocal() as db:
        effect = db.query(WorkflowEffectModel).one()
        assert effect.state == "claimed"
        assert effect.workflow_turn_id == turn_id


def test_f13_not_admitted_effect_is_retryable_but_completed_effect_is_not(workflow_db):
    root = "root-effect-not-admitted"
    turn_id = _start_admitted_input(root)

    first = claim_workflow_effect(root, turn_id, "assign", "review-scope")
    assert first is not None
    assert finish_workflow_effect(root, first["id"], first["claim_token"], "not_admitted")

    # A fresh DB session models retry after restart/capacity release. The same
    # durable row is reclaimed with a new token; a concurrent/late first token
    # cannot finish the second attempt.
    second = claim_workflow_effect(root, turn_id, "assign", "review-scope")
    assert second is not None
    assert second["id"] == first["id"]
    assert second["claim_token"] != first["claim_token"]
    assert not finish_workflow_effect(root, first["id"], first["claim_token"], "completed")
    assert finish_workflow_effect(root, second["id"], second["claim_token"], "completed")
    assert claim_workflow_effect(root, turn_id, "assign", "review-scope") is None


def test_f13_concurrent_not_admitted_retries_admit_exactly_one_effect(workflow_db):
    root = "root-effect-not-admitted-race"
    turn_id = _start_admitted_input(root)
    first = claim_workflow_effect(root, turn_id, "assign", "same-review")
    assert first is not None
    assert finish_workflow_effect(root, first["id"], first["claim_token"], "not_admitted")
    barrier = Barrier(2)

    def retry():
        barrier.wait()
        return claim_workflow_effect(root, turn_id, "assign", "same-review")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _attempt: retry(), range(2)))
    admitted = [result for result in results if result is not None]
    assert len(admitted) == 1
    assert admitted[0]["id"] == first["id"]


def test_f13_capacity_retry_ready_final_and_open_successor_are_autonomous(workflow_db, monkeypatch):
    """Reproduce the 2524 overnight stranding sequence end to end."""
    root = "root-capacity-ready-continuation"
    turn_id = start_workflow_input(root)
    assert turn_id is not None
    monkeypatch.setenv("CAO_TERMINAL_ID", root)
    assert asyncio.run(mcp_server.claim_workflow_turn_receipt(turn_id))["accepted"] is True

    capacity_rejection = {
        "success": False,
        "terminal_id": None,
        "message": "terminal admission denied",
        "reason_code": "WORK_CONTEXT_CAPACITY_EXHAUSTED",
    }
    admitted_child = {
        "success": True,
        "terminal_id": "reviewer-after-capacity",
        "message": "assigned",
    }
    with patch.object(
        mcp_server, "_assign_impl", side_effect=[capacity_rejection, admitted_child]
    ) as assign_impl:
        rejected = asyncio.run(mcp_server.assign(turn_id, "reviewer_sol_high", "rereview"))
        retried = asyncio.run(mcp_server.assign(turn_id, "reviewer_sol_high", "rereview"))
        duplicate = asyncio.run(mcp_server.assign(turn_id, "reviewer_sol_high", "rereview"))

    assert rejected["reason_code"] == "WORK_CONTEXT_CAPACITY_EXHAUSTED"
    assert retried["success"] is True
    assert duplicate["reason_code"] == "DUPLICATE_EFFECT"
    assert assign_impl.call_count == 2
    with database.SessionLocal() as db:
        effect = db.query(WorkflowEffectModel).one()
        assert effect.state == "completed"

    now = datetime(2026, 8, 22, 3, 0, 0)
    mock_terminal = MagicMock()
    mock_terminal.get_terminal.return_value = {
        "status": TerminalStatus.IDLE.value,
        "lifecycle": "running",
    }
    with patch.object(workflow_service, "terminal_service", mock_terminal):
        # First Ready observation is durable but conservative when PROCESSING
        # was not sampled. A restart and later watchdog tick own the same turn.
        assert workflow_service.reconcile_root_workflow(root, now=now) is False
        with database.SessionLocal() as db:
            active = db.query(WorkflowTurnModel).filter_by(id=turn_id).one()
            assert active.provider_ready_observed_at == now
            assert active.state == "sent"

        assert (
            workflow_service.reconcile_root_workflow(root, now=now + timedelta(seconds=31)) is True
        )

    assert mock_terminal.send_input.call_count == 1
    sent_message = mock_terminal.send_input.call_args.args[1]
    assert "The workflow is durably OPEN" in sent_message
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=root).one()
        successor = db.query(WorkflowTurnModel).filter_by(id=workflow.active_turn_id).one()
        assert successor.kind == "open_final"
        assert successor.state == "sent"
        assert workflow.status == "open"


def test_f13_processing_cancels_transient_ready_and_shortens_later_final_debounce(
    workflow_db,
):
    root = "root-ready-processing-ready"
    turn_id = _start_admitted_input(root)
    now = datetime(2026, 8, 22, 4, 0, 0)

    assert observe_workflow_ready(root, now=now) == DEFER_STABLE_READY
    assert observe_workflow_processing(root, now=now + timedelta(seconds=1)) is True
    with database.SessionLocal() as db:
        turn = db.query(WorkflowTurnModel).filter_by(id=turn_id).one()
        assert turn.provider_processing_observed_at == now + timedelta(seconds=1)
        assert turn.provider_ready_observed_at is None

    assert observe_workflow_ready(root, now=now + timedelta(seconds=2)) == DEFER_STABLE_READY
    assert observe_workflow_ready(root, now=now + timedelta(seconds=4)) == DEFER_STABLE_READY
    successor = observe_workflow_ready(root, now=now + timedelta(seconds=5))
    assert isinstance(successor, int)


def test_f13_normal_top_level_input_envelope_admits_its_first_delegation(workflow_db, monkeypatch):
    """The first non-continuation input is as capable as a later wake."""
    root = "root-normal-input"
    turn_id = start_workflow_input(root)
    assert turn_id is not None
    assert f"logical-turn={turn_id}" in workflow_service.admission_message("delegate", turn_id)
    monkeypatch.setenv("CAO_TERMINAL_ID", root)

    assert asyncio.run(mcp_server.claim_workflow_turn_receipt(turn_id))["accepted"] is True
    with patch.object(mcp_server, "_send_message_impl", return_value={"success": True}) as send:
        result = asyncio.run(mcp_server.send_message(turn_id, "child", "F12 callback"))

    assert result["success"] is True
    assert send.call_args.args[:2] == ("child", "F12 callback")


def test_f13_assigned_and_handoff_callbacks_keep_restart_safe_admission_envelopes(
    workflow_db, monkeypatch
):
    """F11/F12 callback deliveries retain a current parent turn across restart."""
    parent = "parent-callback-envelope"
    child = "child-callback-envelope"
    _start_admitted_input(parent)
    child_turn = start_workflow_input(child)
    assert child_turn is not None
    assert f"logical-turn={child_turn}" in workflow_service.admission_message(
        "assigned work", child_turn
    )

    # F12: an assigned child can durably send its first callback after a restart.
    monkeypatch.setenv("CAO_TERMINAL_ID", child)
    assert asyncio.run(mcp_server.claim_workflow_turn_receipt(child_turn))["accepted"] is True
    with patch.object(mcp_server, "_send_message_impl", return_value={"success": True}) as send:
        assert (
            asyncio.run(mcp_server.send_message(child_turn, parent, "F12 result"))["success"]
            is True
        )
    assert send.call_args.args[:2] == (parent, "F12 result")

    assert register_child_assignment(parent, child)
    assigned, assigned_duplicate = create_child_assignment_result_message(
        child, parent, "F12 result", **_authorized_callback(child)
    )
    assert assigned is not None and assigned_duplicate is False
    assigned_turn = get_workflow_turn_for_inbox(assigned.id)
    assert assigned_turn is not None
    assert activate_workflow_turn_for_inbox(assigned.id) == assigned_turn["turn_id"]
    # The next handoff cannot overtake this unadmitted callback. Model the
    # parent's receipt before asking F11 to create its subsequent boundary.
    assert claim_workflow_turn_receipt(parent, assigned_turn["turn_id"])

    # F11 uses the same Inbox-to-current-turn path after recovery.
    handoff_child = "handoff-callback-envelope"
    assert register_handoff_child(parent, handoff_child)
    handoff, handoff_duplicate = create_handoff_child_result_message(handoff_child, "F11 result")
    assert handoff is not None and handoff_duplicate is False
    handoff_turn = get_workflow_turn_for_inbox(handoff.id)
    assert handoff_turn is not None
    assert activate_workflow_turn_for_inbox(handoff.id) == handoff_turn["turn_id"]
    assert f"logical-turn={handoff_turn['turn_id']}" in workflow_service.admission_message(
        handoff.message, handoff_turn["turn_id"]
    )


@patch("cli_agent_orchestrator.services.inbox_service.terminal_service.send_input")
@patch("cli_agent_orchestrator.services.inbox_service.provider_manager.get_provider")
def test_f14_assigned_child_completion_wakes_its_same_idle_parent_once(
    mock_provider, mock_send, workflow_db, monkeypatch
):
    """An admitted child completion becomes one same-parent callback turn."""
    parent, child = "parent-assigned-completion", "child-assigned-completion"
    _start_admitted_input(parent)
    child_turn = start_workflow_input(child)
    assert child_turn is not None
    assert register_child_assignment(parent, child)
    provider = MagicMock()
    provider.get_status.return_value = TerminalStatus.IDLE
    mock_provider.return_value = provider
    monkeypatch.setattr(terminal_service.tmux_client, "window_exists", lambda *_args: True)

    monkeypatch.setenv("CAO_TERMINAL_ID", child)
    assert asyncio.run(mcp_server.claim_workflow_turn_receipt(child_turn))["accepted"] is True
    completed = asyncio.run(mcp_server.complete_workflow(child_turn, "review approved"))
    assert completed["success"] is True
    assert mock_send.call_count == 1

    with database.SessionLocal() as db:
        turn = (
            db.query(WorkflowTurnModel)
            .filter(
                WorkflowTurnModel.workflow_id
                == db.query(WorkflowModel.id)
                .filter(WorkflowModel.root_terminal_id == parent)
                .scalar_subquery(),
                WorkflowTurnModel.kind == "assigned_result",
            )
            .one()
        )
        assert turn.state == "sent"
        assert turn.attempt_count == 1
        parent_turn = turn.id
        assert db.query(InboxModel).one().status == "delivered"
        assert (
            db.query(database.DelegationResultModel).one().authorship == "child_workflow_completion"
        )

    monkeypatch.setenv("CAO_TERMINAL_ID", parent)
    assert asyncio.run(mcp_server.claim_workflow_turn_receipt(parent_turn))["accepted"] is True
    assert not asyncio.run(mcp_server.claim_workflow_turn_receipt(parent_turn))["accepted"]
    assert mock_send.call_count == 1


@patch("cli_agent_orchestrator.services.inbox_service.terminal_service.send_input")
@patch("cli_agent_orchestrator.services.inbox_service.provider_manager.get_provider")
def test_f14_restart_after_assigned_completion_before_wake_recovers_one_parent_turn(
    mock_provider, mock_send, workflow_db, monkeypatch
):
    """A restart replays the one persisted completion callback, not another result."""
    parent, child = "parent-assigned-restart", "child-assigned-restart"
    _start_admitted_input(parent)
    child_turn = start_workflow_input(child)
    assert child_turn is not None
    assert register_child_assignment(parent, child)
    provider = MagicMock()
    provider.get_status.return_value = TerminalStatus.IDLE
    mock_provider.return_value = provider

    monkeypatch.setenv("CAO_TERMINAL_ID", child)
    assert asyncio.run(mcp_server.claim_workflow_turn_receipt(child_turn))["accepted"] is True
    with (
        patch.object(
            mcp_server.inbox_service, "check_and_send_pending_messages", return_value=False
        ),
        patch.object(mcp_server.inbox_service, "wake_provider_execution_queue", return_value=0),
    ):
        assert asyncio.run(mcp_server.complete_workflow(child_turn, "restart-safe review"))[
            "success"
        ]
    assert mock_send.call_count == 0

    with (
        patch(
            "cli_agent_orchestrator.services.inbox_service.terminalize_missing_terminal_assignments_for_restart"
        ),
        patch(
            "cli_agent_orchestrator.services.inbox_service.arm_handoff_continuations_for_restart"
        ),
        patch("cli_agent_orchestrator.services.inbox_service.reconcile_handoff_continuations"),
        patch.object(workflow_service, "reconcile_open_workflows", return_value=0),
    ):
        assert reconcile_pending_messages() == 1
    assert mock_send.call_count == 1

    with database.SessionLocal() as db:
        turns = (
            db.query(WorkflowTurnModel).filter(WorkflowTurnModel.kind == "assigned_result").all()
        )
        assert len(turns) == 1
        assert turns[0].state == "sent"
        parent_turn = turns[0].id
        assert db.query(InboxModel).one().status == "delivered"
        assert (
            db.query(database.DelegationResultModel).one().authorship == "child_workflow_completion"
        )

    monkeypatch.setenv("CAO_TERMINAL_ID", parent)
    assert asyncio.run(mcp_server.claim_workflow_turn_receipt(parent_turn))["accepted"] is True


def test_f13_historical_receipt_cannot_be_borrowed_by_a_later_model_turn(workflow_db, monkeypatch):
    """A caller-selected old ID cannot reopen effects after the next input."""
    root = "root-historical-receipt"
    first = start_workflow_input(root)
    assert first is not None
    monkeypatch.setenv("CAO_TERMINAL_ID", root)
    assert asyncio.run(mcp_server.claim_workflow_turn_receipt(first))["accepted"] is True

    current = start_workflow_input(root)
    assert current is not None and current != first
    # The old receipt remains durable for replay suppression but is no longer
    # the runtime capability. A later invocation must admit only its own turn.
    assert asyncio.run(mcp_server.claim_workflow_turn_receipt(first))["accepted"] is False
    assert claim_workflow_effect(root, first, "send_message", "borrowed") is None
    assert asyncio.run(mcp_server.claim_workflow_turn_receipt(current))["accepted"] is True


def test_f13_public_assign_metadata_cannot_borrow_the_prior_admission(workflow_db, monkeypatch):
    """A forged public assign wake creates turn two and fences admitted turn one."""
    root = "f13a0012"
    with database.SessionLocal() as db:
        db.add(
            TerminalModel(
                id=root,
                tmux_session=f"cao-{root}",
                tmux_window="owner-0000",
                provider="codex",
                runtime_lifecycle="running",
                runtime_generation=TEST_TERMINAL_RUNTIME_GENERATION,
                provider_resume_identity=TEST_CODEX_RESUME_IDENTITY,
                provider_resume_runtime_generation=TEST_TERMINAL_RUNTIME_GENERATION,
            )
        )
        db.commit()
    first = start_workflow_input(root)
    assert first is not None
    monkeypatch.setenv("CAO_TERMINAL_ID", root)
    assert asyncio.run(mcp_server.claim_workflow_turn_receipt(first))["accepted"] is True

    with (patch.object(api_main.terminal_service, "send_input", return_value=True) as send,):
        api_main.app.state.plugin_registry = PluginRegistry()
        response = TestClient(api_main.app, headers={"Host": "localhost"}).post(
            f"/terminals/{root}/input",
            params={
                "message": "public turn two",
                "sender_id": "forged-sender",
                "orchestration_type": "assign",
            },
        )

    assert response.json() == {"success": True}
    current = send.call_args.args[1].split("logical-turn=", 1)[1].split("]", 1)[0]
    assert current != str(first)
    # The previous model turn cannot use its already-admitted receipt to make
    # a new assignment after public metadata delivered a physical second turn.
    assert claim_workflow_effect(root, first, "assign", "borrowed-turn-one") is None
    assert asyncio.run(mcp_server.claim_workflow_turn_receipt(int(current)))["accepted"] is True


def test_f13_direct_transport_binding_is_opaque_and_fenced_by_a_new_input(workflow_db):
    """Only CAO's issued direct binding can resolve, and only while current."""
    root = "f13a0013"
    binding = issue_workflow_input_binding(root)
    assert binding is not None and len(binding) > 32
    assert resolve_workflow_input_binding(root, "forged-binding") is None
    assert resolve_workflow_input_binding(root, binding) is not None

    start_workflow_input(root)
    assert resolve_workflow_input_binding(root, binding) is None


def test_f13_effect_ledger_allows_distinct_task_identities_once_each(workflow_db):
    root = "root-effect-identities"
    start_workflow_input(root)
    turn_id, duplicate = queue_workflow_turn(root, "test", "effect-identities")
    assert turn_id is not None and duplicate is False
    assert activate_workflow_turn(root, turn_id)
    assert claim_workflow_turn_receipt(root, turn_id)
    assign = claim_workflow_effect(root, turn_id, "assign", "task-a")
    handoff = claim_workflow_effect(root, turn_id, "handoff", "task-b")
    assert assign is not None and handoff is not None
    assert finish_workflow_effect(root, assign["id"], assign["claim_token"], "completed")
    assert finish_workflow_effect(root, handoff["id"], handoff["claim_token"], "completed")
    assert claim_workflow_effect(root, turn_id, "assign", "task-a") is None
    with database.SessionLocal() as db:
        assert db.query(WorkflowEffectModel).count() == 2


@pytest.mark.parametrize(
    "close",
    [
        lambda root: set_workflow_terminal_state(root, "terminal", "done"),
        lambda root: set_workflow_terminal_state(root, "owner_gate", "owner"),
        cancel_workflows_for_terminal,
    ],
)
def test_f13_effect_ledger_owner_cancel_matrix_fences_unstarted_effects(workflow_db, close):
    root = "root-effect-closed"
    start_workflow_input(root)
    turn_id, duplicate = queue_workflow_turn(root, "test", "effect-closed")
    assert turn_id is not None and duplicate is False
    assert activate_workflow_turn(root, turn_id)
    assert claim_workflow_turn_receipt(root, turn_id)
    assert close(root)
    assert claim_workflow_effect(root, turn_id, "send_message", "target") is None
    with database.SessionLocal() as db:
        assert db.query(WorkflowEffectModel).count() == 0


@pytest.mark.parametrize(
    "close",
    [
        lambda root: set_workflow_terminal_state(root, "terminal", "done"),
        lambda root: set_workflow_terminal_state(root, "owner_gate", "owner"),
        cancel_workflows_for_terminal,
    ],
)
def test_f14_old_effect_cannot_cross_closed_workflow_into_new_turn_matrix(workflow_db, close):
    """A newly opened workflow admits only its own turn, never an old effect."""
    root = "root-old-effect-new-turn"
    start_workflow_input(root)
    old_turn, duplicate = queue_workflow_turn(root, "test", "old-effect")
    assert old_turn is not None and duplicate is False
    assert activate_workflow_turn(root, old_turn)
    assert claim_workflow_turn_receipt(root, old_turn)
    assert close(root)

    new_input_turn = start_workflow_input(root)
    assert new_input_turn is not None
    new_turn, duplicate = queue_workflow_turn(root, "test", "new-effect")
    assert new_turn is not None and duplicate is False
    assert activate_workflow_turn(root, new_turn)
    assert claim_workflow_turn_receipt(root, new_turn)

    assert claim_workflow_effect(root, old_turn, "send_message", "old-target") is None
    assert claim_workflow_effect(root, new_turn, "send_message", "new-target") is not None
    with database.SessionLocal() as db:
        assert db.query(WorkflowEffectModel).count() == 1
        assert db.query(WorkflowModel).filter(WorkflowModel.root_terminal_id == root).count() == 2


def test_f13_privileged_mcp_operations_cannot_bypass_logical_turn_effect_gate(
    workflow_db, monkeypatch
):
    root = "root-effect-mcp"
    start_workflow_input(root)
    turn_id, duplicate = queue_workflow_turn(root, "test", "effect-mcp")
    assert turn_id is not None and duplicate is False
    assert activate_workflow_turn(root, turn_id)
    monkeypatch.setenv("CAO_TERMINAL_ID", root)

    # The public entrypoints themselves—not only continuation wording—require
    # the stable logical identity for every workflow-owned side effect.
    for operation in (
        mcp_server.assign,
        mcp_server.handoff,
        mcp_server.await_handoff,
        mcp_server.send_message,
        mcp_server.acknowledge_assigned_result,
        mcp_server.retire_completed_child,
        mcp_server.complete_workflow,
        mcp_server.owner_gate_workflow,
    ):
        parameter = inspect.signature(operation).parameters["logical_turn_id"]
        assert parameter.default.is_required()

    with patch.object(mcp_server, "_send_message_impl", return_value={"success": True}) as send:
        # A model that skips receiver admission cannot enqueue a downstream
        # inbox write. A replay after admission cannot enqueue a second one.
        rejected = asyncio.run(mcp_server.send_message(turn_id, "target", "payload"))
        assert rejected["success"] is False
        assert claim_workflow_turn_receipt(root, turn_id)
        accepted = asyncio.run(mcp_server.send_message(turn_id, "target", "payload"))
        replay = asyncio.run(mcp_server.send_message(turn_id, "target", "payload"))
    assert accepted["success"] is True
    assert replay["success"] is False
    assert send.call_args.args[:2] == ("target", "payload")


@pytest.mark.parametrize("register", [register_child_assignment, register_handoff_child])
def test_f14_complete_workflow_retains_active_completion_barriers(
    workflow_db, monkeypatch, register
):
    """Normal completion is retryable and cannot silently cancel a live child."""
    parent = f"parent-complete-{register.__name__}"
    turn_id = start_workflow_input(parent)
    assert turn_id is not None
    monkeypatch.setenv("CAO_TERMINAL_ID", parent)
    assert asyncio.run(mcp_server.claim_workflow_turn_receipt(turn_id))["accepted"] is True
    assert register(parent, f"child-complete-{register.__name__}")

    blocked = asyncio.run(mcp_server.complete_workflow(turn_id, "done"))

    assert blocked == {
        "success": False,
        "terminal_id": parent,
        "status": "open",
        "retryable": True,
        "error": "active child completion barrier",
        "active_children": 1,
        "failed_children": 0,
    }
    assert get_workflow_status(parent) == "open"
    assert get_parent_completion_barrier(parent) == (1, 0)


def test_f14_complete_workflow_retries_after_inbox_ack(workflow_db, monkeypatch):
    """The same admitted parent turn may complete once its Inbox result is acknowledged."""
    parent, child = "parent-complete-ack", "child-complete-ack"
    turn_id = start_workflow_input(parent)
    assert turn_id is not None
    monkeypatch.setenv("CAO_TERMINAL_ID", parent)
    assert asyncio.run(mcp_server.claim_workflow_turn_receipt(turn_id))["accepted"] is True
    assert register_child_assignment(parent, child)

    assert asyncio.run(mcp_server.complete_workflow(turn_id, "done"))["retryable"] is True
    notice, duplicate = create_child_assignment_result_message(
        child, parent, "child result", **_authorized_callback(child)
    )
    assert notice is not None and duplicate is False and notice.result_id is not None
    assert mark_child_assignment_result_delivered(notice.id)

    monkeypatch.setenv("CAO_TERMINAL_ID", parent)
    acknowledged = asyncio.run(
        mcp_server.acknowledge_assigned_result(turn_id, result_id=notice.result_id)
    )
    assert acknowledged["success"] is True
    assert get_parent_completion_barrier(parent) == (0, 0)

    completed = asyncio.run(mcp_server.complete_workflow(turn_id, "done"))
    assert completed["success"] is True
    assert completed["status"] == "terminal"
    assert get_workflow_status(parent) == "terminal"


def test_acknowledgement_mcp_preserves_durable_replay_reason(workflow_db, monkeypatch):
    parent, child = "parent-ack-reason", "child-ack-reason"
    first_turn = start_workflow_input(parent)
    assert first_turn is not None
    monkeypatch.setenv("CAO_TERMINAL_ID", parent)
    assert asyncio.run(mcp_server.claim_workflow_turn_receipt(first_turn))["accepted"] is True
    assert register_child_assignment(parent, child)
    notice, duplicate = create_child_assignment_result_message(
        child, parent, "child result", **_authorized_callback(child)
    )
    assert notice is not None and duplicate is False and notice.result_id is not None
    assert mark_child_assignment_result_delivered(notice.id)
    assert (
        asyncio.run(mcp_server.acknowledge_assigned_result(first_turn, result_id=notice.result_id))[
            "success"
        ]
        is True
    )
    # A provider can replay the same acknowledgement before it sees the first
    # response.  That durable replay must win over generic effect dedupe.
    replay = asyncio.run(
        mcp_server.acknowledge_assigned_result(first_turn, result_id=notice.result_id)
    )
    assert replay["success"] is False
    assert replay["accepted"] is False
    assert replay["reason_code"] == "RESULT_ALREADY_ACKNOWLEDGED"


def test_f14_complete_workflow_retains_delivered_direct_handoff_until_parent_ack(
    workflow_db, monkeypatch
):
    """Live direct handoff cleanup is delivery, not parent result consumption."""
    parent, child = "parent-complete-direct", "child-complete-direct"
    turn_id = start_workflow_input(parent)
    assert turn_id is not None
    monkeypatch.setenv("CAO_TERMINAL_ID", parent)
    assert asyncio.run(mcp_server.claim_workflow_turn_receipt(turn_id))["accepted"] is True
    assert register_handoff_child(parent, child)
    assert claim_handoff_child_result_direct(parent, child, "direct child result") is True
    assert acknowledge_handoff_child_result_direct(parent, child) == "direct child result"
    result = get_delegation_result_for_assignment(child)
    assert result is not None and result["status"] == "complete"

    blocked = asyncio.run(mcp_server.complete_workflow(turn_id, "done"))

    assert blocked["retryable"] is True
    assert blocked["status"] == "open"
    assert get_workflow_status(parent) == "open"
    assert get_parent_completion_barrier(parent) == (1, 0)

    # Direct cleanup and a COMPLETE child artifact are transport facts. The
    # barrier releases only after the parent has consumed and acknowledged it.
    assert acknowledge_child_assignment_result(parent, child) is True
    assert get_parent_completion_barrier(parent) == (0, 0)
    assert asyncio.run(mcp_server.complete_workflow(turn_id, "done"))["success"] is True


@pytest.mark.parametrize(
    "close",
    [
        lambda root: set_workflow_terminal_state(root, "terminal", "done"),
        lambda root: set_workflow_terminal_state(root, "owner_gate", "owner"),
        cancel_workflows_for_terminal,
    ],
)
def test_f13_receiver_receipt_rejects_terminal_workflows(workflow_db, close):
    root = "root-receipt-terminal"
    start_workflow_input(root)
    turn_id, duplicate = queue_workflow_turn(root, "test", "terminal-gate")
    assert turn_id is not None and duplicate is False
    assert close(root)
    assert not claim_workflow_turn_receipt(root, turn_id)
    with database.SessionLocal() as db:
        assert db.query(WorkflowTurnReceiptModel).count() == 0


def test_f13_two_claimers_allow_only_owner_dependent_effects(workflow_db):
    root = "root-two-claimers"
    now = datetime(2026, 8, 9, 12, 0, 0)
    _start_admitted_input(root)
    observe_workflow_final(root, now=now)

    owner = claim_workflow_turn(root, now=now)
    assert owner is not None
    assert claim_workflow_turn(root, now=now) is None
    # A contender that did not obtain the durable claim cannot act on its turn.
    assert not mark_workflow_turn_sent(owner["id"], "not-owner", 0, now=now)
    assert not requeue_workflow_turn(owner["id"], "not-owner", 0, now=now)
    assert mark_workflow_turn_sent(
        owner["id"], owner["claim_token"], owner["claim_generation"], now=now
    )


@patch("cli_agent_orchestrator.services.workflow_service.terminal_service")
def test_f13_failed_continuation_transport_requeues_without_a_phantom_sent_wake(
    mock_terminal, workflow_db
):
    _start_admitted_input("root-transport")
    now = datetime(2026, 8, 9, 12, 0, 0)
    observe_workflow_final("root-transport", now=now)
    mock_terminal.get_terminal.return_value = {
        "status": TerminalStatus.COMPLETED.value,
        "lifecycle": "running",
    }
    mock_terminal.send_input.side_effect = RuntimeError("transport down")

    assert workflow_service.reconcile_root_workflow("root-transport", now=now) is False
    with database.SessionLocal() as db:
        turn = db.query(WorkflowTurnModel).filter(WorkflowTurnModel.kind == "open_final").one()
        assert turn.state == "queued"
        assert turn.attempt_count == 1

    mock_terminal.send_input.side_effect = None
    assert (
        workflow_service.reconcile_root_workflow("root-transport", now=now + timedelta(seconds=1))
        is True
    )
    assert mock_terminal.send_input.call_count == 2


def test_f13_real_handoff_progress_resets_no_progress_before_fourth_final(workflow_db):
    root = "root-real-progress"
    _start_admitted_input(root)
    now = datetime(2026, 8, 9, 12, 0, 0)
    for step in range(3):
        current = now + timedelta(seconds=10 * step)
        observe_workflow_final(root, now=current)
        turn = claim_workflow_turn(root, now=current + timedelta(seconds=5))
        assert turn is not None
        _admit_sent_continuation(root, turn, current + timedelta(seconds=5))

    register_handoff_child(root, "child-real-progress")
    result, duplicate = create_handoff_child_result_message("child-real-progress", "real result")
    assert result is not None and duplicate is False
    assert mark_child_assignment_result_delivered(result.id) is True
    assert get_workflow_status(root) == "open"
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter(WorkflowModel.root_terminal_id == root).one()
        assert workflow.no_progress_count == 0

    # The queued callback is real progress, so the following final cannot
    # owner-gate at the formerly accumulated fourth no-progress observation.
    assert observe_workflow_final(root, now=now + timedelta(seconds=40)) is not None
    assert get_workflow_status(root) == "open"


def test_f13_provider_final_does_not_close_active_top_level_mission(workflow_db):
    _start_admitted_input("root-guard")
    now = datetime(2026, 8, 9, 12, 0, 0)

    for step in range(4):
        current = now + timedelta(seconds=10 * step)
        observe_workflow_final("root-guard", now=current)
        turn = claim_workflow_turn("root-guard", now=current + timedelta(seconds=5))
        assert turn is not None
        _admit_sent_continuation("root-guard", turn, current + timedelta(seconds=5))

    successor = observe_workflow_final("root-guard", now=now + timedelta(seconds=40))
    assert successor is not None
    next_turn = claim_workflow_turn("root-guard", now=now + timedelta(seconds=70))
    assert next_turn is not None and next_turn["id"] == successor
    _admit_sent_continuation("root-guard", next_turn, now + timedelta(seconds=70))

    assert get_workflow_status("root-guard") == "open"
    assert claim_workflow_turn_receipt("root-guard", successor) is False
    with database.SessionLocal() as db:
        assert (
            db.query(WorkflowModel)
            .filter(WorkflowModel.root_terminal_id == "root-guard")
            .one()
            .no_progress_count
            == 5
        )


def test_f13_provider_final_continuations_cross_old_ceiling_until_explicit_transition(
    workflow_db,
):
    root = "root-autonomous-no-progress"
    _start_admitted_input(root)
    now = datetime(2026, 8, 9, 12, 0, 0)

    # Cross the former five-final ceiling and prove the same OPEN workflow
    # continues one admitted logical turn at a time without an owner wake.
    for step in range(12):
        current = now + timedelta(seconds=60 * step)
        successor = observe_workflow_final(root, now=current)
        assert successor is not None
        turn = claim_workflow_turn(root, now=current + timedelta(seconds=40))
        assert turn is not None and turn["id"] == successor
        _admit_sent_continuation(root, turn, current + timedelta(seconds=40))

    assert get_workflow_status(root) == "open"
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter(WorkflowModel.root_terminal_id == root).one()
        assert workflow.status == "open"
        assert workflow.no_progress_count == 12

    assert set_workflow_terminal_state(root, "terminal", "accepted") is True
    assert observe_workflow_final(root, now=now + timedelta(seconds=720)) is None


def test_f13_repeated_no_progress_final_hits_durable_owner_visible_circuit_breaker(
    workflow_db, monkeypatch
):
    root = "root-provider-loop"
    _start_admitted_input(root)
    now = datetime(2026, 8, 9, 12, 0, 0)
    monkeypatch.setattr(database, "MAX_AUTOMATIC_OPEN_FINAL_NO_PROGRESS", 2)
    notified = []
    monkeypatch.setattr(
        database,
        "_dispatch_workflow_notification_fail_open",
        lambda terminal_id, event, workflow_id: notified.append((terminal_id, event, workflow_id)),
    )

    for step in range(2):
        current = now + timedelta(seconds=60 * step)
        successor = observe_workflow_final(root, now=current)
        assert successor is not None
        turn = claim_workflow_turn(root, now=current + timedelta(seconds=40))
        assert turn is not None and turn["id"] == successor
        _admit_sent_continuation(root, turn, current + timedelta(seconds=40))

    stale, stale_turn = _pending_inbox_turn(root, "closed by open-final circuit breaker")
    # A fresh DB session models restart recovery: the third no-progress final
    # atomically finishes the paid turn and enters a visible terminal state.
    assert observe_workflow_final(root, now=now + timedelta(seconds=120)) is None
    assert get_workflow_status(root) == "owner_gate"
    assert claim_workflow_turn(root, now=now + timedelta(seconds=180)) is None
    with database.SessionLocal() as db:
        workflow = db.query(WorkflowModel).filter_by(root_terminal_id=root).one()
        assert workflow.no_progress_count == 3
        assert workflow.terminal_reason == database.OPEN_FINAL_CIRCUIT_BREAKER_REASON
        assert (
            db.query(WorkflowTurnModel).filter_by(workflow_id=workflow.id, state="queued").count()
            == 0
        )
        assert db.get(WorkflowTurnModel, stale_turn).state == "cancelled"
        assert db.get(InboxModel, stale.id).status == "failed"
    assert len(notified) == 1 and notified[0][:2] == (root, "owner_attention")

    # Deliberate owner input starts a new workflow instead of reviving the
    # exhausted paid loop, and the new workflow gets a fresh durable budget.
    resumed = start_workflow_input(root)
    assert resumed is not None
    assert claim_workflow_turn_receipt(root, resumed)
    assert get_workflow_status(root) == "open"
    with database.SessionLocal() as db:
        current = (
            db.query(WorkflowModel)
            .filter_by(root_terminal_id=root)
            .order_by(WorkflowModel.id.desc())
            .first()
        )
        assert current is not None and current.no_progress_count == 0
