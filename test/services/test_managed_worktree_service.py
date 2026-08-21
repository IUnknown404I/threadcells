import subprocess
from pathlib import Path

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
