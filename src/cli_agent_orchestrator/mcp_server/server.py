"""CLI Agent Orchestrator MCP Server implementation."""

import asyncio
import hashlib
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union
from urllib.parse import quote

import mcp.types as mcp_types
import requests
from fastmcp import FastMCP
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from pydantic import Field

from cli_agent_orchestrator.clients.database import (
    acknowledge_child_assignment_result_outcome,
    acknowledge_handoff_child_result_direct,
    bind_child_assignment_input_turn,
    cancel_child_assignment_attempt,
    cancel_child_assignments_for_terminal,
    cancel_reserved_completed_assigned_child_retirement_exit,
    claim_completed_assigned_child_retirement,
    claim_completed_handoff_child_retirement,
    claim_handoff_child_result_direct,
    claim_or_resume_workflow_turn_receipt,
    claim_staged_handoff_result_direct,
    claim_workflow_effect,
    complete_assigned_child_retirement,
    complete_child_retirement,
    create_assigned_child_completion_result_message,
    create_child_assignment_result_message,
    describe_child_assignment_acknowledgement,
    describe_workflow_effect_rejection,
    finish_workflow_effect,
    get_acknowledged_handoff_child_result_direct,
    get_assigned_child_retirement_cleanup_intent,
    get_child_assignment_request_authority,
    get_child_retirement_cleanup_intent,
    get_claimed_handoff_child_result_direct,
    get_delegation_result,
    get_delegation_result_for_assignment,
    get_handoff_parent_terminal_id,
    get_parent_completion_barrier,
    get_terminal_metadata,
    get_workflow_provider_outcome,
    get_workflow_status,
    has_admitted_workflow_turn,
    is_delegated_child_terminal,
    is_managed_structured_handoff_child,
    issue_workflow_input_binding,
    managed_final_problem,
    managed_handoff_retirement_required,
    parse_v1_result_capture,
    record_workflow_provider_reconnect_runtime_ready,
    register_child_assignment,
    register_handoff_child,
    release_completed_assigned_child_retirement,
    release_provider_execution,
    reserve_completed_assigned_child_retirement_exit,
    revalidate_completed_assigned_child_retirement,
    revalidate_historical_assigned_child_retirement,
    schedule_managed_handoff_continuation,
    set_workflow_terminal_state,
)
from cli_agent_orchestrator.constants import API_BASE_URL, DEFAULT_PROVIDER
from cli_agent_orchestrator.mcp_server.models import HandoffResult, HandoffState
from cli_agent_orchestrator.models.inbox import OrchestrationType
from cli_agent_orchestrator.models.result import HandoffResultDocumentV1
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.runtime_generation import (
    ACTIVE_RUNTIME_GENERATION,
    PROVIDER_RECONNECT_ATTEMPT_ENV,
    RUNTIME_GENERATION_ENV,
)
from cli_agent_orchestrator.services import inbox_service, terminal_service
from cli_agent_orchestrator.utils.terminal import generate_session_name, wait_until_terminal_status

logger = logging.getLogger(__name__)

_RUNTIME_GENERATION_PATH = "/_internal/runtime-generation"
# Capture the imported-code identity once. ``os.environ`` is mutable process
# state and a provider can retain its old launch environment across a supported
# service restart. The launch-only generation setting identifies an actual
# managed sidecar at import; its generation comes from the code, never the
# inherited setting's value. A newly initialized sidecar therefore proves
# ownership with the code it imported, while an old surviving process retains
# its old identity.
_SIDECAR_RUNTIME_GENERATION = (
    ACTIVE_RUNTIME_GENERATION if os.environ.get(RUNTIME_GENERATION_ENV) else None
)
_SAFE_PRE_EFFECT_ADMISSION_REASONS = {
    "ADMISSION_FENCE_TIMEOUT",
    "CONTEXT_INVENTORY_UNAVAILABLE",
    "PROVIDER_EXECUTION_CAPACITY_EXHAUSTED",
    "RESIDENT_SUPERVISOR_CAPACITY_EXHAUSTED",
    "RESOURCE_HEALTH_REJECTED",
    "REVIEWER_REUSE_NOT_ELIGIBLE",
    "SESSION_IDENTITY_AMBIGUOUS",
    "SESSION_IDENTITY_UNAVAILABLE",
    "TOTAL_PROVIDER_CAPACITY_EXHAUSTED",
    "WORK_CONTEXT_CAPACITY_EXHAUSTED",
}


class SidecarRuntimeRecoveryRequired(RuntimeError):
    """Tell the MCP client that stale privileged sidecar work was safely rejected."""


class SidecarRuntimeIdentityUnavailable(RuntimeError):
    """Fail closed until a managed sidecar can prove active-code compatibility."""


def _runtime_reconnect_attempt_tag() -> str:
    """Bind a reconnect signal to the exact resumed MCP runtime when possible."""
    attempt_token = os.environ.get(PROVIDER_RECONNECT_ATTEMPT_ENV, "")
    if not re.fullmatch(r"[0-9a-f]{32}", attempt_token):
        return ""
    return f" [{PROVIDER_RECONNECT_ATTEMPT_ENV}={attempt_token}]"


def _runtime_reconnect_required_message(detail: str) -> str:
    """Return a replay-safe reconnect error for this sidecar process."""
    marker = f"CAO_SIDECAR_RECONNECT_REQUIRED{_runtime_reconnect_attempt_tag()}"
    return f"{marker}: {detail}"


def _current_process_start_ticks(proc_root: Path = Path("/proc")) -> Optional[int]:
    """Return this sidecar's Linux process-start identity."""
    process_id = os.getpid()
    try:
        stat_text = (proc_root / str(process_id) / "stat").read_text(encoding="utf-8")
        suffix = stat_text[stat_text.rfind(")") + 2 :].split()
        start_ticks = int(suffix[19])
    except (OSError, ValueError, IndexError):
        return None
    return start_ticks if start_ticks > 0 else None


def _register_provider_reconnect_runtime_ready() -> None:
    """Register a newly launched, nonce-bound MCP runtime after initialize."""
    attempt_token = os.environ.get(PROVIDER_RECONNECT_ATTEMPT_ENV)
    if not attempt_token:
        return
    terminal_id = os.environ.get("CAO_TERMINAL_ID")
    start_ticks = _current_process_start_ticks()
    if (
        not terminal_id
        or start_ticks is None
        or _SIDECAR_RUNTIME_GENERATION != ACTIVE_RUNTIME_GENERATION
    ):
        logger.warning("Rejected provider reconnect sidecar readiness registration")
        return
    try:
        accepted = record_workflow_provider_reconnect_runtime_ready(
            terminal_id,
            attempt_token,
            ACTIVE_RUNTIME_GENERATION,
            os.getpid(),
            start_ticks,
        )
    except Exception:
        logger.warning(
            "Provider reconnect sidecar readiness registration failed",
            exc_info=True,
        )
        return
    if not accepted:
        logger.warning("Provider reconnect sidecar readiness registration was fenced")


class _ProviderReconnectReadinessMiddleware(Middleware):
    """Publish readiness only after FastMCP accepts the client initialize request."""

    async def on_initialize(
        self,
        context: MiddlewareContext[mcp_types.InitializeRequest],
        call_next: CallNext[mcp_types.InitializeRequest, mcp_types.InitializeResult | None],
    ) -> mcp_types.InitializeResult | None:
        result = await call_next(context)
        if result is not None:
            _register_provider_reconnect_runtime_ready()
        return result


def _suspend_provider_execution(logical_turn_id: int) -> tuple[str, bool]:
    """Release a parent's slot while a blocking handoff waits for its child."""
    terminal_id = os.environ.get("CAO_TERMINAL_ID", "")
    released = bool(terminal_id and release_provider_execution(terminal_id, logical_turn_id))
    if released:
        inbox_service.wake_provider_execution_queue()
    return terminal_id, released


async def _resume_provider_execution(
    terminal_id: str, logical_turn_id: int, suspended: bool
) -> None:
    """Reacquire before returning a tool result that resumes the same model turn."""
    if not suspended:
        return
    from cli_agent_orchestrator.services.operations_service import (
        AdmissionDenied,
        acquire_provider_execution_slot,
    )

    while get_workflow_status(terminal_id) == "open":
        metadata = get_terminal_metadata(terminal_id)
        if metadata is None or metadata.get("runtime_lifecycle") != "running":
            return
        try:
            acquire_provider_execution_slot(terminal_id, logical_turn_id)
            return
        except AdmissionDenied as exc:
            if exc.reason_code not in {
                "PROVIDER_EXECUTION_CAPACITY_EXHAUSTED",
                "RESOURCE_HEALTH_REJECTED",
            }:
                raise
            await asyncio.sleep(0.1)


def _active_runtime_generation() -> Optional[str]:
    """Read the generation of the API process serving this sidecar.

    The caller distinguishes a positively proven identity from every transport,
    HTTP, or schema failure. Managed sidecars must never treat unavailable
    compatibility evidence as permission to mutate durable state.
    """
    try:
        response = requests.get(f"{API_BASE_URL}{_RUNTIME_GENERATION_PATH}", timeout=2)
        response.raise_for_status()
        generation = response.json().get("generation")
    except (requests.RequestException, ValueError, AttributeError):
        return None
    return generation if isinstance(generation, str) and generation else None


def _fence_privileged_runtime() -> None:
    """Reject local privileged work from a sidecar launched by an old runtime.

    The immutable import-time compatibility identity is the code-ownership
    proof. In particular, changing ``os.environ[CAO_RUNTIME_GENERATION]`` in a
    surviving process must never bless old code to perform lifecycle,
    provider-exit, or worktree cleanup mutations.
    """
    sidecar_generation = _SIDECAR_RUNTIME_GENERATION
    if not sidecar_generation:
        return
    active_generation = _active_runtime_generation()
    if not active_generation:
        raise SidecarRuntimeIdentityUnavailable(
            "CAO_RUNTIME_GENERATION_UNAVAILABLE: privileged operation was not started; "
            "retry after the active runtime identity is available"
        )
    if sidecar_generation == active_generation:
        return
    logger.info(
        "Fenced stale cao-mcp-server sidecar generation %s; active generation is %s",
        sidecar_generation,
        active_generation,
    )
    raise SidecarRuntimeRecoveryRequired(
        _runtime_reconnect_required_message(
            "privileged operation was not started; reconnect/reinitialize before retrying"
        )
    )


def _runtime_reconnect_response(child_terminal_id: str) -> Dict[str, Any]:
    """Return the structured retry boundary used by retirement entrypoints."""
    return {
        "success": False,
        "child_terminal_id": child_terminal_id,
        "status": "runtime_reconnect_required",
        "recoverable": True,
        "error": _runtime_reconnect_required_message(
            "child retirement was not started; reconnect/reinitialize before retrying"
        ),
    }


def _runtime_identity_unavailable_response(child_terminal_id: str) -> Dict[str, Any]:
    """Return a retry boundary without claiming a proven code mismatch."""
    return {
        "success": False,
        "child_terminal_id": child_terminal_id,
        "status": "runtime_identity_unavailable",
        "recoverable": True,
        "error": (
            "CAO_RUNTIME_GENERATION_UNAVAILABLE: child retirement was not started; "
            "retry after the active runtime identity is available"
        ),
    }


def _retirement_runtime_fence(child_terminal_id: str) -> Optional[Dict[str, Any]]:
    """Recheck code ownership immediately before a retirement mutation boundary."""
    try:
        _fence_privileged_runtime()
    except SidecarRuntimeRecoveryRequired:
        return _runtime_reconnect_response(child_terminal_id)
    except SidecarRuntimeIdentityUnavailable:
        return _runtime_identity_unavailable_response(child_terminal_id)
    return None


def _finish_stale_retirement_boundary(
    effect: Dict[str, Any],
    child_terminal_id: str,
    runtime_fence: Dict[str, Any],
    *,
    claim_token: Optional[str] = None,
    exit_reserved_but_undispatched: bool = False,
) -> Dict[str, Any]:
    """Close local durable state when a generation fence rejects retirement."""
    released = True
    if claim_token is not None:
        if exit_reserved_but_undispatched:
            released = cancel_reserved_completed_assigned_child_retirement_exit(
                child_terminal_id, claim_token
            )
        else:
            released = release_completed_assigned_child_retirement(child_terminal_id, claim_token)
    _finish_privileged_effect(effect, "rejected" if released else "indeterminate")
    response = dict(runtime_fence)
    if not released:
        marker = (
            "CAO_RUNTIME_GENERATION_UNAVAILABLE"
            if response.get("status") == "runtime_identity_unavailable"
            else "CAO_SIDECAR_RECONNECT_REQUIRED"
        )
        if marker == "CAO_SIDECAR_RECONNECT_REQUIRED":
            response["error"] = _runtime_reconnect_required_message(
                "the durable retirement claim was retained for safe reconciliation"
            )
        else:
            response["error"] = (
                f"{marker}: the durable retirement claim was retained for safe reconciliation"
            )
    return response


def _workflow_effect_key(kind: str, *parts: object) -> str:
    """Return a stable operation identity without persisting task contents."""
    digest = hashlib.sha256("\x1f".join(map(str, parts)).encode()).hexdigest()
    return f"{kind}:{digest}"


def _claim_privileged_effect(
    logical_turn_id: int, kind: str, *identity: object
) -> Optional[Dict[str, Any]]:
    """Enforce the durable effect boundary at every privileged MCP entrypoint."""
    _fence_privileged_runtime()
    terminal_id = os.environ.get("CAO_TERMINAL_ID")
    if not terminal_id:
        return None
    return claim_workflow_effect(
        terminal_id, logical_turn_id, kind, _workflow_effect_key(kind, *identity)
    )


def _privileged_effect_rejection(
    logical_turn_id: int, kind: str, *identity: object
) -> Dict[str, Any]:
    """Return the durable reason for a rejected privileged operation."""
    terminal_id = os.environ.get("CAO_TERMINAL_ID")
    detail = describe_workflow_effect_rejection(
        terminal_id, logical_turn_id, kind, _workflow_effect_key(kind, *identity)
    )
    return {"success": False, "accepted": False, **detail, "error": detail["explanation"]}


def _finish_privileged_effect(effect: Dict[str, Any], outcome: str) -> None:
    terminal_id = os.environ.get("CAO_TERMINAL_ID")
    if terminal_id:
        finish_workflow_effect(terminal_id, effect["id"], effect["claim_token"], outcome)


def _delegation_effect_outcome(result: Any) -> str:
    """Classify delegation truth at the non-transactional launch boundary."""
    if isinstance(result, dict):
        success = bool(result.get("success"))
        terminal_id = result.get("terminal_id")
        reason_code = result.get("reason_code")
    else:
        success = bool(getattr(result, "success", False))
        terminal_id = getattr(result, "terminal_id", None)
        reason_code = getattr(result, "reason_code", None)
    if success:
        return "completed"
    if terminal_id is None and reason_code in _SAFE_PRE_EFFECT_ADMISSION_REASONS:
        # The API rejected admission before creating a child terminal. Keep the
        # attempt visible but safely reclaimable under this same logical turn.
        return "not_admitted"
    return "indeterminate"


def _finish_retired_child(
    effect: Dict[str, Any],
    child_terminal_id: str,
    claim_token: str,
    *,
    already_retired: bool = False,
    retiring_supervisor_terminal_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Fulfil exact cleanup intent, then CAS-seal final retirement."""
    runtime_fence = _retirement_runtime_fence(child_terminal_id)
    if runtime_fence is not None:
        return _finish_stale_retirement_boundary(
            effect,
            child_terminal_id,
            runtime_fence,
            claim_token=claim_token,
        )
    cleanup_state = get_assigned_child_retirement_cleanup_intent(child_terminal_id, claim_token)
    if cleanup_state is None:
        _finish_privileged_effect(effect, "indeterminate")
        return {
            "success": False,
            "child_terminal_id": child_terminal_id,
            "status": "retirement_cleanup_pending",
            "recoverable": True,
            "error": "retirement_cleanup_intent_not_confirmed",
            "reason_code": "RETIREMENT_CLEANUP_INTENT_NOT_CONFIRMED",
        }
    if retiring_supervisor_terminal_id is not None and not (
        revalidate_historical_assigned_child_retirement(
            retiring_supervisor_terminal_id, child_terminal_id, claim_token
        )
    ):
        _finish_privileged_effect(effect, "rejected")
        return {
            "success": False,
            "child_terminal_id": child_terminal_id,
            "status": "retirement_cleanup_pending",
            "recoverable": True,
            "error": "historical_retirement_authority_lost",
            "reason_code": "HISTORICAL_RETIREMENT_AUTHORITY_LOST",
        }
    runtime_fence = _retirement_runtime_fence(child_terminal_id)
    if runtime_fence is not None:
        return _finish_stale_retirement_boundary(
            effect,
            child_terminal_id,
            runtime_fence,
            claim_token=claim_token,
        )
    try:
        terminal_service.cleanup_managed_worktree(cleanup_state["intent"])
    except Exception as exc:
        _finish_privileged_effect(effect, "indeterminate")
        return {
            "success": False,
            "child_terminal_id": child_terminal_id,
            "status": "retirement_cleanup_pending",
            "recoverable": True,
            "error": "managed_worktree_cleanup_not_confirmed",
            "reason_code": "MANAGED_WORKTREE_CLEANUP_NOT_CONFIRMED",
            "detail": str(exc),
        }
    runtime_fence = _retirement_runtime_fence(child_terminal_id)
    if runtime_fence is not None:
        return _finish_stale_retirement_boundary(
            effect,
            child_terminal_id,
            runtime_fence,
            claim_token=claim_token,
        )
    if not complete_assigned_child_retirement(
        child_terminal_id,
        claim_token,
        cleanup_state["intent"],
        retiring_supervisor_terminal_id=retiring_supervisor_terminal_id,
    ):
        _finish_privileged_effect(effect, "indeterminate")
        return {
            "success": False,
            "child_terminal_id": child_terminal_id,
            "status": "retirement_cleanup_pending",
            "recoverable": True,
            "error": "retirement_cleanup_finalization_not_confirmed",
            "reason_code": "RETIREMENT_CLEANUP_FINALIZATION_NOT_CONFIRMED",
        }
    _finish_privileged_effect(effect, "completed")
    response: Dict[str, Any] = {
        "success": True,
        "child_terminal_id": child_terminal_id,
        "status": "already_retired" if already_retired else "retired",
    }
    if already_retired:
        response["already_retired"] = True
    return response


def _already_retired_response(effect: Dict[str, Any], child_terminal_id: str) -> Dict[str, Any]:
    _finish_privileged_effect(effect, "completed")
    return {
        "success": True,
        "child_terminal_id": child_terminal_id,
        "status": "already_retired",
        "already_retired": True,
    }


def _durable_handoff_fields(child_terminal_id: str) -> Dict[str, Any]:
    result = get_delegation_result_for_assignment(child_terminal_id)
    if result is None:
        return {}
    return _handoff_result_fields(result)


def _handoff_result_fields(result: Dict[str, Any]) -> Dict[str, Any]:
    """Project immutable delegation-result metadata onto a handoff response."""
    document = result.get("document")
    result_format = document.get("format") if isinstance(document, dict) else None
    return {
        "result_id": result["id"],
        "result_status": result["status"],
        "schema_version": result["schema_version"],
        "result_format": result_format if isinstance(result_format, str) else None,
    }


def _durable_handoff_terminal_result(
    parent_terminal_id: Optional[str], terminal_id: str
) -> Optional[HandoffResult]:
    """Return an already cleanup-safe durable result without provider polling.

    A restart can leave a handoff relation in a recovery state after its
    immutable result committed.  The result is then stronger evidence than a
    transient provider/process observation, and polling the provider first can
    turn a completed handoff into a needless full timeout.

    A direct claim is deliberately excluded: it commits the output before the
    provider /exit and direct-cleanup acknowledgement.  Returning it here
    would strand those exactly-once cleanup steps.
    """
    if not parent_terminal_id or get_handoff_parent_terminal_id(terminal_id) != parent_terminal_id:
        return None

    if get_claimed_handoff_child_result_direct(parent_terminal_id, terminal_id) is not None:
        return None

    result = get_delegation_result_for_assignment(terminal_id)
    if result is None or result["status"] == "awaiting":
        return None

    document = result.get("document")
    output = document.get("body_markdown") if isinstance(document, dict) else None
    fields = _handoff_result_fields(result)
    if result["status"] == "complete" and isinstance(output, str):
        return HandoffResult(
            success=True,
            message=f"Handoff worker {terminal_id} completed",
            output=output,
            terminal_id=terminal_id,
            **fields,
            state=HandoffState.COMPLETED,
        )

    return HandoffResult(
        success=False,
        message=f"Handoff worker {terminal_id} has durable {result['status']} result",
        output=output if isinstance(output, str) else None,
        terminal_id=terminal_id,
        **fields,
        state=HandoffState.FAILED,
    )


_LIVE_TERMINAL_LIFECYCLE = "running"
_PROGRESS_ONLY_RESULT_PATTERN = re.compile(
    r"^\s*[•*\-]?\s*(?:codex\s+is\s+)?"
    r"(?:working|thinking|processing|running|starting|analyzing|creating|"
    r"executing|reading|searching|editing|applying)(?:\s*\([^)]*\))?[.!…]*\s*$",
    re.IGNORECASE,
)
_CODEX_PROGRESS_SPINNER_RESULT_PATTERN = re.compile(
    r"^\s*[•*]?\s*(?:working|thinking|processing|running|starting|analyzing|"
    r"creating|executing|reading|searching|editing|applying)\b[^\n]*"
    r"\(\d+(?:\.\d+)?s\s*[•·]\s*esc\s+to\s+interrupt\)\s*[.!…]*\s*$",
    re.IGNORECASE,
)
_ANSI_CONTROL_PATTERN = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[()][0-2AB])"
)
_CODEX_CONTEXT_EXHAUSTION_RESULT_PATTERN = re.compile(
    r"""^\s*(?:(?:⚠️?|!+)\s*)?
    (?:
        (?:the\s+)?context(?:\s+(?:window|limit))?(?:\s+(?:has|is))?
        \s+(?:been\s+)?(?:exhausted|full|reached|exceeded)
        |(?:you(?:'|’)ve|you\s+have)\s+reached\s+(?:the\s+)?context
        (?:\s+(?:window|limit))?
        |out\s+of\s+context
    )
    (?:
        [.!…]?\s*
        (?:
            (?:please\s+)?(?:start|open|create)\s+(?:a\s+)?new\s+(?:conversation|chat)
            |(?:please\s+)?(?:use|run|try)\s+/?compact(?:ion)?(?:\s+to\s+continue)?
            |compact(?:\s+(?:the\s+)?(?:conversation|context))?(?:\s+to\s+continue)?
            |(?:please\s+)?restart(?:\s+(?:the\s+)?(?:conversation|chat))?
        )?[.!…]?\s*
    )$""",
    re.IGNORECASE | re.VERBOSE,
)
_CODEX_IDLE_CHROME_ONLY_RESULT_PATTERN = re.compile(
    r"""^\s*
    (?:Welcome\s+to\s+Codex(?:\r?\n(?:workdir|directory):[^\n]+)*)?
    (?:\r?\n)?
    (?:
        [›❯][ \t]*
        |[›❯][^\n]*\r?\n\s*
        (?:\?\s+for\s+shortcuts[^\n]*|[^\n]*\b\d+%\s+(?:context\s+)?left\s+·\s+(?:~|/)[^\n]*)
    )\s*$""",
    re.IGNORECASE | re.VERBOSE,
)
_INCOMPLETE_RESULT_PATTERN = re.compile(r"(?:\.\.\.|…)$")

# Environment variable to enable/disable working_directory parameter
ENABLE_WORKING_DIRECTORY = os.getenv("CAO_ENABLE_WORKING_DIRECTORY", "false").lower() == "true"

# Environment variable to enable/disable automatic sender terminal ID injection
ENABLE_SENDER_ID_INJECTION = os.getenv("CAO_ENABLE_SENDER_ID_INJECTION", "false").lower() == "true"

# Create MCP server
mcp = FastMCP(
    "cao-mcp-server",
    instructions="""
    # CLI Agent Orchestrator MCP Server

    This server provides tools to facilitate terminal delegation within CLI Agent Orchestrator sessions.

    ## Best Practices

    - Use specific agent profiles and providers
    - Provide clear and concise messages
    - Ensure you're running within a CAO terminal (CAO_TERMINAL_ID must be set)
    """,
)
mcp.add_middleware(_ProviderReconnectReadinessMiddleware())

LOAD_SKILL_TOOL_DESCRIPTION = """Retrieve the full Markdown body of an available skill from cao-server.

Use this tool when your prompt lists a CAO skill and you need its full instructions at runtime.

Args:
    name: Name of the skill to retrieve

Returns:
    The skill content on success, or a dict with success=False and an error message on failure
"""


def _resolve_child_allowed_tools(
    parent_allowed_tools: Optional[list], child_profile_name: str
) -> Optional[str]:
    """Resolve allowed_tools for a child terminal via intersection.

    The child gets at most the union of: what the parent allows + what the
    child profile specifies. If the parent is unrestricted ("*"), the child
    profile's allowedTools are used as-is.

    Returns:
        Comma-separated string of allowed tools, or None for unrestricted.
    """
    from cli_agent_orchestrator.utils.agent_profiles import load_agent_profile
    from cli_agent_orchestrator.utils.tool_mapping import resolve_allowed_tools

    try:
        child_profile = load_agent_profile(child_profile_name)
        mcp_server_names = (
            list(child_profile.mcpServers.keys()) if child_profile.mcpServers else None
        )
        child_allowed = resolve_allowed_tools(
            child_profile.allowedTools, child_profile.role, mcp_server_names
        )
    except FileNotFoundError:
        child_allowed = None

    # If parent is unrestricted or has no restrictions, use child's tools
    if parent_allowed_tools is None or "*" in parent_allowed_tools:
        if child_allowed:
            return ",".join(child_allowed)
        return None

    # If child has no opinion (None), inherit parent's restrictions
    if child_allowed is None:
        return ",".join(parent_allowed_tools)

    # If child explicitly requests unrestricted ("*"), honor it
    if "*" in child_allowed:
        return None

    # Both have restrictions: child gets its own profile tools
    # (the child profile defines what it needs; parent's restrictions
    # are enforced by the parent not delegating unauthorized work)
    return ",".join(child_allowed)


def _create_terminal(
    agent_profile: str, working_directory: Optional[str] = None
) -> Tuple[str, str]:
    """Create a new terminal with the specified agent profile.

    Args:
        agent_profile: Agent profile for the terminal
        working_directory: Optional working directory for the terminal

    Returns:
        Tuple of (terminal_id, provider)

    Raises:
        Exception: If terminal creation fails
    """
    provider = DEFAULT_PROVIDER
    parent_allowed_tools = None
    explicit_working_directory = working_directory is not None

    # Get current terminal ID from environment
    current_terminal_id = os.environ.get("CAO_TERMINAL_ID")
    if current_terminal_id:
        # Get terminal metadata via API
        response = requests.get(f"{API_BASE_URL}/terminals/{current_terminal_id}")
        response.raise_for_status()
        terminal_metadata = response.json()

        provider = terminal_metadata["provider"]
        session_id = terminal_metadata.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise TerminalAdmissionError(
                "SESSION_IDENTITY_UNAVAILABLE",
                "terminal admission denied: canonical parent session identity is unavailable",
            )
        parent_allowed_tools = terminal_metadata.get("allowed_tools")

        # If no working_directory specified, get conductor's current directory
        if working_directory is None:
            try:
                response = requests.get(
                    f"{API_BASE_URL}/terminals/{current_terminal_id}/working-directory"
                )
                if response.status_code == 200:
                    working_directory = response.json().get("working_directory")
                    logger.info(f"Inherited working directory from conductor: {working_directory}")
                else:
                    logger.warning(
                        f"Failed to get conductor's working directory (status {response.status_code}), "
                        "will use server default"
                    )
            except Exception as e:
                logger.warning(
                    f"Error fetching conductor's working directory: {e}, will use server default"
                )

        # Resolve child's allowed_tools via inheritance
        child_allowed_tools = _resolve_child_allowed_tools(parent_allowed_tools, agent_profile)

        # Create new terminal in existing session - always pass working_directory
        params = {"provider": provider, "agent_profile": agent_profile}
        if not explicit_working_directory:
            from cli_agent_orchestrator.utils.agent_profiles import load_agent_profile

            try:
                role = load_agent_profile(agent_profile).role
            except (FileNotFoundError, RuntimeError):
                role = None
            params["managed_worktree_kind"] = "reviewer" if role == "reviewer" else "task"
        if working_directory:
            params["working_directory"] = working_directory
        if child_allowed_tools:
            params["allowed_tools"] = child_allowed_tools

        response = requests.post(
            f"{API_BASE_URL}/sessions/{quote(session_id, safe='')}/terminals",
            params=params,
        )
        if response.status_code >= 400:
            raise TerminalAdmissionError.from_response(response)
        terminal = response.json()
    else:
        # Create new session with terminal
        session_name = generate_session_name()
        params = {
            "provider": provider,
            "agent_profile": agent_profile,
            "session_name": session_name,
        }
        if working_directory:
            params["working_directory"] = working_directory

        response = requests.post(f"{API_BASE_URL}/sessions", params=params)
        response.raise_for_status()
        terminal = response.json()

    return terminal["id"], provider


class TerminalAdmissionError(RuntimeError):
    """Preserve a stable admission reason across the HTTP/MCP boundary."""

    def __init__(self, reason_code: str, message: str):
        self.reason_code = reason_code
        super().__init__(message)

    @classmethod
    def from_response(cls, response: requests.Response) -> "TerminalAdmissionError":
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        if isinstance(detail, dict) and isinstance(detail.get("reason_code"), str):
            return cls(detail["reason_code"], f"terminal admission denied: {detail['reason_code']}")
        if isinstance(detail, str) and detail:
            return cls("TERMINAL_ADMISSION_FAILED", detail)
        return cls(
            "TERMINAL_ADMISSION_FAILED", f"terminal admission failed ({response.status_code})"
        )


def _send_direct_input(
    terminal_id: str, message: str, orchestration_type: OrchestrationType, binding: str
) -> None:
    """Send input directly to a terminal (bypasses inbox).

    Args:
        terminal_id: Terminal ID
        message: Message to send
        orchestration_type: Orchestration mode for plugin event emission

    Raises:
        Exception: If sending fails
    """
    response = requests.post(
        f"{API_BASE_URL}/_internal/terminals/{terminal_id}/input",
        params={
            "message": message,
            "binding": binding,
            "sender_id": os.environ.get("CAO_TERMINAL_ID", "supervisor"),
            "orchestration_type": orchestration_type,
        },
    )
    response.raise_for_status()


def _send_direct_input_handoff(terminal_id: str, provider: str, message: str, binding: str) -> None:
    """Send handoff payload to an agent, prepending orchestrator instructions if needed."""
    message = _with_no_tg_notify(message)
    # For Codex provider: prepend handoff context so the worker agent knows
    # this is a blocking handoff and should simply output results rather than
    # attempting to call send_message back to the supervisor.
    if provider == "codex":
        supervisor_id = os.environ.get("CAO_TERMINAL_ID", "unknown")
        handoff_message = (
            f"[CAO Handoff] Supervisor terminal ID: {supervisor_id}. "
            "This is a blocking handoff — the orchestrator will automatically "
            "capture your response when you finish. Complete the task and output "
            "your results directly. Submit the structured result with "
            "submit_handoff_result_v1(logical_turn_id=<current logical-turn>, "
            "document=<V1 object>) immediately before finishing; a successful call "
            "is the authoritative V1 artifact. Then emit exactly two logical lines "
            "for compatibility: line 1 must be CAO_RESULT_V1; line 2 must be one "
            "compact single-line JSON object matching V1, with no Markdown fence, "
            "bullet, prefix, suffix, extra text, or extra blank line. "
            "The injected trailing NO_TG_NOTIFY directive is input-only policy context; "
            "do not echo it in your final response. "
            "Do NOT use send_message to notify the supervisor "
            "unless explicitly needed — just do the work and present your deliverables.\n\n"
            f"{message}"
        )
    else:
        handoff_message = message

    _send_direct_input(terminal_id, handoff_message, OrchestrationType.HANDOFF, binding)


def _send_direct_input_assign(terminal_id: str, message: str, binding: str) -> None:
    """Send assign payload to a worker agent, appending callback instructions."""
    # Auto-inject sender terminal ID suffix when enabled
    if ENABLE_SENDER_ID_INJECTION:
        sender_id = os.environ.get("CAO_TERMINAL_ID", "unknown")
        message += (
            f"\n\n[Assigned by terminal {sender_id}. "
            f"When done, send results back to terminal {sender_id} using send_message]"
        )

    _send_direct_input(terminal_id, _with_no_tg_notify(message), OrchestrationType.ASSIGN, binding)


def _with_no_tg_notify(message: str) -> str:
    """Make every delegated payload comply with Telegram ownership policy."""
    non_empty = [line.strip() for line in message.splitlines() if line.strip()]
    if non_empty and (non_empty[0] == "NO_TG_NOTIFY" or non_empty[-1] == "NO_TG_NOTIFY"):
        return message
    return f"{message.rstrip()}\n\nNO_TG_NOTIFY"


def _send_to_inbox(receiver_id: str, message: str) -> Dict[str, Any]:
    """Send message to another terminal's inbox (queued delivery when IDLE).

    Args:
        receiver_id: Target terminal ID
        message: Message content

    Returns:
        Dict with message details

    Raises:
        ValueError: If CAO_TERMINAL_ID not set
        Exception: If API call fails
    """
    sender_id = os.getenv("CAO_TERMINAL_ID")
    if not sender_id:
        raise ValueError("CAO_TERMINAL_ID not set - cannot determine sender")

    response = requests.post(
        f"{API_BASE_URL}/terminals/{receiver_id}/inbox/messages",
        json={
            "sender_id": sender_id,
            "message": message,
        },
    )
    response.raise_for_status()
    return response.json()


def _extract_error_detail(response: requests.Response, fallback: str) -> str:
    """Extract a human-readable error detail from an API response."""
    try:
        payload = response.json()
    except ValueError:
        return fallback

    detail = payload.get("detail")
    if isinstance(detail, str) and detail:
        return detail
    return fallback


def _load_skill_impl(name: str) -> Union[str, Dict[str, Any]]:
    """Fetch a skill body from cao-server and return content or a structured error."""
    try:
        response = requests.get(f"{API_BASE_URL}/skills/{name}")
        response.raise_for_status()
        return response.json()["content"]
    except requests.HTTPError as exc:
        detail = str(exc)
        if exc.response is not None:
            detail = _extract_error_detail(exc.response, detail)
        return {"success": False, "error": detail}
    except requests.ConnectionError:
        return {
            "success": False,
            "error": "Failed to connect to cao-server. The server may not be running.",
        }
    except Exception as exc:
        return {"success": False, "error": f"Failed to retrieve skill: {str(exc)}"}


# Implementation functions
def _read_terminal_state(terminal_id: str) -> tuple[str, str]:
    """Read authoritative provider status and lifecycle from the loopback API."""
    response = requests.get(f"{API_BASE_URL}/terminals/{terminal_id}")
    response.raise_for_status()
    terminal = response.json()
    if not isinstance(terminal, dict):
        raise ValueError("terminal response was not an object")
    terminal_status = terminal.get("status")
    if not isinstance(terminal_status, str):
        raise ValueError("terminal response did not include a status")
    lifecycle = terminal.get("lifecycle")
    if not isinstance(lifecycle, str):
        raise ValueError("terminal response included an invalid lifecycle")
    return terminal_status, lifecycle


def _read_handoff_terminal(terminal_id: str) -> tuple[str, str]:
    """Read provider status and process lifecycle for one durable terminal id."""
    return _read_terminal_state(terminal_id)


def _read_handoff_output(terminal_id: str) -> object:
    response = requests.get(
        f"{API_BASE_URL}/terminals/{terminal_id}/output", params={"mode": "last"}
    )
    response.raise_for_status()
    return response.json().get("output")


def _handoff_output_problem(output: object) -> Optional[str]:
    """Reject known Codex capture artifacts without narrowing final reports."""
    if not isinstance(output, str) or not output.strip():
        return "empty or invalid final output"
    is_v1, document = parse_v1_result_capture(output)
    if is_v1 and document is None:
        return "malformed CAO_RESULT_V1 final output"
    # Terminal capture can retain SGR/cursor sequences around a live Codex
    # spinner.  The provider strips the same presentation noise before status
    # detection and extraction; normalize it here too so a decorated spinner
    # cannot bypass the final-output guard and trigger /exit.
    final_output = _ANSI_CONTROL_PATTERN.sub("", output)
    final_output = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", final_output).strip()
    if _CODEX_CONTEXT_EXHAUSTION_RESULT_PATTERN.fullmatch(final_output):
        return "context-exhausted output"
    if _CODEX_IDLE_CHROME_ONLY_RESULT_PATTERN.fullmatch(final_output):
        return "idle prompt chrome without a final verdict"
    if _PROGRESS_ONLY_RESULT_PATTERN.fullmatch(
        final_output
    ) or _CODEX_PROGRESS_SPINNER_RESULT_PATTERN.fullmatch(final_output):
        return "progress-only output"
    if _INCOMPLETE_RESULT_PATTERN.search(final_output):
        return "incomplete output"
    return None


def _waiting_handoff_result(terminal_id: str, timeout: int, reason: str) -> HandoffResult:
    return HandoffResult(
        success=False,
        message=(
            f"Handoff wait slice expired after {timeout} seconds: {reason}. "
            "Worker remains live; call await_handoff with this terminal_id to resume."
        ),
        output=None,
        terminal_id=terminal_id,
        state=HandoffState.WAITING,
    )


def _provider_content_unavailable_handoff_result(terminal_id: str) -> HandoffResult:
    """Retain a live handoff for deliberate continuation without false success."""
    return HandoffResult(
        success=False,
        message=(
            "Provider response unavailable; workflow state is preserved. "
            "Continue the child through its normal workflow input where permitted, "
            "then call await_handoff with this terminal_id to resume."
        ),
        output=None,
        terminal_id=terminal_id,
        reason_code="PROVIDER_CONTENT_UNAVAILABLE",
        workflow_state=get_workflow_status(terminal_id),
        state=HandoffState.WAITING,
    )


def _has_provider_content_unavailable_outcome(terminal_id: str) -> bool:
    """Recognize only the canonical normalized outcome, never a truthy mock/value."""
    outcome = get_workflow_provider_outcome(terminal_id)
    return isinstance(outcome, dict) and outcome.get("code") == "PROVIDER_CONTENT_UNAVAILABLE"


def _runtime_reconnect_handoff_result(terminal_id: Optional[str]) -> HandoffResult:
    return HandoffResult(
        success=False,
        message=_runtime_reconnect_required_message(
            "handoff lifecycle mutation was not started; reconnect/reinitialize before retrying"
        ),
        output=None,
        terminal_id=terminal_id,
        state=HandoffState.FAILED,
    )


def _runtime_identity_unavailable_handoff_result(
    terminal_id: Optional[str],
) -> HandoffResult:
    return HandoffResult(
        success=False,
        message=(
            "CAO_RUNTIME_GENERATION_UNAVAILABLE: handoff lifecycle mutation was not started; "
            "retry after the active runtime identity is available"
        ),
        output=None,
        terminal_id=terminal_id,
        state=HandoffState.FAILED,
    )


def _handoff_runtime_fence_result(terminal_id: str) -> Optional[HandoffResult]:
    try:
        _fence_privileged_runtime()
    except SidecarRuntimeRecoveryRequired:
        return _runtime_reconnect_handoff_result(terminal_id)
    except SidecarRuntimeIdentityUnavailable:
        return _runtime_identity_unavailable_handoff_result(terminal_id)
    return None


def _finish_claimed_handoff_runtime_fence(
    terminal_id: str,
    claim_token: Optional[str],
    runtime_fence: HandoffResult,
) -> HandoffResult:
    """Release a pre-cleanup handoff retirement claim before retrying."""
    if claim_token is None or release_completed_assigned_child_retirement(terminal_id, claim_token):
        return runtime_fence
    marker = (
        "CAO_RUNTIME_GENERATION_UNAVAILABLE"
        if "CAO_RUNTIME_GENERATION_UNAVAILABLE" in runtime_fence.message
        else "CAO_SIDECAR_RECONNECT_REQUIRED"
    )
    message = f"{marker}: handoff retirement state release was not confirmed"
    if marker == "CAO_SIDECAR_RECONNECT_REQUIRED":
        message = _runtime_reconnect_required_message(
            "handoff retirement state release was not confirmed"
        )
    return HandoffResult(
        success=False,
        message=message,
        output=None,
        terminal_id=terminal_id,
        state=HandoffState.FAILED,
    )


def _cleanup_claimed_handoff_result(
    parent_terminal_id: Optional[str],
    terminal_id: str,
    lifecycle: str,
    timeout: int,
) -> HandoffResult:
    """Exit, retire managed worktree authority, then deliver one direct claim."""
    runtime_fence = _handoff_runtime_fence_result(terminal_id)
    if runtime_fence is not None:
        return runtime_fence
    if not parent_terminal_id:
        return _waiting_handoff_result(
            terminal_id, timeout, "direct-handoff parent identity is unavailable"
        )
    if lifecycle == _LIVE_TERMINAL_LIFECYCLE:
        try:
            response = requests.post(f"{API_BASE_URL}/terminals/{terminal_id}/exit")
            response.raise_for_status()
        except Exception as cleanup_exc:
            return _waiting_handoff_result(
                terminal_id,
                timeout,
                f"cleanup is retryable after final-result claim: {cleanup_exc}",
            )
    retirement: Dict[str, Any] = {"cleanup_required": False}
    claim_token: Optional[str] = None
    if managed_handoff_retirement_required(parent_terminal_id, terminal_id) is True:
        runtime_fence = _handoff_runtime_fence_result(terminal_id)
        if runtime_fence is not None:
            return runtime_fence
        retirement = claim_completed_handoff_child_retirement(parent_terminal_id, terminal_id)
        if not retirement.get("eligible"):
            return _waiting_handoff_result(
                terminal_id,
                timeout,
                "managed retirement is not yet eligible after final-result claim: "
                f"{retirement.get('error', 'unknown')}",
            )
    if retirement.get("cleanup_required"):
        if not retirement.get("already_retired"):
            claim_token = retirement.get("claim_token")
            if not isinstance(claim_token, str) or not claim_token:
                return _waiting_handoff_result(
                    terminal_id,
                    timeout,
                    "managed retirement claim is not durable",
                )
            runtime_fence = _handoff_runtime_fence_result(terminal_id)
            if runtime_fence is not None:
                return _finish_claimed_handoff_runtime_fence(
                    terminal_id, claim_token, runtime_fence
                )
            cleanup_state = get_child_retirement_cleanup_intent(terminal_id, claim_token)
            if cleanup_state is None:
                return _waiting_handoff_result(
                    terminal_id,
                    timeout,
                    "managed retirement cleanup intent is not durable",
                )
            runtime_fence = _handoff_runtime_fence_result(terminal_id)
            if runtime_fence is not None:
                return _finish_claimed_handoff_runtime_fence(
                    terminal_id, claim_token, runtime_fence
                )
            try:
                terminal_service.cleanup_managed_worktree(cleanup_state["intent"])
            except Exception as cleanup_exc:
                return _waiting_handoff_result(
                    terminal_id,
                    timeout,
                    f"managed worktree cleanup is retryable: {cleanup_exc}",
                )
            runtime_fence = _handoff_runtime_fence_result(terminal_id)
            if runtime_fence is not None:
                return _finish_claimed_handoff_runtime_fence(
                    terminal_id, claim_token, runtime_fence
                )
            if not complete_child_retirement(
                terminal_id, claim_token, cleanup_state["intent"], "handoff"
            ):
                return _waiting_handoff_result(
                    terminal_id,
                    timeout,
                    "managed retirement finalization is retryable",
                )
            claim_token = None
    runtime_fence = _handoff_runtime_fence_result(terminal_id)
    if runtime_fence is not None:
        return _finish_claimed_handoff_runtime_fence(terminal_id, claim_token, runtime_fence)
    acknowledged_output = acknowledge_handoff_child_result_direct(parent_terminal_id, terminal_id)
    if acknowledged_output is None:
        return _waiting_handoff_result(
            terminal_id,
            timeout,
            "cleanup completed but final acknowledgement is retryable",
        )
    return HandoffResult(
        success=True,
        message=f"Handoff worker {terminal_id} completed",
        output=acknowledged_output,
        terminal_id=terminal_id,
        **_durable_handoff_fields(terminal_id),
        state=HandoffState.COMPLETED,
    )


async def _await_handoff_impl(terminal_id: str, timeout: int = 600) -> HandoffResult:
    """Await a live child by durable id without creating or messaging another worker."""
    deadline = time.monotonic() + timeout
    guard_reason = "completion was not observed"
    parent_terminal_id = os.environ.get("CAO_TERMINAL_ID")

    try:
        while True:
            durable_result = _durable_handoff_terminal_result(parent_terminal_id, terminal_id)
            if durable_result is not None:
                return durable_result

            terminal_status, lifecycle = _read_handoff_terminal(terminal_id)
            acknowledged_output = (
                get_acknowledged_handoff_child_result_direct(parent_terminal_id, terminal_id)
                if parent_terminal_id
                else None
            )
            if acknowledged_output is not None:
                return HandoffResult(
                    success=True,
                    message=f"Handoff worker {terminal_id} completed",
                    output=acknowledged_output,
                    terminal_id=terminal_id,
                    **_durable_handoff_fields(terminal_id),
                    state=HandoffState.COMPLETED,
                )
            claimed_output = (
                get_claimed_handoff_child_result_direct(parent_terminal_id, terminal_id)
                if parent_terminal_id
                else None
            )
            if claimed_output is not None:
                # The first claim was made only after two stable valid live
                # captures.  A cleanup retry owns that durable output, so do
                # not require a new capture that may have changed or vanished
                # after the earlier /exit failure.
                return _cleanup_claimed_handoff_result(
                    parent_terminal_id, terminal_id, lifecycle, timeout
                )
            if (
                lifecycle != _LIVE_TERMINAL_LIFECYCLE
                and terminal_status != TerminalStatus.COMPLETED.value
            ):
                # A successful /exit can lose its response.  A pre-existing
                # direct claim is then safe to acknowledge because cleanup is
                # observable, while an unclaimed exited child remains invalid.
                cleanup_output = (
                    acknowledge_handoff_child_result_direct(parent_terminal_id, terminal_id)
                    if parent_terminal_id
                    else None
                )
                if cleanup_output is not None:
                    return HandoffResult(
                        success=True,
                        message=f"Handoff worker {terminal_id} completed",
                        output=cleanup_output,
                        terminal_id=terminal_id,
                        **_durable_handoff_fields(terminal_id),
                        state=HandoffState.COMPLETED,
                    )
                cancel_child_assignments_for_terminal(terminal_id)
                return HandoffResult(
                    success=False,
                    message=(
                        "Handoff worker process already exited; persistent tmux state "
                        "is not a resumable worker"
                    ),
                    output=None,
                    terminal_id=terminal_id,
                    state=HandoffState.FAILED,
                )
            if terminal_status == TerminalStatus.ERROR.value:
                cancel_child_assignments_for_terminal(terminal_id)
                return HandoffResult(
                    success=False,
                    message="Handoff worker reached ERROR; worker was not exited by handoff",
                    output=None,
                    terminal_id=terminal_id,
                    state=HandoffState.FAILED,
                )

            if terminal_status == TerminalStatus.COMPLETED.value:
                active_children, failed_children = get_parent_completion_barrier(terminal_id)
                if active_children:
                    guard_reason = f"completion barrier is waiting for {active_children} assigned child result(s)"
                    if failed_children:
                        guard_reason += (
                            f" ({failed_children} callback delivery failure(s) retained)"
                        )
                else:
                    # A strict V1 submission is authenticated and immutable.
                    # Once this exact direct relation has completed, claim it
                    # before consulting mutable terminal rendering.  A missing
                    # or invalid stage intentionally falls through to the
                    # longstanding capture parser.
                    staged_claimed = (
                        claim_staged_handoff_result_direct(parent_terminal_id, terminal_id)
                        if parent_terminal_id
                        else None
                    )
                    if staged_claimed is True:
                        staged_output = get_claimed_handoff_child_result_direct(
                            parent_terminal_id, terminal_id
                        )
                        if staged_output is None:
                            return _waiting_handoff_result(
                                terminal_id,
                                timeout,
                                "staged final-result claim is awaiting durable cleanup state",
                            )
                        return _cleanup_claimed_handoff_result(
                            parent_terminal_id, terminal_id, lifecycle, timeout
                        )
                    if staged_claimed is False:
                        guard_reason = (
                            "direct-handoff staged result is not claimable by this parent"
                        )
                    elif _has_provider_content_unavailable_outcome(terminal_id):
                        return _provider_content_unavailable_handoff_result(terminal_id)
                    elif is_managed_structured_handoff_child(terminal_id):
                        # New managed handoffs possess an injected V1
                        # capability. Provider terminal prose is evidence of
                        # neither success nor a legacy compatibility result.
                        guard_reason = "managed handoff is awaiting authoritative V1 result"
                    else:
                        output = _read_handoff_output(terminal_id)
                        problem = _handoff_output_problem(output)
                        if problem is None:
                            verified_status, verified_lifecycle = _read_handoff_terminal(
                                terminal_id
                            )
                            if verified_lifecycle not in (
                                _LIVE_TERMINAL_LIFECYCLE,
                                "exited",
                            ):
                                return HandoffResult(
                                    success=False,
                                    message="Handoff worker exited during final-output validation",
                                    output=None,
                                    terminal_id=terminal_id,
                                    state=HandoffState.FAILED,
                                )
                            if verified_status == TerminalStatus.COMPLETED.value:
                                verified_output = _read_handoff_output(terminal_id)
                                verified_problem = _handoff_output_problem(verified_output)
                                if verified_problem is None and verified_output == output:
                                    direct_claimed = (
                                        claim_handoff_child_result_direct(
                                            parent_terminal_id, terminal_id, output
                                        )
                                        if parent_terminal_id
                                        else None
                                    )
                                    if direct_claimed is False:
                                        guard_reason = "direct-handoff result is queued for durable parent continuation"
                                    elif direct_claimed is True:
                                        return _cleanup_claimed_handoff_result(
                                            parent_terminal_id,
                                            terminal_id,
                                            verified_lifecycle,
                                            timeout,
                                        )
                                    else:
                                        # No registered direct relation: retain
                                        # legacy capture-only compatibility.
                                        if verified_lifecycle == _LIVE_TERMINAL_LIFECYCLE:
                                            try:
                                                response = requests.post(
                                                    f"{API_BASE_URL}/terminals/{terminal_id}/exit"
                                                )
                                                response.raise_for_status()
                                            except Exception as cleanup_exc:
                                                return _waiting_handoff_result(
                                                    terminal_id,
                                                    timeout,
                                                    "cleanup is retryable after legacy final capture: "
                                                    f"{cleanup_exc}",
                                                )
                                        return HandoffResult(
                                            success=True,
                                            message=f"Handoff worker {terminal_id} completed",
                                            output=output,
                                            terminal_id=terminal_id,
                                            state=HandoffState.COMPLETED,
                                        )
                                guard_reason = (
                                    verified_problem or "final output changed between captures"
                                )
                            else:
                                guard_reason = "status changed after final-output capture"
                        else:
                            guard_reason = problem

            # An exited process can still be accepted only through the stable
            # completed-output path above.  Without that evidence it has no
            # live boundary on which a later wait slice could safely recover.
            if lifecycle != _LIVE_TERMINAL_LIFECYCLE:
                cancel_child_assignments_for_terminal(terminal_id)
                return HandoffResult(
                    success=False,
                    message=(
                        "Handoff worker process already exited; persistent tmux state "
                        "is not a resumable worker"
                    ),
                    output=None,
                    terminal_id=terminal_id,
                    state=HandoffState.FAILED,
                )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return _waiting_handoff_result(terminal_id, timeout, guard_reason)
            await asyncio.sleep(min(1.0, remaining))
    except Exception as exc:
        return HandoffResult(
            success=False,
            message=f"Handoff wait failed: {str(exc)}",
            output=None,
            terminal_id=terminal_id,
            state=HandoffState.FAILED,
        )


async def _handoff_impl(
    agent_profile: str,
    message: str,
    timeout: int = 600,
    working_directory: Optional[str] = None,
    *,
    runtime_fence: bool = True,
    request_effect: Optional[Dict[str, Any]] = None,
    request_workflow_turn_id: Optional[int] = None,
) -> HandoffResult:
    """Create a child, submit one task, then wait through one resumable slice."""
    start_time = time.time()
    deadline = time.monotonic() + timeout
    terminal_id: Optional[str] = None

    try:
        # This is intentionally before terminal creation, input delivery, and
        # the workflow relation.  A stale sidecar therefore has no initial
        # handoff effect to duplicate after client reinitialization.
        if runtime_fence:
            _fence_privileged_runtime()
        terminal_id, provider = _create_terminal(agent_profile, working_directory)
        ready_timeout = min(120.0, max(0.0, deadline - time.monotonic()))
        if ready_timeout <= 0 or not wait_until_terminal_status(
            terminal_id,
            {TerminalStatus.IDLE, TerminalStatus.COMPLETED},
            timeout=ready_timeout,
        ):
            return HandoffResult(
                success=False,
                message=(
                    f"Terminal {terminal_id} did not reach ready status within "
                    f"the {timeout}-second handoff deadline"
                ),
                output=None,
                terminal_id=terminal_id,
                state=HandoffState.FAILED,
            )

        # A direct handoff is normally consumed by this live MCP call.  Store
        # the parent/child edge before input nevertheless: after a service
        # restart its valid final result can be persisted into the parent's
        # Inbox and trigger that same parent's next model turn.
        parent_terminal_id = os.environ.get("CAO_TERMINAL_ID")
        request_effect_id = request_effect.get("id") if request_effect is not None else None
        registered = False
        if parent_terminal_id:
            registered = (
                register_handoff_child(parent_terminal_id, terminal_id)
                if request_effect is None
                else register_handoff_child(
                    parent_terminal_id,
                    terminal_id,
                    workflow_turn_id=request_workflow_turn_id,
                    workflow_effect_id=request_effect.get("id"),
                    request_message=message,
                )
            )
            if not registered and (
                request_effect is not None or get_workflow_status(parent_terminal_id) != "open"
            ):
                return HandoffResult(
                    success=False,
                    message=(
                        "Could not register exact handoff authority"
                        if request_effect is not None
                        else "Parent workflow closed before handoff input could be sent"
                    ),
                    output=None,
                    terminal_id=terminal_id,
                    state=HandoffState.FAILED,
                )
        try:
            from cli_agent_orchestrator.services.operations_service import (
                workflow_execution_admission_fence,
            )

            with workflow_execution_admission_fence():
                binding = issue_workflow_input_binding(terminal_id)
            if binding is None:
                raise RuntimeError("Could not create handoff child workflow binding")
            if (
                parent_terminal_id
                and request_effect is not None
                and not bind_child_assignment_input_turn(terminal_id, binding)
            ):
                raise RuntimeError("Could not bind handoff child workflow authority")
            _send_direct_input_handoff(terminal_id, provider, message, binding)
        except Exception:
            if parent_terminal_id and registered:
                if request_effect_id is None:
                    cancel_child_assignments_for_terminal(terminal_id)
                else:
                    cancel_child_assignment_attempt(
                        parent_terminal_id, terminal_id, int(request_effect_id)
                    )
            raise
        remaining = max(0, deadline - time.monotonic())
        result = await _await_handoff_impl(terminal_id, timeout=remaining)
        if result.state == HandoffState.COMPLETED:
            result.message = (
                f"Successfully handed off to {agent_profile} ({provider}) in "
                f"{time.time() - start_time:.2f}s"
            )
        return result
    except TerminalAdmissionError as exc:
        return HandoffResult(
            success=False,
            message=str(exc),
            output=None,
            terminal_id=terminal_id,
            reason_code=exc.reason_code,
            state=HandoffState.FAILED,
        )
    except Exception as exc:
        return HandoffResult(
            success=False,
            message=f"Handoff failed: {str(exc)}",
            output=None,
            terminal_id=terminal_id,
            state=HandoffState.FAILED,
        )


# Conditional tool registration based on environment variable
if ENABLE_WORKING_DIRECTORY:

    @mcp.tool()
    async def handoff(
        logical_turn_id: int = Field(
            description="Admitted durable workflow logical-turn that owns this handoff"
        ),
        agent_profile: str = Field(
            description='The agent profile to hand off to (e.g., "developer", "analyst")'
        ),
        message: str = Field(description="The message/task to send to the target agent"),
        timeout: int = Field(
            default=600,
            description="Maximum time to wait for the agent to complete the task (in seconds)",
            ge=1,
            le=3600,
        ),
        working_directory: Optional[str] = Field(
            default=None,
            description='Optional working directory where the agent should execute (e.g., "/path/to/workspace/src/Package")',
        ),
    ) -> HandoffResult:
        """Hand off a task to another agent via CAO terminal and wait for completion.

        This tool allows handing off tasks to other agents by creating a new terminal
        in the same session. It sends the message, waits for completion, and captures the output.

        ## Usage

        Use this tool to hand off tasks to another agent and wait for the results.
        The tool will:
        1. Create a new terminal with the specified agent profile and provider
        2. Set the working directory for the terminal (defaults to supervisor's cwd)
        3. Send the message to the terminal
        4. Monitor until completion
        5. Return the agent's response
        6. Clean up the terminal with /exit

        ## Working Directory

        - By default, agents start in the supervisor's current working directory
        - You can specify a custom directory via working_directory parameter
        - Directory must exist and be accessible

        ## Requirements

        - Must be called from within a CAO terminal (CAO_TERMINAL_ID environment variable)
        - Target session must exist and be accessible
        - If working_directory is provided, it must exist and be accessible

        Args:
            agent_profile: The agent profile for the new terminal
            message: The task/message to send
            timeout: Maximum wait time in seconds
            working_directory: Optional directory path where agent should execute

        Returns:
            HandoffResult with success status, message, and agent output
        """
        effect = _claim_privileged_effect(logical_turn_id, "handoff", agent_profile, message)
        if effect is None:
            rejection = _privileged_effect_rejection(
                logical_turn_id, "handoff", agent_profile, message
            )
            return HandoffResult(
                success=False,
                message=str(rejection["error"]),
                output=None,
                terminal_id=None,
                reason_code=rejection["reason_code"],
                workflow_state=rejection["workflow_state"],
                state=HandoffState.FAILED,
            )
        execution_terminal, execution_suspended = _suspend_provider_execution(logical_turn_id)
        try:
            result = await _handoff_impl(
                agent_profile,
                message,
                timeout,
                working_directory,
                runtime_fence=False,
                request_effect=effect,
                request_workflow_turn_id=logical_turn_id,
            )
        except Exception:
            _finish_privileged_effect(effect, "indeterminate")
            raise
        finally:
            await _resume_provider_execution(
                execution_terminal, logical_turn_id, execution_suspended
            )
        _finish_privileged_effect(effect, _delegation_effect_outcome(result))
        return result

else:

    @mcp.tool()
    async def handoff(
        logical_turn_id: int = Field(
            description="Admitted durable workflow logical-turn that owns this handoff"
        ),
        agent_profile: str = Field(
            description='The agent profile to hand off to (e.g., "developer", "analyst")'
        ),
        message: str = Field(description="The message/task to send to the target agent"),
        timeout: int = Field(
            default=600,
            description="Maximum time to wait for the agent to complete the task (in seconds)",
            ge=1,
            le=3600,
        ),
    ) -> HandoffResult:
        """Hand off a task to another agent via CAO terminal and wait for completion.

        This tool allows handing off tasks to other agents by creating a new terminal
        in the same session. It sends the message, waits for completion, and captures the output.

        ## Usage

        Use this tool to hand off tasks to another agent and wait for the results.
        The tool will:
        1. Create a new terminal with the specified agent profile and provider
        2. Send the message to the terminal (starts in supervisor's current directory)
        3. Monitor until completion
        4. Return the agent's response
        5. Clean up the terminal with /exit

        ## Requirements

        - Must be called from within a CAO terminal (CAO_TERMINAL_ID environment variable)
        - Target session must exist and be accessible

        Args:
            agent_profile: The agent profile for the new terminal
            message: The task/message to send
            timeout: Maximum wait time in seconds

        Returns:
            HandoffResult with success status, message, and agent output
        """
        effect = _claim_privileged_effect(logical_turn_id, "handoff", agent_profile, message)
        if effect is None:
            rejection = _privileged_effect_rejection(
                logical_turn_id, "handoff", agent_profile, message
            )
            return HandoffResult(
                success=False,
                message=str(rejection["error"]),
                output=None,
                terminal_id=None,
                reason_code=rejection["reason_code"],
                workflow_state=rejection["workflow_state"],
                state=HandoffState.FAILED,
            )
        execution_terminal, execution_suspended = _suspend_provider_execution(logical_turn_id)
        try:
            result = await _handoff_impl(
                agent_profile,
                message,
                timeout,
                None,
                runtime_fence=False,
                request_effect=effect,
                request_workflow_turn_id=logical_turn_id,
            )
        except Exception:
            _finish_privileged_effect(effect, "indeterminate")
            raise
        finally:
            await _resume_provider_execution(
                execution_terminal, logical_turn_id, execution_suspended
            )
        _finish_privileged_effect(effect, _delegation_effect_outcome(result))
        return result


@mcp.tool()
async def await_handoff(
    logical_turn_id: int = Field(
        description="Admitted durable workflow logical-turn that owns this handoff recovery"
    ),
    terminal_id: str = Field(description="Durable terminal ID returned by a waiting handoff"),
    timeout: int = Field(
        default=600,
        description="Maximum additional wait slice in seconds",
        ge=1,
        le=3600,
    ),
) -> HandoffResult:
    """Resume waiting for an existing handoff worker without sending it another task.

    A waiting result retains the same terminal_id. This tool only observes that
    child, validates its final output, and exits it after a successful result.
    """
    try:
        effect = _claim_privileged_effect(logical_turn_id, "await_handoff", terminal_id)
    except SidecarRuntimeRecoveryRequired:
        return _runtime_reconnect_handoff_result(terminal_id)
    if effect is None:
        rejection = _privileged_effect_rejection(logical_turn_id, "await_handoff", terminal_id)
        return HandoffResult(
            success=False,
            message=str(rejection["error"]),
            output=None,
            terminal_id=terminal_id,
            reason_code=rejection["reason_code"],
            workflow_state=rejection["workflow_state"],
            state=HandoffState.FAILED,
        )
    execution_terminal, execution_suspended = _suspend_provider_execution(logical_turn_id)
    try:
        result = await _await_handoff_impl(terminal_id, timeout)
    except Exception:
        _finish_privileged_effect(effect, "indeterminate")
        raise
    finally:
        await _resume_provider_execution(execution_terminal, logical_turn_id, execution_suspended)
    _finish_privileged_effect(effect, "completed" if result.success else "indeterminate")
    return result


@mcp.tool()
async def submit_handoff_result_v1(
    logical_turn_id: int = Field(
        description="Current admitted workflow turn for this direct-handoff child submission"
    ),
    document: HandoffResultDocumentV1 = Field(
        description="Strict V1 result document; no relation or result identifiers are caller supplied"
    ),
) -> Dict[str, Any]:
    """Stage one authenticated strict V1 result for this direct-handoff child.

    This local endpoint trusts the bearer capability injected by agentctl to
    identify its managed terminal; it does not authenticate a model or accept
    caller-selected terminal/result identities. A recorded submission is not
    handoff completion. The normal stable provider observation and cleanup
    path finalizes it before any parent wake.
    """
    token = os.environ.get("CAO_TERMINAL_AUTH_TOKEN")
    if not token:
        return {"accepted": False, "error": "terminal structured-result auth is unavailable"}
    try:
        response = requests.post(
            f"{API_BASE_URL}/_internal/delegation-results/handoff-v1",
            headers={"Authorization": f"Bearer {token}"},
            json={"logical_turn_id": logical_turn_id, "document": document.model_dump(mode="json")},
            timeout=5,
        )
    except requests.RequestException as exc:
        return {"accepted": False, "error": f"structured-result submission unavailable: {exc}"}
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", "structured-result submission rejected")
        except ValueError:
            detail = "structured-result submission rejected"
        return {"accepted": False, "error": detail}
    return response.json()


# Implementation function for assign
def _assign_impl(
    agent_profile: str,
    message: str,
    working_directory: Optional[str] = None,
    *,
    reviewer_terminal_id: Optional[str] = None,
    request_effect: Optional[Dict[str, Any]] = None,
    request_workflow_turn_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Implementation of assign logic."""
    try:
        reused_reviewer = isinstance(reviewer_terminal_id, str) and bool(
            reviewer_terminal_id.strip()
        )
        review_authority: Optional[Dict[str, str]] = None
        if reused_reviewer:
            terminal_id = reviewer_terminal_id.strip()
            metadata = get_terminal_metadata(terminal_id)
            actual_profile = metadata.get("agent_profile") if metadata is not None else None
            reviewer_profile = actual_profile == "reviewer" or str(actual_profile or "").startswith(
                "reviewer_"
            )
            if (
                metadata is None
                or not reviewer_profile
                or actual_profile != agent_profile
                or metadata.get("runtime_lifecycle") != _LIVE_TERMINAL_LIFECYCLE
                or working_directory is not None
            ):
                return {
                    "success": False,
                    "terminal_id": None,
                    "reviewer_terminal_id": terminal_id,
                    "reason_code": "REVIEWER_REUSE_NOT_ELIGIBLE",
                    "message": ("Existing reviewer is not eligible for an exact bounded rereview"),
                }
        else:
            # A new assignment retains the existing terminal-admission path.
            terminal_id, _ = _create_terminal(agent_profile, working_directory)

        # Guard: wait for the terminal to be genuinely ready before sending
        # the task message. create_terminal() calls provider.initialize() which
        # already waits 30 s for IDLE, but that check can return a false-positive
        # on the pre-existing shell ❯ prompt (zsh/bash) before claude starts.
        # A secondary API-level wait (same as handoff uses) catches that race.
        if not wait_until_terminal_status(
            terminal_id,
            {TerminalStatus.IDLE, TerminalStatus.COMPLETED},
            timeout=60.0,
        ):
            return {
                "success": False,
                "terminal_id": None if reused_reviewer else terminal_id,
                "reviewer_terminal_id": terminal_id if reused_reviewer else None,
                "reason_code": ("REVIEWER_REUSE_NOT_ELIGIBLE" if reused_reviewer else None),
                "message": f"Terminal {terminal_id} did not reach ready status within 60 seconds — agent may not have started",
            }

        # Record the callback expectation before input reaches the worker: a
        # very fast child must never be able to reply before its parent has a
        # durable completion barrier. Top-level assigns have no parent ID and
        # retain their existing fire-and-forget behavior.
        parent_terminal_id = os.environ.get("CAO_TERMINAL_ID")
        request_effect_id = request_effect.get("id") if request_effect is not None else None
        registered = False
        if parent_terminal_id:
            registered = (
                register_child_assignment(parent_terminal_id, terminal_id)
                if request_effect is None
                else register_child_assignment(
                    parent_terminal_id,
                    terminal_id,
                    workflow_turn_id=request_workflow_turn_id,
                    workflow_effect_id=request_effect.get("id"),
                    request_message=message,
                )
            )
            if not registered and (
                request_effect is not None or get_workflow_status(parent_terminal_id) != "open"
            ):
                return {
                    "success": False,
                    "terminal_id": None if reused_reviewer else terminal_id,
                    "reviewer_terminal_id": terminal_id if reused_reviewer else None,
                    "reason_code": ("REVIEWER_REUSE_NOT_ELIGIBLE" if reused_reviewer else None),
                    "message": (
                        "Could not register exact assignment authority"
                        if request_effect is not None
                        else "Parent workflow closed before assignment input could be sent"
                    ),
                }
        try:
            from cli_agent_orchestrator.services.operations_service import (
                workflow_execution_admission_fence,
            )

            if parent_terminal_id and request_effect_id is not None:
                review_authority = get_child_assignment_request_authority(
                    parent_terminal_id, terminal_id, int(request_effect_id)
                )
                if reused_reviewer and review_authority is None:
                    cancel_child_assignment_attempt(
                        parent_terminal_id,
                        terminal_id,
                        int(request_effect_id),
                        reason_code="review_authority_unbound",
                    )
                    return {
                        "success": False,
                        "terminal_id": terminal_id,
                        "reviewer_terminal_id": terminal_id,
                        "reason_code": "REVIEWER_REVIEW_AUTHORITY_UNBOUND",
                        "message": (
                            "Existing reviewer could not be bound to an exact immutable revision"
                        ),
                    }
                if review_authority is not None:
                    message = (
                        f"{message.rstrip()}\n\n"
                        "[CAO immutable review authority: "
                        f"attempt_id={review_authority['attempt_id']} "
                        f"subject_id={review_authority['subject_id']} "
                        f"exact_revision={review_authority['revision']}]"
                    )
            with workflow_execution_admission_fence():
                binding = issue_workflow_input_binding(terminal_id)
            if binding is None:
                raise RuntimeError("Could not create assigned child workflow binding")
            if (
                parent_terminal_id
                and request_effect is not None
                and not bind_child_assignment_input_turn(terminal_id, binding)
            ):
                raise RuntimeError("Could not bind assigned child workflow authority")
            _send_direct_input_assign(terminal_id, message, binding)
        except Exception:
            if parent_terminal_id and registered:
                if request_effect_id is None:
                    cancel_child_assignments_for_terminal(terminal_id)
                else:
                    cancel_child_assignment_attempt(
                        parent_terminal_id, terminal_id, int(request_effect_id)
                    )
            raise

        return {
            "success": True,
            "terminal_id": terminal_id,
            "reviewer_reused": reused_reviewer,
            **({"review_attempt": review_authority} if review_authority is not None else {}),
            "message": f"Task assigned to {agent_profile} (terminal: {terminal_id})",
        }

    except TerminalAdmissionError as e:
        return {
            "success": False,
            "terminal_id": None,
            "message": str(e),
            "reason_code": e.reason_code,
        }
    except Exception as e:
        return {"success": False, "terminal_id": None, "message": f"Assignment failed: {str(e)}"}


def _build_assign_description(enable_sender_id: bool, enable_workdir: bool) -> str:
    """Build the assign tool description based on feature flags."""
    # Build tool description overview.
    if enable_sender_id:
        desc = """\
Assigns a task to another agent without blocking.

The sender's terminal ID and callback instructions will automatically be appended to the message."""
    else:
        desc = """\
Assigns a task to another agent without blocking.

In the message to the worker agent include instruction to send results back via send_message tool.
**IMPORTANT**: The terminal id of each agent is available in environment variable CAO_TERMINAL_ID.
When assigning, first find out your own CAO_TERMINAL_ID value, then include the terminal_id value in the message to the worker agent to allow callback.
Example message: "Analyze the logs. When done, send results back to terminal ee3f93b3 using send_message tool.\""""

    if enable_workdir:
        desc += """

## Working Directory

- By default, agents start in the supervisor's current working directory
- You can specify a custom directory via working_directory parameter
- Directory must exist and be accessible"""

    desc += """

Args:
    agent_profile: Agent profile for the worker terminal
    message: Task message (include callback instructions)"""

    if enable_workdir:
        desc += """
    working_directory: Optional working directory where the agent should execute"""

    desc += """
    reviewer_terminal_id: Optional existing assigned reviewer terminal for one bounded rereview.
        The reviewer profile and parent relation must match, its prior result must be final,
        and its prior workflow must be terminal. Omit this to create a new child."""

    desc += """

Returns:
    Dict with success status, worker terminal_id, and message"""

    return desc


_assign_description = _build_assign_description(
    ENABLE_SENDER_ID_INJECTION, ENABLE_WORKING_DIRECTORY
)
_assign_message_field_desc = (
    "The task message to send to the worker agent."
    if ENABLE_SENDER_ID_INJECTION
    else "The task message to send. Include callback instructions for the worker to send results back."
)

if ENABLE_WORKING_DIRECTORY:

    @mcp.tool(description=_assign_description)
    async def assign(
        logical_turn_id: int = Field(
            description="Admitted durable workflow logical-turn that owns this assignment"
        ),
        agent_profile: str = Field(
            description='The agent profile for the worker agent (e.g., "developer", "analyst")'
        ),
        message: str = Field(description=_assign_message_field_desc),
        working_directory: Optional[str] = Field(
            default=None, description="Optional working directory where the agent should execute"
        ),
        reviewer_terminal_id: Optional[str] = Field(
            default=None,
            description="Existing assigned reviewer terminal to reuse for a bounded rereview",
        ),
    ) -> Dict[str, Any]:
        reviewer_terminal_id = (
            reviewer_terminal_id if isinstance(reviewer_terminal_id, str) else None
        )
        effect_identity = (
            (agent_profile, message)
            if reviewer_terminal_id is None
            else (agent_profile, reviewer_terminal_id, message)
        )
        effect = _claim_privileged_effect(logical_turn_id, "assign", *effect_identity)
        if effect is None:
            return _privileged_effect_rejection(logical_turn_id, "assign", *effect_identity)
        try:
            result = _assign_impl(
                agent_profile,
                message,
                working_directory,
                reviewer_terminal_id=reviewer_terminal_id,
                request_effect=effect,
                request_workflow_turn_id=logical_turn_id,
            )
        except Exception:
            _finish_privileged_effect(effect, "indeterminate")
            raise
        _finish_privileged_effect(effect, _delegation_effect_outcome(result))
        return result

else:

    @mcp.tool(description=_assign_description)
    async def assign(
        logical_turn_id: int = Field(
            description="Admitted durable workflow logical-turn that owns this assignment"
        ),
        agent_profile: str = Field(
            description='The agent profile for the worker agent (e.g., "developer", "analyst")'
        ),
        message: str = Field(description=_assign_message_field_desc),
        reviewer_terminal_id: Optional[str] = Field(
            default=None,
            description="Existing assigned reviewer terminal to reuse for a bounded rereview",
        ),
    ) -> Dict[str, Any]:
        reviewer_terminal_id = (
            reviewer_terminal_id if isinstance(reviewer_terminal_id, str) else None
        )
        effect_identity = (
            (agent_profile, message)
            if reviewer_terminal_id is None
            else (agent_profile, reviewer_terminal_id, message)
        )
        effect = _claim_privileged_effect(logical_turn_id, "assign", *effect_identity)
        if effect is None:
            return _privileged_effect_rejection(logical_turn_id, "assign", *effect_identity)
        try:
            result = _assign_impl(
                agent_profile,
                message,
                None,
                reviewer_terminal_id=reviewer_terminal_id,
                request_effect=effect,
                request_workflow_turn_id=logical_turn_id,
            )
        except Exception:
            _finish_privileged_effect(effect, "indeterminate")
            raise
        _finish_privileged_effect(effect, _delegation_effect_outcome(result))
        return result


# Implementation function for send_message
def _send_message_impl(
    receiver_id: str,
    message: str,
    effect: Optional[Dict[str, Any]] = None,
    logical_turn_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Implementation of send_message logic."""
    from cli_agent_orchestrator.services.operations_service import (
        workflow_execution_admission_fence,
    )

    try:
        # Auto-inject sender terminal ID suffix when enabled
        if ENABLE_SENDER_ID_INJECTION:
            sender_id = os.environ.get("CAO_TERMINAL_ID", "unknown")
            message += (
                f"\n\n[Message from terminal {sender_id}. "
                "Use send_message MCP tool for any follow-up work.]"
            )

        sender_id = os.environ.get("CAO_TERMINAL_ID")
        if sender_id and effect is not None and logical_turn_id is not None:
            # Managed/assigned callbacks persist their Inbox turn locally.
            # Generic messages use the HTTP API below, whose persistence
            # boundary owns this same cross-process fence.
            with workflow_execution_admission_fence():
                continuation = schedule_managed_handoff_continuation(
                    sender_id, receiver_id, message
                )
                if continuation.get("managed"):
                    if not continuation.get("accepted"):
                        return {
                            "success": False,
                            "reason_code": continuation["reason_code"],
                            "error": "managed handoff continuation was not admitted",
                        }
                    scheduled_message = continuation.get("message")
                    if scheduled_message is not None:
                        try:
                            inbox_service.check_and_send_pending_messages(receiver_id)
                        except Exception as exc:
                            # The persisted Inbox/turn pair is authoritative.  A
                            # later watchdog tick retries the same child turn.
                            logger.warning(
                                "Immediate managed continuation delivery failed: %s", exc
                            )
                    return {
                        "success": True,
                        "duplicate": bool(continuation.get("duplicate")),
                        "message_id": (
                            scheduled_message.id if scheduled_message is not None else None
                        ),
                        "sender_id": sender_id,
                        "receiver_id": receiver_id,
                        "logical_turn_id": continuation["turn_id"],
                        "managed_handoff_continuation": True,
                    }
                assigned_result, duplicate = create_child_assignment_result_message(
                    sender_id,
                    receiver_id,
                    message,
                    workflow_effect_id=effect["id"],
                    workflow_turn_id=logical_turn_id,
                )
                if assigned_result is not None:
                    try:
                        inbox_service.check_and_send_pending_messages(receiver_id)
                    except Exception as exc:
                        # Persistence is authoritative; retry delivery through the
                        # normal watchdog/restart path rather than reopening the
                        # child submission effect.
                        logger.warning("Immediate assigned-result delivery failed: %s", exc)
                    return {
                        "success": True,
                        "duplicate": duplicate,
                        "message_id": assigned_result.id,
                        "sender_id": assigned_result.sender_id,
                        "receiver_id": assigned_result.receiver_id,
                        "result_id": assigned_result.result_id,
                    }
                if duplicate:
                    return {
                        "success": True,
                        "ignored": True,
                        "reason": "assigned callback was already closed or cancelled",
                    }
        return _send_to_inbox(receiver_id, message)
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool()
async def send_message(
    logical_turn_id: int = Field(
        description="Admitted durable workflow logical-turn that owns this inbox write"
    ),
    receiver_id: str = Field(description="Target terminal ID to send message to"),
    message: str = Field(description="Message content to send"),
) -> Dict[str, Any]:
    """Send a message to another terminal's inbox.

    The message will be delivered when the destination terminal is IDLE.
    Messages are delivered in order (oldest first).

    Args:
        receiver_id: Terminal ID of the receiver
        message: Message content to send

    Returns:
        Dict with success status and message details
    """
    effect = _claim_privileged_effect(logical_turn_id, "send_message", receiver_id, message)
    if effect is None:
        return _privileged_effect_rejection(logical_turn_id, "send_message", receiver_id, message)
    try:
        result = _send_message_impl(receiver_id, message, effect, logical_turn_id)
    except Exception:
        _finish_privileged_effect(effect, "indeterminate")
        raise
    _finish_privileged_effect(effect, "completed" if result.get("success") else "indeterminate")
    return result


@mcp.tool()
async def acknowledge_assigned_result(
    logical_turn_id: int = Field(
        description="Admitted durable workflow logical-turn that owns this acknowledgement"
    ),
    child_terminal_id: Optional[str] = Field(
        default=None, description="Assigned child terminal whose delivered result is consumed"
    ),
    result_id: Optional[str] = Field(
        default=None, description="Preferred immutable durable result ID"
    ),
) -> Dict[str, Any]:
    """Acknowledge that this parent has incorporated an assigned child's result.

    Call this only after processing the child's Inbox callback.  The durable
    acknowledgement releases the parent's handoff completion barrier; until
    then a delivered callback is retained for at-least-once restart replay.
    """
    parent_terminal_id = os.environ.get("CAO_TERMINAL_ID")
    if not parent_terminal_id:
        return {"success": False, "error": "CAO_TERMINAL_ID is required to acknowledge a child"}
    # FastMCP resolves Field defaults at the transport boundary.  Direct
    # in-process callers used by the compatibility API see FieldInfo objects
    # for omitted arguments, so normalize them before identity comparison.
    child_terminal_id = child_terminal_id if isinstance(child_terminal_id, str) else None
    result_id = result_id if isinstance(result_id, str) else None
    identity = result_id or child_terminal_id
    if not identity:
        return {"success": False, "error": "child_terminal_id or result_id is required"}
    replay = describe_child_assignment_acknowledgement(
        parent_terminal_id, child_terminal_id, result_id
    )
    # A caller that supplies both immutable identities must prove they select
    # one assignment before it can claim an acknowledgement effect.  This
    # keeps a cross-child mismatch entirely inert and prevents the response
    # from echoing an identity that was not mutated.
    if replay.get("reason_code") == "RESULT_IDENTITY_MISMATCH":
        return {
            "success": False,
            "accepted": False,
            **replay,
            "error": "result_id and child_terminal_id do not identify the same assignment.",
        }
    # A same-turn acknowledgement replay has no new privileged side effect;
    # return its durable lifecycle reason before the generic effect dedupe.
    if replay.get("reason_code") == "RESULT_ALREADY_ACKNOWLEDGED" and has_admitted_workflow_turn(
        parent_terminal_id, logical_turn_id
    ):
        return {
            "success": False,
            "accepted": False,
            **replay,
            "error": "The result was already acknowledged; no lifecycle mutation was repeated.",
        }
    canonical_identity = replay.get("result_id") or replay.get("child_terminal_id") or identity
    effect = _claim_privileged_effect(
        logical_turn_id,
        "acknowledge_assignment",
        str(canonical_identity),
    )
    if effect is None:
        return _privileged_effect_rejection(
            logical_turn_id,
            "acknowledge_assignment",
            str(canonical_identity),
        )
    outcome = acknowledge_child_assignment_result_outcome(
        parent_terminal_id, child_terminal_id, result_id
    )
    _finish_privileged_effect(effect, "completed" if outcome["accepted"] else "rejected")
    if not outcome["accepted"]:
        return {
            "success": False,
            "accepted": False,
            **outcome,
            "error": "The result acknowledgement is not eligible in its current durable state.",
        }
    return {
        "success": True,
        "parent_terminal_id": parent_terminal_id,
        "child_terminal_id": outcome.get("child_terminal_id"),
        "result_id": outcome.get("result_id"),
    }


@mcp.tool()
async def retire_completed_child(
    logical_turn_id: int = Field(
        description="Admitted durable workflow turn that owns this assigned-child retirement"
    ),
    child_terminal_id: str = Field(
        description="Acknowledged ordinary assigned child terminal to retire"
    ),
) -> Dict[str, Any]:
    """Retire one fully incorporated ordinary assigned child and its managed worktree.

    This narrow resource-hygiene operation excludes handoffs and verifies the
    immutable result, child workflow, descendant barrier, and metadata before
    it crosses the canonical provider-exit boundary.
    """
    parent_terminal_id = os.environ.get("CAO_TERMINAL_ID")
    if not parent_terminal_id:
        return {"success": False, "error": "CAO_TERMINAL_ID is required"}
    if child_terminal_id == parent_terminal_id:
        return {"success": False, "error": "child_terminal_id must not identify the caller"}

    try:
        effect = _claim_privileged_effect(
            logical_turn_id, "retire_completed_child", child_terminal_id
        )
    except SidecarRuntimeRecoveryRequired:
        return _runtime_reconnect_response(child_terminal_id)
    except SidecarRuntimeIdentityUnavailable:
        return _runtime_identity_unavailable_response(child_terminal_id)
    if effect is None:
        return _privileged_effect_rejection(
            logical_turn_id, "retire_completed_child", child_terminal_id
        )
    runtime_fence = _retirement_runtime_fence(child_terminal_id)
    if runtime_fence is not None:
        return _finish_stale_retirement_boundary(effect, child_terminal_id, runtime_fence)
    fence = claim_completed_assigned_child_retirement(parent_terminal_id, child_terminal_id)
    if not fence["eligible"]:
        _finish_privileged_effect(effect, "rejected")
        return {"success": False, **fence}
    if fence.get("already_retired"):
        return _already_retired_response(effect, child_terminal_id)

    claim_token = fence["claim_token"]
    retiring_supervisor_terminal_id = (
        parent_terminal_id if fence.get("historical_recovery") else None
    )
    if fence.get("exit_dispatch_reserved"):
        # A prior process crossed the durable external-effect boundary.  Its
        # response may have been lost, so this is reconciliation only: never
        # issue a second automatic /exit while the provider is still running.
        try:
            terminal_status, lifecycle = _read_terminal_state(child_terminal_id)
        except Exception as exc:
            _finish_privileged_effect(effect, "indeterminate")
            return {
                "success": False,
                "child_terminal_id": child_terminal_id,
                "status": "exit_dispatch_indeterminate",
                "recoverable": True,
                "error": "child_terminal_unavailable",
                "detail": str(exc),
            }
        if lifecycle == "exited":
            return _finish_retired_child(
                effect,
                child_terminal_id,
                claim_token,
                already_retired=True,
                retiring_supervisor_terminal_id=retiring_supervisor_terminal_id,
            )
        _finish_privileged_effect(effect, "indeterminate")
        return {
            "success": False,
            "child_terminal_id": child_terminal_id,
            "status": "exit_dispatch_indeterminate",
            "recoverable": True,
            "error": "child_retirement_exit_indeterminate",
            "lifecycle": lifecycle,
        }

    try:
        terminal_status, lifecycle = _read_terminal_state(child_terminal_id)
    except Exception as exc:
        release_completed_assigned_child_retirement(child_terminal_id, claim_token)
        _finish_privileged_effect(effect, "indeterminate")
        return {"success": False, "error": "child_terminal_unavailable", "detail": str(exc)}
    if lifecycle == "exited":
        return _finish_retired_child(
            effect,
            child_terminal_id,
            claim_token,
            already_retired=True,
            retiring_supervisor_terminal_id=retiring_supervisor_terminal_id,
        )
    if not terminal_service.is_terminal_quiescent(terminal_status):
        release_completed_assigned_child_retirement(child_terminal_id, claim_token)
        _finish_privileged_effect(effect, "rejected")
        return {"success": False, "error": "child_terminal_not_completed"}
    if lifecycle != "running":
        release_completed_assigned_child_retirement(child_terminal_id, claim_token)
        _finish_privileged_effect(effect, "rejected")
        return {"success": False, "error": "child_terminal_lifecycle_unknown"}
    if not revalidate_completed_assigned_child_retirement(
        parent_terminal_id, child_terminal_id, claim_token
    ):
        release_completed_assigned_child_retirement(child_terminal_id, claim_token)
        _finish_privileged_effect(effect, "rejected")
        return {"success": False, "error": "child_retirement_quiescence_lost"}

    # Provider state is non-transactional, so reread it immediately before
    # crossing /exit.  The durable claim has meanwhile fenced new workflow
    # input and descendants, while this makes a resumed PROCESSING child fail
    # safely instead of being retired on an old lifecycle observation.
    try:
        terminal_status, lifecycle = _read_terminal_state(child_terminal_id)
    except Exception as exc:
        release_completed_assigned_child_retirement(child_terminal_id, claim_token)
        _finish_privileged_effect(effect, "indeterminate")
        return {"success": False, "error": "child_terminal_unavailable", "detail": str(exc)}
    if lifecycle == "exited":
        return _finish_retired_child(
            effect,
            child_terminal_id,
            claim_token,
            already_retired=True,
            retiring_supervisor_terminal_id=retiring_supervisor_terminal_id,
        )
    if not terminal_service.is_terminal_quiescent(terminal_status):
        release_completed_assigned_child_retirement(child_terminal_id, claim_token)
        _finish_privileged_effect(effect, "rejected")
        return {"success": False, "error": "child_terminal_not_completed"}
    if lifecycle != "running":
        release_completed_assigned_child_retirement(child_terminal_id, claim_token)
        _finish_privileged_effect(effect, "rejected")
        return {"success": False, "error": "child_terminal_lifecycle_unknown"}
    runtime_fence = _retirement_runtime_fence(child_terminal_id)
    if runtime_fence is not None:
        return _finish_stale_retirement_boundary(
            effect,
            child_terminal_id,
            runtime_fence,
            claim_token=claim_token,
        )
    if not reserve_completed_assigned_child_retirement_exit(child_terminal_id, claim_token):
        # Do not release this claim: the failed compare-and-mutate may mean a
        # prior process already reserved /exit.  Holding the fence is safer
        # than admitting a new input across an unknown provider outcome.
        _finish_privileged_effect(effect, "indeterminate")
        return {
            "success": False,
            "child_terminal_id": child_terminal_id,
            "status": "exit_dispatch_indeterminate",
            "recoverable": True,
            "error": "child_retirement_exit_reservation_not_confirmed",
        }
    runtime_fence = _retirement_runtime_fence(child_terminal_id)
    if runtime_fence is not None:
        return _finish_stale_retirement_boundary(
            effect,
            child_terminal_id,
            runtime_fence,
            claim_token=claim_token,
            exit_reserved_but_undispatched=True,
        )
    try:
        retired = terminal_service.exit_terminal(child_terminal_id)
    except Exception:
        # A provider exit can raise after an unknown remote outcome.  The
        # durable reservation and quiescence claim remain so a fresh turn can
        # reconcile an exited child but can never issue another automatic exit.
        _finish_privileged_effect(effect, "indeterminate")
        raise
    if not retired:
        # A false provider response is likewise an unknown remote outcome.
        # Keep the reservation and report a recoverable reconciliation state.
        _finish_privileged_effect(effect, "indeterminate")
        return {
            "success": False,
            "child_terminal_id": child_terminal_id,
            "status": "exit_dispatch_indeterminate",
            "recoverable": True,
            "error": "child_retirement_exit_indeterminate",
        }
    return _finish_retired_child(
        effect,
        child_terminal_id,
        claim_token,
        retiring_supervisor_terminal_id=retiring_supervisor_terminal_id,
    )


@mcp.tool()
async def read_delegation_result(
    logical_turn_id: int = Field(description="Current admitted workflow turn for this read"),
    result_id: str = Field(description="Immutable delegation result ID"),
) -> Dict[str, Any]:
    """Read a durable result owned by this parent or produced by this child.

    This is deliberately read-only: it validates current admission but does
    not consume a workflow effect, so replayed reads remain repeatable.
    """
    terminal_id = os.environ.get("CAO_TERMINAL_ID")
    if not terminal_id or not has_admitted_workflow_turn(terminal_id, logical_turn_id):
        return {"success": False, "error": "workflow turn is not admitted"}
    result = get_delegation_result(result_id)
    if result is None:
        return {"success": False, "error": "delegation result not found"}
    if terminal_id not in (result["parent_terminal_id"], result["child_terminal_id"]):
        return {"success": False, "error": "delegation result is not owned by this terminal"}
    return {"success": True, "result": result}


@mcp.tool()
async def complete_workflow(
    logical_turn_id: int = Field(
        description="Admitted durable workflow logical-turn that owns this terminal transition"
    ),
    reason: str = Field(default="", description="Concise terminal completion reason"),
) -> Dict[str, Any]:
    """Explicitly mark this top-level workflow terminal after all approved work."""
    terminal_id = os.environ.get("CAO_TERMINAL_ID")
    if not terminal_id:
        return {"success": False, "error": "CAO_TERMINAL_ID is required"}
    active_children, failed_children = get_parent_completion_barrier(terminal_id)
    if active_children:
        return {
            "success": False,
            "terminal_id": terminal_id,
            "status": "open",
            "retryable": True,
            "error": "active child completion barrier",
            "active_children": active_children,
            "failed_children": failed_children,
        }
    if is_delegated_child_terminal(terminal_id):
        problem = managed_final_problem(reason)
        if problem is not None:
            return {
                "success": False,
                "accepted": False,
                "reason_code": problem,
                "workflow_state": get_workflow_status(terminal_id),
                "error": "Delegated completion requires a substantive final result.",
            }
    effect = _claim_privileged_effect(logical_turn_id, "complete_workflow", reason)
    if effect is None:
        return _privileged_effect_rejection(logical_turn_id, "complete_workflow", reason)
    try:
        completion_result, _duplicate = create_assigned_child_completion_result_message(
            terminal_id,
            reason or "Assigned child completed without a separate result message.",
            effect["id"],
            logical_turn_id,
        )
        if completion_result is not None:
            try:
                inbox_service.check_and_send_pending_messages(completion_result.receiver_id)
            except Exception as exc:
                # The finalization is durable; startup/watchdog reconciliation
                # reuses this one Inbox row if the immediate parent wake loses
                # the provider-idle boundary.
                logger.warning("Immediate assigned-completion delivery failed: %s", exc)
    except Exception:
        _finish_privileged_effect(effect, "indeterminate")
        raise
    success = set_workflow_terminal_state(
        terminal_id, "terminal", reason or None, require_no_active_children=True
    )
    if success:
        inbox_service.wake_provider_execution_queue()
    _finish_privileged_effect(effect, "completed" if success else "indeterminate")
    return {
        "success": success,
        "terminal_id": terminal_id,
        "status": "terminal" if success else "open",
        **({"retryable": True, "error": "active child completion barrier"} if not success else {}),
    }


@mcp.tool()
async def owner_gate_workflow(
    logical_turn_id: int = Field(
        description="Admitted durable workflow logical-turn that owns this owner gate"
    ),
    reason: str = Field(description="Why owner authority is required"),
) -> Dict[str, Any]:
    """Explicitly stop autonomous wakes because this workflow needs its owner."""
    terminal_id = os.environ.get("CAO_TERMINAL_ID")
    if not terminal_id:
        return {"success": False, "error": "CAO_TERMINAL_ID is required"}
    if is_delegated_child_terminal(terminal_id):
        return {
            "success": False,
            "accepted": False,
            "reason_code": "CHILD_NOT_AUTHORIZED",
            "workflow_state": get_workflow_status(terminal_id),
            "error": "Delegated children must return structured blockers to their parent.",
        }
    effect = _claim_privileged_effect(logical_turn_id, "owner_gate_workflow", reason)
    if effect is None:
        return _privileged_effect_rejection(logical_turn_id, "owner_gate_workflow", reason)
    success = set_workflow_terminal_state(terminal_id, "owner_gate", reason)
    if success:
        inbox_service.wake_provider_execution_queue()
    _finish_privileged_effect(effect, "completed" if success else "indeterminate")
    return {
        "success": success,
        "terminal_id": terminal_id,
        "status": "owner_gate",
    }


@mcp.tool()
async def claim_workflow_turn_receipt(
    logical_turn_id: int = Field(
        description="Current logical-turn ID from a CAO workflow input envelope"
    ),
    resume_token: Optional[str] = Field(
        default=None,
        description=(
            "Opaque token returned by the prior successful admission; provide it only when "
            "that admitted model execution was interrupted before its work completed"
        ),
    ),
) -> Dict[str, Any]:
    """Admit the current workflow input before model-dependent work.

    The runtime accepts the ID only when it matches the turn that CAO bound
    immediately before this input was sent. Call this first; only
    ``accepted=true`` owns the supervisor's one logical effect. A false result
    means the delivery was duplicated, stale, or the workflow was closed.
    """
    receiver_terminal_id = os.environ.get("CAO_TERMINAL_ID")
    if not receiver_terminal_id:
        return {"accepted": False, "error": "CAO_TERMINAL_ID is required"}
    effective_resume_token = resume_token if isinstance(resume_token, str) else None
    admission = claim_or_resume_workflow_turn_receipt(
        receiver_terminal_id, logical_turn_id, resume_token=effective_resume_token
    )
    return {**admission, "receiver_terminal_id": receiver_terminal_id}


@mcp.tool(description=LOAD_SKILL_TOOL_DESCRIPTION)
async def load_skill(
    name: str = Field(description="Name of the skill to retrieve"),
) -> Any:
    """Retrieve skill content from cao-server."""
    return _load_skill_impl(name)


def main():
    """Main entry point for the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
