"""Permanent Session deletion, replay fencing, and graph-ownership regressions."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
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
    ManagedAttemptLifecycleEventModel,
    ManagedAttemptLifecycleModel,
    OwnerLaunchGrantModel,
    ProjectModel,
    ProviderExecutionLeaseModel,
    ProviderUsageBindingModel,
    SessionDeletedTerminalFenceModel,
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
from cli_agent_orchestrator.services import (
    interaction_read_model_service,
    managed_worktree_service,
    session_service,
)


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
    database.bind_session_hard_deletion_workspace_authority(
        session_id,
        session_name,
        workspace_authority=_unmanaged_workspace_authority(
            started.get("terminal_ids", terminal_ids),
            session_id=session_id,
            work_context=_current_context_authority(session_id),
        ),
    )
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


def _unmanaged_workspace_authority(
    terminal_ids: tuple[str, ...] | list[str],
    *,
    session_id: str = "session",
    work_context=None,
):
    return {
        "version": 1,
        "work_context": work_context,
        "worktrees": [
            {
                "version": 1,
                "terminal_id": terminal_id,
                "session_id": session_id,
                "managed": False,
            }
            for terminal_id in sorted(terminal_ids)
        ],
    }


def _current_context_authority(session_id: str):
    context = database.get_writable_work_context_by_session(session_id)
    if context is None:
        return None
    return {
        key: context.get(key)
        for key in (
            "id",
            "session_id",
            "terminal_id",
            "project_id",
            "canonical_source",
            "canonical_worktree",
            "branch",
            "base_revision",
            "state",
            "writer_authority_generation",
            "retirement_allow_dirty",
            "retirement_authority_fingerprint",
        )
    }


def _managed_supervisor_deletion_fixture(monkeypatch, tmp_path):
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
        str(repository), "context", "supervisor"
    )
    assert managed is not None and managed.branch is not None
    owner = _terminal("owner")
    owner.project_id = "project"
    owner.launch_worktree = managed.path
    owner.writer_authority_generation = "writer-generation"
    owner.managed_worktree_kind = managed.kind
    owner.managed_worktree_source = managed.source
    owner.managed_worktree_branch = managed.branch
    owner.managed_worktree_commit = managed.commit
    owner.writable_work_context_id = "context"
    with database.SessionLocal() as db:
        db.add(owner)
        db.add(
            ProjectModel(
                id="project",
                name="Project",
                normalized_name="project",
                path=str(repository),
                normalized_path=str(repository),
                is_default=True,
            )
        )
        db.add(
            WritableWorkContextModel(
                id="context",
                request_id="request",
                project_id="project",
                session_id="session",
                terminal_id="owner",
                canonical_source=managed.source,
                canonical_worktree=managed.path,
                branch=managed.branch,
                base_revision=managed.commit,
                state="admitted",
                writer_authority_generation="writer-generation",
            )
        )
        db.commit()
    metadata = {
        **managed.as_dict(),
        "id": "owner",
        "session_id": "session",
        "project_id": "project",
        "launch_worktree": managed.path,
        "managed_worktree_kind": managed.kind,
        "managed_worktree_source": managed.source,
        "managed_worktree_branch": managed.branch,
        "managed_worktree_commit": managed.commit,
        "writer_authority_generation": "writer-generation",
        "writable_work_context_id": "context",
    }
    captured = managed_worktree_service.capture_session_worktree_retirement_authority(
        [metadata], session_id="session"
    )
    assert captured["safe"] is True
    captured["authority"]["work_context"] = _current_context_authority("session")
    return managed, captured["authority"]


def test_production_shaped_fenced_session_binds_exact_projected_writer_generations(
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
    baseline = _git(repository, "rev-parse", "HEAD")
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")

    supervisor = managed_worktree_service.create_managed_worktree(
        str(repository), "supervisor", "supervisor", expected_commit=baseline
    )
    assert supervisor is not None
    supervisor_path = Path(supervisor.path)
    _git(supervisor_path, "switch", "-c", "fix/production-shaped-mission")
    (supervisor_path / "tracked.txt").write_text("mission\n", encoding="utf-8")
    _git(supervisor_path, "commit", "-qam", "mission")
    mission_head = _git(supervisor_path, "rev-parse", "HEAD")

    present_task = managed_worktree_service.create_managed_worktree(
        str(repository), "task-present", "task", expected_commit=baseline
    )
    retired_task = managed_worktree_service.create_managed_worktree(
        str(repository), "task-retired", "task", expected_commit=baseline
    )
    assert present_task is not None and retired_task is not None
    _git(repository, "worktree", "remove", retired_task.path)

    reviewers = []
    for index in range(9):
        reviewer = managed_worktree_service.create_managed_worktree(
            str(repository),
            f"reviewer-{index:02d}",
            "reviewer",
            expected_commit=baseline,
        )
        assert reviewer is not None
        _git(Path(reviewer.path), "switch", "--detach", mission_head)
        reviewers.append((f"reviewer-{index:02d}", reviewer))

    managed_rows = [
        ("supervisor", supervisor),
        ("task-present", present_task),
        ("task-retired", retired_task),
        *reviewers,
    ]
    generations = {
        terminal_id: f"writer-generation-{index:02d}"
        for index, (terminal_id, _managed) in enumerate(managed_rows)
    }
    terminals = []
    for terminal_id, managed in managed_rows:
        terminal = _terminal(terminal_id)
        terminal.project_id = "project"
        terminal.launch_worktree = managed.path
        terminal.write_enabled = managed.kind != "reviewer"
        terminal.managed_worktree_kind = managed.kind
        terminal.managed_worktree_source = managed.source
        terminal.managed_worktree_branch = managed.branch
        terminal.managed_worktree_commit = managed.commit
        terminal.managed_worktree_origin_terminal_id = terminal_id
        terminal.writable_work_context_id = "supervisor" if terminal_id == "supervisor" else None
        terminal.writer_authority_generation = generations[terminal_id]
        terminal.workspace_classification = "managed_isolated"
        terminals.append(terminal)
    with database.SessionLocal() as db:
        db.add_all(
            [
                ProjectModel(
                    id="project",
                    name="Project",
                    normalized_name="project",
                    path=str(repository),
                    normalized_path=str(repository),
                    is_default=True,
                ),
                WritableWorkContextModel(
                    id="supervisor",
                    request_id="request",
                    project_id="project",
                    session_id="session",
                    terminal_id="supervisor",
                    canonical_source=supervisor.source,
                    canonical_worktree=supervisor.path,
                    branch=str(supervisor.branch),
                    base_revision=baseline,
                    state="retiring",
                    writer_authority_generation=generations["supervisor"],
                ),
                *terminals,
            ]
        )
        db.commit()

    terminal_ids = [terminal_id for terminal_id, _managed in managed_rows]
    started = database.begin_session_hard_deletion(
        "session",
        "cao-session",
        expected_terminal_ids=terminal_ids,
        allow_dirty_workspace=False,
    )
    assert started["started"] is True
    resolved = database.resolve_session_lifetime("session")
    assert resolved is not None
    assert resolved["deletion_in_progress"] is True
    resolved_generations = {
        row["id"]: row["writer_authority_generation"] for row in resolved["terminals"]
    }
    assert resolved_generations == generations

    authority = session_service.SessionAuthority(
        session_id="session",
        session_name="cao-session",
        terminals=resolved["terminals"],
        retained_resources=[],
        deleted=False,
        runtime_exists=False,
        deletion_in_progress=True,
    )
    captured = session_service._capture_session_workspace_retirement_authority(authority)

    assert captured["safe"] is True
    captured_rows = {row["terminal_id"]: row for row in captured["authority"]["worktrees"]}
    assert len(captured_rows) == 12
    assert sum(bool(row["present"]) for row in captured_rows.values()) == 11
    assert {
        terminal_id: row["writer_authority_generation"]
        for terminal_id, row in captured_rows.items()
    } == generations
    assert captured_rows["supervisor"]["branch"] == "fix/production-shaped-mission"
    assert captured_rows["supervisor"]["head"] == mission_head
    assert all(
        captured_rows[terminal_id]["detached"] is True
        and captured_rows[terminal_id]["head"] == mission_head
        for terminal_id, _reviewer in reviewers
    )
    assert (
        database.bind_session_hard_deletion_workspace_authority(
            "session",
            "cao-session",
            workspace_authority=captured["authority"],
        )["bound"]
        is True
    )


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
    child_token = "receipt-only-child-token"
    child = _terminal("retired-child", token=child_token)
    child.launch_worktree = managed.path
    child.managed_worktree_kind = managed.kind
    child.managed_worktree_source = managed.source
    child.managed_worktree_branch = managed.branch
    child.managed_worktree_commit = managed.commit
    with database.SessionLocal() as db:
        db.add_all([owner, child])
        db.commit()
        owner_expected_identity = {
            field: getattr(owner, field) for field in database._TERMINAL_DELETION_IDENTITY_FIELDS
        }
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
    assert (
        database.delete_exited_terminal("owner", expected_identity=owner_expected_identity)[
            "deleted"
        ]
        == 1
    )
    lifetime = database.resolve_session_lifetime("session")
    assert lifetime is not None
    assert lifetime["terminals"] == []
    assert lifetime["lifetime_authority"] == "terminal_deletion_receipts"
    with pytest.raises(WritableWorkContextConflict, match="SESSION_DELETED"):
        database.reserve_writable_work_context(
            context_id="transitional-resurrection",
            request_id="transitional-resurrection-request",
            project_id="other-project",
            session_id="other-session",
            terminal_id="retired-child",
            canonical_source="/source/other-project",
            canonical_worktree="/work/transitional-resurrection",
            branch="cao/session/transitional-resurrection",
            base_revision="d" * 40,
        )

    started = database.begin_session_hard_deletion(
        "session",
        "cao-session",
        expected_terminal_ids=[],
        allow_dirty_workspace=False,
    )
    assert started["started"] is True
    assert started["terminal_ids"] == []
    assert started["graph_terminal_ids"] == ["owner", "retired-child"]
    assert database.bind_session_hard_deletion_workspace_authority(
        "session",
        "cao-session",
        workspace_authority=_unmanaged_workspace_authority([]),
    )["bound"]
    cleanup_authorities = database.list_session_historical_terminal_cleanup_authorities("session")
    child_cleanup_authority = next(
        authority
        for authority in cleanup_authorities
        if authority["terminal_id"] == "retired-child"
    )
    assert child_cleanup_authority["managed_worktree_branch_object_id"] == removed["commit"]
    cleanup = managed_worktree_service.purge_managed_worktree(
        child_cleanup_authority, require_already_absent=True
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
        assert db.get(TerminalDeletionReceiptModel, "retired-child") is None
        tombstone = db.get(SessionDeletionReceiptModel, "session")
        assert tombstone.receipt_version == 4
        assert tombstone.workspace_disposition == "retired"
        assert {
            item["terminal_id"] for item in database._session_receipt_terminal_fences(tombstone)
        } == {
            "owner",
            "retired-child",
        }
        assert {row.terminal_id for row in db.query(SessionDeletedTerminalFenceModel).all()} == {
            "owner",
            "retired-child",
        }
    assert database.terminal_deletion_auth_token_matches("retired-child", child_token) is True
    with pytest.raises(HandoffResultSubmissionError) as error:
        database.submit_handoff_result_v1(
            child_token,
            1,
            HandoffResultDocumentV1(
                format="v1",
                summary="late receipt-only callback",
                body_markdown="must not recreate the deleted Session",
                changed_files=[],
                checks=[],
                risks=[],
                blockers=[],
            ),
        )
    assert (error.value.status_code, error.value.code) == (409, "session_deleted")


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
        assert db.query(TerminalDeletionReceiptModel).count() == 0
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
        other_lifecycle = ManagedAttemptLifecycleModel(
            assignment_id=other_assignment.id,
            attempt_id=other_assignment.attempt_id,
            parent_terminal_id="other",
            child_terminal_id="other",
            request_workflow_effect_id=other_effect.id,
            state="completed",
        )
        db.add(other_lifecycle)
        db.flush()
        db.add(
            ManagedAttemptLifecycleEventModel(
                assignment_id=other_assignment.id,
                event_key="other-managed-attempt:completed",
                event_type="completed",
                state="completed",
            )
        )
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
        lifecycle = ManagedAttemptLifecycleModel(
            assignment_id=assignment.id,
            attempt_id=assignment.attempt_id,
            parent_terminal_id="owner",
            child_terminal_id="child",
            request_workflow_effect_id=effect.id,
            state="completed",
        )
        db.add(lifecycle)
        db.flush()
        db.add(
            ManagedAttemptLifecycleEventModel(
                assignment_id=assignment.id,
                event_key="owned-managed-attempt:completed",
                event_type="completed",
                state="completed",
            )
        )
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
        "managed_attempt_lifecycle": 1,
        "managed_attempt_lifecycle_events": 1,
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
        "terminal_deletion_receipts": 0,
    }
    assert all(count == 0 for count in completed["after_counts"].values())
    with database.SessionLocal() as db:
        assert db.query(ProjectModel).count() == 1
        assert db.get(TerminalModel, "other") is not None
        assert db.get(WorkflowModel, other_workflow_id) is not None
        assert db.get(WorkflowTurnModel, other_turn_id) is not None
        assert db.get(WorkflowEffectModel, other_effect_id) is not None
        assert db.get(ChildAssignmentModel, other_assignment_id) is not None
        assert db.get(ManagedAttemptLifecycleModel, other_assignment_id) is not None
        assert (
            db.query(ManagedAttemptLifecycleEventModel)
            .filter_by(assignment_id=other_assignment_id)
            .count()
            == 1
        )
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
        assert tombstone.receipt_version == 4
        assert tombstone.workspace_disposition == "retired"
        assert tombstone.deletion_reason == "operator_session_hard_delete"
        assert tombstone.retained_resources_json == "[]"
        assert db.query(TerminalDeletionReceiptModel).count() == 0


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
    assert database.bind_session_hard_deletion_workspace_authority(
        "session",
        "cao-session",
        workspace_authority=_unmanaged_workspace_authority(["owner"]),
    )["bound"]
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
    # Caller-supplied ownership must not bypass the terminal's permanent
    # identity fence after transitional receipts have been purged.
    with pytest.raises(WritableWorkContextConflict, match="SESSION_DELETED"):
        database.reserve_writable_work_context(
            context_id="late-context-other-session",
            request_id="late-context-request-other-session",
            project_id="different-project",
            session_id="different-session",
            terminal_id="owner",
            canonical_source="/source/different-project",
            canonical_worktree="/work/late-context-other-session",
            branch="cao/session/late-context-other-session",
            base_revision="e" * 40,
        )
    provider = database.acquire_provider_execution_decision("owner", 999, limit=1)
    assert provider["acquired"] is False
    assert provider["reason_code"] == "TERMINAL_DELETED"
    assert database.recovery_takeover_durable_eligibility("owner") == {
        "eligible": False,
        "reason_code": "RECOVERY_TARGET_DELETED",
        "terminal": None,
    }
    with pytest.raises(database.RecoveryTakeoverRejected, match="RECOVERY_TARGET_DELETED"):
        database.claim_recovery_takeover(
            request_id="late-recovery",
            old_terminal_id="owner",
            expected_authority_generation="old-generation",
            expected_runtime_generation="old-runtime",
            agent_profile="developer",
            provider="codex",
            profile_revision_id=None,
            provider_config_revision_id=None,
            owner_grant_token="late-grant",
            owner_grant_launch_id="late-launch",
            owner_grant_scope={},
            new_terminal_id="replacement",
            new_session_name="cao-different-session",
            new_session_id="different-session",
            new_window_name="replacement",
            new_runtime_generation="new-runtime",
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
        assert db.query(TerminalDeletionReceiptModel).count() == 0
        fence = db.get(SessionDeletedTerminalFenceModel, "owner")
        assert fence is not None
        assert fence.session_id == "session"
        assert fence.auth_token_sha256 == hashlib.sha256(token.encode()).hexdigest()
        assert db.query(WritableWorkContextModel).count() == 0
        assert db.query(ProviderExecutionLeaseModel).count() == 0
        assert db.query(database.RecoveryTakeoverModel).count() == 0
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
    assert database.bind_session_hard_deletion_workspace_authority(
        "session",
        "cao-session",
        workspace_authority=_unmanaged_workspace_authority(["owner"]),
    )["bound"]
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
    assert database.bind_session_hard_deletion_workspace_authority(
        "session",
        "cao-session",
        workspace_authority=_unmanaged_workspace_authority(list(terminal_ids)),
    )["bound"]

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
        assert db.query(TerminalDeletionReceiptModel).count() == 0
        assert db.query(SessionDeletionOperationModel).count() == 0
        assert db.query(TerminalModel).count() == 0


def test_workspace_authority_is_bound_once_and_required_before_cleanup_mark(monkeypatch):
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
    evidence = [
        {
            "terminal_id": "owner",
            "managed": False,
            "path_absent": True,
            "git_unregistered": True,
            "branch_absent": True,
            "runtime_artifacts_absent": True,
        }
    ]
    assert database.mark_session_hard_deletion_workspace_retired(
        "session", workspace_evidence=evidence
    ) == {"marked": False, "reason_code": "WORKSPACE_CLEANUP_UNPROVEN"}

    authority = _unmanaged_workspace_authority(["owner"])
    first = database.bind_session_hard_deletion_workspace_authority(
        "session", "cao-session", workspace_authority=authority
    )
    replay = database.bind_session_hard_deletion_workspace_authority(
        "session", "cao-session", workspace_authority=authority
    )
    changed = _unmanaged_workspace_authority(["owner"])
    changed["worktrees"][0]["session_id"] = "changed-session"
    rejected = database.bind_session_hard_deletion_workspace_authority(
        "session", "cao-session", workspace_authority=changed
    )

    assert first["bound"] is True
    assert replay["bound"] is True and replay["already_bound"] is True
    assert rejected == {
        "bound": False,
        "reason_code": "WORKSPACE_AUTHORITY_CHANGED",
    }
    operation = database.get_session_hard_deletion_operation("session")
    assert operation["workspace_authority"] == authority
    assert len(operation["workspace_authority_sha256"]) == 64
    assert database.mark_session_hard_deletion_workspace_retired(
        "session", workspace_evidence=evidence
    )["marked"]


def test_workspace_authority_rejects_terminal_context_relational_mismatch(monkeypatch, tmp_path):
    _managed, authority = _managed_supervisor_deletion_fixture(monkeypatch, tmp_path)
    with database.SessionLocal() as db:
        owner = db.get(TerminalModel, "owner")
        owner.project_id = "different-project"
        db.commit()
    authority["worktrees"][0]["project_id"] = "different-project"
    assert database.begin_session_hard_deletion(
        "session",
        "cao-session",
        expected_terminal_ids=["owner"],
        allow_dirty_workspace=False,
    )["started"]

    assert database.bind_session_hard_deletion_workspace_authority(
        "session", "cao-session", workspace_authority=authority
    ) == {
        "bound": False,
        "reason_code": "WORKSPACE_AUTHORITY_CHANGED",
    }


def test_workspace_authority_rejects_writer_generation_changed_after_capture(monkeypatch, tmp_path):
    _managed, authority = _managed_supervisor_deletion_fixture(monkeypatch, tmp_path)
    with database.SessionLocal() as db:
        owner = db.get(TerminalModel, "owner")
        owner.writer_authority_generation = "writer-generation-changed"
        db.commit()
    assert database.begin_session_hard_deletion(
        "session",
        "cao-session",
        expected_terminal_ids=["owner"],
        allow_dirty_workspace=False,
    )["started"]

    assert database.bind_session_hard_deletion_workspace_authority(
        "session", "cao-session", workspace_authority=authority
    ) == {
        "bound": False,
        "reason_code": "WORKSPACE_AUTHORITY_CHANGED",
    }


def test_workspace_authority_revalidation_rejects_late_foreign_terminal_target_owner(
    monkeypatch, tmp_path
):
    managed, authority = _managed_supervisor_deletion_fixture(monkeypatch, tmp_path)
    assert database.begin_session_hard_deletion(
        "session",
        "cao-session",
        expected_terminal_ids=["owner"],
        allow_dirty_workspace=False,
    )["started"]
    assert database.bind_session_hard_deletion_workspace_authority(
        "session", "cao-session", workspace_authority=authority
    )["bound"]
    foreign = _terminal(
        "foreign-owner",
        session_id="foreign-session",
        session_name="cao-foreign",
    )
    foreign.launch_worktree = managed.path
    foreign.managed_worktree_kind = "supervisor"
    foreign.managed_worktree_source = managed.source
    foreign.managed_worktree_branch = managed.branch
    foreign.managed_worktree_commit = managed.commit
    foreign_writer_generation = foreign.writer_authority_generation
    with database.SessionLocal() as db:
        db.add(foreign)
        db.commit()

    assert database.revalidate_session_hard_deletion("session", "cao-session") == {
        "valid": False,
        "reason_code": "WORKSPACE_FOREIGN_OWNER",
    }
    admitted = database.admit_session_foreign_workspace_preservation("session", "cao-session")
    assert admitted["admitted"] is True
    assert (
        database.admit_session_foreign_workspace_preservation("session", "cao-session")[
            "already_admitted"
        ]
        is True
    )
    revalidated = database.revalidate_session_hard_deletion("session", "cao-session")
    assert revalidated["valid"] is True
    assert revalidated["workspace_disposition"] == "preserved_foreign"
    runtime_artifacts = {"runtime_artifacts_absent": True, "terminals": []}
    assert database.mark_session_hard_deletion_workspace_preserved(
        "session", runtime_artifacts=runtime_artifacts
    )["marked"]
    assert database.mark_session_hard_deletion_workspace_preserved(
        "session", runtime_artifacts=runtime_artifacts
    )["already_marked"]

    completed = database.complete_session_hard_deletion("session", "cao-session")
    assert completed["completed"] is True
    assert completed["workspace_disposition"] == "preserved_foreign"
    assert Path(managed.path).is_dir()
    with database.SessionLocal() as db:
        preserved = db.get(TerminalModel, "foreign-owner")
        assert preserved is not None
        assert preserved.launch_worktree == managed.path
        assert preserved.writer_authority_generation == foreign_writer_generation
        receipt = db.get(SessionDeletionReceiptModel, "session")
        assert receipt.receipt_version == 4
        assert receipt.workspace_disposition == "preserved_foreign"
        evidence = json.loads(receipt.workspace_evidence_json)
        assert evidence["reason_code"] == "WORKSPACE_FOREIGN_OWNER"
        assert evidence["foreign_authority"]["terminals"][0]["terminal_id"] == "foreign-owner"


def test_workspace_authority_preserves_foreign_writer_on_target_path(monkeypatch, tmp_path):
    managed, authority = _managed_supervisor_deletion_fixture(monkeypatch, tmp_path)
    assert database.begin_session_hard_deletion(
        "session",
        "cao-session",
        expected_terminal_ids=["owner"],
        allow_dirty_workspace=False,
    )["started"]
    with database.SessionLocal() as db:
        db.add(
            WorktreeWriterLeaseModel(
                canonical_worktree=managed.path,
                terminal_id="foreign-writer",
                authority_generation="foreign-generation",
            )
        )
        db.commit()

    bound = database.bind_session_hard_deletion_workspace_authority(
        "session", "cao-session", workspace_authority=authority
    )
    assert bound["bound"] is True
    assert bound["workspace_disposition"] == "preserved_foreign"
    runtime_artifacts = {"runtime_artifacts_absent": True, "terminals": []}
    assert database.mark_session_hard_deletion_workspace_preserved(
        "session", runtime_artifacts=runtime_artifacts
    )["marked"]
    completed = database.complete_session_hard_deletion("session", "cao-session")
    assert completed["completed"] is True
    assert Path(managed.path).is_dir()
    with database.SessionLocal() as db:
        lease = db.get(WorktreeWriterLeaseModel, managed.path)
        assert lease is not None
        assert lease.terminal_id == "foreign-writer"
        assert lease.authority_generation == "foreign-generation"


def test_workspace_context_change_after_preflight_cannot_be_bound(monkeypatch):
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
                state="admitted",
                writer_authority_generation="writer-generation",
            )
        )
        db.commit()
    authority = _unmanaged_workspace_authority(
        ["owner"],
        work_context=_current_context_authority("session"),
    )
    assert database.begin_session_hard_deletion(
        "session",
        "cao-session",
        expected_terminal_ids=["owner"],
        allow_dirty_workspace=False,
    )["started"]
    with database.SessionLocal() as db:
        context = db.get(WritableWorkContextModel, "context")
        context.state = "retiring"
        context.retirement_authority_fingerprint = "a" * 64
        db.commit()

    assert database.bind_session_hard_deletion_workspace_authority(
        "session",
        "cao-session",
        workspace_authority=authority,
    ) == {
        "bound": False,
        "reason_code": "WRITABLE_WORKTREE_AUTHORITY_CHANGED",
    }


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
        assert db.get(SessionDeletionReceiptModel, "session").receipt_version == 4
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


def test_transitional_to_final_terminal_fence_serializes_without_resurrection(
    monkeypatch, tmp_path
):
    _install_database(monkeypatch, f"sqlite:///{tmp_path / 'final-fence-race.db'}")
    terminal = _terminal("owner")
    with database.SessionLocal() as db:
        db.add(terminal)
        db.commit()
        expected_identity = {
            field: getattr(terminal, field) for field in database._TERMINAL_DELETION_IDENTITY_FIELDS
        }
    assert (
        database.delete_exited_terminal("owner", expected_identity=expected_identity)["deleted"]
        == 1
    )
    _fence_and_mark(terminal_ids=())

    def complete_delete():
        return database.complete_session_hard_deletion("session", "cao-session")

    def reserve_late_authority():
        try:
            database.reserve_writable_work_context(
                context_id="race-context",
                request_id="race-request",
                project_id="different-project",
                session_id="different-session",
                terminal_id="owner",
                canonical_source="/source/different-project",
                canonical_worktree="/work/race-context",
                branch="cao/session/race-context",
                base_revision="c" * 40,
            )
        except WritableWorkContextConflict as error:
            return error.reason_code
        return "accepted"

    with ThreadPoolExecutor(max_workers=2) as executor:
        deletion_future = executor.submit(complete_delete)
        reservation_future = executor.submit(reserve_late_authority)
        deletion = deletion_future.result()
        reservation = reservation_future.result()

    assert deletion["completed"] is True
    assert reservation == "SESSION_DELETED"
    with database.SessionLocal() as db:
        assert db.get(TerminalDeletionReceiptModel, "owner") is None
        assert db.get(SessionDeletedTerminalFenceModel, "owner") is not None
        assert db.query(WritableWorkContextModel).count() == 0


def test_final_terminal_fence_backfill_is_receipted_crash_resumable_and_indexed(
    monkeypatch, tmp_path
):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'terminal-fence-migration.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        db.add(
            SessionDeletionReceiptModel(
                session_id="deleted-session",
                session_name="cao-deleted-session",
                retained_resources_json="[]",
                deletion_reason="operator_session_hard_delete",
                authority_fingerprint="a" * 64,
                terminal_fences_json=(
                    '[{"auth_token_sha256":"' + "b" * 64 + '","terminal_id":"deleted-child"},'
                    '{"auth_token_sha256":null,"terminal_id":"deleted-owner"}]'
                ),
                receipt_version=3,
                deleted_at=datetime(2026, 9, 8, 8, 0, 0),
            )
        )
        db.commit()
    SessionDeletedTerminalFenceModel.__table__.drop(bind=engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "DELETE FROM migration_receipts WHERE name = ?",
            (database.SESSION_DELETED_TERMINAL_FENCE_MIGRATION,),
        )
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(database, "_session_deletion_receipt_schema_ready", False)
    monkeypatch.setattr(database, "_session_deletion_receipt_schema_engine_identity", None)
    original_backfill = database._backfill_session_deleted_terminal_fences

    def interrupt_backfill(_connection):
        raise RuntimeError("simulated final-fence backfill interruption")

    monkeypatch.setattr(database, "_backfill_session_deleted_terminal_fences", interrupt_backfill)
    with pytest.raises(RuntimeError, match="simulated final-fence backfill interruption"):
        database._ensure_session_deletion_receipt_schema()
    assert database._session_deletion_receipt_schema_ready is False
    assert database._session_deletion_receipt_schema_engine_identity is None
    with engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT COUNT(*) FROM pragma_table_info('session_deleted_terminal_fences')"
            ).scalar_one()
            > 0
        )
        assert (
            connection.exec_driver_sql(
                "SELECT COUNT(*) FROM migration_receipts WHERE name = ?",
                (database.SESSION_DELETED_TERMINAL_FENCE_MIGRATION,),
            ).scalar_one()
            == 0
        )

    # Model a process that committed one exact row before dying without the
    # completion receipt. The next startup must preserve and finish it.
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO session_deleted_terminal_fences"
            "(terminal_id, session_id, auth_token_sha256, deleted_at) "
            "VALUES (?, ?, ?, ?)",
            (
                "deleted-child",
                "deleted-session",
                "b" * 64,
                datetime(2026, 9, 8, 7, 0, 0),
            ),
        )
    monkeypatch.setattr(database, "_backfill_session_deleted_terminal_fences", original_backfill)
    database._ensure_session_deletion_receipt_schema()
    assert database._session_deletion_receipt_schema_ready is True
    assert database._session_deletion_receipt_schema_engine_identity == id(engine)
    with engine.connect() as connection:
        indexed = connection.exec_driver_sql(
            "SELECT terminal_id, session_id, auth_token_sha256, deleted_at "
            "FROM session_deleted_terminal_fences ORDER BY terminal_id"
        ).all()
        assert [(row[0], row[1], row[2]) for row in indexed] == [
            ("deleted-child", "deleted-session", "b" * 64),
            ("deleted-owner", "deleted-session", None),
        ]
        assert str(indexed[0][3]).startswith("2026-09-08 07:00:00")
        assert (
            connection.exec_driver_sql(
                "SELECT COUNT(*) FROM migration_receipts WHERE name = ?",
                (database.SESSION_DELETED_TERMINAL_FENCE_MIGRATION,),
            ).scalar_one()
            == 1
        )
        plan = " ".join(
            str(row[-1])
            for row in connection.exec_driver_sql(
                "EXPLAIN QUERY PLAN SELECT terminal_id "
                "FROM session_deleted_terminal_fences WHERE terminal_id = ?",
                ("deleted-owner",),
            ).all()
        )
        assert "sqlite_autoindex_session_deleted_terminal_fences_1" in plan
        digest_plan = " ".join(
            str(row[-1])
            for row in connection.exec_driver_sql(
                "EXPLAIN QUERY PLAN SELECT terminal_id "
                "FROM session_deleted_terminal_fences WHERE auth_token_sha256 = ?",
                ("b" * 64,),
            ).all()
        )
        assert "ix_session_deleted_terminal_fences_auth_token_sha256" in digest_plan

    monkeypatch.setattr(database, "_backfill_session_deleted_terminal_fences", interrupt_backfill)
    database._ensure_session_deletion_receipt_schema()
    with pytest.raises(WritableWorkContextConflict, match="SESSION_DELETED"):
        database.reserve_writable_work_context(
            context_id="legacy-tombstone-context",
            request_id="legacy-tombstone-request",
            project_id="new-project",
            session_id="new-session",
            terminal_id="deleted-owner",
            canonical_source="/source/new-project",
            canonical_worktree="/work/legacy-tombstone-context",
            branch="cao/session/legacy-tombstone-context",
            base_revision="a" * 40,
        )
    with database.SessionLocal() as db:
        assert db.query(WritableWorkContextModel).count() == 0


def test_session_deletion_schema_readiness_is_engine_scoped_and_query_free(monkeypatch, tmp_path):
    def install(path: Path):
        installed = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(installed)
        monkeypatch.setattr(database, "engine", installed)
        monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=installed))
        return installed

    monkeypatch.setattr(database, "_session_deletion_receipt_schema_ready", False)
    monkeypatch.setattr(database, "_session_deletion_receipt_schema_engine_identity", None)
    first_engine = install(tmp_path / "schema-ready-a.db")
    first_statements: list[str] = []

    def record_first(_connection, _cursor, statement, _parameters, _context, _many):
        first_statements.append(statement)

    event.listen(first_engine, "before_cursor_execute", record_first)
    try:
        database._ensure_session_deletion_receipt_schema()
        assert first_statements
        assert database._session_deletion_receipt_schema_ready is True
        assert database._session_deletion_receipt_schema_engine_identity == id(first_engine)
        first_statements.clear()
        database._ensure_session_deletion_receipt_schema()
        database._ensure_session_deletion_receipt_schema()
        assert first_statements == []
    finally:
        event.remove(first_engine, "before_cursor_execute", record_first)

    second_engine = install(tmp_path / "schema-ready-b.db")
    second_statements: list[str] = []

    def record_second(_connection, _cursor, statement, _parameters, _context, _many):
        second_statements.append(statement)

    event.listen(second_engine, "before_cursor_execute", record_second)
    try:
        database._ensure_session_deletion_receipt_schema()
        assert second_statements
        assert database._session_deletion_receipt_schema_ready is True
        assert database._session_deletion_receipt_schema_engine_identity == id(second_engine)
        second_statements.clear()
        database._ensure_session_deletion_receipt_schema()
        assert second_statements == []
    finally:
        event.remove(second_engine, "before_cursor_execute", record_second)

    monkeypatch.setattr(database, "_terminal_deletion_receipt_schema_ready", False)
    monkeypatch.setattr(database, "_terminal_deletion_receipt_schema_engine_identity", None)
    database._ensure_terminal_deletion_receipt_schema()
    terminal_receipt_statements: list[str] = []

    def record_terminal_receipt(_connection, _cursor, statement, _parameters, _context, _many):
        terminal_receipt_statements.append(statement)

    event.listen(second_engine, "before_cursor_execute", record_terminal_receipt)
    try:
        database._ensure_terminal_deletion_receipt_schema()
        assert terminal_receipt_statements == []
    finally:
        event.remove(second_engine, "before_cursor_execute", record_terminal_receipt)


def test_session_deletion_schema_concurrent_first_access_runs_one_backfill(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'schema-ready-concurrent.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(database, "_session_deletion_receipt_schema_ready", False)
    monkeypatch.setattr(database, "_session_deletion_receipt_schema_engine_identity", None)
    original_backfill = database._backfill_session_deleted_terminal_fences
    backfill_calls: list[int] = []

    def delayed_backfill(connection):
        backfill_calls.append(1)
        time.sleep(0.05)
        return original_backfill(connection)

    monkeypatch.setattr(database, "_backfill_session_deleted_terminal_fences", delayed_backfill)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(database._ensure_session_deletion_receipt_schema) for _index in range(2)
        ]
        for future in futures:
            future.result()

    assert backfill_calls == [1]
    assert database._session_deletion_receipt_schema_ready is True
    assert database._session_deletion_receipt_schema_engine_identity == id(engine)
    with engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT COUNT(*) FROM migration_receipts WHERE name = ?",
                (database.SESSION_DELETED_TERMINAL_FENCE_MIGRATION,),
            ).scalar_one()
            == 1
        )


def test_final_terminal_fence_backfill_fails_closed_on_conflicting_owner(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO session_deletion_receipts"
            "(session_id, session_name, retained_resources_json, deletion_reason, "
            " authority_fingerprint, terminal_fences_json, receipt_version, deleted_at) "
            "VALUES (?, ?, '[]', 'operator_session_hard_delete', ?, ?, 3, CURRENT_TIMESTAMP)",
            (
                "deleted-session",
                "cao-deleted-session",
                "a" * 64,
                '[{"auth_token_sha256":null,"terminal_id":"deleted-owner"}]',
            ),
        )
        connection.exec_driver_sql(
            "INSERT INTO session_deleted_terminal_fences"
            "(terminal_id, session_id, auth_token_sha256, deleted_at) "
            "VALUES ('deleted-owner', 'different-session', NULL, CURRENT_TIMESTAMP)"
        )
        connection.exec_driver_sql(
            "DELETE FROM migration_receipts WHERE name = ?",
            (database.SESSION_DELETED_TERMINAL_FENCE_MIGRATION,),
        )
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    with pytest.raises(database.AmbiguousTerminalIdentity, match="deleted-owner"):
        database._ensure_session_deletion_receipt_schema()
    with engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT COUNT(*) FROM migration_receipts WHERE name = ?",
                (database.SESSION_DELETED_TERMINAL_FENCE_MIGRATION,),
            ).scalar_one()
            == 0
        )


def test_final_terminal_fence_backfill_fails_closed_on_malformed_tombstone(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO session_deletion_receipts"
            "(session_id, session_name, retained_resources_json, deletion_reason, "
            " authority_fingerprint, terminal_fences_json, receipt_version, deleted_at) "
            "VALUES ('deleted-session', 'cao-deleted-session', '[]', "
            "'operator_session_hard_delete', NULL, 'not-json', 3, CURRENT_TIMESTAMP)"
        )
        connection.exec_driver_sql(
            "DELETE FROM migration_receipts WHERE name = ?",
            (database.SESSION_DELETED_TERMINAL_FENCE_MIGRATION,),
        )
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    with pytest.raises(database.AmbiguousSessionIdentity, match="deleted-session"):
        database._ensure_session_deletion_receipt_schema()
    with engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT COUNT(*) FROM migration_receipts WHERE name = ?",
                (database.SESSION_DELETED_TERMINAL_FENCE_MIGRATION,),
            ).scalar_one()
            == 0
        )
        assert (
            connection.exec_driver_sql(
                "SELECT COUNT(*) FROM session_deleted_terminal_fences"
            ).scalar_one()
            == 0
        )


def test_hard_purge_sql_shape_is_fixed_with_one_session_tombstone(
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
    assert one[:2] == fifty[:2] == (104, 75)
    assert one[2] == 0
    assert fifty[2] == 1
