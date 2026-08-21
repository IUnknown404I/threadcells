"""Best-effort operational usage capture and aggregate projections.

This module is intentionally outside provider execution and lifecycle control.
An unavailable database or an unparseable provider capture only loses usage
telemetry; it must never affect an agent result, completion, or retry path.
"""

import logging
from typing import Any, Dict

from cli_agent_orchestrator.clients.database import get_usage_statistics, record_usage_observation
from cli_agent_orchestrator.models.usage import UsageObservation

logger = logging.getLogger(__name__)

PROVIDER_USAGE_LABEL = "Provider-reported usage — not a billing statement"


def observe_provider_completion(metadata: Dict[str, Any], provider: Any) -> None:
    """Capture a completed provider run without changing its outcome.

    This function deliberately owns all failures, including capture, parser and
    storage failures.  It is called only after the normal provider completion
    decision has already been made.
    """
    try:
        extract = getattr(provider, "extract_usage_observation", None)
        if not callable(extract):
            return
        from cli_agent_orchestrator.clients.tmux import tmux_client

        capture = tmux_client.get_history(metadata["tmux_session"], metadata["tmux_window"])
        observation = extract(capture)
        if not isinstance(observation, UsageObservation):
            return
        record_usage_observation(
            observation,
            provider=metadata.get("provider"),
            agent_profile=metadata.get("agent_profile"),
            terminal_id=metadata.get("id"),
            terminal_name=metadata.get("tmux_window"),
            session_id=metadata.get("session_id"),
            session_name=metadata.get("tmux_session"),
            project_id=metadata.get("project_id"),
            project_name=metadata.get("project_name"),
            project_path=metadata.get("project_path"),
        )
    except Exception as exc:
        logger.warning(
            "Usage observation skipped for terminal %s: %s", metadata.get("id", "unknown"), exc
        )


def observe_provider_usage(
    metadata: Dict[str, Any], provider: Any | None, *, completed: bool
) -> None:
    """Observe live durable usage, retaining pane parsing only as a fallback."""
    try:
        if metadata.get("provider") == "codex":
            from cli_agent_orchestrator.services.codex_usage_service import (
                observe_codex_terminal_usage,
            )

            result = observe_codex_terminal_usage(metadata)
            if result.get("binding_count", 0) > 0:
                return
        if completed and provider is not None:
            observe_provider_completion(metadata, provider)
    except Exception as exc:
        logger.warning(
            "Usage observation skipped for terminal %s: %s", metadata.get("id", "unknown"), exc
        )


def statistics() -> Dict[str, Any]:
    """Return read-only aggregate projections for the Statistics UI/API."""
    try:
        from cli_agent_orchestrator.services.codex_usage_service import refresh_all_codex_usage

        refresh_all_codex_usage()
    except Exception as exc:
        # Statistics remains available from its last durable snapshot even when
        # a provider state directory or retained terminal is unavailable.
        logger.warning("Live Codex usage refresh skipped: %s", exc)
    result = get_usage_statistics()
    result["label"] = PROVIDER_USAGE_LABEL
    return result
