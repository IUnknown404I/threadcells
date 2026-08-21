"""Public ThreadCells Provider Adapter API V1."""

from cli_agent_orchestrator.providers.base import BaseProvider
from cli_agent_orchestrator.providers.contracts import (
    PROVIDER_ADAPTER_API_VERSION,
    PROVIDER_ADAPTER_ENTRYPOINT_GROUP,
    AdapterCapabilities,
    AdapterManifest,
    AdapterSettings,
    AuthenticationState,
    CapabilitySupport,
    ProviderAdapterDefinition,
    ProviderAdapterError,
    ProviderConfiguration,
    ProviderConfigurationError,
    ProviderLaunchContext,
    ProviderPreflight,
    ProviderState,
)

__all__ = [
    "PROVIDER_ADAPTER_API_VERSION",
    "PROVIDER_ADAPTER_ENTRYPOINT_GROUP",
    "AdapterCapabilities",
    "AdapterManifest",
    "AdapterSettings",
    "AuthenticationState",
    "BaseProvider",
    "CapabilitySupport",
    "ProviderAdapterDefinition",
    "ProviderAdapterError",
    "ProviderConfiguration",
    "ProviderConfigurationError",
    "ProviderLaunchContext",
    "ProviderPreflight",
    "ProviderState",
]
