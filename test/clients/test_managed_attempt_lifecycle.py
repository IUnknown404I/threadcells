"""Deterministic orphaned managed-attempt admission and fencing regressions."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.database import (
    Base,
    ChildAssignmentModel,
    InboxModel,
    ManagedAttemptFenceError,
    ManagedAttemptLifecycleEventModel,
    ManagedAttemptLifecycleModel,
    OwnerLaunchGrantModel,
    TerminalModel,
    WorkflowEffectModel,
    WorkflowModel,
    WorkflowTurnModel,
    WorktreeWriterLeaseModel,
)
from cli_agent_orchestrator.mcp_server import server as mcp_server
from cli_agent_orchestrator.models.inbox import ChildAssignmentStatus
from cli_agent_orchestrator.models.result import HandoffResultDocumentV1
from cli_agent_orchestrator.runtime_generation import ACTIVE_RUNTIME_GENERATION
from cli_agent_orchestrator.services import (
    interaction_read_model_service,
    managed_attempt_service,
)

RUNTIME_GENERATION = "11111111-2222-4333-8444-555555555555"
WRITER_GENERATION = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
FENCE_REASON = "ORPHANED_MANAGED_CONTINUATION_AFTER_HANDOFF_TIMEOUT"
LATE_TOKEN = "synthetic-late-child-token"


@pytest.fixture
def attempt_db(monkeypatch, tmp_path):
    db_path = tmp_path / "managed-attempt.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    for name in dir(database):
        if name.startswith("_") and name.endswith("_schema_ready"):
            monkeypatch.setattr(database, name, True)
    yield engine
    engine.dispose()


def _add_terminal(
    terminal_id: str,
    *,
    session_id: str = "synthetic-attempt-session",
    writer: bool = False,
    owner: bool = False,
    project_id: str = "synthetic-project",
) -> None:
    worktree = f"/synthetic/{terminal_id}"
    canonical_source = "/synthetic/source"
    owner_grant_id = f"grant-{terminal_id}" if owner else None
    profile_revision_id = "synthetic-owner-profile-r1" if owner else None
    provider_config_revision_id = "synthetic-codex-provider-r1" if owner else None
    with database.SessionLocal() as db:
        if owner:
            now = datetime.now()
            db.add(
                OwnerLaunchGrantModel(
                    id=owner_grant_id,
                    token_sha256=hashlib.sha256(f"token-{terminal_id}".encode()).hexdigest(),
                    launch_id=f"launch-{terminal_id}",
                    agent_profile="critical_sol_xhigh_owner",
                    provider="codex",
                    canonical_worktree=canonical_source,
                    scope_json=json.dumps(
                        {
                            "profile_revision_id": profile_revision_id,
                            "provider_config_revision_id": provider_config_revision_id,
                            "project_id": project_id,
                        }
                    ),
                    issued_by="synthetic-owner",
                    created_at=now,
                    expires_at=now + timedelta(minutes=5),
                    consumed_at=now,
                    consumed_terminal_id=terminal_id,
                )
            )
        db.add(
            TerminalModel(
                id=terminal_id,
                tmux_session="cao-synthetic-attempt",
                session_id=session_id,
                tmux_window=f"window-{terminal_id}",
                provider="codex",
                agent_profile=("critical_sol_xhigh_owner" if owner else "developer"),
                owner_grant_id=owner_grant_id,
                profile_revision_id=profile_revision_id,
                provider_config_revision_id=provider_config_revision_id,
                project_id=project_id,
                project_path=canonical_source,
                managed_worktree_source=canonical_source,
                launch_worktree=worktree,
                write_enabled=writer,
                writer_authority_generation=(WRITER_GENERATION if writer else None),
                runtime_lifecycle="running",
                runtime_pane_id=f"%{terminal_id[-1]}",
                runtime_pane_pid=4100 + len(terminal_id),
                runtime_generation=RUNTIME_GENERATION,
                runtime_generation_origin="launch",
                runtime_process_start_ticks=9000 + len(terminal_id),
                runtime_process_group_id=5100 + len(terminal_id),
                runtime_process_session_id=6100 + len(terminal_id),
                provider_resume_identity="01234567-89ab-cdef-0123-456789abcdef",
                provider_resume_runtime_generation=RUNTIME_GENERATION,
                provider_runtime_compatibility_generation=ACTIVE_RUNTIME_GENERATION,
                auth_token_sha256=(
                    hashlib.sha256(LATE_TOKEN.encode()).hexdigest() if writer else None
                ),
            )
        )
        if writer:
            db.add(
                WorktreeWriterLeaseModel(
                    canonical_worktree=worktree,
                    terminal_id=terminal_id,
                    authority_generation=WRITER_GENERATION,
                )
            )
        db.commit()


def _create_attempt(*, bind: bool = True, child: str = "child-one") -> dict:
    parent = "parent-one"
    _add_terminal(parent, owner=True)
    _add_terminal(child, writer=True)
    parent_turn = database.start_workflow_input(parent)
    assert parent_turn is not None
    assert database.claim_workflow_turn_receipt(parent, parent_turn)
    effect = database.claim_workflow_effect(
        parent, parent_turn, "handoff", f"synthetic-request:{child}"
    )
    assert effect is not None
    assert database.register_handoff_child(
        parent,
        child,
        workflow_turn_id=parent_turn,
        workflow_effect_id=effect["id"],
        request_message="Run the synthetic managed attempt.",
    )
    binding = database.issue_workflow_input_binding(
        child,
        "Run the synthetic managed attempt.",
        child_assignment_workflow_effect_id=effect["id"],
    )
    assert binding is not None
    child_turn = database.resolve_workflow_input_binding(child, binding)
    assert child_turn is not None
    if bind:
        assert database.bind_child_assignment_input_turn(child, binding)
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        return {
            "parent": parent,
            "child": child,
            "parent_turn": parent_turn,
            "effect_id": effect["id"],
            "assignment_id": assignment.id,
            "attempt_id": assignment.attempt_id,
            "binding": binding,
            "child_turn": child_turn,
        }


def _fence_kwargs(attempt: dict, **overrides) -> dict:
    values = {
        "assignment_id": attempt["assignment_id"],
        "attempt_id": attempt["attempt_id"],
        "parent_terminal_id": attempt["parent"],
        "child_terminal_id": attempt["child"],
        "request_workflow_effect_id": attempt["effect_id"],
        "child_workflow_turn_id": attempt["child_turn"],
        "reason_code": FENCE_REASON,
        "expected_runtime_generation": RUNTIME_GENERATION,
        "expected_writer_authority_generation": WRITER_GENERATION,
    }
    values.update(overrides)
    return values


def _owner_fence_kwargs(attempt: dict, **overrides) -> dict:
    values = _fence_kwargs(attempt, **overrides)
    values["caller_terminal_id"] = attempt["parent"]
    return values


def _expire_initial_admission(attempt: dict, now: datetime) -> None:
    with database.SessionLocal() as db:
        lifecycle = db.get(ManagedAttemptLifecycleModel, attempt["assignment_id"])
        lifecycle.recovery_deadline_at = now - timedelta(seconds=1)
        db.commit()


def _exhaust_recovery(attempt: dict, now: datetime) -> None:
    _expire_initial_admission(attempt, now)
    assert database.reconcile_managed_attempt_timeouts(now) == {
        "recovery_scheduled": 1,
        "fence_pending": 0,
    }
    claim = database.claim_workflow_turn(attempt["child"], now=now + timedelta(seconds=3))
    assert claim is not None
    assert database.requeue_workflow_turn(
        claim["id"],
        claim["claim_token"],
        claim["claim_generation"],
        now=now + timedelta(seconds=4),
        admission_reason_code="MANAGED_IDENTITY_BINDING_FAILED",
    )


def _claim_and_complete_fence(attempt: dict) -> tuple[dict, dict]:
    claim = database.claim_managed_attempt_fence(**_owner_fence_kwargs(attempt))
    completed = database.complete_managed_attempt_fence(
        attempt["assignment_id"],
        claim["fence_claim_token"],
        reason_code=FENCE_REASON,
    )
    return claim, completed


def test_handoff_timeout_does_not_recover_an_admitted_running_provider(attempt_db):
    """Regression 1: a real admitted/running child is not a timeout orphan."""
    attempt = _create_attempt()
    assert database.mark_managed_attempt_prompt_delivered(attempt["child"], attempt["child_turn"])
    assert database.claim_workflow_turn_receipt(attempt["child"], attempt["child_turn"])
    assert database.observe_managed_attempt_provider_state(
        attempt["child"], "provider_running", workflow_turn_id=attempt["child_turn"]
    )
    assert database.reconcile_managed_attempt_timeouts(datetime.now() + timedelta(days=1)) == {
        "recovery_scheduled": 0,
        "fence_pending": 0,
    }
    assert (
        database.get_managed_attempt_lifecycle(assignment_id=attempt["assignment_id"])["state"]
        == "provider_running"
    )


def test_pre_delivery_and_ready_without_prompt_get_one_bounded_retry(attempt_db):
    """Regressions 2, 3, 5 and 6: no prompt admission gets one durable wake."""
    attempt = _create_attempt()
    now = datetime.now()
    _expire_initial_admission(attempt, now)
    assert database.reconcile_managed_attempt_timeouts(now) == {
        "recovery_scheduled": 1,
        "fence_pending": 0,
    }
    assert database.reconcile_managed_attempt_timeouts(now) == {
        "recovery_scheduled": 0,
        "fence_pending": 0,
    }
    lifecycle = database.get_managed_attempt_lifecycle(assignment_id=attempt["assignment_id"])
    assert lifecycle["state"] == "recovery_scheduled"
    assert lifecycle["recovery_attempt_count"] == 1
    assert lifecycle["prompt_delivery_acknowledged_at"] is None
    with database.SessionLocal() as db:
        turn = db.get(WorkflowTurnModel, attempt["child_turn"])
        assert turn.state == database.TURN_QUEUED
        assert turn.queue_reason == "MANAGED_ATTEMPT_RECOVERY_SCHEDULED"


def test_delivered_but_unadmitted_retry_requires_a_fresh_ack(attempt_db):
    """A delivery acknowledgement is exact to one scheduled transport attempt."""
    attempt = _create_attempt()
    assert database.mark_managed_attempt_prompt_delivered(attempt["child"], attempt["child_turn"])
    now = datetime.now()
    _expire_initial_admission(attempt, now)
    assert database.reconcile_managed_attempt_timeouts(now)["recovery_scheduled"] == 1
    lifecycle = database.get_managed_attempt_lifecycle(assignment_id=attempt["assignment_id"])
    assert lifecycle["delivery_attempt_count"] == 1
    assert lifecycle["prompt_delivery_acknowledged_at"] is None
    assert database.mark_managed_attempt_prompt_delivered(attempt["child"], attempt["child_turn"])
    lifecycle = database.get_managed_attempt_lifecycle(assignment_id=attempt["assignment_id"])
    assert lifecycle["delivery_attempt_count"] == 2
    assert lifecycle["prompt_delivery_acknowledged_at"] is not None


def test_wrong_managed_binding_never_manufactures_delivery_or_admission(attempt_db):
    """Regression 4: managed identity/binding failure stays pre-delivery."""
    attempt = _create_attempt(bind=False)
    assert not database.bind_child_assignment_input_turn(
        attempt["child"], "not-the-server-issued-binding"
    )
    assert not database.mark_managed_attempt_prompt_delivered(
        attempt["child"], attempt["child_turn"] + 100
    )
    assert not database.observe_managed_attempt_provider_state(
        attempt["child"], "provider_running", workflow_turn_id=attempt["child_turn"]
    )
    lifecycle = database.get_managed_attempt_lifecycle(assignment_id=attempt["assignment_id"])
    assert lifecycle["state"] == "assignment_created"
    assert lifecycle["prompt_delivery_acknowledged_at"] is None
    assert lifecycle["provider_admitted_at"] is None


def test_second_same_child_recovery_is_prohibited_and_requests_fence(attempt_db):
    """Regressions 7 and 8: failed retry becomes explicit fence_pending."""
    attempt = _create_attempt()
    _exhaust_recovery(attempt, datetime.now())
    lifecycle = database.get_managed_attempt_lifecycle(assignment_id=attempt["assignment_id"])
    assert lifecycle["state"] == "fence_pending"
    assert lifecycle["recovery_attempt_count"] == 1
    with database.SessionLocal() as db:
        events = (
            db.query(ManagedAttemptLifecycleEventModel)
            .filter_by(assignment_id=attempt["assignment_id"])
            .all()
        )
        assert sum(event.event_type == "recovery_scheduled" for event in events) == 1
        assert sum(event.event_type == "recovery_failed" for event in events) == 1


def test_atomic_fence_revokes_only_exact_authority_and_parent_wakes_once(attempt_db):
    """Regressions 8, 12 and 17: exact fence revokes local authority and wakes once."""
    attempt = _create_attempt()
    _exhaust_recovery(attempt, datetime.now())
    claim, completed = _claim_and_complete_fence(attempt)
    assert claim["state"] == "fence_claimed"
    assert completed["state"] == "fenced"
    with database.SessionLocal() as db:
        terminal = db.get(TerminalModel, attempt["child"])
        assignment = db.get(ChildAssignmentModel, attempt["assignment_id"])
        assert assignment.status == ChildAssignmentStatus.FENCED.value
        assert terminal.auth_token_sha256 is None
        assert terminal.write_enabled is False
        assert terminal.runtime_lifecycle == "recovery_fenced"
        assert db.get(WorktreeWriterLeaseModel, terminal.launch_worktree) is None
        wakes = db.query(InboxModel).filter_by(kind="managed_attempt_failure").all()
        assert len(wakes) == 1


def test_fenced_attempt_allows_exactly_one_replacement(attempt_db):
    """Regression 9: replacement registration is a one-time fenced CAS."""
    attempt = _create_attempt()
    _claim_and_complete_fence(attempt)
    _add_terminal("replacement-one", writer=True)
    replacement_effect = database.claim_workflow_effect(
        attempt["parent"], attempt["parent_turn"], "handoff", "replacement-one"
    )
    assert replacement_effect is not None
    assert database.register_handoff_child(
        attempt["parent"],
        "replacement-one",
        workflow_turn_id=attempt["parent_turn"],
        workflow_effect_id=replacement_effect["id"],
        request_message="Continue only the unfinished mission.",
        replaces_assignment_id=attempt["assignment_id"],
    )
    _add_terminal("replacement-two", writer=True)
    second_effect = database.claim_workflow_effect(
        attempt["parent"], attempt["parent_turn"], "handoff", "replacement-two"
    )
    assert second_effect is not None
    assert not database.register_handoff_child(
        attempt["parent"],
        "replacement-two",
        workflow_turn_id=attempt["parent_turn"],
        workflow_effect_id=second_effect["id"],
        request_message="Do not create a second replacement.",
        replaces_assignment_id=attempt["assignment_id"],
    )
    lifecycle = database.get_managed_attempt_lifecycle(assignment_id=attempt["assignment_id"])
    assert lifecycle["replacement_assignment_id"] is not None
    assert lifecycle["replacement_effect_id"] == replacement_effect["id"]


def test_late_callback_and_late_result_from_fenced_attempt_are_rejected(attempt_db):
    """Regressions 10 and 11: old bearer and result relation cannot revive a fence."""
    attempt = _create_attempt()
    _claim_and_complete_fence(attempt)
    document = HandoffResultDocumentV1(
        format="v1",
        summary="late result",
        body_markdown="This late result must not be accepted after fencing.",
        changed_files=[],
        checks=[],
        risks=[],
        blockers=[],
    )
    with pytest.raises(database.HandoffResultSubmissionError) as exc_info:
        database.submit_handoff_result_v1(LATE_TOKEN, attempt["child_turn"], document)
    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "invalid_terminal_auth"
    notice, duplicate = database.create_handoff_child_result_message(
        attempt["child"], "Late terminal-capture result."
    )
    assert notice is None
    assert duplicate is True
    with database.SessionLocal() as db:
        assert (
            db.get(ChildAssignmentModel, attempt["assignment_id"]).status
            == ChildAssignmentStatus.FENCED.value
        )


def test_restart_before_delivery_ack_preserves_truthful_state(attempt_db, monkeypatch):
    """Regression 13: restart cannot turn scheduling into delivery acknowledgement."""
    attempt = _create_attempt()
    monkeypatch.setattr(database, "_managed_attempt_lifecycle_schema_ready", False)
    lifecycle = database.get_managed_attempt_lifecycle(assignment_id=attempt["assignment_id"])
    assert lifecycle["state"] == "prompt_delivery_scheduled"
    assert lifecycle["prompt_delivery_acknowledged_at"] is None
    assert database.mark_managed_attempt_prompt_delivered(attempt["child"], attempt["child_turn"])
    assert (
        database.get_managed_attempt_lifecycle(assignment_id=attempt["assignment_id"])["state"]
        == "prompt_delivery_acknowledged"
    )


def test_restart_after_fence_claim_resumes_wake_without_duplicates(attempt_db, monkeypatch):
    """Regression 14: interrupted physical fence resumes and publishes one wake."""
    attempt = _create_attempt()
    claim = database.claim_managed_attempt_fence(**_owner_fence_kwargs(attempt))
    monkeypatch.setattr(database, "_managed_attempt_lifecycle_schema_ready", False)
    candidates = database.list_managed_attempt_fence_candidates()
    assert [candidate["assignment_id"] for candidate in candidates] == [attempt["assignment_id"]]
    completed = database.complete_managed_attempt_fence(
        attempt["assignment_id"], claim["fence_claim_token"], reason_code=FENCE_REASON
    )
    duplicate = database.complete_managed_attempt_fence(
        attempt["assignment_id"], claim["fence_claim_token"], reason_code=FENCE_REASON
    )
    assert completed["state"] == "fenced"
    assert duplicate["duplicate"] is True
    with database.SessionLocal() as db:
        assert db.query(InboxModel).filter_by(kind="managed_attempt_failure").count() == 1


def test_runtime_fence_saga_can_resume_after_physical_retirement_failure(attempt_db):
    """The physical phase is retryable while all logical authority stays revoked."""
    attempt = _create_attempt()
    metadata = {
        "id": attempt["child"],
        "tmux_session": "cao-synthetic-attempt",
        "tmux_window": f"window-{attempt['child']}",
        "runtime_pane_id": "%e",
        "runtime_pane_pid": 4100 + len(attempt["child"]),
        "runtime_generation": RUNTIME_GENERATION,
        "runtime_generation_origin": "launch",
        "runtime_process_start_ticks": 9000 + len(attempt["child"]),
        "runtime_process_group_id": 5100 + len(attempt["child"]),
        "runtime_process_session_id": 6100 + len(attempt["child"]),
        "provider": "codex",
    }
    with (
        patch.object(
            managed_attempt_service.terminal_service,
            "get_terminal_metadata",
            return_value=metadata,
        ),
        patch.object(
            managed_attempt_service,
            "_retire_claimed_runtime",
            side_effect=managed_attempt_service.ManagedAttemptFenceRuntimeError(
                "MANAGED_ATTEMPT_RUNTIME_RETIREMENT_TIMEOUT"
            ),
        ),
    ):
        interrupted = managed_attempt_service.fence_managed_attempt(
            **_owner_fence_kwargs(attempt), timeout=0
        )
    assert interrupted["accepted"] is False
    assert interrupted["state"] == "fence_claimed"
    with database.SessionLocal() as db:
        terminal = db.get(TerminalModel, attempt["child"])
        assert terminal.auth_token_sha256 is None
        assert terminal.write_enabled is False
        assert db.get(WorktreeWriterLeaseModel, terminal.launch_worktree) is None
    with (
        patch.object(
            managed_attempt_service.terminal_service,
            "get_terminal_metadata",
            return_value=metadata,
        ),
        patch.object(managed_attempt_service, "_retire_claimed_runtime"),
    ):
        resumed = managed_attempt_service.fence_managed_attempt(
            **_owner_fence_kwargs(attempt), timeout=0
        )
    assert resumed["accepted"] is True
    assert resumed["state"] == "fenced"


def test_runtime_fence_exactly_retires_after_provider_registry_loss():
    """A restart-lost provider object cannot strand an already logical fence."""
    target = MagicMock(
        current_command="codex",
        pane_id="%42",
    )
    metadata = {"id": "orphan", "tmux_session": "cao-synthetic", "tmux_window": "child"}
    with (
        patch.object(managed_attempt_service, "_validate_runtime_target", return_value=target),
        patch.object(managed_attempt_service.provider_manager, "get_provider", return_value=None),
        patch.object(
            managed_attempt_service,
            "_retire_recovery_runtime",
            side_effect=[(False, "RECOVERY_HEALTHY_RUNTIME_ACTIVE"), (True, None)],
        ),
        patch.object(
            managed_attempt_service.tmux_client, "retire_runtime_pane", return_value=True
        ) as retire,
        patch.object(managed_attempt_service.provider_manager, "cleanup_provider") as cleanup,
    ):
        managed_attempt_service._retire_claimed_runtime(metadata, timeout=0)
    retire.assert_called_once_with(target)
    cleanup.assert_called_once_with("orphan")


def test_duplicate_fence_is_idempotent(attempt_db):
    """Regression 15: repeated exact fencing is a stable success, never a 500."""
    attempt = _create_attempt()
    _claim_and_complete_fence(attempt)
    duplicate = database.claim_managed_attempt_fence(**_owner_fence_kwargs(attempt))
    assert duplicate["accepted"] is True
    assert duplicate["duplicate"] is True
    assert duplicate["state"] == "fenced"


def test_owner_mcp_fence_is_machine_readable_and_idempotent(attempt_db, monkeypatch):
    """The public owner primitive returns stable success for an exact replay."""
    attempt = _create_attempt()
    monkeypatch.setenv("CAO_TERMINAL_ID", attempt["parent"])
    metadata = {
        "id": attempt["child"],
        "tmux_session": "cao-synthetic-attempt",
        "tmux_window": f"window-{attempt['child']}",
        "runtime_pane_id": "%e",
        "runtime_pane_pid": 4100 + len(attempt["child"]),
        "runtime_generation": RUNTIME_GENERATION,
        "runtime_generation_origin": "launch",
        "runtime_process_start_ticks": 9000 + len(attempt["child"]),
        "runtime_process_group_id": 5100 + len(attempt["child"]),
        "runtime_process_session_id": 6100 + len(attempt["child"]),
        "provider": "codex",
    }
    admitted_effect = {"id": 999, "claim_token": "owner-fence-effect"}
    with (
        patch.object(
            mcp_server,
            "_claim_privileged_effect",
            side_effect=[admitted_effect, None],
        ),
        patch.object(mcp_server, "_finish_privileged_effect") as finish,
        patch.object(
            managed_attempt_service.terminal_service,
            "get_terminal_metadata",
            return_value=metadata,
        ),
        patch.object(managed_attempt_service, "_retire_claimed_runtime"),
    ):
        first = asyncio.run(mcp_server.fence_managed_attempt(3591, **_fence_kwargs(attempt)))
        duplicate = asyncio.run(mcp_server.fence_managed_attempt(3591, **_fence_kwargs(attempt)))
    assert first["success"] is True and first["state"] == "fenced", first
    assert duplicate["success"] is True and duplicate["duplicate"] is True
    finish.assert_called_once_with(admitted_effect, "completed")


def test_cross_terminal_owner_requires_exact_gated_parent_scope(attempt_db):
    """A project owner cannot inspect or fence another still-running parent."""
    attempt = _create_attempt()
    _add_terminal("recovery-owner", owner=True)
    assert not database.managed_attempt_fence_caller_is_authorized(
        "recovery-owner",
        assignment_id=attempt["assignment_id"],
        parent_terminal_id=attempt["parent"],
        reason_code=FENCE_REASON,
    )
    assert database.set_workflow_terminal_state(
        attempt["parent"], database.WORKFLOW_OWNER_GATE, FENCE_REASON
    )
    assert database.managed_attempt_fence_caller_is_authorized(
        "recovery-owner",
        assignment_id=attempt["assignment_id"],
        parent_terminal_id=attempt["parent"],
        reason_code=FENCE_REASON,
    )
    _add_terminal("foreign-owner", owner=True, project_id="foreign-project")
    assert not database.managed_attempt_fence_caller_is_authorized(
        "foreign-owner",
        assignment_id=attempt["assignment_id"],
        parent_terminal_id=attempt["parent"],
        reason_code=FENCE_REASON,
    )
    _add_terminal("legacy-owner", owner=True)
    with database.SessionLocal() as db:
        db.get(TerminalModel, "legacy-owner").profile_revision_id = None
        db.commit()
    assert not database.managed_attempt_fence_caller_is_authorized(
        "legacy-owner",
        assignment_id=attempt["assignment_id"],
        parent_terminal_id=attempt["parent"],
        reason_code=FENCE_REASON,
    )


def test_owner_gate_terminalizes_timeout_and_suppresses_fence_wake(attempt_db):
    """An explicit owner gate cannot be undone by timeout or physical fencing."""
    attempt = _create_attempt()
    assert database.set_workflow_terminal_state(
        attempt["parent"], database.WORKFLOW_OWNER_GATE, FENCE_REASON
    )
    lifecycle = database.get_managed_attempt_lifecycle(assignment_id=attempt["assignment_id"])
    assert lifecycle["state"] == "failed"
    assert lifecycle["reason_code"] == "PARENT_WORKFLOW_OWNER_GATE"
    assert database.reconcile_managed_attempt_timeouts(datetime.now() + timedelta(days=1)) == {
        "recovery_scheduled": 0,
        "fence_pending": 0,
    }
    claim = database.claim_managed_attempt_fence(**_owner_fence_kwargs(attempt))
    completed = database.complete_managed_attempt_fence(
        attempt["assignment_id"],
        claim["fence_claim_token"],
        reason_code=FENCE_REASON,
    )
    assert completed["state"] == "fenced"
    assert completed["parent_wake_message_id"] is None
    with database.SessionLocal() as db:
        assert db.query(InboxModel).filter_by(kind="managed_attempt_failure").count() == 0
        workflows = db.query(WorkflowModel).filter_by(root_terminal_id=attempt["parent"]).all()
        assert [workflow.status for workflow in workflows] == [database.WORKFLOW_OWNER_GATE]


def test_timeout_reports_inactive_assignment_without_blame_on_open_parent(attempt_db):
    """A stale assignment edge fails explicitly without a false parent reason."""
    attempt = _create_attempt()
    with database.SessionLocal() as db:
        assignment = db.get(ChildAssignmentModel, attempt["assignment_id"])
        assignment.status = ChildAssignmentStatus.CANCELLED.value
        lifecycle = db.get(ManagedAttemptLifecycleModel, attempt["assignment_id"])
        lifecycle.recovery_deadline_at = datetime.now() - timedelta(seconds=1)
        db.commit()

    assert database.reconcile_managed_attempt_timeouts()["recovery_scheduled"] == 0
    lifecycle = database.get_managed_attempt_lifecycle(assignment_id=attempt["assignment_id"])
    assert lifecycle["state"] == "failed"
    assert lifecycle["reason_code"] == "MANAGED_ATTEMPT_ASSIGNMENT_CANCELLED"


def test_legacy_unreceipted_attempt_gets_bounded_upgrade_deadline(attempt_db, monkeypatch):
    """Upgrade backfill cannot leave an old active attempt inert forever."""
    attempt = _create_attempt()
    with database.SessionLocal() as db:
        db.query(ManagedAttemptLifecycleEventModel).filter_by(
            assignment_id=attempt["assignment_id"]
        ).delete()
        db.query(ManagedAttemptLifecycleModel).filter_by(
            assignment_id=attempt["assignment_id"]
        ).delete()
        db.commit()
    monkeypatch.setattr(database, "_managed_attempt_lifecycle_schema_ready", False)
    lifecycle = database.get_managed_attempt_lifecycle(assignment_id=attempt["assignment_id"])
    assert lifecycle["state"] == "prompt_delivery_scheduled"
    assert lifecycle["reason_code"] == "LEGACY_PROMPT_ADMISSION_UNCONFIRMED"
    assert lifecycle["recovery_deadline_at"] is not None
    assert lifecycle["provider_admitted_at"] is None


def test_legacy_receipted_attempt_backfills_as_admitted(attempt_db, monkeypatch):
    """Upgrade backfill does not retry a legacy child with a durable receipt."""
    attempt = _create_attempt()
    assert database.claim_workflow_turn_receipt(attempt["child"], attempt["child_turn"])
    with database.SessionLocal() as db:
        db.query(ManagedAttemptLifecycleEventModel).filter_by(
            assignment_id=attempt["assignment_id"]
        ).delete()
        db.query(ManagedAttemptLifecycleModel).filter_by(
            assignment_id=attempt["assignment_id"]
        ).delete()
        db.commit()
    monkeypatch.setattr(database, "_managed_attempt_lifecycle_schema_ready", False)
    lifecycle = database.get_managed_attempt_lifecycle(assignment_id=attempt["assignment_id"])
    assert lifecycle["state"] == "waiting_for_result"
    assert lifecycle["provider_admitted_at"] is not None
    assert lifecycle["recovery_deadline_at"] is None


def test_wrong_assignment_or_effect_identity_cannot_fence(attempt_db):
    """Regression 16: every immutable assignment/effect identity is compared."""
    attempt = _create_attempt()
    for overrides in (
        {"attempt_id": "wrong-attempt-identity"},
        {"request_workflow_effect_id": attempt["effect_id"] + 1},
    ):
        with pytest.raises(ManagedAttemptFenceError) as exc_info:
            database.claim_managed_attempt_fence(**_owner_fence_kwargs(attempt, **overrides))
        assert exc_info.value.reason_code == "MANAGED_ATTEMPT_IDENTITY_MISMATCH"
    assert (
        database.get_managed_attempt_lifecycle(assignment_id=attempt["assignment_id"])["state"]
        == "prompt_delivery_scheduled"
    )


def test_foreign_writer_authority_is_untouched(attempt_db):
    """Regression 17: a changed/foreign writer fails closed without mutation."""
    attempt = _create_attempt()
    with database.SessionLocal() as db:
        terminal = db.get(TerminalModel, attempt["child"])
        writer = db.get(WorktreeWriterLeaseModel, terminal.launch_worktree)
        writer.terminal_id = "foreign-terminal"
        db.commit()
    with pytest.raises(ManagedAttemptFenceError) as exc_info:
        database.claim_managed_attempt_fence(**_owner_fence_kwargs(attempt))
    assert exc_info.value.reason_code == "MANAGED_ATTEMPT_FOREIGN_WRITER_AUTHORITY"
    with database.SessionLocal() as db:
        terminal = db.get(TerminalModel, attempt["child"])
        writer = db.get(WorktreeWriterLeaseModel, terminal.launch_worktree)
        assert writer.terminal_id == "foreign-terminal"
        assert terminal.write_enabled is True


def test_admitted_result_completes_without_replacement(attempt_db):
    """Healthy control: delivery ack -> admission -> result, with no replacement."""
    attempt = _create_attempt()
    assert database.mark_managed_attempt_prompt_delivered(attempt["child"], attempt["child_turn"])
    assert database.claim_workflow_turn_receipt(attempt["child"], attempt["child_turn"])
    document = HandoffResultDocumentV1(
        format="v1",
        summary="synthetic success",
        body_markdown="The admitted synthetic child returned a substantive result.",
        changed_files=[],
        checks=[],
        risks=[],
        blockers=[],
    )
    submitted = database.submit_handoff_result_v1(LATE_TOKEN, attempt["child_turn"], document)
    assert submitted["accepted"] is True
    lifecycle = database.get_managed_attempt_lifecycle(assignment_id=attempt["assignment_id"])
    assert lifecycle["state"] == "completed"
    assert lifecycle["replacement_assignment_id"] is None


def test_queue_badge_matches_drawer_and_terminal_history_is_not_current(attempt_db):
    """Regressions 18-20: one projection owns badge/current/history truth."""
    terminal_id = "old-discovery"
    session_id = "old-discovery-session"
    _add_terminal(terminal_id, session_id=session_id)
    now = datetime.now()
    with database.SessionLocal() as db:
        workflow = WorkflowModel(
            root_terminal_id=terminal_id,
            status=database.WORKFLOW_TERMINAL,
            terminal_reason="DISCOVERY.P1 completed",
            created_at=now,
            updated_at=now,
        )
        db.add(workflow)
        db.flush()
        turn = WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="external_input",
            dedupe_key="discovery-p1",
            payload="DISCOVERY.P1",
            state=database.TURN_FINISHED,
            created_at=now,
            updated_at=now,
        )
        db.add(turn)
        db.flush()
        workflow.active_turn_id = turn.id
        db.add(
            WorkflowEffectModel(
                workflow_id=workflow.id,
                workflow_turn_id=turn.id,
                effect_kind="complete_workflow",
                effect_key="discovery-p1-complete",
                state="completed",
                claim_token="synthetic-complete-claim",
                created_at=now,
                updated_at=now,
            )
        )
        db.commit()
    current = interaction_read_model_service.list_interactions(session_id, mode="current", limit=20)
    history = interaction_read_model_service.list_interactions(session_id, mode="history", limit=20)
    badge = interaction_read_model_service.list_session_current_queue_counts([session_id])
    assert badge[session_id] == current["total"] == len(current["items"]) == 0
    assert any(item["workflow"]["reason"] == "DISCOVERY.P1 completed" for item in history["items"])
    assert all(item["current"] is False for item in history["items"])
