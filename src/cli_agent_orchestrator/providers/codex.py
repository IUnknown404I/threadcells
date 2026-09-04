"""Codex CLI provider implementation."""

import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import stat
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

from cli_agent_orchestrator.clients.tmux import tmux_client
from cli_agent_orchestrator.constants import TERMINAL_LOG_DIR
from cli_agent_orchestrator.models.provider import ProviderTurnOutcome
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.models.usage import UsageObservation
from cli_agent_orchestrator.providers.base import BaseProvider
from cli_agent_orchestrator.runtime_generation import (
    ACTIVE_RUNTIME_GENERATION,
    PROVIDER_RECONNECT_ATTEMPT_ENV,
    RUNTIME_GENERATION_ENV,
)
from cli_agent_orchestrator.utils.agent_profiles import load_agent_profile
from cli_agent_orchestrator.utils.mcp_runtime import canonicalize_cao_mcp_server_config
from cli_agent_orchestrator.utils.terminal import wait_for_shell

logger = logging.getLogger(__name__)

# Regex patterns for Codex output analysis
ANSI_CODE_PATTERN = r"\x1b\[[0-9;]*m"
# ``capture-pane`` can retain cursor-control sequences in addition to SGR
# colour codes.  They are presentation noise, not a change to an assistant
# response, so remove them before comparing completion candidates.
ANSI_CONTROL_PATTERN = r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[()][0-2AB])"
IDLE_PROMPT_PATTERN = r"(?:❯|›|codex>)"
# Number of lines from the bottom of capture to check for the idle prompt.
# With --no-alt-screen, codex output is inline (scrollback contains history),
# so we can't anchor to \Z. Instead, check the last few lines where the prompt
# and status bar appear.
IDLE_PROMPT_TAIL_LINES = 5
# The idle prompt character ❯ (U+276F) is rendered on-screen by capture-pane
# but is NOT written to the raw output stream captured by pipe-pane.  Instead,
# the TUI footer text "? for shortcuts" is reliably present whenever the TUI
# is active.  This is intentionally permissive — _has_idle_pattern() is a
# lightweight pre-check; the real status decision is made by get_status()
# which uses capture-pane (rendered screen).
IDLE_PROMPT_PATTERN_LOG = r"\? for shortcuts"
# Match assistant response start: "assistant:/codex:/agent:" (label style from synthetic
# test fixtures) or "•" bullet point (real Codex interactive output format).
# Horizontal space is deliberate: \s* could consume a preceding blank line,
# making a bullet marker start before the bullet itself. Structural tool-block
# selection would then see an empty first line instead of "• Ran ...".
ASSISTANT_PREFIX_PATTERN = r"^(?:(?:assistant|codex|agent)\s*:|[^\S\n]*•)"
# Match user input: "You ..." (label style) or "› text" (Codex interactive prompt).
# The "›[^\S\n]*\S" alternative requires a non-whitespace character on the same line
# to distinguish user input ("› what is your role?") from the empty idle prompt ("› ").
# [^\S\n] matches horizontal whitespace only (spaces/tabs), preventing the pattern
# from crossing newline boundaries into subsequent lines.
USER_PREFIX_PATTERN = r"^(?:You\b|›[^\S\n]*\S)"
# Strict idle prompt pattern for extraction: matches empty prompt lines only.
# Distinguishes "› " (idle) from "› user message" (user input with text).
IDLE_PROMPT_STRICT_PATTERN = r"^\s*(?:❯|›|codex>)\s*$"

PROCESSING_PATTERN = r"\b(thinking|working|running|executing|processing|analyzing)\b"
WAITING_PROMPT_PATTERN = r"^(?:Approve|Allow)\b.*\b(?:y/n|yes/no|yes|no)\b"
ERROR_PATTERN = r"^(?:Error:|ERROR:|Traceback \(most recent call last\):|panic:)"
# Full Codex 0.146.0 advisory menus. The patterns deliberately include every
# title, action, description, and the confirmation/help sentence, rather than
# treating a numbered list or a word such as "fast" as machine-actionable.
# Tmux can wrap a rendered row at narrow widths, so matching is done after
# whitespace normalization; the canonical wording and selected row remain
# mandatory.
RATE_LIMIT_MODEL_SWITCH_PATTERN = re.compile(
    r"Approaching rate limits\s+"
    r"Switch to (?P<rate_question_model>\S+) for lower credit usage\?\s+"
    r"(?P<rate_option_1_selected>[›>])?\s*1\.\s*Switch to "
    r"(?P<rate_option_1_model>\S+)\s+Fast and affordable agentic coding model\.\s+"
    r"(?P<rate_option_2_selected>[›>])?\s*2\.\s*Keep current model\s+"
    r"(?P<rate_option_3_selected>[›>])?\s*3\.\s*Keep current model "
    r"\(never show again\)\s+Hide future rate limit reminders about switching models\.\s+"
    r"Press enter to confirm or esc to go back",
    re.IGNORECASE,
)
FAST_MODE_ADVISORY_PATTERN = re.compile(
    r"Our systems are thinking a bit more about this request before responding\.\s+"
    r"Hang tight or retry with a faster model for a quicker response, though it may be "
    r"less capable of handling complex requests\.\s+"
    r"(?P<fast_option_1_selected>[›>])?\s*1\.\s*Retry with a faster model\s+"
    r"(?P<fast_option_2_selected>[›>])?\s*2\.\s*Dismiss and keep waiting\s+"
    r"(?P<fast_option_3_selected>[›>])?\s*3\.\s*Learn more\s+"
    r"No action is required\. Codex will keep waiting, and this menu will close when "
    r"the response is ready\.",
    re.IGNORECASE,
)
# Startup failures are emitted before Codex draws an idle footer.  Keep this
# separate from ERROR_PATTERN: normal assistant prose can legitimately discuss
# an error after a task has begun, but these lines are actionable during launch.
STARTUP_ERROR_PATTERN = (
    r"^(?:error:|Error:|ERROR:|Traceback \(most recent call last\):|panic:|"
    r"codex: command not found|command not found:)"
)
STARTUP_EVIDENCE_LIMIT = 12_000
CODEX_LAST_RESPONSE_SCAN_MAX_BYTES = 8 * 1024 * 1024
# Resuming a long conversation can redraw more than the ordinary 200-line
# status window before the TUI becomes idle. Keep reconnect evidence bounded,
# but large enough that the shell marker cannot disappear during that redraw.
SIDECAR_RECONNECT_HISTORY_LINES = 2_000

# Codex TUI footer indicators (status bar below the idle prompt).
# Used to detect when the bottom lines contain TUI chrome rather than user input.
# v0.110 and earlier: "? for shortcuts" and "N% context left"
# v0.111+: "model · N% left · path" (PR #13202 restored draft footer hints)
TUI_FOOTER_PATTERN = r"(?:\?\s+for shortcuts|context left|\d+%\s+left|·\s+[~/])"
# Codex TUI progress spinner: "• Working (0s • esc to interrupt)",
# "• Thinking (2s ...)", "• Starting script creation (10s • esc to interrupt)".
# The prefix text varies.  Codex shows seconds initially, then formats longer
# live work as minutes (for example, "1m 01s") and may use either bullet glyph.
# Appears inline with --no-alt-screen when the agent is actively processing.
# Must be checked before COMPLETED to avoid false positives (the • matches
# ASSISTANT_PREFIX_PATTERN and the TUI footer › matches idle prompt).
TUI_PROGRESS_DURATION_PATTERN = (
    r"(?:\d+(?:\.\d+)?s|\d+m(?:\s+\d+(?:\.\d+)?s)?|" r"\d+h(?:\s+\d+m)?(?:\s+\d+(?:\.\d+)?s)?)"
)
TUI_PROGRESS_PATTERN = rf"•.*\({TUI_PROGRESS_DURATION_PATTERN}\s*[•·]\s*esc to interrupt\)"

# A tool label alone is ordinary English prose too (for example, "Ran the
# focused tests successfully").  Treat it as a tool frame only when its Codex
# TUI child/output row is also present.  This is the structural distinction
# between a compact final report and an inline tool transcript.
TOOL_BULLET_LABEL_PATTERN = (
    r"^\s*•\s*(?:Ran|Read|Searched|Edited|Applied|Working|Thinking|Starting|"
    r"Analyzing|Running|Executing|Creating|Called|Explored)\b"
)
TUI_TOOL_CHILD_PATTERN = r"^\s*(?:[└├│]|\$\s)"
# Codex inserts this full-width rule between tool activity and a compact
# assistant report in the real inline capture.
TUI_SEPARATOR_PATTERN = r"^[^\S\n]*─{3,}[^\S\n]*$"
# Codex can finish a provider turn with an informational frame rather than an
# assistant bullet (for example, the policy/safety notice rendered after a
# tool block). With an idle footer this is a stable semantic final, not live
# tool progress. Keep it status-only so handoff result extraction never treats
# the notice itself as a successful worker result.
TUI_INFO_NOTICE_PREFIX_PATTERN = r"^[^\S\n]*ⓘ(?:[^\S\n]+|$)"
SIDECAR_RECONNECT_REQUIRED_TEXT = "CAO_SIDECAR_RECONNECT_REQUIRED"
CONTEXT_COMPACTED_TEXT = "Context compacted"
SIDECAR_RECONNECTED_PREFIX = "__CAO_SIDECAR_RECONNECTED_"
SIDECAR_RECONNECT_LAUNCH_PREFIX = "__CAO_SIDECAR_RECONNECT_LAUNCH_"
FRESH_RUNTIME_OUTPUT_BOUNDARY_PREFIX = "__CAO_FRESH_RUNTIME_OUTPUT_BOUNDARY_"
SIDECAR_RECONNECT_REQUIRED_BLOCK_START_PATTERN = re.compile(
    r"^[^\S\n]*(?:[└├│][^\S\n]*)?(?:Error calling tool(?:[^\S\n]|$)|"
    r"[^\n]*(?:[\"'](?:message|error)[\"'][^\S\n]*:|"
    r"\b(?:message|error)[^\S\n]*=))",
    re.IGNORECASE,
)
SIDECAR_RECONNECT_REQUIRED_BLOCK_LINES = 8
SIDECAR_RECONNECT_REQUIRED_COMPACT_PATTERN = re.compile(
    rf"(?:^[└├│]?Errorcallingtool(?:'[^']+')?:|"
    rf"(?:[\"'](?:message|error)[\"']|(?:message|error))(?::|=)[\"']?)"
    rf"{SIDECAR_RECONNECT_REQUIRED_TEXT}"
    rf"(?:\[{PROVIDER_RECONNECT_ATTEMPT_ENV}=(?P<attempt>[0-9a-f]{{32}})\])?"
    rf"(?::|\b)",
    re.IGNORECASE,
)
CONTEXT_COMPACTED_LINE_PATTERN = re.compile(
    rf"^[^\S\n]*•[^\S\n]+{CONTEXT_COMPACTED_TEXT}[^\S\n]*$",
    re.MULTILINE,
)
SIDECAR_RECONNECTED_LINE_PATTERN = re.compile(
    rf"^[^\S\n]*{SIDECAR_RECONNECTED_PREFIX}"
    rf"(?P<generation>[0-9a-f]{{{len(ACTIVE_RUNTIME_GENERATION)}}})__[^\S\n]*$",
    re.MULTILINE,
)
CODEX_SESSION_ID_PATTERN = re.compile(
    r"(?P<session>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})" r"\.jsonl$",
    re.IGNORECASE,
)
CODEX_PROVIDER_CONTENT_UNAVAILABLE = "PROVIDER_CONTENT_UNAVAILABLE"
CODEX_CONTENT_UNAVAILABLE_ERROR_CODES = frozenset({"cyber_policy"})
CODEX_OUTCOME_CURSOR_PATTERN = re.compile(
    r"^codex-jsonl-v1:(?P<device>[0-9a-f]+):(?P<inode>[0-9a-f]+):"
    r"(?P<offset>[0-9]+):(?P<complete>[01])$"
)
CODEX_OUTCOME_TRANSCRIPT_LIMIT_BYTES = 8 * 1024 * 1024
CODEX_PANE_COMMAND_PATTERN = re.compile(r"^codex(?:[.-][A-Za-z0-9_-]+)?$")
SHELL_PANE_COMMANDS = {"bash", "sh", "dash", "zsh", "fish"}
# The active Codex interaction is rendered at the bottom of capture-pane. Keep
# advisory recognition inside that bounded tail: a complete menu elsewhere is
# transcript/history, not a prompt CAO may answer.
ACTIVE_ADVISORY_TAIL_LINES = 25
# ``capture-pane -e`` retains Codex's selected-row cyan SGR styling. Requiring
# that live-widget marker prevents an assistant/user quotation of a complete
# menu from becoming an input source.
ACTIVE_CODEX_SELECTED_ROW_PATTERN = re.compile(
    r"(?:\x1b\[[0-9;]*m)*\x1b\[[0-9;]*38;5;6[0-9;]*m(?:\x1b\[[0-9;]*m)*[›>]"
)
# CAO launches an interactive shell and its normal prompt includes user, host,
# and an absolute/home working directory. Requiring that shape avoids treating
# arbitrary assistant prose ending in '$' or '#' as terminal lifecycle evidence.
CAO_SHELL_PROMPT_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+:(?:~|/)[^\s#$]*[#$]\s*$")

# Workspace trust/approval prompt shown when Codex opens a new directory
TRUST_PROMPT_PATTERN = (
    r"(?:allow Codex to work in this folder|Do you trust the contents of this directory\?)"
)
# Codex welcome banner indicating normal startup (no trust prompt)
CODEX_WELCOME_PATTERN = r"OpenAI Codex"


def _compute_tui_footer_cutoff(all_lines: list) -> int:
    """Compute the character position where the TUI footer area starts.

    Scans backward from the last line to find the TUI footer status bar
    (matches TUI_FOOTER_PATTERN), then continues upward to include any
    blank lines and the suggestion hint line (› with text) that appear
    above the status bar as part of the footer area.

    Returns the character position in the joined text (``'\\n'.join(all_lines)``)
    where the footer starts. Returns ``len('\\n'.join(all_lines))`` if no
    footer is found.
    """
    n = len(all_lines)
    footer_start_idx = n

    # Find the status bar line (last TUI_FOOTER_PATTERN match in the bottom area)
    for i in range(n - 1, max(n - IDLE_PROMPT_TAIL_LINES - 1, -1), -1):
        if re.search(TUI_FOOTER_PATTERN, all_lines[i]):
            footer_start_idx = i
            break

    if footer_start_idx == n:
        return len("\n".join(all_lines))

    # Scan upward from the status bar to include blank lines and the
    # suggestion hint (› with text) that are part of the TUI footer chrome.
    for j in range(footer_start_idx - 1, max(footer_start_idx - 4, -1), -1):
        line = all_lines[j]
        if not line.strip():
            footer_start_idx = j
        elif re.match(rf"\s*{IDLE_PROMPT_PATTERN}", line):
            footer_start_idx = j
            break
        else:
            break

    return len("\n".join(all_lines[:footer_start_idx]))


def _find_active_footer_start(all_lines: list[str]) -> Optional[int]:
    """Return the first row of the current bottom Codex footer, if present.

    ``capture-pane`` includes scrollback, so only chrome in the normalized
    bottom tail is current.  The v0.110/v0.111 status-bar forms include their
    suggestion row and intervening blanks; a bare strict idle prompt is the
    footer when no status bar is rendered.
    """
    tail_start = max(len(all_lines) - IDLE_PROMPT_TAIL_LINES, 0)
    footer_start_idx: Optional[int] = None

    for index in range(len(all_lines) - 1, tail_start - 1, -1):
        if re.search(TUI_FOOTER_PATTERN, all_lines[index]):
            footer_start_idx = index
            break

    if footer_start_idx is not None:
        for index in range(footer_start_idx - 1, max(footer_start_idx - 4, -1), -1):
            line = all_lines[index]
            if not line.strip():
                footer_start_idx = index
            elif re.match(rf"\s*{IDLE_PROMPT_PATTERN}", line):
                footer_start_idx = index
                break
            else:
                break
        return footer_start_idx

    for index in range(len(all_lines) - 1, tail_start - 1, -1):
        if re.fullmatch(IDLE_PROMPT_STRICT_PATTERN, all_lines[index], re.IGNORECASE):
            return index
    return None


def _has_current_tui_progress(all_lines: list[str], footer_start_idx: int) -> bool:
    """Whether the sole current body row before a footer is a live spinner."""
    for index in range(footer_start_idx - 1, -1, -1):
        row = all_lines[index]
        if row.strip():
            return bool(re.fullmatch(TUI_PROGRESS_PATTERN, row.strip(), re.IGNORECASE))
    return False


def _toml_scalar(value: Any) -> str:
    """Serialize a scalar as a TOML value for a Codex -c override."""
    if isinstance(value, bool):
        return "true" if value else "false"

    if isinstance(value, (int, float)):
        return str(value)

    if not isinstance(value, str):
        raise TypeError(
            "codexConfig values must be scalars "
            "(str, bool, int, or float); "
            f"got {type(value).__name__}"
        )

    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\t", "\\t")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )
    return f'"{escaped}"'


_CODEX_CONFIG_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


def _toml_override(key: str, value: Any) -> str:
    """Build one validated Codex key=<TOML scalar> override."""
    if not isinstance(key, str) or not _CODEX_CONFIG_KEY_PATTERN.fullmatch(key):
        raise ValueError(f"Invalid codexConfig key: {key!r}")
    return f"{key}={_toml_scalar(value)}"


def _codex_wrapper_preapplies_hook_trust() -> bool:
    """Recognize a narrow exec wrapper that already supplies hook trust.

    Ordinary Codex installations receive the flag from this provider. Some
    managed hosts expose a same-purpose shell wrapper, while Codex rejects the
    option when repeated. Only a small, readable ``exec <codex> <flag> "$@"``
    wrapper is accepted as equivalent; uncertainty keeps the provider flag.
    """
    executable = shutil.which("codex")
    if not executable:
        return False
    try:
        wrapper = Path(executable).resolve(strict=True)
        wrapper_stat = wrapper.stat()
        if not stat.S_ISREG(wrapper_stat.st_mode) or wrapper_stat.st_size > 16 * 1024:
            return False
        content = wrapper.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    if not content.startswith("#!"):
        return False
    logical_lines = content.replace("\\\n", " ").splitlines()
    for line in logical_lines:
        try:
            parts = shlex.split(line, comments=True)
        except ValueError:
            continue
        if (
            len(parts) >= 4
            and parts[0] == "exec"
            and Path(parts[1]).name.startswith("codex")
            and "--dangerously-bypass-hook-trust" in parts[2:-1]
            and parts[-1] == "$@"
        ):
            return True
    return False


def _strip_terminal_noise(output: str) -> str:
    """Remove ANSI/control presentation noise while retaining text layout."""
    output = re.sub(ANSI_CONTROL_PATTERN, "", output)
    # Preserve newlines because they delimit assistant blocks, but discard the
    # remaining C0 controls (including carriage returns used for cursor motion).
    output = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", output)
    return output


def _process_start_ticks(process_id: int, proc_root: Path = Path("/proc")) -> Optional[int]:
    """Read Linux process start ticks without trusting a reusable PID alone."""
    if process_id <= 1:
        return None
    try:
        stat_text = (proc_root / str(process_id) / "stat").read_text(encoding="utf-8")
        suffix = stat_text[stat_text.rfind(")") + 2 :].split()
        start_ticks = int(suffix[19])
    except (OSError, ValueError, IndexError):
        return None
    return start_ticks if start_ticks > 0 else None


def _process_job_identity(
    process_id: int, proc_root: Path = Path("/proc")
) -> Optional[dict[str, int]]:
    """Read the job-control fields that bind a pane to its foreground child."""
    if process_id <= 1:
        return None
    try:
        stat_text = (proc_root / str(process_id) / "stat").read_text(encoding="utf-8")
        suffix = stat_text[stat_text.rfind(")") + 2 :].split()
        values = {
            "parent_process_id": int(suffix[1]),
            "process_group_id": int(suffix[2]),
            "foreground_process_group_id": int(suffix[5]),
            "start_ticks": int(suffix[19]),
        }
    except (OSError, ValueError, IndexError):
        return None
    return values if all(value >= 0 for value in values.values()) else None


def _root_rollout_identity(path: Path, working_directory: Path) -> Optional[str]:
    """Validate the bounded root ``session_meta`` row for one rollout."""
    try:
        with path.open("rb") as handle:
            raw = handle.readline(4 * 1024 * 1024 + 1)
        if not raw.endswith(b"\n") or len(raw) > 4 * 1024 * 1024:
            return None
        row = json.loads(raw)
        if not isinstance(row, dict):
            return None
        payload = row.get("payload")
        session_id = (
            payload.get("id") or payload.get("session_id") if isinstance(payload, dict) else None
        )
        cwd = payload.get("cwd") if isinstance(payload, dict) else None
        match = CODEX_SESSION_ID_PATTERN.search(path.name)
        if (
            row.get("type") != "session_meta"
            or not isinstance(payload, dict)
            or payload.get("source") != "cli"
            or not isinstance(session_id, str)
            or match is None
            or match.group("session").lower() != session_id.lower()
            or not isinstance(cwd, str)
            or not os.path.isabs(cwd)
            or Path(cwd).resolve(strict=False) != working_directory
        ):
            return None
        return session_id.lower()
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _rollout_path_for_identity(
    provider_session_id: str,
    working_directory: Path,
    *,
    codex_home: Optional[Path] = None,
) -> Optional[Path]:
    """Resolve one exact root rollout from its persisted provider identity.

    Recency and display names are never authority. A missing or duplicated
    identity is intentionally unclassifiable rather than guessed.
    """
    identity_match = CODEX_SESSION_ID_PATTERN.fullmatch(f"{provider_session_id}.jsonl")
    if identity_match is None:
        return None
    normalized_identity = identity_match.group("session").lower()
    root = (
        codex_home
        if codex_home is not None
        else Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    )
    try:
        session_root = (root / "sessions").resolve(strict=True)
    except (FileNotFoundError, OSError):
        return None
    candidates: set[Path] = set()
    try:
        matching_paths = session_root.glob(f"**/*{normalized_identity}.jsonl")
        for candidate in matching_paths:
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(session_root)
            except (FileNotFoundError, OSError, ValueError):
                continue
            if _root_rollout_identity(resolved, working_directory) == normalized_identity:
                candidates.add(resolved)
    except OSError:
        return None
    return next(iter(candidates)) if len(candidates) == 1 else None


def _codex_outcome_cursor(path: Path) -> Optional[str]:
    """Capture an exact regular-file identity and byte offset."""
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            return None
        complete_row = file_stat.st_size == 0
        if file_stat.st_size:
            os.lseek(descriptor, file_stat.st_size - 1, os.SEEK_SET)
            complete_row = os.read(descriptor, 1) == b"\n"
        return (
            f"codex-jsonl-v1:{file_stat.st_dev:x}:{file_stat.st_ino:x}:"
            f"{file_stat.st_size}:{int(complete_row)}"
        )
    except OSError:
        return None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _codex_turn_execution_active(path: Path) -> Optional[bool]:
    """Return the latest provider-native task boundary for one exact rollout.

    The inline TUI can retain an idle footer while a tool-heavy turn is still
    running, especially after the CAO service reconstructs its provider
    object.  The rollout's ordered ``task_started`` / ``task_complete`` events
    are the narrower authority for whether another input may be transported.
    Malformed or unclassifiable evidence fails closed with ``None``.
    """
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            return None
        latest: Optional[bool] = None
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            for raw in handle:
                if len(raw) > 4 * 1024 * 1024 or not raw.endswith(b"\n"):
                    return None
                try:
                    row = json.loads(raw)
                except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
                    return None
                if not isinstance(row, dict):
                    return None
                payload = row.get("payload")
                event_type = payload.get("type") if isinstance(payload, dict) else None
                if row.get("type") != "event_msg" or event_type not in {
                    "task_started",
                    "task_complete",
                }:
                    continue
                latest = event_type == "task_started"
        return latest if latest is not None else False
    except OSError:
        return None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _latest_completed_codex_response(path: Path) -> Optional[str]:
    """Read the latest provider-native completed response from a bounded suffix."""
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        source = os.fstat(descriptor)
        if not stat.S_ISREG(source.st_mode):
            return None
        start = max(0, source.st_size - CODEX_LAST_RESPONSE_SCAN_MAX_BYTES)
        raw = os.pread(descriptor, source.st_size - start, start)
    except OSError:
        return None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if start:
        _partial, separator, raw = raw.partition(b"\n")
        if not separator:
            return None
    for line in reversed(raw.splitlines()):
        if len(line) > 4 * 1024 * 1024:
            continue
        try:
            row = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        payload = row.get("payload") if isinstance(row, dict) else None
        if (
            row.get("type") == "event_msg"
            and isinstance(payload, dict)
            and payload.get("type") == "task_complete"
            and isinstance(payload.get("last_agent_message"), str)
            and payload["last_agent_message"].strip()
        ):
            return payload["last_agent_message"]
    return None


def _latest_structured_codex_outcome(
    path: Path, after_cursor: str
) -> Optional[ProviderTurnOutcome]:
    """Classify one settled turn after its exact transport boundary.

    Only a provider-native ``task_complete.error.codex_error_info`` value is
    authoritative. Rendered interstitial prose and any unavailable response
    body are deliberately ignored.
    """
    cursor_match = CODEX_OUTCOME_CURSOR_PATTERN.fullmatch(after_cursor)
    if cursor_match is None:
        return None
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            return None
        expected_identity = (
            int(cursor_match.group("device"), 16),
            int(cursor_match.group("inode"), 16),
        )
        if (file_stat.st_dev, file_stat.st_ino) != expected_identity:
            return None
        cursor_offset = int(cursor_match.group("offset"))
        if cursor_offset < 0 or cursor_offset > file_stat.st_size:
            return None
        start = max(cursor_offset, file_stat.st_size - CODEX_OUTCOME_TRANSCRIPT_LIMIT_BYTES)
        os.lseek(descriptor, start, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = file_stat.st_size - start
        while remaining > 0:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    except OSError:
        return None
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    if start > cursor_offset or cursor_match.group("complete") == "0":
        _discarded, separator, raw = raw.partition(b"\n")
        if not separator:
            return None
    turn_started = False
    latest_outcome: Optional[str] = None
    for line in raw.splitlines():
        try:
            row = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(row, dict):
            return None
        if row.get("type") != "event_msg":
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            return None
        event_type = payload.get("type")
        if not isinstance(event_type, str):
            return None
        if event_type == "task_started":
            turn_started = True
            latest_outcome = None
            continue
        if event_type != "task_complete":
            continue
        if not turn_started:
            continue
        error = payload.get("error")
        if error is not None and not isinstance(error, dict):
            return None
        detail_code = error.get("codex_error_info") if isinstance(error, dict) else None
        if detail_code is not None and not isinstance(detail_code, str):
            return None
        latest_outcome = detail_code if isinstance(detail_code, str) else None
        turn_started = False

    if latest_outcome in CODEX_CONTENT_UNAVAILABLE_ERROR_CODES:
        return ProviderTurnOutcome(
            code=CODEX_PROVIDER_CONTENT_UNAVAILABLE,
            detail_code=latest_outcome,
        )
    return None


def _durable_reconnect_output_boundary(terminal_id: str) -> Optional[dict[str, Any]]:
    """Load the DB-authorized byte boundary without creating a provider import cycle."""
    from cli_agent_orchestrator.clients.database import (
        get_latest_workflow_provider_reconnect_output_boundary,
    )

    return get_latest_workflow_provider_reconnect_output_boundary(terminal_id)


def _has_runtime_reconnect_signal(output: str, attempt_token: Optional[str]) -> bool:
    """Recognize protocol errors and structured semantic failures for one runtime."""
    candidate_attempts: list[Optional[str]] = []
    lines = output.splitlines()
    for index, line in enumerate(lines):
        if not SIDECAR_RECONNECT_REQUIRED_BLOCK_START_PATTERN.match(line):
            continue
        block_lines: list[str] = []
        for candidate_line in lines[index : index + SIDECAR_RECONNECT_REQUIRED_BLOCK_LINES]:
            if block_lines and not candidate_line.strip():
                break
            block_lines.append(candidate_line)
        compact_block = re.sub(r"\s+", "", "".join(block_lines))
        compact_match = SIDECAR_RECONNECT_REQUIRED_COMPACT_PATTERN.search(compact_block)
        if compact_match is not None:
            candidate_attempts.append(compact_match.group("attempt"))
    if attempt_token is None:
        return bool(candidate_attempts)
    return attempt_token in candidate_attempts


def _normalize_terminal_suffix_blank_rows(output: str) -> str:
    """Remove only trailing blank capture rows made of horizontal whitespace.

    ``tmux capture-pane`` can append an arbitrary number of empty rendered
    rows after Codex's footer.  Those rows are presentation-only, but a fixed
    bottom-tail footer search would otherwise stop seeing the footer after five
    rows.  Keep every interior row and all non-horizontal whitespace intact:
    response and V1 layout remain parser-owned semantics.
    """
    return re.sub(r"(?:\n[^\S\r\n]*)+\Z", "", output)


def _normalized_tui_text(output: str) -> str:
    """Collapse TUI visual wrapping without discarding menu semantics."""
    return " ".join(output.split())


def _selected_canonical_menu_option(match: re.Match, prefix: str) -> Optional[int]:
    """Return the one selected canonical option, rejecting absent/ambiguous rows."""
    selected = [option for option in (1, 2, 3) if match.group(f"{prefix}_option_{option}_selected")]
    return selected[0] if len(selected) == 1 else None


def _active_bottommost_advisory_match(
    output: str, clean_output: str, pattern: re.Pattern
) -> Optional[re.Match]:
    """Return a canonical advisory only when it is the current bottommost UI.

    ``capture-pane -S`` includes scrollback as well as the live pane. A full
    canonical menu is actionable only when it consumes the final non-whitespace
    text of a bounded bottom tail *and* carries the current Codex selected-row
    rendering. This makes historical/quoted menus inert whenever any newer
    interaction is visible, and prevents plain quoted menu text from supplying
    keystrokes even when it happens to be bottommost.
    """
    active_tail_lines = clean_output.splitlines()[-ACTIVE_ADVISORY_TAIL_LINES:]
    active_tail = "\n".join(active_tail_lines)
    normalized_tail = _normalized_tui_text(active_tail)
    match = pattern.search(normalized_tail)
    if not match or normalized_tail[match.end() :].strip():
        return None
    raw_active_tail = "\n".join(output.splitlines()[-len(active_tail_lines) :])
    if not ACTIVE_CODEX_SELECTED_ROW_PATTERN.search(raw_active_tail):
        return None
    return match


def _semantic_completion_candidate(response_text: str) -> str:
    """Return a stable representation of post-input assistant text."""
    return "\n".join(line.rstrip() for line in response_text.strip().splitlines())


def _is_structural_tool_block(response_text: str, markers: list, index: int) -> bool:
    """Whether one assistant bullet has Codex's tool-output shape.

    Codex renders tool invocations as a labelled bullet followed by an
    indented tree/output row (normally ``└``).  The label is deliberately not
    enough: it may be the first word of a human final report.
    """
    marker = markers[index]
    next_start = markers[index + 1].start() if index + 1 < len(markers) else len(response_text)
    block = response_text[marker.start() : next_start]
    lines = block.splitlines()
    return bool(
        lines
        and re.match(TOOL_BULLET_LABEL_PATTERN, lines[0])
        and any(re.match(TUI_TOOL_CHILD_PATTERN, line) for line in lines[1:])
    )


def _select_final_assistant_block(response_text: str) -> str:
    """Drop leading Codex tool/progress blocks when a later final block exists."""
    markers = list(
        re.finditer(ASSISTANT_PREFIX_PATTERN, response_text, re.IGNORECASE | re.MULTILINE)
    )
    if not markers:
        return response_text.strip()

    # Label-style captures have a distinct assistant line for each message, so
    # the final block is simply the last one.  Real inline Codex reports often
    # use several normal bullets in a single final report; preserve all of them
    # unless a structural Codex tool block separates them from the report.
    if not response_text[markers[0].start() : markers[0].end()].lstrip().startswith("•"):
        return response_text[markers[-1].start() :].strip()

    tool_marker_indexes = [
        index
        for index in range(len(markers))
        if _is_structural_tool_block(response_text, markers, index)
    ]
    if tool_marker_indexes:
        last_tool_marker = tool_marker_indexes[-1]
        next_marker = last_tool_marker + 1
        if next_marker < len(markers):
            final_block = response_text[markers[next_marker].start() :]
            # The F7 capture has a Codex separator after the last structural
            # tool block. Its following bullet is presentation chrome rather
            # than a report bullet; strip only that UI marker. Other final
            # multi-bullet reports retain their first and later bullets.
            between_blocks = response_text[
                markers[last_tool_marker].end() : markers[next_marker].start()
            ]
            if re.search(TUI_SEPARATOR_PATTERN, between_blocks, re.MULTILINE):
                final_block = re.sub(r"^[^\S\n]*•[^\S\n]*", "", final_block, count=1)
            return final_block.strip()

    return response_text.strip()


class ProviderError(Exception):
    """Exception raised for provider-specific errors."""

    pass


class CodexStartupError(ProviderError):
    """Codex failed before reaching an initial ready state.

    ``startup_evidence`` is a bounded terminal snapshot.  The terminal service
    starts pipe-pane before provider initialization, so the full startup stream
    is also retained in the terminal log when this exception reaches it.
    """

    def __init__(self, message: str, startup_evidence: str):
        super().__init__(message)
        self.startup_evidence = startup_evidence


class CodexStartupNoReadyError(CodexStartupError):
    """Codex stayed alive but never rendered a ready state; retry once safely."""

    pass


class CodexReconnectEarlyExitError(CodexStartupError):
    """The exact resumed process exited before its sidecar became ready."""

    reconnect_outcome_code = "process_exited_before_runtime_ready"


class CodexReconnectNoReadyError(CodexStartupNoReadyError):
    """The exact resumed process never established the runtime handshake."""

    reconnect_outcome_code = "runtime_readiness_timeout"


class CodexProvider(BaseProvider):
    """Provider for Codex CLI tool integration."""

    def __init__(
        self,
        terminal_id: str,
        session_name: str,
        window_name: str,
        agent_profile: Optional[str] = None,
        allowed_tools: Optional[list] = None,
        skill_prompt: Optional[str] = None,
        resolved_profile: Any | None = None,
        structured_owner_authorized: bool = False,
    ):
        """Initialize provider state."""
        super().__init__(terminal_id, session_name, window_name, allowed_tools, skill_prompt)
        self._initialized = False
        self._agent_profile = agent_profile
        self._resolved_profile = resolved_profile
        self._structured_owner_authorized = structured_owner_authorized
        # A visible idle footer is not sufficient proof that Codex has stopped
        # producing a turn: it remains on screen while tool output changes.
        # Keep this state on the provider instance because the service/API poll
        # path reuses that instance for every status request.
        self._completion_candidate: Optional[str] = None
        self._completion_candidate_polls = 0
        self._completion_candidate_user: Optional[str] = None
        # Long inline transcripts can scroll the submitted user line outside
        # the fixed capture tail. Set only after CAO actually sends input.
        self._input_received = False
        # Avoid replaying navigation if capture-pane still shows the exact menu
        # after Enter was sent. A changed selected row has a new fingerprint and
        # may still be safely completed relative to its current row.
        self._handled_advisory_fingerprints: dict[str, str] = {}
        self._startup_evidence = ""
        self._startup_exit_marker: Optional[str] = None
        self._startup_attempt = 0
        self._process_exited = False
        self._runtime_sidecar_reconnect_pending = False
        self._reconnect_log_offset: Optional[int] = None
        self._reconnect_log_identity: Optional[tuple[int, int]] = None
        self._reconnect_runtime_attempt_token: Optional[str] = None
        self._reconnect_output_boundary: Optional[tuple[str, int, int, int]] = None

    def _reset_completion_candidate(self) -> None:
        """Forget a pending completion candidate after terminal activity."""
        self._completion_candidate = None
        self._completion_candidate_polls = 0
        self._completion_candidate_user = None

    def mark_input_received(self) -> None:
        """Reset completion debounce immediately when CAO submits new input."""
        self._reset_completion_candidate()
        self._handled_advisory_fingerprints.clear()
        self._input_received = True

    def get_turn_outcome(
        self,
        *,
        provider_session_id: Optional[str] = None,
        after_cursor: Optional[str] = None,
    ) -> Optional[ProviderTurnOutcome]:
        """Return one exact-session policy outcome after its send boundary."""
        if not provider_session_id or not after_cursor:
            return None
        working_directory_value = tmux_client.get_pane_working_directory(
            self.session_name, self.window_name
        )
        if not working_directory_value or not os.path.isabs(working_directory_value):
            return None
        rollout_path = _rollout_path_for_identity(
            provider_session_id,
            Path(working_directory_value).resolve(strict=False),
        )
        return (
            _latest_structured_codex_outcome(rollout_path, after_cursor) if rollout_path else None
        )

    def capture_turn_outcome_cursor(
        self, *, provider_session_id: Optional[str] = None
    ) -> Optional[str]:
        """Capture the exact rollout boundary before provider transport."""
        if not provider_session_id:
            return None
        working_directory_value = tmux_client.get_pane_working_directory(
            self.session_name, self.window_name
        )
        if not working_directory_value or not os.path.isabs(working_directory_value):
            return None
        rollout_path = _rollout_path_for_identity(
            provider_session_id,
            Path(working_directory_value).resolve(strict=False),
        )
        return _codex_outcome_cursor(rollout_path) if rollout_path else None

    def turn_execution_active(self, *, provider_session_id: Optional[str] = None) -> Optional[bool]:
        """Observe whether the exact persisted Codex session has a live task."""
        if not provider_session_id:
            return None
        working_directory_value = tmux_client.get_pane_working_directory(
            self.session_name, self.window_name
        )
        if not working_directory_value or not os.path.isabs(working_directory_value):
            return None
        rollout_path = _rollout_path_for_identity(
            provider_session_id,
            Path(working_directory_value).resolve(strict=False),
        )
        return _codex_turn_execution_active(rollout_path) if rollout_path else None

    def turn_outcome_cursor_required(self) -> bool:
        """Require exact event authority before every logical-turn send."""
        return True

    def defer_turn_outcome_cursor_to_session_start(self) -> bool:
        """Permit only the fresh session's authenticated first-turn handshake.

        The terminal service still reserves the exact runtime generation and
        workflow turn before transport. The synchronous SessionStart hook must
        bind the real provider cursor before Codex begins that model request.
        """
        return True

    def runtime_sidecar_reconnect_required(self) -> bool:
        """Return the signal cached by the normal provider status capture."""
        return self._runtime_sidecar_reconnect_pending

    def _refresh_runtime_reconnect_signal(self, fallback_output: str) -> None:
        """Consume only nonce-bound output from the latest proven runtime.

        A resumed Codex TUI redraws its transcript into tmux.  The pipe-pane
        log therefore contains replay too. The byte offset accepted after the
        nonce-bound MCP initialize handshake is persisted in the attempt row;
        transcript text can imitate the presentation marker but cannot move
        this DB-owned boundary. After that offset, even later repaint is
        ignored unless the newly launched sidecar tags its result with the
        same attempt nonce. Before the first durable boundary, the complete
        log retains the original backward-compatible reconnect signal.
        """
        trusted_boundary: Optional[tuple[str, int, int, int]] = None
        path = TERMINAL_LOG_DIR / f"{self.terminal_id}.log"
        descriptor = -1
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode):
                raise OSError("terminal log is not a regular file")
            try:
                boundary = _durable_reconnect_output_boundary(self.terminal_id)
            except Exception:
                logger.warning(
                    "Codex reconnect output boundary is unavailable for %s",
                    self.terminal_id,
                    exc_info=True,
                )
                self._runtime_sidecar_reconnect_pending = False
                return
            if boundary is not None:
                attempt_token = boundary.get("attempt_token")
                device = boundary.get("output_log_device")
                inode = boundary.get("output_log_inode")
                offset = boundary.get("output_log_offset")
                if (
                    not isinstance(attempt_token, str)
                    or not re.fullmatch(r"[0-9a-f]{32}", attempt_token)
                    or not isinstance(device, int)
                    or device < 0
                    or not isinstance(inode, int)
                    or inode <= 0
                    or not isinstance(offset, int)
                    or offset < 0
                ):
                    self._runtime_sidecar_reconnect_pending = False
                    return
                trusted_boundary = (attempt_token, device, inode, offset)
            if trusted_boundary != self._reconnect_output_boundary:
                self._reconnect_output_boundary = trusted_boundary
                self._runtime_sidecar_reconnect_pending = False
                if trusted_boundary is None:
                    self._reconnect_runtime_attempt_token = None
                    self._reconnect_log_identity = None
                    self._reconnect_log_offset = None
                else:
                    attempt_token, device, inode, offset = trusted_boundary
                    self._reconnect_runtime_attempt_token = attempt_token
                    self._reconnect_log_identity = (device, inode)
                    self._reconnect_log_offset = offset
            identity = (file_stat.st_dev, file_stat.st_ino)
            if trusted_boundary is not None and identity != trusted_boundary[1:3]:
                # Log replacement loses byte-order authority. Do not interpret
                # retained pane transcript as fresh output.
                self._runtime_sidecar_reconnect_pending = False
                self._reconnect_log_identity = identity
                self._reconnect_log_offset = file_stat.st_size
                return
            if trusted_boundary is None and self._reconnect_log_identity != identity:
                self._reconnect_log_identity = identity
                self._reconnect_log_offset = 0
                self._runtime_sidecar_reconnect_pending = False
                self._reconnect_runtime_attempt_token = None
            if self._reconnect_log_offset is None:
                self._reconnect_log_offset = (
                    trusted_boundary[3] if trusted_boundary is not None else 0
                )
            if file_stat.st_size < self._reconnect_log_offset:
                # Truncation destroys the prior ordering proof. Fail closed.
                self._reconnect_log_offset = file_stat.st_size
                self._runtime_sidecar_reconnect_pending = False
                return
            # Retain a small overlap so a pipe-pane write observed between two
            # polls cannot split the reconnect or boundary line across reads.
            minimum_offset = trusted_boundary[3] if trusted_boundary is not None else 0
            read_from = max(minimum_offset, self._reconnect_log_offset - 4096)
            os.lseek(descriptor, read_from, os.SEEK_SET)
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            self._reconnect_log_offset = os.lseek(descriptor, 0, os.SEEK_CUR)
            if not chunks:
                return
            new_output = _strip_terminal_noise(b"".join(chunks).decode("utf-8", "replace"))
        except OSError:
            clean_fallback = _strip_terminal_noise(fallback_output)
            if _has_runtime_reconnect_signal(clean_fallback, self._reconnect_runtime_attempt_token):
                self._runtime_sidecar_reconnect_pending = True
            return
        finally:
            if descriptor >= 0:
                os.close(descriptor)

        if _has_runtime_reconnect_signal(new_output, self._reconnect_runtime_attempt_token):
            self._runtime_sidecar_reconnect_pending = True

    def _publish_fresh_runtime_output_boundary(
        self,
        attempt_token: str,
        record_output_boundary: Callable[[int, int, int], bool],
    ) -> None:
        """Append evidence and persist its unforgeable private-log byte offset."""
        if not re.fullmatch(r"[0-9a-f]{32}", attempt_token):
            raise ProviderError("Codex reconnect attempt token is invalid")
        existing = _durable_reconnect_output_boundary(self.terminal_id)
        if existing is not None and existing.get("attempt_token") == attempt_token:
            device = existing.get("output_log_device")
            inode = existing.get("output_log_inode")
            offset = existing.get("output_log_offset")
            if isinstance(device, int) and isinstance(inode, int) and isinstance(offset, int):
                self._reconnect_output_boundary = (
                    attempt_token,
                    device,
                    inode,
                    offset,
                )
                self._reconnect_runtime_attempt_token = attempt_token
                self._reconnect_log_identity = (device, inode)
                self._reconnect_log_offset = offset
                self._runtime_sidecar_reconnect_pending = False
                return
        path = TERMINAL_LOG_DIR / f"{self.terminal_id}.log"
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_APPEND | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        try:
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode):
                raise ProviderError("Codex terminal log is not a regular file")
            marker = f"\n{FRESH_RUNTIME_OUTPUT_BOUNDARY_PREFIX}{attempt_token}__\n".encode()
            written = os.write(descriptor, marker)
            if written != len(marker):
                raise ProviderError("Codex runtime output boundary write was incomplete")
            # This descriptor's file position advances to the exact end of
            # our O_APPEND write and is unaffected by appends through other
            # descriptors. Capture it before fsync/fstat can interleave with
            # fresh sidecar output; total file size is not our boundary.
            boundary_offset = os.lseek(descriptor, 0, os.SEEK_CUR)
            os.fsync(descriptor)
            file_stat = os.fstat(descriptor)
            if boundary_offset > file_stat.st_size:
                raise ProviderError("Codex runtime output boundary exceeds terminal log size")
            if not record_output_boundary(
                file_stat.st_dev,
                file_stat.st_ino,
                boundary_offset,
            ):
                raise ProviderError("Codex runtime output boundary lost durable admission")
        finally:
            os.close(descriptor)
        # Re-scan once so this process and a reconstructed provider use the
        # same file-owned boundary semantics.
        self._reconnect_output_boundary = (
            attempt_token,
            file_stat.st_dev,
            file_stat.st_ino,
            boundary_offset,
        )
        self._reconnect_log_identity = (file_stat.st_dev, file_stat.st_ino)
        self._reconnect_log_offset = boundary_offset
        self._reconnect_runtime_attempt_token = attempt_token
        self._runtime_sidecar_reconnect_pending = False

    @property
    def runtime_sidecar_reconnect_input(self) -> str:
        """Compatibility input for runtimes without exact process resume support."""
        return "/compact"

    def runtime_sidecar_resume_identity(
        self,
        proc_root: Path = Path("/proc"),
        expected_identity: Optional[str] = None,
        expected_rollout_path: Path | str | None = None,
    ) -> str:
        """Capture or verify the exact foreground Codex root conversation.

        The initial ``SessionStart`` hook supplies both the provider-native
        identity and transcript path before Codex dispatches its first model
        request. Reconnect supplies the already-persisted identity and merely
        proves that the exact foreground runtime still owns its writable root
        rollout. Other Codex processes and read-only rollout descriptors are
        irrelevant authority.
        """
        if expected_rollout_path is not None and expected_identity is None:
            raise ProviderError("Codex resume identity path has no expected identity")
        pane_command = tmux_client.get_pane_current_command(self.session_name, self.window_name)
        if pane_command is None or not CODEX_PANE_COMMAND_PATTERN.fullmatch(pane_command):
            raise ProviderError("Codex resume identity is unavailable from an inactive pane")
        pane_pid = tmux_client.get_pane_process_id(self.session_name, self.window_name)
        if pane_pid is None or pane_pid <= 0:
            raise ProviderError("Codex resume identity is unavailable from the pane process")
        pane_job = _process_job_identity(pane_pid, proc_root)
        provider_pid = pane_job and pane_job["foreground_process_group_id"]
        provider_job = (
            _process_job_identity(provider_pid, proc_root)
            if isinstance(provider_pid, int) and provider_pid > 1
            else None
        )
        if (
            pane_job is None
            or provider_job is None
            or provider_pid == pane_pid
            or provider_job["parent_process_id"] != pane_pid
            or provider_job["process_group_id"] != provider_pid
        ):
            raise ProviderError("Codex resume identity process inventory is uncertain")

        working_directory_value = tmux_client.get_pane_working_directory(
            self.session_name, self.window_name
        )
        if not working_directory_value or not os.path.isabs(working_directory_value):
            raise ProviderError("Codex resume identity working directory is uncertain")
        working_directory = Path(working_directory_value).resolve(strict=False)

        codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).resolve()
        session_root = (codex_home / "sessions").resolve()
        expected_path: Path | None = None
        if expected_rollout_path is not None:
            try:
                candidate = Path(expected_rollout_path)
                if not candidate.is_absolute():
                    raise ValueError
                expected_path = candidate.resolve(strict=True)
                expected_path.relative_to(session_root)
            except (FileNotFoundError, OSError, ValueError) as exc:
                raise ProviderError(
                    "Persisted Codex resume identity is stale or belongs elsewhere"
                ) from exc
        identities: dict[str, set[Path]] = {}
        try:
            descriptors = list((proc_root / str(provider_pid) / "fd").iterdir())
        except OSError as exc:
            raise ProviderError("Codex resume identity descriptor inventory is uncertain") from exc
        for descriptor in descriptors:
            try:
                target_value = os.readlink(descriptor)
                if target_value.endswith(" (deleted)"):
                    continue
                target = Path(target_value).resolve(strict=True)
                target.relative_to(session_root)
            except (FileNotFoundError, OSError, ValueError):
                continue
            match = CODEX_SESSION_ID_PATTERN.search(target.name)
            if match is None:
                continue
            try:
                fdinfo = (proc_root / str(provider_pid) / "fdinfo" / descriptor.name).read_text(
                    encoding="ascii"
                )
                flags_line = next(
                    line.partition(":")[2].strip()
                    for line in fdinfo.splitlines()
                    if line.startswith("flags:")
                )
                flags = int(flags_line, 8)
            except (OSError, StopIteration, ValueError) as exc:
                raise ProviderError(
                    "Codex resume identity descriptor inventory is uncertain"
                ) from exc
            if (flags & os.O_ACCMODE) not in {os.O_WRONLY, os.O_RDWR}:
                continue
            identity = _root_rollout_identity(target, working_directory)
            if identity is not None:
                identities.setdefault(identity, set()).add(target)

        if expected_identity is not None:
            expected_match = CODEX_SESSION_ID_PATTERN.fullmatch(f"{expected_identity}.jsonl")
            matching_paths = identities.get(expected_identity.lower(), set())
            if (
                expected_match is None
                or expected_match.group("session").lower() != expected_identity.lower()
                or len(matching_paths) != 1
                or (expected_path is not None and matching_paths != {expected_path})
            ):
                raise ProviderError("Persisted Codex resume identity is stale or belongs elsewhere")
            return expected_identity.lower()
        if len(identities) != 1:
            raise ProviderError("Codex resume identity is ambiguous")
        identity, paths = next(iter(identities.items()))
        if len(paths) != 1:
            raise ProviderError("Codex resume identity is ambiguous")
        return identity

    def _registered_sidecar_is_live(
        self, registration: object, proc_root: Path = Path("/proc")
    ) -> bool:
        """Validate the registered sidecar by generation, PID, and start ticks."""
        if not isinstance(registration, dict):
            return False
        process_id = registration.get("sidecar_process_id")
        start_ticks = registration.get("sidecar_process_start_ticks")
        return bool(
            registration.get("runtime_generation") == ACTIVE_RUNTIME_GENERATION
            and isinstance(process_id, int)
            and isinstance(start_ticks, int)
            and _process_start_ticks(process_id, proc_root) == start_ticks
        )

    def _wait_for_reconnected_runtime(
        self,
        attempt_token: str,
        runtime_ready: Callable[[], Optional[dict[str, Any]]],
        timeout: float = 60.0,
        proc_root: Path = Path("/proc"),
    ) -> None:
        """Wait for a nonce-bound sidecar plus a stable resumed Codex TUI."""
        if self._startup_exit_marker != (
            f"__CAO_CODEX_RECONNECT_EXIT_{self.terminal_id}_{attempt_token}__"
        ):
            raise ProviderError("Codex reconnect attempt marker is not current")
        deadline = time.monotonic() + timeout
        ready_fingerprint: Optional[str] = None
        ready_polls = 0
        while time.monotonic() < deadline:
            output = tmux_client.get_history(
                self.session_name,
                self.window_name,
                tail_lines=SIDECAR_RECONNECT_HISTORY_LINES,
            )
            clean_output = _strip_terminal_noise(output) if output else ""
            self._startup_evidence = clean_output[-STARTUP_EVIDENCE_LIMIT:]
            if clean_output:
                exit_code = self._startup_exit_code(clean_output)
                if exit_code is not None:
                    raise CodexReconnectEarlyExitError(
                        f"Codex reconnect exited before runtime readiness with status {exit_code}",
                        self._startup_evidence,
                    )
                # A resumed TUI can repaint arbitrary historical ``Error:``
                # lines. Only the nonce-bound shell exit marker above belongs
                # unambiguously to this reconnect attempt; generic startup
                # text cannot be used as fresh reconnect evidence.
            registration = runtime_ready()
            pane_command = tmux_client.get_pane_current_command(self.session_name, self.window_name)
            normalized = _normalize_terminal_suffix_blank_rows(clean_output)
            lines = normalized.splitlines()
            footer_start = _find_active_footer_start(lines)
            footer_ready = footer_start is not None and not _has_current_tui_progress(
                lines, footer_start
            )
            if (
                self._registered_sidecar_is_live(registration, proc_root=proc_root)
                and pane_command
                and CODEX_PANE_COMMAND_PATTERN.fullmatch(pane_command)
                and footer_ready
            ):
                fingerprint = "\n".join(lines[-IDLE_PROMPT_TAIL_LINES:])
                if fingerprint == ready_fingerprint:
                    ready_polls += 1
                else:
                    ready_fingerprint = fingerprint
                    ready_polls = 1
                if ready_polls >= 2:
                    return
            else:
                ready_fingerprint = None
                ready_polls = 0
            time.sleep(0.5)
        raise CodexReconnectNoReadyError(
            f"Codex sidecar reconnect timed out after {int(timeout)} seconds",
            self._startup_evidence,
        )

    def reconnect_runtime_sidecar(
        self,
        resume_identity: str,
        *,
        attempt_token: str,
        attempt_state: str,
        mark_launch_dispatched: Callable[[], bool],
        runtime_ready: Callable[[], Optional[dict[str, Any]]],
        record_output_boundary: Callable[[int, int, int], bool],
        side_effect_guard: Callable[[], bool] = lambda: True,
    ) -> None:
        """Restart Codex's MCP subprocess and resume the exact conversation."""
        identity_match = CODEX_SESSION_ID_PATTERN.fullmatch(f"{resume_identity}.jsonl")
        if not identity_match or identity_match.group("session") != resume_identity.lower():
            raise ProviderError("Codex resume identity is invalid")
        if not re.fullmatch(r"[0-9a-f]{32}", attempt_token):
            raise ProviderError("Codex reconnect attempt token is invalid")
        self._startup_evidence = ""
        self._startup_exit_marker = (
            f"__CAO_CODEX_RECONNECT_EXIT_{self.terminal_id}_{attempt_token}__"
        )
        self._process_exited = False
        if attempt_state in {"launch_dispatched", "runtime_ready"}:
            self._wait_for_reconnected_runtime(attempt_token, runtime_ready)
            if not side_effect_guard():
                raise ProviderError("Codex sidecar reconnect lost ownership before output boundary")
            self._publish_fresh_runtime_output_boundary(attempt_token, record_output_boundary)
            self._runtime_sidecar_reconnect_pending = False
            self._initialized = True
            return
        if attempt_state != "reserved":
            raise ProviderError("Codex reconnect attempt state is invalid")
        pane_command = tmux_client.get_pane_current_command(self.session_name, self.window_name)
        if pane_command is None:
            raise ProviderError("Codex pane command is unavailable during reconnect")
        if pane_command not in SHELL_PANE_COMMANDS:
            if not CODEX_PANE_COMMAND_PATTERN.fullmatch(pane_command):
                raise ProviderError("Codex pane changed before sidecar reconnect")
            if not side_effect_guard():
                raise ProviderError("Codex sidecar reconnect lost ownership before exit")
            tmux_client.send_keys(self.session_name, self.window_name, "/exit")
            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline:
                pane_command = tmux_client.get_pane_current_command(
                    self.session_name, self.window_name
                )
                if pane_command in SHELL_PANE_COMMANDS:
                    break
                if pane_command is None or not CODEX_PANE_COMMAND_PATTERN.fullmatch(pane_command):
                    raise ProviderError("Codex pane changed while exiting for sidecar reconnect")
                time.sleep(0.5)
            else:
                raise ProviderError("Codex did not exit for sidecar reconnect")

        if not mark_launch_dispatched():
            raise ProviderError("Codex sidecar reconnect lost ownership before resume")
        command = self._build_codex_command(
            resume_session_id=resume_identity,
            reconnect_attempt_token=attempt_token,
        )
        marker = f"{SIDECAR_RECONNECTED_PREFIX}{ACTIVE_RUNTIME_GENERATION}__"
        launch_marker = (
            f"{SIDECAR_RECONNECT_LAUNCH_PREFIX}{ACTIVE_RUNTIME_GENERATION}_{attempt_token}__"
        )
        launch_command = (
            f"printf '\\n%s\\n%s\\n' {shlex.quote(marker)} {shlex.quote(launch_marker)}; "
            f"{command}; codex_reconnect_status=$?; "
            f"printf '\\n{self._startup_exit_marker}:%s\\n' \"$codex_reconnect_status\""
        )
        tmux_client.send_keys(self.session_name, self.window_name, launch_command)
        self._wait_for_reconnected_runtime(attempt_token, runtime_ready)
        if not side_effect_guard():
            raise ProviderError("Codex sidecar reconnect lost ownership before output boundary")
        self._publish_fresh_runtime_output_boundary(attempt_token, record_output_boundary)
        self._runtime_sidecar_reconnect_pending = False
        self._initialized = True

    def _handle_rate_limit_model_switch_prompt(self, output: str, clean_output: str) -> bool:
        """Keep the pinned model for Codex's exact rate-limit switch menu.

        The selected row may be any of Codex's three canonical rows. Navigate
        relative to that observed row to the explicit "never show again"
        action; ordinary permission and owner dialogs cannot match this
        predicate.
        """
        match = _active_bottommost_advisory_match(
            output, clean_output, RATE_LIMIT_MODEL_SWITCH_PATTERN
        )
        if not match or match.group("rate_question_model") != match.group("rate_option_1_model"):
            self._handled_advisory_fingerprints.pop("rate", None)
            return False

        selected_option = _selected_canonical_menu_option(match, "rate")
        if selected_option is None:
            self._handled_advisory_fingerprints.pop("rate", None)
            return False

        fingerprint = match.group(0)
        if self._handled_advisory_fingerprints.get("rate") == fingerprint:
            return False

        logger.warning(
            "Codex rate-limit model-switch suggestion detected for terminal %s; "
            "keeping the profile-selected model and suppressing future reminders",
            self.terminal_id,
        )
        for _ in range(3 - selected_option):
            tmux_client.send_special_key(self.session_name, self.window_name, "Down")
        tmux_client.send_special_key(self.session_name, self.window_name, "Enter")
        self._handled_advisory_fingerprints["rate"] = fingerprint
        return True

    def _handle_fast_mode_advisory_prompt(self, output: str, clean_output: str) -> bool:
        """Dismiss Codex's exact non-authoritative faster-model advisory.

        "Dismiss and keep waiting" retains the current configured speed/model
        and does not invoke ``/fast``. The full current Codex 0.146.0 menu and
        a single selected row are required before any key is sent.
        """
        match = _active_bottommost_advisory_match(output, clean_output, FAST_MODE_ADVISORY_PATTERN)
        if not match:
            self._handled_advisory_fingerprints.pop("fast", None)
            return False

        selected_option = _selected_canonical_menu_option(match, "fast")
        if selected_option is None:
            self._handled_advisory_fingerprints.pop("fast", None)
            return False

        fingerprint = match.group(0)
        if self._handled_advisory_fingerprints.get("fast") == fingerprint:
            return False

        logger.warning(
            "Codex faster-model advisory detected for terminal %s; "
            "dismissing and retaining the configured execution mode",
            self.terminal_id,
        )
        if selected_option < 2:
            tmux_client.send_special_key(self.session_name, self.window_name, "Down")
        elif selected_option > 2:
            tmux_client.send_special_key(self.session_name, self.window_name, "Up")
        tmux_client.send_special_key(self.session_name, self.window_name, "Enter")
        self._handled_advisory_fingerprints["fast"] = fingerprint
        return True

    def _startup_exit_match(self, output: str) -> Optional[re.Match[str]]:
        """Find this terminal's current or persisted Codex launch sentinel.

        A provider rebuilt after an API/service restart has no in-memory launch
        marker, but its terminal scrollback keeps the shell-level sentinel.
        Rehydrate only from a marker bound to this terminal id; never infer a
        process exit from another terminal's history or generic sentinel text.
        A newer sidecar-reconnect marker proves that an exact resumed process
        was launched after an older clean-exit sentinel, so that older marker
        cannot retire the resumed runtime after a later service restart.
        """
        marker_pattern = (
            re.escape(self._startup_exit_marker)
            if self._startup_exit_marker
            else (
                rf"(?:__CAO_CODEX_STARTUP_EXIT_{re.escape(self.terminal_id)}_\d+__|"
                rf"__CAO_CODEX_RECONNECT_EXIT_{re.escape(self.terminal_id)}_[0-9a-f]{{32}}__)"
            )
        )
        matches = list(re.finditer(rf"^{marker_pattern}:(\d+)\s*$", output, re.MULTILINE))
        if not matches:
            return None
        latest = matches[-1]
        reconnect_markers = list(SIDECAR_RECONNECTED_LINE_PATTERN.finditer(output))
        if reconnect_markers and reconnect_markers[-1].start() > latest.start():
            return None
        return latest

    def _has_clean_codex_exit_to_shell(self, clean_output: str) -> bool:
        """Whether this provider's Codex process ended normally at a shell prompt."""
        sentinel = self._startup_exit_match(clean_output)
        if not sentinel or sentinel.group(1) != "0":
            return False

        # The shell prompt must be the first non-blank post-sentinel line.
        # A later prompt cannot validate arbitrary intervening assistant prose.
        post_sentinel_lines = clean_output[sentinel.end() :].splitlines()
        first_post_sentinel_line = next(
            (line for line in post_sentinel_lines if line.strip()), None
        )
        return bool(
            first_post_sentinel_line
            and CAO_SHELL_PROMPT_PATTERN.fullmatch(first_post_sentinel_line)
        )

    def is_process_alive(self) -> bool:
        """Use the launch sentinel, not the still-live tmux shell, for liveness."""
        return not self._process_exited

    def _debounce_completion_candidate(
        self, candidate: str, user_candidate: Optional[str]
    ) -> TerminalStatus:
        """Return PROCESSING until a semantic completion candidate is stable."""
        if (
            candidate == self._completion_candidate
            and user_candidate == self._completion_candidate_user
        ):
            self._completion_candidate_polls += 1
        else:
            self._completion_candidate = candidate
            self._completion_candidate_user = user_candidate
            self._completion_candidate_polls = 1

        # Initial sighting plus two later polls prevents footer/tool races.
        if self._completion_candidate_polls >= 3:
            return TerminalStatus.COMPLETED
        return TerminalStatus.PROCESSING

    def _build_codex_command(
        self,
        resume_session_id: str | None = None,
        reconnect_attempt_token: str | None = None,
    ) -> str:
        """Build Codex command with agent profile if provided.

        Returns properly escaped shell command string that can be safely sent via tmux.
        Uses codex's -c developer_instructions flag to inject agent system prompts.
        """
        # --yolo (alias for --dangerously-bypass-approvals-and-sandbox):
        # bypass approval prompts and sandboxing. CAO agents run in
        # non-interactive tmux sessions where interactive approval prompts
        # block handoff/assign flows. This mirrors Claude Code's
        # --dangerously-skip-permissions and Gemini CLI's --yolo flags.
        command_parts = [
            "codex",
            "--yolo",
        ]
        if not _codex_wrapper_preapplies_hook_trust():
            command_parts.append("--dangerously-bypass-hook-trust")
        command_parts.extend(["--no-alt-screen", "--disable", "shell_snapshot"])

        if self._agent_profile is not None:
            try:
                profile = self._resolved_profile or load_agent_profile(self._agent_profile)

                if profile.model:
                    command_parts.extend(["--model", profile.model])

                system_prompt = profile.system_prompt if profile.system_prompt is not None else ""
                if self._structured_owner_authorized:
                    from cli_agent_orchestrator.services.launch_authority import (
                        STRUCTURED_OWNER_AUTHORIZATION_INSTRUCTION,
                    )

                    system_prompt = (
                        STRUCTURED_OWNER_AUTHORIZATION_INSTRUCTION + "\n" + system_prompt
                    )
                system_prompt = self._apply_skill_prompt(system_prompt)

                # Prepend security constraints for soft enforcement (Codex has no
                # native tool restriction mechanism). Only applied when tool
                # restrictions are active (not unrestricted "*").
                if self._allowed_tools and "*" not in self._allowed_tools:
                    from cli_agent_orchestrator.constants import SECURITY_PROMPT

                    tools_list = ", ".join(self._allowed_tools)
                    tool_constraint = f"\nYou only have access to these tools: {tools_list}\n"
                    system_prompt = SECURITY_PROMPT + tool_constraint + system_prompt

                if system_prompt:
                    # Codex accepts developer_instructions via -c config override.
                    # This is injected as a developer role message before AGENTS.md content.
                    # Escape backslashes, double quotes, and newlines for TOML basic string.
                    # Newlines must become literal \n to prevent tmux send_keys from
                    # splitting the command across multiple lines.
                    escaped_prompt = (
                        system_prompt.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
                    )
                    command_parts.extend(["-c", f'developer_instructions="{escaped_prompt}"'])

                # Add MCP servers via -c config overrides (per-session, no global config changes).
                # Each server field is set via dotted path: mcp_servers.<name>.<field>=<value>
                if profile.mcpServers:
                    for server_name, server_config in profile.mcpServers.items():
                        prefix = f"mcp_servers.{server_name}"
                        if isinstance(server_config, dict):
                            cfg = server_config
                        else:
                            cfg = server_config.model_dump(exclude_none=True)
                        cfg = canonicalize_cao_mcp_server_config(server_name, cfg)
                        if "command" in cfg:
                            command_parts.extend(["-c", f'{prefix}.command="{cfg["command"]}"'])
                        if "args" in cfg:
                            args_toml = "[" + ", ".join(f'"{a}"' for a in cfg["args"]) + "]"
                            command_parts.extend(["-c", f"{prefix}.args={args_toml}"])
                        if "env" in cfg and cfg["env"]:
                            for env_key, env_val in cfg["env"].items():
                                command_parts.extend(["-c", f'{prefix}.env.{env_key}="{env_val}"'])
                        if server_name == "cao-mcp-server":
                            # A Codex MCP subprocess can outlive an API service
                            # restart.  Bind it to the API generation that
                            # launched this terminal so its initial-handoff
                            # fence can refresh only that sidecar later.
                            command_parts.extend(
                                [
                                    "-c",
                                    f'{prefix}.env.{RUNTIME_GENERATION_ENV}="{ACTIVE_RUNTIME_GENERATION}"',
                                ]
                            )
                            if reconnect_attempt_token is not None:
                                if not re.fullmatch(r"[0-9a-f]{32}", reconnect_attempt_token):
                                    raise ValueError("invalid provider reconnect attempt token")
                                command_parts.extend(
                                    [
                                        "-c",
                                        f"{prefix}.env.{PROVIDER_RECONNECT_ATTEMPT_ENV}="
                                        f'"{reconnect_attempt_token}"',
                                    ]
                                )
                        # Forward CAO_TERMINAL_ID so MCP servers (e.g. cao-mcp-server)
                        # can identify the current session for handoff/assign operations.
                        # Codex does not forward env vars to MCP subprocesses by default;
                        # env_vars lists names to inherit from the parent shell environment.
                        env_vars = cfg.get("env_vars", [])
                        required_env_vars: tuple[str, ...] = ("CAO_TERMINAL_ID",)
                        if server_name == "cao-mcp-server":
                            required_env_vars += ("CAO_TERMINAL_AUTH_TOKEN",)
                        for required_env in required_env_vars:
                            if required_env not in env_vars:
                                env_vars = list(env_vars) + [required_env]
                        env_vars_toml = "[" + ", ".join(f'"{v}"' for v in env_vars) + "]"
                        command_parts.extend(["-c", f"{prefix}.env_vars={env_vars_toml}"])
                        # Codex deserializes tool_timeout_sec via Option<f64>; a TOML
                        # integer is silently rejected and falls back to its 60s default.
                        # Preserve CAO's 600s fallback only when the profile does not
                        # explicitly set a timeout, otherwise forward its float value.
                        if "tool_timeout_sec" in cfg:
                            tool_timeout = float(cfg["tool_timeout_sec"])
                            command_parts.extend(
                                ["-c", f"{prefix}.tool_timeout_sec={tool_timeout!r}"]
                            )
                        else:
                            command_parts.extend(["-c", f"{prefix}.tool_timeout_sec=600.0"])

                # Per-agent Codex settings such as reasoning effort. Emit these
                # after CAO's MCP overrides so an explicit profile value has
                # final precedence.
                if profile.codexConfig:
                    for key, value in profile.codexConfig.items():
                        command_parts.extend(["-c", _toml_override(key, value)])

            except Exception as e:
                raise ProviderError(f"Failed to load agent profile '{self._agent_profile}': {e}")

        # Codex creates a root conversation lazily, only when it accepts the
        # first prompt. A synchronous SessionStart hook is therefore the first
        # boundary at which its exact native identity exists. Keep this CAO
        # override last so an agent profile cannot replace the managed binding
        # hook; failure returns ``continue: false`` before provider dispatch.
        hook_command = shlex.join(
            [sys.executable, "-m", "cli_agent_orchestrator.codex_session_hook"]
        )
        hook_config = (
            'hooks.SessionStart=[{matcher="^(startup|resume)$",hooks=[{type="command",'
            f"command={_toml_scalar(hook_command)},timeout=30}}]}}]"
        )
        command_parts.extend(["-c", hook_config])

        if resume_session_id is not None:
            command_parts.extend(["resume", resume_session_id])
        return shlex.join(command_parts)

    def _record_startup_output(self, output: str) -> str:
        """Retain a bounded, presentation-clean startup snapshot for failures."""
        clean_output = _strip_terminal_noise(output)
        self._startup_evidence = clean_output[-STARTUP_EVIDENCE_LIMIT:]
        return clean_output

    def _startup_exit_code(self, output: str) -> Optional[str]:
        """Return this terminal's current or persisted Codex shell exit code."""
        match = self._startup_exit_match(output)
        return match.group(1) if match else None

    def _raise_if_startup_failed(self, clean_output: str) -> None:
        """Fail promptly for an exited CLI or an explicit startup error."""
        exit_code = self._startup_exit_code(clean_output)
        if exit_code is not None:
            raise CodexStartupError(
                f"Codex exited during startup with status {exit_code}",
                self._startup_evidence,
            )
        if re.search(STARTUP_ERROR_PATTERN, clean_output, re.IGNORECASE | re.MULTILINE):
            raise CodexStartupError("Codex reported a startup error", self._startup_evidence)

    def _capture_startup_output(self) -> str:
        """Capture startup output once so evidence/error checks share the snapshot."""
        output = tmux_client.get_history(self.session_name, self.window_name)
        return self._record_startup_output(output) if output else ""

    def _handle_trust_prompt(self, timeout: float = 20.0) -> None:
        """Auto-accept the workspace trust prompt if it appears.

        Codex shows a folder approval dialog when opening a new directory.
        This sends Enter to accept the default option (allow Codex to work).
        CAO assumes the user trusts the working directory since they confirmed
        workspace access during the launch command.
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            clean_output = self._capture_startup_output()
            if not clean_output:
                time.sleep(1.0)
                continue

            self._raise_if_startup_failed(clean_output)

            if re.search(TRUST_PROMPT_PATTERN, clean_output):
                logger.info("Codex workspace trust prompt detected, auto-accepting")
                session = tmux_client.server.sessions.get(session_name=self.session_name)
                window = session.windows.get(window_name=self.window_name)
                pane = window.active_pane
                if pane:
                    pane.send_keys("", enter=True)
                return

            # Check if Codex has fully started (welcome banner visible)
            if re.search(CODEX_WELCOME_PATTERN, clean_output):
                logger.info("Codex started without trust prompt")
                return

            time.sleep(1.0)
        logger.warning("Codex trust prompt handler timed out")

    def _wait_for_startup_ready(self, timeout: float = 60.0) -> None:
        """Wait for ready state while surfacing startup failures immediately."""
        deadline = time.monotonic() + timeout
        while True:
            clean_output = self._capture_startup_output()
            if clean_output:
                self._raise_if_startup_failed(clean_output)

            status = self.get_status()
            if status in {TerminalStatus.IDLE, TerminalStatus.COMPLETED}:
                return
            if status == TerminalStatus.ERROR:
                raise CodexStartupError("Codex reported a startup error", self._startup_evidence)

            if time.monotonic() >= deadline:
                raise CodexStartupNoReadyError(
                    f"Codex initialization timed out after {int(timeout)} seconds without a ready state",
                    self._startup_evidence,
                )
            time.sleep(1.0)

    def initialize(self) -> bool:
        """Initialize Codex provider by starting codex command."""
        if not wait_for_shell(tmux_client, self.session_name, self.window_name, timeout=10.0):
            raise TimeoutError("Shell initialization timed out after 10 seconds")

        # Send a warm-up command before launching codex.
        # Codex exits immediately in freshly-created tmux sessions where the shell
        # has not yet processed a full interactive command cycle.
        tmux_client.send_keys(self.session_name, self.window_name, "echo ready")
        time.sleep(2.0)

        # Build command with flags and agent profile (developer_instructions).
        # --no-alt-screen: run in inline mode so output stays in normal scrollback,
        #   making tmux capture-pane reliable.
        # --disable shell_snapshot: avoid TTY input conflicts (SIGTTIN) in tmux
        #   caused by the shell_snapshot subprocess inheriting stdin.
        command = self._build_codex_command()
        self._startup_attempt += 1
        self._startup_evidence = ""
        self._startup_exit_marker = (
            f"__CAO_CODEX_STARTUP_EXIT_{self.terminal_id}_{self._startup_attempt}__"
        )
        self._process_exited = False
        # A shell-level sentinel detects an early Codex exit even when the pane
        # stays alive and would otherwise look like an endlessly processing TUI.
        launch_command = (
            f"{command}; codex_startup_status=$?; "
            f"printf '\\n{self._startup_exit_marker}:%s\\n' \"$codex_startup_status\""
        )
        tmux_client.send_keys(self.session_name, self.window_name, launch_command)

        # Handle workspace trust prompt if it appears (new/untrusted directories)
        self._handle_trust_prompt(timeout=20.0)

        self._wait_for_startup_ready(timeout=60.0)

        self._initialized = True
        return True

    def get_status(self, tail_lines: Optional[int] = None) -> TerminalStatus:
        """Get Codex status by analyzing terminal output."""
        output = tmux_client.get_history(self.session_name, self.window_name, tail_lines=tail_lines)

        if not output:
            return TerminalStatus.ERROR

        clean_output = _strip_terminal_noise(output)
        self._refresh_runtime_reconnect_signal(clean_output)
        normalized_output = _normalize_terminal_suffix_blank_rows(clean_output)
        tail_output = "\n".join(normalized_output.splitlines()[-25:])

        # Codex owns this persisted reminder choice. Handle only the complete,
        # selected rate-limit model menu before generic prompt classification.
        if (
            not self._runtime_sidecar_reconnect_pending
            and self._handle_rate_limit_model_switch_prompt(output, clean_output)
        ):
            self._reset_completion_candidate()
            return TerminalStatus.PROCESSING

        # The long-running-task retry suggestion is likewise advisory, but it
        # has no observed persistent "never show again" action. Dismiss only
        # this complete known menu and keep the already configured speed mode.
        if not self._runtime_sidecar_reconnect_pending and self._handle_fast_mode_advisory_prompt(
            output, clean_output
        ):
            self._reset_completion_candidate()
            return TerminalStatus.PROCESSING

        # After an intentional /exit, the sentinel emitted by initialize() and
        # a normal shell prompt are stronger lifecycle evidence than the absent
        # TUI footer. Without this, the Web UI stays on Processing forever.
        if self._has_clean_codex_exit_to_shell(clean_output):
            if self._runtime_sidecar_reconnect_pending:
                # This is the intentional exit half of an exact-session
                # reconnect. Keep runtime ownership live so concurrent status
                # polling or a service restart cannot retire the pane before
                # the persisted session identity is resumed.
                self._reset_completion_candidate()
                return TerminalStatus.COMPLETED
            self._process_exited = True
            self._reset_completion_candidate()
            return TerminalStatus.COMPLETED

        # A non-zero launch sentinel means the Codex child ended while the
        # tmux shell remained persistent. Do not report that shell as a live
        # worker eligible for a resumable handoff wait.
        if (exit_code := self._startup_exit_code(clean_output)) not in {None, "0"}:
            self._process_exited = True
            self._reset_completion_candidate()
            return TerminalStatus.ERROR

        # Search for user messages, excluding the Codex TUI footer when present.
        # The TUI footer (idle prompt hint like "› Summarize recent commits" +
        # status bar "? for shortcuts / context left") can contain › followed by
        # suggestion text, which USER_PREFIX_PATTERN would incorrectly match as
        # user input, preventing COMPLETED detection.
        # Only apply the cutoff when TUI footer indicators are actually present
        # to avoid over-excluding in short outputs or test fixtures.
        all_lines = normalized_output.splitlines()
        active_footer_start_idx = _find_active_footer_start(all_lines)
        tui_footer_detected = any(
            re.search(TUI_FOOTER_PATTERN, line) for line in all_lines[-IDLE_PROMPT_TAIL_LINES:]
        )
        if tui_footer_detected:
            cutoff_pos = _compute_tui_footer_cutoff(all_lines)
        else:
            cutoff_pos = len(normalized_output)

        last_user = None
        for match in re.finditer(
            USER_PREFIX_PATTERN, normalized_output, re.IGNORECASE | re.MULTILINE
        ):
            if match.start() < cutoff_pos:
                last_user = match

        output_after_last_user = (
            normalized_output[last_user.start() :] if last_user else normalized_output
        )
        assistant_after_last_user = bool(
            last_user
            and re.search(
                ASSISTANT_PREFIX_PATTERN,
                output_after_last_user,
                re.IGNORECASE | re.MULTILINE,
            )
        )

        # Check trust prompt early — the trust menu uses › which matches the idle prompt
        # pattern, and PROCESSING_PATTERN matches "running" in "You are running Codex in..."
        if re.search(TRUST_PROMPT_PATTERN, normalized_output):
            return TerminalStatus.WAITING_USER_ANSWER

        # Check bottom of captured output for idle prompt.
        # With --no-alt-screen, scrollback contains history so we can't anchor
        # to end-of-string. Instead, check only the last few lines.
        bottom_lines = normalized_output.splitlines()[-IDLE_PROMPT_TAIL_LINES:]
        has_idle_prompt_at_end = any(
            re.match(IDLE_PROMPT_STRICT_PATTERN, line, re.IGNORECASE) for line in bottom_lines
        )

        # Only treat ERROR/WAITING prompts as actionable if they appear after the last user message
        # and are not part of an assistant response.
        if last_user is not None:
            if not assistant_after_last_user:
                if re.search(
                    WAITING_PROMPT_PATTERN,
                    output_after_last_user,
                    re.IGNORECASE | re.MULTILINE,
                ):
                    return TerminalStatus.WAITING_USER_ANSWER
                if re.search(
                    ERROR_PATTERN,
                    output_after_last_user,
                    re.IGNORECASE | re.MULTILINE,
                ):
                    return TerminalStatus.ERROR
        else:
            if re.search(WAITING_PROMPT_PATTERN, tail_output, re.IGNORECASE | re.MULTILINE):
                return TerminalStatus.WAITING_USER_ANSWER
            if re.search(ERROR_PATTERN, tail_output, re.IGNORECASE | re.MULTILINE):
                return TerminalStatus.ERROR
        has_idle_footer = has_idle_prompt_at_end or tui_footer_detected
        if has_idle_footer:
            # A live spinner is the complete current body frame immediately
            # before current footer chrome.  Do not search scrollback: final
            # reports and tool JSON can quote a historical spinner verbatim.
            if active_footer_start_idx is not None and _has_current_tui_progress(
                all_lines, active_footer_start_idx
            ):
                self._reset_completion_candidate()
                return TerminalStatus.PROCESSING

            # Consider COMPLETED only if we see an assistant marker after the last user message.
            if last_user is not None:
                assistant_match = re.search(
                    ASSISTANT_PREFIX_PATTERN,
                    normalized_output[last_user.start() :],
                    re.IGNORECASE | re.MULTILINE,
                )
                if assistant_match:
                    assistant_start = last_user.start() + assistant_match.start()
                    assistant_text = normalized_output[assistant_start:cutoff_pos]
                    assistant_markers = list(
                        re.finditer(
                            ASSISTANT_PREFIX_PATTERN,
                            assistant_text,
                            re.IGNORECASE | re.MULTILINE,
                        )
                    )
                    notice_markers = list(
                        re.finditer(
                            TUI_INFO_NOTICE_PREFIX_PATTERN,
                            assistant_text,
                            re.IGNORECASE | re.MULTILINE,
                        )
                    )
                    if notice_markers and (
                        not assistant_markers
                        or notice_markers[-1].start() > assistant_markers[-1].start()
                    ):
                        notice_start = assistant_start + notice_markers[-1].start()
                        candidate = _semantic_completion_candidate(
                            normalized_output[notice_start:cutoff_pos]
                        )
                        user_line_end = normalized_output.find("\n", last_user.start())
                        if user_line_end == -1:
                            user_line_end = len(normalized_output)
                        user_candidate = _semantic_completion_candidate(
                            normalized_output[last_user.start() : user_line_end]
                        )
                        return self._debounce_completion_candidate(candidate, user_candidate)
                    # A terminal can briefly render an idle footer directly
                    # after a structural tool frame.  It is not a final
                    # assistant answer until a later assistant block appears.
                    if assistant_markers and _is_structural_tool_block(
                        assistant_text, assistant_markers, len(assistant_markers) - 1
                    ):
                        self._reset_completion_candidate()
                        return TerminalStatus.PROCESSING
                    # Footer chrome is volatile (context percentage, cursor and
                    # prompt hints), so it is deliberately excluded from the
                    # semantic candidate compared across polls.
                    candidate_end = cutoff_pos if tui_footer_detected else len(normalized_output)
                    candidate = _semantic_completion_candidate(
                        normalized_output[assistant_start:candidate_end]
                    )
                    user_line_end = normalized_output.find("\n", last_user.start())
                    if user_line_end == -1:
                        user_line_end = len(normalized_output)
                    user_candidate = _semantic_completion_candidate(
                        normalized_output[last_user.start() : user_line_end]
                    )

                    return self._debounce_completion_candidate(candidate, user_candidate)

                # A new user line after a pending candidate is an active turn,
                # even if Codex has not rendered its first assistant bullet yet.
                if self._completion_candidate is not None:
                    self._reset_completion_candidate()
                    return TerminalStatus.PROCESSING

                return TerminalStatus.IDLE

            # A long handoff can retain the final report/footer but not the
            # original user row inside TMUX_HISTORY_LINES. Only when CAO has
            # recorded a submitted input may that tail form a completion
            # candidate; startup and ordinary idle footers remain IDLE.
            if self._input_received:
                assistant_text = normalized_output[:cutoff_pos]
                assistant_markers = list(
                    re.finditer(
                        ASSISTANT_PREFIX_PATTERN,
                        assistant_text,
                        re.IGNORECASE | re.MULTILINE,
                    )
                )
                notice_markers = list(
                    re.finditer(
                        TUI_INFO_NOTICE_PREFIX_PATTERN,
                        assistant_text,
                        re.IGNORECASE | re.MULTILINE,
                    )
                )
                if notice_markers and (
                    not assistant_markers
                    or notice_markers[-1].start() > assistant_markers[-1].start()
                ):
                    candidate = _semantic_completion_candidate(
                        assistant_text[notice_markers[-1].start() :]
                    )
                    if candidate:
                        return self._debounce_completion_candidate(candidate, None)
                if assistant_markers:
                    if _is_structural_tool_block(
                        assistant_text, assistant_markers, len(assistant_markers) - 1
                    ):
                        self._reset_completion_candidate()
                        return TerminalStatus.PROCESSING
                    candidate = _semantic_completion_candidate(
                        _select_final_assistant_block(assistant_text)
                    )
                    if candidate:
                        return self._debounce_completion_candidate(candidate, None)

            return TerminalStatus.IDLE

        # If we're not at an idle prompt and we don't see explicit errors/permission prompts,
        # assume the CLI is still producing output.
        self._reset_completion_candidate()
        return TerminalStatus.PROCESSING

    def get_idle_pattern_for_log(self) -> str:
        """Return Codex IDLE prompt pattern for log files."""
        return IDLE_PROMPT_PATTERN_LOG

    def extract_usage_observation(self, script_output: str) -> Optional[UsageObservation]:
        """Extract only explicit Codex TUI token telemetry from a final capture.

        Interactive Codex captures commonly expose no token counts.  In that
        case the completed run is still recorded with nullable token values;
        CAO never estimates tokens from message text or context percentages.
        """
        response = self.extract_last_message_from_script(script_output)
        if not response:
            return None
        clean_output = _strip_terminal_noise(script_output)
        normalized_output = _normalize_terminal_suffix_blank_rows(clean_output)
        user_matches = list(
            re.finditer(USER_PREFIX_PATTERN, normalized_output, re.IGNORECASE | re.MULTILINE)
        )
        user_line = ""
        if user_matches:
            start = user_matches[-1].start()
            end = normalized_output.find("\n", start)
            user_line = normalized_output[
                start : end if end != -1 else len(normalized_output)
            ].strip()
        # Prompt/response text is not an invocation identity: two accepted
        # identical invocations must remain two ledger rows.  The final
        # response's stable capture occurrence disambiguates them while the
        # terminal identity and unchanged capture keep repeat reads/restarts
        # idempotent.  This remains purely observational telemetry.
        response_occurrence = normalized_output.rfind(response)
        identity_material = f"codex_tui_completion_v2\0{self.terminal_id}\0{response_occurrence}\0{user_line}\0{response}"
        source_run_identity = hashlib.sha256(identity_material.encode("utf-8")).hexdigest()

        model_match = re.search(r"\bmodel:\s*([^\s|·]+)", clean_output, re.IGNORECASE)
        token_values = self._extract_reported_token_values(clean_output)
        return UsageObservation(
            source_run_identity=source_run_identity,
            extractor="codex_tui_completion_v2",
            model=model_match.group(1) if model_match else None,
            **token_values,
        )

    @staticmethod
    def _extract_reported_token_values(output: str) -> dict[str, Optional[int]]:
        """Parse a Codex-reported usage row; never derive counts from prose."""
        values: dict[str, Optional[int]] = {
            "input_tokens": None,
            "cached_input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
        }
        usage_lines = [
            line
            for line in output.splitlines()
            if re.search(r"\b(?:token usage|tokens? used|tokens?)\b", line, re.IGNORECASE)
        ]
        if not usage_lines:
            return values
        usage = " ".join(usage_lines)
        fields = {
            "input_tokens": r"\binput(?:[_ ]tokens?)?\s*[:=]\s*([0-9][0-9,]*)",
            "cached_input_tokens": r"\bcached(?:[_ ]input)?(?:[_ ]tokens?)?\s*[:=]\s*([0-9][0-9,]*)",
            "output_tokens": r"\boutput(?:[_ ]tokens?)?\s*[:=]\s*([0-9][0-9,]*)",
            "total_tokens": r"\btotal(?:[_ ]tokens?)?\s*[:=]\s*([0-9][0-9,]*)",
        }
        for name, pattern in fields.items():
            match = re.search(pattern, usage, re.IGNORECASE)
            if match:
                values[name] = int(match.group(1).replace(",", ""))

        # Codex 0.146.0's TUI renders cached input as a postfix
        # ``(+ <count> cached)`` while displaying only non-cached input and
        # its corresponding blended total. Normalize this presentation to the
        # ledger contract: input includes its cached subset and total is input
        # plus output, with cache added exactly once.
        tui_cached_match = re.search(r"\(\+\s*([0-9][0-9,]*)\s+cached\)", usage, re.IGNORECASE)
        if tui_cached_match:
            cached_input_tokens = int(tui_cached_match.group(1).replace(",", ""))
            values["cached_input_tokens"] = cached_input_tokens
            if values["input_tokens"] is not None:
                values["input_tokens"] += cached_input_tokens
            if values["total_tokens"] is not None:
                values["total_tokens"] += cached_input_tokens
        return values

    def get_durable_last_response(self) -> Optional[str]:
        """Return the latest exact-session response without reading terminal history."""
        from cli_agent_orchestrator.clients.database import get_terminal_metadata

        metadata = get_terminal_metadata(self.terminal_id)
        if metadata is None:
            return None
        identity = metadata.get("provider_resume_identity")
        working_directory = metadata.get("launch_worktree")
        if (
            not isinstance(identity, str)
            or not isinstance(working_directory, str)
            or not os.path.isabs(working_directory)
        ):
            return None
        rollout = _rollout_path_for_identity(
            identity,
            Path(working_directory).resolve(strict=False),
        )
        return _latest_completed_codex_response(rollout) if rollout is not None else None

    def extract_last_message_from_script(self, script_output: str) -> str:
        """Extract Codex's final response from terminal output.

        Supports two output formats:
        - Label style: "You ...\\nassistant: response\\n❯" (synthetic/test format)
        - Bullet style: "› user message\\n• response\\n›" (real Codex interactive mode)

        Primary approach: find the last user message and extract everything between
        the end of that line and the next empty idle prompt.
        Fallback: use assistant marker based extraction when no user message is found.
        """
        clean_output = _strip_terminal_noise(script_output)
        normalized_output = _normalize_terminal_suffix_blank_rows(clean_output)

        # Primary: find last user message, extract response between it and idle prompt.
        # Exclude the Codex TUI footer from user-message matching when detected.
        all_lines = normalized_output.splitlines()
        tui_footer_detected = any(
            re.search(TUI_FOOTER_PATTERN, line) for line in all_lines[-IDLE_PROMPT_TAIL_LINES:]
        )
        if tui_footer_detected:
            cutoff_pos = _compute_tui_footer_cutoff(all_lines)
        else:
            cutoff_pos = len(normalized_output)

        user_matches = [
            m
            for m in re.finditer(
                USER_PREFIX_PATTERN, normalized_output, re.IGNORECASE | re.MULTILINE
            )
            if m.start() < cutoff_pos
        ]

        if user_matches:
            last_user = user_matches[-1]

            # Find the first assistant response marker (• or assistant:) after
            # the user message. This correctly skips multi-line user messages
            # that wrap across several lines in the Codex TUI.
            asst_after_user = re.search(
                ASSISTANT_PREFIX_PATTERN,
                normalized_output[last_user.start() :],
                re.IGNORECASE | re.MULTILINE,
            )
            if asst_after_user:
                response_start = last_user.start() + asst_after_user.start()
            else:
                # No assistant marker found; fall back to skipping one line
                user_line_end = normalized_output.find("\n", last_user.start())
                if user_line_end == -1:
                    user_line_end = len(normalized_output)
                response_start = user_line_end + 1

            # Find extraction boundary: empty idle prompt or TUI footer area.
            # With --no-alt-screen, the TUI footer (› hint + status bar) has no
            # empty idle prompt. Use cutoff_pos as the boundary when TUI is present.
            idle_after = re.search(
                IDLE_PROMPT_STRICT_PATTERN,
                normalized_output[response_start:],
                re.MULTILINE,
            )
            if idle_after:
                end_pos = response_start + idle_after.start()
            elif tui_footer_detected:
                end_pos = cutoff_pos
            else:
                end_pos = len(normalized_output)

            response_text = _select_final_assistant_block(
                normalized_output[response_start:end_pos]
            ).strip()

            if response_text:
                # Strip "assistant:" prefix if present (label format)
                response_text = re.sub(
                    r"^(?:assistant|codex|agent)\s*:\s*",
                    "",
                    response_text,
                    count=1,
                    flags=re.IGNORECASE,
                )
                return response_text.strip()

        # Fallback: assistant marker based extraction (no user message found).
        matches = list(
            re.finditer(ASSISTANT_PREFIX_PATTERN, normalized_output, re.IGNORECASE | re.MULTILINE)
        )

        if not matches:
            raise ValueError("No Codex response found - no assistant marker detected")

        last_match = matches[-1]
        # The latest user task may have scrolled out, but an empty idle prompt
        # still separates earlier turns.  Start at the first assistant marker
        # in the final turn, then let the structural tool selector remove only
        # preceding tool blocks.  Starting at ``matches[-1]`` loses every
        # earlier bullet of a multiline final report.
        start_pos = 0
        for idle_match in re.finditer(
            IDLE_PROMPT_STRICT_PATTERN,
            normalized_output[: last_match.start()],
            re.MULTILINE,
        ):
            start_pos = idle_match.end()
        final_turn_markers = [match for match in matches if match.start() >= start_pos]
        start_pos = final_turn_markers[0].start()

        idle_after = re.search(
            IDLE_PROMPT_STRICT_PATTERN,
            normalized_output[start_pos:],
            re.MULTILINE,
        )
        if idle_after:
            end_pos = start_pos + idle_after.start()
        elif tui_footer_detected:
            end_pos = cutoff_pos
        else:
            end_pos = len(normalized_output)

        final_answer = _select_final_assistant_block(normalized_output[start_pos:end_pos]).strip()

        final_answer = re.sub(
            r"^(?:assistant|codex|agent)\s*:\s*",
            "",
            final_answer,
            count=1,
            flags=re.IGNORECASE,
        ).strip()

        if not final_answer:
            raise ValueError("Empty Codex response - no content found")

        return final_answer

    def exit_cli(self) -> str:
        """Get the command to exit Codex CLI."""
        return "/exit"

    def cleanup(self) -> None:
        """Clean up Codex CLI provider."""
        self._initialized = False
