"""C2 strict child-authenticated handoff V1 submission coverage."""

import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import cli_agent_orchestrator.clients.database as database
from cli_agent_orchestrator.clients.database import (
    Base,
    ChildAssignmentModel,
    DelegationResultEventModel,
    DelegationResultModel,
    DelegationResultSubmissionModel,
    HandoffResultSubmissionError,
    TerminalModel,
    WorkflowTurnModel,
    acknowledge_child_assignment_result_outcome,
    activate_workflow_turn_for_inbox,
    cancel_child_assignments_for_terminal,
    claim_handoff_child_result_direct,
    claim_staged_handoff_result_direct,
    claim_workflow_turn_receipt,
    create_handoff_child_result_message,
    delete_terminal,
    get_delegation_result,
    get_delegation_result_for_assignment,
    get_parent_completion_barrier,
    get_pending_handoff_child_terminal_ids,
    get_pending_message_receiver_ids,
    mark_child_assignment_result_delivered,
    mark_workflow_turn_sent_for_inbox,
    purge_expired_delegation_results,
    register_child_assignment,
    register_handoff_child,
    schedule_managed_handoff_continuation,
    set_workflow_terminal_state,
    start_workflow_input,
    submit_handoff_result_v1,
)
from cli_agent_orchestrator.mcp_server import server as mcp_server
from cli_agent_orchestrator.models.inbox import ChildAssignmentStatus
from cli_agent_orchestrator.models.result import (
    MAX_HANDOFF_RESULT_V1_BYTES,
    HandoffResultDocumentV1,
    canonical_handoff_result_v1_bytes,
)
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.services import workflow_service
from cli_agent_orchestrator.services.inbox_service import (
    _message_for_delivery,
    check_and_send_pending_messages,
    reconcile_handoff_continuations,
    reconcile_pending_messages,
)


def _isolated_db(monkeypatch, *, autoflush=True):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine, autoflush=autoflush))
    monkeypatch.setattr(database, "_child_assignment_schema_ready", True)
    monkeypatch.setattr(database, "_delegation_result_schema_ready", True)


def _concurrent_db(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'handoff-submission.db'}",
        connect_args={"check_same_thread": False, "timeout": 1},
    )
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(database, "_child_assignment_schema_ready", True)
    monkeypatch.setattr(database, "_delegation_result_schema_ready", True)


def _document(**overrides):
    values = {
        "format": "v1",
        "summary": "complete",
        "body_markdown": "A long body that is never read back from a terminal capture.",
        "changed_files": ["src/example.py"],
        "checks": [{"command": "pytest -q", "outcome": "passed"}],
        "risks": [],
        "blockers": [],
    }
    values.update(overrides)
    return HandoffResultDocumentV1.model_validate(values)


def _setup_handoff(monkeypatch, reset=True, *, autoflush=True):
    if reset:
        _isolated_db(monkeypatch, autoflush=autoflush)
    token = "t" * 43
    with database.SessionLocal() as db:
        db.add_all(
            [
                TerminalModel(id="parent", tmux_session="s", tmux_window="p", provider="codex"),
                TerminalModel(
                    id="child",
                    tmux_session="s",
                    tmux_window="c",
                    provider="codex",
                    auth_token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                ),
                TerminalModel(
                    id="sibling",
                    tmux_session="s",
                    tmux_window="x",
                    provider="codex",
                    auth_token_sha256=hashlib.sha256(b"sibling").hexdigest(),
                ),
            ]
        )
        db.commit()
    assert register_handoff_child("parent", "child")
    turn = start_workflow_input("child")
    assert turn is not None and claim_workflow_turn_receipt("child", turn)
    return token, turn


def test_strict_schema_and_canonical_digest_are_deterministic():
    document = _document()
    canonical = canonical_handoff_result_v1_bytes(document)
    assert canonical == canonical_handoff_result_v1_bytes(
        HandoffResultDocumentV1.model_validate(document.model_dump())
    )
    assert canonical.startswith(b'{"blockers":[]')

    with pytest.raises(ValidationError):
        HandoffResultDocumentV1.model_validate({"format": "v1"})
    with pytest.raises(ValidationError):
        HandoffResultDocumentV1.model_validate({**document.model_dump(), "extra": True})
    with pytest.raises(ValidationError):
        HandoffResultDocumentV1.model_validate(
            {**document.model_dump(), "checks": [{"command": "x", "outcome": "ok", "extra": 1}]}
        )
    with pytest.raises(ValidationError):
        HandoffResultDocumentV1.model_validate(
            {**document.model_dump(), "body_markdown": "bad\x00value"}
        )
    with pytest.raises(ValidationError):
        HandoffResultDocumentV1.model_validate(
            {**document.model_dump(), "body_markdown": "x" * MAX_HANDOFF_RESULT_V1_BYTES}
        )


def test_exited_structured_handoff_exhausts_to_terminal_incomplete(monkeypatch):
    _setup_handoff(monkeypatch)
    # The first two provider exits preserve the exact managed relation for
    # bounded same-child recovery.  The next reconciliation must not rewrite
    # it back to recovery forever.
    assert cancel_child_assignments_for_terminal("child") == 1
    assert cancel_child_assignments_for_terminal("child") == 1
    assert cancel_child_assignments_for_terminal("child") == 1
    result = get_delegation_result_for_assignment("child")
    assert result is not None
    assert result["status"] == "incomplete"
    assert result["reason_code"] == "handoff_recovery_exhausted"
    assert "child" not in get_pending_handoff_child_terminal_ids()
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id="child").one()
        assert assignment.status == ChildAssignmentStatus.HANDOFF_RESULT_FAILED.value
        before_events = (
            db.query(DelegationResultEventModel).filter_by(result_id=result["id"]).count()
        )
        before_results = db.query(DelegationResultModel).count()
    assert reconcile_handoff_continuations(child_terminal_id="child") == 0
    with database.SessionLocal() as db:
        assert (
            db.query(DelegationResultEventModel).filter_by(result_id=result["id"]).count()
            == before_events
        )
        assert db.query(DelegationResultModel).count() == before_results


def test_authenticated_submission_is_idempotent_and_structured_first(monkeypatch):
    token, turn = _setup_handoff(monkeypatch)
    document = _document()

    first = submit_handoff_result_v1(token, turn, document)
    retry = submit_handoff_result_v1(token, turn, document)
    assert first["accepted"] is True and first["duplicate"] is False
    assert retry["accepted"] is True and retry["duplicate"] is True
    assert retry["result_id"] == first["result_id"]
    assert get_delegation_result_for_assignment("child")["status"] == "complete"

    # Completed direct handoff claims the authenticated immutable document
    # before terminal rendering is consulted.
    assert claim_staged_handoff_result_direct("parent", "child") is not True
    result = get_delegation_result_for_assignment("child")
    assert result["status"] == "complete"
    assert result["authorship"] == "child_structured_submission"
    assert result["document"] == document.model_dump(mode="json")
    assert result["content_sha256"] == first["content_sha256"]
    with database.SessionLocal() as db:
        assert db.query(DelegationResultSubmissionModel).count() == 1


def test_progress_only_public_capture_keeps_managed_handoff_recoverable_until_v1(monkeypatch):
    """The live acceptance text cannot create a legacy managed success row."""
    token, turn = _setup_handoff(monkeypatch)

    assert claim_handoff_child_result_direct("parent", "child", "PROGRESS_ONLY_SCENARIO_1") is False
    initial = get_delegation_result_for_assignment("child")
    assert initial is not None and initial["status"] == "awaiting"
    assert cancel_child_assignments_for_terminal("child") == 1
    recovering = get_delegation_result_for_assignment("child")
    assert recovering is not None
    assert recovering["id"] == initial["id"]
    assert recovering["status"] == "awaiting"

    accepted = submit_handoff_result_v1(token, turn, _document())
    replay = submit_handoff_result_v1(token, turn, _document())
    assert accepted["accepted"] is True and accepted["duplicate"] is False
    assert replay["accepted"] is True and replay["duplicate"] is True
    assert accepted["result_id"] == initial["id"] == replay["result_id"]
    assert get_delegation_result_for_assignment("child")["status"] == "complete"
    with database.SessionLocal() as db:
        assert db.query(DelegationResultModel).count() == 1


def _admit_managed_continuation(child: str, scheduled: dict) -> int:
    """Model the durable Inbox transport boundary without a provider process."""
    message = scheduled["message"]
    assert message is not None
    assert activate_workflow_turn_for_inbox(message.id) == scheduled["turn_id"]
    assert f"logical-turn={scheduled['turn_id']}" in _message_for_delivery(
        message, scheduled["turn_id"]
    )
    assert mark_workflow_turn_sent_for_inbox(message.id)
    assert claim_workflow_turn_receipt(child, scheduled["turn_id"])
    return scheduled["turn_id"]


def test_same_child_recovery_continuation_gets_new_admission_and_completes_same_result(monkeypatch):
    token, first_turn = _setup_handoff(monkeypatch)
    assert claim_handoff_child_result_direct("parent", "child", "PROGRESS_ONLY_SCENARIO_1") is False
    original = get_delegation_result_for_assignment("child")
    assert original is not None and original["status"] == "awaiting"

    scheduled = schedule_managed_handoff_continuation("parent", "child", "submit the V1 result")
    duplicate = schedule_managed_handoff_continuation("parent", "child", "parent retry")

    assert scheduled["managed"] is True and scheduled["accepted"] is True
    assert scheduled["duplicate"] is False
    assert duplicate["duplicate"] is True
    assert scheduled["turn_id"] != first_turn
    assert duplicate["turn_id"] == scheduled["turn_id"]
    with database.SessionLocal() as db:
        assert db.query(WorkflowTurnModel).count() == 2
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id="child").one()
        assert assignment.status == ChildAssignmentStatus.HANDOFF_RECOVERY_AWAITING_RESULT.value

    second_turn = _admit_managed_continuation("child", scheduled)
    accepted = submit_handoff_result_v1(token, second_turn, _document(summary="continued"))
    replay = submit_handoff_result_v1(token, second_turn, _document(summary="continued"))

    assert accepted["accepted"] is True and accepted["duplicate"] is False
    assert replay["duplicate"] is True
    assert accepted["result_id"] == original["id"] == replay["result_id"]
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id="child").one()
        result_messages = (
            db.query(database.InboxModel)
            .filter_by(receiver_id="parent", result_id=accepted["result_id"])
            .all()
        )
        assert len(result_messages) == 1
        assert assignment.result_message_id == result_messages[0].id
        assert mark_child_assignment_result_delivered(result_messages[0].id)
    assert get_parent_completion_barrier("parent") == (1, 0)


@patch("cli_agent_orchestrator.services.inbox_service.workflow_service.reconcile_open_workflows")
@patch("cli_agent_orchestrator.services.inbox_service.reconcile_handoff_continuations")
@patch(
    "cli_agent_orchestrator.services.inbox_service.requeue_unacknowledged_child_assignment_results"
)
@patch("cli_agent_orchestrator.services.inbox_service.arm_handoff_continuations_for_restart")
@patch(
    "cli_agent_orchestrator.services.inbox_service.terminalize_missing_terminal_assignments_for_restart"
)
@patch("cli_agent_orchestrator.services.inbox_service.terminal_service.send_input")
@patch("cli_agent_orchestrator.services.inbox_service.provider_manager.get_provider")
def test_failed_managed_continuation_transport_retries_same_turn_and_result(
    mock_provider,
    mock_send,
    _mock_terminalize,
    _mock_arm,
    _mock_requeue_results,
    _mock_reconcile_handoff,
    _mock_reconcile_workflows,
    monkeypatch,
):
    _mock_reconcile_workflows.return_value = 0
    token, _first_turn = _setup_handoff(monkeypatch)
    original = get_delegation_result_for_assignment("child")
    assert original is not None
    parent_turn = start_workflow_input("parent")
    assert parent_turn is not None and claim_workflow_turn_receipt("parent", parent_turn)

    provider = MagicMock()
    provider.get_status.return_value = TerminalStatus.IDLE
    mock_provider.return_value = provider
    mock_send.side_effect = RuntimeError("transient transport failure")
    with patch.dict("os.environ", {"CAO_TERMINAL_ID": "parent"}):
        first = asyncio.run(mcp_server.send_message(parent_turn, "child", "submit the V1 result"))
        duplicate_effect = asyncio.run(
            mcp_server.send_message(parent_turn, "child", "submit the V1 result")
        )
    assert first["success"] is True and first["managed_handoff_continuation"] is True
    assert duplicate_effect["reason_code"] == "DUPLICATE_EFFECT"
    scheduled = {"turn_id": first["logical_turn_id"]}

    with database.SessionLocal() as db:
        inbox = (
            db.query(database.InboxModel)
            .filter_by(receiver_id="child", kind="handoff_recovery_continuation")
            .one()
        )
        turn = db.query(WorkflowTurnModel).filter_by(id=scheduled["turn_id"]).one()
        assert inbox.status == "pending"
        assert turn.state == "queued"
        # The normal claim backoff is intentional; make its due time explicit
        # so this deterministic test exercises the next watchdog tick.
        turn.not_before = datetime.now() - timedelta(seconds=1)
        db.commit()
    assert get_pending_message_receiver_ids() == ["child"]

    mock_send.side_effect = None
    assert reconcile_pending_messages() == 1
    delivered = mock_send.call_args.args[1]
    assert f"logical-turn={scheduled['turn_id']}" in delivered
    assert claim_workflow_turn_receipt("child", scheduled["turn_id"])
    assert reconcile_pending_messages() == 0

    accepted = submit_handoff_result_v1(token, scheduled["turn_id"], _document(summary="retried"))
    assert accepted["accepted"] is True
    assert accepted["result_id"] == original["id"]
    with database.SessionLocal() as db:
        assert db.query(WorkflowTurnModel).filter_by(kind="handoff_recovery").count() == 1
        assert (
            db.query(database.InboxModel)
            .filter_by(receiver_id="parent", result_id=accepted["result_id"])
            .count()
            == 1
        )


@patch("cli_agent_orchestrator.services.workflow_service.terminal_service")
@patch("cli_agent_orchestrator.services.inbox_service.terminal_service.send_input")
@patch("cli_agent_orchestrator.services.inbox_service.provider_manager.get_provider")
def test_ready_managed_child_dispatches_the_same_recovery_turn_after_initial_busy_probe(
    mock_provider, mock_send, mock_workflow_terminal, monkeypatch
):
    """A live Codex child receives N+1 without a human terminal input.

    The first send_message probe models Codex's completion debounce window.
    The workflow daemon must later deliver the already-persisted Inbox row to
    the exact same child provider, not manufacture another continuation.
    """
    token, initial_turn = _setup_handoff(monkeypatch)
    assert claim_handoff_child_result_direct("parent", "child", "PROGRESS_ONLY_SCENARIO_1") is False
    original = get_delegation_result_for_assignment("child")
    assert original is not None and original["status"] == "awaiting"

    parent_turn = start_workflow_input("parent")
    assert parent_turn is not None and claim_workflow_turn_receipt("parent", parent_turn)
    provider = MagicMock()
    provider.is_process_alive.return_value = True
    provider.get_status.side_effect = [TerminalStatus.PROCESSING, TerminalStatus.IDLE]
    mock_provider.return_value = provider

    with patch.dict("os.environ", {"CAO_TERMINAL_ID": "parent"}):
        scheduled = asyncio.run(
            mcp_server.send_message(parent_turn, "child", "continue and submit the V1 result")
        )
        duplicate_effect = asyncio.run(
            mcp_server.send_message(parent_turn, "child", "continue and submit the V1 result")
        )

    assert scheduled["success"] is True
    assert scheduled["duplicate"] is False
    assert scheduled["managed_handoff_continuation"] is True
    assert duplicate_effect["reason_code"] == "DUPLICATE_EFFECT"
    continuation_turn = scheduled["logical_turn_id"]
    assert continuation_turn != initial_turn
    assert mock_send.call_count == 0

    # The existing child provider is now Ready. A daemon reconciliation must
    # transport N+1 once, with its CAO workflow envelope, to that same pane.
    mock_workflow_terminal.get_terminal.return_value = {
        "status": TerminalStatus.IDLE.value,
        "lifecycle": "running",
    }
    assert workflow_service.reconcile_root_workflow("child") is False
    assert workflow_service.reconcile_root_workflow("child") is False
    mock_send.assert_called_once()
    assert mock_send.call_args.args[0] == "child"
    assert f"logical-turn={continuation_turn}" in mock_send.call_args.args[1]

    assert claim_workflow_turn_receipt("child", continuation_turn)
    accepted = submit_handoff_result_v1(
        token, continuation_turn, _document(summary="continued by the same child")
    )
    assert accepted["accepted"] is True
    assert accepted["result_id"] == original["id"]
    assert get_delegation_result_for_assignment("child")["document"]["format"] == "v1"

    with database.SessionLocal() as db:
        child_assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id="child").one()
        child_workflow = db.query(database.WorkflowModel).filter_by(root_terminal_id="child").one()
        child_turns = db.query(WorkflowTurnModel).filter_by(workflow_id=child_workflow.id).all()
        continuation_messages = (
            db.query(database.InboxModel)
            .filter_by(receiver_id="child", kind="handoff_recovery_continuation")
            .all()
        )
        authoritative_notices = (
            db.query(database.InboxModel)
            .filter_by(receiver_id="parent", result_id=accepted["result_id"])
            .all()
        )
        assert child_assignment.parent_terminal_id == "parent"
        assert child_assignment.child_terminal_id == "child"
        assert len([turn for turn in child_turns if turn.kind == "handoff_recovery"]) == 1
        assert len(continuation_messages) == 1
        assert continuation_messages[0].status == "delivered"
        assert len(authoritative_notices) == 1


@patch("cli_agent_orchestrator.services.inbox_service.provider_manager.get_provider")
def test_exited_provider_does_not_mark_managed_continuation_delivered(mock_provider, monkeypatch):
    """Exit discovery during status refresh leaves the same N+1 retryable."""
    _token, _initial_turn = _setup_handoff(monkeypatch)
    scheduled = schedule_managed_handoff_continuation("parent", "child", "continue with V1")
    provider = MagicMock()
    provider.is_process_alive.return_value = True

    def observe_exit():
        provider.is_process_alive.return_value = False
        return TerminalStatus.COMPLETED

    provider.get_status.side_effect = observe_exit
    mock_provider.return_value = provider

    assert check_and_send_pending_messages("child") is False
    with database.SessionLocal() as db:
        inbox = db.query(database.InboxModel).filter_by(id=scheduled["message"].id).one()
        turn = db.query(WorkflowTurnModel).filter_by(id=scheduled["turn_id"]).one()
        assert inbox.status == "pending"
        assert turn.state == "queued"
        assert (
            db.query(WorkflowTurnModel)
            .filter_by(workflow_id=turn.workflow_id, kind="handoff_recovery")
            .count()
            == 1
        )


@patch("cli_agent_orchestrator.services.inbox_service.provider_manager.get_provider")
def test_exited_provider_fails_an_ordinary_inbox_message_without_retrying(
    mock_provider, monkeypatch
):
    """Only managed continuations remain retryable after provider exit."""
    _setup_handoff(monkeypatch)
    ordinary = database.create_inbox_message("parent", "child", "ordinary message")
    provider = MagicMock()
    provider.is_process_alive.return_value = False
    mock_provider.return_value = provider

    assert check_and_send_pending_messages("child") is False
    with database.SessionLocal() as db:
        inbox = db.query(database.InboxModel).filter_by(id=ordinary.id).one()
        assert inbox.status == "failed"


@pytest.mark.parametrize("close", ["owner_gate", "cancelled"])
def test_managed_continuation_rejects_closed_parent_without_new_child_turn(monkeypatch, close):
    _token, _turn = _setup_handoff(monkeypatch)
    if close == "owner_gate":
        assert set_workflow_terminal_state("parent", "owner_gate", "owner decision")
    else:
        assert database.cancel_workflows_for_terminal("parent") == 1

    rejected = schedule_managed_handoff_continuation("parent", "child", "resume")

    assert rejected == {
        "managed": True,
        "accepted": False,
        "reason_code": "PARENT_WORKFLOW_CLOSED",
    }
    with database.SessionLocal() as db:
        assert db.query(WorkflowTurnModel).count() == 1


def test_managed_continuation_rejects_owner_gated_child_without_new_turn(monkeypatch):
    _token, _turn = _setup_handoff(monkeypatch)
    assert set_workflow_terminal_state("child", "owner_gate", "child requires owner")

    rejected = schedule_managed_handoff_continuation("parent", "child", "resume")

    assert rejected == {
        "managed": True,
        "accepted": False,
        "reason_code": "CHILD_WORKFLOW_CLOSED",
    }
    with database.SessionLocal() as db:
        assert db.query(WorkflowTurnModel).count() == 1


def test_managed_continuation_exhaustion_stops_before_a_third_successor(monkeypatch):
    _token, _turn = _setup_handoff(monkeypatch)
    first = schedule_managed_handoff_continuation("parent", "child", "retry one")
    _admit_managed_continuation("child", first)
    second = schedule_managed_handoff_continuation("parent", "child", "retry two")
    _admit_managed_continuation("child", second)

    exhausted = schedule_managed_handoff_continuation("parent", "child", "retry three")

    assert exhausted == {
        "managed": True,
        "accepted": False,
        "reason_code": "HANDOFF_RECOVERY_EXHAUSTED",
    }
    result = get_delegation_result_for_assignment("child")
    assert result is not None
    assert result["status"] == "incomplete"
    assert result["reason_code"] == "handoff_recovery_exhausted"
    with database.SessionLocal() as db:
        assert db.query(WorkflowTurnModel).count() == 3


def test_delegated_handoff_owner_gate_is_rejected_and_v1_remains_submitable(monkeypatch):
    """The public MCP owner-gate entrypoint cannot strand a managed child."""
    token, turn = _setup_handoff(monkeypatch)
    monkeypatch.setenv("CAO_TERMINAL_ID", "child")

    rejected = asyncio.run(
        mcp_server.owner_gate_workflow(turn, "synthetic child authorization check")
    )

    assert rejected["success"] is False
    assert rejected["accepted"] is False
    assert rejected["reason_code"] == "CHILD_NOT_AUTHORIZED"
    assert rejected["workflow_state"] == "open"
    assert database.get_workflow_status("child") == "open"

    accepted = submit_handoff_result_v1(token, turn, _document())
    assert accepted["accepted"] is True
    assert get_delegation_result_for_assignment("child")["status"] == "complete"


def test_scenario_1_v1_is_immutable_through_provider_compatibility_capture(monkeypatch):
    """Production's no-autoflush session must preserve V1 after final capture."""
    token, turn = _setup_handoff(monkeypatch, autoflush=False)
    document = _document(summary="SCENARIO_1_V1")

    assert claim_handoff_child_result_direct("parent", "child", "PROGRESS_ONLY_SCENARIO_1") is False
    awaiting = get_delegation_result_for_assignment("child")
    assert awaiting is not None and awaiting["status"] == "awaiting"

    accepted = submit_handoff_result_v1(token, turn, document)
    assert accepted["accepted"] is True and accepted["duplicate"] is False
    before = get_delegation_result(accepted["result_id"])
    assert before is not None

    # This is the normal provider-final compatibility capture, deliberately
    # conflicting with the submitted V1. It must return the existing notice,
    # not rebuild the artifact from terminal prose.
    notice, duplicate = create_handoff_child_result_message(
        "child",
        'CAO_RESULT_V1\n{"summary":"conflicting terminal capture","format":"v1"}',
    )
    assert notice is not None and notice.result_id == accepted["result_id"] and duplicate is True
    assert mark_child_assignment_result_delivered(notice.id)
    acknowledgement = acknowledge_child_assignment_result_outcome(
        "parent", result_id=accepted["result_id"]
    )
    assert acknowledgement["accepted"] is True
    replay = submit_handoff_result_v1(token, turn, document)
    after = get_delegation_result(accepted["result_id"])

    assert replay["duplicate"] is True and replay["result_id"] == accepted["result_id"]
    assert after["delivery_status"] == "handoff_result_acknowledged"
    for field in (
        "id",
        "status",
        "schema_version",
        "document",
        "authorship",
        "content_sha256",
        "content_bytes",
        "workflow_turn_id",
        "workflow_effect_id",
        "finalized_at",
    ):
        assert after[field] == before[field]
    assert after["status"] == "complete"
    assert after["schema_version"] == 1
    assert after["document"] == document.model_dump(mode="json")
    assert after["authorship"] == "child_structured_submission"
    assert after["content_sha256"] == accepted["content_sha256"]
    with database.SessionLocal() as db:
        assert db.query(DelegationResultModel).count() == 1


def test_scenario_3_owner_gate_rejection_does_not_downgrade_v1_capture(monkeypatch):
    """A rejected delegated owner gate leaves the V1 authority untouched."""
    token, turn = _setup_handoff(monkeypatch, autoflush=False)
    document = _document(summary="SCENARIO_3_V1")
    monkeypatch.setenv("CAO_TERMINAL_ID", "child")

    rejected = asyncio.run(
        mcp_server.owner_gate_workflow(turn, "synthetic child authorization check")
    )
    assert rejected["reason_code"] == "CHILD_NOT_AUTHORIZED"
    assert database.get_workflow_status("child") == "open"

    accepted = submit_handoff_result_v1(token, turn, document)
    before = get_delegation_result(accepted["result_id"])
    notice, duplicate = create_handoff_child_result_message("child", "legacy provider-final prose")
    after = get_delegation_result(accepted["result_id"])

    assert notice is not None and duplicate is True
    for field in ("id", "status", "schema_version", "document", "authorship", "content_sha256"):
        assert after[field] == before[field]
    assert after["document"] == document.model_dump(mode="json")
    assert after["authorship"] == "child_structured_submission"


@pytest.mark.parametrize("terminal_state", ["cancelled", "owner_gate"])
def test_terminal_or_owner_gate_cannot_revive_staged_handoff(monkeypatch, terminal_state):
    token, turn = _setup_handoff(monkeypatch)
    accepted = submit_handoff_result_v1(token, turn, _document())

    assert database.set_workflow_terminal_state("parent", terminal_state)
    assert claim_staged_handoff_result_direct("parent", "child") is not True
    result = get_delegation_result_for_assignment("child")
    assert result["id"] == accepted["result_id"]
    assert result["status"] == "complete"
    with database.SessionLocal() as db:
        assert db.query(DelegationResultSubmissionModel).count() == 1


@pytest.mark.parametrize("bad_token", ["", "wrong"])
def test_submission_rejects_missing_or_wrong_terminal_capability(monkeypatch, bad_token):
    _token, turn = _setup_handoff(monkeypatch)
    with pytest.raises(HandoffResultSubmissionError) as error:
        submit_handoff_result_v1(bad_token, turn, _document())
    assert error.value.status_code == 401


def test_submission_derives_sibling_identity_from_its_bearer_token(monkeypatch):
    _token, turn = _setup_handoff(monkeypatch)
    with pytest.raises(HandoffResultSubmissionError) as error:
        submit_handoff_result_v1("sibling", turn, _document())
    assert error.value.code == "not_handoff_child"


def test_submission_rejects_stale_turn_and_conflicting_retry_without_overwrite(monkeypatch):
    token, turn = _setup_handoff(monkeypatch)
    with pytest.raises(HandoffResultSubmissionError) as stale:
        submit_handoff_result_v1(token, turn + 1, _document())
    assert stale.value.code == "turn_not_admitted"

    first = submit_handoff_result_v1(token, turn, _document())
    with pytest.raises(HandoffResultSubmissionError) as conflict:
        submit_handoff_result_v1(token, turn, _document(body_markdown="different"))
    assert conflict.value.code == "submission_conflict"
    result = get_delegation_result_for_assignment("child")
    assert result["status"] == "complete"
    with database.SessionLocal() as db:
        events = db.query(DelegationResultEventModel).filter_by(result_id=first["result_id"]).all()
    assert [event.event_type for event in events].count("submission_conflict") == 1


def test_assignment_child_cannot_submit_handoff_document(monkeypatch):
    _isolated_db(monkeypatch)
    token = "assign-token"
    with database.SessionLocal() as db:
        db.add_all(
            [
                TerminalModel(id="parent", tmux_session="s", tmux_window="p", provider="codex"),
                TerminalModel(
                    id="child",
                    tmux_session="s",
                    tmux_window="c",
                    provider="codex",
                    auth_token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                ),
            ]
        )
        db.commit()
    assert register_child_assignment("parent", "child")
    turn = start_workflow_input("child")
    assert turn is not None and claim_workflow_turn_receipt("child", turn)
    with pytest.raises(HandoffResultSubmissionError) as error:
        submit_handoff_result_v1(token, turn, _document())
    assert error.value.code == "not_handoff_child"


def test_cancelled_handoff_purges_staging_without_revival(monkeypatch):
    token, turn = _setup_handoff(monkeypatch)
    accepted = submit_handoff_result_v1(token, turn, _document())
    assert cancel_child_assignments_for_terminal("child") == 0
    result = get_delegation_result_for_assignment("child")
    assert result["id"] == accepted["result_id"]
    assert result["status"] == "complete"
    with database.SessionLocal() as db:
        assert db.query(DelegationResultSubmissionModel).count() == 1
    assert submit_handoff_result_v1(token, turn, _document())["duplicate"] is True


def test_concurrent_identical_submissions_return_one_record_and_one_duplicate(
    monkeypatch, tmp_path
):
    _concurrent_db(monkeypatch, tmp_path)
    token, turn = _setup_handoff(monkeypatch, reset=False)
    gate = Barrier(2)

    def submit():
        gate.wait()
        return submit_handoff_result_v1(token, turn, _document())

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _unused: submit(), range(2)))

    assert sorted((first["duplicate"], second["duplicate"])) == [False, True]
    with database.SessionLocal() as db:
        assert db.query(DelegationResultSubmissionModel).count() == 1
        assert (
            db.query(DelegationResultEventModel).filter_by(event_type="submission_recorded").count()
            == 1
        )


def test_concurrent_conflicting_submissions_preserve_first_document(monkeypatch, tmp_path):
    _concurrent_db(monkeypatch, tmp_path)
    token, turn = _setup_handoff(monkeypatch, reset=False)
    gate = Barrier(2)

    def submit(document):
        gate.wait()
        try:
            return submit_handoff_result_v1(token, turn, document)
        except HandoffResultSubmissionError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, [_document(), _document(body_markdown="conflict")]))

    accepted = [outcome for outcome in outcomes if isinstance(outcome, dict)]
    rejected = [
        outcome for outcome in outcomes if isinstance(outcome, HandoffResultSubmissionError)
    ]
    assert len(accepted) == len(rejected) == 1
    assert rejected[0].code == "submission_conflict"
    with database.SessionLocal() as db:
        staged = db.query(DelegationResultSubmissionModel).one()
        assert staged.document_json in {
            canonical_handoff_result_v1_bytes(_document()).decode(),
            canonical_handoff_result_v1_bytes(_document(body_markdown="conflict")).decode(),
        }
        assert (
            db.query(DelegationResultEventModel).filter_by(event_type="submission_conflict").count()
            == 1
        )


def test_terminal_delete_and_ttl_purge_staging_in_their_transaction(monkeypatch):
    token, turn = _setup_handoff(monkeypatch)
    submit_handoff_result_v1(token, turn, _document())
    assert delete_terminal("child")
    with database.SessionLocal() as db:
        assert db.query(DelegationResultSubmissionModel).count() == 0


def test_parent_workflow_terminalization_purges_staging_in_its_transaction(monkeypatch):
    token, turn = _setup_handoff(monkeypatch)
    submit_handoff_result_v1(token, turn, _document())

    assert database.set_workflow_terminal_state("parent", "cancelled")
    with database.SessionLocal() as db:
        assert db.query(DelegationResultSubmissionModel).count() == 1


def test_parent_terminal_metadata_delete_purges_staging_in_its_transaction(monkeypatch):
    token, turn = _setup_handoff(monkeypatch)
    submit_handoff_result_v1(token, turn, _document())

    assert delete_terminal("parent")
    with database.SessionLocal() as db:
        assert db.query(DelegationResultSubmissionModel).count() == 0


def test_ttl_purges_staging_in_its_transaction(monkeypatch):
    token, turn = _setup_handoff(monkeypatch)
    accepted = submit_handoff_result_v1(token, turn, _document())
    with database.SessionLocal() as db:
        result = db.query(DelegationResultModel).filter_by(id=accepted["result_id"]).one()
        assignment = db.query(ChildAssignmentModel).filter_by(id=result.child_assignment_id).one()
        result.status = "cancelled"
        result.finalized_at = datetime.now() - timedelta(days=90)
        assignment.status = "cancelled"
        db.query(database.WorkflowModel).filter_by(root_terminal_id="parent").update(
            {"status": "terminal"}
        )
        db.commit()
    assert purge_expired_delegation_results(datetime.now() - timedelta(days=30)) == 0
    with database.SessionLocal() as db:
        assert db.query(DelegationResultSubmissionModel).count() == 1
