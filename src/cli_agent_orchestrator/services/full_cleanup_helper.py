"""Narrow privileged transport for the canonical Full Cleanup operation.

The public API never receives filesystem privilege.  A socket-activated,
root-owned one-shot independently verifies the existing operator authority,
rebuilds the exact plan, rechecks the idle gate under canonical locks, and
executes the ordinary Housekeeping Full Cleanup implementation.
"""

from __future__ import annotations

import json
import os
import pwd
import re
import socket
import stat
import struct
import sys
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

_DEFAULT_SOCKET = Path("/run/threadcells/full-cleanup.sock")
_MAX_REQUEST_BYTES = 64 * 1024
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_PLAN_ID = re.compile(r"[0-9a-f]{64}")


class FullCleanupHelperError(RuntimeError):
    """The privileged one-shot rejected or could not execute an operation."""


def _receive_line(connection: socket.socket, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = connection.recv(min(65536, limit + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise FullCleanupHelperError("FULL_CLEANUP_HELPER_MESSAGE_TOO_LARGE")
        if b"\n" in chunk:
            break
    value = b"".join(chunks)
    line, separator, remainder = value.partition(b"\n")
    if not separator or remainder:
        raise FullCleanupHelperError("FULL_CLEANUP_HELPER_PROTOCOL_INVALID")
    return line


def _socket_path(config: Mapping[str, Any]) -> Path:
    value = Path(str(config.get("full_cleanup_helper_socket", _DEFAULT_SOCKET)))
    if not value.is_absolute() or value.name != "full-cleanup.sock":
        raise FullCleanupHelperError("FULL_CLEANUP_HELPER_CONFIG_INVALID")
    return value


def execute_via_privileged_helper(
    *,
    expected_plan_id: str,
    confirmed: bool,
    session_token: str | None = None,
    bearer_secret: str | None = None,
    config: Mapping[str, Any] | None = None,
) -> Any:
    """Execute the release subplan through the root one-shot; never fall back."""
    if confirmed is not True:
        raise FullCleanupHelperError("FULL_CLEANUP_CONFIRMATION_REQUIRED")
    if not _PLAN_ID.fullmatch(expected_plan_id):
        raise FullCleanupHelperError("FULL_CLEANUP_PLAN_ID_INVALID")
    if (session_token is None) == (bearer_secret is None):
        raise FullCleanupHelperError("FULL_CLEANUP_OPERATOR_CREDENTIAL_INVALID")
    from cli_agent_orchestrator.services.operations_service import load_operations_config

    cfg = dict(config or load_operations_config())
    request: dict[str, Any] = {
        "schema_version": 1,
        "operation": "full_cleanup",
        "expected_plan_id": expected_plan_id,
        "confirmed": True,
    }
    if session_token is not None:
        request["operator_session_token"] = session_token
    else:
        request["operator_bearer_secret"] = bearer_secret
    payload = json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(payload) > _MAX_REQUEST_BYTES:
        raise FullCleanupHelperError("FULL_CLEANUP_HELPER_MESSAGE_TOO_LARGE")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(float(cfg.get("full_cleanup_helper_timeout_seconds", 1800)))
            connection.connect(str(_socket_path(cfg)))
            connection.sendall(payload)
            connection.shutdown(socket.SHUT_WR)
            response_payload = _receive_line(connection, _MAX_RESPONSE_BYTES)
    except (OSError, TimeoutError) as exc:
        raise FullCleanupHelperError("FULL_CLEANUP_PRIVILEGED_HELPER_UNAVAILABLE") from exc
    try:
        response = json.loads(response_payload.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FullCleanupHelperError("FULL_CLEANUP_HELPER_RESPONSE_INVALID") from exc
    if not isinstance(response, dict) or response.get("schema_version") != 1:
        raise FullCleanupHelperError("FULL_CLEANUP_HELPER_RESPONSE_INVALID")
    if response.get("ok") is not True:
        reason = response.get("reason_code")
        if not isinstance(reason, str) or not re.fullmatch(r"[A-Z0-9_]{3,96}", reason):
            reason = "FULL_CLEANUP_HELPER_REJECTED"
        raise FullCleanupHelperError(reason)
    report = response.get("report")
    if not isinstance(report, dict):
        raise FullCleanupHelperError("FULL_CLEANUP_HELPER_RESPONSE_INVALID")
    from cli_agent_orchestrator.services.housekeeping.executor import ExecutionReport

    try:
        return ExecutionReport(**report)
    except TypeError as exc:
        raise FullCleanupHelperError("FULL_CLEANUP_HELPER_RESPONSE_INVALID") from exc


def _authenticate_request(request: Mapping[str, Any]) -> None:
    from cli_agent_orchestrator.clients.database import authenticate_operator_session
    from cli_agent_orchestrator.services.operator_auth_service import (
        authenticate_operator_secret,
        load_operator_verifier,
    )

    # Removing or invalidating the existing verifier closes this boundary too.
    load_operator_verifier()
    session_token = request.get("operator_session_token")
    bearer_secret = request.get("operator_bearer_secret")
    if (isinstance(session_token, str)) == (isinstance(bearer_secret, str)):
        raise FullCleanupHelperError("OPERATOR_AUTHENTICATION_FAILED")
    if isinstance(session_token, str):
        if not authenticate_operator_session(session_token):
            raise FullCleanupHelperError("OPERATOR_AUTHENTICATION_FAILED")
    else:
        assert isinstance(bearer_secret, str)
        authenticate_operator_secret(bearer_secret)


def _peer_holds_full_cleanup_fences(peer_pid: int, config: Mapping[str, Any]) -> bool:
    """Prove the socket peer owns every canonical pre-destruction lock.

    Socket ownership plus an operator credential is insufficient: without this
    kernel-backed proof, a same-UID process could bypass the API's final idle
    check and execution serialization.  Each peer fd must reference the exact
    non-symlink lock inode and expose its own advisory write flock in fdinfo.
    """
    lock_dir = Path(str(config.get("lock_dir", "")))
    if not lock_dir.is_absolute() or lock_dir.is_symlink() or not lock_dir.is_dir():
        return False
    required = {
        "housekeeping.lock",
        "context-launch.lock",
        "workflow-execution-admission.lock",
        "provider-execution-admission.lock",
        "heavy-admission.lock",
    }
    identities: dict[tuple[int, int], str] = {}
    for name in required:
        path = lock_dir / name
        descriptor = -1
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            metadata = os.fstat(descriptor)
        except OSError:
            return False
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            return False
        identities[(metadata.st_dev, metadata.st_ino)] = name
    if len(identities) != len(required):
        return False

    owned: set[str] = set()
    fd_root = Path("/proc") / str(peer_pid) / "fd"
    try:
        descriptors = list(fd_root.iterdir())
    except OSError:
        return False
    lock_pattern = re.compile(rf"^lock:\s+.*\bFLOCK\b.*\bWRITE\b\s+{peer_pid}\s+", re.MULTILINE)
    for descriptor_path in descriptors:
        if not descriptor_path.name.isdigit():
            continue
        try:
            metadata = descriptor_path.stat()
            owned_name = identities.get((metadata.st_dev, metadata.st_ino))
            if owned_name is None:
                continue
            fdinfo = (Path("/proc") / str(peer_pid) / "fdinfo" / descriptor_path.name).read_text(
                encoding="utf-8"
            )
        except OSError:
            continue
        if lock_pattern.search(fdinfo):
            owned.add(owned_name)
    return owned == required


@contextmanager
def _runtime_identity(runtime_user: str):
    """Drop root while touching operator, SQLite, and runtime plan authority."""
    account = pwd.getpwnam(runtime_user)
    original_euid = os.geteuid()
    original_egid = os.getegid()
    original_groups = os.getgroups()
    original_home = os.environ.get("HOME")
    if original_euid != 0:
        raise FullCleanupHelperError("FULL_CLEANUP_HELPER_PRIVILEGE_REQUIRED")
    runtime_groups = os.getgrouplist(runtime_user, account.pw_gid)
    os.setgroups(runtime_groups)
    os.setegid(account.pw_gid)
    os.seteuid(account.pw_uid)
    # The one-shot starts as root, but runtime configuration and database paths
    # belong to the configured service account.  Rebind HOME before any lazy
    # runtime imports so Path.home() cannot retain the privileged account's
    # inaccessible state root after the effective identity is dropped.
    os.environ["HOME"] = account.pw_dir
    try:
        yield account
    finally:
        os.seteuid(original_euid)
        os.setgroups(original_groups)
        os.setegid(original_egid)
        if original_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = original_home


def _handle_request(connection: socket.socket) -> dict[str, Any]:
    from cli_agent_orchestrator.services.housekeeping.executor import execute_plan
    from cli_agent_orchestrator.services.housekeeping_service import (
        _runtime_open_paths_inventory,
        full_cleanup_idle_gate,
        get_housekeeping_settings,
        plan_housekeeping,
    )
    from cli_agent_orchestrator.services.operations_service import (
        _load_legacy_operations_config,
        load_operations_config,
    )

    if os.geteuid() != 0:
        raise FullCleanupHelperError("FULL_CLEANUP_HELPER_PRIVILEGE_REQUIRED")
    bootstrap = _load_legacy_operations_config()
    runtime_user = str(bootstrap["runtime_user"])
    try:
        runtime_uid = pwd.getpwnam(runtime_user).pw_uid
    except (KeyError, TypeError) as exc:
        raise FullCleanupHelperError("FULL_CLEANUP_HELPER_CONFIG_INVALID") from exc
    peer_pid, peer_uid, _peer_gid = struct.unpack(
        "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    )
    if peer_pid <= 1 or peer_uid != runtime_uid:
        raise FullCleanupHelperError("FULL_CLEANUP_HELPER_PEER_REJECTED")
    raw = _receive_line(connection, _MAX_REQUEST_BYTES)
    try:
        request = json.loads(raw.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FullCleanupHelperError("FULL_CLEANUP_HELPER_PROTOCOL_INVALID") from exc
    expected_keys = {
        "schema_version",
        "operation",
        "expected_plan_id",
        "confirmed",
    }
    if isinstance(request, dict):
        credential_keys = {
            key for key in ("operator_session_token", "operator_bearer_secret") if key in request
        }
    else:
        credential_keys = set()
    if (
        not isinstance(request, dict)
        or set(request) != expected_keys | credential_keys
        or len(credential_keys) != 1
        or request.get("schema_version") != 1
        or request.get("operation") != "full_cleanup"
        or request.get("confirmed") is not True
        or not isinstance(request.get("expected_plan_id"), str)
        or not _PLAN_ID.fullmatch(request["expected_plan_id"])
    ):
        raise FullCleanupHelperError("FULL_CLEANUP_HELPER_PROTOCOL_INVALID")
    with _runtime_identity(runtime_user):
        config = load_operations_config()
        if str(config.get("runtime_user")) != runtime_user:
            raise FullCleanupHelperError("FULL_CLEANUP_HELPER_CONFIG_INVALID")
        if not _peer_holds_full_cleanup_fences(peer_pid, config):
            raise FullCleanupHelperError("FULL_CLEANUP_HELPER_FENCE_REQUIRED")
        _authenticate_request(request)
        idle_gate = full_cleanup_idle_gate(config)
        if not idle_gate["eligible"]:
            raise FullCleanupHelperError(str(idle_gate["reason_code"]))
        plan = plan_housekeeping(config=config, mode="full")
        if plan.plan_id != request["expected_plan_id"]:
            raise FullCleanupHelperError("HOUSEKEEPING_PLAN_CHANGED")
        settings = get_housekeeping_settings(config)
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

    def runtime_protection():
        from cli_agent_orchestrator.services.housekeeping.protected_set import (
            resolve_protected_set,
        )

        with _runtime_identity(runtime_user):
            return resolve_protected_set(
                Path(plan.root),
                config,
                open_inventory=lambda: _runtime_open_paths_inventory(config, Path("/proc")),
                full_cleanup=True,
            )

    def runtime_open_inventory():
        with _runtime_identity(runtime_user):
            return _runtime_open_paths_inventory(config, Path("/proc"))

    report = execute_plan(
        privileged_plan,
        config=config,
        open_inventory=runtime_open_inventory,
        settings=settings,
        full_cleanup=True,
        lifecycle_fence_held=True,
        reconcile_releases=True,
        protection_resolver=runtime_protection,
        privileged_path_deletion=True,
    )
    return {"schema_version": 1, "ok": True, "report": report.as_dict()}


def main() -> None:
    """Serve exactly one systemd-provided AF_UNIX connection and exit."""
    response: dict[str, Any]
    connection = socket.socket(fileno=os.dup(sys.stdin.fileno()))
    try:
        if connection.family != socket.AF_UNIX:
            raise FullCleanupHelperError("FULL_CLEANUP_HELPER_PEER_REJECTED")
        response = _handle_request(connection)
    except Exception as exc:
        reason = str(exc) if isinstance(exc, FullCleanupHelperError) else str(exc)
        if not re.fullmatch(r"[A-Z0-9_]{3,96}", reason):
            reason = "FULL_CLEANUP_HELPER_FAILED"
        response = {"schema_version": 1, "ok": False, "reason_code": reason}
    try:
        connection.sendall(
            json.dumps(response, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        )
    finally:
        connection.close()


if __name__ == "__main__":
    main()
