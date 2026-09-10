"""Narrow privileged transport for the canonical Full Cleanup operation.

The public API never receives filesystem privilege. A socket-activated,
root-owned one-shot consumes one API-admitted operation capability, rebuilds
the exact plan, rechecks the idle gate under canonical locks, and commits its
terminal report before answering the transport.
"""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import re
import secrets
import shutil
import socket
import stat
import struct
import sys
import traceback
import uuid
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

_DEFAULT_SOCKET = Path("/run/threadcells/full-cleanup.sock")
_MAX_REQUEST_BYTES = 64 * 1024
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_PLAN_ID = re.compile(r"[0-9a-f]{64}")
_OPERATION_ID = re.compile(r"[0-9a-f]{32}")


class FullCleanupHelperError(RuntimeError):
    """The privileged one-shot rejected or could not execute an operation."""

    def __init__(self, reason_code: str, *, diagnostic_id: str | None = None) -> None:
        super().__init__(reason_code)
        self.diagnostic_id = diagnostic_id


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
    operation_id: str,
    expected_plan_id: str,
    confirmed: bool,
    actor_kind: str,
    config: Mapping[str, Any] | None = None,
    retire_dirty_worktrees: bool = False,
) -> Any:
    """Admit once, then recover the helper result from durable state."""
    if confirmed is not True:
        raise FullCleanupHelperError("FULL_CLEANUP_CONFIRMATION_REQUIRED")
    if not _PLAN_ID.fullmatch(expected_plan_id):
        raise FullCleanupHelperError("FULL_CLEANUP_PLAN_ID_INVALID")
    if not _OPERATION_ID.fullmatch(operation_id):
        raise FullCleanupHelperError("FULL_CLEANUP_OPERATION_ID_INVALID")
    if actor_kind not in {"operator_session", "operator_bearer"}:
        raise FullCleanupHelperError("FULL_CLEANUP_OPERATOR_AUTHORITY_INVALID")
    from cli_agent_orchestrator.services.operations_service import load_operations_config

    cfg = dict(config or load_operations_config())
    operation_token = secrets.token_urlsafe(32)
    from cli_agent_orchestrator.clients.database import (
        admit_full_cleanup_operation,
        get_full_cleanup_operation,
        terminalize_full_cleanup_operation,
    )
    from cli_agent_orchestrator.services.full_cleanup_operation_service import (
        reconcile_interrupted_full_cleanup_operations,
        report_from_operation,
    )

    admitted = admit_full_cleanup_operation(
        operation_id,
        expected_plan_id,
        retire_dirty_worktrees=retire_dirty_worktrees,
        actor_kind=actor_kind,
        operation_token=operation_token,
    )
    if admitted["created"] is not True:
        recovered = report_from_operation(admitted)
        if recovered is not None:
            return recovered
        reason = admitted.get("reason_code")
        if not isinstance(reason, str):
            reason = "FULL_CLEANUP_OPERATION_ACTIVE"
        raise FullCleanupHelperError(reason, diagnostic_id=admitted.get("diagnostic_id"))

    def recover_after_transport_failure(
        reason_code: str, cause: BaseException | None = None
    ) -> Any:
        """Prefer the helper's durable receipt over fallible socket transport."""
        current = get_full_cleanup_operation(operation_id)
        if current is not None and current.get("state") == "admitted":
            terminalize_full_cleanup_operation(
                operation_id,
                reason_code=reason_code,
                indeterminate=False,
            )
        else:
            reconcile_interrupted_full_cleanup_operations(include_admitted=False)
        current = get_full_cleanup_operation(operation_id)
        recovered = report_from_operation(current or {})
        if recovered is not None:
            return recovered
        durable_reason = (current or {}).get("reason_code")
        if not isinstance(durable_reason, str):
            durable_reason = reason_code
        error = FullCleanupHelperError(
            durable_reason,
            diagnostic_id=(current or {}).get("diagnostic_id"),
        )
        if cause is None:
            raise error
        raise error from cause

    request: dict[str, Any] = {
        "schema_version": 2,
        "operation": "full_cleanup",
        "operation_id": operation_id,
        "operation_token": operation_token,
        "expected_plan_id": expected_plan_id,
        "confirmed": True,
        "retire_dirty_worktrees": retire_dirty_worktrees,
    }
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
    except (OSError, TimeoutError, FullCleanupHelperError) as exc:
        # The socket response is not lifecycle authority. Recover a report
        # which the helper committed first, or truthfully settle an exact
        # helper which can no longer do so.
        reason = (
            str(exc)
            if isinstance(exc, FullCleanupHelperError)
            else "FULL_CLEANUP_PRIVILEGED_HELPER_UNAVAILABLE"
        )
        return recover_after_transport_failure(reason, exc)
    try:
        response = json.loads(response_payload.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return recover_after_transport_failure("FULL_CLEANUP_HELPER_RESPONSE_INVALID", exc)
    if not isinstance(response, dict) or response.get("schema_version") != 2:
        return recover_after_transport_failure("FULL_CLEANUP_HELPER_RESPONSE_INVALID")
    if response.get("ok") is not True:
        current = get_full_cleanup_operation(operation_id)
        recovered = report_from_operation(current or {})
        if recovered is not None:
            return recovered
        reason = response.get("reason_code")
        if not isinstance(reason, str) or not re.fullmatch(r"[A-Z0-9_]{3,96}", reason):
            reason = "FULL_CLEANUP_HELPER_REJECTED"
        diagnostic_id = response.get("diagnostic_id")
        if not isinstance(diagnostic_id, str) or not re.fullmatch(r"[0-9a-f]{32}", diagnostic_id):
            diagnostic_id = None
        raise FullCleanupHelperError(reason, diagnostic_id=diagnostic_id)
    if response.get("operation_id") != operation_id:
        return recover_after_transport_failure("FULL_CLEANUP_HELPER_RESPONSE_INVALID")
    current = get_full_cleanup_operation(operation_id)
    recovered = report_from_operation(current or {})
    if recovered is None:
        return recover_after_transport_failure("FULL_CLEANUP_HELPER_RESPONSE_INVALID")
    return recovered


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
    from cli_agent_orchestrator.services.full_cleanup_operation_service import (
        process_start_ticks,
    )
    from cli_agent_orchestrator.services.operations_service import (
        _load_legacy_operations_config,
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
    required_keys = {
        "schema_version",
        "operation",
        "operation_id",
        "operation_token",
        "expected_plan_id",
        "confirmed",
    }
    allowed_keys = required_keys | {"retire_dirty_worktrees"}
    if (
        not isinstance(request, dict)
        or not required_keys.issubset(request)
        or not set(request).issubset(allowed_keys)
        or request.get("schema_version") != 2
        or request.get("operation") != "full_cleanup"
        or request.get("confirmed") is not True
        or not isinstance(request.get("retire_dirty_worktrees", False), bool)
        or not isinstance(request.get("operation_id"), str)
        or not _OPERATION_ID.fullmatch(request["operation_id"])
        or not isinstance(request.get("operation_token"), str)
        or not 32 <= len(request["operation_token"]) <= 128
        or not isinstance(request.get("expected_plan_id"), str)
        or not _PLAN_ID.fullmatch(request["expected_plan_id"])
    ):
        raise FullCleanupHelperError("FULL_CLEANUP_HELPER_PROTOCOL_INVALID")
    helper_pid = os.getpid()
    helper_ticks = process_start_ticks(helper_pid)
    if helper_ticks is None:
        raise FullCleanupHelperError("FULL_CLEANUP_HELPER_IDENTITY_UNKNOWN")
    operation_id = request["operation_id"]
    claimed = False
    execution_started = False
    try:
        with _runtime_identity(runtime_user):
            from cli_agent_orchestrator.clients.database import (
                claim_full_cleanup_operation,
                complete_full_cleanup_operation,
                get_full_cleanup_operation,
                terminalize_full_cleanup_operation,
                update_full_cleanup_operation_progress,
            )
            from cli_agent_orchestrator.services.housekeeping.executor import (
                execute_plan,
                merge_execution_reports,
                privileged_full_cleanup_candidate,
            )
            from cli_agent_orchestrator.services.housekeeping_service import (
                HousekeepingSummary,
                _apply_execution_report_to_summary,
                _finalize_housekeeping_summary,
                _prepare_housekeeping_summary,
                _runtime_open_paths_inventory,
                _write_status,
                full_cleanup_idle_gate,
                get_housekeeping_settings,
                plan_housekeeping,
            )
            from cli_agent_orchestrator.services.operations_service import (
                load_operations_config,
            )

            config = dict(load_operations_config())
            config["_retire_dirty_session_workspaces"] = request.get(
                "retire_dirty_worktrees", False
            )
            if str(config.get("runtime_user")) != runtime_user:
                raise FullCleanupHelperError("FULL_CLEANUP_HELPER_CONFIG_INVALID")
            if not _peer_holds_full_cleanup_fences(peer_pid, config):
                raise FullCleanupHelperError("FULL_CLEANUP_HELPER_FENCE_REQUIRED")
            claimed = claim_full_cleanup_operation(
                operation_id,
                request["operation_token"],
                helper_pid=helper_pid,
                helper_process_start_ticks=helper_ticks,
            )
            if not claimed:
                raise FullCleanupHelperError("FULL_CLEANUP_OPERATION_CLAIM_REJECTED")
            operation = get_full_cleanup_operation(operation_id)
            if (
                operation is None
                or operation["plan_id"] != request["expected_plan_id"]
                or operation["retire_dirty_worktrees"]
                != request.get("retire_dirty_worktrees", False)
            ):
                raise FullCleanupHelperError("FULL_CLEANUP_OPERATION_AUTHORITY_CHANGED")
            idle_gate = full_cleanup_idle_gate(config)
            if not idle_gate["eligible"]:
                raise FullCleanupHelperError(str(idle_gate["reason_code"]))
            plan = plan_housekeeping(config=config, mode="full")
            if plan.plan_id != request["expected_plan_id"]:
                raise FullCleanupHelperError("HOUSEKEEPING_PLAN_CHANGED")
            settings = get_housekeeping_settings(config)
            summary = HousekeepingSummary(mode="full", full_cleanup=True, idle_gate=idle_gate)
            summary.disk_before = shutil.disk_usage("/").free
            actionable = _prepare_housekeeping_summary(summary, plan)

        privileged_plan = replace(
            plan,
            candidates=tuple(
                candidate
                for candidate in plan.candidates
                if privileged_full_cleanup_candidate(candidate)
            ),
        )
        local_plan = replace(
            plan,
            candidates=tuple(
                candidate
                for candidate in plan.candidates
                if not privileged_full_cleanup_candidate(candidate)
            ),
        )
        progress_sequence = 0
        progress_by_phase = {
            "privileged": (0, 0, 0, 0),
            "runtime": (0, 0, 0, 0),
        }

        def persist_progress(report, candidate: str, outcome: str, *, phase: str) -> None:
            nonlocal progress_sequence
            progress_sequence += 1
            progress_by_phase[phase] = (
                len(report.executed),
                len(report.skipped),
                len(report.failures),
                report.freed_bytes,
            )
            executed, skipped, failed, freed = (
                sum(values[index] for values in progress_by_phase.values()) for index in range(4)
            )
            progress = {
                "schema_version": 1,
                "sequence": progress_sequence,
                "phase": phase,
                "processed_candidates": executed + skipped + failed,
                "executed_candidates": executed,
                "skipped_candidates": skipped,
                "failed_candidates": failed,
                "freed_bytes": freed,
                "last_candidate_sha256": hashlib.sha256(candidate.encode("utf-8")).hexdigest(),
                "last_outcome": outcome,
            }

            def update() -> bool:
                return update_full_cleanup_operation_progress(
                    operation_id,
                    helper_pid=helper_pid,
                    helper_process_start_ticks=helper_ticks,
                    progress=progress,
                )

            if os.geteuid() == runtime_uid:
                updated = update()
            else:
                with _runtime_identity(runtime_user):
                    updated = update()
            if not updated:
                raise FullCleanupHelperError("FULL_CLEANUP_OPERATION_AUTHORITY_CHANGED")

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

        execution_started = True
        privileged_report = execute_plan(
            privileged_plan,
            config=config,
            open_inventory=runtime_open_inventory,
            settings=settings,
            full_cleanup=True,
            lifecycle_fence_held=True,
            reconcile_releases=True,
            protection_resolver=runtime_protection,
            privileged_path_deletion=True,
            progress_callback=lambda report, candidate, outcome: persist_progress(
                report, candidate, outcome, phase="privileged"
            ),
        )
        if privileged_report.ok:
            with _runtime_identity(runtime_user):
                local_report = execute_plan(
                    local_plan,
                    config=config,
                    open_inventory=lambda: _runtime_open_paths_inventory(config, Path("/proc")),
                    settings=settings,
                    full_cleanup=True,
                    lifecycle_fence_held=True,
                    reconcile_releases=False,
                    progress_callback=lambda report, candidate, outcome: persist_progress(
                        report, candidate, outcome, phase="runtime"
                    ),
                )
            report = merge_execution_reports(privileged_report, local_report)
        else:
            report = privileged_report
        with _runtime_identity(runtime_user):
            _apply_execution_report_to_summary(summary, report, actionable)
            _finalize_housekeeping_summary(
                summary,
                root=Path(plan.root),
                config=config,
                proc_root=Path("/proc"),
                completed_at=plan.generated_at,
                write_status=False,
            )
            if not complete_full_cleanup_operation(
                operation_id,
                helper_pid=helper_pid,
                helper_process_start_ticks=helper_ticks,
                report=summary.as_dict(),
            ):
                raise FullCleanupHelperError("FULL_CLEANUP_OPERATION_AUTHORITY_CHANGED")
            try:
                _write_status(Path(plan.root), summary)
            except OSError:
                # The durable operation report is canonical after a restart;
                # the legacy latest-report file remains best-effort.
                pass
        return {"schema_version": 2, "ok": True, "operation_id": operation_id}
    except Exception as exc:
        if claimed:
            reason = (
                str(exc)
                if isinstance(exc, FullCleanupHelperError)
                else "FULL_CLEANUP_EXECUTION_FAILED"
            )
            if not re.fullmatch(r"[A-Z0-9_]{3,96}", reason):
                reason = "FULL_CLEANUP_EXECUTION_FAILED"
            with _runtime_identity(runtime_user):
                terminalize_full_cleanup_operation(
                    operation_id,
                    reason_code=reason,
                    indeterminate=execution_started,
                    helper_pid=helper_pid,
                    helper_process_start_ticks=helper_ticks,
                )
        raise


def _failure_response(exc: Exception) -> dict[str, Any]:
    """Return a bounded public failure and preserve unexpected detail in journal."""
    if isinstance(exc, FullCleanupHelperError) and re.fullmatch(r"[A-Z0-9_]{3,96}", str(exc)):
        response: dict[str, Any] = {
            "schema_version": 2,
            "ok": False,
            "reason_code": str(exc),
        }
        if exc.diagnostic_id is not None:
            response["diagnostic_id"] = exc.diagnostic_id
        return response

    diagnostic_id = uuid.uuid4().hex
    print(
        "ThreadCells Full Cleanup helper failed "
        f"diagnostic_id={diagnostic_id} exception_type={type(exc).__module__}.{type(exc).__name__}",
        file=sys.stderr,
        flush=True,
    )
    traceback.print_exception(exc, file=sys.stderr)
    return {
        "schema_version": 2,
        "ok": False,
        "reason_code": "FULL_CLEANUP_HELPER_FAILED",
        "diagnostic_id": diagnostic_id,
    }


def main() -> None:
    """Serve exactly one systemd-provided AF_UNIX connection and exit."""
    response: dict[str, Any]
    connection = socket.socket(fileno=os.dup(sys.stdin.fileno()))
    try:
        if connection.family != socket.AF_UNIX:
            raise FullCleanupHelperError("FULL_CLEANUP_HELPER_PEER_REJECTED")
        response = _handle_request(connection)
    except Exception as exc:
        response = _failure_response(exc)
    try:
        connection.sendall(
            json.dumps(response, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        )
    finally:
        connection.close()


if __name__ == "__main__":
    main()
