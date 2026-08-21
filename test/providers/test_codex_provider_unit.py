"""Unit tests for Codex provider."""

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from cli_agent_orchestrator.clients.database import parse_v1_result_capture
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.codex import (
    CodexProvider,
    CodexStartupError,
    CodexStartupNoReadyError,
    ProviderError,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(filename: str) -> str:
    with open(FIXTURES_DIR / filename, "r") as f:
        return f.read()


def assert_stably_completed(provider: CodexProvider) -> None:
    """Completion requires the initial candidate plus two stable polls."""
    assert provider.get_status() == TerminalStatus.PROCESSING
    assert provider.get_status() == TerminalStatus.PROCESSING
    assert provider.get_status() == TerminalStatus.COMPLETED


class TestCodexProviderInitialization:
    @patch("cli_agent_orchestrator.providers.codex.CodexProvider._wait_for_startup_ready")
    @patch("cli_agent_orchestrator.providers.codex.CodexProvider._handle_trust_prompt")
    @patch("cli_agent_orchestrator.providers.codex.time.sleep")
    @patch("cli_agent_orchestrator.providers.codex.wait_for_shell")
    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_initialize_success(
        self, mock_tmux, mock_wait_shell, mock_sleep, mock_trust, mock_wait_ready
    ):
        mock_wait_shell.return_value = True

        provider = CodexProvider("test1234", "test-session", "window-0", None)
        result = provider.initialize()

        assert result is True
        mock_wait_shell.assert_called_once()
        # Two send_keys calls: warm-up echo + codex with tmux-compatible flags
        assert mock_tmux.send_keys.call_count == 2
        mock_tmux.send_keys.assert_any_call("test-session", "window-0", "echo ready")
        launch = mock_tmux.send_keys.call_args_list[1].args[2]
        assert launch.startswith("codex --yolo --no-alt-screen --disable shell_snapshot")
        assert "__CAO_CODEX_STARTUP_EXIT_test1234_1__" in launch
        mock_trust.assert_called_once_with(timeout=20.0)
        mock_wait_ready.assert_called_once_with(timeout=60.0)

    @patch("cli_agent_orchestrator.providers.codex.wait_for_shell")
    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_initialize_shell_timeout(self, mock_tmux, mock_wait_shell):
        mock_wait_shell.return_value = False

        provider = CodexProvider("test1234", "test-session", "window-0", None)

        with pytest.raises(TimeoutError, match="Shell initialization timed out"):
            provider.initialize()

    @patch("cli_agent_orchestrator.providers.codex.CodexProvider._wait_for_startup_ready")
    @patch("cli_agent_orchestrator.providers.codex.CodexProvider._handle_trust_prompt")
    @patch("cli_agent_orchestrator.providers.codex.time.sleep")
    @patch("cli_agent_orchestrator.providers.codex.wait_for_shell")
    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_initialize_codex_timeout(
        self, mock_tmux, mock_wait_shell, mock_sleep, mock_trust, mock_wait_ready
    ):
        mock_wait_shell.return_value = True
        mock_wait_ready.side_effect = CodexStartupNoReadyError("no ready", "startup evidence")

        provider = CodexProvider("test1234", "test-session", "window-0", None)

        with pytest.raises(CodexStartupNoReadyError, match="no ready"):
            provider.initialize()

    @patch("cli_agent_orchestrator.providers.codex.time.sleep")
    @patch("cli_agent_orchestrator.providers.codex.wait_for_shell")
    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_initialize_detects_early_cli_exit_with_retained_evidence(
        self, mock_tmux, mock_wait_shell, mock_sleep
    ):
        mock_wait_shell.return_value = True
        provider = CodexProvider("test1234", "test-session", "window-0", None)

        def startup_exit(*_args, **_kwargs):
            return f"invalid config\n{provider._startup_exit_marker}:127\n"

        mock_tmux.get_history.side_effect = startup_exit

        with pytest.raises(
            CodexStartupError, match="exited during startup with status 127"
        ) as error:
            provider.initialize()

        assert "invalid config" in error.value.startup_evidence

    @patch("cli_agent_orchestrator.providers.codex.time.sleep")
    @patch("cli_agent_orchestrator.providers.codex.time.monotonic", side_effect=[0.0, 60.0])
    @patch("cli_agent_orchestrator.providers.codex.CodexProvider._handle_trust_prompt")
    @patch("cli_agent_orchestrator.providers.codex.wait_for_shell")
    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_initialize_no_ready_state_has_retained_evidence(
        self, mock_tmux, mock_wait_shell, mock_trust, mock_monotonic, mock_sleep
    ):
        mock_wait_shell.return_value = True
        mock_tmux.get_history.return_value = "Booting MCP server: cao-mcp-server\n"
        provider = CodexProvider("test1234", "test-session", "window-0", None)
        provider.get_status = MagicMock(return_value=TerminalStatus.PROCESSING)

        with pytest.raises(CodexStartupNoReadyError, match="without a ready state") as error:
            provider.initialize()

        assert "Booting MCP server" in error.value.startup_evidence


class TestCodexBuildCommand:
    def test_build_command_no_profile(self):
        provider = CodexProvider("test1234", "test-session", "window-0", None)
        command = provider._build_codex_command()
        assert command == "codex --yolo --no-alt-screen --disable shell_snapshot"

    @patch("cli_agent_orchestrator.providers.codex.load_agent_profile")
    def test_structured_owner_attestation_uses_snapshotted_profile_without_secret(
        self, mock_load_profile
    ):
        profile = MagicMock()
        profile.model = "gpt-test"
        profile.system_prompt = "Owner executor instructions."
        profile.mcpServers = None
        profile.codexConfig = {}
        provider = CodexProvider(
            "owner-terminal",
            "owner-session",
            "owner-window",
            "critical_sol_xhigh_owner",
            resolved_profile=profile,
            structured_owner_authorized=True,
        )

        command = provider._build_codex_command()

        mock_load_profile.assert_not_called()
        assert "THREADCELLS STRUCTURED OWNER AUTHORIZATION" in command
        assert "Do not require the compatibility magic text" in command
        assert "Owner executor instructions" in command
        assert "X-ThreadCells-Owner-Grant" not in command

    @patch("cli_agent_orchestrator.providers.codex.load_agent_profile")
    def test_build_command_with_skill_prompt(self, mock_load_profile):
        mock_profile = MagicMock()
        mock_profile.model = None
        mock_profile.system_prompt = "You are a supervisor."
        mock_profile.mcpServers = None
        mock_load_profile.return_value = mock_profile

        provider = CodexProvider(
            "test1234",
            "test-session",
            "window-0",
            "code_supervisor",
            skill_prompt="## Available Skills\n- **python-testing**: Pytest",
        )
        command = provider._build_codex_command()

        mock_load_profile.assert_called_once_with("code_supervisor")
        assert "developer_instructions=" in command
        assert "## Available Skills" in command
        assert "python-testing" in command

    @patch("cli_agent_orchestrator.providers.codex.load_agent_profile")
    def test_build_command_with_agent_profile(self, mock_load_profile):
        mock_profile = MagicMock()
        mock_profile.model = None
        mock_profile.system_prompt = "You are a code supervisor agent."
        mock_profile.mcpServers = None
        mock_load_profile.return_value = mock_profile

        provider = CodexProvider("test1234", "test-session", "window-0", "code_supervisor")
        command = provider._build_codex_command()

        mock_load_profile.assert_called_once_with("code_supervisor")
        assert "codex --yolo --no-alt-screen --disable shell_snapshot" in command
        assert "-c" in command
        assert "developer_instructions=" in command
        assert "You are a code supervisor agent." in command

    @patch("cli_agent_orchestrator.providers.codex.load_agent_profile")
    def test_build_command_escapes_quotes(self, mock_load_profile):
        mock_profile = MagicMock()
        mock_profile.model = None
        mock_profile.system_prompt = 'Use "double quotes" carefully.'
        mock_profile.mcpServers = None
        mock_load_profile.return_value = mock_profile

        provider = CodexProvider("test1234", "test-session", "window-0", "test_agent")
        command = provider._build_codex_command()

        assert '\\"double quotes\\"' in command

    @patch("cli_agent_orchestrator.providers.codex.load_agent_profile")
    def test_build_command_escapes_newlines(self, mock_load_profile):
        mock_profile = MagicMock()
        mock_profile.model = None
        mock_profile.system_prompt = "Line one.\nLine two.\n\n## Section\n- Item"
        mock_profile.mcpServers = None
        mock_load_profile.return_value = mock_profile

        provider = CodexProvider("test1234", "test-session", "window-0", "test_agent")
        command = provider._build_codex_command()

        # Literal newlines must be escaped to \n for TOML and tmux compatibility
        assert "\n" not in command
        assert "\\n" in command
        assert "Line one.\\nLine two.\\n\\n## Section\\n- Item" in command

    @pytest.mark.parametrize("launch_mode", ["handoff", "assign"])
    @patch.dict("os.environ", {"CAO_TERMINAL_AUTH_TOKEN": "secret-terminal-auth"}, clear=False)
    @patch("cli_agent_orchestrator.providers.codex.ACTIVE_RUNTIME_GENERATION", "generation-current")
    @patch("cli_agent_orchestrator.providers.codex.load_agent_profile")
    def test_managed_child_command_pins_cao_mcp_runtime_and_context(
        self, mock_load_profile, launch_mode
    ):
        mock_profile = MagicMock()
        mock_profile.model = None
        mock_profile.system_prompt = "You are a supervisor."
        mock_profile.mcpServers = {
            "cao-mcp-server": {
                "type": "stdio",
                "command": "uvx",
                "args": [
                    "--from",
                    "git+https://example.com/repo.git@main",
                    "cao-mcp-server",
                ],
            }
        }
        mock_load_profile.return_value = mock_profile

        provider = CodexProvider("test1234", "test-session", "window-0", "code_supervisor")
        command = provider._build_codex_command()

        assert "mcp_servers.cao-mcp-server.command=" in command
        assert "threadcells-mcp-server" in command
        assert "uvx" not in command
        assert "git+https://example.com/repo.git@main" not in command
        assert "mcp_servers.cao-mcp-server.args=" not in command
        # Managed handoff and assign children inherit these names without
        # serializing their capability value into the Codex command.
        assert "mcp_servers.cao-mcp-server.env_vars=" in command
        assert "CAO_TERMINAL_ID" in command
        assert "CAO_TERMINAL_AUTH_TOKEN" in command
        assert (
            'mcp_servers.cao-mcp-server.env.CAO_RUNTIME_GENERATION="generation-current"' in command
        )
        assert "secret-terminal-auth" not in command
        # Tool timeout must be a TOML float (600.0) for Codex's f64 deserializer
        assert "mcp_servers.cao-mcp-server.tool_timeout_sec=600.0" in command

    @patch("cli_agent_orchestrator.providers.codex.load_agent_profile")
    def test_codex_startup_relaunch_reuses_canonical_mcp_runtime(self, mock_load_profile):
        mock_profile = MagicMock()
        mock_profile.model = None
        mock_profile.system_prompt = ""
        mock_profile.mcpServers = {
            "cao-mcp-server": {
                "command": "uvx",
                "args": ["--from", "git+https://example.com/repo.git@main", "cao-mcp-server"],
            }
        }
        mock_load_profile.return_value = mock_profile

        first_attempt = CodexProvider("first", "session", "window", "developer")
        retry_attempt = CodexProvider("retry", "session", "window", "developer")

        assert first_attempt._build_codex_command() == retry_attempt._build_codex_command()
        assert "threadcells-mcp-server" in first_attempt._build_codex_command()

    @patch("cli_agent_orchestrator.providers.codex.load_agent_profile")
    def test_build_command_preserves_profile_codex_config_overrides(self, mock_load_profile):
        mock_profile = MagicMock()
        mock_profile.model = "gpt-5.6-terra"
        mock_profile.system_prompt = ""
        mock_profile.mcpServers = None
        mock_profile.codexConfig = {
            "model_reasoning_effort": "high",
            "features.multi_agent_v2": False,
        }
        mock_load_profile.return_value = mock_profile

        command = CodexProvider(
            "test1234", "test-session", "window-0", "developer_terra_high"
        )._build_codex_command()

        assert 'model_reasoning_effort="high"' in command
        assert "features.multi_agent_v2=false" in command

    @patch("cli_agent_orchestrator.providers.codex.load_agent_profile")
    def test_build_command_forwards_explicit_mcp_tool_timeout(self, mock_load_profile):
        mock_profile = MagicMock()
        mock_profile.model = None
        mock_profile.system_prompt = ""
        mock_profile.mcpServers = {
            "cao-mcp-server": {
                "command": "threadcells-mcp-server",
                "args": [],
                "tool_timeout_sec": 1800.0,
            }
        }
        mock_load_profile.return_value = mock_profile

        provider = CodexProvider("test1234", "test-session", "window-0", "test_agent")
        command = provider._build_codex_command()

        assert "mcp_servers.cao-mcp-server.tool_timeout_sec=1800.0" in command
        assert "mcp_servers.cao-mcp-server.tool_timeout_sec=600.0" not in command

    @patch("cli_agent_orchestrator.providers.codex.load_agent_profile")
    def test_build_command_with_mcp_servers_env(self, mock_load_profile):
        mock_profile = MagicMock()
        mock_profile.model = None
        mock_profile.system_prompt = ""
        mock_profile.mcpServers = {
            "test-server": {
                "command": "npx",
                "args": ["-y", "test-server"],
                "env": {"API_KEY": "secret123"},
            }
        }
        mock_load_profile.return_value = mock_profile

        provider = CodexProvider("test1234", "test-session", "window-0", "test_agent")
        command = provider._build_codex_command()

        assert "mcp_servers.test-server.command=" in command
        assert "mcp_servers.test-server.env.API_KEY=" in command
        assert "secret123" in command
        # CAO_TERMINAL_ID always forwarded even without explicit env_vars
        assert "mcp_servers.test-server.env_vars=" in command
        assert "CAO_TERMINAL_ID" in command
        assert "CAO_TERMINAL_AUTH_TOKEN" not in command

    @patch("cli_agent_orchestrator.providers.codex.load_agent_profile")
    def test_build_command_mcp_preserves_existing_env_vars(self, mock_load_profile):
        mock_profile = MagicMock()
        mock_profile.model = None
        mock_profile.system_prompt = ""
        mock_profile.mcpServers = {
            "my-server": {
                "command": "node",
                "args": ["server.js"],
                "env_vars": ["HOME", "PATH"],
            }
        }
        mock_load_profile.return_value = mock_profile

        provider = CodexProvider("test1234", "test-session", "window-0", "test_agent")
        command = provider._build_codex_command()

        # Existing env_vars preserved and CAO_TERMINAL_ID appended
        assert "HOME" in command
        assert "PATH" in command
        assert "CAO_TERMINAL_ID" in command

    @patch("cli_agent_orchestrator.providers.codex.load_agent_profile")
    def test_build_command_empty_system_prompt(self, mock_load_profile):
        mock_profile = MagicMock()
        mock_profile.model = None
        mock_profile.system_prompt = ""
        mock_profile.mcpServers = None
        mock_load_profile.return_value = mock_profile

        provider = CodexProvider("test1234", "test-session", "window-0", "empty_agent")
        command = provider._build_codex_command()

        assert command == "codex --yolo --no-alt-screen --disable shell_snapshot"
        assert "developer_instructions" not in command

    @patch("cli_agent_orchestrator.providers.codex.load_agent_profile")
    def test_build_command_none_system_prompt(self, mock_load_profile):
        mock_profile = MagicMock()
        mock_profile.model = None
        mock_profile.system_prompt = None
        mock_profile.mcpServers = None
        mock_load_profile.return_value = mock_profile

        provider = CodexProvider("test1234", "test-session", "window-0", "none_agent")
        command = provider._build_codex_command()

        assert command == "codex --yolo --no-alt-screen --disable shell_snapshot"

    @patch("cli_agent_orchestrator.providers.codex.load_agent_profile")
    def test_build_command_profile_load_failure(self, mock_load_profile):
        mock_load_profile.side_effect = RuntimeError("Profile not found")

        provider = CodexProvider("test1234", "test-session", "window-0", "bad_agent")

        with pytest.raises(ProviderError, match="Failed to load agent profile"):
            provider._build_codex_command()

    @patch("cli_agent_orchestrator.providers.codex.CodexProvider._wait_for_startup_ready")
    @patch("cli_agent_orchestrator.providers.codex.CodexProvider._handle_trust_prompt")
    @patch("cli_agent_orchestrator.providers.codex.time.sleep")
    @patch("cli_agent_orchestrator.providers.codex.wait_for_shell")
    @patch("cli_agent_orchestrator.providers.codex.load_agent_profile")
    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_initialize_with_agent_profile(
        self,
        mock_tmux,
        mock_load_profile,
        mock_wait_shell,
        mock_sleep,
        mock_trust,
        mock_wait_ready,
    ):
        mock_wait_shell.return_value = True
        mock_profile = MagicMock()
        mock_profile.model = None
        mock_profile.system_prompt = "You are a supervisor."
        mock_profile.mcpServers = None
        mock_load_profile.return_value = mock_profile

        provider = CodexProvider("test1234", "test-session", "window-0", "code_supervisor")
        result = provider.initialize()

        assert result is True
        # The second send_keys call should contain developer_instructions
        codex_call = mock_tmux.send_keys.call_args_list[1]
        assert "developer_instructions=" in codex_call.args[2]
        assert "You are a supervisor." in codex_call.args[2]


class TestCodexProviderModelFlag:
    """Tests that profile.model is forwarded to Codex via --model."""

    @patch("cli_agent_orchestrator.providers.codex.load_agent_profile")
    def test_build_command_appends_model_when_set(self, mock_load):
        mock_profile = MagicMock()
        mock_profile.model = "gpt-5"
        mock_profile.system_prompt = None
        mock_profile.mcpServers = None
        mock_load.return_value = mock_profile

        provider = CodexProvider("tid", "sess", "win", "agent")
        command = provider._build_codex_command()

        assert "--model gpt-5" in command

    @patch("cli_agent_orchestrator.providers.codex.load_agent_profile")
    def test_build_command_omits_model_when_unset(self, mock_load):
        mock_profile = MagicMock()
        mock_profile.model = None
        mock_profile.system_prompt = None
        mock_profile.mcpServers = None
        mock_load.return_value = mock_profile

        provider = CodexProvider("tid", "sess", "win", "agent")
        command = provider._build_codex_command()

        assert "--model" not in command


class TestCodexProviderStatusDetection:
    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_idle(self, mock_tmux):
        mock_tmux.get_history.return_value = load_fixture("codex_idle_output.txt")

        provider = CodexProvider("test1234", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.IDLE

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_completed(self, mock_tmux):
        mock_tmux.get_history.return_value = load_fixture("codex_completed_output.txt")

        provider = CodexProvider("test1234", "test-session", "window-0")
        assert_stably_completed(provider)

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_processing(self, mock_tmux):
        mock_tmux.get_history.return_value = load_fixture("codex_processing_output.txt")

        provider = CodexProvider("test1234", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.PROCESSING

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_waiting_user_answer(self, mock_tmux):
        mock_tmux.get_history.return_value = load_fixture("codex_permission_output.txt")

        provider = CodexProvider("test1234", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.WAITING_USER_ANSWER

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_error(self, mock_tmux):
        mock_tmux.get_history.return_value = load_fixture("codex_error_output.txt")

        provider = CodexProvider("test1234", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.ERROR

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_empty_output(self, mock_tmux):
        mock_tmux.get_history.return_value = ""

        provider = CodexProvider("test1234", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.ERROR

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_with_tail_lines(self, mock_tmux):
        mock_tmux.get_history.return_value = load_fixture("codex_idle_output.txt")

        provider = CodexProvider("test1234", "test-session", "window-0")
        status = provider.get_status(tail_lines=50)

        assert status == TerminalStatus.IDLE
        mock_tmux.get_history.assert_called_once_with("test-session", "window-0", tail_lines=50)

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_processing_when_old_prompt_present(self, mock_tmux):
        # If the captured history contains an earlier prompt but the *latest* output is processing,
        # we should report PROCESSING. The old prompt should be far enough from the bottom
        # (more than IDLE_PROMPT_TAIL_LINES) to avoid false idle detection.
        mock_tmux.get_history.return_value = (
            "Welcome to Codex\n"
            "❯ \n"
            "You Fix the failing tests\n"
            "assistant: Working on it...\n"
            "Reading file src/main.py...\n"
            "Analyzing code structure...\n"
            "Checking dependencies...\n"
            "Codex is thinking…\n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.PROCESSING

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_not_error_on_failed_in_message(self, mock_tmux):
        # "failed" is commonly used in normal assistant output; it should not automatically
        # force ERROR.
        mock_tmux.get_history.return_value = (
            "You Explain why the test failed\n"
            "assistant: The test failed because the assertion is incorrect.\n"
            "\n"
            "❯ \n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")
        assert_stably_completed(provider)

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_idle_if_no_assistant_after_last_user(self, mock_tmux):
        # If there is a user message but no assistant response after it, we should not
        # treat the session as COMPLETED.
        mock_tmux.get_history.return_value = "assistant: Welcome\n" "You Do the thing\n" "\n" "❯ \n"

        provider = CodexProvider("test1234", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.IDLE

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_processing_when_no_prompt_and_no_keywords(self, mock_tmux):
        # Codex output may not always include explicit "thinking/processing" keywords.
        # Without an idle prompt at the end, we should assume it's still processing.
        mock_tmux.get_history.return_value = "You Run the command\nWorking...\n"

        provider = CodexProvider("test1234", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.PROCESSING

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_not_error_when_assistant_mentions_error_text(self, mock_tmux):
        mock_tmux.get_history.return_value = (
            "You Explain the failure\n"
            "assistant: Here's an example error:\n"
            "Error: example only\n"
            "\n"
            "❯ \n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")
        assert_stably_completed(provider)

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_not_waiting_when_assistant_mentions_approval_text(self, mock_tmux):
        mock_tmux.get_history.return_value = (
            "You Explain approvals\n"
            "assistant: You might see this prompt:\n"
            "Approve this command? [y/n]\n"
            "\n"
            "❯ \n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")
        assert_stably_completed(provider)

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_error_when_error_after_user_and_prompt(self, mock_tmux):
        mock_tmux.get_history.return_value = "You Run thing\nError: failed\n\n❯ \n"

        provider = CodexProvider("test1234", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.ERROR

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_waiting_user_answer_when_no_user_prefix(self, mock_tmux):
        mock_tmux.get_history.return_value = "Approve this command? [y/n]\n"

        provider = CodexProvider("test1234", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.WAITING_USER_ANSWER

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_error_when_no_user_prefix(self, mock_tmux):
        mock_tmux.get_history.return_value = "Error: something failed\n"

        provider = CodexProvider("test1234", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.ERROR

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_idle_tui_with_status_bar(self, mock_tmux):
        """Test IDLE detection with realistic TUI output (status bar after prompt)."""
        mock_tmux.get_history.return_value = (
            "╭───────────────────────────────────────────╮\n"
            "│ >_ OpenAI Codex (v0.98.0)                 │\n"
            "│ model: gpt-5.3-codex high                 │\n"
            "│ directory: ~/project                      │\n"
            "╰───────────────────────────────────────────╯\n"
            "  Tip: Try the Codex App\n"
            "› Use /skills to list available skills\n"
            "  ? for shortcuts                     100% context left\n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.IDLE

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_completed_tui_with_status_bar(self, mock_tmux):
        """Test COMPLETED detection with TUI output (status bar after prompt)."""
        mock_tmux.get_history.return_value = (
            "You Fix the bug\n"
            "assistant: I've fixed the issue in main.py.\n"
            "\n"
            "› \n"
            "  ? for shortcuts                     100% context left\n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")
        assert_stably_completed(provider)


class TestCodexBulletFormatStatusDetection:
    """Tests for Codex's real interactive output format using › prompt and • bullets."""

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_completed_bullet_format(self, mock_tmux):
        """COMPLETED when › user message followed by • response and idle prompt."""
        mock_tmux.get_history.return_value = (
            "› what is your role?\n"
            "• I am the Coding Supervisor Agent.\n"
            "• I coordinate tasks between developer and reviewer agents.\n"
            "\n"
            "› \n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")
        assert_stably_completed(provider)

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_processing_bullet_format(self, mock_tmux):
        """PROCESSING when • response started but no idle prompt at bottom."""
        mock_tmux.get_history.return_value = (
            "› fix the failing tests\n"
            "• Let me look at the test files.\n"
            "Reading src/test_main.py...\n"
            "Analyzing code structure...\n"
            "Checking dependencies...\n"
            "Running unit tests...\n"
            "Codex is thinking…\n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.PROCESSING

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_idle_bullet_format_no_response(self, mock_tmux):
        """IDLE when › user message but no • response yet and idle prompt at bottom."""
        mock_tmux.get_history.return_value = "› hello\n\n› \n"

        provider = CodexProvider("test1234", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.IDLE

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_completed_bullet_with_code_block(self, mock_tmux):
        """COMPLETED with • response containing code blocks."""
        mock_tmux.get_history.return_value = (
            "› show me a function\n"
            "• Here's the function:\n"
            "\n"
            "  ```python\n"
            "  def hello():\n"
            "      print('hello')\n"
            "  ```\n"
            "\n"
            "• Let me know if you need changes.\n"
            "\n"
            "› \n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")
        assert_stably_completed(provider)

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_error_not_masked_by_bullet_pattern(self, mock_tmux):
        """ERROR still detected when no • response and error after › user message."""
        mock_tmux.get_history.return_value = "› do something\nError: connection refused\n"

        provider = CodexProvider("test1234", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.ERROR

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_completed_multi_turn_bullet(self, mock_tmux):
        """COMPLETED uses last user message in multi-turn bullet format."""
        mock_tmux.get_history.return_value = (
            "› first question\n"
            "• First answer.\n"
            "\n"
            "› second question\n"
            "• Second answer with details.\n"
            "\n"
            "› \n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")
        assert_stably_completed(provider)

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_completed_bullet_with_tui_status_bar(self, mock_tmux):
        """COMPLETED with bullet format and TUI status bar after prompt."""
        mock_tmux.get_history.return_value = (
            "› fix the bug\n"
            "• I've fixed the issue in main.py by correcting the import.\n"
            "\n"
            "› \n"
            "  ? for shortcuts                     98% context left\n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")
        assert_stably_completed(provider)

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_processing_tui_spinner(self, mock_tmux):
        """PROCESSING when TUI shows • Working spinner, not false COMPLETED."""
        mock_tmux.get_history.return_value = (
            "› [CAO Handoff] Supervisor terminal ID: sup-123. Do the task.\n"
            "\n"
            "• Working (0s • esc to interrupt)\n"
            "\n"
            "› Use /skills to list available skills\n"
            "\n"
            "  ? for shortcuts                     100% context left\n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.PROCESSING

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    @pytest.mark.parametrize(
        "spinner",
        [
            "• Working (1m 01s • esc to interrupt)",
            "• Working (1m 01s · Esc to interrupt)",
        ],
    )
    def test_get_status_processing_tui_spinner_with_minute_duration(self, mock_tmux, spinner):
        """Long-running Codex spinners are live progress, never COMPLETED."""
        mock_tmux.get_history.return_value = (
            "› [CAO Handoff] Produce the report.\n"
            f"{spinner}\n"
            "› Continue working\n"
            "  gpt-5.6-terra high · 96% left · /workspace\n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")

        assert provider.get_status() == TerminalStatus.PROCESSING

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_processing_tui_thinking_spinner(self, mock_tmux):
        """PROCESSING when TUI shows • Thinking spinner."""
        mock_tmux.get_history.return_value = (
            "› Implement feature X\n"
            "\n"
            "• Thinking (3s • esc to interrupt)\n"
            "\n"
            "› Run /review on my current changes\n"
            "\n"
            "  ? for shortcuts                     95% context left\n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.PROCESSING

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_processing_dynamic_spinner_text(self, mock_tmux):
        """PROCESSING when TUI shows spinner with dynamic prefix text."""
        mock_tmux.get_history.return_value = (
            "› [CAO Handoff] Do the task.\n"
            "\n"
            "• Creating /tmp/file.py\n"
            "\n"
            "• Starting script creation (10s • esc to interrupt)\n"
            "\n"
            "› Use /skills to list available skills\n"
            "\n"
            "  ? for shortcuts                     100% context left\n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.PROCESSING


class TestCodexV0111FooterFormat:
    """Tests for Codex v0.111.0+ TUI footer format.

    v0.111.0 (PR #13202 'tui: restore draft footer hints') changed the footer:
    - Old: "› Use /skills to list available skills\\n  ? for shortcuts  100% context left"
    - New: "› Find and fix a bug in @filename\\n  gpt-5.3-codex high · 100% left · ~/path"
    The new format uses "N% left" instead of "N% context left" and removes "? for shortcuts".
    """

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_idle_v0111_footer(self, mock_tmux):
        """IDLE with v0.111.0 footer format (no '? for shortcuts')."""
        mock_tmux.get_history.return_value = (
            "╭───────────────────────────────────────────╮\n"
            "│ >_ OpenAI Codex (v0.111.0)                │\n"
            "│ model: gpt-5.3-codex high                 │\n"
            "│ directory: ~/project                      │\n"
            "╰───────────────────────────────────────────╯\n"
            "  Tip: You can run any shell command from Codex using ! (e.g. !ls)\n"
            "\n"
            "› Find and fix a bug in @filename\n"
            "\n"
            "  gpt-5.3-codex high · 100% left · ~/project\n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.IDLE

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_completed_v0111_footer(self, mock_tmux):
        """COMPLETED with v0.111.0 footer (suggestion hint must not be treated as user input)."""
        mock_tmux.get_history.return_value = (
            "› fix the bug\n"
            "• I've fixed the issue in main.py by correcting the import.\n"
            "\n"
            "› Find and fix a bug in @filename\n"
            "\n"
            "  gpt-5.3-codex high · 98% left · ~/project\n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")
        assert_stably_completed(provider)

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_completed_v0111_multi_turn(self, mock_tmux):
        """COMPLETED in multi-turn with v0.111.0 footer."""
        mock_tmux.get_history.return_value = (
            "› first question\n"
            "• First answer.\n"
            "\n"
            "› second question\n"
            "• Second answer with details.\n"
            "\n"
            "› Write tests for @main.py\n"
            "\n"
            "  gpt-5.3-codex high · 95% left · ~/project\n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")
        assert_stably_completed(provider)

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_processing_v0111_spinner(self, mock_tmux):
        """PROCESSING when TUI shows spinner with v0.111.0 footer."""
        mock_tmux.get_history.return_value = (
            "› [CAO Handoff] Do the task.\n"
            "\n"
            "• Working (0s • esc to interrupt)\n"
            "\n"
            "› Find and fix a bug in @filename\n"
            "\n"
            "  gpt-5.3-codex high · 100% left · ~/project\n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.PROCESSING


class TestCodexV0146FooterAndPromptRegressions:
    """Regression coverage for the 2026-08-07 Codex 0.146.0 captures."""

    @staticmethod
    def _rate_limit_advisory(selected_option: int = 1, styled: bool = True) -> str:
        selected_marker = "\x1b[1m\x1b[38;5;6;49m›\x1b[0m" if styled else "›"
        return (
            "Approaching rate limits\n\n"
            "Switch to gpt-5.6-luna for lower credit usage?\n\n"
            f"{selected_marker if selected_option == 1 else ' '} 1. Switch to gpt-5.6-luna "
            "                Fast and affordable agentic coding model.\n"
            f"{selected_marker if selected_option == 2 else ' '} 2. Keep current model\n"
            f"{selected_marker if selected_option == 3 else ' '} 3. Keep current model (never show again)  "
            "Hide future rate limit reminders about switching models.\n\n"
            "Press enter to confirm or esc to go back\n"
        )

    @staticmethod
    def _fast_mode_advisory(selected_option: int = 1, styled: bool = True) -> str:
        selected_marker = "\x1b[1m\x1b[38;5;6;49m›\x1b[0m" if styled else "›"
        return (
            "Our systems are thinking a bit more about this request before responding.\n"
            "Hang tight or retry with a faster model for a quicker response, though it may be "
            "less capable of handling complex requests.\n\n"
            f"{selected_marker if selected_option == 1 else ' '} 1. Retry with a faster model\n"
            f"{selected_marker if selected_option == 2 else ' '} 2. Dismiss and keep waiting\n"
            f"{selected_marker if selected_option == 3 else ' '} 3. Learn more\n\n"
            "No action is required. Codex will keep waiting, and this menu will close when the "
            "response is ready.\n"
        )

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_long_completed_worker_without_visible_user_line_reaches_completed(self, mock_tmux):
        """The real worker's long output scrolled its handoff input out of tail history."""
        capture = load_fixture("codex_v0146_completed_worker_output.txt")
        mock_tmux.get_history.return_value = capture
        provider = CodexProvider("6dff3cf7", "media", "developer")
        provider.mark_input_received()

        assert_stably_completed(provider)
        message = provider.extract_last_message_from_script(capture)
        assert "consumer-to-preset mapping" in message
        assert "classification: OWNER_GATE" in message
        assert "Explain this codebase" not in message
        assert "gpt-5.6-terra" not in message

    @pytest.mark.parametrize(
        ("selected_option", "expected_keys"),
        [
            (1, ["Down", "Down", "Enter"]),
            (2, ["Down", "Enter"]),
            (3, ["Enter"]),
        ],
    )
    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_rate_limit_switch_prompt_keeps_pinned_model_and_suppresses_reminders(
        self, mock_tmux, selected_option, expected_keys
    ):
        mock_tmux.get_history.return_value = self._rate_limit_advisory(selected_option)
        provider = CodexProvider("aaf8d0e3", "media", "supervisor")

        assert provider.get_status() == TerminalStatus.PROCESSING
        assert mock_tmux.send_special_key.call_args_list == [
            call("media", "supervisor", key) for key in expected_keys
        ]

        # Dismissing the advisory must let the already-running request finish,
        # rather than leaving the provider in an interactive blocked state.
        mock_tmux.get_history.return_value = (
            "› [CAO Handoff] Continue the task.\n"
            "• Continued final report\n"
            "› Explain this codebase\n"
            "gpt-5.6-terra high · /workspace/sample-project\n"
        )
        assert_stably_completed(provider)

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_rate_limit_advisory_uses_raw_capture_with_trailing_blank_rows(self, mock_tmux):
        """Suffix normalization must not weaken the selected-row advisory fence."""
        mock_tmux.get_history.return_value = self._rate_limit_advisory() + "\n" * 6
        provider = CodexProvider("aaf8d0e3", "media", "supervisor")

        assert provider.get_status() == TerminalStatus.PROCESSING
        assert mock_tmux.send_special_key.call_args_list == [
            call("media", "supervisor", "Down"),
            call("media", "supervisor", "Down"),
            call("media", "supervisor", "Enter"),
        ]

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_historical_rate_limit_menu_is_inert_before_active_permission_prompt(self, mock_tmux):
        mock_tmux.get_history.return_value = (
            self._rate_limit_advisory() + "\nApprove command execution? (y/n)\n"
        )
        provider = CodexProvider("aaf8d0e3", "media", "supervisor")

        assert provider.get_status() == TerminalStatus.WAITING_USER_ANSWER
        mock_tmux.send_special_key.assert_not_called()

    @pytest.mark.parametrize(
        "active_prompt",
        [
            "Confirm destructive action? (y/n)",
            "Owner approval required before continuing.",
            "› What should I do next?",
        ],
    )
    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_historical_fast_advisory_is_inert_before_newer_sensitive_or_user_prompt(
        self, mock_tmux, active_prompt
    ):
        mock_tmux.get_history.return_value = self._fast_mode_advisory() + f"\n{active_prompt}\n"
        provider = CodexProvider("56ff2f43", "media", "developer")

        provider.get_status()
        mock_tmux.send_special_key.assert_not_called()

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_advisory_quoted_in_assistant_output_is_inert(self, mock_tmux):
        mock_tmux.get_history.return_value = (
            "• The previous UI contained this rate-limit text:\n"
            + self._rate_limit_advisory(styled=False)
        )
        provider = CodexProvider("aaf8d0e3", "media", "supervisor")

        provider.get_status()
        mock_tmux.send_special_key.assert_not_called()

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_advisory_quoted_in_user_content_is_inert(self, mock_tmux):
        mock_tmux.get_history.return_value = (
            "› Please assess this older Fast advisory verbatim:\n"
            + self._fast_mode_advisory(styled=False)
        )
        provider = CodexProvider("56ff2f43", "media", "developer")

        provider.get_status()
        mock_tmux.send_special_key.assert_not_called()

    @pytest.mark.parametrize(
        ("menu", "expected_keys"),
        [
            (_rate_limit_advisory.__func__(1), ["Down", "Down", "Enter"]),
            (_fast_mode_advisory.__func__(1), ["Down", "Enter"]),
        ],
    )
    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_handled_advisory_remaining_in_scrollback_does_not_replay_keys(
        self, mock_tmux, menu, expected_keys
    ):
        mock_tmux.get_history.return_value = menu
        provider = CodexProvider("advisory", "media", "developer")

        assert provider.get_status() == TerminalStatus.PROCESSING
        assert provider.get_status() == TerminalStatus.PROCESSING
        assert mock_tmux.send_special_key.call_args_list == [
            call("media", "developer", key) for key in expected_keys
        ]

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_permission_prompt_is_not_auto_dismissed_as_rate_limit_menu(self, mock_tmux):
        mock_tmux.get_history.return_value = "Approve command execution? (y/n)\n"
        provider = CodexProvider("aaf8d0e3", "media", "supervisor")

        assert provider.get_status() == TerminalStatus.WAITING_USER_ANSWER
        mock_tmux.send_special_key.assert_not_called()

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_unselected_or_renumbered_model_menu_is_not_auto_dismissed(self, mock_tmux):
        mock_tmux.get_history.return_value = (
            "Approaching rate limits\n\n"
            "Switch to gpt-5.6-luna for lower credit usage?\n\n"
            "  1. Switch to gpt-5.6-luna Fast and affordable agentic coding model.\n"
            "  2. Keep current model\n"
            "  4. Keep current model (never show again) Hide future rate limit reminders about "
            "switching models.\n\n"
            "Press enter to confirm or esc to go back\n"
        )
        provider = CodexProvider("aaf8d0e3", "media", "supervisor")

        assert provider.get_status() == TerminalStatus.PROCESSING
        mock_tmux.send_special_key.assert_not_called()

    @pytest.mark.parametrize(
        ("selected_option", "expected_keys"),
        [
            (1, ["Down", "Enter"]),
            (2, ["Enter"]),
            (3, ["Up", "Enter"]),
        ],
    )
    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_fast_mode_advisory_dismisses_and_keeps_current_mode(
        self, mock_tmux, selected_option, expected_keys
    ):
        mock_tmux.get_history.return_value = self._fast_mode_advisory(selected_option)
        provider = CodexProvider("56ff2f43", "media", "developer")

        assert provider.get_status() == TerminalStatus.PROCESSING
        assert mock_tmux.send_special_key.call_args_list == [
            call("media", "developer", key) for key in expected_keys
        ]
        mock_tmux.send_keys.assert_not_called()

        mock_tmux.get_history.return_value = (
            "› [CAO Handoff] Continue the task.\n"
            "• Continued final report\n"
            "› Explain this codebase\n"
            "gpt-5.6-terra high · /workspace/sample-project\n"
        )
        assert_stably_completed(provider)

    @patch("cli_agent_orchestrator.providers.codex.load_agent_profile")
    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_fast_advisory_does_not_change_profile_selected_model_reasoning_or_fast_preference(
        self, mock_tmux, mock_load_profile
    ):
        profile = MagicMock()
        profile.model = "gpt-5.6-terra"
        profile.system_prompt = ""
        profile.mcpServers = None
        # Local 0.146.0 binary config schema exposes this preference key. CAO
        # only forwards it; dismissing an unsolicited advisory must not alter it.
        profile.codexConfig = {
            "model_reasoning_effort": "high",
            "fast_default_opt_out": False,
        }
        mock_load_profile.return_value = profile
        provider = CodexProvider("56ff2f43", "media", "developer", "developer_terra_high")
        command_before = provider._build_codex_command()
        mock_tmux.get_history.return_value = self._fast_mode_advisory()

        assert provider.get_status() == TerminalStatus.PROCESSING
        assert provider._build_codex_command() == command_before
        assert "--model gpt-5.6-terra" in command_before
        assert 'model_reasoning_effort="high"' in command_before
        assert "fast_default_opt_out=false" in command_before
        assert mock_tmux.send_special_key.call_args_list == [
            call("media", "developer", "Down"),
            call("media", "developer", "Enter"),
        ]
        mock_tmux.send_keys.assert_not_called()

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_fast_tip_or_explicit_fast_command_is_not_auto_answered(self, mock_tmux):
        mock_tmux.get_history.return_value = (
            "Tip: New Use /fast to enable our fastest inference with increased plan usage.\n"
            "› /fast\n"
        )
        provider = CodexProvider("56ff2f43", "media", "developer")

        assert provider.get_status() == TerminalStatus.PROCESSING
        mock_tmux.send_special_key.assert_not_called()

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_clean_codex_exit_to_shell_is_completed_not_processing(self, mock_tmux):
        provider = CodexProvider("c3d28adf", "media", "reviewer")
        provider._initialized = True
        provider._startup_exit_marker = "__CAO_CODEX_STARTUP_EXIT_c3d28adf_1__"
        mock_tmux.get_history.return_value = (
            "• Final review report\n"
            "__CAO_CODEX_STARTUP_EXIT_c3d28adf_1__:0\n"
            "agentctl@cao:/workspace/sample-project$\n"
        )

        assert provider.get_status() == TerminalStatus.COMPLETED
        assert provider.is_process_alive() is False

    @pytest.mark.parametrize(
        "capture",
        [
            (
                "• Assistant prose happens to end in a shell-like token $\n"
                "__CAO_CODEX_STARTUP_EXIT_c3d28adf_1__:0\n"
            ),
            "__CAO_CODEX_STARTUP_EXIT_c3d28adf_1__:0\n",
            ("__CAO_CODEX_STARTUP_EXIT_c3d28adf_2__:0\n" "agentctl@cao:/workspace/sample-project$\n"),
            ("__CAO_CODEX_STARTUP_EXIT_c3d28adf_1__:1\n" "agentctl@cao:/workspace/sample-project$\n"),
            ("__CAO_CODEX_STARTUP_EXIT_c3d28adf_1__:0\n" "Assistant prose ending in currency $\n"),
            (
                "__CAO_CODEX_STARTUP_EXIT_c3d28adf_1__:0\n"
                "agentctl@cao:/workspace/sample-project this is prose $\n"
            ),
        ],
        ids=[
            "shell-like-prose-before-sentinel",
            "sentinel-without-shell",
            "wrong-attempt-marker",
            "nonzero-sentinel",
            "arbitrary-dollar-after-sentinel",
            "shell-prefix-but-prose-after-sentinel",
        ],
    )
    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_exit_to_shell_rejects_unordered_or_non_shell_evidence(self, mock_tmux, capture):
        provider = CodexProvider("c3d28adf", "media", "reviewer")
        provider._initialized = True
        provider._startup_exit_marker = "__CAO_CODEX_STARTUP_EXIT_c3d28adf_1__"
        mock_tmux.get_history.return_value = capture

        assert provider.get_status() != TerminalStatus.COMPLETED

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_nonzero_exit_marks_provider_process_dead(self, mock_tmux):
        provider = CodexProvider("c3d28adf", "media", "reviewer")
        provider._initialized = True
        provider._startup_exit_marker = "__CAO_CODEX_STARTUP_EXIT_c3d28adf_1__"
        mock_tmux.get_history.return_value = "__CAO_CODEX_STARTUP_EXIT_c3d28adf_1__:1\n"

        assert provider.get_status() == TerminalStatus.ERROR
        assert provider.is_process_alive() is False

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_fresh_provider_rehydrates_clean_exit_from_its_persisted_sentinel(self, mock_tmux):
        provider = CodexProvider("96046e3b", "media", "reviewer")
        mock_tmux.get_history.return_value = (
            "• Final review report\n"
            "__CAO_CODEX_STARTUP_EXIT_96046e3b_3__:0\n"
            "agentctl@cao:/workspace/sample-project$\n"
        )

        assert provider.get_status() == TerminalStatus.COMPLETED
        assert provider.is_process_alive() is False

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_fresh_provider_rehydrates_nonzero_exit_from_its_persisted_sentinel(self, mock_tmux):
        provider = CodexProvider("b2ce0ac0", "media", "reviewer")
        mock_tmux.get_history.return_value = "__CAO_CODEX_STARTUP_EXIT_b2ce0ac0_2__:127\n"

        assert provider.get_status() == TerminalStatus.ERROR
        assert provider.is_process_alive() is False

    @pytest.mark.parametrize(
        "capture",
        [
            "__CAO_CODEX_STARTUP_EXIT_other-terminal_1__:0\nagentctl@cao:/workspace/sample-project$\n",
            "__CAO_CODEX_STARTUP_EXIT_96046e3b_1__:0\n",
            (
                "__CAO_CODEX_STARTUP_EXIT_96046e3b_1__:0\n"
                "assistant prose\n"
                "agentctl@cao:/workspace/sample-project$\n"
            ),
        ],
        ids=["wrong-terminal", "no-post-sentinel-prompt", "non-prompt-before-shell"],
    )
    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_fresh_provider_rejects_untrusted_persisted_clean_exit_evidence(
        self, mock_tmux, capture
    ):
        provider = CodexProvider("96046e3b", "media", "reviewer")
        mock_tmux.get_history.return_value = capture

        assert provider.get_status() != TerminalStatus.COMPLETED
        assert provider.is_process_alive() is True

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_fresh_provider_keeps_live_codex_pane_alive_without_sentinel(self, mock_tmux):
        provider = CodexProvider("856868e2", "media", "reviewer")
        mock_tmux.get_history.return_value = (
            "› [CAO Handoff] Keep working.\n"
            "• Working (4s • esc to interrupt)\n"
            "› Continue working\n"
            "gpt-5.6-terra high · 96% left · /workspace/sample-project\n"
        )

        assert provider.get_status() == TerminalStatus.PROCESSING
        assert provider.is_process_alive() is True


class TestCodexProviderMessageExtraction:
    def test_extract_last_message_success(self):
        output = load_fixture("codex_completed_output.txt")

        provider = CodexProvider("test1234", "test-session", "window-0")
        message = provider.extract_last_message_from_script(output)

        assert "Here's the fix" in message
        assert "All tests now pass." in message

    def test_extract_complex_message(self):
        output = load_fixture("codex_complex_response.txt")

        provider = CodexProvider("test1234", "test-session", "window-0")
        message = provider.extract_last_message_from_script(output)

        assert "def add(a, b):" in message
        assert "Let me know" in message

    def test_extract_message_no_marker(self):
        output = "No assistant prefix here"

        provider = CodexProvider("test1234", "test-session", "window-0")

        with pytest.raises(ValueError, match="No Codex response found"):
            provider.extract_last_message_from_script(output)

    def test_extract_message_empty_response(self):
        output = "assistant:   \n\n❯ "

        provider = CodexProvider("test1234", "test-session", "window-0")

        with pytest.raises(ValueError, match="Empty Codex response"):
            provider.extract_last_message_from_script(output)


class TestCodexBulletFormatExtraction:
    """Tests for message extraction from Codex's real • bullet format."""

    def test_extract_bullet_format_single_line(self):
        """Extract single-line • response."""
        output = "› what is your role?\n• I am the Coding Supervisor Agent.\n\n› \n"

        provider = CodexProvider("test1234", "test-session", "window-0")
        message = provider.extract_last_message_from_script(output)

        assert "I am the Coding Supervisor Agent." in message

    def test_extract_bullet_format_multi_line(self):
        """Extract multi-line • response with all bullets preserved."""
        output = (
            "› describe your capabilities\n"
            "• I can coordinate development tasks.\n"
            "• I assign work to developer agents.\n"
            "• I review results from workers.\n"
            "\n"
            "› \n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")
        message = provider.extract_last_message_from_script(output)

        assert "coordinate development tasks" in message
        assert "assign work" in message
        assert "review results" in message

    def test_extract_bullet_format_with_code_block(self):
        """Extract • response containing code blocks."""
        output = (
            "› show me the fix\n"
            "• Here's the corrected code:\n"
            "\n"
            "  ```python\n"
            "  def add(a, b):\n"
            "      return a + b\n"
            "  ```\n"
            "\n"
            "• All tests pass now.\n"
            "\n"
            "› \n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")
        message = provider.extract_last_message_from_script(output)

        assert "def add(a, b):" in message
        assert "All tests pass now." in message

    def test_extract_bullet_format_multi_turn(self):
        """Extract only the last response from multi-turn • format."""
        output = (
            "› first question\n"
            "• First answer.\n"
            "\n"
            "› second question\n"
            "• Second answer with more detail.\n"
            "• Additional context here.\n"
            "\n"
            "› \n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")
        message = provider.extract_last_message_from_script(output)

        # Should only contain the second response
        assert "First answer" not in message
        assert "Second answer with more detail." in message
        assert "Additional context here." in message

    def test_extract_bullet_format_without_trailing_prompt(self):
        """Extract • response when no trailing idle prompt (output still streaming)."""
        output = "› fix the bug\n• I've fixed the import issue in main.py.\n"

        provider = CodexProvider("test1234", "test-session", "window-0")
        message = provider.extract_last_message_from_script(output)

        assert "I've fixed the import issue" in message


class TestCodexV0111Extraction:
    """Extraction tests for Codex v0.111.0+ footer format."""

    def test_extract_bullet_with_v0111_footer(self):
        """Extract response when v0.111.0 footer (suggestion hint) is present."""
        output = (
            "› fix the bug\n"
            "• I've fixed the issue in main.py by correcting the import.\n"
            "\n"
            "› Find and fix a bug in @filename\n"
            "\n"
            "  gpt-5.3-codex high · 98% left · ~/project\n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")
        message = provider.extract_last_message_from_script(output)

        assert "I've fixed the issue" in message
        # Suggestion hint should not leak into extracted output
        assert "Find and fix a bug" not in message
        assert "gpt-5.3-codex" not in message

    def test_extract_multi_turn_with_v0111_footer(self):
        """Extract last response from multi-turn with v0.111.0 footer."""
        output = (
            "› first question\n"
            "• First answer.\n"
            "\n"
            "› second question\n"
            "• Second answer with details.\n"
            "\n"
            "› Write tests for @main.py\n"
            "\n"
            "  gpt-5.3-codex high · 95% left · ~/project\n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")
        message = provider.extract_last_message_from_script(output)

        assert "First answer" not in message
        assert "Second answer with details." in message
        assert "Write tests" not in message

    def test_extract_double_blank_between_hint_and_status(self):
        """Suggestion hint must not leak when 2 blank lines separate it from status bar."""
        output = (
            "› fix the bug\n"
            "• I've fixed the issue in main.py by correcting the import.\n"
            "\n"
            "› Find and fix a bug in @filename\n"
            "\n"
            "\n"
            "  gpt-5.3-codex high · 98% left · ~/project\n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")
        message = provider.extract_last_message_from_script(output)

        assert "I've fixed the issue" in message
        assert "Find and fix a bug" not in message


class TestCodexTrailingCaptureBlankRows:
    """F14 provider coverage for blank rows appended by capture-pane."""

    _v1_document = (
        '{"summary":"suffix capture complete","body_markdown":"stable result",' '"format":"v1"}'
    )

    @staticmethod
    def _footer_output(body: str, suffix: str) -> str:
        return (
            "› [CAO Handoff] Produce the result.\n"
            f"{body}\n"
            "› Continue working\n"
            "  gpt-5.6-terra high · 96% left · /workspace\n"
            f"{suffix}"
        )

    @pytest.mark.parametrize(
        ("suffix", "case"),
        [
            ("\n", "one blank row"),
            ("\n" * 5, "five blank rows"),
            ("\n" * 6, "six blank rows"),
            ("\n \t\n  \t\n\t  ", "horizontal-whitespace blank rows"),
        ],
    )
    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_footer_suffix_blanks_preserve_v1_completion_and_extraction(
        self, mock_tmux, suffix, case
    ):
        output = self._footer_output(f"• CAO_RESULT_V1\n  {self._v1_document}", suffix)
        mock_tmux.get_history.return_value = output
        provider = CodexProvider("test1234", "test-session", "window-0")

        assert_stably_completed(provider)
        is_v1, document = parse_v1_result_capture(provider.extract_last_message_from_script(output))

        assert case
        assert is_v1 is True
        assert document is not None
        assert document.summary == "suffix capture complete"

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_scrolled_user_tail_with_footer_suffix_blanks_completes_and_extracts_v1(
        self, mock_tmux
    ):
        output = self._footer_output(f"• CAO_RESULT_V1\n  {self._v1_document}", "\n" * 6)
        mock_tmux.get_history.return_value = output
        provider = CodexProvider("test1234", "test-session", "window-0")
        provider.mark_input_received()

        assert_stably_completed(provider)
        is_v1, document = parse_v1_result_capture(provider.extract_last_message_from_script(output))

        assert is_v1 is True
        assert document is not None
        assert document.body_markdown == "stable result"

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_spinner_remains_structurally_processing_after_footer_suffix_blanks(self, mock_tmux):
        mock_tmux.get_history.return_value = self._footer_output(
            "• Working (4s • esc to interrupt)", "\n" * 6
        )

        provider = CodexProvider("test1234", "test-session", "window-0")

        assert provider.get_status() == TerminalStatus.PROCESSING

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_no_footer_suffix_blanks_leave_idle_boundary_and_v1_extraction_intact(self, mock_tmux):
        output = (
            "› [CAO Handoff] Produce the result.\n"
            f"• CAO_RESULT_V1\n  {self._v1_document}\n"
            "› \n"
            "\n" * 6
        )
        mock_tmux.get_history.return_value = output
        provider = CodexProvider("test1234", "test-session", "window-0")

        assert_stably_completed(provider)
        is_v1, document = parse_v1_result_capture(provider.extract_last_message_from_script(output))

        assert is_v1 is True
        assert document is not None
        assert document.summary == "suffix capture complete"

    @pytest.mark.parametrize(
        ("output", "expected"),
        [
            ("Approve command execution? (y/n)\n" + "\n" * 6, TerminalStatus.WAITING_USER_ANSWER),
            ("Error: provider terminal failed\n" + "\n" * 6, TerminalStatus.ERROR),
            (
                "Do you trust the contents of this directory?\n" + "\n" * 6,
                TerminalStatus.WAITING_USER_ANSWER,
            ),
        ],
    )
    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_suffix_blanks_preserve_waiting_error_and_trust_classification(
        self, mock_tmux, output, expected
    ):
        mock_tmux.get_history.return_value = output

        assert CodexProvider("test1234", "test-session", "window-0").get_status() == expected


class TestCodexCompletionDebounce:
    """Regression coverage for inline Codex footer/progress races."""

    @staticmethod
    def _frame(body: str, footer: bool = True) -> str:
        suffix = (
            "\n› Continue working\n" "  gpt-5.3-codex high · 96% left · ~/project\n"
            if footer
            else "\n"
        )
        return f"› [CAO Handoff] Produce the report.\n{body}{suffix}"

    @staticmethod
    def _old_footer() -> str:
        return "\n› Continue working\n\n  ? for shortcuts                     96% context left\n"

    @staticmethod
    def _bare_footer() -> str:
        return "\n› \n"

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    @pytest.mark.parametrize("trailing_blanks", ["", "\n" * 6])
    def test_stale_spinner_and_spinner_shaped_json_do_not_mask_current_final(
        self, mock_tmux, trailing_blanks
    ):
        """Only the current whole body row before footer chrome is live progress."""
        mock_tmux.get_history.return_value = (
            "› [CAO Handoff] Produce the report.\n"
            "• Called status probe\n"
            '  └ {"output":"• Working (15s • esc to interrupt)"}\n'
            "• Summary\n"
            "The final report is complete.\n" + self._old_footer() + trailing_blanks
        )
        provider = CodexProvider("test1234", "test-session", "window-0")

        assert_stably_completed(provider)

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    @pytest.mark.parametrize(
        ("spinner", "footer"),
        [
            ("• Working (1s • esc to interrupt)", _old_footer.__func__()),
            (
                "• Thinking (1m 01s · Esc to interrupt)",
                "\n› Continue working\n  gpt-5.6-terra high · 96% left · ~/project\n",
            ),
            ("• Starting task (1h 2m 3s • esc to interrupt)", _bare_footer.__func__()),
        ],
        ids=["v0110", "v0111", "bare"],
    )
    def test_current_spinner_frame_stays_processing(self, mock_tmux, spinner, footer):
        mock_tmux.get_history.return_value = (
            "› [CAO Handoff] Produce the report.\n" + spinner + footer + "\n" * 6
        )
        provider = CodexProvider("test1234", "test-session", "window-0")

        for _ in range(4):
            assert provider.get_status() == TerminalStatus.PROCESSING

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_live_spinner_to_final_requires_a_new_three_poll_candidate(self, mock_tmux):
        spinner = self._frame("• Working (4s • esc to interrupt)\n")
        final = self._frame("• Summary\nFinal report after live work.\n")
        mock_tmux.get_history.side_effect = [spinner, final, final, final]
        provider = CodexProvider("test1234", "test-session", "window-0")

        assert provider.get_status() == TerminalStatus.PROCESSING
        assert provider.get_status() == TerminalStatus.PROCESSING
        assert provider.get_status() == TerminalStatus.PROCESSING
        assert provider.get_status() == TerminalStatus.COMPLETED

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_rehydrated_final_ignores_stale_spinner_only_after_input_was_received(self, mock_tmux):
        output = (
            "• Called status probe\n"
            '  └ {"output":"• Working (15s • esc to interrupt)"}\n'
            "• Summary\n"
            "Rehydrated final report.\n"
            "\n› Continue working\n"
            "  gpt-5.6-terra high · 96% left · ~/project\n"
        )
        mock_tmux.get_history.return_value = output

        ordinary_idle = CodexProvider("test1234", "test-session", "window-0")
        assert ordinary_idle.get_status() == TerminalStatus.IDLE

        rehydrated = CodexProvider("test1234", "test-session", "window-0")
        rehydrated.mark_input_received()
        assert_stably_completed(rehydrated)

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_completion_requires_two_stable_polls_after_changed_tool_frame(self, mock_tmux):
        """A footer after tool output cannot finish a handoff before its report."""
        progress = self._frame("• Ran rg -n handoff src\n  └ partial result\n")
        changed = self._frame("• Ran pytest test/providers\n  └ still collecting results\n")
        final = self._frame(
            "• Summary\n" "Implemented the narrow handoff fix.\n" "SAMPLE_WORKER_READONLY_OK\n"
        )
        # ANSI/cursor noise and a changing context percentage do not constitute
        # a semantic response change during the two confirmation polls.
        final_with_noise = final.replace("96%", "95%").replace(
            "• Summary", "\x1b[32m• Summary\x1b[0m\x1b[?25h"
        )
        mock_tmux.get_history.side_effect = [
            progress,
            changed,
            final,
            final_with_noise,
            final,
        ]

        provider = CodexProvider("test1234", "test-session", "window-0")

        assert provider.get_status() == TerminalStatus.PROCESSING
        assert provider.get_status() == TerminalStatus.PROCESSING
        assert provider.get_status() == TerminalStatus.PROCESSING
        assert provider.get_status() == TerminalStatus.PROCESSING
        assert provider.get_status() == TerminalStatus.COMPLETED

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    @pytest.mark.parametrize("verb", ["Ran", "Read", "Searched"])
    def test_final_prose_with_common_tool_verb_completes(self, mock_tmux, verb):
        """A prose report is not a tool frame merely because of its first word."""
        final = self._frame(f"• {verb} the focused tests successfully; all 144 passed.\n")
        mock_tmux.get_history.return_value = final
        provider = CodexProvider("test1234", "test-session", "window-0")

        assert_stably_completed(provider)

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    @pytest.mark.parametrize(
        "next_frame",
        [
            _frame.__func__("• Summary\nChanged report content.\n"),
            _frame.__func__("• Working (4s • esc to interrupt)\n"),
            "› [CAO Handoff] first task\n• First report.\n› second task\n\n› \n",
            _frame.__func__("• Summary\nFooter vanished.\n", footer=False),
        ],
        ids=["output-changes", "spinner", "new-user-input", "footer-disappears"],
    )
    def test_activity_resets_completion_candidate(self, mock_tmux, next_frame):
        initial = self._frame("• Summary\nInitial report.\n")
        mock_tmux.get_history.side_effect = [initial, next_frame]
        provider = CodexProvider("test1234", "test-session", "window-0")

        assert provider.get_status() == TerminalStatus.PROCESSING
        assert provider.get_status() == TerminalStatus.PROCESSING

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_live_a_explored_frame_remains_processing_until_final_v1(self, mock_tmux):
        """A completed-looking footer after exploration is not a final result."""
        output = load_fixture("codex_live_a_explored_v1_output.txt")
        exploration, _separator, _final = output.partition(
            "────────────────────────────────────────────────────────────────────────────────"
        )
        mock_tmux.get_history.return_value = (
            exploration
            + "› Continue working\n"
            + "  gpt-5.6-terra high · 96% left · /workspace/sample-project\n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")

        assert provider.get_status() == TerminalStatus.PROCESSING


class TestCodexFinalReportExtraction:
    def test_live_undecorated_last_transcript_recovers_plain_separator_v1(self):
        """The live LAST capture may omit the marker's display bullet."""
        output = load_fixture("codex_live_undecorated_last_v1_output.txt")

        message = CodexProvider(
            "test1234", "test-session", "window-0"
        ).extract_last_message_from_script(output)
        is_v1, document = parse_v1_result_capture(message)

        assert "\nCAO_RESULT_V1\n" in output
        assert message.startswith("CAO_RESULT_V1\n")
        assert is_v1 is True
        assert document is not None
        assert document.summary == "Live undecorated LAST handoff complete"
        assert document.body_markdown == "Recovered the exact live re-port fold."

    def test_live_a_explored_transcript_extracts_only_decorated_v1_result(self):
        output = load_fixture("codex_live_a_explored_v1_output.txt")

        message = CodexProvider(
            "test1234", "test-session", "window-0"
        ).extract_last_message_from_script(output)
        is_v1, document = parse_v1_result_capture(message)

        assert "\n• CAO_RESULT_V1\n" in output
        assert message.startswith("CAO_RESULT_V1\n")
        assert "Ran rg --files" not in message
        assert "Explored" not in message
        assert "test/mcp_server/test_handoff.py" not in message
        assert is_v1 is True
        assert document is not None
        assert document.summary == "Live A handoff complete"
        assert document.body_markdown == "The final structured handoff report."

    def test_called_tool_frame_isolated_from_final_v1_block(self):
        output = (
            "› [CAO Handoff] Produce the result.\n"
            "• Called mcp__cao_mcp_server__send_message\n"
            "  └ delivered\n"
            "• CAO_RESULT_V1\n"
            '  {"summary": "implemented", "body_markdown": "report", "format": "v1"}\n'
            "\n› \n"
        )

        message = CodexProvider(
            "test1234", "test-session", "window-0"
        ).extract_last_message_from_script(output)

        assert message.startswith("• CAO_RESULT_V1\n")
        assert "Called mcp__cao_mcp_server__send_message" not in message
        assert "└ delivered" not in message

    def test_extracts_compact_final_report_after_large_tool_transcript(self):
        tool_transcript = "\n".join(f"  │ tool transcript line {i}" for i in range(1151))
        output = (
            "› [CAO Handoff] Run the read-only audit.\n"
            "• Ran a long read-only command\n"
            f"{tool_transcript}\n"
            "• Summary\n"
            "Read-only audit completed.\n"
            "SAMPLE_WORKER_READONLY_OK\n"
            "\n› \n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")
        message = provider.extract_last_message_from_script(output)

        assert "Read-only audit completed." in message
        assert "SAMPLE_WORKER_READONLY_OK" in message
        assert "Ran a long read-only command" not in message
        assert "tool transcript line 0" not in message
        assert "tool transcript line 1150" not in message

    def test_fallback_preserves_complete_multiline_final_report(self):
        """No visible user task must not make fallback discard earlier bullets."""
        output = (
            "• Ran a prior command\n"
            "  └ prior command output\n"
            "• Read a prior file\n"
            "  └ prior file output\n"
            "• Summary\n"
            "Completed the audit.\n"
            "• Verification\n"
            "144 tests passed.\n"
            "SAMPLE_WORKER_READONLY_OK\n"
            "\n› \n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")
        message = provider.extract_last_message_from_script(output)

        assert message == (
            "• Summary\nCompleted the audit.\n• Verification\n"
            "144 tests passed.\nSAMPLE_WORKER_READONLY_OK"
        )

    def test_extracts_no_tool_multibullet_final_turn(self):
        output = (
            "• Summary\n"
            "Completed the audit.\n"
            "• Verification\n"
            "144 tests passed.\n"
            "SAMPLE_WORKER_READONLY_OK\n"
            "\n› \n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")

        assert provider.extract_last_message_from_script(output) == (
            "• Summary\nCompleted the audit.\n• Verification\n"
            "144 tests passed.\nSAMPLE_WORKER_READONLY_OK"
        )

    def test_fallback_extracts_only_final_report_from_real_f7_capture_shape(self):
        """Blank-separated real tool blocks must not obscure the final report."""
        output = (
            "• Performing the required read-only checks.\n"
            "\n"
            "• Ran pwd\n"
            "  │ git branch --show-current\n"
            "  │ git rev-parse HEAD\n"
            "  │ … +1 lines\n"
            "  └ /srv/agent-control/projects/sample-project\n"
            "    master\n"
            "\n"
            "• Ran python3 - <<'PY'\n"
            "  │ for index in range(1, 1152):\n"
            '  │     print(f"F7_TOOL_LINE_{index:04d}")\n'
            "  │ … +1 lines\n"
            "  └ F7_TOOL_LINE_0001\n"
            "    F7_TOOL_LINE_0002\n"
            "    … +1147 lines (ctrl + t to view transcript)\n"
            "    F7_TOOL_LINE_1150\n"
            "    F7_TOOL_LINE_1151\n"
            "\n"
            "• Ran git branch --show-current\n"
            "  └ master\n"
            "\n"
            "• Ran git rev-parse HEAD\n"
            "  └ 0f5f5eeda0fef1ff81277d6aae6690caf42b5918\n"
            "\n"
            "• Ran git status --porcelain=v1 -uall\n"
            "  └ (no output)\n"
            "\n"
            "────────────────────────────────────────────────────────────────────────────────\n"
            "\n"
            "• Ran the real-provider long-transcript handoff regression successfully.\n"
            "  worker_profile=worker_luna_medium\n"
            "  working_directory=/srv/agent-control/projects/sample-project\n"
            "  branch=master\n"
            "  head=0f5f5eeda0fef1ff81277d6aae6690caf42b5918\n"
            "  working_tree_clean=yes\n"
            "  generated_tool_lines=1151\n"
            "  repository_modifications=none\n"
            "\n"
            "  SAMPLE_WORKER_READONLY_OK\n"
            "\n"
            "› Improve documentation in @filename gpt-5.6-luna medium"
            " · /srv/agent-control/projects/sample-project\n"
            "? for shortcuts\n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")
        message = provider.extract_last_message_from_script(output)

        assert message.startswith(
            "Ran the real-provider long-transcript handoff regression successfully."
        )
        for field in (
            "worker_profile=worker_luna_medium",
            "working_directory=/srv/agent-control/projects/sample-project",
            "branch=master",
            "head=0f5f5eeda0fef1ff81277d6aae6690caf42b5918",
            "working_tree_clean=yes",
            "generated_tool_lines=1151",
            "repository_modifications=none",
            "SAMPLE_WORKER_READONLY_OK",
        ):
            assert field in message
        for excluded in (
            "Performing the required read-only checks.",
            "Ran python3",
            "F7_TOOL_LINE_",
            "ctrl + t to view transcript",
            "────────────────────────────────────────────────────────────────────────────────",
            "? for shortcuts",
            "Improve documentation in @filename",
        ):
            assert excluded not in message


class TestCodexProviderMisc:
    def test_get_idle_pattern_for_log(self):
        provider = CodexProvider("test1234", "test-session", "window-0")
        pattern = provider.get_idle_pattern_for_log()
        # Codex TUI renders ❯ via cursor positioning (capture-pane only).
        # The pipe-pane log contains "? for shortcuts" from the TUI footer.
        assert pattern == r"\? for shortcuts"
        import re

        assert re.search(pattern, "? for shortcuts")

    def test_exit_cli(self):
        provider = CodexProvider("test1234", "test-session", "window-0")
        assert provider.exit_cli() == "/exit"

    def test_cleanup(self):
        provider = CodexProvider("test1234", "test-session", "window-0")
        provider._initialized = True
        provider.cleanup()
        assert provider._initialized is False

    def test_extract_last_message_without_trailing_prompt(self):
        output = "You do thing\nassistant: Hello\nSecond line\n"
        provider = CodexProvider("test1234", "test-session", "window-0")
        message = provider.extract_last_message_from_script(output)
        assert message == "Hello\nSecond line"


class TestCodexProviderTrustPrompt:
    """Tests for Codex workspace trust prompt handling."""

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_handle_trust_prompt_detected_and_accepted(self, mock_tmux):
        """Test that trust prompt is detected and auto-accepted."""
        mock_tmux.get_history.return_value = (
            "> You are running Codex in /Users/test/project\n"
            "\n"
            "  Since this folder is version controlled, you may wish to "
            "allow Codex to work in this folder without asking for approval.\n"
            "\n"
            "› 1. Yes, allow Codex to work in this folder without asking for approval\n"
            "  2. No, ask me to approve edits and commands\n"
        )
        mock_session = MagicMock()
        mock_window = MagicMock()
        mock_pane = MagicMock()
        mock_tmux.server.sessions.get.return_value = mock_session
        mock_session.windows.get.return_value = mock_window
        mock_window.active_pane = mock_pane

        provider = CodexProvider("test1234", "test-session", "window-0")
        provider._handle_trust_prompt(timeout=2.0)

        mock_pane.send_keys.assert_called_once_with("", enter=True)

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_handle_trust_prompt_not_needed(self, mock_tmux):
        """Test early return when Codex starts without trust prompt."""
        mock_tmux.get_history.return_value = "OpenAI Codex (v0.98.0)\n› "

        provider = CodexProvider("test1234", "test-session", "window-0")
        provider._handle_trust_prompt(timeout=2.0)

        mock_tmux.server.sessions.get.assert_not_called()

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_handle_current_codex_trust_prompt(self, mock_tmux):
        """Codex 0.146 uses a concise trust screen rather than the old wording."""
        mock_tmux.get_history.return_value = (
            "You are in /srv/agent-control/smoke/autonomy-recovery\n"
            "Do you trust the contents of this directory?\n"
            "1. Yes, continue\n"
            "2. No, quit\n"
            "Press enter to continue\n"
        )
        mock_session = MagicMock()
        mock_window = MagicMock()
        mock_pane = MagicMock()
        mock_tmux.server.sessions.get.return_value = mock_session
        mock_session.windows.get.return_value = mock_window
        mock_window.active_pane = mock_pane

        provider = CodexProvider("test1234", "test-session", "window-0")
        provider._handle_trust_prompt(timeout=2.0)

        mock_pane.send_keys.assert_called_once_with("", enter=True)

    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_get_status_trust_prompt_is_waiting_user_answer(self, mock_tmux):
        """Test that trust prompt reports WAITING_USER_ANSWER, not PROCESSING."""
        mock_tmux.get_history.return_value = (
            "> You are running Codex in /Users/test/project\n"
            "allow Codex to work in this folder without asking for approval.\n"
            "› 1. Yes\n"
        )

        provider = CodexProvider("test1234", "test-session", "window-0")
        status = provider.get_status()

        # Should be WAITING_USER_ANSWER (not PROCESSING despite "running" in text)
        assert status == TerminalStatus.WAITING_USER_ANSWER

    @patch("cli_agent_orchestrator.providers.codex.CodexProvider._wait_for_startup_ready")
    @patch("cli_agent_orchestrator.providers.codex.time.sleep")
    @patch("cli_agent_orchestrator.providers.codex.wait_for_shell")
    @patch("cli_agent_orchestrator.providers.codex.tmux_client")
    def test_initialize_with_trust_prompt(
        self, mock_tmux, mock_wait_shell, mock_sleep, mock_wait_ready
    ):
        """Test that initialize handles trust prompt during startup."""
        mock_wait_shell.return_value = True
        mock_tmux.get_history.return_value = (
            "allow Codex to work in this folder without asking for approval.\n"
        )
        mock_session = MagicMock()
        mock_window = MagicMock()
        mock_pane = MagicMock()
        mock_tmux.server.sessions.get.return_value = mock_session
        mock_session.windows.get.return_value = mock_window
        mock_window.active_pane = mock_pane

        provider = CodexProvider("test1234", "test-session", "window-0")
        result = provider.initialize()

        assert result is True
        mock_pane.send_keys.assert_called_with("", enter=True)
