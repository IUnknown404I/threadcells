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


def test_hard_purge_removes_worktree_registration_and_private_branch(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")
    task = managed_worktree_service.create_managed_worktree(
        str(repository), "terminal-hard-delete", "task"
    )
    assert task is not None

    result = managed_worktree_service.purge_managed_worktree(_metadata(task))

    assert result == {
        "removed": True,
        "managed": True,
        "already_removed": False,
        "path_absent": True,
        "git_unregistered": True,
        "branch_absent": True,
    }
    assert not Path(task.path).exists()
    inventory = _git(repository, "worktree", "list", "--porcelain")
    assert task.path not in inventory
    assert (
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "show-ref",
                "--verify",
                f"refs/heads/{task.branch}",
            ],
            check=False,
            capture_output=True,
            text=True,
        ).returncode
        != 0
    )


def test_session_purge_accepts_clean_supervisor_mission_branch_and_preserves_it(
    tmp_path, monkeypatch
):
    repository = _repository(tmp_path)
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")
    supervisor = managed_worktree_service.create_managed_worktree(
        str(repository), "session-drift", "supervisor"
    )
    assert supervisor is not None
    metadata = {
        **_metadata(supervisor),
        "session_id": "session",
        "writer_authority_generation": "writer-generation",
        "writable_work_context_id": "session-drift",
    }
    worktree = Path(supervisor.path)
    _git(worktree, "switch", "-c", "fix/mission-branch")
    (worktree / "tracked.txt").write_text("advanced\n", encoding="utf-8")
    _git(worktree, "commit", "-qam", "mission change")
    mission_head = _git(worktree, "rev-parse", "HEAD")

    captured = managed_worktree_service.capture_session_worktree_retirement_authority([metadata])
    row = captured["authority"]["worktrees"][0]
    assert captured["safe"] is True
    assert row["branch"] == "fix/mission-branch"
    assert row["head"] == mission_head
    assert row["owned_ref"] == "refs/heads/cao/session/session-drift"

    retired = managed_worktree_service.purge_session_managed_worktrees(
        [metadata], captured["authority"]
    )

    assert retired["removed"] is True
    assert not worktree.exists()
    assert _git(repository, "rev-parse", "refs/heads/fix/mission-branch") == mission_head
    assert (
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "show-ref",
                "--verify",
                "refs/heads/cao/session/session-drift",
            ],
            check=False,
        ).returncode
        != 0
    )
    assert supervisor.path not in _git(repository, "worktree", "list", "--porcelain")


def test_session_purge_accepts_clean_reviewer_at_later_detached_revision(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    launch_revision = _git(repository, "rev-parse", "HEAD")
    (repository / "tracked.txt").write_text("review target\n", encoding="utf-8")
    _git(repository, "commit", "-qam", "review target")
    later_revision = _git(repository, "rev-parse", "HEAD")
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")
    reviewer = managed_worktree_service.create_managed_worktree(
        str(repository), "reviewer-drift", "reviewer", expected_commit=launch_revision
    )
    assert reviewer is not None
    metadata = {**_metadata(reviewer), "session_id": "session"}
    _git(Path(reviewer.path), "switch", "--detach", later_revision)

    captured = managed_worktree_service.capture_session_worktree_retirement_authority([metadata])
    row = captured["authority"]["worktrees"][0]
    assert captured["safe"] is True
    assert row["detached"] is True
    assert row["head"] == later_revision
    assert row["head"] != reviewer.commit

    retired = managed_worktree_service.purge_session_managed_worktrees(
        [metadata], captured["authority"]
    )

    assert retired["removed"] is True
    assert not Path(reviewer.path).exists()


@pytest.mark.parametrize("mutation", ["branch", "head", "dirty", "owned-ref"])
def test_session_purge_fails_closed_when_current_authority_changes_after_capture(
    tmp_path, monkeypatch, mutation
):
    repository = _repository(tmp_path)
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")
    supervisor = managed_worktree_service.create_managed_worktree(
        str(repository), "session-race", "supervisor"
    )
    assert supervisor is not None
    metadata = {
        **_metadata(supervisor),
        "session_id": "session",
        "writer_authority_generation": "writer-a",
        "writable_work_context_id": "session-race",
    }
    worktree = Path(supervisor.path)
    captured = managed_worktree_service.capture_session_worktree_retirement_authority([metadata])
    if mutation == "branch":
        _git(worktree, "switch", "-c", "fix/changed-after-preflight")
    elif mutation == "head":
        (worktree / "tracked.txt").write_text("later\n", encoding="utf-8")
        _git(worktree, "commit", "-qam", "later")
    elif mutation == "dirty":
        (worktree / "untracked.txt").write_text("later\n", encoding="utf-8")
    else:
        moved = _git(repository, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "moved")
        _git(repository, "update-ref", "refs/heads/cao/session/session-race", moved)

    retired = managed_worktree_service.purge_session_managed_worktrees(
        [metadata], captured["authority"]
    )

    assert retired["removed"] is False
    assert retired["reason_code"] == "WRITABLE_WORKTREE_AUTHORITY_CHANGED"
    assert worktree.exists()


def test_session_purge_fails_closed_when_writer_generation_changes(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")
    task = managed_worktree_service.create_managed_worktree(str(repository), "writer-race", "task")
    assert task is not None
    metadata = {**_metadata(task), "session_id": "session", "writer_authority_generation": "a"}
    captured = managed_worktree_service.capture_session_worktree_retirement_authority([metadata])
    changed = {**metadata, "writer_authority_generation": "b"}

    retired = managed_worktree_service.purge_session_managed_worktrees(
        [changed], captured["authority"]
    )

    assert retired["removed"] is False
    assert retired["reason_code"] == "WRITABLE_WORKTREE_AUTHORITY_CHANGED"
    assert Path(task.path).exists()


def test_session_purge_retry_accepts_monotonic_path_and_owned_ref_absence(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")
    task = managed_worktree_service.create_managed_worktree(
        str(repository), "partial-cleanup", "task"
    )
    assert task is not None
    metadata = {**_metadata(task), "session_id": "session"}
    captured = managed_worktree_service.capture_session_worktree_retirement_authority([metadata])
    _git(repository, "worktree", "remove", task.path)
    _git(repository, "update-ref", "-d", "refs/heads/cao/task/partial-cleanup")

    first = managed_worktree_service.purge_session_managed_worktrees(
        [metadata], captured["authority"], require_already_absent=True
    )
    second = managed_worktree_service.purge_session_managed_worktrees(
        [metadata], captured["authority"], require_already_absent=True
    )

    assert first["removed"] is True
    assert second["removed"] is True


def test_session_purge_rejects_replaced_worktree_path(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")
    reviewer = managed_worktree_service.create_managed_worktree(
        str(repository), "replaced-path", "reviewer"
    )
    assert reviewer is not None
    metadata = {**_metadata(reviewer), "session_id": "session"}
    captured = managed_worktree_service.capture_session_worktree_retirement_authority([metadata])
    _git(repository, "worktree", "remove", reviewer.path)
    Path(reviewer.path).symlink_to(repository, target_is_directory=True)

    retired = managed_worktree_service.purge_session_managed_worktrees(
        [metadata], captured["authority"]
    )

    assert retired["removed"] is False
    assert retired["reason_code"] == "MANAGED_WORKTREE_IDENTITY_MISMATCH"


def test_session_capture_rejects_moved_worktree_registration(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")
    task = managed_worktree_service.create_managed_worktree(
        str(repository), "moved-registration", "task"
    )
    assert task is not None
    metadata = {**_metadata(task), "session_id": "session"}
    moved_path = tmp_path / "moved-elsewhere"
    _git(repository, "worktree", "move", task.path, str(moved_path))

    captured = managed_worktree_service.capture_session_worktree_retirement_authority([metadata])

    assert captured == {
        "safe": False,
        "reason_code": "MANAGED_WORKTREE_AUTHORITY_CHANGED",
    }
    assert moved_path.exists()


def test_session_capture_rejects_ambiguous_private_branch(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")
    task = managed_worktree_service.create_managed_worktree(str(repository), "owned-task", "task")
    assert task is not None
    metadata = {**_metadata(task), "session_id": "session"}
    _git(Path(task.path), "switch", "-c", "cao/task/another-session")

    captured = managed_worktree_service.capture_session_worktree_retirement_authority([metadata])

    assert captured == {
        "safe": False,
        "reason_code": "WRITABLE_WORKTREE_AUTHORITY_CHANGED",
    }
    assert Path(task.path).exists()


def test_session_purge_rechecks_each_row_immediately_without_another_inventory_scan(
    tmp_path, monkeypatch
):
    repository = _repository(tmp_path)
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")
    first = managed_worktree_service.create_managed_worktree(str(repository), "a-first", "task")
    second = managed_worktree_service.create_managed_worktree(str(repository), "b-second", "task")
    assert first is not None and second is not None
    metadata = [
        {**_metadata(first), "session_id": "session"},
        {**_metadata(second), "session_id": "session"},
    ]
    captured = managed_worktree_service.capture_session_worktree_retirement_authority(metadata)
    original_git = managed_worktree_service._git
    original_inventory = managed_worktree_service._bounded_git_output
    removal_seen = False
    inventory_scans = 0

    def mutate_after_first_removal(*args, **kwargs):
        nonlocal removal_seen
        result = original_git(*args, **kwargs)
        if args[:2] == ("worktree", "remove") and not removal_seen:
            removal_seen = True
            _git(Path(second.path), "switch", "-c", "fix/changed-during-cleanup")
        return result

    def count_inventory(*args, **kwargs):
        nonlocal inventory_scans
        inventory_scans += 1
        return original_inventory(*args, **kwargs)

    monkeypatch.setattr(managed_worktree_service, "_git", mutate_after_first_removal)
    monkeypatch.setattr(managed_worktree_service, "_bounded_git_output", count_inventory)
    retired = managed_worktree_service.purge_session_managed_worktrees(
        metadata, captured["authority"]
    )

    assert retired["removed"] is False
    assert retired["terminal_id"] == "b-second"
    assert retired["reason_code"] == "MANAGED_WORKTREE_AUTHORITY_CHANGED"
    assert not Path(first.path).exists()
    assert Path(second.path).exists()
    assert inventory_scans == 1


@pytest.mark.parametrize(
    ("limit_name", "limit_value"),
    [
        ("_MAX_WORKTREE_INVENTORY_BYTES", 1),
        ("_MAX_WORKTREE_INVENTORY_ROWS", 0),
    ],
)
def test_session_capture_fails_closed_when_repository_inventory_exceeds_bound(
    tmp_path, monkeypatch, limit_name, limit_value
):
    repository = _repository(tmp_path)
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")
    task = managed_worktree_service.create_managed_worktree(
        str(repository), "bounded-inventory", "task"
    )
    assert task is not None
    monkeypatch.setattr(managed_worktree_service, limit_name, limit_value)

    captured = managed_worktree_service.capture_session_worktree_retirement_authority(
        [{**_metadata(task), "session_id": "session"}]
    )

    assert captured == {
        "safe": False,
        "reason_code": "MANAGED_WORKTREE_INVENTORY_LIMIT",
    }
    assert Path(task.path).exists()


def test_session_purge_rejects_registration_move_after_cached_inventory(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")
    task = managed_worktree_service.create_managed_worktree(
        str(repository), "registration-race", "task"
    )
    assert task is not None
    metadata = {**_metadata(task), "session_id": "session"}
    captured = managed_worktree_service.capture_session_worktree_retirement_authority([metadata])
    moved_path = tmp_path / "moved-after-inventory"
    original_git = managed_worktree_service._git
    moved = False

    def move_during_live_revalidation(*args, **kwargs):
        nonlocal moved
        if (
            not moved
            and args[:2] == ("rev-parse", "--show-toplevel")
            and Path(kwargs["cwd"]) == Path(task.path)
        ):
            moved = True
            _git(repository, "worktree", "move", task.path, str(moved_path))
        return original_git(*args, **kwargs)

    monkeypatch.setattr(managed_worktree_service, "_git", move_during_live_revalidation)
    retired = managed_worktree_service.purge_session_managed_worktrees(
        [metadata], captured["authority"]
    )

    assert retired["removed"] is False
    assert retired["reason_code"] in {
        "MANAGED_WORKTREE_IDENTITY_MISMATCH",
        "MANAGED_WORKTREE_AUTHORITY_CHANGED",
    }
    assert moved_path.exists()


def test_session_purge_revalidates_repository_identity_before_private_ref_cas(
    tmp_path, monkeypatch
):
    repository = _repository(tmp_path)
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")
    task = managed_worktree_service.create_managed_worktree(
        str(repository), "repository-race", "task"
    )
    assert task is not None and task.branch is not None
    metadata = {**_metadata(task), "session_id": "session"}
    captured = managed_worktree_service.capture_session_worktree_retirement_authority([metadata])
    expected_object = captured["authority"]["worktrees"][0]["owned_ref_object_id"]
    original_git = managed_worktree_service._git
    original_repository = tmp_path / "original-source"
    replaced = False

    def replace_after_worktree_removal(*args, **kwargs):
        nonlocal replaced
        result = original_git(*args, **kwargs)
        if not replaced and args[:2] == ("worktree", "remove"):
            replaced = True
            repository.rename(original_repository)
            subprocess.run(
                ["git", "clone", "-q", "--no-hardlinks", str(original_repository), str(repository)],
                check=True,
            )
            _git(repository, "update-ref", f"refs/heads/{task.branch}", expected_object)
        return result

    monkeypatch.setattr(managed_worktree_service, "_git", replace_after_worktree_removal)
    retired = managed_worktree_service.purge_session_managed_worktrees(
        [metadata], captured["authority"]
    )

    assert retired["removed"] is False
    assert retired["reason_code"] == "MANAGED_WORKTREE_IDENTITY_MISMATCH"
    assert _git(repository, "rev-parse", f"refs/heads/{task.branch}") == expected_object


def test_authoritatively_retired_workspace_must_already_be_physically_absent(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")
    task = managed_worktree_service.create_managed_worktree(
        str(repository), "terminal-retired-conflict", "task"
    )
    assert task is not None

    result = managed_worktree_service.purge_managed_worktree(
        _metadata(task), require_already_absent=True
    )

    assert result["removed"] is False
    assert result["reason_code"] == "WORKSPACE_RETIREMENT_STATE_CONFLICT"
    assert Path(task.path).exists()
    assert _git(repository, "show-ref", "--verify", f"refs/heads/{task.branch}")


def test_hard_purge_retry_finishes_private_branch_after_worktree_is_already_absent(
    tmp_path, monkeypatch
):
    repository = _repository(tmp_path)
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")
    task = managed_worktree_service.create_managed_worktree(
        str(repository), "terminal-retry", "task"
    )
    assert task is not None
    metadata = _metadata(task)
    removed = managed_worktree_service.remove_managed_worktree(metadata)
    assert removed["removed"] is True
    assert _git(repository, "show-ref", "--verify", f"refs/heads/{task.branch}")

    first = managed_worktree_service.purge_managed_worktree(metadata, require_already_absent=True)
    second = managed_worktree_service.purge_managed_worktree(metadata, require_already_absent=True)

    assert first["removed"] is True
    assert first["already_removed"] is True
    assert second["removed"] is True
    assert second["already_removed"] is True
    assert not Path(task.path).exists()


def test_historical_terminal_cleanup_deletes_only_the_receipted_branch_object(
    tmp_path, monkeypatch
):
    repository = _repository(tmp_path)
    monkeypatch.setattr(managed_worktree_service, "MANAGED_WORKTREE_DIR", tmp_path / "managed")
    task = managed_worktree_service.create_managed_worktree(
        str(repository), "terminal-history", "task"
    )
    assert task is not None
    metadata = _metadata(task)
    removed = managed_worktree_service.remove_managed_worktree(metadata)
    assert removed["removed"] is True
    receipt_authority = {
        **metadata,
        "managed_worktree_identity": "terminal-history",
        "managed_worktree_branch_object_id": removed["commit"],
    }

    moved = _git(repository, "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "late move")
    _git(repository, "update-ref", f"refs/heads/{task.branch}", moved)
    changed = managed_worktree_service.purge_managed_worktree(
        receipt_authority,
        require_already_absent=True,
    )
    assert changed["removed"] is False
    assert changed["reason_code"] == "MANAGED_WORKTREE_BRANCH_CHANGED"
    assert _git(repository, "rev-parse", f"refs/heads/{task.branch}") == moved

    receipt_authority["managed_worktree_branch_object_id"] = moved
    purged = managed_worktree_service.purge_managed_worktree(
        receipt_authority,
        require_already_absent=True,
    )
    assert purged["removed"] is True
    assert purged["branch_absent"] is True
