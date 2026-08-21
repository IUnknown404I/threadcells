"""Built-in provider adapter definitions behind the public V1 contract."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from importlib import metadata
from typing import Callable, Optional

from pydantic import ConfigDict

from cli_agent_orchestrator.providers.base import BaseProvider
from cli_agent_orchestrator.providers.contracts import (
    AdapterCapabilities,
    AdapterManifest,
    AdapterSettings,
    AuthenticationState,
    CapabilitySupport,
    ProviderAdapterDefinition,
    ProviderAdapterError,
    ProviderConfiguration,
    ProviderFactory,
    ProviderLaunchContext,
    ProviderPreflight,
    ProviderState,
)

try:
    IMPLEMENTATION_VERSION = metadata.version("threadcells")
except metadata.PackageNotFoundError:  # source-tree execution before installation
    IMPLEMENTATION_VERSION = "0+source"


class NoAdapterSettings(AdapterSettings):
    """Built-ins currently use profile/provider-native configuration only."""

    model_config = ConfigDict(extra="forbid")


def _manifest(
    adapter_id: str,
    display_name: str,
    description: str,
    capabilities: AdapterCapabilities,
) -> AdapterManifest:
    return AdapterManifest(
        adapter_id=adapter_id,
        display_name=display_name,
        implementation_version=IMPLEMENTATION_VERSION,
        description=description,
        capabilities=capabilities,
        configuration_schema=NoAdapterSettings.model_json_schema(),
    )


def _safe_version(binary: str) -> Optional[str]:
    try:
        completed = subprocess.run(
            [binary, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode:
        return None
    first_line = (completed.stdout or completed.stderr).splitlines()
    if not first_line:
        return None
    # Versions are public metadata, but discard unexpected terminal/control
    # output rather than reflecting arbitrary CLI text into the API.
    clean = re.sub(r"[^A-Za-z0-9 ._+()/:-]", "", first_line[0]).strip()
    return clean[:120] or None


def _auth_command(binary: str, adapter_id: str) -> AuthenticationState:
    commands = {
        "codex": [binary, "login", "status"],
        "claude_code": [binary, "auth", "status", "--json"],
    }
    command = commands.get(adapter_id)
    if command is None:
        return AuthenticationState.UNKNOWN
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return AuthenticationState.UNKNOWN
    if completed.returncode:
        return AuthenticationState.NOT_AUTHENTICATED
    if adapter_id == "claude_code":
        try:
            status = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            return AuthenticationState.UNKNOWN
        return (
            AuthenticationState.AUTHENTICATED
            if status.get("loggedIn") is True
            else AuthenticationState.NOT_AUTHENTICATED
        )
    return AuthenticationState.AUTHENTICATED


def _cli_preflight(
    binary_name: str, adapter_id: str
) -> Callable[[ProviderConfiguration, AdapterSettings], ProviderPreflight]:
    def run(_artifact: ProviderConfiguration, _settings: AdapterSettings) -> ProviderPreflight:
        binary = shutil.which(binary_name)
        if binary is None:
            return ProviderPreflight(
                state=ProviderState.NOT_CONFIGURED,
                installed=False,
                authentication=AuthenticationState.UNKNOWN,
                compatible=False,
                reason_code="EXECUTABLE_NOT_FOUND",
                message=f"Install {binary_name} and configure its authentication",
            )
        version = _safe_version(binary)
        authentication = _auth_command(binary, adapter_id)
        state = (
            ProviderState.CONNECTED
            if authentication
            in {AuthenticationState.AUTHENTICATED, AuthenticationState.NOT_APPLICABLE}
            else ProviderState.NOT_CONFIGURED
        )
        reason = None
        if authentication == AuthenticationState.NOT_AUTHENTICATED:
            reason = "AUTHENTICATION_REQUIRED"
        elif authentication == AuthenticationState.UNKNOWN:
            reason = "AUTHENTICATION_UNVERIFIED"
        return ProviderPreflight(
            state=state,
            installed=True,
            authentication=authentication,
            version=version,
            compatible=True,
            reason_code=reason,
            message=(
                "Adapter runtime is available"
                if state == ProviderState.CONNECTED
                else f"Verify {binary_name} authentication using its normal operator workflow"
            ),
        )

    return run


def _require_profile(context: ProviderLaunchContext, adapter_id: str) -> str:
    if not context.agent_profile:
        raise ProviderAdapterError("PROFILE_REQUIRED", f"{adapter_id} requires an agent profile")
    return context.agent_profile


def _factory_q(context: ProviderLaunchContext, _settings: AdapterSettings) -> BaseProvider:
    from cli_agent_orchestrator.providers.q_cli import QCliProvider

    return QCliProvider(
        context.terminal_id,
        context.tmux_session,
        context.tmux_window,
        _require_profile(context, "q_cli"),
        context.allowed_tools,
    )


def _factory_kiro(context: ProviderLaunchContext, _settings: AdapterSettings) -> BaseProvider:
    from cli_agent_orchestrator.providers.kiro_cli import KiroCliProvider

    return KiroCliProvider(
        context.terminal_id,
        context.tmux_session,
        context.tmux_window,
        _require_profile(context, "kiro_cli"),
        context.allowed_tools,
    )


def _factory_claude(context: ProviderLaunchContext, _settings: AdapterSettings) -> BaseProvider:
    from cli_agent_orchestrator.providers.claude_code import ClaudeCodeProvider

    return ClaudeCodeProvider(
        context.terminal_id,
        context.tmux_session,
        context.tmux_window,
        context.agent_profile,
        context.allowed_tools,
        skill_prompt=context.skill_prompt,
        resolved_profile=context.resolved_profile,
        structured_owner_authorized=context.structured_owner_authorized,
    )


def _factory_codex(context: ProviderLaunchContext, _settings: AdapterSettings) -> BaseProvider:
    from cli_agent_orchestrator.providers.codex import CodexProvider

    return CodexProvider(
        context.terminal_id,
        context.tmux_session,
        context.tmux_window,
        context.agent_profile,
        context.allowed_tools,
        skill_prompt=context.skill_prompt,
        resolved_profile=context.resolved_profile,
        structured_owner_authorized=context.structured_owner_authorized,
    )


def _factory_copilot(context: ProviderLaunchContext, _settings: AdapterSettings) -> BaseProvider:
    from cli_agent_orchestrator.providers.copilot_cli import CopilotCliProvider

    return CopilotCliProvider(
        context.terminal_id,
        context.tmux_session,
        context.tmux_window,
        context.agent_profile,
        context.allowed_tools,
        model=context.model,
    )


def _factory_gemini(context: ProviderLaunchContext, _settings: AdapterSettings) -> BaseProvider:
    from cli_agent_orchestrator.providers.gemini_cli import GeminiCliProvider

    return GeminiCliProvider(
        context.terminal_id,
        context.tmux_session,
        context.tmux_window,
        context.agent_profile,
        context.allowed_tools,
        skill_prompt=context.skill_prompt,
    )


def _factory_kimi(context: ProviderLaunchContext, _settings: AdapterSettings) -> BaseProvider:
    from cli_agent_orchestrator.providers.kimi_cli import KimiCliProvider

    return KimiCliProvider(
        context.terminal_id,
        context.tmux_session,
        context.tmux_window,
        context.agent_profile,
        context.allowed_tools,
        skill_prompt=context.skill_prompt,
    )


def _factory_opencode(context: ProviderLaunchContext, _settings: AdapterSettings) -> BaseProvider:
    from cli_agent_orchestrator.providers.opencode_cli import OpenCodeCliProvider

    return OpenCodeCliProvider(
        context.terminal_id,
        context.tmux_session,
        context.tmux_window,
        context.agent_profile,
        context.allowed_tools,
        model=context.model,
    )


def _definition(
    adapter_id: str,
    display_name: str,
    binary: str,
    description: str,
    factory: ProviderFactory,
    capabilities: AdapterCapabilities,
) -> ProviderAdapterDefinition:
    return ProviderAdapterDefinition(
        manifest=_manifest(adapter_id, display_name, description, capabilities),
        settings_model=NoAdapterSettings,
        factory=factory,
        preflight=_cli_preflight(binary, adapter_id),
    )


def builtin_adapter_definitions() -> tuple[ProviderAdapterDefinition, ...]:
    common = AdapterCapabilities()
    codex = AdapterCapabilities(
        usage=CapabilitySupport.SUPPORTED,
        input_tokens=CapabilitySupport.SUPPORTED,
        output_tokens=CapabilitySupport.SUPPORTED,
        cached_input_tokens=CapabilitySupport.SUPPORTED,
        session_persistence=CapabilitySupport.SUPPORTED,
        model_selection=CapabilitySupport.SUPPORTED,
        reasoning_controls=CapabilitySupport.SUPPORTED,
    )
    claude = AdapterCapabilities(
        structured_completion=CapabilitySupport.CONDITIONAL,
        usage=CapabilitySupport.CONDITIONAL,
        input_tokens=CapabilitySupport.CONDITIONAL,
        output_tokens=CapabilitySupport.CONDITIONAL,
        session_persistence=CapabilitySupport.CONDITIONAL,
        model_selection=CapabilitySupport.SUPPORTED,
    )
    return (
        _definition(
            "q_cli", "Amazon Q Developer", "q", "Amazon Q CLI adapter.", _factory_q, common
        ),
        _definition("kiro_cli", "Kiro CLI", "kiro-cli", "Kiro CLI adapter.", _factory_kiro, common),
        _definition(
            "claude_code",
            "Claude Code",
            "claude",
            "Claude Code CLI adapter.",
            _factory_claude,
            claude,
        ),
        _definition(
            "codex", "Codex", "codex", "Reference Codex CLI adapter.", _factory_codex, codex
        ),
        _definition(
            "kimi_cli", "Kimi CLI", "kimi", "Kimi coding-agent CLI adapter.", _factory_kimi, common
        ),
        _definition(
            "gemini_cli", "Gemini CLI", "gemini", "Gemini CLI adapter.", _factory_gemini, common
        ),
        _definition(
            "copilot_cli",
            "GitHub Copilot CLI",
            "copilot",
            "GitHub Copilot CLI adapter.",
            _factory_copilot,
            common,
        ),
        _definition(
            "opencode_cli",
            "OpenCode CLI",
            "opencode",
            "OpenCode CLI adapter.",
            _factory_opencode,
            common,
        ),
    )
