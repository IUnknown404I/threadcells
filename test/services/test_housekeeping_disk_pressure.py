import json
import os
import pwd
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.database import (
    Base,
    WorkflowModel,
    WorktreeWriterLeaseModel,
)
from cli_agent_orchestrator.services.housekeeping.executor import (
    _execute_resource,
    execute_plan,
)
from cli_agent_orchestrator.services.housekeeping.models import default_settings
from cli_agent_orchestrator.services.housekeeping.planner import build_plan

NOW = 2_000_000_000.0


@pytest.fixture
def workflow_authority_db(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'workflow-authority.db'}")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", session)
    monkeypatch.setattr(database, "_ensure_workflow_schema", lambda: None)
    yield session
    engine.dispose()


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path, *, payload_bytes: int = 1024) -> tuple[Path, Path]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--initial-branch=main")
    _git(repository, "config", "user.email", "housekeeping@example.invalid")
    _git(repository, "config", "user.name", "Housekeeping Test")
    (repository / "payload.bin").write_bytes(b"x" * payload_bytes)
    _git(repository, "add", "payload.bin")
    _git(repository, "commit", "-m", "durable main")
    worktree_root = tmp_path / "worktrees"
    worktree_root.mkdir()
    worktree = worktree_root / "landed"
    _git(repository, "worktree", "add", "-b", "landed", str(worktree), "main")
    return repository, worktree


def _config(tmp_path: Path, repository: Path | None = None) -> dict[str, object]:
    root = tmp_path / "control"
    release_root = root / "releases"
    release_root.mkdir(parents=True, exist_ok=True)
    metadata = root / "release-metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "active_release": None,
                "rollback_releases": [],
                "candidate_releases": [],
            }
        ),
        encoding="utf-8",
    )
    lock_dir = root / "locks"
    lock_dir.mkdir()
    release_lock = lock_dir / "release-staging.lock"
    release_lock.touch()
    release_lock.chmod(0o660)
    config: dict[str, object] = {
        "root": str(root),
        "lock_dir": str(lock_dir),
        "log_compress_after_minutes": 60,
        "retention_minutes": 120,
        "release_roots": [str(release_root)],
        "release_metadata": str(metadata),
        "active_release_link": str(root / "active"),
        "release_staging_lock": str(release_lock),
        "release_admin_group": __import__("grp").getgrgid(os.getgid()).gr_name,
        "release_control_uid": os.getuid(),
        "runtime_user": pwd.getpwuid(os.getuid()).pw_name,
        "subprocess_timeout_seconds": 20,
        "worktree_roots": [str(tmp_path / "worktrees")],
        "worktree_repository_collections": [],
        "worktree_repository_paths": [str(repository)] if repository else [],
        "worktree_durable_refs": ["refs/heads/main"],
        "reproducible_cache_roots": [],
        "protected_inventory_roots": [],
    }
    return config


def _authority(
    monkeypatch,
    *,
    terminals: list[dict[str, object]] | None = None,
    workflows: list[str] | None = None,
    leases: list[dict[str, object]] | None = None,
    projects: list[object] | None = None,
    work_contexts: list[dict[str, object]] | None = None,
) -> None:
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.list_all_terminals",
        lambda: list(terminals or []),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.get_protected_workflow_root_terminal_ids",
        lambda: list(workflows or []),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.list_worktree_writer_leases",
        lambda: list(leases or []),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.list_projects",
        lambda: list(projects or []),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.list_writable_work_contexts",
        lambda: list(work_contexts or []),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.list_orphaned_protected_workflow_authorities",
        lambda: [],
    )


def _plan(
    tmp_path: Path,
    config: dict[str, object],
    *,
    mode: str = "pressure",
    open_paths: set[Path] | None = None,
):
    return build_plan(
        root=Path(str(config["root"])),
        config=config,
        settings=default_settings(config),
        mode=mode,  # type: ignore[arg-type]
        now=NOW,
        open_inventory=lambda: (set(open_paths or set()), True),
        proc_root=tmp_path / "proc",
    )


def _worktree_candidate(plan, path: Path):
    return next(
        item
        for item in plan.candidates
        if item.resource_kind == "git_worktree" and item.path == str(path.resolve())
    )


def test_clean_merged_inactive_worktree_is_actionable_and_counted(tmp_path, monkeypatch):
    repository, worktree = _repository(tmp_path, payload_bytes=2 * 1024 * 1024)
    config = _config(tmp_path, repository)
    _authority(monkeypatch)

    weekly = _plan(tmp_path, config, mode="weekly")
    pressure = _plan(tmp_path, config, mode="pressure")
    candidate = _worktree_candidate(pressure, worktree)

    assert candidate.action == "retire"
    assert candidate.estimated_reclaim_bytes == candidate.bytes
    assert candidate.bytes >= 2 * 1024 * 1024
    assert pressure.class_summaries["worktrees"]["reclaimable_bytes"] == candidate.bytes
    assert {item.canonical_identity: item.action for item in weekly.candidates} == {
        item.canonical_identity: item.action for item in pressure.candidates
    }
    actionable = [item for item in pressure.candidates if item.action != "preserve"]
    assert actionable == sorted(
        actionable, key=lambda item: item.estimated_reclaim_bytes, reverse=True
    )


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("dirty", "WORKTREE_DIRTY"),
        ("unique", "WORKTREE_HEAD_NOT_DURABLE"),
    ],
)
def test_dirty_or_unique_worktree_is_protected(tmp_path, monkeypatch, state, expected):
    repository, worktree = _repository(tmp_path)
    config = _config(tmp_path, repository)
    _authority(monkeypatch)
    if state == "dirty":
        (worktree / "untracked.txt").write_text("local", encoding="utf-8")
    else:
        (worktree / "payload.bin").write_bytes(b"new commit")
        _git(worktree, "add", "payload.bin")
        _git(worktree, "commit", "-m", "unpublished")

    candidate = _worktree_candidate(_plan(tmp_path, config), worktree)

    assert candidate.action == "preserve"
    assert candidate.protection_reason == expected
    assert candidate.bytes > 0


def test_worktree_path_symlink_is_visible_and_protected(tmp_path, monkeypatch):
    repository, worktree = _repository(tmp_path)
    config = _config(tmp_path, repository)
    _authority(monkeypatch)
    relocated = tmp_path / "relocated-worktree"
    worktree.rename(relocated)
    worktree.symlink_to(relocated, target_is_directory=True)

    candidate = next(
        item
        for item in _plan(tmp_path, config).candidates
        if item.resource_kind == "git_worktree" and item.path == str(worktree.absolute())
    )

    assert candidate.action == "preserve"
    assert candidate.protection_reason == "WORKTREE_PATH_INVALID"
    assert relocated.joinpath("payload.bin").is_file()


def test_git_locked_worktree_is_protected(tmp_path, monkeypatch):
    repository, worktree = _repository(tmp_path)
    config = _config(tmp_path, repository)
    _authority(monkeypatch)
    _git(repository, "worktree", "lock", str(worktree))

    candidate = _worktree_candidate(_plan(tmp_path, config), worktree)

    assert candidate.action == "preserve"
    assert candidate.protection_reason == "WORKTREE_GIT_LOCKED"


@pytest.mark.parametrize(
    ("authority_kind", "expected"),
    [
        ("terminal", "ACTIVE_TERMINAL_WORKTREE"),
        ("managed_source", "ACTIVE_MANAGED_WORKTREE_SOURCE"),
        ("workflow", "ACTIVE_OR_RECOVERY_WORKFLOW"),
        ("lease", "WRITER_LEASE_WORKTREE"),
        ("work_context", "DURABLE_WORK_CONTEXT"),
        ("project", "PROJECT_SOURCE_AUTHORITY"),
    ],
)
def test_active_authority_protects_worktree(tmp_path, monkeypatch, authority_kind, expected):
    repository, worktree = _repository(tmp_path)
    config = _config(tmp_path, repository)
    terminal = {
        "id": "owner",
        "launch_worktree": (
            str(tmp_path / "managed-child") if authority_kind == "managed_source" else str(worktree)
        ),
        "managed_worktree_kind": "task" if authority_kind == "managed_source" else None,
        "managed_worktree_source": str(worktree) if authority_kind == "managed_source" else None,
        "runtime_lifecycle": (
            "running" if authority_kind in {"terminal", "managed_source"} else "exited"
        ),
    }
    _authority(
        monkeypatch,
        terminals=[terminal],
        workflows=["owner"] if authority_kind == "workflow" else [],
        leases=(
            [{"canonical_worktree": str(worktree), "terminal_id": "owner"}]
            if authority_kind == "lease"
            else []
        ),
        projects=([SimpleNamespace(path=str(worktree))] if authority_kind == "project" else []),
        work_contexts=(
            [
                {
                    "state": "reserved",
                    "canonical_worktree": str(worktree),
                    "canonical_source": str(repository),
                }
            ]
            if authority_kind == "work_context"
            else []
        ),
    )

    candidate = _worktree_candidate(_plan(tmp_path, config), worktree)

    assert candidate.action == "preserve"
    assert candidate.protection_reason == expected


def test_orphan_workflow_authority_fails_closed_for_every_worktree(tmp_path, monkeypatch):
    repository, worktree = _repository(tmp_path)
    config = _config(tmp_path, repository)
    _authority(monkeypatch, workflows=["missing-root-terminal"])

    plan = _plan(tmp_path, config)
    candidate = _worktree_candidate(plan, worktree)

    assert candidate.action == "preserve"
    assert candidate.protection_reason == "WORKTREE_AUTHORITY_INVENTORY_UNKNOWN"
    assert "worktree_authority_inventory_uncertain" in plan.warnings
    assert "worktree_orphan_workflow_authority:1" in plan.warnings


def test_missing_root_workflow_authority_is_reconciled_before_worktree_retirement(
    tmp_path, monkeypatch
):
    repository, worktree = _repository(tmp_path)
    config = _config(tmp_path, repository)
    _authority(
        monkeypatch,
        terminals=[
            {
                "id": "active-owner",
                "launch_worktree": str(worktree),
                "runtime_lifecycle": "running",
            }
        ],
    )
    rows = [
        {
            "root_terminal_id": "missing-root",
            "workflows": [
                {
                    "id": 42,
                    "status": "owner_gate",
                    "active_turn_id": None,
                    "updated_at": None,
                }
            ],
            "pending_turns": [],
            "active_assignments": [],
            "provider_execution_turn_id": None,
            "writer_lease_path": str(worktree),
        }
    ]
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.list_orphaned_protected_workflow_authorities",
        lambda: list(rows),
    )

    def reconcile(
        root_terminal_id,
        workflow_ids,
        expected_fingerprint,
        expected_writer_lease_path,
        expected_direct_assignment_ids,
    ):
        assert root_terminal_id == "missing-root"
        assert workflow_ids == [42]
        assert expected_fingerprint
        assert expected_writer_lease_path == str(worktree)
        assert expected_direct_assignment_ids == []
        rows.clear()
        return {
            "reconciled": 1,
            "already_reconciled": False,
            "reason": "root_terminal_absent",
        }

    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.reconcile_orphaned_protected_workflow_authority",
        reconcile,
    )
    plan = _plan(tmp_path, config)
    candidate = next(item for item in plan.candidates if item.resource_kind == "workflow_authority")

    report = execute_plan(
        plan,
        config=config,
        settings=default_settings(config),
        open_inventory=lambda: (set(), True),
        proc_root=tmp_path / "proc",
    )

    assert candidate.action == "prune"
    assert candidate.estimated_reclaim_bytes == 0
    assert candidate.canonical_identity in report.executed
    assert report.executed_count_by_class["retirement_cleanup"] == 1
    assert worktree.exists()


def test_workflow_close_between_plan_and_execute_releases_planned_writer_authority(
    tmp_path, workflow_authority_db
):
    """Executor passes an absent candidate to the exact transactional verifier."""
    root = "missing-root-close-race"
    writer_path = str(tmp_path / "worktrees" / "missing-root-close-race")
    with workflow_authority_db() as db:
        workflow = WorkflowModel(root_terminal_id=root, status="open")
        db.add(workflow)
        db.add(
            WorktreeWriterLeaseModel(
                canonical_worktree=writer_path,
                terminal_id=root,
            )
        )
        db.commit()

    config = _config(tmp_path)
    plan = _plan(tmp_path, config)
    candidate = next(item for item in plan.candidates if item.resource_kind == "workflow_authority")
    assert candidate.action == "prune"

    # This transition removes the candidate from the OPEN/OWNER_GATE planner
    # inventory but deliberately does not release writer authority itself.
    assert database.set_workflow_terminal_state(root, "cancelled") is True
    assert database.list_orphaned_protected_workflow_authorities() == []
    with workflow_authority_db() as db:
        assert db.query(WorktreeWriterLeaseModel).count() == 1

    report = execute_plan(
        plan,
        config=config,
        settings=default_settings(config),
        open_inventory=lambda: (set(), True),
        proc_root=tmp_path / "proc",
    )

    assert report.ok is True
    assert report.skipped == []
    assert report.failures == []
    assert report.executed == [candidate.canonical_identity]
    assert report.executed_count_by_class == {"retirement_cleanup": 1}
    with workflow_authority_db() as db:
        assert db.query(WorktreeWriterLeaseModel).count() == 0


def test_worktree_becoming_active_after_plan_blocks_execution(tmp_path, monkeypatch):
    repository, worktree = _repository(tmp_path)
    config = _config(tmp_path, repository)
    terminals: list[dict[str, object]] = []
    _authority(monkeypatch, terminals=terminals)
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.list_all_terminals", lambda: list(terminals)
    )
    plan = _plan(tmp_path, config)
    candidate = _worktree_candidate(plan, worktree)
    assert candidate.action == "retire"
    terminals.append(
        {"id": "new-owner", "launch_worktree": str(worktree), "runtime_lifecycle": "running"}
    )

    report = execute_plan(
        plan,
        config=config,
        settings=default_settings(config),
        open_inventory=lambda: (set(), True),
        proc_root=tmp_path / "proc",
    )

    assert worktree.exists()
    assert any(item["reason_code"] == "ACTIVE_TERMINAL_WORKTREE" for item in report.skipped)


def test_worktree_retirement_uses_git_and_replay_is_harmless(tmp_path, monkeypatch):
    repository, worktree = _repository(tmp_path)
    config = _config(tmp_path, repository)
    _authority(monkeypatch)
    plan = _plan(tmp_path, config)

    first = execute_plan(
        plan,
        config=config,
        settings=default_settings(config),
        open_inventory=lambda: (set(), True),
        proc_root=tmp_path / "proc",
    )
    second = execute_plan(
        plan,
        config=config,
        settings=default_settings(config),
        open_inventory=lambda: (set(), True),
        proc_root=tmp_path / "proc",
    )

    assert first.ok is True
    assert first.reclaimed_bytes_by_class["worktrees"] > 0
    assert not worktree.exists()
    assert str(worktree) not in _git(repository, "worktree", "list", "--porcelain")
    assert (repository / ".git").is_dir()
    assert _git(repository, "status", "--porcelain") == ""
    assert second.ok is True
    assert second.freed_bytes == 0
    assert any(item["reason_code"] == "CANDIDATE_NO_LONGER_ELIGIBLE" for item in second.skipped)


def test_worktree_retirement_pins_head_if_durable_ref_disappears_after_revalidation(
    tmp_path, monkeypatch
):
    repository, worktree = _repository(tmp_path)
    config = _config(tmp_path, repository)
    _authority(monkeypatch)
    plan = _plan(tmp_path, config)
    removed_ref = False

    def racing_runner(command, **kwargs):
        nonlocal removed_ref
        if command[1:4] == ["--git-dir", str(repository / ".git"), "worktree"] and (
            "remove" in command
        ):
            _git(repository, "update-ref", "-d", "refs/heads/main")
            removed_ref = True
        return subprocess.run(command, **kwargs)

    report = execute_plan(
        plan,
        config=config,
        settings=default_settings(config),
        open_inventory=lambda: (set(), True),
        proc_root=tmp_path / "proc",
        runner=racing_runner,
    )

    pins = _git(
        repository, "for-each-ref", "--format=%(refname)", "refs/threadcells/housekeeping-pins"
    )
    assert removed_ref is True
    assert report.ok is True
    assert not worktree.exists()
    assert pins.startswith("refs/threadcells/housekeeping-pins/")


def _marked_cache(root: Path, name: str = "fixture") -> Path:
    candidate = root / name
    candidate.mkdir(parents=True)
    (candidate / "payload.bin").write_bytes(b"c" * 4096)
    (candidate / ".threadcells-reproducible.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "owner": "threadcells",
                "kind": "cache",
                "created_at": NOW - 10_000,
                "owner_pid": 99_999_999,
            }
        ),
        encoding="utf-8",
    )
    return candidate


def test_reproducible_cache_is_bounded_actionable_and_idempotent(tmp_path, monkeypatch):
    config = _config(tmp_path)
    cache_root = tmp_path / "approved-cache"
    cache_root.mkdir()
    config["reproducible_cache_roots"] = [str(cache_root)]
    config["reproducible_cache_retain_minutes"] = 1
    candidate_path = _marked_cache(cache_root)
    specialized = cache_root / "ms-playwright"
    specialized.mkdir()
    config["playwright_browser_caches"] = [str(specialized)]
    outside_root = tmp_path / "outside"
    outside_root.mkdir()
    outside = _marked_cache(outside_root, "not-in-scope")
    _authority(monkeypatch)
    plan = _plan(tmp_path, config)
    candidate = next(item for item in plan.candidates if item.resource_kind == "reproducible_cache")

    assert candidate.path == str(candidate_path.resolve())
    assert candidate.action == "delete"
    assert all(item.path != str(outside.resolve()) for item in plan.candidates)
    assert all(item.path != str(specialized.resolve()) for item in plan.candidates)
    first = execute_plan(
        plan,
        config=config,
        settings=default_settings(config),
        open_inventory=lambda: (set(), True),
        proc_root=tmp_path / "proc",
    )
    second = execute_plan(
        plan,
        config=config,
        settings=default_settings(config),
        open_inventory=lambda: (set(), True),
        proc_root=tmp_path / "proc",
    )

    assert first.reclaimed_bytes_by_class["reproducible_cache"] > 0
    assert not candidate_path.exists()
    assert outside.exists()
    assert second.ok is True and second.freed_bytes == 0


def test_reproducible_cache_symlink_and_out_of_root_execution_fail_closed(tmp_path, monkeypatch):
    config = _config(tmp_path)
    cache_root = tmp_path / "approved-cache"
    cache_root.mkdir()
    config["reproducible_cache_roots"] = [str(cache_root)]
    config["reproducible_cache_retain_minutes"] = 1
    candidate_path = _marked_cache(cache_root)
    outside = tmp_path / "outside-data"
    outside.mkdir()
    (outside / "sentinel").write_text("preserve", encoding="utf-8")
    _authority(monkeypatch)
    plan = _plan(tmp_path, config)
    candidate = next(item for item in plan.candidates if item.resource_kind == "reproducible_cache")
    for child in candidate_path.iterdir():
        child.unlink()
    candidate_path.rmdir()
    candidate_path.symlink_to(outside, target_is_directory=True)

    report = execute_plan(
        plan,
        config=config,
        settings=default_settings(config),
        open_inventory=lambda: (set(), True),
        proc_root=tmp_path / "proc",
    )
    escaped = replace(candidate, path=str(outside), canonical_identity=f"cache:{outside}")

    assert outside.joinpath("sentinel").read_text(encoding="utf-8") == "preserve"
    assert any(item["reason_code"] == "REPRODUCIBLE_PATH_SYMLINK" for item in report.skipped)
    with pytest.raises(RuntimeError, match="authority changed"):
        _execute_resource(
            escaped,
            config=config,
            proc_root=tmp_path / "proc",
            runner=subprocess.run,
            sleeper=lambda _seconds: None,
        )


def test_owned_ci_cache_prefix_is_actionable_but_ambiguous_candidate_stays_protected(
    tmp_path, monkeypatch
):
    config = _config(tmp_path)
    cache_root = tmp_path / "approved-cache"
    cache_root.mkdir()
    config["reproducible_cache_roots"] = [str(cache_root)]
    config["reproducible_cache_owned_prefixes"] = ["threadcells-ci-venv-"]
    config["reproducible_cache_retain_minutes"] = 1
    ci_cache = cache_root / "threadcells-ci-venv-old"
    ci_cache.mkdir()
    (ci_cache / "payload").write_bytes(b"reproducible")
    ambiguous = cache_root / "threadcells-release-deadbee-candidate"
    ambiguous.mkdir()
    (ambiguous / "artifact").write_bytes(b"preserve")
    timestamp = NOW - 10_000
    os.utime(ci_cache, (timestamp, timestamp))
    os.utime(ambiguous, (timestamp, timestamp))
    _authority(monkeypatch)

    plan = _plan(tmp_path, config)
    by_name = {
        Path(item.path).name: item
        for item in plan.candidates
        if item.resource_kind == "reproducible_cache"
    }

    assert by_name[ci_cache.name].action == "delete"
    assert by_name[ci_cache.name].retention_reason == "owned_prefix_older_than_1_minutes"
    assert by_name[ambiguous.name].action == "preserve"
    assert by_name[ambiguous.name].protection_reason == "REPRODUCIBLE_MARKER_UNKNOWN"


def test_full_package_cache_command_has_truthful_estimate_and_actual_bytes(tmp_path, monkeypatch):
    config = _config(tmp_path)
    cache = tmp_path / "uv-cache"
    cache.mkdir()
    payload = cache / "payload"
    payload.write_bytes(b"u" * 4096)
    config["package_caches"] = [
        {
            "name": "uv-test",
            "path": str(cache),
            "command": ["uv", "cache", "clean", "--cache-dir", str(cache)],
            "path_argument": "--cache-dir",
            "minimum_bytes": 1,
            "full_reclaim": True,
        }
    ]
    _authority(monkeypatch)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping.executor.shutil.which",
        lambda _name: "/usr/bin/uv",
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping.planner.package_command_running",
        lambda *_args: False,
    )
    candidate = next(
        item for item in _plan(tmp_path, config).candidates if item.resource_kind == "package_cache"
    )
    commands: list[list[str]] = []

    def runner(command, **_kwargs):
        commands.append(command)
        payload.unlink()
        return SimpleNamespace(returncode=0)

    reclaimed = _execute_resource(
        candidate,
        config=config,
        proc_root=tmp_path / "proc",
        runner=runner,
        sleeper=lambda _seconds: None,
    )

    assert candidate.estimated_reclaim_bytes == candidate.bytes
    assert candidate.bytes >= 4096
    assert reclaimed == 4096
    assert commands == [["/usr/bin/uv", "cache", "clean", "--cache-dir", str(cache)]]


def test_active_package_cache_owner_is_protected(tmp_path, monkeypatch):
    config = _config(tmp_path)
    cache = tmp_path / "uv-cache"
    cache.mkdir()
    (cache / "payload").write_bytes(b"u" * 4096)
    config["package_caches"] = [
        {
            "name": "uv-test",
            "path": str(cache),
            "command": ["uv", "cache", "clean", "--cache-dir", str(cache)],
            "path_argument": "--cache-dir",
            "minimum_bytes": 1,
            "full_reclaim": True,
        }
    ]
    _authority(monkeypatch)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping.planner.package_command_running",
        lambda *_args: True,
    )

    candidate = next(
        item for item in _plan(tmp_path, config).candidates if item.resource_kind == "package_cache"
    )

    assert candidate.action == "preserve"
    assert candidate.protection_reason == "PACKAGE_CACHE_OWNER_ACTIVE"
    assert candidate.bytes >= 4096


def test_package_cache_script_process_and_class_overlap_fail_closed(tmp_path, monkeypatch):
    config = _config(tmp_path)
    cache = tmp_path / "npm-cache"
    cache.mkdir()
    (cache / "payload").write_bytes(b"n" * 4096)
    config["package_caches"] = [
        {
            "name": "npm-test",
            "path": str(cache),
            "command": ["npm", "cache", "clean", "--force", "--cache", str(cache)],
            "path_argument": "--cache",
            "minimum_bytes": 1,
            "full_reclaim": True,
        }
    ]
    process = tmp_path / "proc" / "123"
    process.mkdir(parents=True)
    (process / "cmdline").write_bytes(b"/usr/bin/node\0/usr/share/nodejs/npm/bin/npm-cli.js\0")
    _authority(monkeypatch)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping.planner.shutil.which",
        lambda _name: "/usr/bin/npm",
    )

    active = next(
        item for item in _plan(tmp_path, config).candidates if item.resource_kind == "package_cache"
    )
    assert active.protection_reason == "PACKAGE_CACHE_OWNER_ACTIVE"

    config["protected_inventory_roots"] = [{"path": str(tmp_path), "category": "tools"}]
    (process / "cmdline").write_bytes(b"/usr/bin/node\0/other.js\0")
    overlapped = next(
        item for item in _plan(tmp_path, config).candidates if item.resource_kind == "package_cache"
    )
    assert overlapped.protection_reason == "PACKAGE_CACHE_CLASS_OVERLAP"


def _release(root: Path, name: str, *, age_minutes: int) -> Path:
    release = root / name
    release.mkdir()
    (release / "payload").write_bytes(b"r" * 4096)
    (release / ".threadcells-release.json").write_text(
        json.dumps({"schema_version": 1, "release_id": name, "source_commit": "a" * 40}),
        encoding="utf-8",
    )
    timestamp = NOW - age_minutes * 60
    for path in (release / "payload", release / ".threadcells-release.json", release):
        os.utime(path, (timestamp, timestamp))
    return release


def test_release_reference_change_blocks_execution_and_protected_bytes_are_visible(
    tmp_path, monkeypatch
):
    config = _config(tmp_path)
    release_root = Path(str(config["release_roots"][0]))  # type: ignore[index]
    active = _release(release_root, "active", age_minutes=300)
    stale = _release(release_root, "stale", age_minutes=400)
    metadata = Path(str(config["release_metadata"]))
    metadata.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "active_release": str(active),
                "rollback_releases": [],
                "candidate_releases": [],
            }
        ),
        encoding="utf-8",
    )
    Path(str(config["active_release_link"])).symlink_to(active, target_is_directory=True)
    _authority(monkeypatch)
    settings = default_settings(config)
    settings["policy"]["releases"]["retain_count"] = 1
    plan = build_plan(
        root=Path(str(config["root"])),
        config=config,
        settings=settings,
        mode="pressure",
        now=NOW,
        open_inventory=lambda: (set(), True),
        proc_root=tmp_path / "proc",
    )
    assert next(item for item in plan.candidates if item.path == str(active)).bytes > 0
    assert next(item for item in plan.candidates if item.path == str(stale)).action == "delete"
    metadata.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "active_release": str(active),
                "rollback_releases": [str(stale)],
                "candidate_releases": [],
            }
        ),
        encoding="utf-8",
    )

    report = execute_plan(
        plan,
        config=config,
        settings=settings,
        open_inventory=lambda: (set(), True),
        proc_root=tmp_path / "proc",
    )

    assert stale.exists()
    assert any(item["reason_code"] == "CANONICAL_ROLLBACK_RELEASE" for item in report.skipped)


def test_release_recovery_reference_is_independently_protected(tmp_path, monkeypatch):
    config = _config(tmp_path)
    release_root = Path(str(config["release_roots"][0]))  # type: ignore[index]
    active = _release(release_root, "active", age_minutes=300)
    rollback = _release(release_root, "rollback", age_minutes=310)
    recovery = _release(release_root, "recovery", age_minutes=320)
    Path(str(config["release_metadata"])).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "active_release": str(active),
                "rollback_releases": [str(rollback), str(recovery)],
                "candidate_releases": [],
            }
        ),
        encoding="utf-8",
    )
    Path(str(config["active_release_link"])).symlink_to(active, target_is_directory=True)
    _authority(monkeypatch)

    by_name = {Path(item.path).name: item for item in _plan(tmp_path, config).candidates}

    assert by_name["active"].protection_reason == "ACTIVE_RELEASE"
    assert by_name["rollback"].protection_reason == "CANONICAL_ROLLBACK_RELEASE"
    assert by_name["recovery"].protection_reason == "RECOVERY_RELEASE"


def test_dominant_inventory_only_classes_report_protected_bytes(tmp_path, monkeypatch):
    config = _config(tmp_path)
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "snapshot.bin").write_bytes(b"t" * 2 * 1024 * 1024)
    config["protected_inventory_roots"] = [
        {
            "category": "tools",
            "path": str(tools),
            "purpose": "candidate tool snapshots",
            "reason": "TOOLS_RETENTION_AUTHORITY_UNKNOWN",
        }
    ]
    _authority(monkeypatch)

    plan = _plan(tmp_path, config)

    summary = plan.class_summaries["tools"]
    assert summary["preserved_bytes"] >= 2 * 1024 * 1024
    assert summary["reclaimable_bytes"] == 0
    assert summary["protection_reasons"] == {"TOOLS_RETENTION_AUTHORITY_UNKNOWN": 1}


def test_tools_inventory_is_per_resource_and_preserves_unknown_authority(tmp_path, monkeypatch):
    config = _config(tmp_path)
    tools = tmp_path / "tools"
    tools.mkdir()
    active = tools / "active-runtime"
    active.mkdir()
    active.joinpath("executable").write_bytes(b"active")
    unknown = tools / "unclassified-candidate"
    unknown.mkdir()
    unknown.joinpath("payload").write_bytes(b"unknown")
    config["protected_inventory_roots"] = [
        {
            "category": "tools",
            "path": str(tools),
            "purpose": "runtime and candidate tools",
            "reason": "TOOLS_RETENTION_AUTHORITY_UNKNOWN",
        }
    ]
    _authority(monkeypatch)

    plan = _plan(tmp_path, config, mode="frequent", open_paths={active / "executable"})
    candidates = [item for item in plan.candidates if item.category == "tools"]

    assert [Path(item.path).name for item in candidates] == [
        "active-runtime",
        "unclassified-candidate",
    ]
    assert all(item.action == "preserve" for item in candidates)
    assert candidates[0].protection_reason == "OPEN_BY_ACTIVE_PROCESS"
    assert dict(candidates[0].attributes)["authority"] == "active_runtime"
    assert candidates[1].protection_reason == "TOOLS_RETENTION_AUTHORITY_UNKNOWN"
    assert dict(candidates[1].attributes)["authority"] == "unknown"
    assert plan.class_summaries["tools"]["actionable_count"] == 0


def test_partial_tools_measurement_stays_protected_and_reports_specific_warning(
    tmp_path, monkeypatch
):
    config = _config(tmp_path)
    tools = tmp_path / "tools"
    tools.mkdir()
    tools.joinpath("unreadable-candidate").mkdir()
    config["protected_inventory_roots"] = [
        {
            "category": "tools",
            "path": str(tools),
            "reason": "TOOLS_RETENTION_AUTHORITY_UNKNOWN",
        }
    ]
    _authority(monkeypatch)
    from cli_agent_orchestrator.services.housekeeping import planner

    original = planner._inventory_tree_sizes

    def measure(paths):
        measured = original(paths)
        return {
            path: (17, False) if path.name == "unreadable-candidate" else value
            for path, value in measured.items()
        }

    monkeypatch.setattr(
        planner,
        "_inventory_tree_sizes",
        measure,
    )

    plan = _plan(tmp_path, config, mode="frequent")
    candidate = next(item for item in plan.candidates if item.category == "tools")

    assert candidate.action == "preserve"
    assert candidate.bytes == 17
    assert dict(candidate.attributes)["measurement"] == "partial"
    assert "protected_inventory_incomplete:tools" in plan.warnings
    assert plan.reclaimable_bytes == 0


def test_inventory_marks_a_resource_uncertain_when_it_changes_during_measurement(
    tmp_path, monkeypatch
):
    from cli_agent_orchestrator.services.housekeeping.planner import (
        _inventory_root_snapshot,
    )

    tools = tmp_path / "tools"
    tools.mkdir()
    candidate = tools / "candidate"
    candidate.write_bytes(b"before")

    def measure(paths):
        candidate.write_bytes(b"changed-during-inventory")
        return {path: (6, True) for path in paths}

    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping.planner._inventory_tree_sizes",
        measure,
    )

    snapshot = _inventory_root_snapshot(tools, expand_entries=True)

    assert snapshot["entries_certain"] is True
    assert snapshot["entries"][0]["name"] == "candidate"
    assert snapshot["entries"][0]["size_certain"] is False


def test_protected_inventory_uses_one_pathless_bounded_measurement_batch(tmp_path, monkeypatch):
    from cli_agent_orchestrator.services.housekeeping.planner import (
        collect_protected_inventory_snapshot,
    )

    root = tmp_path / "control"
    backup = root / "backups" / "daily.sqlite"
    backup.parent.mkdir(parents=True)
    backup.write_bytes(b"backup")
    tools = tmp_path / "tools"
    tool = tools / "runtime"
    tool.mkdir(parents=True)
    tool.joinpath("payload").write_bytes(b"tool")
    observed = []

    def measure(command, **kwargs):
        observed.append((command, kwargs))
        paths = [path for path in kwargs["input"].split(b"\0") if path]
        output = b"".join(b"17\t" + path + b"\0" for path in paths)
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr=b"")

    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping.planner.subprocess.run", measure
    )

    snapshot = collect_protected_inventory_snapshot(
        root=root,
        config={"protected_inventory_roots": [{"category": "tools", "path": str(tools)}]},
    )

    assert len(observed) == 1
    command, kwargs = observed[0]
    assert "--files0-from=-" in command
    assert "--one-file-system" in command
    assert kwargs["timeout"] == 20
    assert str(root) not in command
    assert {record["source"] for record in snapshot["roots"]} == {
        "backups",
        "protected",
    }


def test_batched_inventory_isolates_a_bound_path_failure(monkeypatch, tmp_path):
    from cli_agent_orchestrator.services.housekeeping.planner import (
        _inventory_tree_sizes,
    )

    readable = tmp_path / "readable"
    unreadable = tmp_path / "unreadable"

    def measure(command, **_kwargs):
        stdout = (
            b"11\t"
            + os.fsencode(str(readable))
            + b"\0"
            + b"7\t"
            + os.fsencode(str(unreadable))
            + b"\0"
        )
        stderr = (
            b"du: cannot read directory " + os.fsencode(str(unreadable)) + b": Permission denied\n"
        )
        return subprocess.CompletedProcess(command, 1, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping.planner.subprocess.run", measure
    )

    assert _inventory_tree_sizes([readable, unreadable]) == {
        readable: (11, True),
        unreadable: (7, False),
    }
