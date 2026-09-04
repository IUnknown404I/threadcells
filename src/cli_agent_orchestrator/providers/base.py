"""Base provider interface for CLI tool abstraction.

This module defines the abstract base class that all CLI providers must implement.
A "provider" is an adapter that enables CAO to interact with a specific CLI-based
AI agent (e.g., Kiro CLI, Claude Code, Codex, Q CLI).

Provider Responsibilities:
- Initialize the CLI tool in a tmux window (run startup commands)
- Detect terminal state by parsing terminal output (IDLE, PROCESSING, COMPLETED, etc.)
- Extract agent responses from terminal output
- Provide cleanup logic when terminal is deleted

Implemented Providers:
- KiroCliProvider: For Kiro CLI (kiro-cli chat)
- ClaudeCodeProvider: For Claude Code (claude)
- CodexProvider: For Codex CLI (codex)
- QCliProvider: For Amazon Q Developer CLI (q chat)

Each provider must implement pattern matching for its specific CLI's prompt
and output format to reliably detect status changes.
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from cli_agent_orchestrator.models.provider import ProviderTurnOutcome
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.models.usage import UsageObservation


class BaseProvider(ABC):
    """Abstract base class for CLI tool providers.

    All CLI providers must inherit from this class and implement the abstract methods.
    The provider abstraction allows CAO to work with different CLI-based AI agents
    through a unified interface.

    Attributes:
        terminal_id: Unique identifier for the terminal this provider manages
        session_name: Name of the tmux session containing the terminal
        window_name: Name of the tmux window containing the terminal
        _status: Internal status cache (use get_status() for current status)
        _allowed_tools: CAO-vocabulary tool names this agent is allowed to use
    """

    def __init__(
        self,
        terminal_id: str,
        session_name: str,
        window_name: str,
        allowed_tools: Optional[List[str]] = None,
        skill_prompt: Optional[str] = None,
    ):
        """Initialize provider with terminal context.

        Args:
            terminal_id: Unique identifier for this terminal instance
            session_name: Name of the tmux session
            window_name: Name of the tmux window
            allowed_tools: Optional list of CAO tool names the agent is allowed to use
            skill_prompt: Optional skill catalog text built by the service layer.
                Providers append this to the system prompt when building their CLI command.
        """
        self.terminal_id = terminal_id
        self.session_name = session_name
        self.window_name = window_name
        self._status = TerminalStatus.IDLE
        self._allowed_tools: Optional[List[str]] = allowed_tools
        self._skill_prompt: Optional[str] = skill_prompt

    @property
    def status(self) -> TerminalStatus:
        """Get current provider status."""
        return self._status

    @property
    def paste_enter_count(self) -> int:
        """Number of Enter keys to send after pasting user input.

        After bracketed paste (``paste-buffer -p``), many TUIs (e.g.
        Claude Code) enter multi-line mode. The first Enter adds a
        newline; the second Enter on the empty line triggers submission.

        Default is 2 (double-Enter). Override to 1 for TUIs where single
        Enter submits after bracketed paste.
        """
        return 2

    @property
    def runtime_sidecar_reconnect_input(self) -> str | None:
        """Provider control input that reinitializes a stale MCP sidecar.

        Most providers do not expose a context-preserving reconnect command.
        Providers that do must return the exact input here so the terminal
        service can transport it through the normal capacity-fenced path.
        """
        return None

    @abstractmethod
    def initialize(self) -> bool:
        """Initialize the provider (e.g., start CLI tool, send setup commands).

        Returns:
            bool: True if initialization successful, False otherwise
        """
        pass

    @abstractmethod
    def get_status(self, tail_lines: Optional[int] = None) -> TerminalStatus:
        """Get current provider status by analyzing terminal output.

        Args:
            tail_lines: Number of lines to capture from terminal (default: provider-specific)

        Returns:
            TerminalStatus: Current status of the provider
        """
        pass

    @abstractmethod
    def get_idle_pattern_for_log(self) -> str:
        """Get pattern that indicates IDLE state in log file output.

        Used for quick detection in file watcher before calling full get_status().
        Should return a simple pattern that appears in the IDLE prompt.

        Returns:
            str: Pattern to search for in log file tail
        """
        pass

    @property
    def extraction_retries(self) -> int:
        """Number of extraction retries for transient TUI rendering issues.

        TUI-based providers (e.g. Gemini CLI's Ink renderer) may show
        notification spinners that temporarily obscure response text in
        the tmux capture buffer.  Override this to enable automatic retries
        with re-capture between attempts.  Default is 0 (no retries).
        """
        return 0

    @abstractmethod
    def extract_last_message_from_script(self, script_output: str) -> str:
        """Extract the last message from terminal script output.

        Args:
            script_output: Raw terminal output/script content

        Returns:
            str: Extracted last message from the provider
        """
        pass

    def get_durable_last_response(self) -> Optional[str]:
        """Return a provider-native completed response when one is available.

        The default provider contract has no independent response artifact and
        therefore falls back to bounded terminal-log extraction. Providers
        with an exact durable transcript may override this method.
        """
        return None

    @abstractmethod
    def exit_cli(self) -> str:
        """Get the command to exit the provider CLI.

        Returns:
            Command string to send to terminal for exiting
        """
        pass

    @abstractmethod
    def cleanup(self) -> None:
        """Clean up provider resources."""
        pass

    def mark_input_received(self) -> None:
        """Notify the provider that external input was sent to the terminal.

        Called by the terminal service after send_input() delivers a message.
        Providers can override this to adjust status detection behavior.
        For example, providers with initial prompts can use this to
        distinguish post-init idle (ready for first input) from
        post-task completed.
        """
        pass

    def extract_usage_observation(self, script_output: str) -> Optional[UsageObservation]:
        """Return provider-reported usage for a completed invocation, if available.

        This extension point is deliberately optional.  Providers must not
        estimate tokens from text and callers must treat parser failures as
        telemetry loss, never as a provider failure.
        """
        return None

    def get_turn_outcome(
        self,
        *,
        provider_session_id: Optional[str] = None,
        after_cursor: Optional[str] = None,
    ) -> Optional[ProviderTurnOutcome]:
        """Return a structured outcome after an exact transport boundary.

        This optional extension deliberately does not infer semantics from a
        rendered terminal status. Providers without a stronger protocol signal
        retain the historical ``None`` behavior.
        """
        return None

    def capture_turn_outcome_cursor(
        self, *, provider_session_id: Optional[str] = None
    ) -> Optional[str]:
        """Capture an opaque provider-event boundary before physical input.

        The matching cursor is persisted on the exact workflow turn. Providers
        without a safely comparable structured event stream return ``None``.
        """
        return None

    def turn_outcome_cursor_required(self) -> bool:
        """Return whether logical-turn transport must bind an outcome cursor."""
        return False

    def is_process_alive(self) -> bool:
        """Report provider-process liveness without inferring it from tmux.

        Providers that can observe their child process ending override this.
        The conservative default keeps unknown provider lifecycles resumable.
        """
        return True

    def _apply_skill_prompt(self, system_prompt: str) -> str:
        """Append skill catalog text to a system prompt if available.

        Args:
            system_prompt: The base system prompt string.

        Returns:
            The system prompt with skill catalog appended, or unchanged if
            no skill_prompt was provided.
        """
        if not self._skill_prompt:
            return system_prompt
        if system_prompt:
            return f"{system_prompt}\n\n{self._skill_prompt}"
        return self._skill_prompt

    def _update_status(self, status: TerminalStatus) -> None:
        """Update internal status."""
        self._status = status
