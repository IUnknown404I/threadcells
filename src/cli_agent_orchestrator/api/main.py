"""Single FastAPI entry point for all HTTP routes."""

import asyncio
import fcntl
import hmac
import json
import logging
import os
import pty
import re
import signal
import struct
import subprocess
import termios
from contextlib import asynccontextmanager
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Annotated, Any, Dict, List, Literal, Optional, cast
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

from anyio import CapacityLimiter, to_thread
from fastapi import (
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool
from watchdog.observers.polling import PollingObserver

from cli_agent_orchestrator.clients.database import (
    HandoffResultSubmissionError,
    acquire_terminal_runtime_transport,
    cancel_child_assignments_for_terminal,
    create_inbox_message,
    get_inbox_messages,
    get_terminal_metadata,
    get_writable_work_context_by_session,
    init_db,
    queue_workflow_input_for_provider,
    release_terminal_runtime_operation,
    resolve_workflow_input_binding,
    submit_handoff_result_v1,
    terminal_auth_token_matches,
)
from cli_agent_orchestrator.constants import (
    ALLOWED_HOSTS,
    CAO_HOME_DIR,
    CORS_ORIGINS,
    INBOX_POLLING_INTERVAL,
    PROVIDERS,
    SERVER_HOST,
    SERVER_PORT,
    SERVER_VERSION,
    TERMINAL_LOG_DIR,
)
from cli_agent_orchestrator.models.flow import Flow
from cli_agent_orchestrator.models.inbox import MessageStatus, OrchestrationType
from cli_agent_orchestrator.models.project import Project, UpdateProject
from cli_agent_orchestrator.models.result import HandoffResultDocumentV1
from cli_agent_orchestrator.models.terminal import Terminal, TerminalId
from cli_agent_orchestrator.plugins import PluginRegistry
from cli_agent_orchestrator.providers.contracts import (
    classify_provider_preflight,
    provider_preflight_is_launchable,
)
from cli_agent_orchestrator.providers.manager import provider_manager
from cli_agent_orchestrator.runtime_generation import (
    ACTIVE_RUNTIME_GENERATION,
    RUNTIME_GENERATION_HEADER,
)
from cli_agent_orchestrator.services import (
    branding_service,
    flow_service,
    inbox_service,
    managed_worktree_service,
    project_service,
    recovery_takeover_service,
    result_service,
    session_service,
    telegram_notification_service,
    terminal_attachments,
    terminal_service,
    ui_read_model_service,
    usage_service,
    workflow_service,
)
from cli_agent_orchestrator.services.inbox_service import LogFileHandler
from cli_agent_orchestrator.services.operations_service import (
    AdmissionDenied,
    get_resource_status,
    load_operations_config,
    set_capacity_settings,
)
from cli_agent_orchestrator.services.project_service import ProjectResolutionError
from cli_agent_orchestrator.services.session_service import (
    SessionLifecycleError,
    SessionNotFoundError,
)
from cli_agent_orchestrator.services.terminal_service import (
    ExitAuthorityError,
    OutputMode,
    TerminalDeletionError,
    TerminalOutputCursorError,
    TerminalOutputUnavailable,
)
from cli_agent_orchestrator.utils.agent_profiles import resolve_provider
from cli_agent_orchestrator.utils.logging import setup_logging
from cli_agent_orchestrator.utils.skills import (
    SkillNameError,
    load_skill_content,
    validate_skill_name,
)
from cli_agent_orchestrator.utils.terminal import generate_session_name

logger = logging.getLogger(__name__)

# Home and Agents use only durable read models, while legacy operational routes
# may still invoke tmux, provider status parsing, /proc inventory, or log I/O.
# Keep both classes off the event loop and independently bounded so a burst of
# historical/compatibility requests cannot consume AnyIO's entire process-wide
# worker pool. Four concurrent reads cover the normal UI maximum without making
# server resource use proportional to historical terminal count.
UI_READ_MAX_CONCURRENCY = 4
OPERATIONAL_IO_MAX_CONCURRENCY = 4
WORKFLOW_IO_MAX_CONCURRENCY = 1
MAX_TERMINAL_WS_INPUT_BYTES = 1024 * 1024
_ui_read_limiter = CapacityLimiter(UI_READ_MAX_CONCURRENCY)
_operational_io_limiter = CapacityLimiter(OPERATIONAL_IO_MAX_CONCURRENCY)
_workflow_io_limiter = CapacityLimiter(WORKFLOW_IO_MAX_CONCURRENCY)
_blocking_runner_tasks: set[asyncio.Task[Any]] = set()


def _finish_blocking_runner(task: asyncio.Task[Any]) -> None:
    """Retain and observe detached runners until their worker really exits."""
    _blocking_runner_tasks.discard(task)
    if not task.cancelled():
        task.exception()


async def _run_bounded_blocking(function, *args, limiter: CapacityLimiter, **kwargs):
    # A raw asyncio Task.cancel() can cancel an AnyIO host task even when its
    # synchronous worker cannot be stopped. If run_sync() were awaited here
    # directly, the limiter token could be released while that worker remained
    # alive, allowing disconnect storms to grow beyond the advertised lane
    # capacity. The nested runner owns the token and is shielded from caller
    # cancellation; a cancelled request stops waiting, while the completion-
    # owned runner remains retained until the worker exits and releases it.
    runner = asyncio.create_task(
        to_thread.run_sync(
            partial(function, *args, **kwargs),
            abandon_on_cancel=False,
            limiter=limiter,
        )
    )
    _blocking_runner_tasks.add(runner)
    runner.add_done_callback(_finish_blocking_runner)
    return await asyncio.shield(runner)


async def _run_ui_read(function, *args, **kwargs):
    return await _run_bounded_blocking(function, *args, limiter=_ui_read_limiter, **kwargs)


async def _run_operational_io(function, *args, **kwargs):
    return await _run_bounded_blocking(function, *args, limiter=_operational_io_limiter, **kwargs)


async def _run_workflow_io(function, *args, **kwargs):
    """Reserve one bounded worker for durable continuation reconciliation."""
    return await _run_bounded_blocking(function, *args, limiter=_workflow_io_limiter, **kwargs)


class WorkflowInputRequest(BaseModel):
    """One user-authored Workflow Composer submission."""

    model_config = ConfigDict(extra="forbid")

    message: str
    request_id: UUID = Field(default_factory=uuid4)


class InboxMessageRequest(BaseModel):
    """Transport-only Inbox payload; message text must not enter request URLs."""

    sender_id: str
    message: str


class CapacitySettingsUpdate(BaseModel):
    """Exact, strict replacement for the four canonical capacity limits."""

    model_config = ConfigDict(extra="forbid", strict=True)

    max_resident_supervisors: int = Field(ge=2, le=50)
    max_provider_executions: int = Field(ge=1, le=50)
    max_work_contexts: int = Field(ge=1, le=50)
    max_heavy_execution_slots: int = Field(ge=1, le=50)


class OperatorLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    secret: str = Field(min_length=5, max_length=4096)


class XHighGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    agent_profile: str
    provider: str
    working_directory: Optional[str] = None
    requested_session_name: Optional[str] = None
    project_id: Optional[str] = None
    launch_mode: Literal["new_session", "existing_session", "recovery_takeover"] = "new_session"
    target_terminal_id: Optional[str] = None
    expected_authority_generation: Optional[str] = None
    expected_runtime_generation: Optional[str] = None
    confirmation: Optional[str] = None
    confirmed: bool = False


class RecoveryTakeoverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID = Field(default_factory=uuid4)
    expected_authority_generation: str = Field(pattern=r"^[0-9a-f]{32}$")
    expected_runtime_generation: str = Field(
        pattern=r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$"
    )
    agent_profile: str
    provider: str
    owner_grant_launch_id: str


class RecoveryTakeoverCapabilitiesRequest(BaseModel):
    """Bounded, non-secret recovery eligibility projection for UI actions."""

    model_config = ConfigDict(extra="forbid", strict=True)

    terminal_ids: List[TerminalId] = Field(min_length=1, max_length=100)

    @field_validator("terminal_ids")
    @classmethod
    def terminal_ids_must_be_unique(cls, value: List[str]) -> List[str]:
        if len(value) != len(set(value)):
            raise ValueError("terminal_ids must be unique")
        return value


class RegistryImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    document: Dict[str, object]
    duplicate_builtin: bool = False


class ProfileEnabledUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    enabled: bool


class HousekeepingSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    policy: Dict[str, Dict[str, object]]
    schedule: Dict[str, str]


class HousekeepingRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    dry_run: bool = True
    mode: Literal["frequent", "weekly", "pressure"] = "frequent"
    expected_plan_id: Optional[str] = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )


class FullCleanupRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_plan_id: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    confirmed: Literal[True]
    retire_dirty_worktrees: bool = False


class TelegramSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    enabled: bool
    chat_id: Optional[str] = Field(default=None, max_length=128)
    message_thread_id: Optional[int] = Field(default=None, ge=1, le=2_147_483_647)
    clear_bot_token: bool = False
    # Accept the opaque input at the model boundary so Pydantic never embeds a
    # malformed credential in its standard validation response. ``repr=False``
    # and ``exclude=True`` keep it out of model diagnostics/serialization; the
    # service performs strict type and shape validation with fixed safe errors.
    bot_token: Any = Field(
        default=None,
        exclude=True,
        repr=False,
        json_schema_extra={"type": "string", "format": "password"},
    )


def _decode_terminal_filename(encoded_filename: str) -> str:
    """Decode the ASCII filename header once and reject unsafe metadata."""
    if not encoded_filename or re.search(r"%(?![0-9A-Fa-f]{2})", encoded_filename):
        raise ValueError("Invalid terminal attachment filename")
    try:
        filename = unquote(encoded_filename, encoding="utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("Invalid terminal attachment filename") from error
    if not filename or any(ord(character) < 32 or ord(character) == 127 for character in filename):
        raise ValueError("Invalid terminal attachment filename")
    return filename


async def flow_daemon():
    """Background task to check and execute flows."""
    logger.info("Flow daemon started")
    while True:
        try:
            flows = await _run_operational_io(flow_service.get_flows_to_run)
            for flow in flows:
                try:
                    executed = await _run_operational_io(flow_service.execute_flow, flow.name)
                    if executed:
                        logger.info(f"Flow '{flow.name}' executed successfully")
                    else:
                        logger.info(f"Flow '{flow.name}' skipped (execute=false)")
                except Exception as e:
                    logger.error(f"Flow '{flow.name}' failed: {e}")
        except Exception as e:
            logger.error(f"Flow daemon error: {e}")

        await asyncio.sleep(60)


async def runtime_recovery_daemon():
    """Reconcile legacy live pane identities after health is available."""
    try:
        reconciled = await _run_operational_io(terminal_service.reconcile_legacy_runtime_identities)
        if reconciled:
            logger.info("Reconciled %s legacy runtime launch identities", reconciled)
    except Exception as exc:
        logger.warning("Runtime identity recovery deferred: %s", exc)


async def _workflow_daemon_pause() -> None:
    await asyncio.sleep(1)


async def _workflow_reconciliation_tick(
    registry: PluginRegistry | None, startup_recovery_pending: bool
) -> bool:
    """Run one isolated recovery tick and return whether startup replay remains due."""
    performed_full_recovery = False
    try:
        workspaces = await _run_workflow_io(
            managed_worktree_service.reconcile_writable_work_context_provisioning
        )
        if workspaces:
            logger.info("Reconciled %s managed supervisor workspaces", workspaces)
    except Exception as exc:
        logger.warning("Managed workspace reconciliation failed: %s", exc)
    try:
        from cli_agent_orchestrator.services.workspace_retirement_service import (
            reconcile_retiring_session_workspaces,
        )

        retired = await _run_workflow_io(reconcile_retiring_session_workspaces)
        if retired:
            logger.info("Finished %s interrupted Session workspace retirements", retired)
    except Exception as exc:
        logger.warning("Session workspace retirement reconciliation failed: %s", exc)
    try:
        recovered = await _run_workflow_io(
            recovery_takeover_service.reconcile_recovery_takeovers,
            registry=registry,
        )
        if recovered:
            logger.info("Reconciled %s supervisor recovery takeovers", recovered)
    except Exception as exc:
        logger.warning("Recovery takeover reconciliation failed: %s", exc)
    try:
        if startup_recovery_pending:
            await _run_workflow_io(inbox_service.reconcile_pending_messages, registry)
            startup_recovery_pending = False
            performed_full_recovery = True
        else:
            await _run_workflow_io(inbox_service.reconcile_handoff_continuations, registry)
    except Exception as exc:
        logger.warning("Workflow daemon Inbox reconciliation failed: %s", exc)
    try:
        if not startup_recovery_pending and not performed_full_recovery:
            await _run_workflow_io(inbox_service.reconcile_completed_assigned_children)
    except Exception as exc:
        logger.warning("Workflow daemon assigned-child reconciliation failed: %s", exc)
    try:
        if not startup_recovery_pending and not performed_full_recovery:
            await _run_workflow_io(inbox_service.reconcile_provider_execution_queue, registry)
    except Exception as exc:
        logger.warning("Workflow daemon reconciliation failed: %s", exc)
    return startup_recovery_pending


async def workflow_daemon(registry: PluginRegistry | None = None, *, recover_startup: bool = False):
    """Recover direct handoff completions before advancing durable workflow turns."""
    startup_recovery_pending = recover_startup
    while True:
        startup_recovery_pending = await _workflow_reconciliation_tick(
            registry, startup_recovery_pending
        )
        await _workflow_daemon_pause()


# Response Models
class TerminalOutputResponse(BaseModel):
    output: str
    mode: str
    availability: Literal["available", "unavailable"] = "available"
    reason_code: Optional[str] = None
    cursor: Optional[str] = None
    has_older: bool = False
    range_start: Optional[int] = None
    range_end: Optional[int] = None
    snapshot_size: Optional[int] = None


class SkillContentResponse(BaseModel):
    """Response model for a skill content lookup."""

    name: str
    content: str


class WorkingDirectoryResponse(BaseModel):
    """Response model for terminal working directory."""

    working_directory: Optional[str] = Field(
        description="Current working directory of the terminal, or None if unavailable"
    )


class TerminalAttachmentResponse(BaseModel):
    """An absolute runtime path suitable for insertion into a terminal prompt."""

    path: str


class HandoffResultV1SubmitRequest(BaseModel):
    """Trusted-agentctl sidecar request; relation identities are server-derived."""

    model_config = ConfigDict(extra="forbid", strict=True)

    logical_turn_id: int
    document: HandoffResultDocumentV1


class CodexSessionIdentityRequest(BaseModel):
    """Exact provider-native identity emitted by managed Codex SessionStart."""

    model_config = ConfigDict(extra="forbid", strict=True)

    session_id: str = Field(pattern=r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
    transcript_path: str
    cwd: str
    source: Literal["startup", "resume"]
    runtime_generation: str = Field(pattern=r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")


class CodexTurnCompleteRequest(BaseModel):
    """Exact provider completion emitted by the managed Codex Stop hook."""

    model_config = ConfigDict(extra="forbid", strict=True)

    session_id: str = Field(pattern=r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
    transcript_path: str = Field(min_length=1, max_length=4096)
    cwd: str = Field(min_length=1, max_length=4096)
    turn_id: str = Field(min_length=1, max_length=256)
    last_assistant_message: str = Field(min_length=1, max_length=4 * 1024 * 1024)
    runtime_generation: str = Field(pattern=r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")


class CreateFlowRequest(BaseModel):
    """Request model for creating a flow."""

    name: str
    schedule: str
    agent_profile: str
    provider: str = "kiro_cli"
    prompt_template: str
    project_id: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("projectId", "project_id"),
        serialization_alias="projectId",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Prevent path traversal — flow name becomes a filename."""
        if "/" in v or "\\" in v or ".." in v:
            raise ValueError("Flow name must not contain '/', '\\', or '..'")
        return v


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    path: str
    description: Optional[str] = None
    is_default: bool = Field(False, validation_alias=AliasChoices("isDefault", "is_default"))
    create_directory: bool = Field(
        False, validation_alias=AliasChoices("createDirectory", "create_directory")
    )


def _resolve_launch_project(
    project_id: Optional[str], working_directory: Optional[str]
) -> tuple[Optional[str], Optional[dict[str, str]]]:
    """An explicit project ID is authoritative; omission keeps legacy cwd launch."""
    project_path, context = project_service.launch_context(project_id)
    return (project_path, context) if context else (working_directory, None)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    logger.info("Starting CLI Agent Orchestrator server...")
    setup_logging()
    init_db()
    from cli_agent_orchestrator.services.control_plane_registry import (
        initialize_control_plane_registries,
    )

    initialize_control_plane_registries(provider_manager.adapter_registry)
    repaired_roles = terminal_service.reconcile_terminal_context_roles()
    if repaired_roles:
        logger.info("Reconciled %s exact supervisor context roles", repaired_roles)
    registry = PluginRegistry()
    await registry.load()
    app.state.plugin_registry = registry

    # Start flow daemon as background task
    daemon_task = asyncio.create_task(flow_daemon())
    workflow_task = asyncio.create_task(workflow_daemon(registry, recover_startup=True))
    runtime_recovery_task = asyncio.create_task(runtime_recovery_daemon())

    # Start inbox watcher
    inbox_observer = PollingObserver(timeout=INBOX_POLLING_INTERVAL)
    inbox_observer.schedule(LogFileHandler(registry), str(TERMINAL_LOG_DIR), recursive=False)
    inbox_observer.start()
    logger.info("Inbox watcher started (PollingObserver)")

    yield

    # Stop inbox observer
    inbox_observer.stop()
    inbox_observer.join()
    logger.info("Inbox watcher stopped")

    # Cancel daemon on shutdown
    daemon_task.cancel()
    try:
        await daemon_task
    except asyncio.CancelledError:
        pass
    workflow_task.cancel()
    try:
        await workflow_task
    except asyncio.CancelledError:
        pass
    runtime_recovery_task.cancel()
    try:
        await runtime_recovery_task
    except asyncio.CancelledError:
        pass

    await registry.teardown()
    logger.info("Shutting down CLI Agent Orchestrator server...")


def get_plugin_registry(request: Request) -> PluginRegistry:
    """Return the plugin registry stored on the FastAPI application state."""

    return cast(PluginRegistry, request.app.state.plugin_registry)


app = FastAPI(
    title="ThreadCells",
    description="ThreadCells local operations API",
    version=SERVER_VERSION,
    lifespan=lifespan,
    openapi_url="/_internal/openapi.json",
    docs_url="/_internal/docs",
    redoc_url=None,
)


@app.exception_handler(RequestValidationError)
async def safe_request_validation_error(request: Request, exc: RequestValidationError):
    """Never let Telegram credential input enter a framework validation response."""
    if request.url.path == "/api/v1/telegram":
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "detail": [
                    {
                        "type": "value_error",
                        "loc": ["body"],
                        "msg": "Invalid Telegram settings request",
                    }
                ]
            },
        )
    return await request_validation_exception_handler(request, exc)


@app.get("/_internal/runtime-generation")
async def runtime_generation() -> Dict[str, str]:
    """Expose the running API process generation to local MCP sidecars."""
    return {"generation": ACTIVE_RUNTIME_GENERATION}


# Security: DNS Rebinding Protection
# Validate Host header to prevent DNS rebinding attacks (CVE mitigation)
# Only allow requests with localhost Host headers
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=ALLOWED_HOSTS,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "cli-agent-orchestrator"}


@app.get("/agents/profiles")
async def list_agent_profiles_endpoint() -> List[Dict]:
    """List all available agent profiles from all configured directories."""
    try:
        from cli_agent_orchestrator.utils.agent_profiles import list_agent_profiles

        return await _run_operational_io(list_agent_profiles)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list agent profiles: {str(e)}",
        )


@app.get("/agents/providers")
async def list_providers_endpoint() -> List[Dict]:
    """Compatibility projection sourced from the canonical adapter registry."""
    return await _run_operational_io(_list_providers)


def _list_providers() -> List[Dict]:
    """Run provider executable/config preflight away from the ASGI event loop."""
    legacy_binaries = {
        "kiro_cli": "kiro-cli",
        "claude_code": "claude",
        "q_cli": "q",
        "codex": "codex",
        "gemini_cli": "gemini",
        "kimi_cli": "kimi",
        "copilot_cli": "copilot",
        "opencode_cli": "opencode",
    }
    result = []
    for manifest in provider_manager.adapter_registry.manifests():
        runtime = _provider_runtime_projection(manifest.adapter_id)
        result.append(
            {
                "name": manifest.adapter_id,
                "binary": legacy_binaries.get(manifest.adapter_id),
                "adapter_available": True,
                **runtime,
                "capabilities": manifest.capabilities.model_dump(mode="json"),
            }
        )
    return result


def _provider_runtime_projection(adapter_id: str, configuration: Optional[Dict] = None) -> Dict:
    """One public runtime truth shared by Settings and Spawn Agent."""
    preflight = provider_manager.adapter_registry.preflight(adapter_id, configuration)
    return {
        **preflight.model_dump(mode="json"),
        "availability": classify_provider_preflight(preflight).value,
        "available": provider_preflight_is_launchable(preflight),
    }


@app.get("/settings/agent-dirs")
async def get_agent_dirs_endpoint() -> Dict:
    """Get configured agent directories per provider."""
    from cli_agent_orchestrator.services.settings_service import (
        get_agent_dirs,
        get_extra_agent_dirs,
    )

    return {"agent_dirs": get_agent_dirs(), "extra_dirs": get_extra_agent_dirs()}


@app.get("/settings/orchestration-capacity")
async def get_orchestration_capacity_endpoint() -> Dict:
    """Expose effective read-only policy and live utilization from one backend truth."""
    return await _run_operational_io(get_resource_status)


@app.put("/settings/orchestration-capacity")
async def update_orchestration_capacity_endpoint(
    body: CapacitySettingsUpdate,
    request: Request,
    authorization: Annotated[Optional[str], Header()] = None,
) -> Dict:
    """Atomically update capacity; active work is projected as draining, never killed."""
    actor = _require_operator(request, authorization)
    set_capacity_settings(body.model_dump(), actor=actor)
    return get_resource_status()


@app.get("/api/v1/telegram")
async def get_telegram_settings_endpoint() -> Dict:
    """Return global Telegram state without ever returning the bot token."""
    return telegram_notification_service.get_settings()


@app.put("/api/v1/telegram")
async def update_telegram_settings_endpoint(
    body: TelegramSettingsUpdate,
    request: Request,
    authorization: Annotated[Optional[str], Header()] = None,
) -> Dict:
    actor = _require_operator(request, authorization)
    try:
        values = body.model_dump(exclude={"bot_token"})
        values["bot_token"] = body.bot_token
        return telegram_notification_service.update_settings(values, actor=actor)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"reason_code": "VALIDATION_FAILED", "message": str(exc)},
        ) from exc


@app.post("/api/v1/telegram/check")
async def check_telegram_connection_endpoint(
    request: Request,
    authorization: Annotated[Optional[str], Header()] = None,
) -> Dict:
    from starlette.concurrency import run_in_threadpool

    _require_operator(request, authorization)
    return await run_in_threadpool(telegram_notification_service.check_connection)


@app.post("/api/v1/telegram/test")
async def send_telegram_test_endpoint(
    request: Request,
    authorization: Annotated[Optional[str], Header()] = None,
) -> Dict:
    from starlette.concurrency import run_in_threadpool

    _require_operator(request, authorization)
    return await run_in_threadpool(telegram_notification_service.send_test_notification)


OPERATOR_SESSION_COOKIE = "threadcells_operator_session"
OPERATOR_SESSION_TTL_SECONDS = 300
TRUSTED_PROXY_ORIGINS_ENV = "THREADCELLS_TRUSTED_PROXY_ORIGINS"
LEGACY_TRUSTED_PROXY_ORIGINS_ENV = "THREADMESH_TRUSTED_PROXY_ORIGINS"


class _OperatorOriginError(ValueError):
    """A browser origin did not match the fail-closed operator boundary."""


def _canonical_http_origin(value: str) -> Optional[str]:
    """Return one exact HTTP(S) origin with default ports normalized."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower() if parsed.hostname else None
    if (
        scheme not in {"http", "https"}
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    host = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None and port != (443 if scheme == "https" else 80):
        host = f"{host}:{port}"
    return f"{scheme}://{host}"


def _trusted_proxy_origins() -> set[str]:
    """Load explicit HTTPS public origins without trusting forwarded headers."""
    raw = (
        os.environ.get(TRUSTED_PROXY_ORIGINS_ENV)
        or os.environ.get(LEGACY_TRUSTED_PROXY_ORIGINS_ENV, "")
    ).strip()
    if not raw:
        return set()
    origins: set[str] = set()
    for value in raw.split(","):
        origin = _canonical_http_origin(value.strip())
        if origin is None or not origin.startswith("https://"):
            raise _OperatorOriginError("trusted proxy origin configuration is invalid")
        origins.add(origin)
    return origins


def _validated_operator_browser_origin(request: Request) -> Optional[str]:
    """Validate an Origin header against loopback or explicit proxy origins."""
    supplied = request.headers.get("origin")
    if not supplied:
        return None
    origin = _canonical_http_origin(supplied)
    base_origin = _canonical_http_origin(str(request.base_url))
    if origin is None or base_origin is None:
        raise _OperatorOriginError("operator session origin mismatch")
    if origin != base_origin and origin not in _trusted_proxy_origins():
        raise _OperatorOriginError("operator session origin mismatch")
    return origin


def _operator_auth_error(exc: Exception) -> HTTPException:
    from cli_agent_orchestrator.services.operator_auth_service import (
        OperatorAuthUnavailable,
    )

    if isinstance(exc, OperatorAuthUnavailable):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"reason_code": "OPERATOR_AUTH_NOT_CONFIGURED"},
        )
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"reason_code": "OPERATOR_AUTHENTICATION_FAILED"},
    )


def _require_operator(request: Request, authorization: Optional[str]) -> str:
    from cli_agent_orchestrator.clients.database import authenticate_operator_session
    from cli_agent_orchestrator.services.operator_auth_service import (
        OperatorAuthenticationError,
        authenticate_operator_secret,
        load_operator_verifier,
    )

    try:
        # Removing the configured authority invalidates even unexpired cookies.
        load_operator_verifier()
        session_token = request.cookies.get(OPERATOR_SESSION_COOKIE)
        if session_token:
            session_id = authenticate_operator_session(session_token)
            if session_id:
                _validated_operator_browser_origin(request)
                return f"operator_session:{session_id}"
        prefix = "Bearer "
        if authorization and authorization.startswith(prefix):
            authenticate_operator_secret(authorization[len(prefix) :])
            return "operator_bearer"
        raise OperatorAuthenticationError("operator authentication failed")
    except (OperatorAuthenticationError, RuntimeError, _OperatorOriginError) as exc:
        raise _operator_auth_error(exc) from exc


@app.get("/operator/session")
async def get_operator_session_endpoint(request: Request) -> Dict:
    """Project operator-auth readiness without exposing verifier material or paths."""
    from cli_agent_orchestrator.clients.database import get_operator_session_status
    from cli_agent_orchestrator.services.operator_auth_service import (
        OPERATOR_VERIFIER_FILE_ENV,
        OperatorAuthUnavailable,
        load_operator_verifier,
    )

    try:
        load_operator_verifier()
    except (OperatorAuthUnavailable, RuntimeError) as exc:
        reference_present = bool(os.environ.get(OPERATOR_VERIFIER_FILE_ENV))
        if reference_present:
            logger.warning("Configured operator verifier failed safe validation: %s", exc)
        return {
            "configured": False,
            "configuration_state": "invalid" if reference_present else "missing",
            "authenticated": False,
            "expires_in_seconds": 0,
            "session_ttl_seconds": OPERATOR_SESSION_TTL_SECONDS,
            "verifier_reference": OPERATOR_VERIFIER_FILE_ENV,
        }
    token = request.cookies.get(OPERATOR_SESSION_COOKIE)
    session = get_operator_session_status(token) if token else None
    expires_in = 0
    if session is not None:
        expires_in = max(
            0,
            int((session["expires_at"] - datetime.now()).total_seconds()),
        )
    return {
        "configured": True,
        "configuration_state": "ready",
        "authenticated": session is not None,
        "expires_in_seconds": expires_in,
        "session_ttl_seconds": OPERATOR_SESSION_TTL_SECONDS,
        "verifier_reference": OPERATOR_VERIFIER_FILE_ENV,
    }


@app.post("/operator/session")
async def create_operator_session_endpoint(
    body: OperatorLoginRequest, request: Request, response: Response
) -> Dict[str, bool]:
    from cli_agent_orchestrator.clients.database import create_operator_session
    from cli_agent_orchestrator.services.operator_auth_service import (
        OperatorAuthenticationError,
        authenticate_operator_secret,
    )

    try:
        browser_origin = _validated_operator_browser_origin(request)
        authenticate_operator_secret(body.secret)
    except (OperatorAuthenticationError, RuntimeError, _OperatorOriginError) as exc:
        raise _operator_auth_error(exc) from exc
    token = create_operator_session()
    response.set_cookie(
        OPERATOR_SESSION_COOKIE,
        token,
        max_age=OPERATOR_SESSION_TTL_SECONDS,
        httponly=True,
        secure=request.url.scheme == "https"
        or bool(browser_origin and browser_origin.startswith("https://")),
        samesite="strict",
        path="/",
    )
    return {"authenticated": True}


@app.delete("/operator/session")
async def delete_operator_session_endpoint(request: Request, response: Response) -> Dict[str, bool]:
    from cli_agent_orchestrator.clients.database import revoke_operator_session

    token = request.cookies.get(OPERATOR_SESSION_COOKIE)
    revoked = revoke_operator_session(token) if token else False
    response.delete_cookie(OPERATOR_SESSION_COOKIE, path="/", samesite="strict")
    return {"revoked": revoked}


@app.post("/operator/xhigh-grants")
async def create_xhigh_grant_endpoint(
    body: XHighGrantRequest,
    request: Request,
    authorization: Annotated[Optional[str], Header()] = None,
) -> Dict:
    from cli_agent_orchestrator.services.operator_auth_service import (
        OperatorAuthenticationError,
        mint_xhigh_launch_grant,
    )
    from cli_agent_orchestrator.utils.terminal import validate_session_name

    identity = _require_operator(request, authorization)
    try:
        provider_manager.adapter_registry.get(body.provider)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid provider"
        ) from exc
    if body.launch_mode == "recovery_takeover":
        if not (
            body.target_terminal_id
            and body.expected_authority_generation
            and body.expected_runtime_generation
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"reason_code": "RECOVERY_TAKEOVER_SCOPE_REQUIRED"},
            )
        preview = await _run_operational_io(
            recovery_takeover_service.preview_recovery_takeover,
            body.target_terminal_id,
            expected_authority_generation=body.expected_authority_generation,
            expected_runtime_generation=body.expected_runtime_generation,
        )
        if not preview.get("eligible"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"reason_code": preview.get("reason_code")},
            )
        terminal = cast(Dict[str, Any], preview["terminal"])
        from cli_agent_orchestrator.services.control_plane_registry import resolve_launch

        resolution = resolve_launch(body.agent_profile, fallback_provider=body.provider)
        if resolution.provider_adapter_id != body.provider:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"reason_code": "RECOVERY_PROFILE_AUTHORITY_MISMATCH"},
            )
        try:
            return mint_xhigh_launch_grant(
                auth_identity=identity,
                agent_profile=body.agent_profile,
                provider=body.provider,
                canonical_worktree=str(terminal["launch_worktree"]),
                requested_session_name=None,
                confirmation=(
                    f"RECOVERY TAKEOVER {body.target_terminal_id}"
                    if body.confirmed
                    else body.confirmation or ""
                ),
                expected_confirmation=f"RECOVERY TAKEOVER {body.target_terminal_id}",
                owner_grant_required=resolution.owner_grant_required,
                grant_scope={
                    "profile_revision_id": resolution.profile_revision_id,
                    "provider_config_revision_id": resolution.provider_config_revision_id,
                    "project_id": terminal["project_id"],
                    "launch_mode": "recovery_takeover",
                    "delegation_depth": 0,
                    "target_terminal_id": body.target_terminal_id,
                    "expected_authority_generation": body.expected_authority_generation,
                    "expected_runtime_generation": body.expected_runtime_generation,
                },
            )
        except OperatorAuthenticationError as exc:
            raise _operator_auth_error(exc) from exc

    requested_session_name = (
        body.requested_session_name.strip()
        if body.requested_session_name and body.requested_session_name.strip()
        else None
    )
    if requested_session_name is not None and body.launch_mode != "existing_session":
        requested_session_name = validate_session_name(requested_session_name)
    try:
        resolved_project_id = body.project_id
        if body.launch_mode == "existing_session":
            if requested_session_name is None:
                raise ValueError("requested_session_name is required for existing session launches")
            authority = session_service.resolve_session_authority(
                requested_session_name, require_live=True
            )
            requested_session_name = authority.session_name
            launch_directory, project_context = project_service.resolve_add_agent_context(
                authority.session_id,
                body.project_id,
                body.working_directory,
            )
            resolved_project_id = project_context.get("id") if project_context is not None else None
        else:
            launch_directory, project_context = project_service.launch_context(body.project_id)
        worktree = terminal_service._canonical_worktree(launch_directory or body.working_directory)
        from cli_agent_orchestrator.services.control_plane_registry import resolve_launch

        resolution = resolve_launch(body.agent_profile, fallback_provider=body.provider)
        if resolution.provider_adapter_id != body.provider:
            raise ValueError("provider does not match the active profile revision")
        return mint_xhigh_launch_grant(
            auth_identity=identity,
            agent_profile=body.agent_profile,
            provider=body.provider,
            canonical_worktree=worktree,
            requested_session_name=requested_session_name,
            confirmation=(
                f"LAUNCH {body.agent_profile}" if body.confirmed else body.confirmation or ""
            ),
            owner_grant_required=resolution.owner_grant_required,
            grant_scope={
                "profile_revision_id": resolution.profile_revision_id,
                "provider_config_revision_id": resolution.provider_config_revision_id,
                "project_id": resolved_project_id,
                "launch_mode": body.launch_mode,
                "delegation_depth": 0,
            },
        )
    except OperatorAuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"reason_code": "XHIGH_CONFIRMATION_REQUIRED"},
        ) from exc
    except (SessionNotFoundError, SessionLifecycleError) as exc:
        raise _session_http_exception(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _registry_http_error(exc: Exception) -> HTTPException:
    from cli_agent_orchestrator.services.control_plane_registry import (
        RegistryConflictError,
        RegistryNotFoundError,
        RegistryValidationError,
    )

    if isinstance(exc, RegistryValidationError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"reason_code": "VALIDATION_FAILED", "issues": exc.issues},
        )
    if isinstance(exc, RegistryNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, RegistryConflictError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@app.get("/api/v1/providers")
async def list_provider_settings_endpoint() -> Dict:
    from cli_agent_orchestrator.services.control_plane_registry import (
        list_provider_configurations,
    )

    manifests = [
        {
            **manifest.model_dump(mode="json"),
            "source": provider_manager.adapter_registry.source(manifest.adapter_id),
            "adapter_available": True,
            "runtime": _provider_runtime_projection(manifest.adapter_id),
        }
        for manifest in provider_manager.adapter_registry.manifests()
    ]
    configurations = list_provider_configurations(redact_secret_refs=False)
    for configuration in configurations:
        document = configuration["document"]
        configuration["runtime"] = _provider_runtime_projection(
            str(configuration.get("adapter_id") or document["adapter_id"]), document
        )
        redacted_document = dict(document)
        redacted_document["secret_refs"] = {
            key: "configured" for key in document.get("secret_refs", {})
        }
        configuration["document"] = redacted_document
    return {
        "api_version": "1.0",
        "entry_point_group": "threadcells.provider_adapters.v1",
        "adapters": manifests,
        "configurations": configurations,
        "load_failures": provider_manager.adapter_registry.load_failures,
    }


@app.post("/api/v1/providers/validate")
async def validate_provider_settings_endpoint(body: RegistryImportRequest) -> Dict:
    try:
        resolved = provider_manager.adapter_registry.validate_configuration(body.document)
        return {"valid": True, "document": resolved.artifact.model_dump(mode="json"), "issues": []}
    except Exception as exc:
        from cli_agent_orchestrator.providers.contracts import ProviderConfigurationError

        if isinstance(exc, ProviderConfigurationError):
            return {
                "valid": False,
                "issues": [issue.model_dump(mode="json") for issue in exc.issues],
            }
        raise _registry_http_error(exc) from exc


@app.post("/api/v1/providers/import", status_code=status.HTTP_201_CREATED)
async def import_provider_settings_endpoint(
    body: RegistryImportRequest,
    request: Request,
    authorization: Annotated[Optional[str], Header()] = None,
) -> Dict:
    from cli_agent_orchestrator.services.control_plane_registry import (
        save_provider_configuration,
    )

    actor = _require_operator(request, authorization)
    try:
        return save_provider_configuration(
            body.document,
            actor=actor,
            registry=provider_manager.adapter_registry,
        )
    except Exception as exc:
        raise _registry_http_error(exc) from exc


@app.get("/api/v1/providers/{config_id}/export")
async def export_provider_settings_endpoint(config_id: str) -> Dict:
    from cli_agent_orchestrator.services.control_plane_registry import (
        get_provider_configuration,
    )

    try:
        document = dict(get_provider_configuration(config_id)["document"])
        # Secret references are operational wiring, not portable public data.
        document["secret_refs"] = {}
        return {"document": document, "redacted": True}
    except Exception as exc:
        raise _registry_http_error(exc) from exc


@app.post("/api/v1/providers/{config_id}/preflight")
async def preflight_provider_settings_endpoint(config_id: str) -> Dict:
    from cli_agent_orchestrator.services.control_plane_registry import (
        get_provider_configuration,
    )

    try:
        config = get_provider_configuration(config_id)
        return provider_manager.adapter_registry.preflight(
            config["adapter_id"], config["document"]
        ).model_dump(mode="json")
    except Exception as exc:
        raise _registry_http_error(exc) from exc


@app.get("/api/v1/providers/ai-prompt")
async def provider_ai_prompt_endpoint() -> Dict[str, str]:
    return {
        "prompt": (
            "Create a ThreadCells provider-configuration V1 JSON document. Reference an installed "
            "adapter_id; use declarative settings and secret_refs only. Never include commands, "
            "executable paths, shell arguments, environment maps, or secret values."
        )
    }


@app.get("/api/v1/profiles")
async def list_profile_settings_endpoint(include_disabled: bool = False) -> List[Dict]:
    from cli_agent_orchestrator.services.control_plane_registry import (
        list_profiles,
    )

    return list_profiles(include_disabled=include_disabled)


@app.post("/api/v1/profiles/validate")
async def validate_profile_settings_endpoint(body: RegistryImportRequest) -> Dict:
    from cli_agent_orchestrator.services.control_plane_registry import (
        RegistryValidationError,
        validate_profile_document,
    )

    try:
        document = validate_profile_document(body.document, trusted_operator=False)
        return {"valid": True, "document": document.model_dump(mode="json"), "issues": []}
    except RegistryValidationError as exc:
        return {"valid": False, "issues": exc.issues}


@app.post("/api/v1/profiles/import", status_code=status.HTTP_201_CREATED)
async def import_profile_settings_endpoint(
    body: RegistryImportRequest,
    request: Request,
    authorization: Annotated[Optional[str], Header()] = None,
) -> Dict:
    from cli_agent_orchestrator.services.control_plane_registry import save_profile

    actor = _require_operator(request, authorization)
    try:
        return save_profile(
            body.document,
            actor=actor,
            trusted_operator=True,
            duplicate_builtin=body.duplicate_builtin,
        )
    except Exception as exc:
        raise _registry_http_error(exc) from exc


@app.get("/api/v1/profiles/ai-prompt")
async def profile_ai_prompt_endpoint() -> Dict[str, str]:
    return {
        "prompt": (
            "Create a ThreadCells ProfileDefinition V1 JSON document. Keep execution_mode separate "
            "from model power, reference an installed provider_config_id and registered MCP IDs, "
            "and do not include executable MCP commands or unrestricted tools."
        )
    }


@app.get("/api/v1/profiles/{profile_id}")
async def get_profile_settings_endpoint(profile_id: str) -> Dict:
    from cli_agent_orchestrator.services.control_plane_registry import get_profile

    try:
        return get_profile(profile_id)
    except Exception as exc:
        raise _registry_http_error(exc) from exc


@app.get("/api/v1/profiles/{profile_id}/export")
async def export_profile_settings_endpoint(profile_id: str) -> Dict:
    from cli_agent_orchestrator.services.control_plane_registry import get_profile

    try:
        return {"document": get_profile(profile_id)["document"]}
    except Exception as exc:
        raise _registry_http_error(exc) from exc


@app.get("/api/v1/profiles/{profile_id}/preview")
async def preview_profile_settings_endpoint(profile_id: str) -> Dict:
    from cli_agent_orchestrator.services.control_plane_registry import resolve_launch

    try:
        resolution = resolve_launch(profile_id, fallback_provider="codex")
        provider = dict(resolution.provider_configuration)
        provider["secret_refs"] = {key: "configured" for key in provider.get("secret_refs", {})}
        return {"snapshot": resolution.snapshot, "provider_configuration": provider}
    except Exception as exc:
        raise _registry_http_error(exc) from exc


@app.patch("/api/v1/profiles/{profile_id}")
async def update_profile_enabled_endpoint(
    profile_id: str,
    body: ProfileEnabledUpdate,
    request: Request,
    authorization: Annotated[Optional[str], Header()] = None,
) -> Dict:
    from cli_agent_orchestrator.services.control_plane_registry import set_profile_enabled

    actor = _require_operator(request, authorization)
    try:
        return set_profile_enabled(profile_id, body.enabled, actor=actor)
    except Exception as exc:
        raise _registry_http_error(exc) from exc


@app.get("/api/v1/housekeeping")
async def get_housekeeping_settings_endpoint() -> Dict:
    from cli_agent_orchestrator.services.housekeeping_service import (
        get_housekeeping_settings,
    )

    return get_housekeeping_settings()


@app.put("/api/v1/housekeeping")
async def update_housekeeping_settings_endpoint(
    body: HousekeepingSettingsUpdate,
    request: Request,
    authorization: Annotated[Optional[str], Header()] = None,
) -> Dict:
    from cli_agent_orchestrator.services.housekeeping_service import (
        set_housekeeping_settings,
    )

    actor = _require_operator(request, authorization)
    try:
        return set_housekeeping_settings(body.model_dump(mode="json"), actor=actor)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"reason_code": "VALIDATION_FAILED", "message": str(exc)},
        ) from exc


@app.get("/api/v1/housekeeping/plan")
async def get_housekeeping_plan_endpoint(
    mode: Literal["frequent", "weekly", "pressure"] = "frequent",
) -> Dict:
    from starlette.concurrency import run_in_threadpool

    from cli_agent_orchestrator.services.housekeeping_service import (
        plan_housekeeping_serialized,
    )

    try:
        plan = await run_in_threadpool(lambda: plan_housekeeping_serialized(mode=mode))
        return plan.as_dict()
    except RuntimeError as exc:
        if str(exc) == "HOUSEKEEPING_BUSY":
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail={"reason_code": "HOUSEKEEPING_BUSY"},
            ) from exc
        raise


@app.post("/api/v1/housekeeping/run")
async def run_housekeeping_endpoint(
    body: HousekeepingRunRequest,
    request: Request,
    authorization: Annotated[Optional[str], Header()] = None,
) -> Dict:
    from starlette.concurrency import run_in_threadpool

    from cli_agent_orchestrator.services.housekeeping_service import run_housekeeping

    _require_operator(request, authorization)
    if not body.dry_run and body.expected_plan_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"reason_code": "HOUSEKEEPING_PLAN_REQUIRED"},
        )
    try:
        summary = await run_in_threadpool(
            lambda: run_housekeeping(
                dry_run=body.dry_run,
                mode=body.mode,
                expected_plan_id=body.expected_plan_id,
            )
        )
        return summary.as_dict()
    except RuntimeError as exc:
        if str(exc) == "HOUSEKEEPING_BUSY":
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail={"reason_code": "HOUSEKEEPING_BUSY"},
            ) from exc
        if str(exc) == "HOUSEKEEPING_PLAN_CHANGED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"reason_code": "HOUSEKEEPING_PLAN_CHANGED"},
            ) from exc
        raise


@app.get("/api/v1/housekeeping/full-cleanup/plan")
async def get_full_cleanup_plan_endpoint(retire_dirty_worktrees: bool = False) -> Dict:
    from starlette.concurrency import run_in_threadpool

    from cli_agent_orchestrator.services.housekeeping_service import (
        plan_full_cleanup_serialized,
    )

    try:
        return await run_in_threadpool(
            plan_full_cleanup_serialized,
            retire_dirty_worktrees=retire_dirty_worktrees,
        )
    except RuntimeError as exc:
        if str(exc) == "HOUSEKEEPING_BUSY":
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail={"reason_code": "HOUSEKEEPING_BUSY"},
            ) from exc
        raise


@app.post("/api/v1/housekeeping/full-cleanup/run")
async def run_full_cleanup_endpoint(
    body: FullCleanupRunRequest,
    request: Request,
    authorization: Annotated[Optional[str], Header()] = None,
) -> Dict:
    from starlette.concurrency import run_in_threadpool

    from cli_agent_orchestrator.services.full_cleanup_helper import (
        FullCleanupHelperError,
        execute_via_privileged_helper,
    )
    from cli_agent_orchestrator.services.housekeeping_service import run_full_cleanup

    actor = _require_operator(request, authorization)
    session_token = (
        request.cookies.get(OPERATOR_SESSION_COOKIE)
        if actor.startswith("operator_session:")
        else None
    )
    bearer_secret = (
        authorization[len("Bearer ") :]
        if actor == "operator_bearer"
        and authorization is not None
        and authorization.startswith("Bearer ")
        else None
    )
    try:
        summary = await run_in_threadpool(
            lambda: run_full_cleanup(
                expected_plan_id=body.expected_plan_id,
                confirmed=body.confirmed,
                retire_dirty_worktrees=body.retire_dirty_worktrees,
                privileged_cleanup_executor=lambda **_kwargs: execute_via_privileged_helper(
                    expected_plan_id=body.expected_plan_id,
                    confirmed=True,
                    session_token=session_token,
                    bearer_secret=bearer_secret,
                    retire_dirty_worktrees=body.retire_dirty_worktrees,
                ),
            )
        )
        return summary.as_dict()
    except (FullCleanupHelperError, RuntimeError) as exc:
        reason = str(exc)
        diagnostic_id = (
            exc.diagnostic_id
            if isinstance(exc, FullCleanupHelperError)
            and isinstance(exc.diagnostic_id, str)
            and re.fullmatch(r"[0-9a-f]{32}", exc.diagnostic_id)
            else None
        )
        detail = {"reason_code": reason}
        if diagnostic_id is not None:
            detail["diagnostic_id"] = diagnostic_id
        if not re.fullmatch(r"[A-Z0-9_]{3,96}", reason):
            reason = "FULL_CLEANUP_EXECUTION_FAILED"
            detail["reason_code"] = reason
        if reason in {"HOUSEKEEPING_BUSY", "FULL_CLEANUP_ADMISSION_BUSY"}:
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail=detail,
            ) from exc
        if reason in {
            "HOUSEKEEPING_PLAN_CHANGED",
            "FULL_CLEANUP_NOT_IDLE",
            "FULL_CLEANUP_IDLE_INVENTORY_UNKNOWN",
        }:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=detail,
            ) from exc
        if reason == "FULL_CLEANUP_CONFIRMATION_REQUIRED":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=detail,
            ) from exc
        if reason == "OPERATOR_AUTHENTICATION_FAILED":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=detail,
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
        ) from exc


@app.get("/api/v1/housekeeping/report")
async def get_housekeeping_report_endpoint() -> Dict:
    config = load_operations_config()
    path = Path(str(config["root"])) / "state" / "cao" / "housekeeping-status.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("status must be an object")
        return cast(Dict, value)
    except FileNotFoundError:
        return {"status": "never_run"}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"reason_code": "HOUSEKEEPING_STATUS_UNAVAILABLE"},
        ) from exc


@app.get("/schemas/v1")
async def list_public_schemas_endpoint() -> List[Dict[str, str]]:
    from cli_agent_orchestrator.services.schema_service import list_schemas

    return list_schemas()


@app.get("/schemas/v1/{name}")
async def get_public_schema_endpoint(name: str) -> Dict:
    from cli_agent_orchestrator.services.schema_service import get_schema

    try:
        return get_schema(name)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Schema not found"
        ) from exc


@app.get("/examples/v1/{name}")
async def get_public_example_endpoint(name: str) -> Dict:
    from cli_agent_orchestrator.services.schema_service import get_example

    try:
        return get_example(name)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Example not found"
        ) from exc


def _admission_http_exception(exc: AdmissionDenied) -> HTTPException:
    """Map stable admission semantics to a client-visible HTTP contract."""
    status_by_reason = {
        "WORKTREE_WRITER_LEASE_HELD": status.HTTP_423_LOCKED,
        "WORKTREE_AUTHORITY_UNRECONCILED": status.HTTP_409_CONFLICT,
        "TOTAL_PROVIDER_CAPACITY_EXHAUSTED": status.HTTP_429_TOO_MANY_REQUESTS,
        "PROVIDER_EXECUTION_CAPACITY_EXHAUSTED": status.HTTP_429_TOO_MANY_REQUESTS,
        "RESIDENT_SUPERVISOR_CAPACITY_EXHAUSTED": status.HTTP_429_TOO_MANY_REQUESTS,
        "SESSION_PRIMARY_SUPERVISOR_EXISTS": status.HTTP_409_CONFLICT,
        "PROJECT_SOURCE_NOT_GIT": status.HTTP_409_CONFLICT,
        "WORK_CONTEXT_REQUEST_CONFLICT": status.HTTP_409_CONFLICT,
        "WORK_CONTEXT_AUTHORITY_CONFLICT": status.HTTP_409_CONFLICT,
        "WORK_CONTEXT_AUTHORITY_CHANGED": status.HTTP_409_CONFLICT,
        "WORK_CONTEXT_PROVIDER_LAUNCH_UNCERTAIN": status.HTTP_409_CONFLICT,
        "WORK_CONTEXT_CAPACITY_EXHAUSTED": status.HTTP_429_TOO_MANY_REQUESTS,
        "RESOURCE_HEALTH_REJECTED": status.HTTP_503_SERVICE_UNAVAILABLE,
        "OWNER_GRANT_REQUIRED": status.HTTP_403_FORBIDDEN,
        "OWNER_GRANT_INVALID_OR_EXPIRED": status.HTTP_403_FORBIDDEN,
        "OWNER_GRANT_ALREADY_CONSUMED": status.HTTP_403_FORBIDDEN,
        "OWNER_GRANT_SCOPE_MISMATCH": status.HTTP_403_FORBIDDEN,
        "ADMISSION_FENCE_TIMEOUT": status.HTTP_409_CONFLICT,
        "TERMINAL_NOT_FOUND": status.HTTP_404_NOT_FOUND,
        "TERMINAL_RUNTIME_NOT_WRITABLE": status.HTTP_409_CONFLICT,
        "WORKFLOW_INPUT_IDEMPOTENCY_CONFLICT": status.HTTP_409_CONFLICT,
        "WORKFLOW_INPUT_NO_LONGER_EXECUTABLE": status.HTTP_409_CONFLICT,
        "WORKSPACE_RETIRED": status.HTTP_409_CONFLICT,
    }
    return HTTPException(
        status_code=status_by_reason.get(exc.reason_code, status.HTTP_503_SERVICE_UNAVAILABLE),
        detail={"reason_code": exc.reason_code, "status": exc.status},
    )


class AgentDirsUpdate(BaseModel):
    agent_dirs: Optional[Dict[str, str]] = None
    extra_dirs: Optional[List[str]] = None


class RuntimeBrandingUpdate(BaseModel):
    title: Optional[str] = None
    subtitle: Optional[str] = None


@app.post("/settings/agent-dirs")
async def set_agent_dirs_endpoint(body: AgentDirsUpdate) -> Dict:
    """Update agent directories per provider."""
    from cli_agent_orchestrator.services.settings_service import (
        get_agent_dirs,
        get_extra_agent_dirs,
        set_agent_dirs,
        set_extra_agent_dirs,
    )

    if body.agent_dirs is not None:
        set_agent_dirs(body.agent_dirs)
    if body.extra_dirs is not None:
        set_extra_agent_dirs(body.extra_dirs)
    return {
        "agent_dirs": get_agent_dirs(),
        "extra_dirs": get_extra_agent_dirs(),
    }


@app.get("/settings/branding")
async def get_runtime_branding() -> Dict:
    return await _run_operational_io(branding_service.get_branding)


@app.patch("/settings/branding")
async def update_runtime_branding(body: RuntimeBrandingUpdate) -> Dict:
    try:
        return branding_service.update_branding(title=body.title, subtitle=body.subtitle)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.post("/settings/branding/logo")
async def upload_runtime_branding_logo(request: Request) -> Dict:
    try:
        return branding_service.upload_logo(
            await request.body(), request.headers.get("content-type")
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.post("/settings/branding/logo/reset")
async def reset_runtime_branding_logo() -> Dict:
    return branding_service.reset_logo()


@app.get("/settings/branding/logo")
async def runtime_branding_logo() -> FileResponse:
    logo = branding_service.logo_file()
    if logo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No custom runtime logo")
    path, content_type = logo
    return FileResponse(
        path,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.get("/skills/{name}", response_model=SkillContentResponse)
async def get_skill_content(name: str) -> SkillContentResponse:
    """Return the full Markdown body for an installed skill."""
    try:
        skill_name = validate_skill_name(name)
        content = load_skill_content(skill_name)
        return SkillContentResponse(name=name, content=content)
    except SkillNameError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid skill name: {name}",
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to load skill: {str(e)}",
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill not found: {name}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to load skill: {str(e)}",
        )


@app.get("/projects", response_model=List[Project])
async def list_projects() -> List[Project]:
    """List server-authoritative launch projects, default first."""
    try:
        return await _run_operational_io(project_service.list_projects)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@app.get("/usage/statistics")
async def usage_statistics() -> Dict:
    """Read-only approximate operational usage, not a billing surface."""
    try:
        return await _run_operational_io(usage_service.statistics)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@app.post("/projects", response_model=Project, status_code=status.HTTP_201_CREATED)
async def create_project(body: CreateProjectRequest) -> Project:
    """Register an existing directory or create exactly one final directory leaf."""
    try:
        return project_service.create_project(
            name=body.name,
            path=body.path,
            description=body.description,
            is_default=body.is_default,
            create_directory=body.create_directory,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.post("/projects/{project_id}/default", response_model=Project)
async def set_default_project(project_id: str) -> Project:
    try:
        return project_service.set_default_project(project_id)
    except ProjectResolutionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.put("/projects/{project_id}", response_model=Project)
async def update_project(project_id: str, body: UpdateProject) -> Project:
    """Edit a registry project transactionally without moving files or rewriting history."""
    try:
        current = project_service.get_registered_project(project_id)
        values = body.model_dump(by_alias=False, exclude_unset=True)
        return project_service.update_project(
            project_id,
            name=values.get("name", current.name),
            path=values.get("path", current.path),
            description=values.get("description", current.description),
            is_default=values.get("is_default"),
        )
    except (ProjectResolutionError, ValueError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.delete("/projects/{project_id}")
async def delete_project(project_id: str) -> Dict[str, bool]:
    try:
        return {"success": project_service.delete_project(project_id)}
    except ProjectResolutionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.post("/sessions", response_model=Terminal, status_code=status.HTTP_201_CREATED)
async def create_session(
    request: Request,
    provider: str,
    agent_profile: str,
    session_name: Optional[str] = None,
    working_directory: Optional[str] = None,
    project_id: Optional[str] = Query(default=None, alias="projectId"),
    work_context_request_id: Optional[str] = Query(default=None, alias="workContextRequestId"),
    allowed_tools: Optional[str] = None,
    owner_grant_launch_id: Optional[str] = None,
    owner_grant: Annotated[Optional[str], Header(alias="X-ThreadCells-Owner-Grant")] = None,
) -> Terminal:
    """Create a new session with exactly one terminal."""
    creation_task: asyncio.Task[Terminal] | None = None
    try:
        # Preserve the existing session naming contract: an omitted or blank
        # name delegates to the service's automatic generated-name behavior.
        session_name = session_name.strip() or None if session_name else None

        # Parse comma-separated allowed_tools string into list
        allowed_tools_list = allowed_tools.split(",") if allowed_tools else None

        launch_directory, project_context = _resolve_launch_project(project_id, working_directory)
        registry = get_plugin_registry(request)
        creation_task = asyncio.create_task(
            asyncio.to_thread(
                session_service.create_session,
                provider=provider,
                agent_profile=agent_profile,
                session_name=session_name,
                working_directory=launch_directory,
                allowed_tools=allowed_tools_list,
                registry=registry,
                project_context=project_context,
                owner_grant_token=owner_grant,
                owner_grant_launch_id=owner_grant_launch_id,
                work_context_request_id=work_context_request_id,
            )
        )
        return await asyncio.shield(creation_task)

    except asyncio.CancelledError:
        # Cancelling an await does not stop its worker thread.  Reconcile that
        # worker independently so a session that finishes after its caller has
        # gone away is removed through the normal session cleanup path.
        assert creation_task is not None
        asyncio.create_task(_cleanup_session_created_after_cancellation(creation_task, registry))
        raise
    except AdmissionDenied as e:
        raise _admission_http_exception(e)
    except (ValueError, ProjectResolutionError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create session: {str(e)}",
        )


async def _cleanup_session_created_after_cancellation(
    creation_task: asyncio.Task[Terminal], registry: PluginRegistry
) -> None:
    """Remove a session that completed after its creating request was cancelled."""
    try:
        terminal = await asyncio.shield(creation_task)
    except Exception:
        # The original caller was cancelled, so preserve the creation failure
        # rather than translating it into a cleanup failure.
        return

    try:
        await asyncio.shield(
            asyncio.to_thread(
                session_service.delete_session,
                terminal.session_name,
                registry=registry,
            )
        )
    except Exception:
        logger.exception(
            "Failed to clean up session %s created after request cancellation",
            terminal.session_name,
        )


def _csv_query_values(value: Optional[str]) -> List[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _session_http_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, (SessionNotFoundError, ValueError)):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, SessionLifecycleError):
        return HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
                if exc.inventory_uncertain
                else status.HTTP_409_CONFLICT
            ),
            detail={"reason_code": exc.reason_code, "message": str(exc)},
        )
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@app.get("/ui/overview")
async def get_ui_overview() -> Dict:
    return await _run_ui_read(ui_read_model_service.get_overview)


@app.get("/ui/sessions")
async def list_ui_session_summaries(
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    query: str = Query(default="", max_length=200),
) -> Dict:
    return await _run_ui_read(
        ui_read_model_service.list_session_summaries,
        limit=limit,
        offset=offset,
        query=query,
    )


@app.get("/ui/agents")
async def list_ui_agent_summaries(
    limit: int = Query(default=40, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session_id: Optional[str] = Query(default=None, max_length=200),
    query: str = Query(default="", max_length=200),
    activity: Optional[str] = Query(default=None, max_length=500),
    workflow_state: Optional[str] = Query(default=None, max_length=500),
    profile: Optional[str] = Query(default=None, max_length=1000),
    home_filter: Optional[str] = Query(default=None, max_length=50),
) -> Dict:
    try:
        return await _run_ui_read(
            ui_read_model_service.list_agent_summaries,
            limit=limit,
            offset=offset,
            session_id=session_id,
            query=query,
            activities=_csv_query_values(activity),
            workflow_states=_csv_query_values(workflow_state),
            profiles=_csv_query_values(profile),
            home_filter=home_filter,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@app.get("/sessions")
async def list_sessions() -> List[Dict]:
    try:
        return await _run_operational_io(session_service.list_sessions)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list sessions: {str(e)}",
        )


@app.get("/sessions/{session_name}")
async def get_session(session_name: str) -> Dict:
    try:
        return await _run_operational_io(session_service.get_session, session_name)
    except (SessionNotFoundError, ValueError, SessionLifecycleError) as e:
        raise _session_http_exception(e) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get session: {str(e)}",
        )


@app.get("/sessions/{session_name}/working-directory", response_model=WorkingDirectoryResponse)
async def get_session_root_working_directory(session_name: str) -> WorkingDirectoryResponse:
    """Return the original tmux session root directory, when available."""
    try:
        working_directory = await _run_operational_io(
            session_service.get_session_root_working_directory, session_name
        )
        return WorkingDirectoryResponse(working_directory=working_directory)
    except (SessionNotFoundError, ValueError, SessionLifecycleError) as e:
        raise _session_http_exception(e) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get session working directory: {str(e)}",
        )


@app.delete("/sessions/{session_name}")
async def delete_session(
    request: Request,
    session_name: str,
    confirm_dirty_workspace: bool = False,
) -> Dict:
    try:
        result = await run_in_threadpool(
            session_service.delete_session,
            session_name,
            registry=get_plugin_registry(request),
            confirm_dirty_workspace=confirm_dirty_workspace,
        )
        return {"success": True, **result}
    except (SessionNotFoundError, ValueError, SessionLifecycleError) as e:
        raise _session_http_exception(e) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete session: {str(e)}",
        )


@app.get("/sessions/{session_name}/deletion-preflight")
async def get_session_deletion_preflight(session_name: str) -> Dict:
    try:
        return await _run_ui_read(
            session_service.get_session_deletion_preflight,
            session_name,
        )
    except (SessionNotFoundError, ValueError, SessionLifecycleError) as exc:
        raise _session_http_exception(exc) from exc


@app.post(
    "/sessions/{session_name}/terminals",
    response_model=Terminal,
    status_code=status.HTTP_201_CREATED,
)
async def create_terminal_in_session(
    request: Request,
    session_name: str,
    provider: str,
    agent_profile: str,
    working_directory: Optional[str] = None,
    project_id: Optional[str] = Query(default=None, alias="projectId"),
    allowed_tools: Optional[str] = None,
    managed_worktree_kind: Optional[str] = None,
    owner_grant_launch_id: Optional[str] = None,
    owner_grant: Annotated[Optional[str], Header(alias="X-ThreadCells-Owner-Grant")] = None,
) -> Terminal:
    """Create additional terminal in existing session."""
    try:
        resolved_provider = resolve_provider(agent_profile, fallback_provider=provider)

        # Parse comma-separated allowed_tools string into list
        allowed_tools_list = allowed_tools.split(",") if allowed_tools else None

        authority = await run_in_threadpool(
            session_service.resolve_session_authority,
            session_name,
            require_live=True,
        )
        launch_directory, project_context = await run_in_threadpool(
            project_service.resolve_add_agent_context,
            authority.session_id,
            project_id,
            working_directory,
        )
        result = await run_in_threadpool(
            terminal_service.create_terminal,
            provider=resolved_provider,
            agent_profile=agent_profile,
            session_name=authority.session_name,
            session_lifetime_id=authority.session_id,
            new_session=False,
            working_directory=launch_directory,
            allowed_tools=allowed_tools_list,
            registry=get_plugin_registry(request),
            managed_worktree_kind=managed_worktree_kind,
            project_context=project_context,
            owner_grant_token=owner_grant,
            owner_grant_launch_id=owner_grant_launch_id,
        )
        return result
    except AdmissionDenied as e:
        raise _admission_http_exception(e)
    except ProjectResolutionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except (SessionNotFoundError, SessionLifecycleError) as e:
        raise _session_http_exception(e) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create terminal: {str(e)}",
        )


@app.get("/sessions/{session_name}/terminals")
async def list_terminals_in_session(session_name: str) -> List[Dict]:
    """List all terminals in a session."""
    try:
        authority = await _run_ui_read(session_service.resolve_session_authority, session_name)

        return authority.terminals
    except (SessionNotFoundError, SessionLifecycleError) as e:
        raise _session_http_exception(e) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list terminals: {str(e)}",
        )


@app.get("/terminals/{terminal_id}", response_model=Terminal)
async def get_terminal(terminal_id: TerminalId) -> Terminal:
    try:
        terminal = await _run_operational_io(terminal_service.get_terminal, terminal_id)
        return Terminal(**terminal)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get terminal: {str(e)}",
        )


@app.get("/terminals/{terminal_id}/working-directory", response_model=WorkingDirectoryResponse)
async def get_terminal_working_directory(terminal_id: TerminalId) -> WorkingDirectoryResponse:
    """Get the current working directory of a terminal's pane."""
    try:
        working_directory = await _run_operational_io(
            terminal_service.get_working_directory, terminal_id
        )
        return WorkingDirectoryResponse(working_directory=working_directory)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get working directory: {str(e)}",
        )


@app.get("/terminals/{terminal_id}/recovery-takeover/preview")
async def preview_terminal_recovery_takeover(
    request: Request,
    terminal_id: TerminalId,
    expected_authority_generation: Optional[str] = None,
    expected_runtime_generation: Optional[str] = None,
    authorization: Annotated[Optional[str], Header()] = None,
) -> Dict[str, Any]:
    """Inspect one exact recovery target without mutating its authority."""
    _require_operator(request, authorization)
    return cast(
        Dict[str, Any],
        await _run_operational_io(
            recovery_takeover_service.preview_recovery_takeover,
            str(terminal_id),
            expected_authority_generation=expected_authority_generation,
            expected_runtime_generation=expected_runtime_generation,
        ),
    )


@app.post("/recovery-takeovers/capabilities")
async def list_recovery_takeover_capabilities(
    body: RecoveryTakeoverCapabilitiesRequest,
) -> Dict[str, Any]:
    """Project only safe eligibility needed to render recovery actions.

    The detailed preview and every mutating operation remain behind operator
    authentication. This projection deliberately omits worktree paths and
    authority generations while reusing the canonical backend eligibility
    evaluator.
    """
    return cast(
        Dict[str, Any],
        await _run_operational_io(
            recovery_takeover_service.list_recovery_takeover_capabilities,
            body.terminal_ids,
        ),
    )


@app.post("/terminals/{terminal_id}/recovery-takeover", status_code=status.HTTP_201_CREATED)
async def create_terminal_recovery_takeover(
    request: Request,
    terminal_id: TerminalId,
    body: RecoveryTakeoverRequest,
    owner_grant: Annotated[Optional[str], Header(alias="X-ThreadCells-Owner-Grant")] = None,
    authorization: Annotated[Optional[str], Header()] = None,
) -> Dict[str, Any]:
    """Fence one unusable supervisor and admit its sole recovery successor."""
    _require_operator(request, authorization)
    if not owner_grant:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"reason_code": "OWNER_GRANT_REQUIRED"},
        )
    try:
        return cast(
            Dict[str, Any],
            await _run_operational_io(
                recovery_takeover_service.request_recovery_takeover,
                request_id=str(body.request_id),
                old_terminal_id=str(terminal_id),
                expected_authority_generation=body.expected_authority_generation,
                expected_runtime_generation=body.expected_runtime_generation,
                agent_profile=body.agent_profile,
                provider=body.provider,
                owner_grant_token=owner_grant,
                owner_grant_launch_id=body.owner_grant_launch_id,
                registry=get_plugin_registry(request),
            ),
        )
    except AdmissionDenied as exc:
        raise _admission_http_exception(exc) from exc
    except recovery_takeover_service.RecoveryTakeoverError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_403_FORBIDDEN
                if exc.reason_code.startswith("OWNER_GRANT")
                else status.HTTP_409_CONFLICT
            ),
            detail={"reason_code": exc.reason_code},
        ) from exc


@app.get("/recovery-takeovers/{takeover_id}")
async def get_recovery_takeover_endpoint(
    request: Request,
    takeover_id: str,
    authorization: Annotated[Optional[str], Header()] = None,
) -> Dict[str, Any]:
    """Return one durable, non-secret recovery saga state to its operator."""
    _require_operator(request, authorization)
    if re.fullmatch(r"[0-9a-f]{32}", takeover_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason_code": "RECOVERY_TAKEOVER_NOT_FOUND"},
        )
    from cli_agent_orchestrator.clients.database import get_recovery_takeover

    takeover = await _run_operational_io(get_recovery_takeover, takeover_id)
    if takeover is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason_code": "RECOVERY_TAKEOVER_NOT_FOUND"},
        )
    return cast(Dict[str, Any], takeover)


@app.post("/terminals/{terminal_id}/input")
async def send_terminal_input(
    request: Request,
    terminal_id: TerminalId,
    message: str,
) -> Dict:
    """Deliver a public input as a new, server-bound workflow admission.

    ``sender_id`` and ``orchestration_type`` deliberately are not public
    parameters.  They describe server-to-server transport bookkeeping, and
    allowing a caller to provide them previously let a public request suppress
    the admission which fences an older model invocation.
    """
    return await run_in_threadpool(_send_server_bound_input, request, terminal_id, message)


@app.post("/terminals/{terminal_id}/workflow-input")
async def send_terminal_workflow_input(
    request: Request,
    terminal_id: TerminalId,
    body: WorkflowInputRequest,
) -> Dict:
    """Submit one user-authored Composer message through canonical admission.

    The terminal WebSocket remains a raw PTY transport.  This explicit HTTP
    boundary is the only UI transport that turns a Composer submission into a
    workflow input, and deliberately delegates to the same helper as the
    established public input endpoint.
    """
    if not body.message.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="message is empty"
        )
    return await run_in_threadpool(
        _send_server_bound_input,
        request,
        terminal_id,
        body.message,
        request_id=str(body.request_id),
    )


def _send_server_bound_input(
    request: Request,
    terminal_id: TerminalId,
    message: str,
    turn_id: Optional[int] = None,
    *,
    request_id: Optional[str] = None,
    sender_id: Optional[str] = None,
    orchestration_type: Optional[OrchestrationType] = None,
) -> Dict:
    """Create the current admission immediately before one provider input."""
    raw_message = message
    composer_submission = request_id is not None

    def retain_composer_for_recovery(reason_code: str) -> Optional[Dict[str, Any]]:
        """Turn an uncertain pre-receipt send into one explicit durable retry."""
        if (
            not composer_submission
            or turn_id is None
            or not queue_workflow_input_for_provider(terminal_id, turn_id, raw_message, reason_code)
        ):
            return None
        inbox_service.wake_provider_execution_queue(get_plugin_registry(request))
        return {
            "success": True,
            "accepted": True,
            "duplicate": False,
            "turn_id": turn_id,
            "queued": True,
            "status": "queued_runtime_recovery",
            "reason_code": reason_code,
        }

    try:
        # The caller cannot select the logical turn. The public endpoint gets a
        # fresh one here; the internal route supplies an opaque, current binding.
        if turn_id is None:
            prepared = (
                workflow_service.prepare_external_input(
                    terminal_id,
                    raw_message,
                    request_id=request_id,
                )
                if request_id is not None
                else workflow_service.prepare_external_input(terminal_id, raw_message)
            )
            if prepared.get("accepted") is False:
                raise AdmissionDenied(prepared["reason_code"], {})
            turn_id = prepared["turn_id"]
            if prepared.get("duplicate"):
                response = {
                    "success": True,
                    "queued": bool(prepared["queued"]),
                    "status": (
                        "queued_runtime_recovery"
                        if prepared.get("queue_reason") == "runtime_recovery"
                        else (
                            "queued_provider_execution"
                            if prepared["queued"]
                            else "already_accepted"
                        )
                    ),
                    "reason_code": (
                        prepared.get("reason_code")
                        or (
                            "TERMINAL_RUNTIME_OPERATION_BUSY"
                            if prepared.get("queue_reason") == "runtime_recovery"
                            else "WORKFLOW_CONTINUATION_PENDING" if prepared["queued"] else None
                        )
                    ),
                }
                if composer_submission:
                    response.update({"accepted": True, "duplicate": True, "turn_id": turn_id})
                return response
            if prepared["queued"]:
                runtime_recovery = prepared.get("queue_reason") != "workflow_predecessor"
                inbox_service.wake_provider_execution_queue(get_plugin_registry(request))
                response = {
                    "success": True,
                    "queued": True,
                    "status": (
                        "queued_runtime_recovery"
                        if runtime_recovery
                        else "queued_provider_execution"
                    ),
                    "reason_code": (
                        prepared.get("reason_code")
                        or (
                            "TERMINAL_RUNTIME_OPERATION_BUSY"
                            if runtime_recovery
                            else "WORKFLOW_CONTINUATION_PENDING"
                        )
                    ),
                }
                if composer_submission:
                    response.update({"accepted": True, "duplicate": False, "turn_id": turn_id})
                return response
        message = workflow_service.admission_message(message, turn_id)
        success = terminal_service.send_input(
            terminal_id,
            message,
            registry=get_plugin_registry(request),
            sender_id=sender_id,
            orchestration_type=orchestration_type,
            logical_turn_id=turn_id,
        )
        if not success:
            retained = retain_composer_for_recovery("PROVIDER_TRANSPORT_RETRY_PENDING")
            if retained is not None:
                return retained
        response = {
            "success": success,
        }
        if composer_submission:
            response.update(
                {
                    "accepted": success,
                    "duplicate": False,
                    "turn_id": turn_id,
                    "queued": False,
                    "status": "provider_admitted" if success else "failed",
                    "reason_code": None,
                }
            )
        return response
    except AdmissionDenied as e:
        if turn_id is None or not queue_workflow_input_for_provider(
            terminal_id, turn_id, raw_message, e.reason_code
        ):
            raise _admission_http_exception(e)
        inbox_service.wake_provider_execution_queue(get_plugin_registry(request))
        response = {
            "success": True,
            "queued": True,
            "status": "queued_provider_execution",
            "reason_code": e.reason_code,
        }
        if composer_submission:
            response.update({"accepted": True, "duplicate": False, "turn_id": turn_id})
        return response
    except ValueError as e:
        retained = retain_composer_for_recovery("TERMINAL_RUNTIME_RECOVERY_PENDING")
        if retained is not None:
            return retained
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        retained = retain_composer_for_recovery("PROVIDER_TRANSPORT_RETRY_PENDING")
        if retained is not None:
            return retained
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to send input: {str(e)}",
        )


@app.post("/_internal/terminals/{terminal_id}/input", include_in_schema=False)
async def send_orchestrated_terminal_input(
    request: Request,
    terminal_id: TerminalId,
    message: str,
    binding: str,
    sender_id: str,
    orchestration_type: OrchestrationType,
) -> Dict:
    """Deliver CAO's direct assign/handoff transport through a fresh binding.

    This non-public endpoint is solely the MCP server's direct transport path.
    Its opaque binding is issued by CAO and resolves only while that durable
    turn remains current; it never accepts a caller-selected logical turn.
    """
    turn_id = resolve_workflow_input_binding(terminal_id, binding)
    if turn_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="input binding is stale")
    return await run_in_threadpool(
        _send_server_bound_input,
        request,
        terminal_id,
        message,
        turn_id,
        sender_id=sender_id,
        orchestration_type=orchestration_type,
    )


@app.post("/_internal/delegation-results/handoff-v1", include_in_schema=False)
async def submit_handoff_result_v1_endpoint(
    request: Request, body: HandoffResultV1SubmitRequest
) -> Dict:
    """Stage a strict V1 document from one trusted-agentctl bearer capability."""
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="missing_terminal_auth"
        )
    try:
        return submit_handoff_result_v1(token, body.logical_turn_id, body.document)
    except HandoffResultSubmissionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc


@app.post(
    "/_internal/terminals/{terminal_id}/codex-session-identity",
    include_in_schema=False,
)
async def bind_codex_session_identity_endpoint(
    terminal_id: TerminalId,
    request: Request,
    body: CodexSessionIdentityRequest,
) -> Dict[str, str]:
    """Bind the exact foreground Codex root before its first model request."""
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if (
        scheme.lower() != "bearer"
        or not token
        or not terminal_auth_token_matches(terminal_id, token)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_terminal_auth"
        )
    caller_generation = request.headers.get(RUNTIME_GENERATION_HEADER, "")
    caller_generation_is_current = hmac.compare_digest(caller_generation, ACTIVE_RUNTIME_GENERATION)
    # Codex can defer SessionStart until the first post-promotion prompt, so a
    # valid hash from an older immutable release is not by itself stale
    # terminal authority. It may request only the exact durable rebind below;
    # malformed/missing generations and every fresh identity remain fenced.
    if not caller_generation_is_current and not re.fullmatch(r"[0-9a-f]{64}", caller_generation):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="stale_runtime_generation")
    try:
        identity = await run_in_threadpool(
            partial(
                terminal_service.bind_provider_runtime_session_identity,
                terminal_id,
                resume_identity=body.session_id,
                transcript_path=body.transcript_path,
                working_directory=body.cwd,
                source=body.source,
                runtime_generation=body.runtime_generation,
                require_existing_binding=not caller_generation_is_current,
            )
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "identity_not_proven"
                if caller_generation_is_current
                else "stale_identity_rebind_not_proven"
            ),
        ) from exc
    return {"session_id": identity}


@app.post(
    "/_internal/terminals/{terminal_id}/codex-turn-complete",
    include_in_schema=False,
)
async def persist_codex_turn_complete_endpoint(
    terminal_id: TerminalId,
    request: Request,
    body: CodexTurnCompleteRequest,
) -> Dict[str, Any]:
    """Persist the bounded exact-session response before Codex settles a turn."""
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if (
        scheme.lower() != "bearer"
        or not token
        or not terminal_auth_token_matches(terminal_id, token)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_terminal_auth"
        )
    caller_generation = request.headers.get(RUNTIME_GENERATION_HEADER, "")
    if not hmac.compare_digest(caller_generation, ACTIVE_RUNTIME_GENERATION):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="stale_runtime_generation")
    try:
        completion_offset = await run_in_threadpool(
            partial(
                terminal_service.persist_provider_completed_response,
                terminal_id,
                provider_session_id=body.session_id,
                transcript_path=body.transcript_path,
                working_directory=body.cwd,
                runtime_generation=body.runtime_generation,
                response=body.last_assistant_message,
            )
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="completion_not_proven",
        ) from exc
    return {"session_id": body.session_id, "completion_offset": completion_offset}


@app.post(
    "/terminals/{terminal_id}/attachments/image",
    response_model=TerminalAttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_terminal_image_attachment(
    request: Request,
    terminal_id: TerminalId,
) -> TerminalAttachmentResponse:
    """Store one browser image in a generated, short-lived terminal runtime path."""
    if not get_terminal_metadata(terminal_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Terminal '{terminal_id}' not found"
        )

    normalized_mime = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if normalized_mime not in {"image/png", "image/jpeg", "image/webp"}:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only PNG, JPEG, and WebP images are supported",
        )

    content = bytearray()
    try:
        async for chunk in request.stream():
            if len(content) + len(chunk) > terminal_attachments.MAX_IMAGE_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail="Image attachment exceeds the 10 MiB limit",
                )
            content.extend(chunk)

        path = terminal_attachments.store_terminal_image(
            terminal_id, normalized_mime, bytes(content)
        )
        return TerminalAttachmentResponse(path=str(path))
    except HTTPException:
        raise
    except terminal_attachments.TerminalImageTooLarge as e:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(e))
    except terminal_attachments.UnsupportedTerminalImage as e:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.exception("Failed to store terminal image attachment for %s", terminal_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to store terminal image attachment: {str(e)}",
        )


@app.post(
    "/terminals/{terminal_id}/attachments/file",
    response_model=TerminalAttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_terminal_file_attachment(
    request: Request,
    terminal_id: TerminalId,
) -> TerminalAttachmentResponse:
    """Store one validated text or opaque ZIP file in a generated private runtime path."""
    if not get_terminal_metadata(terminal_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Terminal '{terminal_id}' not found"
        )
    try:
        filename = _decode_terminal_filename(request.headers.get("x-terminal-filename", ""))
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    is_archive = Path(filename).suffix.lower() == ".zip"
    max_bytes = (
        terminal_attachments.MAX_ARCHIVE_BYTES
        if is_archive
        else terminal_attachments.MAX_IMAGE_BYTES
    )
    size_error = (
        "Archive attachment exceeds the 25 MiB limit"
        if is_archive
        else "File attachment exceeds the 10 MiB limit"
    )
    content = bytearray()
    try:
        async for chunk in request.stream():
            if len(content) + len(chunk) > max_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail=size_error,
                )
            content.extend(chunk)
        path = terminal_attachments.store_terminal_file(terminal_id, filename, bytes(content))
        return TerminalAttachmentResponse(path=str(path))
    except HTTPException:
        raise
    except terminal_attachments.TerminalImageTooLarge as e:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(e))
    except terminal_attachments.UnsupportedTerminalFile as e:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.exception("Failed to store terminal file attachment for %s", terminal_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to store terminal file attachment: {str(e)}",
        )


@app.get("/terminals/{terminal_id}/output", response_model=TerminalOutputResponse)
async def get_terminal_output(
    terminal_id: TerminalId,
    mode: OutputMode = OutputMode.FULL,
    cursor: Optional[str] = Query(default=None, max_length=128),
) -> TerminalOutputResponse:
    try:
        if mode == OutputMode.FULL:
            chunk = await _run_operational_io(
                terminal_service.get_output_chunk, terminal_id, cursor
            )
            return TerminalOutputResponse(
                output=chunk.output,
                mode=mode,
                availability="available",
                cursor=chunk.cursor,
                has_older=chunk.has_older,
                range_start=chunk.start_offset,
                range_end=chunk.end_offset,
                snapshot_size=chunk.snapshot_size,
            )
        if cursor is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A cursor is only valid for full output",
            )
        output = await _run_operational_io(terminal_service.get_output, terminal_id, mode)
        return TerminalOutputResponse(output=output, mode=mode, availability="available")
    except TerminalOutputUnavailable as exc:
        return TerminalOutputResponse(
            output="",
            mode=mode,
            availability="unavailable",
            reason_code=exc.reason_code,
        )
    except TerminalOutputCursorError as exc:
        status_code = (
            status.HTTP_409_CONFLICT
            if exc.reason_code == "OUTPUT_CURSOR_STALE"
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get output: {str(e)}",
        )


@app.post("/terminals/{terminal_id}/exit")
async def exit_terminal(terminal_id: TerminalId) -> Dict:
    """Gracefully exit a provider without overstating command delivery."""
    try:
        result = await run_in_threadpool(terminal_service.exit_terminal, terminal_id)
        return {
            "success": result.success,
            "lifecycle": result.lifecycle,
            "outcome": result.outcome,
            "message": result.message,
            "command_delivered": result.command_delivered,
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ExitAuthorityError as e:
        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
                if e.inventory_uncertain
                else status.HTTP_409_CONFLICT
            ),
            detail={"reason_code": e.reason_code, "message": str(e)},
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to exit terminal: {str(e)}",
        )


@app.delete("/terminals/{terminal_id}")
async def delete_terminal(request: Request, terminal_id: TerminalId) -> Dict:
    """Delete one durably exited terminal under exact runtime authority."""
    try:
        success = await run_in_threadpool(
            terminal_service.delete_terminal,
            terminal_id,
            registry=get_plugin_registry(request),
        )
        return {"success": success}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except TerminalDeletionError as e:
        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
                if e.inventory_uncertain
                else status.HTTP_409_CONFLICT
            ),
            detail={"reason_code": e.reason_code, "message": str(e)},
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete terminal: {str(e)}",
        )


@app.post("/terminals/{receiver_id}/inbox/messages")
async def create_inbox_message_endpoint(
    request: Request,
    receiver_id: TerminalId,
    payload: InboxMessageRequest,
) -> Dict:
    """Create inbox message and attempt immediate delivery."""
    try:
        # This public API is intentionally transport-only.  A caller supplied
        # sender_id is not a capability to finalize a registered child result;
        # authoritative callbacks are persisted directly by MCP send_message
        # after its admitted durable effect is claimed.
        def persist_message():
            from cli_agent_orchestrator.services.operations_service import (
                workflow_execution_admission_fence,
            )

            with workflow_execution_admission_fence():
                return create_inbox_message(payload.sender_id, receiver_id, payload.message)

        inbox_msg = await _run_operational_io(persist_message)
    except AdmissionDenied as e:
        raise _admission_http_exception(e)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create inbox message: {str(e)}",
        )

    # Best-effort immediate delivery. If the receiver terminal is idle, the
    # message is delivered now; otherwise the watchdog will deliver it when
    # the terminal becomes idle. Delivery failures must not cause the API
    # to report an error — the message was already persisted above.
    try:
        await _run_operational_io(
            inbox_service.check_and_send_pending_messages,
            receiver_id,
            registry=get_plugin_registry(request),
        )
    except Exception as e:
        logger.warning(f"Immediate delivery attempt failed for {receiver_id}: {e}")

    return {
        "success": True,
        "duplicate": False,
        "message_id": inbox_msg.id,
        "sender_id": inbox_msg.sender_id,
        "receiver_id": inbox_msg.receiver_id,
        "created_at": inbox_msg.created_at.isoformat(),
    }


@app.get("/delegation-results/{result_id}")
async def get_delegation_result_endpoint(result_id: str) -> Dict:
    """Read one immutable local-operator delegation result artifact."""
    result = await _run_operational_io(result_service.read_result, result_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Delegation result not found"
        )
    return result


@app.get("/delegation-results")
async def list_delegation_results_endpoint(
    terminal_id: Optional[str] = None,
    session_name: Optional[str] = None,
    status_param: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: Optional[str] = None,
) -> List[Dict]:
    """List retained result history, including artifacts after terminal cleanup."""
    if status_param and status_param not in {"awaiting", "complete", "incomplete", "cancelled"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid result status"
        )
    return await _run_operational_io(
        result_service.list_results,
        terminal_id,
        session_name,
        status_param,
        limit,
        cursor,
    )


@app.get("/terminals/{terminal_id}/inbox/messages")
async def get_inbox_messages_endpoint(
    terminal_id: TerminalId,
    limit: int = Query(default=10, le=100, description="Maximum number of messages to retrieve"),
    status_param: Optional[str] = Query(
        default=None, alias="status", description="Filter by message status"
    ),
) -> List[Dict]:
    """Get inbox messages for a terminal.

    Args:
        terminal_id: Terminal ID to get messages for
        limit: Maximum number of messages to return (default: 10, max: 100)
        status_param: Optional filter by message status
            ('pending', 'delivered', 'failed', 'superseded')

    Returns:
        List of inbox messages with sender_id, message, created_at, status
    """
    try:
        # Convert status filter if provided
        status_filter = None
        if status_param:
            try:
                status_filter = MessageStatus(status_param)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Invalid status: {status_param}. Valid values: "
                        "pending, delivered, failed, superseded"
                    ),
                )

        # Get messages using existing database function
        messages = await _run_ui_read(
            get_inbox_messages, terminal_id, limit=limit, status=status_filter
        )

        # Convert to response format
        result = []
        for msg in messages:
            result.append(
                {
                    "id": msg.id,
                    "sender_id": msg.sender_id,
                    "receiver_id": msg.receiver_id,
                    "message": msg.message,
                    "status": msg.status.value,
                    "result_id": msg.result_id,
                    "kind": msg.kind,
                    "superseded_at": (msg.superseded_at.isoformat() if msg.superseded_at else None),
                    "callback_reconciled_at": (
                        msg.callback_reconciled_at.isoformat()
                        if msg.callback_reconciled_at
                        else None
                    ),
                    "callback_reconciled_from_turn_id": (msg.callback_reconciled_from_turn_id),
                    "created_at": msg.created_at.isoformat() if msg.created_at else None,
                }
            )

        return result

    except HTTPException:
        # Re-raise HTTPException (validation errors)
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve inbox messages: {str(e)}",
        )


@app.websocket("/terminals/{terminal_id}/ws")
async def terminal_ws(websocket: WebSocket, terminal_id: str):
    """WebSocket endpoint for live terminal streaming via tmux attach.

    Security: This endpoint provides full PTY access with no authentication.
    It is intended for localhost-only use. Do NOT expose the server to
    untrusted networks (e.g. --host 0.0.0.0) without adding authentication.
    """
    # Reject connections from non-loopback clients
    client_host = websocket.client.host if websocket.client else None
    if client_host not in (None, "127.0.0.1", "::1", "localhost"):
        await websocket.close(code=4003, reason="WebSocket access is restricted to localhost")
        return

    await websocket.accept()

    metadata = get_terminal_metadata(terminal_id)
    if not metadata:
        await websocket.close(code=4004, reason="Terminal not found")
        return
    workspace = get_writable_work_context_by_session(
        metadata.get("session_id") or f"legacy:{metadata['tmux_session']}"
    )
    if workspace is not None and workspace.get("state") in {"retiring", "retired"}:
        await websocket.close(code=4009, reason="Workspace retired")
        return

    session_name = metadata["tmux_session"]
    window_name = metadata["tmux_window"]

    # Create PTY pair for tmux attach
    master_fd, slave_fd = pty.openpty()

    # Set initial terminal size
    winsize = struct.pack("HHHH", 24, 80, 0, 0)
    fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

    # Start tmux attach inside the PTY
    proc = subprocess.Popen(
        ["tmux", "-u", "attach-session", "-t", f"{session_name}:{window_name}"],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
        preexec_fn=os.setsid,
    )
    os.close(slave_fd)

    # Make master_fd non-blocking for event-driven reads
    flag = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flag | os.O_NONBLOCK)

    loop = asyncio.get_event_loop()
    output_queue: asyncio.Queue[bytes] = asyncio.Queue()
    done = asyncio.Event()

    def _on_pty_data():
        """Callback when PTY has data available."""
        try:
            data = os.read(master_fd, 65536)
            if data:
                output_queue.put_nowait(data)
            else:
                done.set()
        except BlockingIOError:
            pass
        except OSError:
            done.set()

    loop.add_reader(master_fd, _on_pty_data)

    async def _forward_output():
        """Read from PTY queue and send to WebSocket."""
        while not done.is_set():
            try:
                data = await asyncio.wait_for(output_queue.get(), timeout=1.0)
                # Drain any additional pending data for batching
                while not output_queue.empty():
                    try:
                        data += output_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                await websocket.send_bytes(data)
            except asyncio.TimeoutError:
                if proc.poll() is not None:
                    break
            except (Exception, asyncio.CancelledError):
                break

    async def _forward_input():
        """Receive from WebSocket and write to PTY."""
        try:
            while not done.is_set():
                msg = await websocket.receive_text()
                payload = json.loads(msg)
                if payload.get("type") == "input":
                    raw = payload["data"].encode()
                    if len(raw) > MAX_TERMINAL_WS_INPUT_BYTES:
                        await websocket.close(code=1009, reason="Terminal input frame is too large")
                        break
                    operation_token = await _run_operational_io(
                        acquire_terminal_runtime_transport, terminal_id
                    )
                    if operation_token is None:
                        await websocket.close(
                            code=4011,
                            reason="Terminal runtime is temporarily owned by recovery or exit",
                        )
                        break
                    try:
                        # Hold the durable pane-operation claim across every
                        # chunk so reconnect/retirement cannot expose a shell
                        # halfway through an operator paste.
                        chunk_size = 1024
                        for i in range(0, len(raw), chunk_size):
                            os.write(master_fd, raw[i : i + chunk_size])
                            if i + chunk_size < len(raw):
                                await asyncio.sleep(0.01)
                    finally:
                        await _run_operational_io(
                            release_terminal_runtime_operation,
                            terminal_id,
                            operation_token,
                        )
                elif payload.get("type") == "resize":
                    rows = payload.get("rows", 24)
                    cols = payload.get("cols", 80)
                    winsize_data = struct.pack("HHHH", rows, cols, 0, 0)
                    fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize_data)
                    # Explicitly notify tmux of the size change —
                    # TIOCSWINSZ on the master doesn't always deliver
                    # SIGWINCH to the child process group.
                    try:
                        os.kill(proc.pid, signal.SIGWINCH)
                    except OSError:
                        pass
        except WebSocketDisconnect:
            pass
        except (Exception, asyncio.CancelledError):
            pass
        finally:
            done.set()

    try:
        await asyncio.gather(_forward_output(), _forward_input())
    except (Exception, asyncio.CancelledError):
        pass
    finally:
        done.set()
        try:
            loop.remove_reader(master_fd)
        except Exception:
            pass
        try:
            os.close(master_fd)
        except OSError:
            pass
        # Terminate tmux attach (just detaches, doesn't kill the session)
        proc.terminate()
        try:
            await asyncio.wait_for(asyncio.to_thread(proc.wait), timeout=3.0)
        except asyncio.TimeoutError:
            proc.kill()
            await asyncio.to_thread(proc.wait)


# ── Flow management endpoints ────────────────────────────────────────


def _create_flow(body: CreateFlowRequest) -> Flow:
    """Persist and register one flow without blocking the request event loop."""
    flows_dir = CAO_HOME_DIR / "flows"
    flows_dir.mkdir(parents=True, exist_ok=True)
    file_path = flows_dir / f"{body.name}.flow.md"
    _launch_directory, project_context = _resolve_launch_project(body.project_id, None)
    frontmatter_lines = [
        "---",
        f"name: {body.name}",
        f'schedule: "{body.schedule}"',
        f"agent_profile: {body.agent_profile}",
        f"provider: {body.provider}",
        *([f"project_id: {body.project_id}"] if body.project_id else []),
        "---",
    ]
    file_path.write_text(
        "\n".join(frontmatter_lines) + "\n" + body.prompt_template,
        encoding="utf-8",
    )
    return flow_service.add_flow(str(file_path), project_context=project_context)


@app.get("/flows", response_model=List[Flow])
async def list_flows() -> List[Flow]:
    """List all flows."""
    try:
        return await _run_operational_io(flow_service.list_flows)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list flows: {str(e)}",
        )


@app.get("/flows/{name}", response_model=Flow)
async def get_flow(name: str) -> Flow:
    """Get a specific flow by name."""
    try:
        return await _run_operational_io(flow_service.get_flow, name)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get flow: {str(e)}",
        )


@app.post("/flows", response_model=Flow, status_code=status.HTTP_201_CREATED)
async def create_flow(body: CreateFlowRequest) -> Flow:
    """Create a new flow.

    Writes a .flow.md file with YAML frontmatter and prompt body, then
    registers it via flow_service.add_flow().
    """
    try:
        return await _run_operational_io(_create_flow, body)
    except ProjectResolutionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create flow: {str(e)}",
        )


@app.delete("/flows/{name}")
async def remove_flow(name: str) -> Dict:
    """Remove a flow."""
    try:
        await _run_operational_io(flow_service.remove_flow, name)
        return {"success": True}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to remove flow: {str(e)}",
        )


@app.post("/flows/{name}/enable")
async def enable_flow(name: str) -> Dict:
    """Enable a flow."""
    try:
        await _run_operational_io(flow_service.enable_flow, name)
        return {"success": True}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to enable flow: {str(e)}",
        )


@app.post("/flows/{name}/disable")
async def disable_flow(name: str) -> Dict:
    """Disable a flow."""
    try:
        await _run_operational_io(flow_service.disable_flow, name)
        return {"success": True}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to disable flow: {str(e)}",
        )


@app.post("/flows/{name}/run")
async def run_flow(name: str) -> Dict:
    """Manually execute a flow."""
    try:
        executed = await _run_operational_io(flow_service.execute_flow, name)
        return {"executed": executed}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to execute flow: {str(e)}",
        )


# Static file serving for built web UI.
# Anchored to the package via importlib.resources so it works for both
# editable installs (uv sync) and wheel installs (uv tool install, pip install).
from importlib.resources import files as _pkg_files

WEB_DIST = Path(str(_pkg_files("cli_agent_orchestrator") / "web_ui"))


@app.get("/docs")
@app.get("/docs/{path:path}")
async def docs_spa_entry(path: str = "") -> FileResponse:
    """Serve the ordinary SPA shell for allowlisted documentation routes.

    The client only reads ``docs-bundle.json`` generated at build time; this
    route intentionally accepts no filesystem path and never serves Markdown.
    """
    index = WEB_DIST / "index.html"
    if not index.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Web UI is not installed")
    return FileResponse(index)


@app.get("/settings")
@app.get("/settings/{path:path}")
async def settings_spa_entry(path: str = "") -> FileResponse:
    """Serve the SPA shell for direct Settings routes and browser refreshes."""
    index = WEB_DIST / "index.html"
    if not index.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Web UI is not installed")
    return FileResponse(index)


if (WEB_DIST / "index.html").exists():
    from starlette.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")


def main():
    """Entry point for cao-server command."""
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="ThreadCells server")
    parser.add_argument(
        "--agents-dir",
        type=str,
        default=None,
        help="Path to agents directory (overrides CAO_AGENTS_DIR env var)",
    )
    parser.add_argument("--host", type=str, default=None, help="Server host")
    parser.add_argument("--port", type=int, default=None, help="Server port")
    args = parser.parse_args()

    if args.agents_dir:
        os.environ["CAO_AGENTS_DIR"] = args.agents_dir
        import cli_agent_orchestrator.constants as constants

        constants.KIRO_AGENTS_DIR = Path(args.agents_dir)
        logger.info(f"Using agents directory: {args.agents_dir}")

    host = args.host or SERVER_HOST
    port = args.port or SERVER_PORT
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
