"""Security and compatibility coverage for Provider Adapter API V1."""

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest
from pydantic import Field

from cli_agent_orchestrator.models.provider import ProviderType
from cli_agent_orchestrator.providers.builtin_adapters import (
    NoAdapterSettings,
    builtin_adapter_definitions,
)
from cli_agent_orchestrator.providers.contracts import (
    AdapterCapabilities,
    AdapterManifest,
    AdapterSettings,
    AuthenticationState,
    CapabilitySupport,
    ProviderAdapterDefinition,
    ProviderAdapterError,
    ProviderConfigurationError,
    ProviderLaunchContext,
    ProviderPreflight,
    ProviderState,
    validate_provider_configuration,
)
from cli_agent_orchestrator.providers.registry import (
    ProviderAdapterRegistry,
    build_provider_adapter_registry,
)


def _context() -> ProviderLaunchContext:
    return ProviderLaunchContext("terminal", "session", "window", "developer")


def test_builtins_cover_legacy_provider_ids_and_codex_is_reference_adapter():
    registry = build_provider_adapter_registry(discover=False)
    manifests = {manifest.adapter_id: manifest for manifest in registry.manifests()}

    assert set(manifests) == {provider.value for provider in ProviderType}
    codex = manifests["codex"]
    assert codex.display_name == "Codex"
    assert codex.capabilities.usage == CapabilitySupport.SUPPORTED
    assert codex.capabilities.cached_input_tokens == CapabilitySupport.SUPPORTED
    assert codex.capabilities.reasoning_controls == CapabilitySupport.SUPPORTED
    claude = manifests["claude_code"]
    assert claude.capabilities.usage == CapabilitySupport.CONDITIONAL
    assert claude.capabilities.model_discovery == CapabilitySupport.UNSUPPORTED


@pytest.mark.parametrize(
    "unsafe",
    [
        {"command": "do-not-run"},
        {"nested": {"argv": ["--unsafe"]}},
        {"binary_path": "/opt/untrusted/provider"},
        {"flags": ["--arbitrary"]},
        {"api_key": "must-be-a-reference"},
        {"environment": {"TOKEN": "secret"}},
    ],
)
def test_declarative_provider_config_rejects_execution_and_secrets(unsafe):
    definition = next(
        item for item in builtin_adapter_definitions() if item.manifest.adapter_id == "codex"
    )
    raw = {
        "schema_version": 1,
        "config_id": "my-codex",
        "adapter_id": "codex",
        "display_name": "Codex",
        "settings": unsafe,
        "secret_refs": {},
    }

    with pytest.raises(ProviderConfigurationError) as caught:
        validate_provider_configuration(raw, definition)

    assert caught.value.issues[0].code == "executable_or_secret_config_forbidden"
    assert "do-not-run" not in str(caught.value)
    assert "secret" not in str(caught.value)


def test_secret_reference_is_opaque_and_export_safe():
    definition = next(
        item for item in builtin_adapter_definitions() if item.manifest.adapter_id == "codex"
    )
    resolved = validate_provider_configuration(
        {
            "schema_version": 1,
            "config_id": "my-codex",
            "adapter_id": "codex",
            "display_name": "Codex",
            "settings": {},
            "secret_refs": {"authentication": "secret-store/codex-default"},
        },
        definition,
    )

    exported = resolved.artifact.model_dump(mode="json")
    assert exported["secret_refs"] == {"authentication": "secret-store/codex-default"}
    assert "token" not in exported
    assert "password" not in exported


def test_unknown_adapter_is_a_json_pointer_validation_error():
    registry = build_provider_adapter_registry(discover=False)

    with pytest.raises(ProviderConfigurationError) as caught:
        registry.validate_configuration(
            {
                "schema_version": 1,
                "config_id": "community",
                "adapter_id": "not-installed",
                "display_name": "Community",
                "settings": {},
                "secret_refs": {},
            }
        )

    assert caught.value.issues[0].pointer == "/adapter_id"
    assert caught.value.issues[0].code == "ADAPTER_NOT_INSTALLED"


def test_adapter_owned_model_produces_field_level_pointer_errors():
    class CommunitySettings(AdapterSettings):
        model: str = Field(min_length=2)

    builtin = next(
        item for item in builtin_adapter_definitions() if item.manifest.adapter_id == "codex"
    )
    definition = replace(builtin, settings_model=CommunitySettings)

    with pytest.raises(ProviderConfigurationError) as caught:
        validate_provider_configuration(
            {
                "schema_version": 1,
                "config_id": "community",
                "adapter_id": "codex",
                "display_name": "Community",
                "settings": {"model": "x"},
                "secret_refs": {},
            },
            definition,
        )

    assert caught.value.issues[0].pointer == "/settings/model"
    assert caught.value.issues[0].code == "string_too_short"
    assert "x" not in str(caught.value)


def test_registry_rejects_incompatible_and_unsafe_manifests():
    base = next(
        item for item in builtin_adapter_definitions() if item.manifest.adapter_id == "codex"
    )
    registry = ProviderAdapterRegistry()
    incompatible = replace(
        base,
        manifest=base.manifest.model_copy(update={"plugin_api_version": "2.0"}),
    )
    with pytest.raises(ProviderAdapterError) as caught:
        registry.register(incompatible, source="test")
    assert caught.value.reason_code == "ADAPTER_API_INCOMPATIBLE"

    unsafe_schema = replace(
        base,
        manifest=base.manifest.model_copy(
            update={
                "adapter_id": "unsafe",
                "configuration_schema": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                },
            }
        ),
    )
    with pytest.raises(ProviderAdapterError) as caught:
        registry.register(unsafe_schema, source="test")
    assert caught.value.reason_code == "ADAPTER_MANIFEST_UNSAFE"


def test_installed_entry_point_adds_adapter_without_core_branch():
    base = next(
        item for item in builtin_adapter_definitions() if item.manifest.adapter_id == "codex"
    )
    community = replace(
        base,
        manifest=base.manifest.model_copy(
            update={"adapter_id": "community.example", "display_name": "Community Example"}
        ),
    )
    entry_point = MagicMock(name="entry_point")
    entry_point.name = "community-example"
    entry_point.value = "community_example:adapter"
    entry_point.load.return_value = community

    registry = ProviderAdapterRegistry()
    with patch(
        "cli_agent_orchestrator.providers.registry.metadata.entry_points",
        return_value=[entry_point],
    ):
        registry.discover_installed()

    assert registry.get("community.example") is community
    assert registry.source("community.example") == "package:community-example"
    assert registry.load_failures == ()


def test_bad_installed_adapter_isolated_as_sanitized_failure():
    entry_point = MagicMock(name="entry_point")
    entry_point.name = "broken"
    entry_point.value = "broken:adapter"
    entry_point.load.side_effect = RuntimeError("private plugin detail")
    registry = ProviderAdapterRegistry()
    with patch(
        "cli_agent_orchestrator.providers.registry.metadata.entry_points",
        return_value=[entry_point],
    ):
        registry.discover_installed()

    assert registry.load_failures == (
        {
            "entry_point": "broken",
            "reason_code": "ADAPTER_LOAD_FAILED",
            "error_type": "RuntimeError",
        },
    )
    assert "private plugin detail" not in str(registry.load_failures)


def test_preflight_truthfully_reports_missing_cli():
    registry = build_provider_adapter_registry(discover=False)

    with patch(
        "cli_agent_orchestrator.providers.builtin_adapters.shutil.which",
        return_value=None,
    ):
        result = registry.preflight("claude_code")

    assert result == ProviderPreflight(
        state=ProviderState.NOT_CONFIGURED,
        installed=False,
        authentication=AuthenticationState.UNKNOWN,
        compatible=False,
        reason_code="EXECUTABLE_NOT_FOUND",
        message="Install claude and configure its authentication",
    )


def test_registry_create_uses_trusted_factory_and_rejects_unknown_adapter():
    registry = build_provider_adapter_registry(discover=False)
    provider = registry.create("codex", _context())
    assert provider.__class__.__name__ == "CodexProvider"

    with pytest.raises(ProviderAdapterError) as caught:
        registry.create("not-installed", _context())
    assert caught.value.reason_code == "ADAPTER_NOT_INSTALLED"
