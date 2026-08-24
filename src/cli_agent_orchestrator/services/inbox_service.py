"""Inbox service with watchdog for automatic message delivery.

This module provides the inbox functionality for agent-to-agent communication,
using file system monitoring to detect when agents become idle and can receive messages.

Architecture:
- Messages are queued in the database (inbox table) via send_message MCP tool
- LogFileHandler monitors terminal log files for changes using watchdog
- When a terminal becomes idle (detected via log patterns), pending messages are delivered
- Messages are sent via terminal_service.send_input() which types into the tmux pane

Message Flow:
1. Agent A calls send_message(terminal_id, message) → message queued in DB
2. Agent B's terminal log file updates (via tmux pipe-pane)
3. LogFileHandler.on_modified() triggered → checks for pending messages
4. If terminal is IDLE and has pending messages → deliver via send_input()
5. Message status updated to DELIVERED or FAILED

Performance Optimization:
- Uses fast log tail check before expensive tmux status queries
- Only queries full provider status when idle pattern detected in log
"""

import logging
import re
import subprocess
import threading
from pathlib import Path

from watchdog.events import FileModifiedEvent, FileSystemEventHandler

from cli_agent_orchestrator.clients.database import (
    DEFER_UNADMITTED,
    activate_workflow_turn_for_inbox,
    arm_handoff_continuations_for_restart,
    cancel_child_assignments_for_terminal,
    claim_handoff_result_batch_for_inbox,
    claim_workflow_turn,
    create_handoff_child_result_message,
    ensure_workflow_turn_for_inbox,
    get_child_assignment_result_child_id,
    get_child_assignment_result_id,
    get_delegation_result,
    get_handoff_child_result_message,
    get_handoff_child_status,
    get_handoff_parent_terminal_id,
    get_pending_handoff_child_terminal_ids,
    get_pending_messages,
    get_provider_execution_admission_queue,
    get_workflow_status,
    get_workflow_turn_for_inbox,
    handoff_child_cleanup_acknowledged,
    handoff_child_cleanup_is_acknowledged,
    keep_managed_handoff_continuation_retryable,
    mark_child_assignment_result_delivered,
    mark_child_assignment_result_failed,
    mark_workflow_turn_sent,
    mark_workflow_turn_sent_for_inbox,
    materialize_deferred_handoff_result_turn_for_inbox,
    reconcile_closed_workflow_inbox_transports,
    reconcile_superseded_workflow_turns_for_restart,
    request_workflow_provider_reconnect,
    requeue_unacknowledged_child_assignment_results,
    requeue_unadmitted_workflow_turns_for_restart,
    requeue_workflow_turn,
    terminalize_missing_terminal_assignments_for_restart,
    update_message_status,
)
from cli_agent_orchestrator.constants import TERMINAL_LOG_DIR
from cli_agent_orchestrator.models.inbox import MessageStatus, OrchestrationType
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.plugins import PluginRegistry
from cli_agent_orchestrator.providers.manager import provider_manager
from cli_agent_orchestrator.services import terminal_service, workflow_service

logger = logging.getLogger(__name__)
_provider_queue_reconcile_lock = threading.Lock()


def _message_for_delivery(message, logical_turn_id: int | None = None) -> str:
    """Add acknowledgement metadata only to durable assigned callbacks.

    Generic Inbox messages retain their byte-for-byte payload. The envelope is
    rebuilt from the assignment relation instead of trusting optional text
    injection, so redelivery carries the same child identity after restart.
    """
    child_terminal_id = get_child_assignment_result_child_id(message.id)
    result_id = get_child_assignment_result_id(message.id)
    delivered = message.message
    if isinstance(child_terminal_id, str):
        acknowledgement = f'acknowledge_assigned_result(child_terminal_id="{child_terminal_id}")'
        if result_id is not None:
            acknowledgement = (
                f'read_delegation_result(result_id="{result_id}") and after incorporating it call '
                f'acknowledge_assigned_result(result_id="{result_id}", child_terminal_id="{child_terminal_id}") '
                f"(legacy form: {acknowledgement})"
            )
        delivered = (
            f"{message.message}\n\n"
            "[CAO assigned-result callback: "
            f"child_terminal_id={child_terminal_id}"
            f"{f'; result_id={result_id}' if result_id is not None else ''}. "
            f"After incorporating this result, call {acknowledgement}.]"
        )
    if logical_turn_id is not None:
        return workflow_service.admission_message(delivered, logical_turn_id)
    return delivered


def _get_log_tail(terminal_id: str, lines: int = 100) -> str:
    """Get last N lines from terminal log file.

    Default of 100 lines covers full-screen TUI providers where the idle
    prompt sits mid-screen with 30+ padding lines below it.
    Reading 100 lines via tail is still sub-millisecond.
    """
    log_path = TERMINAL_LOG_DIR / f"{terminal_id}.log"
    try:
        result = subprocess.run(
            ["tail", "-n", str(lines), str(log_path)], capture_output=True, text=True, timeout=1
        )
        return result.stdout
    except Exception:
        return ""


def _has_idle_pattern(terminal_id: str) -> bool:
    """Check if log tail contains idle pattern without expensive tmux calls."""
    tail = _get_log_tail(terminal_id)
    if not tail:
        return False

    try:
        provider = provider_manager.get_provider(terminal_id)
        if provider is None:
            return False
        idle_pattern = provider.get_idle_pattern_for_log()
        return bool(re.search(idle_pattern, tail))
    except Exception:
        return False


def _dispatch_pending_messages_with_admission(
    terminal_id: str, registry: PluginRegistry | None = None
) -> bool:
    """Dispatch the resident's FIFO head with an exact provider lease.

    Args:
        terminal_id: Terminal ID to check messages for

    Returns:
        bool: True if a message was sent, False otherwise

    Raises:
        ValueError: If provider not found for terminal
    """
    # Check for pending messages.  A parent terminal can begin a new workflow
    # after an earlier workflow was owner-gated, terminal, or cancelled.  The
    # old workflow's result notices are then deliberately ineligible for
    # transport, but they must not remain at the head of this FIFO forever and
    # strand a later OPEN workflow's durable handoff-result boundary.
    #
    # Any Inbox row bound to a closed workflow is historical transport, never
    # authority for a later semantic workflow. Result artifacts retain their
    # immutable projection independently; ordinary messages retain their
    # cancelled workflow turn. Terminalizing either transport cannot rebind or
    # revive it, and lets the next eligible FIFO item advance.
    while True:
        messages = get_pending_messages(terminal_id, limit=1)
        if not messages:
            return False

        message = messages[0]
        workflow_turn = (
            get_workflow_turn_for_inbox(message.id) if isinstance(message.id, int) else None
        )
        # Only the first notice in a handoff result batch owns the workflow
        # turn's ``inbox_message_id``. Later batch members carry that same
        # immutable result turn through ``delegation_results`` instead, so
        # checking the Inbox anchor alone would leave a cancelled non-anchor
        # permanently at the FIFO head after its parent starts a new workflow.
        result_id = message.result_id if isinstance(message.result_id, str) else None
        result = get_delegation_result(result_id) if result_id else None
        if (
            message.kind == "delegation_result_notice"
            and result is not None
            and result.get("delivery_status") == "cancelled"
        ):
            if not update_message_status(message.id, MessageStatus.FAILED):
                # Another reconciler won the terminal transition. Re-read the
                # FIFO head so this observer stays idempotent.
                continue
            logger.info(
                "Marked cancelled delegation-result notice %s failed before delivery",
                message.id,
            )
            continue
        if workflow_turn is None or workflow_turn["status"] == "open":
            break
        if not update_message_status(message.id, MessageStatus.FAILED):
            # Another reconciler won the terminal transition. Re-read the
            # FIFO head so this observer stays idempotent.
            continue
        logger.info(
            "Marked ineligible Inbox transport %s failed for closed workflow",
            message.id,
        )

    # Delegated-result notices are never ordinary Inbox text. A late handoff
    # result may intentionally remain turn-less while another callback awaits
    # admission; materialize its normal successor only after that fence clears.
    if message.kind == "delegation_result_notice" and workflow_turn is None:
        materialized = materialize_deferred_handoff_result_turn_for_inbox(message.id)
        if materialized == DEFER_UNADMITTED:
            logger.info("Deferring result notice %s behind an unadmitted active turn", message.id)
            return False
        workflow_turn = get_workflow_turn_for_inbox(message.id) if materialized else None
    if workflow_turn is None and message.kind == "message" and isinstance(message.id, int):
        ensured_turn = ensure_workflow_turn_for_inbox(message.id)
        workflow_turn = get_workflow_turn_for_inbox(message.id) if ensured_turn else None
    if workflow_turn is None:
        logger.warning("Suppressing Inbox wake %s without a durable provider turn", message.id)
        return False

    delivery_messages = [message]

    # Get provider and check status
    provider = provider_manager.get_provider(terminal_id)
    if provider is None:
        raise ValueError(f"Provider not found for terminal {terminal_id}")
    # Let the provider use its own default tail_lines. Each provider knows how
    # many lines it needs to reliably detect the idle prompt (TUI providers
    # need 50 lines due to TUI padding). Previously this passed
    # INBOX_SERVICE_TAIL_LINES=5, which was too few for TUI-based providers —
    # the idle prompt was never found, so messages stayed PENDING forever.
    status = provider.get_status()

    # A stale privileged sidecar can report its generation fence while an
    # Inbox row is already eligible for the fast path. Persist the reconnect
    # barrier before any activation/claim so this path cannot overtake runtime
    # replacement merely because the provider footer is Ready.
    if (
        terminal_service.provider_runtime_sidecar_reconnect_required(terminal_id, provider=provider)
        is True
    ):
        persisted = request_workflow_provider_reconnect(terminal_id)
        if not persisted:
            logger.warning(
                "Provider reconnect signal for %s had no admitted active turn",
                terminal_id,
            )
        logger.info("Deferring Inbox wake %s behind provider reconnect", message.id)
        return False

    # Codex discovers a persisted exit sentinel during get_status(). Refresh
    # before reading liveness so a stale provider instance cannot paste Inbox
    # text into the shell after its CLI exited. Keep only the managed
    # continuation queued for its bounded recovery/lifecycle path; ordinary
    # Inbox messages retain their terminal failed behavior instead of becoming
    # an unbounded pending retry.
    if not provider.is_process_alive():
        logger.info("Terminal %s provider process is not resumably ready", terminal_id)
        if message.kind != "handoff_recovery_continuation":
            update_message_status(message.id, MessageStatus.FAILED)
            mark_child_assignment_result_failed(message.id)
        return False

    if status not in (TerminalStatus.IDLE, TerminalStatus.COMPLETED):
        logger.debug(f"Terminal {terminal_id} not ready (status={status})")
        return False

    # Send message. Inbox-queued delivery is only reached via the send_message
    # MCP tool, so the orchestration_type is always "send_message" here — the
    # synchronous handoff/assign paths bypass the inbox and pass their own
    # orchestration_type directly to send_input().
    try:
        workflow_claim = None
        activation = None
        if workflow_turn is not None and workflow_turn.get("kind") == "handoff_result":
            # Claiming seals membership before this query returns its payload.
            # A result finalized after this point receives its own later
            # boundary instead of becoming a non-anchor orphan.
            handoff_batch = claim_handoff_result_batch_for_inbox(message.id)
            if handoff_batch == DEFER_UNADMITTED:
                logger.info("Deferring Inbox wake %s behind an unadmitted active turn", message.id)
                return False
            if not isinstance(handoff_batch, dict):
                logger.info("Suppressing Inbox wake %s without a sealed handoff batch", message.id)
                return False
            workflow_claim = handoff_batch
            activation = workflow_claim["id"]
            delivery_messages = workflow_claim["messages"]
        else:
            activation = (
                activate_workflow_turn_for_inbox(message.id)
                if isinstance(message.id, int) and workflow_turn is not None
                else None
            )
        if activation == DEFER_UNADMITTED:
            logger.info("Deferring Inbox wake %s behind an unadmitted active turn", message.id)
            return False
        if workflow_turn is not None and not isinstance(activation, int):
            logger.info("Suppressing Inbox wake %s without an active workflow turn", message.id)
            return False
        logical_turn_id = activation if isinstance(activation, int) else None
        if logical_turn_id is not None:
            # Activation binds the Inbox turn, but the durable claim owns the
            # physical send. Concurrent ready ticks can therefore observe the
            # same PENDING Inbox row yet only one reaches transport.
            workflow_claim = (
                workflow_claim
                if workflow_claim is not None
                else claim_workflow_turn(terminal_id, inbox_message_id=message.id)
            )
            if workflow_claim is None or workflow_claim["id"] != logical_turn_id:
                logger.info("Suppressing Inbox wake %s without a live turn claim", message.id)
                return False
        delivered_payload = "\n\n".join(
            _message_for_delivery(delivery_message) for delivery_message in delivery_messages
        )
        delivery_message = (
            workflow_service.admission_message(delivered_payload, logical_turn_id)
            if logical_turn_id is not None
            else delivered_payload
        )
        execution_kwargs = (
            {"logical_turn_id": logical_turn_id} if logical_turn_id is not None else {}
        )
        if registry is None:
            terminal_service.send_input(terminal_id, delivery_message, **execution_kwargs)
        else:
            terminal_service.send_input(
                terminal_id,
                delivery_message,
                registry=registry,
                sender_id=message.sender_id,
                orchestration_type=OrchestrationType.SEND_MESSAGE,
                **execution_kwargs,
            )
        for delivery in delivery_messages:
            update_message_status(delivery.id, MessageStatus.DELIVERED)
        # This is only an at-least-once transport receipt.  An assigned
        # parent's barrier stays active until that same parent explicitly
        # acknowledges having consumed the child result.
        for delivery in delivery_messages:
            mark_child_assignment_result_delivered(delivery.id)
        if workflow_claim is not None:
            if not mark_workflow_turn_sent(
                workflow_claim["id"],
                workflow_claim["claim_token"],
                workflow_claim["claim_generation"],
            ):
                logger.warning(
                    "Inbox wake %s lost its workflow turn claim after transport", message.id
                )
        elif isinstance(message.id, int):
            mark_workflow_turn_sent_for_inbox(message.id)
        logger.info(f"Delivered message {message.id} to terminal {terminal_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to send message {message.id} to {terminal_id}: {e}")
        if workflow_claim is not None:
            requeue_workflow_turn(
                workflow_claim["id"],
                workflow_claim["claim_token"],
                workflow_claim["claim_generation"],
            )
        from cli_agent_orchestrator.services.operations_service import AdmissionDenied

        if isinstance(e, AdmissionDenied) and e.reason_code in {
            "PROVIDER_EXECUTION_CAPACITY_EXHAUSTED",
            "PROVIDER_EXECUTION_TERMINAL_BUSY",
            "RESOURCE_HEALTH_REJECTED",
            "TERMINAL_RUNTIME_OPERATION_BUSY",
            "TERMINAL_RUNTIME_RECONNECT_PENDING",
        }:
            logger.info(
                "Keeping Inbox wake %s queued for provider admission (%s)",
                message.id,
                e.reason_code,
            )
            return False
        for delivery in delivery_messages:
            if not keep_managed_handoff_continuation_retryable(delivery.id):
                update_message_status(delivery.id, MessageStatus.FAILED)
                mark_child_assignment_result_failed(delivery.id)
        raise


def check_and_send_pending_messages(
    terminal_id: str, registry: PluginRegistry | None = None
) -> bool:
    """Wake the shared durable queue instead of granting direct priority."""
    if not get_pending_messages(terminal_id, limit=1):
        return False
    return reconcile_provider_execution_queue(registry, observe_open_workflows=False) > 0


def reconcile_pending_messages(registry: PluginRegistry | None = None) -> int:
    """Replay durable pending inbox rows once after a server restart.

    The watcher only observes future log writes. This bounded sweep makes a
    callback saved immediately before restart deliverable without a competing
    queue or a synthetic terminal event.
    """
    terminalize_missing_terminal_assignments_for_restart()
    arm_handoff_continuations_for_restart()
    reconcile_superseded_workflow_turns_for_restart()
    requeue_unadmitted_workflow_turns_for_restart()
    requeue_unacknowledged_child_assignment_results()
    reconcile_handoff_continuations(registry)
    return reconcile_provider_execution_queue(registry)


def reconcile_provider_execution_queue(
    registry: PluginRegistry | None = None, *, observe_open_workflows: bool = True
) -> int:
    """Wake durable provider inputs in one deterministic cross-source FIFO.

    Each source retains its own claim/transport CAS.  The non-blocking process
    guard prevents recursive status observations from restarting the scan; the
    durable ordering and provider lease CAS remain authoritative across
    processes and after crashes.
    """
    if not _provider_queue_reconcile_lock.acquire(blocking=False):
        return 0
    try:
        # Remove rows whose exact workflow has already closed before building
        # the merged queue. Otherwise that stale Inbox head suppresses the
        # same resident's queued OPEN-workflow turn for the whole scan. The
        # transaction is restart-safe and dispatch repeats the fence for a
        # close-vs-scan race.
        reconcile_closed_workflow_inbox_transports()
        admitted = 0
        for candidate in get_provider_execution_admission_queue():
            terminal_id = candidate["terminal_id"]
            try:
                if candidate["source"] == "inbox":
                    admitted += int(
                        _dispatch_pending_messages_with_admission(terminal_id, registry=registry)
                    )
                else:
                    admitted += int(
                        workflow_service.reconcile_root_workflow(terminal_id, registry=registry)
                    )
            except Exception as exc:
                logger.debug(
                    "Queued %s provider admission deferred for %s: %s",
                    candidate["source"],
                    terminal_id,
                    exc,
                )
        # Only after all already-durable provider inputs had their fair attempt
        # may OPEN roots observe completed output and manufacture an F13
        # successor.  Its newly queued turn participates in the next merged
        # scan, so a hot root cannot jump either pending source.
        if observe_open_workflows:
            admitted += workflow_service.reconcile_open_workflows(registry)
        return admitted
    finally:
        _provider_queue_reconcile_lock.release()


def wake_provider_execution_queue(registry: PluginRegistry | None = None) -> int:
    """Best-effort fast wake after a committed release.

    Release/closure is already durable when this runs.  A probe failure must
    not turn that successful lifecycle operation into an indeterminate API
    result; startup and watchdog reconciliation deterministically retry the
    same durable queue.
    """
    try:
        return reconcile_provider_execution_queue(registry)
    except Exception as exc:
        logger.warning("Provider execution queue wake deferred to reconciliation: %s", exc)
        return 0


def reconcile_handoff_continuations(
    registry: PluginRegistry | None = None, child_terminal_id: str | None = None
) -> int:
    """Capture completed direct handoffs into the ordinary durable Inbox path.

    The live MCP wait remains the F11 fast path.  This recovery path is only
    able to claim a handoff that still has no result row, so startup scans and
    repeated log events produce one logical continuation and no duplicate wake.
    """
    child_ids = (
        [child_terminal_id] if child_terminal_id else get_pending_handoff_child_terminal_ids()
    )
    queued = 0
    for child_id in child_ids:
        if child_terminal_id and child_id not in get_pending_handoff_child_terminal_ids():
            continue
        try:
            state = get_handoff_child_status(child_id)
            result = get_handoff_child_result_message(child_id)
            terminal = terminal_service.get_terminal(child_id)
            if result is None:
                if state == "handoff_recovery_awaiting_result":
                    # A provider rebuilt after restart may report PROCESSING
                    # across one or two immediate polls before settling as
                    # COMPLETED. Recovery has no later log write to trigger
                    # those observations, so make at most two bounded rereads
                    # without delay. Stop as soon as the child completes or
                    # is no longer running; the completed path below retains
                    # the existing stable-output and CAS fences.
                    for _ in range(2):
                        if (
                            terminal.get("lifecycle") != "running"
                            or terminal.get("status") == TerminalStatus.COMPLETED.value
                        ):
                            break
                        terminal = terminal_service.get_terminal(child_id)
                if state == "handoff_awaiting_result":
                    # Preserve F11's live fast path while the parent call is
                    # still processing.  Once that parent has crossed its own
                    # final/idle boundary, capture exactly one durable result
                    # and let the ordinary Inbox wake its next turn.
                    parent_id = get_handoff_parent_terminal_id(child_id)
                    if parent_id is None or get_workflow_status(parent_id) not in (None, "open"):
                        continue
                    parent = terminal_service.get_terminal(parent_id)
                    if parent.get("lifecycle") != "running" or parent.get("status") not in (
                        TerminalStatus.IDLE.value,
                        TerminalStatus.COMPLETED.value,
                    ):
                        continue
                # Recovery must see the same completed terminal and the same
                # valid final extraction twice before it creates a durable
                # Inbox effect. A child can be cleanly exited after reporting
                # COMPLETED, so its terminal lifecycle is not evidence that a
                # durable final is invalid.
                if (
                    terminal.get("status") != TerminalStatus.COMPLETED.value
                    or terminal.get("lifecycle") not in ("running", "exited")
                    or state is None
                ):
                    continue
                output = terminal_service.get_output(child_id, terminal_service.OutputMode.LAST)
                from cli_agent_orchestrator.mcp_server.server import _handoff_output_problem

                if _handoff_output_problem(output) is not None:
                    if terminal.get("lifecycle") == "exited":
                        cancel_child_assignments_for_terminal(child_id)
                    continue
                verified_terminal = terminal_service.get_terminal(child_id)
                if verified_terminal.get(
                    "status"
                ) != TerminalStatus.COMPLETED.value or verified_terminal.get("lifecycle") not in (
                    "running",
                    "exited",
                ):
                    continue
                verified_output = terminal_service.get_output(
                    child_id, terminal_service.OutputMode.LAST
                )
                if (
                    _handoff_output_problem(verified_output) is not None
                    or verified_output != output
                ):
                    # An exited child has no future final boundary to observe.
                    # Do not leave its recovery assignment awaiting forever;
                    # retain an explicit INCOMPLETE lifecycle result instead.
                    if verified_terminal.get("lifecycle") == "exited":
                        cancel_child_assignments_for_terminal(child_id)
                    continue
                result, duplicate = create_handoff_child_result_message(child_id, output)
                if result is None:
                    continue
                if not duplicate:
                    queued += 1
                terminal = verified_terminal

            if not handoff_child_cleanup_is_acknowledged(child_id):
                lifecycle = terminal.get("lifecycle")
                if lifecycle in ("running", "exit_pending"):
                    # The first dispatch remains fenced by the provider's
                    # COMPLETED boundary. Once exit has been durably claimed,
                    # later reconciliations must observe that same request
                    # until it positively settles instead of treating
                    # ``exit_pending`` as an ineligible lifecycle.
                    if terminal.get("status") != TerminalStatus.COMPLETED.value:
                        if lifecycle == "running":
                            continue
                    try:
                        exit_result = terminal_service.exit_terminal(child_id)
                    except Exception as exc:
                        logger.warning("Handoff child cleanup failed for %s: %s", child_id, exc)
                        continue
                    if not exit_result.success or exit_result.lifecycle != "exited":
                        continue
                elif lifecycle != "exited":
                    continue
                if not handoff_child_cleanup_acknowledged(child_id):
                    continue
            # Only an acknowledged cleanup may wake the same parent.  Repeated
            # retries reuse this one Inbox row and the normal transport claim.
            check_and_send_pending_messages(result.receiver_id, registry=registry)
            workflow_service.reconcile_root_workflow(result.receiver_id, registry=registry)
        except Exception as exc:
            logger.warning("Handoff continuation reconciliation failed for %s: %s", child_id, exc)
    return queued


class LogFileHandler(FileSystemEventHandler):
    """Handler for terminal log file changes."""

    def __init__(self, registry: PluginRegistry | None = None) -> None:
        """Initialize the log file handler with an optional plugin registry."""

        super().__init__()
        self._registry = registry

    def on_modified(self, event):
        """Handle file modification events."""
        if isinstance(event, FileModifiedEvent) and event.src_path.endswith(".log"):
            log_path = Path(event.src_path)
            terminal_id = log_path.stem
            logger.debug(f"Log file modified: {terminal_id}.log")
            self._handle_log_change(terminal_id)

    def _handle_log_change(self, terminal_id: str):
        """Handle log file change and attempt message delivery."""
        try:
            # A direct handoff child can complete after the service restarts
            # while its parent is idle. Claiming its result here creates one
            # durable Inbox wake for that same parent before normal delivery.
            reconcile_handoff_continuations(self._registry, child_terminal_id=terminal_id)
            # A parent log write can be its completed turn, immediately before
            # F13 submits an automatic successor.  Capture each already
            # completed direct child of this parent first: after that send,
            # the child's live ``mode=last`` output can be the successor's
            # legacy prose instead of the child's completed provider result.
            # The per-child reconciliation keeps the existing two-read stable
            # output fence and durable-result idempotency intact.
            for child_terminal_id in get_pending_handoff_child_terminal_ids():
                if get_handoff_parent_terminal_id(child_terminal_id) == terminal_id:
                    reconcile_handoff_continuations(
                        self._registry, child_terminal_id=child_terminal_id
                    )
            # Observe Ready/Completed first so its exact provider lease is
            # released. The global reconciler then admits already-queued
            # turns oldest-first before manufacturing this root's F13
            # successor, preventing a hot supervisor from monopolizing slots.
            try:
                terminal_service.get_terminal(terminal_id)
            except Exception as exc:
                logger.debug(
                    "Provider execution boundary not yet observable for %s: %s",
                    terminal_id,
                    exc,
                )
            reconcile_provider_execution_queue(self._registry)

        except Exception as e:
            logger.error(f"Error handling log change for {terminal_id}: {e}")
