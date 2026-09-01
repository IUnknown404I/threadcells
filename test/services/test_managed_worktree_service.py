import subprocess
from contextlib import nullcontext
from pathlib import Path

import pytest

from cli_agent_orchestrator.services import managed_worktree_service


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "source"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "CAO Test")
    _git(repository, "config", "user.email", "cao-test@example.invalid")
    (repository / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-qm", "baseline")
    return repository


def _metadata(worktree):
    return {
        "id": Path(worktree.path).name.removeprefix(f"{worktree.kind}-"),
        "launch_worktree": worktree.path,
        "managed_worktree_kind": worktree.kind,
        "managed_worktree_source": worktree.source,
        "managed_worktree_branch": worktree.branch,
        "managed_worktree_commit": worktree.commit,
    }


def test_task_worktree_shares_objects_reuses_one_path_and_preserves_branch(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")

    task = managed_worktree_service.create_managed_worktree(str(repository), "terminal01", "task")
    assert task is not None
    assert task.branch == "cao/task/terminal01"
    assert Path(task.path).is_dir()
    assert (Path(task.path) / ".git").is_file()
    common = _git(Path(task.path), "rev-parse", "--git-common-dir")
    assert Path(common).resolve() == (repository / ".git").resolve()
    assert managed_worktree_service.managed_worktree_status(_metadata(task))["path"] == task.path
    assert not (Path(task.path) / "node_modules").exists()

    removed = managed_worktree_service.remove_managed_worktree(_metadata(task))
    assert removed["removed"] is True
    assert not Path(task.path).exists()
    assert _git(repository, "show-ref", "--verify", "refs/heads/cao/task/terminal01")


def test_reviewer_is_detached_at_exact_commit_and_dirty_cleanup_is_fail_closed(
    tmp_path, monkeypatch
):
    repository = _repository(tmp_path)
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")

    reviewer = managed_worktree_service.create_managed_worktree(
        str(repository), "terminal02", "reviewer"
    )
    assert reviewer is not None
    assert reviewer.branch is None
    assert _git(Path(reviewer.path), "rev-parse", "HEAD") == reviewer.commit
    assert (
        subprocess.run(
            ["git", "-C", reviewer.path, "symbolic-ref", "--quiet", "HEAD"], check=False
        ).returncode
        != 0
    )

    untracked = Path(reviewer.path) / "review-notes.txt"
    untracked.write_text("must not be discarded\n", encoding="utf-8")
    retained = managed_worktree_service.remove_managed_worktree(_metadata(reviewer))
    assert retained["removed"] is False
    assert retained["reason_code"] == "MANAGED_WORKTREE_DIRTY"
    assert Path(reviewer.path).exists()

    untracked.unlink()
    removed = managed_worktree_service.remove_managed_worktree(_metadata(reviewer))
    assert removed["removed"] is True
    assert not Path(reviewer.path).exists()
    repeated = managed_worktree_service.remove_managed_worktree(_metadata(reviewer))
    assert repeated["removed"] is True
    assert repeated["already_removed"] is True
    assert repeated["commit"] == reviewer.commit


def test_supervisor_worktrees_are_isolated_unique_branches_and_idempotent(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")

    first = managed_worktree_service.create_managed_worktree(
        str(repository), "context-a", "supervisor", allow_existing=True
    )
    second = managed_worktree_service.create_managed_worktree(
        str(repository), "context-b", "supervisor", allow_existing=True
    )
    repeated = managed_worktree_service.create_managed_worktree(
        str(repository),
        "context-a",
        "supervisor",
        expected_commit=first.commit,
        allow_existing=True,
    )

    assert first.path != second.path
    assert first.branch == "cao/session/context-a"
    assert second.branch == "cao/session/context-b"
    assert repeated == first
    assert Path(first.path) != repository
    assert Path(second.path) != repository
    (Path(first.path) / "from-a.txt").write_text("a\n", encoding="utf-8")
    assert not (Path(second.path) / "from-a.txt").exists()
    assert not (repository / "from-a.txt").exists()


def test_supervisor_existing_identity_fails_closed_when_branch_moves(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")
    managed = managed_worktree_service.create_managed_worktree(
        str(repository), "context-a", "supervisor"
    )
    _git(Path(managed.path), "switch", "-c", "foreign")

    with pytest.raises(
        managed_worktree_service.ManagedWorktreeError,
        match="existing revision or branch changed",
    ):
        managed_worktree_service.create_managed_worktree(
            str(repository),
            "context-a",
            "supervisor",
            expected_commit=managed.commit,
            allow_existing=True,
        )


def test_startup_reconciliation_finishes_reserved_worktree_without_provider_dispatch(
    tmp_path, monkeypatch
):
    repository = _repository(tmp_path)
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")
    plan = managed_worktree_service.plan_managed_worktree(
        str(repository), "context-a", "supervisor"
    )
    transitions = []
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.list_writable_work_contexts",
        lambda *, states=None: (
            [
                {
                    "id": "context-a",
                    "terminal_id": "context-a",
                    "canonical_source": plan.source,
                    "canonical_worktree": plan.path,
                    "branch": plan.branch,
                    "base_revision": plan.commit,
                }
            ]
            if states == ("reserved",)
            else []
        ),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.transition_writable_work_context",
        lambda context_id, **kwargs: transitions.append((context_id, kwargs)) or True,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.context_lifecycle_fence",
        lambda **_kwargs: nullcontext(True),
    )

    assert managed_worktree_service.reconcile_writable_work_context_provisioning() == 1
    assert Path(plan.path).is_dir()
    assert transitions == [
        (
            "context-a",
            {
                "expected_states": ("reserved",),
                "state": "provisioned",
                "event_type": "provisioning_recovered_after_restart",
            },
        )
    ]


def test_restart_cleans_only_clean_unclaimed_provisioned_worktree(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")
    managed = managed_worktree_service.create_managed_worktree(
        str(repository), "context-a", "supervisor"
    )
    row = {
        "id": "context-a",
        "terminal_id": "context-a",
        "canonical_source": managed.source,
        "canonical_worktree": managed.path,
        "branch": managed.branch,
        "base_revision": managed.commit,
    }
    transitions = []
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.list_writable_work_contexts",
        lambda *, states=None: [row] if states == ("provisioned",) else [],
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.get_terminal_metadata", lambda _id: None
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.transition_writable_work_context",
        lambda context_id, **kwargs: transitions.append((context_id, kwargs)) or True,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.context_lifecycle_fence",
        lambda **_kwargs: nullcontext(True),
    )

    assert managed_worktree_service.reconcile_writable_work_context_provisioning() == 1
    assert not Path(managed.path).exists()
    assert transitions[-1][1] == {
        "expected_states": ("provisioned",),
        "state": "abandoned",
        "event_type": "provisioning_abandoned",
        "reason_code": "PROVISIONING_INTERRUPTED_BEFORE_ADMISSION",
    }


@pytest.mark.parametrize(
    ("lifecycle", "expected_state"),
    [("starting", "preserved"), ("running", "admitted")],
)
def test_restart_never_redispatches_writer_claimed_provider_launch(
    monkeypatch, lifecycle, expected_state
):
    row = {"id": "context-a", "terminal_id": "terminal-a"}
    transitions = []
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.list_writable_work_contexts",
        lambda *, states=None: [row] if states == ("launching",) else [],
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.get_terminal_metadata",
        lambda _id: {"runtime_lifecycle": lifecycle, "recovery_takeover_id": None},
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.transition_writable_work_context",
        lambda context_id, **kwargs: transitions.append((context_id, kwargs)) or True,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.context_lifecycle_fence",
        lambda **_kwargs: nullcontext(True),
    )

    assert managed_worktree_service.reconcile_writable_work_context_provisioning() == 1
    assert transitions == [
        (
            "context-a",
            {
                "expected_states": ("launching",),
                "state": expected_state,
                "event_type": (
                    "supervisor_admitted"
                    if expected_state == "admitted"
                    else "provisioning_preserved"
                ),
                "reason_code": (
                    None if expected_state == "admitted" else "PROVIDER_LAUNCH_OUTCOME_UNCERTAIN"
                ),
            },
        )
    ]


def test_non_git_directory_is_not_duplicated(tmp_path, monkeypatch):
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")
    plain = tmp_path / "plain"
    plain.mkdir()
    assert (
        managed_worktree_service.create_managed_worktree(str(plain), "terminal03", "task") is None
    )
    assert not (tmp_path / "managed").exists()


def test_cleanup_never_targets_source_or_paths_outside_managed_root(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")
    forged = {
        "id": "terminal04",
        "launch_worktree": str(repository),
        "managed_worktree_kind": "task",
        "managed_worktree_source": str(repository),
        "managed_worktree_branch": "cao/task/terminal04",
        "managed_worktree_commit": _git(repository, "rev-parse", "HEAD"),
    }
    result = managed_worktree_service.remove_managed_worktree(forged)
    assert result["removed"] is False
    assert result["reason_code"] == "MANAGED_WORKTREE_IDENTITY_MISMATCH"
    assert repository.exists()
