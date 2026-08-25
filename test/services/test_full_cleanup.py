import grp
import json
import os
import pwd
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from cli_agent_orchestrator.services.housekeeping.executor import execute_plan
from cli_agent_orchestrator.services.housekeeping.models import default_settings
from cli_agent_orchestrator.services.housekeeping_service import (
    _full_cleanup_execution_fence,
    full_cleanup_idle_gate,
    plan_full_cleanup,
    run_full_cleanup,
)


def _idle_inventory(monkeypatch, *, state="ready", lifecycle="running", leases=None, heavy=0):
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.list_all_terminals",
        lambda: [
            {
                "id": "agent-a",
                "runtime_lifecycle": lifecycle,
                "runtime_operation_kind": None,
            }
        ],
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.ui_read_model_service.list_agent_summaries",
        lambda **_kwargs: {
            "items": [{"id": "agent-a", "execution_state": state}],
            "next_offset": None,
        },
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.list_provider_execution_leases",
        lambda: list(leases or []),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.get_provider_execution_admission_queue",
        lambda: [],
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service._heavy_utilization",
        lambda _config: (heavy, 1),
    )


@pytest.mark.parametrize(
    ("state", "lifecycle"),
    [("ready", "running"), ("exited", "exited")],
)
def test_full_cleanup_idle_gate_accepts_ready_and_exited(monkeypatch, state, lifecycle):
    _idle_inventory(monkeypatch, state=state, lifecycle=lifecycle)

    result = full_cleanup_idle_gate({})

    assert result["eligible"] is True
    assert result["blockers"] == []


@pytest.mark.parametrize("state", ["working", "processing", "starting", "queued"])
def test_full_cleanup_idle_gate_blocks_executing_states(monkeypatch, state):
    _idle_inventory(monkeypatch, state=state)

    result = full_cleanup_idle_gate({})

    assert result["eligible"] is False
    assert result["reason_code"] == "FULL_CLEANUP_NOT_IDLE"
    assert any(item["reason_code"] == "AGENT_EXECUTION_NOT_IDLE" for item in result["blockers"])


@pytest.mark.parametrize(
    ("leases", "heavy", "reason"),
    [
        ([{"terminal_id": "agent-a"}], 0, "PROVIDER_EXECUTION_ACTIVE"),
        ([], 1, "HEAVY_EXECUTION_ACTIVE"),
    ],
)
def test_full_cleanup_idle_gate_blocks_execution_authority(monkeypatch, leases, heavy, reason):
    _idle_inventory(monkeypatch, leases=leases, heavy=heavy)

    result = full_cleanup_idle_gate({})

    assert result["eligible"] is False
    assert any(item["reason_code"] == reason for item in result["blockers"])


def test_full_cleanup_idle_gate_blocks_queued_provider_input(monkeypatch):
    _idle_inventory(monkeypatch)
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.get_provider_execution_admission_queue",
        lambda: [{"source": "inbox", "terminal_id": "agent-a", "source_id": 7}],
    )

    result = full_cleanup_idle_gate({})

    assert result["eligible"] is False
    assert {
        "terminal_id": "agent-a",
        "reason_code": "PROVIDER_EXECUTION_QUEUED",
    } in result["blockers"]


def test_full_cleanup_idle_gate_fails_closed_on_read_model_identity_mismatch(monkeypatch):
    _idle_inventory(monkeypatch)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.ui_read_model_service.list_agent_summaries",
        lambda **_kwargs: {"items": [], "next_offset": None},
    )

    result = full_cleanup_idle_gate({})

    assert result["eligible"] is False
    assert result["blockers"] == [
        {"reason_code": "AGENT_EXECUTION_STATE_UNKNOWN", "terminal_id": "agent-a"}
    ]


def test_full_cleanup_execute_rechecks_idle_before_planning(tmp_path, monkeypatch):
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service.full_cleanup_idle_gate",
        lambda _config: {
            "eligible": False,
            "reason_code": "FULL_CLEANUP_NOT_IDLE",
            "blockers": [{"terminal_id": "agent-a", "reason_code": "AGENT_EXECUTION_NOT_IDLE"}],
        },
    )
    planned = False

    def plan(**_kwargs):
        nonlocal planned
        planned = True

    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service.plan_housekeeping", plan
    )

    with pytest.raises(RuntimeError, match="FULL_CLEANUP_NOT_IDLE"):
        run_full_cleanup(
            expected_plan_id="a" * 64,
            confirmed=True,
            config={"root": str(tmp_path), "lock_dir": str(lock_dir)},
        )

    assert planned is False


def test_full_cleanup_requires_explicit_confirmation_before_loading_config(monkeypatch):
    loaded = False

    def load():
        nonlocal loaded
        loaded = True

    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service.load_operations_config",
        load,
    )

    with pytest.raises(RuntimeError, match="FULL_CLEANUP_CONFIRMATION_REQUIRED"):
        run_full_cleanup(expected_plan_id="a" * 64, confirmed=False)

    assert loaded is False


def test_full_cleanup_isolated_end_to_end_and_idempotent(tmp_path, monkeypatch):
    release_root = tmp_path / "releases"
    release_root.mkdir()

    def release(name: str) -> Path:
        path = release_root / name
        path.mkdir()
        (path / "payload").write_text(name, encoding="utf-8")
        (path / ".threadcells-release.json").write_text(
            json.dumps({"schema_version": 1, "release_id": name, "source_commit": "a" * 40}),
            encoding="utf-8",
        )
        return path

    active = release("active")
    rollback = release("rollback")
    state = tmp_path / "state/cao"
    state.mkdir(parents=True)
    metadata = state / "release-metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "active_release": str(active.resolve()),
                "rollback_releases": [str(rollback.resolve())],
                "candidate_releases": [],
            }
        ),
        encoding="utf-8",
    )
    active_link = state / "active"
    active_link.symlink_to(active, target_is_directory=True)
    log = state / "logs/archive.log"
    log.parent.mkdir()
    log.write_bytes(b"historical output")
    cache_root = tmp_path / "cache"
    cache = cache_root / "generated-evidence"
    cache.mkdir(parents=True)
    (cache / "payload").write_bytes(b"reproducible")
    (cache / ".threadcells-reproducible.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "owner": "threadcells",
                "kind": "test_evidence",
                "created_at": 1,
                "owner_pid": 99_999_999,
            }
        ),
        encoding="utf-8",
    )
    backup = tmp_path / "backups/database.sqlite"
    backup.parent.mkdir()
    backup.write_bytes(b"preserve")
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    disposable_artifact = artifact_root / "threadcells-candidate-old"
    disposable_artifact.mkdir()
    disposable_artifact.joinpath("bundle.whl").write_bytes(b"wheel")
    ambiguous_artifact = artifact_root / "operator-notes"
    ambiguous_artifact.write_text("preserve", encoding="utf-8")
    git_artifact = artifact_root / "threadcells-review-worktree"
    git_artifact.mkdir()
    git_artifact.joinpath(".git").write_text("gitdir: protected", encoding="utf-8")
    locks = state / "locks"
    locks.mkdir()
    release_lock = tmp_path / "release-staging.lock"
    release_lock.touch()
    release_lock.chmod(0o660)
    config = {
        "root": str(tmp_path),
        "lock_dir": str(locks),
        "runtime_user": pwd.getpwuid(os.getuid()).pw_name,
        "release_roots": [str(release_root)],
        "release_metadata": str(metadata),
        "active_release_link": str(active_link),
        "release_staging_lock": str(release_lock),
        "release_admin_group": grp.getgrgid(os.getgid()).gr_name,
        "release_control_uid": os.getuid(),
        "reproducible_cache_roots": [str(cache_root)],
        "reproducible_cache_retain_minutes": 1,
        "reproducible_cache_owned_prefixes": [],
        "playwright_browser_caches": [],
        "package_caches": [],
        "full_cleanup_artifact_roots": [
            {
                "path": str(artifact_root),
                "owned_names": [],
                "owned_prefixes": ["threadcells-"],
                "process_names": {},
            }
        ],
        "protected_inventory_roots": [],
        "worktree_roots": [],
        "worktree_repository_collections": [],
        "worktree_repository_paths": [],
        "context_launch_lock_timeout_seconds": 0.1,
        "log_compress_after_minutes": 1,
        "retention_minutes": 1,
        "log_tree_warning_gib": 1,
        "backup_tree_warning_gib": 1,
    }
    _idle_inventory(monkeypatch)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service.get_housekeeping_settings",
        lambda _config: default_settings(config),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service._runtime_open_paths_inventory",
        lambda _config, _proc_root: (set(), True),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.list_orphaned_protected_workflow_authorities",
        lambda: [],
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.list_legacy_child_retirements_for_cleanup",
        lambda: [],
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.list_pending_child_retirement_cleanups",
        lambda: [],
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping.worktrees.plan_worktrees",
        lambda **_kwargs: ([], []),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service._reconcile_supervisor_context_roles",
        lambda _summary: None,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service._reconcile_writer_leases",
        lambda _summary: None,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service._reconcile_legacy_terminal_authority",
        lambda _summary: None,
    )
    proc_root = tmp_path / "proc"
    proc_root.mkdir()

    preview = plan_full_cleanup(config=config, now=1000, proc_root=proc_root)
    assert preview["idle_gate"]["eligible"] is True
    assert preview["release_state"]["active_only_expected"] is True
    assert any(
        item["category"] == "build_artifact" and item["action"] == "delete"
        for item in preview["candidates"]
    )

    def privileged_cleanup_executor(*, plan, config, settings, proc_root):
        from cli_agent_orchestrator.services.housekeeping.executor import (
            privileged_full_cleanup_candidate,
        )

        privileged_plan = replace(
            plan,
            candidates=tuple(
                candidate
                for candidate in plan.candidates
                if privileged_full_cleanup_candidate(candidate)
            ),
        )
        return execute_plan(
            privileged_plan,
            config=config,
            settings=settings,
            open_inventory=lambda: (set(), True),
            proc_root=proc_root,
            full_cleanup=True,
            lifecycle_fence_held=True,
        )

    first = run_full_cleanup(
        expected_plan_id=preview["plan_id"],
        confirmed=True,
        config=config,
        now=1000,
        proc_root=proc_root,
        privileged_cleanup_executor=privileged_cleanup_executor,
    )

    assert first.ok is True
    assert first.rollback_available is False
    assert first.active_release == str(active.resolve())
    assert first.releases_removed == 1
    active_protection = next(
        item
        for item in first.protected_resources
        if item["canonical_identity"] == f"releases:{active.resolve()}"
    )
    assert active_protection["category"] == "releases"
    assert active_protection["bytes"] > 0
    assert active_protection["reason"] == "ACTIVE_RELEASE"
    assert {
        "canonical_identity": f"backups:{backup.parent.resolve()}",
        "category": "backups",
        "bytes": len(b"preserve"),
        "reason": "BACKUP_PROTECTED",
    } in first.protected_resources
    assert first.execution_skips == []
    assert active.is_dir() and not rollback.exists()
    assert not log.exists() and not cache.exists()
    assert not disposable_artifact.exists()
    assert ambiguous_artifact.exists()
    assert git_artifact.exists()
    assert backup.read_bytes() == b"preserve"

    replay = plan_full_cleanup(config=config, now=1000, proc_root=proc_root)
    assert replay["reclaimable_bytes"] == 0
    second = run_full_cleanup(
        expected_plan_id=replay["plan_id"],
        confirmed=True,
        config=config,
        now=1000,
        proc_root=proc_root,
        privileged_cleanup_executor=privileged_cleanup_executor,
    )
    assert second.ok is True
    assert second.freed_bytes == 0
    assert second.rollback_available is False
    assert any(
        item["canonical_identity"] == f"backups:{backup.parent.resolve()}"
        and item["reason"] == "BACKUP_PROTECTED"
        for item in second.protected_resources
    )
    assert active.is_dir() and backup.is_file()
    unknown_release_entry = release_root / "unclassified.bundle"
    unknown_release_entry.write_bytes(b"unknown")
    ambiguous_preview = plan_full_cleanup(config=config, now=1000, proc_root=proc_root)
    assert ambiguous_preview["release_state"]["active_only_expected"] is False
    assert any(
        item["path"] == str(unknown_release_entry)
        and item["protection_reason"] == "RELEASE_ENTRY_IDENTITY_UNKNOWN"
        for item in ambiguous_preview["candidates"]
    )


def test_full_cleanup_fence_blocks_new_turn_and_reconnect_admission(tmp_path, monkeypatch):
    config = {
        "lock_dir": str(tmp_path / "locks"),
        "context_launch_lock_timeout_seconds": 0.05,
    }
    Path(config["lock_dir"]).mkdir()
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.load_operations_config",
        lambda: config,
    )
    mutations: list[str] = []
    errors: list[str] = []
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.workflow_service.prepare_workflow_input",
        lambda *_args: mutations.append("turn") or {"turn_id": 1, "queued": True},
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.workflow_service._reconcile_root_workflow_with_admission",
        lambda *_args, **_kwargs: mutations.append("reconnect") or False,
    )

    def attempt(operation):
        try:
            operation()
        except Exception as exc:
            errors.append(getattr(exc, "reason_code", str(exc)))

    from cli_agent_orchestrator.services import workflow_service

    with _full_cleanup_execution_fence(config):
        workers = [
            threading.Thread(
                target=attempt,
                args=(lambda: workflow_service.prepare_external_input("agent-a", "work"),),
            ),
            threading.Thread(
                target=attempt,
                args=(lambda: workflow_service.reconcile_root_workflow("agent-a"),),
            ),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=1)

    assert mutations == []
    assert errors == ["ADMISSION_FENCE_TIMEOUT"]
