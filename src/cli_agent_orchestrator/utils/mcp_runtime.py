"""Canonical runtime settings for CAO-owned MCP sidecars."""

import os
from typing import Any

CAO_MCP_SERVER_NAME = "cao-mcp-server"
CAO_MCP_SERVER_COMMAND = (
    os.environ.get("THREADCELLS_MCP_SERVER_COMMAND")
    or os.environ.get("THREADMESH_MCP_SERVER_COMMAND")
    or "threadcells-mcp-server"
)
TRUSTED_MCP_SERVER_IDS = frozenset({CAO_MCP_SERVER_NAME})


def resolve_trusted_mcp_server_refs(server_names: list[str]) -> dict[str, dict[str, Any]]:
    """Resolve declarative profile references through the server-owned registry."""
    unknown = sorted(set(server_names) - TRUSTED_MCP_SERVER_IDS)
    if unknown:
        raise ValueError(f"unregistered MCP server reference: {unknown[0]}")
    return {name: {"type": "stdio", "command": CAO_MCP_SERVER_COMMAND} for name in server_names}


def canonicalize_cao_mcp_server_config(server_name: str, config: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a server config pinned to ThreadCells's local command.

    CAO-managed terminals follow the installed local command instead of
    resolving a package from a remote registry or Git at child-launch time.
    This is applied when profiles are loaded and again at provider rendering.
    """
    canonical_config = dict(config)
    if server_name == CAO_MCP_SERVER_NAME:
        canonical_config["command"] = CAO_MCP_SERVER_COMMAND
        canonical_config.pop("args", None)
    return canonical_config
