import fcntl
import grp
import json
import os
import pwd
from pathlib import Path
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.services.housekeeping import executor as housekeeping_executor
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
    plan_housekeeping,
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
    release_lock = root / "locks/release-staging.lock"
    release_lock.parent.mkdir(parents=True, exist_ok=True)
    release_lock.touch(exist_ok=True)
    release_lock.chmod(0o660)
    return {
        "root": str(root),
        "lock_dir": str(root / "locks"),
        "log_compress_after_minutes": 60,
        "retention_minutes": 120,
        "release_roots": [str(root / "tools")],
        "release_metadata": str(root / "state/cao/release-metadata.json"),
        "active_release_link": str(root / "state/cao/active"),
        "release_staging_lock": str(release_lock),
        "release_admin_group": grp.getgrgid(os.getgid()).gr_name,
        "release_control_uid": os.getuid(),
    }


def _age(path: Path, minutes: int):
    os.utime(path, (NOW - minutes * 60, NOW - minutes * 60))


def test_post_validation_path_replacement_is_never_deleted(tmp_path, monkeypatch):
    candidate_path = tmp_path / "state/cao/logs/archive"
    candidate_path.mkdir(parents=True)
    candidate_path.joinpath("original.log").write_text("original", encoding="utf-8")
    fingerprint, size = candidate_fingerprint(candidate_path)
    candidate = HousekeepingCandidate(
        category="logs",
        path=str(candidate_path),
        canonical_identity=f"logs:{candidate_path}",
        fingerprint=fingerprint,
        bytes=size,
        estimated_reclaim_bytes=size,
        action="delete",
        retention_reason="test",
    )
    plan = finalize_plan(
        generated_at=NOW,
        mode="weekly",
        root=tmp_path,
        candidates=[candidate],
        warnings=[],
    )
    original_execute = housekeeping_executor._execute_candidate
    saved_original = tmp_path / "saved-original"

    def replace_after_validation(path, planned, **kwargs):
        path.rename(saved_original)
        path.mkdir()
        path.joinpath("replacement.log").write_text("replacement", encoding="utf-8")
        return original_execute(path, planned, **kwargs)

    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping.executor._execute_candidate",
        replace_after_validation,
    )
    report = execute_plan(
        plan,
        config=_config(tmp_path),
        open_inventory=lambda: (set(), True),
    )

    assert report.ok is False
    assert candidate_path.joinpath("replacement.log").read_text(encoding="utf-8") == "replacement"
    assert saved_original.joinpath("original.log").read_text(encoding="utf-8") == "original"
    assert report.executed == []


def test_post_quarantine_replacement_is_never_deleted(tmp_path, monkeypatch):
    candidate_path = tmp_path / "state/cao/logs/archive"
    candidate_path.mkdir(parents=True)
    candidate_path.joinpath("original.log").write_text("original", encoding="utf-8")
    fingerprint, size = candidate_fingerprint(candidate_path)
    candidate = HousekeepingCandidate(
        category="logs",
        path=str(candidate_path),
        canonical_identity=f"logs:{candidate_path}",
        fingerprint=fingerprint,
        bytes=size,
        estimated_reclaim_bytes=size,
        action="delete",
        retention_reason="test",
    )
    original_fingerprint = housekeeping_executor._descriptor_fingerprint
    saved_original = candidate_path.parent / "saved-captured-original"

    def replace_captured_after_fingerprint(descriptor):
        result = original_fingerprint(descriptor)
        quarantine = next(candidate_path.parent.glob(".threadcells-housekeeping-*"))
        captured = quarantine / "candidate"
        captured.rename(saved_original)
        captured.mkdir()
        captured.joinpath("replacement.log").write_text("replacement", encoding="utf-8")
        return result

    monkeypatch.setattr(
        housekeeping_executor,
        "_descriptor_fingerprint",
        replace_captured_after_fingerprint,
    )

    with pytest.raises(RuntimeError, match="changed after quarantine"):
        housekeeping_executor._execute_candidate(candidate_path, candidate)

    assert saved_original.joinpath("original.log").read_text(encoding="utf-8") == "original"
    quarantine = next(candidate_path.parent.glob(".threadcells-housekeeping-*"))
    assert quarantine.joinpath("candidate/replacement.log").read_text(encoding="utf-8") == (
        "replacement"
    )


def test_descriptor_fingerprint_matches_canonical_global_path_order(tmp_path):
    candidate_path = tmp_path / "artifact"
    candidate_path.joinpath("a").mkdir(parents=True)
    candidate_path.joinpath("a/nested").write_text("nested", encoding="utf-8")
    candidate_path.joinpath("a.txt").write_text("sibling", encoding="utf-8")
    expected_fingerprint, expected_bytes = candidate_fingerprint(candidate_path)
    descriptor = os.open(
        candidate_path,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    try:
        actual_fingerprint, actual_bytes, manifest = housekeeping_executor._descriptor_fingerprint(
            descriptor
        )
    finally:
        os.close(descriptor)

    assert actual_fingerprint == expected_fingerprint
    assert actual_bytes == expected_bytes
    assert set(manifest) == {".", "a", "a/nested", "a.txt"}


def test_post_fingerprint_child_replacement_is_never_deleted(tmp_path, monkeypatch):
    candidate_path = tmp_path / "logs"
    candidate_path.mkdir()
    candidate_path.joinpath("original.log").write_text("original", encoding="utf-8")
    fingerprint, size = candidate_fingerprint(candidate_path)
    candidate = HousekeepingCandidate(
        category="logs",
        path=str(candidate_path),
        canonical_identity=f"logs:{candidate_path}",
        fingerprint=fingerprint,
        bytes=size,
        estimated_reclaim_bytes=size,
        action="delete",
        retention_reason="test",
    )
    original_fingerprint = housekeeping_executor._descriptor_fingerprint
    saved_original = tmp_path / "saved-original.log"

    def replace_child_after_fingerprint(descriptor):
        result = original_fingerprint(descriptor)
        quarantine = next(tmp_path.glob(".threadcells-housekeeping-*"))
        captured_child = quarantine / "candidate/original.log"
        captured_child.rename(saved_original)
        captured_child.write_text("replacement", encoding="utf-8")
        return result

    monkeypatch.setattr(
        housekeeping_executor,
        "_descriptor_fingerprint",
        replace_child_after_fingerprint,
    )

    with pytest.raises(RuntimeError, match="identity changed after fingerprint"):
        housekeeping_executor._execute_candidate(candidate_path, candidate)

    assert saved_original.read_text(encoding="utf-8") == "original"
    quarantine = next(tmp_path.glob(".threadcells-housekeeping-*"))
    assert quarantine.joinpath("candidate/original.log").read_text(encoding="utf-8") == (
        "replacement"
    )


def test_privileged_quarantine_has_no_unprivileged_fallback(tmp_path):
    candidate_path = tmp_path / "logs"
    candidate_path.mkdir()
    candidate_path.joinpath("original.log").write_text("original", encoding="utf-8")
    fingerprint, _size = candidate_fingerprint(candidate_path)

    with pytest.raises(RuntimeError, match="privileged quarantine authority is unavailable"):
        housekeeping_executor._quarantine_and_delete(
            candidate_path,
            fingerprint,
            exclusive_untrusted_uid=os.getuid(),
        )

    quarantine = next(tmp_path.glob(".threadcells-housekeeping-*"))
    assert quarantine.joinpath("candidate/original.log").read_text(encoding="utf-8") == ("original")


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


def test_plan_inventory_uses_the_configured_runtime_owner(tmp_path, monkeypatch):
    config = _config(tmp_path)
    config["runtime_user"] = "agentctl"
    expected = ({tmp_path.resolve()}, True)
    observed = []
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service.get_housekeeping_settings",
        lambda _config: default_settings(config),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service._runtime_open_paths_inventory",
        lambda supplied, proc_root: observed.append((supplied, proc_root)) or expected,
    )

    def fake_build_plan(**kwargs):
        assert kwargs["open_inventory"]() == expected
        return SimpleNamespace(plan_id="a" * 64)

    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping.planner.build_plan", fake_build_plan
    )

    plan = plan_housekeeping(config=config, mode="frequent", proc_root=tmp_path / "proc")

    assert plan.plan_id == "a" * 64
    assert observed == [(config, tmp_path / "proc")]


def test_execution_revalidation_uses_the_configured_runtime_owner(tmp_path, monkeypatch):
    config = _config(tmp_path)
    config["runtime_user"] = "agentctl"
    config["log_tree_warning_gib"] = 5
    config["backup_tree_warning_gib"] = 5
    plan = SimpleNamespace(
        plan_id="a" * 64,
        candidates=(),
        reclaimable_bytes=0,
        warnings=(),
    )
    expected = ({tmp_path.resolve()}, True)
    observed = []
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service.get_housekeeping_settings",
        lambda _config: default_settings(config),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service.plan_housekeeping",
        lambda **_kwargs: plan,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service._runtime_open_paths_inventory",
        lambda supplied, proc_root: observed.append((supplied, proc_root)) or expected,
    )

    def fake_execute_plan(_plan, *, open_inventory, **_kwargs):
        assert open_inventory() == expected
        return SimpleNamespace(ok=True, freed_bytes=0, failures=[], executed=[])

    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping.executor.execute_plan",
        fake_execute_plan,
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

    summary = run_housekeeping(
        config=config,
        dry_run=False,
        mode="frequent",
        proc_root=tmp_path / "proc",
        expected_plan_id=plan.plan_id,
    )

    assert summary.ok is True
    assert observed == [(config, tmp_path / "proc")]


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
    assert candidate.estimated_reclaim_bytes == 0
    assert plan.reclaimable_bytes == 0


def test_marker_unknown_ephemeral_tree_is_shallow_and_not_reported_as_reclaimable(
    tmp_path, monkeypatch
):
    unknown = tmp_path / "tmp/unmarked-build"
    payload = unknown / "nested/payload.bin"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"x" * 1024 * 1024)
    monkeypatch.setattr("cli_agent_orchestrator.clients.database.list_all_terminals", lambda: [])

    plan = build_plan(
        root=tmp_path,
        config=_config(tmp_path),
        settings=default_settings(_config(tmp_path)),
        mode="frequent",
        now=NOW,
        open_inventory=lambda: (set(), True),
    )
    candidate = next(item for item in plan.candidates if item.path == str(unknown.resolve()))

    assert candidate.action == "preserve"
    assert candidate.protection_reason == "EPHEMERAL_MARKER_UNKNOWN"
    assert candidate.bytes == 0
    assert candidate.estimated_reclaim_bytes == 0
    assert plan.reclaimable_bytes == 0


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


def test_unclaimed_retirement_cleanup_reenters_atomic_claim_before_execution(tmp_path, monkeypatch):
    intent = {"version": 1, "terminal_id": "child-unclaimed", "managed": False}
    pending = {
        "parent_terminal_id": "parent-exact",
        "child_terminal_id": "child-unclaimed",
        "claim_token": None,
        "intent": intent,
        "delegation_kind": "assign",
    }
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.list_pending_child_retirement_cleanups",
        lambda: [pending],
    )
    monkeypatch.setattr("cli_agent_orchestrator.clients.database.list_all_terminals", lambda: [])
    claims = []
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.claim_completed_child_retirement",
        lambda parent, child, kind, require_exited_runtime=False: claims.append(
            (parent, child, kind, require_exited_runtime)
        )
        or {"eligible": True, "claim_token": "recovered-claim"},
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.get_child_retirement_cleanup_intent",
        lambda child, token: {
            "intent": intent,
            "cleanup_completed": False,
            "claim_token": token,
        },
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

    assert dict(candidate.attributes)["stage"] == "unclaimed"
    assert not any(
        warning.startswith("retirement_cleanup_claim_unknown:") for warning in plan.warnings
    )
    report = execute_plan(plan, config=config, open_inventory=lambda: (set(), True))
    assert report.ok is True
    assert claims == [("parent-exact", "child-unclaimed", "assign", True)]
    assert completed == [("child-unclaimed", "recovered-claim", intent, "assign")]


def test_unclaimed_retirement_cleanup_without_exact_parent_remains_preserved(tmp_path, monkeypatch):
    pending = {
        "child_terminal_id": "child-unknown",
        "claim_token": None,
        "intent": {"version": 1, "terminal_id": "child-unknown", "managed": False},
        "delegation_kind": "assign",
    }
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.list_pending_child_retirement_cleanups",
        lambda: [pending],
    )
    monkeypatch.setattr("cli_agent_orchestrator.clients.database.list_all_terminals", lambda: [])
    config = _config(tmp_path)

    plan = build_plan(
        root=tmp_path,
        config=config,
        settings=default_settings(config),
        mode="frequent",
        now=NOW,
        open_inventory=lambda: (set(), True),
    )

    assert not [item for item in plan.candidates if item.resource_kind == "retirement_cleanup"]
    assert "retirement_cleanup_claim_unknown:child-unknown" in plan.warnings


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


def test_scheduled_housekeeping_treats_an_active_canonical_run_as_a_safe_skip(monkeypatch, capsys):
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service.run_housekeeping",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("HOUSEKEEPING_BUSY")),
    )

    assert housekeeping_main(["--mode", "weekly", "--scheduled"]) == 0
    assert capsys.readouterr().out.strip() == "HOUSEKEEPING_SKIPPED reason=HOUSEKEEPING_BUSY"


def test_manual_housekeeping_keeps_lock_contention_as_a_hard_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service.run_housekeeping",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("HOUSEKEEPING_BUSY")),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service.load_operations_config",
        lambda: (_ for _ in ()).throw(RuntimeError("config unavailable")),
    )

    assert housekeeping_main(["--mode", "weekly", "--plan-id", "a" * 64]) == 1
    assert "HOUSEKEEPING_FAILED error=RuntimeError:HOUSEKEEPING_BUSY" in capsys.readouterr().out


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


def test_protected_byte_drift_does_not_churn_plan_but_actionability_does(tmp_path):
    protected = HousekeepingCandidate(
        category="tools",
        path=str(tmp_path / "tools"),
        canonical_identity="tools:inventory",
        fingerprint="a" * 64,
        bytes=100,
        estimated_reclaim_bytes=0,
        action="preserve",
        retention_reason="inventory_only",
        protection_reason="TOOLS_RETENTION_AUTHORITY_UNKNOWN",
        resource_kind="inventory",
    )
    first = finalize_plan(
        generated_at=NOW,
        mode="pressure",
        root=tmp_path,
        candidates=[protected],
        warnings=[],
    )
    drifted = finalize_plan(
        generated_at=NOW + 1,
        mode="pressure",
        root=tmp_path,
        candidates=[
            HousekeepingCandidate(**{**protected.as_dict(), "fingerprint": "b" * 64, "bytes": 200})
        ],
        warnings=[],
    )
    actionable = finalize_plan(
        generated_at=NOW + 1,
        mode="pressure",
        root=tmp_path,
        candidates=[
            HousekeepingCandidate(
                **{
                    **protected.as_dict(),
                    "action": "retire",
                    "protection_reason": None,
                    "estimated_reclaim_bytes": 200,
                }
            )
        ],
        warnings=[],
    )

    assert drifted.plan_id == first.plan_id
    assert actionable.plan_id != first.plan_id


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
    recent = cache / "chromium-3333"
    for path in (referenced, stale):
        path.mkdir(parents=True)
        (path / "binary").write_bytes(b"rebuildable")
        _age(path, 300)
    recent.mkdir(parents=True)
    (recent / "binary").write_bytes(b"rebuildable")
    _age(recent, 30)
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
    assert by_name["chromium-3333"].protection_reason == "BROWSER_WITHIN_RETENTION"

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
    Path(config["active_release_link"]).symlink_to(active, target_is_directory=True)
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
    assert by_name["active"].protection_reason == "ACTIVE_RELEASE"
    assert by_name["rollback"].protection_reason == "CANONICAL_ROLLBACK_RELEASE"
    assert by_name["stale"].action == "delete"
    assert by_name["backups"].protection_reason == "BACKUP_PROTECTED"

    report = execute_plan(plan, config=config, open_inventory=lambda: (set(), True))
    assert report.ok is True
    assert active.exists() and rollback.exists() and backup.exists()
    assert not stale.exists()


def test_full_cleanup_preserves_only_active_release_and_reconciles_metadata(tmp_path, monkeypatch):
    active = _release(tmp_path, "active", minutes=300)
    rollback = _release(tmp_path, "rollback", minutes=301)
    recovery = _release(tmp_path, "recovery", minutes=302)
    stale = _release(tmp_path, "stale", minutes=303)
    config = _config(tmp_path)
    metadata = Path(config["release_metadata"])
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "active_release": str(active.resolve()),
                "rollback_releases": [
                    str(rollback.resolve()),
                    str(recovery.resolve()),
                ],
                "candidate_releases": [str(stale.resolve())],
            }
        ),
        encoding="utf-8",
    )
    Path(config["active_release_link"]).symlink_to(active, target_is_directory=True)
    monkeypatch.setattr("cli_agent_orchestrator.clients.database.list_all_terminals", lambda: [])
    settings = default_settings(config)
    settings["policy"]["releases"]["enabled"] = False

    plan = build_plan(
        root=tmp_path,
        config=config,
        settings=settings,
        mode="full",
        now=NOW,
        open_inventory=lambda: (set(), True),
    )
    by_name = {
        Path(item.path).name: item for item in plan.candidates if item.category == "releases"
    }
    assert by_name["active"].protection_reason == "ACTIVE_RELEASE"
    assert all(by_name[name].action == "delete" for name in ("rollback", "recovery", "stale"))

    report = execute_plan(
        plan,
        config=config,
        settings=settings,
        open_inventory=lambda: (set(), True),
        full_cleanup=True,
        lifecycle_fence_held=True,
    )

    assert report.ok is True
    assert report.active_release == str(active.resolve())
    assert report.rollback_available is False
    assert active.is_dir()
    assert Path(config["active_release_link"]).resolve(strict=True) == active.resolve()
    assert not rollback.exists() and not recovery.exists() and not stale.exists()
    assert json.loads(metadata.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "active_release": str(active.resolve()),
        "rollback_releases": [],
        "candidate_releases": [],
    }

    replay = build_plan(
        root=tmp_path,
        config=config,
        settings=settings,
        mode="full",
        now=NOW,
        open_inventory=lambda: (set(), True),
    )
    second = execute_plan(
        replay,
        config=config,
        settings=settings,
        open_inventory=lambda: (set(), True),
        full_cleanup=True,
        lifecycle_fence_held=True,
    )
    assert second.ok is True
    assert second.rollback_available is False
    assert active.is_dir()


def test_full_cleanup_replay_still_requires_release_staging_lock(tmp_path, monkeypatch):
    active = _release(tmp_path, "active", minutes=300)
    config = _config(tmp_path)
    metadata = Path(config["release_metadata"])
    metadata.parent.mkdir(parents=True, exist_ok=True)
    original = {
        "schema_version": 1,
        "active_release": str(active.resolve()),
        "rollback_releases": [],
        "candidate_releases": [],
    }
    metadata.write_text(json.dumps(original), encoding="utf-8")
    Path(config["active_release_link"]).symlink_to(active, target_is_directory=True)
    monkeypatch.setattr("cli_agent_orchestrator.clients.database.list_all_terminals", lambda: [])
    settings = default_settings(config)
    plan = build_plan(
        root=tmp_path,
        config=config,
        settings=settings,
        mode="full",
        now=NOW,
        open_inventory=lambda: (set(), True),
    )
    assert not [
        item for item in plan.candidates if item.category == "releases" and item.action == "delete"
    ]

    with Path(config["release_staging_lock"]).open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report = execute_plan(
            plan,
            config=config,
            settings=settings,
            open_inventory=lambda: (set(), True),
            full_cleanup=True,
            lifecycle_fence_held=True,
        )

    assert report.ok is False
    assert report.active_release is None
    assert report.rollback_available is None
    assert json.loads(metadata.read_text(encoding="utf-8")) == original
    assert any(item["reason_code"] == "RELEASE_STAGING_BUSY" for item in report.failures)


def test_active_link_target_remains_protected_after_candidate_metadata_eviction(
    tmp_path, monkeypatch
):
    linked_active = _release(tmp_path, "linked-active", minutes=400)
    newer_one = _release(tmp_path, "newer-one", minutes=300)
    newer_two = _release(tmp_path, "newer-two", minutes=200)
    stale = _release(tmp_path, "stale", minutes=500)
    config = _config(tmp_path)
    metadata = Path(config["release_metadata"])
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "active_release": None,
                "rollback_releases": [],
                "candidate_releases": [str(newer_two.resolve()), str(newer_one.resolve())],
            }
        ),
        encoding="utf-8",
    )
    Path(config["active_release_link"]).symlink_to(linked_active, target_is_directory=True)
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
    by_name = {Path(item.path).name: item for item in plan.candidates}

    assert by_name["linked-active"].protection_reason == "ACTIVE_RELEASE"
    assert by_name["newer-one"].protection_reason == "CANDIDATE_RELEASE"
    assert by_name["newer-two"].protection_reason == "CANDIDATE_RELEASE"
    assert by_name["stale"].action == "delete"
    assert "active_release_metadata_diverged" in plan.warnings


def test_unknown_release_metadata_fails_closed(tmp_path, monkeypatch):
    stale = _release(tmp_path, "stale", minutes=302)

    plan = _plan(tmp_path, monkeypatch)
    candidate = next(item for item in plan.candidates if Path(item.path).name == "stale")

    assert candidate.action == "preserve"
    assert candidate.protection_reason == "RELEASE_METADATA_UNKNOWN"
    assert stale.exists()


def test_release_metadata_with_untrusted_owner_identity_fails_closed(tmp_path, monkeypatch):
    stale = _release(tmp_path, "stale", minutes=302)
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
    config["release_control_uid"] = os.getuid() + 1
    monkeypatch.setattr("cli_agent_orchestrator.clients.database.list_all_terminals", lambda: [])

    plan = build_plan(
        root=tmp_path,
        config=config,
        settings=default_settings(config),
        mode="weekly",
        now=NOW,
        open_inventory=lambda: (set(), True),
    )
    candidate = next(item for item in plan.candidates if Path(item.path).name == "stale")

    assert candidate.action == "preserve"
    assert candidate.protection_reason == "RELEASE_METADATA_UNKNOWN"
    assert "release_metadata_inventory_uncertain" in plan.warnings
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


def test_replaced_release_lock_is_rejected_without_blocking_log_cleanup(tmp_path, monkeypatch):
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
    lock = Path(config["release_staging_lock"])
    outside = tmp_path / "outside-release-lock"
    outside.write_text("do not mutate", encoding="utf-8")
    lock.unlink()
    lock.symlink_to(outside)

    report = execute_plan(plan, config=config, open_inventory=lambda: (set(), True))

    assert report.ok is False
    assert not log.exists()
    assert stale.exists()
    assert outside.read_text(encoding="utf-8") == "do not mutate"
    assert any(item["reason_code"] == "RELEASE_STAGING_LOCK_INVALID" for item in report.failures)


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
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping.executor.grp.getgrnam",
        lambda _name: SimpleNamespace(gr_gid=max({os.getegid(), *os.getgroups()}) + 1),
    )

    report = execute_plan(plan, config=config, open_inventory=lambda: (set(), True))

    assert report.ok is False
    assert not log.exists()
    assert stale.exists()
    assert {item["reason_code"] for item in report.failures} == {"RELEASE_ADMIN_GROUP_REQUIRED"}
    assert any(item["reason_code"] == "RELEASE_ADMIN_GROUP_REQUIRED" for item in report.skipped)
