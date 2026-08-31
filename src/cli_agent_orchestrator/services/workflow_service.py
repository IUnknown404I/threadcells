"""Durable top-level workflow/run loop.

Provider ``COMPLETED`` is an observation, not a semantic completion.  This
service records safe continuation turns and only stops an OPEN workflow after
an explicit terminal/owner-gate/cancelled transition.
"""

import logging
import threading
from datetime import datetime

from cli_agent_orchestrator.clients.database import (
    activate_workflow_turn,
    cancel_workflows_for_terminal,
    claim_workflow_provider_reconnect,
    claim_workflow_turn,
    complete_workflow_provider_reconnect,
    fail_workflow_provider_reconnect_attempt,
    get_handoff_child_status,
    get_open_workflow_root_terminal_ids,
    get_parent_completion_barrier,
    get_pending_message_receiver_ids,
    get_pending_workflow_provider_reconnect_root_terminal_ids,
    get_queued_workflow_root_terminal_ids,
    get_workflow_provider_outcome_observation,
    mark_workflow_turn_sent,
    observe_workflow_final,
    observe_workflow_processing,
    observe_workflow_provider_outcome,
    observe_workflow_ready,
    prepare_workflow_input,
    renew_workflow_provider_reconnect,
    renew_workflow_turn_claim,
    request_workflow_provider_reconnect,
    requeue_expired_workflow_turn_claims,
    requeue_workflow_turn,
    set_workflow_terminal_state,
    start_workflow_input,
    workflow_has_active_queued_external_input,
    workflow_provider_reconnect_pending,
)
from cli_agent_orchestrator.models.inbox import ChildAssignmentStatus, OrchestrationType
from cli_agent_orchestrator.models.provider import ProviderTurnOutcome
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.plugins import PluginRegistry
from cli_agent_orchestrator.services import terminal_service

logger = logging.getLogger(__name__)


class ProviderResumeIdentityUnavailable(RuntimeError):
    """Reconnect has no launch-bound identity it can safely resume."""

    reconnect_outcome_code = "resume_identity_unavailable_or_unproven"


class _WorkflowTurnClaimHeartbeat:
    """Keep one transport claim alive while a provider call is in flight."""

    def __init__(self, turn: dict) -> None:
        self._turn = turn
        self._stop = threading.Event()
        self.lost = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self._stop.set()
        self._thread.join(timeout=1)

    def _run(self) -> None:
        # Renew well inside the lease.  The wait is interruptible so normal
        # sends do not add shutdown latency.
        from cli_agent_orchestrator.clients.database import WORKFLOW_TURN_CLAIM_LEASE_SECONDS

        interval = max(1, WORKFLOW_TURN_CLAIM_LEASE_SECONDS // 3)
        while not self._stop.wait(interval):
            if not renew_workflow_turn_claim(
                self._turn["id"], self._turn["claim_token"], self._turn["claim_generation"]
            ):
                self.lost = True
                self._stop.set()
                return


class _ProviderReconnectClaimHeartbeat:
    """Keep exact reconnect ownership while provider exit/resume is blocking."""

    def __init__(self, root_terminal_id: str, reconnect: dict) -> None:
        self._root_terminal_id = root_terminal_id
        self._reconnect = reconnect
        self._stop = threading.Event()
        self.lost = False
        self.error: Exception | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self._stop.set()
        self._thread.join(timeout=1)

    def _run(self) -> None:
        from cli_agent_orchestrator.clients.database import (
            WORKFLOW_PROVIDER_RECONNECT_LEASE_SECONDS,
        )

        interval = max(1, WORKFLOW_PROVIDER_RECONNECT_LEASE_SECONDS // 3)
        while not self._stop.wait(interval):
            try:
                renewed = renew_workflow_provider_reconnect(
                    self._root_terminal_id,
                    self._reconnect["turn_id"],
                    self._reconnect["claim_token"],
                )
            except Exception as exc:
                self.error = exc
                renewed = False
            if not renewed:
                self.lost = True
                self._stop.set()
                return


def record_external_input(root_terminal_id: str) -> int:
    """Create the durable root turn before an API-originated user prompt."""
    from cli_agent_orchestrator.services.operations_service import (
        workflow_execution_admission_fence,
    )

    with workflow_execution_admission_fence():
        turn_id = start_workflow_input(root_terminal_id)
    if turn_id is None:
        raise RuntimeError(f"Could not create workflow turn for {root_terminal_id}")
    return turn_id


def prepare_external_input(
    root_terminal_id: str, payload: str, *, request_id: str | None = None
) -> dict:
    """Persist a user/scheduled input, deferring behind runtime recovery."""
    from cli_agent_orchestrator.services.operations_service import (
        workflow_execution_admission_fence,
    )

    with workflow_execution_admission_fence():
        prepared = prepare_workflow_input(
            root_terminal_id,
            payload,
            request_id=request_id,
            require_live_terminal=True,
        )
    if prepared is None:
        raise RuntimeError(f"Could not create workflow turn for {root_terminal_id}")
    return prepared


def complete_workflow(root_terminal_id: str, reason: str = "") -> bool:
    """Terminalize only after every owned child completion barrier is released."""
    completed = set_workflow_terminal_state(
        root_terminal_id, "terminal", reason or None, require_no_active_children=True
    )
    if completed:
        from cli_agent_orchestrator.services.inbox_service import wake_provider_execution_queue

        wake_provider_execution_queue()
    return completed


def owner_gate_workflow(root_terminal_id: str, reason: str) -> bool:
    gated = set_workflow_terminal_state(root_terminal_id, "owner_gate", reason)
    if gated:
        from cli_agent_orchestrator.services.inbox_service import wake_provider_execution_queue

        wake_provider_execution_queue()
    return gated


def cancel_workflow(root_terminal_id: str) -> int:
    cancelled = cancel_workflows_for_terminal(root_terminal_id)
    if cancelled:
        from cli_agent_orchestrator.services.inbox_service import wake_provider_execution_queue

        wake_provider_execution_queue()
    return cancelled


def admission_envelope(turn_id: int) -> str:
    """Describe the server-bound admission capability for one model input."""
    envelope = (
        f"[CAO workflow input: logical-turn={turn_id}]\n"
        "Before any model-dependent work, call "
        f"claim_workflow_turn_receipt(logical_turn_id={turn_id}). "
        "Preserve the returned resume_token across context compaction. If this admitted "
        "model execution is interrupted before its work is complete, call the same tool "
        "with that resume_token; a safe resume receives a new logical_turn_id. If it "
        "returns accepted=false, this is a duplicate or a closed workflow: stop without "
        "creating another supervisor effect. Every privileged CAO operation (assign, "
        "handoff, send_message, acknowledgement, or workflow terminal transition) must "
        "use the logical_turn_id returned by the successful receipt call (normally "
        f"{turn_id}); the MCP runtime rejects duplicate or unadmitted effects.\n\n"
    )
    return envelope


def admission_message(message: str, turn_id: int) -> str:
    """Carry an input's durable admission identity into the model runtime."""
    return f"{admission_envelope(turn_id)}{message}"


def _continuation_message(kind: str, payload: str, turn_id: int) -> str:
    envelope = admission_envelope(turn_id)
    if kind == "open_final":
        return (
            envelope + "The workflow is durably OPEN. Review the completed turn, continue the "
            "approved work, or explicitly call complete_workflow / owner_gate_workflow.\n\n"
            f"Reason: {payload}"
        )
    return f"{envelope}{payload}"


def _reconcile_root_workflow_with_admission(
    root_terminal_id: str,
    registry: PluginRegistry | None = None,
    now: datetime | None = None,
    pending_inbox: bool | None = None,
    pending_reconnect: bool | None = None,
) -> bool:
    """Observe one root and submit at most one due, durable continuation."""
    try:
        terminal = terminal_service.get_terminal(root_terminal_id)
    except Exception as exc:
        logger.debug("Workflow root %s is not currently observable: %s", root_terminal_id, exc)
        return False
    if terminal.get("lifecycle") != "running":
        return False
    status = terminal.get("status")

    # Capture the stale-sidecar fence before any status-specific early return.
    # The marker commonly appears during a still-PROCESSING model turn; the
    # reconnect itself must wait for that turn's provider lease, but Inbox or
    # service-restart transport must already observe a durable barrier.
    reconnect_signal = (
        terminal_service.provider_runtime_sidecar_reconnect_required(root_terminal_id) is True
    )
    if reconnect_signal:
        if not request_workflow_provider_reconnect(root_terminal_id, now=now):
            logger.warning(
                "Provider reconnect signal for %s had no admitted active turn",
                root_terminal_id,
            )
            return False
        pending_reconnect = True
    if status == TerminalStatus.PROCESSING.value:
        observe_workflow_processing(root_terminal_id, now=now)
        return False
    if status not in (TerminalStatus.IDLE.value, TerminalStatus.COMPLETED.value):
        return False

    # A promoted API must never let its stale in-process MCP sidecar mutate
    # current state. When Codex surfaces the exact generation fence, persist
    # its conversation identity before exiting and resume that exact session
    # with a fresh MCP client. The durable lease prevents concurrent restarts;
    # the stored identity makes the exit/resume crash gap recoverable.
    if pending_reconnect is None:
        pending_reconnect = workflow_provider_reconnect_pending(root_terminal_id)
    if pending_reconnect:
        reconnect = claim_workflow_provider_reconnect(root_terminal_id, now=now)
        if reconnect is None:
            return False
        if reconnect.get("exhausted"):
            return False
        try:
            with _ProviderReconnectClaimHeartbeat(root_terminal_id, reconnect) as heartbeat:

                def side_effect_guard() -> bool:
                    if heartbeat.lost:
                        return False
                    try:
                        renewed = renew_workflow_provider_reconnect(
                            root_terminal_id,
                            reconnect["turn_id"],
                            reconnect["claim_token"],
                            now=now,
                        )
                    except Exception as exc:
                        heartbeat.error = exc
                        renewed = False
                    if not renewed:
                        heartbeat.lost = True
                    return renewed

                resume_identity = reconnect["resume_identity"]
                if (
                    not resume_identity
                    or reconnect.get("resume_identity_authoritative") is not True
                ):
                    raise ProviderResumeIdentityUnavailable(
                        "provider reconnect has no authoritative launch-bound identity"
                    )
                if reconnect["attempt_state"] == "reserved":
                    terminal_service.verify_provider_runtime_sidecar_resume_identity(
                        root_terminal_id, resume_identity
                    )
                terminal_service.request_provider_runtime_sidecar_reconnect(
                    root_terminal_id,
                    reconnect["turn_id"],
                    resume_identity,
                    registry=registry,
                    claim_token=reconnect["claim_token"],
                    attempt_token=reconnect["attempt_token"],
                    attempt_state=reconnect["attempt_state"],
                    side_effect_guard=side_effect_guard,
                )
            if heartbeat.lost:
                raise RuntimeError("provider reconnect claim expired during resume")
            if not complete_workflow_provider_reconnect(
                root_terminal_id,
                reconnect["turn_id"],
                reconnect["claim_token"],
                reconnect["attempt_token"],
            ):
                raise RuntimeError("provider reconnect completion lost admission")
        except Exception as exc:
            outcome_code = getattr(exc, "reconnect_outcome_code", "reconnect_failed")
            failed = fail_workflow_provider_reconnect_attempt(
                root_terminal_id,
                reconnect["turn_id"],
                reconnect["claim_token"],
                reconnect["attempt_token"],
                outcome_code,
                now=now,
            )
            logger.warning(
                "Provider sidecar reconnect request failed for %s (outcome=%s, persisted=%s): %s",
                root_terminal_id,
                outcome_code,
                failed,
                exc,
            )
            return False
        return False

    outcome_observation = get_workflow_provider_outcome_observation(root_terminal_id)
    provider_outcome = (
        terminal_service.provider_turn_outcome(root_terminal_id, outcome_observation["cursor"])
        if outcome_observation is not None
        else None
    )
    if isinstance(provider_outcome, ProviderTurnOutcome):
        persisted = observe_workflow_provider_outcome(
            root_terminal_id,
            outcome_observation["turn_id"],
            outcome_observation["cursor"],
            provider_outcome.code,
            provider_outcome.detail_code,
            now=now,
        )
        logger.info(
            "Provider outcome %s observed for %s (persisted=%s)",
            provider_outcome.code,
            root_terminal_id,
            persisted,
        )
        # A policy outcome never owns an automatic retry or an Inbox-driven
        # continuation. A deliberate external Composer input can supersede the
        # finished turn through the normal exactly-once admission contract.
        return False

    # Inbox-backed provider inputs already own their exact logical turn and
    # must win over a synthetic open-final successor. This also gives the
    # workflow daemon durable retry ownership when a Ready transition occurs
    # without a fresh terminal-log event.
    active_external_head = workflow_has_active_queued_external_input(root_terminal_id)
    if pending_inbox is None:
        pending_inbox = root_terminal_id in set(get_pending_message_receiver_ids())
    if pending_inbox and not active_external_head:
        from cli_agent_orchestrator.services.inbox_service import check_and_send_pending_messages

        check_and_send_pending_messages(root_terminal_id, registry=registry)
        return False

    active_children, _failed_children = get_parent_completion_barrier(root_terminal_id)
    if active_children and not active_external_head:
        # An assigned-child result can be persisted just after the receiver's
        # one immediate Inbox probe observes it as busy.  Its own log then has
        # no reason to change, so the filesystem watcher alone cannot retry
        # delivery.  The durable workflow loop owns this bounded retry while
        # the parent barrier is active; it never manufactures a second message.
        from cli_agent_orchestrator.services.inbox_service import check_and_send_pending_messages

        check_and_send_pending_messages(root_terminal_id, registry=registry)
        return False

    # A direct-handoff child can also have its own OPEN F13 workflow.  Its
    # result boundary belongs exclusively to the live wait or restart recovery
    # path, both of which validate stable output and coordinate cleanup.  F13
    # must therefore hard-defer while the relation is unresolved: submitting
    # an ``open_final`` successor would replace ``mode=last`` before either
    # owner can capture the result, while claiming it here would strand a
    # direct result without the owning cleanup/continuation path.  An active
    # assigned-child barrier above is deliberately serviced first: that child
    # may be the direct-handoff worker's dependency and its persisted callback
    # is the only way it can continue to its own final result.
    if not active_external_head and get_handoff_child_status(root_terminal_id) in (
        ChildAssignmentStatus.HANDOFF_AWAITING_RESULT.value,
        ChildAssignmentStatus.HANDOFF_RECOVERY_AWAITING_RESULT.value,
    ):
        # A same-child recovery continuation is an Inbox-backed provider
        # input, not an F13 successor. The first immediate delivery probe can
        # legitimately see Codex's short completion-debounce PROCESSING
        # window. Once the provider becomes ready, the workflow daemon is the
        # durable retry owner for that exact Inbox row and logical turn. Do not
        # return before probing it: that would leave a scheduled recovery
        # continuation pending forever unless an unrelated terminal log event
        # happened to arrive.
        #
        # ``check_and_send_pending_messages`` claims the existing turn before
        # transport and marks it sent only after transport accepts it. Thus
        # repeated daemon ticks retry N+1, never create N+2, and remain a
        # no-op after successful provider delivery.
        from cli_agent_orchestrator.services.inbox_service import check_and_send_pending_messages

        check_and_send_pending_messages(root_terminal_id, registry=registry)
        # This remains a handoff-recovery observation rather than an F13
        # workflow successor, so retain the historical ``False`` result even
        # when the Inbox transport accepted the provider input.
        return False

    if status == TerminalStatus.COMPLETED.value:
        observe_workflow_final(root_terminal_id, now=now)
    else:
        observe_workflow_ready(root_terminal_id, now=now)

    turn = claim_workflow_turn(root_terminal_id, now=now)
    if turn is None:
        return False
    # This compare-and-set is the last durable fence before the irreversible
    # tmux side effect.  A lease that was reclaimed by another reconciler
    # therefore cannot be used by a stale worker to send its old attempt.
    if not renew_workflow_turn_claim(
        turn["id"], turn["claim_token"], turn["claim_generation"], now=now
    ):
        logger.info("Workflow continuation %s was fenced before transport", turn["id"])
        return False
    try:
        with _WorkflowTurnClaimHeartbeat(turn) as heartbeat:
            if not activate_workflow_turn(root_terminal_id, turn["id"]):
                logger.info("Workflow continuation %s lost admission binding", turn["id"])
                return False
            terminal_service.send_input(
                root_terminal_id,
                _continuation_message(turn["kind"], turn["payload"], turn["id"]),
                registry=registry,
                sender_id="cao-workflow",
                orchestration_type=OrchestrationType.SEND_MESSAGE,
                logical_turn_id=turn["id"],
            )
        if heartbeat.lost or not mark_workflow_turn_sent(
            turn["id"], turn["claim_token"], turn["claim_generation"], now=now
        ):
            logger.info(
                "Workflow continuation %s for %s was fenced after transport",
                turn["id"],
                root_terminal_id,
            )
            return False
        return True
    except Exception as exc:
        logger.warning(
            "Workflow continuation %s failed for %s: %s", turn["id"], root_terminal_id, exc
        )
        from cli_agent_orchestrator.services.operations_service import AdmissionDenied

        requeue_workflow_turn(
            turn["id"],
            turn["claim_token"],
            turn["claim_generation"],
            now=now,
            admission_reason_code=(exc.reason_code if isinstance(exc, AdmissionDenied) else None),
        )
        return False


def reconcile_root_workflow(
    root_terminal_id: str,
    registry: PluginRegistry | None = None,
    now: datetime | None = None,
    pending_inbox: bool | None = None,
    pending_reconnect: bool | None = None,
) -> bool:
    """Reconcile one root while fencing Full Cleanup workflow admission."""
    from cli_agent_orchestrator.services.operations_service import (
        workflow_execution_admission_fence,
    )

    with workflow_execution_admission_fence(nonblocking=True) as admitted:
        if not admitted:
            return False
        return _reconcile_root_workflow_with_admission(
            root_terminal_id,
            registry=registry,
            now=now,
            pending_inbox=pending_inbox,
            pending_reconnect=pending_reconnect,
        )


def reconcile_open_workflows(
    registry: PluginRegistry | None = None, now: datetime | None = None
) -> int:
    """Rehydrate all OPEN roots after restart or a watchdog event."""
    # Recover only expired pre-send claims.  A live reconciliation owns its
    # lease, while a process that died before transport cannot strand a turn.
    requeue_expired_workflow_turn_claims(now=now)
    sent = 0
    queued = get_queued_workflow_root_terminal_ids()
    queued_set = set(queued)
    roots = queued + [
        root for root in get_open_workflow_root_terminal_ids() if root not in queued_set
    ]
    if not roots:
        return 0
    pending_receivers = set(get_pending_message_receiver_ids())
    pending_reconnects = set(get_pending_workflow_provider_reconnect_root_terminal_ids())
    for root_terminal_id in roots:
        sent += int(
            reconcile_root_workflow(
                root_terminal_id,
                registry=registry,
                now=now,
                pending_inbox=root_terminal_id in pending_receivers,
                pending_reconnect=root_terminal_id in pending_reconnects,
            )
        )
    return sent
