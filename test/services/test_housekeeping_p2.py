import fcntl
import grp
import json
import os
import pwd
from pathlib import Path
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.services.housekeeping.executor import (
    _execute_resource,
    execute_plan,
)
from cli_agent_orchestrator.services.housekeeping.models import (
    HousekeepingCandidate,
    candidate_fingerprint,
    default_settings,
    finalize_plan,
    validate_settings,
)
from cli_agent_orchestrator.services.housekeeping.planner import build_plan
from cli_agent_orchestrator.services.housekeeping_service import (
    _scheduled_mode_due,
    _write_schedule_receipt,
    housekeeping_main,
    run_housekeeping,
)

NOW = 2_000_000_000.0


@pytest.fixture(autouse=True)
def empty_retirement_cleanup_inventory(monkeypatch):
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.list_legacy_child_retirements_for_cleanup",
        lambda: [],
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.list_pending_child_retirement_cleanups",
        lambda: [],
    )


def _config(root: Path):
    return {
        "root": str(root),
        "lock_dir": str(root / "locks"),
        "log_compress_after_minutes": 60,
        "retention_minutes": 120,
        "release_roots": [str(root / "tools")],
        "release_metadata": str(root / "state/cao/release-metadata.json"),
        "release_staging_lock": str(root / "locks/release-staging.lock"),
        "release_admin_group": grp.getgrgid(os.getgid()).gr_name,
    }


def _age(path: Path, minutes: int):
    os.utime(path, (NOW - minutes * 60, NOW - minutes * 60))


def _plan(root: Path, monkeypatch, *, mode="weekly"):
    monkeypatch.setattr("cli_agent_orchestrator.clients.database.list_all_terminals", lambda: [])
    config = _config(root)
    return build_plan(
        root=root,
        config=config,
        settings=default_settings(config),
        mode=mode,
        now=NOW,
        open_inventory=lambda: (set(), True),
    )


def test_execution_fails_closed_when_the_inspected_plan_changed(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service.get_housekeeping_settings",
        lambda _config: default_settings(config),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service.plan_housekeeping",
        lambda **_kwargs: SimpleNamespace(
            plan_id="a" * 64,
            candidates=(),
            reclaimable_bytes=0,
            warnings=(),
        ),
    )

    with pytest.raises(RuntimeError, match="HOUSEKEEPING_PLAN_CHANGED"):
        run_housekeeping(
            config=config,
            dry_run=False,
            mode="frequent",
            expected_plan_id="b" * 64,
        )

    assert not (tmp_path / "state/cao/housekeeping-status.json").exists()


def test_manual_execution_requires_an_inspected_plan_before_loading_config(monkeypatch):
    config_loaded = False

    def load_config():
        nonlocal config_loaded
        config_loaded = True
        return {}

    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service.load_operations_config",
        load_config,
    )

    with pytest.raises(RuntimeError, match="HOUSEKEEPING_PLAN_REQUIRED"):
        run_housekeeping(dry_run=False, mode="frequent")

    assert config_loaded is False


def test_exited_terminal_runtime_is_planned_revalidated_and_retired_without_history_deletion(
    tmp_path, monkeypatch
):
    terminal = {
        "id": "closed001",
        "tmux_session": "cao-history",
        "tmux_window": "reviewer",
        "runtime_lifecycle": "exited",
        "runtime_pane_id": "%41",
        "runtime_pane_pid": 4242,
        "runtime_generation": "gen-1",
        "runtime_generation_origin": "launch",
        "runtime_process_start_ticks": 777,
    }
    target = SimpleNamespace(
        pane_id="%41",
        pane_pid=4242,
        current_command="bash",
        terminal_id="closed001",
        runtime_generation="gen-1",
        process_start_ticks=777,
        generation_inherited=True,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.list_all_terminals", lambda: [terminal]
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.tmux.tmux_client.exact_runtime_target",
        lambda *_args, **_kwargs: target,
    )
    retired = []
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.retire_exited_terminal_runtime",
        lambda terminal_id, **_kwargs: retired.append(terminal_id) or True,
    )
    config = _config(tmp_path)
    plan = build_plan(
        root=tmp_path,
        config=config,
        settings=default_settings(config),
        mode="frequent",
        now=NOW,
        open_inventory=lambda: (set(), True),
    )
    candidate = next(item for item in plan.candidates if item.resource_kind == "terminal_runtime")

    assert candidate.action == "terminate"
    assert candidate.estimated_reclaim_bytes == 0
    report = execute_plan(plan, config=config, open_inventory=lambda: (set(), True))
    assert report.ok is True
    assert retired == ["closed001"]
    assert terminal["runtime_lifecycle"] == "exited"


def test_exited_terminal_runtime_identity_mismatch_is_protected(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.list_all_terminals",
        lambda: [
            {
                "id": "historical",
                "tmux_session": "cao-reused",
                "tmux_window": "agent",
                "runtime_lifecycle": "exited",
                "runtime_pane_id": "%42",
                "runtime_pane_pid": 4343,
                "runtime_generation": "old-gen",
                "runtime_generation_origin": "launch",
                "runtime_process_start_ticks": 700,
            }
        ],
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.tmux.tmux_client.exact_runtime_target",
        lambda *_args, **_kwargs: SimpleNamespace(
            pane_id="%42",
            pane_pid=4343,
            current_command="bash",
            terminal_id="new-owner",
            runtime_generation="new-gen",
            process_start_ticks=800,
            generation_inherited=True,
        ),
    )
    plan = build_plan(
        root=tmp_path,
        config=_config(tmp_path),
        settings=default_settings(_config(tmp_path)),
        mode="frequent",
        now=NOW,
        open_inventory=lambda: (set(), True),
    )
    candidate = next(item for item in plan.candidates if item.resource_kind == "terminal_runtime")
    assert candidate.action == "preserve"
    assert candidate.protection_reason == "TERMINAL_RUNTIME_IDENTITY_MISMATCH"


def test_pending_retirement_cleanup_uses_the_revalidated_plan_executor(tmp_path, monkeypatch):
    intent = {"version": 1, "terminal_id": "child001", "managed": False}
    pending = {
        "child_terminal_id": "child001",
        "claim_token": "claim-token",
        "intent": intent,
        "delegation_kind": "assign",
    }
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.list_pending_child_retirement_cleanups",
        lambda: [pending],
    )
    monkeypatch.setattr("cli_agent_orchestrator.clients.database.list_all_terminals", lambda: [])
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.get_child_retirement_cleanup_intent",
        lambda child, token: (
            {
                "intent": intent,
                "cleanup_completed": False,
                "claim_token": token,
            }
            if child == "child001"
            else None
        ),
    )
    completed = []
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.complete_child_retirement",
        lambda child, token, current, kind: completed.append((child, token, current, kind)) or True,
    )
    config = _config(tmp_path)
    plan = build_plan(
        root=tmp_path,
        config=config,
        settings=default_settings(config),
        mode="frequent",
        now=NOW,
        open_inventory=lambda: (set(), True),
    )
    candidate = next(item for item in plan.candidates if item.resource_kind == "retirement_cleanup")

    assert candidate.action == "prune"
    report = execute_plan(plan, config=config, open_inventory=lambda: (set(), True))
    assert report.ok is True
    assert report.executed == [candidate.canonical_identity]
    assert completed == [("child001", "claim-token", intent, "assign")]


def test_housekeeping_cli_forwards_the_inspected_plan_id(monkeypatch, capsys):
    calls = []

    class Summary:
        ok = True

        @staticmethod
        def as_dict():
            return {"ok": True, "plan_id": "a" * 64}

    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service.run_housekeeping",
        lambda **kwargs: calls.append(kwargs) or Summary(),
    )

    assert housekeeping_main(["--mode", "frequent", "--plan-id", "a" * 64]) == 0
    assert calls == [
        {
            "dry_run": False,
            "mode": "frequent",
            "scheduled": False,
            "expected_plan_id": "a" * 64,
        }
    ]
    assert "HOUSEKEEPING_OK" in capsys.readouterr().out


def test_plan_identity_is_content_addressed_not_timestamp_addressed(tmp_path):
    candidate = HousekeepingCandidate(
        category="logs",
        path=str(tmp_path / "cao.log"),
        canonical_identity="logs:cao.log",
        fingerprint="a" * 64,
        bytes=100,
        estimated_reclaim_bytes=50,
        action="compress",
        retention_reason="older_than_policy",
    )
    first = finalize_plan(
        generated_at=NOW,
        mode="frequent",
        root=tmp_path,
        candidates=[candidate],
        warnings=[],
    )
    later = finalize_plan(
        generated_at=NOW + 30,
        mode="frequent",
        root=tmp_path,
        candidates=[candidate],
        warnings=[],
    )
    changed = finalize_plan(
        generated_at=NOW + 30,
        mode="frequent",
        root=tmp_path,
        candidates=[HousekeepingCandidate(**{**candidate.as_dict(), "fingerprint": "b" * 64})],
        warnings=[],
    )

    assert first.generated_at != later.generated_at
    assert first.plan_id == later.plan_id
    assert changed.plan_id != first.plan_id


def test_plan_is_structured_and_dry_run_compression_estimate_is_nonzero(tmp_path, monkeypatch):
    log = tmp_path / "state/cao/logs/cao_old.log"
    log.parent.mkdir(parents=True)
    log.write_bytes(b"compressible\n" * 10_000)
    _age(log, 90)

    plan = _plan(tmp_path, monkeypatch, mode="frequent")
    candidate = next(item for item in plan.candidates if item.path == str(log.resolve()))

    assert len(plan.plan_id) == 64
    assert candidate.category == "logs"
    assert candidate.action == "compress"
    assert candidate.bytes == log.stat().st_size
    assert 0 < candidate.estimated_reclaim_bytes < candidate.bytes
    assert plan.reclaimable_bytes == candidate.estimated_reclaim_bytes


def test_active_terminal_attachment_is_protected_even_when_not_open(tmp_path, monkeypatch):
    attachment = tmp_path / "state/cao/runtime/terminal-attachments/live-child/old.png"
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(b"attachment")
    _age(attachment, 121)
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.list_all_terminals",
        lambda: [{"id": "live-child", "runtime_lifecycle": "running"}],
    )
    config = _config(tmp_path)

    plan = build_plan(
        root=tmp_path,
        config=config,
        settings=default_settings(config),
        mode="frequent",
        now=NOW,
        open_inventory=lambda: (set(), True),
    )
    candidate = next(item for item in plan.candidates if item.category == "attachments")

    assert candidate.action == "preserve"
    assert candidate.protection_reason == "ACTIVE_TERMINAL_ATTACHMENT"
    assert candidate.estimated_reclaim_bytes == 0


def test_executor_revalidates_each_candidate_and_continues_after_failure(tmp_path, monkeypatch):
    logs = tmp_path / "state/cao/logs"
    logs.mkdir(parents=True)
    changed = logs / "cao_changed.log"
    stable = logs / "cao_stable.log"
    changed.write_bytes(b"old")
    stable.write_bytes(b"stable")
    _age(changed, 121)
    _age(stable, 121)
    plan = _plan(tmp_path, monkeypatch, mode="frequent")
    changed.write_bytes(b"changed after planning")

    report = execute_plan(
        plan,
        config=_config(tmp_path),
        open_inventory=lambda: (set(), True),
    )

    assert report.ok is False
    assert changed.exists()
    assert not stable.exists()
    assert any(item["candidate"].endswith("cao_changed.log") for item in report.failures)
    assert any(identity.endswith("cao_stable.log") for identity in report.executed)


def test_ephemeral_directory_is_planned_and_revalidated_before_delete(tmp_path, monkeypatch):
    expired = tmp_path / "tmp/expired"
    expired.mkdir(parents=True)
    (expired / "payload").write_bytes(b"rebuildable")
    (expired / ".cao-ephemeral.json").write_text(
        json.dumps({"version": 1, "expires_at": NOW - 1, "owner_pid": 99999999}),
        encoding="utf-8",
    )
    plan = _plan(tmp_path, monkeypatch, mode="frequent")
    candidate = next(item for item in plan.candidates if item.path == str(expired.resolve()))

    assert candidate.category == "ephemeral"
    assert candidate.action == "delete"
    assert candidate.retention_reason == "expired_marker_dead_owner"

    report = execute_plan(
        plan,
        config=_config(tmp_path),
        settings=default_settings(_config(tmp_path)),
        open_inventory=lambda: (set(), True),
        proc_root=tmp_path / "proc",
    )
    assert report.ok is True
    assert not expired.exists()


def test_ephemeral_marker_change_after_plan_fails_closed(tmp_path, monkeypatch):
    expired = tmp_path / "tmp/expired"
    expired.mkdir(parents=True)
    marker = expired / ".cao-ephemeral.json"
    marker.write_text(
        json.dumps({"version": 1, "expires_at": NOW - 1, "owner_pid": 99999999}),
        encoding="utf-8",
    )
    plan = _plan(tmp_path, monkeypatch, mode="frequent")
    marker.write_text(
        json.dumps({"version": 1, "expires_at": NOW + 3600, "owner_pid": 99999999}),
        encoding="utf-8",
    )

    report = execute_plan(
        plan,
        config=_config(tmp_path),
        settings=default_settings(_config(tmp_path)),
        open_inventory=lambda: (set(), True),
        proc_root=tmp_path / "proc",
    )

    assert expired.exists()
    assert any(item["reason_code"] == "EPHEMERAL_NOT_EXPIRED" for item in report.skipped)


def test_browser_profile_is_revalidated_after_process_termination_wait(tmp_path, monkeypatch):
    profile = tmp_path / "playwright-profile"
    profile.mkdir()
    payload = profile / "state"
    payload.write_text("planned", encoding="utf-8")
    planned_fingerprint = candidate_fingerprint(profile)[0]
    candidate = HousekeepingCandidate(
        category="ephemeral",
        path="process-group:123",
        canonical_identity="ephemeral:process-group:123",
        fingerprint="a" * 64,
        bytes=payload.stat().st_size,
        estimated_reclaim_bytes=payload.stat().st_size,
        action="terminate",
        retention_reason="expired_marker_dead_owner_orphan",
        resource_kind="browser_process_group",
        attributes=tuple(
            sorted(
                {
                    "pid": "123",
                    "profile": str(profile),
                    "profile_fingerprint": planned_fingerprint,
                }.items()
            )
        ),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping.executor._process_group_pidfds",
        lambda *_args: {123: 99},
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping.executor._pidfd_alive", lambda _fd: False
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping.executor.signal.pidfd_send_signal",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping.executor.os.close", lambda _fd: None
    )
    sleeps = 0

    def mutate_after_termination(_seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps == 1:
            payload.write_text("replacement state", encoding="utf-8")

    with pytest.raises(RuntimeError, match="browser profile fingerprint changed"):
        _execute_resource(
            candidate,
            config={"subprocess_timeout_seconds": 1},
            proc_root=tmp_path / "proc",
            runner=lambda *_args, **_kwargs: None,
            sleeper=mutate_after_termination,
        )

    assert profile.exists()
    assert payload.read_text(encoding="utf-8") == "replacement state"


def test_browser_cache_cleanup_is_reference_aware_and_plan_driven(tmp_path, monkeypatch):
    manifest = tmp_path / "projects/app/node_modules/playwright-core/browsers.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"browsers": [{"revision": "2222"}]}), encoding="utf-8")
    cache = tmp_path / "browser-cache"
    referenced = cache / "chromium-2222"
    stale = cache / "chromium-1111"
    for path in (referenced, stale):
        path.mkdir(parents=True)
        (path / "binary").write_bytes(b"rebuildable")
        _age(path, 300)
    config = _config(tmp_path)
    config.update(
        playwright_browser_cache=str(cache),
        playwright_manifest_roots=[str(tmp_path / "projects")],
    )
    monkeypatch.setattr("cli_agent_orchestrator.clients.database.list_all_terminals", lambda: [])
    settings = default_settings(config)
    settings["policy"]["browser_cache"]["retain_minutes"] = 120
    plan = build_plan(
        root=tmp_path,
        config=config,
        settings=settings,
        mode="weekly",
        now=NOW,
        open_inventory=lambda: (set(), True),
        proc_root=tmp_path / "proc",
    )

    by_name = {
        Path(item.path).name: item for item in plan.candidates if item.category == "browser_cache"
    }
    assert by_name["chromium-2222"].protection_reason == "BROWSER_REVISION_REFERENCED"
    assert by_name["chromium-1111"].action == "delete"

    report = execute_plan(
        plan,
        config=config,
        settings=settings,
        open_inventory=lambda: (set(), True),
        proc_root=tmp_path / "proc",
    )
    assert report.ok is True
    assert referenced.exists()
    assert not stale.exists()


def test_docker_resources_are_identity_fingerprinted_before_execution(tmp_path, monkeypatch):
    config = _config(tmp_path)
    config["runtime_user"] = pwd.getpwuid(os.getuid()).pw_name
    calls = []

    def runner(command, **_kwargs):
        calls.append(command)
        joined = " ".join(command)
        if "ps -a --filter label=cao.ephemeral=true" in joined:
            return SimpleNamespace(returncode=0, stdout="dead-container\n")
        if "volume ls" in joined:
            return SimpleNamespace(returncode=0, stdout="")
        if "State.Running" in joined:
            return SimpleNamespace(returncode=0, stdout="false\n")
        if "inspect" in joined:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "cao.ephemeral": "true",
                        "cao.expires_at": str(NOW - 1),
                        "cao.owner_pid": "99999999",
                    }
                ),
            )
        if command[1:2] == ["rm"]:
            return SimpleNamespace(returncode=0, stdout="")
        raise AssertionError(command)

    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping.planner.shutil.which",
        lambda name: "/usr/bin/docker" if name == "docker" else None,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping.executor.shutil.which",
        lambda name: "/usr/bin/docker" if name == "docker" else None,
    )
    monkeypatch.setattr("cli_agent_orchestrator.clients.database.list_all_terminals", lambda: [])
    plan = build_plan(
        root=tmp_path,
        config=config,
        settings=default_settings(config),
        mode="frequent",
        now=NOW,
        open_inventory=lambda: (set(), True),
        proc_root=tmp_path / "proc",
        runner=runner,
    )
    candidate = next(item for item in plan.candidates if item.resource_kind == "docker_container")
    assert candidate.canonical_identity == "ephemeral:docker:container:dead-container"
    assert len(candidate.fingerprint) == 64

    report = execute_plan(
        plan,
        config=config,
        settings=default_settings(config),
        open_inventory=lambda: (set(), True),
        proc_root=tmp_path / "proc",
        runner=runner,
    )
    assert report.ok is True
    assert candidate.canonical_identity in report.executed
    assert ["/usr/bin/docker", "rm", "dead-container"] in calls


def test_housekeeping_schedule_has_strict_machine_readable_contract(tmp_path):
    settings = default_settings(_config(tmp_path))
    assert validate_settings(settings)["schedule"] == {
        "frequent": "6h",
        "weekly": "Sun 04:00 UTC",
        "pressure": "on_red",
    }
    settings["schedule"]["frequent"] = "whenever convenient"
    try:
        validate_settings(settings)
    except ValueError as error:
        assert "schedule: frequent" in str(error)
    else:
        raise AssertionError("free-form schedules must be rejected")


def test_persisted_schedule_receipts_gate_frequent_and_weekly_ticks(tmp_path):
    schedule = {"frequent": "6h", "weekly": "Sun 04:00 UTC", "pressure": "on_red"}
    assert _scheduled_mode_due(tmp_path, "frequent", schedule, now=NOW) == (True, None)
    _write_schedule_receipt(tmp_path, "frequent", NOW)
    assert _scheduled_mode_due(tmp_path, "frequent", schedule, now=NOW + 5 * 3600) == (
        False,
        None,
    )
    assert _scheduled_mode_due(tmp_path, "frequent", schedule, now=NOW + 6 * 3600) == (
        True,
        None,
    )

    sunday_0400 = 2_000_347_200.0  # 2033-05-22 04:00:00 UTC
    assert _scheduled_mode_due(tmp_path, "weekly", schedule, now=sunday_0400) == (True, None)
    _write_schedule_receipt(tmp_path, "weekly", sunday_0400)
    assert _scheduled_mode_due(tmp_path, "weekly", schedule, now=sunday_0400 + 86400) == (
        False,
        None,
    )
    assert _scheduled_mode_due(tmp_path, "weekly", schedule, now=sunday_0400 + 7 * 86400) == (
        True,
        None,
    )


def _release(root: Path, name: str, *, minutes: int):
    release = root / "tools" / name
    release.mkdir(parents=True)
    (release / "payload").write_text(name, encoding="utf-8")
    (release / ".threadcells-release.json").write_text(
        json.dumps({"schema_version": 1, "release_id": name, "source_commit": "a" * 40}),
        encoding="utf-8",
    )
    _age(release / "payload", minutes)
    _age(release / ".threadcells-release.json", minutes)
    _age(release, minutes)
    return release


def test_release_gc_preserves_active_rollback_and_backups_and_removes_only_stale_marker_owned(
    tmp_path, monkeypatch
):
    active = _release(tmp_path, "active", minutes=300)
    rollback = _release(tmp_path, "rollback", minutes=301)
    stale = _release(tmp_path, "stale", minutes=302)
    backup = tmp_path / "backups/database.sqlite"
    backup.parent.mkdir(parents=True)
    backup.write_bytes(b"never delete")
    metadata = tmp_path / "state/cao/release-metadata.json"
    metadata.parent.mkdir(parents=True)
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
    monkeypatch.setattr("cli_agent_orchestrator.clients.database.list_all_terminals", lambda: [])
    config = _config(tmp_path)
    settings = default_settings(config)
    settings["policy"]["releases"]["retain_count"] = 1
    plan = build_plan(
        root=tmp_path,
        config=config,
        settings=settings,
        mode="weekly",
        now=NOW,
        open_inventory=lambda: (set(), True),
    )

    by_name = {Path(item.path).name: item for item in plan.candidates}
    assert by_name["active"].protection_reason == "ACTIVE_OR_ROLLBACK_RELEASE"
    assert by_name["rollback"].protection_reason == "ACTIVE_OR_ROLLBACK_RELEASE"
    assert by_name["stale"].action == "delete"
    assert by_name["backups"].protection_reason == "BACKUP_PROTECTED"

    report = execute_plan(plan, config=config, open_inventory=lambda: (set(), True))
    assert report.ok is True
    assert active.exists() and rollback.exists() and backup.exists()
    assert not stale.exists()


def test_unknown_release_metadata_fails_closed(tmp_path, monkeypatch):
    stale = _release(tmp_path, "stale", minutes=302)

    plan = _plan(tmp_path, monkeypatch)
    candidate = next(item for item in plan.candidates if Path(item.path).name == "stale")

    assert candidate.action == "preserve"
    assert candidate.protection_reason == "RELEASE_METADATA_UNKNOWN"
    assert stale.exists()


def test_release_gc_accepts_only_configured_release_roots_outside_state_root(tmp_path, monkeypatch):
    state_root = tmp_path / "state-root"
    external = tmp_path / "release-store"
    newest = _release(external, "newest", minutes=200)
    stale = _release(external, "stale", minutes=300)
    config = _config(state_root)
    config["release_roots"] = [str(external / "tools")]
    metadata = Path(config["release_metadata"])
    metadata.parent.mkdir(parents=True)
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
    monkeypatch.setattr("cli_agent_orchestrator.clients.database.list_all_terminals", lambda: [])
    settings = default_settings(config)
    settings["policy"]["releases"]["retain_count"] = 1
    plan = build_plan(
        root=state_root,
        config=config,
        settings=settings,
        mode="weekly",
        now=NOW,
        open_inventory=lambda: (set(), True),
    )

    report = execute_plan(plan, config=config, open_inventory=lambda: (set(), True))

    assert report.ok is True
    assert newest.exists()
    assert not stale.exists()


def test_busy_release_staging_lock_does_not_block_independent_log_cleanup(tmp_path, monkeypatch):
    log = tmp_path / "state/cao/logs/cao_stale.log"
    log.parent.mkdir(parents=True)
    log.write_bytes(b"stale log")
    _age(log, 300)
    _release(tmp_path, "newest", minutes=200)
    stale = _release(tmp_path, "stale", minutes=300)
    config = _config(tmp_path)
    metadata = Path(config["release_metadata"])
    metadata.parent.mkdir(parents=True, exist_ok=True)
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
    monkeypatch.setattr("cli_agent_orchestrator.clients.database.list_all_terminals", lambda: [])
    settings = default_settings(config)
    settings["policy"]["releases"]["retain_count"] = 1
    plan = build_plan(
        root=tmp_path,
        config=config,
        settings=settings,
        mode="weekly",
        now=NOW,
        open_inventory=lambda: (set(), True),
    )
    lock_path = Path(config["release_staging_lock"])
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report = execute_plan(plan, config=config, open_inventory=lambda: (set(), True))

    assert report.ok is False
    assert not log.exists()
    assert stale.exists()
    assert any(item["reason_code"] == "RELEASE_STAGING_BUSY" for item in report.failures)


def test_missing_release_authority_preserves_releases_without_blocking_log_cleanup(
    tmp_path, monkeypatch
):
    log = tmp_path / "state/cao/logs/cao_stale.log"
    log.parent.mkdir(parents=True)
    log.write_bytes(b"stale log")
    _age(log, 300)
    _release(tmp_path, "newest", minutes=200)
    stale = _release(tmp_path, "stale", minutes=300)
    config = _config(tmp_path)
    metadata = Path(config["release_metadata"])
    metadata.parent.mkdir(parents=True, exist_ok=True)
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
    monkeypatch.setattr("cli_agent_orchestrator.clients.database.list_all_terminals", lambda: [])
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping.executor.grp.getgrnam",
        lambda _name: SimpleNamespace(gr_gid=max({os.getegid(), *os.getgroups()}) + 1),
    )
    settings = default_settings(config)
    settings["policy"]["releases"]["retain_count"] = 1
    plan = build_plan(
        root=tmp_path,
        config=config,
        settings=settings,
        mode="weekly",
        now=NOW,
        open_inventory=lambda: (set(), True),
    )

    report = execute_plan(plan, config=config, open_inventory=lambda: (set(), True))

    assert report.ok is False
    assert not log.exists()
    assert stale.exists()
    assert {item["reason_code"] for item in report.failures} == {"RELEASE_ADMIN_GROUP_REQUIRED"}
    assert any(item["reason_code"] == "RELEASE_ADMIN_GROUP_REQUIRED" for item in report.skipped)
