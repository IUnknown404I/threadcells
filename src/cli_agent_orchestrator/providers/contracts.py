"""Versioned public contracts for trusted ThreadCells provider adapters.

Adapter code is installed and trusted separately.  Provider configuration is
declarative data validated by that adapter's Pydantic model; it can never select
an executable or inject shell arguments.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Dict, Mapping, Optional, Type

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from cli_agent_orchestrator.providers.base import BaseProvider

if TYPE_CHECKING:
    from cli_agent_orchestrator.models.agent_profile import AgentProfile

PROVIDER_ADAPTER_API_VERSION = "1.0"
PROVIDER_ADAPTER_ENTRYPOINT_GROUP = "threadcells.provider_adapters.v1"
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_SECRET_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}$")
_FORBIDDEN_CONFIG_KEYS = frozenset(
    {
        "apikey",
        "args",
        "arguments",
        "argv",
        "bearertoken",
        "bin",
        "binary",
        "binarypath",
        "command",
        "commandline",
        "credential",
        "credentials",
        "env",
        "environment",
        "executable",
        "executablepath",
        "flags",
        "password",
        "program",
        "programpath",
        "secret",
        "shell",
        "shellargs",
        "token",
    }
)


class CapabilitySupport(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    CONDITIONAL = "conditional"


class ProviderState(str, Enum):
    CONNECTED = "connected"
    NOT_CONFIGURED = "not_configured"
    DISABLED = "disabled"
    ERROR = "error"
    INCOMPATIBLE = "incompatible"


class AuthenticationState(str, Enum):
    AUTHENTICATED = "authenticated"
    NOT_AUTHENTICATED = "not_authenticated"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class ProviderAvailability(str, Enum):
    """Operator-facing runtime classification derived only from preflight."""

    INSTALLED_AND_READY = "INSTALLED_AND_READY"
    INSTALLED_NOT_AUTHENTICATED = "INSTALLED_NOT_AUTHENTICATED"
    INSTALLED_BUT_UNHEALTHY = "INSTALLED_BUT_UNHEALTHY"
    NOT_INSTALLED = "NOT_INSTALLED"
    UNKNOWN = "UNKNOWN"


class AdapterCapabilities(BaseModel):
    """Normalized lifecycle and telemetry capabilities."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start_turn: CapabilitySupport = CapabilitySupport.SUPPORTED
    resume_turn: CapabilitySupport = CapabilitySupport.CONDITIONAL
    cancel: CapabilitySupport = CapabilitySupport.SUPPORTED
    structured_completion: CapabilitySupport = CapabilitySupport.CONDITIONAL
    usage: CapabilitySupport = CapabilitySupport.UNSUPPORTED
    input_tokens: CapabilitySupport = CapabilitySupport.UNSUPPORTED
    output_tokens: CapabilitySupport = CapabilitySupport.UNSUPPORTED
    cached_input_tokens: CapabilitySupport = CapabilitySupport.UNSUPPORTED
    session_persistence: CapabilitySupport = CapabilitySupport.CONDITIONAL
    model_selection: CapabilitySupport = CapabilitySupport.CONDITIONAL
    model_discovery: CapabilitySupport = CapabilitySupport.UNSUPPORTED
    reasoning_controls: CapabilitySupport = CapabilitySupport.UNSUPPORTED
    health: CapabilitySupport = CapabilitySupport.SUPPORTED


class AdapterManifest(BaseModel):
    """Installed trusted-code identity and public compatibility contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    adapter_id: str
    display_name: str = Field(min_length=1, max_length=120)
    plugin_api_version: str = PROVIDER_ADAPTER_API_VERSION
    implementation_version: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=500)
    capabilities: AdapterCapabilities
    configuration_schema: Dict[str, Any]

    @field_validator("adapter_id")
    @classmethod
    def validate_adapter_id(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("must be a lowercase dotted/dashed identifier")
        return value


class AdapterSettings(BaseModel):
    """Base for adapter-owned declarative settings models."""

    model_config = ConfigDict(extra="forbid")


class ProviderConfiguration(BaseModel):
    """Versioned, export-safe configuration for an installed adapter."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1, le=1)
    config_id: str
    adapter_id: str
    display_name: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    settings: Dict[str, Any] = Field(default_factory=dict)
    secret_refs: Dict[str, str] = Field(default_factory=dict)

    @field_validator("config_id", "adapter_id")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if not _IDENTIFIER.fullmatch(value):
            raise ValueError("must be a lowercase dotted/dashed identifier")
        return value

    @field_validator("secret_refs")
    @classmethod
    def validate_secret_references(cls, value: Dict[str, str]) -> Dict[str, str]:
        for name, reference in value.items():
            if not _IDENTIFIER.fullmatch(name) or not _SECRET_REFERENCE.fullmatch(reference):
                raise ValueError("secret_refs must contain semantic names and opaque references")
        return value


class ProviderPreflight(BaseModel):
    """Truthful, secret-free adapter readiness result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: ProviderState
    installed: bool
    authentication: AuthenticationState = AuthenticationState.UNKNOWN
    version: Optional[str] = None
    compatible: bool = True
    models: tuple[str, ...] = ()
    reason_code: Optional[str] = None
    message: str


def classify_provider_preflight(preflight: ProviderPreflight) -> ProviderAvailability:
    """Classify CLI readiness without conflating adapter registration with installation."""
    if not preflight.installed:
        return ProviderAvailability.NOT_INSTALLED
    if not preflight.compatible or preflight.state in {
        ProviderState.ERROR,
        ProviderState.INCOMPATIBLE,
    }:
        return ProviderAvailability.INSTALLED_BUT_UNHEALTHY
    if preflight.authentication == AuthenticationState.NOT_AUTHENTICATED:
        return ProviderAvailability.INSTALLED_NOT_AUTHENTICATED
    if preflight.state == ProviderState.CONNECTED and preflight.authentication in {
        AuthenticationState.AUTHENTICATED,
        AuthenticationState.NOT_APPLICABLE,
    }:
        return ProviderAvailability.INSTALLED_AND_READY
    return ProviderAvailability.UNKNOWN


def provider_preflight_is_launchable(preflight: ProviderPreflight) -> bool:
    """Disable only runtimes proven unavailable by the canonical preflight."""
    availability = classify_provider_preflight(preflight)
    if availability == ProviderAvailability.INSTALLED_AND_READY:
        return True
    return (
        availability == ProviderAvailability.UNKNOWN
        and preflight.installed
        and preflight.compatible
        and preflight.state == ProviderState.NOT_CONFIGURED
        and preflight.authentication == AuthenticationState.UNKNOWN
    )


class ProviderConfigurationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pointer: str
    code: str
    message: str


class ProviderConfigurationError(ValueError):
    """Field-level validation failure that never includes supplied values."""

    def __init__(self, issues: tuple[ProviderConfigurationIssue, ...]):
        self.issues = issues
        super().__init__("provider configuration is invalid")


class ProviderAdapterError(RuntimeError):
    """Normalized runtime/compatibility error safe for public surfaces."""

    def __init__(self, reason_code: str, message: str):
        self.reason_code = reason_code
        super().__init__(message)


@dataclass(frozen=True)
class ProviderLaunchContext:
    terminal_id: str
    tmux_session: str
    tmux_window: str
    agent_profile: Optional[str] = None
    allowed_tools: Optional[list[str]] = None
    skill_prompt: Optional[str] = None
    model: Optional[str] = None
    resolved_profile: Optional["AgentProfile"] = None
    structured_owner_authorized: bool = False


ProviderFactory = Callable[[ProviderLaunchContext, AdapterSettings], BaseProvider]
ProviderPreflightRunner = Callable[[ProviderConfiguration, AdapterSettings], ProviderPreflight]


@dataclass(frozen=True)
class ProviderAdapterDefinition:
    """Trusted installed adapter implementation registered with ThreadCells."""

    manifest: AdapterManifest
    settings_model: Type[AdapterSettings]
    factory: ProviderFactory
    preflight: ProviderPreflightRunner


@dataclass(frozen=True)
class ResolvedProviderConfiguration:
    artifact: ProviderConfiguration
    settings: AdapterSettings


def _normalized_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _find_forbidden_config_key(value: Any, pointer: str = "/settings") -> Optional[str]:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            child_pointer = f"{pointer}/{key.replace('~', '~0').replace('/', '~1')}"
            if _normalized_key(key) in _FORBIDDEN_CONFIG_KEYS:
                return child_pointer
            found = _find_forbidden_config_key(child, child_pointer)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            found = _find_forbidden_config_key(child, f"{pointer}/{index}")
            if found:
                return found
    return None


def forbidden_configuration_pointer(value: Any, pointer: str = "/settings") -> Optional[str]:
    """Return the first forbidden executable/secret config key, if any."""

    return _find_forbidden_config_key(value, pointer)


def _validation_issues(
    error: ValidationError, prefix: str = ""
) -> tuple[ProviderConfigurationIssue, ...]:
    issues = []
    for detail in error.errors(include_url=False, include_input=False):
        location = "/".join(
            str(part).replace("~", "~0").replace("/", "~1") for part in detail["loc"]
        )
        pointer = f"{prefix}/{location}" if location else (prefix or "/")
        issues.append(
            ProviderConfigurationIssue(
                pointer=pointer,
                code=str(detail["type"]),
                message=str(detail["msg"]),
            )
        )
    return tuple(issues)


def validate_provider_configuration(
    raw: Mapping[str, Any], definition: ProviderAdapterDefinition
) -> ResolvedProviderConfiguration:
    """Validate declarative data without ever interpreting it as execution."""

    try:
        artifact = ProviderConfiguration.model_validate(raw)
    except ValidationError as error:
        raise ProviderConfigurationError(_validation_issues(error)) from None
    if artifact.adapter_id != definition.manifest.adapter_id:
        raise ProviderConfigurationError(
            (
                ProviderConfigurationIssue(
                    pointer="/adapter_id",
                    code="adapter_mismatch",
                    message="does not match the selected installed adapter",
                ),
            )
        )
    forbidden = forbidden_configuration_pointer(artifact.settings)
    if forbidden:
        raise ProviderConfigurationError(
            (
                ProviderConfigurationIssue(
                    pointer=forbidden,
                    code="executable_or_secret_config_forbidden",
                    message=(
                        "executable, shell, environment, argument, and secret values are not "
                        "provider configuration; install trusted adapter code and use secret_refs"
                    ),
                ),
            )
        )
    try:
        settings = definition.settings_model.model_validate(artifact.settings)
    except ValidationError as error:
        raise ProviderConfigurationError(_validation_issues(error, "/settings")) from None
    return ResolvedProviderConfiguration(artifact=artifact, settings=settings)


def default_provider_configuration(adapter_id: str, display_name: str) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "config_id": f"builtin-{adapter_id}",
        "adapter_id": adapter_id,
        "display_name": display_name,
        "enabled": True,
        "settings": {},
        "secret_refs": {},
    }
