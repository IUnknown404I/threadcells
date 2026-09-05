"""Issue #81: immutable review-attempt and exact Git-revision authority."""

import asyncio
import hashlib
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import cli_agent_orchestrator.clients.database as database
import cli_agent_orchestrator.mcp_server.server as mcp_server
from cli_agent_orchestrator.clients.database import (
    Base,
    ChildAssignmentModel,
    TerminalModel,
    WorkflowModel,
    acknowledge_child_assignment_result_outcome,
    activate_workflow_turn_for_inbox,
    bind_child_assignment_input_turn,
    cancel_child_assignment_attempt,
    claim_workflow_effect,
    claim_workflow_turn_receipt,
    create_child_assignment_result_message,
    finish_workflow_effect,
    get_delegation_result,
    get_parent_completion_barrier,
    issue_workflow_input_binding,
    list_completed_assigned_child_retirement_candidates,
    list_delegation_results,
    mark_child_assignment_result_delivered,
    mark_workflow_turn_sent_for_inbox,
    register_child_assignment,
    requeue_unacknowledged_child_assignment_results,
    resolve_workflow_input_binding,
    set_workflow_terminal_state,
    start_workflow_input,
)


@pytest.fixture
def authority_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(database, "_child_assignment_schema_ready", True)
    monkeypatch.setattr(database, "_delegation_result_schema_ready", True)


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "subject"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Review Authority Test")
    _git(repo, "config", "user.email", "review-authority@example.invalid")
    (repo / "subject.txt").write_text("revision A\n")
    _git(repo, "add", "subject.txt")
    _git(repo, "commit", "-qm", "revision A")
    return repo, _git(repo, "rev-parse", "HEAD")


def _advance(repo: Path, label: str) -> str:
    (repo / "subject.txt").write_text(f"revision {label}\n")
    _git(repo, "add", "subject.txt")
    _git(repo, "commit", "-qm", f"revision {label}")
    return _git(repo, "rev-parse", "HEAD")


def _reviewer(
    child_id: str,
    repo: Path,
    revision: str,
    *,
    launch_worktree: Path | None = None,
) -> TerminalModel:
    return TerminalModel(
        id=child_id,
        tmux_session="review-session",
        tmux_window=child_id,
        provider="codex",
        agent_profile="reviewer_sol_high",
        launch_worktree=str(launch_worktree or repo),
        managed_worktree_kind="reviewer",
        managed_worktree_source=str(repo),
        managed_worktree_commit=revision,
        runtime_lifecycle="running",
    )


def _start_review(
    parent_id: str,
    child_id: str,
    request: str,
    *,
    requested_revision: str | None = None,
) -> dict:
    turn_id = start_workflow_input(parent_id)
    assert turn_id is not None
    assert claim_workflow_turn_receipt(parent_id, turn_id)
    effect = claim_workflow_effect(parent_id, turn_id, "assign", request)
    assert effect is not None
    assert register_child_assignment(
        parent_id,
        child_id,
        workflow_turn_id=turn_id,
        workflow_effect_id=effect["id"],
        request_message=request,
        requested_review_revision=requested_revision,
    )
    assert finish_workflow_effect(parent_id, effect["id"], effect["claim_token"], "completed")
    binding = issue_workflow_input_binding(child_id)
    assert binding is not None
    assert bind_child_assignment_input_turn(child_id, binding)
    child_turn_id = resolve_workflow_input_binding(child_id, binding)
    assert child_turn_id is not None
    return {
        "turn_id": turn_id,
        "effect_id": effect["id"],
        "child_turn_id": child_turn_id,
    }


def _submit_result(parent_id: str, child_id: str, body: str) -> dict:
    with database.SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel)
            .filter_by(child_terminal_id=child_id)
            .order_by(ChildAssignmentModel.id.desc())
            .first()
        )
        turn_id = assignment.child_workflow_turn_id if assignment is not None else None
    if turn_id is None:
        turn_id = start_workflow_input(child_id)
        assert turn_id is not None
    assert claim_workflow_turn_receipt(child_id, turn_id)
    effect = claim_workflow_effect(child_id, turn_id, "send_message", body)
    assert effect is not None
    notice, duplicate = create_child_assignment_result_message(
        child_id,
        parent_id,
        body,
        workflow_effect_id=effect["id"],
        workflow_turn_id=turn_id,
    )
    assert notice is not None and duplicate is False and notice.result_id
    assert finish_workflow_effect(child_id, effect["id"], effect["claim_token"], "completed")
    assert mark_child_assignment_result_delivered(notice.id)
    return {"notice_id": notice.id, "result_id": notice.result_id}


def test_pass_becomes_stale_when_same_branch_moves(authority_db, tmp_path):
    repo, revision_a = _repository(tmp_path)
    with database.SessionLocal() as db:
        db.add(_reviewer("reviewer", repo, revision_a))
        db.commit()

    request = _start_review("parent", "reviewer", "Review exact revision A")
    result = _submit_result("parent", "reviewer", "PASS for revision A")
    artifact = get_delegation_result(result["result_id"])
    assert artifact["review"]["revision"] == revision_a
    assert artifact["review"]["request_workflow_turn_id"] == request["turn_id"]
    assert artifact["review"]["request_workflow_effect_id"] == request["effect_id"]

    revision_b = _advance(repo, "B")
    assert revision_b != revision_a
    stale_artifact = get_delegation_result(result["result_id"])
    assert stale_artifact["review"]["authority_state"] == "stale_revision"
    assert stale_artifact["review"]["current_authority"] is False
    assert "worktree" not in stale_artifact["review"]
    rejected = acknowledge_child_assignment_result_outcome("parent", result_id=result["result_id"])
    assert rejected["accepted"] is False
    assert rejected["reason_code"] == "RESULT_REVIEW_REVISION_STALE"


def test_correction_and_rereview_preserve_history_but_only_b_is_current(authority_db, tmp_path):
    repo, revision_a = _repository(tmp_path)
    with database.SessionLocal() as db:
        db.add(_reviewer("reviewer", repo, revision_a))
        db.commit()
    _start_review("parent", "reviewer", "V1 review revision A")
    result_a = _submit_result("parent", "reviewer", "BLOCK revision A")

    revision_b = _advance(repo, "B")
    with database.SessionLocal() as db:
        db.query(TerminalModel).filter_by(id="reviewer").update(
            {TerminalModel.managed_worktree_commit: revision_b}
        )
        db.commit()
    _start_review("parent", "reviewer", "Blocker-only rereview revision B")
    result_b = _submit_result("parent", "reviewer", "PASS revision B")

    history = list_delegation_results(terminal_id="parent")
    by_id = {entry["id"]: entry for entry in history}
    assert by_id[result_a["result_id"]]["review"]["authority_state"] == "historical"
    assert by_id[result_a["result_id"]]["review"]["current_authority"] is False
    assert by_id[result_b["result_id"]]["review"]["authority_state"] == "current"
    assert by_id[result_b["result_id"]]["review"]["current_authority"] is True
    assert by_id[result_a["result_id"]]["attempt_id"] != by_id[result_b["result_id"]]["attempt_id"]
    assert get_parent_completion_barrier("parent") == (1, 0)

    stale = acknowledge_child_assignment_result_outcome("parent", result_id=result_a["result_id"])
    assert stale["reason_code"] == "RESULT_REVIEW_ATTEMPT_SUPERSEDED"
    accepted = acknowledge_child_assignment_result_outcome(
        "parent", result_id=result_b["result_id"]
    )
    assert accepted["accepted"] is True
    assert get_parent_completion_barrier("parent") == (0, 0)


def test_superseded_review_does_not_block_transactional_terminal_guard(authority_db, tmp_path):
    repo, revision_a = _repository(tmp_path)
    with database.SessionLocal() as db:
        db.add(_reviewer("reviewer-a", repo, revision_a))
        db.commit()
    _start_review("parent", "reviewer-a", "Review exact revision A")
    result_a = _submit_result("parent", "reviewer-a", "BLOCK revision A")

    revision_b = _advance(repo, "B")
    with database.SessionLocal() as db:
        db.add(_reviewer("reviewer-b", repo, revision_b))
        db.commit()
    _start_review("parent", "reviewer-b", "Review exact revision B")
    result_b = _submit_result("parent", "reviewer-b", "PASS revision B")

    stale = acknowledge_child_assignment_result_outcome("parent", result_id=result_a["result_id"])
    assert stale["reason_code"] == "RESULT_REVIEW_ATTEMPT_SUPERSEDED"
    accepted = acknowledge_child_assignment_result_outcome(
        "parent", result_id=result_b["result_id"]
    )
    assert accepted["accepted"] is True

    # Historical handoff reviews can retain a delivered transport status after
    # a newer exact attempt supersedes their authority. The superseded marker,
    # not transport cleanup, removes them from the completion barrier.
    with database.SessionLocal() as db:
        old = (
            db.query(ChildAssignmentModel)
            .filter_by(parent_terminal_id="parent")
            .order_by(ChildAssignmentModel.id)
            .first()
        )
        old.status = "handoff_result_delivered"
        db.commit()
    assert get_parent_completion_barrier("parent") == (0, 0)

    assert set_workflow_terminal_state(
        "parent",
        "terminal",
        "exact review accepted",
        require_no_active_children=True,
    )

    with database.SessionLocal() as db:
        assignments = (
            db.query(ChildAssignmentModel)
            .filter_by(parent_terminal_id="parent")
            .order_by(ChildAssignmentModel.id)
            .all()
        )
        assert assignments[0].review_superseded_at is not None
        assert assignments[0].status == "cancelled"
        assert assignments[1].review_superseded_at is None
        assert assignments[1].status == "result_acknowledged"
        assert db.query(WorkflowModel).filter_by(root_terminal_id="parent").one().status == (
            "terminal"
        )


def test_same_reviewer_terminal_two_attempts_do_not_collapse_delivery(authority_db, tmp_path):
    repo, revision_a = _repository(tmp_path)
    with database.SessionLocal() as db:
        db.add(_reviewer("reviewer", repo, revision_a))
        db.commit()
    _start_review("parent", "reviewer", "First exact attempt")
    result_a = _submit_result("parent", "reviewer", "PASS first")
    _start_review("parent", "reviewer", "Second exact attempt")
    result_b = _submit_result("parent", "reviewer", "PASS second")

    assert result_a["result_id"] != result_b["result_id"]
    with database.SessionLocal() as db:
        attempts = (
            db.query(ChildAssignmentModel)
            .filter_by(child_terminal_id="reviewer")
            .order_by(ChildAssignmentModel.id)
            .all()
        )
        assert len(attempts) == 2
        assert attempts[0].attempt_id != attempts[1].attempt_id
        assert attempts[0].result_message_id == result_a["notice_id"]
        assert attempts[1].result_message_id == result_b["notice_id"]


def test_mcp_assign_reuses_same_reviewer_with_exact_new_attempt_and_ack(
    authority_db, monkeypatch, tmp_path
):
    """Public admitted assign owns a bounded rereview on a warm reviewer."""
    repo, revision_a = _repository(tmp_path)
    reviewer_worktree = tmp_path / "reviewer-worktree"
    _git(repo, "worktree", "add", "--detach", str(reviewer_worktree), revision_a)
    with database.SessionLocal() as db:
        db.add(
            _reviewer(
                "reviewer",
                repo,
                revision_a,
                launch_worktree=reviewer_worktree,
            )
        )
        db.commit()
    _start_review("parent", "reviewer", "V1 review revision A")
    result_a = _submit_result("parent", "reviewer", "BLOCK revision A")

    with database.SessionLocal() as db:
        first = db.query(ChildAssignmentModel).filter_by(child_terminal_id="reviewer").one()
        child_workflow = db.get(WorkflowModel, first.child_workflow_id)
        assert child_workflow is not None
        child_workflow.status = "terminal"
        parent_turn = (
            db.query(database.WorkflowTurnModel)
            .filter_by(inbox_message_id=result_a["notice_id"])
            .one()
        )
        db.commit()
        parent_turn_id = parent_turn.id

    assert activate_workflow_turn_for_inbox(result_a["notice_id"]) == parent_turn_id
    assert mark_workflow_turn_sent_for_inbox(result_a["notice_id"])

    revision_b = _advance(repo, "B")
    assert _git(reviewer_worktree, "rev-parse", "HEAD") == revision_a

    assert database.claim_workflow_turn_receipt("parent", parent_turn_id)
    monkeypatch.setenv("CAO_TERMINAL_ID", "parent")
    with (
        patch.object(mcp_server, "_fence_privileged_runtime"),
        patch.object(mcp_server, "wait_until_terminal_status", return_value=True),
        patch.object(mcp_server, "_send_direct_input_assign") as send_input,
    ):
        rereview = asyncio.run(
            mcp_server.assign(
                parent_turn_id,
                "reviewer_sol_high",
                "Blocker-only rereview revision B",
                reviewer_terminal_id="reviewer",
                **({"working_directory": None} if mcp_server.ENABLE_WORKING_DIRECTORY else {}),
            )
        )
    assert rereview["success"] is True, rereview
    assert rereview["terminal_id"] == "reviewer"
    assert rereview["reviewer_reused"] is True
    assert rereview["review_attempt"]["revision"] == revision_b
    send_input.assert_called_once()
    delivered_review_request = send_input.call_args.args[1]
    assert "CAO immutable review authority" in delivered_review_request
    assert f"exact_revision={revision_b}" in delivered_review_request

    with database.SessionLocal() as db:
        attempts = (
            db.query(ChildAssignmentModel)
            .filter_by(child_terminal_id="reviewer")
            .order_by(ChildAssignmentModel.id)
            .all()
        )
        assert len(attempts) == 2
        assert attempts[0].attempt_id != attempts[1].attempt_id
        assert attempts[0].review_superseded_at is not None
        assert attempts[1].review_subject_revision == revision_b
        child_turn_id = attempts[1].child_workflow_turn_id
        assert child_turn_id is not None

    assert database.claim_workflow_turn_receipt("reviewer", child_turn_id)
    monkeypatch.setenv("CAO_TERMINAL_ID", "reviewer")
    with (
        patch.object(mcp_server, "_fence_privileged_runtime"),
        patch.object(mcp_server.inbox_service, "check_and_send_pending_messages"),
    ):
        submitted = asyncio.run(mcp_server.send_message(child_turn_id, "parent", "PASS revision B"))
    assert submitted["success"] is True
    assert submitted["result_id"] != result_a["result_id"]
    assert activate_workflow_turn_for_inbox(submitted["message_id"]) is not None
    assert mark_workflow_turn_sent_for_inbox(submitted["message_id"])
    assert database.mark_child_assignment_result_delivered(submitted["message_id"])

    with database.SessionLocal() as db:
        acknowledgement_turn = (
            db.query(database.WorkflowTurnModel)
            .filter_by(inbox_message_id=submitted["message_id"])
            .one()
        ).id
    assert database.claim_workflow_turn_receipt("parent", acknowledgement_turn)
    monkeypatch.setenv("CAO_TERMINAL_ID", "parent")
    with patch.object(mcp_server, "_fence_privileged_runtime"):
        stale = asyncio.run(
            mcp_server.acknowledge_assigned_result(
                acknowledgement_turn, "reviewer", result_a["result_id"]
            )
        )
        accepted = asyncio.run(
            mcp_server.acknowledge_assigned_result(
                acknowledgement_turn, "reviewer", submitted["result_id"]
            )
        )
    assert stale["accepted"] is False
    assert stale["reason_code"] == "RESULT_REVIEW_ATTEMPT_SUPERSEDED"
    assert accepted["success"] is True
    assert get_delegation_result(result_a["result_id"])["review"]["authority_state"] == "historical"
    assert get_delegation_result(submitted["result_id"])["review"]["current_authority"] is True


def test_block_acknowledge_preserves_same_reviewer_for_exact_correction(
    authority_db, monkeypatch, tmp_path
):
    """Issue #130 repro A: acknowledgement must not auto-retire the sole reviewer."""
    repo, revision_a = _repository(tmp_path)
    reviewer_worktree = tmp_path / "resident-reviewer"
    _git(repo, "worktree", "add", "--detach", str(reviewer_worktree), revision_a)
    with database.SessionLocal() as db:
        db.add(_reviewer("reviewer", repo, revision_a, launch_worktree=reviewer_worktree))
        db.commit()

    _start_review("parent", "reviewer", "Review exact revision A")
    result_a = _submit_result("parent", "reviewer", "BLOCK revision A")
    parent_turn = activate_workflow_turn_for_inbox(result_a["notice_id"])
    assert isinstance(parent_turn, int)
    assert mark_workflow_turn_sent_for_inbox(result_a["notice_id"])
    assert claim_workflow_turn_receipt("parent", parent_turn)
    assert acknowledge_child_assignment_result_outcome("parent", result_id=result_a["result_id"])[
        "accepted"
    ]

    with database.SessionLocal() as db:
        first = db.query(ChildAssignmentModel).filter_by(child_terminal_id="reviewer").one()
        first_workflow = db.get(WorkflowModel, first.child_workflow_id)
        assert first_workflow is not None and first_workflow.status == "terminal"
        first_workflow_id = first.child_workflow_id
        first_turn_id = first.child_workflow_turn_id

    # Before #130 the acknowledgement immediately entered the automatic
    # retirement queue, exited the reviewer provider, and made the only
    # policy-eligible same-reviewer rereview fail with
    # REVIEWER_REUSE_NOT_ELIGIBLE. Preserve that runtime until the parent
    # review workflow itself reaches a terminal state.
    assert list_completed_assigned_child_retirement_candidates() == []
    retirement = database.claim_completed_assigned_child_retirement("parent", "reviewer")
    assert retirement == {"eligible": False, "error": "reviewer_reuse_window_open"}

    assert set_workflow_terminal_state(
        "parent", "owner_gate", "owner authorization required for correction"
    )
    assert list_completed_assigned_child_retirement_candidates() == []
    parent_turn = start_workflow_input("parent")
    assert parent_turn is not None and claim_workflow_turn_receipt("parent", parent_turn)

    revision_b = _advance(repo, "B")
    monkeypatch.setenv("CAO_TERMINAL_ID", "parent")
    with (
        patch.object(mcp_server, "_fence_privileged_runtime"),
        patch.object(mcp_server, "wait_until_terminal_status", return_value=True),
        patch.object(mcp_server, "_send_direct_input_assign") as send_input,
    ):
        rereview = asyncio.run(
            mcp_server.assign(
                parent_turn,
                "reviewer_sol_high",
                "Bounded blocker-only rereview",
                reviewer_terminal_id="reviewer",
                review_revision=revision_b,
                **({"working_directory": None} if mcp_server.ENABLE_WORKING_DIRECTORY else {}),
            )
        )

    assert rereview["success"] is True, rereview
    assert rereview["reviewer_reused"] is True
    assert rereview["review_attempt"]["revision"] == revision_b
    assert rereview["review_attempt"]["revision_source"] == "explicit"
    send_input.assert_called_once()
    assert f"exact_revision={revision_b}" in send_input.call_args.args[1]

    with database.SessionLocal() as db:
        attempts = (
            db.query(ChildAssignmentModel)
            .filter_by(child_terminal_id="reviewer")
            .order_by(ChildAssignmentModel.id)
            .all()
        )
        assert len(attempts) == 2
        assert attempts[0].attempt_id != attempts[1].attempt_id
        assert attempts[0].status == "result_acknowledged"
        assert attempts[0].review_superseded_at is not None
        assert attempts[1].review_subject_revision == revision_b
        assert attempts[1].review_subject_revision_source == "explicit"
        assert attempts[1].child_workflow_id != first_workflow_id
        assert attempts[1].child_workflow_turn_id != first_turn_id

    result_b = _submit_result("parent", "reviewer", "PASS revision B")
    artifact_a = get_delegation_result(result_a["result_id"])
    artifact_b = get_delegation_result(result_b["result_id"])
    assert artifact_a["review"]["authority_state"] == "historical"
    assert artifact_a["review"]["current_authority"] is False
    assert artifact_b["attempt_id"] != artifact_a["attempt_id"]
    assert artifact_b["review"]["revision"] == revision_b
    assert artifact_b["review"]["current_authority"] is True

    acknowledgement_turn = activate_workflow_turn_for_inbox(result_b["notice_id"])
    assert isinstance(acknowledgement_turn, int)
    assert mark_workflow_turn_sent_for_inbox(result_b["notice_id"])
    assert claim_workflow_turn_receipt("parent", acknowledgement_turn)
    assert acknowledge_child_assignment_result_outcome("parent", result_id=result_b["result_id"])[
        "accepted"
    ]
    assert list_completed_assigned_child_retirement_candidates() == []
    assert set_workflow_terminal_state(
        "parent",
        "terminal",
        "exact correction review accepted",
        require_no_active_children=True,
    )
    assert [
        candidate["child_terminal_id"]
        for candidate in list_completed_assigned_child_retirement_candidates()
    ] == ["reviewer"]


def test_resumed_reviewer_callback_preserves_exact_attempt_authority(authority_db, tmp_path):
    """A one-use execution resume remains inside the immutable review attempt."""
    repo, revision = _repository(tmp_path)
    with database.SessionLocal() as db:
        db.add(_reviewer("reviewer", repo, revision))
        db.commit()

    request = _start_review(
        "parent",
        "reviewer",
        "Review exact revision across a safe reconnect",
        requested_revision=revision,
    )
    admitted = database.claim_or_resume_workflow_turn_receipt("reviewer", request["child_turn_id"])
    assert admitted["accepted"] is True
    resumed = database.claim_or_resume_workflow_turn_receipt(
        "reviewer",
        request["child_turn_id"],
        resume_token=admitted["resume_token"],
    )
    assert resumed["accepted"] is True
    assert resumed["resumed"] is True
    resumed_turn = resumed["logical_turn_id"]
    assert resumed_turn != request["child_turn_id"]

    effect = claim_workflow_effect(
        "reviewer", resumed_turn, "send_message", "PASS after safe reconnect"
    )
    assert effect is not None
    notice, duplicate = create_child_assignment_result_message(
        "reviewer",
        "parent",
        "PASS after safe reconnect",
        workflow_effect_id=effect["id"],
        workflow_turn_id=resumed_turn,
    )
    assert notice is not None and duplicate is False and notice.result_id
    assert finish_workflow_effect("reviewer", effect["id"], effect["claim_token"], "completed")
    assert mark_child_assignment_result_delivered(notice.id)

    artifact = get_delegation_result(notice.result_id)
    assert artifact["attempt_id"]
    assert artifact["review"]["revision"] == revision
    assert artifact["review"]["revision_source"] == "explicit"
    assert artifact["review"]["current_authority"] is True
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id="reviewer").one()
        result = (
            db.query(database.DelegationResultModel)
            .filter_by(child_assignment_id=assignment.id)
            .one()
        )
        child_effect = db.get(database.WorkflowEffectModel, result.workflow_effect_id)
        assert assignment.child_workflow_turn_id == request["child_turn_id"]
        assert child_effect is not None and child_effect.workflow_turn_id == resumed_turn
        assert result.workflow_effect_id == effect["id"]

    callback_turn = activate_workflow_turn_for_inbox(notice.id)
    assert isinstance(callback_turn, int)
    assert get_delegation_result(notice.result_id)["workflow_turn_id"] == callback_turn
    assert mark_workflow_turn_sent_for_inbox(notice.id)
    assert claim_workflow_turn_receipt("parent", callback_turn)
    accepted = acknowledge_child_assignment_result_outcome("parent", result_id=notice.result_id)
    assert accepted["accepted"] is True
    replay = acknowledge_child_assignment_result_outcome("parent", result_id=notice.result_id)
    assert replay["accepted"] is False
    assert replay["reason_code"] == "RESULT_ALREADY_ACKNOWLEDGED"


def test_replacement_reviewer_binds_explicit_revision_not_its_current_head(
    authority_db, monkeypatch, tmp_path
):
    """Issue #130 repro B: typed B wins even when the replacement starts at C."""
    repo, _revision_a = _repository(tmp_path)
    revision_b = _advance(repo, "B")
    revision_c = _advance(repo, "C")
    reviewer_worktree = tmp_path / "replacement-reviewer"
    _git(repo, "worktree", "add", "--detach", str(reviewer_worktree), revision_c)
    with database.SessionLocal() as db:
        db.add(
            _reviewer(
                "replacement",
                repo,
                revision_c,
                launch_worktree=reviewer_worktree,
            )
        )
        db.commit()
    assert _git(repo, "rev-parse", "HEAD") == revision_c
    assert _git(reviewer_worktree, "rev-parse", "HEAD") == revision_c

    request_message = "Review the owner-authorized correction"
    with database.SessionLocal() as db:
        replacement = db.get(TerminalModel, "replacement")
        assert replacement is not None
        implicit_binding = database._review_subject_for_child(
            replacement,
            hashlib.sha256(request_message.encode("utf-8")).hexdigest(),
        )
    # Negative control for the production failure: without typed authority,
    # a SHA appearing only in prose cannot override the replacement's C HEAD.
    assert implicit_binding["review_subject_revision"] == revision_c

    parent_turn = start_workflow_input("parent")
    assert parent_turn is not None and claim_workflow_turn_receipt("parent", parent_turn)
    monkeypatch.setenv("CAO_TERMINAL_ID", "parent")
    with (
        patch.object(mcp_server, "_fence_privileged_runtime"),
        patch.object(
            mcp_server, "_create_terminal", return_value=("replacement", "codex")
        ) as create,
        patch.object(mcp_server, "wait_until_terminal_status", return_value=True),
        patch.object(mcp_server, "_send_direct_input_assign") as send_input,
    ):
        assigned = asyncio.run(
            mcp_server.assign(
                parent_turn,
                "reviewer_sol_high",
                request_message,
                review_revision=revision_b,
                **({"working_directory": None} if mcp_server.ENABLE_WORKING_DIRECTORY else {}),
            )
        )
        duplicate = asyncio.run(
            mcp_server.assign(
                parent_turn,
                "reviewer_sol_high",
                request_message,
                review_revision=revision_b,
                **({"working_directory": None} if mcp_server.ENABLE_WORKING_DIRECTORY else {}),
            )
        )

    assert assigned["success"] is True, assigned
    assert assigned["review_attempt"]["revision"] == revision_b
    assert assigned["review_attempt"]["revision_source"] == "explicit"
    assert duplicate["success"] is False
    assert duplicate["reason_code"] == "DUPLICATE_EFFECT"
    create.assert_called_once()
    send_input.assert_called_once()

    with database.SessionLocal() as db:
        attempt = db.query(ChildAssignmentModel).filter_by(child_terminal_id="replacement").one()
        assert attempt.review_subject_revision == revision_b
        assert attempt.review_subject_revision != revision_c
        assert attempt.review_subject_revision_source == "explicit"

    result = _submit_result("parent", "replacement", "PASS exact revision B")
    artifact = get_delegation_result(result["result_id"])
    assert artifact["review"]["revision"] == revision_b
    assert artifact["review"]["revision_source"] == "explicit"
    assert artifact["review"]["current_authority"] is True
    callback_turn = activate_workflow_turn_for_inbox(result["notice_id"])
    assert isinstance(callback_turn, int)
    assert mark_workflow_turn_sent_for_inbox(result["notice_id"])
    assert claim_workflow_turn_receipt("parent", callback_turn)
    assert acknowledge_child_assignment_result_outcome("parent", result_id=result["result_id"])[
        "accepted"
    ]


def test_unavailable_explicit_revision_fails_closed_before_reviewer_input(
    authority_db, monkeypatch, tmp_path
):
    repo, revision = _repository(tmp_path)
    reviewer_worktree = tmp_path / "unbound-replacement"
    _git(repo, "worktree", "add", "--detach", str(reviewer_worktree), revision)
    with database.SessionLocal() as db:
        db.add(
            _reviewer(
                "replacement",
                repo,
                revision,
                launch_worktree=reviewer_worktree,
            )
        )
        db.commit()

    unavailable = "f" * 40
    parent_turn = start_workflow_input("parent")
    assert parent_turn is not None and claim_workflow_turn_receipt("parent", parent_turn)
    monkeypatch.setenv("CAO_TERMINAL_ID", "parent")
    with (
        patch.object(mcp_server, "_fence_privileged_runtime"),
        patch.object(mcp_server, "_create_terminal", return_value=("replacement", "codex")),
        patch.object(mcp_server, "wait_until_terminal_status", return_value=True),
        patch.object(mcp_server, "_send_direct_input_assign") as send_input,
    ):
        rejected = asyncio.run(
            mcp_server.assign(
                parent_turn,
                "reviewer_sol_high",
                f"Text mentions {revision}, but typed authority requests an unavailable commit",
                review_revision=unavailable,
                **({"working_directory": None} if mcp_server.ENABLE_WORKING_DIRECTORY else {}),
            )
        )

    assert rejected["success"] is False
    assert rejected["reason_code"] == "REVIEW_REVISION_UNAVAILABLE"
    send_input.assert_not_called()
    result = database.get_delegation_result_for_assignment("replacement")
    assert result is not None
    assert result["status"] == "cancelled"
    assert result["reason_code"] == "review_authority_unbound"
    assert result["review"]["authority_state"] == "unbound"
    assert result["review"]["current_authority"] is False


def test_invalid_explicit_revision_is_rejected_before_claim_or_launch(authority_db, monkeypatch):
    parent_turn = start_workflow_input("parent")
    assert parent_turn is not None and claim_workflow_turn_receipt("parent", parent_turn)
    monkeypatch.setenv("CAO_TERMINAL_ID", "parent")
    with (
        patch.object(mcp_server, "_fence_privileged_runtime") as runtime_fence,
        patch.object(mcp_server, "_create_terminal") as create,
    ):
        rejected = asyncio.run(
            mcp_server.assign(
                parent_turn,
                "reviewer_sol_high",
                "Do not admit an abbreviated or symbolic target",
                review_revision="HEAD",
                **({"working_directory": None} if mcp_server.ENABLE_WORKING_DIRECTORY else {}),
            )
        )

    assert rejected["success"] is False
    assert rejected["reason_code"] == "REVIEW_REVISION_INVALID"
    runtime_fence.assert_not_called()
    create.assert_not_called()
    with database.SessionLocal() as db:
        assert db.query(database.WorkflowEffectModel).count() == 0
        assert db.query(ChildAssignmentModel).count() == 0


def test_mcp_assign_cannot_reuse_an_ordinary_child_as_reviewer(authority_db, monkeypatch):
    with database.SessionLocal() as db:
        db.add(
            TerminalModel(
                id="ordinary-child",
                tmux_session="review-session",
                tmux_window="ordinary-child",
                provider="codex",
                agent_profile="developer",
                runtime_lifecycle="running",
            )
        )
        db.commit()
    turn_id = start_workflow_input("parent")
    assert turn_id is not None
    assert database.claim_workflow_turn_receipt("parent", turn_id)
    monkeypatch.setenv("CAO_TERMINAL_ID", "parent")
    with patch.object(mcp_server, "_fence_privileged_runtime"):
        rejected = asyncio.run(
            mcp_server.assign(
                turn_id,
                "reviewer_sol_high",
                "Do not reuse an ordinary child",
                reviewer_terminal_id="ordinary-child",
                **({"working_directory": None} if mcp_server.ENABLE_WORKING_DIRECTORY else {}),
            )
        )
    assert rejected["success"] is False
    assert rejected["terminal_id"] is None
    assert rejected["reason_code"] == "REVIEWER_REUSE_NOT_ELIGIBLE"
    with database.SessionLocal() as db:
        assert db.query(ChildAssignmentModel).count() == 0


def test_mcp_assign_cannot_claim_unrelated_reviewer_as_first_attempt(
    authority_db, monkeypatch, tmp_path
):
    repo, revision = _repository(tmp_path)
    with database.SessionLocal() as db:
        db.add(_reviewer("unrelated-reviewer", repo, revision))
        db.commit()
    turn_id = start_workflow_input("parent")
    assert turn_id is not None
    assert database.claim_workflow_turn_receipt("parent", turn_id)
    monkeypatch.setenv("CAO_TERMINAL_ID", "parent")
    with (
        patch.object(mcp_server, "_fence_privileged_runtime"),
        patch.object(mcp_server, "wait_until_terminal_status", return_value=True),
    ):
        rejected = asyncio.run(
            mcp_server.assign(
                turn_id,
                "reviewer_sol_high",
                "Caller-selected reviewer must already belong to this parent",
                reviewer_terminal_id="unrelated-reviewer",
                **({"working_directory": None} if mcp_server.ENABLE_WORKING_DIRECTORY else {}),
            )
        )
    assert rejected["success"] is False
    assert rejected["terminal_id"] is None
    assert rejected["reason_code"] == "REVIEWER_REUSE_NOT_ELIGIBLE"
    with database.SessionLocal() as db:
        assert db.query(ChildAssignmentModel).count() == 0


def test_cancelling_new_attempt_does_not_rewrite_prior_review_history(authority_db, tmp_path):
    repo, revision = _repository(tmp_path)
    with database.SessionLocal() as db:
        db.add(_reviewer("reviewer", repo, revision))
        db.commit()
    _start_review("parent", "reviewer", "First exact attempt")
    result_a = _submit_result("parent", "reviewer", "PASS first")
    attempt_b = _start_review("parent", "reviewer", "Second exact attempt")

    assert cancel_child_assignment_attempt(
        "parent", "reviewer", attempt_b["effect_id"], reason_code="test_send_failed"
    )
    with database.SessionLocal() as db:
        attempts = (
            db.query(ChildAssignmentModel)
            .filter_by(child_terminal_id="reviewer")
            .order_by(ChildAssignmentModel.id)
            .all()
        )
        assert attempts[0].result_message_id == result_a["notice_id"]
        assert attempts[0].status == "result_superseded"
        assert attempts[1].status == "cancelled"
        second_result = (
            db.query(database.DelegationResultModel)
            .filter_by(child_assignment_id=attempts[1].id)
            .one()
        )
        assert second_result.status == "cancelled"
        assert second_result.reason_code == "test_send_failed"


def test_different_reviewer_attempts_on_same_revision_remain_distinct(authority_db, tmp_path):
    repo, revision = _repository(tmp_path)
    with database.SessionLocal() as db:
        db.add_all(
            [
                _reviewer("reviewer-a", repo, revision),
                _reviewer("reviewer-b", repo, revision),
            ]
        )
        db.commit()
    _start_review("parent", "reviewer-a", "Review attempt A")
    result_a = _submit_result("parent", "reviewer-a", "PASS A")
    _start_review("parent", "reviewer-b", "Review attempt B")
    result_b = _submit_result("parent", "reviewer-b", "PASS B")

    history = {entry["id"]: entry for entry in list_delegation_results("parent")}
    assert (
        history[result_a["result_id"]]["attempt_id"] != history[result_b["result_id"]]["attempt_id"]
    )
    assert history[result_a["result_id"]]["review"]["revision"] == revision
    assert history[result_b["result_id"]]["review"]["revision"] == revision


def test_legacy_reviewer_result_is_readable_but_cannot_authorize_new_gate(authority_db, tmp_path):
    repo, revision = _repository(tmp_path)
    with database.SessionLocal() as db:
        db.add(_reviewer("legacy-reviewer", repo, revision))
        db.commit()
    assert register_child_assignment("parent", "legacy-reviewer")
    result = _submit_result("parent", "legacy-reviewer", "historical PASS")

    artifact = get_delegation_result(result["result_id"])
    assert artifact["review"]["authority_state"] == "legacy_unscoped"
    rejected = acknowledge_child_assignment_result_outcome("parent", result_id=result["result_id"])
    assert rejected["reason_code"] == "RESULT_REVIEW_AUTHORITY_UNBOUND"

    _start_review("parent", "legacy-reviewer", "Replacement exact review")
    replacement = _submit_result("parent", "legacy-reviewer", "current PASS")
    history = {entry["id"]: entry for entry in list_delegation_results("parent")}
    assert history[result["result_id"]]["review"]["superseded_at"] is not None
    assert history[replacement["result_id"]]["review"]["current_authority"] is True
    assert get_parent_completion_barrier("parent") == (1, 0)


def test_same_parent_effect_retry_does_not_create_another_attempt(authority_db, tmp_path):
    repo, revision = _repository(tmp_path)
    with database.SessionLocal() as db:
        db.add(_reviewer("reviewer", repo, revision))
        db.commit()
    request = _start_review("parent", "reviewer", "One immutable request")
    assert (
        register_child_assignment(
            "parent",
            "reviewer",
            workflow_turn_id=request["turn_id"],
            workflow_effect_id=request["effect_id"],
            request_message="One immutable request",
        )
        is False
    )
    with database.SessionLocal() as db:
        assert db.query(ChildAssignmentModel).filter_by(child_terminal_id="reviewer").count() == 1


def test_concurrent_review_requests_create_distinct_attempts_and_one_current(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'concurrent-review.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(database, "_child_assignment_schema_ready", True)
    monkeypatch.setattr(database, "_delegation_result_schema_ready", True)
    repo, revision = _repository(tmp_path)
    with database.SessionLocal() as db:
        db.add_all(
            [_reviewer("reviewer-a", repo, revision), _reviewer("reviewer-b", repo, revision)]
        )
        db.commit()
    turn_id = start_workflow_input("parent")
    assert turn_id is not None and claim_workflow_turn_receipt("parent", turn_id)
    effects = [
        claim_workflow_effect("parent", turn_id, "assign", f"concurrent-{suffix}")
        for suffix in ("a", "b")
    ]
    assert all(effect is not None for effect in effects)
    barrier = Barrier(2)

    def register(index: int) -> bool:
        barrier.wait()
        effect = effects[index]
        assert effect is not None
        return register_child_assignment(
            "parent",
            f"reviewer-{'a' if index == 0 else 'b'}",
            workflow_turn_id=turn_id,
            workflow_effect_id=effect["id"],
            request_message=f"concurrent-{index}",
            requested_review_revision=revision,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(register, (0, 1))) == [True, True]
    with database.SessionLocal() as db:
        attempts = db.query(ChildAssignmentModel).all()
        assert len(attempts) == 2
        assert len({attempt.attempt_id for attempt in attempts}) == 2
        assert {attempt.review_subject_revision for attempt in attempts} == {revision}
        assert {attempt.review_subject_revision_source for attempt in attempts} == {"explicit"}
        assert sum(attempt.review_superseded_at is None for attempt in attempts) == 1


def test_restart_requeues_only_current_review_attempt(authority_db, tmp_path):
    repo, revision = _repository(tmp_path)
    with database.SessionLocal() as db:
        db.add(_reviewer("reviewer", repo, revision))
        db.commit()
    _start_review("parent", "reviewer", "Attempt A")
    result_a = _submit_result("parent", "reviewer", "PASS A")
    _start_review("parent", "reviewer", "Attempt B")
    result_b = _submit_result("parent", "reviewer", "PASS B")

    assert requeue_unacknowledged_child_assignment_results() == 1
    with database.SessionLocal() as db:
        old = (
            db.query(ChildAssignmentModel).filter_by(result_message_id=result_a["notice_id"]).one()
        )
        current = (
            db.query(ChildAssignmentModel).filter_by(result_message_id=result_b["notice_id"]).one()
        )
        assert old.review_superseded_at is not None
        assert old.status == "result_superseded"
        assert current.review_superseded_at is None
        assert current.status == "result_queued"


def test_result_identity_cannot_cross_parent_or_child(authority_db, tmp_path):
    repo, revision = _repository(tmp_path)
    with database.SessionLocal() as db:
        db.add(_reviewer("reviewer", repo, revision))
        db.commit()
    _start_review("parent", "reviewer", "Review revision")
    result = _submit_result("parent", "reviewer", "PASS")
    assert start_workflow_input("other-parent") is not None

    wrong_parent = acknowledge_child_assignment_result_outcome(
        "other-parent", result_id=result["result_id"]
    )
    wrong_child = acknowledge_child_assignment_result_outcome(
        "parent", child_terminal_id="other-child", result_id=result["result_id"]
    )
    assert wrong_parent["accepted"] is False
    assert wrong_child["reason_code"] == "RESULT_IDENTITY_MISMATCH"


def test_reviewer_acknowledgement_requires_immutable_result_identity(authority_db, tmp_path):
    repo, revision = _repository(tmp_path)
    with database.SessionLocal() as db:
        db.add(_reviewer("reviewer", repo, revision))
        db.commit()
    _start_review("parent", "reviewer", "Review exact revision")
    result = _submit_result("parent", "reviewer", "PASS")

    ambiguous = acknowledge_child_assignment_result_outcome("parent", child_terminal_id="reviewer")
    assert ambiguous["accepted"] is False
    assert ambiguous["reason_code"] == "RESULT_REVIEW_IDENTITY_REQUIRED"
    exact = acknowledge_child_assignment_result_outcome(
        "parent", child_terminal_id="reviewer", result_id=result["result_id"]
    )
    assert exact["accepted"] is True


def test_result_from_prior_parent_workflow_is_historical(authority_db, tmp_path):
    repo, revision = _repository(tmp_path)
    with database.SessionLocal() as db:
        db.add(_reviewer("reviewer", repo, revision))
        db.commit()
    _start_review("parent", "reviewer", "Review old parent workflow")
    result = _submit_result("parent", "reviewer", "PASS")
    assert get_delegation_result(result["result_id"])["review"]["current_authority"] is True

    with database.SessionLocal() as db:
        old_workflow = (
            db.query(WorkflowModel)
            .filter_by(root_terminal_id="parent")
            .order_by(WorkflowModel.id.desc())
            .one()
        )
        old_workflow.status = "terminal"
        db.add(WorkflowModel(root_terminal_id="parent", status="open"))
        db.commit()

    historical = get_delegation_result(result["result_id"])
    assert historical["review"]["authority_state"] == "historical"
    assert historical["review"]["current_authority"] is False


def test_superseded_acknowledged_attempt_cannot_retire_reused_reviewer(authority_db, tmp_path):
    repo, revision = _repository(tmp_path)
    with database.SessionLocal() as db:
        db.add(_reviewer("reviewer", repo, revision))
        db.commit()
    _start_review("parent", "reviewer", "Review attempt A")
    result_a = _submit_result("parent", "reviewer", "PASS A")
    assert acknowledge_child_assignment_result_outcome("parent", result_id=result_a["result_id"])[
        "accepted"
    ]

    _start_review("parent", "reviewer", "Review attempt B")
    _submit_result("parent", "reviewer", "PASS B")

    assert list_completed_assigned_child_retirement_candidates() == []


def test_legacy_unique_child_schema_migrates_without_fabricating_revision(monkeypatch, tmp_path):
    database_file = tmp_path / "legacy.db"
    with sqlite3.connect(database_file) as conn:
        conn.execute(
            "CREATE TABLE terminals ("
            "id VARCHAR PRIMARY KEY, managed_worktree_kind VARCHAR, "
            "launch_snapshot_json TEXT)"
        )
        conn.execute(
            "INSERT INTO terminals VALUES (?, ?, ?)",
            ("reviewer", "reviewer", None),
        )
        conn.execute(
            "CREATE TABLE child_assignments ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "parent_terminal_id VARCHAR NOT NULL, "
            "child_terminal_id VARCHAR NOT NULL UNIQUE, "
            "status VARCHAR NOT NULL, result_message_id INTEGER, "
            "cleanup_acknowledged BOOLEAN NOT NULL DEFAULT 0, "
            "direct_result_output TEXT, "
            "handoff_input_received BOOLEAN NOT NULL DEFAULT 0, "
            "created_at DATETIME, updated_at DATETIME)"
        )
        conn.execute(
            "INSERT INTO child_assignments "
            "(parent_terminal_id, child_terminal_id, status) VALUES (?, ?, ?)",
            ("parent", "reviewer", "result_delivered"),
        )
    monkeypatch.setattr("cli_agent_orchestrator.constants.DATABASE_FILE", database_file)

    assert database._migrate_child_assignment_columns() is True
    with sqlite3.connect(database_file) as conn:
        migrated = conn.execute(
            "SELECT attempt_id, review_subject_kind, review_subject_revision, "
            "review_subject_revision_source "
            "FROM child_assignments"
        ).fetchone()
        assert migrated is not None
        assert migrated[0]
        assert migrated[1:] == ("legacy_unscoped", None, None)
        # A pre-upgrade process does not know about attempt_id.  Its write
        # must remain possible after the new process has rebuilt the shared
        # table; the database supplies identity, while the absent review
        # provenance keeps the row non-authoritative for a new review gate.
        conn.execute(
            "INSERT INTO child_assignments "
            "(parent_terminal_id, child_terminal_id, status) VALUES (?, ?, ?)",
            ("parent", "legacy-rolling-child", "awaiting_result"),
        )
        rolling_attempt = conn.execute(
            "SELECT attempt_id, review_subject_kind, review_subject_revision, "
            "review_subject_revision_source "
            "FROM child_assignments WHERE child_terminal_id = ?",
            ("legacy-rolling-child",),
        ).fetchone()
        assert rolling_attempt is not None
        assert len(rolling_attempt[0]) == 32
        assert rolling_attempt[1:] == (None, None, None)
        conn.execute(
            "INSERT INTO child_assignments "
            "(parent_terminal_id, child_terminal_id, status, attempt_id) "
            "VALUES (?, ?, ?, ?)",
            ("parent", "reviewer", "awaiting_result", "new-attempt"),
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM child_assignments WHERE child_terminal_id = 'reviewer'"
            ).fetchone()[0]
            == 2
        )


def test_current_child_schema_adds_explicit_revision_provenance(monkeypatch, tmp_path):
    database_file = tmp_path / "current-without-revision-source.db"
    engine = create_engine(f"sqlite:///{database_file}")
    Base.metadata.create_all(bind=engine)
    engine.dispose()
    with sqlite3.connect(database_file) as conn:
        conn.execute("ALTER TABLE child_assignments DROP COLUMN review_subject_revision_source")
    monkeypatch.setattr("cli_agent_orchestrator.constants.DATABASE_FILE", database_file)

    assert database._migrate_child_assignment_columns() is True
    with sqlite3.connect(database_file) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(child_assignments)")}
        assert "review_subject_revision_source" in columns
