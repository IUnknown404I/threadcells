"""A complete minimal adapter for a trusted, separately installed CLI."""

from __future__ import annotations

import shlex
import shutil
from typing import Literal, Optional

from cli_agent_orchestrator.clients.tmux import tmux_client
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.base import BaseProvider
from cli_agent_orchestrator.providers.contracts import (
    AdapterCapabilities,
    AdapterManifest,
    AdapterSettings,
    AuthenticationState,
    CapabilitySupport,
    ProviderAdapterDefinition,
    ProviderConfiguration,
    ProviderLaunchContext,
    ProviderPreflight,
    ProviderState,
)


class EchoSettings(AdapterSettings):
    model: Literal["echo-v1"] = "echo-v1"


class EchoProvider(BaseProvider):
    def __init__(self, context: ProviderLaunchContext, settings: EchoSettings):
        super().__init__(
            context.terminal_id,
            context.tmux_session,
            context.tmux_window,
            context.allowed_tools,
            context.skill_prompt,
        )
        self.settings = settings

    def initialize(self) -> bool:
        # The executable is selected by trusted adapter code. Configuration can
        # only select the validated enum above and cannot inject shell text.
        command = shlex.join(["threadcells-echo-agent", "--model", self.settings.model])
        tmux_client.send_keys(self.session_name, self.window_name, command)
        return True

    def get_status(self, tail_lines: Optional[int] = None) -> TerminalStatus:
        output = tmux_client.get_history(self.session_name, self.window_name) or ""
        if "THREADCELLS_ECHO_COMPLETE" in output:
            return TerminalStatus.COMPLETED
        if "THREADCELLS_ECHO_READY" in output:
            return TerminalStatus.IDLE
        return TerminalStatus.PROCESSING

    def get_idle_pattern_for_log(self) -> str:
        return "THREADCELLS_ECHO_READY"

    def extract_last_message_from_script(self, script_output: str) -> str:
        marker = "THREADCELLS_ECHO_RESULT:"
        matches = [
            line.partition(marker)[2].strip()
            for line in script_output.splitlines()
            if marker in line
        ]
        return matches[-1] if matches else ""

    def exit_cli(self) -> str:
        return "/exit"

    def cleanup(self) -> None:
        return None


def _factory(context: ProviderLaunchContext, settings: AdapterSettings) -> BaseProvider:
    if not isinstance(settings, EchoSettings):
        raise TypeError("EchoSettings required")
    return EchoProvider(context, settings)


def _preflight(
    _configuration: ProviderConfiguration, _settings: AdapterSettings
) -> ProviderPreflight:
    installed = shutil.which("threadcells-echo-agent") is not None
    return ProviderPreflight(
        state=ProviderState.CONNECTED if installed else ProviderState.NOT_CONFIGURED,
        installed=installed,
        authentication=AuthenticationState.NOT_APPLICABLE,
        compatible=installed,
        reason_code=None if installed else "EXECUTABLE_NOT_FOUND",
        message=(
            "Community Echo is ready"
            if installed
            else "Install the trusted threadcells-echo-agent executable"
        ),
    )


def provider_adapter() -> ProviderAdapterDefinition:
    capabilities = AdapterCapabilities(
        structured_completion=CapabilitySupport.SUPPORTED,
        session_persistence=CapabilitySupport.SUPPORTED,
        model_selection=CapabilitySupport.SUPPORTED,
    )
    return ProviderAdapterDefinition(
        manifest=AdapterManifest(
            adapter_id="community.echo",
            display_name="Community Echo",
            implementation_version="0.1.0",
            description="Deterministic example adapter for a trusted installed CLI.",
            capabilities=capabilities,
            configuration_schema=EchoSettings.model_json_schema(),
        ),
        settings_model=EchoSettings,
        factory=_factory,
        preflight=_preflight,
    )
