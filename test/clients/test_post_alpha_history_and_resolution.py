from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.database import (
    Base,
    ChildAssignmentModel,
    HousekeepingRunModel,
    InboxModel,
    WorkflowEffectModel,
    WorkflowEffectResolutionModel,
    WorkflowModel,
    WorkflowProviderReconnectAttemptModel,
    WorkflowTurnModel,
)
from cli_agent_orchestrator.services import housekeeping_service


def _database(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'post-alpha.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(database, "_ensure_workflow_schema", lambda: None)
    return engine


def test_owner_retires_exact_indeterminate_effect_without_replay_or_success(monkeypatch, tmp_path):
    _database(monkeypatch, tmp_path)
    with database.SessionLocal() as db:
        workflow = WorkflowModel(root_terminal_id="owner", status="owner_gate")
        db.add(workflow)
        db.flush()
        turn = WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="external_input",
            dedupe_key="historical-owner-resolution",
            state="sent",
        )
        db.add(turn)
        db.flush()
        workflow.active_turn_id = turn.id
        effect = WorkflowEffectModel(
            workflow_id=workflow.id,
            workflow_turn_id=turn.id,
            effect_kind="send_message",
            effect_key="unknown-message",
            state="indeterminate",
            claim_token="sealed-claim",
        )
        db.add(effect)
        db.commit()
        ids = (effect.id, workflow.id, turn.id)

    result = database.retire_indeterminate_workflow_effect(
        ids[0],
        expected_workflow_id=ids[1],
        expected_workflow_turn_id=ids[2],
        expected_root_terminal_id="owner",
        expected_effect_kind="send_message",
    )
    assert result == {
        "retired": True,
        "already_retired": False,
        "outcome": "unknown_preserved",
        "workflow_status": "cancelled",
    }
    assert database.retire_indeterminate_workflow_effect(
        ids[0],
        expected_workflow_id=ids[1],
        expected_workflow_turn_id=ids[2],
        expected_root_terminal_id="owner",
        expected_effect_kind="send_message",
    ) == {"retired": True, "already_retired": True, "outcome": "unknown_preserved"}
    assert database.finish_workflow_effect("owner", ids[0], "sealed-claim", "completed") is False
    with database.SessionLocal() as db:
        assert db.get(WorkflowEffectModel, ids[0]).state == "indeterminate"
        assert db.get(WorkflowModel, ids[1]).status == "cancelled"
        resolution = db.query(WorkflowEffectResolutionModel).one()
        assert resolution.outcome == "unknown_preserved"


def test_superseded_owner_gate_cannot_retire_or_cancel_successor_work(monkeypatch, tmp_path):
    _database(monkeypatch, tmp_path)
    with database.SessionLocal() as db:
        gate = WorkflowModel(root_terminal_id="owner", status="owner_gate")
        db.add(gate)
        db.flush()
        gate_turn = WorkflowTurnModel(
            workflow_id=gate.id,
            kind="external_input",
            dedupe_key="historical-gate",
            state="sent",
        )
        db.add(gate_turn)
        db.flush()
        gate.active_turn_id = gate_turn.id
        effect = WorkflowEffectModel(
            workflow_id=gate.id,
            workflow_turn_id=gate_turn.id,
            effect_kind="send_message",
            effect_key="historical-unknown",
            state="indeterminate",
            claim_token="historical-claim",
        )
        successor = WorkflowModel(
            root_terminal_id="owner",
            status="open",
            resumed_from_owner_gate_workflow_id=gate.id,
        )
        db.add_all([effect, successor])
        db.flush()
        successor_turn = WorkflowTurnModel(
            workflow_id=successor.id,
            kind="external_input",
            dedupe_key="successor-input",
            state="claimed",
        )
        db.add(successor_turn)
        db.flush()
        successor.active_turn_id = successor_turn.id
        assignment = ChildAssignmentModel(
            parent_terminal_id="owner",
            child_terminal_id="successor-child",
            status="awaiting_result",
            attempt_id="successor-attempt",
            request_workflow_id=successor.id,
            request_workflow_turn_id=successor_turn.id,
        )
        reconnect = WorkflowProviderReconnectAttemptModel(
            workflow_id=successor.id,
            workflow_turn_id=successor_turn.id,
            root_terminal_id="owner",
            attempt_number=1,
            attempt_token="successor-reconnect",
            state="reserved",
        )
        db.add_all([assignment, reconnect])
        db.commit()
        ids = (effect.id, gate.id, gate_turn.id, successor.id, assignment.id, reconnect.id)

    assert database.retire_indeterminate_workflow_effect(
        ids[0],
        expected_workflow_id=ids[1],
        expected_workflow_turn_id=ids[2],
        expected_root_terminal_id="owner",
        expected_effect_kind="send_message",
    ) == {"retired": False, "reason_code": "OWNER_RESOLUTION_NOT_ELIGIBLE"}
    with database.SessionLocal() as db:
        assert db.get(WorkflowModel, ids[1]).status == "owner_gate"
        assert db.get(WorkflowModel, ids[3]).status == "open"
        assert db.get(ChildAssignmentModel, ids[4]).status == "awaiting_result"
        assert db.get(WorkflowProviderReconnectAttemptModel, ids[5]).state == "reserved"
        assert db.query(WorkflowEffectResolutionModel).count() == 0


def test_exact_retirement_fails_only_target_inbox_and_assignment_across_restart(
    monkeypatch, tmp_path
):
    engine = _database(monkeypatch, tmp_path)
    with database.SessionLocal() as db:
        target_message = InboxModel(
            sender_id="operator",
            receiver_id="owner",
            message="target",
            status="pending",
        )
        other_message = InboxModel(
            sender_id="operator",
            receiver_id="owner",
            message="other",
            status="pending",
        )
        target = WorkflowModel(root_terminal_id="owner", status="owner_gate")
        other = WorkflowModel(root_terminal_id="owner", status="terminal")
        db.add_all([target_message, other_message, target, other])
        db.flush()
        target_turn = WorkflowTurnModel(
            workflow_id=target.id,
            kind="external_input",
            dedupe_key="target-input",
            state="sent",
            inbox_message_id=target_message.id,
        )
        other_turn = WorkflowTurnModel(
            workflow_id=other.id,
            kind="external_input",
            dedupe_key="other-input",
            state="sent",
            inbox_message_id=other_message.id,
        )
        db.add_all([target_turn, other_turn])
        db.flush()
        target.active_turn_id = target_turn.id
        other.active_turn_id = other_turn.id
        effect = WorkflowEffectModel(
            workflow_id=target.id,
            workflow_turn_id=target_turn.id,
            effect_kind="send_message",
            effect_key="target-unknown",
            state="indeterminate",
            claim_token="target-claim",
        )
        target_assignment = ChildAssignmentModel(
            parent_terminal_id="owner",
            child_terminal_id="target-child",
            status="awaiting_result",
            attempt_id="target-attempt",
            request_workflow_id=target.id,
            request_workflow_turn_id=target_turn.id,
        )
        other_assignment = ChildAssignmentModel(
            parent_terminal_id="owner",
            child_terminal_id="other-child",
            status="awaiting_result",
            attempt_id="other-attempt",
            request_workflow_id=other.id,
            request_workflow_turn_id=other_turn.id,
        )
        db.add_all([effect, target_assignment, other_assignment])
        db.commit()
        ids = (
            effect.id,
            target.id,
            target_turn.id,
            target_message.id,
            other_message.id,
            target_assignment.id,
            other_assignment.id,
        )

    expected = {
        "retired": True,
        "already_retired": False,
        "outcome": "unknown_preserved",
        "workflow_status": "cancelled",
    }
    assert (
        database.retire_indeterminate_workflow_effect(
            ids[0],
            expected_workflow_id=ids[1],
            expected_workflow_turn_id=ids[2],
            expected_root_terminal_id="owner",
            expected_effect_kind="send_message",
        )
        == expected
    )

    engine.dispose()
    restarted_engine = create_engine(str(engine.url))
    monkeypatch.setattr(database, "engine", restarted_engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=restarted_engine))
    assert database.retire_indeterminate_workflow_effect(
        ids[0],
        expected_workflow_id=ids[1],
        expected_workflow_turn_id=ids[2],
        expected_root_terminal_id="owner",
        expected_effect_kind="send_message",
    ) == {"retired": True, "already_retired": True, "outcome": "unknown_preserved"}
    with database.SessionLocal() as db:
        assert db.get(InboxModel, ids[3]).status == "failed"
        assert db.get(InboxModel, ids[4]).status == "pending"
        assert db.get(ChildAssignmentModel, ids[5]).status == "cancelled"
        assert db.get(ChildAssignmentModel, ids[6]).status == "awaiting_result"
        assert db.query(WorkflowEffectResolutionModel).count() == 1


def test_housekeeping_history_is_bounded_paginated_and_keeps_failed_truth(monkeypatch, tmp_path):
    _database(monkeypatch, tmp_path)
    stamp = datetime(2026, 9, 18, tzinfo=timezone.utc).isoformat()
    for index in range(55):
        database.record_housekeeping_run(
            {
                "ok": index != 54,
                "dry_run": False,
                "mode": "frequent",
                "started_at": stamp,
                "completed_at": stamp,
                "duration_seconds": index / 10,
                "final_status": "failed" if index == 54 else "completed",
                "freed_bytes": index,
                "warnings": ["x" * 5000] * 150,
                "execution_failures": ([{"reason_code": "EXACT_FAILURE"}] if index == 54 else []),
            }
        )
    first = database.list_housekeeping_runs(limit=20)
    second = database.list_housekeeping_runs(limit=20, before_id=first["next_before_id"])
    assert len(first["items"]) == 20
    assert len(second["items"]) == 20
    assert first["items"][0]["outcome"] == "failed"
    assert first["items"][0]["report"]["execution_failures"][0]["reason_code"] == "EXACT_FAILURE"
    assert set(item["id"] for item in first["items"]).isdisjoint(
        item["id"] for item in second["items"]
    )
    with database.SessionLocal() as db:
        assert db.query(HousekeepingRunModel).count() == 50


def test_housekeeping_history_derives_missing_start_from_completion_and_duration(
    monkeypatch, tmp_path
):
    _database(monkeypatch, tmp_path)
    completed_at = "2026-09-18T00:00:03+00:00"
    database.record_housekeeping_run(
        {
            "ok": True,
            "dry_run": False,
            "mode": "full",
            "started_at": None,
            "completed_at": completed_at,
            "duration_seconds": 2.5,
            "final_status": "completed",
            "freed_bytes": 1,
        }
    )

    item = database.list_housekeeping_runs(limit=1)["items"][0]
    assert item["started_at"] == "2026-09-18T00:00:00.500000"
    assert item["completed_at"] == "2026-09-18T00:00:03"
    assert item["duration_seconds"] == 2.5
    assert item["report"]["started_at"] is None


def test_housekeeping_records_each_real_run_once_and_never_records_preview(monkeypatch):
    records = []
    report = {
        "ok": True,
        "dry_run": False,
        "mode": "frequent",
        "started_at": "2026-09-18T00:00:00+00:00",
        "completed_at": "2026-09-18T00:00:01+00:00",
        "duration_seconds": 1.0,
        "final_status": "completed",
        "freed_bytes": 1,
    }
    monkeypatch.setattr(database, "record_housekeeping_run", records.append)
    monkeypatch.setattr(
        housekeeping_service,
        "_run_housekeeping_impl",
        lambda **_kwargs: SimpleNamespace(as_dict=lambda: report),
    )

    housekeeping_service.run_housekeeping(dry_run=False, mode="frequent")
    assert records == [report]

    housekeeping_service.run_housekeeping(dry_run=True, mode="frequent")
    assert records == [report]

    def fail(**_kwargs):
        raise RuntimeError("EXACT_FAILURE")

    monkeypatch.setattr(housekeeping_service, "_run_housekeeping_impl", fail)
    with pytest.raises(RuntimeError, match="EXACT_FAILURE"):
        housekeeping_service.run_housekeeping(dry_run=False, mode="weekly")
    assert len(records) == 2
    assert records[-1]["final_status"] == "failed"
    assert records[-1]["execution_failures"] == [{"reason_code": "EXACT_FAILURE"}]
