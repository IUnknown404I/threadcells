import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.database import (
    Base,
    ChildAssignmentModel,
    DelegationResultModel,
    TerminalModel,
    WorkflowEffectModel,
    WorkflowEffectResolutionModel,
    WorkflowModel,
    WorkflowTurnModel,
    WorkflowTurnReceiptModel,
)
from cli_agent_orchestrator.services import interaction_read_model_service


def _install_database(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'effects.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(database, "_terminal_ui_projection_schema_ready", False)
    monkeypatch.setattr(database, "_terminal_ui_projection_schema_engine_identity", None)
    for name in (
        "_ensure_workflow_schema",
        "_ensure_child_assignment_schema",
        "_ensure_delegation_result_schema",
        "_ensure_terminal_ui_projection_schema",
    ):
        monkeypatch.setattr(database, name, lambda: None)
    return engine


def _terminal(terminal_id: str, session_id: str = "direct-implementation") -> TerminalModel:
    return TerminalModel(
        id=terminal_id,
        tmux_session=f"cao-{session_id}",
        session_id=session_id,
        tmux_window=terminal_id,
        provider="codex",
        runtime_lifecycle="running",
        last_active=datetime(2026, 9, 13, 1, 0, 0),
    )


def _effect_key(kind: str, value: str) -> str:
    return f"{kind}:{hashlib.sha256(value.encode()).hexdigest()}"


def test_terminal_replay_resolves_exact_four_effects_and_preserves_owner_gate(
    monkeypatch, tmp_path
):
    _install_database(monkeypatch, tmp_path)
    now = datetime(2026, 9, 13, 1, 0, 0)
    with database.SessionLocal() as db:
        db.add_all([_terminal("owner-5076a6ad"), _terminal("alpha-owner")])
        closed = WorkflowModel(
            root_terminal_id="owner-5076a6ad",
            status="terminal",
            terminal_reason="fence managed attempt",
            created_at=now,
            updated_at=now,
        )
        owner_gate = WorkflowModel(
            root_terminal_id="alpha-owner",
            status="owner_gate",
            terminal_reason="4 Alpha flow owner decision",
            created_at=now,
            updated_at=now,
        )
        db.add_all([closed, owner_gate])
        db.flush()
        turns = []
        for index in range(4):
            turn = WorkflowTurnModel(
                workflow_id=closed.id,
                kind="execution_resume" if index else "external_input",
                dedupe_key=f"managed-attempt-{index}",
                payload="top-level direct implementation",
                state="finished",
                resume_parent_turn_id=turns[-1].id if turns else None,
                created_at=now + timedelta(seconds=index),
                updated_at=now + timedelta(seconds=index),
            )
            db.add(turn)
            db.flush()
            turns.append(turn)
        closed.active_turn_id = turns[-1].id
        gate_turn = WorkflowTurnModel(
            workflow_id=owner_gate.id,
            kind="external_input",
            dedupe_key="alpha-owner-gate",
            payload="4 Alpha flow",
            state="sent",
            created_at=now,
            updated_at=now,
        )
        db.add(gate_turn)
        db.flush()
        owner_gate.active_turn_id = gate_turn.id
        for index, turn in enumerate(turns):
            db.add(
                WorkflowTurnReceiptModel(
                    workflow_turn_id=turn.id,
                    receiver_terminal_id="owner-5076a6ad",
                    resumed_by_turn_id=turns[index + 1].id if index < 3 else None,
                    resumed_at=now + timedelta(seconds=index + 1) if index < 3 else None,
                    consumed_at=now + timedelta(seconds=index),
                )
            )
            db.add(
                WorkflowEffectModel(
                    workflow_id=closed.id,
                    workflow_turn_id=turn.id,
                    effect_kind="complete_workflow",
                    effect_key=_effect_key("complete_workflow", "fence managed attempt"),
                    state="claimed" if index == 3 else "indeterminate",
                    claim_token=f"claim-{index}",
                    created_at=now + timedelta(seconds=index),
                    updated_at=now + timedelta(seconds=index),
                )
            )
        final_turn_id = turns[-1].id
        db.commit()

    assert database.reconcile_workflow_effect_resolutions() == 4
    assert database.reconcile_workflow_effect_resolutions() == 0

    with database.SessionLocal() as db:
        effects = db.query(WorkflowEffectModel).order_by(WorkflowEffectModel.id).all()
        resolutions = (
            db.query(WorkflowEffectResolutionModel)
            .order_by(WorkflowEffectResolutionModel.workflow_effect_id)
            .all()
        )
        assert [effect.state for effect in effects] == [
            "indeterminate",
            "indeterminate",
            "indeterminate",
            "claimed",
        ]
        assert [row.reason_code for row in resolutions] == [
            "EFFECT_COMPLETED_BY_REPLAY",
            "EFFECT_COMPLETED_BY_REPLAY",
            "EFFECT_COMPLETED_BY_REPLAY",
            "WORKFLOW_TERMINALIZED",
        ]
        assert all(row.evidence_workflow_turn_id == final_turn_id for row in resolutions)

    current = interaction_read_model_service.list_interactions(
        "direct-implementation", mode="current", limit=20
    )
    assert not any(item["interaction_type"] == "effect" for item in current["items"])
    assert any(
        item["workflow"]["status"] == "owner_gate" and item["input_preview"] == "4 Alpha flow"
        for item in current["items"]
    )
    history = interaction_read_model_service.list_interactions(
        "direct-implementation", mode="history", limit=20
    )
    resolved = [item for item in history["items"] if item["interaction_type"] == "effect"]
    assert len(resolved) == 4
    assert {item["final_disposition"] for item in resolved} == {"completed"}
    assert {item["workflow"]["effect_state"] for item in resolved} == {
        "claimed",
        "indeterminate",
    }


def test_uncertain_and_cross_workflow_effects_remain_current(monkeypatch, tmp_path):
    _install_database(monkeypatch, tmp_path)
    now = datetime(2026, 9, 13, 1, 10, 0)
    with database.SessionLocal() as db:
        db.add_all([_terminal("owner-a", "session-a"), _terminal("owner-b", "session-b")])
        workflows = []
        for owner in ("owner-a", "owner-b"):
            workflow = WorkflowModel(
                root_terminal_id=owner,
                status="terminal",
                terminal_reason="different reason",
                created_at=now,
                updated_at=now,
            )
            db.add(workflow)
            db.flush()
            turn = WorkflowTurnModel(
                workflow_id=workflow.id,
                kind="external_input",
                dedupe_key=owner,
                payload=owner,
                state="finished",
                created_at=now,
                updated_at=now,
            )
            db.add(turn)
            db.flush()
            workflow.active_turn_id = turn.id
            db.add(
                WorkflowTurnReceiptModel(
                    workflow_turn_id=turn.id,
                    receiver_terminal_id=owner,
                    consumed_at=now,
                )
            )
            db.add(
                WorkflowEffectModel(
                    workflow_id=workflow.id,
                    workflow_turn_id=turn.id,
                    effect_kind="complete_workflow",
                    effect_key=_effect_key("complete_workflow", "not the terminal reason"),
                    state="indeterminate",
                    claim_token=owner,
                    created_at=now,
                    updated_at=now,
                )
            )
            workflows.append(workflow)
        db.commit()

    assert database.reconcile_workflow_effect_resolutions() == 0
    assert (
        interaction_read_model_service.list_interactions("session-a", mode="current")["total"] == 1
    )
    assert (
        interaction_read_model_service.list_interactions("session-b", mode="current")["total"] == 1
    )


def test_superseded_review_acknowledgement_is_rejected_once_under_concurrency(
    monkeypatch, tmp_path
):
    _install_database(monkeypatch, tmp_path)
    now = datetime(2026, 9, 13, 1, 20, 0)
    with database.SessionLocal() as db:
        db.add(_terminal("review-owner", "review-session"))
        workflow = WorkflowModel(
            root_terminal_id="review-owner", status="open", created_at=now, updated_at=now
        )
        db.add(workflow)
        db.flush()
        old_turn = WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="assigned_result",
            dedupe_key="old-review",
            payload="review result",
            state="finished",
            superseded_by_turn_id=999,
            superseded_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(old_turn)
        db.flush()
        db.add(
            WorkflowTurnReceiptModel(
                workflow_turn_id=old_turn.id,
                receiver_terminal_id="review-owner",
                consumed_at=now,
            )
        )
        assignment = ChildAssignmentModel(
            parent_terminal_id="review-owner",
            child_terminal_id="reviewer",
            status="result_superseded",
            request_workflow_id=workflow.id,
            review_subject_kind="git_commit",
            review_superseded_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(assignment)
        db.flush()
        result = DelegationResultModel(
            id="superseded-result",
            child_assignment_id=assignment.id,
            delegation_kind="assign",
            parent_terminal_id="review-owner",
            child_terminal_id="reviewer",
            authorship="child_submission",
            status="complete",
            finalized_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(result)
        db.add(
            WorkflowEffectModel(
                workflow_id=workflow.id,
                workflow_turn_id=old_turn.id,
                effect_kind="acknowledge_assignment",
                effect_key=_effect_key("acknowledge_assignment", result.id),
                state="indeterminate",
                claim_token="review-ack",
                created_at=now,
                updated_at=now,
            )
        )
        assignment_id = assignment.id
        result_id = result.id
        db.commit()

    with ThreadPoolExecutor(max_workers=2) as executor:
        counts = list(
            executor.map(lambda _index: database.reconcile_workflow_effect_resolutions(), range(2))
        )
    assert sorted(counts) == [0, 1]
    with database.SessionLocal() as db:
        resolution = db.query(WorkflowEffectResolutionModel).one()
        assert resolution.outcome == "rejected"
        assert resolution.reason_code == "RESULT_REVIEW_ATTEMPT_SUPERSEDED"
        assert resolution.evidence_assignment_id == assignment_id
        assert resolution.evidence_result_id == result_id
    history = interaction_read_model_service.list_interactions("review-session", mode="history")
    effect_item = next(item for item in history["items"] if item["interaction_type"] == "effect")
    assert effect_item["final_disposition"] == "rejected"
    assert effect_item["workflow"]["effect_reason_code"] == "RESULT_REVIEW_ATTEMPT_SUPERSEDED"
