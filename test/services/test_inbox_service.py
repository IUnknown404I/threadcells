"""Tests for the inbox service."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cli_agent_orchestrator.models.inbox import MessageStatus
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.services import inbox_service
from cli_agent_orchestrator.services.inbox_service import (
    LogFileHandler,
    _dispatch_pending_messages_with_admission,
    _get_log_tail,
    _has_idle_pattern,
)


class TestGetLogTail:
    """Tests for _get_log_tail function."""

    @patch("cli_agent_orchestrator.services.inbox_service.subprocess.run")
    @patch("cli_agent_orchestrator.services.inbox_service.TERMINAL_LOG_DIR")
    def test_get_log_tail_success(self, mock_log_dir, mock_run):
        """Test getting log tail successfully."""
        mock_log_dir.__truediv__ = lambda self, x: Path("/tmp") / x
        mock_run.return_value = MagicMock(stdout="last line\n")

        result = _get_log_tail("test-terminal", lines=5)

        assert result == "last line\n"
        mock_run.assert_called_once()

    @patch("cli_agent_orchestrator.services.inbox_service.subprocess.run")
    @patch("cli_agent_orchestrator.services.inbox_service.TERMINAL_LOG_DIR")
    def test_get_log_tail_exception(self, mock_log_dir, mock_run):
        """Test getting log tail with exception."""
        mock_log_dir.__truediv__ = lambda self, x: Path("/tmp") / x
        mock_run.side_effect = Exception("Subprocess error")

        result = _get_log_tail("test-terminal")

        assert result == ""


class TestHasIdlePattern:
    """Tests for _has_idle_pattern function."""

    @patch("cli_agent_orchestrator.services.inbox_service.provider_manager")
    @patch("cli_agent_orchestrator.services.inbox_service._get_log_tail")
    def test_has_idle_pattern_true(self, mock_tail, mock_provider_manager):
        """Test idle pattern detection returns True."""
        mock_tail.return_value = "[developer]> "
        mock_provider = MagicMock()
        mock_provider.get_idle_pattern_for_log.return_value = r"\[developer\]>"
        mock_provider_manager.get_provider.return_value = mock_provider

        result = _has_idle_pattern("test-terminal")

        assert result is True

    @patch("cli_agent_orchestrator.services.inbox_service._get_log_tail")
    def test_has_idle_pattern_empty_tail(self, mock_tail):
        """Test idle pattern detection with empty tail."""
        mock_tail.return_value = ""

        result = _has_idle_pattern("test-terminal")

        assert result is False

    @patch("cli_agent_orchestrator.services.inbox_service.provider_manager")
    @patch("cli_agent_orchestrator.services.inbox_service._get_log_tail")
    def test_has_idle_pattern_no_provider(self, mock_tail, mock_provider_manager):
        """Test idle pattern detection with no provider."""
        mock_tail.return_value = "some content"
        mock_provider_manager.get_provider.return_value = None

        result = _has_idle_pattern("test-terminal")

        assert result is False

    @patch("cli_agent_orchestrator.services.inbox_service.provider_manager")
    @patch("cli_agent_orchestrator.services.inbox_service._get_log_tail")
    def test_has_idle_pattern_exception(self, mock_tail, mock_provider_manager):
        """Test idle pattern detection with exception."""
        mock_tail.return_value = "some content"
        mock_provider_manager.get_provider.side_effect = Exception("Error")

        result = _has_idle_pattern("test-terminal")

        assert result is False


class TestCheckAndSendPendingMessages:
    """Tests for check_and_send_pending_messages function."""

    @pytest.fixture(autouse=True)
    def _durable_inbox_turn(self, monkeypatch):
        turn = {"id": 71, "status": "open", "kind": "inbox_message"}
        monkeypatch.setattr(inbox_service, "ensure_workflow_turn_for_inbox", lambda *_: 71)
        monkeypatch.setattr(inbox_service, "get_workflow_turn_for_inbox", lambda *_: turn)
        monkeypatch.setattr(inbox_service, "activate_workflow_turn_for_inbox", lambda *_: 71)
        monkeypatch.setattr(
            inbox_service,
            "claim_workflow_turn",
            lambda *_args, **_kwargs: {
                "id": 71,
                "claim_token": "claim",
                "claim_generation": 1,
            },
        )
        monkeypatch.setattr(inbox_service, "mark_workflow_turn_sent", lambda *_: True)

    @patch("cli_agent_orchestrator.services.inbox_service.get_pending_messages")
    def test_no_pending_messages(self, mock_get_messages):
        """Test when no pending messages exist."""
        mock_get_messages.return_value = []

        result = _dispatch_pending_messages_with_admission("test-terminal")

        assert result is False

    @patch("cli_agent_orchestrator.services.inbox_service.provider_manager")
    @patch("cli_agent_orchestrator.services.inbox_service.get_pending_messages")
    def test_provider_not_found(self, mock_get_messages, mock_provider_manager):
        """Test when provider not found."""
        mock_message = MagicMock()
        mock_message.id = 1
        mock_message.message = "test message"
        mock_get_messages.return_value = [mock_message]
        mock_provider_manager.get_provider.return_value = None

        with pytest.raises(ValueError, match="Provider not found"):
            _dispatch_pending_messages_with_admission("test-terminal")

    @patch("cli_agent_orchestrator.services.inbox_service.provider_manager")
    @patch("cli_agent_orchestrator.services.inbox_service.get_pending_messages")
    def test_terminal_not_ready(self, mock_get_messages, mock_provider_manager):
        """Test when terminal not ready."""
        mock_message = MagicMock()
        mock_get_messages.return_value = [mock_message]
        mock_provider = MagicMock()
        mock_provider.get_status.return_value = TerminalStatus.PROCESSING
        mock_provider_manager.get_provider.return_value = mock_provider

        result = _dispatch_pending_messages_with_admission("test-terminal")

        assert result is False

    def test_stale_sidecar_fast_path_persists_before_activation_or_transport(self, monkeypatch):
        """Inbox input cannot overtake a provider reconnect fence."""
        message = MagicMock(id=42, message="new owner input", kind="message")
        provider = MagicMock()
        provider.get_status.return_value = TerminalStatus.IDLE
        provider.is_process_alive.return_value = True
        reconnect_signal = MagicMock(return_value=True)
        persist_reconnect = MagicMock(return_value=True)
        activate = MagicMock(return_value=71)
        send = MagicMock(return_value=True)
        monkeypatch.setattr(inbox_service, "get_pending_messages", lambda *_args, **_kw: [message])
        monkeypatch.setattr(inbox_service.provider_manager, "get_provider", lambda *_: provider)
        monkeypatch.setattr(
            inbox_service.terminal_service,
            "provider_runtime_sidecar_reconnect_required",
            reconnect_signal,
        )
        monkeypatch.setattr(
            inbox_service,
            "request_workflow_provider_reconnect",
            persist_reconnect,
        )
        monkeypatch.setattr(inbox_service, "activate_workflow_turn_for_inbox", activate)
        monkeypatch.setattr(inbox_service.terminal_service, "send_input", send)

        assert _dispatch_pending_messages_with_admission("test-terminal") is False
        reconnect_signal.assert_called_once_with("test-terminal", provider=provider)
        persist_reconnect.assert_called_once_with("test-terminal")
        activate.assert_not_called()
        send.assert_not_called()

    @patch("cli_agent_orchestrator.services.inbox_service.mark_child_assignment_result_failed")
    @patch("cli_agent_orchestrator.services.inbox_service.update_message_status")
    @patch("cli_agent_orchestrator.services.inbox_service.terminal_service.send_input")
    @patch("cli_agent_orchestrator.services.inbox_service.provider_manager")
    @patch("cli_agent_orchestrator.services.inbox_service.get_workflow_turn_for_inbox")
    @patch("cli_agent_orchestrator.services.inbox_service.get_pending_messages")
    def test_status_refresh_fences_stale_alive_provider_exit_before_transport(
        self,
        mock_get_messages,
        mock_workflow_turn,
        mock_provider_manager,
        mock_send_input,
        mock_update_status,
        mock_mark_failed,
    ):
        """Codex exit discovery during status refresh cannot deliver to its shell."""
        message = MagicMock(id=41, message="ordinary message", kind="message")
        mock_get_messages.return_value = [message]
        mock_workflow_turn.return_value = {
            "id": 71,
            "status": "open",
            "kind": "inbox_message",
        }
        provider = MagicMock()
        provider.is_process_alive.return_value = True

        def observe_exit():
            provider.is_process_alive.return_value = False
            return TerminalStatus.COMPLETED

        provider.get_status.side_effect = observe_exit
        mock_provider_manager.get_provider.return_value = provider

        assert _dispatch_pending_messages_with_admission("test-terminal") is False
        provider.get_status.assert_called_once_with()
        provider.is_process_alive.assert_called_once_with()
        mock_send_input.assert_not_called()
        mock_update_status.assert_called_once_with(41, MessageStatus.FAILED)
        mock_mark_failed.assert_called_once_with(41)

    @patch("cli_agent_orchestrator.services.inbox_service.update_message_status")
    @patch("cli_agent_orchestrator.services.inbox_service.terminal_service")
    @patch("cli_agent_orchestrator.services.inbox_service.provider_manager")
    @patch("cli_agent_orchestrator.services.inbox_service.get_pending_messages")
    def test_message_sent_successfully(
        self, mock_get_messages, mock_provider_manager, mock_terminal_service, mock_update_status
    ):
        """Test successful message delivery."""
        mock_message = MagicMock()
        mock_message.id = 1
        mock_message.message = "test message"
        mock_get_messages.return_value = [mock_message]
        mock_provider = MagicMock()
        mock_provider.get_status.return_value = TerminalStatus.IDLE
        mock_provider_manager.get_provider.return_value = mock_provider

        result = _dispatch_pending_messages_with_admission("test-terminal")

        assert result is True
        sent = mock_terminal_service.send_input.call_args
        assert sent.args[0] == "test-terminal"
        assert sent.args[1].endswith("test message")
        assert sent.kwargs == {"logical_turn_id": 71}
        mock_update_status.assert_called_once_with(1, MessageStatus.DELIVERED)

    @patch("cli_agent_orchestrator.services.inbox_service.update_message_status")
    @patch(
        "cli_agent_orchestrator.services.inbox_service.keep_managed_handoff_continuation_retryable",
        return_value=False,
    )
    @patch("cli_agent_orchestrator.services.inbox_service.terminal_service")
    @patch("cli_agent_orchestrator.services.inbox_service.provider_manager")
    @patch("cli_agent_orchestrator.services.inbox_service.get_pending_messages")
    def test_message_send_failure(
        self,
        mock_get_messages,
        mock_provider_manager,
        mock_terminal_service,
        mock_retryable,
        mock_update_status,
    ):
        """Test message delivery failure."""
        mock_message = MagicMock()
        mock_message.id = 1
        mock_message.message = "test message"
        mock_get_messages.return_value = [mock_message]
        mock_provider = MagicMock()
        mock_provider.get_status.return_value = TerminalStatus.IDLE
        mock_provider_manager.get_provider.return_value = mock_provider
        mock_terminal_service.send_input.side_effect = Exception("Send failed")

        with pytest.raises(Exception, match="Send failed"):
            _dispatch_pending_messages_with_admission("test-terminal")

        mock_update_status.assert_called_once_with(1, MessageStatus.FAILED)
        mock_retryable.assert_called_once_with(1)


class TestLogFileHandler:
    """Tests for LogFileHandler class."""

    @patch("cli_agent_orchestrator.services.inbox_service.reconcile_provider_execution_queue")
    @patch("cli_agent_orchestrator.services.inbox_service._has_idle_pattern")
    @patch("cli_agent_orchestrator.services.inbox_service.get_pending_messages")
    def test_on_modified_triggers_delivery(self, mock_get_messages, mock_has_idle, mock_check_send):
        """Test on_modified triggers message delivery."""
        from watchdog.events import FileModifiedEvent

        mock_get_messages.return_value = [MagicMock()]
        mock_has_idle.return_value = True

        handler = LogFileHandler()
        event = FileModifiedEvent("/path/to/test-terminal.log")

        handler.on_modified(event)

        mock_check_send.assert_called_once_with(None)

    @patch("cli_agent_orchestrator.services.inbox_service.reconcile_provider_execution_queue")
    def test_handle_log_change_wakes_shared_queue(self, mock_queue_wakeup):
        """Every log event wakes the shared queue without a resident fast path."""
        handler = LogFileHandler()
        handler._handle_log_change("test-terminal")

        mock_queue_wakeup.assert_called_once_with(None)

    @patch("cli_agent_orchestrator.services.inbox_service.reconcile_provider_execution_queue")
    def test_handle_log_change_registry_reaches_shared_queue(self, mock_queue_wakeup):
        registry = MagicMock()
        handler = LogFileHandler()
        handler._registry = registry
        handler._handle_log_change("test-terminal")

        mock_queue_wakeup.assert_called_once_with(registry)

    def test_on_modified_non_log_file(self):
        """Test on_modified ignores non-log files."""
        from watchdog.events import FileModifiedEvent

        handler = LogFileHandler()
        # Create a non-.log file event
        event = MagicMock(spec=FileModifiedEvent)
        event.src_path = "/path/to/test-terminal.txt"

        # Should not process non-log files
        handler.on_modified(event)

    def test_on_modified_not_file_modified_event(self):
        """Test on_modified ignores non-FileModifiedEvent."""
        handler = LogFileHandler()
        event = MagicMock()  # Not a FileModifiedEvent
        event.src_path = "/path/to/test-terminal.log"

        # Should not process non-FileModifiedEvent
        handler.on_modified(event)

    @patch("cli_agent_orchestrator.services.inbox_service.get_pending_messages")
    def test_handle_log_change_exception(self, mock_get_messages):
        """Test _handle_log_change handles exceptions (covers line 119-120)."""
        mock_get_messages.side_effect = Exception("Database error")

        handler = LogFileHandler()

        # Should not raise exception - handles it gracefully
        handler._handle_log_change("test-terminal")
