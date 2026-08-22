"""Focused P1 lifecycle/result fences for acknowledged assigned-child retirement."""

import asyncio
import subprocess
import threading
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import cli_agent_orchestrator.clients.database as database
from cli_agent_orchestrator.clients.database import (
    Base,
    ChildAssignmentModel,
    DelegationResultModel,
    TerminalModel,
    WorkflowEffectModel,
    WorkflowModel,
    WorktreeWriterLeaseModel,
    acknowledge_child_assignment_result,
    claim_completed_assigned_child_retirement,
    claim_completed_handoff_child_retirement,
    claim_workflow_effect,
    claim_workflow_turn_receipt,
    complete_child_retirement,
    create_child_assignment_result_message,
    create_terminal,
    get_assigned_child_retirement_cleanup_intent,
    get_child_retirement_cleanup_intent,
    get_delegation_result,
    get_delegation_result_for_assignment,
    mark_child_assignment_result_delivered,
    mark_terminal_runtime_exited,
    register_child_assignment,
    register_handoff_child,
    reserve_completed_assigned_child_retirement_exit,
    set_workflow_terminal_state,
    start_workflow_input,
)
from cli_agent_orchestrator.mcp_server import server as mcp_server
from cli_agent_orchestrator.models.inbox import ChildAssignmentStatus
from cli_agent_orchestrator.models.result import DelegationResultStatus
from cli_agent_orchestrator.runtime_generation import RUNTIME_GENERATION_ENV
from cli_agent_orchestrator.services import (
    housekeeping_service,
    managed_worktree_service,
    operations_service,
)


@pytest.fixture
def resource_db(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(database, "_child_assignment_schema_ready", True)
    monkeypatch.setattr(database, "_delegation_result_schema_ready", True)


@pytest.fixture
def sqlite_interleaving_db(monkeypatch, tmp_path):
    """Use independent SQLite connections for a deterministic writer race."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'resource-hygiene.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(database, "_child_assignment_schema_ready", True)
    monkeypatch.setattr(database, "_delegation_result_schema_ready", True)


def _admit(parent: str, monkeypatch) -> int:
    turn_id = start_workflow_input(parent)
    assert turn_id is not None
    assert claim_workflow_turn_receipt(parent, turn_id)
    monkeypatch.setenv("CAO_TERMINAL_ID", parent)
    monkeypatch.delenv(RUNTIME_GENERATION_ENV, raising=False)
    monkeypatch.setattr(mcp_server, "_SIDECAR_RUNTIME_GENERATION", None)
    return turn_id


def _metadata(child: str) -> None:
    with database.SessionLocal() as db:
        db.add(
            TerminalModel(
                id=child,
                tmux_session="cao-resource-hygiene",
                tmux_window=f"window-{child}",
                provider="codex",
                agent_profile="developer",
            )
        )
        db.commit()


def _acknowledged_child(
    parent: str, child: str, monkeypatch, terminal=True, create_metadata=True
) -> str:
    assert register_child_assignment(parent, child)
    child_turn = start_workflow_input(child)
    assert child_turn is not None
    assert claim_workflow_turn_receipt(child, child_turn)
    callback_effect = claim_workflow_effect(child, child_turn, "send_message", "p1-result")
    assert callback_effect is not None
    notice, duplicate = create_child_assignment_result_message(
        child,
        parent,
        "P1 completed result",
        workflow_effect_id=callback_effect["id"],
        workflow_turn_id=child_turn,
    )
    assert notice is not None and duplicate is False and notice.result_id is not None
    assert mark_child_assignment_result_delivered(notice.id)
    assert acknowledge_child_assignment_result(parent, child, notice.result_id)
    if terminal:
        assert set_workflow_terminal_state(child, "terminal", "child completed")
    if create_metadata:
        _metadata(child)
    return notice.result_id


def _running_terminal(child: str) -> tuple[str, str]:
    return ("completed", "running")


def _completed_handoff_child(
    parent: str,
    child: str,
    *,
    kind: str = "task",
    source: str = "/srv/agent-control/sources/cli-agent-orchestrator-v2.1.1",
    commit: str = "5e3a43d2e42d8a9b6448e3c550d139ff4ff4d409",
) -> str:
    """Persist the exact post-exit direct-handoff shape eligible for cleanup."""
    assert register_handoff_child(parent, child)
    child_turn = start_workflow_input(child)
    assert child_turn is not None and claim_workflow_turn_receipt(child, child_turn)
    assert set_workflow_terminal_state(child, "terminal", "handoff completed")
    branch = f"cao/task/{child}" if kind == "task" else None
    create_terminal(
        child,
        "cao-resource-hygiene",
        f"window-{child}",
        "codex",
        "developer" if kind == "task" else "reviewer",
        launch_worktree=f"/srv/agent-control/state/cao/worktrees/fixture/task-{child}",
        write_enabled=True,
        context_role="work",
        managed_worktree_kind=kind,
        managed_worktree_source=source,
        managed_worktree_branch=branch,
        managed_worktree_commit=commit,
    )
    with database.SessionLocal() as db:
        terminal = db.query(TerminalModel).filter_by(id=child).one()
        terminal.runtime_lifecycle = "exited"
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        assignment.status = ChildAssignmentStatus.HANDOFF_RESULT_DELIVERED.value
        assignment.cleanup_acknowledged = True
        result = db.query(DelegationResultModel).filter_by(child_assignment_id=assignment.id).one()
        result.delegation_kind = "handoff"
        result.parent_terminal_id = parent
        result.child_terminal_id = child
        result.status = DelegationResultStatus.COMPLETE.value
        result.finalized_at = datetime.now()
        result.document_json = '{"body_markdown":"complete","format":"v1"}'
        db.commit()
        return result.id


def _historical_assigned_child(
    monkeypatch, tmp_path
) -> tuple[str, str, str, int, str, Path, str, str]:
    """Create the isolated A/B/child recovery shape from the owner decision."""
    original_parent, supervisor, child = "b7994fbd", "017aeafd", "8a175343"
    session_name = "cao-historical-assigned-recovery"
    repository = tmp_path / "source"
    repository.mkdir()
    subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "CAO Test"], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "cao-test@example.invalid"],
        check=True,
    )
    (repository / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "baseline"], check=True)
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")
    managed = managed_worktree_service.create_managed_worktree(str(repository), child, "task")
    assert managed is not None and managed.branch == f"cao/task/{child}"

    create_terminal(
        original_parent,
        session_name,
        f"window-{original_parent}",
        "codex",
        "supervisor",
        launch_worktree=str(repository.resolve()),
        write_enabled=False,
        context_role="supervisor",
    )
    create_terminal(
        supervisor,
        session_name,
        f"window-{supervisor}",
        "codex",
        "supervisor",
        launch_worktree=str(repository.resolve()),
        write_enabled=False,
        context_role="supervisor",
    )
    create_terminal(
        child,
        session_name,
        f"window-{child}",
        "codex",
        "developer",
        launch_worktree=managed.path,
        write_enabled=True,
        context_role="work",
        managed_worktree_kind=managed.kind,
        managed_worktree_source=managed.source,
        managed_worktree_branch=managed.branch,
        managed_worktree_commit=managed.commit,
    )
    assert start_workflow_input(original_parent) is not None
    result_id = _acknowledged_child(original_parent, child, monkeypatch, create_metadata=False)
    assert set_workflow_terminal_state(original_parent, "terminal", "replaced supervisor")
    assert mark_terminal_runtime_exited(original_parent)
    assert mark_terminal_runtime_exited(child)
    turn_id = _admit(supervisor, monkeypatch)
    return (
        original_parent,
        supervisor,
        child,
        turn_id,
        result_id,
        repository,
        managed.path,
        managed.branch,
    )


def _api_terminal_response(mocker, status: str, lifecycle: str):
    response = mocker.Mock()
    response.json.return_value = {"status": status, "lifecycle": lifecycle}
    return response


def test_p1_canonical_api_completed_running_exits_without_provider_observation(
    resource_db, monkeypatch, mocker
):
    parent, child = "parent-p1-ok", "child-p1-ok"
    turn_id = _admit(parent, monkeypatch)
    result_id = _acknowledged_child(parent, child, monkeypatch)
    get_terminal = mocker.patch.object(
        mcp_server.requests,
        "get",
        return_value=_api_terminal_response(mocker, "completed", "running"),
    )
    provider_state = mocker.patch.object(
        mcp_server.terminal_service,
        "get_terminal",
        return_value={"id": child, "status": "processing", "lifecycle": "running"},
    )
    exit_terminal = mocker.patch.object(
        mcp_server.terminal_service, "exit_terminal", return_value=True
    )

    result = asyncio.run(mcp_server.retire_completed_child(turn_id, child))

    assert result == {"success": True, "child_terminal_id": child, "status": "retired"}
    assert get_terminal.call_count == 2
    assert get_terminal.call_args.args[0] == f"{mcp_server.API_BASE_URL}/terminals/{child}"
    provider_state.assert_not_called()
    exit_terminal.assert_called_once_with(child)
    assert get_delegation_result(result_id)["status"] == "complete"


def test_p1_success_removes_managed_assigned_task_worktree_but_preserves_history(
    resource_db, monkeypatch, mocker, tmp_path
):
    parent, child = "parent-p1-managed", "child-p1-managed"
    repository = tmp_path / "source"
    repository.mkdir()
    subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "CAO Test"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "config",
            "user.email",
            "cao-test@example.invalid",
        ],
        check=True,
    )
    (repository / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "baseline"], check=True)
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")

    turn_id = _admit(parent, monkeypatch)
    result_id = _acknowledged_child(parent, child, monkeypatch)
    managed = managed_worktree_service.create_managed_worktree(str(repository), child, "task")
    assert managed is not None
    with database.SessionLocal() as db:
        metadata = db.query(TerminalModel).filter_by(id=child).one()
        metadata.launch_worktree = managed.path
        metadata.managed_worktree_kind = managed.kind
        metadata.managed_worktree_source = managed.source
        metadata.managed_worktree_branch = managed.branch
        metadata.managed_worktree_commit = managed.commit
        db.commit()
    mocker.patch.object(mcp_server, "_read_terminal_state", return_value=_running_terminal(child))
    mocker.patch.object(mcp_server.terminal_service, "exit_terminal", return_value=True)

    result = asyncio.run(mcp_server.retire_completed_child(turn_id, child))

    assert result == {"success": True, "child_terminal_id": child, "status": "retired"}
    assert not Path(managed.path).exists()
    registered = subprocess.run(
        ["git", "-C", str(repository), "worktree", "list", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert managed.path not in registered
    assert (
        subprocess.run(
            ["git", "-C", str(repository), "show-ref", "--verify", f"refs/heads/{managed.branch}"],
            check=False,
        ).returncode
        == 0
    )
    assert get_delegation_result(result_id) is not None
    with database.SessionLocal() as db:
        assert db.query(TerminalModel).filter_by(id=child).one().tmux_window == f"window-{child}"

    retry_turn = _admit(parent, monkeypatch)
    retry = asyncio.run(mcp_server.retire_completed_child(retry_turn, child))
    assert retry == {
        "success": True,
        "child_terminal_id": child,
        "status": "already_retired",
        "already_retired": True,
    }


def test_p1_already_retired_retry_reconciles_retained_task_worktree(
    resource_db, monkeypatch, mocker, tmp_path
):
    parent, child = "parent-p1-retained", "child-p1-retained"
    repository = tmp_path / "source"
    repository.mkdir()
    subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "CAO Test"], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "cao-test@example.invalid"],
        check=True,
    )
    (repository / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "baseline"], check=True)
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")

    result_id = _acknowledged_child(parent, child, monkeypatch, create_metadata=False)
    managed = managed_worktree_service.create_managed_worktree(str(repository), child, "task")
    assert managed is not None and managed.branch is not None
    create_terminal(
        child,
        "cao-resource-hygiene",
        f"window-{child}",
        "codex",
        "developer",
        launch_worktree=managed.path,
        write_enabled=True,
        context_role="work",
        managed_worktree_kind=managed.kind,
        managed_worktree_source=managed.source,
        managed_worktree_branch=managed.branch,
        managed_worktree_commit=managed.commit,
    )
    (Path(managed.path) / "durable.txt").write_text("child history\n", encoding="utf-8")
    subprocess.run(["git", "-C", managed.path, "add", "durable.txt"], check=True)
    subprocess.run(
        ["git", "-C", managed.path, "commit", "-qm", "durable child history"], check=True
    )
    durable_commit = subprocess.run(
        ["git", "-C", managed.path, "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    fence = claim_completed_assigned_child_retirement(parent, child)
    assert fence["eligible"] is True
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        assignment.retirement_claim_token = None
        assignment.retirement_exit_dispatched_at = datetime.now()
        assignment.retirement_completed_at = datetime.now()
        assignment.retirement_cleanup_intent = None
        db.commit()
    assert mark_terminal_runtime_exited(child)
    mocker.patch.object(mcp_server, "_read_terminal_state", return_value=("completed", "exited"))
    assert Path(managed.path).is_dir()
    with database.SessionLocal() as db:
        assert db.query(WorktreeWriterLeaseModel).count() == 0

    turn_id = _admit(parent, monkeypatch)
    result = asyncio.run(mcp_server.retire_completed_child(turn_id, child))

    assert result == {
        "success": True,
        "child_terminal_id": child,
        "status": "already_retired",
        "already_retired": True,
    }
    assert not Path(managed.path).exists()
    registration = subprocess.run(
        ["git", "-C", str(repository), "worktree", "list", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert managed.path not in registration
    assert (
        subprocess.run(
            ["git", "-C", str(repository), "rev-parse", managed.branch],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == durable_commit
    )
    assert get_delegation_result(result_id) is not None
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        metadata = db.query(TerminalModel).filter_by(id=child).one()
        assert assignment.retirement_completed_at is not None
        assert metadata.runtime_lifecycle == "exited"
    with monkeypatch.context() as capacity_patch:
        capacity_patch.setattr(
            operations_service.subprocess,
            "run",
            lambda *args, **kwargs: pytest.fail("exited history must not be probed for capacity"),
        )
        assert operations_service._active_contexts() == []

    retry_turn = _admit(parent, monkeypatch)
    retry = asyncio.run(mcp_server.retire_completed_child(retry_turn, child))
    assert retry == result


def test_p1_already_retired_retry_is_recoverable_when_cleanup_identity_is_unproven(
    resource_db, monkeypatch, mocker
):
    parent, child = "parent-p1-unsafe-cleanup", "child-p1-unsafe-cleanup"
    _acknowledged_child(parent, child, monkeypatch)
    fence = claim_completed_assigned_child_retirement(parent, child)
    assert fence["eligible"] is True
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        assignment.retirement_claim_token = None
        assignment.retirement_exit_dispatched_at = datetime.now()
        assignment.retirement_completed_at = datetime.now()
        assignment.retirement_cleanup_intent = None
        db.commit()
    cleanup = mocker.patch.object(
        mcp_server.terminal_service,
        "cleanup_managed_worktree",
        side_effect=RuntimeError("MANAGED_WORKTREE_IDENTITY_MISMATCH"),
    )
    mocker.patch.object(mcp_server, "_read_terminal_state", return_value=("completed", "exited"))

    turn_id = _admit(parent, monkeypatch)
    result = asyncio.run(mcp_server.retire_completed_child(turn_id, child))

    assert result == {
        "success": False,
        "child_terminal_id": child,
        "status": "retirement_cleanup_pending",
        "recoverable": True,
        "error": "managed_worktree_cleanup_not_confirmed",
        "reason_code": "MANAGED_WORKTREE_CLEANUP_NOT_CONFIRMED",
        "detail": "MANAGED_WORKTREE_IDENTITY_MISMATCH",
    }
    cleanup.assert_called_once()


def test_p1_stale_supervisor_sidecar_cannot_report_assigned_child_retired(
    resource_db, monkeypatch, mocker
):
    parent, child = "parent-p1-stale-runtime", "child-p1-stale-runtime"
    turn_id = _admit(parent, monkeypatch)
    _acknowledged_child(parent, child, monkeypatch)
    monkeypatch.setattr(
        mcp_server, "_SIDECAR_RUNTIME_GENERATION", "generation-before-cleanup-contract"
    )
    # Reproduce the old fence's in-memory blessing.  The immutable launch
    # snapshot must still identify this process as stale.
    monkeypatch.setenv(RUNTIME_GENERATION_ENV, "generation-active")
    mocker.patch.object(mcp_server, "_active_runtime_generation", return_value="generation-active")
    claim_effect = mocker.patch.object(mcp_server, "claim_workflow_effect")
    claim_retirement = mocker.patch.object(mcp_server, "claim_completed_assigned_child_retirement")
    exit_terminal = mocker.patch.object(mcp_server.terminal_service, "exit_terminal")
    cleanup = mocker.patch.object(mcp_server.terminal_service, "cleanup_managed_worktree")
    finalize = mocker.patch.object(mcp_server, "complete_assigned_child_retirement")

    result = asyncio.run(mcp_server.retire_completed_child(turn_id, child))

    assert result == {
        "success": False,
        "child_terminal_id": child,
        "status": "runtime_reconnect_required",
        "recoverable": True,
        "error": (
            "CAO_SIDECAR_RECONNECT_REQUIRED: child retirement was not started; "
            "reconnect/reinitialize before retrying"
        ),
    }
    claim_effect.assert_not_called()
    claim_retirement.assert_not_called()
    exit_terminal.assert_not_called()
    cleanup.assert_not_called()
    finalize.assert_not_called()


@pytest.mark.parametrize("failure", ["connection", "http", "malformed"])
def test_p1_managed_sidecar_identity_failure_claims_no_privileged_effect(
    monkeypatch, mocker, failure
):
    monkeypatch.setattr(mcp_server, "_SIDECAR_RUNTIME_GENERATION", "managed-build")
    monkeypatch.setenv("CAO_TERMINAL_ID", "managed-parent")
    get = mocker.patch.object(mcp_server.requests, "get")
    if failure == "connection":
        get.side_effect = mcp_server.requests.ConnectionError("API restarting")
    elif failure == "http":
        get.return_value.raise_for_status.side_effect = mcp_server.requests.HTTPError(
            "generation endpoint unavailable"
        )
    else:
        get.return_value.raise_for_status.return_value = None
        get.return_value.json.return_value = {"generation": 7}
    claim_effect = mocker.patch.object(mcp_server, "claim_workflow_effect")

    with pytest.raises(
        mcp_server.SidecarRuntimeIdentityUnavailable,
        match="CAO_RUNTIME_GENERATION_UNAVAILABLE",
    ):
        mcp_server._claim_privileged_effect(41, "send_message", "receiver", "payload")

    claim_effect.assert_not_called()


def test_p1_current_supervisor_sidecar_runs_assigned_retirement_saga(
    resource_db, monkeypatch, mocker
):
    parent, child = "parent-p1-current-runtime", "child-p1-current-runtime"
    turn_id = _admit(parent, monkeypatch)
    _acknowledged_child(parent, child, monkeypatch)
    monkeypatch.setattr(mcp_server, "_SIDECAR_RUNTIME_GENERATION", "generation-current")
    mocker.patch.object(mcp_server, "_active_runtime_generation", return_value="generation-current")
    claim_effect = mocker.spy(mcp_server, "claim_workflow_effect")
    mocker.patch.object(mcp_server, "_read_terminal_state", return_value=("completed", "running"))
    exit_terminal = mocker.patch.object(
        mcp_server.terminal_service, "exit_terminal", return_value=True
    )

    result = asyncio.run(mcp_server.retire_completed_child(turn_id, child))

    assert result == {"success": True, "child_terminal_id": child, "status": "retired"}
    claim_effect.assert_called_once()
    exit_terminal.assert_called_once_with(child)


def test_p1_mid_retirement_identity_outage_releases_claim_then_fresh_turn_retries(
    resource_db, monkeypatch, mocker
):
    parent, child = "parent-p1-runtime-outage", "child-p1-runtime-outage"
    first_turn = _admit(parent, monkeypatch)
    _acknowledged_child(parent, child, monkeypatch)
    monkeypatch.setattr(mcp_server, "_SIDECAR_RUNTIME_GENERATION", "managed-build")
    active_generation = mocker.patch.object(
        mcp_server,
        "_active_runtime_generation",
        side_effect=["managed-build", "managed-build", None],
    )
    mocker.patch.object(
        mcp_server,
        "_read_terminal_state",
        return_value=("completed", "running"),
    )
    exit_terminal = mocker.patch.object(
        mcp_server.terminal_service, "exit_terminal", return_value=True
    )

    first = asyncio.run(mcp_server.retire_completed_child(first_turn, child))

    assert first["status"] == "runtime_identity_unavailable"
    assert "CAO_RUNTIME_GENERATION_UNAVAILABLE" in first["error"]
    assert "CAO_SIDECAR_RECONNECT_REQUIRED" not in first["error"]
    exit_terminal.assert_not_called()
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        assert assignment.retirement_claim_token is None
        assert assignment.retirement_exit_dispatched_at is None

    retry_turn = _admit(parent, monkeypatch)
    monkeypatch.setattr(mcp_server, "_SIDECAR_RUNTIME_GENERATION", "managed-build")
    active_generation.side_effect = None
    active_generation.return_value = "managed-build"

    retry = asyncio.run(mcp_server.retire_completed_child(retry_turn, child))

    assert retry == {"success": True, "child_terminal_id": child, "status": "retired"}
    exit_terminal.assert_called_once_with(child)


def test_c1_stale_sidecar_cannot_enter_direct_handoff_retirement(resource_db, monkeypatch, mocker):
    parent, child = "parent-c1-stale-handoff", "child-c1-stale-handoff"
    turn_id = _admit(parent, monkeypatch)
    _completed_handoff_child(parent, child)
    monkeypatch.setattr(mcp_server, "_SIDECAR_RUNTIME_GENERATION", "generation-old-code")
    monkeypatch.setenv(RUNTIME_GENERATION_ENV, "generation-current")
    mocker.patch.object(mcp_server, "_active_runtime_generation", return_value="generation-current")
    claim_effect = mocker.patch.object(mcp_server, "claim_workflow_effect")
    provider_exit = mocker.patch.object(mcp_server.requests, "post")
    claim_retirement = mocker.patch.object(mcp_server, "claim_completed_handoff_child_retirement")
    cleanup = mocker.patch.object(mcp_server.terminal_service, "cleanup_managed_worktree")

    result = asyncio.run(mcp_server.await_handoff(turn_id, child, timeout=1))

    assert result.state.value == "failed"
    assert "CAO_SIDECAR_RECONNECT_REQUIRED" in result.message
    claim_effect.assert_not_called()
    provider_exit.assert_not_called()
    claim_retirement.assert_not_called()
    cleanup.assert_not_called()


def test_p1_rejects_unacknowledged_result_without_exit(resource_db, monkeypatch, mocker):
    parent, child = "parent-p1-unacked", "child-p1-unacked"
    turn_id = _admit(parent, monkeypatch)
    _acknowledged_child(parent, child, monkeypatch)
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        assignment.status = "result_delivered"
        db.commit()
    exit_terminal = mocker.patch.object(mcp_server.terminal_service, "exit_terminal")

    result = asyncio.run(mcp_server.retire_completed_child(turn_id, child))

    assert result["error"] == "assigned_result_not_acknowledged"
    exit_terminal.assert_not_called()


@pytest.mark.parametrize("workflow_state", ["open", "owner_gate"])
def test_p1_rejects_open_or_owner_gated_child_workflow(
    resource_db, monkeypatch, mocker, workflow_state
):
    parent, child = f"parent-p1-{workflow_state}", f"child-p1-{workflow_state}"
    turn_id = _admit(parent, monkeypatch)
    _acknowledged_child(parent, child, monkeypatch, terminal=False)
    if workflow_state == "owner_gate":
        assert set_workflow_terminal_state(child, "owner_gate", "owner action")
    exit_terminal = mocker.patch.object(mcp_server.terminal_service, "exit_terminal")

    result = asyncio.run(mcp_server.retire_completed_child(turn_id, child))

    # Resource retirement checks terminal availability before a workflow
    # state that cannot be safely inspected; owner-gated metadata remains a
    # workflow-state rejection when the child terminal is available.
    assert result["error"] == (
        "child_terminal_unavailable" if workflow_state == "open" else "child_workflow_not_terminal"
    )
    exit_terminal.assert_not_called()


def test_p1_rejects_foreign_or_self_child(resource_db, monkeypatch, mocker):
    parent, foreign, child = "parent-p1-foreign", "foreign-p1", "child-p1-foreign"
    turn_id = _admit(parent, monkeypatch)
    _acknowledged_child(foreign, child, monkeypatch)
    exit_terminal = mocker.patch.object(mcp_server.terminal_service, "exit_terminal")

    foreign_result = asyncio.run(mcp_server.retire_completed_child(turn_id, child))
    self_result = asyncio.run(mcp_server.retire_completed_child(turn_id, parent))

    assert foreign_result["error"] == "child_assignment_not_owned"
    assert self_result["error"] == "child_terminal_id must not identify the caller"
    exit_terminal.assert_not_called()


def test_historical_assigned_child_recovery_preserves_provenance_and_history(
    resource_db, monkeypatch, mocker, tmp_path
):
    (
        original_parent,
        supervisor,
        child,
        turn_id,
        result_id,
        repository,
        managed_path,
        branch,
    ) = _historical_assigned_child(monkeypatch, tmp_path)
    (Path(managed_path) / "durable.txt").write_text("historical child result\n", encoding="utf-8")
    subprocess.run(["git", "-C", managed_path, "add", "durable.txt"], check=True)
    subprocess.run(
        ["git", "-C", managed_path, "commit", "-qm", "retain historical child"], check=True
    )
    durable_commit = subprocess.run(
        ["git", "-C", managed_path, "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    cleanup_managed_worktree = mcp_server.terminal_service.cleanup_managed_worktree

    def cleanup_after_intent(intent):
        with database.SessionLocal() as db:
            assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
            assert assignment.parent_terminal_id == original_parent
            assert assignment.retirement_cleanup_intent is not None
            assert assignment.retirement_exit_dispatched_at is not None
        cleanup_managed_worktree(intent)

    cleanup = mocker.patch.object(
        mcp_server.terminal_service,
        "cleanup_managed_worktree",
        side_effect=cleanup_after_intent,
    )
    exit_terminal = mocker.patch.object(mcp_server.terminal_service, "exit_terminal")
    mocker.patch.object(mcp_server, "_read_terminal_state", return_value=("completed", "exited"))

    result = asyncio.run(mcp_server.retire_completed_child(turn_id, child))

    assert result == {
        "success": True,
        "child_terminal_id": child,
        "status": "already_retired",
        "already_retired": True,
    }
    cleanup.assert_called_once()
    exit_terminal.assert_not_called()
    assert not Path(managed_path).exists()
    registration = subprocess.run(
        ["git", "-C", str(repository), "worktree", "list", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert managed_path not in registration
    assert (
        subprocess.run(
            ["git", "-C", str(repository), "rev-parse", branch],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == durable_commit
    )
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        durable_result = db.query(DelegationResultModel).filter_by(id=result_id).one()
        child_metadata = db.query(TerminalModel).filter_by(id=child).one()
        child_workflow = db.query(WorkflowModel).filter_by(root_terminal_id=child).one()
        assert assignment.parent_terminal_id == original_parent
        assert durable_result.parent_terminal_id == original_parent
        assert child_metadata.runtime_lifecycle == "exited"
        assert child_workflow.status == "terminal"

    retry = asyncio.run(mcp_server.retire_completed_child(_admit(supervisor, monkeypatch), child))
    assert retry == result


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ("parent_runtime_active", "historical_parent_runtime_active"),
        ("parent_workflow_active", "historical_parent_workflow_active"),
        ("different_session", "retirement_session_mismatch"),
        ("non_supervisor", "child_assignment_not_owned"),
        ("result_incomplete", "delegation_result_not_complete"),
        ("result_unacknowledged", "assigned_result_not_acknowledged"),
        ("child_running", "child_runtime_not_exited"),
        ("child_capacity_retained", "child_capacity_not_released"),
        ("uncertain_identity", "retirement_cleanup_identity_unproven"),
    ],
)
def test_historical_assigned_child_recovery_fails_closed(
    resource_db, monkeypatch, mocker, tmp_path, mutation, error
):
    original_parent, supervisor, child, turn_id, result_id, *_rest = _historical_assigned_child(
        monkeypatch, tmp_path
    )
    with database.SessionLocal() as db:
        parent_metadata = db.query(TerminalModel).filter_by(id=original_parent).one()
        supervisor_metadata = db.query(TerminalModel).filter_by(id=supervisor).one()
        child_metadata = db.query(TerminalModel).filter_by(id=child).one()
        parent_workflow = db.query(WorkflowModel).filter_by(root_terminal_id=original_parent).one()
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        durable_result = db.query(DelegationResultModel).filter_by(id=result_id).one()
        if mutation == "parent_runtime_active":
            parent_metadata.runtime_lifecycle = "running"
        elif mutation == "parent_workflow_active":
            parent_workflow.status = "open"
        elif mutation == "different_session":
            parent_metadata.tmux_session = "cao-other-session"
        elif mutation == "non_supervisor":
            supervisor_metadata.context_role = "work"
        elif mutation == "result_incomplete":
            durable_result.finalized_at = None
        elif mutation == "result_unacknowledged":
            assignment.status = ChildAssignmentStatus.RESULT_DELIVERED.value
        elif mutation == "child_running":
            child_metadata.runtime_lifecycle = "running"
        elif mutation == "child_capacity_retained":
            db.add(
                WorktreeWriterLeaseModel(
                    canonical_worktree=child_metadata.launch_worktree,
                    terminal_id=child,
                )
            )
        elif mutation == "uncertain_identity":
            child_metadata.managed_worktree_commit = "unknown"
        db.commit()
    cleanup = mocker.patch.object(mcp_server.terminal_service, "cleanup_managed_worktree")
    exit_terminal = mocker.patch.object(mcp_server.terminal_service, "exit_terminal")

    result = asyncio.run(mcp_server.retire_completed_child(turn_id, child))

    assert result["success"] is False
    assert result["error"] == error
    cleanup.assert_not_called()
    exit_terminal.assert_not_called()
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        assert assignment.parent_terminal_id == original_parent
        assert assignment.retirement_cleanup_intent is None
        assert assignment.retirement_claim_token is None


def test_historical_assigned_child_rechecks_parent_inactivity_before_cleanup(
    resource_db, monkeypatch, mocker, tmp_path
):
    original_parent, _supervisor, child, turn_id, _result_id, *_rest = _historical_assigned_child(
        monkeypatch, tmp_path
    )

    def parent_restarts_before_cleanup(_child):
        with database.SessionLocal() as db:
            parent = db.query(TerminalModel).filter_by(id=original_parent).one()
            parent.runtime_lifecycle = "running"
            db.commit()
        return ("completed", "exited")

    mocker.patch.object(
        mcp_server, "_read_terminal_state", side_effect=parent_restarts_before_cleanup
    )
    cleanup = mocker.patch.object(mcp_server.terminal_service, "cleanup_managed_worktree")

    result = asyncio.run(mcp_server.retire_completed_child(turn_id, child))

    assert result["error"] == "historical_retirement_authority_lost"
    cleanup.assert_not_called()
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        assert assignment.parent_terminal_id == original_parent
        assert assignment.retirement_cleanup_intent is not None
        assert assignment.retirement_cleanup_completed_at is None


def test_p1_rejects_handoff_child_without_touching_handoff_cleanup(
    resource_db, monkeypatch, mocker
):
    parent, child = "parent-p1-handoff", "child-p1-handoff"
    turn_id = _admit(parent, monkeypatch)
    assert register_handoff_child(parent, child)
    exit_terminal = mocker.patch.object(mcp_server.terminal_service, "exit_terminal")

    result = asyncio.run(mcp_server.retire_completed_child(turn_id, child))

    assert result["error"] == "handoff_child_not_retireable"
    exit_terminal.assert_not_called()


@pytest.mark.parametrize("result_status", ["incomplete", "cancelled"])
def test_p1_rejects_incomplete_or_cancelled_result(resource_db, monkeypatch, mocker, result_status):
    parent, child = f"parent-p1-{result_status}", f"child-p1-{result_status}"
    turn_id = _admit(parent, monkeypatch)
    _acknowledged_child(parent, child, monkeypatch)
    with database.SessionLocal() as db:
        result = db.query(DelegationResultModel).filter_by(child_terminal_id=child).one()
        result.status = result_status
        db.commit()
    exit_terminal = mocker.patch.object(mcp_server.terminal_service, "exit_terminal")

    response = asyncio.run(mcp_server.retire_completed_child(turn_id, child))

    assert response["error"] == "delegation_result_not_complete"
    exit_terminal.assert_not_called()


def test_p1_rejects_child_with_active_descendant_barrier(resource_db, monkeypatch, mocker):
    parent, child = "parent-p1-descendant", "child-p1-descendant"
    turn_id = _admit(parent, monkeypatch)
    _acknowledged_child(parent, child, monkeypatch)
    with database.SessionLocal() as db:
        db.add(
            ChildAssignmentModel(
                parent_terminal_id=child,
                child_terminal_id="grandchild-p1",
                status="awaiting_result",
            )
        )
        db.commit()
    exit_terminal = mocker.patch.object(mcp_server.terminal_service, "exit_terminal")

    result = asyncio.run(mcp_server.retire_completed_child(turn_id, child))

    assert result["error"] == "active_child_completion_barrier"
    assert result["active_children"] == 1
    exit_terminal.assert_not_called()


def test_p1_api_exited_child_is_idempotently_already_retired(resource_db, monkeypatch, mocker):
    parent, child = "parent-p1-exited", "child-p1-exited"
    turn_id = _admit(parent, monkeypatch)
    _acknowledged_child(parent, child, monkeypatch)
    mocker.patch.object(
        mcp_server.requests,
        "get",
        return_value=_api_terminal_response(mocker, "completed", "exited"),
    )
    provider_state = mocker.patch.object(mcp_server.terminal_service, "get_terminal")
    exit_terminal = mocker.patch.object(mcp_server.terminal_service, "exit_terminal")

    result = asyncio.run(mcp_server.retire_completed_child(turn_id, child))

    assert result["success"] is True and result["status"] == "already_retired"
    provider_state.assert_not_called()
    exit_terminal.assert_not_called()


def test_p1_api_rejects_processing_child_without_reservation_or_exit(
    resource_db, monkeypatch, mocker
):
    parent, child = "parent-p1-processing", "child-p1-processing"
    turn_id = _admit(parent, monkeypatch)
    _acknowledged_child(parent, child, monkeypatch)
    mocker.patch.object(
        mcp_server.requests,
        "get",
        return_value=_api_terminal_response(mocker, "processing", "running"),
    )
    provider_state = mocker.patch.object(mcp_server.terminal_service, "get_terminal")
    exit_terminal = mocker.patch.object(mcp_server.terminal_service, "exit_terminal")

    result = asyncio.run(mcp_server.retire_completed_child(turn_id, child))

    assert result == {"success": False, "error": "child_terminal_not_completed"}
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        assert assignment.retirement_claim_token is None
        assert assignment.retirement_exit_dispatched_at is None
    provider_state.assert_not_called()
    exit_terminal.assert_not_called()


def test_p1_api_rechecks_processing_at_external_exit_boundary(resource_db, monkeypatch, mocker):
    parent, child = "parent-p1-boundary", "child-p1-boundary"
    turn_id = _admit(parent, monkeypatch)
    _acknowledged_child(parent, child, monkeypatch)
    mocker.patch.object(
        mcp_server.requests,
        "get",
        side_effect=[
            _api_terminal_response(mocker, "completed", "running"),
            _api_terminal_response(mocker, "processing", "running"),
        ],
    )
    exit_terminal = mocker.patch.object(mcp_server.terminal_service, "exit_terminal")

    result = asyncio.run(mcp_server.retire_completed_child(turn_id, child))

    assert result == {"success": False, "error": "child_terminal_not_completed"}
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        assert assignment.retirement_claim_token is None
        assert assignment.retirement_exit_dispatched_at is None
    exit_terminal.assert_not_called()


@pytest.mark.parametrize("payload", [{"status": "completed"}, []])
def test_p1_api_malformed_terminal_state_releases_claim_without_exit(
    resource_db, monkeypatch, mocker, payload
):
    parent, child = "parent-p1-malformed", "child-p1-malformed"
    turn_id = _admit(parent, monkeypatch)
    _acknowledged_child(parent, child, monkeypatch)
    response = mocker.Mock()
    response.json.return_value = payload
    mocker.patch.object(mcp_server.requests, "get", return_value=response)
    exit_terminal = mocker.patch.object(mcp_server.terminal_service, "exit_terminal")

    result = asyncio.run(mcp_server.retire_completed_child(turn_id, child))

    assert result["error"] == "child_terminal_unavailable"
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        assert assignment.retirement_claim_token is None
        assert assignment.retirement_exit_dispatched_at is None
    exit_terminal.assert_not_called()


@pytest.mark.parametrize(
    "error_type",
    [mcp_server.requests.ConnectionError, mcp_server.requests.HTTPError],
    ids=["unavailable", "http_error"],
)
def test_p1_api_unavailable_terminal_state_releases_claim_without_exit(
    resource_db, monkeypatch, mocker, error_type
):
    parent, child = "parent-p1-unavailable", "child-p1-unavailable"
    turn_id = _admit(parent, monkeypatch)
    _acknowledged_child(parent, child, monkeypatch)
    mocker.patch.object(
        mcp_server.requests,
        "get",
        side_effect=error_type("cao-server unavailable"),
    )
    exit_terminal = mocker.patch.object(mcp_server.terminal_service, "exit_terminal")

    result = asyncio.run(mcp_server.retire_completed_child(turn_id, child))

    assert result["error"] == "child_terminal_unavailable"
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        assert assignment.retirement_claim_token is None
        assert assignment.retirement_exit_dispatched_at is None
    exit_terminal.assert_not_called()


def test_p1_reserved_reconciliation_uses_api_without_redispatch(resource_db, monkeypatch, mocker):
    parent, child = "parent-p1-api-reconcile", "child-p1-api-reconcile"
    first_turn = _admit(parent, monkeypatch)
    _acknowledged_child(parent, child, monkeypatch)
    get_terminal = mocker.patch.object(
        mcp_server.requests,
        "get",
        side_effect=[
            _api_terminal_response(mocker, "completed", "running"),
            _api_terminal_response(mocker, "completed", "running"),
        ],
    )
    provider_state = mocker.patch.object(mcp_server.terminal_service, "get_terminal")
    exit_terminal = mocker.patch.object(
        mcp_server.terminal_service, "exit_terminal", return_value=False
    )

    first = asyncio.run(mcp_server.retire_completed_child(first_turn, child))

    assert first["error"] == "child_retirement_exit_indeterminate"
    exit_terminal.assert_called_once_with(child)
    retry_turn = _admit(parent, monkeypatch)
    get_terminal.reset_mock()
    get_terminal.side_effect = None
    get_terminal.return_value = _api_terminal_response(mocker, "completed", "exited")

    retry = asyncio.run(mcp_server.retire_completed_child(retry_turn, child))

    assert retry["success"] is True and retry["status"] == "already_retired"
    assert get_terminal.call_count == 1
    provider_state.assert_not_called()
    exit_terminal.assert_called_once_with(child)


def test_p1_rejects_reopened_child_with_new_active_descendant(resource_db, monkeypatch, mocker):
    parent, child, grandchild = "parent-p1-reopened", "child-p1-reopened", "grandchild-p1"
    turn_id = _admit(parent, monkeypatch)
    _acknowledged_child(parent, child, monkeypatch)
    assert start_workflow_input(child) is not None
    assert register_child_assignment(child, grandchild)
    exit_terminal = mocker.patch.object(mcp_server.terminal_service, "exit_terminal")

    result = asyncio.run(mcp_server.retire_completed_child(turn_id, child))

    assert result == {"success": False, "eligible": False, "error": "child_workflow_not_terminal"}
    exit_terminal.assert_not_called()


def test_p1_claim_blocks_reopen_and_descendant_registration(resource_db, monkeypatch, mocker):
    parent, child, grandchild = "parent-p1-claim", "child-p1-claim", "grandchild-p1"
    turn_id = _admit(parent, monkeypatch)
    _acknowledged_child(parent, child, monkeypatch)

    def completed_terminal(_child):
        assert start_workflow_input(child) is None
        assert register_child_assignment(child, grandchild) is False
        return _running_terminal(child)

    mocker.patch.object(mcp_server, "_read_terminal_state", side_effect=completed_terminal)
    exit_terminal = mocker.patch.object(
        mcp_server.terminal_service, "exit_terminal", return_value=True
    )

    assert asyncio.run(mcp_server.retire_completed_child(turn_id, child))["success"] is True
    exit_terminal.assert_called_once_with(child)


def test_p1_sqlite_input_attempt_before_retirement_cannot_exit_across_commit(
    sqlite_interleaving_db, monkeypatch, mocker
):
    """An input's SQLite writer fence wins before retirement can exit."""
    parent, child = "parent-p1-sqlite-race", "child-p1-sqlite-race"
    turn_id = _admit(parent, monkeypatch)
    _acknowledged_child(parent, child, monkeypatch)
    input_read = threading.Event()
    allow_input_commit = threading.Event()
    input_done = threading.Event()
    input_result = []
    retirement_done = threading.Event()
    retirement_result = []
    original_fence = database._retirement_quiescence_allows_commit

    def paused_input_fence(db, terminal_id):
        allowed = original_fence(db, terminal_id)
        if terminal_id == child:
            input_read.set()
            assert allow_input_commit.wait(timeout=2)
        return allowed

    def attempt_input():
        input_result.append(start_workflow_input(child))
        input_done.set()

    monkeypatch.setattr(database, "_retirement_quiescence_allows_commit", paused_input_fence)
    input_thread = threading.Thread(target=attempt_input)
    input_thread.start()
    assert input_read.wait(timeout=2)

    def retire():
        retirement_result.append(asyncio.run(mcp_server.retire_completed_child(turn_id, child)))
        retirement_done.set()

    exit_terminal = mocker.patch.object(mcp_server.terminal_service, "exit_terminal")
    retirement_thread = threading.Thread(target=retire)
    retirement_thread.start()
    allow_input_commit.set()
    assert input_done.wait(timeout=2)
    assert retirement_done.wait(timeout=2)
    input_thread.join(timeout=2)
    retirement_thread.join(timeout=2)

    assert not input_thread.is_alive()
    assert not retirement_thread.is_alive()
    assert input_result[0] is not None
    assert retirement_result == [
        {"success": False, "eligible": False, "error": "child_workflow_not_terminal"}
    ]
    exit_terminal.assert_not_called()


def test_p1_exit_exception_reserves_once_and_retries_by_reconciliation(
    resource_db, monkeypatch, mocker
):
    parent, child, grandchild = "parent-p1-reserve", "child-p1-reserve", "grandchild-p1-reserve"
    first_turn = _admit(parent, monkeypatch)
    _acknowledged_child(parent, child, monkeypatch)
    mocker.patch.object(mcp_server, "_read_terminal_state", return_value=_running_terminal(child))
    exit_terminal = mocker.patch.object(
        mcp_server.terminal_service, "exit_terminal", side_effect=RuntimeError("exit response lost")
    )

    with pytest.raises(RuntimeError, match="exit response lost"):
        asyncio.run(mcp_server.retire_completed_child(first_turn, child))

    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        assert assignment.retirement_claim_token is not None
        assert assignment.retirement_exit_dispatched_at is not None
        assert assignment.retirement_completed_at is None
    assert start_workflow_input(child) is None
    assert register_child_assignment(child, grandchild) is False

    retry_turn = _admit(parent, monkeypatch)
    mocker.patch.object(
        mcp_server,
        "_read_terminal_state",
        return_value=_running_terminal(child),
    )

    retry = asyncio.run(mcp_server.retire_completed_child(retry_turn, child))

    assert retry == {
        "success": False,
        "child_terminal_id": child,
        "status": "exit_dispatch_indeterminate",
        "recoverable": True,
        "error": "child_retirement_exit_indeterminate",
        "lifecycle": "running",
    }
    exit_terminal.assert_called_once_with(child)

    later_turn = _admit(parent, monkeypatch)
    mocker.patch.object(
        mcp_server,
        "_read_terminal_state",
        return_value=("completed", "exited"),
    )

    later = asyncio.run(mcp_server.retire_completed_child(later_turn, child))

    assert later["success"] is True and later["status"] == "already_retired"
    exit_terminal.assert_called_once_with(child)


def test_p1_restart_reconstruction_never_redispatches_reserved_exit(
    resource_db, monkeypatch, mocker
):
    """SQLite state alone makes a restarted server reconcile rather than exit."""
    parent, child = "parent-p1-restart", "child-p1-restart"
    first_turn = _admit(parent, monkeypatch)
    _acknowledged_child(parent, child, monkeypatch)
    mocker.patch.object(mcp_server, "_read_terminal_state", return_value=_running_terminal(child))
    exit_terminal = mocker.patch.object(
        mcp_server.terminal_service, "exit_terminal", return_value=False
    )

    first = asyncio.run(mcp_server.retire_completed_child(first_turn, child))

    assert first["error"] == "child_retirement_exit_indeterminate"
    assert exit_terminal.call_count == 1
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        assert assignment.retirement_exit_dispatched_at is not None
        assert assignment.retirement_completed_at is None

    retry_turn = _admit(parent, monkeypatch)
    retry = asyncio.run(mcp_server.retire_completed_child(retry_turn, child))

    assert retry["status"] == "exit_dispatch_indeterminate"
    assert retry["recoverable"] is True
    exit_terminal.assert_called_once_with(child)


@pytest.mark.parametrize("make_stale", [False, True])
def test_p1_unadmitted_or_stale_turn_cannot_retire(resource_db, monkeypatch, mocker, make_stale):
    parent, child = "parent-p1-unadmitted", "child-p1-unadmitted"
    stale_turn = start_workflow_input(parent)
    assert stale_turn is not None
    monkeypatch.setenv("CAO_TERMINAL_ID", parent)
    _acknowledged_child(parent, child, monkeypatch)
    monkeypatch.setenv("CAO_TERMINAL_ID", parent)
    if make_stale:
        assert start_workflow_input(parent) is not None
    exit_terminal = mocker.patch.object(mcp_server.terminal_service, "exit_terminal")

    result = asyncio.run(mcp_server.retire_completed_child(stale_turn, child))

    assert result["success"] is False
    assert result["accepted"] is False
    assert result["reason_code"] == "STALE_LOGICAL_TURN"
    exit_terminal.assert_not_called()


def test_p1_duplicate_effect_never_issues_a_second_exit(resource_db, monkeypatch, mocker):
    parent, child = "parent-p1-replay", "child-p1-replay"
    turn_id = _admit(parent, monkeypatch)
    _acknowledged_child(parent, child, monkeypatch)
    mocker.patch.object(mcp_server, "_read_terminal_state", return_value=_running_terminal(child))
    exit_terminal = mocker.patch.object(
        mcp_server.terminal_service, "exit_terminal", return_value=True
    )

    first = asyncio.run(mcp_server.retire_completed_child(turn_id, child))
    replay = asyncio.run(mcp_server.retire_completed_child(turn_id, child))

    assert first["success"] is True
    assert replay["success"] is False
    assert replay["accepted"] is False
    assert replay["reason_code"] == "DUPLICATE_EFFECT"
    exit_terminal.assert_called_once_with(child)


@pytest.mark.parametrize("stale_boundary", [1, 2, 3])
def test_c1_stale_generation_closes_effect_and_releases_undispatched_retirement(
    resource_db, monkeypatch, mocker, stale_boundary
):
    parent, child = f"parent-c1-stale-{stale_boundary}", f"child-c1-stale-{stale_boundary}"
    turn_id = _admit(parent, monkeypatch)
    _acknowledged_child(parent, child, monkeypatch)
    mocker.patch.object(mcp_server, "_read_terminal_state", return_value=_running_terminal(child))
    exit_terminal = mocker.patch.object(mcp_server.terminal_service, "exit_terminal")
    calls = 0

    def fence(_child_terminal_id):
        nonlocal calls
        calls += 1
        if calls == stale_boundary:
            return mcp_server._runtime_reconnect_response(child)
        return None

    mocker.patch.object(mcp_server, "_retirement_runtime_fence", side_effect=fence)

    result = asyncio.run(mcp_server.retire_completed_child(turn_id, child))

    assert result["status"] == "runtime_reconnect_required"
    exit_terminal.assert_not_called()
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        assert assignment.retirement_claim_token is None
        assert assignment.retirement_exit_dispatched_at is None
        effect = db.query(WorkflowEffectModel).filter_by(workflow_turn_id=turn_id).one()
        assert effect.state == "rejected"


@pytest.mark.parametrize("stale_boundary", [4, 5, 6])
def test_c1_stale_generation_after_exit_finalizes_effect_and_leaves_cleanup_recoverable(
    resource_db, monkeypatch, mocker, stale_boundary
):
    parent, child = f"parent-c1-cleanup-{stale_boundary}", f"child-c1-cleanup-{stale_boundary}"
    turn_id = _admit(parent, monkeypatch)
    _acknowledged_child(parent, child, monkeypatch)
    mocker.patch.object(mcp_server, "_read_terminal_state", return_value=_running_terminal(child))

    def exit_and_confirm(_child_terminal_id):
        assert mark_terminal_runtime_exited(child)
        return True

    exit_terminal = mocker.patch.object(
        mcp_server.terminal_service, "exit_terminal", side_effect=exit_and_confirm
    )
    cleanup = mocker.patch.object(mcp_server.terminal_service, "cleanup_managed_worktree")
    calls = 0

    def fence(_child_terminal_id):
        nonlocal calls
        calls += 1
        if calls == stale_boundary:
            return mcp_server._runtime_reconnect_response(child)
        return None

    mocker.patch.object(mcp_server, "_retirement_runtime_fence", side_effect=fence)

    result = asyncio.run(mcp_server.retire_completed_child(turn_id, child))

    assert result["status"] == "runtime_reconnect_required"
    exit_terminal.assert_called_once_with(child)
    assert cleanup.call_count == (1 if stale_boundary == 6 else 0)
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        assert assignment.retirement_claim_token is not None
        assert assignment.retirement_exit_dispatched_at is not None
        assert assignment.retirement_cleanup_completed_at is None
        effect = db.query(WorkflowEffectModel).filter_by(workflow_turn_id=turn_id).one()
        assert effect.state == "indeterminate"

    mocker.patch.object(mcp_server, "_read_terminal_state", return_value=("completed", "exited"))
    retry = asyncio.run(mcp_server.retire_completed_child(_admit(parent, monkeypatch), child))
    assert retry["success"] is True
    exit_terminal.assert_called_once_with(child)


def test_p1_preserves_acknowledged_assignment_result_and_metadata(resource_db, monkeypatch, mocker):
    parent, child = "parent-p1-preserve", "child-p1-preserve"
    turn_id = _admit(parent, monkeypatch)
    result_id = _acknowledged_child(parent, child, monkeypatch)
    before = get_delegation_result(result_id)
    assert before is not None
    mocker.patch.object(mcp_server, "_read_terminal_state", return_value=_running_terminal(child))
    mocker.patch.object(mcp_server.terminal_service, "exit_terminal", return_value=True)

    assert asyncio.run(mcp_server.retire_completed_child(turn_id, child))["success"] is True

    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        metadata = db.query(TerminalModel).filter_by(id=child).one()
        assert assignment.status == "result_acknowledged"
        assert metadata.tmux_window == f"window-{child}"
    after = get_delegation_result_for_assignment(child)
    assert after is not None
    assert after["id"] == result_id
    assert after["document"] == before["document"]


def test_p1_requires_terminal_metadata(resource_db, monkeypatch, mocker):
    parent, child = "parent-p1-metadata", "child-p1-metadata"
    turn_id = _admit(parent, monkeypatch)
    _acknowledged_child(parent, child, monkeypatch)
    with database.SessionLocal() as db:
        db.query(TerminalModel).filter_by(id=child).delete()
        db.commit()
    exit_terminal = mocker.patch.object(mcp_server.terminal_service, "exit_terminal")

    result = asyncio.run(mcp_server.retire_completed_child(turn_id, child))

    assert result["error"] == "child_terminal_metadata_not_found"
    exit_terminal.assert_not_called()


def test_c1_cleanup_failure_releases_capacity_but_keeps_retirement_pending(
    resource_db, monkeypatch, mocker
):
    parent, child = "parent-c1-capacity", "child-c1-capacity"
    turn_id = _admit(parent, monkeypatch)
    _acknowledged_child(parent, child, monkeypatch)
    mocker.patch.object(mcp_server, "_read_terminal_state", return_value=_running_terminal(child))

    def exit_and_release(_child):
        assert mark_terminal_runtime_exited(child)
        return True

    exit_terminal = mocker.patch.object(
        mcp_server.terminal_service, "exit_terminal", side_effect=exit_and_release
    )
    cleanup = mocker.patch.object(
        mcp_server.terminal_service,
        "cleanup_managed_worktree",
        side_effect=RuntimeError("cleanup crash"),
    )

    pending = asyncio.run(mcp_server.retire_completed_child(turn_id, child))

    assert pending["status"] == "retirement_cleanup_pending"
    assert pending["reason_code"] == "MANAGED_WORKTREE_CLEANUP_NOT_CONFIRMED"
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        assert assignment.retirement_cleanup_intent is not None
        assert assignment.retirement_cleanup_completed_at is None
        assert assignment.retirement_completed_at is None
    assert operations_service._active_contexts() == []

    cleanup.side_effect = None
    mocker.patch.object(mcp_server, "_read_terminal_state", return_value=("completed", "exited"))
    retry = asyncio.run(mcp_server.retire_completed_child(_admit(parent, monkeypatch), child))
    assert retry["status"] == "already_retired"
    exit_terminal.assert_called_once_with(child)


def test_c1_crash_after_cleanup_before_final_cas_is_idempotent(resource_db, monkeypatch, mocker):
    parent, child = "parent-c1-final-cas", "child-c1-final-cas"
    turn_id = _admit(parent, monkeypatch)
    _acknowledged_child(parent, child, monkeypatch)
    mocker.patch.object(mcp_server, "_read_terminal_state", return_value=_running_terminal(child))
    exit_terminal = mocker.patch.object(
        mcp_server.terminal_service, "exit_terminal", return_value=True
    )
    finalizer = mocker.patch.object(
        mcp_server, "complete_assigned_child_retirement", return_value=False
    )

    pending = asyncio.run(mcp_server.retire_completed_child(turn_id, child))
    assert pending["reason_code"] == "RETIREMENT_CLEANUP_FINALIZATION_NOT_CONFIRMED"
    mocker.stop(finalizer)
    mocker.patch.object(mcp_server, "_read_terminal_state", return_value=("completed", "exited"))

    retry = asyncio.run(mcp_server.retire_completed_child(_admit(parent, monkeypatch), child))

    assert retry["status"] == "already_retired"
    exit_terminal.assert_called_once_with(child)


def test_c1_partial_legacy_managed_identity_fails_closed(resource_db, monkeypatch, mocker):
    parent, child = "parent-c1-unsafe", "child-c1-unsafe"
    turn_id = _admit(parent, monkeypatch)
    _acknowledged_child(parent, child, monkeypatch)
    with database.SessionLocal() as db:
        terminal = db.query(TerminalModel).filter_by(id=child).one()
        terminal.managed_worktree_kind = "task"
        terminal.managed_worktree_source = "/exact/source"
        terminal.managed_worktree_branch = f"cao/task/{child}"
        terminal.managed_worktree_commit = None
        db.commit()
    exit_terminal = mocker.patch.object(mcp_server.terminal_service, "exit_terminal")

    result = asyncio.run(mcp_server.retire_completed_child(turn_id, child))

    assert result["status"] == "retirement_cleanup_pending"
    assert result["reason_code"] == "RETIREMENT_CLEANUP_IDENTITY_UNPROVEN"
    exit_terminal.assert_not_called()


def test_c1_housekeeping_resumes_exited_cleanup_intent_after_restart(resource_db, monkeypatch):
    parent, child = "parent-c1-housekeeping", "child-c1-housekeeping"
    _acknowledged_child(parent, child, monkeypatch)
    fence = claim_completed_assigned_child_retirement(parent, child)
    assert reserve_completed_assigned_child_retirement_exit(child, fence["claim_token"])
    assert mark_terminal_runtime_exited(child)
    summary = housekeeping_service.HousekeepingSummary(dry_run=False)

    housekeeping_service._reconcile_retirement_cleanups(summary)

    assert summary.retirement_cleanups_reconciled == 1
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        assert assignment.retirement_cleanup_completed_at is not None
        assert assignment.retirement_completed_at is not None


def test_c1_housekeeping_adopts_exact_legacy_retirement_after_restart(resource_db, monkeypatch):
    parent, child = "parent-c1-legacy-hk", "child-c1-legacy-hk"
    _acknowledged_child(parent, child, monkeypatch)
    with database.SessionLocal() as db:
        terminal = db.query(TerminalModel).filter_by(id=child).one()
        terminal.runtime_lifecycle = "exited"
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        assignment.retirement_exit_dispatched_at = datetime.now()
        assignment.retirement_completed_at = datetime.now()
        db.commit()
    summary = housekeeping_service.HousekeepingSummary(dry_run=False)

    housekeeping_service._reconcile_retirement_cleanups(summary)

    assert summary.retirement_cleanups_reconciled == 1
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        assert assignment.retirement_cleanup_intent is not None
        assert assignment.retirement_cleanup_completed_at is not None


def test_c1_real_05049a08_persisted_shape_proves_exact_legacy_cleanup_intent(
    resource_db, monkeypatch
):
    parent, child = "1fb9cad4", "05049a08"
    _acknowledged_child(parent, child, monkeypatch)
    with database.SessionLocal() as db:
        terminal = db.query(TerminalModel).filter_by(id=child).one()
        terminal.launch_worktree = (
            "/srv/agent-control/state/cao/worktrees/dda922ff20605de4/task-05049a08"
        )
        terminal.write_enabled = True
        terminal.context_role = "work"
        terminal.managed_worktree_kind = "task"
        terminal.managed_worktree_source = (
            "/srv/agent-control/sources/cli-agent-orchestrator-v2.1.1"
        )
        terminal.managed_worktree_branch = "cao/task/05049a08"
        terminal.managed_worktree_commit = "5e3a43d2e42d8a9b6448e3c550d139ff4ff4d409"
        terminal.runtime_lifecycle = "exited"
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        assignment.retirement_claim_token = None
        assignment.retirement_exit_dispatched_at = datetime.fromisoformat(
            "2026-08-17 08:47:12.729715"
        )
        assignment.retirement_completed_at = datetime.fromisoformat("2026-08-17 08:47:13.633590")
        assignment.retirement_cleanup_intent = None
        db.commit()

    fence = claim_completed_assigned_child_retirement(parent, child)
    state = get_assigned_child_retirement_cleanup_intent(child, fence["claim_token"])

    assert fence["eligible"] is True and fence["exit_dispatch_reserved"] is True
    assert state is not None
    assert state["intent"]["launch_worktree"].endswith("/task-05049a08")
    assert state["intent"]["managed_worktree_branch"] == "cao/task/05049a08"
    assert state["intent"]["managed_worktree_commit"] == (
        "5e3a43d2e42d8a9b6448e3c550d139ff4ff4d409"
    )
    assert state["cleanup_completed"] is False


def test_c1_b4d46035_legacy_retirement_adopts_absent_exact_worktree_and_preserves_branch(
    resource_db, monkeypatch, tmp_path
):
    parent, child = "b7994fbd", "b4d46035"
    repository = tmp_path / "source"
    repository.mkdir()
    subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "CAO Test"], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "cao-test@example.invalid"],
        check=True,
    )
    (repository / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "baseline"], check=True)
    baseline = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")
    _acknowledged_child(parent, child, monkeypatch)
    managed = managed_worktree_service.create_managed_worktree(str(repository), child, "task")
    assert managed is not None and managed.branch == f"cao/task/{child}"
    (Path(managed.path) / "durable.txt").write_text("retained history\n", encoding="utf-8")
    subprocess.run(["git", "-C", managed.path, "add", "durable.txt"], check=True)
    subprocess.run(
        ["git", "-C", managed.path, "commit", "-qm", "preserve retired child history"],
        check=True,
    )
    durable_commit = subprocess.run(
        ["git", "-C", managed.path, "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    with database.SessionLocal() as db:
        terminal = db.query(TerminalModel).filter_by(id=child).one()
        terminal.launch_worktree = managed.path
        terminal.write_enabled = True
        terminal.context_role = "work"
        terminal.managed_worktree_kind = managed.kind
        terminal.managed_worktree_source = managed.source
        terminal.managed_worktree_branch = managed.branch
        terminal.managed_worktree_commit = durable_commit
        terminal.runtime_lifecycle = "exited"
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        assignment.retirement_exit_dispatched_at = datetime.now()
        assignment.retirement_completed_at = datetime.now()
        assignment.retirement_cleanup_intent = None
        assignment.retirement_cleanup_completed_at = None
        db.commit()
    subprocess.run(["git", "-C", str(repository), "worktree", "remove", managed.path], check=True)
    assert not Path(managed.path).exists()

    summary = housekeeping_service.HousekeepingSummary(dry_run=False)
    housekeeping_service._reconcile_retirement_cleanups(summary)

    assert summary.retirement_cleanups_reconciled == 1
    state = get_assigned_child_retirement_cleanup_intent(child)
    assert state is not None and state["cleanup_completed"] is True
    assert state["retirement_completed"] is True
    assert state["intent"] == {
        "version": 1,
        "terminal_id": child,
        "managed": True,
        "id": child,
        "launch_worktree": managed.path,
        "managed_worktree_kind": "task",
        "managed_worktree_source": str(repository.resolve()),
        "managed_worktree_branch": f"cao/task/{child}",
        "managed_worktree_commit": durable_commit,
        "legacy_retirement_completed_at": state["intent"]["legacy_retirement_completed_at"],
    }
    registered = subprocess.run(
        ["git", "-C", str(repository), "worktree", "list", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert managed.path not in registered
    assert (
        subprocess.run(
            ["git", "-C", str(repository), "rev-parse", f"cao/task/{child}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == durable_commit
    )
    assert baseline != durable_commit


def test_c1_r1_real_4e26dbb0_handoff_shape_uses_retirement_saga_without_fake_ack(
    resource_db, monkeypatch
):
    parent, child = "b7994fbd", "4e26dbb0"
    result_id = _completed_handoff_child(
        parent,
        child,
        source="/srv/agent-control/sources/cli-agent-orchestrator-v2.1.1",
        commit="499e8ce9e423b68ec9de7e68a2116ca8b7ec12db",
    )

    fence = claim_completed_handoff_child_retirement(parent, child)
    state = get_child_retirement_cleanup_intent(child, fence["claim_token"])

    assert fence["eligible"] is True
    assert fence["delegation_kind"] == "handoff"
    assert fence["exit_dispatch_reserved"] is True
    assert state is not None and state["intent"]["managed"] is True
    assert state["intent"]["terminal_id"] == "4e26dbb0"
    assert state["intent"]["managed_worktree_branch"] == "cao/task/4e26dbb0"
    assert state["intent"]["managed_worktree_commit"] == (
        "499e8ce9e423b68ec9de7e68a2116ca8b7ec12db"
    )
    assert complete_child_retirement(child, fence["claim_token"], state["intent"], "handoff")
    retry = claim_completed_handoff_child_retirement(parent, child)
    assert retry["already_retired"] is True
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        result = db.query(DelegationResultModel).filter_by(id=result_id).one()
        assert assignment.status == ChildAssignmentStatus.HANDOFF_RESULT_DELIVERED.value
        assert result.delegation_kind == "handoff"
        assert result.status == DelegationResultStatus.COMPLETE.value


@pytest.mark.parametrize(
    ("relation_status", "result_status", "finalized", "runtime", "error"),
    [
        (
            ChildAssignmentStatus.CANCELLED.value,
            "complete",
            True,
            "exited",
            "wrong_delegation_kind",
        ),
        (
            ChildAssignmentStatus.HANDOFF_RESULT_FAILED.value,
            "incomplete",
            True,
            "exited",
            "handoff_result_not_final",
        ),
        (
            ChildAssignmentStatus.HANDOFF_RESULT_DELIVERED.value,
            None,
            False,
            "exited",
            "handoff_result_not_complete",
        ),
        (
            ChildAssignmentStatus.HANDOFF_RESULT_DELIVERED.value,
            "complete",
            False,
            "exited",
            "handoff_result_not_complete",
        ),
        (
            ChildAssignmentStatus.HANDOFF_RESULT_DELIVERED.value,
            "complete",
            True,
            "running",
            "handoff_runtime_not_exited",
        ),
    ],
)
def test_c1_r1_handoff_retirement_fails_closed_before_persisting_intent(
    resource_db,
    relation_status,
    result_status,
    finalized,
    runtime,
    error,
):
    parent, child = "parent-c1-r1-closed", f"child-{error}"
    _completed_handoff_child(parent, child)
    with database.SessionLocal() as db:
        terminal = db.query(TerminalModel).filter_by(id=child).one()
        terminal.runtime_lifecycle = runtime
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        assignment.status = relation_status
        result = db.query(DelegationResultModel).filter_by(child_assignment_id=assignment.id).one()
        if result_status is None:
            db.delete(result)
        else:
            result.status = result_status
            result.finalized_at = datetime.now() if finalized else None
        db.commit()

    fence = claim_completed_handoff_child_retirement(parent, child)

    assert fence == {"eligible": False, "error": error}
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        assert assignment.retirement_cleanup_intent is None
        assert assignment.retirement_claim_token is None


def test_c1_r1_housekeeping_resumes_handoff_cleanup_after_crash_before_final_cas(
    resource_db, monkeypatch, mocker
):
    parent, child = "parent-c1-r1-resume", "child-c1-r1-resume"
    _completed_handoff_child(parent, child)
    fence = claim_completed_handoff_child_retirement(parent, child)
    cleanup = mocker.patch(
        "cli_agent_orchestrator.services.terminal_service.cleanup_managed_worktree"
    )
    finalizer = mocker.patch.object(database, "complete_child_retirement", return_value=False)
    summary = housekeeping_service.HousekeepingSummary(dry_run=False)

    housekeeping_service._reconcile_retirement_cleanups(summary)
    assert summary.retirement_cleanups_reconciled == 0
    assert f"retirement_cleanup_finalization_raced:{child}" in summary.warnings
    cleanup.assert_called_once()
    mocker.stop(finalizer)

    resumed = housekeeping_service.HousekeepingSummary(dry_run=False)
    housekeeping_service._reconcile_retirement_cleanups(resumed)
    assert resumed.retirement_cleanups_reconciled == 1
    retry = claim_completed_handoff_child_retirement(parent, child)
    assert retry["already_retired"] is True
    assert retry.get("claim_token") is None
    assert fence["claim_token"]


def test_c1_r1_direct_handoff_cleanup_retry_finalizes_before_result_delivery(resource_db, mocker):
    parent, child = "parent-c1-r1-direct", "child-c1-r1-direct"
    _completed_handoff_child(parent, child)
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        assignment.status = ChildAssignmentStatus.HANDOFF_DIRECT_RESULT_CLAIMED.value
        assignment.cleanup_acknowledged = False
        db.commit()
    cleanup = mocker.patch.object(
        mcp_server.terminal_service,
        "cleanup_managed_worktree",
        side_effect=[RuntimeError("cleanup crash"), None],
    )

    first = mcp_server._cleanup_claimed_handoff_result(parent, child, "exited", 1)
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        assert first.state.value == "waiting"
        assert assignment.status == ChildAssignmentStatus.HANDOFF_DIRECT_RESULT_CLAIMED.value
        assert assignment.retirement_cleanup_intent is not None
        assert assignment.retirement_completed_at is None

    second = mcp_server._cleanup_claimed_handoff_result(parent, child, "exited", 1)
    third = mcp_server._cleanup_claimed_handoff_result(parent, child, "exited", 1)

    assert second.state.value == "completed"
    assert third.state.value == "completed"
    assert cleanup.call_count == 2
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        assert assignment.status == ChildAssignmentStatus.HANDOFF_RESULT_DELIVERED.value
        assert assignment.retirement_cleanup_completed_at is not None
        assert assignment.retirement_completed_at is not None


def test_c1_r1_mid_handoff_identity_outage_retains_recoverable_exit_claim_for_retry(
    resource_db, monkeypatch, mocker
):
    parent, child = "parent-c1-r1-runtime-outage", "child-c1-r1-runtime-outage"
    _completed_handoff_child(parent, child)
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        assignment.status = ChildAssignmentStatus.HANDOFF_DIRECT_RESULT_CLAIMED.value
        assignment.cleanup_acknowledged = False
        db.commit()
    monkeypatch.setattr(mcp_server, "_SIDECAR_RUNTIME_GENERATION", "managed-build")
    active_generation = mocker.patch.object(
        mcp_server,
        "_active_runtime_generation",
        side_effect=["managed-build", "managed-build", None],
    )
    cleanup = mocker.patch.object(mcp_server.terminal_service, "cleanup_managed_worktree")

    first = mcp_server._cleanup_claimed_handoff_result(parent, child, "exited", 1)

    assert "CAO_RUNTIME_GENERATION_UNAVAILABLE" in first.message
    assert "CAO_SIDECAR_RECONNECT_REQUIRED" not in first.message
    cleanup.assert_not_called()
    with database.SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=child).one()
        # The child was already durably exited, so this claim is intentionally
        # retained as reconciliation authority rather than releasing the exit
        # boundary. The database returns this same token on retry.
        assert assignment.retirement_claim_token is not None
        assert assignment.retirement_exit_dispatched_at is not None
        assert assignment.retirement_completed_at is None

    active_generation.side_effect = None
    active_generation.return_value = "managed-build"
    retry = mcp_server._cleanup_claimed_handoff_result(parent, child, "exited", 1)

    assert retry.state.value == "completed"
    cleanup.assert_called_once()


@pytest.mark.parametrize("kind", ["task", "reviewer"])
def test_c1_r1_handoff_cleanup_removes_task_and_reviewer_worktrees_but_retains_history(
    resource_db, monkeypatch, tmp_path, kind
):
    parent, child = f"parent-c1-r1-{kind}", f"child-c1-r1-{kind}"
    repository = tmp_path / f"source-{kind}"
    repository.mkdir()
    subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "CAO Test"], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "cao-test@example.invalid"],
        check=True,
    )
    (repository / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "baseline"], check=True)
    baseline = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")
    _completed_handoff_child(parent, child, kind=kind, source=str(repository), commit=baseline)
    managed = managed_worktree_service.create_managed_worktree(str(repository), child, kind)
    assert managed is not None
    with database.SessionLocal() as db:
        terminal = db.query(TerminalModel).filter_by(id=child).one()
        terminal.launch_worktree = managed.path
        terminal.managed_worktree_kind = managed.kind
        terminal.managed_worktree_source = managed.source
        terminal.managed_worktree_branch = managed.branch
        terminal.managed_worktree_commit = managed.commit
        db.commit()
    durable_commit = baseline
    if kind == "task":
        (Path(managed.path) / "durable.txt").write_text("handoff history\n", encoding="utf-8")
        subprocess.run(["git", "-C", managed.path, "add", "durable.txt"], check=True)
        subprocess.run(
            ["git", "-C", managed.path, "commit", "-qm", "durable handoff history"],
            check=True,
        )
        durable_commit = subprocess.run(
            ["git", "-C", managed.path, "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    fence = claim_completed_handoff_child_retirement(parent, child)
    state = get_child_retirement_cleanup_intent(child, fence["claim_token"])
    assert state is not None
    mcp_server.terminal_service.cleanup_managed_worktree(state["intent"])
    assert complete_child_retirement(child, fence["claim_token"], state["intent"], "handoff")

    assert not Path(managed.path).exists()
    registered = subprocess.run(
        ["git", "-C", str(repository), "worktree", "list", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert managed.path not in registered
    if kind == "task":
        assert managed.branch is not None
        assert (
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "merge-base",
                    "--is-ancestor",
                    durable_commit,
                    managed.branch,
                ],
                check=False,
            ).returncode
            == 0
        )
    else:
        assert managed.branch is None
        assert (
            subprocess.run(
                ["git", "-C", str(repository), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            == baseline
        )
