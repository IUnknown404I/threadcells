from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.database import (
    Base,
    HousekeepingRunModel,
    WorkflowEffectModel,
    WorkflowEffectResolutionModel,
    WorkflowModel,
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
