"""Terminal service with workflow functions.

This module provides high-level terminal management operations that orchestrate
multiple components (database, tmux, providers) to create a unified terminal
abstraction for CLI agents.

Key Responsibilities:
- Terminal lifecycle management (create, get, delete)
- Provider initialization and cleanup
- Tmux session/window management
- Terminal output capture and message extraction

Terminal Workflow:
1. create_terminal() → Creates tmux window, initializes provider, starts logging
2. send_input() → Sends user message to the agent via tmux
3. get_output() → Retrieves agent response from terminal history
4. delete_terminal() → Cleans up provider, database record, and logging
"""

import gzip
import hashlib
import hmac
import logging
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from cli_agent_orchestrator.clients.database import (
    AmbiguousTerminalIdentity,
    OwnerGrantRejected,
    UnreconciledTerminalAuthority,
    WorktreeWriterLeaseConflict,
    acquire_terminal_runtime_transport,
    bind_terminal_provider_resume_identity,
    bind_workflow_turn_provider_outcome_cursor,
    cancel_child_assignments_for_terminal,
    cancel_workflows_for_terminal,
    claim_terminal_runtime_exit,
)
from cli_agent_orchestrator.clients.database import create_terminal as db_create_terminal
from cli_agent_orchestrator.clients.database import (
    delete_exited_terminal as db_delete_exited_terminal,
)
from cli_agent_orchestrator.clients.database import delete_terminal as db_delete_terminal
from cli_agent_orchestrator.clients.database import (
    get_provider_execution_turn,
    get_terminal_execution_projection,
    get_terminal_metadata,
    get_terminal_workflow_projection,
    get_workflow_provider_reconnect_runtime_ready,
    get_workflow_turn_provider_outcome_cursor_bootstrap,
    list_all_terminals,
    mark_handoff_child_input_received,
    mark_terminal_runtime_exited,
    mark_terminal_runtime_exited_with_workflow_ids,
    mark_terminal_runtime_running,
    mark_workflow_provider_reconnect_launch_dispatched,
    persist_terminal_result_snapshot,
    promote_terminal_context_role_to_supervisor,
    reconcile_legacy_terminal_runtime_identity,
    record_workflow_provider_reconnect_output_boundary,
    release_provider_execution,
    release_terminal_runtime_operation,
    replace_starting_terminal_runtime_identity,
    requeue_settled_unadmitted_workflow_turn,
    reserve_workflow_turn_provider_outcome_cursor_bootstrap,
    resolve_session_lifetime,
    terminal_deletion_receipt_exists,
    terminal_requires_result_snapshot,
    terminal_runtime_operation_owned,
    update_last_active,
    validate_owner_launch_grant,
)
from cli_agent_orchestrator.clients.tmux import PaneTargetError, tmux_client
from cli_agent_orchestrator.constants import SESSION_PREFIX, TERMINAL_LOG_DIR
from cli_agent_orchestrator.models.inbox import OrchestrationType
from cli_agent_orchestrator.models.provider import ProviderTurnOutcome, ProviderType
from cli_agent_orchestrator.models.terminal import Terminal, TerminalLifecycle, TerminalStatus
from cli_agent_orchestrator.plugins import (
    PluginRegistry,
    PostCreateTerminalEvent,
    PostKillTerminalEvent,
    PostSendMessageEvent,
)
from cli_agent_orchestrator.providers.codex import (
    CodexStartupNoReadyError,
)
from cli_agent_orchestrator.providers.codex import ProviderError as CodexProviderError
from cli_agent_orchestrator.providers.manager import provider_manager
from cli_agent_orchestrator.services.plugin_dispatch import dispatch_plugin_event
from cli_agent_orchestrator.utils.agent_profiles import load_agent_profile
from cli_agent_orchestrator.utils.skills import build_skill_catalog
from cli_agent_orchestrator.utils.terminal import (
    generate_session_name,
    generate_terminal_id,
    generate_window_name,
    validate_session_name,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionRuntimeAuthorityProof:
    """Fail-closed proof that one durable lifetime owns its live tmux session."""

    proven: bool
    reason_code: str | None = None
    message: str | None = None
    inventory_uncertain: bool = False


def prove_live_session_runtime_authority(
    session_name: str,
    terminals: list[Dict[str, Any]],
    *,
    runtime_client: Any | None = None,
) -> SessionRuntimeAuthorityProof:
    """Match every active durable terminal to its exact process-backed pane.

    A tmux session name is reusable, so ``session_exists`` cannot by itself
    authorize adding a window or killing a session. Persisted pane/process
    generations bind the current runtime to the stable session lifetime.
    """
    client = runtime_client or tmux_client
    active = [
        terminal
        for terminal in terminals
        if terminal.get("runtime_lifecycle") != TerminalLifecycle.EXITED.value
    ]
    if not active:
        return SessionRuntimeAuthorityProof(
            False,
            "SESSION_HISTORY_INELIGIBLE",
            "This session lifetime has no live runtime owner",
        )
    exists = client.session_exists(session_name)
    if exists is None:
        return SessionRuntimeAuthorityProof(
            False,
            "SESSION_RUNTIME_INVENTORY_UNCERTAIN",
            "ThreadCells could not verify the live session runtime",
            inventory_uncertain=True,
        )
    if exists is False:
        return SessionRuntimeAuthorityProof(
            False,
            "SESSION_RUNTIME_MISSING",
            "The durable session is not backed by a live runtime",
        )
    try:
        windows = client.get_session_windows(session_name)
    except Exception:
        windows = None
    if not isinstance(windows, list):
        return SessionRuntimeAuthorityProof(
            False,
            "SESSION_RUNTIME_INVENTORY_UNCERTAIN",
            "ThreadCells could not inventory every window in the live session",
            inventory_uncertain=True,
        )
    observed_windows: list[str] = []
    for window in windows:
        if not isinstance(window, dict) or not str(window.get("name") or ""):
            return SessionRuntimeAuthorityProof(
                False,
                "SESSION_RUNTIME_INVENTORY_UNCERTAIN",
                "ThreadCells received an incomplete live session window inventory",
                inventory_uncertain=True,
            )
        observed_windows.append(str(window["name"]))
    if not observed_windows:
        return SessionRuntimeAuthorityProof(
            False,
            "SESSION_RUNTIME_INVENTORY_UNCERTAIN",
            "The live session has no provable runtime windows",
            inventory_uncertain=True,
        )
    if len(observed_windows) != len(set(observed_windows)):
        return SessionRuntimeAuthorityProof(
            False,
            "SESSION_RUNTIME_AUTHORITY_DIVERGED",
            "The live session contains duplicate window identities",
        )
    durable_by_window: dict[str, Dict[str, Any]] = {}
    for terminal in terminals:
        window_name = str(terminal.get("tmux_window") or "")
        if not window_name or window_name in durable_by_window:
            return SessionRuntimeAuthorityProof(
                False,
                "SESSION_RUNTIME_AUTHORITY_DIVERGED",
                "Durable session window authority is incomplete or duplicated",
            )
        durable_by_window[window_name] = terminal
    observed_set = set(observed_windows)
    if not {str(terminal.get("tmux_window") or "") for terminal in active}.issubset(observed_set):
        return SessionRuntimeAuthorityProof(
            False,
            "SESSION_RUNTIME_AUTHORITY_DIVERGED",
            "A live durable terminal is missing from the session runtime",
        )
    if not observed_set.issubset(durable_by_window):
        return SessionRuntimeAuthorityProof(
            False,
            "SESSION_RUNTIME_AUTHORITY_DIVERGED",
            "The live session contains a window without durable lifetime authority",
        )
    for window_name in observed_windows:
        terminal = durable_by_window[window_name]
        if str(terminal.get("tmux_session") or "") != session_name:
            return SessionRuntimeAuthorityProof(
                False,
                "SESSION_RUNTIME_AUTHORITY_DIVERGED",
                "Durable terminal ownership no longer matches the session",
            )
        try:
            target = client.exact_runtime_target(session_name, window_name)
        except PaneTargetError as exc:
            uncertain = exc.reason_code == "EXIT_INVENTORY_UNCERTAIN"
            return SessionRuntimeAuthorityProof(
                False,
                (
                    "SESSION_RUNTIME_INVENTORY_UNCERTAIN"
                    if uncertain
                    else "SESSION_RUNTIME_AUTHORITY_DIVERGED"
                ),
                "ThreadCells could not prove the durable session's exact runtime authority",
                inventory_uncertain=uncertain,
            )
        except Exception:
            return SessionRuntimeAuthorityProof(
                False,
                "SESSION_RUNTIME_INVENTORY_UNCERTAIN",
                "ThreadCells could not inventory the durable session's exact runtime authority",
                inventory_uncertain=True,
            )
        durable_identity = (
            terminal.get("runtime_pane_id"),
            terminal.get("runtime_pane_pid"),
            terminal.get("runtime_generation"),
            terminal.get("runtime_process_start_ticks"),
        )
        observed_identity = (
            target.pane_id,
            target.pane_pid,
            target.runtime_generation,
            target.process_start_ticks,
        )
        origin = terminal.get("runtime_generation_origin")
        if (
            target.terminal_id != terminal.get("id")
            or any(value in (None, "") for value in durable_identity)
            or durable_identity != observed_identity
            or origin not in {"launch", "reconciled"}
            or ((origin == "launch") != bool(target.generation_inherited))
        ):
            return SessionRuntimeAuthorityProof(
                False,
                "SESSION_RUNTIME_AUTHORITY_DIVERGED",
                "The live pane identity does not match durable session authority",
            )
    return SessionRuntimeAuthorityProof(True)


def _capture_created_runtime_identity(
    session_name: str, window_name: str, terminal_id: str, runtime_generation: str
):
    """Capture and validate the process identity created for one launch."""
    target = tmux_client.exact_runtime_target(session_name, window_name)
    if (
        target.terminal_id != terminal_id
        or target.runtime_generation != runtime_generation
        or not target.generation_inherited
    ):
        raise RuntimeError("Created pane did not retain its launch identity")
    return target


def reconcile_legacy_runtime_identities() -> int:
    """Add a restart-safe process fence to intact pre-generation runtimes."""
    reconciled = 0
    for metadata in list_all_terminals():
        if metadata.get("runtime_lifecycle") not in {
            TerminalLifecycle.STARTING.value,
            TerminalLifecycle.RUNNING.value,
        }:
            continue
        if metadata.get("runtime_generation"):
            continue
        legacy_fields = (
            metadata.get("runtime_pane_id"),
            metadata.get("runtime_pane_pid"),
            metadata.get("runtime_process_start_ticks"),
        )
        if any(value not in (None, "") for value in legacy_fields):
            logger.warning(
                "Terminal %s has a partial legacy runtime identity; preserving it",
                metadata.get("id"),
            )
            continue
        generation = str(uuid.uuid4())
        try:
            target = tmux_client.bind_legacy_runtime_generation(
                str(metadata["tmux_session"]),
                str(metadata["tmux_window"]),
                str(metadata["id"]),
                generation,
            )
        except Exception:
            # Missing historical panes are normal; uncertainty is preserved.
            continue
        if reconcile_legacy_terminal_runtime_identity(
            str(metadata["id"]),
            pane_id=target.pane_id,
            pane_pid=target.pane_pid,
            runtime_generation=target.runtime_generation,
            process_start_ticks=target.process_start_ticks,
        ):
            reconciled += 1
    return reconciled


def _wake_queued_provider_execution(registry: PluginRegistry | None = None) -> None:
    """Best-effort fast wake; the durable queue is also replayed after restart."""
    from cli_agent_orchestrator.services.inbox_service import wake_provider_execution_queue

    wake_provider_execution_queue(registry)


class OutputMode(str, Enum):
    """Output mode for terminal history retrieval.

    FULL: Returns complete terminal output (scrollback buffer)
    LAST: Returns only the last agent response (extracted by provider)
    """

    FULL = "full"
    LAST = "last"


class TerminalOutputUnavailable(Exception):
    """Durable terminal history was intentionally cleaned or is absent."""


def _consume_terminal_control_string(value: str, index: int) -> int:
    """Return the first index after an OSC/DCS-style control string."""
    while index < len(value):
        if value[index] in {"\x07", "\x9c"}:
            return index + 1
        if value[index] == "\x1b" and index + 1 < len(value) and value[index + 1] == "\\":
            return index + 2
        index += 1
    return len(value)


def _consume_terminal_csi(value: str, index: int) -> int:
    """Consume one CSI sequence, including a safe incomplete suffix."""
    while index < len(value):
        if value[index] in {"\n", "\r", "\t"}:
            return index
        codepoint = ord(value[index])
        if 0x40 <= codepoint <= 0x7E:
            return index + 1
        if 0x20 <= codepoint <= 0x3F or codepoint < 0x20 or codepoint == 0x7F:
            index += 1
            continue
        # Preserve non-ASCII/plain text after a malformed introducer while
        # still removing the control bytes already consumed.
        return index
    return len(value)


def _sanitize_human_terminal_output(value: str) -> str:
    """Remove terminal effects while preserving readable Unicode text.

    Raw tmux history and durable logs intentionally retain controls for
    provider parsing and diagnostics.  This renderer is applied only to the
    human-facing FULL output boundary.
    """
    rendered: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        codepoint = ord(character)

        if character == "\x1b":
            if index + 1 >= len(value):
                break
            introducer = value[index + 1]
            if introducer == "[":
                index = _consume_terminal_csi(value, index + 2)
                continue
            if introducer in "]PX^_":
                index = _consume_terminal_control_string(value, index + 2)
                continue
            if introducer in "\n\r\t":
                index += 1
                continue
            introducer_codepoint = ord(introducer)
            if 0x20 <= introducer_codepoint <= 0x2F:
                index += 2
                while index < len(value) and 0x20 <= ord(value[index]) <= 0x2F:
                    index += 1
                if index < len(value) and 0x30 <= ord(value[index]) <= 0x7E:
                    index += 1
                continue
            if 0x30 <= introducer_codepoint <= 0x7E:
                index += 2
                continue
            # Unknown/non-ASCII text is not part of a valid ESC sequence.
            index += 1
            continue

        if character == "\x9b":
            index = _consume_terminal_csi(value, index + 1)
            continue
        if character in {"\x90", "\x98", "\x9d", "\x9e", "\x9f"}:
            index = _consume_terminal_control_string(value, index + 1)
            continue
        if 0x80 <= codepoint <= 0x9F:
            index += 1
            continue

        if character == "\r":
            if index + 1 < len(value) and value[index + 1] == "\n":
                if not rendered or rendered[-1] != "\n":
                    rendered.append("\n")
                index += 2
                continue
            if not rendered or rendered[-1] != "\n":
                rendered.append("\n")
            index += 1
            continue
        if character == "\b":
            if rendered and rendered[-1] not in {"\n", "\t"}:
                rendered.pop()
            index += 1
            continue
        if character in {"\n", "\t"}:
            rendered.append(character)
            index += 1
            continue
        if codepoint < 0x20 or codepoint == 0x7F:
            index += 1
            continue

        rendered.append(character)
        index += 1
    return "".join(rendered)


# Providers that accept a runtime skill_prompt kwarg and append it to the
# system prompt at launch time.  Other providers deliver skills differently:
# Kiro (skill:// resources) and OpenCode (OPENCODE_CONFIG_DIR/skills symlink)
# discover skills natively; Q and Copilot receive a baked catalog at install
# time.
RUNTIME_SKILL_PROMPT_PROVIDERS = {
    ProviderType.CLAUDE_CODE.value,
    ProviderType.CODEX.value,
    ProviderType.GEMINI_CLI.value,
    ProviderType.KIMI_CLI.value,
}

SHELL_COMMANDS = {"bash", "sh", "dash", "zsh", "fish"}
EXIT_CONFIRMATION_TIMEOUT_SECONDS = 5.0
EXIT_CONFIRMATION_POLL_SECONDS = 0.1


@dataclass(frozen=True)
class _LaunchCleanupOutcome:
    """Cleanup evidence attached to a failed launch for its outer boundary."""

    session_name: str | None
    window_name: str | None
    target_attempted: bool
    death_confirmed: bool


@dataclass(frozen=True)
class ExitTerminalResult:
    """Authoritative graceful-exit outcome returned to API and automation callers."""

    success: bool
    lifecycle: str
    outcome: str
    message: str
    command_delivered: bool

    def __bool__(self) -> bool:
        return self.success


class ExitAuthorityError(RuntimeError):
    """Exact provider/tmux delivery authority could not be established."""

    def __init__(self, reason_code: str, message: str, *, inventory_uncertain: bool = False):
        super().__init__(message)
        self.reason_code = reason_code
        self.inventory_uncertain = inventory_uncertain


class TerminalDeletionError(RuntimeError):
    """Terminal deletion lacked exact, exited runtime authority."""

    def __init__(self, reason_code: str, message: str, *, inventory_uncertain: bool = False):
        super().__init__(message)
        self.reason_code = reason_code
        self.inventory_uncertain = inventory_uncertain


class ManagedWorktreeCleanupError(RuntimeError):
    """A managed worktree must be retained under its durable authority."""

    def __init__(self, reason_code: str):
        super().__init__("Managed worktree cleanup was not proven safe " f"({reason_code})")
        self.reason_code = reason_code


def is_terminal_quiescent(status: str | None) -> bool:
    """Return whether provider state is safe behind durable workflow fences.

    Providers may expose their post-result prompt as either ``completed`` or
    ``idle``.  Neither state alone authorizes lifecycle mutation; callers must
    still hold the durable result/workflow/descendant retirement fences.
    """
    return status in {TerminalStatus.IDLE.value, TerminalStatus.COMPLETED.value}


_PROVIDER_CLASS_NAMES = {
    ProviderType.Q_CLI.value: "QCliProvider",
    ProviderType.KIRO_CLI.value: "KiroCliProvider",
    ProviderType.CLAUDE_CODE.value: "ClaudeCodeProvider",
    ProviderType.CODEX.value: "CodexProvider",
    ProviderType.COPILOT_CLI.value: "CopilotCliProvider",
    ProviderType.GEMINI_CLI.value: "GeminiCliProvider",
    ProviderType.KIMI_CLI.value: "KimiCliProvider",
    ProviderType.OPENCODE_CLI.value: "OpenCodeCliProvider",
}


def _attach_launch_cleanup_outcome(
    error: Exception,
    *,
    session_name: str | None,
    window_name: str | None,
    target_attempted: bool,
    death_confirmed: bool,
) -> None:
    """Preserve exact target cleanup evidence without changing the original error."""
    try:
        setattr(
            error,
            "_cao_launch_cleanup_outcome",
            _LaunchCleanupOutcome(
                session_name=session_name,
                window_name=window_name,
                target_attempted=target_attempted,
                death_confirmed=death_confirmed,
            ),
        )
    except Exception:
        # The launch failure is authoritative; missing outcome evidence must
        # remain fail-closed at the managed-worktree boundary.
        pass


def _runtime_death_observation(metadata: Dict, provider=None) -> bool | None:
    """Return True only for positive provider/tmux/process death evidence."""
    exists = tmux_client.window_exists(metadata["tmux_session"], metadata["tmux_window"])
    if exists is False:
        return True
    if provider is not None:
        try:
            if provider.is_process_alive() is False:
                return True
        except Exception:
            return None
    if exists is None:
        return None

    # During the create transaction the pane is intentionally a shell before
    # provider startup.  Never mistake that launch boundary for provider death.
    command = tmux_client.get_pane_current_command(
        metadata["tmux_session"], metadata["tmux_window"]
    )
    if not isinstance(command, str):
        command = None
    if metadata.get("runtime_lifecycle") != "starting" and (
        command == "" or command in SHELL_COMMANDS
    ):
        return True
    if command is not None:
        return False
    return None


def reconcile_terminal_runtime(terminal_id: str, provider=None) -> bool | None:
    """Persist a positive runtime-death observation without deleting history."""
    metadata = get_terminal_metadata(terminal_id)
    if not metadata:
        return None
    if metadata.get("runtime_lifecycle") == "exited":
        _retire_exited_terminal_runtime(metadata)
        return True
    if metadata.get("runtime_operation_kind") == "reconnect":
        # The exact-session recovery intentionally crosses a shell gap.  That
        # shell is not provider-death evidence while durable reconnect state
        # owns the pane, including after a service crash and lease expiry.
        return False
    observation = _runtime_death_observation(metadata, provider)
    if observation is not True:
        return observation
    exited, cancelled_workflow_ids = mark_terminal_runtime_exited_with_workflow_ids(terminal_id)
    if not exited:
        return None
    cancel_child_assignments_for_terminal(terminal_id)
    provider_manager.cleanup_provider(terminal_id)
    _wake_queued_provider_execution()
    if cancelled_workflow_ids:
        try:
            from cli_agent_orchestrator.services.telegram_notification_service import (
                dispatch_workflow_notification,
            )

            dispatch_workflow_notification(
                terminal_id,
                "failed",
                workflow_id=max(cancelled_workflow_ids),
            )
        except Exception:
            # Never interpolate exceptions that may contain a bot-token URL.
            logger.warning("Telegram terminal-failure notification failed safely")
    _retire_exited_terminal_runtime(metadata)
    return True


def _retire_exited_terminal_runtime(
    metadata: dict, *, proc_root: Path = Path("/proc")
) -> bool | None:
    """Retire an exited runtime without deleting its durable terminal history.

    This is deliberately fail-closed. A reusable tmux name is never sufficient:
    the exact pane's inherited terminal identity must match the exited DB row.
    """
    terminal_id = str(metadata.get("id") or "")
    if not terminal_id:
        return None
    current = get_terminal_metadata(terminal_id)
    if not current or current.get("runtime_lifecycle") != TerminalLifecycle.EXITED.value:
        return None
    try:
        target = tmux_client.exact_runtime_target(
            str(current["tmux_session"]), str(current["tmux_window"]), proc_root=proc_root
        )
    except PaneTargetError as exc:
        if exc.reason_code in {
            "EXIT_SESSION_MISSING",
            "EXIT_WINDOW_MISSING",
            "EXIT_PANE_MISSING",
            "EXIT_PANE_DEAD",
        }:
            return True
        logger.warning("Exited runtime %s was preserved: %s", terminal_id, exc.reason_code)
        return None
    except Exception:
        logger.warning("Exited runtime %s inventory failed safely", terminal_id)
        return None
    if target.terminal_id != terminal_id:
        logger.warning("Exited runtime %s identity mismatch; pane preserved", terminal_id)
        return None
    durable_identity = (
        current.get("runtime_pane_id"),
        current.get("runtime_pane_pid"),
        current.get("runtime_generation"),
        current.get("runtime_process_start_ticks"),
    )
    observed_identity = (
        target.pane_id,
        target.pane_pid,
        target.runtime_generation,
        target.process_start_ticks,
    )
    if any(value in (None, "") for value in durable_identity):
        logger.warning(
            "Exited runtime %s has no persisted launch identity; pane preserved", terminal_id
        )
        return None
    origin = current.get("runtime_generation_origin")
    if origin not in {"launch", "reconciled"} or (
        (origin == "launch") != bool(target.generation_inherited)
    ):
        logger.warning(
            "Exited runtime %s generation provenance mismatch; pane preserved", terminal_id
        )
        return None
    if durable_identity != observed_identity:
        logger.warning("Exited runtime %s launch generation mismatch; pane preserved", terminal_id)
        return None
    if target.current_command not in SHELL_COMMANDS:
        logger.warning("Exited runtime %s is not at a shell; pane preserved", terminal_id)
        return None
    if not tmux_client.retire_runtime_pane(target, proc_root=proc_root):
        logger.warning("Exited runtime %s could not be retired", terminal_id)
        return False
    logger.info(
        "Retired exited runtime pane for terminal %s; durable history retained", terminal_id
    )
    return True


def retire_exited_terminal_runtime(
    terminal_id: str, *, proc_root: Path = Path("/proc")
) -> bool | None:
    """Public idempotent retirement entry point for Housekeeping/recovery."""
    metadata = get_terminal_metadata(terminal_id)
    if not metadata:
        return None
    return _retire_exited_terminal_runtime(metadata, proc_root=proc_root)


def _canonical_worktree(working_directory: Optional[str]) -> str:
    """Canonicalize a directory to its containing Git worktree when present."""
    directory = Path(os.path.expanduser(working_directory or os.getcwd())).resolve(strict=True)
    if not directory.is_dir():
        raise ValueError(f"Working directory does not exist: {working_directory}")
    for candidate in (directory, *directory.parents):
        if (candidate / ".git").exists():
            return str(candidate)
    return str(directory)


def _write_enabled_lane(
    provider: str, agent_profile: str, allowed_tools: Optional[list[str]]
) -> bool:
    """Classify all provider lanes as writer-capable until isolated proof exists.

    CAO does not yet have an immutable, enforced, and tested isolated-reader
    contract for any provider.  Provider-declared tool subsets (including
    Claude's) therefore cannot release the durable writer lease.
    """
    return True


def _resolve_context_role(*, new_session: bool, context_role: str | None) -> str:
    """Resolve residency from launch topology, never model/profile semantics.

    A session's initial terminal is its resident conductor (ordinary
    supervisor or owner-executor). Every additional/delegated terminal is a
    work context unless a trusted internal caller supplies an explicit role.
    """
    if context_role is not None:
        if context_role not in {"supervisor", "work"}:
            raise ValueError("context_role must be supervisor or work")
        return context_role
    return "supervisor" if new_session else "work"


def reconcile_terminal_context_roles(*, dry_run: bool = False) -> int:
    """Repair live legacy roles from durable session/parentage topology."""
    from cli_agent_orchestrator.clients.database import (
        reconcile_terminal_context_roles_by_topology,
    )

    return reconcile_terminal_context_roles_by_topology(dry_run=dry_run)


def _active_worktree_lanes() -> list[tuple[str, bool]] | None:
    """Read immutable terminal ownership metadata; ``None`` means uncertain."""
    lanes: list[tuple[str, bool]] = []
    try:
        terminals = list_all_terminals()
    except Exception:
        return None
    for terminal in terminals:
        worktree = terminal.get("launch_worktree")
        write_enabled = terminal.get("write_enabled")
        if (
            not isinstance(worktree, str)
            or not worktree
            or not Path(worktree).is_absolute()
            or not isinstance(write_enabled, bool)
        ):
            return None
        lanes.append((worktree, write_enabled))
    return lanes


def _create_terminal_after_admission(
    provider: str,
    agent_profile: str,
    session_name: Optional[str] = None,
    new_session: bool = False,
    working_directory: Optional[str] = None,
    allowed_tools: Optional[list[str]] = None,
    registry: PluginRegistry | None = None,
    launch_worktree: str | None = None,
    write_enabled: bool | None = None,
    context_role: str = "work",
    managed_worktree_kind: str | None = None,
    managed_worktree_source: str | None = None,
    managed_worktree_branch: str | None = None,
    managed_worktree_commit: str | None = None,
    project_context: Dict[str, str] | None = None,
    terminal_id_override: str | None = None,
    privileged_launch: bool = False,
    structured_owner_authorized: bool | None = None,
    owner_grant_token: str | None = None,
    owner_grant_launch_id: str | None = None,
    owner_grant_requested_session_name: str | None = None,
    owner_grant_scope: Dict[str, object] | None = None,
    profile_revision_id: str | None = None,
    provider_config_revision_id: str | None = None,
    launch_snapshot: Dict[str, Any] | None = None,
    provider_configuration: Dict[str, Any] | None = None,
    resolved_profile: Any | None = None,
    owner_grant_canonical_worktree: str | None = None,
    session_lifetime_id: str | None = None,
) -> Terminal:
    """Create a new terminal with an initialized CLI agent.

    This function orchestrates the complete terminal creation workflow:
    1. Generate unique terminal ID and window name
    2. Create tmux session/window (new or existing)
    3. Save terminal metadata to database
    4. Initialize the CLI provider (starts the agent)
    5. Set up terminal logging via tmux pipe-pane

    Args:
        provider: Provider type string (e.g., "kiro_cli", "claude_code")
        agent_profile: Name of the agent profile to use
        session_name: Optional custom session name. If not provided, auto-generated.
        new_session: If True, creates a new tmux session. If False, adds to existing.
        working_directory: Optional working directory for the terminal shell

    Returns:
        Terminal object with all metadata populated

    Raises:
        ValueError: If session already exists (new_session=True) or not found (new_session=False)
        TimeoutError: If provider initialization times out
    """
    session_created = False  # tracks whether THIS call created the tmux session
    tmux_target_attempted = False
    metadata_persisted = False
    persisted_metadata: Dict | None = None
    terminal_id: str | None = None
    window_name: str | None = None
    runtime_generation = str(uuid.uuid4())
    runtime_target = None
    terminal_auth_token = secrets.token_urlsafe(32)
    terminal_auth_token_sha256 = hashlib.sha256(
        terminal_auth_token.encode("utf-8", "strict")
    ).hexdigest()
    session_lifetime_id = str(uuid.uuid4()) if new_session else session_lifetime_id
    if structured_owner_authorized is None:
        structured_owner_authorized = privileged_launch
    try:
        # Step 1: Generate unique identifiers
        terminal_id = terminal_id_override or generate_terminal_id()

        if not session_name:
            session_name = generate_session_name()
        elif new_session:
            session_name = validate_session_name(session_name)

        window_name = generate_window_name(agent_profile)

        # Step 2: Create tmux session or window
        if new_session:
            # Ensure session name has the CAO prefix for identification
            if not session_name.startswith(SESSION_PREFIX):
                session_name = f"{SESSION_PREFIX}{session_name}"

            # Prevent duplicate sessions
            session_observation = tmux_client.session_exists(session_name)
            if session_observation is None:
                raise RuntimeError(f"Could not determine whether session '{session_name}' exists")
            if session_observation is True:
                raise ValueError(f"Session '{session_name}' already exists")

            # Create new tmux session with initial window
            tmux_target_attempted = True
            created_window_name = tmux_client.create_session(
                session_name,
                window_name,
                terminal_id,
                working_directory,
                terminal_auth_token,
                runtime_generation,
            )
            if isinstance(created_window_name, str) and created_window_name:
                window_name = created_window_name
            session_created = True  # only set after successful creation
        else:
            # Add window to existing session
            session_observation = tmux_client.session_exists(session_name)
            if session_observation is None:
                raise RuntimeError(f"Could not determine whether session '{session_name}' exists")
            if session_observation is False:
                raise ValueError(f"Session '{session_name}' not found")
            tmux_target_attempted = True
            window_name = tmux_client.create_window(
                session_name,
                window_name,
                terminal_id,
                working_directory,
                terminal_auth_token,
                runtime_generation,
            )

        runtime_target = _capture_created_runtime_identity(
            session_name, window_name, terminal_id, runtime_generation
        )

        if not new_session and session_lifetime_id is not None:
            # Creating the window is an external tmux mutation. Re-prove the
            # complete session after that mutation and before attaching the
            # new row to a stable lifetime, so a kill/recreate race cannot
            # contaminate either the old or replacement session identity.
            durable_session = resolve_session_lifetime(session_lifetime_id)
            if (
                durable_session is None
                or durable_session["deleted"]
                or durable_session["session_id"] != session_lifetime_id
                or durable_session["session_name"] != session_name
            ):
                from cli_agent_orchestrator.services.operations_service import AdmissionDenied

                raise AdmissionDenied("SESSION_IDENTITY_CHANGED", {})
            prospective_terminal = {
                "id": terminal_id,
                "tmux_session": session_name,
                "session_id": session_lifetime_id,
                "tmux_window": window_name,
                "runtime_lifecycle": TerminalLifecycle.STARTING.value,
                "runtime_pane_id": runtime_target.pane_id,
                "runtime_pane_pid": runtime_target.pane_pid,
                "runtime_generation": runtime_target.runtime_generation,
                "runtime_generation_origin": "launch",
                "runtime_process_start_ticks": runtime_target.process_start_ticks,
            }
            runtime_proof = prove_live_session_runtime_authority(
                session_name,
                [*durable_session["terminals"], prospective_terminal],
                runtime_client=tmux_client,
            )
            if not runtime_proof.proven:
                from cli_agent_orchestrator.services.operations_service import AdmissionDenied

                raise AdmissionDenied(
                    runtime_proof.reason_code or "SESSION_IDENTITY_CHANGED",
                    {"inventory_uncertain": runtime_proof.inventory_uncertain},
                )

        # Step 3: Persist terminal metadata to database
        try:
            persisted_metadata = db_create_terminal(
                terminal_id,
                session_name,
                window_name,
                provider,
                agent_profile,
                allowed_tools,
                terminal_auth_token_sha256,
                launch_worktree=launch_worktree,
                write_enabled=write_enabled,
                context_role=context_role,
                managed_worktree_kind=managed_worktree_kind,
                managed_worktree_source=managed_worktree_source,
                managed_worktree_branch=managed_worktree_branch,
                managed_worktree_commit=managed_worktree_commit,
                project_id=project_context.get("id") if project_context else None,
                project_name=project_context.get("name") if project_context else None,
                project_path=project_context.get("path") if project_context else None,
                project_description=project_context.get("description") if project_context else None,
                privileged_launch=privileged_launch,
                owner_grant_token=owner_grant_token,
                owner_grant_launch_id=owner_grant_launch_id,
                owner_grant_requested_session_name=owner_grant_requested_session_name,
                owner_grant_scope=owner_grant_scope,
                profile_revision_id=profile_revision_id,
                provider_config_revision_id=provider_config_revision_id,
                launch_snapshot=launch_snapshot,
                owner_grant_canonical_worktree=owner_grant_canonical_worktree,
                session_lifetime_id=session_lifetime_id,
                runtime_pane_id=runtime_target.pane_id,
                runtime_pane_pid=runtime_target.pane_pid,
                runtime_generation=runtime_target.runtime_generation,
                runtime_process_start_ticks=runtime_target.process_start_ticks,
            )
            metadata_persisted = True
        except (
            OwnerGrantRejected,
            UnreconciledTerminalAuthority,
            WorktreeWriterLeaseConflict,
        ) as exc:
            from cli_agent_orchestrator.services.operations_service import AdmissionDenied

            if isinstance(exc, OwnerGrantRejected):
                raise AdmissionDenied(exc.reason_code, {}) from exc
            if isinstance(exc, UnreconciledTerminalAuthority):
                raise AdmissionDenied(
                    "WORKTREE_AUTHORITY_UNRECONCILED",
                    {"terminal_id": exc.terminal_id},
                ) from exc
            raise AdmissionDenied(
                "WORKTREE_WRITER_LEASE_HELD",
                {"canonical_worktree": exc.canonical_worktree},
            ) from exc

        # Step 3b: Load the profile once for allowed tool resolution before
        # provider initialization. The skill catalog is computed only for
        # providers that consume it at launch time (see RUNTIME_SKILL_PROMPT_PROVIDERS).
        if resolved_profile is not None:
            profile = resolved_profile
        else:
            try:
                profile = load_agent_profile(agent_profile)
            except FileNotFoundError:
                profile = None
        project_instructions = None
        if project_context:
            description = project_context.get("description")
            project_instructions = (
                "Project Context\n"
                f"Project: {project_context['name']}\n"
                f"Path: {project_context['path']}"
                + (f"\nDescription: {description}" if description else "")
                + "\nUse this project context for the work you perform in this terminal."
            )
        prompt_parts = []
        if provider in RUNTIME_SKILL_PROMPT_PROVIDERS:
            prompt_parts.append(build_skill_catalog())
        if project_instructions:
            prompt_parts.append(project_instructions)
        skill_prompt = "\n\n".join(part for part in prompt_parts if part)
        if not skill_prompt and provider not in RUNTIME_SKILL_PROMPT_PROVIDERS:
            skill_prompt = None

        # Step 3c: Resolve allowed_tools from profile if not explicitly provided
        if allowed_tools is None and profile is not None:
            from cli_agent_orchestrator.utils.tool_mapping import resolve_allowed_tools

            mcp_server_names = list(profile.mcpServers.keys()) if profile.mcpServers else None
            allowed_tools = resolve_allowed_tools(
                profile.allowedTools, profile.role, mcp_server_names
            )

        # Start capture before provider initialization.  A Codex process can
        # fail before it reaches an idle prompt; retaining that startup stream
        # is required to diagnose the failure after its tmux attempt is cleaned.
        log_path = TERMINAL_LOG_DIR / f"{terminal_id}.log"
        log_path.touch()
        tmux_client.pipe_pane(session_name, window_name, str(log_path))

        # Step 4: Create and initialize the CLI provider
        # This starts the agent (e.g., runs "kiro-cli chat --agent developer").
        # Only runtime-prompt providers (Claude Code, Codex, Gemini, Kimi) receive
        # the skill catalog here; Kiro (skill:// resources) and OpenCode
        # (OPENCODE_CONFIG_DIR/skills symlink) discover skills natively; Q and
        # Copilot get the catalog baked at install time.
        def start_provider():
            instance = provider_manager.create_provider(
                provider,
                terminal_id,
                session_name,
                window_name,
                agent_profile,
                allowed_tools,
                skill_prompt=skill_prompt,
                model=profile.model if profile else None,
                provider_configuration=provider_configuration,
                resolved_profile=profile,
                structured_owner_authorized=structured_owner_authorized,
            )
            instance.initialize()
            return instance

        try:
            provider_instance = start_provider()
        except CodexStartupNoReadyError as first_error:
            if provider != ProviderType.CODEX.value:
                raise
            logger.warning(
                "Codex reached no ready state for terminal %s; cleaning attempt and retrying once: %s",
                terminal_id,
                first_error,
            )
            provider_manager.cleanup_provider(terminal_id)
            runtime_generation = str(uuid.uuid4())
            if session_created:
                if not tmux_client.kill_session(session_name):
                    raise RuntimeError("Failed to clean tmux session before Codex startup retry")
                window_name = tmux_client.create_session(
                    session_name,
                    window_name,
                    terminal_id,
                    working_directory,
                    terminal_auth_token,
                    runtime_generation,
                )
            else:
                if not tmux_client.kill_window(session_name, window_name):
                    raise RuntimeError("Failed to clean tmux window before Codex startup retry")
                window_name = tmux_client.create_window(
                    session_name,
                    window_name,
                    terminal_id,
                    working_directory,
                    terminal_auth_token,
                    runtime_generation,
                )
            runtime_target = _capture_created_runtime_identity(
                session_name, window_name, terminal_id, runtime_generation
            )
            if not replace_starting_terminal_runtime_identity(
                terminal_id,
                pane_id=runtime_target.pane_id,
                pane_pid=runtime_target.pane_pid,
                runtime_generation=runtime_target.runtime_generation,
                process_start_ticks=runtime_target.process_start_ticks,
            ):
                raise RuntimeError("Could not publish the retry pane launch identity")
            tmux_client.pipe_pane(session_name, window_name, str(log_path))
            try:
                provider_instance = start_provider()
            except Exception as second_error:
                raise RuntimeError(
                    "Codex startup retry failed; startup output retained at "
                    f"{log_path}: {second_error}"
                ) from second_error

        lifecycle_published = mark_terminal_runtime_running(terminal_id)
        if isinstance(persisted_metadata, dict) and not lifecycle_published:
            raise RuntimeError(f"Could not publish running lifecycle for {terminal_id}")

        # Build and return the Terminal object
        terminal = Terminal(
            id=terminal_id,
            name=window_name,
            provider=provider,
            session_name=session_name,
            session_id=session_lifetime_id or f"legacy:{session_name}",
            agent_profile=agent_profile,
            status=TerminalStatus.IDLE,
            provider_outcome_code=None,
            provider_outcome_detail=None,
            last_active=datetime.now(),
        )

        logger.info(
            f"Created terminal: {terminal_id} in session: {session_name} (new_session={new_session})"
        )
        dispatch_plugin_event(
            registry,
            "post_create_terminal",
            PostCreateTerminalEvent(
                session_id=terminal.session_name,
                terminal_id=terminal.id,
                agent_name=terminal.agent_profile,
                provider=provider,
            ),
        )
        return terminal

    except Exception as e:
        # Cleanup on failure: clean up provider resources, persisted metadata,
        # and the tmux session. Metadata is created before provider startup, so
        # leaving it behind after a startup failure makes later terminal reads
        # fail against a session that no longer exists.
        logger.error(f"Failed to create terminal: {e}")
        if terminal_id is not None:
            try:
                provider_manager.cleanup_provider(terminal_id)
            except Exception:
                pass  # Ignore cleanup errors
        death_confirmed = False
        if tmux_target_attempted and session_name and window_name:
            try:
                if new_session:
                    death_confirmed = bool(tmux_client.kill_session(session_name))
                    # A session creation call can create its session and then
                    # raise.  Its initial window disappearing does not prove
                    # the session died: another window/process may remain.
                    # Only the exact session inventory may establish death
                    # after an unsuccessful kill attempt.
                    if not death_confirmed:
                        death_confirmed = tmux_client.session_exists(session_name) is False
                else:
                    death_confirmed = bool(tmux_client.kill_window(session_name, window_name))
                    if not death_confirmed:
                        death_confirmed = (
                            tmux_client.window_exists(session_name, window_name) is False
                        )
            except Exception:
                death_confirmed = False
        if metadata_persisted and terminal_id is not None and death_confirmed:
            try:
                if managed_worktree_kind is not None:
                    # The outer launch boundary owns managed-worktree cleanup.
                    # Preserve its complete durable identity until that cleanup
                    # succeeds; an exited row is also safe for later recovery.
                    mark_terminal_runtime_exited(terminal_id)
                else:
                    db_delete_terminal(terminal_id)
            except Exception:
                pass  # A retained lease is safer than an uncertain release.
        _attach_launch_cleanup_outcome(
            e,
            session_name=session_name,
            window_name=window_name,
            target_attempted=tmux_target_attempted,
            death_confirmed=death_confirmed,
        )
        raise


def create_terminal(
    provider: str,
    agent_profile: str,
    session_name: Optional[str] = None,
    new_session: bool = False,
    working_directory: Optional[str] = None,
    allowed_tools: Optional[list[str]] = None,
    registry: PluginRegistry | None = None,
    context_role: str | None = None,
    managed_worktree_kind: str | None = None,
    project_context: Dict[str, str] | None = None,
    owner_grant_token: str | None = None,
    owner_grant_launch_id: str | None = None,
    session_lifetime_id: str | None = None,
) -> Terminal:
    """Create one terminal under the cross-process context admission fence."""
    # An explicit name for a new session is request admission data.  Normalize
    # or reject it before resolving launch authority or entering any
    # capacity/writer fence; those boundaries may create durable leases or
    # managed worktrees.
    if new_session and session_name:
        session_name = validate_session_name(session_name)

    from cli_agent_orchestrator.services.managed_worktree_service import (
        create_managed_worktree,
        remove_managed_worktree,
    )
    from cli_agent_orchestrator.services.operations_service import context_launch_admission

    resolved_role = _resolve_context_role(new_session=new_session, context_role=context_role)
    launch_resolution = None
    from cli_agent_orchestrator.services.control_plane_registry import (
        registry_is_initialized,
        resolve_launch,
    )

    if registry_is_initialized():
        launch_resolution = resolve_launch(agent_profile, fallback_provider=provider)
        provider = launch_resolution.provider_adapter_id
        if allowed_tools is None:
            allowed_tools = list(launch_resolution.profile.allowedTools or [])
    if managed_worktree_kind is not None and resolved_role != "work":
        raise ValueError("only work contexts may use managed worktrees")
    source_worktree = _canonical_worktree(working_directory)
    requested_session_name = session_name
    from cli_agent_orchestrator.services.launch_authority import is_privileged_profile

    privileged_launch = (
        launch_resolution.owner_grant_required
        if launch_resolution is not None
        else is_privileged_profile(agent_profile)
    )
    owner_grant_scope: Dict[str, object] = {
        "profile_revision_id": (
            launch_resolution.profile_revision_id if launch_resolution is not None else None
        ),
        "provider_config_revision_id": (
            launch_resolution.provider_config_revision_id if launch_resolution is not None else None
        ),
        "project_id": project_context.get("id") if project_context else None,
        "launch_mode": "new_session" if new_session else "existing_session",
        "delegation_depth": 0,
    }
    if privileged_launch:
        if (
            not owner_grant_token
            or not owner_grant_launch_id
            or not validate_owner_launch_grant(
                owner_grant_token,
                launch_id=owner_grant_launch_id,
                agent_profile=agent_profile,
                provider=provider,
                canonical_worktree=source_worktree,
                requested_session_name=requested_session_name,
                grant_scope=owner_grant_scope,
            )
        ):
            from cli_agent_orchestrator.services.operations_service import AdmissionDenied

            raise AdmissionDenied("OWNER_GRANT_REQUIRED", {})
    elif owner_grant_token or owner_grant_launch_id:
        from cli_agent_orchestrator.services.operations_service import AdmissionDenied

        raise AdmissionDenied("OWNER_GRANT_SCOPE_MISMATCH", {})
    write_enabled = _write_enabled_lane(provider, agent_profile, allowed_tools)
    terminal_id = generate_terminal_id()
    managed = None
    with context_launch_admission(
        canonical_worktree=source_worktree,
        write_enabled=write_enabled,
        context_role=resolved_role,
        project_id=project_context.get("id") if project_context else None,
    ):
        # Housekeeping holds this same lifecycle fence while retiring linked
        # worktrees. A caller may have resolved its directory before waiting
        # for the fence, so prove that authority again before creating tmux or
        # publishing a writer lease.
        try:
            current_source_worktree = _canonical_worktree(working_directory)
        except (FileNotFoundError, ValueError) as exc:
            from cli_agent_orchestrator.services.operations_service import AdmissionDenied

            raise AdmissionDenied("WORKTREE_AUTHORITY_CHANGED", {}) from exc
        if current_source_worktree != source_worktree:
            from cli_agent_orchestrator.services.operations_service import AdmissionDenied

            raise AdmissionDenied("WORKTREE_AUTHORITY_CHANGED", {})
        if not new_session and session_name is not None:
            durable_session = resolve_session_lifetime(session_lifetime_id or session_name or "")
            expected_session_id = (
                session_lifetime_id
                if session_lifetime_id is not None
                else durable_session["session_id"] if durable_session is not None else None
            )
            if (
                durable_session is None
                or durable_session["deleted"]
                or durable_session["session_id"] != expected_session_id
                or durable_session["session_name"] != session_name
                or not any(
                    terminal.get("runtime_lifecycle") != TerminalLifecycle.EXITED.value
                    for terminal in durable_session["terminals"]
                )
            ):
                from cli_agent_orchestrator.services.operations_service import AdmissionDenied

                raise AdmissionDenied("SESSION_IDENTITY_CHANGED", {})
            runtime_proof = prove_live_session_runtime_authority(
                str(session_name),
                durable_session["terminals"],
                runtime_client=tmux_client,
            )
            if not runtime_proof.proven:
                from cli_agent_orchestrator.services.operations_service import AdmissionDenied

                raise AdmissionDenied(
                    runtime_proof.reason_code or "SESSION_IDENTITY_CHANGED",
                    {"inventory_uncertain": runtime_proof.inventory_uncertain},
                )
            session_lifetime_id = str(expected_session_id)
        if managed_worktree_kind is not None:
            managed = create_managed_worktree(source_worktree, terminal_id, managed_worktree_kind)
        launch_worktree = managed.path if managed is not None else source_worktree
        launch_directory = managed.path if managed is not None else working_directory
        try:
            return _create_terminal_after_admission(
                provider=provider,
                agent_profile=agent_profile,
                session_name=session_name,
                new_session=new_session,
                working_directory=launch_directory,
                allowed_tools=allowed_tools,
                registry=registry,
                launch_worktree=launch_worktree,
                write_enabled=write_enabled,
                context_role=resolved_role,
                managed_worktree_kind=managed.kind if managed is not None else None,
                managed_worktree_source=managed.source if managed is not None else None,
                managed_worktree_branch=managed.branch if managed is not None else None,
                managed_worktree_commit=managed.commit if managed is not None else None,
                project_context=project_context,
                terminal_id_override=terminal_id,
                privileged_launch=privileged_launch,
                structured_owner_authorized=privileged_launch,
                owner_grant_token=owner_grant_token,
                owner_grant_launch_id=owner_grant_launch_id,
                owner_grant_requested_session_name=requested_session_name,
                owner_grant_scope=owner_grant_scope,
                profile_revision_id=(
                    launch_resolution.profile_revision_id if launch_resolution is not None else None
                ),
                provider_config_revision_id=(
                    launch_resolution.provider_config_revision_id
                    if launch_resolution is not None
                    else None
                ),
                launch_snapshot=(
                    {
                        **launch_resolution.snapshot,
                        "tools": list(allowed_tools or []),
                    }
                    if launch_resolution is not None
                    else None
                ),
                provider_configuration=(
                    launch_resolution.provider_configuration
                    if launch_resolution is not None
                    else None
                ),
                resolved_profile=(
                    launch_resolution.profile if launch_resolution is not None else None
                ),
                owner_grant_canonical_worktree=source_worktree,
                session_lifetime_id=session_lifetime_id,
            )
        except Exception as error:
            if managed is not None:
                identity = {
                    "id": terminal_id,
                    "managed_worktree_kind": managed.kind,
                    "managed_worktree_source": managed.source,
                    "managed_worktree_branch": managed.branch,
                    "managed_worktree_commit": managed.commit,
                    "launch_worktree": managed.path,
                }
                launch_cleanup = getattr(error, "_cao_launch_cleanup_outcome", None)
                try:
                    durable = get_terminal_metadata(terminal_id)
                    durable_inventory_certain = True
                except Exception:
                    durable = None
                    durable_inventory_certain = False
                if (
                    isinstance(launch_cleanup, _LaunchCleanupOutcome)
                    and launch_cleanup.target_attempted
                    and not launch_cleanup.death_confirmed
                ):
                    logger.error(
                        "Managed worktree retained after launch failure because exact tmux target "
                        "cleanup is unconfirmed (%s:%s): %s",
                        launch_cleanup.session_name,
                        launch_cleanup.window_name,
                        identity,
                    )
                elif not durable_inventory_certain:
                    logger.error(
                        "Managed worktree retained after launch failure because durable "
                        "terminal inventory is uncertain: %s",
                        terminal_id,
                    )
                elif durable is not None and durable.get("runtime_lifecycle") != "exited":
                    logger.error(
                        "Managed worktree retained after uncertain launch failure; "
                        "durable terminal metadata remains for recovery: %s",
                        terminal_id,
                    )
                else:
                    cleanup = remove_managed_worktree(identity)
                    if cleanup.get("removed"):
                        if durable is not None:
                            try:
                                db_delete_terminal(terminal_id)
                            except Exception:
                                logger.exception(
                                    "Removed managed worktree but retained terminal metadata: %s",
                                    terminal_id,
                                )
                    else:
                        logger.error(
                            "Managed worktree retained after launch failure with durable identity "
                            "%s: %s",
                            identity,
                            cleanup,
                        )
            raise


def provider_runtime_sidecar_reconnect_required(terminal_id: str, provider: Any = None) -> bool:
    """Read the provider-owned stale-sidecar signal without mutating runtime."""
    runtime_provider = (
        provider if provider is not None else provider_manager.get_provider(terminal_id)
    )
    predicate = getattr(runtime_provider, "runtime_sidecar_reconnect_required", None)
    return bool(predicate and predicate() is True)


def _runtime_bound_provider_observer(
    terminal_id: str, provider: Any = None
) -> tuple[Any, str] | None:
    """Resolve one provider observer only for the persisted current generation."""
    metadata = get_terminal_metadata(terminal_id)
    if (
        metadata is None
        or metadata.get("runtime_lifecycle") not in {"starting", "running"}
        or not metadata.get("provider_resume_identity")
        or metadata.get("provider_resume_runtime_generation") != metadata.get("runtime_generation")
    ):
        return None
    runtime_provider = (
        provider if provider is not None else provider_manager.get_provider(terminal_id)
    )
    return runtime_provider, str(metadata["provider_resume_identity"])


def capture_provider_turn_outcome_cursor(terminal_id: str, provider: Any = None) -> Optional[str]:
    """Capture an opaque structured-event boundary for the current runtime."""
    try:
        resolved = _runtime_bound_provider_observer(terminal_id, provider)
        if resolved is None:
            return None
        runtime_provider, provider_session_id = resolved
        capture = getattr(runtime_provider, "capture_turn_outcome_cursor", None)
        if capture is None:
            return None
        cursor = capture(provider_session_id=provider_session_id)
    except Exception:
        logger.warning("Provider turn cursor capture failed for %s", terminal_id, exc_info=True)
        return None
    return cursor if isinstance(cursor, str) and cursor else None


def provider_turn_execution_active(terminal_id: str, provider: Any = None) -> Optional[bool]:
    """Classify exact provider-native task activity before another send.

    ``False`` is positive idle evidence (or a provider without this optional
    guard). ``None`` means a bound provider session could not be classified
    and must therefore fail closed at the workflow dispatcher.
    """
    metadata = get_terminal_metadata(terminal_id)
    if metadata is None or not metadata.get("provider_resume_identity"):
        return False
    if metadata.get("provider_resume_runtime_generation") != metadata.get("runtime_generation"):
        return None
    runtime_provider = (
        provider if provider is not None else provider_manager.get_provider(terminal_id)
    )
    observer = getattr(runtime_provider, "turn_execution_active", None)
    if observer is None:
        return False
    try:
        active = observer(provider_session_id=str(metadata["provider_resume_identity"]))
    except Exception:
        logger.warning(
            "Provider task-boundary observation failed for %s", terminal_id, exc_info=True
        )
        return None
    return active if isinstance(active, bool) else None


def provider_turn_outcome(
    terminal_id: str, after_cursor: str, provider: Any = None
) -> Optional[ProviderTurnOutcome]:
    """Read a provider-native outcome after this turn's exact send boundary."""
    try:
        resolved = _runtime_bound_provider_observer(terminal_id, provider)
        if resolved is None:
            return None
        runtime_provider, provider_session_id = resolved
        observer = getattr(runtime_provider, "get_turn_outcome", None)
        if observer is None:
            return None
        outcome = observer(
            provider_session_id=provider_session_id,
            after_cursor=after_cursor,
        )
    except Exception:
        logger.warning(
            "Provider turn outcome observation failed for %s",
            terminal_id,
            exc_info=True,
        )
        return None
    return outcome if isinstance(outcome, ProviderTurnOutcome) else None


def provider_runtime_sidecar_resume_identity(terminal_id: str) -> str:
    """Capture the exact provider identity at a managed readiness boundary."""
    provider = provider_manager.get_provider(terminal_id)
    resolver = getattr(provider, "runtime_sidecar_resume_identity", None)
    if not resolver:
        raise RuntimeError(f"Provider for terminal '{terminal_id}' cannot resume its sidecar")
    identity = resolver()
    if not isinstance(identity, str) or not identity:
        raise RuntimeError(f"Provider for terminal '{terminal_id}' has no resume identity")
    return identity


def bind_provider_runtime_session_identity(
    terminal_id: str,
    *,
    resume_identity: str,
    transcript_path: str,
    working_directory: str,
    source: str,
    runtime_generation: str,
) -> str:
    """Prove and bind Codex's exact root session at its synchronous start hook.

    A fresh Codex TUI has no conversation while idle. The provider-native
    identity first exists at ``SessionStart``, before the first model request;
    this method fences that callback to the managed terminal generation and
    exact foreground writable transcript.
    """
    metadata = get_terminal_metadata(terminal_id)
    if (
        metadata is None
        or metadata.get("provider") != ProviderType.CODEX.value
        or metadata.get("runtime_lifecycle") not in {"starting", "running"}
        or source not in {"startup", "resume"}
        or not isinstance(runtime_generation, str)
        or not hmac.compare_digest(
            str(metadata.get("runtime_generation") or ""), runtime_generation
        )
        or not os.path.isabs(working_directory)
        or not os.path.isabs(transcript_path)
    ):
        raise RuntimeError("Codex session identity callback is stale or malformed")

    provider = provider_manager.get_provider(terminal_id)
    resolver = getattr(provider, "runtime_sidecar_resume_identity", None)
    if resolver is None:
        raise RuntimeError(f"Provider for terminal '{terminal_id}' cannot prove resume identity")
    try:
        verified = resolver(
            expected_identity=resume_identity,
            expected_rollout_path=transcript_path,
        )
    except CodexProviderError as exc:
        raise RuntimeError("Codex session identity does not own the foreground runtime") from exc
    if not isinstance(verified, str) or not hmac.compare_digest(verified, resume_identity):
        raise RuntimeError(f"Provider for terminal '{terminal_id}' has stale resume identity")

    pane_working_directory = tmux_client.get_pane_working_directory(
        str(metadata["tmux_session"]), str(metadata["tmux_window"])
    )
    if (
        not pane_working_directory
        or not os.path.isabs(pane_working_directory)
        or Path(pane_working_directory).resolve(strict=False)
        != Path(working_directory).resolve(strict=False)
    ):
        raise RuntimeError("Codex session identity working directory is stale")
    if not bind_terminal_provider_resume_identity(
        terminal_id,
        provider=ProviderType.CODEX.value,
        resume_identity=verified,
        runtime_generation=runtime_generation,
    ):
        raise RuntimeError("Could not durably bind Codex session identity")
    bootstrap_turn_id = get_workflow_turn_provider_outcome_cursor_bootstrap(
        terminal_id, runtime_generation
    )
    if bootstrap_turn_id is not None:
        capture = getattr(provider, "capture_turn_outcome_cursor", None)
        cursor = capture(provider_session_id=verified) if capture is not None else None
        if (
            not isinstance(cursor, str)
            or not cursor
            or not bind_workflow_turn_provider_outcome_cursor(
                terminal_id, bootstrap_turn_id, cursor
            )
        ):
            # SessionStart is synchronous and precedes the first model
            # request. Failing this callback therefore stops work before a
            # policy outcome could occur without its exact event boundary.
            raise RuntimeError("Could not bind first-turn provider outcome boundary")
    return verified


def verify_provider_runtime_sidecar_resume_identity(terminal_id: str, resume_identity: str) -> None:
    """Prove the persisted identity still belongs to the foreground runtime."""
    provider = provider_manager.get_provider(terminal_id)
    resolver = getattr(provider, "runtime_sidecar_resume_identity", None)
    if not resolver:
        raise RuntimeError(f"Provider for terminal '{terminal_id}' cannot prove resume identity")
    verified = resolver(expected_identity=resume_identity)
    if not isinstance(verified, str) or not hmac.compare_digest(verified, resume_identity):
        raise RuntimeError(f"Provider for terminal '{terminal_id}' has stale resume identity")


def request_provider_runtime_sidecar_reconnect(
    terminal_id: str,
    logical_turn_id: int,
    resume_identity: str,
    registry: PluginRegistry | None = None,
    *,
    claim_token: str | None = None,
    attempt_token: str | None = None,
    attempt_state: str | None = None,
    side_effect_guard: Callable[[], bool] = lambda: True,
) -> None:
    """Restart a stale MCP client while resuming the exact provider context."""
    provider = provider_manager.get_provider(terminal_id)
    reconnect = getattr(provider, "reconnect_runtime_sidecar", None)
    if reconnect:
        if claim_token is None or attempt_token is None or attempt_state is None:
            raise RuntimeError("exact provider reconnect requires a durable attempt")

        def mark_launch_dispatched() -> bool:
            if not side_effect_guard():
                return False
            return mark_workflow_provider_reconnect_launch_dispatched(
                terminal_id,
                logical_turn_id,
                claim_token,
                attempt_token,
            )

        reconnect(
            resume_identity,
            attempt_token=attempt_token,
            attempt_state=attempt_state,
            mark_launch_dispatched=mark_launch_dispatched,
            runtime_ready=lambda: get_workflow_provider_reconnect_runtime_ready(
                terminal_id, attempt_token
            ),
            record_output_boundary=lambda device, inode, offset: (
                record_workflow_provider_reconnect_output_boundary(
                    terminal_id,
                    attempt_token,
                    device,
                    inode,
                    offset,
                )
            ),
            side_effect_guard=side_effect_guard,
        )
        return
    reconnect_input = getattr(provider, "runtime_sidecar_reconnect_input", None)
    if not reconnect_input:
        raise RuntimeError(f"Provider for terminal '{terminal_id}' cannot reconnect its sidecar")
    if not side_effect_guard():
        raise RuntimeError("provider reconnect lost runtime-operation ownership")
    reconnect_kwargs: Dict[str, Any] = {}
    if claim_token is not None:
        reconnect_kwargs["runtime_operation_claim_token"] = claim_token
    send_input(
        terminal_id,
        reconnect_input,
        registry=registry,
        sender_id="cao-workflow",
        orchestration_type=OrchestrationType.SEND_MESSAGE,
        logical_turn_id=logical_turn_id,
        **reconnect_kwargs,
    )


def get_terminal(terminal_id: str) -> Dict:
    """Get terminal data."""
    try:
        metadata = get_terminal_metadata(terminal_id)
        if not metadata:
            raise ValueError(f"Terminal '{terminal_id}' not found")
        # Snapshot ownership before the external provider observation.  A
        # successor may be admitted while status detection is in flight; only
        # this exact observed turn is eligible for release below.
        observed_execution_turn = get_provider_execution_turn(terminal_id)

        lifecycle = metadata.get("runtime_lifecycle")
        provider = None
        if lifecycle == TerminalLifecycle.EXITED.value:
            status = TerminalStatus.COMPLETED.value
        else:
            provider = provider_manager.get_provider(terminal_id)
            if provider is None:
                raise ValueError(f"Provider not found for terminal {terminal_id}")
            try:
                status = provider.get_status().value
            except Exception:
                reconciled = reconcile_terminal_runtime(terminal_id, provider)
                if reconciled is not True:
                    raise
                status = TerminalStatus.COMPLETED.value
            else:
                reconciled = reconcile_terminal_runtime(terminal_id, provider)
            if reconciled is True:
                lifecycle = TerminalLifecycle.EXITED.value
                status = TerminalStatus.COMPLETED.value
            else:
                refreshed = get_terminal_metadata(terminal_id)
                lifecycle = (refreshed or metadata).get("runtime_lifecycle") or "running"
            # A reconstructed provider can briefly project its resident Codex
            # session as Ready even though the exact provider-native task has
            # not emitted task_complete. Preserve both the durable execution
            # lease and capacity ownership until that stronger boundary says
            # the model turn settled. An unclassifiable bound runtime likewise
            # fails closed; a truly exited process was handled above.
            if (
                lifecycle
                in {
                    TerminalLifecycle.STARTING.value,
                    TerminalLifecycle.RUNNING.value,
                }
                and status == TerminalStatus.IDLE.value
            ):
                provider_task_active = provider_turn_execution_active(terminal_id, provider)
                if provider_task_active is not False:
                    status = TerminalStatus.PROCESSING.value
        # Usage is a separate, best-effort observation that cannot affect this
        # response or provider lifecycle. Durable providers are sampled while
        # active; pane parsing remains a completion-only fallback.
        from cli_agent_orchestrator.services.usage_service import observe_provider_usage

        observe_provider_usage(
            metadata,
            provider,
            completed=status == TerminalStatus.COMPLETED.value,
        )
        if status in (
            TerminalStatus.IDLE.value,
            TerminalStatus.COMPLETED.value,
            TerminalStatus.WAITING_USER_ANSWER.value,
            TerminalStatus.ERROR.value,
        ):
            if observed_execution_turn is not None and release_provider_execution(
                terminal_id, observed_execution_turn
            ):
                # Startup never replays a SENT envelope while its durable
                # execution lease survives. Only this exact settled-provider
                # observation may requeue it, and a concurrent receipt or
                # successor wins the database transaction instead.
                requeue_settled_unadmitted_workflow_turn(terminal_id, observed_execution_turn)
                _wake_queued_provider_execution()
        workflow = get_terminal_workflow_projection(terminal_id)
        execution = get_terminal_execution_projection(terminal_id)
        wait_reason = execution["wait_reason"]
        execution_state = (
            "exited"
            if lifecycle == "exited"
            else (
                "processing"
                if status == TerminalStatus.PROCESSING.value or execution["active_turn"]
                else (
                    "queued_provider_execution"
                    if wait_reason == "provider_capacity"
                    else (
                        "waiting_child_retirement"
                        if wait_reason == "child_retirement"
                        else (
                            "waiting_resource_recovery"
                            if wait_reason == "resource_health"
                            else (
                                "waiting_runtime_recovery"
                                if wait_reason == "runtime_recovery"
                                else (
                                    "waiting_workflow_continuation"
                                    if wait_reason == "workflow_continuation"
                                    else "ready"
                                )
                            )
                        )
                    )
                )
            )
        )

        return {
            "id": metadata["id"],
            "name": metadata["tmux_window"],
            "provider": metadata["provider"],
            "session_name": metadata["tmux_session"],
            "session_id": metadata.get("session_id") or f"legacy:{metadata['tmux_session']}",
            "agent_profile": metadata["agent_profile"],
            "allowed_tools": metadata.get("allowed_tools"),
            "status": status,
            "execution_state": execution_state,
            "execution_wait_reason": wait_reason,
            "lifecycle": lifecycle,
            "workflow_state": workflow["state"],
            "workflow_status": workflow["workflow_status"],
            "workflow_reason": workflow.get("workflow_reason"),
            "provider_outcome_code": workflow.get("provider_outcome_code"),
            "provider_outcome_detail": workflow.get("provider_outcome_detail"),
            "assignment_status": workflow["assignment_status"],
            "result_status": workflow["result_status"],
            "delivery_status": workflow["delivery_status"],
            "context_role": metadata.get("context_role"),
            "launch_worktree": metadata.get("launch_worktree"),
            "managed_worktree_kind": metadata.get("managed_worktree_kind"),
            "managed_worktree_commit": metadata.get("managed_worktree_commit"),
            "managed_worktree_branch": metadata.get("managed_worktree_branch"),
            "projectId": metadata.get("project_id"),
            "project_name": metadata.get("project_name"),
            "project_path": metadata.get("project_path"),
            "project_description": metadata.get("project_description"),
            "last_active": metadata["last_active"],
        }

    except Exception as e:
        logger.error(f"Failed to get terminal {terminal_id}: {e}")
        raise


def get_working_directory(terminal_id: str) -> Optional[str]:
    """Get the current working directory of a terminal's pane.

    Args:
        terminal_id: The terminal identifier

    Returns:
        Working directory path, or None if pane has no directory

    Raises:
        ValueError: If terminal not found
        Exception: If unable to query working directory
    """
    try:
        metadata = get_terminal_metadata(terminal_id)
        if not metadata:
            raise ValueError(f"Terminal '{terminal_id}' not found")

        working_dir = tmux_client.get_pane_working_directory(
            metadata["tmux_session"], metadata["tmux_window"]
        )
        return working_dir

    except Exception as e:
        logger.error(f"Failed to get working directory for terminal {terminal_id}: {e}")
        raise


def send_input(
    terminal_id: str,
    message: str,
    registry: PluginRegistry | None = None,
    sender_id: str | None = None,
    orchestration_type: OrchestrationType | None = None,
    logical_turn_id: int | None = None,
    runtime_operation_claim_token: str | None = None,
) -> bool:
    """Send input to terminal via tmux paste buffer.

    Uses bracketed paste mode (-p) to bypass TUI hotkey handling. The number
    of Enter keys sent after pasting is determined by the provider's
    ``paste_enter_count`` property (e.g., some TUIs need 2 Enters because
    bracketed paste triggers multi-line mode).
    """
    runtime_operation_token: str | None = None
    provider_execution_released = False
    try:
        metadata = get_terminal_metadata(terminal_id)
        if not metadata:
            raise ValueError(f"Terminal '{terminal_id}' not found")
        if metadata.get("runtime_lifecycle") in {"exit_pending", "exited"}:
            raise RuntimeError(f"Terminal '{terminal_id}' runtime is not writable")

        # Check how many Enter keys the provider needs after paste
        provider = provider_manager.get_provider(terminal_id)
        enter_count = provider.paste_enter_count if provider else 1

        execution_acquired = False
        transport_accepted = False
        if runtime_operation_claim_token is not None:
            if not terminal_runtime_operation_owned(
                terminal_id, runtime_operation_claim_token, "reconnect"
            ):
                raise RuntimeError("provider reconnect lost runtime-operation ownership")
            runtime_operation_token = runtime_operation_claim_token
        elif logical_turn_id is not None:
            from cli_agent_orchestrator.services.operations_service import (
                acquire_provider_execution_slot,
            )

            acquire_provider_execution_slot(terminal_id, logical_turn_id)
            execution_acquired = True
        if runtime_operation_token is None:
            runtime_operation_token = acquire_terminal_runtime_transport(terminal_id)
        if runtime_operation_token is None:
            from cli_agent_orchestrator.services.operations_service import AdmissionDenied

            if execution_acquired and release_provider_execution(terminal_id, logical_turn_id):
                _wake_queued_provider_execution(registry)
            raise AdmissionDenied("TERMINAL_RUNTIME_OPERATION_BUSY", {})
        try:
            if logical_turn_id is not None:
                cursor_requirement = getattr(provider, "turn_outcome_cursor_required", None)
                cursor_required = bool(cursor_requirement and cursor_requirement() is True)
                outcome_cursor = capture_provider_turn_outcome_cursor(terminal_id, provider)
                if cursor_required and outcome_cursor is None:
                    deferred_requirement = getattr(
                        provider, "defer_turn_outcome_cursor_to_session_start", None
                    )
                    runtime_generation = metadata.get("runtime_generation")
                    if (
                        not deferred_requirement
                        or deferred_requirement() is not True
                        or not isinstance(runtime_generation, str)
                        or not reserve_workflow_turn_provider_outcome_cursor_bootstrap(
                            terminal_id, logical_turn_id, runtime_generation
                        )
                    ):
                        raise RuntimeError("required provider outcome boundary is unavailable")
                if outcome_cursor is not None and not bind_workflow_turn_provider_outcome_cursor(
                    terminal_id, logical_turn_id, outcome_cursor
                ):
                    raise RuntimeError("provider outcome boundary lost workflow-turn ownership")
            tmux_client.send_keys(
                metadata["tmux_session"],
                metadata["tmux_window"],
                message,
                enter_count=enter_count,
            )
            transport_accepted = True
        finally:
            if execution_acquired and not transport_accepted:
                provider_execution_released = release_provider_execution(
                    terminal_id, logical_turn_id
                )

        # Notify the provider that external input was received.
        # This allows providers to adjust status
        # detection — specifically to stop reporting IDLE for the post-init
        # state and resume normal COMPLETED detection after a real task.
        if provider:
            try:
                provider.mark_input_received()
            except Exception:
                logger.warning(
                    "Provider input marker failed after accepted transport for %s",
                    terminal_id,
                    exc_info=True,
                )

        # The provider object is rebuilt after a service restart.  Retain the
        # direct-handoff submission boundary on its relation so Codex can
        # restore the narrow no-visible-user-row completion path.  This is a
        # no-op for ordinary input and for non-handoff relations.
        if orchestration_type == OrchestrationType.HANDOFF:
            try:
                mark_handoff_child_input_received(terminal_id)
            except Exception:
                logger.warning(
                    "Handoff input marker failed after accepted transport for %s",
                    terminal_id,
                    exc_info=True,
                )

        try:
            update_last_active(terminal_id)
        except Exception:
            logger.warning(
                "Last-active update failed after accepted transport for %s",
                terminal_id,
                exc_info=True,
            )
        logger.info(f"Sent input to terminal: {terminal_id}")
        if registry is not None and sender_id is not None and orchestration_type is not None:
            dispatch_plugin_event(
                registry,
                "post_send_message",
                PostSendMessageEvent(
                    session_id=metadata["tmux_session"],
                    sender=sender_id,
                    receiver=terminal_id,
                    message=message,
                    orchestration_type=orchestration_type,
                ),
            )
        return True

    except Exception as e:
        logger.error(f"Failed to send input to terminal {terminal_id}: {e}")
        raise
    finally:
        if runtime_operation_token is not None and runtime_operation_claim_token is None:
            if (
                release_terminal_runtime_operation(terminal_id, runtime_operation_token)
                or provider_execution_released
            ):
                _wake_queued_provider_execution(registry)


def send_special_key(terminal_id: str, key: str) -> bool:
    """Send a tmux special key sequence (e.g., C-d, C-c) to terminal.

    Unlike send_input(), this sends the key as a tmux key name (not literal text)
    and does not append a carriage return. Used for control signals like Ctrl+D (EOF).

    Args:
        terminal_id: Target terminal identifier
        key: Tmux key name (e.g., "C-d", "C-c", "Escape")

    Returns:
        True if the key was sent successfully

    Raises:
        ValueError: If terminal not found
    """
    runtime_operation_token: str | None = None
    try:
        metadata = get_terminal_metadata(terminal_id)
        if not metadata:
            raise ValueError(f"Terminal '{terminal_id}' not found")
        if metadata.get("runtime_lifecycle") in {"exit_pending", "exited"}:
            raise RuntimeError(f"Terminal '{terminal_id}' runtime is not writable")

        runtime_operation_token = acquire_terminal_runtime_transport(terminal_id)
        if runtime_operation_token is None:
            from cli_agent_orchestrator.services.operations_service import AdmissionDenied

            raise AdmissionDenied("TERMINAL_RUNTIME_OPERATION_BUSY", {})

        tmux_client.send_special_key(metadata["tmux_session"], metadata["tmux_window"], key)

        update_last_active(terminal_id)
        logger.info(f"Sent special key '{key}' to terminal: {terminal_id}")
        return True

    except Exception as e:
        logger.error(f"Failed to send special key to terminal {terminal_id}: {e}")
        raise
    finally:
        if runtime_operation_token is not None:
            if release_terminal_runtime_operation(terminal_id, runtime_operation_token):
                _wake_queued_provider_execution()


def prepare_terminal_for_destruction(terminal_id: str) -> None:
    """Persist a child snapshot before any action that can destroy its pane.

    This deliberately raises on capture or persistence failure.  Callers must
    not signal/kill/delete the terminal after such a failure because the only
    recoverable partial evidence is still in the live pane.
    """
    if not terminal_requires_result_snapshot(terminal_id):
        return
    partial = get_output(terminal_id, OutputMode.LAST)
    if not persist_terminal_result_snapshot(terminal_id, partial):
        raise RuntimeError(f"Could not persist durable result snapshot for {terminal_id}")


def cleanup_managed_worktree(metadata: Dict) -> None:
    """Remove a clean managed worktree or retain all authority fail-closed."""
    if not metadata.get("managed_worktree_kind"):
        return
    from cli_agent_orchestrator.services.managed_worktree_service import (
        remove_managed_worktree,
    )

    cleanup = remove_managed_worktree(metadata)
    if not cleanup.get("removed"):
        raise ManagedWorktreeCleanupError(cleanup.get("reason_code", "MANAGED_WORKTREE_UNVERIFIED"))


def validate_managed_worktree_cleanup(metadata: Dict) -> None:
    """Prove cleanup eligibility without mutating a worktree."""
    if not metadata.get("managed_worktree_kind"):
        return
    from cli_agent_orchestrator.services.managed_worktree_service import (
        managed_worktree_status,
    )

    status = managed_worktree_status(metadata)
    reason = None
    if not status.get("safe"):
        reason = status.get("reason_code", "MANAGED_WORKTREE_UNVERIFIED")
    elif not status.get("clean"):
        reason = "MANAGED_WORKTREE_DIRTY"
    elif status.get("kind") == "task" and status.get("branch") != status.get("expected_branch"):
        reason = "TASK_WORKTREE_AUTHORITY_CHANGED"
    elif status.get("kind") == "reviewer" and (
        status.get("branch") is not None or status.get("commit") != status.get("expected_commit")
    ):
        reason = "REVIEW_WORKTREE_AUTHORITY_CHANGED"
    if reason:
        raise ManagedWorktreeCleanupError(reason)


def _validate_exit_provider(metadata: Dict, provider) -> None:
    """Prove that the provider adapter owns the durable terminal target."""
    expected = {
        "terminal_id": metadata["id"],
        "session_name": metadata["tmux_session"],
        "window_name": metadata["tmux_window"],
    }
    mismatches = [
        name for name, value in expected.items() if getattr(provider, name, None) != value
    ]
    provider_type = metadata.get("provider")
    expected_class = (
        _PROVIDER_CLASS_NAMES.get(provider_type) if isinstance(provider_type, str) else None
    )
    provider_module = type(provider).__module__
    if (
        expected_class
        and provider_module.startswith("cli_agent_orchestrator.providers.")
        and type(provider).__name__ != expected_class
    ):
        mismatches.append("provider")
    if mismatches:
        raise ExitAuthorityError(
            "EXIT_PROVIDER_AUTHORITY_STALE",
            "Provider authority does not match the durable terminal "
            f"({', '.join(sorted(set(mismatches)))})",
        )


def _already_exited_result(terminal_id: str, message: str) -> ExitTerminalResult:
    """Reconcile positive death and report that no exit command was delivered."""
    metadata = get_terminal_metadata(terminal_id)
    if metadata and metadata.get("runtime_lifecycle") != TerminalLifecycle.EXITED.value:
        if reconcile_terminal_runtime(terminal_id) is not True:
            raise ExitAuthorityError(
                "EXIT_DEATH_RECONCILIATION_FAILED",
                "Provider death was observed but durable exit reconciliation failed",
            )
    return ExitTerminalResult(
        success=True,
        lifecycle=TerminalLifecycle.EXITED.value,
        outcome="already_exited",
        message=message,
        command_delivered=False,
    )


def exit_terminal(terminal_id: str) -> ExitTerminalResult:
    """Deliver one provider exit command only under exact live-pane authority."""
    metadata = get_terminal_metadata(terminal_id)
    if not metadata:
        raise ValueError(f"Terminal '{terminal_id}' not found")
    if metadata.get("runtime_lifecycle") == TerminalLifecycle.EXITED.value:
        return _already_exited_result(terminal_id, "Terminal was already exited; no command sent")
    provider = provider_manager.get_provider(terminal_id)
    if provider is None:
        raise ValueError(f"Provider not found for terminal {terminal_id}")
    _validate_exit_provider(metadata, provider)

    try:
        if provider.is_process_alive() is False:
            reconciled = reconcile_terminal_runtime(terminal_id, provider)
            if reconciled is True:
                return ExitTerminalResult(
                    success=True,
                    lifecycle=TerminalLifecycle.EXITED.value,
                    outcome="already_exited",
                    message="Provider was already dead and was reconciled; no command sent",
                    command_delivered=False,
                )
    except Exception as exc:
        if isinstance(exc, ExitAuthorityError):
            raise
        # Provider liveness is supplementary. Exact tmux inventory below is
        # still allowed to establish live delivery authority.
        pass

    if metadata.get("runtime_lifecycle") == TerminalLifecycle.EXIT_PENDING.value:
        target = None
    else:
        try:
            target = tmux_client.exact_pane_target(
                metadata["tmux_session"], metadata["tmux_window"]
            )
        except PaneTargetError as exc:
            raise ExitAuthorityError(
                exc.reason_code,
                str(exc),
                inventory_uncertain=exc.reason_code == "EXIT_INVENTORY_UNCERTAIN",
            ) from exc
        if target.current_command in SHELL_COMMANDS:
            current = get_terminal_metadata(terminal_id)
            if not current or current.get("runtime_operation_kind") != "reconnect":
                raise ExitAuthorityError(
                    "EXIT_PROVIDER_NOT_LIVE",
                    "The exact pane is at a shell, not a live provider process",
                )
            # A crashed reconnect may have durably exited the provider before
            # its lease expired. Retirement first competes for the same
            # terminal operation; if it wins, the shell is positive death
            # evidence and the stale reconnect token can never relaunch.
            prepare_terminal_for_destruction(terminal_id)
            shell_gap_claim = claim_terminal_runtime_exit(terminal_id)
            if shell_gap_claim == "busy":
                raise ExitAuthorityError(
                    "EXIT_RUNTIME_OPERATION_BUSY",
                    "Terminal recovery is in progress; retry exit safely",
                    inventory_uncertain=True,
                )
            if shell_gap_claim == "exited":
                return _already_exited_result(
                    terminal_id, "Terminal exited before command delivery; no command sent"
                )
            if shell_gap_claim == "dispatch":
                cancel_child_assignments_for_terminal(terminal_id)
                cancel_workflows_for_terminal(terminal_id)
                _wake_queued_provider_execution()
                if reconcile_terminal_runtime(terminal_id, provider) is True:
                    return ExitTerminalResult(
                        success=True,
                        lifecycle=TerminalLifecycle.EXITED.value,
                        outcome="already_exited",
                        message="Reconnect shell gap retired safely; no command sent",
                        command_delivered=False,
                    )
            raise ExitAuthorityError(
                "EXIT_DEATH_RECONCILIATION_FAILED",
                "Reconnect shell gap could not be reconciled safely",
                inventory_uncertain=True,
            )

        # Fence a stale provider/window observation before the irreversible
        # exactly-once claim. Terminal target fields are durable authority.
        current = get_terminal_metadata(terminal_id)
        authority_fields = ("id", "tmux_session", "tmux_window", "provider")
        if not current or any(current.get(key) != metadata.get(key) for key in authority_fields):
            raise ExitAuthorityError(
                "EXIT_TERMINAL_AUTHORITY_STALE",
                "Terminal authority changed while resolving its live pane",
            )
        if current.get("runtime_lifecycle") == TerminalLifecycle.EXITED.value:
            return _already_exited_result(
                terminal_id, "Terminal exited before command delivery; no command sent"
            )
        prepare_terminal_for_destruction(terminal_id)

    claim = claim_terminal_runtime_exit(terminal_id)
    if claim == "missing":
        raise ValueError(f"Terminal '{terminal_id}' not found")
    if claim == "exited":
        return _already_exited_result(
            terminal_id, "Terminal exited before command delivery; no command sent"
        )
    if claim == "busy":
        raise ExitAuthorityError(
            "EXIT_RUNTIME_OPERATION_BUSY",
            "Terminal recovery or input transport is in progress; retry exit safely",
            inventory_uncertain=True,
        )
    command_delivered = False
    if claim == "dispatch":
        if target is None:
            raise ExitAuthorityError(
                "EXIT_INVENTORY_UNCERTAIN",
                "Exit dispatch was claimed without an exact live pane",
                inventory_uncertain=True,
            )
        cancel_child_assignments_for_terminal(terminal_id)
        cancel_workflows_for_terminal(terminal_id)
        _wake_queued_provider_execution()
        exit_command = provider.exit_cli()
        if exit_command.startswith(("C-", "M-")):
            tmux_client.send_special_key(
                metadata["tmux_session"],
                metadata["tmux_window"],
                exit_command,
                pane_id=target.pane_id,
            )
        else:
            # Exit commands are one-shot control input, not ordinary provider
            # messages.  In particular Codex's normal bracketed-paste path may
            # use two Enters; a slash exit needs exactly one effective submit.
            tmux_client.send_keys(
                metadata["tmux_session"],
                metadata["tmux_window"],
                exit_command,
                enter_count=1,
                pane_id=target.pane_id,
            )
        command_delivered = True
    deadline = time.monotonic() + EXIT_CONFIRMATION_TIMEOUT_SECONDS
    while True:
        try:
            provider.get_status()
        except Exception:
            # Status parsing is not death evidence.  The independent process
            # and tmux observations below may still positively settle exit.
            pass
        reconciled = reconcile_terminal_runtime(terminal_id, provider)
        if reconciled is True:
            return ExitTerminalResult(
                success=True,
                lifecycle=TerminalLifecycle.EXITED.value,
                outcome="command_delivered" if command_delivered else "already_exited",
                message=(
                    "Exit command delivered and provider exit confirmed"
                    if command_delivered
                    else "A prior exit request completed; no command was redelivered"
                ),
                command_delivered=command_delivered,
            )
        if time.monotonic() >= deadline:
            return ExitTerminalResult(
                success=False,
                lifecycle=TerminalLifecycle.EXIT_PENDING.value,
                outcome="exit_pending",
                message=(
                    "Exit command was delivered, but provider exit is not yet confirmed; "
                    "ownership and capacity remain reserved"
                    if command_delivered
                    else "A prior exit request is still pending; no command was redelivered"
                ),
                command_delivered=command_delivered,
            )
        time.sleep(EXIT_CONFIRMATION_POLL_SECONDS)


def get_output(terminal_id: str, mode: OutputMode = OutputMode.FULL) -> str:
    """Get terminal output.

    For ``LAST`` mode, if the provider declares ``extraction_retries > 0``,
    retries extraction with 10 s delays between attempts.  This handles
    TUI-based providers (e.g. Gemini CLI's Ink renderer) whose notification
    spinners can temporarily obscure response text in the tmux capture buffer.

    If the provider exposes an ``extraction_tail_lines`` attribute, the
    history capture for LAST mode uses that value instead of the default
    ``TMUX_HISTORY_LINES``. Status-check captures are unaffected (they go
    through get_status directly). A single capture-pane call is made per
    get_output invocation.
    """
    try:
        metadata = get_terminal_metadata(terminal_id)
        if not metadata:
            raise ValueError(f"Terminal '{terminal_id}' not found")

        def durable_output() -> str:
            log_path = TERMINAL_LOG_DIR / f"{terminal_id}.log"
            if log_path.is_file() and not log_path.is_symlink():
                return log_path.read_text(encoding="utf-8", errors="replace")
            compressed = log_path.with_suffix(log_path.suffix + ".gz")
            if compressed.is_file() and not compressed.is_symlink():
                with gzip.open(compressed, "rt", encoding="utf-8", errors="replace") as stream:
                    return stream.read()
            raise TerminalOutputUnavailable(
                f"Durable output is unavailable for terminal {terminal_id}"
            )

        def capture_output(*, tail_lines: int | None = None) -> str:
            try:
                return tmux_client.get_history(
                    metadata["tmux_session"], metadata["tmux_window"], tail_lines=tail_lines
                )
            except Exception:
                if metadata.get("runtime_lifecycle") == TerminalLifecycle.EXITED.value:
                    return durable_output()
                raise

        if mode == OutputMode.FULL:
            return _sanitize_human_terminal_output(capture_output())
        elif mode == OutputMode.LAST:
            provider = provider_manager.get_provider(terminal_id)
            if provider is None:
                if metadata.get("runtime_lifecycle") == TerminalLifecycle.EXITED.value:
                    return durable_output()
                raise ValueError(f"Provider not found for terminal {terminal_id}")

            # Capability check: providers that need deeper scrollback for extraction
            # opt in by defining ``extraction_tail_lines``. Base providers don't.
            extract_lines = getattr(provider, "extraction_tail_lines", None)
            full_output = capture_output(tail_lines=extract_lines)

            retries = provider.extraction_retries
            last_err: Exception | None = None
            for attempt in range(1 + retries):
                try:
                    if attempt > 0:
                        time.sleep(10.0)
                        full_output = capture_output(tail_lines=extract_lines)
                    return provider.extract_last_message_from_script(full_output)
                except ValueError as exc:
                    last_err = exc
                    logger.debug(
                        "Output extraction attempt %d/%d for %s failed: %s",
                        attempt + 1,
                        1 + retries,
                        terminal_id,
                        exc,
                    )
            raise last_err  # type: ignore[misc]

    except Exception as e:
        logger.error(f"Failed to get output from terminal {terminal_id}: {e}")
        raise


_TERMINAL_DELETION_IDENTITY_FIELDS = (
    "session_id",
    "tmux_session",
    "tmux_window",
    "launch_worktree",
    "write_enabled",
    "managed_worktree_kind",
    "managed_worktree_source",
    "managed_worktree_branch",
    "managed_worktree_commit",
    "runtime_pane_id",
    "runtime_pane_pid",
    "runtime_generation",
    "runtime_generation_origin",
    "runtime_process_start_ticks",
)


def _terminal_deletion_identity(metadata: Dict) -> Dict[str, object]:
    return {field: metadata.get(field) for field in _TERMINAL_DELETION_IDENTITY_FIELDS}


def delete_terminal(terminal_id: str, registry: PluginRegistry | None = None) -> bool:
    """Delete only one durably exited terminal under exact runtime authority."""
    try:
        from cli_agent_orchestrator.services.operations_service import (
            context_lifecycle_fence,
        )

        with context_lifecycle_fence():
            metadata = get_terminal_metadata(terminal_id)
            if not metadata:
                if terminal_deletion_receipt_exists(terminal_id):
                    return True
                raise ValueError(f"Terminal '{terminal_id}' not found")

            lifecycle = metadata.get("runtime_lifecycle")
            if lifecycle != TerminalLifecycle.EXITED.value:
                reason_code = (
                    "TERMINAL_EXIT_PENDING"
                    if lifecycle == TerminalLifecycle.EXIT_PENDING.value
                    else "TERMINAL_RUNTIME_ACTIVE"
                )
                raise TerminalDeletionError(
                    reason_code,
                    "Terminal runtime is not durably exited; use graceful exit and wait for "
                    "positive provider death before deleting history",
                )

            # Worktree authority and any required child-result snapshot are
            # read-only preconditions. Never retire a pane until both pass.
            try:
                validate_managed_worktree_cleanup(metadata)
            except ManagedWorktreeCleanupError as exc:
                raise TerminalDeletionError(
                    "TERMINAL_WORKTREE_PROTECTED",
                    "The exited terminal still owns a managed worktree that ThreadCells "
                    f"must retain ({exc.reason_code})",
                ) from exc
            prepare_terminal_for_destruction(terminal_id)

            retirement = retire_exited_terminal_runtime(terminal_id)
            if retirement is not True:
                raise TerminalDeletionError(
                    (
                        "TERMINAL_DEATH_UNCONFIRMED"
                        if retirement is False
                        else "TERMINAL_RUNTIME_AUTHORITY_UNCERTAIN"
                    ),
                    (
                        "The exact exited terminal pane could not be retired; metadata remains "
                        "protected"
                        if retirement is False
                        else "Exact terminal runtime identity could not be established; metadata "
                        "remains protected"
                    ),
                    inventory_uncertain=retirement is None,
                )

            current = get_terminal_metadata(terminal_id)
            if (
                not current
                or current.get("runtime_lifecycle") != TerminalLifecycle.EXITED.value
                or _terminal_deletion_identity(current) != _terminal_deletion_identity(metadata)
            ):
                raise TerminalDeletionError(
                    "TERMINAL_IDENTITY_CHANGED",
                    "Terminal identity changed during deletion; retry after reconciliation",
                    inventory_uncertain=True,
                )

            # Re-running the exited transition is intentional: it clears any
            # legacy stale writer/provider leases only after exact death proof.
            if not mark_terminal_runtime_exited(terminal_id):
                raise TerminalDeletionError(
                    "TERMINAL_IDENTITY_CHANGED",
                    "Terminal metadata changed during deletion; retry after reconciliation",
                    inventory_uncertain=True,
                )
            cancel_child_assignments_for_terminal(terminal_id)
            cancel_workflows_for_terminal(terminal_id)
            _wake_queued_provider_execution(registry)
            try:
                provider_manager.cleanup_provider(terminal_id)
            except Exception:
                logger.warning("Provider cleanup failed for exited terminal %s", terminal_id)
            try:
                cleanup_managed_worktree(metadata)
            except ManagedWorktreeCleanupError as exc:
                raise TerminalDeletionError(
                    "TERMINAL_WORKTREE_PROTECTED",
                    "The exited terminal's managed worktree changed during deletion and "
                    f"was retained ({exc.reason_code})",
                ) from exc
            try:
                deletion = db_delete_exited_terminal(
                    terminal_id,
                    expected_identity=_terminal_deletion_identity(metadata),
                )
            except AmbiguousTerminalIdentity as exc:
                raise TerminalDeletionError(
                    "TERMINAL_IDENTITY_CHANGED",
                    "Terminal identity changed during deletion; retry after reconciliation",
                    inventory_uncertain=True,
                ) from exc
            if deletion["missing"]:
                raise TerminalDeletionError(
                    "TERMINAL_IDENTITY_CHANGED",
                    "Terminal metadata disappeared during deletion; retry after reconciliation",
                    inventory_uncertain=True,
                )

        deleted = bool(deletion["deleted"])
        logger.info(
            "Deleted terminal: %s (already_deleted=%s)",
            terminal_id,
            deletion["already_deleted"],
        )
        if deleted:
            dispatch_plugin_event(
                registry,
                "post_kill_terminal",
                PostKillTerminalEvent(
                    session_id=metadata["tmux_session"],
                    terminal_id=terminal_id,
                    agent_name=metadata.get("agent_profile"),
                ),
            )
        return True

    except Exception as e:
        logger.error(f"Failed to delete terminal {terminal_id}: {e}")
        raise
