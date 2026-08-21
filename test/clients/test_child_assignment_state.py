"""Deterministic Phase-B coverage for durable assign callback state."""

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import cli_agent_orchestrator.clients.database as database
from cli_agent_orchestrator.clients.database import (
    Base,
    ChildAssignmentModel,
    DelegationResultModel,
    InboxModel,
    WorkflowModel,
    WorkflowTurnModel,
    WorkflowTurnReceiptModel,
    acknowledge_child_assignment_result,
    acknowledge_handoff_child_result_direct,
    arm_handoff_continuations_for_restart,
    cancel_child_assignments_for_terminal,
    claim_handoff_child_result_direct,
    claim_workflow_effect,
    claim_workflow_turn_receipt,
    create_child_assignment_result_message,
    create_handoff_child_result_message,
    get_acknowledged_handoff_child_result_direct,
    get_child_assignment_result_child_id,
    get_claimed_handoff_child_result_direct,
    get_parent_completion_barrier,
    get_pending_handoff_child_terminal_ids,
    handoff_child_input_received,
    issue_workflow_input_binding,
    mark_child_assignment_result_delivered,
    mark_child_assignment_result_failed,
    mark_handoff_child_input_received,
    register_child_assignment,
    register_handoff_child,
    requeue_unacknowledged_child_assignment_results,
    resolve_workflow_input_binding,
    start_workflow_input,
)
from cli_agent_orchestrator.models.inbox import MessageStatus


@pytest.fixture
def child_assignment_db(monkeypatch):
    """Use an isolated durable-store shape without the server runtime."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(database, "_child_assignment_schema_ready", True)


def _authorized_callback(child_id: str):
    turn_id = start_workflow_input(child_id)
    assert turn_id is not None
    assert claim_workflow_turn_receipt(child_id, turn_id)
    effect = claim_workflow_effect(child_id, turn_id, "send_message", "test-callback")
    assert effect is not None
    return {"workflow_effect_id": effect["id"], "workflow_turn_id": turn_id}


def _legacy_handoff_snapshot_with_admitted_child_input(parent_id: str, child_id: str) -> int:
    """Build the exact pre-marker state accepted by the compatibility read."""
    assert register_handoff_child(parent_id, child_id) is True
    binding = issue_workflow_input_binding(child_id)
    assert binding is not None
    turn_id = resolve_workflow_input_binding(child_id, binding)
    assert turn_id is not None
    assert claim_workflow_turn_receipt(child_id, turn_id)
    return turn_id


def test_first_child_result_is_idempotent_and_releases_only_after_parent_ack(child_assignment_db):
    assert register_child_assignment("parent01", "child001") is True
    assert register_child_assignment("parent01", "child001") is False

    first, duplicate = create_child_assignment_result_message(
        "child001", "parent01", "result", **_authorized_callback("child001")
    )
    retry, retry_duplicate = create_child_assignment_result_message(
        "child001", "parent01", "result retry", **_authorized_callback("child001")
    )

    assert duplicate is False
    assert retry_duplicate is True
    assert first is not None and retry is not None
    assert retry.id == first.id
    assert get_parent_completion_barrier("parent01") == (1, 0)

    assert mark_child_assignment_result_delivered(first.id) is True
    # Delivery means only that input reached the terminal, so a parent that
    # already looks completed remains protected from a false /exit.
    assert get_parent_completion_barrier("parent01") == (1, 0)
    assert acknowledge_child_assignment_result("other-parent", "child001") is False
    assert acknowledge_child_assignment_result("parent01", "child001") is True
    assert get_parent_completion_barrier("parent01") == (0, 0)
    assert acknowledge_child_assignment_result("parent01", "child001") is True


def test_restart_requeues_unacknowledged_delivery_without_new_logical_result(child_assignment_db):
    register_child_assignment("parent-restart", "child-restart")
    first, duplicate = create_child_assignment_result_message(
        "child-restart", "parent-restart", "first result", **_authorized_callback("child-restart")
    )
    assert first is not None and duplicate is False
    assert mark_child_assignment_result_delivered(first.id) is True

    assert requeue_unacknowledged_child_assignment_results() == 1
    assert get_parent_completion_barrier("parent-restart") == (1, 0)
    with database.SessionLocal() as db:
        inbox_rows = db.query(InboxModel).all()
        assert len(inbox_rows) == 1
        assert inbox_rows[0].id == first.id
        assert inbox_rows[0].status == MessageStatus.PENDING.value

    retry, retry_duplicate = create_child_assignment_result_message(
        "child-restart", "parent-restart", "retry result", **_authorized_callback("child-restart")
    )
    assert retry_duplicate is True
    assert retry is not None and retry.id == first.id


def test_assigned_result_identity_survives_delivery_and_restart(child_assignment_db):
    register_child_assignment("parent-envelope", "child-envelope")
    result, duplicate = create_child_assignment_result_message(
        "child-envelope",
        "parent-envelope",
        "result without injected sender text",
        **_authorized_callback("child-envelope"),
    )
    assert result is not None and duplicate is False
    assert get_child_assignment_result_child_id(result.id) == "child-envelope"

    assert mark_child_assignment_result_delivered(result.id) is True
    assert get_child_assignment_result_child_id(result.id) == "child-envelope"
    assert requeue_unacknowledged_child_assignment_results() == 1
    assert get_child_assignment_result_child_id(result.id) == "child-envelope"

    assert acknowledge_child_assignment_result("parent-envelope", "child-envelope") is False
    assert mark_child_assignment_result_delivered(result.id) is True
    assert acknowledge_child_assignment_result("parent-envelope", "child-envelope") is True
    assert get_child_assignment_result_child_id(result.id) == "child-envelope"


def test_failed_callback_remains_a_visible_barrier_until_explicit_cancellation(child_assignment_db):
    register_child_assignment("parent02", "child002")
    result, _ = create_child_assignment_result_message(
        "child002", "parent02", "result", **_authorized_callback("child002")
    )
    assert result is not None

    assert mark_child_assignment_result_failed(result.id) is True
    assert get_parent_completion_barrier("parent02") == (1, 1)

    assert cancel_child_assignments_for_terminal("parent02") == 1
    assert get_parent_completion_barrier("parent02") == (0, 0)
    late, duplicate = create_child_assignment_result_message("child002", "parent02", "late")
    assert late is None
    assert duplicate is True


def test_child_cannot_be_adopted_by_a_different_parent(child_assignment_db):
    register_child_assignment("parent03", "child003")

    with pytest.raises(ValueError, match="already belongs"):
        register_child_assignment("other003", "child003")


def test_restart_handoff_result_queues_one_durable_same_parent_continuation(child_assignment_db):
    assert register_handoff_child("parent-restart", "child-handoff") is True
    assert get_pending_handoff_child_terminal_ids() == ["child-handoff"]
    assert arm_handoff_continuations_for_restart() == 1
    assert get_pending_handoff_child_terminal_ids() == ["child-handoff"]

    result, duplicate = create_handoff_child_result_message("child-handoff", "child report")
    retry, retry_duplicate = create_handoff_child_result_message(
        "child-handoff", "different retry report"
    )

    assert result is not None and duplicate is False
    assert retry is not None and retry_duplicate is True
    assert retry.id == result.id
    assert get_parent_completion_barrier("parent-restart") == (1, 0)
    assert mark_child_assignment_result_delivered(result.id) is True
    assert acknowledge_child_assignment_result("parent-restart", "child-handoff") is True
    assert get_parent_completion_barrier("parent-restart") == (0, 0)
    with database.SessionLocal() as db:
        assert db.query(InboxModel).count() == 1


def test_direct_handoff_input_marker_survives_restart_arming(child_assignment_db):
    assert register_handoff_child("parent-input", "child-input") is True
    assert handoff_child_input_received("child-input") is False
    assert mark_handoff_child_input_received("child-input") is True
    assert mark_handoff_child_input_received("child-input") is True

    assert arm_handoff_continuations_for_restart() == 1
    assert handoff_child_input_received("child-input") is True


def test_legacy_handoff_snapshot_recovers_only_the_exact_admitted_relation(child_assignment_db):
    _legacy_handoff_snapshot_with_admitted_child_input("parent-legacy", "child-legacy")
    with database.SessionLocal() as db:
        state_before = (
            db.query(DelegationResultModel).count(),
            db.query(WorkflowTurnModel).count(),
            db.query(WorkflowTurnReceiptModel).count(),
        )

    assert handoff_child_input_received("child-legacy") is True
    with database.SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel).filter_by(child_terminal_id="child-legacy").one()
        )
        snapshot = (
            db.query(DelegationResultModel).filter_by(child_assignment_id=assignment.id).one()
        )
        assert assignment.handoff_input_received is False
        assert assignment.direct_result_output is None
        assert assignment.cleanup_acknowledged is False
        assert snapshot.authorship == "cao_lifecycle_snapshot"
        assert snapshot.workflow_turn_id is None
        assert (
            db.query(DelegationResultModel).count(),
            db.query(WorkflowTurnModel).count(),
            db.query(WorkflowTurnReceiptModel).count(),
        ) == state_before


def test_legacy_handoff_snapshot_without_child_receipt_is_not_recovered(child_assignment_db):
    assert register_handoff_child("parent-no-receipt", "child-no-receipt") is True
    binding = issue_workflow_input_binding("child-no-receipt")
    assert binding is not None

    assert handoff_child_input_received("child-no-receipt") is False


@pytest.mark.parametrize(
    ("field", "value"),
    [("direct_result_output", "captured result"), ("cleanup_acknowledged", True)],
)
def test_legacy_handoff_snapshot_never_recovers_direct_result_or_cleanup_state(
    child_assignment_db, field, value
):
    parent_id, child_id = f"parent-{field}", f"child-{field}"
    _legacy_handoff_snapshot_with_admitted_child_input(parent_id, child_id)
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child_id).one()
        setattr(assignment, field, value)
        db.commit()

    assert handoff_child_input_received(child_id) is False


@pytest.mark.parametrize("state", ["inactive", "stale"])
def test_legacy_handoff_snapshot_requires_one_current_active_child_binding(
    child_assignment_db, state
):
    parent_id, child_id = f"parent-{state}", f"child-{state}"
    _legacy_handoff_snapshot_with_admitted_child_input(parent_id, child_id)
    with database.SessionLocal() as db:
        child_workflow = db.query(WorkflowModel).filter_by(root_terminal_id=child_id).one()
        if state == "inactive":
            child_workflow.status = "terminal"
        else:
            stale_turn = (
                db.query(WorkflowTurnModel).filter_by(id=child_workflow.active_turn_id).one()
            )
            child_workflow.active_turn_id = None
            assert (
                db.query(WorkflowTurnReceiptModel)
                .filter_by(workflow_turn_id=stale_turn.id, receiver_terminal_id=child_id)
                .count()
                == 1
            )
        db.commit()

    assert handoff_child_input_received(child_id) is False


def test_legacy_handoff_snapshot_rejects_ambiguous_child_bindings(child_assignment_db):
    parent_id, child_id = "parent-ambiguous", "child-ambiguous"
    _legacy_handoff_snapshot_with_admitted_child_input(parent_id, child_id)
    with database.SessionLocal() as db:
        duplicate_workflow = WorkflowModel(root_terminal_id=child_id, status="open")
        db.add(duplicate_workflow)
        db.flush()
        duplicate_turn = WorkflowTurnModel(
            workflow_id=duplicate_workflow.id,
            kind="external_input",
            dedupe_key="ambiguous-bound-transport",
            state="sent",
            transport_binding="ambiguous-bound-transport",
        )
        db.add(duplicate_turn)
        db.flush()
        duplicate_workflow.active_turn_id = duplicate_turn.id
        db.add(
            WorkflowTurnReceiptModel(
                workflow_turn_id=duplicate_turn.id,
                receiver_terminal_id=child_id,
            )
        )
        db.commit()

    assert handoff_child_input_received(child_id) is False


def test_live_handoff_consumption_preserves_f11_without_inbox_wake(child_assignment_db):
    assert register_handoff_child("parent-live", "child-live") is True
    assert claim_handoff_child_result_direct("parent-live", "child-live", "final report") is True
    assert acknowledge_handoff_child_result_direct("parent-live", "child-live") == "final report"
    assert (
        get_acknowledged_handoff_child_result_direct("parent-live", "child-live") == "final report"
    )
    assert get_pending_handoff_child_terminal_ids() == []
    # Direct cleanup returns the result to the parent, but does not silently
    # consume it.  The parent acknowledgement is the only barrier release.
    assert get_parent_completion_barrier("parent-live") == (1, 0)
    assert acknowledge_child_assignment_result("parent-live", "child-live") is True
    assert get_parent_completion_barrier("parent-live") == (0, 0)
    with database.SessionLocal() as db:
        assert db.query(InboxModel).count() == 0


def test_direct_handoff_claim_retries_cleanup_without_duplicate_inbox_effect(child_assignment_db):
    register_handoff_child("parent-direct", "child-direct")

    assert (
        claim_handoff_child_result_direct("parent-direct", "child-direct", "stable report") is True
    )
    # A failed cleanup leaves this durable claim available to the same parent,
    # but it is not yet a consumed result and creates no Inbox callback.
    assert (
        claim_handoff_child_result_direct("parent-direct", "child-direct", "ignored retry") is True
    )
    assert (
        get_claimed_handoff_child_result_direct("parent-direct", "child-direct") == "stable report"
    )
    assert get_acknowledged_handoff_child_result_direct("parent-direct", "child-direct") is None
    with database.SessionLocal() as db:
        assert db.query(InboxModel).count() == 0

    assert (
        acknowledge_handoff_child_result_direct("parent-direct", "child-direct") == "stable report"
    )
    assert (
        acknowledge_handoff_child_result_direct("parent-direct", "child-direct") == "stable report"
    )
    assert get_parent_completion_barrier("parent-direct") == (1, 0)
    assert acknowledge_child_assignment_result("parent-direct", "child-direct") is True
    assert get_parent_completion_barrier("parent-direct") == (0, 0)
