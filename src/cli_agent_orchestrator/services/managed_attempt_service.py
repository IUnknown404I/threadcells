"""Crash-safe fencing for orphaned managed child attempts."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.tmux import PaneTargetError, tmux_client
from cli_agent_orchestrator.providers.manager import provider_manager
from cli_agent_orchestrator.services import terminal_service
from cli_agent_orchestrator.services.recovery_takeover_service import _retire_recovery_runtime

logger = logging.getLogger(__name__)

FENCE_RETIRE_TIMEOUT_SECONDS = 30.0
FENCE_RETIRE_POLL_SECONDS = 0.25


class ManagedAttemptFenceRuntimeError(RuntimeError):
    """Machine-readable physical fencing failure."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


def _validate_runtime_target(metadata: dict[str, Any]) -> Any:
    try:
        target = tmux_client.exact_runtime_target(
            str(metadata["tmux_session"]), str(metadata["tmux_window"])
        )
    except PaneTargetError as exc:
        if exc.reason_code in {
            "EXIT_SESSION_MISSING",
            "EXIT_WINDOW_MISSING",
            "EXIT_PANE_MISSING",
            "EXIT_PANE_DEAD",
        }:
            return None
        raise ManagedAttemptFenceRuntimeError(
            "MANAGED_ATTEMPT_RUNTIME_AUTHORITY_AMBIGUOUS"
        ) from exc
    durable = (
        metadata.get("id"),
        metadata.get("runtime_pane_id"),
        metadata.get("runtime_pane_pid"),
        metadata.get("runtime_generation"),
        metadata.get("runtime_process_start_ticks"),
        metadata.get("runtime_process_group_id"),
        metadata.get("runtime_process_session_id"),
    )
    observed = (
        target.terminal_id,
        target.pane_id,
        target.pane_pid,
        target.runtime_generation,
        target.process_start_ticks,
        target.process_group_id,
        target.process_session_id,
    )
    if durable != observed or any(value in (None, "") for value in durable):
        raise ManagedAttemptFenceRuntimeError("MANAGED_ATTEMPT_RUNTIME_AUTHORITY_AMBIGUOUS")
    origin = metadata.get("runtime_generation_origin")
    if origin not in {"launch", "reconciled"} or (
        (origin == "launch") != bool(target.generation_inherited)
    ):
        raise ManagedAttemptFenceRuntimeError("MANAGED_ATTEMPT_RUNTIME_AUTHORITY_AMBIGUOUS")
    return target


def _retire_claimed_runtime(metadata: dict[str, Any], *, timeout: float) -> None:
    """Exit and remove only the exact runtime bound by the durable fence claim."""
    target = _validate_runtime_target(metadata)
    if target is None:
        retired, reason = _retire_recovery_runtime(metadata)
        if not retired:
            raise ManagedAttemptFenceRuntimeError(
                reason or "MANAGED_ATTEMPT_RUNTIME_RETIREMENT_FAILED"
            )
        return
    force_retirement = False
    if target.current_command not in terminal_service.SHELL_COMMANDS and target.current_command:
        provider = provider_manager.get_provider(str(metadata["id"]))
        if provider is None:
            # A service restart can lose the in-memory provider object. The
            # durable runtime tuple was compared exactly and logical writer
            # authority is already revoked, so fall back to the exact-pane
            # retirement predicate instead of leaving the fence immortal.
            force_retirement = True
        else:
            terminal_service._validate_exit_provider(metadata, provider)
            exit_command = provider.exit_cli()
            if exit_command.startswith(("C-", "M-")):
                tmux_client.send_special_key(
                    str(metadata["tmux_session"]),
                    str(metadata["tmux_window"]),
                    exit_command,
                    pane_id=target.pane_id,
                )
            else:
                tmux_client.send_keys(
                    str(metadata["tmux_session"]),
                    str(metadata["tmux_window"]),
                    exit_command,
                    enter_count=1,
                    pane_id=target.pane_id,
                )
    deadline = time.monotonic() + timeout
    last_reason: Optional[str] = None
    forced = False
    while True:
        retired, last_reason = _retire_recovery_runtime(metadata)
        if retired:
            provider_manager.cleanup_provider(str(metadata["id"]))
            return
        now = time.monotonic()
        if force_retirement or (now >= deadline and not forced):
            current = _validate_runtime_target(metadata)
            if current is not None and not tmux_client.retire_runtime_pane(current):
                raise ManagedAttemptFenceRuntimeError("MANAGED_ATTEMPT_RUNTIME_RETIREMENT_FAILED")
            force_retirement = False
            forced = True
            deadline = now + 2.0
        elif now >= deadline:
            raise ManagedAttemptFenceRuntimeError(
                last_reason or "MANAGED_ATTEMPT_RUNTIME_RETIREMENT_TIMEOUT"
            )
        time.sleep(FENCE_RETIRE_POLL_SECONDS)


def fence_managed_attempt(
    *,
    assignment_id: int,
    attempt_id: str,
    parent_terminal_id: str,
    child_terminal_id: str,
    request_workflow_effect_id: int,
    child_workflow_turn_id: int,
    reason_code: str,
    expected_runtime_generation: str,
    expected_writer_authority_generation: str,
    timeout: float = FENCE_RETIRE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Fence one exact attempt, retire its runtime, and publish one parent wake."""
    claim = database.claim_managed_attempt_fence(
        assignment_id=assignment_id,
        attempt_id=attempt_id,
        parent_terminal_id=parent_terminal_id,
        child_terminal_id=child_terminal_id,
        request_workflow_effect_id=request_workflow_effect_id,
        child_workflow_turn_id=child_workflow_turn_id,
        reason_code=reason_code,
        expected_runtime_generation=expected_runtime_generation,
        expected_writer_authority_generation=expected_writer_authority_generation,
    )
    if claim.get("state") == "fenced":
        return claim
    metadata = terminal_service.get_terminal_metadata(child_terminal_id)
    if metadata is None:
        return {
            **claim,
            "accepted": False,
            "reason_code": "MANAGED_ATTEMPT_TERMINAL_MISSING_AFTER_FENCE_CLAIM",
        }
    try:
        _retire_claimed_runtime(metadata, timeout=timeout)
    except Exception as exc:
        reason = getattr(exc, "reason_code", "MANAGED_ATTEMPT_RUNTIME_RETIREMENT_FAILED")
        logger.warning(
            "Managed attempt %s remains logically fenced pending runtime retirement (%s)",
            assignment_id,
            reason,
        )
        return {**claim, "accepted": False, "reason_code": reason}
    completed = database.complete_managed_attempt_fence(
        assignment_id,
        str(claim["fence_claim_token"]),
        reason_code=reason_code,
    )
    return completed


def reconcile_managed_attempt_fences(limit: int = 20) -> int:
    """Resume interrupted fence sagas and fence expired recovery attempts."""
    reconciled = 0
    for candidate in database.list_managed_attempt_fence_candidates(limit=limit):
        try:
            result = fence_managed_attempt(**candidate)
            reconciled += int(result.get("state") == "fenced")
        except (database.ManagedAttemptFenceError, ManagedAttemptFenceRuntimeError) as exc:
            logger.warning(
                "Managed attempt fence reconciliation deferred for %s: %s",
                candidate.get("assignment_id"),
                exc,
            )
    return reconciled
