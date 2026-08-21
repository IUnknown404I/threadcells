"""Versioned, durable artifacts produced by delegated CAO work."""

import json
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_HANDOFF_RESULT_V1_BYTES = 256 * 1024
MAX_HANDOFF_RESULT_V1_ITEMS = 1024
MAX_HANDOFF_RESULT_V1_STRING_BYTES = 64 * 1024


class DelegationResultStatus(str, Enum):
    AWAITING = "awaiting"
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    CANCELLED = "cancelled"


class DelegationResultDocument(BaseModel):
    """The deliberately small v1 structured report contract."""

    model_config = ConfigDict(extra="forbid")

    summary: Optional[str] = None
    body_markdown: str = ""
    changed_files: List[str] = Field(default_factory=list)
    checks: List[Dict[str, Any]] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    blockers: List[str] = Field(default_factory=list)
    format: Literal["v1"] = "v1"


class HandoffResultCheckV1(BaseModel):
    """Strict check item accepted only by the authenticated V1 submit channel."""

    model_config = ConfigDict(extra="forbid", strict=True)

    command: str
    outcome: str


class HandoffResultDocumentV1(BaseModel):
    """Canonical, bounded V1 document for child-authenticated handoff staging.

    This is intentionally separate from ``DelegationResultDocument``.  The
    latter remains the tolerant reader for already-persisted legacy captures;
    widening it would accidentally make terminal parsing less fail-closed.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    format: Literal["v1"]
    summary: Optional[str]
    body_markdown: str
    changed_files: List[str]
    checks: List[HandoffResultCheckV1]
    risks: List[str]
    blockers: List[str]

    @field_validator("summary", "body_markdown", mode="after")
    @classmethod
    def _validate_scalar(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            _validate_v1_string(value)
        return value

    @field_validator("changed_files", "risks", "blockers", mode="after")
    @classmethod
    def _validate_string_list(cls, values: List[str]) -> List[str]:
        if len(values) > MAX_HANDOFF_RESULT_V1_ITEMS:
            raise ValueError("result list exceeds the V1 item limit")
        for value in values:
            _validate_v1_string(value)
        return values

    @field_validator("checks", mode="after")
    @classmethod
    def _validate_checks(cls, values: List[HandoffResultCheckV1]) -> List[HandoffResultCheckV1]:
        if len(values) > MAX_HANDOFF_RESULT_V1_ITEMS:
            raise ValueError("checks exceed the V1 item limit")
        for value in values:
            _validate_v1_string(value.command)
            _validate_v1_string(value.outcome)
        return values

    @model_validator(mode="after")
    def _validate_canonical_size(self) -> "HandoffResultDocumentV1":
        if len(canonical_handoff_result_v1_bytes(self)) > MAX_HANDOFF_RESULT_V1_BYTES:
            raise ValueError("result document exceeds the 256 KiB V1 limit")
        return self


def _validate_v1_string(value: str) -> None:
    if "\x00" in value:
        raise ValueError("NUL is not permitted in a V1 result")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise ValueError("V1 result strings must be valid UTF-8") from exc
    if len(encoded) > MAX_HANDOFF_RESULT_V1_STRING_BYTES:
        raise ValueError("V1 result string exceeds the 64 KiB limit")


def canonical_handoff_result_v1_bytes(document: HandoffResultDocumentV1) -> bytes:
    """Return the sole digest representation for a strict submitted document."""
    return json.dumps(
        document.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8", "strict")
