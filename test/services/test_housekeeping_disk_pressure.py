import json
import os
import pwd
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.services.housekeeping.executor import (
    _execute_resource,
    execute_plan,
)
from cli_agent_orchestrator.services.housekeeping.models import default_settings
from cli_agent_orchestrator.services.housekeeping.planner import build_plan

NOW = 2_000_000_000.0


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
