"""Allowlisted access to packaged public JSON Schemas and safe examples."""

from __future__ import annotations

import json
from importlib import resources
from typing import Any, cast

SCHEMA_VERSION = "v1"
SCHEMA_NAMES = (
    "adapter-manifest",
    "capabilities",
    "profile",
    "provider-config",
)


def list_schemas() -> list[dict[str, str]]:
    return [
        {"name": name, "version": SCHEMA_VERSION, "path": f"/schemas/v1/{name}"}
        for name in SCHEMA_NAMES
    ]


def get_schema(name: str) -> dict[str, Any]:
    if name not in SCHEMA_NAMES:
        raise KeyError(name)
    root = resources.files("cli_agent_orchestrator.public_schemas") / SCHEMA_VERSION
    document = json.loads((root / f"{name}.schema.json").read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise RuntimeError(f"Packaged schema is not an object: {name}")
    return cast(dict[str, Any], document)


def get_example(name: str) -> dict[str, Any]:
    if name == "profile":
        return {
            "schema_version": 1,
            "profile_id": "custom-reviewer",
            "display_name": "Custom reviewer",
            "description": "Reviews bounded changes with read-only project tools.",
            "provider_config_id": "builtin-codex",
            "role": "reviewer",
            "execution_mode": "reviewer",
            "enabled": True,
            "owner_authorization_required": False,
            "authority": {
                "approval_policy": "never",
                "sandbox_mode": "read-only",
                "unrestricted_tools_authorized": False,
            },
            "model": None,
            "reasoning_level": "medium",
            "allowed_tools": ["@builtin", "fs_read", "fs_list", "@cao-mcp-server"],
            "mcp_server_refs": ["cao-mcp-server"],
            "timeouts": {},
            "instructions": "Review the assigned bounded change and report actionable findings.",
        }
    if name == "provider-config":
        return {
            "schema_version": 1,
            "config_id": "custom-codex",
            "adapter_id": "codex",
            "display_name": "Codex local",
            "enabled": True,
            "settings": {},
            "secret_refs": {},
        }
    raise KeyError(name)
