"""MCP server models."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class HandoffState(str, Enum):
    """Durable state returned by a handoff wait slice."""

    COMPLETED = "completed"
    WAITING = "waiting"
    FAILED = "failed"


class HandoffResult(BaseModel):
    """Result of a handoff operation."""

    success: bool = Field(description="Whether the handoff was successful")
    message: str = Field(description="A message describing the result of the handoff")
    output: Optional[str] = Field(None, description="The output from the target agent")
    terminal_id: Optional[str] = Field(None, description="The terminal ID used for the handoff")
    result_id: Optional[str] = Field(None, description="Immutable durable delegation result ID")
    result_status: Optional[str] = Field(None, description="Semantic durable result status")
    schema_version: Optional[int] = Field(None, description="Result document schema version")
    result_format: Optional[str] = Field(
        None, description="Additive durable result document format"
    )
    reason_code: Optional[str] = Field(
        None, description="Machine-readable reason when success is false"
    )
    workflow_state: Optional[str] = Field(
        None, description="Current durable workflow state when known"
    )
    state: Optional[HandoffState] = Field(
        default=None,
        description="Completed only after validated output; waiting is resumable with terminal_id",
    )

    @model_validator(mode="after")
    def set_legacy_state(self) -> "HandoffResult":
        """Keep callers constructing the former shape semantically compatible."""
        if self.state is None:
            self.state = HandoffState.COMPLETED if self.success else HandoffState.FAILED
        return self
