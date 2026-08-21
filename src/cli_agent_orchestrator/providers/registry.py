"""Trusted-code discovery and runtime registry for provider adapters."""

from __future__ import annotations

from importlib import metadata
from threading import RLock
from typing import Any, Iterable, Mapping, Optional

from cli_agent_orchestrator.providers.base import BaseProvider
from cli_agent_orchestrator.providers.contracts import (
    PROVIDER_ADAPTER_API_VERSION,
    PROVIDER_ADAPTER_ENTRYPOINT_GROUP,
    AdapterManifest,
    ProviderAdapterDefinition,
    ProviderAdapterError,
    ProviderConfigurationError,
    ProviderConfigurationIssue,
    ProviderLaunchContext,
    ProviderPreflight,
    ResolvedProviderConfiguration,
    default_provider_configuration,
    forbidden_configuration_pointer,
    validate_provider_configuration,
)


class ProviderAdapterRegistry:
    """Process-local registry of explicitly installed trusted adapters."""

    def __init__(self) -> None:
        self._definitions: dict[str, ProviderAdapterDefinition] = {}
        self._sources: dict[str, str] = {}
        self._load_failures: list[dict[str, str]] = []
        self._lock = RLock()

    def register(self, definition: ProviderAdapterDefinition, *, source: str) -> None:
        manifest = definition.manifest
        if manifest.plugin_api_version != PROVIDER_ADAPTER_API_VERSION:
            raise ProviderAdapterError(
                "ADAPTER_API_INCOMPATIBLE",
                f"Adapter {manifest.adapter_id} targets unsupported provider API version",
            )
        forbidden = forbidden_configuration_pointer(
            manifest.configuration_schema, "/configuration_schema"
        )
        if forbidden:
            raise ProviderAdapterError(
                "ADAPTER_MANIFEST_UNSAFE",
                f"Adapter {manifest.adapter_id} declares forbidden executable/secret configuration",
            )
        generated_schema = definition.settings_model.model_json_schema()
        if manifest.configuration_schema != generated_schema:
            raise ProviderAdapterError(
                "ADAPTER_SCHEMA_MISMATCH",
                f"Adapter {manifest.adapter_id} manifest does not match its validation model",
            )
        with self._lock:
            if manifest.adapter_id in self._definitions:
                raise ProviderAdapterError(
                    "ADAPTER_ID_CONFLICT",
                    f"Provider adapter ID is already registered: {manifest.adapter_id}",
                )
            self._definitions[manifest.adapter_id] = definition
            self._sources[manifest.adapter_id] = source

    def discover_installed(self) -> None:
        """Load only package entry points explicitly installed by the operator."""

        try:
            entry_points: Iterable[Any] = metadata.entry_points(
                group=PROVIDER_ADAPTER_ENTRYPOINT_GROUP
            )
        except TypeError:  # Python 3.10 importlib.metadata compatibility
            entry_points = metadata.entry_points().select(group=PROVIDER_ADAPTER_ENTRYPOINT_GROUP)
        for entry_point in sorted(entry_points, key=lambda item: (item.name, item.value)):
            try:
                loaded = entry_point.load()
                definition = (
                    loaded()
                    if callable(loaded) and not isinstance(loaded, ProviderAdapterDefinition)
                    else loaded
                )
                if not isinstance(definition, ProviderAdapterDefinition):
                    raise TypeError("entry point did not return ProviderAdapterDefinition")
                self.register(definition, source=f"package:{entry_point.name}")
            except Exception as error:  # fail one plugin closed without hiding built-ins
                self._load_failures.append(
                    {
                        "entry_point": str(entry_point.name),
                        "reason_code": "ADAPTER_LOAD_FAILED",
                        "error_type": type(error).__name__,
                    }
                )

    def get(self, adapter_id: str) -> ProviderAdapterDefinition:
        with self._lock:
            definition = self._definitions.get(adapter_id)
        if definition is None:
            raise ProviderAdapterError(
                "ADAPTER_NOT_INSTALLED", f"Provider adapter is not installed: {adapter_id}"
            )
        return definition

    def manifests(self) -> tuple[AdapterManifest, ...]:
        with self._lock:
            return tuple(self._definitions[key].manifest for key in sorted(self._definitions))

    def source(self, adapter_id: str) -> str:
        self.get(adapter_id)
        return self._sources[adapter_id]

    @property
    def load_failures(self) -> tuple[dict[str, str], ...]:
        return tuple(dict(item) for item in self._load_failures)

    def validate_configuration(self, raw: Mapping[str, Any]) -> ResolvedProviderConfiguration:
        adapter_id = raw.get("adapter_id")
        if not isinstance(adapter_id, str):
            raise ProviderConfigurationError(
                (
                    ProviderConfigurationIssue(
                        pointer="/adapter_id",
                        code="missing_or_invalid",
                        message="must identify an installed provider adapter",
                    ),
                )
            )
        try:
            definition = self.get(adapter_id)
        except ProviderAdapterError as error:
            raise ProviderConfigurationError(
                (
                    ProviderConfigurationIssue(
                        pointer="/adapter_id",
                        code=error.reason_code,
                        message="must reference a trusted installed provider adapter",
                    ),
                )
            ) from None
        return validate_provider_configuration(raw, definition)

    def create(
        self,
        adapter_id: str,
        context: ProviderLaunchContext,
        configuration: Optional[Mapping[str, Any]] = None,
    ) -> BaseProvider:
        definition = self.get(adapter_id)
        raw = configuration or default_provider_configuration(
            adapter_id, definition.manifest.display_name
        )
        resolved = validate_provider_configuration(raw, definition)
        if not resolved.artifact.enabled:
            raise ProviderAdapterError(
                "PROVIDER_CONFIGURATION_DISABLED", "Provider configuration is disabled"
            )
        try:
            provider = definition.factory(context, resolved.settings)
        except ProviderAdapterError:
            raise
        except Exception as error:
            raise ProviderAdapterError(
                "PROVIDER_CREATE_FAILED",
                f"Provider adapter could not create the runtime ({type(error).__name__})",
            ) from error
        if not isinstance(provider, BaseProvider):
            raise ProviderAdapterError(
                "PROVIDER_RUNTIME_INVALID", "Provider adapter returned an invalid runtime"
            )
        return provider

    def preflight(
        self, adapter_id: str, configuration: Optional[Mapping[str, Any]] = None
    ) -> ProviderPreflight:
        definition = self.get(adapter_id)
        raw = configuration or default_provider_configuration(
            adapter_id, definition.manifest.display_name
        )
        resolved = validate_provider_configuration(raw, definition)
        if not resolved.artifact.enabled:
            from cli_agent_orchestrator.providers.contracts import (
                AuthenticationState,
                ProviderState,
            )

            return ProviderPreflight(
                state=ProviderState.DISABLED,
                installed=True,
                authentication=AuthenticationState.UNKNOWN,
                message="Provider configuration is disabled",
            )
        return definition.preflight(resolved.artifact, resolved.settings)


def build_provider_adapter_registry(*, discover: bool = True) -> ProviderAdapterRegistry:
    from cli_agent_orchestrator.providers.builtin_adapters import builtin_adapter_definitions

    registry = ProviderAdapterRegistry()
    for definition in builtin_adapter_definitions():
        registry.register(definition, source="built-in")
    if discover:
        registry.discover_installed()
    return registry
