"""Tests for MCP server handoff logic."""

import asyncio
import os
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from cli_agent_orchestrator.mcp_server.models import HandoffResult, HandoffState
from cli_agent_orchestrator.mcp_server.server import (
    _await_handoff_impl,
    _handoff_impl,
    _handoff_output_problem,
    await_handoff,
)
from cli_agent_orchestrator.runtime_generation import RUNTIME_GENERATION_ENV

FIXTURES_DIR = Path(__file__).parent / "fixtures"
STDIO_RUNTIME_FENCE_SIDECAR = FIXTURES_DIR / "stale_runtime_sidecar.py"


def load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


@contextmanager
def runtime_generation_api(generation: str):
    """Serve the one local API endpoint exercised by the real MCP sidecar."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib HTTP handler API
            if self.path != "/_internal/runtime-generation":
                self.send_error(404)
                return
            body = f'{{"generation":"{generation}"}}'.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_port
    finally:
        httpd.shutdown()
        thread.join()
        httpd.server_close()


async def call_stdio_handoff(params: StdioServerParameters):
    """Initialize a real stdio session then issue exactly one MCP tool call."""
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            return await session.call_tool(
                "handoff",
                {"logical_turn_id": 763, "agent_profile": "developer", "message": "stdio retry"},
            )


class TestHandoffMessageContext:
    """Tests for handoff message context prepended to worker agents."""

    @pytest.fixture(autouse=True)
    def managed_relation(self, monkeypatch):
        monkeypatch.setattr(
            "cli_agent_orchestrator.mcp_server.server.register_handoff_child", lambda *_: True
        )
        monkeypatch.setattr(
            "cli_agent_orchestrator.mcp_server.server.issue_workflow_input_binding",
            lambda *_: "binding",
        )

    @patch("cli_agent_orchestrator.mcp_server.server._send_direct_input")
    @patch("cli_agent_orchestrator.mcp_server.server.wait_until_terminal_status")
    @patch("cli_agent_orchestrator.mcp_server.server._create_terminal")
    def test_codex_provider_prepends_handoff_context(self, mock_create, mock_wait, mock_send):
        """Codex provider should prepend [CAO Handoff] with supervisor ID."""
        mock_create.return_value = ("dev-terminal-1", "codex")
        # First call: wait for IDLE (True), second call: wait for COMPLETED (True)
        mock_wait.side_effect = [True, True]
        mock_send.return_value = None

        with patch.dict(os.environ, {"CAO_TERMINAL_ID": "supervisor-abc123"}):
            with patch("cli_agent_orchestrator.mcp_server.server.requests") as mock_requests:
                mock_response = MagicMock()
                mock_response.json.return_value = {"output": "task done"}
                mock_response.raise_for_status.return_value = None
                mock_requests.get.return_value = mock_response
                mock_requests.post.return_value = mock_response

                result = asyncio.run(_handoff_impl("developer", "Implement hello world"))

        # Verify _send_direct_input was called with the handoff prefix
        mock_send.assert_called_once()
        sent_message = mock_send.call_args[0][1]
        assert mock_send.call_args[0][2] == "handoff"
        assert sent_message.startswith("[CAO Handoff]")
        assert "supervisor-abc123" in sent_message
        assert "Implement hello world" in sent_message
        assert "Do NOT use send_message" in sent_message
        assert (
            "Submit the structured result with submit_handoff_result_v1("
            "logical_turn_id=<current logical-turn>, document=<V1 object>) immediately before "
            "finishing; a successful call is the authoritative V1 artifact."
        ) in sent_message
        assert (
            "Then emit exactly two logical lines for compatibility: line 1 must be "
            "CAO_RESULT_V1; line 2 must be one compact single-line JSON object matching V1, "
            "with no Markdown fence, bullet, prefix, suffix, extra text, or extra blank line."
        ) in sent_message
        assert "input-only policy context" in sent_message
        assert "do not echo it in your final response" in sent_message
        assert sent_message.rstrip().endswith("NO_TG_NOTIFY")

    @patch("cli_agent_orchestrator.mcp_server.server._send_direct_input")
    @patch("cli_agent_orchestrator.mcp_server.server.wait_until_terminal_status")
    @patch("cli_agent_orchestrator.mcp_server.server._create_terminal")
    @patch.dict(os.environ, {"CAO_TERMINAL_ID": ""})
    def test_claude_code_provider_no_handoff_context(self, mock_create, mock_wait, mock_send):
        """Claude Code provider should NOT prepend any handoff context."""
        mock_create.return_value = ("dev-terminal-2", "claude_code")
        mock_wait.side_effect = [True, True]
        mock_send.return_value = None

        with patch("cli_agent_orchestrator.mcp_server.server.requests") as mock_requests:
            mock_response = MagicMock()
            mock_response.json.return_value = {"output": "task done"}
            mock_response.raise_for_status.return_value = None
            mock_requests.get.return_value = mock_response
            mock_requests.post.return_value = mock_response

            result = asyncio.run(_handoff_impl("developer", "Implement hello world"))

        # Non-Codex providers receive the payload plus the mandatory child
        # Telegram ownership directive; the internal API adds admission.
        mock_send.assert_called_once()
        sent_message = mock_send.call_args[0][1]
        assert mock_send.call_args[0][2] == "handoff"
        assert sent_message.startswith("Implement hello world")
        assert sent_message.endswith("NO_TG_NOTIFY")


class TestInitialHandoffRuntimeGenerationFence:
    """Only a new handoff is fenced; an existing wait is a recovery fast path."""

    @patch(
        "cli_agent_orchestrator.mcp_server.server._active_runtime_generation",
        return_value="generation-N-plus-1",
    )
    @patch(
        "cli_agent_orchestrator.mcp_server.server._SIDECAR_RUNTIME_GENERATION",
        "generation-N",
    )
    @patch.dict(
        os.environ,
        {"CAO_TERMINAL_ID": "parent-generation", RUNTIME_GENERATION_ENV: "generation-N"},
    )
    def test_n_to_n_plus_1_returns_recovery_boundary_before_plain_v1_initial_handoff(
        self, mock_active_generation
    ):
        """A stale sidecar produces a retryable response before any child relation."""
        result = asyncio.run(_handoff_impl("developer", "plain V1", timeout=3))

        assert result.state == HandoffState.FAILED
        assert "CAO_SIDECAR_RECONNECT_REQUIRED" in result.message
        assert os.environ[RUNTIME_GENERATION_ENV] == "generation-N"
        mock_active_generation.assert_called_once()

    @patch("cli_agent_orchestrator.mcp_server.server.claim_handoff_child_result_direct")
    @patch("cli_agent_orchestrator.mcp_server.server._await_handoff_impl")
    @patch("cli_agent_orchestrator.mcp_server.server._send_direct_input_handoff")
    @patch(
        "cli_agent_orchestrator.mcp_server.server.issue_workflow_input_binding",
        return_value="binding",
    )
    @patch("cli_agent_orchestrator.mcp_server.server.register_handoff_child", return_value=True)
    @patch("cli_agent_orchestrator.mcp_server.server.wait_until_terminal_status", return_value=True)
    @patch("cli_agent_orchestrator.mcp_server.server._create_terminal")
    @patch(
        "cli_agent_orchestrator.mcp_server.server._active_runtime_generation",
        return_value="generation-N",
    )
    @patch(
        "cli_agent_orchestrator.mcp_server.server._SIDECAR_RUNTIME_GENERATION",
        "generation-N",
    )
    @patch.dict(
        os.environ,
        {"CAO_TERMINAL_ID": "parent-processing", RUNTIME_GENERATION_ENV: "generation-N"},
    )
    def test_processing_initial_handoff_waits_without_claim_or_replacement(
        self,
        mock_active_generation,
        mock_create,
        mock_ready,
        mock_register,
        mock_binding,
        mock_send,
        mock_await,
        mock_claim,
    ):
        mock_create.return_value = ("child-processing", "codex")
        mock_await.return_value = HandoffResult(
            success=False,
            message="waiting",
            terminal_id="child-processing",
            state=HandoffState.WAITING,
        )

        result = asyncio.run(_handoff_impl("developer", "still processing", timeout=3))

        assert result.state == HandoffState.WAITING
        mock_claim.assert_not_called()
        mock_register.assert_called_once()
        mock_binding.assert_called_once_with("child-processing")
        mock_send.assert_called_once()
        mock_await.assert_awaited_once()
        mock_active_generation.assert_called_once()

    @patch("cli_agent_orchestrator.mcp_server.server._finish_privileged_effect")
    @patch("cli_agent_orchestrator.mcp_server.server._await_handoff_impl")
    @patch(
        "cli_agent_orchestrator.mcp_server.server._claim_privileged_effect",
        return_value={"id": "await-effect", "claim_token": "await-token"},
    )
    @patch("cli_agent_orchestrator.mcp_server.server._active_runtime_generation")
    @patch(
        "cli_agent_orchestrator.mcp_server.server._SIDECAR_RUNTIME_GENERATION",
        "generation-current",
    )
    def test_await_handoff_allows_current_runtime_recovery(
        self, mock_active_generation, mock_claim, mock_await, mock_finish
    ):
        mock_active_generation.return_value = "generation-current"
        mock_await.return_value = HandoffResult(
            success=False,
            message="waiting",
            terminal_id="child-existing",
            state=HandoffState.WAITING,
        )

        result = asyncio.run(await_handoff(763, "child-existing", timeout=3))

        assert result.state == HandoffState.WAITING
        # This unit isolates the await orchestration by replacing the central
        # effect-claim helper; focused lifecycle tests exercise the real gate.
        mock_active_generation.assert_not_called()
        mock_claim.assert_called_once_with(763, "await_handoff", "child-existing")
        mock_await.assert_awaited_once_with("child-existing", 3)
        mock_finish.assert_called_once_with(mock_claim.return_value, "indeterminate")


@pytest.mark.integration
def test_stale_stdio_handoff_reconnects_reinitializes_then_creates_one_child_and_effect(tmp_path):
    """A stale call has a visible boundary; one fresh stdio retry has one effect."""
    active_generation = "generation-N-plus-1"
    trace = tmp_path / "sidecar-effects.log"
    base_env = {
        "CAO_TERMINAL_ID": "stdio-parent",
        "CAO_STDIO_RUNTIME_FENCE_TRACE": str(trace),
    }

    with runtime_generation_api(active_generation) as port:

        def parameters(sidecar_generation: str) -> StdioServerParameters:
            return StdioServerParameters(
                command=sys.executable,
                args=[str(STDIO_RUNTIME_FENCE_SIDECAR)],
                cwd=Path(__file__).parents[2],
                env={
                    **base_env,
                    "CAO_API_HOST": "127.0.0.1",
                    "CAO_API_PORT": str(port),
                    RUNTIME_GENERATION_ENV: sidecar_generation,
                    "CAO_TEST_SIDECAR_RUNTIME_GENERATION": sidecar_generation,
                },
            )

        stale_result = asyncio.run(call_stdio_handoff(parameters("generation-N")))
        assert stale_result.isError
        assert "CAO_SIDECAR_RECONNECT_REQUIRED" in stale_result.content[0].text
        assert not trace.exists()

        retry_result = asyncio.run(call_stdio_handoff(parameters(active_generation)))

    assert not retry_result.isError
    assert trace.read_text(encoding="utf-8").splitlines() == [
        "effect-claim",
        "child-create",
        "effect-finish:completed",
    ]

    @patch("cli_agent_orchestrator.mcp_server.server._send_direct_input")
    @patch("cli_agent_orchestrator.mcp_server.server.wait_until_terminal_status")
    @patch("cli_agent_orchestrator.mcp_server.server._create_terminal")
    @patch.dict(os.environ, {"CAO_TERMINAL_ID": ""})
    def test_kiro_cli_provider_no_handoff_context(self, mock_create, mock_wait, mock_send):
        """Kiro CLI provider should NOT prepend any handoff context."""
        mock_create.return_value = ("dev-terminal-3", "kiro_cli")
        mock_wait.side_effect = [True, True]
        mock_send.return_value = None

        with patch("cli_agent_orchestrator.mcp_server.server.requests") as mock_requests:
            mock_response = MagicMock()
            mock_response.json.return_value = {"output": "task done"}
            mock_response.raise_for_status.return_value = None
            mock_requests.get.return_value = mock_response
            mock_requests.post.return_value = mock_response

            result = asyncio.run(_handoff_impl("developer", "Implement hello world"))

        mock_send.assert_called_once()
        sent_message = mock_send.call_args[0][1]
        assert mock_send.call_args[0][2] == "handoff"
        assert sent_message.startswith("Implement hello world")
        assert sent_message.endswith("NO_TG_NOTIFY")

    @patch("cli_agent_orchestrator.mcp_server.server._send_direct_input")
    @patch("cli_agent_orchestrator.mcp_server.server.wait_until_terminal_status")
    @patch("cli_agent_orchestrator.mcp_server.server._create_terminal")
    def test_codex_handoff_context_includes_supervisor_id_from_env(
        self, mock_create, mock_wait, mock_send
    ):
        """Supervisor terminal ID should come from CAO_TERMINAL_ID env var."""
        mock_create.return_value = ("dev-terminal-4", "codex")
        mock_wait.side_effect = [True, True]
        mock_send.return_value = None

        with patch.dict(os.environ, {"CAO_TERMINAL_ID": "sup-xyz789"}):
            with patch("cli_agent_orchestrator.mcp_server.server.requests") as mock_requests:
                mock_response = MagicMock()
                mock_response.json.return_value = {"output": "done"}
                mock_response.raise_for_status.return_value = None
                mock_requests.get.return_value = mock_response
                mock_requests.post.return_value = mock_response

                asyncio.run(_handoff_impl("developer", "Build feature X"))

        sent_message = mock_send.call_args[0][1]
        assert mock_send.call_args[0][2] == "handoff"
        assert "sup-xyz789" in sent_message
        assert "Build feature X" in sent_message

    @patch("cli_agent_orchestrator.mcp_server.server._send_direct_input")
    @patch("cli_agent_orchestrator.mcp_server.server.wait_until_terminal_status")
    @patch("cli_agent_orchestrator.mcp_server.server._create_terminal")
    def test_codex_handoff_context_fallback_when_no_env(self, mock_create, mock_wait, mock_send):
        """When CAO_TERMINAL_ID is not set, supervisor ID should be 'unknown'."""
        mock_create.return_value = ("dev-terminal-5", "codex")
        mock_wait.side_effect = [True, True]
        mock_send.return_value = None

        with patch.dict(os.environ, {}, clear=True):
            with patch("cli_agent_orchestrator.mcp_server.server.requests") as mock_requests:
                mock_response = MagicMock()
                mock_response.json.return_value = {"output": "done"}
                mock_response.raise_for_status.return_value = None
                mock_requests.get.return_value = mock_response
                mock_requests.post.return_value = mock_response

                asyncio.run(_handoff_impl("developer", "Do task"))

        sent_message = mock_send.call_args[0][1]
        assert mock_send.call_args[0][2] == "handoff"
        assert "unknown" in sent_message
        assert "[CAO Handoff]" in sent_message
        assert "Do task" in sent_message

    @patch("cli_agent_orchestrator.mcp_server.server._send_direct_input")
    @patch("cli_agent_orchestrator.mcp_server.server.wait_until_terminal_status")
    @patch("cli_agent_orchestrator.mcp_server.server._create_terminal")
    def test_codex_handoff_original_message_preserved(self, mock_create, mock_wait, mock_send):
        """Original message should appear in full after the handoff prefix."""
        mock_create.return_value = ("dev-terminal-6", "codex")
        mock_wait.side_effect = [True, True]
        mock_send.return_value = None

        original = "Implement the task described in /path/to/task.md. Write tests."
        with patch.dict(os.environ, {"CAO_TERMINAL_ID": "sup-111"}):
            with patch("cli_agent_orchestrator.mcp_server.server.requests") as mock_requests:
                mock_response = MagicMock()
                mock_response.json.return_value = {"output": "done"}
                mock_response.raise_for_status.return_value = None
                mock_requests.get.return_value = mock_response
                mock_requests.post.return_value = mock_response

                asyncio.run(_handoff_impl("developer", original))

        sent_message = mock_send.call_args[0][1]
        assert mock_send.call_args[0][2] == "handoff"
        assert original in sent_message
        assert sent_message.endswith("NO_TG_NOTIFY")


class TestResumableHandoffWait:
    """A wait slice retains the worker identity until a validated result exits it."""

    @patch("cli_agent_orchestrator.mcp_server.server.requests.post")
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_claimed_handoff_child_result_direct",
        return_value=None,
    )
    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_terminal")
    @patch("cli_agent_orchestrator.mcp_server.server.get_delegation_result_for_assignment")
    @patch("cli_agent_orchestrator.mcp_server.server.get_handoff_parent_terminal_id")
    @patch.dict(os.environ, {"CAO_TERMINAL_ID": "parent-available"})
    def test_available_durable_result_returns_during_long_wait_without_provider_cleanup(
        self, mock_parent, mock_result, mock_terminal, mock_claimed, mock_exit
    ):
        mock_parent.return_value = "parent-available"
        mock_result.return_value = {
            "id": "result-available",
            "status": "complete",
            "schema_version": 1,
            "document": {"body_markdown": "durable report"},
        }

        result = asyncio.run(_await_handoff_impl("child-available", timeout=1200))

        assert result.state == HandoffState.COMPLETED
        assert result.output == "durable report"
        assert result.result_id == "result-available"
        assert result.result_status == "complete"
        mock_terminal.assert_not_called()
        mock_claimed.assert_called_once_with("parent-available", "child-available")
        mock_exit.assert_not_called()

    @patch("cli_agent_orchestrator.mcp_server.server.requests.post")
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_claimed_handoff_child_result_direct",
        return_value=None,
    )
    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_terminal")
    @patch("cli_agent_orchestrator.mcp_server.server.get_delegation_result_for_assignment")
    @patch("cli_agent_orchestrator.mcp_server.server.get_handoff_parent_terminal_id")
    @patch.dict(os.environ, {"CAO_TERMINAL_ID": "parent-incomplete"})
    def test_durable_incomplete_result_returns_without_waiting_for_provider(
        self, mock_parent, mock_result, mock_terminal, mock_claimed, mock_exit
    ):
        mock_parent.return_value = "parent-incomplete"
        mock_result.return_value = {
            "id": "result-incomplete",
            "status": "incomplete",
            "schema_version": 1,
            "document": {"body_markdown": "partial report"},
        }

        result = asyncio.run(_await_handoff_impl("child-incomplete", timeout=1200))

        assert result.state == HandoffState.FAILED
        assert result.output == "partial report"
        assert result.result_status == "incomplete"
        mock_terminal.assert_not_called()
        mock_claimed.assert_called_once_with("parent-incomplete", "child-incomplete")
        mock_exit.assert_not_called()

    @patch("cli_agent_orchestrator.mcp_server.server.requests.post")
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_claimed_handoff_child_result_direct",
        return_value=None,
    )
    @patch("cli_agent_orchestrator.mcp_server.server.acknowledge_handoff_child_result_direct")
    @patch("cli_agent_orchestrator.mcp_server.server.claim_handoff_child_result_direct")
    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_terminal")
    @patch("cli_agent_orchestrator.mcp_server.server.get_delegation_result_for_assignment")
    @patch("cli_agent_orchestrator.mcp_server.server.get_handoff_parent_terminal_id")
    @patch.dict(os.environ, {"CAO_TERMINAL_ID": "parent-restart"})
    def test_post_restart_durable_result_returns_once_without_duplicate_effect(
        self, mock_parent, mock_result, mock_terminal, mock_claim, mock_ack, mock_claimed, mock_exit
    ):
        mock_parent.return_value = "parent-restart"
        mock_result.return_value = {
            "id": "result-restart",
            "status": "complete",
            "schema_version": 1,
            "document": {"body_markdown": "recovered report"},
        }

        first = asyncio.run(_await_handoff_impl("child-restart", timeout=1200))
        second = asyncio.run(_await_handoff_impl("child-restart", timeout=1200))

        assert (first.state, second.state) == (HandoffState.COMPLETED, HandoffState.COMPLETED)
        assert (first.result_id, second.result_id) == ("result-restart", "result-restart")
        mock_terminal.assert_not_called()
        mock_claim.assert_not_called()
        mock_ack.assert_not_called()
        assert mock_claimed.call_count == 2
        mock_exit.assert_not_called()

    @patch("cli_agent_orchestrator.mcp_server.server.acknowledge_handoff_child_result_direct")
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_claimed_handoff_child_result_direct",
        return_value="claimed report",
    )
    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_terminal")
    @patch("cli_agent_orchestrator.mcp_server.server.requests.post")
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_delegation_result_for_assignment",
        return_value={
            "id": "result-claimed",
            "status": "complete",
            "schema_version": 1,
            "document": {"body_markdown": "claimed report"},
        },
    )
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_handoff_parent_terminal_id",
        return_value="parent-claimed",
    )
    @patch.dict(os.environ, {"CAO_TERMINAL_ID": "parent-claimed"})
    def test_direct_claimed_complete_runs_exit_and_ack_before_returning(
        self, mock_parent, mock_result, mock_exit, mock_terminal, mock_claimed, mock_ack
    ):
        mock_terminal.return_value = ("completed", "running")
        mock_exit.return_value.raise_for_status.return_value = None
        mock_ack.return_value = "claimed report"

        result = asyncio.run(_await_handoff_impl("child-claimed", timeout=1))

        assert result.state == HandoffState.COMPLETED
        assert result.output == "claimed report"
        mock_exit.assert_called_once_with("http://127.0.0.1:9889/terminals/child-claimed/exit")
        mock_ack.assert_called_once_with("parent-claimed", "child-claimed")
        assert mock_claimed.call_count == 2

    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_output")
    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_terminal")
    @patch("cli_agent_orchestrator.mcp_server.server.requests.post")
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_parent_completion_barrier",
        return_value=(0, 0),
    )
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_delegation_result_for_assignment",
        return_value={
            "id": "result-other-parent",
            "status": "complete",
            "schema_version": 1,
            "document": {"body_markdown": "wrong parent report"},
        },
    )
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_handoff_parent_terminal_id",
        return_value="different-parent",
    )
    @patch.dict(os.environ, {"CAO_TERMINAL_ID": "parent-claimed"})
    def test_wrong_parent_durable_complete_does_not_use_fast_path(
        self, mock_parent, mock_result, mock_barrier, mock_exit, mock_terminal, mock_output
    ):
        mock_terminal.side_effect = [("completed", "running"), ("completed", "running")]
        mock_output.side_effect = ["live report", "live report"]
        mock_exit.return_value.raise_for_status.return_value = None

        result = asyncio.run(_await_handoff_impl("child-other-parent", timeout=1))

        assert result.state == HandoffState.COMPLETED
        assert result.output == "live report"
        assert mock_terminal.call_count == 2
        mock_exit.assert_called_once_with("http://127.0.0.1:9889/terminals/child-other-parent/exit")

    @patch("cli_agent_orchestrator.mcp_server.server.get_delegation_result_for_assignment")
    @patch("cli_agent_orchestrator.mcp_server.server.get_handoff_parent_terminal_id")
    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_terminal")
    @patch("cli_agent_orchestrator.mcp_server.server.requests.post")
    @patch.dict(os.environ, {"CAO_TERMINAL_ID": "parent-running"})
    def test_genuinely_running_handoff_with_awaiting_result_remains_resumable(
        self, mock_exit, mock_terminal, mock_parent, mock_result
    ):
        mock_parent.return_value = "parent-running"
        mock_result.return_value = {
            "id": "result-awaiting",
            "status": "awaiting",
            "schema_version": 1,
            "document": None,
        }
        mock_terminal.return_value = ("processing", "running")

        result = asyncio.run(_await_handoff_impl("child-running", timeout=0))

        assert result.state == HandoffState.WAITING
        assert result.terminal_id == "child-running"
        mock_exit.assert_not_called()

    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_terminal")
    @patch("cli_agent_orchestrator.mcp_server.server.requests.post")
    def test_live_wait_slice_returns_durable_waiting_id(self, mock_exit, mock_terminal):
        mock_terminal.return_value = ("processing", "running")

        result = asyncio.run(_await_handoff_impl("codex-live", timeout=0))

        assert result.state == HandoffState.WAITING
        assert result.success is False
        assert result.terminal_id == "codex-live"
        mock_exit.assert_not_called()

    @patch("cli_agent_orchestrator.mcp_server.server.get_delegation_result_for_assignment")
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_handoff_parent_terminal_id",
        return_value=None,
    )
    @patch("cli_agent_orchestrator.mcp_server.server.acknowledge_handoff_child_result_direct")
    @patch("cli_agent_orchestrator.mcp_server.server.get_claimed_handoff_child_result_direct")
    @patch("cli_agent_orchestrator.mcp_server.server.claim_staged_handoff_result_direct")
    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_output")
    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_terminal")
    @patch("cli_agent_orchestrator.mcp_server.server.requests.post")
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_parent_completion_barrier",
        return_value=(0, 0),
    )
    @patch.dict(os.environ, {"CAO_TERMINAL_ID": "parent-staged"})
    def test_staged_v1_claim_precedes_malformed_terminal_output_and_cleans_once(
        self,
        mock_barrier,
        mock_exit,
        mock_terminal,
        mock_output,
        mock_claim_staged,
        mock_claimed,
        mock_ack,
        mock_parent,
        mock_result,
    ):
        """A valid staged V1 result cannot be overridden by malformed capture."""
        mock_terminal.return_value = ("completed", "running")
        mock_claim_staged.return_value = True
        mock_claimed.side_effect = [None, "authoritative structured report"]
        mock_ack.return_value = "authoritative structured report"
        mock_result.return_value = {
            "id": "staged-result",
            "status": "complete",
            "schema_version": 1,
            "document": {"format": "v1", "body_markdown": "authoritative structured report"},
        }
        mock_exit.return_value.raise_for_status.return_value = None

        result = asyncio.run(_await_handoff_impl("child-staged", timeout=1))

        assert result.state == HandoffState.COMPLETED
        assert result.output == "authoritative structured report"
        mock_claim_staged.assert_called_once_with("parent-staged", "child-staged")
        mock_output.assert_not_called()
        mock_exit.assert_called_once_with("http://127.0.0.1:9889/terminals/child-staged/exit")
        mock_ack.assert_called_once_with("parent-staged", "child-staged")

    @patch("cli_agent_orchestrator.mcp_server.server.is_managed_structured_handoff_child")
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_workflow_provider_outcome",
        return_value={
            "code": "PROVIDER_CONTENT_UNAVAILABLE",
            "detail_code": "cyber_policy",
        },
    )
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_workflow_status",
        return_value="open",
    )
    @patch(
        "cli_agent_orchestrator.mcp_server.server.claim_staged_handoff_result_direct",
        return_value=None,
    )
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_claimed_handoff_child_result_direct",
        return_value=None,
    )
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_acknowledged_handoff_child_result_direct",
        return_value=None,
    )
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_handoff_parent_terminal_id",
        return_value="parent-policy",
    )
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_delegation_result_for_assignment",
        return_value=None,
    )
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_parent_completion_barrier",
        return_value=(0, 0),
    )
    @patch(
        "cli_agent_orchestrator.mcp_server.server._read_handoff_terminal",
        return_value=("completed", "running"),
    )
    @patch.dict(os.environ, {"CAO_TERMINAL_ID": "parent-policy"})
    def test_provider_content_unavailable_keeps_handoff_live_and_recoverable(
        self,
        _mock_terminal,
        _mock_barrier,
        _mock_result,
        _mock_parent,
        _mock_acknowledged,
        _mock_claimed,
        mock_staged,
        _mock_workflow_status,
        _mock_outcome,
        mock_managed,
    ):
        result = asyncio.run(_await_handoff_impl("child-policy", timeout=1))

        assert result.state == HandoffState.WAITING
        assert result.reason_code == "PROVIDER_CONTENT_UNAVAILABLE"
        assert result.workflow_state == "open"
        assert result.output is None
        assert "workflow state is preserved" in result.message
        mock_staged.assert_called_once_with("parent-policy", "child-policy")
        mock_managed.assert_not_called()

    @patch("cli_agent_orchestrator.mcp_server.server.cancel_child_assignments_for_terminal")
    @patch(
        "cli_agent_orchestrator.mcp_server.server.is_managed_structured_handoff_child",
        return_value=True,
    )
    @patch("cli_agent_orchestrator.mcp_server.server.claim_handoff_child_result_direct")
    @patch("cli_agent_orchestrator.mcp_server.server.claim_staged_handoff_result_direct")
    @patch("cli_agent_orchestrator.mcp_server.server.get_claimed_handoff_child_result_direct")
    @patch(
        "cli_agent_orchestrator.mcp_server.server.acknowledge_handoff_child_result_direct",
        return_value="authoritative V1 report",
    )
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_acknowledged_handoff_child_result_direct",
        return_value=None,
    )
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_handoff_parent_terminal_id",
        return_value="parent-managed",
    )
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_delegation_result_for_assignment",
        return_value=None,
    )
    @patch(
        "cli_agent_orchestrator.mcp_server.server._read_handoff_output",
        return_value="PROGRESS_ONLY_SCENARIO_1",
    )
    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_terminal")
    @patch("cli_agent_orchestrator.mcp_server.server.requests.post")
    @patch.dict(os.environ, {"CAO_TERMINAL_ID": "parent-managed"})
    def test_managed_structured_exit_never_falls_back_to_legacy_capture_then_same_child_v1_completes(
        self,
        mock_exit,
        mock_terminal,
        mock_output,
        _mock_result,
        _mock_parent,
        _mock_acknowledged,
        _mock_ack,
        mock_claimed,
        mock_staged_claim,
        mock_direct_claim,
        _mock_managed,
        mock_cancel,
    ):
        """The public wait path retains a managed child until its V1 result exists."""
        mock_terminal.return_value = ("completed", "exited")
        # Each wait slice checks the durable fast path and the cleanup claim
        # before a staged V1 claim; the final lookup alone observes the V1.
        mock_claimed.side_effect = [None, None, None, None, "authoritative V1 report"]
        mock_staged_claim.side_effect = [None, True]
        mock_exit.return_value.raise_for_status.return_value = None

        before_v1 = asyncio.run(_await_handoff_impl("child-managed", timeout=0))
        assert before_v1.state == HandoffState.FAILED
        assert before_v1.success is False
        assert before_v1.terminal_id == "child-managed"
        assert "process already exited" in before_v1.message
        mock_output.assert_not_called()
        mock_direct_claim.assert_not_called()
        mock_cancel.assert_called_once_with("child-managed")

        # The exact child ID is resumed; a subsequently persisted V1 result,
        # not terminal prose, is the one and only successful completion path.
        after_v1 = asyncio.run(_await_handoff_impl("child-managed", timeout=1))
        assert after_v1.state == HandoffState.COMPLETED
        assert after_v1.terminal_id == "child-managed"
        assert after_v1.output == "authoritative V1 report"
        mock_staged_claim.assert_called_with("parent-managed", "child-managed")

    @patch("cli_agent_orchestrator.mcp_server.server.acknowledge_handoff_child_result_direct")
    @patch("cli_agent_orchestrator.mcp_server.server.claim_handoff_child_result_direct")
    @patch(
        "cli_agent_orchestrator.mcp_server.server.claim_staged_handoff_result_direct",
        return_value=None,
    )
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_claimed_handoff_child_result_direct",
        return_value=None,
    )
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_acknowledged_handoff_child_result_direct",
        return_value=None,
    )
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_handoff_parent_terminal_id",
        return_value=None,
    )
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_parent_completion_barrier",
        return_value=(0, 0),
    )
    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_output")
    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_terminal")
    @patch("cli_agent_orchestrator.mcp_server.server.requests.post")
    @patch.dict(os.environ, {"CAO_TERMINAL_ID": "parent-live"})
    def test_marker_only_v1_waits_then_claims_later_complete_v1_once(
        self,
        mock_exit,
        mock_terminal,
        mock_output,
        mock_barrier,
        mock_parent,
        mock_acknowledged,
        mock_claimed,
        mock_staged_claim,
        mock_claim,
        mock_ack,
    ):
        """A partial marker cannot claim before the later stable V1 capture."""
        full_output = (
            "• CAO_RESULT_V1\n"
            '  {"summary": "complete", "body_markdown": "full structured report", "format": "v1"}'
        )
        mock_terminal.side_effect = [("completed", "running")] * 3
        mock_output.side_effect = ["CAO_RESULT_V1", full_output, full_output]
        mock_staged_claim.return_value = None
        mock_claim.return_value = True
        mock_ack.return_value = full_output
        mock_exit.return_value.raise_for_status.return_value = None

        first = asyncio.run(_await_handoff_impl("codex-live", timeout=0))

        assert first.state == HandoffState.WAITING
        assert "malformed CAO_RESULT_V1 final output" in first.message
        mock_staged_claim.assert_called_once_with("parent-live", "codex-live")
        mock_claim.assert_not_called()
        mock_exit.assert_not_called()

        second = asyncio.run(_await_handoff_impl("codex-live", timeout=1))

        assert second.state == HandoffState.COMPLETED
        assert second.output == full_output
        mock_claim.assert_called_once_with("parent-live", "codex-live", full_output)
        mock_ack.assert_called_once_with("parent-live", "codex-live")
        mock_exit.assert_called_once_with("http://127.0.0.1:9889/terminals/codex-live/exit")

    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_output")
    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_terminal")
    @patch("cli_agent_orchestrator.mcp_server.server.requests.post")
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_parent_completion_barrier",
        return_value=(0, 0),
    )
    @patch.dict(os.environ, {"CAO_TERMINAL_ID": ""})
    def test_reawaits_same_child_then_exits_once(
        self, mock_barrier, mock_exit, mock_terminal, mock_output
    ):
        mock_terminal.side_effect = [("completed", "running"), ("completed", "running")]
        mock_output.side_effect = ["Summary\nall checks passed", "Summary\nall checks passed"]

        result = asyncio.run(_await_handoff_impl("codex-live", timeout=1))

        assert result.state == HandoffState.COMPLETED
        assert result.success is True
        assert result.terminal_id == "codex-live"
        assert result.output == "Summary\nall checks passed"
        mock_exit.assert_called_once_with("http://127.0.0.1:9889/terminals/codex-live/exit")

    @patch("cli_agent_orchestrator.mcp_server.server.acknowledge_handoff_child_result_direct")
    @patch("cli_agent_orchestrator.mcp_server.server.claim_handoff_child_result_direct")
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_claimed_handoff_child_result_direct",
        return_value=None,
    )
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_acknowledged_handoff_child_result_direct",
        return_value=None,
    )
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_parent_completion_barrier",
        return_value=(0, 0),
    )
    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_output")
    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_terminal")
    @patch("cli_agent_orchestrator.mcp_server.server.requests.post")
    @patch.dict(os.environ, {"CAO_TERMINAL_ID": "ordinary-parent"})
    def test_f11_no_relation_returns_valid_result_after_one_cleanup(
        self,
        mock_exit,
        mock_terminal,
        mock_output,
        mock_barrier,
        mock_acknowledged,
        mock_claimed,
        mock_claim,
        mock_ack,
    ):
        mock_terminal.side_effect = [("completed", "running"), ("completed", "running")]
        mock_output.side_effect = ["ordinary handoff result", "ordinary handoff result"]
        mock_claim.return_value = None
        mock_exit.return_value.raise_for_status.return_value = None

        result = asyncio.run(_await_handoff_impl("unrelated-child", timeout=1))

        assert result.state == HandoffState.COMPLETED
        assert result.success is True
        assert result.output == "ordinary handoff result"
        mock_claim.assert_called_once_with(
            "ordinary-parent", "unrelated-child", "ordinary handoff result"
        )
        mock_exit.assert_called_once_with("http://127.0.0.1:9889/terminals/unrelated-child/exit")
        mock_ack.assert_not_called()

    @patch("cli_agent_orchestrator.mcp_server.server.acknowledge_handoff_child_result_direct")
    @patch("cli_agent_orchestrator.mcp_server.server.get_claimed_handoff_child_result_direct")
    @patch("cli_agent_orchestrator.mcp_server.server.get_acknowledged_handoff_child_result_direct")
    @patch("cli_agent_orchestrator.mcp_server.server.claim_handoff_child_result_direct")
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_parent_completion_barrier",
        return_value=(0, 0),
    )
    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_output")
    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_terminal")
    @patch("cli_agent_orchestrator.mcp_server.server.requests.post")
    def test_cleanup_failure_keeps_direct_claim_retryable_until_one_successful_exit(
        self,
        mock_exit,
        mock_terminal,
        mock_output,
        mock_barrier,
        mock_claim,
        mock_claimed_output,
        mock_durable_claim,
        mock_ack,
    ):
        mock_terminal.side_effect = [("completed", "running")] * 3
        # The changed retry capture is deliberately never consumed: after the
        # first stable claim, cleanup resumes from durable state alone.
        mock_output.side_effect = ["stable report", "stable report", "changed report"]
        mock_claim.return_value = True
        mock_claimed_output.return_value = None
        mock_durable_claim.side_effect = [None, "stable report"]
        mock_exit.side_effect = [RuntimeError("temporary cleanup failure"), MagicMock()]
        mock_exit.return_value.raise_for_status.return_value = None
        mock_ack.return_value = "stable report"

        with patch.dict(os.environ, {"CAO_TERMINAL_ID": "parent-live"}):
            first = asyncio.run(_await_handoff_impl("child-live", timeout=1))
            second = asyncio.run(_await_handoff_impl("child-live", timeout=1))

        assert first.state == HandoffState.WAITING
        assert second.state == HandoffState.COMPLETED
        assert second.output == "stable report"
        mock_claim.assert_called_once_with("parent-live", "child-live", "stable report")
        assert mock_output.call_count == 2
        assert mock_exit.call_count == 2
        mock_ack.assert_called_once_with("parent-live", "child-live")

    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_output")
    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_terminal")
    @patch("cli_agent_orchestrator.mcp_server.server.requests.post")
    def test_exited_worker_without_a_valid_capture_is_not_resumable(
        self, mock_exit, mock_terminal, mock_output
    ):
        mock_terminal.return_value = ("completed", "exited")
        mock_output.return_value = "partial report..."

        result = asyncio.run(_await_handoff_impl("codex-exited", timeout=1))

        assert result.state == HandoffState.FAILED
        assert "persistent tmux" in result.message
        mock_exit.assert_not_called()

    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_output")
    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_terminal")
    @patch("cli_agent_orchestrator.mcp_server.server.requests.post")
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_parent_completion_barrier",
        return_value=(0, 0),
    )
    @patch.dict(os.environ, {"CAO_TERMINAL_ID": ""})
    def test_exited_worker_with_two_stable_valid_captures_returns_its_handoff_result(
        self, mock_barrier, mock_exit, mock_terminal, mock_output
    ):
        mock_terminal.side_effect = [("completed", "exited"), ("completed", "exited")]
        mock_output.side_effect = ["Summary\nall checks passed", "Summary\nall checks passed"]

        result = asyncio.run(_await_handoff_impl("codex-exited", timeout=1))

        assert result.state == HandoffState.COMPLETED
        assert result.success is True
        assert result.output == "Summary\nall checks passed"
        mock_exit.assert_not_called()

    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_terminal")
    @patch("cli_agent_orchestrator.mcp_server.server.requests.post")
    def test_provider_error_is_not_waiting_or_success(self, mock_exit, mock_terminal):
        mock_terminal.return_value = ("error", "running")

        result = asyncio.run(_await_handoff_impl("codex-error", timeout=1))

        assert result.state == HandoffState.FAILED
        mock_exit.assert_not_called()

    @pytest.mark.parametrize(
        "output",
        [
            "Working (11s • esc to interrupt)",
            "• Working (1m 01s · Esc to interrupt)",
            "\x1b[38;5;244m• Working (1m 01s • esc to interrupt)\x1b[0m\x1b[?25h",
            load_fixture("codex_progress_spinner_output.txt"),
            load_fixture("codex_progress_status_output.txt"),
            "partial report...",
        ],
    )
    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_output")
    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_terminal")
    @patch("cli_agent_orchestrator.mcp_server.server.requests.post")
    def test_progress_or_incomplete_output_never_succeeds(
        self, mock_exit, mock_terminal, mock_output, output
    ):
        mock_terminal.return_value = ("completed", "running")
        mock_output.return_value = output

        result = asyncio.run(_await_handoff_impl("codex-live", timeout=0))

        assert result.state == HandoffState.WAITING
        assert "output" in result.message
        mock_exit.assert_not_called()

    @pytest.mark.parametrize(
        ("fixture_name", "expected_problem"),
        [
            ("codex_context_exhausted_output.txt", "context-exhausted"),
            ("codex_idle_without_verdict_output.txt", "idle prompt chrome"),
        ],
    )
    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_output")
    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_terminal")
    @patch("cli_agent_orchestrator.mcp_server.server.requests.post")
    def test_t9_context_exhausted_or_idle_chrome_never_succeeds(
        self, mock_exit, mock_terminal, mock_output, fixture_name, expected_problem
    ):
        """T9: a Codex terminal can be idle without ever producing a verdict."""
        mock_terminal.return_value = ("completed", "running")
        mock_output.return_value = load_fixture(fixture_name)

        result = asyncio.run(_await_handoff_impl("codex-live", timeout=0))

        assert result.state == HandoffState.WAITING
        assert expected_problem in result.message
        mock_exit.assert_not_called()

    @pytest.mark.parametrize(
        "report",
        [
            "Summary\nContext window exhaustion handling is covered by regression tests.",
            "Context window exhausted handling was fixed; focused checks passed.",
        ],
    )
    def test_final_reports_that_discuss_context_exhaustion_remain_valid(self, report):
        assert _handoff_output_problem(report) is None

    def test_ordinary_prose_that_mentions_a_spinner_remains_valid(self):
        report = "The captured • Working (1m 01s • esc to interrupt) spinner was rejected."

        assert _handoff_output_problem(report) is None

    @pytest.mark.parametrize(
        "report",
        [
            "CAO_RESULT_V1",
            'CAO_RESULT_V1\n{"summary": "missing closing brace"',
        ],
    )
    def test_malformed_top_level_v1_envelope_never_succeeds(self, report):
        assert _handoff_output_problem(report) == "malformed CAO_RESULT_V1 final output"

    def test_trailing_no_tg_notify_after_v1_result_remains_fail_closed(self):
        """An echoed input-only marker cannot turn a malformed V1 capture into success."""
        report = (
            'CAO_RESULT_V1\n{"summary": "complete", "body_markdown": "report"}\n' "NO_TG_NOTIFY"
        )

        assert _handoff_output_problem(report) == "malformed CAO_RESULT_V1 final output"

    def test_undecorated_worked_footer_with_plain_indent_folds_is_a_valid_final_output(self):
        """Accept only the exact live Codex layout used by managed handoffs."""
        report = """CAO_RESULT_V1
  {"summary":"complete","body_markdown":"A re-
  wrapped report.","changed_files":[],"checks":[],"risks":[],"blockers":[],"format":"v1"}
─ Worked for 3m 18s ───────────────────────────────────────────────────────────
"""

        assert _handoff_output_problem(report) is None

    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_output")
    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_terminal")
    @patch("cli_agent_orchestrator.mcp_server.server.requests.post")
    @patch(
        "cli_agent_orchestrator.mcp_server.server.get_parent_completion_barrier",
        return_value=(0, 0),
    )
    @patch.dict(os.environ, {"CAO_TERMINAL_ID": ""})
    def test_final_report_with_context_language_exits_once(
        self, mock_barrier, mock_exit, mock_terminal, mock_output
    ):
        report = "Context window exhausted handling was fixed; focused checks passed."
        mock_terminal.side_effect = [("completed", "running"), ("completed", "running")]
        mock_output.side_effect = [report, report]

        result = asyncio.run(_await_handoff_impl("codex-live", timeout=1))

        assert result.state == HandoffState.COMPLETED
        assert result.output == report
        mock_exit.assert_called_once_with("http://127.0.0.1:9889/terminals/codex-live/exit")

    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_output")
    @patch("cli_agent_orchestrator.mcp_server.server._read_handoff_terminal")
    @patch("cli_agent_orchestrator.mcp_server.server.requests.post")
    def test_changed_output_does_not_exit_before_wait_slice_expires(
        self, mock_exit, mock_terminal, mock_output
    ):
        mock_terminal.side_effect = [("completed", "running"), ("completed", "running")]
        mock_output.side_effect = ["Summary\nfirst", "Summary\nsecond"]

        result = asyncio.run(_await_handoff_impl("codex-race", timeout=0))

        assert result.state == HandoffState.WAITING
        assert "changed" in result.message
        mock_exit.assert_not_called()

    @patch("cli_agent_orchestrator.mcp_server.server._await_handoff_impl")
    @patch("cli_agent_orchestrator.mcp_server.server._send_direct_input")
    @patch("cli_agent_orchestrator.mcp_server.server.wait_until_terminal_status", return_value=True)
    @patch("cli_agent_orchestrator.mcp_server.server._create_terminal")
    @patch.dict(os.environ, {"CAO_TERMINAL_ID": ""})
    def test_initial_handoff_submits_once_then_delegates_to_resumable_wait(
        self, mock_create, mock_ready, mock_send, mock_await
    ):
        mock_create.return_value = ("codex-live", "codex")
        mock_await.return_value = HandoffResult(
            success=False,
            message="waiting",
            terminal_id="codex-live",
            state=HandoffState.WAITING,
        )

        result = asyncio.run(_handoff_impl("developer", "Produce a final marker.", timeout=1))

        assert result.state == HandoffState.WAITING
        mock_send.assert_called_once()
        mock_await.assert_awaited_once()
