"""Tests for send_message MCP tool."""

import os
from unittest.mock import MagicMock, patch


@patch("cli_agent_orchestrator.mcp_server.server.requests.post")
def test_send_to_inbox_posts_payload_in_json_body_not_query(mock_post):
    """The MCP durable sender must keep potentially long prompt text out of URLs."""
    from cli_agent_orchestrator.constants import API_BASE_URL
    from cli_agent_orchestrator.mcp_server.server import _send_to_inbox

    mock_post.return_value.json.return_value = {"success": True}
    message = "Первая строка\nВторая строка — " + "длинный фрагмент " * 1000

    with patch.dict(os.environ, {"CAO_TERMINAL_ID": "sender-xyz"}):
        assert _send_to_inbox("receiver-123", message) == {"success": True}

    assert mock_post.call_args.args == (f"{API_BASE_URL}/terminals/receiver-123/inbox/messages",)
    assert mock_post.call_args.kwargs["json"] == {"sender_id": "sender-xyz", "message": message}
    assert "params" not in mock_post.call_args.kwargs


class TestSendMessageSenderIdInjection:
    """Tests for sender ID injection in _send_message_impl."""

    @patch("cli_agent_orchestrator.mcp_server.server.ENABLE_SENDER_ID_INJECTION", True)
    @patch("cli_agent_orchestrator.mcp_server.server._send_to_inbox")
    def test_send_message_appends_sender_id_when_injection_enabled(self, mock_inbox):
        """When injection is enabled, send_message should append sender ID suffix."""
        from cli_agent_orchestrator.mcp_server.server import _send_message_impl

        mock_inbox.return_value = {"success": True}

        with patch.dict(os.environ, {"CAO_TERMINAL_ID": "sender-xyz"}):
            result = _send_message_impl("receiver-123", "Here are the results")

        sent_message = mock_inbox.call_args[0][1]
        assert sent_message.startswith("Here are the results")
        assert "[Message from terminal sender-xyz" in sent_message
        assert "Use send_message MCP tool for any follow-up work.]" in sent_message

    @patch("cli_agent_orchestrator.mcp_server.server.ENABLE_SENDER_ID_INJECTION", False)
    @patch("cli_agent_orchestrator.mcp_server.server._send_to_inbox")
    def test_send_message_no_suffix_when_injection_disabled(self, mock_inbox):
        """When injection is disabled, send_message should pass the message unchanged."""
        from cli_agent_orchestrator.mcp_server.server import _send_message_impl

        mock_inbox.return_value = {"success": True}

        with patch.dict(os.environ, {"CAO_TERMINAL_ID": "sender-xyz"}):
            result = _send_message_impl("receiver-123", "Here are the results")

        sent_message = mock_inbox.call_args[0][1]
        assert sent_message == "Here are the results"

    @patch("cli_agent_orchestrator.mcp_server.server.ENABLE_SENDER_ID_INJECTION", True)
    @patch("cli_agent_orchestrator.mcp_server.server._send_to_inbox")
    def test_send_message_sender_id_fallback_unknown(self, mock_inbox):
        """When CAO_TERMINAL_ID is not set, suffix should use 'unknown'."""
        from cli_agent_orchestrator.mcp_server.server import _send_message_impl

        mock_inbox.return_value = {"success": True}

        with patch.dict(os.environ, {}, clear=True):
            result = _send_message_impl("receiver-123", "Status update")

        sent_message = mock_inbox.call_args[0][1]
        assert "[Message from terminal unknown" in sent_message

    @patch("cli_agent_orchestrator.mcp_server.server.ENABLE_SENDER_ID_INJECTION", True)
    @patch("cli_agent_orchestrator.mcp_server.server._send_to_inbox")
    def test_send_message_suffix_is_appended_not_prepended(self, mock_inbox):
        """The sender ID should be a suffix, not a prefix."""
        from cli_agent_orchestrator.mcp_server.server import _send_message_impl

        mock_inbox.return_value = {"success": True}
        original = "Task complete. Here are the deliverables."

        with patch.dict(os.environ, {"CAO_TERMINAL_ID": "sender-999"}):
            _send_message_impl("receiver-123", original)

        sent_message = mock_inbox.call_args[0][1]
        assert sent_message.startswith(original)
        assert sent_message.index("[Message from terminal") > len(original)


@patch("cli_agent_orchestrator.mcp_server.server.inbox_service.check_and_send_pending_messages")
@patch("cli_agent_orchestrator.mcp_server.server.schedule_managed_handoff_continuation")
@patch("cli_agent_orchestrator.mcp_server.server.ENABLE_SENDER_ID_INJECTION", False)
def test_managed_same_child_send_schedules_admitted_continuation_before_delivery(
    mock_schedule, mock_deliver
):
    """A parent continuation cannot fall through to the Inbox-only send path."""
    from cli_agent_orchestrator.mcp_server.server import _send_message_impl

    message = MagicMock(id=91)
    mock_schedule.return_value = {
        "managed": True,
        "accepted": True,
        "duplicate": False,
        "turn_id": 92,
        "message": message,
    }
    effect = {"id": 17}

    with patch.dict(os.environ, {"CAO_TERMINAL_ID": "parent"}):
        result = _send_message_impl("child", "continue with V1", effect, 81)

    mock_schedule.assert_called_once_with("parent", "child", "continue with V1")
    mock_deliver.assert_called_once_with("child")
    assert result == {
        "success": True,
        "duplicate": False,
        "message_id": 91,
        "sender_id": "parent",
        "receiver_id": "child",
        "logical_turn_id": 92,
        "managed_handoff_continuation": True,
    }
