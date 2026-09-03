"""Inbox message models."""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class OrchestrationType(str, Enum):
    """Orchestration mode for a message delivery."""

    SEND_MESSAGE = "send_message"
    HANDOFF = "handoff"
    ASSIGN = "assign"


class MessageStatus(str, Enum):
    """Message status enumeration."""

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"


class ChildAssignmentStatus(str, Enum):
    """Durable state for a delegated child expected to resume its parent."""

    AWAITING_RESULT = "awaiting_result"
    RESULT_QUEUED = "result_queued"
    RESULT_DELIVERED = "result_delivered"
    RESULT_FAILED = "result_failed"
    RESULT_ACKNOWLEDGED = "result_acknowledged"
    RESULT_SUPERSEDED = "result_superseded"
    HANDOFF_AWAITING_RESULT = "handoff_awaiting_result"
    HANDOFF_RECOVERY_AWAITING_RESULT = "handoff_recovery_awaiting_result"
    HANDOFF_DIRECT_RESULT_CLAIMED = "handoff_direct_result_claimed"
    HANDOFF_RESULT_QUEUED = "handoff_result_queued"
    HANDOFF_RESULT_DELIVERED = "handoff_result_delivered"
    HANDOFF_RESULT_FAILED = "handoff_result_failed"
    HANDOFF_RESULT_ACKNOWLEDGED = "handoff_result_acknowledged"
    CANCELLED = "cancelled"


class InboxMessage(BaseModel):
    """Inbox message model."""

    id: int = Field(..., description="Message ID")
    sender_id: str = Field(..., description="Sender terminal ID")
    receiver_id: str = Field(..., description="Receiver terminal ID")
    message: str = Field(..., description="Message content")
    status: MessageStatus = Field(..., description="Message status")
    result_id: Optional[str] = Field(None, description="Linked durable delegation result")
    kind: str = Field("message", description="message or delegation_result_notice")
    superseded_at: Optional[datetime] = Field(None, description="Legacy body superseded by result")
    created_at: datetime = Field(..., description="Creation timestamp")
