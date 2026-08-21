"""Provider manager backed by the versioned trusted adapter registry."""

import logging
from typing import Any, Dict, List, Mapping, Optional

from cli_agent_orchestrator.clients.database import (
    get_terminal_metadata,
    handoff_child_input_received,
)
from cli_agent_orchestrator.models.provider import ProviderType
from cli_agent_orchestrator.providers.base import BaseProvider
from cli_agent_orchestrator.providers.contracts import (
    ProviderAdapterError,
    ProviderLaunchContext,
)
from cli_agent_orchestrator.providers.registry import (
    ProviderAdapterRegistry,
    build_provider_adapter_registry,
)

logger = logging.getLogger(__name__)


class ProviderManager:
    """Simplified provider manager with direct mapping."""

    def __init__(self, adapter_registry: Optional[ProviderAdapterRegistry] = None) -> None:
        self._providers: Dict[str, BaseProvider] = {}
        self.adapter_registry = adapter_registry or build_provider_adapter_registry()

    def create_provider(
        self,
        provider_type: str,
        terminal_id: str,
        tmux_session: str,
        tmux_window: str,
        agent_profile: Optional[str] = None,
        allowed_tools: Optional[List[str]] = None,
        skill_prompt: Optional[str] = None,
        model: Optional[str] = None,
        provider_configuration: Optional[Mapping[str, Any]] = None,
        resolved_profile: Any | None = None,
        structured_owner_authorized: bool = False,
    ) -> BaseProvider:
        """Create and store provider instance."""
        try:
            provider = self.adapter_registry.create(
                provider_type,
                ProviderLaunchContext(
                    terminal_id=terminal_id,
                    tmux_session=tmux_session,
                    tmux_window=tmux_window,
                    agent_profile=agent_profile,
                    allowed_tools=allowed_tools,
                    skill_prompt=skill_prompt,
                    model=model,
                    resolved_profile=resolved_profile,
                    structured_owner_authorized=structured_owner_authorized,
                ),
                configuration=provider_configuration,
            )

            # Store in direct mapping
            self._providers[terminal_id] = provider
            logger.info(f"Created {provider_type} provider for terminal: {terminal_id}")
            return provider

        except ProviderAdapterError as e:
            logger.error(
                "Failed to create provider %s for terminal %s: %s",
                provider_type,
                terminal_id,
                e.reason_code,
            )
            if e.reason_code == "ADAPTER_NOT_INSTALLED":
                raise ValueError(f"Unknown provider type: {provider_type}") from e
            if e.reason_code == "PROFILE_REQUIRED":
                display = "Q CLI" if provider_type == "q_cli" else "Kiro CLI"
                raise ValueError(f"{display} provider requires agent_profile parameter") from e
            raise
        except Exception as e:
            logger.error(
                f"Failed to create provider {provider_type} for terminal {terminal_id}: {e}"
            )
            raise

    def get_provider(self, terminal_id: str) -> Optional[BaseProvider]:
        """Get provider instance, creating on-demand if not found.

        Args:
            terminal_id: Terminal ID to get provider for

        Returns:
            Provider instance

        Raises:
            ValueError: If terminal not found in database or provider creation fails
        """
        # Check if already exists
        provider = self._providers.get(terminal_id)
        if provider:
            return provider

        # Try to create on-demand from database metadata
        metadata = get_terminal_metadata(terminal_id)
        if not metadata:
            raise ValueError(f"Terminal {terminal_id} not found in database")

        # Create provider on-demand
        provider_configuration = None
        revision_id = metadata.get("provider_config_revision_id")
        if isinstance(revision_id, str):
            from cli_agent_orchestrator.services.control_plane_registry import (
                get_provider_configuration_revision,
            )

            provider_configuration = get_provider_configuration_revision(revision_id)["document"]
        provider = self.create_provider(
            metadata["provider"],
            terminal_id,
            metadata["tmux_session"],
            metadata["tmux_window"],
            metadata["agent_profile"],
            provider_configuration=provider_configuration,
        )
        # Only a direct handoff relation can restore Codex's no-visible-user
        # completion path.  A generic terminal must keep an idle footer as
        # IDLE after restart, even if it has historical assistant output.
        if metadata["provider"] == ProviderType.CODEX.value and handoff_child_input_received(
            terminal_id
        ):
            provider.mark_input_received()
        logger.info(f"Created provider on-demand for terminal {terminal_id}")
        return provider

    def cleanup_provider(self, terminal_id: str) -> None:
        """Cleanup provider and remove from map (used when terminal is deleted)."""
        try:
            provider = self._providers.pop(terminal_id, None)
            if provider:
                provider.cleanup()
                logger.info(f"Cleaned up provider for terminal: {terminal_id}")
        except Exception as e:
            logger.error(f"Failed to cleanup provider for terminal {terminal_id}: {e}")

    def list_providers(self) -> Dict[str, str]:
        """List all active providers (for debugging)."""
        return {
            terminal_id: provider.__class__.__name__
            for terminal_id, provider in self._providers.items()
        }


# Module-level singleton
provider_manager = ProviderManager()
