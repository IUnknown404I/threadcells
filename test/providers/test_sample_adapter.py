"""The public sample proves third-party registration without a core branch."""

import importlib
from pathlib import Path
from unittest.mock import patch

from cli_agent_orchestrator.providers.contracts import ProviderLaunchContext, ProviderState
from cli_agent_orchestrator.providers.registry import ProviderAdapterRegistry

SAMPLE = Path(__file__).parents[2] / "examples/provider-adapters/threadcells-echo"


def test_sample_adapter_registers_validates_and_creates(monkeypatch):
    monkeypatch.syspath_prepend(str(SAMPLE))
    module = importlib.import_module("threadcells_echo_adapter")
    definition = module.provider_adapter()
    registry = ProviderAdapterRegistry()
    registry.register(definition, source="test-sample")

    provider = registry.create(
        "community.echo",
        ProviderLaunchContext("terminal", "session", "window"),
        {
            "schema_version": 1,
            "config_id": "echo-local",
            "adapter_id": "community.echo",
            "display_name": "Local Echo",
            "enabled": True,
            "settings": {"model": "echo-v1"},
            "secret_refs": {},
        },
    )

    assert provider.__class__.__name__ == "EchoProvider"
    assert provider.settings.model == "echo-v1"
    with patch("threadcells_echo_adapter.adapter.shutil.which", return_value=None):
        assert registry.preflight("community.echo").state == ProviderState.NOT_CONFIGURED
