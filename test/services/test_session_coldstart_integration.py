"""Process-level regression coverage for top-level Session cold starts."""

import re
import shutil
import subprocess
import time
import uuid
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.database import (
    OwnerLaunchGrantModel,
    WritableWorkContextAuditModel,
    WritableWorkContextModel,
)
from cli_agent_orchestrator.clients.tmux import TmuxClient, _BoundedTmuxServer
from cli_agent_orchestrator.models.agent_profile import AgentProfile
from cli_agent_orchestrator.services import (
    control_plane_registry,
    managed_worktree_service,
    operations_service,
    terminal_service,
)


def _wait_for_authoritative_server_absence(
    tmux_binary: str, socket_name: str, session_name: str
) -> None:
    """Prove the private tmux server is gone before exercising cold bootstrap."""
    deadline = time.monotonic() + 5
    result = None
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            result = subprocess.run(
                [
                    tmux_binary,
                    "-L",
                    socket_name,
                    "-f",
                    "/dev/null",
                    "has-session",
                    "-t",
                    f"={session_name}",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=remaining,
            )
        except subprocess.TimeoutExpired:
            pytest.fail("tmux absence probe exceeded its five-second deadline")
        stderr = result.stderr.strip()
        if result.returncode == 1 and (
            re.fullmatch(r"no server running on .+", stderr)
            or re.fullmatch(
                r"error connecting to .+ \((?:No such file or directory|Connection refused)\)",
                stderr,
            )
        ):
            return
        time.sleep(min(0.01, max(0, deadline - time.monotonic())))
    pytest.fail(
        "tmux server did not reach authoritative absence: "
        f"returncode={getattr(result, 'returncode', None)!r} "
        f"stderr={getattr(result, 'stderr', None)!r}"
    )


def test_authoritative_server_absence_wait_fails_explicitly_on_timeout(monkeypatch):
    def timeout_probe(*_args, **kwargs):
        assert 0 < kwargs["timeout"] <= 5
        raise subprocess.TimeoutExpired(["tmux", "has-session"], kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout_probe)

    with pytest.raises(pytest.fail.Exception, match="five-second deadline"):
        _wait_for_authoritative_server_absence("tmux", "private-socket", "target")


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")
def test_last_session_exit_then_privileged_managed_session_coldstart_succeeds(
    monkeypatch, tmp_path
):
    if not Path("/proc").is_dir():
        pytest.skip("process identity inventory requires /proc")

    repository = tmp_path / "source"
    repository.mkdir()
    subprocess.run(["git", "-C", str(repository), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "test@example.invalid"],
        check=True,
    )
    (repository / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-qm", "base"], check=True)

    socket_name = f"cao-managed-coldstart-{uuid.uuid4().hex}"
    client = TmuxClient()
    client.server = _BoundedTmuxServer(socket_name=socket_name, config_file="/dev/null")
    previous_session = "cao-last-managed"
    next_session = "managed-coldstart"
    terminal_id = f"cold{uuid.uuid4().hex[:4]}"
    request_id = str(uuid.uuid4())
    launch_id = f"coldstart-{uuid.uuid4().hex}"
    profile_revision_id = f"profile-{uuid.uuid4()}"
    provider_config_revision_id = f"provider-{uuid.uuid4()}"
    project_id = f"project-{uuid.uuid4()}"
    scope = {
        "profile_revision_id": profile_revision_id,
        "provider_config_revision_id": provider_config_revision_id,
        "project_id": project_id,
        "launch_mode": "new_session",
        "delegation_depth": 0,
    }
    token = database.issue_owner_launch_grant(
        launch_id=launch_id,
        agent_profile="critical_sol_xhigh_owner",
        provider="codex",
        canonical_worktree=str(repository.resolve()),
        requested_session_name=next_session,
        grant_scope=scope,
    )

    resolution = SimpleNamespace(
        profile=AgentProfile(
            name="critical_sol_xhigh_owner",
            description="Test owner executor",
            role="supervisor",
            execution_mode="owner_executor",
            owner_authorization_required=True,
            allowedTools=[],
        ),
        profile_revision_id=profile_revision_id,
        provider_config_revision_id=provider_config_revision_id,
        provider_adapter_id="codex",
        provider_configuration={},
        owner_grant_required=True,
        snapshot={"schema_version": 1, "profile_id": "critical_sol_xhigh_owner"},
    )
    provider = MagicMock()
    states = []
    original_reserve = terminal_service.reserve_writable_work_context
    original_transition = terminal_service.transition_writable_work_context
    original_create_terminal = terminal_service.db_create_terminal

    def current_context_state():
        with database.SessionLocal() as session:
            row = session.get(WritableWorkContextModel, terminal_id)
            return row.state if row is not None else None

    def reserve(*args, **kwargs):
        result = original_reserve(*args, **kwargs)
        states.append(current_context_state())
        return result

    def transition(*args, **kwargs):
        result = original_transition(*args, **kwargs)
        states.append(current_context_state())
        return result

    def persist_terminal(*args, **kwargs):
        result = original_create_terminal(*args, **kwargs)
        states.append(current_context_state())
        return result

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(terminal_service, "tmux_client", client)
    monkeypatch.setattr(terminal_service, "TERMINAL_LOG_DIR", log_dir)
    monkeypatch.setattr(terminal_service, "generate_terminal_id", lambda: terminal_id)
    monkeypatch.setattr(terminal_service, "build_skill_catalog", lambda: "")
    monkeypatch.setattr(terminal_service, "reserve_writable_work_context", reserve)
    monkeypatch.setattr(terminal_service, "transition_writable_work_context", transition)
    monkeypatch.setattr(terminal_service, "db_create_terminal", persist_terminal)
    monkeypatch.setattr(
        terminal_service.provider_manager, "create_provider", MagicMock(return_value=provider)
    )
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")
    monkeypatch.setattr(
        operations_service,
        "context_launch_admission",
        lambda **_kwargs: nullcontext({}),
    )
    monkeypatch.setattr(control_plane_registry, "registry_is_initialized", lambda: True)
    monkeypatch.setattr(
        control_plane_registry,
        "resolve_launch",
        lambda _profile, fallback_provider: resolution,
    )

    try:
        client.create_session(
            session_name=previous_session,
            window_name="owner",
            terminal_id="previous",
            runtime_generation="previous-generation",
            working_directory=str(repository),
        )
        socket_path = subprocess.run(
            ["tmux", "-L", socket_name, "display-message", "-p", "#{socket_path}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        monkeypatch.setenv("TMUX", f"{socket_path},0,0")
        assert client.session_exists(previous_session) is True
        assert client.kill_session(previous_session) is True
        _wait_for_authoritative_server_absence("tmux", socket_name, previous_session)
        assert client.session_exists(f"cao-{next_session}") is False

        terminal = terminal_service.create_terminal(
            provider="codex",
            agent_profile="critical_sol_xhigh_owner",
            session_name=next_session,
            new_session=True,
            working_directory=str(repository),
            project_context={
                "id": project_id,
                "name": "Cold start",
                "path": str(repository),
            },
            owner_grant_token=token,
            owner_grant_launch_id=launch_id,
            work_context_request_id=request_id,
        )

        metadata = database.get_terminal_metadata(terminal_id)
        assert metadata is not None
        target = client.exact_runtime_target(terminal.session_name, terminal.name)
        assert states == ["reserved", "provisioned", "launching", "admitted"]
        assert terminal.id == terminal_id
        assert terminal.session_name == f"cao-{next_session}"
        assert terminal.provider_outcome_code is None
        assert metadata["profile_revision_id"] == profile_revision_id
        assert metadata["provider_config_revision_id"] == provider_config_revision_id
        assert metadata["runtime_lifecycle"] == "running"
        assert (
            metadata["runtime_pane_id"],
            metadata["runtime_pane_pid"],
            metadata["runtime_generation"],
            metadata["runtime_process_start_ticks"],
            metadata["runtime_process_group_id"],
            metadata["runtime_process_session_id"],
        ) == (
            target.pane_id,
            target.pane_pid,
            target.runtime_generation,
            target.process_start_ticks,
            target.process_group_id,
            target.process_session_id,
        )
        assert target.terminal_id == terminal_id
        assert (
            database.validate_owner_launch_grant(
                token,
                launch_id=launch_id,
                agent_profile="critical_sol_xhigh_owner",
                provider="codex",
                canonical_worktree=str(repository.resolve()),
                requested_session_name=next_session,
                grant_scope=scope,
            )
            is False
        )
        with database.SessionLocal() as session:
            grants = session.query(OwnerLaunchGrantModel).filter_by(launch_id=launch_id).all()
            context = session.get(WritableWorkContextModel, terminal_id)
            audits = (
                session.query(WritableWorkContextAuditModel)
                .filter_by(work_context_id=terminal_id)
                .all()
            )
            assert len(grants) == 1
            assert grants[0].consumed_terminal_id == terminal_id
            assert grants[0].consumed_at is not None
            assert context is not None
            assert context.state == "admitted"
            assert context.failure_reason is None
            assert all(audit.reason_code != "PROVIDER_LAUNCH_FAILED_CONFIRMED" for audit in audits)
        provider.initialize.assert_called_once_with()
        assert terminal_service.provider_manager.create_provider.call_count == 1
    finally:
        subprocess.run(
            ["tmux", "-L", socket_name, "kill-server"],
            check=False,
            capture_output=True,
            text=True,
        )
