"""Durable terminal lifecycle read-model coverage."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import cli_agent_orchestrator.clients.database as database
from cli_agent_orchestrator.clients.database import (
    Base,
    ChildAssignmentModel,
    DelegationResultModel,
    ensure_open_workflow,
    get_terminal_workflow_projection,
    register_handoff_child,
    set_workflow_terminal_state,
)
from cli_agent_orchestrator.models.inbox import ChildAssignmentStatus
from cli_agent_orchestrator.models.result import DelegationResultStatus


def _isolated_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(database, "_child_assignment_schema_ready", True)
    monkeypatch.setattr(database, "_delegation_result_schema_ready", True)


def _handoff(monkeypatch):
    _isolated_db(monkeypatch)
    assert register_handoff_child("parent", "child")


def _set_relation(status, result_status):
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id="child").one()
        result = db.query(DelegationResultModel).filter_by(child_assignment_id=assignment.id).one()
        assignment.status = status
        result.status = result_status
        db.commit()


def test_projection_prioritizes_durable_handoff_relation_for_parent_and_child(monkeypatch):
    _handoff(monkeypatch)

    assert get_terminal_workflow_projection("child")["state"] == "waiting"
    assert get_terminal_workflow_projection("parent")["state"] == "waiting"

    _set_relation(
        ChildAssignmentStatus.HANDOFF_RECOVERY_AWAITING_RESULT.value,
        DelegationResultStatus.AWAITING.value,
    )
    assert get_terminal_workflow_projection("child")["state"] == "recoverable"
    assert get_terminal_workflow_projection("parent")["state"] == "recoverable"

    _set_relation(
        ChildAssignmentStatus.HANDOFF_RESULT_QUEUED.value,
        DelegationResultStatus.COMPLETE.value,
    )
    assert get_terminal_workflow_projection("parent")["state"] == "result_ready"

    _set_relation(
        ChildAssignmentStatus.HANDOFF_RESULT_DELIVERED.value,
        DelegationResultStatus.COMPLETE.value,
    )
    # Provider Ready is intentionally not an input to this read model.
    child = get_terminal_workflow_projection("child")
    parent = get_terminal_workflow_projection("parent")
    assert child["state"] == parent["state"] == "result_ready"
    assert parent["delivery_status"] == "handoff_result_delivered"
    assert parent["result_status"] == "complete"


def test_projection_returns_terminal_workflow_authority_over_provider_or_relation(monkeypatch):
    _isolated_db(monkeypatch)
    assert ensure_open_workflow("owner") is not None
    assert set_workflow_terminal_state("owner", "owner_gate")
    owner = get_terminal_workflow_projection("owner")
    assert owner["state"] == "owner_gate"
    assert owner["workflow_status"] == "owner_gate"

    assert ensure_open_workflow("completed") is not None
    assert set_workflow_terminal_state("completed", "terminal")
    assert get_terminal_workflow_projection("completed")["state"] == "completed"


def test_projection_reports_real_incomplete_failed_and_cancelled_durable_states(monkeypatch):
    _handoff(monkeypatch)
    _set_relation(
        ChildAssignmentStatus.HANDOFF_RESULT_FAILED.value,
        DelegationResultStatus.INCOMPLETE.value,
    )
    assert get_terminal_workflow_projection("child")["state"] == "incomplete"

    _set_relation(
        ChildAssignmentStatus.HANDOFF_RESULT_FAILED.value,
        DelegationResultStatus.COMPLETE.value,
    )
    assert get_terminal_workflow_projection("child")["state"] == "failed"

    _set_relation(
        ChildAssignmentStatus.CANCELLED.value,
        DelegationResultStatus.CANCELLED.value,
    )
    assert get_terminal_workflow_projection("child")["state"] == "cancelled"


def test_projection_returns_active_after_acknowledged_result_is_incorporated(monkeypatch):
    _handoff(monkeypatch)
    _set_relation(
        ChildAssignmentStatus.HANDOFF_RESULT_ACKNOWLEDGED.value,
        DelegationResultStatus.COMPLETE.value,
    )
    projection = get_terminal_workflow_projection("parent")
    assert projection["state"] == "active"
    assert projection["delivery_status"] == "handoff_result_acknowledged"
