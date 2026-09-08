"""Permanent Session deletion, replay fencing, and graph-ownership regressions."""

from __future__ import annotations

import hashlib
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.database import (
    Base,
    ChildAssignmentModel,
    DelegationResultEventModel,
    DelegationResultModel,
    DelegationResultSubmissionModel,
    HandoffResultSubmissionError,
    InboxModel,
    OwnerLaunchGrantModel,
    ProjectModel,
    ProviderExecutionLeaseModel,
    ProviderUsageBindingModel,
    SessionDeletionCancellationAuditModel,
    SessionDeletionOperationModel,
    SessionDeletionReceiptModel,
    TelegramDeliveryModel,
    TerminalDeletionReceiptModel,
    TerminalModel,
    UsageRecordModel,
    WorkflowEffectModel,
    WorkflowModel,
    WorkflowProviderReconnectAttemptModel,
    WorkflowTurnModel,
    WorkflowTurnReceiptModel,
    WorktreeWriterLeaseModel,
    WritableWorkContextAuditModel,
    WritableWorkContextConflict,
    WritableWorkContextModel,
)
from cli_agent_orchestrator.models.inbox import ChildAssignmentStatus, MessageStatus
from cli_agent_orchestrator.models.result import HandoffResultDocumentV1
from cli_agent_orchestrator.models.usage import UsageObservation
from cli_agent_orchestrator.services import interaction_read_model_service, managed_worktree_service


def _install_database(monkeypatch, url: str = "sqlite:///:memory:"):
    engine = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    for name in (
        "_ensure_terminal_worktree_authority_schema",
        "_ensure_workflow_schema",
        "_ensure_child_assignment_schema",
        "_ensure_delegation_result_schema",
        "_ensure_provider_execution_schema",
        "_ensure_session_deletion_receipt_schema",
        "_ensure_terminal_deletion_receipt_schema",
        "_ensure_terminal_ui_projection_schema",
        "_ensure_control_plane_schema",
        "_ensure_usage_schema",
    ):
        monkeypatch.setattr(database, name, lambda: None)
    return engine


def _terminal(
    terminal_id: str,
    *,
    session_id: str = "session",
    session_name: str = "cao-session",
    token: str | None = None,
) -> TerminalModel:
    return TerminalModel(
        id=terminal_id,
        tmux_session=session_name,
        session_id=session_id,
        tmux_window=terminal_id,
        provider="codex",
        runtime_lifecycle="exited",
        auth_token_sha256=(
            hashlib.sha256(token.encode("utf-8", "strict")).hexdigest() if token else None
        ),
        last_active=datetime(2026, 9, 8, 10, 0, 0),
    )


def _fence_and_mark(
    session_id: str = "session",
    session_name: str = "cao-session",
    terminal_ids: tuple[str, ...] = ("owner",),
):
    started = database.begin_session_hard_deletion(
        session_id,
        session_name,
        expected_terminal_ids=terminal_ids,
        allow_dirty_workspace=False,
    )
    assert started["started"] is True
    marked = database.mark_session_hard_deletion_workspace_retired(
        session_id,
        workspace_evidence=[
            {
                "terminal_id": terminal_id,
                "managed": False,
                "path_absent": True,
                "git_unregistered": True,
                "branch_absent": True,
                "runtime_artifacts_absent": True,
            }
            for terminal_id in started.get("graph_terminal_ids", terminal_ids)
        ],
    )
    assert marked["marked"] is True
    return started


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_individually_retired_private_branch_is_receipted_then_session_purged(
    monkeypatch, tmp_path
):
    _install_database(monkeypatch)
    repository = tmp_path / "source"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "ThreadCells Test")
    _git(repository, "config", "user.email", "threadcells@example.invalid")
    (repository / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-qm", "baseline")
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")
    managed = managed_worktree_service.create_managed_worktree(
        str(repository), "retired-child", "task"
    )
    assert managed is not None and managed.branch is not None

    owner = _terminal("owner")
    child = _terminal("retired-child")
    child.launch_worktree = managed.path
    child.managed_worktree_kind = managed.kind
    child.managed_worktree_source = managed.source
    child.managed_worktree_branch = managed.branch
    child.managed_worktree_commit = managed.commit
    with database.SessionLocal() as db:
        db.add_all([owner, child])
        db.commit()
        expected_identity = {
            field: getattr(child, field) for field in database._TERMINAL_DELETION_IDENTITY_FIELDS
        }

    metadata = {
        "id": "retired-child",
        "launch_worktree": managed.path,
        "managed_worktree_kind": managed.kind,
        "managed_worktree_source": managed.source,
        "managed_worktree_branch": managed.branch,
        "managed_worktree_commit": managed.commit,
    }
    removed = managed_worktree_service.remove_managed_worktree(metadata)
    assert removed["removed"] is True
    deleted = database.delete_exited_terminal(
        "retired-child",
        expected_identity=expected_identity,
        workspace_cleanup_authority={
            "version": 1,
            "managed": True,
            "kind": managed.kind,
            "source": managed.source,
            "path": managed.path,
            "branch": managed.branch,
            "branch_object_id": removed["commit"],
            "identity": "retired-child",
            "path_absent": True,
            "git_unregistered": True,
        },
    )
    assert deleted["deleted"] == 1
    assert _git(repository, "rev-parse", f"refs/heads/{managed.branch}") == removed["commit"]

    started = database.begin_session_hard_deletion(
        "session",
        "cao-session",
        expected_terminal_ids=["owner"],
        allow_dirty_workspace=False,
    )
    assert started["started"] is True
    cleanup_authorities = database.list_session_historical_terminal_cleanup_authorities("session")
    assert cleanup_authorities[0]["managed_worktree_branch_object_id"] == removed["commit"]
    cleanup = managed_worktree_service.purge_managed_worktree(
        cleanup_authorities[0], require_already_absent=True
    )
    assert cleanup["removed"] is True
    assert cleanup["branch_absent"] is True
    assert not Path(managed.path).exists()
    assert managed.path not in _git(repository, "worktree", "list", "--porcelain")
    database.mark_session_hard_deletion_workspace_retired(
        "session",
        workspace_evidence=[
            {
                "terminal_id": "owner",
                "managed": False,
                "path_absent": True,
                "git_unregistered": True,
                "branch_absent": True,
                "runtime_artifacts_absent": True,
            },
            {
                "terminal_id": "retired-child",
                "managed": True,
                "path_absent": True,
                "git_unregistered": True,
                "branch_absent": True,
                "runtime_artifacts_absent": True,
            },
        ],
    )
    completed = database.complete_session_hard_deletion("session", "cao-session")
    assert completed["completed"] is True
    with database.SessionLocal() as db:
        receipt = db.get(TerminalDeletionReceiptModel, "retired-child")
        assert receipt.workspace_cleanup_authority_version is None
        assert receipt.managed_worktree_branch is None
        assert db.get(SessionDeletionReceiptModel, "session").receipt_version == 2


def test_individually_retired_terminal_graph_is_planned_projected_and_hard_purged(
    monkeypatch,
):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add(_terminal("owner"))
        db.add(
            TerminalDeletionReceiptModel(
                terminal_id="retired-child",
                session_id="session",
                session_name="cao-session",
                window_name="retired-child",
                auth_token_sha256="c" * 64,
                workspace_cleanup_authority_version=1,
                deleted_at=datetime(2026, 9, 8, 9, 30, 0),
            )
        )
        workflow = WorkflowModel(root_terminal_id="retired-child", status="terminal")
        db.add(workflow)
        db.flush()
        turn = WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="external_input",
            dedupe_key="retired-child-unknown",
            state="finished",
        )
        db.add(turn)
        db.flush()
        effect = WorkflowEffectModel(
            workflow_id=workflow.id,
            workflow_turn_id=turn.id,
            effect_kind="send_message",
            effect_key="retired-child-unknown",
            state="indeterminate",
            claim_token="retired-child-claim",
        )
        db.add(effect)
        assignment = ChildAssignmentModel(
            parent_terminal_id="owner",
            child_terminal_id="retired-child",
            status=ChildAssignmentStatus.RESULT_ACKNOWLEDGED.value,
            request_workflow_id=workflow.id,
            request_workflow_turn_id=turn.id,
        )
        db.add(assignment)
        db.flush()
        db.add(
            DelegationResultModel(
                id="retired-child-result",
                child_assignment_id=assignment.id,
                delegation_kind="assign",
                parent_terminal_id="owner",
                child_terminal_id="retired-child",
                parent_workflow_id=workflow.id,
                workflow_turn_id=turn.id,
                authorship="child",
                status="complete",
                document_json='{"summary":"durable child result"}',
            )
        )
        db.commit()

    plan = database.get_session_unresolved_work_plan("session", expected_terminal_ids=["owner"])
    assert plan["deletion_mode"] == "eligible_with_historical_indeterminate_retirement"
    assert plan["historical_indeterminate_count"] == 1
    current = interaction_read_model_service.list_interactions("session", mode="current")
    assert current["total"] == 1
    assert current["items"][0]["workflow"]["effect_state"] == "indeterminate"

    retired = database.cancel_session_work_for_deletion(
        "session",
        expected_plan_token=plan["plan_token"],
        expected_terminal_ids=["owner"],
        cancel_unresolved_work=False,
        retire_historical_indeterminate=True,
    )
    assert retired["cancelled"] is True
    assert retired["retired_indeterminate_count"] == 1
    assert interaction_read_model_service.list_interactions("session", mode="current")["total"] == 0

    started = _fence_and_mark()
    assert started["terminal_ids"] == ["owner"]
    assert started["graph_terminal_ids"] == ["owner", "retired-child"]
    completed = database.complete_session_hard_deletion("session", "cao-session")
    assert completed["completed"] is True
    assert completed["terminal_fence_count"] == 2
    assert completed["before_counts"]["terminals"] == 1
    assert completed["before_counts"]["workflows"] == 1
    assert completed["before_counts"]["delegation_results"] == 1
    assert all(count == 0 for count in completed["after_counts"].values())
    with database.SessionLocal() as db:
        assert db.query(TerminalDeletionReceiptModel).count() == 2
        retired_receipt = db.get(TerminalDeletionReceiptModel, "retired-child")
        assert retired_receipt.workspace_cleanup_authority_version is None
        assert retired_receipt.managed_worktree_source is None
        assert retired_receipt.managed_worktree_branch_object_id is None
        assert db.query(WorkflowModel).count() == 0
        assert db.query(WorkflowEffectModel).count() == 0
        assert db.query(DelegationResultModel).count() == 0


def test_legacy_terminal_receipt_without_workspace_cleanup_authority_fails_closed(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add(_terminal("owner"))
        db.add(
            TerminalDeletionReceiptModel(
                terminal_id="legacy-retired-child",
                session_id="session",
                session_name="cao-session",
                window_name="legacy-retired-child",
                deleted_at=datetime(2026, 9, 8, 9, 30, 0),
            )
        )
        db.commit()

    plan = database.get_session_unresolved_work_plan("session", expected_terminal_ids=["owner"])
    assert plan["deletion_mode"] == "blocked_live_or_unsafe_authority"
    assert plan["reason_codes"] == ["TERMINAL_WORKSPACE_CLEANUP_AUTHORITY_MISSING"]
    assert database.begin_session_hard_deletion(
        "session",
        "cao-session",
        expected_terminal_ids=["owner"],
        allow_dirty_workspace=False,
    ) == {
        "started": False,
        "reason_code": "TERMINAL_WORKSPACE_CLEANUP_AUTHORITY_MISSING",
    }


def test_hard_delete_purges_owned_graph_and_preserves_shared_registry_and_other_session(
    monkeypatch,
):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        owner = _terminal("owner")
        child = _terminal("child")
        other = _terminal("other", session_id="other-session", session_name="cao-other")
        db.add_all([owner, child, other])
        db.add(
            ProjectModel(
                id="project",
                name="Project",
                normalized_name="project",
                path="/source/project",
                normalized_path="/source/project",
                is_default=True,
            )
        )
        workflow = WorkflowModel(root_terminal_id="owner", status="terminal")
        other_workflow = WorkflowModel(root_terminal_id="other", status="terminal")
        db.add_all([workflow, other_workflow])
        db.flush()
        other_workflow_id = int(other_workflow.id)
        other_turn = WorkflowTurnModel(
            workflow_id=other_workflow.id,
            kind="external_input",
            dedupe_key="other-done",
            state="finished",
        )
        db.add(other_turn)
        db.flush()
        other_effect = WorkflowEffectModel(
            workflow_id=other_workflow.id,
            workflow_turn_id=other_turn.id,
            effect_kind="send_message",
            effect_key="other-done",
            state="completed",
            claim_token="other-effect-claim",
        )
        db.add(other_effect)
        db.flush()
        other_assignment = ChildAssignmentModel(
            parent_terminal_id="other",
            child_terminal_id="other",
            status=ChildAssignmentStatus.RESULT_ACKNOWLEDGED.value,
            request_workflow_id=other_workflow.id,
            request_workflow_turn_id=other_turn.id,
            request_workflow_effect_id=other_effect.id,
        )
        db.add(other_assignment)
        db.flush()
        other_result = DelegationResultModel(
            id="other-result",
            child_assignment_id=other_assignment.id,
            delegation_kind="assign",
            parent_terminal_id="other",
            child_terminal_id="other",
            parent_workflow_id=other_workflow.id,
            workflow_turn_id=other_turn.id,
            workflow_effect_id=other_effect.id,
            authorship="child",
            status="complete",
            document_json='{"summary":"other"}',
        )
        db.add(other_result)
        db.flush()
        other_turn_id = int(other_turn.id)
        other_effect_id = int(other_effect.id)
        other_assignment_id = int(other_assignment.id)
        # These intentionally inconsistent redundant provenance fields prove
        # purge ownership follows the primary graph relation rather than a
        # coincidental reference to the deleted Session.
        db.add_all(
            [
                WorkflowTurnReceiptModel(
                    workflow_turn_id=other_turn.id,
                    receiver_terminal_id="owner",
                ),
                WorkflowProviderReconnectAttemptModel(
                    workflow_id=other_workflow.id,
                    workflow_turn_id=other_turn.id,
                    root_terminal_id="owner",
                    attempt_number=1,
                    attempt_token="other-reconnect",
                    state="completed",
                ),
                TelegramDeliveryModel(
                    event_key="other-telegram",
                    event_kind="workflow_terminal",
                    workflow_id=other_workflow.id,
                    root_terminal_id="owner",
                    state="delivered",
                ),
                InboxModel(
                    sender_id="owner",
                    receiver_id="other",
                    message="other result",
                    status=MessageStatus.DELIVERED.value,
                    result_id="other-result",
                    kind="delegation_result_notice",
                ),
                DelegationResultEventModel(
                    result_id="other-result",
                    event_key="other-result:complete",
                    event_type="completed",
                    actor_kind="child",
                    actor_terminal_id="owner",
                    workflow_turn_id=other_turn.id,
                ),
                DelegationResultSubmissionModel(
                    result_id="other-result",
                    child_terminal_id="other",
                    workflow_turn_id=other_turn.id,
                    workflow_effect_id=other_effect.id,
                    document_json='{"summary":"other"}',
                    content_sha256="d" * 64,
                    content_bytes=19,
                ),
            ]
        )
        turn = WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="external_input",
            dedupe_key="done",
            state="finished",
        )
        db.add(turn)
        db.flush()
        receipt = WorkflowTurnReceiptModel(
            workflow_turn_id=turn.id,
            receiver_terminal_id="owner",
        )
        effect = WorkflowEffectModel(
            workflow_id=workflow.id,
            workflow_turn_id=turn.id,
            effect_kind="send_message",
            effect_key="done",
            state="completed",
            claim_token="effect-claim",
        )
        db.add_all([receipt, effect])
        db.flush()
        assignment = ChildAssignmentModel(
            parent_terminal_id="owner",
            child_terminal_id="child",
            status=ChildAssignmentStatus.RESULT_ACKNOWLEDGED.value,
            request_workflow_id=workflow.id,
            request_workflow_turn_id=turn.id,
            request_workflow_effect_id=effect.id,
        )
        db.add(assignment)
        db.flush()
        result = DelegationResultModel(
            id="result",
            child_assignment_id=assignment.id,
            delegation_kind="assign",
            parent_terminal_id="owner",
            child_terminal_id="child",
            parent_workflow_id=workflow.id,
            workflow_turn_id=turn.id,
            workflow_effect_id=effect.id,
            authorship="child",
            status="complete",
            document_json='{"summary":"owned"}',
        )
        db.add(result)
        db.flush()
        db.add_all(
            [
                InboxModel(
                    sender_id="child",
                    receiver_id="owner",
                    message="result",
                    status=MessageStatus.DELIVERED.value,
                    result_id="result",
                    kind="delegation_result_notice",
                ),
                DelegationResultEventModel(
                    result_id="result",
                    event_key="result:complete",
                    event_type="completed",
                    actor_kind="child",
                    actor_terminal_id="child",
                    workflow_turn_id=turn.id,
                ),
                DelegationResultSubmissionModel(
                    result_id="result",
                    child_terminal_id="child",
                    workflow_turn_id=turn.id,
                    workflow_effect_id=effect.id,
                    document_json='{"summary":"owned"}',
                    content_sha256="a" * 64,
                    content_bytes=19,
                ),
                WorkflowProviderReconnectAttemptModel(
                    workflow_id=workflow.id,
                    workflow_turn_id=turn.id,
                    root_terminal_id="owner",
                    attempt_number=1,
                    attempt_token="reconnect",
                    state="completed",
                ),
                TelegramDeliveryModel(
                    event_key="telegram",
                    event_kind="workflow_terminal",
                    workflow_id=workflow.id,
                    root_terminal_id="owner",
                    state="delivered",
                ),
                ProviderUsageBindingModel(
                    provider="codex",
                    provider_session_id="provider-session",
                    terminal_id="owner",
                    source="hook",
                ),
                UsageRecordModel(
                    source_run_identity="usage",
                    extractor="hook",
                    terminal_id="owner",
                    session_id="session",
                ),
                OwnerLaunchGrantModel(
                    id="grant",
                    token_sha256="b" * 64,
                    launch_id="launch",
                    agent_profile="cao_owner",
                    provider="codex",
                    canonical_worktree="/work/session",
                    issued_by="owner",
                    created_at=datetime(2026, 9, 8, 9, 0, 0),
                    expires_at=datetime(2026, 9, 9, 9, 0, 0),
                    consumed_at=datetime(2026, 9, 8, 9, 1, 0),
                    consumed_terminal_id="owner",
                ),
                SessionDeletionCancellationAuditModel(
                    event_key="cancelled-before-purge",
                    session_id="session",
                    item_kind="workflow",
                    item_id=str(workflow.id),
                    previous_state="open",
                    final_state="cancelled",
                    reason_code="operator_session_delete",
                ),
                WritableWorkContextModel(
                    id="context",
                    request_id="request",
                    project_id="project",
                    session_id="session",
                    terminal_id="owner",
                    canonical_source="/source/project",
                    canonical_worktree="/work/session",
                    branch="cao/session/context",
                    base_revision="c" * 40,
                    state="retired",
                ),
                WritableWorkContextAuditModel(
                    work_context_id="context",
                    event_key="context:retired",
                    event_type="managed_worktree_retired",
                    terminal_id="owner",
                ),
            ]
        )
        db.commit()

    _fence_and_mark(terminal_ids=("owner", "child"))
    completed = database.complete_session_hard_deletion("session", "cao-session")

    assert completed["completed"] is True
    assert completed["already_deleted"] is False
    assert completed["tombstone_count"] == 1
    assert completed["terminal_fence_count"] == 2
    assert completed["before_counts"] == {
        "terminals": 2,
        "workflows": 1,
        "workflow_turns": 1,
        "workflow_turn_receipts": 1,
        "workflow_effects": 1,
        "workflow_provider_reconnect_attempts": 1,
        "provider_execution_leases": 0,
        "worktree_writer_leases": 0,
        "child_assignments": 1,
        "delegation_results": 1,
        "delegation_result_submissions": 1,
        "delegation_result_events": 1,
        "inbox": 1,
        "provider_usage_bindings": 1,
        "usage_records": 1,
        "owner_launch_grants": 1,
        "recovery_takeovers": 0,
        "recovery_takeover_audit": 0,
        "telegram_notification_deliveries": 1,
        "writable_work_contexts": 1,
        "writable_work_context_audit": 1,
        "session_deletion_cancellation_audit": 1,
    }
    assert all(count == 0 for count in completed["after_counts"].values())
    with database.SessionLocal() as db:
        assert db.query(ProjectModel).count() == 1
        assert db.get(TerminalModel, "other") is not None
        assert db.get(WorkflowModel, other_workflow_id) is not None
        assert db.get(WorkflowTurnModel, other_turn_id) is not None
        assert db.get(WorkflowEffectModel, other_effect_id) is not None
        assert db.get(ChildAssignmentModel, other_assignment_id) is not None
        assert db.get(DelegationResultModel, "other-result") is not None
        assert db.query(WorkflowTurnReceiptModel).count() == 1
        assert db.query(WorkflowProviderReconnectAttemptModel).count() == 1
        assert db.query(TelegramDeliveryModel).count() == 1
        assert db.query(InboxModel).count() == 1
        assert db.query(DelegationResultEventModel).count() == 1
        assert db.query(DelegationResultSubmissionModel).count() == 1
        assert db.query(SessionDeletionOperationModel).count() == 0
        tombstone = db.get(SessionDeletionReceiptModel, "session")
        assert tombstone is not None
        assert tombstone.receipt_version == 2
        assert tombstone.deletion_reason == "operator_session_hard_delete"
        assert tombstone.retained_resources_json == "[]"
        assert db.query(TerminalDeletionReceiptModel).count() == 2


def test_historical_indeterminate_is_fenced_then_purged_without_result_fabrication(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add(_terminal("owner"))
        workflow = WorkflowModel(root_terminal_id="owner", status="terminal")
        db.add(workflow)
        db.flush()
        turn = WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="external_input",
            dedupe_key="unknown",
            state="finished",
        )
        db.add(turn)
        db.flush()
        effect = WorkflowEffectModel(
            workflow_id=workflow.id,
            workflow_turn_id=turn.id,
            effect_kind="send_message",
            effect_key="unknown",
            state="indeterminate",
            claim_token="unknown-claim",
        )
        db.add(effect)
        db.commit()
        effect_id = effect.id

    plan = database.get_session_unresolved_work_plan("session", expected_terminal_ids=["owner"])
    assert plan["historical_indeterminate_count"] == 1
    retired = database.cancel_session_work_for_deletion(
        "session",
        expected_plan_token=plan["plan_token"],
        expected_terminal_ids=["owner"],
        cancel_unresolved_work=False,
        retire_historical_indeterminate=True,
    )
    assert retired["retired_indeterminate_count"] == 1
    with database.SessionLocal() as db:
        assert db.get(WorkflowEffectModel, effect_id).state == "operator_retired_indeterminate"
        assert db.query(DelegationResultModel).count() == 0

    _fence_and_mark()
    assert database.complete_session_hard_deletion("session", "cao-session")["completed"] is True
    with database.SessionLocal() as db:
        assert db.query(WorkflowEffectModel).count() == 0
        assert db.query(DelegationResultModel).count() == 0
        assert db.query(SessionDeletionCancellationAuditModel).count() == 0


@pytest.mark.parametrize(
    "authority",
    ["provider", "writer", "reconnect", "runtime_operation", "claimed_effect", "runtime"],
)
def test_hard_delete_fence_rechecks_live_authority(monkeypatch, authority):
    _install_database(monkeypatch)
    terminal = _terminal("owner")
    with database.SessionLocal() as db:
        db.add(terminal)
        db.flush()
        if authority == "provider":
            db.add(ProviderExecutionLeaseModel(terminal_id="owner", workflow_turn_id=41))
        elif authority == "writer":
            db.add(
                WorktreeWriterLeaseModel(canonical_worktree="/work/session", terminal_id="owner")
            )
        elif authority == "runtime_operation":
            terminal.runtime_operation_kind = "provider_restart"
            terminal.runtime_operation_token = "runtime-claim"
        elif authority == "runtime":
            terminal.runtime_lifecycle = "running"
        elif authority == "claimed_effect":
            workflow = WorkflowModel(root_terminal_id="owner", status="open")
            db.add(workflow)
            db.flush()
            turn = WorkflowTurnModel(
                workflow_id=workflow.id,
                kind="external_input",
                dedupe_key="claimed-effect",
                state="claimed",
            )
            db.add(turn)
            db.flush()
            workflow.active_turn_id = turn.id
            db.add(
                WorkflowEffectModel(
                    workflow_id=workflow.id,
                    workflow_turn_id=turn.id,
                    effect_kind="send_message",
                    effect_key="claimed",
                    state="claimed",
                    claim_token="live-effect-claim",
                )
            )
        else:
            workflow = WorkflowModel(root_terminal_id="owner", status="terminal")
            db.add(workflow)
            db.flush()
            turn = WorkflowTurnModel(
                workflow_id=workflow.id,
                kind="external_input",
                dedupe_key="reconnect",
                state="finished",
            )
            db.add(turn)
            db.flush()
            db.add(
                WorkflowProviderReconnectAttemptModel(
                    workflow_id=workflow.id,
                    workflow_turn_id=turn.id,
                    root_terminal_id="owner",
                    attempt_number=1,
                    attempt_token="live-reconnect",
                    state="runtime_ready",
                )
            )
        db.commit()

    started = database.begin_session_hard_deletion(
        "session",
        "cao-session",
        expected_terminal_ids=["owner"],
        allow_dirty_workspace=False,
    )
    assert started["started"] is False
    with database.SessionLocal() as db:
        assert db.query(SessionDeletionOperationModel).count() == 0
        assert db.get(TerminalModel, "owner") is not None


def test_new_authority_after_fence_aborts_final_purge_and_preserves_graph(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add(_terminal("owner"))
        db.commit()
    _fence_and_mark()
    with database.SessionLocal() as db:
        db.add(ProviderExecutionLeaseModel(terminal_id="owner", workflow_turn_id=99))
        db.commit()

    assert database.revalidate_session_hard_deletion("session", "cao-session") == {
        "valid": False,
        "reason_code": "SESSION_DELETE_PLAN_CHANGED",
    }
    completed = database.complete_session_hard_deletion("session", "cao-session")
    assert completed == {"completed": False, "reason_code": "SESSION_DELETE_PLAN_CHANGED"}
    with database.SessionLocal() as db:
        assert db.get(TerminalModel, "owner") is not None
        assert db.get(SessionDeletionOperationModel, "session") is not None
        assert db.get(SessionDeletionReceiptModel, "session") is None


def test_inflight_non_authority_history_commits_before_purge_and_is_removed(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add(_terminal("owner"))
        db.commit()
    database.begin_session_hard_deletion(
        "session",
        "cao-session",
        expected_terminal_ids=["owner"],
        allow_dirty_workspace=False,
    )
    with database.SessionLocal() as db:
        db.add(
            UsageRecordModel(
                source_run_identity="committed-before-purge",
                extractor="fixture",
                terminal_id="owner",
                session_id="session",
            )
        )
        db.commit()

    assert database.revalidate_session_hard_deletion("session", "cao-session")["valid"] is True
    database.mark_session_hard_deletion_workspace_retired(
        "session",
        workspace_evidence=[
            {
                "terminal_id": "owner",
                "managed": False,
                "path_absent": True,
                "git_unregistered": True,
                "branch_absent": True,
                "runtime_artifacts_absent": True,
            }
        ],
    )
    assert database.complete_session_hard_deletion("session", "cao-session")["completed"] is True
    with database.SessionLocal() as db:
        assert db.query(UsageRecordModel).count() == 0


def test_hard_delete_receipts_fence_late_work_and_return_deleted_history_semantics(monkeypatch):
    _install_database(monkeypatch)
    token = "late-child-token"
    with database.SessionLocal() as db:
        db.add(_terminal("owner", token=token))
        db.commit()
    _fence_and_mark()
    first = database.complete_session_hard_deletion("session", "cao-session")
    second = database.complete_session_hard_deletion("session", "cao-session")
    assert first["completed"] is True and first["already_deleted"] is False
    assert second["completed"] is True and second["already_deleted"] is True

    assert database.queue_workflow_input_for_provider("owner", 1, "late") is False
    assert database.queue_workflow_turn("owner", "external_input", "late") == (None, True)
    assert database.prepare_workflow_input("owner", "late", require_live_terminal=True) == {
        "accepted": False,
        "reason_code": "SESSION_DELETED",
    }
    assert database.claim_or_resume_workflow_turn_receipt("owner", 1) == {
        "accepted": False,
        "reason": "session_deleted",
    }
    assert database.claim_workflow_effect("owner", 1, "send_message", "late") is None
    assert database.request_workflow_provider_reconnect("owner") is False
    assert database.create_child_assignment_result_message("owner", "owner", "late") == (
        None,
        True,
    )
    with pytest.raises(HandoffResultSubmissionError) as error:
        database.submit_handoff_result_v1(
            token,
            1,
            HandoffResultDocumentV1(
                format="v1",
                summary="late",
                body_markdown="late callback",
                changed_files=[],
                checks=[],
                risks=[],
                blockers=[],
            ),
        )
    assert (error.value.status_code, error.value.code) == (409, "session_deleted")
    assert database.terminal_deletion_auth_token_matches("owner", token) is True
    assert database.terminal_deletion_auth_token_matches("owner", "wrong-token") is False
    with pytest.raises(WritableWorkContextConflict, match="SESSION_DELETED"):
        database.reserve_writable_work_context(
            context_id="late-context",
            request_id="late-context-request",
            project_id="project",
            session_id="session",
            terminal_id="owner",
            canonical_source="/source/project",
            canonical_worktree="/work/late-context",
            branch="cao/session/late-context",
            base_revision="f" * 40,
        )
    late_usage = UsageObservation(
        source_run_identity="late-usage",
        extractor="fixture",
        total_tokens=1,
    )
    usage_kwargs = {
        "provider": "codex",
        "agent_profile": "developer",
        "terminal_id": "owner",
        "terminal_name": "owner",
        "session_id": "session",
        "session_name": "cao-session",
        "project_id": "project",
        "project_name": "Project",
        "project_path": "/source/project",
    }
    assert database.record_usage_observation(late_usage, **usage_kwargs) is False
    assert (
        database.bind_provider_usage_session(
            provider="codex",
            provider_session_id="late-provider-session",
            terminal_id="owner",
            source="late",
        )
        is False
    )
    assert (
        database.record_provider_usage_checkpoint(
            late_usage,
            provider="codex",
            provider_session_id="late-provider-session",
            terminal_id="owner",
            terminal_name="owner",
            session_id="session",
            session_name="cao-session",
            agent_profile="developer",
            project_id="project",
            project_name="Project",
            project_path="/source/project",
            next_byte_offset=1,
        )
        == "session_deleted"
    )
    assert (
        database.claim_telegram_delivery(
            event_key="late-notification",
            event_kind="workflow_complete",
            workflow_id=999,
            root_terminal_id="owner",
        )
        is False
    )
    with pytest.raises(interaction_read_model_service.SessionInteractionsDeleted):
        interaction_read_model_service.list_interactions("session", mode="history")
    with database.SessionLocal() as db:
        assert db.query(TerminalModel).count() == 0
        assert db.query(WorkflowModel).count() == 0
        assert db.query(SessionDeletionReceiptModel).count() == 1
        assert db.query(TerminalDeletionReceiptModel).count() == 1
        assert db.query(UsageRecordModel).count() == 0
        assert db.query(ProviderUsageBindingModel).count() == 0
        assert db.query(TelegramDeliveryModel).count() == 0


def test_crash_left_external_notification_is_exactly_retired_then_purged(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add(_terminal("owner"))
        workflow = WorkflowModel(root_terminal_id="owner", status="terminal")
        db.add(workflow)
        db.flush()
        db.add(
            TelegramDeliveryModel(
                event_key="workflow:1:completed",
                event_kind="completed",
                workflow_id=workflow.id,
                root_terminal_id="owner",
                state="claimed",
                attempt_count=1,
            )
        )
        db.commit()

    plan = database.get_session_unresolved_work_plan("session", expected_terminal_ids=["owner"])
    assert plan["deletion_mode"] == "eligible_with_historical_indeterminate_retirement"
    assert plan["historical_indeterminate_count"] == 1
    assert plan["reason_codes"] == ["HISTORICAL_NOTIFICATION_OUTCOME_UNKNOWN"]

    retired = database.cancel_session_work_for_deletion(
        "session",
        expected_plan_token=plan["plan_token"],
        expected_terminal_ids=["owner"],
        cancel_unresolved_work=False,
        retire_historical_indeterminate=True,
    )
    assert retired["cancelled"] is True
    assert retired["retired_indeterminate_count"] == 1
    with database.SessionLocal() as db:
        notification = db.get(TelegramDeliveryModel, "workflow:1:completed")
        assert notification is not None
        assert notification.state == "operator_retired_indeterminate"
        assert notification.error_code == "operator_session_deletion_unknown_outcome"
        audit = db.query(SessionDeletionCancellationAuditModel).one()
        assert audit.item_kind == "telegram_delivery"
        assert audit.final_state == "operator_retired_indeterminate"
        assert audit.reason_code == "OPERATOR_RETIRED_UNKNOWN_OUTCOME"

    _fence_and_mark()
    deletion = database.complete_session_hard_deletion("session", "cao-session")
    assert deletion["completed"] is True
    assert deletion["before_counts"]["telegram_notification_deliveries"] == 1
    with database.SessionLocal() as db:
        assert db.query(TelegramDeliveryModel).count() == 0
        assert db.query(SessionDeletionCancellationAuditModel).count() == 0


def test_indeterminate_notification_with_mismatched_workflow_link_fails_closed(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add(_terminal("owner"))
        db.add(_terminal("other", session_id="other-session", session_name="cao-other"))
        foreign_workflow = WorkflowModel(root_terminal_id="other", status="terminal")
        db.add(foreign_workflow)
        db.flush()
        db.add(
            TelegramDeliveryModel(
                event_key="foreign-workflow:completed",
                event_kind="completed",
                workflow_id=foreign_workflow.id,
                root_terminal_id="owner",
                state="claimed",
                attempt_count=1,
            )
        )
        db.commit()

    plan = database.get_session_unresolved_work_plan("session", expected_terminal_ids=["owner"])
    assert plan["deletion_mode"] == "blocked_live_or_unsafe_authority"
    assert plan["unsafe_count"] == 1
    assert plan["reason_codes"] == ["NOTIFICATION_WORKFLOW_LINK_MISMATCH"]
    assert plan["plan_token"] is None


def test_deleted_or_fenced_lifetime_cannot_register_a_replacement_terminal(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add(_terminal("owner"))
        db.commit()
    database.begin_session_hard_deletion(
        "session",
        "cao-session",
        expected_terminal_ids=["owner"],
        allow_dirty_workspace=False,
    )
    assert database.queue_workflow_input_for_provider("owner", 1, "late") is False
    assert database.queue_workflow_turn("owner", "external_input", "late") == (None, True)
    assert database.claim_workflow_effect("owner", 1, "send_message", "late") is None
    assert database.request_workflow_provider_reconnect("owner") is False
    assert database.create_child_assignment_result_message("owner", "owner", "late") == (
        None,
        True,
    )
    with pytest.raises(WritableWorkContextConflict, match="SESSION_DELETION_IN_PROGRESS"):
        database.reserve_writable_work_context(
            context_id="late-context",
            request_id="late-context-request",
            project_id="project",
            session_id="session",
            terminal_id="owner",
            canonical_source="/source/project",
            canonical_worktree="/work/late-context",
            branch="cao/session/late-context",
            base_revision="f" * 40,
        )
    with pytest.raises(WritableWorkContextConflict, match="SESSION_DELETION_IN_PROGRESS"):
        database.create_terminal(
            "replacement",
            "cao-session",
            "replacement",
            "codex",
            session_lifetime_id="session",
        )
    with pytest.raises(WritableWorkContextConflict, match="SESSION_DELETION_IN_PROGRESS"):
        database.create_terminal(
            "replacement",
            "cao-session",
            "replacement",
            "codex",
            session_lifetime_id="different-session",
        )

    database.mark_session_hard_deletion_workspace_retired(
        "session",
        workspace_evidence=[
            {
                "terminal_id": "owner",
                "managed": False,
                "path_absent": True,
                "git_unregistered": True,
                "branch_absent": True,
                "runtime_artifacts_absent": True,
            }
        ],
    )
    database.complete_session_hard_deletion("session", "cao-session")
    with pytest.raises(WritableWorkContextConflict, match="SESSION_DELETED"):
        database.create_terminal(
            "replacement",
            "cao-session",
            "replacement",
            "codex",
            session_lifetime_id="session",
        )
    with pytest.raises(WritableWorkContextConflict, match="SESSION_DELETED"):
        database.create_terminal(
            "owner",
            "cao-replacement",
            "replacement",
            "codex",
            session_lifetime_id="different-session",
        )
    with pytest.raises(WritableWorkContextConflict, match="SESSION_DELETED"):
        database.create_terminal(
            "different-terminal",
            "cao-session",
            "replacement",
            "codex",
            session_lifetime_id="different-session",
        )


def test_resolve_by_name_reports_in_progress_fence(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add(_terminal("owner"))
        db.commit()
    database.begin_session_hard_deletion(
        "session",
        "cao-session",
        expected_terminal_ids=["owner"],
        allow_dirty_workspace=False,
    )

    assert database.resolve_session_lifetime("session")["deletion_in_progress"] is True
    assert database.resolve_session_lifetime("cao-session")["deletion_in_progress"] is True
    assert interaction_read_model_service.list_session_current_queue_counts(["session"]) == {
        "session": 0
    }


def test_crash_retry_and_concurrent_completion_converge_to_one_tombstone(monkeypatch, tmp_path):
    _install_database(monkeypatch, f"sqlite:///{tmp_path / 'hard-delete.db'}")
    with database.SessionLocal() as db:
        db.add_all([_terminal(f"terminal-{index}") for index in range(12)])
        db.commit()
    terminal_ids = tuple(f"terminal-{index}" for index in range(12))

    first = database.begin_session_hard_deletion(
        "session",
        "cao-session",
        expected_terminal_ids=terminal_ids,
        allow_dirty_workspace=False,
    )
    resumed = database.begin_session_hard_deletion(
        "session",
        "cao-session",
        expected_terminal_ids=terminal_ids,
        allow_dirty_workspace=False,
    )
    assert first["started"] is True
    assert resumed["started"] is True and resumed["already_started"] is True

    evidence = [
        {
            "terminal_id": terminal_id,
            "managed": False,
            "path_absent": True,
            "git_unregistered": True,
            "branch_absent": True,
            "runtime_artifacts_absent": True,
        }
        for terminal_id in terminal_ids
    ]
    first_mark = database.mark_session_hard_deletion_workspace_retired(
        "session", workspace_evidence=evidence
    )
    repeated_mark = database.mark_session_hard_deletion_workspace_retired(
        "session", workspace_evidence=evidence
    )
    assert first_mark["marked"] is True
    assert repeated_mark["marked"] is True and repeated_mark["already_marked"] is True

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                lambda _index: database.complete_session_hard_deletion("session", "cao-session"),
                range(2),
            )
        )

    assert all(outcome["completed"] for outcome in outcomes)
    assert sorted(outcome["already_deleted"] for outcome in outcomes) == [False, True]
    with database.SessionLocal() as db:
        assert db.query(SessionDeletionReceiptModel).count() == 1
        assert db.query(TerminalDeletionReceiptModel).count() == len(terminal_ids)
        assert db.query(SessionDeletionOperationModel).count() == 0
        assert db.query(TerminalModel).count() == 0


def test_hard_delete_resumes_the_durable_dirty_workspace_retirement_decision(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add(_terminal("owner"))
        db.add(
            WritableWorkContextModel(
                id="context",
                request_id="request",
                project_id="project",
                session_id="session",
                terminal_id="owner",
                canonical_source="/source/project",
                canonical_worktree="/work/session",
                branch="cao/session/owner",
                base_revision="f" * 40,
                state="retiring",
                retirement_allow_dirty=True,
                retirement_authority_fingerprint="durable-workspace-authority",
            )
        )
        db.commit()

    started = database.begin_session_hard_deletion(
        "session",
        "cao-session",
        expected_terminal_ids=["owner"],
        allow_dirty_workspace=False,
    )

    assert started["started"] is True
    assert started["allow_dirty_workspace"] is True
    assert database.revalidate_session_hard_deletion("session", "cao-session")["valid"] is True


def test_partial_database_purge_rolls_back_and_retry_converges(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add(_terminal("owner"))
        workflow = WorkflowModel(root_terminal_id="owner", status="terminal")
        db.add(workflow)
        db.flush()
        db.add(
            WorkflowTurnModel(
                workflow_id=workflow.id,
                kind="external_input",
                dedupe_key="history",
                state="finished",
            )
        )
        db.commit()
    _fence_and_mark()

    original_delete = database._delete_query
    calls = 0

    def interrupted(query):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("simulated database crash")
        return original_delete(query)

    monkeypatch.setattr(database, "_delete_query", interrupted)
    with pytest.raises(RuntimeError, match="simulated database crash"):
        database.complete_session_hard_deletion("session", "cao-session")
    with database.SessionLocal() as db:
        assert db.get(SessionDeletionOperationModel, "session") is not None
        assert db.get(SessionDeletionReceiptModel, "session") is None
        assert db.get(TerminalModel, "owner") is not None
        assert db.query(WorkflowModel).count() == 1
        assert db.query(WorkflowTurnModel).count() == 1

    monkeypatch.setattr(database, "_delete_query", original_delete)
    assert database.complete_session_hard_deletion("session", "cao-session")["completed"] is True
    with database.SessionLocal() as db:
        assert db.get(SessionDeletionOperationModel, "session") is None
        assert db.get(SessionDeletionReceiptModel, "session").receipt_version == 2
        assert db.query(TerminalModel).count() == 0
        assert db.query(WorkflowModel).count() == 0
        assert db.query(WorkflowTurnModel).count() == 0


def test_terminal_creation_and_hard_delete_fence_serialize_without_resurrection(
    monkeypatch, tmp_path
):
    _install_database(monkeypatch, f"sqlite:///{tmp_path / 'terminal-race.db'}")
    with database.SessionLocal() as db:
        db.add(_terminal("owner"))
        db.commit()

    def begin_delete():
        try:
            return (
                "delete",
                database.begin_session_hard_deletion(
                    "session",
                    "cao-session",
                    expected_terminal_ids=["owner"],
                    allow_dirty_workspace=False,
                ),
            )
        except database.AmbiguousSessionIdentity:
            return "delete_conflict", None

    def create_late_terminal():
        try:
            created = database.create_terminal(
                "late",
                "cao-session",
                "late",
                "codex",
                session_lifetime_id="session",
            )
            return "created", created
        except WritableWorkContextConflict as error:
            return "creation_fenced", error.reason_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        delete_future = executor.submit(begin_delete)
        create_future = executor.submit(create_late_terminal)
        delete_outcome = delete_future.result()
        create_outcome = create_future.result()

    with database.SessionLocal() as db:
        operation = db.get(SessionDeletionOperationModel, "session")
        late = db.get(TerminalModel, "late")
    if operation is not None:
        assert delete_outcome[0] == "delete" and delete_outcome[1]["started"] is True
        assert create_outcome == ("creation_fenced", "SESSION_DELETION_IN_PROGRESS")
        assert late is None
    else:
        assert delete_outcome == ("delete_conflict", None)
        assert create_outcome[0] == "created"
        assert late is not None


def test_hard_purge_sql_shape_is_fixed_and_uses_one_batched_terminal_fence_insert(
    monkeypatch,
):
    def run(size: int) -> tuple[int, int, int]:
        engine = _install_database(monkeypatch)
        terminal_ids = tuple(f"terminal-{index}" for index in range(size))
        with database.SessionLocal() as db:
            db.add_all([_terminal(terminal_id) for terminal_id in terminal_ids])
            db.commit()
        _fence_and_mark(terminal_ids=terminal_ids)
        statements: list[tuple[str, bool]] = []

        def record(_connection, _cursor, statement, _parameters, _context, many):
            statements.append((statement.lstrip().split(None, 1)[0].upper(), bool(many)))

        event.listen(engine, "before_cursor_execute", record)
        try:
            assert database.complete_session_hard_deletion("session", "cao-session")["completed"]
        finally:
            event.remove(engine, "before_cursor_execute", record)
        return (
            len(statements),
            sum(kind == "SELECT" for kind, _many in statements),
            sum(many for _kind, many in statements),
        )

    one = run(1)
    fifty = run(50)
    assert one[:2] == fifty[:2] == (92, 65)
    assert one[2] == 0
    assert fifty[2] == 1
