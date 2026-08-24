"""Tests for plugin event emission from service-layer operations."""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cli_agent_orchestrator.models.agent_profile import AgentProfile
from cli_agent_orchestrator.models.inbox import MessageStatus
from cli_agent_orchestrator.models.terminal import Terminal, TerminalStatus
from cli_agent_orchestrator.plugins import (
    PostCreateSessionEvent,
    PostCreateTerminalEvent,
    PostKillSessionEvent,
    PostKillTerminalEvent,
    PostSendMessageEvent,
)
from cli_agent_orchestrator.services.inbox_service import _dispatch_pending_messages_with_admission
from cli_agent_orchestrator.services.session_service import create_session, delete_session
from cli_agent_orchestrator.services.terminal_service import (
    create_terminal,
    delete_terminal,
    send_input,
)


@pytest.fixture(autouse=True)
def isolate_operational_admission(monkeypatch):
    """Plugin emission tests isolate construction from host admission state."""
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.context_launch_admission",
        lambda **kwargs: nullcontext({}),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.control_plane_registry.registry_is_initialized",
        lambda: False,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service._capture_created_runtime_identity",
        lambda _session, _window, terminal_id, generation: SimpleNamespace(
            pane_id="%41",
            pane_pid=4242,
            terminal_id=terminal_id,
            runtime_generation=generation,
            process_start_ticks=777,
        ),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.acquire_terminal_runtime_transport",
        lambda _terminal_id: "transport-token",
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.terminal_service.release_terminal_runtime_operation",
        lambda _terminal_id, _token: True,
    )


def _registry_mock() -> MagicMock:
    """Build a registry double whose async dispatch can be asserted directly."""

    registry = MagicMock()
    registry.dispatch = AsyncMock()
    return registry


class TestSessionPluginEvents:
    """Verify session lifecycle events are emitted correctly."""

    @patch("cli_agent_orchestrator.services.session_service.create_terminal")
    def test_create_session_dispatches_post_create_session_event(self, mock_create_terminal):
        """Successful session creation should emit exactly one post_create_session event."""
        registry = _registry_mock()
        mock_create_terminal.return_value = Terminal(
            id="abcd1234",
            name="developer-abcd",
            session_name="cao-demo",
            provider="kiro_cli",
            agent_profile="developer",
        )

        result = create_session(
            provider="kiro_cli",
            agent_profile="developer",
            session_name="cao-demo",
            registry=registry,
        )

        assert result.session_name == "cao-demo"
        registry.dispatch.assert_awaited_once()
        event_type, event = registry.dispatch.await_args.args
        assert event_type == "post_create_session"
        assert isinstance(event, PostCreateSessionEvent)
        assert event.session_id == "cao-demo"
        assert event.session_name == "cao-demo"
        assert mock_create_terminal.call_args.kwargs.get("context_role") is None

    @patch("cli_agent_orchestrator.services.session_service.create_terminal")
    def test_create_session_does_not_dispatch_on_failure(self, mock_create_terminal):
        """Session creation failures must not emit plugin events."""
        registry = _registry_mock()
        mock_create_terminal.side_effect = RuntimeError("tmux failed")

        with pytest.raises(RuntimeError, match="tmux failed"):
            create_session(provider="kiro_cli", agent_profile="developer", registry=registry)

        registry.dispatch.assert_not_awaited()

    @patch("cli_agent_orchestrator.services.session_service.delete_terminals_by_session_lifetime")
    @patch("cli_agent_orchestrator.services.session_service.prove_live_session_runtime_authority")
    @patch("cli_agent_orchestrator.services.session_service.resolve_session_authority")
    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_delete_session_dispatches_post_kill_session_event_after_cleanup(
        self, mock_tmux, mock_resolve, mock_prove_runtime, mock_delete_terminals
    ):
        """Session kill should emit after the tmux kill and DB cleanup succeed."""
        registry = _registry_mock()
        call_order: list[str] = []

        async def record_dispatch(*_args):
            call_order.append("dispatch")

        mock_resolve.return_value = SimpleNamespace(
            session_id="session-lifetime-1",
            session_name="cao-demo",
            terminals=[],
            deleted=False,
            has_live_runtime_owner=True,
        )
        mock_prove_runtime.return_value = SimpleNamespace(proven=True)
        mock_tmux.kill_session.side_effect = lambda *_: call_order.append("kill_session") or True
        mock_tmux.session_exists.return_value = False
        mock_delete_terminals.side_effect = lambda *_args, **_kwargs: (
            call_order.append("delete_terminals") or {"already_deleted": False, "deleted": 0}
        )
        registry.dispatch.side_effect = record_dispatch

        result = delete_session("cao-demo", registry=registry)

        assert result == {"deleted": ["cao-demo"], "errors": [], "already_deleted": False}
        assert call_order == ["kill_session", "delete_terminals", "dispatch"]
        event_type, event = registry.dispatch.await_args.args
        assert event_type == "post_kill_session"
        assert isinstance(event, PostKillSessionEvent)
        assert event.session_id == "session-lifetime-1"
        assert event.session_name == "cao-demo"

    @patch("cli_agent_orchestrator.services.session_service.tmux_client")
    def test_delete_session_does_not_dispatch_on_failure(self, mock_tmux):
        """Missing sessions should raise without emitting events."""
        registry = _registry_mock()
        mock_tmux.session_exists.return_value = False

        with pytest.raises(ValueError, match="Session 'cao-missing' not found"):
            delete_session("cao-missing", registry=registry)

        registry.dispatch.assert_not_awaited()


class TestTerminalPluginEvents:
    """Verify terminal lifecycle events are emitted correctly."""

    @patch("cli_agent_orchestrator.services.terminal_service.TERMINAL_LOG_DIR")
    @patch("cli_agent_orchestrator.services.terminal_service.build_skill_catalog", return_value="")
    @patch("cli_agent_orchestrator.services.terminal_service.load_agent_profile")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_terminal_id")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_window_name")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.db_create_terminal")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    def test_create_terminal_dispatches_post_create_terminal_event_after_setup(
        self,
        mock_provider_manager,
        mock_db_create_terminal,
        mock_tmux,
        mock_generate_window_name,
        mock_generate_terminal_id,
        mock_load_agent_profile,
        mock_build_skill_catalog,
        mock_log_dir,
    ):
        """Terminal creation should emit only after persistence and startup complete."""
        registry = _registry_mock()
        call_order: list[str] = []

        async def record_dispatch(*_args):
            call_order.append("dispatch")

        mock_generate_terminal_id.return_value = "abcd1234"
        mock_generate_window_name.return_value = "developer-abcd"
        mock_tmux.session_exists.return_value = False
        mock_db_create_terminal.side_effect = lambda *_, **__: call_order.append("db_create")
        mock_load_agent_profile.return_value = AgentProfile(name="developer", description="Dev")

        provider = MagicMock()
        provider.initialize.side_effect = lambda: call_order.append("provider_initialize")
        mock_provider_manager.create_provider.return_value = provider

        log_path = MagicMock()
        mock_log_dir.__truediv__.return_value = log_path
        mock_tmux.pipe_pane.side_effect = lambda *_: call_order.append("pipe_pane")
        registry.dispatch.side_effect = record_dispatch

        terminal = create_terminal(
            provider="kiro_cli",
            agent_profile="developer",
            session_name="demo",
            new_session=True,
            allowed_tools=["*"],
            registry=registry,
        )

        assert terminal.id == "abcd1234"
        assert call_order == ["db_create", "pipe_pane", "provider_initialize", "dispatch"]
        event_type, event = registry.dispatch.await_args.args
        assert event_type == "post_create_terminal"
        assert isinstance(event, PostCreateTerminalEvent)
        assert event.session_id == "cao-demo"
        assert event.terminal_id == "abcd1234"
        assert event.agent_name == "developer"
        assert event.provider == "kiro_cli"

    @patch("cli_agent_orchestrator.services.terminal_service.TERMINAL_LOG_DIR")
    @patch("cli_agent_orchestrator.services.terminal_service.build_skill_catalog", return_value="")
    @patch("cli_agent_orchestrator.services.terminal_service.load_agent_profile")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_terminal_id")
    @patch("cli_agent_orchestrator.services.terminal_service.generate_window_name")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.db_create_terminal")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    def test_create_terminal_does_not_dispatch_on_failure(
        self,
        mock_provider_manager,
        mock_db_create_terminal,
        mock_tmux,
        mock_generate_window_name,
        mock_generate_terminal_id,
        mock_load_agent_profile,
        mock_build_skill_catalog,
        mock_log_dir,
    ):
        """Terminal creation failures must not emit post_create_terminal."""
        registry = _registry_mock()
        mock_generate_terminal_id.return_value = "abcd1234"
        mock_generate_window_name.return_value = "developer-abcd"
        mock_tmux.session_exists.return_value = False
        mock_load_agent_profile.return_value = AgentProfile(name="developer", description="Dev")

        provider = MagicMock()
        provider.initialize.side_effect = RuntimeError("provider init failed")
        mock_provider_manager.create_provider.return_value = provider
        mock_log_dir.__truediv__.return_value = MagicMock()

        with pytest.raises(RuntimeError, match="provider init failed"):
            create_terminal(
                provider="kiro_cli",
                agent_profile="developer",
                session_name="demo",
                new_session=True,
                allowed_tools=["*"],
                registry=registry,
            )

        registry.dispatch.assert_not_awaited()

    def test_delete_terminal_dispatches_post_kill_terminal_event_after_delete(self):
        """Terminal kill should emit only after deletion succeeds."""
        registry = _registry_mock()
        call_order: list[str] = []

        async def record_dispatch(*_args):
            call_order.append("dispatch")

        metadata = {
            "id": "abcd1234",
            "tmux_session": "cao-demo",
            "tmux_window": "developer-abcd",
            "agent_profile": "developer",
            "runtime_lifecycle": "exited",
        }
        registry.dispatch.side_effect = record_dispatch
        with (
            patch(
                "cli_agent_orchestrator.services.terminal_service.get_terminal_metadata",
                return_value=metadata,
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.prepare_terminal_for_destruction"
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.retire_exited_terminal_runtime",
                return_value=True,
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.mark_terminal_runtime_exited",
                return_value=True,
            ),
            patch("cli_agent_orchestrator.services.terminal_service.provider_manager") as manager,
            patch(
                "cli_agent_orchestrator.services.terminal_service.db_delete_exited_terminal",
                side_effect=lambda *_args, **_kwargs: call_order.append("db_delete")
                or {"deleted": 1, "already_deleted": False, "missing": False},
            ),
        ):
            manager.cleanup_provider.side_effect = lambda *_: call_order.append("cleanup")
            deleted = delete_terminal("abcd1234", registry=registry)

        assert deleted is True
        assert call_order[-2:] == ["db_delete", "dispatch"]
        event_type, event = registry.dispatch.await_args.args
        assert event_type == "post_kill_terminal"
        assert isinstance(event, PostKillTerminalEvent)
        assert event.session_id == "cao-demo"
        assert event.terminal_id == "abcd1234"
        assert event.agent_name == "developer"

    def test_delete_terminal_does_not_dispatch_on_failure(self):
        """Deletion failures must not emit post_kill_terminal."""
        registry = _registry_mock()
        metadata = {
            "id": "abcd1234",
            "tmux_session": "cao-demo",
            "tmux_window": "developer-abcd",
            "agent_profile": "developer",
            "runtime_lifecycle": "exited",
        }
        with (
            patch(
                "cli_agent_orchestrator.services.terminal_service.get_terminal_metadata",
                return_value=metadata,
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.prepare_terminal_for_destruction"
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.retire_exited_terminal_runtime",
                return_value=True,
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.mark_terminal_runtime_exited",
                return_value=True,
            ),
            patch(
                "cli_agent_orchestrator.services.terminal_service.db_delete_exited_terminal",
                side_effect=RuntimeError("db delete failed"),
            ),
            pytest.raises(RuntimeError, match="db delete failed"),
        ):
            delete_terminal("abcd1234", registry=registry)

        registry.dispatch.assert_not_awaited()


class TestMessagePluginEvents:
    """Verify message delivery emits the correct event payloads."""

    @pytest.mark.parametrize("orchestration_type", ["send_message", "assign", "handoff"])
    @patch("cli_agent_orchestrator.services.terminal_service.update_last_active")
    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_metadata")
    def test_send_input_dispatches_post_send_message_event_for_each_orchestration_mode(
        self,
        mock_get_metadata,
        mock_provider_manager,
        mock_tmux,
        mock_update_last_active,
        orchestration_type,
    ):
        """Every successful delivery should emit one post_send_message event."""
        registry = _registry_mock()
        call_order: list[str] = []

        async def record_dispatch(*_args):
            call_order.append("dispatch")

        mock_get_metadata.return_value = {
            "tmux_session": "cao-demo",
            "tmux_window": "developer-abcd",
        }
        provider = MagicMock()
        provider.paste_enter_count = 2
        provider.mark_input_received.side_effect = lambda: call_order.append("mark_input_received")
        mock_provider_manager.get_provider.return_value = provider
        mock_tmux.send_keys.side_effect = lambda *_args, **_kwargs: call_order.append("send_keys")
        mock_update_last_active.side_effect = lambda *_: call_order.append("update_last_active")
        registry.dispatch.side_effect = record_dispatch

        delivered = send_input(
            "abcd1234",
            "Hello from supervisor",
            registry=registry,
            sender_id="supervisor-1",
            orchestration_type=orchestration_type,
        )

        assert delivered is True
        assert call_order[-1] == "dispatch"
        event_type, event = registry.dispatch.await_args.args
        assert event_type == "post_send_message"
        assert isinstance(event, PostSendMessageEvent)
        assert event.session_id == "cao-demo"
        assert event.sender == "supervisor-1"
        assert event.receiver == "abcd1234"
        assert event.message == "Hello from supervisor"
        assert event.orchestration_type == orchestration_type

    @patch("cli_agent_orchestrator.services.terminal_service.tmux_client")
    @patch("cli_agent_orchestrator.services.terminal_service.provider_manager")
    @patch("cli_agent_orchestrator.services.terminal_service.get_terminal_metadata")
    def test_send_input_does_not_dispatch_on_failure(
        self, mock_get_metadata, mock_provider_manager, mock_tmux
    ):
        """Message delivery failures must not emit post_send_message."""
        registry = _registry_mock()
        mock_get_metadata.return_value = {
            "tmux_session": "cao-demo",
            "tmux_window": "developer-abcd",
        }
        provider = MagicMock()
        provider.paste_enter_count = 1
        mock_provider_manager.get_provider.return_value = provider
        mock_tmux.send_keys.side_effect = RuntimeError("send failed")

        with pytest.raises(RuntimeError, match="send failed"):
            send_input(
                "abcd1234",
                "Hello from supervisor",
                registry=registry,
                sender_id="supervisor-1",
                orchestration_type="assign",
            )

        registry.dispatch.assert_not_awaited()

    @patch("cli_agent_orchestrator.services.inbox_service.update_message_status")
    @patch("cli_agent_orchestrator.services.inbox_service.mark_child_assignment_result_delivered")
    @patch("cli_agent_orchestrator.services.inbox_service.terminal_service")
    @patch("cli_agent_orchestrator.services.inbox_service.provider_manager")
    @patch("cli_agent_orchestrator.services.inbox_service.get_pending_messages")
    @patch("cli_agent_orchestrator.services.inbox_service.get_child_assignment_result_child_id")
    def test_inbox_delivery_threads_send_message_context_to_terminal_service(
        self,
        mock_child_identity,
        mock_get_pending_messages,
        mock_provider_manager,
        mock_terminal_service,
        mock_mark_delivered,
        mock_update_message_status,
    ):
        """Queued inbox delivery should forward sender context and hardcode send_message."""
        registry = _registry_mock()
        message = MagicMock()
        message.id = 17
        message.sender_id = "supervisor-1"
        message.message = "Please review this"
        message.kind = "message"
        message.result_id = None
        mock_get_pending_messages.return_value = [message]
        mock_child_identity.return_value = None

        provider = MagicMock()
        provider.get_status.return_value = TerminalStatus.IDLE
        provider.is_process_alive.return_value = True
        mock_provider_manager.get_provider.return_value = provider

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
            delivered = _dispatch_pending_messages_with_admission("abcd1234", registry=registry)

        assert delivered is True
        sent = mock_terminal_service.send_input.call_args
        assert sent.args[0] == "abcd1234"
        assert sent.args[1].endswith("Please review this")
        assert sent.kwargs == {
            "registry": registry,
            "sender_id": "supervisor-1",
            "orchestration_type": "send_message",
            "logical_turn_id": 71,
        }
        mock_update_message_status.assert_called_once_with(17, MessageStatus.DELIVERED)
        mock_mark_delivered.assert_called_once_with(17)
