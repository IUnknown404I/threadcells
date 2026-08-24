"""Durable missing-root workflow authority reconciliation."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.database import (
    Base,
    ChildAssignmentModel,
    ProviderExecutionLeaseModel,
    TerminalModel,
    WorkflowModel,
    WorkflowTurnModel,
    WorktreeWriterLeaseModel,
)


@pytest.fixture
def workflow_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", session)
    monkeypatch.setattr(database, "_ensure_workflow_schema", lambda: None)
    return session


def test_orphan_authority_reconciliation_cancels_edges_and_preserves_history(workflow_db):
    root = "missing-root"
    with workflow_db() as db:
        first = WorkflowModel(root_terminal_id=root, status="open")
        second = WorkflowModel(root_terminal_id=root, status="owner_gate")
        db.add_all([first, second])
        db.flush()
        turn = WorkflowTurnModel(
            workflow_id=first.id,
            kind="external_input",
            dedupe_key="owner-input",
            state="claimed",
            claim_generation=2,
            claim_token="claim",
            claim_expires_at=datetime.now() + timedelta(minutes=1),
        )
        db.add(turn)
        db.flush()
        turn_id = int(turn.id)
        first.active_turn_id = turn.id
        db.add_all(
            [
                ProviderExecutionLeaseModel(terminal_id=root, workflow_turn_id=turn_id),
                WorktreeWriterLeaseModel(
                    canonical_worktree="/tmp/missing-root-worktree",
                    terminal_id=root,
                ),
                ChildAssignmentModel(
                    parent_terminal_id=root,
                    child_terminal_id="child-of-missing-root",
                    status="awaiting_result",
                ),
            ]
        )
        db.commit()

    inventory = database.list_orphaned_protected_workflow_authorities()
    assert len(inventory) == 1
    expected = [item["id"] for item in inventory[0]["workflows"]]
    fingerprint = database._workflow_authority_snapshot_fingerprint(inventory[0])

    result = database.reconcile_orphaned_protected_workflow_authority(
        root, expected, fingerprint, "/tmp/missing-root-worktree", []
    )
    replay = database.reconcile_orphaned_protected_workflow_authority(
        root, expected, fingerprint, "/tmp/missing-root-worktree", []
    )

    assert result == {
        "reconciled": 2,
        "already_reconciled": False,
        "reason": "root_terminal_absent",
    }
    assert replay == {
        "reconciled": 0,
        "already_reconciled": True,
        "reason": "authority_already_terminal",
    }
    assert database.list_orphaned_protected_workflow_authorities() == []
    with workflow_db() as db:
        assert {row.status for row in db.query(WorkflowModel).all()} == {"cancelled"}
        stored_turn = db.get(WorkflowTurnModel, turn_id)
        assert stored_turn.state == "cancelled"
        assert stored_turn.claim_token is None
        assert stored_turn.claim_expires_at is None
        assignment = db.query(ChildAssignmentModel).one()
        assert assignment.status == "cancelled"
        assert db.get(ProviderExecutionLeaseModel, root) is None
        assert db.query(WorktreeWriterLeaseModel).count() == 0


def test_existing_terminal_prevents_orphan_reconciliation(workflow_db):
    root = "live-root"
    with workflow_db() as db:
        db.add(
            TerminalModel(
                id=root,
                tmux_session="cao-live",
                tmux_window="root",
                provider="codex",
                runtime_lifecycle="running",
            )
        )
        workflow = WorkflowModel(root_terminal_id=root, status="owner_gate")
        db.add(workflow)
        db.commit()
        workflow_id = int(workflow.id)

    assert database.list_orphaned_protected_workflow_authorities() == []
    assert database.reconcile_orphaned_protected_workflow_authority(
        root, [workflow_id], "expected-fingerprint", "", []
    ) == {
        "reconciled": 0,
        "already_reconciled": False,
        "reason": "terminal_exists",
    }
    with workflow_db() as db:
        assert db.get(WorkflowModel, workflow_id).status == "owner_gate"


def test_orphan_reconciliation_rejects_changed_authority_snapshot(workflow_db):
    root = "racing-root"
    with workflow_db() as db:
        workflow = WorkflowModel(root_terminal_id=root, status="open")
        db.add(workflow)
        db.flush()
        turn = WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="external_input",
            dedupe_key="racing-owner-input",
            state="queued",
            claim_generation=0,
        )
        db.add(turn)
        db.commit()
        workflow_id = int(workflow.id)
        turn_id = int(turn.id)

    inventory = database.list_orphaned_protected_workflow_authorities()
    fingerprint = database._workflow_authority_snapshot_fingerprint(inventory[0])
    with workflow_db() as db:
        stored_turn = db.get(WorkflowTurnModel, turn_id)
        stored_turn.state = "claimed"
        stored_turn.claim_generation = 1
        stored_turn.claim_token = "new-claim"
        db.commit()

    assert database.reconcile_orphaned_protected_workflow_authority(
        root, [workflow_id], fingerprint, "", []
    ) == {
        "reconciled": 0,
        "already_reconciled": False,
        "reason": "state_changed",
    }
    with workflow_db() as db:
        assert db.get(WorkflowModel, workflow_id).status == "open"
        assert db.get(WorkflowTurnModel, turn_id).state == "claimed"


def test_terminal_close_race_releases_the_exact_planned_writer_lease(workflow_db):
    root = "closing-root"
    worktree = "/tmp/closing-root-worktree"
    with workflow_db() as db:
        workflow = WorkflowModel(root_terminal_id=root, status="open")
        db.add(workflow)
        db.flush()
        db.add(
            WorktreeWriterLeaseModel(
                canonical_worktree=worktree,
                terminal_id=root,
            )
        )
        db.commit()
        workflow_id = int(workflow.id)

    inventory = database.list_orphaned_protected_workflow_authorities()
    fingerprint = database._workflow_authority_snapshot_fingerprint(inventory[0])
    assert database.set_workflow_terminal_state(root, "cancelled") is True

    assert database.reconcile_orphaned_protected_workflow_authority(
        root,
        [workflow_id],
        fingerprint,
        worktree,
        [],
    ) == {
        "reconciled": 0,
        "already_reconciled": True,
        "reason": "authority_already_terminal",
    }
    with workflow_db() as db:
        assert db.query(WorktreeWriterLeaseModel).count() == 0
    assert database.list_orphaned_protected_workflow_authorities() == []


def test_terminal_close_race_preserves_planned_direct_handoff_claim(workflow_db):
    root = "direct-claim-root"
    worktree = "/tmp/direct-claim-root-worktree"
    with workflow_db() as db:
        workflow = WorkflowModel(root_terminal_id=root, status="open")
        assignment = ChildAssignmentModel(
            parent_terminal_id=root,
            child_terminal_id="direct-claim-child",
            status="handoff_direct_result_claimed",
        )
        db.add_all(
            [
                workflow,
                assignment,
                WorktreeWriterLeaseModel(
                    canonical_worktree=worktree,
                    terminal_id=root,
                ),
            ]
        )
        db.commit()
        workflow_id = int(workflow.id)
        assignment_id = int(assignment.id)

    inventory = database.list_orphaned_protected_workflow_authorities()
    fingerprint = database._workflow_authority_snapshot_fingerprint(inventory[0])
    assert database.set_workflow_terminal_state(root, "cancelled") is True

    assert (
        database.reconcile_orphaned_protected_workflow_authority(
            root,
            [workflow_id],
            fingerprint,
            worktree,
            [assignment_id],
        )["already_reconciled"]
        is True
    )
    with workflow_db() as db:
        assert db.query(WorktreeWriterLeaseModel).count() == 0
        assert db.get(ChildAssignmentModel, assignment_id).status == (
            "handoff_direct_result_claimed"
        )


def test_terminal_deletion_cancels_owner_gated_workflow_atomically(workflow_db):
    root = "deleted-root"
    with workflow_db() as db:
        db.add(
            TerminalModel(
                id=root,
                tmux_session="cao-delete",
                tmux_window="root",
                provider="codex",
                runtime_lifecycle="exited",
            )
        )
        workflow = WorkflowModel(root_terminal_id=root, status="owner_gate")
        db.add(workflow)
        db.commit()
        workflow_id = int(workflow.id)

    assert database.delete_terminal(root) is True
    with workflow_db() as db:
        assert db.get(TerminalModel, root) is None
        assert db.get(WorkflowModel, workflow_id).status == "cancelled"


def test_legacy_terminal_authority_retirement_cancels_protected_workflow(workflow_db):
    root = "legacy-deleted-root"
    with workflow_db() as db:
        db.add(
            TerminalModel(
                id=root,
                tmux_session="cao-legacy-delete",
                tmux_window="root",
                provider="codex",
                runtime_lifecycle="exited",
                launch_worktree=None,
                write_enabled=None,
                context_role=None,
            )
        )
        workflow = WorkflowModel(root_terminal_id=root, status="open")
        db.add(workflow)
        db.commit()
        workflow_id = int(workflow.id)

    assert database.retire_unreconciled_terminal_authority(root) is True
    with workflow_db() as db:
        assert db.get(TerminalModel, root) is None
        assert db.get(WorkflowModel, workflow_id).status == "cancelled"
