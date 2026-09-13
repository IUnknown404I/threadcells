"""Durable lifecycle authority for one long-running Full Cleanup operation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

ACTIVE_STATES = {"admitted", "running"}
TERMINAL_STATES = {"completed", "completed_with_issues", "failed", "indeterminate"}


def process_start_ticks(process_id: int, proc_root: Path = Path("/proc")) -> int | None:
    """Return Linux process start ticks, which fence PID reuse."""
    try:
        stat_text = (proc_root / str(process_id) / "stat").read_text(encoding="utf-8")
        _prefix, suffix = stat_text.rsplit(")", 1)
        ticks = int(suffix.split()[19])
    except (OSError, ValueError, IndexError):
        return None
    return ticks if ticks > 0 else None


def reconcile_interrupted_full_cleanup_operations(
    *,
    proc_root: Path = Path("/proc"),
    include_admitted: bool = True,
) -> dict[str, int]:
    """Terminalize authority whose exact helper can no longer publish a result.

    ``include_admitted`` is reserved for service startup, after the previous
    API process and its unclaimed socket peers are gone. Runtime callers must
    not retire the short admitted-before-helper-connect interval.
    """
    from cli_agent_orchestrator.clients.database import (
        list_active_full_cleanup_operations,
        terminalize_full_cleanup_operation,
    )

    result = {"inspected": 0, "active": 0, "terminalized": 0}
    for operation in list_active_full_cleanup_operations():
        result["inspected"] += 1
        state = operation["state"]
        if state == "admitted":
            if include_admitted and terminalize_full_cleanup_operation(
                operation["operation_id"],
                reason_code="FULL_CLEANUP_INTERRUPTED_BEFORE_START",
                indeterminate=False,
            ):
                result["terminalized"] += 1
            else:
                result["active"] += 1
            continue
        if state != "running":
            raise RuntimeError("FULL_CLEANUP_OPERATION_AUTHORITY_AMBIGUOUS")
        pid = operation.get("helper_pid")
        ticks = operation.get("helper_process_start_ticks")
        if (
            isinstance(pid, int)
            and isinstance(ticks, int)
            and process_start_ticks(pid, proc_root) == ticks
        ):
            result["active"] += 1
            continue
        if terminalize_full_cleanup_operation(
            operation["operation_id"],
            reason_code="FULL_CLEANUP_HELPER_EXITED_WITHOUT_RECEIPT",
            indeterminate=True,
            helper_pid=pid if isinstance(pid, int) else -1,
            helper_process_start_ticks=ticks if isinstance(ticks, int) else -1,
        ):
            result["terminalized"] += 1
        else:
            result["active"] += 1
    return result


def require_no_active_full_cleanup_operation() -> None:
    """Fence new resource authority while an admitted destructive run exists."""
    from cli_agent_orchestrator.clients.database import list_active_full_cleanup_operations

    if list_active_full_cleanup_operations():
        raise RuntimeError("FULL_CLEANUP_OPERATION_ACTIVE")


def public_operation(operation: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a bounded non-secret read model suitable for operator polling."""
    if operation is None:
        return {"status": "never_run"}
    return {
        key: operation.get(key)
        for key in (
            "operation_id",
            "plan_id",
            "retire_dirty_worktrees",
            "state",
            "progress",
            "report",
            "reason_code",
            "diagnostic_id",
            "created_at",
            "started_at",
            "updated_at",
            "completed_at",
        )
    }


def report_from_operation(operation: Mapping[str, Any]):
    """Rehydrate only a validated terminal summary, never a guessed result."""
    from cli_agent_orchestrator.services.housekeeping_service import HousekeepingSummary

    report = operation.get("report")
    if operation.get("state") not in {"completed", "completed_with_issues"} or not isinstance(
        report, dict
    ):
        return None
    fields = set(HousekeepingSummary.__dataclass_fields__)
    if set(report) - fields:
        raise RuntimeError("FULL_CLEANUP_OPERATION_CORRUPT")
    try:
        return HousekeepingSummary(**report)
    except TypeError as exc:
        raise RuntimeError("FULL_CLEANUP_OPERATION_CORRUPT") from exc
