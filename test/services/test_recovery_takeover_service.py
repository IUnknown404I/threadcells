from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Lock
from time import sleep
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cli_agent_orchestrator.clients.tmux import TmuxClient
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.services import recovery_takeover_service as service


def _metadata(command: str = "bash"):
    return {
        "id": "a11ce001",
        "tmux_session": "cao-old",
        "tmux_window": "old-window",
        "runtime_pane_id": "%1",
        "runtime_pane_pid": 1234,
        "runtime_generation": "11111111-1111-4111-8111-111111111111",
        "runtime_generation_origin": "launch",
        "runtime_process_start_ticks": 5678,
        "runtime_process_group_id": 1234,
        "runtime_process_session_id": 1234,
        "_command": command,
    }


def _target(command: str):
    return SimpleNamespace(
        terminal_id="a11ce001",
        pane_id="%1",
        pane_pid=1234,
        runtime_generation="11111111-1111-4111-8111-111111111111",
        process_start_ticks=5678,
        process_group_id=1234,
        process_session_id=1234,
        generation_inherited=True,
        current_command=command,
    )


def test_ready_or_processing_runtime_is_not_takeover_evidence(monkeypatch):
    monkeypatch.setattr(service.tmux_client, "window_exists", lambda *_args: True)
    monkeypatch.setattr(
        service.tmux_client, "exact_runtime_target", lambda *_args: _target("codex")
    )
    assert service._physical_runtime_absence(_metadata("codex")) == (
        False,
        "RECOVERY_HEALTHY_RUNTIME_ACTIVE",
    )


def test_exact_current_generation_shell_proves_runtime_absence(monkeypatch):
    monkeypatch.setattr(service.tmux_client, "window_exists", lambda *_args: True)
    monkeypatch.setattr(service.tmux_client, "exact_runtime_target", lambda *_args: _target("bash"))
    assert service._physical_runtime_absence(_metadata()) == (True, None)


def test_exact_idle_runtime_is_retired_before_writer_transfer(monkeypatch):
    target = _target("bash")
    retired = []
    monkeypatch.setattr(service.tmux_client, "window_exists", lambda *_args: True)
    monkeypatch.setattr(service.tmux_client, "exact_runtime_target", lambda *_args: target)
    monkeypatch.setattr(
        service.tmux_client,
        "retire_runtime_pane",
        lambda observed: retired.append(observed) or True,
    )
    monkeypatch.setattr(
        service, "_runtime_process_tree_absent", lambda *_args, **_kwargs: (True, None)
    )
    assert service._retire_recovery_runtime(_metadata()) == (True, None)
    assert retired == [target]


def test_missing_tmux_window_does_not_override_live_process_tree(monkeypatch, tmp_path):
    child = tmp_path / "4321"
    child.mkdir()
    fields = ["S", "1", "1234", "1234", *(["0"] * 15), "8765"]
    (child / "stat").write_text(f"4321 (orphaned provider) {' '.join(fields)}\n", encoding="utf-8")
    monkeypatch.setattr(service.tmux_client, "window_exists", lambda *_args: False)

    assert service._physical_runtime_absence(_metadata(), proc_root=tmp_path) == (
        False,
        "RECOVERY_RUNTIME_PROCESS_TREE_ACTIVE",
    )


def test_real_libtmux_absence_keeps_recovery_required_capability_eligible(monkeypatch):
    from libtmux._internal.query_list import ObjectDoesNotExist

    terminal = {
        **_metadata(),
        "runtime_lifecycle": "recovery_required",
        "launch_worktree": "/managed/recovery-worktree",
    }
    presence = TmuxClient()
    presence.server = Mock()
    presence.server.sessions.get.side_effect = ObjectDoesNotExist("cao-old")
    presence.server.cmd.return_value.returncode = 0
    presence.server.cmd.return_value.stderr = []
    presence.server.cmd.return_value.stdout = []
    monkeypatch.setattr(service, "tmux_client", presence)
    monkeypatch.setattr(
        service,
        "recovery_takeover_durable_eligibility",
        lambda *_args, **_kwargs: {
            "eligible": True,
            "reason_code": None,
            "terminal": terminal,
        },
    )
    monkeypatch.setattr(
        service,
        "_runtime_process_tree_absent",
        lambda *_args, **_kwargs: (True, None),
    )
    monkeypatch.setattr(
        service,
        "_worktree_snapshot",
        lambda *_args: {"state": "clean", "dirty": False, "reason_code": None},
    )

    first = service._recovery_takeover_capability("a11ce001")
    second = service._recovery_takeover_capability("a11ce001")

    assert first["eligible"] is True
    assert second["eligible"] is True
    assert first["runtime_absent"] is second["runtime_absent"] is True


def test_generation_mismatch_fails_closed(monkeypatch):
    wrong = _target("bash")
    wrong.runtime_generation = "22222222-2222-4222-8222-222222222222"
    monkeypatch.setattr(service.tmux_client, "window_exists", lambda *_args: True)
    monkeypatch.setattr(service.tmux_client, "exact_runtime_target", lambda *_args: wrong)
    assert service._physical_runtime_absence(_metadata()) == (
        False,
        "RECOVERY_RUNTIME_AUTHORITY_AMBIGUOUS",
    )


def test_capability_projection_reuses_preview_and_omits_authority_details(monkeypatch):
    previews = {
        "a11ce001": {
            "eligible": True,
            "reason_code": None,
            "terminal": {"launch_worktree": "/private/worktree", "runtime_generation": "secret"},
        },
        "b22ce001": {
            "eligible": False,
            "reason_code": "RECOVERY_HEALTHY_RUNTIME_ACTIVE",
            "terminal": {"launch_worktree": "/private/other"},
        },
    }
    monkeypatch.setattr(
        service,
        "_recovery_takeover_capability",
        lambda terminal_id, **_kwargs: previews[terminal_id],
    )

    assert service.list_recovery_takeover_capabilities(["a11ce001", "b22ce001"]) == {
        "capabilities": [
            {"terminal_id": "a11ce001", "eligible": True, "reason_code": None},
            {
                "terminal_id": "b22ce001",
                "eligible": False,
                "reason_code": "RECOVERY_HEALTHY_RUNTIME_ACTIVE",
            },
        ]
    }


def test_capability_projection_fails_closed_when_inventory_raises(monkeypatch):
    monkeypatch.setattr(
        service,
        "_recovery_takeover_capability",
        lambda _terminal_id, **_kwargs: (_ for _ in ()).throw(OSError("inventory unavailable")),
    )

    assert service.list_recovery_takeover_capabilities(["a11ce001"]) == {
        "capabilities": [
            {
                "terminal_id": "a11ce001",
                "eligible": False,
                "reason_code": "RECOVERY_ELIGIBILITY_UNAVAILABLE",
            }
        ]
    }


def test_capability_fast_path_does_not_probe_runtime_for_durable_rejection(monkeypatch):
    monkeypatch.setattr(
        service,
        "recovery_takeover_durable_eligibility",
        lambda *_args, **_kwargs: {
            "eligible": False,
            "reason_code": "RECOVERY_TARGET_NOT_TAKEOVER_ELIGIBLE",
            "terminal": {"id": "a11ce001"},
        },
    )
    monkeypatch.setattr(
        service,
        "_physical_runtime_absence",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("durably blocked capability must not inspect runtime")
        ),
    )

    result = service._recovery_takeover_capability("a11ce001")
    assert result["eligible"] is False
    assert result["reason_code"] == "RECOVERY_TARGET_NOT_TAKEOVER_ELIGIBLE"


def test_capability_fast_path_does_not_scan_worktree_for_active_runtime(monkeypatch):
    monkeypatch.setattr(
        service,
        "recovery_takeover_durable_eligibility",
        lambda *_args, **_kwargs: {
            "eligible": True,
            "reason_code": None,
            "terminal": {"id": "a11ce001"},
        },
    )
    monkeypatch.setattr(
        service,
        "_physical_runtime_absence",
        lambda *_args, **_kwargs: (False, "RECOVERY_HEALTHY_RUNTIME_ACTIVE"),
    )
    monkeypatch.setattr(
        service,
        "_worktree_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("active runtime must block before Git inventory")
        ),
    )

    result = service._recovery_takeover_capability("a11ce001")
    assert result["eligible"] is False
    assert result["reason_code"] == "RECOVERY_HEALTHY_RUNTIME_ACTIVE"


def test_capability_positive_path_defers_to_canonical_preview(monkeypatch):
    terminal = {"id": "a11ce001"}
    monkeypatch.setattr(
        service,
        "recovery_takeover_durable_eligibility",
        lambda *_args, **_kwargs: {
            "eligible": True,
            "reason_code": None,
            "terminal": terminal,
        },
    )
    monkeypatch.setattr(
        service,
        "_physical_runtime_absence",
        lambda candidate: (candidate is terminal, "RECOVERY_RUNTIME_PRESENT"),
    )
    preview = {
        "eligible": True,
        "reason_code": None,
        "terminal": {"id": "a11ce001", "writer_authority_generation": "writer-2"},
    }
    canonical_preview = Mock(return_value=preview)
    monkeypatch.setattr(service, "preview_recovery_takeover", canonical_preview)

    assert service._recovery_takeover_capability("a11ce001") is preview
    canonical_preview.assert_called_once_with("a11ce001")


def test_invalid_owner_grant_cannot_retire_old_runtime(monkeypatch):
    terminal = {
        **_metadata(),
        "project_id": "project-1",
        "launch_worktree": "/repo",
    }
    monkeypatch.setattr(service, "get_recovery_takeover_by_request_id", lambda _id: None)
    monkeypatch.setattr(
        service,
        "preview_recovery_takeover",
        lambda *_args, **_kwargs: {
            "eligible": True,
            "reason_code": None,
            "terminal": terminal,
        },
    )
    monkeypatch.setattr(
        service,
        "resolve_launch",
        lambda *_args, **_kwargs: SimpleNamespace(
            owner_grant_required=True,
            provider_adapter_id="codex",
            profile_revision_id="profile-revision",
            provider_config_revision_id="provider-revision",
        ),
    )
    monkeypatch.setattr(service, "validate_owner_launch_grant", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        service,
        "_retire_recovery_runtime",
        lambda _metadata: (_ for _ in ()).throw(
            AssertionError("invalid authority must not mutate the old runtime")
        ),
    )
    monkeypatch.setattr(service, "record_recovery_takeover_rejection", lambda **_kwargs: None)
    with pytest.raises(service.RecoveryTakeoverError) as exc:
        service.request_recovery_takeover(
            request_id="request-1",
            old_terminal_id="a11ce001",
            expected_authority_generation="a" * 32,
            expected_runtime_generation=_metadata()["runtime_generation"],
            agent_profile="critical_sol_xhigh_owner",
            provider="codex",
            owner_grant_token="invalid",
            owner_grant_launch_id="launch-1",
        )
    assert exc.value.reason_code == "OWNER_GRANT_INVALID_OR_EXPIRED"


def test_idempotent_request_reconciles_without_reusing_consumed_grant(monkeypatch):
    existing = {
        "id": "takeover-1",
        "old_terminal_id": "a11ce001",
        "expected_authority_generation": "a" * 32,
        "expected_runtime_generation": _metadata()["runtime_generation"],
        "agent_profile": "critical_sol_xhigh_owner",
        "provider": "codex",
    }
    monkeypatch.setattr(service, "get_recovery_takeover_by_request_id", lambda _id: existing)
    monkeypatch.setattr(
        service,
        "reconcile_recovery_takeover",
        lambda takeover_id, registry=None: {"id": takeover_id, "state": "completed"},
    )
    result = service.request_recovery_takeover(
        request_id="request-1",
        old_terminal_id="a11ce001",
        expected_authority_generation="a" * 32,
        expected_runtime_generation=_metadata()["runtime_generation"],
        agent_profile="critical_sol_xhigh_owner",
        provider="codex",
        owner_grant_token="already-consumed",
        owner_grant_launch_id="launch-1",
    )
    assert result == {"id": "takeover-1", "state": "completed"}


def test_idempotent_request_reports_durable_uncertain_dispatch_truthfully(monkeypatch):
    existing = {
        "id": "takeover-1",
        "old_terminal_id": "a11ce001",
        "expected_authority_generation": "a" * 32,
        "expected_runtime_generation": _metadata()["runtime_generation"],
        "agent_profile": "critical_sol_xhigh_owner",
        "provider": "codex",
    }
    monkeypatch.setattr(service, "get_recovery_takeover_by_request_id", lambda _id: existing)
    monkeypatch.setattr(
        service,
        "reconcile_recovery_takeover",
        lambda *_args, **_kwargs: {
            **existing,
            "state": "dispatch_uncertain",
            "failure_reason": "RECOVERY_PROVIDER_DISPATCH_UNCERTAIN",
        },
    )
    with pytest.raises(service.RecoveryTakeoverError) as exc:
        service.request_recovery_takeover(
            request_id="request-1",
            old_terminal_id="a11ce001",
            expected_authority_generation="a" * 32,
            expected_runtime_generation=_metadata()["runtime_generation"],
            agent_profile="critical_sol_xhigh_owner",
            provider="codex",
            owner_grant_token="already-consumed",
            owner_grant_launch_id="launch-1",
        )
    assert exc.value.reason_code == "RECOVERY_PROVIDER_DISPATCH_UNCERTAIN"


def test_request_durably_claims_before_runtime_retirement_and_writer_transfer(monkeypatch):
    terminal = {
        **_metadata(),
        "project_id": "project-1",
        "launch_worktree": "/repo",
    }
    preview = {"eligible": True, "reason_code": None, "terminal": terminal}
    resolution = SimpleNamespace(
        owner_grant_required=True,
        provider_adapter_id="codex",
        profile_revision_id="profile-revision",
        provider_config_revision_id="provider-revision",
    )
    events = []
    monkeypatch.setattr(service, "get_recovery_takeover_by_request_id", lambda _id: None)
    monkeypatch.setattr(service, "preview_recovery_takeover", lambda *_args, **_kwargs: preview)
    monkeypatch.setattr(service, "resolve_launch", lambda *_args, **_kwargs: resolution)
    monkeypatch.setattr(service, "validate_owner_launch_grant", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        service,
        "_retire_recovery_runtime",
        lambda _metadata: (events.append("runtime_retired") or True, None),
    )

    @contextmanager
    def admitted(**_kwargs):
        events.append("admission_locked")
        yield {}

    monkeypatch.setattr(service, "context_lifecycle_fence", admitted)

    def claim(**_kwargs):
        assert events == ["admission_locked"]
        events.append("durable_claimed")
        return {"id": "takeover-1", "state": "claimed"}

    monkeypatch.setattr(service, "claim_recovery_takeover", claim)
    monkeypatch.setattr(
        service,
        "reconcile_recovery_takeover",
        lambda takeover_id, registry=None: (
            events.extend(["runtime_retired", "writer_transferred"])
            or {"id": takeover_id, "state": "completed"}
        ),
    )

    result = service.request_recovery_takeover(
        request_id="request-1",
        old_terminal_id="a11ce001",
        expected_authority_generation="a" * 32,
        expected_runtime_generation=_metadata()["runtime_generation"],
        agent_profile="critical_sol_xhigh_owner",
        provider="codex",
        owner_grant_token="valid",
        owner_grant_launch_id="launch-1",
    )
    assert result["state"] == "completed"
    assert events == [
        "admission_locked",
        "durable_claimed",
        "runtime_retired",
        "writer_transferred",
    ]


def test_dirty_worktree_is_reported_and_preserved(monkeypatch, tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.invalid"], check=True
    )
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    (tmp_path / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "base"], check=True)
    (tmp_path / "recovery-evidence.txt").write_text("preserve me\n", encoding="utf-8")
    terminal = {
        **_metadata(),
        "session_id": "session-1",
        "project_id": "project-1",
        "project_name": "Test",
        "project_path": str(tmp_path),
        "launch_worktree": str(tmp_path),
        "managed_worktree_kind": None,
        "writer_authority_generation": "a" * 32,
        "runtime_lifecycle": "running",
    }
    monkeypatch.setattr(
        service,
        "recovery_takeover_durable_eligibility",
        lambda *_args, **_kwargs: {"eligible": True, "reason_code": None, "terminal": terminal},
    )
    monkeypatch.setattr(service.tmux_client, "window_exists", lambda *_args: False)
    monkeypatch.setattr(
        service, "_runtime_process_tree_absent", lambda *_args, **_kwargs: (True, None)
    )
    preview = service.preview_recovery_takeover("a11ce001")
    assert preview["eligible"] is True
    assert preview["worktree"] == {"state": "dirty", "dirty": True, "reason_code": None}
    assert (tmp_path / "recovery-evidence.txt").read_text(encoding="utf-8") == "preserve me\n"


def test_restart_before_dispatch_launches_exactly_once(monkeypatch):
    takeover = {
        "id": "takeover-1",
        "state": "fenced",
        "canonical_worktree": "/repo",
        "project_id": "project-1",
    }
    launches = []
    monkeypatch.setattr(
        service,
        "get_recovery_takeover",
        lambda _id: {**takeover, "state": "completed" if launches else "fenced"},
    )
    monkeypatch.setattr(
        service,
        "claim_recovery_takeover_dispatch",
        lambda _id: {**takeover, "state": "dispatching"},
    )
    monkeypatch.setattr(
        service, "_launch_claimed_takeover", lambda row, registry=None: launches.append(row["id"])
    )

    @contextmanager
    def admitted(**_kwargs):
        yield {}

    monkeypatch.setattr(service, "context_lifecycle_fence", admitted)
    assert service.reconcile_recovery_takeover("takeover-1")["state"] == "completed"
    assert launches == ["takeover-1"]


def test_restart_after_claim_retires_then_fences_then_dispatches_once(monkeypatch):
    current = {
        "id": "takeover-1",
        "state": "claimed",
        "old_terminal_id": "a11ce001",
        "canonical_worktree": "/repo",
        "project_id": "project-1",
    }
    events = []
    monkeypatch.setattr(service, "get_recovery_takeover", lambda _id: dict(current))
    monkeypatch.setattr(service, "get_terminal_metadata", lambda _id: _metadata())
    monkeypatch.setattr(service, "_physical_runtime_absence", lambda _old: (True, None))
    monkeypatch.setattr(
        service,
        "_retire_recovery_runtime",
        lambda _old: (events.append("runtime_retired") or True, None),
    )

    def fence(_id):
        events.append("writer_fenced")
        current["state"] = "fenced"
        return dict(current)

    def dispatch(_id):
        events.append("dispatch_claimed")
        current["state"] = "dispatching"
        return dict(current)

    def launch(_row, registry=None):
        events.append("provider_launched")
        current["state"] = "completed"

    monkeypatch.setattr(service, "fence_claimed_recovery_takeover", fence)
    monkeypatch.setattr(service, "claim_recovery_takeover_dispatch", dispatch)
    monkeypatch.setattr(service, "_launch_claimed_takeover", launch)

    @contextmanager
    def admitted(**_kwargs):
        yield {}

    monkeypatch.setattr(service, "context_lifecycle_fence", admitted)
    assert service.reconcile_recovery_takeover("takeover-1")["state"] == "completed"
    assert events == [
        "runtime_retired",
        "writer_fenced",
        "dispatch_claimed",
        "provider_launched",
    ]


def test_claim_fails_closed_if_old_runtime_becomes_healthy(monkeypatch):
    takeover = {
        "id": "takeover-1",
        "state": "claimed",
        "old_terminal_id": "a11ce001",
    }
    failed = {**takeover, "state": "failed", "failure_reason": "RECOVERY_HEALTHY_RUNTIME_ACTIVE"}
    monkeypatch.setattr(service, "get_recovery_takeover", lambda _id: takeover)
    monkeypatch.setattr(service, "get_terminal_metadata", lambda _id: _metadata("codex"))
    monkeypatch.setattr(
        service,
        "_physical_runtime_absence",
        lambda _old: (False, "RECOVERY_HEALTHY_RUNTIME_ACTIVE"),
    )
    waits = []
    monkeypatch.setattr(
        service,
        "record_recovery_takeover_claim_wait",
        lambda takeover_id, reason, terminal: (
            waits.append((takeover_id, reason, terminal)) or failed
        ),
    )
    assert service.reconcile_recovery_takeover("takeover-1") == failed
    assert waits == [("takeover-1", "RECOVERY_HEALTHY_RUNTIME_ACTIVE", True)]


def test_restart_after_admission_observes_existing_provider_without_second_launch(monkeypatch):
    takeover = {
        "id": "takeover-1",
        "state": "admitted",
        "canonical_worktree": "/repo",
        "project_id": "project-1",
        "new_terminal_id": "b22ce001",
        "new_session_name": "cao-recovery",
        "new_window_name": "recovery-window",
    }
    new = {
        "id": "b22ce001",
        "runtime_lifecycle": "starting",
        "runtime_pane_id": "%2",
        "runtime_pane_pid": 4321,
        "runtime_generation": "22222222-2222-4222-8222-222222222222",
        "runtime_process_start_ticks": 8765,
        "runtime_process_group_id": 4321,
        "runtime_process_session_id": 4321,
        "writable_work_context_id": "context-a",
        "writer_authority_generation": "writer-generation-a2",
    }
    target = SimpleNamespace(
        terminal_id="b22ce001",
        pane_id="%2",
        pane_pid=4321,
        runtime_generation="22222222-2222-4222-8222-222222222222",
        process_start_ticks=8765,
        process_group_id=4321,
        process_session_id=4321,
        generation_inherited=True,
        current_command="codex",
    )
    provider = SimpleNamespace(
        get_status=lambda: TerminalStatus.PROCESSING,
        initialize=lambda: (_ for _ in ()).throw(AssertionError("must not initialize twice")),
    )
    current = dict(takeover)
    monkeypatch.setattr(service, "get_recovery_takeover", lambda _id: dict(current))
    monkeypatch.setattr(service, "get_terminal_metadata", lambda _id: new)
    monkeypatch.setattr(service.tmux_client, "exact_runtime_target", lambda *_args: target)
    monkeypatch.setattr(service.provider_manager, "get_provider", lambda _id: provider)
    monkeypatch.setattr(service, "mark_terminal_runtime_running", lambda _id: True)
    events = []

    @contextmanager
    def fenced(**_kwargs):
        yield True

    monkeypatch.setattr(service, "context_lifecycle_fence", fenced)

    def complete(takeover_id):
        events.append(("takeover_completed", takeover_id))
        current["state"] = "completed"
        return True

    def admit_context(context_id, **kwargs):
        events.append(("context_admitted", context_id, kwargs))
        return True

    monkeypatch.setattr(service, "transition_writable_work_context", admit_context)
    monkeypatch.setattr(service, "mark_recovery_takeover_completed", complete)
    assert service.reconcile_recovery_takeover("takeover-1")["state"] == "completed"
    assert events == [
        (
            "context_admitted",
            "context-a",
            {
                "expected_states": ("launching", "preserved"),
                "state": "admitted",
                "event_type": "recovery_supervisor_admitted",
                "expected_terminal_id": "b22ce001",
                "expected_writer_authority_generation": "writer-generation-a2",
            },
        ),
        ("takeover_completed", "takeover-1"),
    ]


def test_running_recovery_readmits_context_preserved_by_earlier_daemon_tick(monkeypatch):
    takeover = {
        "id": "takeover-1",
        "new_terminal_id": "b22ce001",
        "new_session_name": "cao-recovery",
        "new_window_name": "recovery-window",
    }
    terminal = {
        "id": "b22ce001",
        "runtime_lifecycle": "running",
        "writable_work_context_id": "context-a",
        "writer_authority_generation": "writer-generation-a2",
    }
    events = []
    monkeypatch.setattr(service, "get_terminal_metadata", lambda _id: terminal)

    def admit_context(context_id, **kwargs):
        events.append(("context_admitted", context_id, kwargs))
        return True

    monkeypatch.setattr(service, "transition_writable_work_context", admit_context)
    monkeypatch.setattr(
        service,
        "mark_recovery_takeover_completed",
        lambda takeover_id: events.append(("takeover_completed", takeover_id)) or True,
    )

    assert service._recover_dispatching_takeover(takeover) is False
    assert events[0] == (
        "context_admitted",
        "context-a",
        {
            "expected_states": ("launching", "preserved"),
            "state": "admitted",
            "event_type": "recovery_supervisor_admitted",
            "expected_terminal_id": "b22ce001",
            "expected_writer_authority_generation": "writer-generation-a2",
        },
    )
    assert events[1] == ("takeover_completed", "takeover-1")


def test_concurrent_admitted_reconcilers_initialize_provider_once(monkeypatch):
    current = {
        "id": "takeover-1",
        "state": "admitted",
        "canonical_worktree": "/repo",
        "project_id": "project-1",
    }
    admission_lock = Lock()
    recoveries = []

    @contextmanager
    def admitted(**_kwargs):
        with admission_lock:
            yield {}

    def recover(_takeover):
        recoveries.append("initialize")
        sleep(0.05)
        current["state"] = "completed"
        return False

    monkeypatch.setattr(service, "context_lifecycle_fence", admitted)
    monkeypatch.setattr(service, "get_recovery_takeover", lambda _id: dict(current))
    monkeypatch.setattr(service, "_recover_dispatching_takeover", recover)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(service.reconcile_recovery_takeover, ["takeover-1"] * 2))

    assert recoveries == ["initialize"]
    assert [result["state"] for result in results] == ["completed", "completed"]


def test_unknown_post_dispatch_state_is_fenced_not_replayed(monkeypatch):
    takeover = {
        "id": "takeover-1",
        "state": "dispatching",
        "new_terminal_id": "b22ce001",
        "new_session_name": "cao-recovery",
        "new_window_name": "recovery-window",
    }
    monkeypatch.setattr(service, "get_terminal_metadata", lambda _id: None)
    monkeypatch.setattr(service.tmux_client, "session_exists", lambda _name: None)
    uncertain = []
    monkeypatch.setattr(
        service,
        "mark_recovery_takeover_dispatch_uncertain",
        lambda takeover_id, reason: uncertain.append((takeover_id, reason)) or True,
    )
    monkeypatch.setattr(
        service,
        "reset_recovery_takeover_after_confirmed_prestart_failure",
        lambda _id: (_ for _ in ()).throw(AssertionError("must not retry")),
    )
    assert service._recover_dispatching_takeover(takeover) is False
    assert uncertain == [("takeover-1", "RECOVERY_PROVIDER_DISPATCH_UNCERTAIN")]


def test_daemon_reconciliation_isolates_one_deferred_takeover(monkeypatch):
    monkeypatch.setattr(
        service,
        "list_reconcilable_recovery_takeovers",
        lambda: [
            {"id": "takeover-blocked", "state": "claimed"},
            {"id": "takeover-ready", "state": "fenced"},
        ],
    )

    def reconcile(takeover_id, registry=None):
        if takeover_id == "takeover-blocked":
            raise RuntimeError("temporary capacity fence")
        return {"id": takeover_id, "state": "completed"}

    monkeypatch.setattr(service, "reconcile_recovery_takeover", reconcile)
    assert service.reconcile_recovery_takeovers() == 1
