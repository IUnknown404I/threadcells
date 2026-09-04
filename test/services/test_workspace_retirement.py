"""Session workspace retirement safety and restart convergence."""

from __future__ import annotations

import json
import subprocess
from contextlib import nullcontext
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.database import (
    Base,
    InboxModel,
    ProviderExecutionLeaseModel,
    TerminalModel,
    WorkflowModel,
    WorkflowTurnModel,
    WorktreeWriterLeaseModel,
    WritableWorkContextConflict,
    WritableWorkContextModel,
)
from cli_agent_orchestrator.services import managed_worktree_service
from cli_agent_orchestrator.services.housekeeping.executor import execute_plan
from cli_agent_orchestrator.services.housekeeping.models import finalize_plan
from cli_agent_orchestrator.services.workspace_retirement_service import (
    candidate_from_snapshot,
    plan_session_workspaces,
    reconcile_retiring_session_workspaces,
)


def _git(repository: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *args],
        check=check,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def workspace_factory(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    for name in (
        "_ensure_terminal_worktree_authority_schema",
        "_ensure_workflow_schema",
        "_ensure_child_assignment_schema",
        "_ensure_provider_execution_schema",
        "_ensure_usage_schema",
    ):
        monkeypatch.setattr(database, name, lambda: None)
    managed_root = tmp_path / "managed"
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", managed_root)

    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    _git(source, "config", "user.name", "ThreadCells Test")
    _git(source, "config", "user.email", "threadcells@example.invalid")
    source.joinpath("tracked.txt").write_text("baseline\n", encoding="utf-8")
    _git(source, "add", "tracked.txt")
    _git(source, "commit", "-qm", "baseline")

    sequence = 0

    def create(*, lifecycle: str = "exited", state: str = "admitted"):
        nonlocal sequence
        sequence += 1
        context_id = f"workspace-{sequence}"
        session_id = f"session-{sequence}"
        terminal_id = f"terminal-{sequence}"
        managed = managed_worktree_service.create_managed_worktree(
            str(source), context_id, "supervisor"
        )
        assert managed is not None
        with database.SessionLocal() as db:
            db.add(
                WritableWorkContextModel(
                    id=context_id,
                    request_id=f"request-{sequence}",
                    project_id="project-test",
                    session_id=session_id,
                    terminal_id=terminal_id,
                    canonical_source=managed.source,
                    canonical_worktree=managed.path,
                    branch=str(managed.branch),
                    base_revision=managed.commit,
                    state=state,
                    writer_authority_generation=f"writer-{sequence}",
                )
            )
            db.add(
                TerminalModel(
                    id=terminal_id,
                    tmux_session=f"cao-history-{sequence}",
                    session_id=session_id,
                    tmux_window="supervisor",
                    provider="codex",
                    agent_profile="critical_sol_xhigh_owner",
                    launch_worktree=managed.path,
                    write_enabled=True,
                    writer_authority_generation=f"writer-{sequence}",
                    context_role="supervisor",
                    managed_worktree_kind="supervisor",
                    managed_worktree_source=managed.source,
                    managed_worktree_branch=managed.branch,
                    managed_worktree_commit=managed.commit,
                    managed_worktree_origin_terminal_id=terminal_id,
                    writable_work_context_id=context_id,
                    workspace_classification="managed_isolated",
                    runtime_lifecycle=lifecycle,
                    creation_order=sequence,
                )
            )
            db.add(
                InboxModel(
                    sender_id="owner",
                    receiver_id=terminal_id,
                    message="historical message",
                    status="delivered",
                )
            )
            db.commit()
        return {
            "context_id": context_id,
            "session_id": session_id,
            "terminal_id": terminal_id,
            "managed": managed,
            "source": source,
        }

    return create


def _candidate(identity: dict, *, allow_dirty: bool = False):
    snapshot = database.get_session_workspace_retirement_snapshot(identity["context_id"])
    assert snapshot is not None
    return candidate_from_snapshot(snapshot, allow_dirty=allow_dirty)


def _claim(identity: dict, *, allow_dirty: bool = False):
    candidate = _candidate(identity, allow_dirty=allow_dirty)
    snapshot = database.get_session_workspace_retirement_snapshot(identity["context_id"])
    assert snapshot is not None
    claim = database.claim_session_workspace_retirement(
        identity["context_id"],
        snapshot["authority_fingerprint"],
        allow_dirty=allow_dirty,
        retirement_plan_json=dict(candidate.attributes)["retirement_plan_json"],
    )
    assert claim["claimed"] is True
    return candidate


def _execute(candidate, root: Path):
    plan = finalize_plan(
        generated_at=2_000_000_000.0,
        mode="frequent",
        root=root,
        candidates=[candidate],
        warnings=[],
    )
    return execute_plan(
        plan,
        config={"root": str(root)},
        open_inventory=lambda: (set(), True),
        lifecycle_fence_held=True,
    )


def test_clean_inactive_workspace_retires_but_history_branch_and_output_remain(
    workspace_factory, tmp_path, monkeypatch
):
    identity = workspace_factory()
    managed = identity["managed"]
    log_dir = tmp_path / "terminal-output"
    log_dir.mkdir()
    log_dir.joinpath(f"{identity['terminal_id']}.log").write_text(
        "durable output\n", encoding="utf-8"
    )
    from cli_agent_orchestrator.services import terminal_service

    monkeypatch.setattr(terminal_service, "TERMINAL_LOG_DIR", log_dir)
    branch_ref = f"refs/heads/{managed.branch}"
    assert _git(identity["source"], "show-ref", "--verify", branch_ref)

    candidate = _candidate(identity)
    assert candidate.action == "retire"
    report = _execute(candidate, tmp_path)

    assert report.ok is True
    assert report.executed == [candidate.canonical_identity]
    assert report.reclaimed_bytes_by_class["session_workspaces"] == candidate.bytes
    assert not Path(managed.path).exists()
    assert _git(identity["source"], "show-ref", "--verify", branch_ref)
    context = database.get_writable_work_context_by_session(identity["session_id"])
    assert context is not None and context["state"] == "retired"
    with database.SessionLocal() as db:
        assert db.get(TerminalModel, identity["terminal_id"]) is not None
        assert db.query(InboxModel).filter_by(receiver_id=identity["terminal_id"]).count() == 1
    history = database.list_terminal_ui_session_page(limit=10, offset=0)
    assert history["items"][0]["workspace_state"] == "retired"
    assert terminal_service.get_output(identity["terminal_id"]) == "durable output\n"

    refused = database.prepare_workflow_input(
        identity["terminal_id"],
        "must not recreate workspace",
        request_id="retired-input",
        require_live_terminal=True,
    )
    assert refused == {"accepted": False, "reason_code": "WORKSPACE_RETIRED"}
    with pytest.raises(WritableWorkContextConflict, match="WORKSPACE_RETIRED"):
        database.create_terminal(
            "replacement-agent",
            f"cao-history-{identity['session_id'].removeprefix('session-')}",
            "developer",
            "codex",
            session_lifetime_id=identity["session_id"],
            writable_work_context_id=identity["context_id"],
        )

    repeated = _candidate(identity)
    assert repeated.action == "preserve"
    assert repeated.protection_reason == "WORKSPACE_ALREADY_RETIRED"


def test_dirty_workspace_is_preserved_automatically_but_full_cleanup_override_retires_it(
    workspace_factory, tmp_path
):
    identity = workspace_factory()
    dirty = Path(identity["managed"].path) / "unfinished.txt"
    dirty.write_text("uncommitted\n", encoding="utf-8")

    automatic = _candidate(identity)
    assert automatic.action == "preserve"
    assert automatic.protection_reason == "WORKTREE_DIRTY"
    assert Path(identity["managed"].path).exists()

    destructive = _candidate(identity, allow_dirty=True)
    assert destructive.action == "retire"
    assert dict(destructive.attributes)["allow_dirty"] == "true"
    report = _execute(destructive, tmp_path)
    assert report.ok is True
    assert not Path(identity["managed"].path).exists()
    assert _git(
        identity["source"],
        "show-ref",
        "--verify",
        f"refs/heads/{identity['managed'].branch}",
    )


def test_dirty_override_fails_closed_when_changed_file_contents_change(workspace_factory, tmp_path):
    identity = workspace_factory()
    dirty = Path(identity["managed"].path) / "unfinished.txt"
    dirty.write_text("first\n", encoding="utf-8")
    candidate = _candidate(identity, allow_dirty=True)
    dirty.write_text("other\n", encoding="utf-8")

    report = _execute(candidate, tmp_path)

    assert report.ok is False
    assert report.executed == []
    assert report.failures == [
        {
            "candidate": candidate.canonical_identity,
            "reason_code": "RuntimeError",
        }
    ]
    assert dirty.read_text(encoding="utf-8") == "other\n"


@pytest.mark.parametrize(
    ("authority", "expected"),
    [
        ("ready", "WORKTREE_ACTIVE"),
        ("processing", "WORKTREE_ACTIVE"),
        ("owner_gate", "OWNER_GATE"),
        ("open_workflow", "WORKFLOW_OPEN"),
        ("queued", "QUEUED_WORK"),
        ("waiting_resource", "QUEUED_WORK"),
        ("provider", "PROVIDER_EXECUTION_ACTIVE"),
        ("writer", "WRITER_LEASE_ACTIVE"),
        ("recovery", "RECOVERY_PROTECTED"),
        ("recovery_required", "RECOVERY_PROTECTED"),
    ],
)
def test_active_waiting_and_recovery_authority_never_auto_retires(
    workspace_factory, authority, expected
):
    identity = workspace_factory()
    with database.SessionLocal() as db:
        terminal = db.get(TerminalModel, identity["terminal_id"])
        assert terminal is not None
        if authority in {"ready", "processing", "recovery_required"}:
            terminal.runtime_lifecycle = (
                "recovery_required" if authority == "recovery_required" else "running"
            )
        elif authority == "recovery":
            terminal.runtime_operation_kind = "recovery_takeover"
            terminal.runtime_operation_token = "recovery-claim"
        elif authority in {"owner_gate", "open_workflow", "queued", "waiting_resource"}:
            workflow = WorkflowModel(
                root_terminal_id=identity["terminal_id"],
                status=(
                    "owner_gate"
                    if authority == "owner_gate"
                    else "open" if authority == "open_workflow" else "completed"
                ),
            )
            db.add(workflow)
            db.flush()
            if authority in {"queued", "waiting_resource"}:
                db.add(
                    WorkflowTurnModel(
                        workflow_id=workflow.id,
                        kind="external_input",
                        dedupe_key=f"{authority}-turn",
                        payload="queued",
                        state="queued",
                        queue_reason=(
                            "RESOURCE_PRESSURE_WAIT" if authority == "waiting_resource" else None
                        ),
                    )
                )
        elif authority == "provider":
            db.add(
                ProviderExecutionLeaseModel(
                    terminal_id=identity["terminal_id"], workflow_turn_id=9001
                )
            )
        elif authority == "writer":
            db.add(
                WorktreeWriterLeaseModel(
                    canonical_worktree=identity["managed"].path,
                    terminal_id=identity["terminal_id"],
                    authority_generation="writer-active",
                )
            )
        db.commit()

    candidate = _candidate(identity)
    assert candidate.action == "preserve"
    assert candidate.protection_reason == expected
    assert Path(identity["managed"].path).exists()


def test_durable_active_blocker_skips_git_inspection(workspace_factory, monkeypatch):
    identity = workspace_factory(lifecycle="running")
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.workspace_retirement_service.managed_worktree_status",
        lambda _terminal: pytest.fail("active worktree must not be inspected"),
    )

    candidate = _candidate(identity)

    assert candidate.action == "preserve"
    assert candidate.protection_reason == "WORKTREE_ACTIVE"
    assert candidate.bytes == 0


def test_missing_managed_worktree_metadata_fails_closed(workspace_factory):
    identity = workspace_factory()
    with database.SessionLocal() as db:
        terminal = db.get(TerminalModel, identity["terminal_id"])
        assert terminal is not None
        terminal.managed_worktree_kind = None
        db.commit()

    candidate = _candidate(identity)

    assert candidate.action == "preserve"
    assert candidate.protection_reason == "MANAGED_WORKTREE_METADATA_INVALID"
    assert Path(identity["managed"].path).exists()


@pytest.mark.parametrize("removal_before_restart", [False, True])
def test_retiring_workspace_converges_across_restart_and_second_reconcile_is_noop(
    workspace_factory, monkeypatch, removal_before_restart
):
    identity = workspace_factory()
    candidate = _claim(identity)
    plan = json.loads(dict(candidate.attributes)["retirement_plan_json"])
    if removal_before_restart:
        removed = managed_worktree_service.remove_managed_worktree(
            {
                "id": identity["terminal_id"],
                "writable_work_context_id": identity["context_id"],
                "launch_worktree": identity["managed"].path,
                "managed_worktree_kind": "supervisor",
                "managed_worktree_source": identity["managed"].source,
                "managed_worktree_branch": identity["managed"].branch,
                "managed_worktree_commit": identity["managed"].commit,
            },
            expected_status=plan["worktrees"][0],
        )
        assert removed["removed"] is True
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.context_lifecycle_fence",
        lambda **_kwargs: nullcontext(True),
    )

    assert reconcile_retiring_session_workspaces() == 1
    assert reconcile_retiring_session_workspaces() == 0
    context = database.get_writable_work_context_by_session(identity["session_id"])
    assert context is not None and context["state"] == "retired"
    assert not Path(identity["managed"].path).exists()
    assert _git(
        identity["source"],
        "show-ref",
        "--verify",
        f"refs/heads/{identity['managed'].branch}",
    )


@pytest.mark.parametrize("removal_before_restart", [False, True])
def test_confirmed_dirty_retirement_authority_survives_restart_exactly(
    workspace_factory, monkeypatch, removal_before_restart
):
    identity = workspace_factory()
    dirty = Path(identity["managed"].path, "unfinished.txt")
    dirty.write_text("owner approved deletion\n", encoding="utf-8")
    candidate = _claim(identity, allow_dirty=True)
    plan = json.loads(dict(candidate.attributes)["retirement_plan_json"])
    if removal_before_restart:
        snapshot = database.get_session_workspace_retirement_snapshot(identity["context_id"])
        assert snapshot is not None
        removed = managed_worktree_service.remove_managed_worktree(
            snapshot["terminals"][0],
            allow_dirty=True,
            expected_status=plan["worktrees"][0],
        )
        assert removed["removed"] is True
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.context_lifecycle_fence",
        lambda **_kwargs: nullcontext(True),
    )

    assert reconcile_retiring_session_workspaces() == 1
    assert reconcile_retiring_session_workspaces() == 0
    assert not Path(identity["managed"].path).exists()
    context = database.get_writable_work_context_by_session(identity["session_id"])
    assert context is not None
    assert context["state"] == "retired"
    assert context["retirement_allow_dirty"] is True
    # Replaying a candidate from the original plan is now historical only.
    assert candidate.canonical_identity == f"session-workspace:{identity['context_id']}"


def test_confirmed_dirty_retirement_rejects_changed_contents_after_claim(
    workspace_factory, monkeypatch
):
    identity = workspace_factory()
    dirty = Path(identity["managed"].path, "unfinished.txt")
    dirty.write_text("owner approved deletion\n", encoding="utf-8")
    candidate = _claim(identity, allow_dirty=True)
    dirty.write_text("changed after confirmation\n", encoding="utf-8")
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.context_lifecycle_fence",
        lambda **_kwargs: nullcontext(True),
    )

    assert reconcile_retiring_session_workspaces() == 0
    assert dirty.read_text(encoding="utf-8") == "changed after confirmation\n"
    context = database.get_writable_work_context_by_session(identity["session_id"])
    assert context is not None and context["state"] == "retiring"
    assert context["retirement_plan_json"] == dict(candidate.attributes)["retirement_plan_json"]


def test_final_git_removal_rejects_contents_changed_after_claim(workspace_factory):
    identity = workspace_factory()
    dirty = Path(identity["managed"].path, "unfinished.txt")
    dirty.write_text("owner approved deletion\n", encoding="utf-8")
    candidate = _claim(identity, allow_dirty=True)
    approved = json.loads(dict(candidate.attributes)["retirement_plan_json"])["worktrees"][0]
    dirty.write_text("changed at final boundary\n", encoding="utf-8")
    snapshot = database.get_session_workspace_retirement_snapshot(identity["context_id"])
    assert snapshot is not None

    removed = managed_worktree_service.remove_managed_worktree(
        snapshot["terminals"][0],
        allow_dirty=True,
        expected_status=approved,
    )

    assert removed["removed"] is False
    assert removed["reason_code"] == "WORKSPACE_AUTHORITY_CHANGED"
    assert dirty.read_text(encoding="utf-8") == "changed at final boundary\n"


def test_legacy_retiring_dirty_workspace_without_content_plan_fails_closed(
    workspace_factory, monkeypatch
):
    identity = workspace_factory(state="retiring")
    dirty = Path(identity["managed"].path, "unfinished.txt")
    dirty.write_text("unbound historical bytes\n", encoding="utf-8")
    with database.SessionLocal() as db:
        context = db.get(WritableWorkContextModel, identity["context_id"])
        assert context is not None
        context.retirement_allow_dirty = True
        db.commit()
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.context_lifecycle_fence",
        lambda **_kwargs: nullcontext(True),
    )

    assert reconcile_retiring_session_workspaces() == 0
    assert dirty.read_text(encoding="utf-8") == "unbound historical bytes\n"
    candidate = _candidate(identity)
    assert candidate.action == "preserve"
    assert candidate.protection_reason == "WORKSPACE_RETIREMENT_AUTHORITY_MISSING"


def test_housekeeping_inventory_reports_exact_skip_reason(workspace_factory):
    identity = workspace_factory()
    Path(identity["managed"].path, "dirty.txt").write_text("keep\n", encoding="utf-8")

    candidates = plan_session_workspaces()

    candidate = next(
        item for item in candidates if item.canonical_identity.endswith(identity["context_id"])
    )
    assert candidate.action == "preserve"
    assert candidate.protection_reason == "WORKTREE_DIRTY"
    plan = finalize_plan(
        generated_at=2_000_000_000.0,
        mode="frequent",
        root=identity["source"],
        candidates=candidates,
        warnings=[],
    )
    assert plan.class_summaries["session_workspaces"]["protection_reasons"] == {"WORKTREE_DIRTY": 1}
