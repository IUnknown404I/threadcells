"""Phase-B handoff/Inbox completion-barrier matrix."""

import asyncio
import os
from unittest.mock import MagicMock, patch

import pytest

from cli_agent_orchestrator.mcp_server.models import HandoffState
from cli_agent_orchestrator.mcp_server.server import _await_handoff_impl
from cli_agent_orchestrator.models.inbox import MessageStatus
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.services.inbox_service import (
    _dispatch_pending_messages_with_admission,
    _message_for_delivery,
    reconcile_handoff_continuations,
    reconcile_pending_messages,
)
from cli_agent_orchestrator.services.terminal_service import ExitTerminalResult


@patch("cli_agent_orchestrator.mcp_server.server._read_handoff_output")
@patch(
    "cli_agent_orchestrator.mcp_server.server.get_parent_completion_barrier", return_value=(1, 0)
)
@patch("cli_agent_orchestrator.mcp_server.server._read_handoff_terminal")
@patch("cli_agent_orchestrator.mcp_server.server.requests.post")
def test_completed_parent_with_assigned_child_returns_waiting_without_exit(
    mock_exit, mock_terminal, mock_barrier, mock_output
):
    mock_terminal.return_value = ("completed", "running")

    result = asyncio.run(_await_handoff_impl("parent01", timeout=0))

    assert result.state == HandoffState.WAITING
    assert "completion barrier" in result.message
    mock_output.assert_not_called()
    mock_exit.assert_not_called()


@patch("cli_agent_orchestrator.services.inbox_service.mark_child_assignment_result_delivered")
@patch("cli_agent_orchestrator.services.inbox_service.update_message_status")
@patch("cli_agent_orchestrator.services.inbox_service.terminal_service")
@patch("cli_agent_orchestrator.services.inbox_service.provider_manager")
@patch("cli_agent_orchestrator.services.inbox_service.get_pending_messages")
def test_callback_delivery_records_transport_only_until_parent_ack(
    mock_messages, mock_provider_manager, mock_terminal_service, mock_update, mock_mark_delivered
):
    message = MagicMock(
        id=11,
        message="child result",
        kind="message",
        result_id=None,
        sender_id="child01",
    )
    mock_messages.return_value = [message]
    mock_provider_manager.get_provider.return_value.get_status.return_value = (
        TerminalStatus.COMPLETED
    )
    mock_provider_manager.get_provider.return_value.is_process_alive.return_value = True

    with (
        patch(
            "cli_agent_orchestrator.services.inbox_service.get_workflow_turn_for_inbox",
            return_value={"id": 71, "status": "open", "kind": "inbox_message"},
        ),
        patch(
            "cli_agent_orchestrator.services.inbox_service.activate_workflow_turn_for_inbox",
            return_value=71,
        ),
        patch(
            "cli_agent_orchestrator.services.inbox_service.claim_workflow_turn",
            return_value={"id": 71, "claim_token": "claim", "claim_generation": 1},
        ),
        patch(
            "cli_agent_orchestrator.services.inbox_service.mark_workflow_turn_sent",
            return_value=True,
        ),
    ):
        assert _dispatch_pending_messages_with_admission("parent01") is True

    sent = mock_terminal_service.send_input.call_args
    assert sent.args[0] == "parent01"
    assert sent.args[1].endswith("child result")
    assert sent.kwargs == {"logical_turn_id": 71}
    mock_update.assert_called_once_with(11, MessageStatus.DELIVERED)
    mock_mark_delivered.assert_called_once_with(11)


@pytest.mark.parametrize("sender_injection", [False, True])
@patch("cli_agent_orchestrator.mcp_server.server._send_to_inbox")
@patch("cli_agent_orchestrator.services.inbox_service.get_child_assignment_result_child_id")
def test_assigned_callback_delivery_envelope_exposes_stable_child_identity_for_both_injection_modes(
    mock_child_identity, mock_send_to_inbox, sender_injection
):
    from cli_agent_orchestrator.mcp_server.server import _send_message_impl

    mock_send_to_inbox.return_value = {"success": True}
    mock_child_identity.return_value = "child-default-false"
    with patch(
        "cli_agent_orchestrator.mcp_server.server.ENABLE_SENDER_ID_INJECTION", sender_injection
    ):
        with patch.dict(os.environ, {"CAO_TERMINAL_ID": "child-default-false"}):
            _send_message_impl("parent01", "F12_CHILD_DONE")
    callback_text = mock_send_to_inbox.call_args.args[1]
    message = MagicMock(id=11, message=callback_text)

    delivered = _message_for_delivery(message)

    assert delivered.startswith("F12_CHILD_DONE")
    assert "[CAO assigned-result callback:" in delivered
    assert "child_terminal_id=child-default-false" in delivered
    assert 'acknowledge_assigned_result(child_terminal_id="child-default-false")' in delivered
    if sender_injection:
        assert "[Message from terminal child-default-false" in delivered
    else:
        assert "[Message from terminal" not in delivered


@patch("cli_agent_orchestrator.services.inbox_service.get_child_assignment_result_child_id")
def test_regular_inbox_delivery_remains_unchanged(mock_child_identity):
    message = MagicMock(id=12, message="ordinary Inbox message")
    mock_child_identity.return_value = None

    assert _message_for_delivery(message) == "ordinary Inbox message"


@patch(
    "cli_agent_orchestrator.mcp_server.server._read_handoff_output", return_value="parent report"
)
@patch(
    "cli_agent_orchestrator.mcp_server.server.get_parent_completion_barrier",
    side_effect=[(1, 0), (0, 0)],
)
@patch("cli_agent_orchestrator.mcp_server.server._read_handoff_terminal")
@patch("cli_agent_orchestrator.mcp_server.server.requests.post")
@patch.dict(os.environ, {"CAO_TERMINAL_ID": ""})
def test_parent_final_attempt_exits_only_after_durable_ack(
    mock_exit, mock_terminal, mock_barrier, mock_output
):
    # First completed observation is delivery-before-ack; the second reflects
    # the parent's explicit acknowledgement while await_handoff is still live.
    mock_terminal.return_value = ("completed", "running")
    mock_exit.return_value.raise_for_status.return_value = None

    result = asyncio.run(_await_handoff_impl("parent01", timeout=1))

    assert result.state == HandoffState.COMPLETED
    assert mock_barrier.call_count == 2
    assert mock_exit.call_count == 1
    assert mock_exit.call_args.args[0].endswith("/terminals/parent01/exit")


@patch(
    "cli_agent_orchestrator.services.inbox_service.reconcile_provider_execution_queue",
    return_value=1,
)
@patch(
    "cli_agent_orchestrator.services.inbox_service.reconcile_handoff_continuations", return_value=0
)
@patch(
    "cli_agent_orchestrator.services.inbox_service.requeue_unadmitted_workflow_turns_for_restart"
)
@patch("cli_agent_orchestrator.services.inbox_service.arm_handoff_continuations_for_restart")
@patch(
    "cli_agent_orchestrator.services.inbox_service.terminalize_missing_terminal_assignments_for_restart"
)
@patch(
    "cli_agent_orchestrator.services.inbox_service.requeue_unacknowledged_child_assignment_results"
)
def test_restart_reconciliation_requeues_unacknowledged_then_uses_existing_transport(
    mock_requeue,
    mock_terminalize,
    mock_arm,
    mock_requeue_turns,
    mock_handoff_reconcile,
    mock_queue_reconcile,
):
    mock_requeue.return_value = 1

    assert reconcile_pending_messages() == 1
    mock_terminalize.assert_called_once_with()
    mock_arm.assert_called_once_with()
    mock_requeue_turns.assert_called_once_with()
    mock_requeue.assert_called_once_with()
    mock_handoff_reconcile.assert_called_once_with(None)
    mock_queue_reconcile.assert_called_once_with(None)


@patch(
    "cli_agent_orchestrator.services.inbox_service.reconcile_provider_execution_queue",
    return_value=1,
)
@patch(
    "cli_agent_orchestrator.services.inbox_service.reconcile_handoff_continuations", return_value=0
)
@patch(
    "cli_agent_orchestrator.services.inbox_service.requeue_unadmitted_workflow_turns_for_restart"
)
@patch("cli_agent_orchestrator.services.inbox_service.arm_handoff_continuations_for_restart")
@patch(
    "cli_agent_orchestrator.services.inbox_service.terminalize_missing_terminal_assignments_for_restart"
)
@patch(
    "cli_agent_orchestrator.services.inbox_service.requeue_unacknowledged_child_assignment_results"
)
def test_restart_reconciliation_returns_merged_queue_result(
    mock_requeue,
    mock_terminalize,
    mock_arm,
    mock_requeue_turns,
    mock_handoff_reconcile,
    mock_queue_reconcile,
):
    assert reconcile_pending_messages() == 1
    mock_terminalize.assert_called_once_with()
    mock_arm.assert_called_once_with()
    mock_requeue_turns.assert_called_once_with()
    mock_handoff_reconcile.assert_called_once_with(None)
    mock_queue_reconcile.assert_called_once_with(None)


@patch("cli_agent_orchestrator.services.inbox_service.check_and_send_pending_messages")
@patch("cli_agent_orchestrator.services.inbox_service.handoff_child_cleanup_acknowledged")
@patch("cli_agent_orchestrator.services.inbox_service.handoff_child_cleanup_is_acknowledged")
@patch("cli_agent_orchestrator.services.inbox_service.terminal_service.exit_terminal")
@patch("cli_agent_orchestrator.services.inbox_service.terminal_service.get_output")
@patch("cli_agent_orchestrator.services.inbox_service.terminal_service.get_terminal")
@patch("cli_agent_orchestrator.services.inbox_service.get_handoff_child_result_message")
@patch("cli_agent_orchestrator.services.inbox_service.get_handoff_child_status")
@patch("cli_agent_orchestrator.services.inbox_service.create_handoff_child_result_message")
@patch("cli_agent_orchestrator.services.inbox_service.get_pending_handoff_child_terminal_ids")
def test_completed_handoff_after_restart_wakes_its_same_idle_parent_once(
    mock_pending,
    mock_create_result,
    mock_handoff_status,
    mock_result,
    mock_terminal,
    mock_output,
    mock_exit,
    mock_cleanup_acked,
    mock_cleanup_ack,
    mock_deliver,
):
    from datetime import datetime

    from cli_agent_orchestrator.models.inbox import InboxMessage

    mock_pending.return_value = ["child34"]
    mock_handoff_status.return_value = "handoff_recovery_awaiting_result"
    mock_result.return_value = None
    mock_terminal.return_value = {
        "status": TerminalStatus.COMPLETED.value,
        "lifecycle": "running",
    }
    mock_output.side_effect = ["child report", "child report"]
    mock_create_result.return_value = (
        InboxMessage(
            id=91,
            sender_id="child34",
            receiver_id="parent-ea22e2c9",
            message="child report",
            status=MessageStatus.PENDING,
            created_at=datetime.now(),
        ),
        False,
    )
    mock_cleanup_acked.return_value = False
    mock_cleanup_ack.return_value = True
    mock_exit.return_value = ExitTerminalResult(
        success=True,
        lifecycle="exited",
        outcome="command_delivered",
        message="exit confirmed",
        command_delivered=True,
    )

    assert reconcile_handoff_continuations() == 1

    mock_create_result.assert_called_once_with("child34", "child report")
    mock_exit.assert_called_once_with("child34")
    mock_cleanup_acked.assert_called_once_with("child34")
    mock_deliver.assert_called_once_with("parent-ea22e2c9", registry=None)


@patch("cli_agent_orchestrator.services.inbox_service.workflow_service.reconcile_root_workflow")
@patch("cli_agent_orchestrator.services.inbox_service.check_and_send_pending_messages")
@patch("cli_agent_orchestrator.services.inbox_service.handoff_child_cleanup_acknowledged")
@patch("cli_agent_orchestrator.services.inbox_service.handoff_child_cleanup_is_acknowledged")
@patch("cli_agent_orchestrator.services.inbox_service.terminal_service.exit_terminal")
@patch("cli_agent_orchestrator.services.inbox_service.terminal_service.get_terminal")
@patch("cli_agent_orchestrator.services.inbox_service.get_handoff_child_result_message")
@patch("cli_agent_orchestrator.services.inbox_service.get_handoff_child_status")
@patch("cli_agent_orchestrator.services.inbox_service.get_pending_handoff_child_terminal_ids")
def test_handoff_cleanup_waits_for_pending_exit_then_wakes_parent_once(
    mock_pending,
    mock_handoff_status,
    mock_result,
    mock_terminal,
    mock_exit,
    mock_cleanup_acked,
    mock_cleanup_ack,
    mock_deliver,
    mock_reconcile,
):
    from datetime import datetime

    from cli_agent_orchestrator.models.inbox import InboxMessage

    mock_pending.return_value = ["child-pending-exit"]
    mock_handoff_status.return_value = "handoff_result_queued"
    mock_result.return_value = InboxMessage(
        id=92,
        sender_id="child-pending-exit",
        receiver_id="parent-pending-exit",
        message="child report",
        status=MessageStatus.PENDING,
        created_at=datetime.now(),
    )
    mock_terminal.side_effect = [
        {"status": TerminalStatus.COMPLETED.value, "lifecycle": "running"},
        {"status": TerminalStatus.PROCESSING.value, "lifecycle": "exit_pending"},
    ]
    mock_exit.side_effect = [
        ExitTerminalResult(
            success=False,
            lifecycle="exit_pending",
            outcome="exit_pending",
            message="exit not confirmed",
            command_delivered=True,
        ),
        ExitTerminalResult(
            success=True,
            lifecycle="exited",
            outcome="already_exited",
            message="prior exit confirmed",
            command_delivered=False,
        ),
    ]
    mock_cleanup_acked.return_value = False
    mock_cleanup_ack.return_value = True

    assert reconcile_handoff_continuations() == 0
    mock_cleanup_ack.assert_not_called()
    mock_deliver.assert_not_called()
    mock_reconcile.assert_not_called()

    assert reconcile_handoff_continuations() == 0
    assert mock_exit.call_count == 2
    mock_cleanup_ack.assert_called_once_with("child-pending-exit")
    mock_deliver.assert_called_once_with("parent-pending-exit", registry=None)
    mock_reconcile.assert_called_once_with("parent-pending-exit", registry=None)


@patch("cli_agent_orchestrator.services.inbox_service.check_and_send_pending_messages")
@patch("cli_agent_orchestrator.services.inbox_service.handoff_child_cleanup_acknowledged")
@patch("cli_agent_orchestrator.services.inbox_service.handoff_child_cleanup_is_acknowledged")
@patch("cli_agent_orchestrator.services.inbox_service.terminal_service.exit_terminal")
@patch("cli_agent_orchestrator.services.inbox_service.terminal_service.get_output")
@patch("cli_agent_orchestrator.services.inbox_service.terminal_service.get_terminal")
@patch("cli_agent_orchestrator.services.inbox_service.get_handoff_child_result_message")
@patch("cli_agent_orchestrator.services.inbox_service.get_handoff_child_status")
@patch("cli_agent_orchestrator.services.inbox_service.create_handoff_child_result_message")
@patch("cli_agent_orchestrator.services.inbox_service.get_pending_handoff_child_terminal_ids")
def test_duplicate_handoff_recovery_never_creates_or_delivers_a_second_effect(
    mock_pending,
    mock_create_result,
    mock_handoff_status,
    mock_result,
    mock_terminal,
    mock_output,
    mock_exit,
    mock_cleanup_ack,
    mock_cleanup_acked,
    mock_deliver,
):
    from datetime import datetime

    from cli_agent_orchestrator.models.inbox import InboxMessage

    mock_pending.return_value = ["child34"]
    existing = InboxMessage(
        id=91,
        sender_id="child34",
        receiver_id="parent-ea22e2c9",
        message="child report",
        status=MessageStatus.PENDING,
        created_at=datetime.now(),
    )
    mock_handoff_status.return_value = "handoff_result_queued"
    mock_result.return_value = existing
    mock_terminal.return_value = {
        "status": TerminalStatus.COMPLETED.value,
        "lifecycle": "running",
    }
    mock_cleanup_acked.return_value = True
    mock_create_result.return_value = (
        InboxMessage(
            id=91,
            sender_id="child34",
            receiver_id="parent-ea22e2c9",
            message="child report",
            status=MessageStatus.PENDING,
            created_at=datetime.now(),
        ),
        True,
    )

    assert reconcile_handoff_continuations() == 0

    mock_create_result.assert_not_called()
    mock_exit.assert_not_called()
    mock_deliver.assert_called_once_with("parent-ea22e2c9", registry=None)


@patch("cli_agent_orchestrator.services.inbox_service.check_and_send_pending_messages")
@patch("cli_agent_orchestrator.services.inbox_service.handoff_child_cleanup_acknowledged")
@patch("cli_agent_orchestrator.services.inbox_service.handoff_child_cleanup_is_acknowledged")
@patch("cli_agent_orchestrator.services.inbox_service.terminal_service.exit_terminal")
@patch("cli_agent_orchestrator.services.inbox_service.terminal_service.get_output")
@patch("cli_agent_orchestrator.services.inbox_service.terminal_service.get_terminal")
@patch("cli_agent_orchestrator.services.inbox_service.get_handoff_child_result_message")
@patch("cli_agent_orchestrator.services.inbox_service.get_handoff_child_status")
@patch("cli_agent_orchestrator.services.inbox_service.create_handoff_child_result_message")
@patch("cli_agent_orchestrator.services.inbox_service.get_pending_handoff_child_terminal_ids")
def test_restart_recovery_changed_second_capture_persists_and_wakes_nothing(
    mock_pending,
    mock_create_result,
    mock_handoff_status,
    mock_result,
    mock_terminal,
    mock_output,
    mock_exit,
    mock_cleanup_ack,
    mock_cleanup_acked,
    mock_deliver,
):
    mock_pending.return_value = ["child-unstable"]
    mock_handoff_status.return_value = "handoff_recovery_awaiting_result"
    mock_result.return_value = None
    mock_terminal.return_value = {
        "status": TerminalStatus.COMPLETED.value,
        "lifecycle": "running",
    }
    mock_output.side_effect = ["first report", "changed report"]

    assert reconcile_handoff_continuations() == 0

    mock_create_result.assert_not_called()
    mock_exit.assert_not_called()
    mock_cleanup_acked.assert_not_called()
    mock_deliver.assert_not_called()


@patch("cli_agent_orchestrator.services.inbox_service.check_and_send_pending_messages")
@patch("cli_agent_orchestrator.services.inbox_service.cancel_child_assignments_for_terminal")
@patch("cli_agent_orchestrator.services.inbox_service.handoff_child_cleanup_acknowledged")
@patch("cli_agent_orchestrator.services.inbox_service.handoff_child_cleanup_is_acknowledged")
@patch("cli_agent_orchestrator.services.inbox_service.terminal_service.exit_terminal")
@patch("cli_agent_orchestrator.services.inbox_service.terminal_service.get_output")
@patch("cli_agent_orchestrator.services.inbox_service.terminal_service.get_terminal")
@patch("cli_agent_orchestrator.services.inbox_service.get_handoff_child_result_message")
@patch("cli_agent_orchestrator.services.inbox_service.get_handoff_child_status")
@patch("cli_agent_orchestrator.services.inbox_service.create_handoff_child_result_message")
@patch("cli_agent_orchestrator.services.inbox_service.get_pending_handoff_child_terminal_ids")
def test_restart_recovery_exited_terminal_without_valid_final_terminalizes_once(
    mock_pending,
    mock_create_result,
    mock_handoff_status,
    mock_result,
    mock_terminal,
    mock_output,
    mock_exit,
    mock_cleanup_acked,
    mock_cleanup_ack,
    mock_cancel,
    mock_deliver,
):
    mock_pending.return_value = ["child-exited"]
    mock_handoff_status.return_value = "handoff_recovery_awaiting_result"
    mock_result.return_value = None
    mock_terminal.return_value = {
        "status": TerminalStatus.COMPLETED.value,
        "lifecycle": "exited",
    }
    mock_output.return_value = "Working (11s • esc to interrupt)"

    assert reconcile_handoff_continuations() == 0

    assert mock_output.call_args.args[0] == "child-exited"
    mock_create_result.assert_not_called()
    mock_exit.assert_not_called()
    mock_cleanup_acked.assert_not_called()
    mock_cancel.assert_called_once_with("child-exited")
    mock_deliver.assert_not_called()
