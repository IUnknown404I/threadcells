from datetime import datetime
from enum import Enum
from typing import Annotated, List, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Terminal ID validation (8 character hex string)
TerminalId = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{8}$")]


class TerminalStatus(str, Enum):
    """Terminal status enumeration with provider-aware states."""

    IDLE = "idle"
    PROCESSING = "processing"
    COMPLETED = "completed"
    WAITING_USER_ANSWER = "waiting_user_answer"
    ERROR = "error"


class TerminalLifecycle(str, Enum):
    """Whether the provider process, rather than only its tmux pane, is live."""

    STARTING = "starting"
    RUNNING = "running"
    EXIT_PENDING = "exit_pending"
    EXITED = "exited"
    RECOVERY_FENCED = "recovery_fenced"


class Terminal(BaseModel):
    """Terminal model - represents a tmux window."""

    model_config = ConfigDict(use_enum_values=True)

    id: str = Field(..., description="Unique terminal identifier")
    name: str = Field(..., description="Terminal/window name")
    # Built-in ProviderType values remain compatible, while trusted installed
    # adapters may expose additional registered identifiers.
    provider: str = Field(..., description="Registered provider adapter identifier")
    session_name: str = Field(..., description="Session name")
    session_id: Optional[str] = Field(
        None, description="Canonical durable session lifetime identifier"
    )
    agent_profile: Optional[str] = Field(None, description="Agent profile")
    allowed_tools: Optional[List[str]] = Field(None, description="Allowed CAO tools")
    status: Optional[TerminalStatus] = Field(
        None, description="Current terminal status (live only)"
    )
    execution_state: Optional[str] = Field(
        None,
        description="Provider execution projection: ready, processing, or a durable wait reason",
    )
    lifecycle: Optional[TerminalLifecycle] = Field(
        None, description="Provider-process lifecycle; independent of persistent tmux panes"
    )
    workflow_state: Optional[str] = Field(
        None, description="Canonical durable lifecycle projection; this alone authorizes Completed"
    )
    workflow_status: Optional[str] = Field(
        None, description="Raw durable workflow status retained as diagnostic metadata"
    )
    provider_outcome_code: Optional[str] = Field(
        None, description="Normalized structured outcome for the active provider turn"
    )
    provider_outcome_detail: Optional[str] = Field(
        None, description="Bounded provider-native outcome identifier; never response content"
    )
    assignment_status: Optional[str] = Field(
        None, description="Raw delegated-child relation status retained as diagnostic metadata"
    )
    result_status: Optional[str] = Field(
        None, description="Raw durable delegation-result status retained as diagnostic metadata"
    )
    delivery_status: Optional[str] = Field(
        None, description="Raw durable result-delivery status retained as diagnostic metadata"
    )
    context_role: Optional[str] = Field(
        None, description="Explicit provider accounting role: supervisor or work"
    )
    launch_worktree: Optional[str] = Field(None, description="Immutable canonical launch worktree")
    managed_worktree_kind: Optional[str] = Field(
        None, description="CAO-managed worktree lifecycle: task or reviewer"
    )
    managed_worktree_commit: Optional[str] = Field(
        None, description="Exact source commit used to create a managed worktree"
    )
    managed_worktree_branch: Optional[str] = Field(
        None, description="Preserved task branch for deterministic integration"
    )
    project_id: Optional[str] = Field(None, alias="projectId", description="Launch project ID")
    project_name: Optional[str] = Field(None, description="Historical launch project name")
    project_path: Optional[str] = Field(None, description="Historical launch project path")
    project_description: Optional[str] = Field(
        None, description="Historical launch project description"
    )
    last_active: Optional[datetime] = Field(None, description="Last active timestamp")
