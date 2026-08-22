"""Minimal real-stdio CAO MCP server harness for runtime-fence regression tests."""

import os
from pathlib import Path

from cli_agent_orchestrator.mcp_server import server
from cli_agent_orchestrator.mcp_server.models import HandoffResult, HandoffState

server._SIDECAR_RUNTIME_GENERATION = os.environ["CAO_TEST_SIDECAR_RUNTIME_GENERATION"]


def _record(event: str) -> None:
    with Path(os.environ["CAO_STDIO_RUNTIME_FENCE_TRACE"]).open("a", encoding="utf-8") as trace:
        trace.write(f"{event}\n")


def _claim_effect(*_args: object) -> dict[str, str]:
    _record("effect-claim")
    return {"id": "test-effect", "claim_token": "test-token"}


def _finish_effect(_effect: dict[str, str], outcome: str) -> None:
    _record(f"effect-finish:{outcome}")


async def _create_one_child(*_args: object, **_kwargs: object) -> HandoffResult:
    _record("child-create")
    return HandoffResult(
        success=True,
        message="completed",
        output="stdio recovery completed",
        terminal_id="child-stdio-recovery",
        state=HandoffState.COMPLETED,
    )


server.claim_workflow_effect = _claim_effect
server._finish_privileged_effect = _finish_effect
server._handoff_impl = _create_one_child
server.main()
