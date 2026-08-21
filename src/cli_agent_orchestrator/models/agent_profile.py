"""Agent profile models."""

import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

PROFILE_SCHEMA_VERSION = 1
PROFILE_EXECUTION_MODES = ("orchestrator", "owner_executor", "executor", "reviewer")
_REGISTRY_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")


class McpServer(BaseModel):
    """MCP server configuration."""

    type: Optional[str] = None
    command: str
    args: Optional[List[str]] = None
    env: Optional[Dict[str, str]] = None
    timeout: Optional[int] = None


class AgentProfile(BaseModel):
    """Agent profile configuration with Q CLI agent fields."""

    name: str
    description: str
    provider: Optional[str] = None  # Provider override (e.g. "claude_code", "kiro_cli")
    system_prompt: Optional[str] = None  # The markdown content
    role: Optional[str] = None  # "supervisor", "developer", "reviewer"
    # Organizational behavior is independent from provider/model power.  This
    # optional field is additive for legacy Markdown profiles and becomes a
    # required property in the versioned Profile Registry definition.
    execution_mode: Optional[Literal["orchestrator", "owner_executor", "executor", "reviewer"]] = (
        None
    )
    owner_authorization_required: bool = False

    # Q CLI agent fields (all optional, will be passed through to JSON)
    prompt: Optional[str] = None
    mcpServers: Optional[Dict[str, Any]] = None
    tools: Optional[List[str]] = Field(default=None)
    toolAliases: Optional[Dict[str, str]] = None
    allowedTools: Optional[List[str]] = None
    toolsSettings: Optional[Dict[str, Any]] = None
    resources: Optional[List[str]] = None
    hooks: Optional[Dict[str, Any]] = None
    useLegacyMcpJson: Optional[bool] = None
    model: Optional[str] = None
    # Per-profile Codex CLI TOML overrides, for example reasoning effort.
    # Keep the frontmatter spelling for compatibility with installed profiles.
    codexConfig: Optional[Dict[str, Any]] = None


class ProfileAuthority(BaseModel):
    """Provider-independent launch authority captured in every snapshot."""

    model_config = ConfigDict(extra="forbid")

    approval_policy: Literal["untrusted", "on-failure", "on-request", "never"] = "on-request"
    sandbox_mode: Literal["read-only", "workspace-write", "danger-full-access"] = "workspace-write"
    unrestricted_tools_authorized: bool = False


class ProfileDefinition(BaseModel):
    """Portable V1 profile document stored as immutable registry revisions."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    profile_id: str
    display_name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    provider_config_id: str
    role: Literal["supervisor", "developer", "reviewer", "worker"]
    execution_mode: Literal["orchestrator", "owner_executor", "executor", "reviewer"]
    enabled: bool = True
    owner_authorization_required: bool = False
    authority: ProfileAuthority = Field(default_factory=ProfileAuthority)
    model: Optional[str] = Field(default=None, max_length=120)
    reasoning_level: Optional[Literal["minimal", "low", "medium", "high", "xhigh"]] = None
    allowed_tools: List[str] = Field(default_factory=list, max_length=128)
    mcp_server_refs: List[str] = Field(default_factory=list, max_length=32)
    timeouts: Dict[str, int] = Field(default_factory=dict)
    instructions: str = Field(min_length=1, max_length=250_000)

    @field_validator("profile_id", "provider_config_id")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if not _REGISTRY_IDENTIFIER.fullmatch(value):
            raise ValueError("must be a lowercase dotted/dashed identifier")
        return value

    @field_validator("mcp_server_refs")
    @classmethod
    def validate_mcp_refs(cls, values: List[str]) -> List[str]:
        if len(values) != len(set(values)):
            raise ValueError("must not contain duplicate references")
        if any(not _REGISTRY_IDENTIFIER.fullmatch(value) for value in values):
            raise ValueError("must contain registered MCP server identifiers")
        return values

    @field_validator("allowed_tools")
    @classmethod
    def validate_allowed_tools(cls, values: List[str]) -> List[str]:
        if len(values) != len(set(values)):
            raise ValueError("must not contain duplicate tools")
        if any(not value or len(value) > 120 for value in values):
            raise ValueError("contains an invalid tool identifier")
        return values

    @field_validator("timeouts")
    @classmethod
    def validate_timeouts(cls, values: Dict[str, int]) -> Dict[str, int]:
        for name, value in values.items():
            if not _REGISTRY_IDENTIFIER.fullmatch(name):
                raise ValueError("contains an invalid timeout name")
            if isinstance(value, bool) or not 1 <= value <= 86_400:
                raise ValueError("timeout values must be integers from 1 to 86400")
        return values
