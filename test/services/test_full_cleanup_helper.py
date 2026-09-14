import json
import os
import pwd
import socket
import threading
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cli_agent_orchestrator.services.full_cleanup_helper import (
    FullCleanupHelperError,
    _exclusive_inventory_lock,
    _failure_response,
    _handle_request,
    execute_via_privileged_helper,
    inventory_via_privileged_helper,
)
from cli_agent_orchestrator.services.housekeeping.executor import ExecutionReport
from cli_agent_orchestrator.services.housekeeping.models import (
    HousekeepingCandidate,
    HousekeepingPlan,
)
from cli_agent_orchestrator.services.housekeeping_service import HousekeepingSummary


def _completed_operation(operation_id: str, plan_id: str, *, freed_bytes: int = 12):
    return {
        "operation_id": operation_id,
        "plan_id": plan_id,
        "retire_dirty_worktrees": False,
        "state": "completed",
        "report": HousekeepingSummary(
            mode="full",
            full_cleanup=True,
            plan_id=plan_id,
            freed_bytes=freed_bytes,
        ).as_dict(),
        "progress": {},
        "reason_code": None,
        "diagnostic_id": None,
    }


def test_privileged_helper_client_uses_durable_bounded_unix_protocol(
    short_unix_socket_path, monkeypatch
):
    socket_path = short_unix_socket_path
    operation_id = "f" * 32
    plan_id = "a" * 64
    observed = {}
    current = _completed_operation(operation_id, plan_id)
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.admit_full_cleanup_operation",
        lambda *_args, **_kwargs: {**current, "state": "admitted", "report": None, "created": True},
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.get_full_cleanup_operation",
        lambda _operation_id: current,
    )

    def serve():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(socket_path))
            server.listen(1)
            ready.set()
            connection, _address = server.accept()
            with connection:
                observed.update(json.loads(connection.makefile("rb").readline()))
                connection.sendall(
                    json.dumps(
                        {
                            "schema_version": 2,
                            "ok": True,
                            "operation_id": operation_id,
                        }
                    ).encode()
                    + b"\n"
                )

    ready = threading.Event()
    worker = threading.Thread(target=serve)
    worker.start()
    ready.wait(timeout=1)
    result = execute_via_privileged_helper(
        operation_id=operation_id,
        expected_plan_id=plan_id,
        confirmed=True,
        actor_kind="operator_session",
        config={
            "full_cleanup_helper_socket": str(socket_path),
            "full_cleanup_helper_timeout_seconds": 1,
        },
    )
    worker.join(timeout=1)

    assert isinstance(result, HousekeepingSummary)
    assert result.plan_id == plan_id
    assert result.freed_bytes == 12
    assert observed["schema_version"] == 2
    assert observed["operation_id"] == operation_id
    assert observed["expected_plan_id"] == plan_id
    assert observed["confirmed"] is True
    assert observed["retire_dirty_worktrees"] is False
    assert isinstance(observed["operation_token"], str)
    assert len(observed["operation_token"]) >= 32
    assert not any(
        "operator" in key or "bearer" in key or "session_token" in key for key in observed
    )


def test_privileged_inventory_client_uses_pathless_read_only_protocol(
    short_unix_socket_path,
):
    observed = {}
    inventory = {
        "schema_version": 1,
        "roots": [
            {
                "source": "backups",
                "index": -1,
                "present": False,
                "entries": [],
                "entries_certain": True,
            }
        ],
    }

    def serve():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(short_unix_socket_path))
            server.listen(1)
            ready.set()
            connection, _address = server.accept()
            with connection:
                observed.update(json.loads(connection.makefile("rb").readline()))
                connection.sendall(
                    json.dumps(
                        {
                            "schema_version": 2,
                            "ok": True,
                            "operation": "protected_inventory",
                            "inventory": inventory,
                        }
                    ).encode()
                    + b"\n"
                )

    ready = threading.Event()
    worker = threading.Thread(target=serve)
    worker.start()
    ready.wait(timeout=1)
    result = inventory_via_privileged_helper(
        config={
            "full_cleanup_helper_socket": str(short_unix_socket_path),
            "full_cleanup_helper_timeout_seconds": 1,
        }
    )
    worker.join(timeout=1)

    assert result == inventory
    assert observed == {"schema_version": 2, "operation": "protected_inventory"}


def test_privileged_inventory_handler_reads_only_configured_roots(tmp_path, monkeypatch):
    backups = tmp_path / "backups"
    backups.mkdir()
    backups.joinpath("daily.sqlite").write_bytes(b"backup")
    tools = tmp_path / "tools"
    tools.mkdir()
    tools.joinpath("candidate").mkdir()
    tools.joinpath("candidate/payload").write_bytes(b"tool")
    config = {
        "root": str(tmp_path),
        "runtime_user": pwd.getpwuid(os.getuid()).pw_name,
        "protected_inventory_roots": [
            {
                "path": str(tools),
                "category": "tools",
                "reason": "TOOLS_RETENTION_AUTHORITY_UNKNOWN",
            }
        ],
    }
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service._load_legacy_operations_config",
        lambda: config,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.full_cleanup_helper._exclusive_inventory_lock",
        lambda _config: nullcontext(),
    )
    server, client = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    client.sendall(b'{"schema_version":2,"operation":"protected_inventory"}\n')
    client.shutdown(socket.SHUT_WR)
    try:
        response = _handle_request(server)
    finally:
        server.close()
        client.close()

    assert response["ok"] is True
    records = {(item["source"], item["index"]): item for item in response["inventory"]["roots"]}
    assert [item["name"] for item in records[("backups", -1)]["entries"]] == ["daily.sqlite"]
    assert [item["name"] for item in records[("protected", 0)]["entries"]] == ["candidate"]


def test_privileged_inventory_lock_rejects_parallel_root_scan(tmp_path, monkeypatch):
    real_fstat = os.fstat

    def root_owned_fstat(descriptor):
        metadata = real_fstat(descriptor)
        return type(
            "RootOwnedStat",
            (),
            {"st_uid": 0, "st_mode": metadata.st_mode},
        )()

    monkeypatch.setattr(os, "fstat", root_owned_fstat)
    config = {"full_cleanup_helper_socket": str(tmp_path / "full-cleanup.sock")}

    with _exclusive_inventory_lock(config):
        with pytest.raises(FullCleanupHelperError, match="FULL_CLEANUP_INVENTORY_BUSY"):
            with _exclusive_inventory_lock(config):
                pass


def test_privileged_inventory_handler_rejects_caller_selected_path(tmp_path, monkeypatch):
    config = {
        "root": str(tmp_path),
        "runtime_user": pwd.getpwuid(os.getuid()).pw_name,
        "protected_inventory_roots": [],
    }
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service._load_legacy_operations_config",
        lambda: config,
    )
    server, client = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    client.sendall(
        json.dumps(
            {
                "schema_version": 2,
                "operation": "protected_inventory",
                "path": str(tmp_path),
            }
        ).encode()
        + b"\n"
    )
    client.shutdown(socket.SHUT_WR)
    try:
        with pytest.raises(FullCleanupHelperError, match="PROTOCOL_INVALID"):
            _handle_request(server)
    finally:
        server.close()
        client.close()


def test_privileged_helper_client_preserves_safe_diagnostic_id(short_unix_socket_path, monkeypatch):
    socket_path = short_unix_socket_path
    operation_id = "e" * 32
    plan_id = "a" * 64
    failed = {
        **_completed_operation(operation_id, plan_id),
        "state": "failed",
        "report": None,
        "reason_code": "FULL_CLEANUP_HELPER_FAILED",
        "diagnostic_id": "d" * 32,
    }
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.admit_full_cleanup_operation",
        lambda *_args, **_kwargs: {**failed, "state": "admitted", "created": True},
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.get_full_cleanup_operation",
        lambda _operation_id: failed,
    )

    def serve():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(socket_path))
            server.listen(1)
            ready.set()
            connection, _address = server.accept()
            with connection:
                connection.makefile("rb").readline()
                connection.sendall(
                    json.dumps(
                        {
                            "schema_version": 2,
                            "ok": False,
                            "reason_code": "FULL_CLEANUP_HELPER_FAILED",
                            "diagnostic_id": "d" * 32,
                        }
                    ).encode()
                    + b"\n"
                )

    ready = threading.Event()
    worker = threading.Thread(target=serve)
    worker.start()
    ready.wait(timeout=1)
    with pytest.raises(FullCleanupHelperError) as raised:
        execute_via_privileged_helper(
            operation_id=operation_id,
            expected_plan_id=plan_id,
            confirmed=True,
            actor_kind="operator_bearer",
            config={
                "full_cleanup_helper_socket": str(socket_path),
                "full_cleanup_helper_timeout_seconds": 1,
            },
        )
    worker.join(timeout=1)

    assert str(raised.value) == "FULL_CLEANUP_HELPER_FAILED"
    assert raised.value.diagnostic_id == "d" * 32


def test_client_recovers_committed_report_when_helper_disconnects(
    short_unix_socket_path, monkeypatch
):
    socket_path = short_unix_socket_path
    operation_id = "c" * 32
    plan_id = "d" * 64
    current = {
        **_completed_operation(operation_id, plan_id, freed_bytes=77),
        "state": "admitted",
        "report": None,
    }
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.admit_full_cleanup_operation",
        lambda *_args, **_kwargs: {**current, "created": True},
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.get_full_cleanup_operation",
        lambda _operation_id: current,
    )

    def serve():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(socket_path))
            server.listen(1)
            ready.set()
            connection, _address = server.accept()
            with connection:
                connection.makefile("rb").readline()
                current.update(_completed_operation(operation_id, plan_id, freed_bytes=77))
                # Model a browser/service transport loss after the helper's
                # canonical DB commit but before its response is observable.

    ready = threading.Event()
    worker = threading.Thread(target=serve)
    worker.start()
    ready.wait(timeout=1)
    result = execute_via_privileged_helper(
        operation_id=operation_id,
        expected_plan_id=plan_id,
        confirmed=True,
        actor_kind="operator_session",
        config={
            "full_cleanup_helper_socket": str(socket_path),
            "full_cleanup_helper_timeout_seconds": 1,
        },
    )
    worker.join(timeout=1)

    assert result.freed_bytes == 77
    assert result.plan_id == plan_id


def test_client_terminalizes_unclaimed_operation_after_invalid_response(
    short_unix_socket_path, monkeypatch
):
    socket_path = short_unix_socket_path
    operation_id = "b" * 32
    plan_id = "e" * 64
    current = {
        **_completed_operation(operation_id, plan_id),
        "state": "admitted",
        "report": None,
    }
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.admit_full_cleanup_operation",
        lambda *_args, **_kwargs: {**current, "created": True},
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.get_full_cleanup_operation",
        lambda _operation_id: current,
    )

    def terminalize(_operation_id, *, reason_code, indeterminate, **_kwargs):
        current.update(state="failed", reason_code=reason_code)
        assert indeterminate is False
        return True

    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.terminalize_full_cleanup_operation",
        terminalize,
    )

    def serve():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(socket_path))
            server.listen(1)
            ready.set()
            connection, _address = server.accept()
            with connection:
                connection.makefile("rb").readline()
                connection.sendall(b"not-json\n")

    ready = threading.Event()
    worker = threading.Thread(target=serve)
    worker.start()
    ready.wait(timeout=1)
    with pytest.raises(FullCleanupHelperError, match="FULL_CLEANUP_HELPER_RESPONSE_INVALID"):
        execute_via_privileged_helper(
            operation_id=operation_id,
            expected_plan_id=plan_id,
            confirmed=True,
            actor_kind="operator_session",
            config={
                "full_cleanup_helper_socket": str(socket_path),
                "full_cleanup_helper_timeout_seconds": 1,
            },
        )
    worker.join(timeout=1)

    assert current["state"] == "failed"


def test_unexpected_helper_failure_gets_journal_diagnostic_id(capsys):
    response = _failure_response(PermissionError(13, "denied", "/protected/resource"))

    diagnostic_id = response["diagnostic_id"]
    assert isinstance(diagnostic_id, str)
    assert len(diagnostic_id) == 32
    assert response == {
        "schema_version": 2,
        "ok": False,
        "reason_code": "FULL_CLEANUP_HELPER_FAILED",
        "diagnostic_id": diagnostic_id,
    }
    journal = capsys.readouterr().err
    assert f"diagnostic_id={diagnostic_id}" in journal
    assert "PermissionError" in journal
    assert "/protected/resource" in journal


def test_helper_claims_durable_authority_executes_both_subplans_and_commits_first(
    tmp_path, monkeypatch
):
    server, client = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    runtime_account = pwd.getpwuid(os.getuid())
    config = {"runtime_user": runtime_account.pw_name}
    operation_id = "d" * 32
    plan_id = "b" * 64
    durable_started_at = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
    events = []
    executed = []
    progresses = []
    path_candidate = HousekeepingCandidate(
        category="build_artifact",
        path=str(tmp_path / "threadcells-build"),
        canonical_identity="build_artifact:one",
        fingerprint="c" * 64,
        bytes=12,
        estimated_reclaim_bytes=12,
        action="delete",
        retention_reason="FULL_CLEANUP_DISPOSABLE_ARTIFACT",
    )
    workflow_candidate = HousekeepingCandidate(
        category="workflow",
        path="workflow:12",
        canonical_identity="workflow:12",
        fingerprint="d" * 64,
        bytes=0,
        estimated_reclaim_bytes=0,
        action="retire",
        retention_reason="ORPHANED_WORKFLOW_AUTHORITY",
        resource_kind="workflow_authority",
    )
    plan = HousekeepingPlan(
        schema_version=1,
        plan_id=plan_id,
        generated_at=1,
        mode="full",
        root=str(tmp_path),
        candidates=(path_candidate, workflow_candidate),
    )
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.full_cleanup_helper._runtime_identity",
        lambda _runtime_user: nullcontext(),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service._load_legacy_operations_config",
        lambda: config,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.load_operations_config", lambda: config
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.full_cleanup_helper._peer_holds_full_cleanup_fences",
        lambda *_args: True,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.full_cleanup_operation_service.process_start_ticks",
        lambda _pid: 987,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.claim_full_cleanup_operation",
        lambda *_args, **_kwargs: events.append("claim") or True,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.get_full_cleanup_operation",
        lambda _operation_id: {
            "operation_id": operation_id,
            "plan_id": plan_id,
            "retire_dirty_worktrees": False,
            "state": "running",
            "started_at": durable_started_at,
        },
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.update_full_cleanup_operation_progress",
        lambda *_args, **kwargs: (
            events.append("progress") or progresses.append(kwargs["progress"]) or True
        ),
    )
    committed = {}

    def complete(*_args, **kwargs):
        events.append("complete")
        committed.update(kwargs["report"])
        return True

    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.complete_full_cleanup_operation", complete
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.terminalize_full_cleanup_operation",
        lambda *_args, **_kwargs: events.append("terminalize") or True,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service.full_cleanup_idle_gate",
        lambda _config: {"eligible": True, "reason_code": None, "blockers": []},
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service.plan_housekeeping",
        lambda **_kwargs: plan,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service.get_housekeeping_settings",
        lambda _config: {"policy": {}},
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service._finalize_housekeeping_summary",
        lambda summary, **_kwargs: summary,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service._write_status",
        lambda *_args: events.append("status"),
    )

    def execute(candidate_plan, **kwargs):
        executed.append(candidate_plan.candidates)
        report = ExecutionReport(
            plan_id=candidate_plan.plan_id,
            executed=[item.canonical_identity for item in candidate_plan.candidates],
        )
        for item in candidate_plan.candidates:
            kwargs["progress_callback"](report, item.canonical_identity, "executed")
        return report

    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping.executor.execute_plan", execute
    )
    client.sendall(
        json.dumps(
            {
                "schema_version": 2,
                "operation": "full_cleanup",
                "operation_id": operation_id,
                "operation_token": "opaque-operation-token-with-enough-bytes",
                "expected_plan_id": plan_id,
                "confirmed": True,
                "retire_dirty_worktrees": False,
            }
        ).encode()
        + b"\n"
    )
    client.shutdown(socket.SHUT_WR)

    try:
        response = _handle_request(server)
    finally:
        server.close()
        client.close()

    assert executed == [(path_candidate,), (workflow_candidate,)]
    assert events[0] == "claim"
    assert events.count("progress") == 2
    assert [item["processed_candidates"] for item in progresses] == [1, 2]
    assert [item["executed_candidates"] for item in progresses] == [1, 2]
    assert events[-2:] == ["complete", "status"]
    assert committed["plan_id"] == plan_id
    assert committed["full_cleanup"] is True
    assert committed["started_at"] == durable_started_at.isoformat()
    assert response == {"schema_version": 2, "ok": True, "operation_id": operation_id}


def test_helper_refuses_unprivileged_direct_invocation(monkeypatch):
    server, client = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    try:
        with pytest.raises(FullCleanupHelperError, match="PRIVILEGE_REQUIRED"):
            _handle_request(server)
    finally:
        server.close()
        client.close()


def test_helper_requires_canonical_peer_fences(tmp_path):
    from cli_agent_orchestrator.services.full_cleanup_helper import (
        _peer_holds_full_cleanup_fences,
    )

    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    names = (
        "housekeeping.lock",
        "context-launch.lock",
        "workflow-execution-admission.lock",
        "provider-execution-admission.lock",
        "heavy-admission.lock",
    )
    handles = []
    try:
        import fcntl

        for name in names:
            handle = (lock_dir / name).open("a+")
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            handles.append(handle)

        assert _peer_holds_full_cleanup_fences(os.getpid(), {"lock_dir": str(lock_dir)}) is True
        fcntl.flock(handles[-1], fcntl.LOCK_UN)
        assert _peer_holds_full_cleanup_fences(os.getpid(), {"lock_dir": str(lock_dir)}) is False
    finally:
        for handle in handles:
            handle.close()


def test_helper_client_requires_exact_operation_authority(tmp_path):
    config = {
        "full_cleanup_helper_socket": str(tmp_path / "full-cleanup.sock"),
        "full_cleanup_helper_timeout_seconds": 1,
    }
    with pytest.raises(FullCleanupHelperError, match="OPERATION_ID_INVALID"):
        execute_via_privileged_helper(
            operation_id="short",
            expected_plan_id="c" * 64,
            confirmed=True,
            actor_kind="operator_session",
            config=config,
        )
    with pytest.raises(FullCleanupHelperError, match="OPERATOR_AUTHORITY_INVALID"):
        execute_via_privileged_helper(
            operation_id="c" * 32,
            expected_plan_id="c" * 64,
            confirmed=True,
            actor_kind="unknown",
            config=config,
        )
