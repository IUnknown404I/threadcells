import json
import os
import pwd
import socket
import threading
from pathlib import Path

import pytest

from cli_agent_orchestrator.services.full_cleanup_helper import (
    FullCleanupHelperError,
    _authenticate_request,
    _failure_response,
    _handle_request,
    execute_via_privileged_helper,
)
from cli_agent_orchestrator.services.operator_auth_service import OperatorAuthUnavailable
from cli_agent_orchestrator.services.housekeeping.executor import ExecutionReport
from cli_agent_orchestrator.services.housekeeping.models import (
    HousekeepingCandidate,
    HousekeepingPlan,
)


def test_privileged_helper_client_uses_bounded_unix_protocol(tmp_path):
    socket_path = tmp_path / "full-cleanup.sock"
    observed = {}

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
                            "schema_version": 1,
                            "ok": True,
                            "report": ExecutionReport(plan_id="a" * 64, freed_bytes=12).as_dict(),
                        }
                    ).encode()
                    + b"\n"
                )

    ready = threading.Event()
    worker = threading.Thread(target=serve)
    worker.start()
    ready.wait(timeout=1)
    result = execute_via_privileged_helper(
        expected_plan_id="a" * 64,
        confirmed=True,
        session_token="opaque-session-token",
        config={
            "full_cleanup_helper_socket": str(socket_path),
            "full_cleanup_helper_timeout_seconds": 1,
        },
    )
    worker.join(timeout=1)

    assert isinstance(result, ExecutionReport)
    assert result.plan_id == "a" * 64
    assert result.freed_bytes == 12
    assert observed == {
        "schema_version": 1,
        "operation": "full_cleanup",
        "expected_plan_id": "a" * 64,
        "confirmed": True,
        "operator_session_token": "opaque-session-token",
    }


def test_privileged_helper_client_preserves_safe_diagnostic_id(tmp_path):
    socket_path = tmp_path / "full-cleanup.sock"

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
                            "schema_version": 1,
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
            expected_plan_id="a" * 64,
            confirmed=True,
            session_token="opaque-session-token",
            config={
                "full_cleanup_helper_socket": str(socket_path),
                "full_cleanup_helper_timeout_seconds": 1,
            },
        )
    worker.join(timeout=1)

    assert str(raised.value) == "FULL_CLEANUP_HELPER_FAILED"
    assert raised.value.diagnostic_id == "d" * 32


def test_helper_reports_missing_operator_authority_before_execution(monkeypatch):
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operator_auth_service.load_operator_verifier",
        lambda: (_ for _ in ()).throw(OperatorAuthUnavailable("not configured")),
    )

    with pytest.raises(FullCleanupHelperError, match="^OPERATOR_AUTH_NOT_CONFIGURED$"):
        _authenticate_request({"operator_session_token": "opaque"})


def test_unexpected_helper_failure_gets_journal_diagnostic_id(capsys):
    response = _failure_response(PermissionError(13, "denied", "/protected/resource"))

    diagnostic_id = response["diagnostic_id"]
    assert isinstance(diagnostic_id, str)
    assert len(diagnostic_id) == 32
    assert response == {
        "schema_version": 1,
        "ok": False,
        "reason_code": "FULL_CLEANUP_HELPER_FAILED",
        "diagnostic_id": diagnostic_id,
    }
    journal = capsys.readouterr().err
    assert f"diagnostic_id={diagnostic_id}" in journal
    assert "PermissionError" in journal
    assert "/protected/resource" in journal


def test_helper_reauthenticates_existing_session_before_canonical_execution(monkeypatch):
    server, client = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    authenticated = []
    executed = []
    inventory_calls = []
    runtime_account = pwd.getpwuid(os.getuid())
    config = {"runtime_user": runtime_account.pw_name}
    identity_transitions = []
    monkeypatch.setenv("HOME", "/root")
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        os, "setgroups", lambda groups: identity_transitions.append(("groups", groups))
    )
    monkeypatch.setattr(os, "setegid", lambda gid: identity_transitions.append(("egid", gid)))
    monkeypatch.setattr(os, "seteuid", lambda uid: identity_transitions.append(("euid", uid)))
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service._load_legacy_operations_config",
        lambda: config,
    )

    def load_runtime_config():
        assert os.environ["HOME"] == runtime_account.pw_dir
        return config

    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.load_operations_config",
        load_runtime_config,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.full_cleanup_helper._peer_holds_full_cleanup_fences",
        lambda *_args: True,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operator_auth_service.load_operator_verifier",
        lambda: {"configured": True},
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.authenticate_operator_session",
        lambda token: authenticated.append(token) or "session-id",
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service.full_cleanup_idle_gate",
        lambda _config: {"eligible": True, "reason_code": None},
    )
    path_candidate = HousekeepingCandidate(
        category="build_artifact",
        path="/srv/agent-control/tmp/threadcells-build",
        canonical_identity="build_artifact:/srv/agent-control/tmp/threadcells-build",
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
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service.plan_housekeeping",
        lambda **_kwargs: HousekeepingPlan(
            schema_version=1,
            plan_id="b" * 64,
            generated_at=1,
            mode="full",
            root="/srv/agent-control",
            candidates=(path_candidate, workflow_candidate),
        ),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service.get_housekeeping_settings",
        lambda _config: {"policy": {}},
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping_service._runtime_open_paths_inventory",
        lambda *_args: (
            inventory_calls.append(len(inventory_calls) + 1)
            or (
                set()
                if len(inventory_calls) == 1
                else {Path("/var/lib/threadcells/releases/late-open/output.log")}
            ),
            True,
        ),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping.protected_set.resolve_protected_set",
        lambda _root, _config, *, open_inventory, full_cleanup: open_inventory(),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.housekeeping.executor.execute_plan",
        lambda plan, **kwargs: executed.append((plan, kwargs))
        or ExecutionReport(
            plan_id=plan.plan_id, active_release="/active", rollback_available=False
        ),
    )
    client.sendall(
        json.dumps(
            {
                "schema_version": 1,
                "operation": "full_cleanup",
                "expected_plan_id": "b" * 64,
                "confirmed": True,
                "operator_session_token": "opaque",
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

    assert authenticated == ["opaque"]
    assert os.environ["HOME"] == "/root"
    assert identity_transitions[0][0] == "groups"
    assert identity_transitions[-3:] == [
        ("euid", 0),
        ("groups", os.getgroups()),
        ("egid", os.getegid()),
    ]
    assert len(executed) == 1
    assert executed[0][0].candidates == (path_candidate,)
    assert executed[0][1]["full_cleanup"] is True
    assert executed[0][1]["reconcile_releases"] is True
    assert executed[0][1]["privileged_path_deletion"] is True
    assert callable(executed[0][1]["protection_resolver"])
    first_protection = executed[0][1]["protection_resolver"]()
    second_protection = executed[0][1]["protection_resolver"]()
    assert first_protection == (set(), True)
    assert second_protection == (
        {Path("/var/lib/threadcells/releases/late-open/output.log")},
        True,
    )
    assert inventory_calls == [1, 2]
    assert response == {
        "schema_version": 1,
        "ok": True,
        "report": ExecutionReport(
            plan_id="b" * 64,
            active_release="/active",
            rollback_available=False,
        ).as_dict(),
    }


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


def test_helper_requires_exactly_one_existing_operator_credential(tmp_path):
    config = {
        "full_cleanup_helper_socket": str(tmp_path / "full-cleanup.sock"),
        "full_cleanup_helper_timeout_seconds": 1,
    }
    with pytest.raises(FullCleanupHelperError, match="OPERATOR_CREDENTIAL_INVALID"):
        execute_via_privileged_helper(
            expected_plan_id="c" * 64,
            confirmed=True,
            config=config,
        )
    with pytest.raises(FullCleanupHelperError, match="OPERATOR_CREDENTIAL_INVALID"):
        execute_via_privileged_helper(
            expected_plan_id="c" * 64,
            confirmed=True,
            session_token="session",
            bearer_secret="secret",
            config=config,
        )
