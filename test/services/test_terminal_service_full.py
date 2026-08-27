"""Full tests for terminal service."""

from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, call, patch

import pytest

from cli_agent_orchestrator.clients.database import (
    UnreconciledTerminalAuthority,
    WorktreeWriterLeaseConflict,
)
from cli_agent_orchestrator.clients.tmux import TmuxClient
from cli_agent_orchestrator.models.agent_profile import AgentProfile
from cli_agent_orchestrator.models.inbox import OrchestrationType
from cli_agent_orchestrator.models.provider import ProviderType
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.codex import (
    CodexProvider,
    CodexStartupError,
    CodexStartupNoReadyError,
    ProviderError,
)
from cli_agent_orchestrator.services.terminal_service import (
    OutputMode,
    TerminalOutputUnavailable,
    _active_worktree_lanes,
    _canonical_worktree,
    _create_terminal_after_admission,
    _resolve_context_role,
    _sanitize_human_terminal_output,
    _write_enabled_lane,
    bind_provider_runtime_session_identity,
    create_terminal,
    delete_terminal,
    get_output,
    get_terminal,
    get_working_directory,
    reconcile_terminal_context_roles,
    request_provider_runtime_sidecar_reconnect,
    send_input,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("plain\x1b", "plain"),
        ("plain\x1b[?2026", "plain"),
        ("plain\x1b]unterminated", "plain"),
        ("plain\x90unterminated", "plain"),
        ("plain\x1b[\nnext", "plain\nnext"),
    ],
)
def test_human_output_sanitizer_fails_closed_on_partial_controls(raw, expected):
    assert _sanitize_human_terminal_output(raw) == expected


def test_bind_provider_runtime_session_identity_proves_exact_hook_path(monkeypatch, tmp_path):
    identity = "01234567-89ab-cdef-0123-456789abcdef"
    generation = "a" * 64
    working_directory = tmp_path / "project"
    working_directory.mkdir()
    transcript = tmp_path / "codex/sessions/rollout.jsonl"
    provider = MagicMock()
    provider.runtime_sidecar_resume_identity.return_value = identity
    manager = MagicMock()
    manager.get_provider.return_value = provider
    tmux = MagicMock()
    tmux.get_pane_working_directory.return_value = str(working_directory)
    bind = MagicMock(return_value=True)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.get_terminal_metadata",
        lambda _terminal_id: {
            "provider": "codex",
            "runtime_lifecycle": "running",
            "runtime_generation": generation,
            "tmux_session": "cao-managed",
            "tmux_window": "managed",
        },
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.provider_manager", manager
    )
    monkeypatch.setattr("cli_agent_orchestrator.services.terminal_service.tmux_client", tmux)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.bind_terminal_provider_resume_identity",
        bind,
    )

    assert (
        bind_provider_runtime_session_identity(
            "abcdef12",
            resume_identity=identity,
            transcript_path=str(transcript),
            working_directory=str(working_directory),
            source="startup",
            runtime_generation=generation,
        )
        == identity
    )
    provider.runtime_sidecar_resume_identity.assert_called_once_with(
        expected_identity=identity,
        expected_rollout_path=str(transcript),
    )
    bind.assert_called_once_with(
        "abcdef12",
        provider="codex",
        resume_identity=identity,
        runtime_generation=generation,
    )


def test_bind_provider_runtime_session_identity_fails_closed_for_stale_generation(
    monkeypatch, tmp_path
):
    manager = MagicMock()
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.get_terminal_metadata",
        lambda _terminal_id: {
            "provider": "codex",
            "runtime_lifecycle": "running",
            "runtime_generation": "a" * 64,
        },
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.provider_manager", manager
    )

    with pytest.raises(RuntimeError, match="stale or malformed"):
        bind_provider_runtime_session_identity(
            "abcdef12",
            resume_identity="01234567-89ab-cdef-0123-456789abcdef",
            transcript_path=str(tmp_path / "rollout.jsonl"),
            working_directory=str(tmp_path),
            source="startup",
            runtime_generation="b" * 64,
        )
    manager.get_provider.assert_not_called()


def test_bind_provider_runtime_session_identity_fails_closed_for_wrong_identity(
    monkeypatch, tmp_path
):
    generation = "a" * 64
    provider = MagicMock()
    provider.runtime_sidecar_resume_identity.side_effect = ProviderError("wrong rollout")
    manager = MagicMock()
    manager.get_provider.return_value = provider
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.get_terminal_metadata",
        lambda _terminal_id: {
            "provider": "codex",
            "runtime_lifecycle": "running",
            "runtime_generation": generation,
            "tmux_session": "cao-managed",
            "tmux_window": "managed",
        },
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.provider_manager", manager
    )

    with pytest.raises(RuntimeError, match="does not own"):
        bind_provider_runtime_session_identity(
            "abcdef12",
            resume_identity="01234567-89ab-cdef-0123-456789abcdef",
            transcript_path=str(tmp_path / "rollout.jsonl"),
            working_directory=str(tmp_path),
            source="resume",
            runtime_generation=generation,
        )


@pytest.fixture(autouse=True)
def _legacy_profile_resolution_for_service_unit_tests(monkeypatch):
    """Keep low-level lifecycle tests independent from API registry bootstrap order."""
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.control_plane_registry.registry_is_initialized",
        lambda: False,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service._capture_created_runtime_identity",
        lambda _session, _window, terminal_id, generation: SimpleNamespace(
            pane_id="%41",
            pane_pid=4242,
            terminal_id=terminal_id,
            runtime_generation=generation,
            process_start_ticks=777,
        ),
    )
    resolved_session_name: dict[str, str | None] = {"value": None}

    def resolve_session(identifier):
        if identifier != "stable-existing-session":
            resolved_session_name["value"] = identifier
        return {
            "session_id": "stable-existing-session",
            "session_name": resolved_session_name["value"] or identifier,
            "deleted": False,
            "terminals": [{"id": "resident", "runtime_lifecycle": "running"}],
        }

    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.resolve_session_lifetime",
        resolve_session,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.prove_live_session_runtime_authority",
        lambda _name, _terminals, **_kwargs: SimpleNamespace(
            proven=True, reason_code=None, inventory_uncertain=False
        ),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.replace_starting_terminal_runtime_identity",
        lambda *_args, **_kwargs: True,
    )


@pytest.mark.parametrize("new_session", [True, False])
def test_session_inventory_uncertainty_stops_terminal_creation(monkeypatch, new_session):
    tmux = MagicMock()
    tmux.session_exists.return_value = None
    monkeypatch.setattr("cli_agent_orchestrator.services.terminal_service.tmux_client", tmux)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.generate_terminal_id", lambda: "uncertain"
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.generate_window_name",
        lambda _profile: "uncertain-window",
    )

    with pytest.raises(RuntimeError, match="Could not determine whether session"):
        _create_terminal_after_admission(
            "kiro_cli",
            "developer",
            session_name="target",
            new_session=new_session,
        )

    tmux.create_session.assert_not_called()
    tmux.create_window.assert_not_called()
    tmux.kill_session.assert_not_called()
    tmux.kill_window.assert_not_called()


def test_managed_launch_failure_cleanup_uses_complete_identity(monkeypatch, tmp_path):
    from cli_agent_orchestrator.services.managed_worktree_service import ManagedWorktree

    managed = ManagedWorktree(
        kind="task",
        source=str(tmp_path),
        path=str(tmp_path / "managed"),
        branch="cao/task/managed01",
        commit="a" * 40,
    )
    cleanup = MagicMock(return_value={"removed": True})
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.context_launch_admission",
        lambda **kwargs: nullcontext({}),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.managed_worktree_service.create_managed_worktree",
        lambda *args: managed,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.managed_worktree_service.remove_managed_worktree",
        cleanup,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.generate_terminal_id",
        lambda: "managed01",
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service._create_terminal_after_admission",
        MagicMock(side_effect=RuntimeError("launch failed")),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.get_terminal_metadata", lambda _id: None
    )

    with pytest.raises(RuntimeError, match="launch failed"):
        create_terminal(
            "codex", "developer", working_directory=str(tmp_path), managed_worktree_kind="task"
        )

    cleanup.assert_called_once_with(
        {
            "id": "managed01",
            "managed_worktree_kind": "task",
            "managed_worktree_source": str(tmp_path),
            "managed_worktree_branch": "cao/task/managed01",
            "managed_worktree_commit": "a" * 40,
            "launch_worktree": str(tmp_path / "managed"),
        }
    )


def test_managed_launch_failure_retains_worktree_when_durable_runtime_is_uncertain(
    monkeypatch, tmp_path
):
    from cli_agent_orchestrator.services.managed_worktree_service import ManagedWorktree

    managed = ManagedWorktree(
        kind="reviewer",
        source=str(tmp_path),
        path=str(tmp_path / "managed"),
        branch=None,
        commit="b" * 40,
    )
    cleanup = MagicMock()
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.context_launch_admission",
        lambda **kwargs: nullcontext({}),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.managed_worktree_service.create_managed_worktree",
        lambda *args: managed,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.managed_worktree_service.remove_managed_worktree",
        cleanup,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.generate_terminal_id",
        lambda: "managed02",
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service._create_terminal_after_admission",
        MagicMock(side_effect=RuntimeError("launch failed")),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.get_terminal_metadata",
        lambda _id: {"id": "managed02", "runtime_lifecycle": "starting"},
    )

    with pytest.raises(RuntimeError, match="launch failed"):
        create_terminal(
            "codex",
            "reviewer",
            working_directory=str(tmp_path),
            managed_worktree_kind="reviewer",
        )

    cleanup.assert_not_called()


def test_managed_pre_db_launch_failure_retains_worktree_when_target_death_is_unconfirmed(
    monkeypatch, tmp_path
):
    """A failed DB write cannot erase a worktree while its exact tmux target is uncertain."""
    from cli_agent_orchestrator.services.managed_worktree_service import ManagedWorktree

    managed = ManagedWorktree(
        kind="task",
        source=str(tmp_path),
        path=str(tmp_path / "managed"),
        branch="cao/task/managed03",
        commit="c" * 40,
    )
    cleanup = MagicMock()
    tmux = MagicMock()
    tmux.session_exists.return_value = True
    tmux.create_window.return_value = "exact-created-window"
    tmux.kill_window.return_value = False
    tmux.window_exists.return_value = None
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.context_launch_admission",
        lambda **kwargs: nullcontext({}),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.managed_worktree_service.create_managed_worktree",
        lambda *args: managed,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.managed_worktree_service.remove_managed_worktree",
        cleanup,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.generate_terminal_id",
        lambda: "managed03",
    )
    monkeypatch.setattr("cli_agent_orchestrator.services.terminal_service.tmux_client", tmux)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.db_create_terminal",
        MagicMock(side_effect=RuntimeError("database unavailable")),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.get_terminal_metadata", lambda _id: None
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        create_terminal(
            "kiro_cli",
            "developer",
            session_name="existing-session",
            working_directory=str(tmp_path),
            managed_worktree_kind="task",
        )

    tmux.kill_window.assert_called_once_with("existing-session", "exact-created-window")
    tmux.window_exists.assert_called_once_with("existing-session", "exact-created-window")
    cleanup.assert_not_called()


@pytest.mark.parametrize(
    (
        "session_observation",
        "cleanup_expected",
    ),
    [
        (True, False),
        (None, False),
        (False, True),
    ],
)
def test_managed_new_session_create_then_raise_requires_session_death_for_cleanup(
    monkeypatch,
    tmp_path,
    session_observation,
    cleanup_expected,
):
    """Window absence cannot establish death after a partial session create."""
    from cli_agent_orchestrator.services.managed_worktree_service import ManagedWorktree

    managed = ManagedWorktree(
        kind="task",
        source=str(tmp_path),
        path=str(tmp_path / "managed"),
        branch="cao/task/managed-create-raise",
        commit="e" * 40,
    )
    cleanup = MagicMock(return_value={"removed": True})
    tmux = MagicMock()
    tmux.session_exists.side_effect = [False, session_observation]
    tmux.create_session.side_effect = RuntimeError("tmux create then raise")
    tmux.kill_session.return_value = False
    tmux.window_exists.return_value = False
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.context_launch_admission",
        lambda **kwargs: nullcontext({}),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.managed_worktree_service.create_managed_worktree",
        lambda *args: managed,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.managed_worktree_service.remove_managed_worktree",
        cleanup,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.generate_terminal_id",
        lambda: "managed-create-raise",
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.generate_window_name",
        lambda _profile: "exact-attempted-window",
    )
    monkeypatch.setattr("cli_agent_orchestrator.services.terminal_service.tmux_client", tmux)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.get_terminal_metadata", lambda _id: None
    )

    with pytest.raises(RuntimeError, match="tmux create then raise"):
        create_terminal(
            "kiro_cli",
            "developer",
            session_name="new-session",
            new_session=True,
            context_role="work",
            working_directory=str(tmp_path),
            managed_worktree_kind="task",
        )

    tmux.kill_session.assert_called_once_with("cao-new-session")
    tmux.session_exists.assert_has_calls([call("cao-new-session"), call("cao-new-session")])
    tmux.window_exists.assert_not_called()
    if cleanup_expected:
        cleanup.assert_called_once()
    else:
        cleanup.assert_not_called()


def test_managed_new_session_inventory_exception_retains_worktree_for_recovery(
    monkeypatch, tmp_path
):
    """Real inventory uncertainty cannot authorize partial-create cleanup."""
    from cli_agent_orchestrator.services.managed_worktree_service import ManagedWorktree

    managed_path = tmp_path / "managed"
    managed_path.mkdir()
    managed = ManagedWorktree(
        kind="task",
        source=str(tmp_path),
        path=str(managed_path),
        branch="cao/task/managed-inventory-error",
        commit="f" * 40,
    )
    cleanup = MagicMock(return_value={"removed": True})
    tmux = object.__new__(TmuxClient)
    tmux.server = MagicMock()
    tmux._start_credential_free_bootstrap = MagicMock(return_value="cao-bootstrap-test")
    tmux.server.sessions.get.side_effect = [None, None, RuntimeError("inventory unavailable")]
    tmux.server.new_session.side_effect = RuntimeError("tmux create then raise")
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.context_launch_admission",
        lambda **kwargs: nullcontext({}),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.managed_worktree_service.create_managed_worktree",
        lambda *args: managed,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.managed_worktree_service.remove_managed_worktree",
        cleanup,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.generate_terminal_id",
        lambda: "managed-inventory-error",
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.generate_window_name",
        lambda _profile: "exact-attempted-window",
    )
    monkeypatch.setattr("cli_agent_orchestrator.services.terminal_service.tmux_client", tmux)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.get_terminal_metadata", lambda _id: None
    )

    with pytest.raises(RuntimeError, match="tmux create then raise") as raised:
        create_terminal(
            "kiro_cli",
            "developer",
            session_name="new-session",
            new_session=True,
            context_role="work",
            working_directory=str(tmp_path),
            managed_worktree_kind="task",
        )

    outcome = raised.value._cao_launch_cleanup_outcome
    assert outcome.target_attempted is True
    assert outcome.death_confirmed is False
    tmux._start_credential_free_bootstrap.assert_called_once_with(str(managed_path))
    tmux.server.cmd.assert_called_once_with("kill-session", "-t", "cao-bootstrap-test")
    assert tmux.server.sessions.get.call_count == 3
    cleanup.assert_not_called()


def test_managed_new_session_create_then_raise_cleans_after_successful_kill(monkeypatch, tmp_path):
    """A successful session kill is positive death evidence without probing windows."""
    from cli_agent_orchestrator.services.managed_worktree_service import ManagedWorktree

    managed = ManagedWorktree(
        kind="task",
        source=str(tmp_path),
        path=str(tmp_path / "managed"),
        branch="cao/task/managed-create-raise",
        commit="e" * 40,
    )
    cleanup = MagicMock(return_value={"removed": True})
    tmux = MagicMock()
    tmux.session_exists.return_value = False
    tmux.create_session.side_effect = RuntimeError("tmux create then raise")
    tmux.kill_session.return_value = True
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.context_launch_admission",
        lambda **kwargs: nullcontext({}),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.managed_worktree_service.create_managed_worktree",
        lambda *args: managed,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.managed_worktree_service.remove_managed_worktree",
        cleanup,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.generate_terminal_id",
        lambda: "managed-create-raise",
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.generate_window_name",
        lambda _profile: "exact-attempted-window",
    )
    monkeypatch.setattr("cli_agent_orchestrator.services.terminal_service.tmux_client", tmux)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.get_terminal_metadata", lambda _id: None
    )

    with pytest.raises(RuntimeError, match="tmux create then raise"):
        create_terminal(
            "kiro_cli",
            "developer",
            session_name="new-session",
            new_session=True,
            context_role="work",
            working_directory=str(tmp_path),
            managed_worktree_kind="task",
        )

    tmux.kill_session.assert_called_once_with("cao-new-session")
    tmux.session_exists.assert_called_once_with("cao-new-session")
    tmux.window_exists.assert_not_called()
    cleanup.assert_called_once()


def test_managed_existing_session_create_then_raise_keeps_exact_window_rule(monkeypatch, tmp_path):
    """Existing sessions still use their attempted window as the cleanup target."""
    from cli_agent_orchestrator.services.managed_worktree_service import ManagedWorktree

    managed = ManagedWorktree(
        kind="task",
        source=str(tmp_path),
        path=str(tmp_path / "managed"),
        branch="cao/task/managed-create-raise",
        commit="e" * 40,
    )
    cleanup = MagicMock(return_value={"removed": True})
    tmux = MagicMock()
    tmux.session_exists.return_value = True
    tmux.create_window.side_effect = RuntimeError("tmux create then raise")
    tmux.kill_window.return_value = False
    tmux.window_exists.return_value = False
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.context_launch_admission",
        lambda **kwargs: nullcontext({}),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.managed_worktree_service.create_managed_worktree",
        lambda *args: managed,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.managed_worktree_service.remove_managed_worktree",
        cleanup,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.generate_terminal_id",
        lambda: "managed-create-raise",
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.generate_window_name",
        lambda _profile: "exact-attempted-window",
    )
    monkeypatch.setattr("cli_agent_orchestrator.services.terminal_service.tmux_client", tmux)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.get_terminal_metadata", lambda _id: None
    )

    with pytest.raises(RuntimeError, match="tmux create then raise"):
        create_terminal(
            "kiro_cli",
            "developer",
            session_name="existing-session",
            working_directory=str(tmp_path),
            managed_worktree_kind="task",
        )

    tmux.kill_window.assert_called_once_with("existing-session", "exact-attempted-window")
    tmux.window_exists.assert_called_once_with("existing-session", "exact-attempted-window")
    cleanup.assert_called_once()


def test_managed_pre_db_launch_failure_removes_worktree_after_positive_target_death(
    monkeypatch, tmp_path
):
    """The same pre-DB failure may clean up only after its exact target is dead."""
    from cli_agent_orchestrator.services.managed_worktree_service import ManagedWorktree

    managed = ManagedWorktree(
        kind="task",
        source=str(tmp_path),
        path=str(tmp_path / "managed"),
        branch="cao/task/managed04",
        commit="d" * 40,
    )
    cleanup = MagicMock(return_value={"removed": True})
    tmux = MagicMock()
    tmux.session_exists.return_value = True
    tmux.create_window.return_value = "exact-created-window"
    tmux.kill_window.return_value = True
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.context_launch_admission",
        lambda **kwargs: nullcontext({}),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.managed_worktree_service.create_managed_worktree",
        lambda *args: managed,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.managed_worktree_service.remove_managed_worktree",
        cleanup,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.generate_terminal_id",
        lambda: "managed04",
    )
    monkeypatch.setattr("cli_agent_orchestrator.services.terminal_service.tmux_client", tmux)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.db_create_terminal",
        MagicMock(side_effect=RuntimeError("database unavailable")),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.get_terminal_metadata", lambda _id: None
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        create_terminal(
            "kiro_cli",
            "developer",
            session_name="existing-session",
            working_directory=str(tmp_path),
            managed_worktree_kind="task",
        )

    cleanup.assert_called_once_with(
        {
            "id": "managed04",
            "managed_worktree_kind": "task",
            "managed_worktree_source": str(tmp_path),
            "managed_worktree_branch": "cao/task/managed04",
            "managed_worktree_commit": "d" * 40,
            "launch_worktree": str(tmp_path / "managed"),
        }
    )


def test_worktree_canonicalization(tmp_path):
    worktree = tmp_path / "worktree"
    nested = worktree / "src/package"
    nested.mkdir(parents=True)
    (worktree / ".git").mkdir()
    assert _canonical_worktree(str(nested)) == str(worktree)


@pytest.mark.parametrize(
    ("profile_name", "new_session", "expected"),
    [
        ("supervisor_terra_medium", True, "supervisor"),
        ("supervisor_sol_medium", True, "supervisor"),
        ("critical_sol_xhigh_owner", True, "supervisor"),
        ("developer", False, "work"),
        ("reviewer", False, "work"),
        ("critical_sol_xhigh_owner", False, "work"),
    ],
)
def test_context_role_uses_launch_topology_not_profile_execution_semantics(
    monkeypatch, tmp_path, profile_name, new_session, expected
):
    captured = {}
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.context_launch_admission",
        lambda **kwargs: (captured.update(admission=kwargs), nullcontext({}))[1],
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service._create_terminal_after_admission",
        lambda **kwargs: captured.update(creation=kwargs) or "created",
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.validate_owner_launch_grant",
        lambda *args, **kwargs: True,
    )
    owner_authority = (
        {
            "owner_grant_token": "structured-owner-token",
            "owner_grant_launch_id": "topology-test",
        }
        if profile_name == "critical_sol_xhigh_owner"
        else {}
    )

    assert (
        create_terminal(
            "codex",
            profile_name,
            new_session=new_session,
            session_name=None if new_session else "cao-existing",
            working_directory=str(tmp_path),
            **owner_authority,
        )
        == "created"
    )
    assert captured["admission"]["context_role"] == expected
    assert captured["creation"]["context_role"] == expected


def test_explicit_context_role_is_validated_without_profile_name_inference():
    assert _resolve_context_role(new_session=False, context_role="supervisor") == "supervisor"
    assert _resolve_context_role(new_session=True, context_role="work") == "work"
    with pytest.raises(ValueError, match="context_role must be supervisor or work"):
        _resolve_context_role(new_session=True, context_role="reviewer")


def test_execution_mode_does_not_redefine_topology_residency():
    assert _resolve_context_role(new_session=True, context_role=None) == "supervisor"
    assert _resolve_context_role(new_session=False, context_role=None) == "work"


def test_context_role_reconciliation_delegates_to_topology_authority(monkeypatch):
    reconcile = MagicMock(return_value=3)
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.reconcile_terminal_context_roles_by_topology",
        reconcile,
    )

    assert reconcile_terminal_context_roles() == 3
    assert reconcile_terminal_context_roles(dry_run=True) == 3
    assert reconcile.call_args_list == [call(dry_run=False), call(dry_run=True)]


@pytest.mark.parametrize(
    ("provider", "mcp_servers"),
    [
        *((provider.value, None) for provider in ProviderType),
        ("unknown", None),
        (ProviderType.CLAUDE_CODE.value, {"reader-looking-mcp": {"command": "server"}}),
    ],
)
def test_every_provider_effective_lane_is_admitted_as_writer(
    provider, mcp_servers, monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.load_agent_profile",
        lambda _: AgentProfile(name="reviewer", description="Reviewer", mcpServers=mcp_servers),
    )
    captured = {}
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.context_launch_admission",
        lambda **kwargs: (captured.update(admission=kwargs), nullcontext({}))[1],
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service._create_terminal_after_admission",
        lambda **kwargs: captured.update(creation=kwargs) or "created",
    )

    assert _write_enabled_lane(provider, "reviewer", ["fs_read", "fs_list"])
    assert (
        create_terminal(
            provider,
            "reviewer",
            working_directory=str(tmp_path),
            allowed_tools=["fs_read", "fs_list"],
        )
        == "created"
    )
    assert captured["admission"]["write_enabled"] is True
    assert captured["creation"]["write_enabled"] is True


def test_worktree_lane_inventory_uses_immutable_launch_metadata_when_pane_cwd_moves(
    monkeypatch,
):
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.list_all_terminals",
        lambda: [
            {
                "id": "terminal",
                "launch_worktree": "/launch/worktree",
                "write_enabled": True,
            }
        ],
    )
    pane_cwd = MagicMock(side_effect=AssertionError("mutable pane cwd must not be read"))
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.tmux_client.get_pane_working_directory",
        pane_cwd,
    )

    assert _active_worktree_lanes() == [("/launch/worktree", True)]
    pane_cwd.assert_not_called()


@pytest.mark.parametrize(
    "terminal",
    [
        {"id": "terminal", "launch_worktree": None, "write_enabled": True},
        {"id": "terminal", "launch_worktree": "/launch/worktree", "write_enabled": None},
    ],
)
def test_worktree_lane_inventory_is_uncertain_when_launch_metadata_unavailable(
    monkeypatch, terminal
):
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.list_all_terminals",
        lambda: [terminal],
    )
    assert _active_worktree_lanes() is None


@pytest.fixture(autouse=True)
def isolate_operational_admission(monkeypatch):
    """These tests isolate terminal construction; admission has its own contour tests."""
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.context_launch_admission",
        lambda **kwargs: nullcontext({}),
    )


class TestCreateTerminal:
    """Tests for create_terminal function."""

    @pytest.mark.parametrize("session_name", ["   ", "release\x00candidate"])
    def test_invalid_new_session_name_is_rejected_before_launch_admission(
        self, monkeypatch, session_name
    ):
        admission = MagicMock(return_value=nullcontext({}))
        create_worktree = MagicMock()
        remove_worktree = MagicMock()
        create_after_admission = MagicMock()
        generate_id = MagicMock(return_value="must-not-be-generated")
        tmux = MagicMock()
        provider = MagicMock()
        db_create = MagicMock()
        db_delete = MagicMock()

        monkeypatch.setattr(
            "cli_agent_orchestrator.services.operations_service.context_launch_admission",
            admission,
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.managed_worktree_service.create_managed_worktree",
            create_worktree,
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.managed_worktree_service.remove_managed_worktree",
            remove_worktree,
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.terminal_service._create_terminal_after_admission",
            create_after_admission,
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.terminal_service.generate_terminal_id", generate_id
        )
        monkeypatch.setattr("cli_agent_orchestrator.services.terminal_service.tmux_client", tmux)
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.terminal_service.provider_manager", provider
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.terminal_service.db_create_terminal", db_create
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.terminal_service.db_delete_terminal", db_delete
        )

        with pytest.raises(ValueError):
            create_terminal(
                "codex",
                "developer",
                session_name=session_name,
                new_session=True,
                managed_worktree_kind="task",
            )

        admission.assert_not_called()
        create_worktree.assert_not_called()
        remove_worktree.assert_not_called()
        create_after_admission.assert_not_called()
        generate_id.assert_not_called()
        tmux.assert_not_called()
        provider.assert_not_called()
        db_create.assert_not_called()
        db_delete.assert_not_called()

    @pytest.mark.parametrize(
        ("session_name", "expected"),
        [
            (r"team\child", "team_child"),
            ("team/child", "team_child"),
            ("../team", "_team"),
            ("  Design Review — Привет 東京  ", "Design Review — Привет 東京"),
        ],
    )
    def test_new_session_name_is_normalized_before_admission_and_creation(
        self, monkeypatch, session_name, expected
    ):
        admission = MagicMock(return_value=nullcontext({}))
        create_after_admission = MagicMock(return_value="created")
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.operations_service.context_launch_admission",
            admission,
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.terminal_service._create_terminal_after_admission",
            create_after_admission,
        )

        result = create_terminal(
            "codex",
            "developer",
            session_name=session_name,
            new_session=True,
        )

        assert result == "created"
        admission.assert_called_once()
        assert create_after_admission.call_args.kwargs["session_name"] == expected

    def test_direct_helper_empty_name_preserves_validation_error(self):
        with pytest.raises(ValueError, match="Session name must not be empty"):
            _create_terminal_after_admission(
                "codex",
                "developer",
                session_name="   ",
                new_session=True,
            )

    def test_existing_session_identifier_is_not_renormalized(self, monkeypatch):
        tmux = MagicMock()
        db_create = MagicMock()
        prove_runtime = MagicMock(
            return_value=SimpleNamespace(proven=True, reason_code=None, inventory_uncertain=False)
        )
        tmux.session_exists.return_value = True
        tmux.create_window.return_value = "developer-window"
        monkeypatch.setattr("cli_agent_orchestrator.services.terminal_service.tmux_client", tmux)
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.terminal_service.db_create_terminal",
            db_create,
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.terminal_service.prove_live_session_runtime_authority",
            prove_runtime,
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.terminal_service.load_agent_profile",
            lambda _name: AgentProfile(name="developer", description="Developer"),
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.terminal_service.provider_manager.create_provider",
            MagicMock(return_value=MagicMock()),
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.terminal_service.TERMINAL_LOG_DIR", MagicMock()
        )

        create_terminal(
            "kiro_cli",
            "developer",
            session_name="cao-existing.v1",
            new_session=False,
        )

        tmux.session_exists.assert_called_once_with("cao-existing.v1")
        assert tmux.create_window.call_args.args[0] == "cao-existing.v1"
        assert db_create.call_args.kwargs["session_lifetime_id"] == "stable-existing-session"
        assert prove_runtime.call_count == 2
        prospective = prove_runtime.call_args_list[1].args[1][-1]
        assert prospective["id"] == db_create.call_args.args[0]
        assert prospective["tmux_window"] == "developer-window"
        assert prospective["runtime_lifecycle"] == "starting"

    def test_existing_session_post_create_identity_divergence_aborts_before_metadata(
        self, monkeypatch
    ):
        from cli_agent_orchestrator.services.operations_service import AdmissionDenied

        tmux = MagicMock()
        tmux.session_exists.return_value = True
        tmux.create_window.return_value = "developer-window"
        tmux.kill_window.return_value = True
        db_create = MagicMock()
        monkeypatch.setattr("cli_agent_orchestrator.services.terminal_service.tmux_client", tmux)
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.terminal_service.generate_terminal_id",
            lambda: "new-terminal",
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.terminal_service.generate_window_name",
            lambda _profile: "developer-window",
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.terminal_service.db_create_terminal", db_create
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.terminal_service.resolve_session_lifetime",
            lambda _identifier: {
                "session_id": "stable-existing-session",
                "session_name": "cao-existing",
                "deleted": False,
                "terminals": [{"id": "resident", "runtime_lifecycle": "running"}],
            },
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.terminal_service.provider_manager", MagicMock()
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.terminal_service.prove_live_session_runtime_authority",
            lambda *_args, **_kwargs: SimpleNamespace(
                proven=False,
                reason_code="SESSION_RUNTIME_AUTHORITY_DIVERGED",
                inventory_uncertain=False,
            ),
        )

        with pytest.raises(AdmissionDenied) as error:
            _create_terminal_after_admission(
                "kiro_cli",
                "developer",
                session_name="cao-existing",
                new_session=False,
                session_lifetime_id="stable-existing-session",
            )

        assert error.value.reason_code == "SESSION_RUNTIME_AUTHORITY_DIVERGED"
        db_create.assert_not_called()
        tmux.kill_window.assert_called_once_with("cao-existing", "developer-window")

    @patch("cli_agent_orchestrator.services.terminal_service.TERMINAL_LOG_DIR")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.db_create_terminal")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_window_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_session_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_terminal_id")
    @patch("cli_agent_orchestrator.services.terminal_service.load_agent_profile")
    def test_create_terminal_new_session(
        self,
        mock_load_profile,
        mock_gen_id,
        mock_gen_session,
        mock_gen_window,
        mock_tmux,
        mock_db_create,
        mock_provider_manager,
        mock_log_dir,
    ):
        """Test creating terminal with new session."""
        mock_gen_id.return_value = "test1234"
        mock_gen_session.return_value = "cao-session"
        mock_gen_window.return_value = "developer-abcd"
        mock_tmux.session_exists.return_value = False
        mock_load_profile.return_value = AgentProfile(name="developer", description="Developer")
        mock_provider = MagicMock()
        mock_provider_manager.create_provider.return_value = mock_provider
        mock_log_path = MagicMock()
        mock_log_dir.__truediv__.return_value = mock_log_path

        result = create_terminal("kiro_cli", "developer", new_session=True)

        assert result.id == "test1234"
        mock_tmux.create_session.assert_called_once()
        mock_provider.initialize.assert_called_once()

    @patch("cli_agent_orchestrator.services.terminal_service.TERMINAL_LOG_DIR")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.db_create_terminal")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_window_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_terminal_id")
    @patch("cli_agent_orchestrator.services.terminal_service.load_agent_profile")
    @pytest.mark.parametrize(
        ("session_name", "expected"),
        [
            (
                "WORKSPACE.UI.P1 — Responsive Final Correction & Implementation Handoff",
                "cao-WORKSPACE_UI_P1 — Responsive Final Correction & Implementation Handoff",
            ),
            (r"Release../\\candidate::Привет", "cao-Release_candidate_Привет"),
        ],
    )
    def test_normalizes_name_before_creating_tmux_session(
        self,
        mock_load_profile,
        mock_gen_id,
        mock_gen_window,
        mock_tmux,
        mock_db_create,
        mock_provider_manager,
        mock_log_dir,
        session_name,
        expected,
    ):
        mock_gen_id.return_value = "test1234"
        mock_gen_window.return_value = "developer-abcd"
        mock_tmux.session_exists.return_value = False
        mock_load_profile.return_value = AgentProfile(name="developer", description="Developer")
        mock_provider_manager.create_provider.return_value = MagicMock()
        mock_log_dir.__truediv__.return_value = MagicMock()

        create_terminal(
            "kiro_cli",
            "developer",
            session_name=session_name,
            new_session=True,
        )

        assert mock_tmux.create_session.call_args.args[0] == expected

    @patch("cli_agent_orchestrator.services.terminal_service.TERMINAL_LOG_DIR")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.db_delete_terminal")
    @patch("cli_agent_orchestrator.services.terminal_service.db_create_terminal")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_window_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_session_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_terminal_id")
    @patch("cli_agent_orchestrator.services.terminal_service.load_agent_profile")
    def test_provider_startup_failure_removes_persisted_terminal_metadata(
        self,
        mock_load_profile,
        mock_gen_id,
        mock_gen_session,
        mock_gen_window,
        mock_tmux,
        mock_db_create,
        mock_db_delete,
        mock_provider_manager,
        mock_log_dir,
    ):
        """A failed launch cannot leave an API-visible orphan terminal."""
        mock_gen_id.return_value = "startup-fail"
        mock_gen_session.return_value = "cao-startup-fail"
        mock_gen_window.return_value = "developer-fail"
        mock_tmux.session_exists.return_value = False
        mock_load_profile.return_value = AgentProfile(name="developer", description="Developer")
        mock_provider = MagicMock()
        mock_provider.initialize.side_effect = RuntimeError("kiro-cli: command not found")
        mock_provider_manager.create_provider.return_value = mock_provider
        mock_log_dir.__truediv__.return_value = MagicMock()

        with pytest.raises(RuntimeError, match="kiro-cli: command not found"):
            create_terminal("kiro_cli", "developer", new_session=True)

        mock_db_create.assert_called_once()
        mock_db_delete.assert_called_once_with("startup-fail")
        mock_tmux.kill_session.assert_called_once_with("cao-startup-fail")

    @patch("cli_agent_orchestrator.services.terminal_service.TERMINAL_LOG_DIR")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.db_delete_terminal")
    @patch("cli_agent_orchestrator.services.terminal_service.db_create_terminal")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_window_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_session_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_terminal_id")
    @patch("cli_agent_orchestrator.services.terminal_service.load_agent_profile")
    def test_provider_startup_failure_retains_lease_when_death_is_uncertain(
        self,
        mock_load_profile,
        mock_gen_id,
        mock_gen_session,
        mock_gen_window,
        mock_tmux,
        mock_db_create,
        mock_db_delete,
        mock_provider_manager,
        mock_log_dir,
    ):
        mock_gen_id.return_value = "startup-uncertain"
        mock_gen_session.return_value = "cao-startup-uncertain"
        mock_gen_window.return_value = "developer-uncertain"
        mock_tmux.session_exists.side_effect = [False, None]
        mock_tmux.kill_session.return_value = False
        mock_load_profile.return_value = AgentProfile(name="developer", description="Developer")
        provider = MagicMock()
        provider.initialize.side_effect = RuntimeError("startup failed")
        mock_provider_manager.create_provider.return_value = provider
        mock_log_dir.__truediv__.return_value = MagicMock()

        with pytest.raises(RuntimeError, match="startup failed"):
            create_terminal("kiro_cli", "developer", new_session=True)

        mock_db_create.assert_called_once()
        mock_db_delete.assert_not_called()
        mock_tmux.window_exists.assert_not_called()

    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.db_delete_terminal")
    @patch("cli_agent_orchestrator.services.terminal_service.db_create_terminal")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_window_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_session_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_terminal_id")
    @patch("cli_agent_orchestrator.services.terminal_service.load_agent_profile")
    @pytest.mark.parametrize(
        ("database_error", "reason_code"),
        [
            (WorktreeWriterLeaseConflict("/srv/worktree"), "WORKTREE_WRITER_LEASE_HELD"),
            (
                UnreconciledTerminalAuthority("live-pre-p1"),
                "WORKTREE_AUTHORITY_UNRECONCILED",
            ),
        ],
    )
    def test_writer_admission_conflict_stops_before_provider_execution(
        self,
        mock_load_profile,
        mock_gen_id,
        mock_gen_session,
        mock_gen_window,
        mock_tmux,
        mock_db_create,
        mock_db_delete,
        mock_provider_manager,
        database_error,
        reason_code,
    ):
        mock_gen_id.return_value = "writer-conflict"
        mock_gen_session.return_value = "cao-writer-conflict"
        mock_gen_window.return_value = "developer-conflict"
        mock_tmux.session_exists.return_value = False
        mock_tmux.kill_session.return_value = True
        mock_load_profile.return_value = AgentProfile(name="developer", description="Developer")
        mock_db_create.side_effect = database_error

        with pytest.raises(Exception, match=reason_code):
            create_terminal("codex", "developer", new_session=True)

        mock_provider_manager.create_provider.assert_not_called()
        mock_db_delete.assert_not_called()


class TestCodexStartupReliability:
    """Service-level startup capture and bounded-retry coverage for Codex."""

    @patch("cli_agent_orchestrator.services.terminal_service.TERMINAL_LOG_DIR")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.db_create_terminal")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_window_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_session_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_terminal_id")
    @patch("cli_agent_orchestrator.services.terminal_service.build_skill_catalog")
    @patch("cli_agent_orchestrator.services.terminal_service.load_agent_profile")
    def test_no_ready_state_cleans_attempt_then_retries_once_and_succeeds(
        self,
        mock_load_profile,
        mock_build_skills,
        mock_gen_id,
        mock_gen_session,
        mock_gen_window,
        mock_tmux,
        mock_db_create,
        mock_provider_manager,
        mock_log_dir,
    ):
        mock_gen_id.return_value = "test1234"
        mock_gen_session.return_value = "cao-retry"
        mock_gen_window.return_value = "supervisor-abcd"
        mock_tmux.session_exists.return_value = False
        mock_tmux.create_session.side_effect = ["supervisor-abcd", "supervisor-retry"]
        mock_load_profile.return_value = AgentProfile(name="supervisor", description="Supervisor")
        mock_build_skills.return_value = ""
        first_provider = MagicMock()
        first_provider.initialize.side_effect = CodexStartupNoReadyError("no ready", "attempt one")
        second_provider = MagicMock()
        second_provider.runtime_sidecar_resume_identity.return_value = (
            "01234567-89ab-cdef-0123-456789abcdef"
        )
        mock_provider_manager.create_provider.side_effect = [first_provider, second_provider]
        mock_log_path = MagicMock()
        mock_log_dir.__truediv__.return_value = mock_log_path
        call_order = []
        mock_tmux.pipe_pane.side_effect = lambda *_: call_order.append("pipe")
        first_provider.initialize.side_effect = lambda: (
            call_order.append("first_initialize"),
            (_ for _ in ()).throw(CodexStartupNoReadyError("no ready", "attempt one")),
        )[1]
        second_provider.initialize.side_effect = lambda: call_order.append("second_initialize")

        with patch(
            "cli_agent_orchestrator.services.terminal_service.bind_terminal_provider_resume_identity",
            return_value=True,
        ) as bind_identity:
            result = create_terminal("codex", "supervisor", new_session=True)

        assert result.name == "supervisor-retry"
        assert call_order == ["pipe", "first_initialize", "pipe", "second_initialize"]
        mock_provider_manager.cleanup_provider.assert_called_once_with("test1234")
        mock_tmux.kill_session.assert_called_once_with("cao-retry")
        assert mock_tmux.kill_window.call_count == 0
        assert mock_provider_manager.create_provider.call_count == 2
        bind_identity.assert_not_called()
        mock_log_path.touch.assert_called_once()

    @patch("cli_agent_orchestrator.services.terminal_service.TERMINAL_LOG_DIR")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.db_create_terminal")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_window_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_session_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_terminal_id")
    @patch("cli_agent_orchestrator.services.terminal_service.build_skill_catalog")
    @patch("cli_agent_orchestrator.services.terminal_service.load_agent_profile")
    def test_second_no_ready_failure_keeps_startup_log_and_leaks_no_session(
        self,
        mock_load_profile,
        mock_build_skills,
        mock_gen_id,
        mock_gen_session,
        mock_gen_window,
        mock_tmux,
        mock_db_create,
        mock_provider_manager,
        mock_log_dir,
    ):
        mock_gen_id.return_value = "test1234"
        mock_gen_session.return_value = "cao-retry-fail"
        mock_gen_window.return_value = "supervisor-abcd"
        mock_tmux.session_exists.return_value = False
        mock_tmux.create_session.side_effect = ["supervisor-abcd", "supervisor-retry"]
        mock_tmux.kill_session.return_value = True
        mock_load_profile.return_value = AgentProfile(name="supervisor", description="Supervisor")
        mock_build_skills.return_value = ""
        first_provider = MagicMock()
        first_provider.initialize.side_effect = CodexStartupNoReadyError("no ready", "attempt one")
        second_provider = MagicMock()
        second_provider.initialize.side_effect = CodexStartupNoReadyError("no ready", "attempt two")
        mock_provider_manager.create_provider.side_effect = [first_provider, second_provider]
        mock_log_path = MagicMock()
        mock_log_path.__str__.return_value = "/tmp/test1234.log"
        mock_log_dir.__truediv__.return_value = mock_log_path

        with pytest.raises(RuntimeError, match="startup output retained at /tmp/test1234.log"):
            create_terminal("codex", "supervisor", new_session=True)

        # One clean retry and the outer failure cleanup each remove the tmux
        # session, so no first-attempt process/window can survive.
        assert mock_tmux.kill_session.call_count == 2
        assert mock_tmux.kill_window.call_count == 0
        assert mock_provider_manager.cleanup_provider.call_count == 2
        mock_log_path.touch.assert_called_once()

    @patch("cli_agent_orchestrator.services.terminal_service.TERMINAL_LOG_DIR")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.db_create_terminal")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_window_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_session_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_terminal_id")
    @patch("cli_agent_orchestrator.services.terminal_service.build_skill_catalog")
    @patch("cli_agent_orchestrator.services.terminal_service.load_agent_profile")
    def test_early_codex_error_does_not_retry_and_retains_startup_capture(
        self,
        mock_load_profile,
        mock_build_skills,
        mock_gen_id,
        mock_gen_session,
        mock_gen_window,
        mock_tmux,
        mock_db_create,
        mock_provider_manager,
        mock_log_dir,
    ):
        mock_gen_id.return_value = "test1234"
        mock_gen_session.return_value = "cao-early-error"
        mock_gen_window.return_value = "supervisor-abcd"
        mock_tmux.session_exists.return_value = False
        mock_load_profile.return_value = AgentProfile(name="supervisor", description="Supervisor")
        mock_build_skills.return_value = ""
        provider = MagicMock()
        provider.initialize.side_effect = CodexStartupError("bad config", "error: bad config")
        mock_provider_manager.create_provider.return_value = provider
        mock_log_path = MagicMock()
        mock_log_dir.__truediv__.return_value = mock_log_path

        with pytest.raises(CodexStartupError, match="bad config"):
            create_terminal("codex", "supervisor", new_session=True)

        mock_tmux.pipe_pane.assert_called_once()
        mock_provider_manager.create_provider.assert_called_once()
        mock_tmux.kill_session.assert_called_once_with("cao-early-error")
        mock_log_path.touch.assert_called_once()

    @patch("cli_agent_orchestrator.services.terminal_service.TERMINAL_LOG_DIR")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.db_create_terminal")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_window_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_session_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_terminal_id")
    @patch("cli_agent_orchestrator.services.terminal_service.load_agent_profile")
    def test_create_terminal_existing_session(
        self,
        mock_load_profile,
        mock_gen_id,
        mock_gen_session,
        mock_gen_window,
        mock_tmux,
        mock_db_create,
        mock_provider_manager,
        mock_log_dir,
    ):
        """Test creating terminal in existing session."""
        mock_gen_id.return_value = "test1234"
        mock_gen_session.return_value = "cao-session"
        mock_gen_window.return_value = "developer-abcd"
        mock_tmux.session_exists.return_value = True
        mock_tmux.create_window.return_value = "developer-abcd"
        mock_load_profile.return_value = AgentProfile(name="developer", description="Developer")
        mock_provider = MagicMock()
        mock_provider_manager.create_provider.return_value = mock_provider
        mock_log_path = MagicMock()
        mock_log_dir.__truediv__.return_value = mock_log_path

        result = create_terminal("kiro_cli", "developer", session_name="cao-existing")

        assert result.id == "test1234"
        mock_tmux.create_window.assert_called_once()

    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_window_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_session_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_terminal_id")
    @patch("cli_agent_orchestrator.services.terminal_service.load_agent_profile")
    def test_create_terminal_session_not_found(
        self, mock_load_profile, mock_gen_id, mock_gen_session, mock_gen_window, mock_tmux
    ):
        """Test creating terminal when session not found."""
        mock_gen_id.return_value = "test1234"
        mock_gen_session.return_value = "cao-session"
        mock_gen_window.return_value = "developer-abcd"
        mock_tmux.session_exists.return_value = False
        mock_load_profile.return_value = AgentProfile(name="developer", description="Developer")

        with pytest.raises(ValueError, match="not found"):
            create_terminal("kiro_cli", "developer", session_name="cao-nonexistent")

    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_window_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_session_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_terminal_id")
    @patch("cli_agent_orchestrator.services.terminal_service.load_agent_profile")
    def test_create_terminal_session_already_exists(
        self, mock_load_profile, mock_gen_id, mock_gen_session, mock_gen_window, mock_tmux
    ):
        """Test creating terminal when session already exists."""
        mock_gen_id.return_value = "test1234"
        mock_gen_session.return_value = "cao-session"
        mock_gen_window.return_value = "developer-abcd"
        mock_tmux.session_exists.return_value = True
        mock_load_profile.return_value = AgentProfile(name="developer", description="Developer")

        with pytest.raises(ValueError, match="already exists"):
            create_terminal("kiro_cli", "developer", session_name="cao-existing", new_session=True)

    @patch(
        "cli_agent_orchestrator.services.terminal_service.bind_terminal_provider_resume_identity",
        return_value=True,
    )
    @patch("cli_agent_orchestrator.services.terminal_service.TERMINAL_LOG_DIR")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.db_create_terminal")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_window_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_session_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_terminal_id")
    @patch("cli_agent_orchestrator.services.terminal_service.build_skill_catalog")
    @patch("cli_agent_orchestrator.services.terminal_service.load_agent_profile")
    def test_create_terminal_appends_skill_catalog(
        self,
        mock_load_profile,
        mock_build_skill_catalog,
        mock_gen_id,
        mock_gen_session,
        mock_gen_window,
        mock_tmux,
        mock_db_create,
        mock_provider_manager,
        mock_log_dir,
        mock_bind_resume_identity,
    ):
        """Providers that consume runtime prompts should receive the global skill catalog."""
        mock_gen_id.return_value = "test1234"
        mock_gen_session.return_value = "cao-session"
        mock_gen_window.return_value = "developer-abcd"
        mock_tmux.session_exists.return_value = False
        mock_load_profile.return_value = AgentProfile(
            name="developer",
            description="Developer",
            system_prompt="You are the developer.",
        )
        mock_build_skill_catalog.return_value = (
            "## Available Skills\n\n"
            "The following skills are available exclusively in this CAO orchestration context. "
            "To load a skill's full content, use the `load_skill` MCP tool provided by the "
            "CAO MCP server. These skills are not accessible through provider-native skill "
            "commands or directories.\n\n"
            "- **cao-worker-protocols**: Worker communication\n"
            "- **python-testing**: Pytest conventions"
        )
        mock_provider = MagicMock()
        mock_provider.runtime_sidecar_resume_identity.return_value = (
            "01234567-89ab-cdef-0123-456789abcdef"
        )
        mock_provider_manager.create_provider.return_value = mock_provider
        mock_log_path = MagicMock()
        mock_log_dir.__truediv__.return_value = mock_log_path

        create_terminal("codex", "developer", new_session=True)

        skill_prompt = mock_provider_manager.create_provider.call_args.kwargs["skill_prompt"]
        assert skill_prompt == (
            "## Available Skills\n\n"
            "The following skills are available exclusively in this CAO orchestration context. "
            "To load a skill's full content, use the `load_skill` MCP tool provided by the "
            "CAO MCP server. These skills are not accessible through provider-native skill "
            "commands or directories.\n\n"
            "- **cao-worker-protocols**: Worker communication\n"
            "- **python-testing**: Pytest conventions"
        )
        mock_bind_resume_identity.assert_not_called()

    @patch(
        "cli_agent_orchestrator.services.terminal_service.bind_terminal_provider_resume_identity",
        return_value=True,
    )
    @patch("cli_agent_orchestrator.services.terminal_service.TERMINAL_LOG_DIR")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.db_create_terminal")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_window_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_session_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_terminal_id")
    @patch("cli_agent_orchestrator.services.terminal_service.build_skill_catalog")
    @patch("cli_agent_orchestrator.services.terminal_service.load_agent_profile")
    def test_create_terminal_without_skills_is_unchanged(
        self,
        mock_load_profile,
        mock_build_skill_catalog,
        mock_gen_id,
        mock_gen_session,
        mock_gen_window,
        mock_tmux,
        mock_db_create,
        mock_provider_manager,
        mock_log_dir,
        _mock_bind_resume_identity,
    ):
        """Providers should receive an empty skill prompt when no skills are installed."""
        mock_gen_id.return_value = "test1234"
        mock_gen_session.return_value = "cao-session"
        mock_gen_window.return_value = "developer-abcd"
        mock_tmux.session_exists.return_value = False
        mock_load_profile.return_value = AgentProfile(
            name="developer",
            description="Developer",
            system_prompt="Base prompt",
        )
        mock_build_skill_catalog.return_value = ""
        mock_provider = MagicMock()
        mock_provider.runtime_sidecar_resume_identity.return_value = (
            "01234567-89ab-cdef-0123-456789abcdef"
        )
        mock_provider_manager.create_provider.return_value = mock_provider
        mock_log_path = MagicMock()
        mock_log_dir.__truediv__.return_value = mock_log_path

        create_terminal("codex", "developer", new_session=True)

        skill_prompt = mock_provider_manager.create_provider.call_args.kwargs["skill_prompt"]
        assert skill_prompt == ""
        mock_build_skill_catalog.assert_called_once_with()

    @pytest.mark.parametrize("provider_name", ["kiro_cli", "q_cli", "copilot_cli"])
    @patch("cli_agent_orchestrator.services.terminal_service.TERMINAL_LOG_DIR")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.db_create_terminal")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_window_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_session_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_terminal_id")
    @patch("cli_agent_orchestrator.services.terminal_service.build_skill_catalog")
    @patch("cli_agent_orchestrator.services.terminal_service.load_agent_profile")
    def test_create_terminal_does_not_pass_skill_prompt_to_non_runtime_provider(
        self,
        mock_load_profile,
        mock_build_skill_catalog,
        mock_gen_id,
        mock_gen_session,
        mock_gen_window,
        mock_tmux,
        mock_db_create,
        mock_provider_manager,
        mock_log_dir,
        provider_name,
    ):
        """Kiro, Q, and Copilot should receive skill_prompt=None."""
        mock_gen_id.return_value = "test1234"
        mock_gen_session.return_value = "cao-session"
        mock_gen_window.return_value = "developer-abcd"
        mock_tmux.session_exists.return_value = False
        mock_load_profile.return_value = AgentProfile(
            name="developer",
            description="Developer",
            system_prompt="Base prompt",
        )
        mock_build_skill_catalog.return_value = (
            "## Available Skills\n\n"
            "The following skills are available exclusively in this CAO orchestration context. "
            "To load a skill's full content, use the `load_skill` MCP tool provided by the "
            "CAO MCP server. These skills are not accessible through provider-native skill "
            "commands or directories.\n\n"
            "- **python-testing**: Pytest conventions"
        )
        mock_provider = MagicMock()
        mock_provider_manager.create_provider.return_value = mock_provider
        mock_log_path = MagicMock()
        mock_log_dir.__truediv__.return_value = mock_log_path

        create_terminal(provider_name, "developer", new_session=True)

        assert mock_provider_manager.create_provider.call_args.kwargs["skill_prompt"] is None

    @patch("cli_agent_orchestrator.services.terminal_service.TERMINAL_LOG_DIR")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.db_create_terminal")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_window_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_session_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_terminal_id")
    @patch("cli_agent_orchestrator.services.terminal_service.build_skill_catalog")
    @patch("cli_agent_orchestrator.services.terminal_service.load_agent_profile")
    def test_build_skill_catalog_called_for_runtime_prompt_provider(
        self,
        mock_load_profile,
        mock_build_skill_catalog,
        mock_gen_id,
        mock_gen_session,
        mock_gen_window,
        mock_tmux,
        mock_db_create,
        mock_provider_manager,
        mock_log_dir,
    ):
        """build_skill_catalog() is called exactly once for runtime-prompt providers."""
        mock_gen_id.return_value = "test1234"
        mock_gen_session.return_value = "cao-session"
        mock_gen_window.return_value = "developer-abcd"
        mock_tmux.session_exists.return_value = False
        mock_load_profile.return_value = AgentProfile(
            name="developer", description="Developer", system_prompt="You are the developer."
        )
        mock_build_skill_catalog.return_value = "## Available Skills\n\n- skill-a"
        mock_provider_manager.create_provider.return_value = MagicMock()
        mock_log_dir.__truediv__.return_value = MagicMock()

        create_terminal("claude_code", "developer", new_session=True)

        mock_build_skill_catalog.assert_called_once_with()

    @pytest.mark.parametrize("provider_name", ["opencode_cli", "kiro_cli", "q_cli", "copilot_cli"])
    @patch("cli_agent_orchestrator.services.terminal_service.TERMINAL_LOG_DIR")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.db_create_terminal")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_window_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_session_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_terminal_id")
    @patch("cli_agent_orchestrator.services.terminal_service.build_skill_catalog")
    @patch("cli_agent_orchestrator.services.terminal_service.load_agent_profile")
    def test_build_skill_catalog_not_called_for_native_or_baked_provider(
        self,
        mock_load_profile,
        mock_build_skill_catalog,
        mock_gen_id,
        mock_gen_session,
        mock_gen_window,
        mock_tmux,
        mock_db_create,
        mock_provider_manager,
        mock_log_dir,
        provider_name,
    ):
        """build_skill_catalog() is never called for providers that deliver skills natively or
        at install time — OpenCode (symlink), Kiro (skill:// resources), Q, Copilot."""
        mock_gen_id.return_value = "test1234"
        mock_gen_session.return_value = "cao-session"
        mock_gen_window.return_value = "developer-abcd"
        mock_tmux.session_exists.return_value = False
        mock_load_profile.return_value = AgentProfile(
            name="developer", description="Developer", system_prompt="Base prompt"
        )
        mock_provider_manager.create_provider.return_value = MagicMock()
        mock_log_dir.__truediv__.return_value = MagicMock()

        create_terminal(provider_name, "developer", new_session=True)

        mock_build_skill_catalog.assert_not_called()

    @patch("cli_agent_orchestrator.services.terminal_service.TERMINAL_LOG_DIR")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.db_create_terminal")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_window_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_session_name")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_terminal_id")
    @patch("cli_agent_orchestrator.services.terminal_service.load_agent_profile")
    def test_create_terminal_profile_not_found(
        self,
        mock_load_profile,
        mock_gen_id,
        mock_gen_session,
        mock_gen_window,
        mock_tmux,
        mock_db_create,
        mock_provider_manager,
        mock_log_dir,
    ):
        """Terminal creation succeeds when agent profile is not in CAO store (e.g. JSON-only profiles)."""
        mock_gen_id.return_value = "test1234"
        mock_gen_session.return_value = "cao-session"
        mock_gen_window.return_value = "my-agent-abcd"
        mock_tmux.session_exists.return_value = False
        mock_load_profile.side_effect = FileNotFoundError("Agent profile not found: my-agent")
        mock_provider = MagicMock()
        mock_provider_manager.create_provider.return_value = mock_provider
        mock_log_path = MagicMock()
        mock_log_dir.__truediv__.return_value = mock_log_path

        result = create_terminal("kiro_cli", "my-agent", new_session=True)

        assert result.id == "test1234"
        mock_provider.initialize.assert_called_once()
        # allowed_tools should be None since profile was not found
        assert mock_provider_manager.create_provider.call_args.kwargs.get("allowed_tools") is None


class TestGetTerminal:
    """Tests for get_terminal function."""

    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_workflow_projection")
    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_metadata")
    def test_get_terminal_success(
        self, mock_get_metadata, mock_get_projection, mock_provider_manager, mock_tmux
    ):
        """Test getting terminal successfully."""
        mock_get_metadata.return_value = {
            "id": "test1234",
            "tmux_window": "developer-abcd",
            "provider": "kiro_cli",
            "tmux_session": "cao-session",
            "session_id": "stable-session-lifetime",
            "runtime_generation": "generation-before-restart",
            "agent_profile": "developer",
            "last_active": datetime.now(),
        }
        mock_provider = MagicMock()
        mock_provider.get_status.return_value = TerminalStatus.IDLE
        mock_provider.is_process_alive.return_value = True
        mock_provider_manager.get_provider.return_value = mock_provider
        mock_tmux.window_exists.return_value = True
        mock_get_projection.return_value = {
            "state": "waiting",
            "workflow_status": "open",
            "assignment_status": "handoff_awaiting_result",
            "result_status": "awaiting",
            "delivery_status": "handoff_awaiting_result",
        }

        result = get_terminal("test1234")

        assert result["id"] == "test1234"
        assert result["session_id"] == "stable-session-lifetime"
        assert result["status"] == TerminalStatus.IDLE.value
        assert result["lifecycle"] == "running"
        assert result["workflow_state"] == "waiting"
        assert result["assignment_status"] == "handoff_awaiting_result"

        mock_get_metadata.return_value["runtime_generation"] = "generation-after-restart"
        after_restart = get_terminal("test1234")
        assert after_restart["session_id"] == "stable-session-lifetime"

    def test_get_terminal_releases_only_observed_turn_and_wakes_queue(self):
        metadata = {
            "id": "test1234",
            "tmux_window": "developer-abcd",
            "provider": "codex",
            "tmux_session": "cao-session",
            "agent_profile": "supervisor",
            "last_active": datetime.now(),
        }
        projection = {
            "state": "active",
            "workflow_status": "open",
            "assignment_status": None,
            "result_status": None,
            "delivery_status": None,
        }
        provider = MagicMock()
        provider.get_status.return_value = TerminalStatus.IDLE
        with (
            patch(
                "cli_agent_orchestrator.services.terminal_service.get_terminal_metadata",
                return_value=metadata,
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.get_terminal_workflow_projection",
                return_value=projection,
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.get_provider_execution_turn",
                return_value=77,
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.reconcile_terminal_runtime",
                return_value=False,
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.provider_manager.get_provider",
                return_value=provider,
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.release_provider_execution",
                return_value=True,
            ) as release,
            patch(
                "cli_agent_orchestrator.services.terminal_service._wake_queued_provider_execution"
            ) as wake,
        ):
            assert get_terminal("test1234")["status"] == TerminalStatus.IDLE.value

        release.assert_called_once_with("test1234", 77)
        wake.assert_called_once_with()

    def test_get_terminal_reports_processing_for_active_turn_when_provider_is_ready(self):
        metadata = {
            "id": "11a094aa",
            "tmux_window": "conductor",
            "provider": "codex",
            "tmux_session": "cao-session",
            "agent_profile": "supervisor",
            "last_active": datetime.now(),
        }
        projection = {
            "state": "active",
            "workflow_status": "open",
            "workflow_reason": None,
            "assignment_status": None,
            "result_status": None,
            "delivery_status": None,
        }
        provider = MagicMock()
        provider.get_status.return_value = TerminalStatus.IDLE
        provider.is_process_alive.return_value = True
        with (
            patch(
                "cli_agent_orchestrator.services.terminal_service.get_terminal_metadata",
                return_value=metadata,
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.get_terminal_workflow_projection",
                return_value=projection,
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.get_terminal_execution_projection",
                return_value={"active_turn": True, "wait_reason": None},
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.get_provider_execution_turn",
                return_value=None,
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.reconcile_terminal_runtime",
                return_value=False,
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.provider_manager.get_provider",
                return_value=provider,
            ),
        ):
            terminal = get_terminal("11a094aa")

        assert terminal["status"] == TerminalStatus.IDLE.value
        assert terminal["execution_state"] == "processing"
        assert terminal["execution_wait_reason"] is None

    @patch(
        "cli_agent_orchestrator.services.terminal_service.mark_terminal_runtime_exited_with_workflow_ids",
        return_value=(True, []),
    )
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_workflow_projection")
    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_metadata")
    def test_get_terminal_reports_exited_process_even_if_tmux_metadata_persists(
        self, mock_get_metadata, mock_get_projection, mock_provider_manager, mock_mark_exited
    ):
        mock_get_metadata.return_value = {
            "id": "test1234",
            "tmux_window": "developer-abcd",
            "provider": "codex",
            "tmux_session": "cao-session",
            "agent_profile": "developer",
            "last_active": datetime.now(),
        }
        mock_provider = MagicMock()
        mock_provider.get_status.return_value = TerminalStatus.COMPLETED
        mock_provider.is_process_alive.return_value = False
        mock_provider_manager.get_provider.return_value = mock_provider
        mock_get_projection.return_value = {
            "state": "owner_gate",
            "workflow_status": "owner_gate",
            "assignment_status": None,
            "result_status": None,
            "delivery_status": None,
        }

        terminal = get_terminal("test1234")
        assert terminal["lifecycle"] == "exited"
        assert terminal["workflow_state"] == "owner_gate"
        mock_mark_exited.assert_called_once_with("test1234")

    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_metadata")
    def test_get_terminal_not_found(self, mock_get_metadata):
        """Test getting non-existent terminal."""
        mock_get_metadata.return_value = None

        with pytest.raises(ValueError, match="not found"):
            get_terminal("nonexistent")

    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_metadata")
    def test_get_terminal_no_provider(self, mock_get_metadata, mock_provider_manager):
        """Test getting terminal when provider not found."""
        mock_get_metadata.return_value = {
            "id": "test1234",
            "tmux_window": "developer-abcd",
            "provider": "kiro_cli",
            "tmux_session": "cao-session",
            "agent_profile": "developer",
            "last_active": datetime.now(),
        }
        mock_provider_manager.get_provider.return_value = None

        with pytest.raises(ValueError, match="Provider not found"):
            get_terminal("test1234")


class TestGetWorkingDirectory:
    """Tests for get_working_directory function."""

    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_metadata")
    def test_get_working_directory_success(self, mock_get_metadata, mock_tmux):
        """Test getting working directory successfully."""
        mock_get_metadata.return_value = {
            "tmux_session": "cao-session",
            "tmux_window": "developer-abcd",
        }
        mock_tmux.get_pane_working_directory.return_value = "/home/user/project"

        result = get_working_directory("test1234")

        assert result == "/home/user/project"

    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_metadata")
    def test_get_working_directory_not_found(self, mock_get_metadata):
        """Test getting working directory for non-existent terminal."""
        mock_get_metadata.return_value = None

        with pytest.raises(ValueError, match="not found"):
            get_working_directory("nonexistent")


class TestSendInput:
    """Tests for send_input function."""

    @patch("cli_agent_orchestrator.services.terminal_service.send_input")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    def test_sidecar_reconnect_uses_capacity_fenced_input(
        self, mock_provider_manager, mock_send_input
    ):
        provider = mock_provider_manager.get_provider.return_value
        provider.reconnect_runtime_sidecar = None
        provider.runtime_sidecar_reconnect_input = "/compact"

        request_provider_runtime_sidecar_reconnect("test1234", 42, "resume-exact")

        mock_send_input.assert_called_once_with(
            "test1234",
            "/compact",
            registry=None,
            sender_id="cao-workflow",
            orchestration_type=OrchestrationType.SEND_MESSAGE,
            logical_turn_id=42,
        )

    @patch(
        "cli_agent_orchestrator.services.terminal_service.record_workflow_provider_reconnect_output_boundary",
        return_value=True,
    )
    @patch(
        "cli_agent_orchestrator.services.terminal_service.get_workflow_provider_reconnect_runtime_ready"
    )
    @patch(
        "cli_agent_orchestrator.services.terminal_service.mark_workflow_provider_reconnect_launch_dispatched",
        return_value=True,
    )
    @patch("cli_agent_orchestrator.services.terminal_service.send_input")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    def test_sidecar_reconnect_prefers_exact_provider_resume(
        self,
        mock_provider_manager,
        mock_send_input,
        mock_mark_launch,
        mock_runtime_ready,
        mock_record_boundary,
    ):
        provider = mock_provider_manager.get_provider.return_value

        request_provider_runtime_sidecar_reconnect(
            "test1234",
            42,
            "resume-exact",
            claim_token="claim-token",
            attempt_token="a" * 32,
            attempt_state="reserved",
        )

        provider.reconnect_runtime_sidecar.assert_called_once_with(
            "resume-exact",
            attempt_token="a" * 32,
            attempt_state="reserved",
            mark_launch_dispatched=ANY,
            runtime_ready=ANY,
            record_output_boundary=ANY,
            side_effect_guard=ANY,
        )
        kwargs = provider.reconnect_runtime_sidecar.call_args.kwargs
        assert kwargs["mark_launch_dispatched"]() is True
        mock_mark_launch.assert_called_once_with("test1234", 42, "claim-token", "a" * 32)
        kwargs["runtime_ready"]()
        mock_runtime_ready.assert_called_once_with("test1234", "a" * 32)
        assert kwargs["record_output_boundary"](11, 22, 33) is True
        mock_record_boundary.assert_called_once_with("test1234", "a" * 32, 11, 22, 33)
        mock_send_input.assert_not_called()

    @patch("cli_agent_orchestrator.services.terminal_service.update_last_active")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_metadata")
    def test_send_input_success(self, mock_get_metadata, mock_tmux, mock_pm, mock_update):
        """Test sending input successfully."""
        mock_get_metadata.return_value = {
            "tmux_session": "cao-session",
            "tmux_window": "developer-abcd",
        }
        mock_provider = mock_pm.get_provider.return_value
        mock_provider.paste_enter_count = 2

        with (
            patch(
                "cli_agent_orchestrator.services.terminal_service.acquire_terminal_runtime_transport",
                return_value="transport-token",
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.release_terminal_runtime_operation",
                return_value=True,
            ),
        ):
            result = send_input("test1234", "test message")

        assert result is True
        mock_tmux.send_keys.assert_called_once_with(
            "cao-session", "developer-abcd", "test message", enter_count=2
        )
        mock_update.assert_called_once_with("test1234")

    @patch("cli_agent_orchestrator.services.terminal_service.release_provider_execution")
    @patch("cli_agent_orchestrator.services.operations_service.acquire_provider_execution_slot")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_metadata")
    def test_send_input_releases_execution_once_when_transport_fails(
        self, mock_get_metadata, mock_tmux, mock_pm, mock_acquire, mock_release
    ):
        mock_get_metadata.return_value = {
            "tmux_session": "cao-session",
            "tmux_window": "supervisor-window",
            "runtime_lifecycle": "running",
        }
        mock_pm.get_provider.return_value.paste_enter_count = 1
        mock_tmux.send_keys.side_effect = RuntimeError("transport failed")
        mock_release.return_value = True

        with (
            patch(
                "cli_agent_orchestrator.services.terminal_service.acquire_terminal_runtime_transport",
                return_value="transport-token",
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.release_terminal_runtime_operation",
                return_value=True,
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service._wake_queued_provider_execution"
            ) as wake,
            pytest.raises(RuntimeError, match="transport failed"),
        ):
            send_input("test1234", "task", logical_turn_id=42)

        mock_acquire.assert_called_once_with("test1234", 42)
        mock_release.assert_called_once_with("test1234", 42)
        wake.assert_called_once_with(None)

    @patch("cli_agent_orchestrator.services.terminal_service.update_last_active")
    @patch("cli_agent_orchestrator.services.terminal_service.mark_handoff_child_input_received")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_metadata")
    def test_send_input_marks_direct_handoff_submission(
        self, mock_get_metadata, mock_tmux, mock_pm, mock_marker, mock_update
    ):
        """Direct handoff delivery retains the restart hydration boundary."""
        from cli_agent_orchestrator.models.inbox import OrchestrationType

        mock_get_metadata.return_value = {
            "tmux_session": "cao-session",
            "tmux_window": "developer-window",
        }
        mock_pm.get_provider.return_value.paste_enter_count = 1

        with (
            patch(
                "cli_agent_orchestrator.services.terminal_service.acquire_terminal_runtime_transport",
                return_value="transport-token",
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.release_terminal_runtime_operation",
                return_value=True,
            ),
        ):
            assert send_input(
                "handoff-child",
                "complete the task",
                orchestration_type=OrchestrationType.HANDOFF,
            )

        mock_marker.assert_called_once_with("handoff-child")
        mock_update.assert_called_once_with("handoff-child")

    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_metadata")
    def test_send_input_not_found(self, mock_get_metadata):
        """Test sending input to non-existent terminal."""
        mock_get_metadata.return_value = None

        with pytest.raises(ValueError, match="not found"):
            send_input("nonexistent", "message")


class TestGetOutput:
    """Tests for get_output function."""

    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_metadata")
    def test_get_output_full(self, mock_get_metadata, mock_tmux):
        """Test getting full output."""
        mock_get_metadata.return_value = {
            "tmux_session": "cao-session",
            "tmux_window": "developer-abcd",
        }
        mock_tmux.get_history.return_value = "full terminal output"

        result = get_output("test1234", OutputMode.FULL)

        assert result == "full terminal output"

    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_metadata")
    def test_get_output_full_sanitizes_ansi_vt_and_preserves_unicode(
        self, mock_get_metadata, mock_tmux
    ):
        mock_get_metadata.return_value = {
            "tmux_session": "cao-session",
            "tmux_window": "developer-abcd",
        }
        mock_tmux.get_history.return_value = (
            "\x1b[?2026h\x1b[31mПривет\x1b[0m, мир\x1b[?2026l\n"
            "\x1b[?25h\x1b[0 q\x1bMТекст\tданные\x1b[?2004h\x1b[?2004l"
        )

        result = get_output("test1234", OutputMode.FULL)

        assert result == "Привет, мир\nТекст\tданные"
        assert "\x1b" not in result

    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_metadata")
    def test_get_output_full_removes_control_strings_and_c1_forms(
        self, mock_get_metadata, mock_tmux
    ):
        mock_get_metadata.return_value = {
            "tmux_session": "cao-session",
            "tmux_window": "developer-abcd",
        }
        mock_tmux.get_history.return_value = (
            "до\x1b]0;private title\x07после\n"
            "link \x1b]8;;https://example.invalid\x1b\\ссылка\x1b]8;;\x1b\\\n"
            "\x1bPprivate-dcs\x1b\\готово\n"
            "A\x9b31mB\x9b0m C\x9dprivate-osc\x9cD\x90private-dcs\x9cE"
        )

        result = get_output("test1234", OutputMode.FULL)

        assert result == "допосле\nlink ссылка\nготово\nAB CDE"
        assert "private" not in result
        assert all(not (0x80 <= ord(character) <= 0x9F) for character in result)

    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_metadata")
    def test_get_output_full_handles_partial_controls_and_normalizes_lines(
        self, mock_get_metadata, mock_tmux
    ):
        mock_get_metadata.return_value = {
            "tmux_session": "cao-session",
            "tmux_window": "developer-abcd",
        }
        mock_tmux.get_history.return_value = (
            "Русский\r\nтекст\t✓\x00\x07\nprogressXX\b\bOK\rnext\nplain\x1b[?2026"
        )

        result = get_output("test1234", OutputMode.FULL)

        assert result == "Русский\nтекст\t✓\nprogressOK\nnext\nplain"
        assert "\x1b" not in result

    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_metadata")
    def test_get_output_last_keeps_raw_controls_for_provider_extraction(
        self, mock_get_metadata, mock_tmux, mock_provider_manager
    ):
        raw = "\x1b[36m• result\x1b[0m"
        mock_get_metadata.return_value = {
            "tmux_session": "cao-session",
            "tmux_window": "developer-abcd",
        }
        mock_tmux.get_history.return_value = raw
        mock_provider = MagicMock(extraction_retries=0)
        mock_provider.extract_last_message_from_script.return_value = "result"
        mock_provider_manager.get_provider.return_value = mock_provider

        assert get_output("test1234", OutputMode.LAST) == "result"
        mock_provider.extract_last_message_from_script.assert_called_once_with(raw)

    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_metadata")
    def test_get_output_last(self, mock_get_metadata, mock_tmux, mock_provider_manager):
        """Test getting last message."""
        mock_get_metadata.return_value = {
            "tmux_session": "cao-session",
            "tmux_window": "developer-abcd",
        }
        mock_tmux.get_history.return_value = "full terminal output"
        mock_provider = MagicMock()
        mock_provider.extract_last_message_from_script.return_value = "last message"
        mock_provider_manager.get_provider.return_value = mock_provider

        result = get_output("test1234", OutputMode.LAST)

        assert result == "last message"

    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_metadata")
    def test_get_output_not_found(self, mock_get_metadata):
        """Test getting output from non-existent terminal."""
        mock_get_metadata.return_value = None

        with pytest.raises(ValueError, match="not found"):
            get_output("nonexistent")

    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_metadata")
    def test_exited_terminal_reports_cleaned_durable_output_truthfully(
        self, mock_get_metadata, mock_tmux, tmp_path, monkeypatch
    ):
        mock_get_metadata.return_value = {
            "tmux_session": "cao-session",
            "tmux_window": "developer-abcd",
            "runtime_lifecycle": "exited",
        }
        mock_tmux.get_history.side_effect = RuntimeError("runtime absent")
        monkeypatch.setattr(
            "cli_agent_orchestrator.services.terminal_service.TERMINAL_LOG_DIR",
            tmp_path,
        )

        with pytest.raises(TerminalOutputUnavailable):
            get_output("test1234", OutputMode.FULL)

    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_metadata")
    def test_get_output_last_no_provider(self, mock_get_metadata, mock_tmux, mock_provider_manager):
        """Test getting last message when provider not found."""
        mock_get_metadata.return_value = {
            "tmux_session": "cao-session",
            "tmux_window": "developer-abcd",
        }
        mock_tmux.get_history.return_value = "full output"
        mock_provider_manager.get_provider.return_value = None

        with pytest.raises(ValueError, match="Provider not found"):
            get_output("test1234", OutputMode.LAST)


class TestCodexProviderLifecycleThroughTerminalService:
    """Exercise one real provider through the normal manager/service seam."""

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.update_last_active")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_metadata")
    def test_codex_status_and_final_extraction_share_provider_state(
        self,
        mock_get_metadata,
        mock_provider_manager,
        mock_service_tmux,
        mock_update_last_active,
        mock_codex_tmux,
    ):
        metadata = {
            "id": "codex-lifecycle",
            "tmux_window": "developer-abcd",
            "provider": "codex",
            "tmux_session": "cao-session",
            "agent_profile": "developer",
            "last_active": datetime.now(),
        }
        mock_get_metadata.return_value = metadata
        provider = CodexProvider("codex-lifecycle", "cao-session", "developer-abcd")
        mock_provider_manager.get_provider.return_value = provider

        provider._completion_candidate = "stale prior task"
        provider._completion_candidate_polls = 2
        with (
            patch(
                "cli_agent_orchestrator.services.terminal_service.acquire_terminal_runtime_transport",
                return_value="transport-token",
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.release_terminal_runtime_operation",
                return_value=True,
            ),
        ):
            send_input("codex-lifecycle", "Produce the final report.")
        assert provider._completion_candidate is None
        assert provider._completion_candidate_polls == 0

        footer = "\n› Continue working\n  gpt-5.3-codex high · 96% left · ~/project\n"
        progress = (
            "› [CAO Handoff] Produce the final report.\n"
            "• Ran rg -n handoff src\n"
            "  └ partial tool output\n"
            f"{footer}"
        )
        complete_capture = (
            "› [CAO Handoff] Produce the final report.\n"
            "• Ran rg -n handoff src\n"
            "  └ completed tool output\n"
            "• Summary\n"
            "Completed the audit.\n"
            "• Verification\n"
            "144 tests passed.\n"
            "SAMPLE_WORKER_READONLY_OK\n"
            f"{footer}"
        )
        mock_codex_tmux.get_history.side_effect = [
            progress,
            complete_capture,
            complete_capture,
            complete_capture,
        ]
        mock_service_tmux.get_history.return_value = complete_capture

        assert get_terminal("codex-lifecycle")["status"] == TerminalStatus.PROCESSING.value
        assert get_terminal("codex-lifecycle")["status"] == TerminalStatus.PROCESSING.value
        assert get_terminal("codex-lifecycle")["status"] == TerminalStatus.PROCESSING.value
        assert get_terminal("codex-lifecycle")["status"] == TerminalStatus.COMPLETED.value
        assert get_output("codex-lifecycle", OutputMode.LAST) == (
            "• Summary\nCompleted the audit.\n• Verification\n"
            "144 tests passed.\nSAMPLE_WORKER_READONLY_OK"
        )


class TestDeleteTerminal:
    """Tests for delete_terminal function."""

    @patch(
        "cli_agent_orchestrator.services.terminal_service.terminal_deletion_receipt_exists",
        return_value=False,
    )
    @patch(
        "cli_agent_orchestrator.services.terminal_service.get_terminal_metadata",
        return_value=None,
    )
    def test_delete_terminal_unknown_metadata_is_not_invented(self, _metadata, _receipt):
        with pytest.raises(ValueError, match="not found"):
            delete_terminal("test1234")

    @patch(
        "cli_agent_orchestrator.services.terminal_service.terminal_deletion_receipt_exists",
        return_value=True,
    )
    @patch(
        "cli_agent_orchestrator.services.terminal_service.get_terminal_metadata",
        return_value=None,
    )
    def test_delete_terminal_retry_uses_durable_receipt(self, _metadata, _receipt):
        assert delete_terminal("test1234") is True

    @patch("cli_agent_orchestrator.services.terminal_service.prepare_terminal_for_destruction")
    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_metadata")
    def test_delete_terminal_aborts_before_retirement_when_snapshot_fails(
        self, mock_metadata, mock_prepare
    ):
        mock_metadata.return_value = {
            "id": "child-with-partial",
            "tmux_session": "cao-session",
            "tmux_window": "child",
            "runtime_lifecycle": "exited",
        }
        mock_prepare.side_effect = RuntimeError("snapshot persistence failed")

        with (
            patch(
                "cli_agent_orchestrator.services.terminal_service.retire_exited_terminal_runtime"
            ) as retire,
            pytest.raises(RuntimeError, match="snapshot persistence failed"),
        ):
            delete_terminal("child-with-partial")
        retire.assert_not_called()
