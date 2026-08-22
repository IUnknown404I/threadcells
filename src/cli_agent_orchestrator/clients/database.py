"""Minimal database client with only terminal metadata."""

import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional, cast

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    inspect,
    or_,
    text,
)
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import DeclarativeBase, declarative_base, sessionmaker

from cli_agent_orchestrator.constants import DATABASE_URL, DB_DIR, DEFAULT_PROVIDER
from cli_agent_orchestrator.models.flow import Flow
from cli_agent_orchestrator.models.inbox import (
    ChildAssignmentStatus,
    InboxMessage,
    MessageStatus,
)
from cli_agent_orchestrator.models.project import Project
from cli_agent_orchestrator.models.result import (
    DelegationResultDocument,
    DelegationResultStatus,
    HandoffResultDocumentV1,
    canonical_handoff_result_v1_bytes,
)
from cli_agent_orchestrator.models.usage import UsageObservation

logger = logging.getLogger(__name__)

Base: Any = declarative_base()


class TerminalModel(Base):
    """SQLAlchemy model for terminal metadata only."""

    __tablename__ = "terminals"

    id = Column(String, primary_key=True)  # "abc123ef"
    tmux_session = Column(String, nullable=False)  # "cao-session-name"
    # The tmux name is reusable.  Keep an opaque per-lifetime identity so
    # historical usage cannot be merged when a user recreates a session with
    # the same display name.
    session_id = Column(String, nullable=True, index=True)
    tmux_window = Column(String, nullable=False)  # "window-name"
    provider = Column(String, nullable=False)  # "q_cli", "claude_code"
    agent_profile = Column(String)  # "developer", "reviewer" (optional)
    allowed_tools = Column(String, nullable=True)  # JSON-encoded list of CAO tool names
    # Immutable launch-time ownership metadata.  Pane cwd is mutable and must
    # never be used to infer which worktree a terminal is authorized to write.
    launch_worktree = Column(String, nullable=True)
    write_enabled = Column(Boolean, nullable=True)
    # Explicit launch-time accounting authority. Only an actual orchestration
    # supervisor is exempt; every substantive developer/reviewer/worker is a
    # work context regardless of session topology. NULL is a legacy unknown
    # and is conservatively counted as work.
    context_role = Column(String, nullable=True)
    # Optional CAO-managed Git worktree lifecycle metadata.
    managed_worktree_kind = Column(String, nullable=True)
    managed_worktree_source = Column(String, nullable=True)
    managed_worktree_branch = Column(String, nullable=True)
    managed_worktree_commit = Column(String, nullable=True)
    # Project context is copied at launch, so history remains truthful even if
    # an administrator later changes or removes the registry entry.
    project_id = Column(String, nullable=True)
    project_name = Column(String, nullable=True)
    project_path = Column(String, nullable=True)
    project_description = Column(Text, nullable=True)
    # Runtime ownership is deliberately independent from durable terminal
    # history.  NULL is a rolling-upgrade unknown and therefore remains
    # fail-closed until exact runtime observation reconciles it.
    runtime_lifecycle = Column(String, nullable=True)
    runtime_exit_requested_at = Column(DateTime, nullable=True)
    runtime_exited_at = Column(DateTime, nullable=True)
    # Exact launch identity for destructive runtime retirement. Names and PIDs
    # are reusable; the opaque generation is present in both pane metadata and
    # the inherited shell environment, while process start ticks fence PID reuse.
    runtime_pane_id = Column(String, nullable=True)
    runtime_pane_pid = Column(Integer, nullable=True)
    runtime_generation = Column(String, nullable=True)
    runtime_generation_origin = Column(String, nullable=True)
    runtime_process_start_ticks = Column(Integer, nullable=True)
    # Exactly one operation may mutate a live pane at a time.  Provider
    # execution capacity is deliberately separate: status observation may
    # release that capacity while a physical paste is still completing.
    runtime_operation_kind = Column(String, nullable=True)
    runtime_operation_token = Column(String, nullable=True)
    runtime_operation_claimed_at = Column(DateTime, nullable=True)
    runtime_operation_expires_at = Column(DateTime, nullable=True)
    # Immutable durable insertion order. IDs, activity timestamps, runtime
    # presence, and provider response order are not creation-order facts.
    creation_order = Column(Integer, nullable=True)
    # Revocable child-only capability for the hidden structured handoff submit
    # endpoint.  The plaintext token exists only in the terminal environment.
    auth_token_sha256 = Column(String, nullable=True)
    # Immutable receipt linking a privileged launch to its one-use owner grant.
    owner_grant_id = Column(String, nullable=True, unique=True)
    # New launches carry an immutable public-control-plane snapshot. Existing
    # rows remain explicitly legacy rather than receiving invented history.
    profile_revision_id = Column(String, nullable=True, index=True)
    provider_config_revision_id = Column(String, nullable=True, index=True)
    launch_snapshot_json = Column(Text, nullable=True)
    launch_snapshot_status = Column(String, nullable=True)
    last_active = Column(DateTime, default=datetime.now)


class WorktreeWriterLeaseModel(Base):
    """Durable exclusive writer ownership for one canonical worktree."""

    __tablename__ = "worktree_writer_leases"

    canonical_worktree = Column(String, primary_key=True)
    terminal_id = Column(String, nullable=False, unique=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class ProviderExecutionLeaseModel(Base):
    """One durable slot owned only while a provider turn is executing."""

    __tablename__ = "provider_execution_leases"

    terminal_id = Column(String, primary_key=True)
    workflow_turn_id = Column(Integer, nullable=False, unique=True, index=True)
    acquired_at = Column(DateTime, nullable=False, default=datetime.now)


class CapacitySettingsModel(Base):
    """Canonical singleton capacity policy, updated transactionally."""

    __tablename__ = "capacity_settings"

    id = Column(Integer, primary_key=True)
    schema_version = Column(Integer, nullable=False, default=1)
    max_resident_supervisors = Column(Integer, nullable=False)
    max_provider_executions = Column(Integer, nullable=False)
    max_work_contexts = Column(Integer, nullable=False)
    max_heavy_execution_slots = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)


class CapacitySettingsAuditModel(Base):
    """Append-only operator and migration history for capacity changes."""

    __tablename__ = "capacity_settings_audit"

    id = Column(Integer, primary_key=True, autoincrement=True)
    actor = Column(String, nullable=False)
    reason = Column(String, nullable=False)
    previous_json = Column(Text, nullable=True)
    settings_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class HousekeepingSettingsModel(Base):
    """Canonical singleton cleanup policy and schedule metadata."""

    __tablename__ = "housekeeping_settings"

    id = Column(Integer, primary_key=True)
    schema_version = Column(Integer, nullable=False, default=1)
    policy_json = Column(Text, nullable=False)
    schedule_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)


class HousekeepingSettingsAuditModel(Base):
    """Append-only operator and migration history for cleanup policy."""

    __tablename__ = "housekeeping_settings_audit"

    id = Column(Integer, primary_key=True, autoincrement=True)
    actor = Column(String, nullable=False)
    reason = Column(String, nullable=False)
    previous_json = Column(Text, nullable=True)
    settings_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class TelegramSettingsModel(Base):
    """Installation-global, non-secret Telegram notification settings."""

    __tablename__ = "telegram_settings"

    id = Column(Integer, primary_key=True)
    schema_version = Column(Integer, nullable=False, default=1)
    enabled = Column(Boolean, nullable=False, default=False)
    chat_id = Column(String, nullable=True)
    message_thread_id = Column(Integer, nullable=True)
    last_result = Column(String, nullable=True)
    last_result_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)


class TelegramDeliveryModel(Base):
    """One durable, non-replayable Telegram lifecycle delivery attempt."""

    __tablename__ = "telegram_notification_deliveries"

    event_key = Column(String, primary_key=True)
    event_kind = Column(String, nullable=False)
    workflow_id = Column(Integer, nullable=False, index=True)
    root_terminal_id = Column(String, nullable=False, index=True)
    state = Column(String, nullable=False, default="claimed")
    error_code = Column(String, nullable=True)
    attempt_count = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)


class MigrationReceiptModel(Base):
    """Idempotent durable receipts for additive runtime migrations."""

    __tablename__ = "migration_receipts"

    name = Column(String, primary_key=True)
    schema_version = Column(Integer, nullable=False)
    detail_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class OwnerLaunchGrantModel(Base):
    """Short-lived, digest-only authority for one privileged terminal launch."""

    __tablename__ = "owner_launch_grants"

    id = Column(String, primary_key=True)
    token_sha256 = Column(String, nullable=False, unique=True)
    launch_id = Column(String, nullable=False, unique=True)
    agent_profile = Column(String, nullable=False)
    provider = Column(String, nullable=False)
    canonical_worktree = Column(String, nullable=False)
    requested_session_name = Column(String, nullable=True)
    scope_json = Column(Text, nullable=True)
    issued_by = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    expires_at = Column(DateTime, nullable=False)
    consumed_at = Column(DateTime, nullable=True)
    consumed_terminal_id = Column(String, nullable=True, unique=True)


class OperatorSessionModel(Base):
    """Digest-only, revocable browser session for the configured operator."""

    __tablename__ = "operator_sessions"

    id = Column(String, primary_key=True)
    token_sha256 = Column(String, nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)


class ControlPlaneSchemaModel(Base):
    """Singleton version for the additive public control-plane schema."""

    __tablename__ = "control_plane_schema"

    id = Column(Integer, primary_key=True)
    schema_version = Column(Integer, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)


class ProviderConfigRecordModel(Base):
    """Stable provider-configuration identity with one active revision."""

    __tablename__ = "provider_config_records"

    config_id = Column(String, primary_key=True)
    adapter_id = Column(String, nullable=False, index=True)
    display_name = Column(String, nullable=False)
    active_revision_id = Column(String, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    built_in = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)


class ProviderConfigRevisionModel(Base):
    """Immutable, secret-reference-only provider configuration revision."""

    __tablename__ = "provider_config_revisions"
    __table_args__ = (
        UniqueConstraint("config_id", "revision_number", name="uq_provider_config_revision"),
        UniqueConstraint("config_id", "fingerprint", name="uq_provider_config_fingerprint"),
    )

    id = Column(String, primary_key=True)
    config_id = Column(String, nullable=False, index=True)
    revision_number = Column(Integer, nullable=False)
    document_json = Column(Text, nullable=False)
    fingerprint = Column(String, nullable=False)
    created_by = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class ProfileRecordModel(Base):
    """Stable public profile identity with immutable revision history."""

    __tablename__ = "profile_records"

    profile_id = Column(String, primary_key=True)
    display_name = Column(String, nullable=False)
    description = Column(String, nullable=False)
    active_revision_id = Column(String, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    built_in = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)


class ProfileRevisionModel(Base):
    """Immutable profile document and canonical content fingerprint."""

    __tablename__ = "profile_revisions"
    __table_args__ = (
        UniqueConstraint("profile_id", "revision_number", name="uq_profile_revision"),
        UniqueConstraint("profile_id", "fingerprint", name="uq_profile_fingerprint"),
    )

    id = Column(String, primary_key=True)
    profile_id = Column(String, nullable=False, index=True)
    revision_number = Column(Integer, nullable=False)
    document_json = Column(Text, nullable=False)
    fingerprint = Column(String, nullable=False)
    created_by = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class ProfileReferenceModel(Base):
    """Explicit capabilities referenced by one immutable profile revision."""

    __tablename__ = "profile_references"
    __table_args__ = (
        UniqueConstraint(
            "profile_revision_id", "reference_kind", "reference_id", name="uq_profile_reference"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_revision_id = Column(String, nullable=False, index=True)
    reference_kind = Column(String, nullable=False)
    reference_id = Column(String, nullable=False)


class WorktreeWriterLeaseConflict(RuntimeError):
    """A canonical worktree already has a durable writer owner."""

    def __init__(self, canonical_worktree: str):
        self.canonical_worktree = canonical_worktree
        super().__init__(f"writer lease already exists for {canonical_worktree}")


class UnreconciledTerminalAuthority(RuntimeError):
    """A pre-authority terminal still has unknown worktree mutation rights."""

    def __init__(self, terminal_id: str):
        self.terminal_id = terminal_id
        super().__init__(f"terminal authority is unreconciled for {terminal_id}")


class OwnerGrantRejected(RuntimeError):
    """A privileged launch lacks matching, live, one-use owner authority."""

    def __init__(self, reason_code: str = "OWNER_GRANT_REQUIRED"):
        self.reason_code = reason_code
        super().__init__(reason_code)


class InboxModel(Base):
    """SQLAlchemy model for inbox messages."""

    __tablename__ = "inbox"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sender_id = Column(String, nullable=False)
    receiver_id = Column(String, nullable=False)
    message = Column(String, nullable=False)
    status = Column(String, nullable=False)  # MessageStatus enum value
    # A result notice is still delivered by Inbox, but the immutable artifact
    # is its content authority. These nullable columns keep rolling upgrades
    # and ordinary messages compatible.
    result_id = Column(String, nullable=True, index=True)
    kind = Column(String, nullable=False, default="message")
    superseded_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)


class ChildAssignmentModel(Base):
    """One durable result expectation for a delegated child."""

    __tablename__ = "child_assignments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    parent_terminal_id = Column(String, nullable=False)
    child_terminal_id = Column(String, nullable=False, unique=True)
    status = Column(String, nullable=False)
    result_message_id = Column(Integer, nullable=True)
    # Recovery must not wake a parent until the completed child has been
    # cleaned up.  Keep that receipt independent from Inbox delivery state.
    cleanup_acknowledged = Column(Boolean, nullable=False, default=False)
    # A direct handoff has no Inbox row.  Persist its validated result while
    # cleanup is retried so a lost cleanup response cannot consume it twice.
    direct_result_output = Column(String, nullable=True)
    # Codex completion detection needs to distinguish a long submitted
    # handoff whose user row scrolled out of capture history from an ordinary
    # idle prompt after a provider is rebuilt.  This belongs to the direct
    # handoff relation, never to the terminal globally.
    handoff_input_received = Column(Boolean, nullable=False, default=False)
    # An acknowledged assigned child may be retired only after it claims this
    # durable quiescence fence.  While claimed, the child cannot be reopened
    # or register a descendant between the completion barrier check and /exit.
    retirement_claim_token = Column(String, nullable=True)
    retirement_claimed_at = Column(DateTime, nullable=True)
    # This durable reservation is written immediately before the non-transactional
    # provider /exit.  Unlike the transient quiescence claim, it is never
    # released: an interrupted call has an unknown provider outcome and must be
    # reconciled by observation rather than dispatched again.
    retirement_exit_dispatched_at = Column(DateTime, nullable=True)
    # Immutable, exact authority used by the post-exit cleanup saga.  Runtime
    # ownership may be released before this intent is fulfilled, but a child
    # is not finally retired until cleanup_completed_at is durably recorded.
    retirement_cleanup_intent = Column(Text, nullable=True)
    retirement_cleanup_completed_at = Column(DateTime, nullable=True)
    retirement_completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)


class DelegationResultModel(Base):
    """One immutable semantic result for one durable child assignment."""

    __tablename__ = "delegation_results"
    __table_args__ = (
        UniqueConstraint("child_assignment_id", name="uq_delegation_result_assignment"),
    )

    id = Column(String, primary_key=True)
    child_assignment_id = Column(Integer, nullable=False)
    schema_version = Column(Integer, nullable=False, default=1)
    delegation_kind = Column(String, nullable=False)
    parent_terminal_id = Column(String, nullable=False)
    child_terminal_id = Column(String, nullable=False)
    session_name = Column(String, nullable=True)
    child_provider = Column(String, nullable=True)
    child_agent_profile = Column(String, nullable=True)
    parent_workflow_id = Column(Integer, nullable=True)
    workflow_turn_id = Column(Integer, nullable=True)
    workflow_effect_id = Column(Integer, nullable=True)
    authorship = Column(String, nullable=False)
    status = Column(String, nullable=False)
    reason_code = Column(String, nullable=True)
    document_json = Column(Text, nullable=True)
    content_sha256 = Column(String, nullable=True)
    content_bytes = Column(Integer, nullable=True)
    capture_sha256 = Column(String, nullable=True)
    capture_bytes = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    finalized_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)
    content_purged_at = Column(DateTime, nullable=True)


class DelegationResultEventModel(Base):
    """Append-only, idempotent lifecycle evidence for a result artifact."""

    __tablename__ = "delegation_result_events"
    __table_args__ = (UniqueConstraint("event_key", name="uq_delegation_result_event_key"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    result_id = Column(String, nullable=False, index=True)
    event_key = Column(String, nullable=False)
    event_type = Column(String, nullable=False)
    actor_kind = Column(String, nullable=False)
    actor_terminal_id = Column(String, nullable=True)
    workflow_turn_id = Column(Integer, nullable=True)
    detail_json = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class DelegationResultSubmissionModel(Base):
    """One staged strict V1 document for a pre-created direct-handoff result."""

    __tablename__ = "delegation_result_submissions"

    result_id = Column(String, primary_key=True)
    child_terminal_id = Column(String, nullable=False, index=True)
    workflow_turn_id = Column(Integer, nullable=False)
    workflow_effect_id = Column(Integer, nullable=False, unique=True)
    schema_version = Column(Integer, nullable=False, default=1)
    document_json = Column(Text, nullable=False)
    content_sha256 = Column(String, nullable=False)
    content_bytes = Column(Integer, nullable=False)
    submitted_at = Column(DateTime, nullable=False, default=datetime.now)


class FlowModel(Base):
    """SQLAlchemy model for flow metadata."""

    __tablename__ = "flows"

    name = Column(String, primary_key=True)
    file_path = Column(String, nullable=False)
    schedule = Column(String, nullable=False)
    agent_profile = Column(String, nullable=False)
    provider = Column(String, nullable=False)
    script = Column(String, nullable=True)
    last_run = Column(DateTime, nullable=True)
    next_run = Column(DateTime, nullable=True)
    enabled = Column(Boolean, default=True)
    project_id = Column(String, nullable=True)
    project_name = Column(String, nullable=True)
    project_path = Column(String, nullable=True)
    project_description = Column(Text, nullable=True)


class ProjectModel(Base):
    """Normalized server-side project registry."""

    __tablename__ = "projects"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    normalized_name = Column(String, nullable=False, unique=True)
    path = Column(String, nullable=False)
    normalized_path = Column(String, nullable=False, unique=True)
    description = Column(Text, nullable=True)
    is_default = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


class RuntimeBrandingModel(Base):
    """Single persisted runtime branding row; image bytes stay outside web assets."""

    __tablename__ = "runtime_branding"

    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False, default="ThreadCells")
    subtitle = Column(String, nullable=False, default="Multi-agent control plane")
    logo_filename = Column(String, nullable=True)
    logo_hash = Column(String, nullable=True)
    logo_content_type = Column(String, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


class UsageRecordModel(Base):
    """Provider-observed operational usage for one run or durable session.

    Legacy TUI observations remain append-only. Durable provider session
    checkpoints update one stable row so a live cumulative total cannot be
    counted again on every poll or service restart.
    """

    __tablename__ = "usage_records"
    __table_args__ = (UniqueConstraint("source_run_identity", name="uq_usage_records_run"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_run_identity = Column(String, nullable=False)
    extractor = Column(String, nullable=False)
    provider = Column(String, nullable=True)
    model = Column(String, nullable=True)
    agent_profile = Column(String, nullable=True)
    terminal_id = Column(String, nullable=True, index=True)
    terminal_name = Column(String, nullable=True)
    # IDs are aggregate keys; names are immutable-at-recording snapshots for
    # display only.  In particular, tmux session names are not identities.
    session_id = Column(String, nullable=True, index=True)
    session_name = Column(String, nullable=True, index=True)
    project_id = Column(String, nullable=True, index=True)
    project_name = Column(String, nullable=True)
    project_path = Column(String, nullable=True)
    input_tokens = Column(Integer, nullable=True)
    cached_input_tokens = Column(Integer, nullable=True)
    cache_write_input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    reasoning_output_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    superseded_by_source_identity = Column(String, nullable=True, index=True)
    recorded_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.now,
        server_default=func.now(),
        onupdate=datetime.now,
    )


class ProviderUsageBindingModel(Base):
    """Exact private binding from a ThreadCells terminal to a provider session."""

    __tablename__ = "provider_usage_bindings"
    __table_args__ = (
        UniqueConstraint("provider", "provider_session_id", name="uq_provider_usage_session"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider = Column(String, nullable=False)
    provider_session_id = Column(String, nullable=False)
    terminal_id = Column(String, nullable=False, index=True)
    source = Column(String, nullable=False)
    byte_offset = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


class WorkflowModel(Base):
    """Durable semantic state for one top-level terminal workflow.

    Provider ready/final observations are deliberately not stored here as a
    terminal outcome.  They only advance durable turns while this workflow is
    OPEN; an agent or caller must explicitly choose a terminal semantic state.
    """

    __tablename__ = "workflows"

    id = Column(Integer, primary_key=True, autoincrement=True)
    root_terminal_id = Column(String, nullable=False)
    status = Column(String, nullable=False, default="open")
    no_progress_count = Column(Integer, nullable=False, default=0)
    # The one model invocation currently admitted to act for this workflow.
    # A receipt supplied by model text is not a capability by itself: it must
    # match this server-owned transport binding.
    active_turn_id = Column(Integer, nullable=True)
    terminal_reason = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)


class WorkflowTurnModel(Base):
    """One deduplicated, restart-safe continuation input for a workflow."""

    __tablename__ = "workflow_turns"
    __table_args__ = (UniqueConstraint("workflow_id", "dedupe_key", name="uq_workflow_turn"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_id = Column(Integer, nullable=False)
    kind = Column(String, nullable=False)
    dedupe_key = Column(String, nullable=False)
    payload = Column(String, nullable=True)
    state = Column(String, nullable=False, default="queued")
    inbox_message_id = Column(Integer, nullable=True, unique=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    not_before = Column(DateTime, nullable=True)
    # A lease alone is insufficient: a worker can wake after its lease was
    # reclaimed and accidentally commit the next worker's attempt.  Every
    # claimant receives a fresh opaque token and monotonically increasing
    # generation; all claim-owned transitions compare both values.
    claim_generation = Column(Integer, nullable=False, default=0)
    claim_token = Column(String, nullable=True)
    claim_expires_at = Column(DateTime, nullable=True)
    # Opaque capability used only between CAO's MCP transport and API. Unlike a
    # logical turn ID it is not model-selected or publicly enumerable.
    transport_binding = Column(String, nullable=True)
    # Provider status can move straight from a fast final to Ready/IDLE, and
    # that observation must survive a server restart. A short durable debounce
    # prevents a transient pre-processing idle frame from manufacturing a
    # successor while guaranteeing that a stable Ready OPEN workflow advances.
    provider_processing_observed_at = Column(DateTime, nullable=True)
    provider_ready_observed_at = Column(DateTime, nullable=True)
    # A stale MCP sidecar is recovered by one provider-native context
    # reinitialization. This durable timestamp is a short retry lease so two
    # workflow reconcilers cannot race the restart. The opaque provider resume
    # identity is persisted before the old process exits, so a service crash in
    # the exit/resume gap can restart the exact conversation instead of
    # guessing from provider-global recency.
    provider_reconnect_requested_at = Column(DateTime, nullable=True)
    provider_reconnect_claim_token = Column(String, nullable=True)
    provider_reconnect_resume_identity = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)


class WorkflowProviderReconnectAttemptModel(Base):
    """One bounded, durable provider resume attempt and its final outcome.

    The row is reserved before the external pane mutation.  Reconciliation
    reuses a non-terminal row instead of purchasing another provider launch,
    while a new row is possible only after the prior attempt has one durable
    outcome.  A sidecar-ready record is bound to the opaque attempt token, the
    active workflow turn, and the process identity of the newly launched MCP
    runtime.
    """

    __tablename__ = "workflow_provider_reconnect_attempts"
    __table_args__ = (
        UniqueConstraint(
            "workflow_turn_id",
            "attempt_number",
            name="uq_workflow_provider_reconnect_attempt_number",
        ),
        UniqueConstraint("attempt_token", name="uq_workflow_provider_reconnect_attempt_token"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_id = Column(Integer, nullable=False, index=True)
    workflow_turn_id = Column(Integer, nullable=False, index=True)
    root_terminal_id = Column(String, nullable=False, index=True)
    attempt_number = Column(Integer, nullable=False)
    attempt_token = Column(String, nullable=False)
    resume_identity = Column(String, nullable=True)
    state = Column(String, nullable=False, default="reserved")
    outcome_code = Column(String, nullable=True)
    runtime_generation = Column(String, nullable=True)
    sidecar_process_id = Column(Integer, nullable=True)
    sidecar_process_start_ticks = Column(Integer, nullable=True)
    output_log_device = Column(Integer, nullable=True)
    output_log_inode = Column(Integer, nullable=True)
    output_log_offset = Column(Integer, nullable=True)
    output_boundary_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    launched_at = Column(DateTime, nullable=True)
    ready_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)


class WorkflowTurnReceiptModel(Base):
    """One irreversible receiver-side admission for a stable logical turn.

    Transport may paste the same continuation more than once after a sender
    crash.  The receiving supervisor must admit model-dependent work through
    this record first; the unique pair is therefore the durable idempotency
    boundary rather than a second delivery queue.
    """

    __tablename__ = "workflow_turn_receipts"
    __table_args__ = (
        UniqueConstraint(
            "workflow_turn_id", "receiver_terminal_id", name="uq_workflow_turn_receipt"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_turn_id = Column(Integer, nullable=False)
    receiver_terminal_id = Column(String, nullable=False)
    consumed_at = Column(DateTime, nullable=False, default=datetime.now)


class WorkflowEffectModel(Base):
    """Durable, one-way gate for a privileged supervisor operation.

    A model turn and tmux cannot share a transaction.  This record therefore
    does *not* pretend that a provider invocation is exactly once.  It does
    make the CAO-owned operation behind an admitted logical turn explicit: a
    duplicate delivery observes the same row and cannot enter the operation
    again. A process death while it owns a row is intentionally
    ``indeterminate`` rather than replayed blindly. ``not_admitted`` is
    different: it records a proven pre-effect rejection and is the only state
    that may be claimed again after the transient admission condition changes.
    """

    __tablename__ = "workflow_effects"
    __table_args__ = (
        UniqueConstraint(
            "workflow_id",
            "workflow_turn_id",
            "effect_kind",
            "effect_key",
            name="uq_workflow_effect",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_id = Column(Integer, nullable=False)
    workflow_turn_id = Column(Integer, nullable=False)
    effect_kind = Column(String, nullable=False)
    effect_key = Column(String, nullable=False)
    state = Column(String, nullable=False, default="claimed")
    claim_token = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)


# Module-level singletons
DB_DIR.mkdir(parents=True, exist_ok=True)
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
_child_assignment_schema_lock = threading.Lock()
_child_assignment_schema_ready = False
_delegation_result_schema_lock = threading.Lock()
_delegation_result_schema_ready = False
_terminal_authority_schema_lock = threading.Lock()
_terminal_authority_schema_ready = False
_provider_execution_schema_lock = threading.Lock()
_provider_execution_schema_ready = False
_usage_schema_lock = threading.Lock()
_usage_schema_ready = False
_capacity_settings_schema_lock = threading.Lock()
_telegram_settings_schema_lock = threading.Lock()
_telegram_settings_schema_ready = False
_telegram_settings_schema_engine_identity: Optional[int] = None
_control_plane_schema_lock = threading.Lock()
_control_plane_schema_ready = False
_control_plane_schema_engine_identity: Optional[int] = None
_terminal_ui_projection_schema_lock = threading.Lock()
_terminal_ui_projection_schema_ready = False
_terminal_ui_projection_schema_engine_identity: Optional[int] = None

CONTROL_PLANE_SCHEMA_VERSION = 1
# Durable compatibility identifier: deployed databases already use this key.
CONTROL_PLANE_MIGRATION_RECEIPT = "threadmesh-control-plane-schema-v1"


def init_db() -> None:
    """Initialize database tables and apply schema migrations."""
    Base.metadata.create_all(bind=engine)
    _migrate_add_allowed_tools()
    _ensure_terminal_worktree_authority_schema()
    _ensure_provider_execution_schema()
    # Backfills below load the complete ChildAssignment ORM row.  SQLite's
    # create_all does not add columns to an existing table, so finish and
    # verify every additive assignment migration before any such query.
    _ensure_child_assignment_schema()
    _ensure_delegation_result_schema()
    _ensure_project_schema()
    _ensure_runtime_branding_schema()
    _ensure_usage_schema()
    _ensure_control_plane_schema()
    _ensure_telegram_settings_schema()
    _ensure_terminal_ui_projection_schema()
    from cli_agent_orchestrator.services.operations_service import _load_legacy_operations_config

    ensure_capacity_settings(_load_legacy_operations_config())


def _ensure_control_plane_schema() -> None:
    """Create registry tables and additive launch-snapshot columns idempotently."""
    global _control_plane_schema_engine_identity, _control_plane_schema_ready
    if _control_plane_schema_ready and _control_plane_schema_engine_identity == id(engine):
        return
    with _control_plane_schema_lock:
        if _control_plane_schema_ready and _control_plane_schema_engine_identity == id(engine):
            return
        for table in (
            ControlPlaneSchemaModel.__table__,
            ProviderConfigRecordModel.__table__,
            ProviderConfigRevisionModel.__table__,
            ProfileRecordModel.__table__,
            ProfileRevisionModel.__table__,
            ProfileReferenceModel.__table__,
            MigrationReceiptModel.__table__,
        ):
            table.create(bind=engine, checkfirst=True)
        with engine.begin() as connection:
            terminal_columns = {
                row[1]
                for row in connection.exec_driver_sql("PRAGMA table_info(terminals)").fetchall()
            }
            for name in (
                "profile_revision_id",
                "provider_config_revision_id",
                "launch_snapshot_json",
                "launch_snapshot_status",
            ):
                if name not in terminal_columns:
                    connection.exec_driver_sql(f"ALTER TABLE terminals ADD COLUMN {name} TEXT")
            connection.exec_driver_sql(
                "UPDATE terminals SET launch_snapshot_status = 'legacy_unavailable' "
                "WHERE launch_snapshot_status IS NULL"
            )
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_terminals_profile_revision_id "
                "ON terminals(profile_revision_id)"
            )
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_terminals_provider_config_revision_id "
                "ON terminals(provider_config_revision_id)"
            )
        with SessionLocal() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            state = db.get(ControlPlaneSchemaModel, 1)
            if state is None:
                state = ControlPlaneSchemaModel(
                    id=1,
                    schema_version=CONTROL_PLANE_SCHEMA_VERSION,
                    updated_at=datetime.now(),
                )
                db.add(state)
            elif state.schema_version > CONTROL_PLANE_SCHEMA_VERSION:
                db.rollback()
                raise RuntimeError("control-plane schema is newer than this runtime")
            elif state.schema_version < CONTROL_PLANE_SCHEMA_VERSION:
                state.schema_version = CONTROL_PLANE_SCHEMA_VERSION
                state.updated_at = datetime.now()
            if db.get(MigrationReceiptModel, CONTROL_PLANE_MIGRATION_RECEIPT) is None:
                db.add(
                    MigrationReceiptModel(
                        name=CONTROL_PLANE_MIGRATION_RECEIPT,
                        schema_version=CONTROL_PLANE_SCHEMA_VERSION,
                        detail_json=json.dumps(
                            {"schema_version": CONTROL_PLANE_SCHEMA_VERSION},
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        created_at=datetime.now(),
                    )
                )
            db.commit()
        _control_plane_schema_ready = True
        _control_plane_schema_engine_identity = id(engine)


def _ensure_provider_execution_schema() -> None:
    """Create the additive provider-turn lease ledger for rolling upgrades."""
    global _provider_execution_schema_ready
    if _provider_execution_schema_ready:
        return
    with _provider_execution_schema_lock:
        if _provider_execution_schema_ready:
            return
        ProviderExecutionLeaseModel.__table__.create(bind=engine, checkfirst=True)
        _provider_execution_schema_ready = True


CAPACITY_SETTING_KEYS = (
    "max_resident_supervisors",
    "max_provider_executions",
    "max_work_contexts",
    "max_heavy_execution_slots",
)
CAPACITY_SETTING_RANGES = {
    "max_resident_supervisors": (2, 50),
    "max_provider_executions": (1, 50),
    "max_work_contexts": (1, 50),
    "max_heavy_execution_slots": (1, 50),
}
CAPACITY_MIGRATION_RECEIPT = "capacity-settings-v1-legacy-seed"


def validate_capacity_settings(values: Mapping[str, Any]) -> Dict[str, int]:
    """Return exact canonical settings; booleans and partial payloads are invalid."""
    if set(values) != set(CAPACITY_SETTING_KEYS):
        raise ValueError("capacity settings must contain exactly the four canonical limits")
    validated: Dict[str, int] = {}
    for key in CAPACITY_SETTING_KEYS:
        value = values[key]
        minimum, maximum = CAPACITY_SETTING_RANGES[key]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} must be an integer")
        if not minimum <= value <= maximum:
            raise ValueError(f"{key} must be between {minimum} and {maximum}")
        validated[key] = value
    return validated


def _capacity_row_dict(row: CapacitySettingsModel) -> Dict[str, Any]:
    return {
        "schema_version": row.schema_version,
        **{key: int(getattr(row, key)) for key in CAPACITY_SETTING_KEYS},
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def ensure_capacity_settings(legacy_config: Mapping[str, Any]) -> Dict[str, Any]:
    """Seed canonical capacity once from the effective legacy authority."""
    seed = validate_capacity_settings({key: legacy_config[key] for key in CAPACITY_SETTING_KEYS})
    with _capacity_settings_schema_lock:
        CapacitySettingsModel.__table__.create(bind=engine, checkfirst=True)
        CapacitySettingsAuditModel.__table__.create(bind=engine, checkfirst=True)
        MigrationReceiptModel.__table__.create(bind=engine, checkfirst=True)
        OwnerLaunchGrantModel.__table__.create(bind=engine, checkfirst=True)
        OperatorSessionModel.__table__.create(bind=engine, checkfirst=True)
        with engine.begin() as connection:
            grant_columns = {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA table_info(owner_launch_grants)"
                ).fetchall()
            }
            if "scope_json" not in grant_columns:
                connection.exec_driver_sql(
                    "ALTER TABLE owner_launch_grants ADD COLUMN scope_json TEXT"
                )
        with SessionLocal() as db:
            existing = db.get(CapacitySettingsModel, 1)
            if existing is not None:
                return _capacity_row_dict(existing)
            now = datetime.now()
            row = CapacitySettingsModel(
                id=1, schema_version=1, **seed, created_at=now, updated_at=now
            )
            serialized = json.dumps(seed, sort_keys=True, separators=(",", ":"))
            db.add(row)
            db.add(
                CapacitySettingsAuditModel(
                    actor="migration",
                    reason="legacy_seed",
                    previous_json=None,
                    settings_json=serialized,
                    created_at=now,
                )
            )
            db.add(
                MigrationReceiptModel(
                    name=CAPACITY_MIGRATION_RECEIPT,
                    schema_version=1,
                    detail_json=serialized,
                    created_at=now,
                )
            )
            try:
                db.commit()
            except IntegrityError:
                # A concurrent startup won the singleton/receipt transaction.
                db.rollback()
            existing = db.get(CapacitySettingsModel, 1)
            if existing is None:
                raise RuntimeError("capacity settings migration is incomplete")
            return _capacity_row_dict(existing)


def get_capacity_settings() -> Dict[str, Any]:
    """Read the canonical capacity singleton without process-local caching."""
    with SessionLocal() as db:
        row = db.get(CapacitySettingsModel, 1)
        if row is None:
            raise RuntimeError("capacity settings are not initialized")
        return _capacity_row_dict(row)


def update_capacity_settings(
    values: Mapping[str, Any], *, actor: str, reason: str = "operator_update"
) -> Dict[str, Any]:
    """Atomically replace all four limits and append their audit record."""
    settings = validate_capacity_settings(values)
    if not actor or len(actor) > 120:
        raise ValueError("capacity settings actor is required")
    with SessionLocal() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = db.get(CapacitySettingsModel, 1)
        if row is None:
            raise RuntimeError("capacity settings are not initialized")
        previous = {key: int(getattr(row, key)) for key in CAPACITY_SETTING_KEYS}
        now = datetime.now()
        for key, value in settings.items():
            setattr(row, key, value)
        row.updated_at = now
        db.add(
            CapacitySettingsAuditModel(
                actor=actor,
                reason=reason,
                previous_json=json.dumps(previous, sort_keys=True, separators=(",", ":")),
                settings_json=json.dumps(settings, sort_keys=True, separators=(",", ":")),
                created_at=now,
            )
        )
        db.commit()
        return _capacity_row_dict(row)


def list_capacity_settings_audit(limit: int = 20) -> List[Dict[str, Any]]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("audit limit must be between 1 and 100")
    with SessionLocal() as db:
        rows = (
            db.query(CapacitySettingsAuditModel)
            .order_by(CapacitySettingsAuditModel.id.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": row.id,
                "actor": row.actor,
                "reason": row.reason,
                "previous": json.loads(row.previous_json) if row.previous_json else None,
                "settings": json.loads(row.settings_json),
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]


HOUSEKEEPING_MIGRATION_RECEIPT = "housekeeping-settings-v1-seed"


def _housekeeping_settings_dict(row: HousekeepingSettingsModel) -> Dict[str, Any]:
    return {
        "schema_version": int(row.schema_version),
        "policy": json.loads(row.policy_json),
        "schedule": json.loads(row.schedule_json),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def ensure_housekeeping_settings(seed: Mapping[str, Any]) -> Dict[str, Any]:
    """Seed the canonical P2 policy once; legacy config is never a co-authority."""
    policy = seed.get("policy")
    schedule = seed.get("schedule")
    if not isinstance(policy, Mapping) or not isinstance(schedule, Mapping):
        raise ValueError("housekeeping settings require policy and schedule objects")
    serialized = json.dumps(
        {"schema_version": 1, "policy": dict(policy), "schedule": dict(schedule)},
        sort_keys=True,
        separators=(",", ":"),
    )
    HousekeepingSettingsModel.__table__.create(bind=engine, checkfirst=True)
    HousekeepingSettingsAuditModel.__table__.create(bind=engine, checkfirst=True)
    MigrationReceiptModel.__table__.create(bind=engine, checkfirst=True)
    with SessionLocal() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        existing = db.get(HousekeepingSettingsModel, 1)
        if existing is None:
            now = datetime.now()
            existing = HousekeepingSettingsModel(
                id=1,
                schema_version=1,
                policy_json=json.dumps(dict(policy), sort_keys=True, separators=(",", ":")),
                schedule_json=json.dumps(dict(schedule), sort_keys=True, separators=(",", ":")),
                created_at=now,
                updated_at=now,
            )
            db.add(existing)
            db.add(
                HousekeepingSettingsAuditModel(
                    actor="migration",
                    reason="legacy_seed",
                    previous_json=None,
                    settings_json=serialized,
                    created_at=now,
                )
            )
            if db.get(MigrationReceiptModel, HOUSEKEEPING_MIGRATION_RECEIPT) is None:
                db.add(
                    MigrationReceiptModel(
                        name=HOUSEKEEPING_MIGRATION_RECEIPT,
                        schema_version=1,
                        detail_json=serialized,
                        created_at=now,
                    )
                )
            db.commit()
        return _housekeeping_settings_dict(existing)


def get_housekeeping_settings() -> Dict[str, Any]:
    HousekeepingSettingsModel.__table__.create(bind=engine, checkfirst=True)
    with SessionLocal() as db:
        row = db.get(HousekeepingSettingsModel, 1)
        if row is None:
            raise RuntimeError("housekeeping settings are not initialized")
        return _housekeeping_settings_dict(row)


def update_housekeeping_settings(
    values: Mapping[str, Any], *, actor: str, reason: str = "operator_update"
) -> Dict[str, Any]:
    policy = values.get("policy")
    schedule = values.get("schedule")
    if not isinstance(policy, Mapping) or not isinstance(schedule, Mapping):
        raise ValueError("housekeeping settings require policy and schedule objects")
    if not actor or len(actor) > 120:
        raise ValueError("housekeeping settings actor is required")
    with SessionLocal() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = db.get(HousekeepingSettingsModel, 1)
        if row is None:
            raise RuntimeError("housekeeping settings are not initialized")
        previous = _housekeeping_settings_dict(row)
        row.policy_json = json.dumps(dict(policy), sort_keys=True, separators=(",", ":"))
        row.schedule_json = json.dumps(dict(schedule), sort_keys=True, separators=(",", ":"))
        row.updated_at = datetime.now()
        current = {
            "schema_version": 1,
            "policy": dict(policy),
            "schedule": dict(schedule),
        }
        db.add(
            HousekeepingSettingsAuditModel(
                actor=actor,
                reason=reason,
                previous_json=json.dumps(previous, sort_keys=True, separators=(",", ":")),
                settings_json=json.dumps(current, sort_keys=True, separators=(",", ":")),
                created_at=row.updated_at,
            )
        )
        db.commit()
        return _housekeeping_settings_dict(row)


def _ensure_telegram_settings_schema() -> None:
    """Create the additive global settings and idempotency ledger once per engine."""
    global _telegram_settings_schema_engine_identity, _telegram_settings_schema_ready
    if not (
        _telegram_settings_schema_ready and _telegram_settings_schema_engine_identity == id(engine)
    ):
        with _telegram_settings_schema_lock:
            if not (
                _telegram_settings_schema_ready
                and _telegram_settings_schema_engine_identity == id(engine)
            ):
                TelegramSettingsModel.__table__.create(bind=engine, checkfirst=True)
                TelegramDeliveryModel.__table__.create(bind=engine, checkfirst=True)
                _telegram_settings_schema_ready = True
                _telegram_settings_schema_engine_identity = id(engine)

    # SessionLocal is deliberately patchable by isolated database tests. Seed
    # through the active session factory even when the process-global engine
    # has already completed its additive table migration.
    with SessionLocal() as db:
        if db.get(TelegramSettingsModel, 1) is not None:
            return
        now = datetime.now()
        db.add(
            TelegramSettingsModel(
                id=1,
                schema_version=1,
                enabled=False,
                created_at=now,
                updated_at=now,
            )
        )
        try:
            db.commit()
        except IntegrityError:
            # Concurrent startup won the singleton insert.
            db.rollback()


def _telegram_settings_dict(row: TelegramSettingsModel) -> Dict[str, Any]:
    return {
        "schema_version": int(row.schema_version),
        "enabled": bool(row.enabled),
        "chat_id": row.chat_id,
        "message_thread_id": row.message_thread_id,
        "last_result": row.last_result,
        "last_result_at": row.last_result_at.isoformat() if row.last_result_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def get_telegram_settings() -> Dict[str, Any]:
    _ensure_telegram_settings_schema()
    with SessionLocal() as db:
        row = db.get(TelegramSettingsModel, 1)
        if row is None:
            raise RuntimeError("Telegram settings are not initialized")
        return _telegram_settings_dict(row)


def update_telegram_settings(
    *, enabled: bool, chat_id: Optional[str], message_thread_id: Optional[int]
) -> Dict[str, Any]:
    _ensure_telegram_settings_schema()
    with SessionLocal() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = db.get(TelegramSettingsModel, 1)
        if row is None:
            raise RuntimeError("Telegram settings are not initialized")
        row.enabled = enabled
        row.chat_id = chat_id
        row.message_thread_id = message_thread_id
        row.updated_at = datetime.now()
        db.commit()
        return _telegram_settings_dict(row)


def record_telegram_result(result: str) -> Dict[str, Any]:
    if result not in {
        "connection_ok",
        "connection_failed",
        "test_sent",
        "test_failed",
        "not_configured",
    }:
        raise ValueError("Invalid Telegram result state")
    _ensure_telegram_settings_schema()
    with SessionLocal() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = db.get(TelegramSettingsModel, 1)
        if row is None:
            raise RuntimeError("Telegram settings are not initialized")
        row.last_result = result
        row.last_result_at = datetime.now()
        db.commit()
        return _telegram_settings_dict(row)


def get_workflow_notification_context(
    root_terminal_id: str, workflow_id: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    """Return safe context for the exact lifecycle transition when identified."""
    _ensure_workflow_schema()
    _ensure_child_assignment_schema()
    with SessionLocal() as db:
        query = db.query(WorkflowModel).filter(WorkflowModel.root_terminal_id == root_terminal_id)
        if workflow_id is not None:
            query = query.filter(WorkflowModel.id == workflow_id)
        workflow = query.order_by(WorkflowModel.id.desc()).first()
        if workflow is None:
            return None
        terminal = db.get(TerminalModel, root_terminal_id)
        return {
            "workflow_id": int(workflow.id),
            "workflow_status": str(workflow.status),
            "root_terminal_id": root_terminal_id,
            "session_name": terminal.tmux_session if terminal is not None else None,
            "project_name": terminal.project_name if terminal is not None else None,
            # Delegation provenance is immutable for notification policy. A
            # failure cleanup may cancel the relation before delivery runs.
            "delegated_child": bool(
                db.query(ChildAssignmentModel.id)
                .filter(ChildAssignmentModel.child_terminal_id == root_terminal_id)
                .first()
            ),
        }


def claim_telegram_delivery(
    *, event_key: str, event_kind: str, workflow_id: int, root_terminal_id: str
) -> bool:
    """Claim one lifecycle event permanently; indeterminate sends are never replayed."""
    if not event_key or len(event_key) > 240:
        raise ValueError("Invalid Telegram event key")
    _ensure_telegram_settings_schema()
    try:
        with SessionLocal() as db:
            db.add(
                TelegramDeliveryModel(
                    event_key=event_key,
                    event_kind=event_kind,
                    workflow_id=workflow_id,
                    root_terminal_id=root_terminal_id,
                    state="claimed",
                    attempt_count=1,
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                )
            )
            db.commit()
        return True
    except IntegrityError:
        return False


def finish_telegram_delivery(event_key: str, state: str, error_code: Optional[str] = None) -> bool:
    if state not in {"sent", "failed", "skipped"}:
        raise ValueError("Invalid Telegram delivery state")
    _ensure_telegram_settings_schema()
    with SessionLocal() as db:
        updated = (
            db.query(TelegramDeliveryModel)
            .filter(
                TelegramDeliveryModel.event_key == event_key,
                TelegramDeliveryModel.state == "claimed",
            )
            .update(
                {
                    TelegramDeliveryModel.state: state,
                    TelegramDeliveryModel.error_code: error_code,
                    TelegramDeliveryModel.updated_at: datetime.now(),
                },
                synchronize_session=False,
            )
        )
        if updated:
            db.commit()
        return updated == 1


def issue_owner_launch_grant(
    *,
    launch_id: str,
    agent_profile: str,
    provider: str,
    canonical_worktree: str,
    requested_session_name: Optional[str],
    issued_by: str = "local_operator_cli",
    ttl_seconds: int = 60,
    grant_scope: Optional[Mapping[str, Any]] = None,
) -> str:
    """Persist only a token digest and return plaintext once to the issuing process."""
    if not 10 <= ttl_seconds <= 300:
        raise ValueError("owner grant ttl must be between 10 and 300 seconds")
    if not launch_id or len(launch_id) > 128:
        raise ValueError("launch_id is required")
    if not canonical_worktree.startswith("/"):
        raise ValueError("owner grant worktree must be absolute")
    token = secrets.token_urlsafe(32)
    now = datetime.now()
    serialized_scope = json.dumps(dict(grant_scope or {}), sort_keys=True, separators=(",", ":"))
    with SessionLocal() as db:
        db.add(
            OwnerLaunchGrantModel(
                id=str(uuid.uuid4()),
                token_sha256=hashlib.sha256(token.encode("utf-8", "strict")).hexdigest(),
                launch_id=launch_id,
                agent_profile=agent_profile,
                provider=provider,
                canonical_worktree=canonical_worktree,
                requested_session_name=requested_session_name,
                scope_json=serialized_scope,
                issued_by=issued_by,
                created_at=now,
                expires_at=now + timedelta(seconds=ttl_seconds),
            )
        )
        db.commit()
    return token


def create_operator_session(ttl_seconds: int = 300) -> str:
    if not 30 <= ttl_seconds <= 900:
        raise ValueError("operator session ttl must be between 30 and 900 seconds")
    token = secrets.token_urlsafe(32)
    now = datetime.now()
    with SessionLocal() as db:
        db.add(
            OperatorSessionModel(
                id=str(uuid.uuid4()),
                token_sha256=hashlib.sha256(token.encode("utf-8", "strict")).hexdigest(),
                created_at=now,
                expires_at=now + timedelta(seconds=ttl_seconds),
            )
        )
        db.commit()
    return token


def authenticate_operator_session(token: str, now: Optional[datetime] = None) -> Optional[str]:
    session = get_operator_session_status(token, now=now)
    return str(session["id"]) if session is not None else None


def get_operator_session_status(
    token: str, now: Optional[datetime] = None
) -> Optional[Dict[str, Any]]:
    """Return non-secret status for one live operator session cookie."""
    digest = hashlib.sha256(token.encode("utf-8", "strict")).hexdigest()
    now = now or datetime.now()
    with SessionLocal() as db:
        row = db.query(OperatorSessionModel).filter_by(token_sha256=digest).first()
        if row is None or row.revoked_at is not None or row.expires_at < now:
            return None
        return {"id": str(row.id), "expires_at": row.expires_at}


def revoke_operator_session(token: str) -> bool:
    digest = hashlib.sha256(token.encode("utf-8", "strict")).hexdigest()
    with SessionLocal() as db:
        changed = (
            db.query(OperatorSessionModel)
            .filter(
                OperatorSessionModel.token_sha256 == digest,
                OperatorSessionModel.revoked_at.is_(None),
            )
            .update({OperatorSessionModel.revoked_at: datetime.now()}, synchronize_session=False)
        )
        db.commit()
        return changed == 1


def validate_owner_launch_grant(
    token: str,
    *,
    launch_id: str,
    agent_profile: str,
    provider: str,
    canonical_worktree: str,
    requested_session_name: Optional[str],
    grant_scope: Optional[Mapping[str, Any]] = None,
    now: Optional[datetime] = None,
) -> bool:
    """Side-effect-free preflight; terminal insertion performs the real consume."""
    digest = hashlib.sha256(token.encode("utf-8", "strict")).hexdigest()
    now = now or datetime.now()
    serialized_scope = json.dumps(dict(grant_scope or {}), sort_keys=True, separators=(",", ":"))
    with SessionLocal() as db:
        row = db.query(OwnerLaunchGrantModel).filter_by(token_sha256=digest).first()
        return bool(
            row
            and row.consumed_at is None
            and row.expires_at >= now
            and hmac.compare_digest(row.launch_id, launch_id)
            and hmac.compare_digest(row.agent_profile, agent_profile)
            and hmac.compare_digest(row.provider, provider)
            and hmac.compare_digest(row.canonical_worktree, canonical_worktree)
            and row.requested_session_name == requested_session_name
            and hmac.compare_digest(row.scope_json or "{}", serialized_scope)
        )


def _ensure_usage_schema() -> None:
    """Create the additive usage ledger and durable P2 identity fields.

    A legacy usage row can receive a session identity only when its original
    terminal metadata still exists.  Deleted terminal rows are deliberately
    left without one: a display name alone cannot distinguish old session
    lifetimes and must not be guessed into a coalesced history.
    """
    global _usage_schema_ready
    if _usage_schema_ready:
        return
    with _usage_schema_lock:
        if _usage_schema_ready:
            return
        try:
            UsageRecordModel.__table__.create(bind=engine, checkfirst=True)
            ProviderUsageBindingModel.__table__.create(bind=engine, checkfirst=True)
            _migrate_usage_identity_columns()
            _usage_schema_ready = True
        except Exception as exc:
            # This migration is only reached from non-execution paths.  Capture
            # callers still contain the failure so provider completion stays safe.
            logger.warning("Usage ledger schema migration failed: %s", exc)
            raise RuntimeError("Usage ledger schema migration is incomplete") from exc


def _migrate_usage_identity_columns() -> None:
    """Apply the additive P2 identity migration and exact-only backfill."""
    with engine.begin() as connection:
        tables = {
            row[0]
            for row in connection.exec_driver_sql("SELECT name FROM sqlite_master").fetchall()
        }
        usage_columns = {
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(usage_records)").fetchall()
        }
        if "terminal_name" not in usage_columns:
            connection.exec_driver_sql("ALTER TABLE usage_records ADD COLUMN terminal_name TEXT")
        if "session_id" not in usage_columns:
            connection.exec_driver_sql("ALTER TABLE usage_records ADD COLUMN session_id TEXT")
        if "provider" not in usage_columns:
            connection.exec_driver_sql("ALTER TABLE usage_records ADD COLUMN provider TEXT")
        for column_name in (
            "cache_write_input_tokens",
            "reasoning_output_tokens",
            "superseded_by_source_identity",
            "updated_at",
        ):
            if column_name not in usage_columns:
                column_type = (
                    "DATETIME"
                    if column_name == "updated_at"
                    else (
                        "INTEGER"
                        if column_name in {"cache_write_input_tokens", "reasoning_output_tokens"}
                        else "TEXT"
                    )
                )
                connection.exec_driver_sql(
                    f"ALTER TABLE usage_records ADD COLUMN {column_name} {column_type}"
                )
        connection.exec_driver_sql(
            "UPDATE usage_records SET updated_at = recorded_at WHERE updated_at IS NULL"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_usage_records_superseded_by "
            "ON usage_records(superseded_by_source_identity)"
        )

        if "terminals" not in tables:
            return

        terminal_columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(terminals)").fetchall()
        }
        if "session_id" not in terminal_columns:
            connection.exec_driver_sql("ALTER TABLE terminals ADD COLUMN session_id TEXT")

        # Some pre-project databases have only a minimal terminals table. It
        # proves no launch/session membership, so the schema is upgraded but
        # no historical identity is invented.
        if not {"tmux_session", "tmux_window"}.issubset(terminal_columns):
            return

        # Existing live terminal rows are authoritative membership evidence.
        # Derive one stable opaque identity per extant tmux session; deleted
        # sessions have no equivalent primary evidence and are intentionally
        # not reconstructed from their reusable display names.
        sessions = connection.exec_driver_sql(
            "SELECT DISTINCT tmux_session FROM terminals WHERE session_id IS NULL"
        ).fetchall()
        for (session_name,) in sessions:
            terminal_ids = [
                str(row[0])
                for row in connection.exec_driver_sql(
                    "SELECT id FROM terminals WHERE tmux_session = ? ORDER BY id", (session_name,)
                ).fetchall()
            ]
            identity = (
                "legacy-session-v2:"
                + hashlib.sha256("\x1f".join(terminal_ids).encode("utf-8")).hexdigest()
            )
            connection.exec_driver_sql(
                "UPDATE terminals SET session_id = ? "
                "WHERE tmux_session = ? AND session_id IS NULL",
                (identity, session_name),
            )

        # Only copy immutable-at-launch terminal snapshots when the exact
        # terminal row and its session display name still match the ledger row.
        # An absent legacy name is not evidence of membership: tmux session
        # names are reusable, so it remains an honest historical attribution
        # gap rather than being joined to a current lifetime.
        rows = connection.exec_driver_sql(
            "SELECT u.id, u.terminal_id, u.session_name, t.session_id, t.tmux_window "
            "FROM usage_records AS u JOIN terminals AS t ON t.id = u.terminal_id "
            "WHERE u.session_id IS NULL AND t.session_id IS NOT NULL "
            "AND u.session_name = t.tmux_session"
        ).fetchall()
        for record_id, _terminal_id, _session_name, session_id, terminal_name in rows:
            connection.exec_driver_sql(
                "UPDATE usage_records SET session_id = ?, terminal_name = ? WHERE id = ?",
                (session_id, terminal_name, record_id),
            )


def _ensure_project_schema() -> None:
    """Apply additive Projects P1 migrations without rewriting launch history."""
    import sqlite3

    from cli_agent_orchestrator.constants import DATABASE_FILE

    ProjectModel.__table__.create(bind=engine, checkfirst=True)
    try:
        conn = sqlite3.connect(str(DATABASE_FILE))
        for table_name, columns in {
            "projects": {
                "normalized_name": "TEXT",
                "normalized_path": "TEXT",
                "description": "TEXT",
                "is_default": "BOOLEAN NOT NULL DEFAULT 0",
                "created_at": "DATETIME",
                "updated_at": "DATETIME",
            },
            "terminals": {
                "project_id": "TEXT",
                "project_name": "TEXT",
                "project_path": "TEXT",
                "project_description": "TEXT",
            },
            "flows": {
                "project_id": "TEXT",
                "project_name": "TEXT",
                "project_path": "TEXT",
                "project_description": "TEXT",
            },
        }.items():
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})")}
            for column, definition in columns.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column} {definition}")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_projects_normalized_name ON projects(normalized_name)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_projects_normalized_path ON projects(normalized_path)"
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Projects P1 schema migration failed: %s", exc)
        raise RuntimeError("Projects P1 schema migration is incomplete") from exc


def _ensure_runtime_branding_schema() -> None:
    """Create additive runtime branding metadata without involving web/public."""
    RuntimeBrandingModel.__table__.create(bind=engine, checkfirst=True)


def record_usage_observation(
    observation: UsageObservation,
    *,
    provider: Optional[str],
    agent_profile: Optional[str],
    terminal_id: Optional[str],
    terminal_name: Optional[str],
    session_id: Optional[str],
    session_name: Optional[str],
    project_id: Optional[str],
    project_name: Optional[str],
    project_path: Optional[str],
) -> bool:
    """Append one observation, returning false for duplicates or storage failure.

    The source identity is calculated by the provider extractor from the actual
    completed invocation surface.  The unique constraint makes repeated status
    polls harmless without turning this telemetry path into a lifecycle gate.
    """
    try:
        _ensure_usage_schema()
        with SessionLocal() as db:
            db.add(
                UsageRecordModel(
                    source_run_identity=observation.source_run_identity,
                    extractor=observation.extractor,
                    provider=provider,
                    model=observation.model,
                    agent_profile=agent_profile,
                    terminal_id=terminal_id,
                    terminal_name=terminal_name,
                    session_id=session_id,
                    session_name=session_name,
                    project_id=project_id,
                    project_name=project_name,
                    project_path=project_path,
                    input_tokens=observation.input_tokens,
                    cached_input_tokens=observation.cached_input_tokens,
                    cache_write_input_tokens=observation.cache_write_input_tokens,
                    output_tokens=observation.output_tokens,
                    reasoning_output_tokens=observation.reasoning_output_tokens,
                    total_tokens=observation.total_tokens,
                )
            )
            db.commit()
        return True
    except IntegrityError:
        # A completion may be observed by several status polls.  The original
        # append remains authoritative and duplicate observation is expected.
        return False
    except Exception as exc:
        logger.warning("Usage observation persistence skipped: %s", exc)
        return False


def bind_provider_usage_session(
    *, provider: str, provider_session_id: str, terminal_id: str, source: str
) -> bool:
    """Persist one exact provider-session binding without reassigning it.

    A provider session may have only one ThreadCells owner. A terminal may own
    several provider sessions when the provider creates native subagents.
    """
    if not all(
        isinstance(value, str) and value.strip()
        for value in (provider, provider_session_id, terminal_id, source)
    ):
        return False
    _ensure_usage_schema()
    with SessionLocal() as db:
        existing = (
            db.query(ProviderUsageBindingModel)
            .filter(
                ProviderUsageBindingModel.provider == provider,
                ProviderUsageBindingModel.provider_session_id == provider_session_id,
            )
            .first()
        )
        if existing is not None:
            return hmac.compare_digest(str(existing.terminal_id), terminal_id)
        db.add(
            ProviderUsageBindingModel(
                provider=provider,
                provider_session_id=provider_session_id,
                terminal_id=terminal_id,
                source=source,
                byte_offset=0,
            )
        )
        try:
            db.commit()
            return True
        except IntegrityError:
            db.rollback()
            existing = (
                db.query(ProviderUsageBindingModel)
                .filter(
                    ProviderUsageBindingModel.provider == provider,
                    ProviderUsageBindingModel.provider_session_id == provider_session_id,
                )
                .first()
            )
            return bool(existing and hmac.compare_digest(str(existing.terminal_id), terminal_id))


def list_provider_usage_bindings(
    *, terminal_id: Optional[str] = None, provider: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Return private provider bindings for usage ingestion, never for the API."""
    _ensure_usage_schema()
    with SessionLocal() as db:
        query = db.query(ProviderUsageBindingModel)
        if terminal_id is not None:
            query = query.filter(ProviderUsageBindingModel.terminal_id == terminal_id)
        if provider is not None:
            query = query.filter(ProviderUsageBindingModel.provider == provider)
        rows = query.order_by(ProviderUsageBindingModel.id.asc()).all()
        return [
            {
                "id": int(row.id),
                "provider": str(row.provider),
                "provider_session_id": str(row.provider_session_id),
                "terminal_id": str(row.terminal_id),
                "source": str(row.source),
                "byte_offset": int(row.byte_offset or 0),
            }
            for row in rows
        ]


def record_provider_usage_checkpoint(
    observation: Optional[UsageObservation],
    *,
    provider: str,
    provider_session_id: str,
    terminal_id: str,
    terminal_name: Optional[str],
    session_id: Optional[str],
    session_name: Optional[str],
    agent_profile: Optional[str],
    project_id: Optional[str],
    project_name: Optional[str],
    project_path: Optional[str],
    next_byte_offset: int,
) -> str:
    """Atomically advance a provider cursor and upsert its cumulative snapshot.

    The cursor and totals commit together. Replayed reads therefore either
    update the same stable row or advance from the previous durable boundary;
    neither process restarts nor concurrent status polls can double-count it.
    """
    if next_byte_offset < 0:
        return "invalid"
    _ensure_usage_schema()
    identity = (
        "provider_session_v1:"
        + hashlib.sha256(f"{provider}\0{provider_session_id}".encode("utf-8", "strict")).hexdigest()
    )
    for attempt in range(2):
        with SessionLocal() as db:
            binding = (
                db.query(ProviderUsageBindingModel)
                .filter(
                    ProviderUsageBindingModel.provider == provider,
                    ProviderUsageBindingModel.provider_session_id == provider_session_id,
                    ProviderUsageBindingModel.terminal_id == terminal_id,
                )
                .first()
            )
            if binding is None:
                return "unbound"
            current_offset = int(binding.byte_offset or 0)
            if next_byte_offset < current_offset:
                return "stale"

            record = (
                db.query(UsageRecordModel)
                .filter(UsageRecordModel.source_run_identity == identity)
                .first()
            )
            if observation is not None:
                token_fields = (
                    "input_tokens",
                    "cached_input_tokens",
                    "cache_write_input_tokens",
                    "output_tokens",
                    "reasoning_output_tokens",
                    "total_tokens",
                )
                values = {name: getattr(observation, name) for name in token_fields}
                if any(
                    value is not None
                    and (isinstance(value, bool) or not isinstance(value, int) or value < 0)
                    for value in values.values()
                ):
                    return "invalid"
                if record is not None and any(
                    getattr(record, name) is not None
                    and value is not None
                    and value < getattr(record, name)
                    for name, value in values.items()
                ):
                    return "regressed"
                if record is None:
                    record = UsageRecordModel(
                        source_run_identity=identity,
                        extractor=observation.extractor,
                        provider=provider,
                        model=observation.model,
                        agent_profile=agent_profile,
                        terminal_id=terminal_id,
                        terminal_name=terminal_name,
                        session_id=session_id,
                        session_name=session_name,
                        project_id=project_id,
                        project_name=project_name,
                        project_path=project_path,
                        **values,
                    )
                    db.add(record)
                else:
                    record.extractor = observation.extractor
                    if observation.model:
                        record.model = observation.model
                    for name, value in values.items():
                        if value is not None:
                            setattr(record, name, value)
                    record.updated_at = datetime.now()
                # The cumulative durable Codex snapshot includes every TUI
                # completion for this exact terminal. Retain old rows as audit
                # evidence but exclude them from aggregates exactly once.
                if provider == "codex":
                    (
                        db.query(UsageRecordModel)
                        .filter(
                            UsageRecordModel.terminal_id == terminal_id,
                            UsageRecordModel.extractor.like("codex_tui_completion%"),
                            UsageRecordModel.superseded_by_source_identity.is_(None),
                        )
                        .update(
                            {UsageRecordModel.superseded_by_source_identity: identity},
                            synchronize_session=False,
                        )
                    )
            binding.byte_offset = next_byte_offset
            binding.updated_at = datetime.now()
            try:
                db.commit()
                return "updated" if observation is not None else "advanced"
            except IntegrityError:
                db.rollback()
                if attempt == 0:
                    continue
                return "duplicate"
    return "duplicate"


def _usage_aggregate_rows(group_column: Any = None) -> List[Dict[str, Any]]:
    """Project nullable provider telemetry without replacing absent values by zero."""
    with SessionLocal() as db:
        aggregate_columns = [
            func.count(UsageRecordModel.id).label("provider_run_count"),
            func.sum(UsageRecordModel.input_tokens).label("input_tokens"),
            func.sum(UsageRecordModel.cached_input_tokens).label("cached_input_tokens"),
            func.sum(UsageRecordModel.cache_write_input_tokens).label("cache_write_input_tokens"),
            func.sum(UsageRecordModel.output_tokens).label("output_tokens"),
            func.sum(UsageRecordModel.reasoning_output_tokens).label("reasoning_output_tokens"),
            func.sum(UsageRecordModel.total_tokens).label("total_tokens"),
        ]
        if group_column is None:
            row = (
                db.query(*aggregate_columns)
                .filter(UsageRecordModel.superseded_by_source_identity.is_(None))
                .one()
            )
            return [_usage_row(row, None)]
        rows = (
            db.query(group_column, *aggregate_columns)
            .filter(UsageRecordModel.superseded_by_source_identity.is_(None))
            .group_by(group_column)
            .order_by(group_column)
            .all()
        )
        return [_usage_row(row, row[0]) for row in rows]


def _top_usage_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep the UI projection concise without treating absent totals as zero."""

    def order(row: Dict[str, Any]) -> tuple[Any, ...]:
        total = row["total_tokens"]
        identity = str(row.get("id") or "")
        # Reported totals rank first.  When telemetry omits totals, provider run
        # count and then the stable group identity make the fallback predictable.
        if total is not None:
            return (0, -total, -row["provider_run_count"], identity)
        return (1, 0, -row["provider_run_count"], identity)

    return sorted(rows, key=order)[:10]


def _usage_labeled_rows(
    identity_column: Any,
    label_column: Any,
    unknown_label: str,
    *,
    exclude_missing_identity: bool = False,
) -> List[Dict[str, Any]]:
    """Aggregate ledger identities and labels without reading mutable live rows."""
    rows = _usage_aggregate_rows(identity_column)
    labels: Dict[Optional[str], str] = {}
    with SessionLocal() as db:
        metadata_rows = (
            db.query(
                identity_column,
                label_column,
            )
            .filter(UsageRecordModel.superseded_by_source_identity.is_(None))
            .order_by(
                UsageRecordModel.recorded_at.desc(),
                UsageRecordModel.id.desc(),
            )
            .all()
        )
    for identity, label in metadata_rows:
        if (
            identity
            and str(identity).strip()
            and identity not in labels
            and label
            and str(label).strip()
        ):
            labels[identity] = str(label).strip()
    if exclude_missing_identity:
        rows = [row for row in rows if row.get("id")]
    for row in rows:
        row["label"] = labels.get(row.get("id"), unknown_label)
    return _top_usage_rows(rows)


def _usage_project_rows() -> List[Dict[str, Any]]:
    """Aggregate by immutable ID and prefer the current registered title.

    Ledger snapshots remain the fallback for removed projects, but a rename must
    never fragment one project's statistics or leave the UI on a stale title.
    """
    rows = _usage_labeled_rows(
        UsageRecordModel.project_id, UsageRecordModel.project_name, "Unknown project"
    )
    project_ids = {str(row["id"]) for row in rows if row.get("id")}
    if not project_ids or not inspect(engine).has_table(ProjectModel.__tablename__):
        return rows
    with SessionLocal() as db:
        live_names = {
            str(project_id): str(name).strip()
            for project_id, name in (
                db.query(ProjectModel.id, ProjectModel.name)
                .filter(ProjectModel.id.in_(project_ids))
                .all()
            )
            if name and str(name).strip()
        }
    for row in rows:
        if row.get("id") in live_names:
            row["label"] = live_names[str(row["id"])]
    return rows


def _usage_terminal_rows() -> List[Dict[str, Any]]:
    """Attach terminal launch names from the immutable usage ledger."""
    return _usage_labeled_rows(
        UsageRecordModel.terminal_id, UsageRecordModel.terminal_name, "Unknown terminal"
    )


def _usage_identity_rows(identity_column: Any, unknown_label: str) -> List[Dict[str, Any]]:
    """Aggregate a self-labeling immutable dimension such as provider/profile."""
    rows = _usage_aggregate_rows(identity_column)
    for row in rows:
        identity = row.get("id")
        row["label"] = (
            str(identity).strip() if identity and str(identity).strip() else unknown_label
        )
    return _top_usage_rows(rows)


def _usage_session_rows() -> List[Dict[str, Any]]:
    """Project durable lifetimes plus one visible row per unreconciled legacy record."""
    rows = _usage_labeled_rows(
        UsageRecordModel.session_id,
        UsageRecordModel.session_name,
        "Unknown session",
        exclude_missing_identity=True,
    )
    # A legacy session name is useful display evidence but not an aggregate
    # identity. Keep each append-only row separate so two old lifetimes with
    # the same reusable tmux name can never be silently coalesced.
    with SessionLocal() as db:
        legacy_rows = (
            db.query(
                UsageRecordModel.id,
                UsageRecordModel.session_name,
                UsageRecordModel.input_tokens,
                UsageRecordModel.cached_input_tokens,
                UsageRecordModel.cache_write_input_tokens,
                UsageRecordModel.output_tokens,
                UsageRecordModel.reasoning_output_tokens,
                UsageRecordModel.total_tokens,
            )
            .filter(
                UsageRecordModel.session_id.is_(None),
                UsageRecordModel.superseded_by_source_identity.is_(None),
            )
            .order_by(UsageRecordModel.recorded_at.desc(), UsageRecordModel.id.desc())
            .all()
        )
    for (
        record_id,
        session_name,
        input_tokens,
        cached_input_tokens,
        cache_write_input_tokens,
        output_tokens,
        reasoning_output_tokens,
        total_tokens,
    ) in legacy_rows:
        label = (
            str(session_name).strip()
            if session_name and str(session_name).strip()
            else "Unknown session"
        )
        row = {
            "id": f"legacy-session-record:{record_id}",
            "provider_run_count": 1,
            "input_tokens": int(input_tokens) if input_tokens is not None else None,
            "cached_input_tokens": (
                int(cached_input_tokens) if cached_input_tokens is not None else None
            ),
            "cache_write_input_tokens": (
                int(cache_write_input_tokens) if cache_write_input_tokens is not None else None
            ),
            "output_tokens": int(output_tokens) if output_tokens is not None else None,
            "reasoning_output_tokens": (
                int(reasoning_output_tokens) if reasoning_output_tokens is not None else None
            ),
            "total_tokens": int(total_tokens) if total_tokens is not None else None,
        }
        row["label"] = label
        row["legacy"] = True
        rows.append(row)
    return _top_usage_rows(rows)


def _usage_row(row: Any, identity: Optional[str]) -> Dict[str, Any]:
    values = {
        "provider_run_count": row.provider_run_count,
        "input_tokens": row.input_tokens,
        "cached_input_tokens": row.cached_input_tokens,
        "cache_write_input_tokens": row.cache_write_input_tokens,
        "output_tokens": row.output_tokens,
        "reasoning_output_tokens": row.reasoning_output_tokens,
        "total_tokens": row.total_tokens,
    }
    result = {key: int(value) if value is not None else None for key, value in values.items()}
    if identity is not None:
        result["id"] = identity
    return result


def get_usage_statistics() -> Dict[str, Any]:
    """Return deletion-independent truthful usage projections."""
    _ensure_usage_schema()
    return {
        "global": _usage_aggregate_rows()[0],
        "terminals": _usage_terminal_rows(),
        "sessions": _usage_session_rows(),
        "projects": _usage_project_rows(),
        "providers": _usage_identity_rows(UsageRecordModel.provider, "Unknown provider"),
        "profiles": _usage_identity_rows(UsageRecordModel.agent_profile, "Unknown profile"),
    }


def _ensure_workflow_schema() -> None:
    """Create additive F13 tables without altering existing runtime data."""
    WorkflowModel.__table__.create(bind=engine, checkfirst=True)
    WorkflowTurnModel.__table__.create(bind=engine, checkfirst=True)
    WorkflowProviderReconnectAttemptModel.__table__.create(bind=engine, checkfirst=True)
    WorkflowTurnReceiptModel.__table__.create(bind=engine, checkfirst=True)
    WorkflowEffectModel.__table__.create(bind=engine, checkfirst=True)
    _migrate_workflow_turn_columns()


def _migrate_workflow_turn_columns() -> None:
    """Add F13 claim/admission fields without rewriting runtime data."""
    import sqlite3

    from cli_agent_orchestrator.constants import DATABASE_FILE

    try:
        conn = sqlite3.connect(str(DATABASE_FILE))
        workflow_columns = {row[1] for row in conn.execute("PRAGMA table_info(workflows)")}
        if "active_turn_id" not in workflow_columns:
            conn.execute("ALTER TABLE workflows ADD COLUMN active_turn_id INTEGER")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(workflow_turns)")}
        if "claim_generation" not in columns:
            conn.execute(
                "ALTER TABLE workflow_turns "
                "ADD COLUMN claim_generation INTEGER NOT NULL DEFAULT 0"
            )
        if "claim_token" not in columns:
            conn.execute("ALTER TABLE workflow_turns ADD COLUMN claim_token TEXT")
        if "claim_expires_at" not in columns:
            conn.execute("ALTER TABLE workflow_turns ADD COLUMN claim_expires_at DATETIME")
        if "transport_binding" not in columns:
            conn.execute("ALTER TABLE workflow_turns ADD COLUMN transport_binding TEXT")
        if "provider_processing_observed_at" not in columns:
            conn.execute(
                "ALTER TABLE workflow_turns " "ADD COLUMN provider_processing_observed_at DATETIME"
            )
        if "provider_ready_observed_at" not in columns:
            conn.execute(
                "ALTER TABLE workflow_turns ADD COLUMN provider_ready_observed_at DATETIME"
            )
        if "provider_reconnect_requested_at" not in columns:
            conn.execute(
                "ALTER TABLE workflow_turns ADD COLUMN provider_reconnect_requested_at DATETIME"
            )
        if "provider_reconnect_claim_token" not in columns:
            conn.execute(
                "ALTER TABLE workflow_turns ADD COLUMN provider_reconnect_claim_token TEXT"
            )
        if "provider_reconnect_resume_identity" not in columns:
            conn.execute(
                "ALTER TABLE workflow_turns " "ADD COLUMN provider_reconnect_resume_identity TEXT"
            )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Workflow-turn schema migration failed: %s", exc)


def _ensure_child_assignment_schema() -> None:
    """Make a rolling source/runtime upgrade safe before the next API startup."""
    global _child_assignment_schema_ready
    if _child_assignment_schema_ready:
        return
    with _child_assignment_schema_lock:
        if not _child_assignment_schema_ready:
            ChildAssignmentModel.__table__.create(bind=engine, checkfirst=True)
            if not _migrate_child_assignment_columns():
                raise RuntimeError("Child-assignment schema migration is incomplete")
            _child_assignment_schema_ready = True


def _ensure_delegation_result_schema() -> None:
    """Create the additive F14 schema and non-destructively backfill it once."""
    global _delegation_result_schema_ready
    if _delegation_result_schema_ready:
        return
    with _delegation_result_schema_lock:
        if _delegation_result_schema_ready:
            return
        # Keep this consumer safe when called directly by command-only
        # processes as well as through init_db.
        _ensure_child_assignment_schema()
        DelegationResultModel.__table__.create(bind=engine, checkfirst=True)
        DelegationResultEventModel.__table__.create(bind=engine, checkfirst=True)
        DelegationResultSubmissionModel.__table__.create(bind=engine, checkfirst=True)
        _migrate_terminal_auth_token_column()
        _migrate_inbox_result_columns()
        _backfill_delegation_results()
        _delegation_result_schema_ready = True


def _migrate_terminal_auth_token_column() -> None:
    """Add the nullable C2 token digest without exposing or backfilling tokens."""
    import sqlite3

    from cli_agent_orchestrator.constants import DATABASE_FILE

    try:
        conn = sqlite3.connect(str(DATABASE_FILE))
        columns = {row[1] for row in conn.execute("PRAGMA table_info(terminals)")}
        if "auth_token_sha256" not in columns:
            conn.execute("ALTER TABLE terminals ADD COLUMN auth_token_sha256 TEXT")
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Terminal auth-token schema migration failed: %s", exc)


def _migrate_inbox_result_columns() -> None:
    """Add F14 Inbox linkage without rewriting existing message bodies."""
    import sqlite3

    from cli_agent_orchestrator.constants import DATABASE_FILE

    try:
        conn = sqlite3.connect(str(DATABASE_FILE))
        columns = {row[1] for row in conn.execute("PRAGMA table_info(inbox)")}
        if "result_id" not in columns:
            conn.execute("ALTER TABLE inbox ADD COLUMN result_id TEXT")
        if "kind" not in columns:
            conn.execute("ALTER TABLE inbox ADD COLUMN kind TEXT NOT NULL DEFAULT 'message'")
        if "superseded_at" not in columns:
            conn.execute("ALTER TABLE inbox ADD COLUMN superseded_at DATETIME")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_inbox_result_id ON inbox(result_id)")
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Delegation-result Inbox migration failed: %s", exc)


def _legacy_document(text: str) -> str:
    return json.dumps({"body_markdown": text, "format": "legacy_text"}, sort_keys=True)


_RESULT_V1_MARKER = "CAO_RESULT_V1"
_TERMINAL_CONTROL_PATTERN = r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[()][0-2AB])"
_RAW_C0_CONTROL_PATTERN = r"[\x00-\x08\x0b-\x1f\x7f]"
_PRESENTATION_BULLET_PATTERN = re.compile(r"^[^\S\n]*•[^\S\n]?")
_CODEX_DISPLAY_WRAP_LINE_PATTERN = re.compile(r"^  •(?: |$)")
_CODEX_WORKED_FOR_FOOTER_PATTERN = re.compile(r"^─ Worked for [^─\n]+ ─+$")
_CODEX_PLAIN_TUI_INDENT_PATTERN = re.compile(r"^ {2}\S")
_CODEX_FULL_WIDTH_SEPARATOR_PATTERN = re.compile(r"^─{20,}$")


def _strip_presentation_edge_chrome(line: str) -> str:
    """Strip terminal chrome only when it cannot be part of JSON content."""
    chrome = rf"(?:{_TERMINAL_CONTROL_PATTERN}|{_RAW_C0_CONTROL_PATTERN})"
    line = re.sub(rf"^(?:{chrome})+", "", line)
    return re.sub(rf"(?:{chrome})+$", "", line)


def _normalized_v1_result_capture(text: str) -> Optional[tuple[str, bool, bool]]:
    """Remove only Codex presentation chrome from an otherwise exact V1 capture.

    The provider returns the final assistant block, whose first rendered line
    normally retains Codex's ``•`` bullet.  Some narrow terminal captures
    repeat that bullet on wrapped continuation lines.  Accept that display-only
    form only when the first normalized line is the dedicated V1 marker; do
    not search arbitrary prose for the marker or recover malformed JSON.
    """
    lines = text.splitlines()
    if not lines:
        return None

    first_line = _strip_presentation_edge_chrome(lines[0]).strip()
    decorated = bool(_PRESENTATION_BULLET_PATTERN.match(first_line))
    if decorated:
        first_line = _PRESENTATION_BULLET_PATTERN.sub("", first_line).strip()
    if first_line != _RESULT_V1_MARKER:
        return None

    payload_lines = [_strip_presentation_edge_chrome(line) for line in lines[1:]]
    display_wrap_signature = decorated and all(
        _CODEX_DISPLAY_WRAP_LINE_PATTERN.match(line)
        for line in payload_lines
        if line.strip() and not _CODEX_WORKED_FOR_FOOTER_PATTERN.fullmatch(line.strip())
    )
    if decorated:
        payload_lines = [_PRESENTATION_BULLET_PATTERN.sub("", line) for line in payload_lines]
    payload = "\n".join(payload_lines).strip("\n")
    return (
        f"{_RESULT_V1_MARKER}\n{payload}",
        display_wrap_signature,
        decorated,
    )


def _strip_codex_worked_for_footer(normalized: str) -> Optional[str]:
    """Strip only a trailing, full-line Codex completion footer."""
    marker, separator, payload = normalized.partition("\n")
    if marker != _RESULT_V1_MARKER or not separator:
        return None
    lines = payload.splitlines()
    if not lines or not _CODEX_WORKED_FOR_FOOTER_PATTERN.fullmatch(lines[-1].strip()):
        return None
    payload_without_footer = "\n".join(lines[:-1]).rstrip()
    return f"{marker}\n{payload_without_footer}"


def _strip_codex_plain_indent_separator(normalized: str) -> Optional[str]:
    """Remove the one diagnosed plain-indented Codex terminal separator."""
    marker, separator, payload = normalized.partition("\n")
    if marker != _RESULT_V1_MARKER or not separator:
        return None
    lines = payload.splitlines()
    if len(lines) < 2 or not _CODEX_FULL_WIDTH_SEPARATOR_PATTERN.fullmatch(lines[-1]):
        return None
    payload_lines = lines[:-1]
    if not any(line.strip() for line in payload_lines) or any(
        line.strip() and not _CODEX_PLAIN_TUI_INDENT_PATTERN.match(line) for line in payload_lines
    ):
        return None
    payload_without_separator = "\n".join(payload_lines)
    return f"{marker}\n{payload_without_separator}"


def _has_codex_plain_indent_payload(normalized: str) -> bool:
    """Recognize the exact undecorated Codex TUI payload layout.

    Newer Codex captures can omit the display bullet from an otherwise
    identical V1 response while retaining its two-space physical line wraps.
    Keep this deliberately structural: every non-empty payload line must have
    that indent, so an arbitrary malformed envelope cannot borrow the footer
    recovery path.
    """
    marker, separator, payload = normalized.partition("\n")
    if marker != _RESULT_V1_MARKER or not separator:
        return False
    lines = payload.splitlines()
    return bool(lines) and all(
        not line.strip() or _CODEX_PLAIN_TUI_INDENT_PATTERN.match(line) for line in lines
    )


def _dewrap_codex_json_string_folds(payload: str) -> Optional[str]:
    """Undo physical Codex folds inside JSON strings without repairing JSON.

    Codex wraps long terminal lines with an indented continuation.  That raw
    newline is invalid inside a JSON string, but is display-only when it has
    continuation indentation.  Any other raw newline remains malformed.
    """
    result: list[str] = []
    index = 0
    in_string = False
    while index < len(payload):
        character = payload[index]
        if in_string and character == "\\":
            result.append(character)
            index += 1
            if index < len(payload):
                result.append(payload[index])
                index += 1
            continue
        if character == '"':
            in_string = not in_string
            result.append(character)
            index += 1
            continue
        if in_string and character == "\n":
            continuation = index + 1
            while continuation < len(payload) and payload[continuation] in " \t":
                continuation += 1
            if continuation == index + 1 or continuation == len(payload):
                return None
            if not result or result[-1] != "-":
                result.append(" ")
            index = continuation
            continue
        result.append(character)
        index += 1
    return "".join(result)


def _parse_v1_document(normalized: str) -> Optional[DelegationResultDocument]:
    try:
        candidate = json.loads(normalized[len(_RESULT_V1_MARKER) + 1 :])
        if isinstance(candidate, dict):
            return DelegationResultDocument.model_validate(candidate)
    except (ValueError, TypeError):
        pass
    return None


def parse_v1_result_capture(text: str) -> tuple[bool, Optional[DelegationResultDocument]]:
    """Strictly parse the dedicated V1 channel without reclassifying legacy text.

    The boolean distinguishes an absent V1 channel from a malformed envelope;
    callers that must validate terminal completion can therefore fail closed
    without treating ordinary prose as structured output.
    """
    normalized_capture = _normalized_v1_result_capture(text)
    if normalized_capture is None:
        return False, None
    normalized, display_wrap_signature, decorated = normalized_capture
    document = _parse_v1_document(normalized)
    if document is not None:
        return True, document

    # The recovery path is deliberately narrower than ordinary V1 parsing:
    # only the known footer plus Codex's repeated rendered bullet signature
    # authorize removing terminal line folds.
    footer_stripped = _strip_codex_worked_for_footer(normalized)
    if footer_stripped is not None and (
        display_wrap_signature or _has_codex_plain_indent_payload(footer_stripped)
    ):
        recovered = _dewrap_codex_json_string_folds(footer_stripped[len(_RESULT_V1_MARKER) + 1 :])
        if recovered is not None:
            document = _parse_v1_document(f"{_RESULT_V1_MARKER}\n{recovered}")
            if document is not None:
                return True, document

    # This alternate capture has two-space TUI indentation on every payload
    # line and a full-width completion separator. Keep it distinct from the
    # older Worked/bullet recovery above; the exact separator helper itself is
    # the gate, regardless of whether the marker retained a display bullet.
    plain_separator_stripped = _strip_codex_plain_indent_separator(normalized)
    if plain_separator_stripped is not None:
        recovered = _dewrap_codex_json_string_folds(
            plain_separator_stripped[len(_RESULT_V1_MARKER) + 1 :]
        )
        if recovered is not None:
            document = _parse_v1_document(f"{_RESULT_V1_MARKER}\n{recovered}")
            if document is not None:
                return True, document
    return True, None


def _result_document(text: str) -> str:
    """Parse only the explicit handoff result channel; retain every other capture verbatim.

    Terminal transcripts regularly contain JSON from tools, commands, and pasted
    source. Treating any such object as a result document makes the durable
    artifact depend on incidental transcript shape. A child must instead put
    the complete capture on the dedicated, top-level ``CAO_RESULT_V1`` channel.
    """
    _is_v1, document = parse_v1_result_capture(text)
    if document is not None:
        return document.model_dump_json()
    return _legacy_document(text)


def _record_result_event(
    db: Any,
    result_id: str,
    event_key: str,
    event_type: str,
    actor_kind: str,
    actor_terminal_id: Optional[str] = None,
    workflow_turn_id: Optional[int] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> None:
    if db.query(DelegationResultEventModel).filter_by(event_key=event_key).first() is None:
        db.add(
            DelegationResultEventModel(
                result_id=result_id,
                event_key=event_key,
                event_type=event_type,
                actor_kind=actor_kind,
                actor_terminal_id=actor_terminal_id,
                workflow_turn_id=workflow_turn_id,
                detail_json=json.dumps(detail, sort_keys=True) if detail is not None else None,
            )
        )


def _create_result_for_assignment(
    db: Any,
    assignment: ChildAssignmentModel,
    delegation_kind: str,
    workflow: Optional[WorkflowModel],
    status: str = DelegationResultStatus.AWAITING.value,
    reason_code: Optional[str] = None,
) -> DelegationResultModel:
    existing = db.query(DelegationResultModel).filter_by(child_assignment_id=assignment.id).first()
    if existing is not None:
        return existing
    child = db.query(TerminalModel).filter_by(id=assignment.child_terminal_id).first()
    result = DelegationResultModel(
        id=str(uuid.uuid4()),
        child_assignment_id=assignment.id,
        delegation_kind=delegation_kind,
        parent_terminal_id=assignment.parent_terminal_id,
        child_terminal_id=assignment.child_terminal_id,
        session_name=child.tmux_session if child else None,
        child_provider=child.provider if child else None,
        child_agent_profile=child.agent_profile if child else None,
        parent_workflow_id=workflow.id if workflow else None,
        authorship="cao_lifecycle_snapshot",
        status=status,
        reason_code=reason_code,
        finalized_at=datetime.now() if status != DelegationResultStatus.AWAITING.value else None,
    )
    db.add(result)
    db.flush()
    _record_result_event(db, result.id, f"result-created:{assignment.id}", "created", "cao_system")
    return result


def _backfill_delegation_results() -> None:
    """Backfill only exact assignment links; generic Inbox prose is never inferred."""
    with SessionLocal() as db:
        assignments = db.query(ChildAssignmentModel).all()
        changed = False
        for assignment in assignments:
            if db.query(DelegationResultModel).filter_by(child_assignment_id=assignment.id).first():
                continue
            kind = "handoff" if assignment.status.startswith("handoff_") else "assign"
            workflow = _open_workflow(db, assignment.parent_terminal_id, create=False)
            result_status = DelegationResultStatus.AWAITING.value
            reason = None
            body = None
            if assignment.status == ChildAssignmentStatus.CANCELLED.value:
                result_status, reason = DelegationResultStatus.CANCELLED.value, "cancelled"
            elif assignment.direct_result_output:
                result_status, body = (
                    DelegationResultStatus.COMPLETE.value,
                    assignment.direct_result_output,
                )
            elif assignment.result_message_id:
                msg = db.query(InboxModel).filter_by(id=assignment.result_message_id).first()
                if msg is not None:
                    result_status, body = DelegationResultStatus.COMPLETE.value, msg.message
            elif db.query(TerminalModel).filter_by(id=assignment.child_terminal_id).first() is None:
                # A missing child has no remaining producer capable of
                # resolving an old awaiting relation.  Preserve the absence
                # as an explicit terminal artifact instead of manufacturing
                # a permanent restart ambiguity.
                result_status, reason = (
                    DelegationResultStatus.INCOMPLETE.value,
                    "backfill_missing_child_terminal",
                )
                assignment.status = ChildAssignmentStatus.CANCELLED.value
            elif (
                db.query(TerminalModel).filter_by(id=assignment.parent_terminal_id).first() is None
            ):
                result_status, reason = (
                    DelegationResultStatus.CANCELLED.value,
                    "backfill_missing_parent_terminal",
                )
                assignment.status = ChildAssignmentStatus.CANCELLED.value
            result = _create_result_for_assignment(
                db, assignment, kind, workflow, result_status, reason
            )
            if result.status != DelegationResultStatus.AWAITING.value:
                _purge_staged_handoff_submission(db, result.id)
            if body is not None:
                encoded = _legacy_document(body)
                result.document_json = encoded
                result.content_sha256 = hashlib.sha256(body.encode()).hexdigest()
                result.content_bytes = len(body.encode())
                result.authorship = (
                    "cao_handoff_capture" if kind == "handoff" else "child_submission"
                )
            changed = True
        if changed:
            db.commit()


def _migrate_child_assignment_columns() -> bool:
    """Add and verify every assignment column before ORM rows may be loaded."""
    import sqlite3

    from cli_agent_orchestrator.constants import DATABASE_FILE

    try:
        conn = sqlite3.connect(str(DATABASE_FILE))
        columns = {row[1] for row in conn.execute("PRAGMA table_info(child_assignments)")}
        if "cleanup_acknowledged" not in columns:
            conn.execute(
                "ALTER TABLE child_assignments "
                "ADD COLUMN cleanup_acknowledged BOOLEAN NOT NULL DEFAULT 0"
            )
        if "direct_result_output" not in columns:
            conn.execute("ALTER TABLE child_assignments ADD COLUMN direct_result_output TEXT")
        if "handoff_input_received" not in columns:
            conn.execute(
                "ALTER TABLE child_assignments "
                "ADD COLUMN handoff_input_received BOOLEAN NOT NULL DEFAULT 0"
            )
        if "retirement_claim_token" not in columns:
            conn.execute("ALTER TABLE child_assignments ADD COLUMN retirement_claim_token TEXT")
        if "retirement_claimed_at" not in columns:
            conn.execute("ALTER TABLE child_assignments ADD COLUMN retirement_claimed_at DATETIME")
        if "retirement_exit_dispatched_at" not in columns:
            conn.execute(
                "ALTER TABLE child_assignments ADD COLUMN retirement_exit_dispatched_at DATETIME"
            )
        if "retirement_completed_at" not in columns:
            conn.execute(
                "ALTER TABLE child_assignments ADD COLUMN retirement_completed_at DATETIME"
            )
        if "retirement_cleanup_intent" not in columns:
            conn.execute("ALTER TABLE child_assignments ADD COLUMN retirement_cleanup_intent TEXT")
        if "retirement_cleanup_completed_at" not in columns:
            conn.execute(
                "ALTER TABLE child_assignments "
                "ADD COLUMN retirement_cleanup_completed_at DATETIME"
            )
        conn.commit()
        columns = {row[1] for row in conn.execute("PRAGMA table_info(child_assignments)")}
        expected = {column.name for column in ChildAssignmentModel.__table__.columns}
        missing = expected - columns
        conn.close()
        if missing:
            logger.error(
                "Child-assignment schema migration left missing columns: %s",
                ", ".join(sorted(missing)),
            )
            return False
        return True
    except Exception as exc:
        logger.warning("Child-assignment schema migration failed: %s", exc)
        return False


def _migrate_add_allowed_tools() -> None:
    """Add allowed_tools column to terminals table if missing (schema migration)."""
    import sqlite3

    from cli_agent_orchestrator.constants import DATABASE_FILE

    try:
        conn = sqlite3.connect(str(DATABASE_FILE))
        cursor = conn.execute("PRAGMA table_info(terminals)")
        columns = {row[1] for row in cursor.fetchall()}
        if "allowed_tools" not in columns:
            conn.execute("ALTER TABLE terminals ADD COLUMN allowed_tools TEXT")
            conn.commit()
            logger.info("Migration: added allowed_tools column to terminals table")
        conn.close()
    except Exception as e:
        logger.warning(f"Migration check for allowed_tools failed: {e}")


def _migrate_terminal_worktree_authority_columns() -> bool:
    """Add immutable authority metadata and the durable writer-lease table."""
    import sqlite3

    from cli_agent_orchestrator.constants import DATABASE_FILE

    try:
        conn = sqlite3.connect(str(DATABASE_FILE))
        columns = {row[1] for row in conn.execute("PRAGMA table_info(terminals)")}
        if "session_id" not in columns:
            conn.execute("ALTER TABLE terminals ADD COLUMN session_id TEXT")
        if "launch_worktree" not in columns:
            conn.execute("ALTER TABLE terminals ADD COLUMN launch_worktree TEXT")
        if "write_enabled" not in columns:
            conn.execute("ALTER TABLE terminals ADD COLUMN write_enabled BOOLEAN")
        for name in (
            "context_role",
            "managed_worktree_kind",
            "managed_worktree_source",
            "managed_worktree_branch",
            "managed_worktree_commit",
            "runtime_lifecycle",
            "owner_grant_id",
            "runtime_pane_id",
            "runtime_generation",
            "runtime_generation_origin",
            "runtime_operation_kind",
            "runtime_operation_token",
        ):
            if name not in columns:
                conn.execute(f"ALTER TABLE terminals ADD COLUMN {name} TEXT")
        for name in (
            "runtime_exit_requested_at",
            "runtime_exited_at",
            "runtime_operation_claimed_at",
            "runtime_operation_expires_at",
        ):
            if name not in columns:
                conn.execute(f"ALTER TABLE terminals ADD COLUMN {name} DATETIME")
        for name in ("runtime_pane_pid", "runtime_process_start_ticks"):
            if name not in columns:
                conn.execute(f"ALTER TABLE terminals ADD COLUMN {name} INTEGER")
        if "creation_order" not in columns:
            conn.execute("ALTER TABLE terminals ADD COLUMN creation_order INTEGER")
        # SQLite rowid is an accurate insertion order at migration/insert time,
        # but it can be reassigned by table rebuilds. Copy it into a durable
        # column once and use only that explicit value thereafter.
        conn.execute("UPDATE terminals SET creation_order = rowid WHERE creation_order IS NULL")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_terminals_session_creation_order "
            "ON terminals (session_id, creation_order, id)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS worktree_writer_leases ("
            "canonical_worktree TEXT PRIMARY KEY, "
            "terminal_id TEXT NOT NULL UNIQUE, "
            "created_at DATETIME NOT NULL)"
        )
        # Rows already carrying authority metadata may come from a partially
        # upgraded P1 deployment.  Preserve one deterministic owner per
        # worktree.  Genuine pre-P1 rows retain NULL authority below; their
        # durable presence fences every new writer until positive-death cleanup
        # or explicit operator reconciliation removes that uncertainty.
        conn.execute(
            "INSERT OR IGNORE INTO worktree_writer_leases "
            "(canonical_worktree, terminal_id, created_at) "
            "SELECT launch_worktree, id, CURRENT_TIMESTAMP FROM terminals "
            "WHERE write_enabled = 1 AND launch_worktree IS NOT NULL "
            "AND (runtime_lifecycle IS NULL OR runtime_lifecycle != 'exited') ORDER BY id"
        )
        conn.commit()
        conn.close()
        return True
    except Exception as exc:
        logger.warning("Terminal worktree-authority schema migration failed: %s", exc)
        return False


def _ensure_terminal_worktree_authority_schema() -> None:
    """Lazily migrate command-only processes that do not call ``init_db``."""
    global _terminal_authority_schema_ready
    if _terminal_authority_schema_ready:
        return
    with _terminal_authority_schema_lock:
        if not _terminal_authority_schema_ready:
            _terminal_authority_schema_ready = _migrate_terminal_worktree_authority_columns()


def create_terminal(
    terminal_id: str,
    tmux_session: str,
    tmux_window: str,
    provider: str,
    agent_profile: Optional[str] = None,
    allowed_tools: Optional[List[str]] = None,
    auth_token_sha256: Optional[str] = None,
    launch_worktree: Optional[str] = None,
    write_enabled: Optional[bool] = None,
    context_role: Optional[str] = "work",
    managed_worktree_kind: Optional[str] = None,
    managed_worktree_source: Optional[str] = None,
    managed_worktree_branch: Optional[str] = None,
    managed_worktree_commit: Optional[str] = None,
    project_id: Optional[str] = None,
    project_name: Optional[str] = None,
    project_path: Optional[str] = None,
    project_description: Optional[str] = None,
    privileged_launch: bool = False,
    owner_grant_token: Optional[str] = None,
    owner_grant_launch_id: Optional[str] = None,
    owner_grant_requested_session_name: Optional[str] = None,
    owner_grant_scope: Optional[Mapping[str, Any]] = None,
    profile_revision_id: Optional[str] = None,
    provider_config_revision_id: Optional[str] = None,
    launch_snapshot: Optional[Mapping[str, Any]] = None,
    owner_grant_canonical_worktree: Optional[str] = None,
    session_lifetime_id: Optional[str] = None,
    runtime_pane_id: Optional[str] = None,
    runtime_pane_pid: Optional[int] = None,
    runtime_generation: Optional[str] = None,
    runtime_generation_origin: Optional[str] = None,
    runtime_process_start_ticks: Optional[int] = None,
) -> Dict[str, Any]:
    """Create metadata and atomically acquire any required writer lease."""
    import json as _json

    _ensure_terminal_worktree_authority_schema()
    _ensure_control_plane_schema()
    # Session identity is a launch-time fact and must exist before this row is
    # ever used to record provider completion.
    _ensure_usage_schema()
    if write_enabled is True and (not launch_worktree or not launch_worktree.startswith("/")):
        raise ValueError("write-enabled terminals require an absolute canonical worktree")
    if context_role not in {"supervisor", "work"}:
        raise ValueError("terminal context_role must be supervisor or work")
    if managed_worktree_kind not in {None, "task", "reviewer"}:
        raise ValueError("managed_worktree_kind must be task or reviewer")
    with SessionLocal() as db:
        if privileged_launch:
            # Serialize validation, one-use consumption, and terminal metadata.
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        existing_session = (
            db.query(TerminalModel.session_id)
            .filter(
                TerminalModel.tmux_session == tmux_session,
                TerminalModel.session_id.is_not(None),
                (TerminalModel.runtime_lifecycle.is_(None))
                | (TerminalModel.runtime_lifecycle != "exited"),
            )
            .order_by(TerminalModel.last_active.desc(), TerminalModel.id.desc())
            .first()
        )
        session_id = (
            str(session_lifetime_id)
            if session_lifetime_id
            else str(existing_session[0]) if existing_session else str(uuid.uuid4())
        )
        if write_enabled is True:
            unresolved = (
                db.query(TerminalModel.id)
                .filter(
                    (TerminalModel.launch_worktree.is_(None))
                    | (TerminalModel.write_enabled.is_(None))
                )
                .order_by(TerminalModel.id.asc())
                .first()
            )
            if unresolved is not None:
                raise UnreconciledTerminalAuthority(str(unresolved[0]))
        owner_grant_id = None
        if privileged_launch:
            if not owner_grant_token or not owner_grant_launch_id:
                raise OwnerGrantRejected()
            digest = hashlib.sha256(owner_grant_token.encode("utf-8", "strict")).hexdigest()
            grant = db.query(OwnerLaunchGrantModel).filter_by(token_sha256=digest).first()
            now = datetime.now()
            serialized_scope = json.dumps(
                dict(owner_grant_scope or {}), sort_keys=True, separators=(",", ":")
            )
            if not (
                grant
                and grant.consumed_at is None
                and grant.expires_at >= now
                and hmac.compare_digest(grant.launch_id, owner_grant_launch_id)
                and hmac.compare_digest(grant.agent_profile, agent_profile or "")
                and hmac.compare_digest(grant.provider, provider)
                and hmac.compare_digest(
                    grant.canonical_worktree,
                    owner_grant_canonical_worktree or launch_worktree or "",
                )
                and grant.requested_session_name == owner_grant_requested_session_name
                and hmac.compare_digest(grant.scope_json or "{}", serialized_scope)
            ):
                raise OwnerGrantRejected("OWNER_GRANT_INVALID_OR_EXPIRED")
            consumed = (
                db.query(OwnerLaunchGrantModel)
                .filter(
                    OwnerLaunchGrantModel.id == grant.id,
                    OwnerLaunchGrantModel.consumed_at.is_(None),
                )
                .update(
                    {
                        OwnerLaunchGrantModel.consumed_at: now,
                        OwnerLaunchGrantModel.consumed_terminal_id: terminal_id,
                    },
                    synchronize_session=False,
                )
            )
            if consumed != 1:
                db.rollback()
                raise OwnerGrantRejected("OWNER_GRANT_ALREADY_CONSUMED")
            owner_grant_id = grant.id
        elif owner_grant_token or owner_grant_launch_id:
            raise OwnerGrantRejected("OWNER_GRANT_SCOPE_MISMATCH")

        terminal = TerminalModel(
            id=terminal_id,
            tmux_session=tmux_session,
            session_id=session_id,
            tmux_window=tmux_window,
            provider=provider,
            agent_profile=agent_profile,
            allowed_tools=_json.dumps(allowed_tools) if allowed_tools else None,
            auth_token_sha256=auth_token_sha256,
            owner_grant_id=owner_grant_id,
            profile_revision_id=profile_revision_id,
            provider_config_revision_id=provider_config_revision_id,
            launch_snapshot_json=(
                json.dumps(dict(launch_snapshot), sort_keys=True, separators=(",", ":"))
                if launch_snapshot is not None
                else None
            ),
            launch_snapshot_status=(
                "available" if launch_snapshot is not None else "legacy_unavailable"
            ),
            launch_worktree=launch_worktree,
            write_enabled=write_enabled,
            context_role=context_role,
            managed_worktree_kind=managed_worktree_kind,
            managed_worktree_source=managed_worktree_source,
            managed_worktree_branch=managed_worktree_branch,
            managed_worktree_commit=managed_worktree_commit,
            project_id=project_id,
            project_name=project_name,
            project_path=project_path,
            project_description=project_description,
            runtime_lifecycle="starting",
            runtime_pane_id=runtime_pane_id,
            runtime_pane_pid=runtime_pane_pid,
            runtime_generation=runtime_generation,
            runtime_generation_origin=(
                runtime_generation_origin or ("launch" if runtime_generation is not None else None)
            ),
            runtime_process_start_ticks=runtime_process_start_ticks,
        )
        db.add(terminal)
        if write_enabled is True:
            db.add(
                WorktreeWriterLeaseModel(
                    canonical_worktree=launch_worktree,
                    terminal_id=terminal_id,
                )
            )
        try:
            db.flush()
            terminal.creation_order = int(
                db.execute(
                    text("SELECT rowid FROM terminals WHERE id = :terminal_id"),
                    {"terminal_id": terminal_id},
                ).scalar_one()
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            if write_enabled is True:
                owner = (
                    db.query(WorktreeWriterLeaseModel)
                    .filter(WorktreeWriterLeaseModel.canonical_worktree == launch_worktree)
                    .first()
                )
                if owner is not None:
                    raise WorktreeWriterLeaseConflict(cast(str, launch_worktree)) from exc
            raise
        return {
            "id": terminal.id,
            "tmux_session": terminal.tmux_session,
            "session_id": terminal.session_id,
            "tmux_window": terminal.tmux_window,
            "provider": terminal.provider,
            "agent_profile": terminal.agent_profile,
            "profile_revision_id": terminal.profile_revision_id,
            "provider_config_revision_id": terminal.provider_config_revision_id,
            "launch_snapshot_status": terminal.launch_snapshot_status,
            "allowed_tools": allowed_tools,
            "launch_worktree": terminal.launch_worktree,
            "write_enabled": terminal.write_enabled,
            "context_role": terminal.context_role,
            "managed_worktree_kind": terminal.managed_worktree_kind,
            "managed_worktree_source": terminal.managed_worktree_source,
            "managed_worktree_branch": terminal.managed_worktree_branch,
            "managed_worktree_commit": terminal.managed_worktree_commit,
            "project_id": terminal.project_id,
            "project_name": terminal.project_name,
            "project_path": terminal.project_path,
            "project_description": terminal.project_description,
            "runtime_lifecycle": terminal.runtime_lifecycle,
            "runtime_exit_requested_at": terminal.runtime_exit_requested_at,
            "runtime_exited_at": terminal.runtime_exited_at,
            "runtime_pane_id": terminal.runtime_pane_id,
            "runtime_pane_pid": terminal.runtime_pane_pid,
            "runtime_generation": terminal.runtime_generation,
            "runtime_generation_origin": terminal.runtime_generation_origin,
            "runtime_process_start_ticks": terminal.runtime_process_start_ticks,
            "runtime_operation_kind": terminal.runtime_operation_kind,
            "runtime_operation_token": terminal.runtime_operation_token,
            "runtime_operation_claimed_at": terminal.runtime_operation_claimed_at,
            "runtime_operation_expires_at": terminal.runtime_operation_expires_at,
            "creation_order": terminal.creation_order,
        }


def get_terminal_metadata(terminal_id: str) -> Optional[Dict[str, Any]]:
    """Get terminal metadata by ID."""
    import json as _json

    _ensure_terminal_worktree_authority_schema()
    _ensure_control_plane_schema()
    _ensure_usage_schema()
    with SessionLocal() as db:
        terminal = db.query(TerminalModel).filter(TerminalModel.id == terminal_id).first()
        if not terminal:
            logger.warning(f"Terminal metadata not found for terminal_id: {terminal_id}")
            return None
        logger.debug(
            f"Retrieved terminal metadata for {terminal_id}: provider={terminal.provider}, session={terminal.tmux_session}"
        )
        allowed_tools = _json.loads(terminal.allowed_tools) if terminal.allowed_tools else None
        return {
            "id": terminal.id,
            "tmux_session": terminal.tmux_session,
            "session_id": terminal.session_id,
            "tmux_window": terminal.tmux_window,
            "provider": terminal.provider,
            "agent_profile": terminal.agent_profile,
            "profile_revision_id": terminal.profile_revision_id,
            "provider_config_revision_id": terminal.provider_config_revision_id,
            "launch_snapshot_status": terminal.launch_snapshot_status,
            "launch_snapshot": (
                _json.loads(terminal.launch_snapshot_json)
                if terminal.launch_snapshot_json
                else None
            ),
            "allowed_tools": allowed_tools,
            "launch_worktree": terminal.launch_worktree,
            "write_enabled": terminal.write_enabled,
            "context_role": terminal.context_role,
            "managed_worktree_kind": terminal.managed_worktree_kind,
            "managed_worktree_source": terminal.managed_worktree_source,
            "managed_worktree_branch": terminal.managed_worktree_branch,
            "managed_worktree_commit": terminal.managed_worktree_commit,
            "project_id": terminal.project_id,
            "project_name": terminal.project_name,
            "project_path": terminal.project_path,
            "project_description": terminal.project_description,
            "runtime_lifecycle": terminal.runtime_lifecycle,
            "runtime_exit_requested_at": terminal.runtime_exit_requested_at,
            "runtime_exited_at": terminal.runtime_exited_at,
            "runtime_pane_id": terminal.runtime_pane_id,
            "runtime_pane_pid": terminal.runtime_pane_pid,
            "runtime_generation": terminal.runtime_generation,
            "runtime_generation_origin": terminal.runtime_generation_origin,
            "runtime_process_start_ticks": terminal.runtime_process_start_ticks,
            "runtime_operation_kind": terminal.runtime_operation_kind,
            "runtime_operation_token": terminal.runtime_operation_token,
            "runtime_operation_claimed_at": terminal.runtime_operation_claimed_at,
            "runtime_operation_expires_at": terminal.runtime_operation_expires_at,
            "last_active": terminal.last_active,
        }


def list_terminals_by_session(tmux_session: str) -> List[Dict[str, Any]]:
    """List all terminals in a tmux session."""
    _ensure_terminal_worktree_authority_schema()
    _ensure_control_plane_schema()
    with SessionLocal() as db:
        terminals = db.query(TerminalModel).filter(TerminalModel.tmux_session == tmux_session).all()
        return [
            {
                "id": t.id,
                "tmux_session": t.tmux_session,
                "session_id": t.session_id,
                "tmux_window": t.tmux_window,
                "provider": t.provider,
                "agent_profile": t.agent_profile,
                "profile_revision_id": t.profile_revision_id,
                "provider_config_revision_id": t.provider_config_revision_id,
                "launch_snapshot_status": t.launch_snapshot_status,
                "launch_worktree": t.launch_worktree,
                "write_enabled": t.write_enabled,
                "context_role": t.context_role,
                "managed_worktree_kind": t.managed_worktree_kind,
                "managed_worktree_source": t.managed_worktree_source,
                "managed_worktree_branch": t.managed_worktree_branch,
                "managed_worktree_commit": t.managed_worktree_commit,
                "project_id": t.project_id,
                "project_name": t.project_name,
                "project_path": t.project_path,
                "project_description": t.project_description,
                "runtime_lifecycle": t.runtime_lifecycle,
                "runtime_exit_requested_at": t.runtime_exit_requested_at,
                "runtime_exited_at": t.runtime_exited_at,
                "runtime_pane_id": t.runtime_pane_id,
                "runtime_pane_pid": t.runtime_pane_pid,
                "runtime_generation": t.runtime_generation,
                "runtime_generation_origin": t.runtime_generation_origin,
                "runtime_process_start_ticks": t.runtime_process_start_ticks,
                "last_active": t.last_active,
            }
            for t in terminals
        ]


def update_last_active(terminal_id: str) -> bool:
    """Update last active timestamp."""
    with SessionLocal() as db:
        terminal = db.query(TerminalModel).filter(TerminalModel.id == terminal_id).first()
        if terminal:
            terminal.last_active = datetime.now()
            db.commit()
            return True
        return False


def list_all_terminals() -> List[Dict[str, Any]]:
    """List all terminals."""
    _ensure_terminal_worktree_authority_schema()
    _ensure_control_plane_schema()
    with SessionLocal() as db:
        terminals = db.query(TerminalModel).all()
        return [
            {
                "id": t.id,
                "tmux_session": t.tmux_session,
                "session_id": t.session_id,
                "tmux_window": t.tmux_window,
                "provider": t.provider,
                "agent_profile": t.agent_profile,
                "profile_revision_id": t.profile_revision_id,
                "provider_config_revision_id": t.provider_config_revision_id,
                "launch_snapshot_status": t.launch_snapshot_status,
                "launch_worktree": t.launch_worktree,
                "write_enabled": t.write_enabled,
                "context_role": t.context_role,
                "managed_worktree_kind": t.managed_worktree_kind,
                "managed_worktree_source": t.managed_worktree_source,
                "managed_worktree_branch": t.managed_worktree_branch,
                "managed_worktree_commit": t.managed_worktree_commit,
                "project_id": t.project_id,
                "project_name": t.project_name,
                "project_path": t.project_path,
                "project_description": t.project_description,
                "runtime_lifecycle": t.runtime_lifecycle,
                "runtime_exit_requested_at": t.runtime_exit_requested_at,
                "runtime_exited_at": t.runtime_exited_at,
                "runtime_pane_id": t.runtime_pane_id,
                "runtime_pane_pid": t.runtime_pane_pid,
                "runtime_generation": t.runtime_generation,
                "runtime_generation_origin": t.runtime_generation_origin,
                "runtime_process_start_ticks": t.runtime_process_start_ticks,
                "last_active": t.last_active,
            }
            for t in terminals
        ]


def _terminal_ui_projection_cte(
    *, session_ids: Optional[List[str]] = None
) -> tuple[str, Dict[str, Any]]:
    """Build one durable, lightweight UI projection without live polling."""
    parameters: Dict[str, Any] = {}
    selected_where = ""
    if session_ids is not None:
        if not session_ids:
            selected_where = " WHERE 1 = 0"
        else:
            placeholders = []
            for index, value in enumerate(session_ids):
                name = f"projection_session_{index}"
                parameters[name] = value
                placeholders.append(f":{name}")
            selected_where = (
                " WHERE COALESCE(session_id, 'legacy:' || tmux_session) "
                f"IN ({', '.join(placeholders)})"
            )
    return (
        """
WITH selected_terminals AS MATERIALIZED (
    SELECT id, tmux_window, provider, tmux_session,
           COALESCE(session_id, 'legacy:' || tmux_session) AS stable_session_id,
           agent_profile, runtime_lifecycle, context_role, launch_worktree,
           managed_worktree_kind, managed_worktree_commit, managed_worktree_branch,
           project_id, project_name, project_path,
           COALESCE(creation_order, rowid) AS creation_order, last_active
    FROM terminals
"""
        + selected_where
        + """
), workflow_ranked AS (
    SELECT w.root_terminal_id, w.status,
           ROW_NUMBER() OVER (PARTITION BY w.root_terminal_id ORDER BY w.id DESC) AS rank
    FROM workflows w JOIN selected_terminals st ON st.id = w.root_terminal_id
), latest_workflow AS (
    SELECT root_terminal_id, status FROM workflow_ranked WHERE rank = 1
), assignment_relations AS (
    SELECT ca.id AS assignment_id, ca.parent_terminal_id AS terminal_id,
           0 AS is_child, ca.status, ca.updated_at
    FROM child_assignments ca JOIN selected_terminals st ON st.id = ca.parent_terminal_id
    UNION ALL
    SELECT ca.id, ca.child_terminal_id, 1, ca.status, ca.updated_at
    FROM child_assignments ca JOIN selected_terminals st ON st.id = ca.child_terminal_id
), relation_states AS (
    SELECT ar.*, dr.status AS result_status,
           CASE
             WHEN dr.status = 'incomplete' THEN 'incomplete'
             WHEN ar.status IN ('result_failed', 'handoff_result_failed') THEN 'failed'
             WHEN ar.is_child = 1 AND dr.status = 'cancelled' THEN 'cancelled'
             WHEN ar.status = 'handoff_recovery_awaiting_result' THEN 'recoverable'
             WHEN ar.status IN ('awaiting_result', 'handoff_awaiting_result') THEN 'waiting'
             WHEN ar.status IN ('handoff_direct_result_claimed', 'result_queued',
                                'result_delivered', 'handoff_result_queued',
                                'handoff_result_delivered') AND dr.status = 'complete'
                  THEN 'result_ready'
             ELSE NULL
           END AS relation_state,
           CASE
             WHEN ar.is_child = 1 AND dr.status = 'cancelled' THEN 6
             WHEN dr.status = 'incomplete' THEN 5
             WHEN ar.status IN ('result_failed', 'handoff_result_failed') THEN 4
             WHEN ar.status IN ('handoff_direct_result_claimed', 'result_queued',
                                'result_delivered', 'handoff_result_queued',
                                'handoff_result_delivered') AND dr.status = 'complete' THEN 3
             WHEN ar.status = 'handoff_recovery_awaiting_result' THEN 2
             WHEN ar.status IN ('awaiting_result', 'handoff_awaiting_result') THEN 1
             ELSE 0
           END AS relation_priority
    FROM assignment_relations ar
    LEFT JOIN delegation_results dr ON dr.child_assignment_id = ar.assignment_id
), latest_relations AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY terminal_id ORDER BY updated_at DESC, assignment_id DESC
    ) AS latest_rank FROM relation_states
), best_relations AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY terminal_id
        ORDER BY relation_priority DESC, updated_at DESC, assignment_id DESC
    ) AS state_rank FROM relation_states WHERE relation_state IS NOT NULL
), projected AS MATERIALIZED (
    SELECT t.id, t.tmux_window AS name, t.provider,
           t.tmux_session AS session_name, t.stable_session_id AS session_id,
           t.agent_profile,
           CASE
             WHEN COALESCE(t.runtime_lifecycle, 'starting') = 'exited' THEN 'exited'
             WHEN pel.terminal_id IS NOT NULL THEN 'processing'
             WHEN EXISTS (
               SELECT 1 FROM workflows qw JOIN workflow_turns qt ON qt.workflow_id = qw.id
               WHERE qw.root_terminal_id = t.id AND qw.status = 'open' AND qt.state = 'queued'
             ) THEN 'queued'
             ELSE 'ready'
           END AS activity,
           CASE
             WHEN COALESCE(t.runtime_lifecycle, 'starting') = 'exited' THEN 'exited'
             WHEN pel.terminal_id IS NOT NULL THEN 'processing'
             WHEN EXISTS (
               SELECT 1 FROM workflows qw JOIN workflow_turns qt ON qt.workflow_id = qw.id
               WHERE qw.root_terminal_id = t.id AND qw.status = 'open' AND qt.state = 'queued'
             ) THEN 'queued_provider_execution'
             ELSE 'ready'
           END AS execution_state,
           COALESCE(t.runtime_lifecycle, 'starting') AS lifecycle,
           CASE
             WHEN lw.status = 'owner_gate' THEN 'owner_gate'
             WHEN lw.status = 'terminal' THEN 'completed'
             WHEN lw.status = 'cancelled' THEN 'cancelled'
             ELSE COALESCE(br.relation_state, CASE WHEN lw.status = 'open' THEN 'active' END)
           END AS workflow_state,
           lw.status AS workflow_status, lr.status AS assignment_status,
           lr.result_status, lr.status AS delivery_status,
           t.context_role, t.launch_worktree, t.managed_worktree_kind,
           t.managed_worktree_commit, t.managed_worktree_branch,
           t.project_id AS projectId, t.project_name, t.project_path,
           t.creation_order, t.last_active
    FROM selected_terminals t
    LEFT JOIN latest_workflow lw ON lw.root_terminal_id = t.id
    LEFT JOIN latest_relations lr ON lr.terminal_id = t.id AND lr.latest_rank = 1
    LEFT JOIN best_relations br ON br.terminal_id = t.id AND br.state_rank = 1
    LEFT JOIN provider_execution_leases pel ON pel.terminal_id = t.id
)
""",
        parameters,
    )


def _ensure_terminal_ui_projection_schema() -> None:
    global _terminal_ui_projection_schema_engine_identity
    global _terminal_ui_projection_schema_ready
    _ensure_terminal_worktree_authority_schema()
    _ensure_provider_execution_schema()
    _ensure_workflow_schema()
    _ensure_child_assignment_schema()
    _ensure_delegation_result_schema()
    engine_identity = id(engine)
    if (
        _terminal_ui_projection_schema_ready
        and _terminal_ui_projection_schema_engine_identity == engine_identity
    ):
        return
    with _terminal_ui_projection_schema_lock:
        if (
            _terminal_ui_projection_schema_ready
            and _terminal_ui_projection_schema_engine_identity == engine_identity
        ):
            return
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_terminals_session_lifetime_activity "
                "ON terminals (session_id, last_active DESC, id DESC)"
            )
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_terminals_session_creation_order "
                "ON terminals (session_id, creation_order, id)"
            )
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_workflows_root_terminal_id "
                "ON workflows (root_terminal_id)"
            )
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_workflow_turns_workflow_state "
                "ON workflow_turns (workflow_id, state)"
            )
        _terminal_ui_projection_schema_engine_identity = engine_identity
        _terminal_ui_projection_schema_ready = True


def _ui_projection_filters(
    *,
    session_id: Optional[str] = None,
    query: str = "",
    activities: Optional[List[str]] = None,
    workflow_states: Optional[List[str]] = None,
    profiles: Optional[List[str]] = None,
    home_filter: Optional[str] = None,
) -> tuple[str, Dict[str, Any]]:
    clauses: List[str] = []
    parameters: Dict[str, Any] = {}

    def add_values(column: str, values: Optional[List[str]], prefix: str) -> None:
        normalized = [value for value in values or [] if value]
        if not normalized:
            return
        names = []
        for index, value in enumerate(normalized):
            name = f"{prefix}_{index}"
            parameters[name] = value
            names.append(f":{name}")
        clauses.append(f"{column} IN ({', '.join(names)})")

    if session_id:
        clauses.append("session_id = :session_id")
        parameters["session_id"] = session_id
    normalized_query = query.strip().lower()
    if normalized_query:
        clauses.append(
            "(LOWER(id) LIKE :query OR LOWER(name) LIKE :query OR "
            "LOWER(session_name) LIKE :query OR LOWER(provider) LIKE :query OR "
            "LOWER(COALESCE(agent_profile, '')) LIKE :query OR "
            "LOWER(COALESCE(project_name, '')) LIKE :query)"
        )
        parameters["query"] = f"%{normalized_query}%"
    add_values("activity", activities, "activity")
    add_values("workflow_state", workflow_states, "workflow")
    add_values("agent_profile", profiles, "profile")
    if home_filter and home_filter != "all":
        if home_filter == "active":
            clauses.append("lifecycle != 'exited' AND COALESCE(workflow_state, '') != 'completed'")
        elif home_filter == "waiting":
            clauses.append(
                "workflow_state IS NOT NULL "
                "AND workflow_state NOT IN ('owner_gate', 'cancelled', 'completed') "
                "AND lifecycle != 'exited' AND activity != 'processing'"
            )
        elif home_filter in {"owner_gate", "cancelled", "completed"}:
            clauses.append("workflow_state = :home_filter")
            parameters["home_filter"] = home_filter
        else:
            raise ValueError("home_filter is invalid")
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), parameters


def list_terminal_ui_summary_page(
    *,
    limit: int,
    offset: int = 0,
    session_id: Optional[str] = None,
    query: str = "",
    activities: Optional[List[str]] = None,
    workflow_states: Optional[List[str]] = None,
    profiles: Optional[List[str]] = None,
    home_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """Filter and page the durable terminal projection inside SQLite."""
    _ensure_terminal_ui_projection_schema()
    projection_cte, parameters = _terminal_ui_projection_cte(
        session_ids=[session_id] if session_id else None
    )
    where, filters = _ui_projection_filters(
        session_id=session_id,
        query=query,
        activities=activities,
        workflow_states=workflow_states,
        profiles=profiles,
        home_filter=home_filter,
    )
    parameters.update(filters)
    parameters.update({"limit": limit, "offset": offset})
    order_by = (
        "creation_order ASC, id ASC"
        if session_id
        else "last_active DESC, creation_order DESC, id DESC"
    )
    sql = projection_cte + ", filtered AS MATERIALIZED (SELECT * FROM projected" + where + f"""),
    page AS (SELECT * FROM filtered ORDER BY {order_by} LIMIT :limit OFFSET :offset),
    meta AS (
      SELECT (SELECT COUNT(*) FROM filtered) AS total_count,
             (SELECT json_group_array(activity) FROM
                (SELECT activity FROM projected GROUP BY activity ORDER BY activity)) AS activities_json,
             (SELECT json_group_array(workflow_state) FROM
                (SELECT workflow_state FROM projected WHERE workflow_state IS NOT NULL
                 GROUP BY workflow_state ORDER BY workflow_state)) AS workflows_json,
             (SELECT json_group_array(agent_profile) FROM
                (SELECT agent_profile FROM projected WHERE agent_profile IS NOT NULL
                 GROUP BY agent_profile ORDER BY agent_profile)) AS profiles_json
    )
    SELECT page.*, meta.total_count, meta.activities_json, meta.workflows_json, meta.profiles_json
    FROM meta LEFT JOIN page ON 1 = 1 ORDER BY page.{order_by}
    """
    with SessionLocal() as db:
        rows = db.execute(text(sql), parameters).mappings().all()
    meta = rows[0]
    page_rows = [row for row in rows if row["id"] is not None]
    ignored = {"total_count", "activities_json", "workflows_json", "profiles_json"}
    return {
        "items": [
            {key: value for key, value in row.items() if key not in ignored} for row in page_rows
        ],
        "total": int(meta["total_count"] or 0),
        "facets": {
            "activities": json.loads(meta["activities_json"] or "[]"),
            "workflow_states": json.loads(meta["workflows_json"] or "[]"),
            "profiles": json.loads(meta["profiles_json"] or "[]"),
        },
    }


def get_terminal_ui_overview_counts() -> Dict[str, int]:
    """Aggregate Home counters and durable session lifetimes in SQLite."""
    _ensure_terminal_ui_projection_schema()
    projection_cte, parameters = _terminal_ui_projection_cte()
    sql = projection_cte + """
        SELECT COUNT(DISTINCT session_id) AS sessions, COUNT(*) AS agents,
               SUM(CASE WHEN lifecycle != 'exited'
                         AND COALESCE(workflow_state, '') != 'completed' THEN 1 ELSE 0 END) AS active,
               SUM(CASE WHEN workflow_state IS NOT NULL
                         AND workflow_state NOT IN ('owner_gate', 'cancelled', 'completed')
                         AND lifecycle != 'exited'
                         AND activity != 'processing' THEN 1 ELSE 0 END) AS waiting,
               SUM(CASE WHEN workflow_state = 'owner_gate' THEN 1 ELSE 0 END) AS owner_gate,
               SUM(CASE WHEN workflow_state = 'cancelled' THEN 1 ELSE 0 END) AS cancelled,
               SUM(CASE WHEN workflow_state = 'completed' THEN 1 ELSE 0 END) AS completed
        FROM projected
    """
    with SessionLocal() as db:
        row = db.execute(text(sql), parameters).mappings().one()
    return {key: int(row[key] or 0) for key in row.keys()}


def list_terminal_ui_session_page(*, limit: int, offset: int, query: str = "") -> Dict[str, Any]:
    """Page stable session lifetimes; runtime retirement cannot erase them."""
    _ensure_terminal_ui_projection_schema()
    projection_cte, parameters = _terminal_ui_projection_cte()
    normalized = query.strip().lower()
    search = ""
    if normalized:
        parameters["query"] = f"%{normalized}%"
        search = (
            " WHERE LOWER(session_id) LIKE :query OR LOWER(session_name) LIKE :query "
            "OR LOWER(COALESCE(project_name, '')) LIKE :query"
        )
    parameters.update({"limit": limit, "offset": offset})
    sql = (
        projection_cte
        + """, aggregates AS MATERIALIZED (
      SELECT session_id AS id, MAX(session_name) AS name,
             CASE WHEN SUM(CASE WHEN lifecycle != 'exited' THEN 1 ELSE 0 END) > 0
                  THEN 'active' ELSE 'history' END AS status,
             MIN(last_active) AS created_at, COUNT(*) AS agent_count,
             SUM(CASE WHEN lifecycle != 'exited'
                       AND COALESCE(workflow_state, '') != 'completed' THEN 1 ELSE 0 END)
                  AS active_agent_count,
             CASE WHEN COUNT(projectId) = COUNT(*) AND COUNT(project_name) = COUNT(*)
                       AND COUNT(project_path) = COUNT(*)
                       AND COUNT(DISTINCT projectId || char(31) || project_name
                                                || char(31) || project_path) = 1
                  THEN MAX(project_name) ELSE NULL END AS project_name,
             MAX(last_active) AS last_active
      FROM projected GROUP BY session_id
    ), filtered AS MATERIALIZED (SELECT * FROM aggregates"""
        + search
        + """),
    page AS (SELECT * FROM filtered ORDER BY last_active DESC, id DESC LIMIT :limit OFFSET :offset),
    meta AS (SELECT COUNT(*) AS total_count FROM filtered)
    SELECT page.*, meta.total_count,
           COALESCE((SELECT json_group_object(state, amount) FROM (
             SELECT COALESCE(workflow_state, 'untracked') AS state, COUNT(*) AS amount
             FROM projected WHERE projected.session_id = page.id
             GROUP BY COALESCE(workflow_state, 'untracked'))), '{}') AS workflow_counts_json,
           COALESCE((SELECT json_group_object(state, amount) FROM (
             SELECT activity AS state, COUNT(*) AS amount
             FROM projected WHERE projected.session_id = page.id GROUP BY activity)), '{}')
             AS activity_counts_json,
           (SELECT json_object(
               'id', id, 'activity', activity, 'execution_state', execution_state,
               'lifecycle', lifecycle, 'workflow_state', workflow_state
             ) FROM projected WHERE projected.session_id = page.id
             ORDER BY creation_order ASC, id ASC LIMIT 1) AS first_agent_json,
           (SELECT json_object(
               'id', id, 'activity', activity, 'execution_state', execution_state,
               'lifecycle', lifecycle, 'workflow_state', workflow_state
             ) FROM projected WHERE projected.session_id = page.id
             ORDER BY creation_order DESC, id DESC LIMIT 1) AS last_agent_json
    FROM meta LEFT JOIN page ON 1 = 1 ORDER BY page.last_active DESC, page.id DESC
    """
    )
    with SessionLocal() as db:
        rows = db.execute(text(sql), parameters).mappings().all()
    meta = rows[0]
    items = []
    for row in rows:
        if row["id"] is None:
            continue
        item = dict(row)
        item.pop("total_count", None)
        item["workflow_counts"] = json.loads(item.pop("workflow_counts_json") or "{}")
        item["activity_counts"] = json.loads(item.pop("activity_counts_json") or "{}")
        item["first_agent"] = json.loads(item.pop("first_agent_json") or "null")
        item["last_agent"] = json.loads(item.pop("last_agent_json") or "null")
        items.append(item)
    return {"items": items, "total": int(meta["total_count"] or 0)}


def _release_or_transfer_worktree_writer_lease(
    db: Any, terminal_id: str, *, excluding_terminal_ids: List[str] | None = None
) -> bool:
    """Keep a legacy duplicate writer fenced or release the final owner."""
    lease = (
        db.query(WorktreeWriterLeaseModel)
        .filter(WorktreeWriterLeaseModel.terminal_id == terminal_id)
        .first()
    )
    if lease is None:
        return False
    excluded = set(excluding_terminal_ids or ())
    excluded.add(terminal_id)
    replacement_query = db.query(TerminalModel).filter(
        TerminalModel.launch_worktree == lease.canonical_worktree,
        TerminalModel.write_enabled.is_(True),
        (TerminalModel.runtime_lifecycle.is_(None)) | (TerminalModel.runtime_lifecycle != "exited"),
        TerminalModel.id.notin_(sorted(excluded)),
    )
    replacement = replacement_query.order_by(TerminalModel.id.asc()).first()
    if replacement is None:
        db.delete(lease)
    else:
        lease.terminal_id = replacement.id
    return True


def delete_terminal(terminal_id: str) -> bool:
    """Delete terminal metadata and release its writer lease atomically.

    Callers must first establish positive terminal death.  Keeping this
    transaction narrow prevents an uncertain process/tmux cleanup from
    accidentally reopening the canonical worktree to another writer.
    """
    with SessionLocal() as db:
        _purge_staged_handoff_submissions_for_terminals(db, [terminal_id])
        _release_or_transfer_worktree_writer_lease(db, terminal_id)
        db.flush()
        deleted = db.query(TerminalModel).filter(TerminalModel.id == terminal_id).delete()
        db.commit()
        return deleted > 0


def delete_terminals_by_session(tmux_session: str) -> int:
    """Delete dead-session metadata and release its writer leases atomically."""
    with SessionLocal() as db:
        terminal_ids = [
            row[0]
            for row in db.query(TerminalModel.id)
            .filter(TerminalModel.tmux_session == tmux_session)
            .all()
        ]
        _purge_staged_handoff_submissions_for_terminals(db, terminal_ids)
        for terminal_id in terminal_ids:
            _release_or_transfer_worktree_writer_lease(
                db, terminal_id, excluding_terminal_ids=terminal_ids
            )
        db.flush()
        deleted = (
            db.query(TerminalModel).filter(TerminalModel.tmux_session == tmux_session).delete()
        )
        db.commit()
        return deleted


def list_worktree_writer_leases() -> List[Dict[str, Any]]:
    """Return durable leases with the terminal target needed for reconciliation."""
    _ensure_terminal_worktree_authority_schema()
    with SessionLocal() as db:
        rows = (
            db.query(WorktreeWriterLeaseModel, TerminalModel)
            .outerjoin(TerminalModel, TerminalModel.id == WorktreeWriterLeaseModel.terminal_id)
            .all()
        )
        return [
            {
                "canonical_worktree": lease.canonical_worktree,
                "terminal_id": lease.terminal_id,
                "tmux_session": terminal.tmux_session if terminal is not None else None,
                "tmux_window": terminal.tmux_window if terminal is not None else None,
                "runtime_lifecycle": (terminal.runtime_lifecycle if terminal is not None else None),
            }
            for lease, terminal in rows
        ]


def list_unreconciled_terminal_authorities() -> List[Dict[str, Any]]:
    """Return legacy UNKNOWN-authority rows with their exact tmux targets."""
    _ensure_terminal_worktree_authority_schema()
    with SessionLocal() as db:
        rows = (
            db.query(TerminalModel)
            .filter(
                (TerminalModel.launch_worktree.is_(None))
                | (TerminalModel.write_enabled.is_(None))
                | (TerminalModel.context_role.is_(None))
            )
            .order_by(TerminalModel.id.asc())
            .all()
        )
        return [
            {
                "id": row.id,
                "tmux_session": row.tmux_session,
                "tmux_window": row.tmux_window,
                "launch_worktree": row.launch_worktree,
                "write_enabled": row.write_enabled,
                "context_role": row.context_role,
            }
            for row in rows
        ]


def retire_unreconciled_terminal_authority(terminal_id: str) -> bool:
    """Retire one externally proven-dead UNKNOWN row without inventing authority."""
    _ensure_terminal_worktree_authority_schema()
    with SessionLocal() as db:
        terminal = db.query(TerminalModel).filter(TerminalModel.id == terminal_id).first()
        if terminal is None:
            return False
        if (
            terminal.launch_worktree is not None
            and terminal.write_enabled is not None
            and terminal.context_role is not None
        ):
            return False
        lease = (
            db.query(WorktreeWriterLeaseModel)
            .filter(WorktreeWriterLeaseModel.terminal_id == terminal_id)
            .first()
        )
        if lease is not None:
            return False
        _purge_staged_handoff_submissions_for_terminals(db, [terminal_id])
        deleted = db.query(TerminalModel).filter(TerminalModel.id == terminal_id).delete()
        db.commit()
        return deleted == 1


def release_worktree_writer_lease(terminal_id: str) -> bool:
    """Release one lease after an external positive terminal-death observation."""
    _ensure_terminal_worktree_authority_schema()
    with SessionLocal() as db:
        changed = _release_or_transfer_worktree_writer_lease(db, terminal_id)
        db.commit()
        return changed


def acquire_provider_execution(
    terminal_id: str, workflow_turn_id: int, limit: Optional[int] = None
) -> bool:
    """Atomically acquire one provider-turn slot, idempotently for one turn.

    SQLite's immediate transaction is the admission CAS: concurrent API and
    watchdog processes cannot both observe the last free slot and over-admit.
    A terminal owns at most one slot and a logical turn can execute only once.
    """
    _ensure_provider_execution_schema()
    with SessionLocal() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        canonical = db.get(CapacitySettingsModel, 1)
        effective_limit = int(canonical.max_provider_executions) if canonical is not None else limit
        if effective_limit is None:
            db.rollback()
            raise RuntimeError("provider capacity settings are not initialized")
        existing = (
            db.query(ProviderExecutionLeaseModel)
            .filter(ProviderExecutionLeaseModel.terminal_id == terminal_id)
            .first()
        )
        if existing is not None:
            acquired = existing.workflow_turn_id == workflow_turn_id
            db.commit()
            return acquired
        duplicate_turn = (
            db.query(ProviderExecutionLeaseModel)
            .filter(ProviderExecutionLeaseModel.workflow_turn_id == workflow_turn_id)
            .first()
        )
        if duplicate_turn is not None:
            db.commit()
            return False
        active = db.query(ProviderExecutionLeaseModel).count()
        if active >= effective_limit:
            db.commit()
            return False
        terminal = db.query(TerminalModel).filter(TerminalModel.id == terminal_id).first()
        if terminal is None or terminal.runtime_lifecycle in ("exit_pending", "exited"):
            db.commit()
            return False
        if _terminal_runtime_mutation_blocked(db, terminal_id, datetime.now()):
            db.commit()
            return False
        workflow_exists = (
            db.query(WorkflowModel.id).filter(WorkflowModel.root_terminal_id == terminal_id).first()
            is not None
        )
        admitted_turn = (
            db.query(WorkflowTurnModel.id)
            .join(WorkflowModel, WorkflowTurnModel.workflow_id == WorkflowModel.id)
            .filter(
                WorkflowTurnModel.id == workflow_turn_id,
                WorkflowModel.root_terminal_id == terminal_id,
                WorkflowModel.status == WORKFLOW_OPEN,
            )
            .first()
        )
        # Preserve compatibility for legacy residents that predate workflows,
        # but once a root has durable workflow state only one of its OPEN turns
        # may acquire.  This shares the BEGIN IMMEDIATE fence with terminal
        # closure, preventing a post-close successor lease.
        if workflow_exists and admitted_turn is None:
            db.commit()
            return False
        db.add(
            ProviderExecutionLeaseModel(
                terminal_id=terminal_id,
                workflow_turn_id=workflow_turn_id,
            )
        )
        db.commit()
        return True


def release_provider_execution(terminal_id: str, workflow_turn_id: Optional[int] = None) -> bool:
    """Release one exact provider slot; retries are inert and never decrement."""
    _ensure_provider_execution_schema()
    with SessionLocal() as db:
        query = db.query(ProviderExecutionLeaseModel).filter(
            ProviderExecutionLeaseModel.terminal_id == terminal_id
        )
        if workflow_turn_id is not None:
            query = query.filter(ProviderExecutionLeaseModel.workflow_turn_id == workflow_turn_id)
        changed = query.delete(synchronize_session=False)
        db.commit()
        return changed == 1


def get_provider_execution_turn(terminal_id: str) -> Optional[int]:
    """Snapshot the exact provider turn owned at an observation boundary.

    Callers that infer provider readiness from an external status observation
    must carry this value through to ``release_provider_execution``.  A stale
    observer can then release only the lease it actually observed, never a
    successor acquired after its status probe.
    """
    _ensure_provider_execution_schema()
    with SessionLocal() as db:
        row = (
            db.query(ProviderExecutionLeaseModel.workflow_turn_id)
            .filter(ProviderExecutionLeaseModel.terminal_id == terminal_id)
            .first()
        )
        return cast(Optional[int], row[0]) if row is not None else None


def list_provider_execution_leases() -> List[Dict[str, Any]]:
    """Return the durable active-turn inventory used by status projections."""
    _ensure_provider_execution_schema()
    with SessionLocal() as db:
        rows = db.query(ProviderExecutionLeaseModel).order_by(
            ProviderExecutionLeaseModel.acquired_at.asc(),
            ProviderExecutionLeaseModel.terminal_id.asc(),
        )
        return [
            {
                "terminal_id": row.terminal_id,
                "workflow_turn_id": row.workflow_turn_id,
                "acquired_at": row.acquired_at,
            }
            for row in rows
        ]


def mark_terminal_runtime_running(terminal_id: str) -> bool:
    """Publish successful provider startup without changing writer authority."""
    _ensure_terminal_worktree_authority_schema()
    with SessionLocal() as db:
        terminal = db.query(TerminalModel).filter(TerminalModel.id == terminal_id).first()
        if terminal is None or terminal.runtime_lifecycle == "exited":
            return False
        terminal.runtime_lifecycle = "running"
        terminal.runtime_exit_requested_at = None
        db.commit()
        return True


def replace_starting_terminal_runtime_identity(
    terminal_id: str,
    *,
    pane_id: str,
    pane_pid: int,
    runtime_generation: str,
    process_start_ticks: int,
) -> bool:
    """Replace launch identity only while startup still owns the DB row.

    Codex may recreate its pane once during the bounded startup retry. This CAS
    publishes that new generation before provider initialization resumes; a
    stale retry can never rewrite a running, pending-exit, or exited runtime.
    """
    _ensure_terminal_worktree_authority_schema()
    if (
        not re.fullmatch(r"%[0-9]+", pane_id)
        or pane_pid <= 1
        or process_start_ticks <= 0
        or not runtime_generation
    ):
        return False
    with SessionLocal() as db:
        changed = (
            db.query(TerminalModel)
            .filter(
                TerminalModel.id == terminal_id,
                TerminalModel.runtime_lifecycle == "starting",
            )
            .update(
                {
                    TerminalModel.runtime_pane_id: pane_id,
                    TerminalModel.runtime_pane_pid: pane_pid,
                    TerminalModel.runtime_generation: runtime_generation,
                    TerminalModel.runtime_generation_origin: "launch",
                    TerminalModel.runtime_process_start_ticks: process_start_ticks,
                    TerminalModel.last_active: datetime.now(),
                },
                synchronize_session=False,
            )
        )
        db.commit()
        return changed == 1


def reconcile_legacy_terminal_runtime_identity(
    terminal_id: str,
    *,
    pane_id: str,
    pane_pid: int,
    runtime_generation: str,
    process_start_ticks: int,
) -> bool:
    """Persist a one-time exact-process fence for a pre-generation runtime."""
    _ensure_terminal_worktree_authority_schema()
    if (
        not re.fullmatch(r"%[0-9]+", pane_id)
        or pane_pid <= 1
        or process_start_ticks <= 0
        or not runtime_generation
    ):
        return False
    with SessionLocal() as db:
        changed = (
            db.query(TerminalModel)
            .filter(
                TerminalModel.id == terminal_id,
                TerminalModel.runtime_generation.is_(None),
                TerminalModel.runtime_pane_id.is_(None),
                TerminalModel.runtime_pane_pid.is_(None),
                TerminalModel.runtime_process_start_ticks.is_(None),
            )
            .update(
                {
                    TerminalModel.runtime_pane_id: pane_id,
                    TerminalModel.runtime_pane_pid: pane_pid,
                    TerminalModel.runtime_generation: runtime_generation,
                    TerminalModel.runtime_generation_origin: "reconciled",
                    TerminalModel.runtime_process_start_ticks: process_start_ticks,
                },
                synchronize_session=False,
            )
        )
        db.commit()
        return changed == 1


def mark_terminal_runtime_exit_pending(terminal_id: str) -> bool:
    """Durably retain ownership while a provider-exit outcome is uncertain."""
    _ensure_terminal_worktree_authority_schema()
    with SessionLocal() as db:
        terminal = db.query(TerminalModel).filter(TerminalModel.id == terminal_id).first()
        if terminal is None:
            return False
        if terminal.runtime_lifecycle == "exited":
            return True
        terminal.runtime_lifecycle = "exit_pending"
        terminal.runtime_exit_requested_at = terminal.runtime_exit_requested_at or datetime.now()
        db.commit()
        return True


def claim_terminal_runtime_exit(terminal_id: str) -> str:
    """Atomically reserve the sole provider-exit dispatch for one runtime.

    ``dispatch`` is returned only to the transaction that moves a live runtime
    into ``exit_pending``.  Retries observe the durable pending/exited state and
    must never send another command.  Persisting the claim before transport is
    deliberately fail-closed: an uncertain transport outcome retains ownership
    rather than risking a duplicate exit command.
    """
    _ensure_terminal_worktree_authority_schema()
    with SessionLocal() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        terminal = db.query(TerminalModel).filter(TerminalModel.id == terminal_id).first()
        if terminal is None:
            return "missing"
        if terminal.runtime_lifecycle == "exited":
            return "exited"
        if terminal.runtime_lifecycle == "exit_pending":
            return "observe"
        now = datetime.now()
        if (
            terminal.runtime_operation_token is not None
            and terminal.runtime_operation_expires_at is not None
            and terminal.runtime_operation_expires_at > now
        ):
            db.commit()
            return "busy"
        updated = (
            db.query(TerminalModel)
            .filter(
                TerminalModel.id == terminal_id,
                or_(
                    TerminalModel.runtime_lifecycle.is_(None),
                    TerminalModel.runtime_lifecycle.notin_(("exit_pending", "exited")),
                ),
            )
            .update(
                {
                    TerminalModel.runtime_lifecycle: "exit_pending",
                    TerminalModel.runtime_exit_requested_at: now,
                    TerminalModel.runtime_operation_kind: "retire",
                    TerminalModel.runtime_operation_token: uuid.uuid4().hex,
                    TerminalModel.runtime_operation_claimed_at: now,
                    # An exit claim is irreversible and is reconciled by
                    # lifecycle observation, never by a second mutator.
                    TerminalModel.runtime_operation_expires_at: None,
                },
                synchronize_session=False,
            )
        )
        db.commit()
        if updated == 1:
            return "dispatch"
        refreshed = db.query(TerminalModel).filter(TerminalModel.id == terminal_id).first()
        if refreshed is None:
            return "missing"
        return "exited" if refreshed.runtime_lifecycle == "exited" else "observe"


RUNTIME_OPERATION_LEASE_SECONDS = 30


def _terminal_has_pending_provider_reconnect(db, terminal_id: str) -> bool:
    return (
        db.query(WorkflowTurnModel.id)
        .join(WorkflowModel, WorkflowModel.active_turn_id == WorkflowTurnModel.id)
        .filter(
            WorkflowModel.root_terminal_id == terminal_id,
            WorkflowModel.status == WORKFLOW_OPEN,
            WorkflowTurnModel.provider_reconnect_requested_at.is_not(None),
        )
        .first()
        is not None
    )


def _terminal_runtime_mutation_blocked(db, terminal_id: str, now: datetime) -> bool:
    terminal = db.query(TerminalModel).filter(TerminalModel.id == terminal_id).first()
    if terminal is None:
        return False
    if terminal.runtime_lifecycle in ("exit_pending", "exited"):
        return True
    if _terminal_has_pending_provider_reconnect(db, terminal_id):
        return True
    return bool(
        terminal.runtime_operation_token is not None
        and (
            terminal.runtime_operation_expires_at is None
            or terminal.runtime_operation_expires_at > now
        )
    )


def acquire_terminal_runtime_transport(
    terminal_id: str, *, now: Optional[datetime] = None
) -> Optional[str]:
    """Claim the exact live pane for one bounded input/write operation.

    A pending reconnect is authoritative even after its short operation lease
    expires: only the reconnect recovery path may reclaim that crash gap.
    """
    _ensure_terminal_worktree_authority_schema()
    _ensure_workflow_schema()
    now = now or datetime.now()
    token = uuid.uuid4().hex
    with SessionLocal() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        terminal = db.query(TerminalModel).filter(TerminalModel.id == terminal_id).first()
        if terminal is None or terminal.runtime_lifecycle not in (None, "running"):
            db.commit()
            return None
        if _terminal_has_pending_provider_reconnect(db, terminal_id):
            db.commit()
            return None
        operation_live = terminal.runtime_operation_token is not None and (
            terminal.runtime_operation_expires_at is None
            or terminal.runtime_operation_expires_at > now
        )
        if operation_live:
            db.commit()
            return None
        terminal.runtime_operation_kind = "transport"
        terminal.runtime_operation_token = token
        terminal.runtime_operation_claimed_at = now
        terminal.runtime_operation_expires_at = now + timedelta(
            seconds=RUNTIME_OPERATION_LEASE_SECONDS
        )
        db.commit()
        return token


def release_terminal_runtime_operation(terminal_id: str, claim_token: str) -> bool:
    """Release only the exact short-lived pane-operation claim."""
    _ensure_terminal_worktree_authority_schema()
    with SessionLocal() as db:
        released = (
            db.query(TerminalModel)
            .filter(
                TerminalModel.id == terminal_id,
                TerminalModel.runtime_operation_token == claim_token,
                TerminalModel.runtime_operation_kind == "transport",
            )
            .update(
                {
                    TerminalModel.runtime_operation_kind: None,
                    TerminalModel.runtime_operation_token: None,
                    TerminalModel.runtime_operation_claimed_at: None,
                    TerminalModel.runtime_operation_expires_at: None,
                },
                synchronize_session=False,
            )
        )
        db.commit()
        return released == 1


def terminal_runtime_operation_owned(
    terminal_id: str, claim_token: str, operation_kind: str
) -> bool:
    """Check an internal long-lived runtime mutation capability."""
    _ensure_terminal_worktree_authority_schema()
    with SessionLocal() as db:
        return (
            db.query(TerminalModel.id)
            .filter(
                TerminalModel.id == terminal_id,
                or_(
                    TerminalModel.runtime_lifecycle.is_(None),
                    TerminalModel.runtime_lifecycle == "running",
                ),
                TerminalModel.runtime_operation_kind == operation_kind,
                TerminalModel.runtime_operation_token == claim_token,
            )
            .first()
            is not None
        )


def promote_terminal_context_role_to_supervisor(terminal_id: str, agent_profile: str) -> bool:
    """Repair one exact-profile legacy role without broadening the mutation."""
    _ensure_terminal_worktree_authority_schema()
    with SessionLocal() as db:
        updated = (
            db.query(TerminalModel)
            .filter(
                TerminalModel.id == terminal_id,
                TerminalModel.agent_profile == agent_profile,
                TerminalModel.context_role == "work",
            )
            .update(
                {TerminalModel.context_role: "supervisor"},
                synchronize_session=False,
            )
        )
        db.commit()
        return updated == 1


def reconcile_terminal_context_roles_by_topology(*, dry_run: bool = False) -> int:
    """Classify live residency by durable session root and child parentage.

    The earliest non-child terminal in each immutable session lifetime is the
    resident conductor. Child-assignment parentage always wins and consumes a
    work-context slot, regardless of profile role or execution mode.
    """
    _ensure_terminal_worktree_authority_schema()
    _ensure_child_assignment_schema()
    with SessionLocal() as db:
        ordered_ids = [
            str(row[0])
            for row in db.connection()
            .exec_driver_sql(
                "SELECT id FROM terminals "
                "WHERE runtime_lifecycle IS NULL OR runtime_lifecycle != 'exited' "
                "ORDER BY rowid"
            )
            .fetchall()
        ]
        by_id = {
            str(row.id): row
            for row in db.query(TerminalModel).filter(TerminalModel.id.in_(ordered_ids)).all()
        }
        rows = [by_id[terminal_id] for terminal_id in ordered_ids if terminal_id in by_id]
        child_ids = {str(row[0]) for row in db.query(ChildAssignmentModel.child_terminal_id).all()}
        roots: dict[str, str] = {}
        for row in rows:
            terminal_id = str(row.id)
            if terminal_id in child_ids:
                continue
            session_key = str(row.session_id or f"legacy:{row.tmux_session}")
            roots.setdefault(session_key, terminal_id)
        changes: list[tuple[str, str]] = []
        for row in rows:
            terminal_id = str(row.id)
            session_key = str(row.session_id or f"legacy:{row.tmux_session}")
            desired = (
                "supervisor"
                if terminal_id not in child_ids and roots.get(session_key) == terminal_id
                else "work"
            )
            if row.context_role != desired:
                changes.append((terminal_id, desired))
        if not dry_run:
            for terminal_id, desired in changes:
                db.query(TerminalModel).filter(TerminalModel.id == terminal_id).update(
                    {TerminalModel.context_role: desired}, synchronize_session=False
                )
            db.commit()
        return len(changes)


def mark_terminal_runtime_exited(terminal_id: str) -> bool:
    """Atomically retire runtime and provider ownership while preserving history.

    Callers must positively establish provider/tmux/process death first.  The
    terminal row, session association, logs, Inbox, results, and workflows are
    intentionally retained; runtime lifecycle and both runtime-owned leases
    cross their terminal boundary in one transaction.
    """
    _ensure_terminal_worktree_authority_schema()
    _ensure_provider_execution_schema()
    with SessionLocal() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        terminal = db.query(TerminalModel).filter(TerminalModel.id == terminal_id).first()
        if terminal is None:
            return False
        terminal.runtime_lifecycle = "exited"
        terminal.runtime_exited_at = terminal.runtime_exited_at or datetime.now()
        terminal.runtime_operation_kind = None
        terminal.runtime_operation_token = None
        terminal.runtime_operation_claimed_at = None
        terminal.runtime_operation_expires_at = None
        lease = (
            db.query(WorktreeWriterLeaseModel)
            .filter(WorktreeWriterLeaseModel.terminal_id == terminal_id)
            .first()
        )
        if lease is not None:
            db.delete(lease)
        execution = (
            db.query(ProviderExecutionLeaseModel)
            .filter(ProviderExecutionLeaseModel.terminal_id == terminal_id)
            .first()
        )
        if execution is not None:
            db.delete(execution)
        db.commit()
        return True


def create_inbox_message(sender_id: str, receiver_id: str, message: str) -> InboxMessage:
    """Create inbox message with status=MessageStatus.PENDING."""
    with SessionLocal() as db:
        inbox_msg = InboxModel(
            sender_id=sender_id,
            receiver_id=receiver_id,
            message=message,
            status=MessageStatus.PENDING.value,
        )
        db.add(inbox_msg)
        db.commit()
        db.refresh(inbox_msg)
        return _inbox_model_to_message(inbox_msg)


def ensure_workflow_turn_for_inbox(message_id: int) -> Optional[int]:
    """Attach one durable provider turn to a legacy or ordinary Inbox row.

    Managed callbacks already create their Inbox row and turn atomically. An
    ordinary public Inbox row historically did not, so its watchdog/API fast
    paths could reach physical transport without provider admission. This
    transaction upgrades that row in place and preserves its FIFO timestamp.
    """
    _ensure_workflow_schema()
    with SessionLocal() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        inbox = (
            db.query(InboxModel)
            .filter(
                InboxModel.id == message_id,
                InboxModel.status == MessageStatus.PENDING.value,
            )
            .first()
        )
        if inbox is None:
            db.commit()
            return None
        existing = (
            db.query(WorkflowTurnModel)
            .filter(WorkflowTurnModel.inbox_message_id == message_id)
            .first()
        )
        if existing is not None:
            db.commit()
            return cast(int, existing.id)

        workflow = _open_workflow(db, str(inbox.receiver_id), create=True)
        assert workflow is not None
        if workflow.status != WORKFLOW_OPEN:
            # Ordinary Inbox delivery has always been allowed to begin fresh
            # work after a previous workflow reached a terminal state.
            workflow = WorkflowModel(root_terminal_id=str(inbox.receiver_id), status=WORKFLOW_OPEN)
            db.add(workflow)
            db.flush()
        turn = WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="inbox_message",
            dedupe_key=f"inbox:{message_id}",
            payload=inbox.message,
            inbox_message_id=message_id,
            state=TURN_QUEUED,
            created_at=inbox.created_at,
        )
        db.add(turn)
        workflow.updated_at = datetime.now()
        db.commit()
        return cast(int, turn.id)


def schedule_managed_handoff_continuation(
    parent_terminal_id: str, child_terminal_id: str, message: str
) -> Dict[str, Any]:
    """Atomically queue one admitted same-child handoff recovery continuation.

    A managed handoff may retain its terminal and authoritative awaiting result
    after a non-substantive provider final.  A parent follow-up to that exact
    child must therefore never use the generic Inbox-only path: the Inbox row
    and successor child workflow turn are one durable unit.  The predecessor
    turn is part of the dedupe key, so retries of a parent continuation cannot
    manufacture ``N+2`` while the child has not admitted a new turn.
    """
    _ensure_child_assignment_schema()
    _ensure_delegation_result_schema()
    _ensure_workflow_schema()
    with SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel)
            .filter_by(
                parent_terminal_id=parent_terminal_id,
                child_terminal_id=child_terminal_id,
            )
            .first()
        )
        if assignment is None:
            return {"managed": False}
        existing_result = (
            db.query(DelegationResultModel)
            .filter_by(child_assignment_id=assignment.id, delegation_kind="handoff")
            .first()
        )
        if not assignment.status.startswith("handoff_") and existing_result is None:
            return {"managed": False}
        parent_workflow = _open_workflow(db, parent_terminal_id, create=False)
        if parent_workflow is None or parent_workflow.status != WORKFLOW_OPEN:
            return {"managed": True, "accepted": False, "reason_code": "PARENT_WORKFLOW_CLOSED"}
        if assignment.status == ChildAssignmentStatus.CANCELLED.value:
            return {"managed": True, "accepted": False, "reason_code": "HANDOFF_CANCELLED"}
        if assignment.status not in (
            ChildAssignmentStatus.HANDOFF_AWAITING_RESULT.value,
            ChildAssignmentStatus.HANDOFF_RECOVERY_AWAITING_RESULT.value,
        ):
            return {"managed": True, "accepted": False, "reason_code": "HANDOFF_NOT_AWAITING"}

        child_workflow = _open_workflow(db, child_terminal_id, create=False)
        if child_workflow is None or child_workflow.status != WORKFLOW_OPEN:
            return {"managed": True, "accepted": False, "reason_code": "CHILD_WORKFLOW_CLOSED"}
        if child_workflow.active_turn_id is None:
            return {"managed": True, "accepted": False, "reason_code": "CHILD_TURN_MISSING"}

        predecessor = (
            db.query(WorkflowTurnModel)
            .filter(
                WorkflowTurnModel.id == child_workflow.active_turn_id,
                WorkflowTurnModel.workflow_id == child_workflow.id,
            )
            .first()
        )
        if (
            predecessor is None
            or not db.query(WorkflowTurnReceiptModel)
            .filter_by(workflow_turn_id=predecessor.id, receiver_terminal_id=child_terminal_id)
            .first()
        ):
            return {"managed": True, "accepted": False, "reason_code": "CHILD_TURN_NOT_ADMITTED"}

        dedupe_key = f"handoff-recovery:{assignment.id}:{predecessor.id}"
        existing_turn = (
            db.query(WorkflowTurnModel)
            .filter(
                WorkflowTurnModel.workflow_id == child_workflow.id,
                WorkflowTurnModel.dedupe_key == dedupe_key,
            )
            .first()
        )
        if existing_turn is not None:
            existing_message = (
                db.query(InboxModel).filter_by(id=existing_turn.inbox_message_id).first()
            )
            return {
                "managed": True,
                "accepted": True,
                "duplicate": True,
                "turn_id": existing_turn.id,
                "message": (
                    _inbox_model_to_message(existing_message)
                    if existing_message is not None
                    else None
                ),
            }

        result = _create_result_for_assignment(db, assignment, "handoff", parent_workflow)
        recovery_events = _handoff_recovery_count(db, result.id)
        if recovery_events >= _MAX_HANDOFF_EXIT_RECOVERY_CYCLES:
            _terminalize_handoff_recovery_exhausted(db, assignment, result, child_terminal_id)
            db.commit()
            return {"managed": True, "accepted": False, "reason_code": "HANDOFF_RECOVERY_EXHAUSTED"}

        inbox = InboxModel(
            sender_id=parent_terminal_id,
            receiver_id=child_terminal_id,
            message=message,
            status=MessageStatus.PENDING.value,
            kind="handoff_recovery_continuation",
        )
        db.add(inbox)
        db.flush()
        successor = WorkflowTurnModel(
            workflow_id=child_workflow.id,
            kind="handoff_recovery",
            dedupe_key=dedupe_key,
            payload=message,
            inbox_message_id=inbox.id,
            state=TURN_QUEUED,
        )
        db.add(successor)
        try:
            # Materialize the turn before recording its identity and before
            # commit, so the unique dedupe constraint also fences concurrent
            # schedulers before either can append recovery evidence.
            db.flush()
            assignment.status = ChildAssignmentStatus.HANDOFF_RECOVERY_AWAITING_RESULT.value
            assignment.updated_at = datetime.now()
            _record_result_event(
                db,
                result.id,
                f"handoff-continuation-recovery:{assignment.id}:{recovery_events + 1}",
                "handoff-continuation-recovery",
                "cao_lifecycle",
                parent_terminal_id,
                predecessor.id,
                {"cycle": recovery_events + 1, "successor_turn_id": successor.id},
            )
            db.commit()
        except IntegrityError:
            # The workflow-turn uniqueness constraint is the cross-process
            # exactly-once fence.  A concurrent scheduler owns the original
            # continuation; discard this transaction and return its identity.
            db.rollback()
            existing_turn = (
                db.query(WorkflowTurnModel)
                .filter(
                    WorkflowTurnModel.workflow_id == child_workflow.id,
                    WorkflowTurnModel.dedupe_key == dedupe_key,
                )
                .first()
            )
            if existing_turn is None:
                raise
            existing_message = (
                db.query(InboxModel).filter_by(id=existing_turn.inbox_message_id).first()
            )
            return {
                "managed": True,
                "accepted": True,
                "duplicate": True,
                "turn_id": existing_turn.id,
                "message": (
                    _inbox_model_to_message(existing_message)
                    if existing_message is not None
                    else None
                ),
            }
        return {
            "managed": True,
            "accepted": True,
            "duplicate": False,
            "turn_id": successor.id,
            "message": _inbox_model_to_message(inbox),
        }


def get_pending_messages(receiver_id: str, limit: int = 1) -> List[InboxMessage]:
    """Get pending messages ordered by created_at ASC (oldest first)."""
    return get_inbox_messages(receiver_id, limit=limit, status=MessageStatus.PENDING)


def get_inbox_messages(
    receiver_id: str, limit: int = 10, status: Optional[MessageStatus] = None
) -> List[InboxMessage]:
    """Get inbox messages with optional status filter ordered by created_at ASC (oldest first).

    Args:
        receiver_id: Terminal ID to get messages for
        limit: Maximum number of messages to return (default: 10)
        status: Optional filter by message status (None = all statuses)

    Returns:
        List of inbox messages ordered by creation time (oldest first)
    """
    with SessionLocal() as db:
        query = db.query(InboxModel).filter(InboxModel.receiver_id == receiver_id)

        if status is not None:
            query = query.filter(InboxModel.status == status.value)

        messages = query.order_by(InboxModel.created_at.asc()).limit(limit).all()

        return [_inbox_model_to_message(msg) for msg in messages]


def update_message_status(message_id: int, status: MessageStatus) -> bool:
    """Update message status to MessageStatus.DELIVERED or MessageStatus.FAILED."""
    with SessionLocal() as db:
        message = db.query(InboxModel).filter(InboxModel.id == message_id).first()
        if message:
            message.status = status.value
            db.commit()
            return True
        return False


def keep_managed_handoff_continuation_retryable(message_id: int) -> bool:
    """Retain one failed managed continuation on the normal pending retry path.

    This deliberately recognizes only the Inbox row atomically paired with a
    queued same-child handoff continuation. Ordinary failed messages retain
    their terminal ``failed`` semantics, while the workflow turn's bounded
    transport retry policy remains the authority for this narrow path.
    """
    _ensure_child_assignment_schema()
    _ensure_workflow_schema()
    with SessionLocal() as db:
        message = (
            db.query(InboxModel)
            .filter(
                InboxModel.id == message_id,
                InboxModel.kind == "handoff_recovery_continuation",
            )
            .first()
        )
        if message is None:
            return False
        turn = (
            db.query(WorkflowTurnModel)
            .filter(
                WorkflowTurnModel.inbox_message_id == message.id,
                WorkflowTurnModel.kind == "handoff_recovery",
                WorkflowTurnModel.state == TURN_QUEUED,
            )
            .first()
        )
        if turn is None:
            return False
        workflow = (
            db.query(WorkflowModel)
            .filter(
                WorkflowModel.id == turn.workflow_id,
                WorkflowModel.root_terminal_id == message.receiver_id,
                WorkflowModel.status == WORKFLOW_OPEN,
            )
            .first()
        )
        assignment = (
            db.query(ChildAssignmentModel)
            .filter(
                ChildAssignmentModel.parent_terminal_id == message.sender_id,
                ChildAssignmentModel.child_terminal_id == message.receiver_id,
                ChildAssignmentModel.status
                == ChildAssignmentStatus.HANDOFF_RECOVERY_AWAITING_RESULT.value,
            )
            .first()
        )
        if workflow is None or assignment is None:
            return False
        message.status = MessageStatus.PENDING.value
        db.commit()
        return True


WORKFLOW_OPEN = "open"
WORKFLOW_TERMINAL = "terminal"
WORKFLOW_OWNER_GATE = "owner_gate"
WORKFLOW_CANCELLED = "cancelled"
TURN_QUEUED = "queued"
TURN_CLAIMED = "claimed"
TURN_SENT = "sent"
TURN_FINISHED = "finished"
TURN_CANCELLED = "cancelled"
# A provider-final observation for a turn whose receiver never admitted the
# envelope is not progress.  Keep this distinct from ``None`` (no workflow or
# no sendable turn) so callers and deterministic tests can prove that the
# durable state was deliberately left untouched.
DEFER_UNADMITTED = "DEFER_UNADMITTED"
DEFER_STABLE_READY = "DEFER_STABLE_READY"
WORKFLOW_TURN_CLAIM_LEASE_SECONDS = 30
WORKFLOW_PROVIDER_RECONNECT_LEASE_SECONDS = 30
MAX_WORKFLOW_PROVIDER_RECONNECT_ATTEMPTS = 3
PROVIDER_RECONNECT_RESERVED = "reserved"
PROVIDER_RECONNECT_LAUNCHED = "launch_dispatched"
PROVIDER_RECONNECT_READY = "runtime_ready"
PROVIDER_RECONNECT_SUCCEEDED = "succeeded"
PROVIDER_RECONNECT_FAILED = "failed"
PROVIDER_RECONNECT_RECOVERY_EXHAUSTED_REASON = (
    "Provider reconnect recovery exhausted after three bounded exact-resume attempts. "
    "No further provider launch will occur automatically."
)
WORKFLOW_READY_AFTER_PROCESSING_GRACE_SECONDS = 3
WORKFLOW_READY_WITHOUT_PROCESSING_GRACE_SECONDS = 30
# Provider finals are transport observations, not semantic mission outcomes.
# Keep exactly one durable successor moving until the workflow reaches an
# explicit terminal state. The capped delay prevents a tight paid loop. A
# separate, durable no-progress circuit breaker stops a malfunctioning provider
# from purchasing work forever: ordinary child results or direct owner input
# reset this counter, so productive multi-hour workflows are unaffected.
MAX_OPEN_FINAL_CONTINUATION_DELAY_SECONDS = 30
MAX_AUTOMATIC_OPEN_FINAL_NO_PROGRESS = 64
OPEN_FINAL_CIRCUIT_BREAKER_REASON = (
    "Automatic continuation paused when 65 consecutive provider finals produced no "
    "durable workflow progress. Review the provider/runtime before resuming."
)


def _dispatch_workflow_notification_fail_open(
    root_terminal_id: str, event_kind: str, workflow_id: int
) -> None:
    """Keep external notification failures outside durable workflow authority."""
    try:
        from cli_agent_orchestrator.services.telegram_notification_service import (
            dispatch_workflow_notification,
        )

        dispatch_workflow_notification(root_terminal_id, event_kind, workflow_id=workflow_id)
    except Exception:
        # Never interpolate the exception: request errors can contain a bot-token URL.
        logger.warning(
            "Telegram lifecycle notification failed safely for event %s",
            event_kind,
        )


def _workflow_has_unadmitted_active_turn(db: Any, workflow: WorkflowModel) -> bool:
    """Return whether the active logical turn still owns the parent receiver.

    ``claimed`` is already an in-flight continuation: the physical transport
    can be executing between its claim and the later ``sent`` receipt.  Treat
    it exactly like an unreceipted ``sent`` turn so no successor may overtake
    the same parent in that window.  A requeued active turn remains the same
    outstanding logical ID and is likewise retried before any successor.
    """
    if workflow.active_turn_id is None:
        return False
    active_turn = (
        db.query(WorkflowTurnModel)
        .filter(
            WorkflowTurnModel.id == workflow.active_turn_id,
            WorkflowTurnModel.workflow_id == workflow.id,
            WorkflowTurnModel.state.in_((TURN_QUEUED, TURN_CLAIMED, TURN_SENT)),
        )
        .first()
    )
    if active_turn is None:
        return False
    return (
        db.query(WorkflowTurnReceiptModel)
        .filter(
            WorkflowTurnReceiptModel.workflow_turn_id == active_turn.id,
            WorkflowTurnReceiptModel.receiver_terminal_id == workflow.root_terminal_id,
        )
        .first()
        is None
    )


def _workflow_has_unadmitted_active_continuation(db: Any, workflow: WorkflowModel) -> bool:
    """Narrow the shared fence to callbacks, not the initial external input."""
    if not _workflow_has_unadmitted_active_turn(db, workflow):
        return False
    active_turn = (
        db.query(WorkflowTurnModel)
        .filter(
            WorkflowTurnModel.id == workflow.active_turn_id,
            WorkflowTurnModel.workflow_id == workflow.id,
        )
        .first()
    )
    return active_turn is not None and active_turn.kind != "external_input"


def _active_child_assignment_statuses() -> tuple[str, ...]:
    """Return relation states that can still affect a parent workflow."""
    return (
        ChildAssignmentStatus.AWAITING_RESULT.value,
        ChildAssignmentStatus.RESULT_QUEUED.value,
        ChildAssignmentStatus.RESULT_DELIVERED.value,
        ChildAssignmentStatus.RESULT_FAILED.value,
        ChildAssignmentStatus.HANDOFF_AWAITING_RESULT.value,
        ChildAssignmentStatus.HANDOFF_RECOVERY_AWAITING_RESULT.value,
        ChildAssignmentStatus.HANDOFF_DIRECT_RESULT_CLAIMED.value,
        ChildAssignmentStatus.HANDOFF_RESULT_QUEUED.value,
        ChildAssignmentStatus.HANDOFF_RESULT_DELIVERED.value,
        ChildAssignmentStatus.HANDOFF_RESULT_FAILED.value,
    )


def _cancel_parent_assignments(db, parent_terminal_id: str, now: datetime) -> int:
    """Fence unresolved callback edges without discarding a direct V1 claim."""
    assignments = (
        db.query(ChildAssignmentModel)
        .filter(
            ChildAssignmentModel.parent_terminal_id == parent_terminal_id,
            ChildAssignmentModel.status.in_(_active_child_assignment_statuses()),
        )
        .all()
    )
    for assignment in assignments:
        # A direct handoff result has already crossed its strict final-output
        # boundary and was durably finalized.  Parent cancellation must not
        # erase that exact claim: it is returnable to the waiting MCP caller,
        # but creates no new Inbox wake and never triggers a history scan.
        if assignment.status == ChildAssignmentStatus.HANDOFF_DIRECT_RESULT_CLAIMED.value:
            continue
        kind = "handoff" if assignment.status.startswith("handoff_") else "assign"
        assignment.status = ChildAssignmentStatus.CANCELLED.value
        assignment.updated_at = now
        result = _create_result_for_assignment(
            db, assignment, kind, _open_workflow(db, parent_terminal_id, create=False)
        )
        if result.status == DelegationResultStatus.AWAITING.value:
            result.status = DelegationResultStatus.CANCELLED.value
            result.reason_code = "parent_cancelled"
            result.finalized_at = result.updated_at = now
            _record_result_event(
                db,
                result.id,
                f"result-cancelled:{assignment.id}:parent",
                "cancelled",
                "cao_lifecycle",
                parent_terminal_id,
            )
            _purge_staged_handoff_submission(db, result.id)
    return len(assignments)


def _open_workflow(db, root_terminal_id: str, create: bool) -> Optional[WorkflowModel]:
    workflow = (
        db.query(WorkflowModel)
        .filter(WorkflowModel.root_terminal_id == root_terminal_id)
        .order_by(WorkflowModel.id.desc())
        .first()
    )
    if workflow is None and create:
        workflow = WorkflowModel(root_terminal_id=root_terminal_id, status=WORKFLOW_OPEN)
        db.add(workflow)
        db.flush()
    return workflow


def _retirement_quiescence_allows_commit(db, terminal_id: str) -> bool:
    """Atomically fence a pending input/descendant write against retirement.

    A retiring relation exists only for an assigned child.  For that relation,
    a conditional update deliberately acquires SQLite writer serialization
    before the caller reads or mutates workflow state, and is repeated before
    commit.  An input/registration that starts first therefore commits before
    retirement can claim; retirement's revalidation then observes the new
    workflow or descendant.  A retirement claim that starts first makes the
    conditional update fail and rolls the new work back.  Engines with row
    locks receive the same compare-and-mutate boundary rather than relying on
    ``FOR UPDATE``.
    """
    assignment = (
        db.query(ChildAssignmentModel)
        .filter(ChildAssignmentModel.child_terminal_id == terminal_id)
        .first()
    )
    if assignment is None:
        return True
    return (
        cast(
            int,
            db.query(ChildAssignmentModel)
            .filter(
                ChildAssignmentModel.child_terminal_id == terminal_id,
                ChildAssignmentModel.retirement_claim_token.is_(None),
                ChildAssignmentModel.retirement_cleanup_completed_at.is_(None),
            )
            .update(
                {ChildAssignmentModel.updated_at: datetime.now()},
                synchronize_session=False,
            ),
        )
        == 1
    )


def _prepare_workflow_input(
    root_terminal_id: str,
    *,
    payload: Optional[str] = None,
    transport_binding: Optional[str] = None,
    defer_while_runtime_owned: bool = False,
) -> Optional[Dict[str, Any]]:
    """Persist one input without replacing a reconnect-owned active turn."""
    _ensure_workflow_schema()
    _ensure_child_assignment_schema()
    with SessionLocal() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        if not _retirement_quiescence_allows_commit(db, root_terminal_id):
            return None
        workflow = _open_workflow(db, root_terminal_id, create=True)
        assert workflow is not None
        if workflow.status != WORKFLOW_OPEN:
            # A deliberate new user input starts a new semantic workflow after
            # a prior terminal/owner/cancelled outcome.
            workflow = WorkflowModel(root_terminal_id=root_terminal_id, status=WORKFLOW_OPEN)
            db.add(workflow)
            db.flush()
        runtime_owned = False
        if defer_while_runtime_owned:
            terminal = db.query(TerminalModel).filter(TerminalModel.id == root_terminal_id).first()
            runtime_owned = bool(
                terminal
                and (
                    terminal.runtime_lifecycle in ("exit_pending", "exited")
                    or terminal.runtime_operation_kind in ("reconnect", "retire")
                    or _terminal_has_pending_provider_reconnect(db, root_terminal_id)
                )
            )
        turn = WorkflowTurnModel(
            workflow_id=workflow.id,
            kind="external_input",
            dedupe_key=f"external:{datetime.now().isoformat()}:{id(workflow)}",
            payload=payload if runtime_owned else None,
            state=TURN_QUEUED if runtime_owned else TURN_SENT,
            transport_binding=transport_binding,
        )
        db.add(turn)
        db.flush()
        # The direct input about to reach the provider is the only turn whose
        # public MCP calls may be admitted.  A later input replaces this
        # binding, so an old prompt cannot borrow its retained receipt.
        if not runtime_owned:
            workflow.active_turn_id = turn.id
        # A direct owner/user input is genuine progress and re-arms the bounded
        # automatic continuation path for this still-open mission.
        workflow.no_progress_count = 0
        workflow.updated_at = datetime.now()
        if not _retirement_quiescence_allows_commit(db, root_terminal_id):
            db.rollback()
            return None
        db.commit()
        return {"turn_id": cast(int, turn.id), "queued": runtime_owned}


def _start_workflow_input(
    root_terminal_id: str, transport_binding: Optional[str] = None
) -> Optional[int]:
    """Persist one input and optionally bind it to CAO's internal transport."""
    prepared = _prepare_workflow_input(root_terminal_id, transport_binding=transport_binding)
    return cast(int, prepared["turn_id"]) if prepared is not None else None


def prepare_workflow_input(root_terminal_id: str, payload: str) -> Optional[Dict[str, Any]]:
    """Prepare a public/scheduled input, queueing behind runtime recovery."""
    return _prepare_workflow_input(
        root_terminal_id,
        payload=payload,
        defer_while_runtime_owned=True,
    )


def start_workflow_input(root_terminal_id: str) -> Optional[int]:
    """Persist a public input before it reaches the provider."""
    return _start_workflow_input(root_terminal_id)


def queue_workflow_input_for_provider(
    root_terminal_id: str, logical_turn_id: int, payload: str
) -> bool:
    """Retain an unsent direct input in the existing workflow-turn queue."""
    _ensure_workflow_schema()
    with SessionLocal() as db:
        workflow = _open_workflow(db, root_terminal_id, create=False)
        if (
            workflow is None
            or workflow.status != WORKFLOW_OPEN
            or workflow.active_turn_id != logical_turn_id
        ):
            return False
        changed = (
            db.query(WorkflowTurnModel)
            .filter(
                WorkflowTurnModel.id == logical_turn_id,
                WorkflowTurnModel.workflow_id == workflow.id,
                WorkflowTurnModel.state == TURN_SENT,
            )
            .update(
                {
                    WorkflowTurnModel.payload: payload,
                    WorkflowTurnModel.state: TURN_QUEUED,
                    WorkflowTurnModel.claim_token: None,
                    WorkflowTurnModel.claim_expires_at: None,
                    WorkflowTurnModel.updated_at: datetime.now(),
                },
                synchronize_session=False,
            )
        )
        db.commit()
        return changed == 1


def issue_workflow_input_binding(root_terminal_id: str) -> Optional[str]:
    """Issue an opaque binding for one direct CAO assign/handoff delivery."""
    binding = secrets.token_urlsafe(32)
    if _start_workflow_input(root_terminal_id, transport_binding=binding) is None:
        return None
    return binding


def resolve_workflow_input_binding(root_terminal_id: str, binding: str) -> Optional[int]:
    """Resolve a direct binding only while its workflow turn remains current."""
    _ensure_workflow_schema()
    with SessionLocal() as db:
        turn = (
            db.query(WorkflowTurnModel)
            .join(WorkflowModel, WorkflowTurnModel.workflow_id == WorkflowModel.id)
            .filter(
                WorkflowModel.root_terminal_id == root_terminal_id,
                WorkflowModel.status == WORKFLOW_OPEN,
                WorkflowModel.active_turn_id == WorkflowTurnModel.id,
                WorkflowTurnModel.transport_binding == binding,
            )
            .first()
        )
        return cast(int, turn.id) if turn is not None else None


def activate_workflow_turn(root_terminal_id: str, logical_turn_id: int) -> bool:
    """Bind the next provider input to its durable logical turn before send.

    This is deliberately a server-side transport action, not a model-selected
    value.  Replays activate the same turn; a newer input atomically replaces
    it and fences historical receipts/effects.
    """
    _ensure_workflow_schema()
    with SessionLocal() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        if _terminal_runtime_mutation_blocked(db, root_terminal_id, datetime.now()):
            db.rollback()
            return False
        workflow = _open_workflow(db, root_terminal_id, create=False)
        if workflow is None or workflow.status != WORKFLOW_OPEN:
            return False
        turn = (
            db.query(WorkflowTurnModel)
            .filter(
                WorkflowTurnModel.id == logical_turn_id,
                WorkflowTurnModel.workflow_id == workflow.id,
            )
            .first()
        )
        if turn is None:
            return False
        workflow.active_turn_id = logical_turn_id
        workflow.updated_at = datetime.now()
        db.commit()
        return True


def activate_workflow_turn_for_inbox(message_id: int) -> Optional[int] | str:
    """Bind a queued Inbox continuation without bypassing an unadmitted send.

    An already-sent active turn owns the receiver until that receiver records
    its durable receipt.  In particular, a later Inbox callback must not
    replace that binding merely because the provider became idle.  Returning
    :data:`DEFER_UNADMITTED` is deliberately byte-stable: callers retain the
    Inbox row and its queued turn for a later retry.
    """
    _ensure_workflow_schema()
    with SessionLocal() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        turn = (
            db.query(WorkflowTurnModel)
            .filter(WorkflowTurnModel.inbox_message_id == message_id)
            .first()
        )
        if turn is None or turn.state != TURN_QUEUED:
            return None
        workflow = db.query(WorkflowModel).filter(WorkflowModel.id == turn.workflow_id).first()
        if workflow is None or workflow.status != WORKFLOW_OPEN:
            return None
        if _terminal_runtime_mutation_blocked(
            db, cast(str, workflow.root_terminal_id), datetime.now()
        ):
            db.rollback()
            return DEFER_UNADMITTED

        active_turn_id = workflow.active_turn_id
        if active_turn_id != turn.id and _workflow_has_unadmitted_active_turn(db, workflow):
            # Do not mutate the active binding, queued turn, or any
            # timestamps. A receipt racing this read wins on the next retry;
            # this observer remains a no-op.
            return DEFER_UNADMITTED

        # The compare-and-set fences a concurrent terminal/cancel/new-input
        # transition. Inbox delivery then takes the normal turn claim before
        # transport, so only one ready tick can own the actual send.
        active_predicate = (
            WorkflowModel.active_turn_id.is_(None)
            if active_turn_id is None
            else WorkflowModel.active_turn_id == active_turn_id
        )
        activated = (
            db.query(WorkflowModel)
            .filter(
                WorkflowModel.id == workflow.id,
                WorkflowModel.root_terminal_id == workflow.root_terminal_id,
                WorkflowModel.status == WORKFLOW_OPEN,
                active_predicate,
            )
            .update(
                {
                    WorkflowModel.active_turn_id: turn.id,
                    WorkflowModel.updated_at: datetime.now(),
                },
                synchronize_session=False,
            )
        )
        if activated != 1:
            db.rollback()
            return None
        db.commit()
        return turn.id


def ensure_open_workflow(root_terminal_id: str) -> Optional[int]:
    """Return the existing OPEN workflow, creating one for delegated work."""
    _ensure_workflow_schema()
    with SessionLocal() as db:
        workflow = _open_workflow(db, root_terminal_id, create=True)
        assert workflow is not None
        if workflow.status != WORKFLOW_OPEN:
            return None
        db.commit()
        return workflow.id


def get_workflow_status(root_terminal_id: str) -> Optional[str]:
    _ensure_workflow_schema()
    with SessionLocal() as db:
        workflow = _open_workflow(db, root_terminal_id, create=False)
        return workflow.status if workflow is not None else None


def get_terminal_workflow_projection(terminal_id: str) -> Dict[str, Optional[str]]:
    """Project durable workflow/result truth for one terminal's primary UI state.

    Provider process state is deliberately absent here. The projection is a
    read model over the terminal's own workflow and every directly related
    child-assignment/result relation (as either parent or child). Raw durable
    values remain available as diagnostics, while ``state`` is the single
    API/UI lifecycle authority.
    """
    _ensure_workflow_schema()
    _ensure_child_assignment_schema()
    _ensure_delegation_result_schema()
    with SessionLocal() as db:
        workflow = _open_workflow(db, terminal_id, create=False)
        raw_workflow = workflow.status if workflow is not None else None
        assignments = (
            db.query(ChildAssignmentModel)
            .filter(
                (ChildAssignmentModel.child_terminal_id == terminal_id)
                | (ChildAssignmentModel.parent_terminal_id == terminal_id)
            )
            .order_by(ChildAssignmentModel.updated_at.desc(), ChildAssignmentModel.id.desc())
            .all()
        )
        assignment = assignments[0] if assignments else None
        result = (
            db.query(DelegationResultModel).filter_by(child_assignment_id=assignment.id).first()
            if assignment is not None
            else None
        )

        # A durable workflow terminal transition is the strongest authority.
        if raw_workflow == WORKFLOW_OWNER_GATE:
            state = WORKFLOW_OWNER_GATE
        elif raw_workflow == WORKFLOW_TERMINAL:
            state = "completed"
        elif raw_workflow == WORKFLOW_CANCELLED:
            state = WORKFLOW_CANCELLED
        else:
            # Parent and child both need the same durable relation truth. A
            # parent with a delivered child result is RESULT_READY even though
            # the completed child itself is terminal; conversely, a child can
            # be WAITING/RECOVERABLE before it submits an authoritative result.
            priority = {
                "cancelled": 6,
                "incomplete": 5,
                "failed": 4,
                "result_ready": 3,
                "recoverable": 2,
                "waiting": 1,
            }
            relation_state: Optional[str] = None

            for candidate in assignments:
                candidate_result = (
                    db.query(DelegationResultModel)
                    .filter_by(child_assignment_id=candidate.id)
                    .first()
                )
                candidate_state: Optional[str] = None
                if (
                    candidate_result is not None
                    and candidate_result.status == DelegationResultStatus.INCOMPLETE.value
                ):
                    candidate_state = "incomplete"
                elif candidate.status in (
                    ChildAssignmentStatus.RESULT_FAILED.value,
                    ChildAssignmentStatus.HANDOFF_RESULT_FAILED.value,
                ):
                    candidate_state = "failed"
                elif (
                    candidate.child_terminal_id == terminal_id
                    and candidate_result is not None
                    and candidate_result.status == DelegationResultStatus.CANCELLED.value
                ):
                    candidate_state = WORKFLOW_CANCELLED
                elif (
                    candidate.status == ChildAssignmentStatus.HANDOFF_RECOVERY_AWAITING_RESULT.value
                ):
                    candidate_state = "recoverable"
                elif candidate.status in (
                    ChildAssignmentStatus.AWAITING_RESULT.value,
                    ChildAssignmentStatus.HANDOFF_AWAITING_RESULT.value,
                ):
                    candidate_state = "waiting"
                elif (
                    candidate.status
                    in (
                        ChildAssignmentStatus.HANDOFF_DIRECT_RESULT_CLAIMED.value,
                        ChildAssignmentStatus.RESULT_QUEUED.value,
                        ChildAssignmentStatus.RESULT_DELIVERED.value,
                        ChildAssignmentStatus.HANDOFF_RESULT_QUEUED.value,
                        ChildAssignmentStatus.HANDOFF_RESULT_DELIVERED.value,
                    )
                    and candidate_result is not None
                    and candidate_result.status == DelegationResultStatus.COMPLETE.value
                ):
                    candidate_state = "result_ready"

                if candidate_state is not None and (
                    relation_state is None or priority[candidate_state] > priority[relation_state]
                ):
                    relation_state = candidate_state

            state = relation_state or ("active" if raw_workflow == WORKFLOW_OPEN else None)

        assignment_status = assignment.status if assignment is not None else None
        result_status = result.status if result is not None else None

        return {
            "state": state,
            "workflow_status": raw_workflow,
            "assignment_status": assignment_status,
            "result_status": result_status,
            "delivery_status": assignment_status,
        }


def is_delegated_child_terminal(terminal_id: str) -> bool:
    """Return whether a terminal is an ordinary delegated worker/reviewer."""
    _ensure_child_assignment_schema()
    with SessionLocal() as db:
        return bool(
            db.query(ChildAssignmentModel)
            .filter(
                ChildAssignmentModel.child_terminal_id == terminal_id,
                ChildAssignmentModel.status != ChildAssignmentStatus.CANCELLED.value,
            )
            .first()
        )


def is_managed_structured_handoff_child(terminal_id: str) -> bool:
    """Whether terminal capture must never promote this child to legacy success."""
    _ensure_child_assignment_schema()
    with SessionLocal() as db:
        assignment = db.query(ChildAssignmentModel).filter_by(child_terminal_id=terminal_id).first()
        return bool(
            assignment
            and assignment.status.startswith("handoff_")
            and _handoff_requires_structured_result(db, assignment)
        )


def get_open_workflow_root_terminal_ids() -> List[str]:
    _ensure_workflow_schema()
    with SessionLocal() as db:
        return [
            row[0]
            for row in db.query(WorkflowModel.root_terminal_id)
            .filter(WorkflowModel.status == WORKFLOW_OPEN)
            .all()
        ]


def get_queued_workflow_root_terminal_ids() -> List[str]:
    """Return queued provider inputs oldest-first, with one entry per resident."""
    _ensure_workflow_schema()
    with SessionLocal() as db:
        rows = (
            db.query(WorkflowModel.root_terminal_id)
            .join(WorkflowTurnModel, WorkflowTurnModel.workflow_id == WorkflowModel.id)
            .filter(
                WorkflowModel.status == WORKFLOW_OPEN,
                WorkflowTurnModel.state == TURN_QUEUED,
            )
            .order_by(WorkflowTurnModel.created_at.asc(), WorkflowTurnModel.id.asc())
            .all()
        )
        return list(dict.fromkeys(str(row[0]) for row in rows))


def get_provider_execution_admission_queue() -> List[Dict[str, Any]]:
    """Merge durable Inbox and workflow provider inputs into one fair FIFO.

    Inbox-backed workflow turns are represented only by their Inbox row; their
    transport has additional result/batch CAS boundaries that the generic
    workflow sender must not bypass.  One oldest item per resident is enough:
    a resident cannot execute two provider turns concurrently.
    """
    _ensure_workflow_schema()
    with SessionLocal() as db:
        candidates: List[Dict[str, Any]] = []
        inbox_rows = (
            db.query(InboxModel)
            .filter(InboxModel.status == MessageStatus.PENDING.value)
            .order_by(InboxModel.created_at.asc(), InboxModel.id.asc())
            .all()
        )
        seen_inbox: set[str] = set()
        for row in inbox_rows:
            terminal_id = str(row.receiver_id)
            if terminal_id in seen_inbox:
                continue
            seen_inbox.add(terminal_id)
            candidates.append(
                {
                    "source": "inbox",
                    "terminal_id": terminal_id,
                    "created_at": row.created_at,
                    "source_id": int(row.id),
                }
            )

        workflow_rows = (
            db.query(WorkflowTurnModel, WorkflowModel.root_terminal_id)
            .join(WorkflowModel, WorkflowTurnModel.workflow_id == WorkflowModel.id)
            .filter(
                WorkflowModel.status == WORKFLOW_OPEN,
                WorkflowTurnModel.state == TURN_QUEUED,
                WorkflowTurnModel.inbox_message_id.is_(None),
            )
            .order_by(WorkflowTurnModel.created_at.asc(), WorkflowTurnModel.id.asc())
            .all()
        )
        seen_workflow: set[str] = set()
        for turn, root_terminal_id in workflow_rows:
            terminal_id = str(root_terminal_id)
            if terminal_id in seen_workflow:
                continue
            seen_workflow.add(terminal_id)
            candidates.append(
                {
                    "source": "workflow",
                    "terminal_id": terminal_id,
                    "created_at": turn.created_at,
                    "source_id": int(turn.id),
                }
            )

        candidates.sort(
            key=lambda item: (
                item["created_at"],
                item["source_id"],
                item["source"],
                item["terminal_id"],
            )
        )
        return candidates


def terminal_has_queued_provider_turn(terminal_id: str) -> bool:
    """Whether this resident context is durably waiting for an execution slot."""
    _ensure_workflow_schema()
    with SessionLocal() as db:
        return bool(
            db.query(WorkflowTurnModel.id)
            .join(WorkflowModel, WorkflowTurnModel.workflow_id == WorkflowModel.id)
            .filter(
                WorkflowModel.root_terminal_id == terminal_id,
                WorkflowModel.status == WORKFLOW_OPEN,
                WorkflowTurnModel.state == TURN_QUEUED,
            )
            .first()
        )


def queue_workflow_turn(
    root_terminal_id: str,
    kind: str,
    dedupe_key: str,
    payload: Optional[str] = None,
    inbox_message_id: Optional[int] = None,
    not_before: Optional[datetime] = None,
) -> tuple[Optional[int], bool]:
    """Atomically retain one logical continuation; duplicate retries are inert."""
    _ensure_workflow_schema()
    with SessionLocal() as db:
        workflow = _open_workflow(db, root_terminal_id, create=False)
        if workflow is None or workflow.status != WORKFLOW_OPEN:
            return None, True
        existing = (
            db.query(WorkflowTurnModel)
            .filter(
                WorkflowTurnModel.workflow_id == workflow.id,
                WorkflowTurnModel.dedupe_key == dedupe_key,
            )
            .first()
        )
        if existing is not None:
            return existing.id, True
        turn = WorkflowTurnModel(
            workflow_id=workflow.id,
            kind=kind,
            dedupe_key=dedupe_key,
            payload=payload,
            inbox_message_id=inbox_message_id,
            state=TURN_QUEUED,
            not_before=not_before,
        )
        db.add(turn)
        workflow.updated_at = datetime.now()
        db.commit()
        return turn.id, False


def get_workflow_turn_for_inbox(message_id: int) -> Optional[Dict[str, Any]]:
    _ensure_workflow_schema()
    with SessionLocal() as db:
        turn = (
            db.query(WorkflowTurnModel)
            .filter(WorkflowTurnModel.inbox_message_id == message_id)
            .first()
        )
        if turn is None:
            return None
        workflow = db.query(WorkflowModel).filter(WorkflowModel.id == turn.workflow_id).first()
        if workflow is None:
            return None
        return {
            "turn_id": turn.id,
            "workflow_id": workflow.id,
            "status": workflow.status,
            "kind": turn.kind,
        }


def get_handoff_result_batch_for_inbox(message_id: int) -> List[InboxMessage]:
    """Return all result notices owned by one queued handoff boundary turn.

    Only the turn's anchor Inbox row has ``inbox_message_id``. Later results
    finalized during that same active parent turn remain separate immutable
    notices but share the anchor's successor turn and must be delivered in the
    same parent callback.
    """
    _ensure_child_assignment_schema()
    _ensure_delegation_result_schema()
    _ensure_workflow_schema()
    with SessionLocal() as db:
        turn = (
            db.query(WorkflowTurnModel)
            .filter(
                WorkflowTurnModel.inbox_message_id == message_id,
                WorkflowTurnModel.kind == "handoff_result",
            )
            .first()
        )
        if turn is None:
            return []
        messages = (
            db.query(InboxModel)
            .join(DelegationResultModel, DelegationResultModel.id == InboxModel.result_id)
            .filter(
                DelegationResultModel.workflow_turn_id == turn.id,
                InboxModel.kind == "delegation_result_notice",
                InboxModel.status == MessageStatus.PENDING.value,
            )
            .order_by(InboxModel.id.asc())
            .all()
        )
        return [_inbox_model_to_message(message) for message in messages]


def materialize_deferred_handoff_result_turn_for_inbox(message_id: int) -> bool | str:
    """Attach one deferred handoff result after its parent is admitted.

    A result finalized while another callback is in flight deliberately has no
    workflow turn.  This is not a second queue: the immutable result notice
    remains its one durable transport record until the normal safe-boundary
    turn can be created.  While the active parent has no receipt, this helper
    is a strict no-op.
    """
    _ensure_child_assignment_schema()
    _ensure_delegation_result_schema()
    _ensure_workflow_schema()
    with SessionLocal() as db:
        inbox = (
            db.query(InboxModel)
            .filter(
                InboxModel.id == message_id,
                InboxModel.kind == "delegation_result_notice",
                InboxModel.status == MessageStatus.PENDING.value,
            )
            .first()
        )
        if inbox is None or inbox.result_id is None:
            return False
        result = (
            db.query(DelegationResultModel)
            .filter(DelegationResultModel.id == inbox.result_id)
            .first()
        )
        assignment = (
            db.query(ChildAssignmentModel)
            .filter(
                ChildAssignmentModel.result_message_id == inbox.id,
                ChildAssignmentModel.status == ChildAssignmentStatus.HANDOFF_RESULT_QUEUED.value,
            )
            .first()
        )
        if result is None or assignment is None or result.workflow_turn_id is not None:
            return False
        workflow = _open_workflow(db, assignment.parent_terminal_id, create=False)
        if workflow is None or workflow.status != WORKFLOW_OPEN:
            return False
        if _workflow_has_unadmitted_active_turn(db, workflow):
            return DEFER_UNADMITTED

        boundary_key = f"handoff-result-boundary:{workflow.active_turn_id}"
        turn = (
            db.query(WorkflowTurnModel)
            .filter(
                WorkflowTurnModel.workflow_id == workflow.id,
                WorkflowTurnModel.kind == "handoff_result",
                WorkflowTurnModel.dedupe_key == boundary_key,
                WorkflowTurnModel.state == TURN_QUEUED,
            )
            .order_by(WorkflowTurnModel.id.asc())
            .first()
        )
        if turn is None:
            turn = WorkflowTurnModel(
                workflow_id=workflow.id,
                kind="handoff_result",
                dedupe_key=boundary_key,
                payload=inbox.message,
                inbox_message_id=inbox.id,
                state=TURN_QUEUED,
            )
            db.add(turn)
            db.flush()
        result.workflow_turn_id = turn.id
        workflow.updated_at = datetime.now()
        db.commit()
        return True


def claim_handoff_result_batch_for_inbox(
    message_id: int, now: Optional[datetime] = None
) -> Optional[Dict[str, Any]] | str:
    """Atomically seal and claim one handoff-result safe-boundary batch.

    A direct handoff finalizer may join a boundary only while its turn remains
    ``queued``.  Claim that turn *before* reading the result notices, in the
    same transaction that activates it, so a late finalizer must create a
    subsequent boundary rather than become an untransported member of this
    one.  The returned messages are therefore the exact admitted payload.
    """
    _ensure_child_assignment_schema()
    _ensure_delegation_result_schema()
    _ensure_workflow_schema()
    now = now or datetime.now()
    with SessionLocal() as db:
        turn = (
            db.query(WorkflowTurnModel)
            .filter(
                WorkflowTurnModel.inbox_message_id == message_id,
                WorkflowTurnModel.kind == "handoff_result",
                WorkflowTurnModel.state == TURN_QUEUED,
            )
            .first()
        )
        if turn is None:
            return None
        workflow = db.query(WorkflowModel).filter(WorkflowModel.id == turn.workflow_id).first()
        if workflow is None or workflow.status != WORKFLOW_OPEN:
            return None

        active_turn_id = workflow.active_turn_id
        if active_turn_id != turn.id and _workflow_has_unadmitted_active_turn(db, workflow):
            return DEFER_UNADMITTED

        # Acquire a write through the active-turn CAS before sealing the
        # queued turn. SQLite serializes this with a concurrent finalizer; on
        # other supported engines both updates remain one transaction.
        active_predicate = (
            WorkflowModel.active_turn_id.is_(None)
            if active_turn_id is None
            else WorkflowModel.active_turn_id == active_turn_id
        )
        activated = (
            db.query(WorkflowModel)
            .filter(
                WorkflowModel.id == workflow.id,
                WorkflowModel.root_terminal_id == workflow.root_terminal_id,
                WorkflowModel.status == WORKFLOW_OPEN,
                active_predicate,
            )
            .update(
                {
                    WorkflowModel.active_turn_id: turn.id,
                    WorkflowModel.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        if activated != 1:
            db.rollback()
            return None

        claim_token = uuid.uuid4().hex
        claim_generation = turn.claim_generation + 1
        claim_expires_at = datetime.fromtimestamp(
            now.timestamp() + WORKFLOW_TURN_CLAIM_LEASE_SECONDS
        )
        claimed = (
            db.query(WorkflowTurnModel)
            .filter(
                WorkflowTurnModel.id == turn.id,
                WorkflowTurnModel.state == TURN_QUEUED,
            )
            .update(
                {
                    WorkflowTurnModel.state: TURN_CLAIMED,
                    WorkflowTurnModel.attempt_count: WorkflowTurnModel.attempt_count + 1,
                    WorkflowTurnModel.claim_generation: claim_generation,
                    WorkflowTurnModel.claim_token: claim_token,
                    WorkflowTurnModel.claim_expires_at: claim_expires_at,
                    WorkflowTurnModel.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        if claimed != 1:
            db.rollback()
            return None

        # This query follows the state transition that prevents any later
        # finalizer from joining this turn. Its result is the sealed payload;
        # do not replace it with a later Inbox scan in the caller.
        messages = (
            db.query(InboxModel)
            .join(DelegationResultModel, DelegationResultModel.id == InboxModel.result_id)
            .filter(
                DelegationResultModel.workflow_turn_id == turn.id,
                InboxModel.kind == "delegation_result_notice",
                InboxModel.status == MessageStatus.PENDING.value,
            )
            .order_by(InboxModel.id.asc())
            .all()
        )
        if not messages:
            db.rollback()
            return None
        db.commit()
        return {
            "id": turn.id,
            "kind": turn.kind,
            "payload": turn.payload or "",
            "claim_token": claim_token,
            "claim_generation": claim_generation,
            "messages": [_inbox_model_to_message(message) for message in messages],
        }


def claim_workflow_turn_receipt(
    receiver_terminal_id: str, logical_turn_id: int, now: Optional[datetime] = None
) -> bool:
    """Atomically admit one receiver-side logical turn before model work.

    A sender can die after tmux accepts a continuation and retry the same
    stable ``logical-turn`` envelope.  This receipt deliberately records the
    *receiver's* one semantic admission, independent of any physical sends.
    There is no lease to reclaim: once model work has been admitted it is an
    irreversible effect, so a duplicate must be rejected rather than allowed
    to create a second supervisor turn.

    The eligibility update and receipt insert share one write transaction. A
    terminal transition that wins first makes the update fail; a receipt that
    wins first is a valid pre-terminal admission and is then permanently
    idempotent across restarts and duplicate arrivals.
    """
    _ensure_workflow_schema()
    now = now or datetime.now()
    with SessionLocal() as db:
        existing = (
            db.query(WorkflowTurnReceiptModel)
            .filter(
                WorkflowTurnReceiptModel.workflow_turn_id == logical_turn_id,
                WorkflowTurnReceiptModel.receiver_terminal_id == receiver_terminal_id,
            )
            .first()
        )
        if existing is not None:
            return False

        # Acquire a short write fence against terminal closure before adding
        # the unique receipt.  This is not a queue transition; it only proves
        # that this exact receiver/workflow was OPEN at admission time.
        eligible = (
            db.query(WorkflowTurnModel)
            .filter(
                WorkflowTurnModel.id == logical_turn_id,
                WorkflowTurnModel.workflow_id.in_(
                    db.query(WorkflowModel.id).filter(
                        WorkflowModel.root_terminal_id == receiver_terminal_id,
                        WorkflowModel.status == WORKFLOW_OPEN,
                        WorkflowModel.active_turn_id == logical_turn_id,
                    )
                ),
            )
            .update({WorkflowTurnModel.updated_at: now}, synchronize_session=False)
        )
        if eligible != 1:
            db.rollback()
            return False
        db.add(
            WorkflowTurnReceiptModel(
                workflow_turn_id=logical_turn_id,
                receiver_terminal_id=receiver_terminal_id,
                consumed_at=now,
            )
        )
        try:
            db.commit()
        except IntegrityError:
            # A concurrent physical duplicate inserted the same durable
            # receipt. Its supervisor turn owns the only logical effect.
            db.rollback()
            return False
        return True


def has_admitted_workflow_turn(receiver_terminal_id: str, logical_turn_id: int) -> bool:
    """Read-only fence for repeatable MCP result reads."""
    _ensure_workflow_schema()
    with SessionLocal() as db:
        workflow = _open_workflow(db, receiver_terminal_id, create=False)
        return bool(
            workflow
            and workflow.status == WORKFLOW_OPEN
            and workflow.active_turn_id == logical_turn_id
            and db.query(WorkflowTurnReceiptModel)
            .filter_by(workflow_turn_id=logical_turn_id, receiver_terminal_id=receiver_terminal_id)
            .first()
        )


def claim_workflow_effect(
    receiver_terminal_id: str,
    logical_turn_id: int,
    effect_kind: str,
    effect_key: str,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Open the sole runtime gate for one privileged logical effect.

    Only a receiver-side admitted turn can obtain an effect capability.  The
    unique key is stable across duplicate physical deliveries. An existing
    ``claimed`` row is deliberately not reclaimed after restart: the process
    may have crossed a non-transactional CAO boundary before it died. A proven
    ``not_admitted`` row did not cross that boundary and is safely claimable
    again under the same current turn.
    """
    _ensure_workflow_schema()
    now = now or datetime.now()
    with SessionLocal() as db:
        workflow = _open_workflow(db, receiver_terminal_id, create=False)
        if (
            workflow is None
            or workflow.status != WORKFLOW_OPEN
            or workflow.active_turn_id != logical_turn_id
        ):
            return None
        receipt = (
            db.query(WorkflowTurnReceiptModel)
            .filter(
                WorkflowTurnReceiptModel.workflow_turn_id == logical_turn_id,
                WorkflowTurnReceiptModel.receiver_terminal_id == receiver_terminal_id,
            )
            .first()
        )
        if receipt is None:
            return None
        turn = (
            db.query(WorkflowTurnModel)
            .filter(
                WorkflowTurnModel.id == logical_turn_id,
                WorkflowTurnModel.workflow_id == workflow.id,
            )
            .first()
        )
        if turn is None:
            return None
        existing = (
            db.query(WorkflowEffectModel)
            .filter(
                WorkflowEffectModel.workflow_id == workflow.id,
                WorkflowEffectModel.workflow_turn_id == logical_turn_id,
                WorkflowEffectModel.effect_kind == effect_kind,
                WorkflowEffectModel.effect_key == effect_key,
            )
            .first()
        )
        token = uuid.uuid4().hex
        if existing is not None:
            if existing.state != "not_admitted":
                return None
            reclaimed = (
                db.query(WorkflowEffectModel)
                .filter(
                    WorkflowEffectModel.id == existing.id,
                    WorkflowEffectModel.state == "not_admitted",
                    WorkflowEffectModel.workflow_id.in_(
                        db.query(WorkflowModel.id).filter(
                            WorkflowModel.id == workflow.id,
                            WorkflowModel.root_terminal_id == receiver_terminal_id,
                            WorkflowModel.status == WORKFLOW_OPEN,
                            WorkflowModel.active_turn_id == logical_turn_id,
                        )
                    ),
                )
                .update(
                    {
                        WorkflowEffectModel.state: "claimed",
                        WorkflowEffectModel.claim_token: token,
                        WorkflowEffectModel.updated_at: now,
                    },
                    synchronize_session=False,
                )
            )
            if reclaimed != 1:
                db.rollback()
                return None
            db.commit()
            return {"id": existing.id, "claim_token": token}
        effect = WorkflowEffectModel(
            workflow_id=workflow.id,
            workflow_turn_id=logical_turn_id,
            effect_kind=effect_kind,
            effect_key=effect_key,
            state="claimed",
            claim_token=token,
            created_at=now,
            updated_at=now,
        )
        db.add(effect)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return None
        return {"id": effect.id, "claim_token": token}


def finish_workflow_effect(
    receiver_terminal_id: str,
    effect_id: int,
    claim_token: str,
    outcome: str,
    now: Optional[datetime] = None,
) -> bool:
    """Seal a claimed effect without permitting a duplicate future entry.

    ``completed`` and ``indeterminate`` are terminal ledger states.  The
    latter is used for exceptions after an external boundary has been entered;
    it preserves truthfulness over an unsafe replay.
    """
    if outcome not in {"completed", "indeterminate", "rejected", "not_admitted"}:
        raise ValueError(f"Invalid workflow effect outcome: {outcome}")
    _ensure_workflow_schema()
    now = now or datetime.now()
    with SessionLocal() as db:
        updated = (
            db.query(WorkflowEffectModel)
            .filter(
                WorkflowEffectModel.id == effect_id,
                WorkflowEffectModel.claim_token == claim_token,
                WorkflowEffectModel.state == "claimed",
                WorkflowEffectModel.workflow_id.in_(
                    db.query(WorkflowModel.id).filter(
                        WorkflowModel.root_terminal_id == receiver_terminal_id
                    )
                ),
            )
            .update(
                {WorkflowEffectModel.state: outcome, WorkflowEffectModel.updated_at: now},
                synchronize_session=False,
            )
        )
        if updated != 1:
            db.rollback()
            return False
        db.commit()
        return True


def describe_workflow_effect_rejection(
    receiver_terminal_id: Optional[str], logical_turn_id: int, effect_kind: str, effect_key: str
) -> Dict[str, Optional[str]]:
    """Explain why a privileged effect cannot be claimed without mutating state.

    The old ``None`` result from :func:`claim_workflow_effect` was safe but
    operationally opaque: a duplicate, a stale turn, and an owner gate were
    indistinguishable to a recovering child. Expose a stable diagnostic
    projection for terminal rows; a ``not_admitted`` row is reclaimed by the
    claim path before this diagnostic is needed.
    """
    if not receiver_terminal_id:
        return {
            "reason_code": "CHILD_NOT_AUTHORIZED",
            "workflow_state": None,
            "explanation": "The caller is not a CAO-managed terminal.",
        }
    _ensure_workflow_schema()
    with SessionLocal() as db:
        workflow = _open_workflow(db, receiver_terminal_id, create=False)
        if workflow is None:
            return {
                "reason_code": "STALE_LOGICAL_TURN",
                "workflow_state": None,
                "explanation": "No current workflow owns this logical turn.",
            }
        if workflow.status == WORKFLOW_OWNER_GATE:
            return {
                "reason_code": "WORKFLOW_OWNER_GATED",
                "workflow_state": workflow.status,
                "explanation": "The workflow is waiting for its owner.",
            }
        if workflow.status != WORKFLOW_OPEN:
            return {
                "reason_code": "WORKFLOW_ALREADY_TERMINAL",
                "workflow_state": workflow.status,
                "explanation": "The workflow has already reached a terminal state.",
            }
        existing = (
            db.query(WorkflowEffectModel)
            .filter_by(
                workflow_id=workflow.id,
                workflow_turn_id=logical_turn_id,
                effect_kind=effect_kind,
                effect_key=effect_key,
            )
            .first()
        )
        if existing is not None:
            return {
                "reason_code": "DUPLICATE_EFFECT",
                "workflow_state": workflow.status,
                "explanation": "This logical effect was already claimed.",
            }
        return {
            "reason_code": "STALE_LOGICAL_TURN",
            "workflow_state": workflow.status,
            "explanation": "The logical turn is not the current admitted turn.",
        }


def mark_workflow_turn_sent_for_inbox(message_id: int) -> bool:
    _ensure_workflow_schema()
    with SessionLocal() as db:
        turn = (
            db.query(WorkflowTurnModel)
            .filter(WorkflowTurnModel.inbox_message_id == message_id)
            .first()
        )
        if turn is None or turn.state != TURN_QUEUED:
            return False
        workflow = db.query(WorkflowModel).filter(WorkflowModel.id == turn.workflow_id).first()
        if workflow is None or workflow.status != WORKFLOW_OPEN:
            return False
        turn.state = TURN_SENT
        turn.attempt_count += 1
        turn.updated_at = datetime.now()
        # A durable child result is real workflow progress, not another
        # provider-only final observation. Reset the continuation backoff
        # before the callback is evaluated.
        if turn.kind == "handoff_result":
            workflow.no_progress_count = 0
            workflow.updated_at = turn.updated_at
        db.commit()
        return True


def claim_workflow_provider_reconnect(
    root_terminal_id: str, now: Optional[datetime] = None
) -> Optional[Dict[str, Any]]:
    """Claim one bounded provider reconnect without duplicating an attempt."""
    _ensure_workflow_schema()
    now = now or datetime.now()
    stale_before = datetime.fromtimestamp(
        now.timestamp() - WORKFLOW_PROVIDER_RECONNECT_LEASE_SECONDS
    )
    claim_token = uuid.uuid4().hex
    exhausted_workflow_id: Optional[int] = None
    with SessionLocal() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        workflow = _open_workflow(db, root_terminal_id, create=False)
        if workflow is None or workflow.status != WORKFLOW_OPEN or workflow.active_turn_id is None:
            db.rollback()
            return None
        terminal = db.query(TerminalModel).filter(TerminalModel.id == root_terminal_id).first()
        if terminal is None or terminal.runtime_lifecycle not in (None, "running"):
            db.rollback()
            return None
        operation_live = terminal.runtime_operation_token is not None and (
            terminal.runtime_operation_expires_at is None
            or terminal.runtime_operation_expires_at > now
        )
        if operation_live:
            db.rollback()
            return None
        if db.get(ProviderExecutionLeaseModel, root_terminal_id) is not None:
            db.rollback()
            return None
        active_turn_id = cast(int, workflow.active_turn_id)
        admitted = (
            db.query(WorkflowTurnReceiptModel)
            .filter_by(
                workflow_turn_id=active_turn_id,
                receiver_terminal_id=root_terminal_id,
            )
            .first()
        )
        if admitted is None:
            db.rollback()
            return None
        active_turn = db.get(WorkflowTurnModel, active_turn_id)
        if active_turn is None:
            db.rollback()
            return None
        attempts = (
            db.query(WorkflowProviderReconnectAttemptModel)
            .filter_by(
                workflow_id=workflow.id,
                workflow_turn_id=active_turn_id,
                root_terminal_id=root_terminal_id,
            )
            .order_by(WorkflowProviderReconnectAttemptModel.attempt_number.asc())
            .all()
        )
        attempt = next(
            (
                candidate
                for candidate in reversed(attempts)
                if candidate.state
                in {
                    PROVIDER_RECONNECT_RESERVED,
                    PROVIDER_RECONNECT_LAUNCHED,
                    PROVIDER_RECONNECT_READY,
                }
            ),
            None,
        )
        if attempt is None and len(attempts) >= MAX_WORKFLOW_PROVIDER_RECONNECT_ATTEMPTS:
            workflow.status = WORKFLOW_OWNER_GATE
            workflow.terminal_reason = PROVIDER_RECONNECT_RECOVERY_EXHAUSTED_REASON
            workflow.updated_at = now
            turn = db.get(WorkflowTurnModel, active_turn_id)
            if turn is not None:
                turn.provider_reconnect_requested_at = None
                turn.provider_reconnect_claim_token = None
                turn.updated_at = now
            if terminal.runtime_operation_kind == "reconnect":
                terminal.runtime_operation_kind = None
                terminal.runtime_operation_token = None
                terminal.runtime_operation_claimed_at = None
                terminal.runtime_operation_expires_at = None
            db.query(WorkflowTurnModel).filter(
                WorkflowTurnModel.workflow_id == workflow.id,
                WorkflowTurnModel.state.in_((TURN_QUEUED, TURN_CLAIMED)),
            ).update(
                {
                    WorkflowTurnModel.state: TURN_CANCELLED,
                    WorkflowTurnModel.claim_token: None,
                    WorkflowTurnModel.claim_expires_at: None,
                    WorkflowTurnModel.updated_at: now,
                },
                synchronize_session=False,
            )
            _cancel_parent_assignments(db, root_terminal_id, now)
            db.query(ProviderExecutionLeaseModel).filter_by(terminal_id=root_terminal_id).delete(
                synchronize_session=False
            )
            exhausted_workflow_id = int(workflow.id)
            db.commit()
            result = {
                "exhausted": True,
                "turn_id": active_turn_id,
                "attempt_count": len(attempts),
            }
            # Notification is deliberately outside the state transaction.
            _dispatch_workflow_notification_fail_open(
                root_terminal_id, "owner_attention", exhausted_workflow_id
            )
            return result
        if attempt is None:
            attempt = WorkflowProviderReconnectAttemptModel(
                workflow_id=workflow.id,
                workflow_turn_id=active_turn_id,
                root_terminal_id=root_terminal_id,
                attempt_number=len(attempts) + 1,
                attempt_token=uuid.uuid4().hex,
                resume_identity=active_turn.provider_reconnect_resume_identity,
                state=PROVIDER_RECONNECT_RESERVED,
                created_at=now,
                updated_at=now,
            )
            db.add(attempt)
            db.flush()
        claimed = (
            db.query(WorkflowTurnModel)
            .filter(
                WorkflowTurnModel.id == active_turn_id,
                WorkflowTurnModel.workflow_id == workflow.id,
                WorkflowTurnModel.state == TURN_SENT,
                or_(
                    WorkflowTurnModel.provider_reconnect_requested_at.is_(None),
                    WorkflowTurnModel.provider_reconnect_requested_at <= stale_before,
                ),
                WorkflowTurnModel.workflow_id.in_(
                    db.query(WorkflowModel.id).filter(
                        WorkflowModel.id == workflow.id,
                        WorkflowModel.root_terminal_id == root_terminal_id,
                        WorkflowModel.status == WORKFLOW_OPEN,
                        WorkflowModel.active_turn_id == active_turn_id,
                    )
                ),
            )
            .update(
                {
                    WorkflowTurnModel.provider_reconnect_requested_at: now,
                    WorkflowTurnModel.provider_reconnect_claim_token: claim_token,
                    WorkflowTurnModel.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        if claimed != 1:
            db.rollback()
            return None
        terminal.runtime_operation_kind = "reconnect"
        terminal.runtime_operation_token = claim_token
        terminal.runtime_operation_claimed_at = now
        terminal.runtime_operation_expires_at = now + timedelta(
            seconds=WORKFLOW_PROVIDER_RECONNECT_LEASE_SECONDS
        )
        db.commit()
        resume_identity = (
            db.query(WorkflowTurnModel.provider_reconnect_resume_identity)
            .filter(
                WorkflowTurnModel.id == active_turn_id,
                WorkflowTurnModel.provider_reconnect_claim_token == claim_token,
            )
            .scalar()
        )
        return {
            "turn_id": active_turn_id,
            "claimed_at": now,
            "claim_token": claim_token,
            "resume_identity": resume_identity,
            "attempt_token": str(attempt.attempt_token),
            "attempt_state": str(attempt.state),
            "attempt_number": int(attempt.attempt_number),
            "attempt_limit": MAX_WORKFLOW_PROVIDER_RECONNECT_ATTEMPTS,
        }


def workflow_provider_reconnect_pending(root_terminal_id: str) -> bool:
    """Whether the active OPEN turn owns an unfinished provider reconnect."""
    _ensure_workflow_schema()
    with SessionLocal() as db:
        return (
            db.query(WorkflowTurnModel.id)
            .join(WorkflowModel, WorkflowModel.id == WorkflowTurnModel.workflow_id)
            .filter(
                WorkflowModel.root_terminal_id == root_terminal_id,
                WorkflowModel.status == WORKFLOW_OPEN,
                WorkflowModel.active_turn_id == WorkflowTurnModel.id,
                WorkflowTurnModel.provider_reconnect_requested_at.is_not(None),
            )
            .first()
            is not None
        )


def get_pending_workflow_provider_reconnect_root_terminal_ids() -> List[str]:
    """Return OPEN roots with one unfinished reconnect in a single query."""
    _ensure_workflow_schema()
    with SessionLocal() as db:
        return [
            root_terminal_id
            for (root_terminal_id,) in (
                db.query(WorkflowModel.root_terminal_id)
                .join(
                    WorkflowTurnModel,
                    WorkflowTurnModel.id == WorkflowModel.active_turn_id,
                )
                .filter(
                    WorkflowModel.status == WORKFLOW_OPEN,
                    WorkflowTurnModel.provider_reconnect_requested_at.is_not(None),
                )
                .all()
            )
        ]


def persist_workflow_provider_reconnect_identity(
    root_terminal_id: str,
    turn_id: int,
    claim_token: str,
    attempt_token: str,
    resume_identity: str,
) -> bool:
    """Persist one provider resume identity before any reconnect side effect."""
    _ensure_workflow_schema()
    if not resume_identity:
        return False
    with SessionLocal() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        terminal = db.query(TerminalModel).filter(TerminalModel.id == root_terminal_id).first()
        if (
            terminal is None
            or terminal.runtime_lifecycle not in (None, "running")
            or terminal.runtime_operation_kind != "reconnect"
            or terminal.runtime_operation_token != claim_token
        ):
            db.rollback()
            return False
        turn = (
            db.query(WorkflowTurnModel)
            .filter(
                WorkflowTurnModel.id == turn_id,
                WorkflowTurnModel.provider_reconnect_claim_token == claim_token,
                WorkflowTurnModel.provider_reconnect_requested_at.is_not(None),
                WorkflowTurnModel.workflow_id.in_(
                    db.query(WorkflowModel.id).filter(
                        WorkflowModel.root_terminal_id == root_terminal_id,
                        WorkflowModel.status == WORKFLOW_OPEN,
                        WorkflowModel.active_turn_id == turn_id,
                    )
                ),
            )
            .one_or_none()
        )
        if turn is None:
            return False
        if turn.provider_reconnect_resume_identity not in (None, resume_identity):
            return False
        attempt = (
            db.query(WorkflowProviderReconnectAttemptModel)
            .filter_by(
                workflow_turn_id=turn_id,
                root_terminal_id=root_terminal_id,
                attempt_token=attempt_token,
            )
            .one_or_none()
        )
        if attempt is None or attempt.state not in {
            PROVIDER_RECONNECT_RESERVED,
            PROVIDER_RECONNECT_LAUNCHED,
            PROVIDER_RECONNECT_READY,
        }:
            return False
        if attempt.resume_identity not in (None, resume_identity):
            return False
        turn.provider_reconnect_resume_identity = resume_identity
        turn.updated_at = datetime.now()
        attempt.resume_identity = resume_identity
        attempt.updated_at = turn.updated_at
        db.commit()
        return True


def mark_workflow_provider_reconnect_launch_dispatched(
    root_terminal_id: str,
    turn_id: int,
    claim_token: str,
    attempt_token: str,
    now: Optional[datetime] = None,
) -> bool:
    """Persist the paid-launch boundary before sending the shell command."""
    _ensure_workflow_schema()
    now = now or datetime.now()
    with SessionLocal() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        terminal = db.get(TerminalModel, root_terminal_id)
        turn = db.get(WorkflowTurnModel, turn_id)
        attempt = (
            db.query(WorkflowProviderReconnectAttemptModel)
            .filter_by(
                workflow_turn_id=turn_id,
                root_terminal_id=root_terminal_id,
                attempt_token=attempt_token,
            )
            .one_or_none()
        )
        workflow = db.get(WorkflowModel, turn.workflow_id) if turn is not None else None
        if (
            terminal is None
            or turn is None
            or attempt is None
            or workflow is None
            or workflow.status != WORKFLOW_OPEN
            or workflow.active_turn_id != turn_id
            or terminal.runtime_lifecycle not in (None, "running")
            or terminal.runtime_operation_kind != "reconnect"
            or terminal.runtime_operation_token != claim_token
            or turn.provider_reconnect_claim_token != claim_token
            or turn.provider_reconnect_requested_at is None
            or attempt.state != PROVIDER_RECONNECT_RESERVED
        ):
            db.rollback()
            return False
        attempt.state = PROVIDER_RECONNECT_LAUNCHED
        attempt.launched_at = now
        attempt.updated_at = now
        db.commit()
        return True


def record_workflow_provider_reconnect_runtime_ready(
    root_terminal_id: str,
    attempt_token: str,
    runtime_generation: str,
    sidecar_process_id: int,
    sidecar_process_start_ticks: int,
    now: Optional[datetime] = None,
) -> bool:
    """Register the new nonce-bound MCP sidecar process exactly once."""
    from cli_agent_orchestrator.runtime_generation import ACTIVE_RUNTIME_GENERATION

    _ensure_workflow_schema()
    if (
        not re.fullmatch(r"[0-9a-f]{32}", attempt_token)
        or not re.fullmatch(r"[0-9a-f]{64}", runtime_generation)
        or not hmac.compare_digest(runtime_generation, ACTIVE_RUNTIME_GENERATION)
        or sidecar_process_id <= 1
        or sidecar_process_start_ticks <= 0
    ):
        return False
    now = now or datetime.now()
    with SessionLocal() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        attempt = (
            db.query(WorkflowProviderReconnectAttemptModel)
            .filter_by(root_terminal_id=root_terminal_id, attempt_token=attempt_token)
            .one_or_none()
        )
        if attempt is None:
            db.rollback()
            return False
        workflow = db.get(WorkflowModel, attempt.workflow_id)
        turn = db.get(WorkflowTurnModel, attempt.workflow_turn_id)
        terminal = db.get(TerminalModel, root_terminal_id)
        if (
            workflow is None
            or turn is None
            or terminal is None
            or workflow.status != WORKFLOW_OPEN
            or workflow.active_turn_id != turn.id
            or turn.provider_reconnect_requested_at is None
            or terminal.runtime_lifecycle not in (None, "running")
            or terminal.runtime_operation_kind != "reconnect"
            or attempt.state not in {PROVIDER_RECONNECT_LAUNCHED, PROVIDER_RECONNECT_READY}
        ):
            db.rollback()
            return False
        identity = (
            runtime_generation,
            sidecar_process_id,
            sidecar_process_start_ticks,
        )
        existing_identity = (
            attempt.runtime_generation,
            attempt.sidecar_process_id,
            attempt.sidecar_process_start_ticks,
        )
        if attempt.state == PROVIDER_RECONNECT_READY:
            db.rollback()
            return existing_identity == identity
        attempt.state = PROVIDER_RECONNECT_READY
        attempt.runtime_generation = runtime_generation
        attempt.sidecar_process_id = sidecar_process_id
        attempt.sidecar_process_start_ticks = sidecar_process_start_ticks
        attempt.ready_at = now
        attempt.updated_at = now
        db.commit()
        return True


def get_workflow_provider_reconnect_runtime_ready(
    root_terminal_id: str, attempt_token: str
) -> Optional[Dict[str, Any]]:
    """Return the exact registered sidecar identity for an active attempt."""
    _ensure_workflow_schema()
    with SessionLocal() as db:
        attempt = (
            db.query(WorkflowProviderReconnectAttemptModel)
            .filter_by(
                root_terminal_id=root_terminal_id,
                attempt_token=attempt_token,
                state=PROVIDER_RECONNECT_READY,
            )
            .one_or_none()
        )
        if attempt is None:
            return None
        workflow = db.get(WorkflowModel, attempt.workflow_id)
        turn = db.get(WorkflowTurnModel, attempt.workflow_turn_id)
        if (
            workflow is None
            or turn is None
            or workflow.status != WORKFLOW_OPEN
            or workflow.active_turn_id != turn.id
            or turn.provider_reconnect_requested_at is None
        ):
            return None
        return {
            "runtime_generation": attempt.runtime_generation,
            "sidecar_process_id": attempt.sidecar_process_id,
            "sidecar_process_start_ticks": attempt.sidecar_process_start_ticks,
            "ready_at": attempt.ready_at,
        }


def record_workflow_provider_reconnect_output_boundary(
    root_terminal_id: str,
    attempt_token: str,
    output_log_device: int,
    output_log_inode: int,
    output_log_offset: int,
    now: Optional[datetime] = None,
) -> bool:
    """Persist the exact private-log byte boundary for one ready runtime."""
    _ensure_workflow_schema()
    if (
        not re.fullmatch(r"[0-9a-f]{32}", attempt_token)
        or output_log_device < 0
        or output_log_inode <= 0
        or output_log_offset < 0
    ):
        return False
    now = now or datetime.now()
    with SessionLocal() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        attempt = (
            db.query(WorkflowProviderReconnectAttemptModel)
            .filter_by(
                root_terminal_id=root_terminal_id,
                attempt_token=attempt_token,
                state=PROVIDER_RECONNECT_READY,
            )
            .one_or_none()
        )
        if attempt is None:
            db.rollback()
            return False
        workflow = db.get(WorkflowModel, attempt.workflow_id)
        turn = db.get(WorkflowTurnModel, attempt.workflow_turn_id)
        terminal = db.get(TerminalModel, root_terminal_id)
        if (
            workflow is None
            or turn is None
            or terminal is None
            or workflow.status != WORKFLOW_OPEN
            or workflow.active_turn_id != turn.id
            or turn.provider_reconnect_requested_at is None
            or terminal.runtime_lifecycle not in (None, "running")
            or terminal.runtime_operation_kind != "reconnect"
        ):
            db.rollback()
            return False
        identity = (output_log_device, output_log_inode, output_log_offset)
        existing = (
            attempt.output_log_device,
            attempt.output_log_inode,
            attempt.output_log_offset,
        )
        if attempt.output_boundary_at is not None:
            db.rollback()
            return existing == identity
        attempt.output_log_device = output_log_device
        attempt.output_log_inode = output_log_inode
        attempt.output_log_offset = output_log_offset
        attempt.output_boundary_at = now
        attempt.updated_at = now
        db.commit()
        return True


def get_latest_workflow_provider_reconnect_output_boundary(
    root_terminal_id: str,
) -> Optional[Dict[str, Any]]:
    """Return the newest DB-authorized log boundary for this provider runtime."""
    _ensure_workflow_schema()
    with SessionLocal() as db:
        attempt = (
            db.query(WorkflowProviderReconnectAttemptModel)
            .filter(
                WorkflowProviderReconnectAttemptModel.root_terminal_id == root_terminal_id,
                WorkflowProviderReconnectAttemptModel.state.in_(
                    (PROVIDER_RECONNECT_READY, PROVIDER_RECONNECT_SUCCEEDED)
                ),
                WorkflowProviderReconnectAttemptModel.output_log_device.is_not(None),
                WorkflowProviderReconnectAttemptModel.output_log_inode.is_not(None),
                WorkflowProviderReconnectAttemptModel.output_log_offset.is_not(None),
                WorkflowProviderReconnectAttemptModel.output_boundary_at.is_not(None),
            )
            .order_by(WorkflowProviderReconnectAttemptModel.id.desc())
            .first()
        )
        if attempt is None:
            return None
        if attempt.state == PROVIDER_RECONNECT_READY:
            workflow = db.get(WorkflowModel, attempt.workflow_id)
            turn = db.get(WorkflowTurnModel, attempt.workflow_turn_id)
            if (
                workflow is None
                or turn is None
                or workflow.status != WORKFLOW_OPEN
                or workflow.active_turn_id != turn.id
                or turn.provider_reconnect_requested_at is None
            ):
                return None
        return {
            "attempt_token": str(attempt.attempt_token),
            "output_log_device": int(attempt.output_log_device),
            "output_log_inode": int(attempt.output_log_inode),
            "output_log_offset": int(attempt.output_log_offset),
            "output_boundary_at": attempt.output_boundary_at,
        }


def complete_workflow_provider_reconnect(
    root_terminal_id: str,
    turn_id: int,
    claim_token: str,
    attempt_token: str,
) -> bool:
    """Close the durable reconnect episode after the resumed TUI is ready."""
    _ensure_workflow_schema()
    with SessionLocal() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        terminal = db.query(TerminalModel).filter(TerminalModel.id == root_terminal_id).first()
        if (
            terminal is None
            or terminal.runtime_lifecycle not in (None, "running")
            or terminal.runtime_operation_kind != "reconnect"
            or terminal.runtime_operation_token != claim_token
        ):
            db.rollback()
            return False
        attempt = (
            db.query(WorkflowProviderReconnectAttemptModel)
            .filter_by(
                workflow_turn_id=turn_id,
                root_terminal_id=root_terminal_id,
                attempt_token=attempt_token,
                state=PROVIDER_RECONNECT_READY,
            )
            .one_or_none()
        )
        if attempt is None:
            db.rollback()
            return False
        if (
            attempt.output_log_device is None
            or attempt.output_log_inode is None
            or attempt.output_log_offset is None
            or attempt.output_boundary_at is None
        ):
            db.rollback()
            return False
        now = datetime.now()
        completed = (
            db.query(WorkflowTurnModel)
            .filter(
                WorkflowTurnModel.id == turn_id,
                WorkflowTurnModel.provider_reconnect_claim_token == claim_token,
                WorkflowTurnModel.provider_reconnect_requested_at.is_not(None),
                WorkflowTurnModel.workflow_id.in_(
                    db.query(WorkflowModel.id).filter(
                        WorkflowModel.root_terminal_id == root_terminal_id,
                        WorkflowModel.status == WORKFLOW_OPEN,
                        WorkflowModel.active_turn_id == turn_id,
                    )
                ),
            )
            .update(
                {
                    WorkflowTurnModel.provider_reconnect_requested_at: None,
                    WorkflowTurnModel.provider_reconnect_claim_token: None,
                    WorkflowTurnModel.provider_reconnect_resume_identity: None,
                    WorkflowTurnModel.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        if completed != 1:
            db.rollback()
            return False
        terminal.runtime_operation_kind = None
        terminal.runtime_operation_token = None
        terminal.runtime_operation_claimed_at = None
        terminal.runtime_operation_expires_at = None
        attempt.state = PROVIDER_RECONNECT_SUCCEEDED
        attempt.outcome_code = "runtime_ready"
        attempt.finished_at = now
        attempt.updated_at = now
        db.commit()
        return True


def fail_workflow_provider_reconnect_attempt(
    root_terminal_id: str,
    turn_id: int,
    claim_token: str,
    attempt_token: str,
    outcome_code: str,
    now: Optional[datetime] = None,
) -> bool:
    """Give one attempt a durable failure and exhaust the shared budget safely."""
    _ensure_workflow_schema()
    if not re.fullmatch(r"[a-z0-9_]{1,64}", outcome_code):
        outcome_code = "reconnect_failed"
    now = now or datetime.now()
    notify_workflow_id: Optional[int] = None
    with SessionLocal() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        terminal = db.get(TerminalModel, root_terminal_id)
        turn = db.get(WorkflowTurnModel, turn_id)
        attempt = (
            db.query(WorkflowProviderReconnectAttemptModel)
            .filter_by(
                workflow_turn_id=turn_id,
                root_terminal_id=root_terminal_id,
                attempt_token=attempt_token,
            )
            .one_or_none()
        )
        workflow = db.get(WorkflowModel, turn.workflow_id) if turn is not None else None
        if (
            terminal is None
            or turn is None
            or attempt is None
            or workflow is None
            or workflow.status != WORKFLOW_OPEN
            or workflow.active_turn_id != turn_id
            or terminal.runtime_operation_kind != "reconnect"
            or terminal.runtime_operation_token != claim_token
            or turn.provider_reconnect_claim_token != claim_token
            or turn.provider_reconnect_requested_at is None
            or attempt.state
            not in {
                PROVIDER_RECONNECT_RESERVED,
                PROVIDER_RECONNECT_LAUNCHED,
                PROVIDER_RECONNECT_READY,
            }
        ):
            db.rollback()
            return False
        attempt.state = PROVIDER_RECONNECT_FAILED
        attempt.outcome_code = outcome_code
        attempt.finished_at = now
        attempt.updated_at = now
        turn.provider_reconnect_claim_token = None
        # Keep the reconnect episode durable, but make its failed lease
        # immediately reclaimable for the next bounded attempt.
        turn.provider_reconnect_requested_at = datetime.fromtimestamp(
            now.timestamp() - WORKFLOW_PROVIDER_RECONNECT_LEASE_SECONDS - 1
        )
        turn.updated_at = now
        terminal.runtime_operation_kind = None
        terminal.runtime_operation_token = None
        terminal.runtime_operation_claimed_at = None
        terminal.runtime_operation_expires_at = None
        if attempt.attempt_number >= MAX_WORKFLOW_PROVIDER_RECONNECT_ATTEMPTS:
            workflow.status = WORKFLOW_OWNER_GATE
            workflow.terminal_reason = PROVIDER_RECONNECT_RECOVERY_EXHAUSTED_REASON
            workflow.updated_at = now
            turn.provider_reconnect_requested_at = None
            db.query(WorkflowTurnModel).filter(
                WorkflowTurnModel.workflow_id == workflow.id,
                WorkflowTurnModel.state.in_((TURN_QUEUED, TURN_CLAIMED)),
            ).update(
                {
                    WorkflowTurnModel.state: TURN_CANCELLED,
                    WorkflowTurnModel.claim_token: None,
                    WorkflowTurnModel.claim_expires_at: None,
                    WorkflowTurnModel.updated_at: now,
                },
                synchronize_session=False,
            )
            _cancel_parent_assignments(db, root_terminal_id, now)
            db.query(ProviderExecutionLeaseModel).filter_by(terminal_id=root_terminal_id).delete(
                synchronize_session=False
            )
            notify_workflow_id = int(workflow.id)
        db.commit()
    if notify_workflow_id is not None:
        _dispatch_workflow_notification_fail_open(
            root_terminal_id, "owner_attention", notify_workflow_id
        )
    return True


def renew_workflow_provider_reconnect(
    root_terminal_id: str,
    turn_id: int,
    claim_token: str,
    now: Optional[datetime] = None,
) -> bool:
    """Renew one exact reconnect lease without changing its ownership token."""
    _ensure_workflow_schema()
    now = now or datetime.now()
    with SessionLocal() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        terminal = db.query(TerminalModel).filter(TerminalModel.id == root_terminal_id).first()
        if (
            terminal is None
            or terminal.runtime_lifecycle not in (None, "running")
            or terminal.runtime_operation_kind != "reconnect"
            or terminal.runtime_operation_token != claim_token
        ):
            db.rollback()
            return False
        renewed = (
            db.query(WorkflowTurnModel)
            .filter(
                WorkflowTurnModel.id == turn_id,
                WorkflowTurnModel.provider_reconnect_claim_token == claim_token,
                WorkflowTurnModel.provider_reconnect_requested_at.is_not(None),
                WorkflowTurnModel.workflow_id.in_(
                    db.query(WorkflowModel.id).filter(
                        WorkflowModel.root_terminal_id == root_terminal_id,
                        WorkflowModel.status == WORKFLOW_OPEN,
                        WorkflowModel.active_turn_id == turn_id,
                    )
                ),
            )
            .update(
                {
                    WorkflowTurnModel.provider_reconnect_requested_at: now,
                    WorkflowTurnModel.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        if renewed != 1:
            db.rollback()
            return False
        terminal.runtime_operation_expires_at = now + timedelta(
            seconds=WORKFLOW_PROVIDER_RECONNECT_LEASE_SECONDS
        )
        db.commit()
        return True


def observe_workflow_processing(root_terminal_id: str, now: Optional[datetime] = None) -> bool:
    """Persist processing evidence and clear any transient Ready debounce."""
    _ensure_workflow_schema()
    now = now or datetime.now()
    with SessionLocal() as db:
        workflow = _open_workflow(db, root_terminal_id, create=False)
        if workflow is None or workflow.status != WORKFLOW_OPEN or workflow.active_turn_id is None:
            return False
        active_turn_id = cast(int, workflow.active_turn_id)
        admitted = (
            db.query(WorkflowTurnReceiptModel)
            .filter_by(
                workflow_turn_id=active_turn_id,
                receiver_terminal_id=root_terminal_id,
            )
            .first()
        )
        if admitted is None:
            return False
        changed = (
            db.query(WorkflowTurnModel)
            .filter(
                WorkflowTurnModel.id == active_turn_id,
                WorkflowTurnModel.workflow_id == workflow.id,
                WorkflowTurnModel.state == TURN_SENT,
                or_(
                    WorkflowTurnModel.provider_processing_observed_at.is_(None),
                    WorkflowTurnModel.provider_ready_observed_at.is_not(None),
                ),
                WorkflowTurnModel.workflow_id.in_(
                    db.query(WorkflowModel.id).filter(
                        WorkflowModel.id == workflow.id,
                        WorkflowModel.root_terminal_id == root_terminal_id,
                        WorkflowModel.status == WORKFLOW_OPEN,
                        WorkflowModel.active_turn_id == active_turn_id,
                    )
                ),
            )
            .update(
                {
                    WorkflowTurnModel.provider_processing_observed_at: func.coalesce(
                        WorkflowTurnModel.provider_processing_observed_at, now
                    ),
                    WorkflowTurnModel.provider_ready_observed_at: None,
                    WorkflowTurnModel.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        if changed != 1:
            db.rollback()
            return False
        db.commit()
        return True


def observe_workflow_ready(
    root_terminal_id: str, now: Optional[datetime] = None
) -> Optional[int] | str:
    """Advance a durably OPEN turn after the provider remains stably Ready.

    Some provider finals settle directly on IDLE/Ready and never expose a
    repeatable COMPLETED frame. The first Ready observation is persisted. A
    later observation of the same active admitted turn finalizes it only after
    a debounce that survives restart. Previously observed PROCESSING permits a
    short grace; a turn never seen processing gets a conservative longer one.
    """
    _ensure_workflow_schema()
    now = now or datetime.now()
    should_finalize = False
    with SessionLocal() as db:
        workflow = _open_workflow(db, root_terminal_id, create=False)
        if workflow is None or workflow.status != WORKFLOW_OPEN or workflow.active_turn_id is None:
            return None
        active_turn_id = cast(int, workflow.active_turn_id)
        turn = (
            db.query(WorkflowTurnModel)
            .filter(
                WorkflowTurnModel.id == active_turn_id,
                WorkflowTurnModel.workflow_id == workflow.id,
            )
            .first()
        )
        if turn is None:
            return None
        if turn.state == TURN_FINISHED:
            should_finalize = True
        elif turn.state != TURN_SENT:
            return None
        else:
            admitted = (
                db.query(WorkflowTurnReceiptModel)
                .filter_by(
                    workflow_turn_id=active_turn_id,
                    receiver_terminal_id=root_terminal_id,
                )
                .first()
            )
            if admitted is None:
                return DEFER_UNADMITTED
            if turn.provider_ready_observed_at is None:
                marked = (
                    db.query(WorkflowTurnModel)
                    .filter(
                        WorkflowTurnModel.id == active_turn_id,
                        WorkflowTurnModel.workflow_id == workflow.id,
                        WorkflowTurnModel.state == TURN_SENT,
                        WorkflowTurnModel.provider_ready_observed_at.is_(None),
                        WorkflowTurnModel.workflow_id.in_(
                            db.query(WorkflowModel.id).filter(
                                WorkflowModel.id == workflow.id,
                                WorkflowModel.root_terminal_id == root_terminal_id,
                                WorkflowModel.status == WORKFLOW_OPEN,
                                WorkflowModel.active_turn_id == active_turn_id,
                            )
                        ),
                    )
                    .update(
                        {
                            WorkflowTurnModel.provider_ready_observed_at: now,
                            WorkflowTurnModel.updated_at: now,
                        },
                        synchronize_session=False,
                    )
                )
                if marked != 1:
                    db.rollback()
                    return None
                db.commit()
                return DEFER_STABLE_READY
            grace_seconds = (
                WORKFLOW_READY_AFTER_PROCESSING_GRACE_SECONDS
                if turn.provider_processing_observed_at is not None
                else WORKFLOW_READY_WITHOUT_PROCESSING_GRACE_SECONDS
            )
            ready_seconds = now.timestamp() - turn.provider_ready_observed_at.timestamp()
            if ready_seconds < grace_seconds:
                return DEFER_STABLE_READY
            should_finalize = True
    return observe_workflow_final(root_terminal_id, now=now) if should_finalize else None


def observe_workflow_final(
    root_terminal_id: str, now: Optional[datetime] = None
) -> Optional[int] | str:
    """Finalize only the admitted active turn and queue one durable successor.

    A terminal can report ``COMPLETED`` after a physical send was accepted but
    before the receiver consumed the admission envelope.  That observation is
    explicitly not durable progress: finalizing it would make the watchdog
    manufacture a continuation without receiver admission.  In that case this
    function returns :data:`DEFER_UNADMITTED` without writing any workflow
    state.  An admitted provider turn ending is not semantic mission completion;
    the top-level workflow remains OPEN until an explicit completion, owner gate,
    cancellation, or genuine recovery-exhaustion transition.  Historical
    ``SENT`` rows are audit history, never candidates for this transition.
    """
    _ensure_workflow_schema()
    now = now or datetime.now()
    with SessionLocal() as db:
        workflow = _open_workflow(db, root_terminal_id, create=False)
        if workflow is None or workflow.status != WORKFLOW_OPEN or workflow.active_turn_id is None:
            return None

        active_turn_id = cast(int, workflow.active_turn_id)
        active_turn = (
            db.query(WorkflowTurnModel)
            .filter(
                WorkflowTurnModel.workflow_id == workflow.id,
                WorkflowTurnModel.id == active_turn_id,
            )
            .first()
        )
        if active_turn is None:
            return None

        if active_turn.state == TURN_SENT:
            receipt = (
                db.query(WorkflowTurnReceiptModel)
                .filter(
                    WorkflowTurnReceiptModel.workflow_turn_id == active_turn_id,
                    WorkflowTurnReceiptModel.receiver_terminal_id == root_terminal_id,
                )
                .first()
            )
            if receipt is None:
                # Do not even advance timestamps here.  The byte-for-byte
                # state invariant is important: a repeated stale final must
                # be as inert as a duplicate physical delivery.
                return DEFER_UNADMITTED

            # The turn update is the ownership decision.  It includes the
            # active binding and OPEN workflow predicates so a concurrent new
            # input/terminal transition cannot be finalized by a stale final.
            finalized = (
                db.query(WorkflowTurnModel)
                .filter(
                    WorkflowTurnModel.id == active_turn_id,
                    WorkflowTurnModel.workflow_id == workflow.id,
                    WorkflowTurnModel.state == TURN_SENT,
                    WorkflowTurnModel.workflow_id.in_(
                        db.query(WorkflowModel.id).filter(
                            WorkflowModel.id == workflow.id,
                            WorkflowModel.root_terminal_id == root_terminal_id,
                            WorkflowModel.status == WORKFLOW_OPEN,
                            WorkflowModel.active_turn_id == active_turn_id,
                        )
                    ),
                )
                .update(
                    {
                        WorkflowTurnModel.state: TURN_FINISHED,
                        WorkflowTurnModel.updated_at: now,
                    },
                    synchronize_session=False,
                )
            )
            if finalized != 1:
                db.rollback()
                return None

            no_progress_count = workflow.no_progress_count + 1
            advanced = (
                db.query(WorkflowModel)
                .filter(
                    WorkflowModel.id == workflow.id,
                    WorkflowModel.root_terminal_id == root_terminal_id,
                    WorkflowModel.status == WORKFLOW_OPEN,
                    WorkflowModel.active_turn_id == active_turn_id,
                )
                .update(
                    {
                        WorkflowModel.no_progress_count: no_progress_count,
                        WorkflowModel.updated_at: now,
                    },
                    synchronize_session=False,
                )
            )
            if advanced != 1:
                db.rollback()
                return None
            if no_progress_count > MAX_AUTOMATIC_OPEN_FINAL_NO_PROGRESS:
                # This is an explicit, owner-visible terminal workflow state,
                # never a silent OPEN idle. Keep the transition in the same
                # write transaction as the final observation so restart cannot
                # strand the workflow between its last paid turn and its gate.
                workflow.status = WORKFLOW_OWNER_GATE
                workflow.terminal_reason = OPEN_FINAL_CIRCUIT_BREAKER_REASON
                workflow.updated_at = now
                (
                    db.query(WorkflowTurnModel)
                    .filter(
                        WorkflowTurnModel.workflow_id == workflow.id,
                        WorkflowTurnModel.state.in_((TURN_QUEUED, TURN_CLAIMED)),
                    )
                    .update(
                        {
                            WorkflowTurnModel.state: TURN_CANCELLED,
                            WorkflowTurnModel.claim_token: None,
                            WorkflowTurnModel.claim_expires_at: None,
                            WorkflowTurnModel.updated_at: now,
                        },
                        synchronize_session=False,
                    )
                )
                _cancel_parent_assignments(db, root_terminal_id, now)
                db.query(ProviderExecutionLeaseModel).filter(
                    ProviderExecutionLeaseModel.terminal_id == root_terminal_id
                ).delete(synchronize_session=False)
                workflow_id = int(workflow.id)
                db.commit()
                _dispatch_workflow_notification_fail_open(
                    root_terminal_id, "owner_attention", workflow_id
                )
                return None
            # The CAS above means only one observer can create this successor.
            # Still retain the durable dedupe lookup for recovery from a
            # partially populated database and to document the one-row rule.
            dedupe_key = f"open-final:{active_turn_id}"
            successor = (
                db.query(WorkflowTurnModel)
                .filter(
                    WorkflowTurnModel.workflow_id == workflow.id,
                    WorkflowTurnModel.dedupe_key == dedupe_key,
                )
                .first()
            )
            if successor is None:
                # Back off repeated provider-only finals, but never translate
                # them into an owner gate: only an explicit semantic transition
                # may close a still-eligible top-level mission.
                delay_seconds = (
                    0
                    if no_progress_count == 1
                    else min(
                        MAX_OPEN_FINAL_CONTINUATION_DELAY_SECONDS,
                        2 ** min(no_progress_count - 2, 30),
                    )
                )
                successor = WorkflowTurnModel(
                    workflow_id=workflow.id,
                    kind="open_final",
                    dedupe_key=dedupe_key,
                    payload="Provider reported final while this workflow remains OPEN.",
                    state=TURN_QUEUED,
                    not_before=(
                        now
                        if delay_seconds == 0
                        else datetime.fromtimestamp(now.timestamp() + delay_seconds)
                    ),
                )
                db.add(successor)
                db.flush()
            db.commit()
            return successor.id

        if active_turn.state == TURN_FINISHED:
            successor = (
                db.query(WorkflowTurnModel)
                .filter(
                    WorkflowTurnModel.workflow_id == workflow.id,
                    WorkflowTurnModel.dedupe_key == f"open-final:{active_turn_id}",
                )
                .first()
            )
            return successor.id if successor is not None else None

        if active_turn.state in (TURN_QUEUED, TURN_CLAIMED):
            # Pre-send recovery owns this same logical ID.  Never substitute a
            # fresh turn merely because a stale final poll raced a restart.
            return active_turn_id

        return None


def claim_workflow_turn(
    root_terminal_id: str,
    now: Optional[datetime] = None,
    inbox_message_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Claim one due provider turn without crossing transport ownership.

    Generic workflow reconciliation never owns Inbox-backed turns. Inbox
    delivery supplies the exact message ID, preventing either path from
    bypassing the other's claim and acknowledgement boundaries.
    """
    _ensure_workflow_schema()
    now = now or datetime.now()
    with SessionLocal() as db:
        workflow = _open_workflow(db, root_terminal_id, create=False)
        if workflow is None or workflow.status != WORKFLOW_OPEN:
            return None
        outstanding = _workflow_has_unadmitted_active_turn(db, workflow)
        inbox_predicate = (
            WorkflowTurnModel.inbox_message_id == inbox_message_id
            if inbox_message_id is not None
            else WorkflowTurnModel.inbox_message_id.is_(None)
        )
        if outstanding and workflow.active_turn_id is not None:
            # Recover the same queued logical ID after a pre-send interruption;
            # a different successor must wait for this receiver admission.
            turn = (
                db.query(WorkflowTurnModel)
                .filter(
                    WorkflowTurnModel.id == workflow.active_turn_id,
                    WorkflowTurnModel.workflow_id == workflow.id,
                    WorkflowTurnModel.state == TURN_QUEUED,
                    inbox_predicate,
                    (WorkflowTurnModel.not_before.is_(None))
                    | (WorkflowTurnModel.not_before <= now),
                )
                .first()
            )
        else:
            turn = (
                db.query(WorkflowTurnModel)
                .filter(
                    WorkflowTurnModel.workflow_id == workflow.id,
                    WorkflowTurnModel.state == TURN_QUEUED,
                    inbox_predicate,
                    (WorkflowTurnModel.not_before.is_(None))
                    | (WorkflowTurnModel.not_before <= now),
                )
                .order_by(WorkflowTurnModel.id.asc())
                .first()
            )
        if turn is None:
            return None
        # Do not rely on the ORM identity-map assignment as a claim. Two
        # reconciler threads can read the same QUEUED row; the compare-and-set
        # update makes exactly one of them its transport owner.
        claim_token = uuid.uuid4().hex
        claim_generation = turn.claim_generation + 1
        claim_expires_at = datetime.fromtimestamp(
            now.timestamp() + WORKFLOW_TURN_CLAIM_LEASE_SECONDS
        )
        claimed = (
            db.query(WorkflowTurnModel)
            .filter(
                WorkflowTurnModel.id == turn.id,
                WorkflowTurnModel.state == TURN_QUEUED,
            )
            .update(
                {
                    WorkflowTurnModel.state: TURN_CLAIMED,
                    WorkflowTurnModel.attempt_count: WorkflowTurnModel.attempt_count + 1,
                    WorkflowTurnModel.claim_generation: claim_generation,
                    WorkflowTurnModel.claim_token: claim_token,
                    WorkflowTurnModel.claim_expires_at: claim_expires_at,
                    WorkflowTurnModel.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        if claimed != 1:
            db.rollback()
            return None
        db.commit()
        return {
            "id": turn.id,
            "kind": turn.kind,
            "payload": turn.payload or "",
            "claim_token": claim_token,
            "claim_generation": claim_generation,
        }


def _claim_matches(turn, claim_token: str, claim_generation: int, now: datetime) -> bool:
    return (
        turn.state == TURN_CLAIMED
        and turn.claim_token == claim_token
        and turn.claim_generation == claim_generation
        and turn.claim_expires_at is not None
        and turn.claim_expires_at > now
    )


def renew_workflow_turn_claim(
    turn_id: int,
    claim_token: str,
    claim_generation: int,
    now: Optional[datetime] = None,
) -> bool:
    """Renew one live transport lease; stale claimants are fenced out."""
    _ensure_workflow_schema()
    now = now or datetime.now()
    expires_at = datetime.fromtimestamp(now.timestamp() + WORKFLOW_TURN_CLAIM_LEASE_SECONDS)
    with SessionLocal() as db:
        renewed = (
            db.query(WorkflowTurnModel)
            .filter(
                WorkflowTurnModel.id == turn_id,
                WorkflowTurnModel.state == TURN_CLAIMED,
                WorkflowTurnModel.claim_token == claim_token,
                WorkflowTurnModel.claim_generation == claim_generation,
                WorkflowTurnModel.claim_expires_at > now,
            )
            .update(
                {
                    WorkflowTurnModel.claim_expires_at: expires_at,
                    WorkflowTurnModel.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        if renewed:
            db.commit()
        return renewed == 1


def mark_workflow_turn_sent(
    turn_id: int,
    claim_token: str,
    claim_generation: int,
    now: Optional[datetime] = None,
) -> bool:
    """Acknowledge transport only for the claimant that still owns the turn."""
    _ensure_workflow_schema()
    now = now or datetime.now()
    with SessionLocal() as db:
        turn = db.query(WorkflowTurnModel).filter(WorkflowTurnModel.id == turn_id).first()
        if turn is None or not _claim_matches(turn, claim_token, claim_generation, now):
            return False
        workflow = db.query(WorkflowModel).filter(WorkflowModel.id == turn.workflow_id).first()
        if workflow is None or workflow.status != WORKFLOW_OPEN:
            return False
        # Re-check ownership in the UPDATE itself.  Reading a matching ORM row
        # is not enough: it may be reclaimed between that read and commit.
        acknowledged = (
            db.query(WorkflowTurnModel)
            .filter(
                WorkflowTurnModel.id == turn_id,
                WorkflowTurnModel.state == TURN_CLAIMED,
                WorkflowTurnModel.claim_token == claim_token,
                WorkflowTurnModel.claim_generation == claim_generation,
                WorkflowTurnModel.claim_expires_at > now,
            )
            .update(
                {
                    WorkflowTurnModel.state: TURN_SENT,
                    WorkflowTurnModel.claim_token: None,
                    WorkflowTurnModel.claim_expires_at: None,
                    WorkflowTurnModel.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        if acknowledged != 1:
            db.rollback()
            return False
        # Inbox-backed handoff results now use this same claim/ack path. Keep
        # their existing progress semantics: a durable child result resets the
        # automatic-final guard before the parent continues.
        if turn.kind == "handoff_result":
            (
                db.query(WorkflowModel)
                .filter(
                    WorkflowModel.id == workflow.id,
                    WorkflowModel.status == WORKFLOW_OPEN,
                )
                .update(
                    {WorkflowModel.no_progress_count: 0, WorkflowModel.updated_at: now},
                    synchronize_session=False,
                )
            )
        db.commit()
        return True


def requeue_workflow_turn(
    turn_id: int,
    claim_token: str,
    claim_generation: int,
    now: Optional[datetime] = None,
) -> bool:
    """Retry only the live claimant's failed pre-send attempt."""
    _ensure_workflow_schema()
    now = now or datetime.now()
    with SessionLocal() as db:
        turn = db.query(WorkflowTurnModel).filter(WorkflowTurnModel.id == turn_id).first()
        if turn is None or not _claim_matches(turn, claim_token, claim_generation, now):
            return False
        workflow = db.query(WorkflowModel).filter(WorkflowModel.id == turn.workflow_id).first()
        if workflow is None or workflow.status != WORKFLOW_OPEN:
            return False
        values: Dict[Any, Any] = {
            WorkflowTurnModel.claim_token: None,
            WorkflowTurnModel.claim_expires_at: None,
            WorkflowTurnModel.updated_at: now,
        }
        owner_gate = cast(int, turn.attempt_count) >= 3
        if owner_gate:
            values[WorkflowTurnModel.state] = TURN_CANCELLED
        else:
            values[WorkflowTurnModel.state] = TURN_QUEUED
            values[WorkflowTurnModel.not_before] = datetime.fromtimestamp(
                now.timestamp() + 2 ** (turn.attempt_count - 1)
            )
        requeued = (
            db.query(WorkflowTurnModel)
            .filter(
                WorkflowTurnModel.id == turn_id,
                WorkflowTurnModel.state == TURN_CLAIMED,
                WorkflowTurnModel.claim_token == claim_token,
                WorkflowTurnModel.claim_generation == claim_generation,
                WorkflowTurnModel.claim_expires_at > now,
            )
            .update(values, synchronize_session=False)
        )
        if requeued != 1:
            db.rollback()
            return False
        workflow_values: Dict[Any, Any] = {WorkflowModel.updated_at: now}
        if owner_gate:
            workflow_values.update(
                {
                    WorkflowModel.status: WORKFLOW_OWNER_GATE,
                    WorkflowModel.terminal_reason: "bounded continuation transport retry guard",
                }
            )
        workflow_updated = (
            db.query(WorkflowModel)
            .filter(WorkflowModel.id == workflow.id, WorkflowModel.status == WORKFLOW_OPEN)
            .update(workflow_values, synchronize_session=False)
        )
        if workflow_updated != 1:
            db.rollback()
            return False
        db.commit()
        if owner_gate:
            _dispatch_workflow_notification_fail_open(
                str(workflow.root_terminal_id), "owner_attention", int(workflow.id)
            )
        return True


def requeue_expired_workflow_turn_claims(now: Optional[datetime] = None) -> int:
    """Release unacknowledged claims after their durable lease expires.

    A process can die before or after the irreversible transport boundary.
    Recovery retries the same durable logical turn; delivery is therefore
    at-least-once and consumers must treat its logical-turn key idempotently.
    """
    _ensure_workflow_schema()
    now = now or datetime.now()
    with SessionLocal() as db:
        # The conditional bulk update is itself the expiration fence: a
        # heartbeat that wins first changes claim_expires_at and is never
        # overwritten by this stale reclaimer.
        reclaimed = (
            db.query(WorkflowTurnModel)
            .filter(
                WorkflowTurnModel.state == TURN_CLAIMED,
                WorkflowTurnModel.claim_expires_at <= now,
                WorkflowTurnModel.workflow_id.in_(
                    db.query(WorkflowModel.id).filter(WorkflowModel.status == WORKFLOW_OPEN)
                ),
            )
            .update(
                {
                    WorkflowTurnModel.state: TURN_QUEUED,
                    WorkflowTurnModel.claim_token: None,
                    WorkflowTurnModel.claim_expires_at: None,
                    WorkflowTurnModel.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        if reclaimed:
            db.commit()
        return reclaimed


def requeue_unadmitted_workflow_turns_for_restart(now: Optional[datetime] = None) -> int:
    """Replay a restart-interrupted transport under its same logical turn.

    A ``sent`` turn without a receiver receipt has crossed only the physical
    transport boundary. On restart it is safe to retry that exact envelope;
    the receiver receipt remains the idempotency fence. Restrict this to the
    current active turn so historical audit rows can never be revived.
    """
    _ensure_workflow_schema()
    now = now or datetime.now()
    with SessionLocal() as db:
        unreceived = (
            db.query(WorkflowTurnModel)
            .filter(
                WorkflowTurnModel.state == TURN_SENT,
                WorkflowTurnModel.workflow_id.in_(
                    db.query(WorkflowModel.id).filter(
                        WorkflowModel.status == WORKFLOW_OPEN,
                        WorkflowModel.active_turn_id == WorkflowTurnModel.id,
                    )
                ),
                ~db.query(WorkflowTurnReceiptModel)
                .filter(
                    WorkflowTurnReceiptModel.workflow_turn_id == WorkflowTurnModel.id,
                )
                .exists(),
            )
            .update(
                {
                    WorkflowTurnModel.state: TURN_QUEUED,
                    WorkflowTurnModel.claim_token: None,
                    WorkflowTurnModel.claim_expires_at: None,
                    WorkflowTurnModel.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        if unreceived:
            db.commit()
        return unreceived


def set_workflow_terminal_state(
    root_terminal_id: str,
    status: str,
    reason: Optional[str] = None,
    require_no_active_children: bool = False,
) -> bool:
    """Explicitly end a workflow and suppress every unsent durable wake.

    Normal completion may opt into the active-child guard. Explicit owner
    gates and lifecycle cancellation intentionally remain able to fence live
    children, because those paths carry an explicit decision to stop work.
    """
    if status not in {WORKFLOW_TERMINAL, WORKFLOW_OWNER_GATE, WORKFLOW_CANCELLED}:
        raise ValueError(f"Invalid terminal workflow state: {status}")
    _ensure_workflow_schema()
    with SessionLocal() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        workflow = _open_workflow(db, root_terminal_id, create=False)
        if workflow is None:
            return False
        notify_transition = workflow.status == WORKFLOW_OPEN
        workflow_id = int(workflow.id)
        now = datetime.now()
        if require_no_active_children:
            active_children = (
                db.query(ChildAssignmentModel)
                .filter(
                    ChildAssignmentModel.parent_terminal_id == root_terminal_id,
                    ChildAssignmentModel.status.in_(
                        (
                            ChildAssignmentStatus.AWAITING_RESULT.value,
                            ChildAssignmentStatus.RESULT_QUEUED.value,
                            ChildAssignmentStatus.RESULT_DELIVERED.value,
                            ChildAssignmentStatus.RESULT_FAILED.value,
                            ChildAssignmentStatus.HANDOFF_AWAITING_RESULT.value,
                            ChildAssignmentStatus.HANDOFF_RECOVERY_AWAITING_RESULT.value,
                            ChildAssignmentStatus.HANDOFF_DIRECT_RESULT_CLAIMED.value,
                            ChildAssignmentStatus.HANDOFF_RESULT_QUEUED.value,
                            ChildAssignmentStatus.HANDOFF_RESULT_DELIVERED.value,
                            ChildAssignmentStatus.HANDOFF_RESULT_FAILED.value,
                        )
                    ),
                )
                .count()
            )
            if active_children:
                return False
        workflow.status = status
        workflow.terminal_reason = reason
        workflow.updated_at = now
        (
            db.query(WorkflowTurnModel)
            .filter(
                WorkflowTurnModel.workflow_id == workflow.id,
                WorkflowTurnModel.state.in_((TURN_QUEUED, TURN_CLAIMED)),
            )
            .update(
                {
                    WorkflowTurnModel.state: TURN_CANCELLED,
                    WorkflowTurnModel.claim_token: None,
                    WorkflowTurnModel.claim_expires_at: None,
                },
                synchronize_session=False,
            )
        )
        # State transition and callback fence share this transaction: no late
        # child result can observe a terminal workflow while retaining a live
        # assignment edge that would wake it again.
        _cancel_parent_assignments(db, root_terminal_id, now)
        # Closing the root and releasing its provider ownership are one SQLite
        # transaction.  Acquisition validates the OPEN workflow under the same
        # write-serialization boundary, so a closed root cannot leak a lease or
        # acquire a successor between these two state changes.
        db.query(ProviderExecutionLeaseModel).filter(
            ProviderExecutionLeaseModel.terminal_id == root_terminal_id
        ).delete(synchronize_session=False)
        db.commit()
        if notify_transition and status == WORKFLOW_TERMINAL:
            _dispatch_workflow_notification_fail_open(root_terminal_id, "completed", workflow_id)
        elif notify_transition and status == WORKFLOW_OWNER_GATE:
            _dispatch_workflow_notification_fail_open(
                root_terminal_id, "owner_attention", workflow_id
            )
        return True


def cancel_workflows_for_terminal_with_ids(terminal_id: str) -> List[int]:
    """Cancel open workflows and return the exact transition-winning IDs."""
    _ensure_workflow_schema()
    with SessionLocal() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        workflows = (
            db.query(WorkflowModel)
            .filter(
                WorkflowModel.root_terminal_id == terminal_id,
                WorkflowModel.status == WORKFLOW_OPEN,
            )
            .all()
        )
        workflow_ids = [int(workflow.id) for workflow in workflows]
        now = datetime.now()
        for workflow in workflows:
            workflow.status = WORKFLOW_CANCELLED
            workflow.terminal_reason = "root terminal exited or deleted"
            workflow.updated_at = now
            (
                db.query(WorkflowTurnModel)
                .filter(
                    WorkflowTurnModel.workflow_id == workflow.id,
                    WorkflowTurnModel.state.in_((TURN_QUEUED, TURN_CLAIMED)),
                )
                .update(
                    {
                        WorkflowTurnModel.state: TURN_CANCELLED,
                        WorkflowTurnModel.claim_token: None,
                        WorkflowTurnModel.claim_expires_at: None,
                    },
                    synchronize_session=False,
                )
            )
        # A session/terminal removal can race a child callback.  Fence every
        # active edge owned by this parent in the same commit as cancellation.
        assignments_cancelled = _cancel_parent_assignments(db, terminal_id, now)
        execution_released = (
            db.query(ProviderExecutionLeaseModel)
            .filter(ProviderExecutionLeaseModel.terminal_id == terminal_id)
            .delete(synchronize_session=False)
        )
        if workflows or assignments_cancelled or execution_released:
            db.commit()
        return workflow_ids


def cancel_workflows_for_terminal(terminal_id: str) -> int:
    """Cancel open workflows while preserving the established count contract."""
    return len(cancel_workflows_for_terminal_with_ids(terminal_id))


def _inbox_model_to_message(inbox_msg: InboxModel) -> InboxMessage:
    return InboxMessage(
        id=inbox_msg.id,
        sender_id=inbox_msg.sender_id,
        receiver_id=inbox_msg.receiver_id,
        message=inbox_msg.message,
        status=MessageStatus(inbox_msg.status),
        result_id=inbox_msg.result_id,
        # Legacy rows and lightweight mocked rows can predate the additive
        # non-null migration; they remain ordinary transport messages.
        kind=inbox_msg.kind or "message",
        superseded_at=inbox_msg.superseded_at,
        created_at=inbox_msg.created_at,
    )


def _result_to_dict(
    result: DelegationResultModel, delivery_status: Optional[str] = None
) -> Dict[str, Any]:
    payload = {
        "id": result.id,
        "child_assignment_id": result.child_assignment_id,
        "schema_version": result.schema_version,
        "delegation_kind": result.delegation_kind,
        "parent_terminal_id": result.parent_terminal_id,
        "child_terminal_id": result.child_terminal_id,
        "session_name": result.session_name,
        "child_provider": result.child_provider,
        "child_agent_profile": result.child_agent_profile,
        "parent_workflow_id": result.parent_workflow_id,
        "workflow_turn_id": result.workflow_turn_id,
        "workflow_effect_id": result.workflow_effect_id,
        "authorship": result.authorship,
        "status": result.status,
        "reason_code": result.reason_code,
        "document": json.loads(result.document_json) if result.document_json else None,
        "content_sha256": result.content_sha256,
        "content_bytes": result.content_bytes,
        "created_at": result.created_at,
        "finalized_at": result.finalized_at,
        "updated_at": result.updated_at,
        "content_purged_at": result.content_purged_at,
    }
    if delivery_status is not None:
        payload["delivery_status"] = delivery_status
    return payload


def get_delegation_result(result_id: str) -> Optional[Dict[str, Any]]:
    _ensure_delegation_result_schema()
    with SessionLocal() as db:
        result = db.query(DelegationResultModel).filter_by(id=result_id).first()
        if result is None:
            return None
        assignment = db.query(ChildAssignmentModel).filter_by(id=result.child_assignment_id).first()
        # A missing mutable assignment row is not durable evidence that the
        # immutable result was superseded.  Keep the result and omit only the
        # delivery projection when it is no longer available.
        return _result_to_dict(result, assignment.status if assignment else None)


def list_delegation_results(
    terminal_id: Optional[str] = None,
    session_name: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    cursor: Optional[str] = None,
) -> List[Dict[str, Any]]:
    _ensure_delegation_result_schema()
    with SessionLocal() as db:
        query = db.query(DelegationResultModel)
        if terminal_id:
            query = query.filter(
                (DelegationResultModel.parent_terminal_id == terminal_id)
                | (DelegationResultModel.child_terminal_id == terminal_id)
            )
        if session_name:
            query = query.filter(DelegationResultModel.session_name == session_name)
        if status:
            query = query.filter(DelegationResultModel.status == status)
        if cursor:
            query = query.filter(DelegationResultModel.id < cursor)
        rows = (
            query.order_by(DelegationResultModel.created_at.desc(), DelegationResultModel.id.desc())
            .limit(limit)
            .all()
        )
        assignment_states = {
            assignment.id: assignment.status
            for assignment in db.query(ChildAssignmentModel)
            .filter(ChildAssignmentModel.id.in_([row.child_assignment_id for row in rows]))
            .all()
        }
        return [
            _result_to_dict(row, assignment_states.get(row.child_assignment_id)) for row in rows
        ]


def get_delegation_result_for_assignment(child_terminal_id: str) -> Optional[Dict[str, Any]]:
    _ensure_delegation_result_schema()
    with SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel).filter_by(child_terminal_id=child_terminal_id).first()
        )
        if not assignment:
            return None
        result = (
            db.query(DelegationResultModel).filter_by(child_assignment_id=assignment.id).first()
        )
        return _result_to_dict(result) if result else None


def _exact_retirement_cleanup_intent(
    terminal: TerminalModel, *, legacy_completed_at: Optional[datetime] = None
) -> Optional[str]:
    """Serialize exact cleanup authority, or refuse incomplete managed identity."""
    kind = terminal.managed_worktree_kind
    if kind is None:
        if any(
            value is not None
            for value in (
                terminal.managed_worktree_source,
                terminal.managed_worktree_branch,
                terminal.managed_worktree_commit,
            )
        ):
            return None
        payload: Dict[str, Any] = {
            "version": 1,
            "terminal_id": terminal.id,
            "managed": False,
        }
    else:
        values = (
            terminal.launch_worktree,
            terminal.managed_worktree_source,
            terminal.managed_worktree_commit,
        )
        if (
            kind not in {"task", "reviewer"}
            or not all(isinstance(value, str) and value for value in values)
            or not cast(str, terminal.launch_worktree).startswith("/")
            or not cast(str, terminal.managed_worktree_source).startswith("/")
            or not re.fullmatch(r"[0-9a-f]{40}", cast(str, terminal.managed_worktree_commit))
        ):
            return None
        expected_branch = f"cao/task/{terminal.id}" if kind == "task" else None
        if terminal.managed_worktree_branch != expected_branch:
            return None
        payload = {
            "version": 1,
            "terminal_id": terminal.id,
            "managed": True,
            "id": terminal.id,
            "launch_worktree": terminal.launch_worktree,
            "managed_worktree_kind": kind,
            "managed_worktree_source": terminal.managed_worktree_source,
            "managed_worktree_branch": terminal.managed_worktree_branch,
            "managed_worktree_commit": terminal.managed_worktree_commit,
        }
    if legacy_completed_at is not None:
        payload["legacy_retirement_completed_at"] = legacy_completed_at.isoformat()
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _decode_retirement_cleanup_intent(
    assignment: ChildAssignmentModel,
) -> Optional[Dict[str, Any]]:
    try:
        intent = json.loads(cast(str, assignment.retirement_cleanup_intent))
    except (TypeError, ValueError):
        return None
    if (
        not isinstance(intent, dict)
        or intent.get("version") != 1
        or intent.get("terminal_id") != assignment.child_terminal_id
        or not isinstance(intent.get("managed"), bool)
    ):
        return None
    return intent


def get_child_retirement_cleanup_intent(
    child_terminal_id: str, claim_token: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Return a validated pending/final cleanup intent without live inference."""
    _ensure_child_assignment_schema()
    with SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel)
            .filter(ChildAssignmentModel.child_terminal_id == child_terminal_id)
            .first()
        )
        if assignment is None:
            return None
        if claim_token is not None and assignment.retirement_claim_token != claim_token:
            return None
        intent = _decode_retirement_cleanup_intent(assignment)
        if intent is None:
            return None
        return {
            "intent": intent,
            "cleanup_completed": assignment.retirement_cleanup_completed_at is not None,
            "retirement_completed": (
                assignment.retirement_cleanup_completed_at is not None
                and assignment.retirement_completed_at is not None
            ),
            "claim_token": assignment.retirement_claim_token,
        }


# Compatibility name retained for callers and rolling source deployments.
get_assigned_child_retirement_cleanup_intent = get_child_retirement_cleanup_intent


_HANDOFF_RETIREMENT_RESULT_STATUSES = (
    ChildAssignmentStatus.HANDOFF_DIRECT_RESULT_CLAIMED.value,
    ChildAssignmentStatus.HANDOFF_RESULT_QUEUED.value,
    ChildAssignmentStatus.HANDOFF_RESULT_DELIVERED.value,
    ChildAssignmentStatus.HANDOFF_RESULT_ACKNOWLEDGED.value,
)


def _completed_retirement_result(
    db: Any, assignment: ChildAssignmentModel, delegation_kind: str
) -> Optional[DelegationResultModel]:
    """Return only one exact, immutable, finalized result for this relation."""
    result = (
        db.query(DelegationResultModel)
        .filter(DelegationResultModel.child_assignment_id == assignment.id)
        .first()
    )
    if (
        result is None
        or result.delegation_kind != delegation_kind
        or result.parent_terminal_id != assignment.parent_terminal_id
        or result.child_terminal_id != assignment.child_terminal_id
        or result.status != DelegationResultStatus.COMPLETE.value
        or result.finalized_at is None
    ):
        return None
    return cast(DelegationResultModel, result)


def _handoff_retirement_eligible(
    db: Any,
    assignment: ChildAssignmentModel,
    terminal: TerminalModel,
) -> tuple[bool, str]:
    """Fence managed direct-handoff cleanup from every non-final outcome."""
    if assignment.status not in _HANDOFF_RETIREMENT_RESULT_STATUSES:
        return False, "handoff_result_not_final"
    if _completed_retirement_result(db, assignment, "handoff") is None:
        return False, "handoff_result_not_complete"
    if terminal.runtime_lifecycle != "exited":
        return False, "handoff_runtime_not_exited"
    workflow = _open_workflow(db, assignment.child_terminal_id, create=False)
    if workflow is None or workflow.status != WORKFLOW_TERMINAL:
        return False, "child_workflow_not_terminal"
    active_children = (
        db.query(ChildAssignmentModel)
        .filter(
            ChildAssignmentModel.parent_terminal_id == assignment.child_terminal_id,
            ChildAssignmentModel.status.in_(_active_child_assignment_statuses()),
        )
        .count()
    )
    if active_children:
        return False, "active_child_completion_barrier"
    return True, "eligible"


def _historical_assigned_retirement_error(
    db: Any,
    supervisor_terminal_id: str,
    assignment: ChildAssignmentModel,
    child_terminal: TerminalModel,
) -> Optional[str]:
    """Validate the narrow replacement-supervisor recovery authority.

    This does not transfer assignment ownership.  It only lets an exact,
    same-session supervisor claim cleanup after the original parent and child
    have both positively left runtime capacity and the assigned result is
    already immutable, final, and acknowledged.
    """
    supervisor = db.query(TerminalModel).filter_by(id=supervisor_terminal_id).first()
    if supervisor is None or supervisor.context_role != "supervisor":
        # Preserve the long-standing foreign-assignment response while
        # withholding the existence and ownership details of the relation.
        return "child_assignment_not_owned"
    original_parent = db.query(TerminalModel).filter_by(id=assignment.parent_terminal_id).first()
    if original_parent is None:
        return "historical_parent_terminal_not_found"
    session_name = child_terminal.tmux_session
    if (
        not session_name
        or supervisor.tmux_session != session_name
        or original_parent.tmux_session != session_name
    ):
        return "retirement_session_mismatch"
    if original_parent.runtime_lifecycle != "exited":
        return "historical_parent_runtime_active"
    parent_workflow = _open_workflow(db, assignment.parent_terminal_id, create=False)
    if parent_workflow is None or parent_workflow.status not in {
        WORKFLOW_TERMINAL,
        WORKFLOW_OWNER_GATE,
        WORKFLOW_CANCELLED,
    }:
        return "historical_parent_workflow_active"
    if assignment.status != ChildAssignmentStatus.RESULT_ACKNOWLEDGED.value:
        return "assigned_result_not_acknowledged"
    result = _completed_retirement_result(db, assignment, "assign")
    if result is None:
        return "delegation_result_not_complete"
    if result.session_name != session_name:
        return "retirement_session_mismatch"
    if child_terminal.runtime_lifecycle != "exited":
        return "child_runtime_not_exited"
    if (
        db.query(WorktreeWriterLeaseModel)
        .filter(WorktreeWriterLeaseModel.terminal_id == assignment.child_terminal_id)
        .first()
        is not None
    ):
        return "child_capacity_not_released"
    encoded_intent = _exact_retirement_cleanup_intent(child_terminal)
    try:
        intent = json.loads(encoded_intent) if encoded_intent is not None else None
    except (TypeError, ValueError):
        intent = None
    if not isinstance(intent, dict) or intent.get("managed") is not True:
        return "retirement_cleanup_identity_unproven"
    return None


def managed_handoff_retirement_required(
    parent_terminal_id: str, child_terminal_id: str
) -> Optional[bool]:
    """Classify whether a registered direct handoff owns managed cleanup.

    ``None`` is not cleanup authority: it represents no exact relation. The
    subsequent handoff acknowledgement remains the fail-closed relation check.
    Partial or missing terminal identity returns ``True`` so the retirement
    claim, rather than a legacy unmanaged bypass, rejects it explicitly.
    """
    _ensure_child_assignment_schema()
    _ensure_terminal_worktree_authority_schema()
    with SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel)
            .filter(ChildAssignmentModel.child_terminal_id == child_terminal_id)
            .first()
        )
        if assignment is None:
            return None
        if assignment.parent_terminal_id != parent_terminal_id or not assignment.status.startswith(
            "handoff_"
        ):
            return True
        terminal = db.query(TerminalModel).filter_by(id=child_terminal_id).first()
        if terminal is None:
            return True
        if terminal.managed_worktree_kind is not None:
            return True
        return any(
            value is not None
            for value in (
                terminal.managed_worktree_source,
                terminal.managed_worktree_branch,
                terminal.managed_worktree_commit,
            )
        )


def list_pending_child_retirement_cleanups() -> List[Dict[str, Any]]:
    """List exited runtimes whose exact cleanup saga still needs completion."""
    _ensure_child_assignment_schema()
    _ensure_terminal_worktree_authority_schema()
    with SessionLocal() as db:
        rows = (
            db.query(ChildAssignmentModel, TerminalModel)
            .join(TerminalModel, TerminalModel.id == ChildAssignmentModel.child_terminal_id)
            .filter(
                ChildAssignmentModel.retirement_cleanup_intent.is_not(None),
                ChildAssignmentModel.retirement_cleanup_completed_at.is_(None),
                TerminalModel.runtime_lifecycle == "exited",
            )
            .all()
        )
        pending = []
        for assignment, terminal in rows:
            intent = _decode_retirement_cleanup_intent(assignment)
            delegation_kind = "handoff" if assignment.status.startswith("handoff_") else "assign"
            handoff_eligible = (
                delegation_kind != "handoff"
                or _handoff_retirement_eligible(db, assignment, terminal)[0]
            )
            if intent is not None and handoff_eligible:
                pending.append(
                    {
                        "child_terminal_id": assignment.child_terminal_id,
                        "claim_token": assignment.retirement_claim_token,
                        "delegation_kind": delegation_kind,
                        "intent": intent,
                    }
                )
        return pending


list_pending_assigned_child_retirement_cleanups = list_pending_child_retirement_cleanups


def list_legacy_child_retirements_for_cleanup() -> List[Dict[str, Any]]:
    """Inventory pre-saga final markers with read-only identity proof."""
    _ensure_child_assignment_schema()
    _ensure_terminal_worktree_authority_schema()
    with SessionLocal() as db:
        rows = (
            db.query(ChildAssignmentModel, TerminalModel)
            .join(TerminalModel, TerminalModel.id == ChildAssignmentModel.child_terminal_id)
            .filter(
                ChildAssignmentModel.retirement_cleanup_intent.is_(None),
                ChildAssignmentModel.retirement_cleanup_completed_at.is_(None),
                TerminalModel.runtime_lifecycle == "exited",
            )
            .all()
        )
        candidates = []
        for assignment, terminal in rows:
            delegation_kind = "handoff" if assignment.status.startswith("handoff_") else "assign"
            if delegation_kind == "assign":
                if assignment.retirement_completed_at is None:
                    continue
            elif not _handoff_retirement_eligible(db, assignment, terminal)[0]:
                continue
            encoded_intent = _exact_retirement_cleanup_intent(
                terminal, legacy_completed_at=assignment.retirement_completed_at
            )
            decoded_intent = json.loads(encoded_intent) if encoded_intent is not None else None
            candidates.append(
                {
                    "parent_terminal_id": assignment.parent_terminal_id,
                    "child_terminal_id": assignment.child_terminal_id,
                    "delegation_kind": delegation_kind,
                    "identity_proven": bool(
                        isinstance(decoded_intent, dict)
                        and (delegation_kind != "handoff" or decoded_intent.get("managed") is True)
                    ),
                    "intent": decoded_intent,
                }
            )
        return candidates


list_legacy_assigned_child_retirements_for_cleanup = list_legacy_child_retirements_for_cleanup


def claim_completed_child_retirement(
    parent_terminal_id: str,
    child_terminal_id: str,
    expected_delegation_kind: Optional[str] = None,
) -> Dict[str, Any]:
    """Atomically claim exact cleanup for an eligible completed child.

    Assigned children retain their acknowledgement/quiescence/provider-exit
    contract.  A direct handoff can join only after its exact immutable result
    is final and its runtime is positively exited; this path never fabricates
    ``result_acknowledged`` for a handoff.
    """
    _ensure_child_assignment_schema()
    _ensure_delegation_result_schema()
    _ensure_workflow_schema()
    with SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel)
            .filter(ChildAssignmentModel.child_terminal_id == child_terminal_id)
            .first()
        )
        if assignment is None:
            return {"eligible": False, "error": "child_assignment_not_found"}
        historical_supervisor = assignment.parent_terminal_id != parent_terminal_id
        delegation_kind = "handoff" if assignment.status.startswith("handoff_") else "assign"
        if expected_delegation_kind is not None and delegation_kind != expected_delegation_kind:
            return {"eligible": False, "error": "wrong_delegation_kind"}
        terminal = db.query(TerminalModel).filter(TerminalModel.id == child_terminal_id).first()
        if terminal is None:
            return {"eligible": False, "error": "child_terminal_metadata_not_found"}
        if historical_supervisor:
            if delegation_kind != "assign":
                return {"eligible": False, "error": "child_assignment_not_owned"}
            historical_error = _historical_assigned_retirement_error(
                db, parent_terminal_id, assignment, terminal
            )
            if historical_error is not None:
                return {"eligible": False, "error": historical_error}
        if delegation_kind == "handoff":
            eligible, error = _handoff_retirement_eligible(db, assignment, terminal)
            if not eligible:
                return {"eligible": False, "error": error}
            # Historical unmanaged handoffs have no worktree authority to
            # retire. Preserve their existing result-delivery behavior.
            if terminal.managed_worktree_kind is None and all(
                value is None
                for value in (
                    terminal.managed_worktree_source,
                    terminal.managed_worktree_branch,
                    terminal.managed_worktree_commit,
                )
            ):
                return {
                    "eligible": True,
                    "cleanup_required": False,
                    "delegation_kind": "handoff",
                }
        elif assignment.status != ChildAssignmentStatus.RESULT_ACKNOWLEDGED.value:
            return {"eligible": False, "error": "assigned_result_not_acknowledged"}

        if (
            assignment.retirement_completed_at is not None
            and assignment.retirement_cleanup_completed_at is not None
        ):
            return {
                "eligible": True,
                "already_retired": True,
                "cleanup_required": True,
                "delegation_kind": delegation_kind,
            }
        if assignment.retirement_cleanup_intent is None:
            cleanup_intent = _exact_retirement_cleanup_intent(
                terminal, legacy_completed_at=assignment.retirement_completed_at
            )
            decoded_cleanup_intent = (
                json.loads(cleanup_intent) if cleanup_intent is not None else None
            )
            if cleanup_intent is None or (
                (delegation_kind == "handoff" or historical_supervisor)
                and (
                    not isinstance(decoded_cleanup_intent, dict)
                    or decoded_cleanup_intent.get("managed") is not True
                )
            ):
                return {
                    "eligible": False,
                    "recoverable": True,
                    "status": "retirement_cleanup_pending",
                    "error": "retirement_cleanup_identity_unproven",
                    "reason_code": "RETIREMENT_CLEANUP_IDENTITY_UNPROVEN",
                }
            assignment.retirement_cleanup_intent = cleanup_intent
            assignment.updated_at = datetime.now()
            db.flush()
        elif delegation_kind == "handoff" or historical_supervisor:
            cleanup_intent = _decode_retirement_cleanup_intent(assignment)
            if cleanup_intent is None or cleanup_intent.get("managed") is not True:
                return {
                    "eligible": False,
                    "recoverable": True,
                    "status": "retirement_cleanup_pending",
                    "error": "retirement_cleanup_identity_unproven",
                    "reason_code": "RETIREMENT_CLEANUP_IDENTITY_UNPROVEN",
                }
        if assignment.retirement_exit_dispatched_at is not None:
            if assignment.retirement_claim_token is None:
                # Rolling-upgrade recovery for historical rows such as C1's
                # 05049a08: exact persisted identity is sufficient to resume
                # cleanup, but never to redispatch provider exit.
                assignment.retirement_claim_token = uuid.uuid4().hex
                assignment.retirement_claimed_at = datetime.now()
                db.commit()
            return {
                "eligible": True,
                "claim_token": assignment.retirement_claim_token,
                "exit_dispatch_reserved": True,
                "cleanup_required": True,
                "delegation_kind": delegation_kind,
                "historical_recovery": historical_supervisor,
            }
        if assignment.retirement_claim_token is not None:
            return {"eligible": False, "error": "child_retirement_in_progress"}

        result_row = (
            db.query(DelegationResultModel)
            .filter(DelegationResultModel.child_assignment_id == assignment.id)
            .first()
        )
        if result_row is None:
            return {"eligible": False, "error": "delegation_result_not_found"}
        result = _completed_retirement_result(db, assignment, delegation_kind)
        if result is None:
            return {"eligible": False, "error": "delegation_result_not_complete"}

        workflow = _open_workflow(db, child_terminal_id, create=False)
        if workflow is None or workflow.status != WORKFLOW_TERMINAL:
            return {"eligible": False, "error": "child_workflow_not_terminal"}

        active_children = (
            db.query(ChildAssignmentModel)
            .filter(
                ChildAssignmentModel.parent_terminal_id == child_terminal_id,
                ChildAssignmentModel.status.in_(_active_child_assignment_statuses()),
            )
            .count()
        )
        if active_children:
            return {
                "eligible": False,
                "error": "active_child_completion_barrier",
                "active_children": active_children,
            }
        token = uuid.uuid4().hex
        claimed_at = datetime.now()
        expected_status = (
            assignment.status
            if delegation_kind == "handoff"
            else ChildAssignmentStatus.RESULT_ACKNOWLEDGED.value
        )
        update_values: Dict[Any, Any] = {
            ChildAssignmentModel.retirement_claim_token: token,
            ChildAssignmentModel.retirement_claimed_at: claimed_at,
            ChildAssignmentModel.retirement_cleanup_intent: assignment.retirement_cleanup_intent,
            ChildAssignmentModel.updated_at: claimed_at,
        }
        if delegation_kind == "handoff":
            # The normal handoff path already crossed and positively observed
            # provider exit. Record that fact; never dispatch another exit.
            update_values[ChildAssignmentModel.retirement_exit_dispatched_at] = claimed_at
        elif historical_supervisor:
            # Both runtime death and capacity release were proven above.  This
            # recovery must therefore reconcile cleanup only and can never
            # redispatch provider exit on behalf of the inactive parent.
            update_values[ChildAssignmentModel.retirement_exit_dispatched_at] = claimed_at
        claimed = (
            db.query(ChildAssignmentModel)
            .filter(
                ChildAssignmentModel.child_terminal_id == child_terminal_id,
                ChildAssignmentModel.parent_terminal_id == assignment.parent_terminal_id,
                ChildAssignmentModel.status == expected_status,
                ChildAssignmentModel.retirement_claim_token.is_(None),
                ChildAssignmentModel.retirement_exit_dispatched_at.is_(None),
                ChildAssignmentModel.retirement_completed_at.is_(None),
            )
            .update(update_values, synchronize_session=False)
        )
        if claimed != 1:
            db.rollback()
            return {"eligible": False, "error": "child_retirement_in_progress"}
        if historical_supervisor:
            # The assignment update acquires the database writer boundary.
            # Re-read every cross-row authority predicate before commit so a
            # concurrent parent/runtime/workflow transition cannot be hidden
            # behind the earlier snapshot.
            db.expire_all()
            refreshed_assignment = (
                db.query(ChildAssignmentModel)
                .filter_by(child_terminal_id=child_terminal_id)
                .first()
            )
            refreshed_terminal = db.query(TerminalModel).filter_by(id=child_terminal_id).first()
            if (
                refreshed_assignment is None
                or refreshed_terminal is None
                or _historical_assigned_retirement_error(
                    db, parent_terminal_id, refreshed_assignment, refreshed_terminal
                )
                is not None
            ):
                db.rollback()
                return {"eligible": False, "error": "historical_retirement_authority_lost"}
        db.commit()
        return {
            "eligible": True,
            "result_id": result.id,
            "claim_token": token,
            "cleanup_required": True,
            "delegation_kind": delegation_kind,
            "exit_dispatch_reserved": delegation_kind == "handoff" or historical_supervisor,
            "historical_recovery": historical_supervisor,
        }


def claim_completed_assigned_child_retirement(
    parent_terminal_id: str, child_terminal_id: str
) -> Dict[str, Any]:
    """Compatibility wrapper retaining assigned-only public semantics."""
    outcome = claim_completed_child_retirement(parent_terminal_id, child_terminal_id, "assign")
    if outcome.get("error") == "wrong_delegation_kind":
        return {"eligible": False, "error": "handoff_child_not_retireable"}
    return outcome


def claim_completed_handoff_child_retirement(
    parent_terminal_id: str, child_terminal_id: str
) -> Dict[str, Any]:
    """Claim post-exit cleanup for one exact completed direct handoff."""
    return claim_completed_child_retirement(parent_terminal_id, child_terminal_id, "handoff")


def revalidate_completed_assigned_child_retirement(
    parent_terminal_id: str, child_terminal_id: str, claim_token: str
) -> bool:
    """Confirm a retirement claim still owns a quiescent external-exit boundary."""
    _ensure_child_assignment_schema()
    _ensure_delegation_result_schema()
    _ensure_workflow_schema()
    with SessionLocal() as db:
        owns_claim = (
            db.query(ChildAssignmentModel)
            .filter(
                ChildAssignmentModel.child_terminal_id == child_terminal_id,
                ChildAssignmentModel.parent_terminal_id == parent_terminal_id,
                ChildAssignmentModel.retirement_claim_token == claim_token,
                ChildAssignmentModel.retirement_cleanup_completed_at.is_(None),
                ChildAssignmentModel.status == ChildAssignmentStatus.RESULT_ACKNOWLEDGED.value,
            )
            .update(
                {ChildAssignmentModel.updated_at: datetime.now()},
                synchronize_session=False,
            )
        )
        if owns_claim != 1:
            db.rollback()
            return False
        workflow = _open_workflow(db, child_terminal_id, create=False)
        if workflow is None or workflow.status != WORKFLOW_TERMINAL:
            db.rollback()
            return False
        active_children = (
            db.query(ChildAssignmentModel)
            .filter(
                ChildAssignmentModel.parent_terminal_id == child_terminal_id,
                ChildAssignmentModel.status.in_(_active_child_assignment_statuses()),
            )
            .count()
        )
        if active_children:
            db.rollback()
            return False
        db.commit()
        return True


def revalidate_historical_assigned_child_retirement(
    supervisor_terminal_id: str, child_terminal_id: str, claim_token: str
) -> bool:
    """Recheck replacement-supervisor authority at a cleanup boundary."""
    _ensure_child_assignment_schema()
    _ensure_delegation_result_schema()
    _ensure_workflow_schema()
    with SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel)
            .filter(
                ChildAssignmentModel.child_terminal_id == child_terminal_id,
                ChildAssignmentModel.retirement_claim_token == claim_token,
                ChildAssignmentModel.retirement_cleanup_completed_at.is_(None),
            )
            .first()
        )
        terminal = db.query(TerminalModel).filter_by(id=child_terminal_id).first()
        if (
            assignment is None
            or terminal is None
            or assignment.parent_terminal_id == supervisor_terminal_id
            or _historical_assigned_retirement_error(
                db, supervisor_terminal_id, assignment, terminal
            )
            is not None
        ):
            return False
        workflow = _open_workflow(db, child_terminal_id, create=False)
        if workflow is None or workflow.status != WORKFLOW_TERMINAL:
            return False
        if (
            db.query(ChildAssignmentModel)
            .filter(
                ChildAssignmentModel.parent_terminal_id == child_terminal_id,
                ChildAssignmentModel.status.in_(_active_child_assignment_statuses()),
            )
            .count()
        ):
            return False
        assignment.updated_at = datetime.now()
        db.commit()
        return True


def release_completed_assigned_child_retirement(child_terminal_id: str, claim_token: str) -> bool:
    """Release a claim that did not cross the provider exit boundary."""
    _ensure_child_assignment_schema()
    with SessionLocal() as db:
        released = (
            db.query(ChildAssignmentModel)
            .filter(
                ChildAssignmentModel.child_terminal_id == child_terminal_id,
                ChildAssignmentModel.retirement_claim_token == claim_token,
                ChildAssignmentModel.retirement_exit_dispatched_at.is_(None),
                ChildAssignmentModel.retirement_cleanup_completed_at.is_(None),
            )
            .update(
                {
                    ChildAssignmentModel.retirement_claim_token: None,
                    ChildAssignmentModel.retirement_claimed_at: None,
                    ChildAssignmentModel.updated_at: datetime.now(),
                },
                synchronize_session=False,
            )
        )
        if released:
            db.commit()
        return released == 1


def reserve_completed_assigned_child_retirement_exit(
    child_terminal_id: str, claim_token: str
) -> bool:
    """Durably reserve the one allowed automatic provider exit.

    The reservation is intentionally retained if the following provider call
    raises, loses its response, or the process crashes.  Retrying code must
    then observe the terminal and finalize only an already-exited child.
    """
    _ensure_child_assignment_schema()
    now = datetime.now()
    with SessionLocal() as db:
        reserved = (
            db.query(ChildAssignmentModel)
            .filter(
                ChildAssignmentModel.child_terminal_id == child_terminal_id,
                ChildAssignmentModel.retirement_claim_token == claim_token,
                ChildAssignmentModel.retirement_exit_dispatched_at.is_(None),
                ChildAssignmentModel.retirement_cleanup_completed_at.is_(None),
            )
            .update(
                {
                    ChildAssignmentModel.retirement_exit_dispatched_at: now,
                    ChildAssignmentModel.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        if reserved:
            db.commit()
        return reserved == 1


def cancel_reserved_completed_assigned_child_retirement_exit(
    child_terminal_id: str, claim_token: str
) -> bool:
    """Release a reservation when the caller proves dispatch never began.

    This is deliberately narrower than generic retirement release: callers may
    use it only at an in-process fence between the durable reservation and the
    provider call.  A failed CAS retains the reservation fail-closed because a
    different observer may already own a later lifecycle state.
    """
    _ensure_child_assignment_schema()
    with SessionLocal() as db:
        released = (
            db.query(ChildAssignmentModel)
            .filter(
                ChildAssignmentModel.child_terminal_id == child_terminal_id,
                ChildAssignmentModel.retirement_claim_token == claim_token,
                ChildAssignmentModel.retirement_exit_dispatched_at.is_not(None),
                ChildAssignmentModel.retirement_cleanup_completed_at.is_(None),
            )
            .update(
                {
                    ChildAssignmentModel.retirement_claim_token: None,
                    ChildAssignmentModel.retirement_claimed_at: None,
                    ChildAssignmentModel.retirement_exit_dispatched_at: None,
                    ChildAssignmentModel.updated_at: datetime.now(),
                },
                synchronize_session=False,
            )
        )
        if released:
            db.commit()
        return released == 1


def complete_child_retirement(
    child_terminal_id: str,
    claim_token: str,
    cleanup_intent: Mapping[str, Any],
    expected_delegation_kind: Optional[str] = None,
    retiring_supervisor_terminal_id: Optional[str] = None,
) -> bool:
    """CAS-seal retirement only after exact cleanup has been verified."""
    _ensure_child_assignment_schema()
    if (
        cleanup_intent.get("version") != 1
        or cleanup_intent.get("terminal_id") != child_terminal_id
        or not isinstance(cleanup_intent.get("managed"), bool)
    ):
        return False
    encoded_intent = json.dumps(dict(cleanup_intent), sort_keys=True, separators=(",", ":"))
    now = datetime.now()
    with SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel)
            .filter(
                ChildAssignmentModel.child_terminal_id == child_terminal_id,
                ChildAssignmentModel.retirement_claim_token == claim_token,
                ChildAssignmentModel.retirement_cleanup_intent == encoded_intent,
                ChildAssignmentModel.retirement_cleanup_completed_at.is_(None),
            )
            .first()
        )
        if assignment is None:
            return False
        delegation_kind = "handoff" if assignment.status.startswith("handoff_") else "assign"
        if expected_delegation_kind is not None and delegation_kind != expected_delegation_kind:
            return False
        terminal = db.query(TerminalModel).filter_by(id=child_terminal_id).first()
        if terminal is None:
            return False
        if retiring_supervisor_terminal_id is not None and (
            assignment.parent_terminal_id == retiring_supervisor_terminal_id
            or _historical_assigned_retirement_error(
                db, retiring_supervisor_terminal_id, assignment, terminal
            )
            is not None
        ):
            return False
        if delegation_kind == "handoff":
            eligible, _error = _handoff_retirement_eligible(db, assignment, terminal)
            if not eligible or cleanup_intent.get("managed") is not True:
                return False
        elif (
            assignment.status != ChildAssignmentStatus.RESULT_ACKNOWLEDGED.value
            or _completed_retirement_result(db, assignment, "assign") is None
        ):
            return False
        completed = (
            db.query(ChildAssignmentModel)
            .filter(
                ChildAssignmentModel.id == assignment.id,
                ChildAssignmentModel.child_terminal_id == child_terminal_id,
                ChildAssignmentModel.retirement_claim_token == claim_token,
                ChildAssignmentModel.retirement_cleanup_intent == encoded_intent,
                ChildAssignmentModel.retirement_cleanup_completed_at.is_(None),
                ChildAssignmentModel.status == assignment.status,
            )
            .update(
                {
                    ChildAssignmentModel.retirement_claim_token: None,
                    ChildAssignmentModel.retirement_cleanup_completed_at: now,
                    ChildAssignmentModel.retirement_completed_at: now,
                    ChildAssignmentModel.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        if completed:
            db.commit()
        return completed == 1


def complete_assigned_child_retirement(
    child_terminal_id: str,
    claim_token: str,
    cleanup_intent: Mapping[str, Any],
    *,
    retiring_supervisor_terminal_id: Optional[str] = None,
) -> bool:
    """Compatibility wrapper retaining assigned-only public semantics."""
    return complete_child_retirement(
        child_terminal_id,
        claim_token,
        cleanup_intent,
        "assign",
        retiring_supervisor_terminal_id,
    )


class HandoffResultSubmissionError(ValueError):
    """A stable, non-secret reason suitable for the hidden loopback API."""

    def __init__(self, status_code: int, code: str):
        super().__init__(code)
        self.status_code = status_code
        self.code = code


def submit_handoff_result_v1(
    auth_token: str, logical_turn_id: int, document: HandoffResultDocumentV1
) -> Dict[str, Any]:
    """Atomically finalize one authenticated strict V1 managed handoff."""
    token_digest = hashlib.sha256(auth_token.encode("utf-8", "strict")).hexdigest()
    document_bytes = canonical_handoff_result_v1_bytes(document)
    problem = managed_final_problem(document.body_markdown)
    if problem is not None:
        raise HandoffResultSubmissionError(422, problem.lower())
    content_sha256 = hashlib.sha256(document_bytes).hexdigest()
    document_json = document_bytes.decode("utf-8")

    # The bearer capability is the only caller-provided identity.  The server
    # resolves a unique managed terminal from its digest; a terminal header or
    # result identifier would let a caller select a relation it does not own.
    for attempt in range(3):
        try:
            return _submit_handoff_result_v1_once(
                token_digest,
                logical_turn_id,
                document_json,
                content_sha256,
                len(document_bytes),
            )
        except IntegrityError:
            return _resolve_handoff_submission_race(token_digest, logical_turn_id, content_sha256)
        except OperationalError as exc:
            # SQLite reports a transient writer collision as OperationalError.
            # Retry the entire transaction so the unique staged row remains the
            # cross-process idempotency authority rather than exposing a 500.
            if "locked" not in str(exc).lower() or attempt == 2:
                raise HandoffResultSubmissionError(409, "submission_indeterminate") from exc
            time.sleep(0.01 * (attempt + 1))

    raise AssertionError("unreachable")


def _submission_response(
    result_id: str, content_sha256: str, duplicate: bool, result_status: str = "complete"
) -> Dict[str, Any]:
    return {
        "accepted": True,
        "duplicate": duplicate,
        "result_id": result_id,
        "result_status": result_status,
        "submission_status": "finalized",
        "schema_version": 1,
        "content_sha256": content_sha256,
    }


def _terminal_for_handoff_submission(db: Any, token_digest: str) -> TerminalModel:
    terminals = (
        db.query(TerminalModel)
        .filter(TerminalModel.auth_token_sha256 == token_digest)
        .limit(2)
        .all()
    )
    if len(terminals) != 1 or not hmac.compare_digest(
        cast(str, terminals[0].auth_token_sha256), token_digest
    ):
        raise HandoffResultSubmissionError(401, "invalid_terminal_auth")
    return cast(TerminalModel, terminals[0])


def _submission_relation(
    db: Any, child_terminal_id: str, logical_turn_id: int
) -> tuple[ChildAssignmentModel, WorkflowModel, DelegationResultModel]:
    assignment = (
        db.query(ChildAssignmentModel).filter_by(child_terminal_id=child_terminal_id).first()
    )
    if assignment is None or not assignment.status.startswith("handoff_"):
        raise HandoffResultSubmissionError(409, "not_handoff_child")
    if assignment.status not in (
        ChildAssignmentStatus.HANDOFF_AWAITING_RESULT.value,
        ChildAssignmentStatus.HANDOFF_RECOVERY_AWAITING_RESULT.value,
    ):
        raise HandoffResultSubmissionError(409, "handoff_not_awaiting")

    parent_workflow = _open_workflow(db, assignment.parent_terminal_id, create=False)
    child_workflow = _open_workflow(db, child_terminal_id, create=False)
    if parent_workflow is None or parent_workflow.status != WORKFLOW_OPEN:
        raise HandoffResultSubmissionError(409, "parent_workflow_closed")
    if (
        child_workflow is None
        or child_workflow.status != WORKFLOW_OPEN
        or child_workflow.active_turn_id != logical_turn_id
        or db.query(WorkflowTurnReceiptModel)
        .filter_by(workflow_turn_id=logical_turn_id, receiver_terminal_id=child_terminal_id)
        .first()
        is None
    ):
        raise HandoffResultSubmissionError(409, "turn_not_admitted")

    result = (
        db.query(DelegationResultModel)
        .filter_by(child_assignment_id=assignment.id, delegation_kind="handoff")
        .first()
    )
    if result is None or result.status != DelegationResultStatus.AWAITING.value:
        raise HandoffResultSubmissionError(409, "result_not_awaiting")
    return assignment, cast(WorkflowModel, child_workflow), cast(DelegationResultModel, result)


def _raise_submission_conflict(
    db: Any,
    result_id: str,
    child_terminal_id: str,
    logical_turn_id: int,
    content_sha256: str,
) -> None:
    _record_result_event(
        db,
        result_id,
        f"submission-conflict:{result_id}:{content_sha256}",
        "submission_conflict",
        "child_mcp_submission",
        child_terminal_id,
        logical_turn_id,
        {"content_sha256": content_sha256},
    )
    try:
        db.commit()
    except IntegrityError:
        # Another conflicting retry recorded the same immutable event first.
        db.rollback()
    raise HandoffResultSubmissionError(409, "submission_conflict")


def _submit_handoff_result_v1_once(
    token_digest: str,
    logical_turn_id: int,
    document_json: str,
    content_sha256: str,
    content_bytes: int,
) -> Dict[str, Any]:
    _ensure_child_assignment_schema()
    _ensure_delegation_result_schema()
    _ensure_workflow_schema()
    with SessionLocal() as db:
        terminal = _terminal_for_handoff_submission(db, token_digest)
        child_terminal_id = cast(str, terminal.id)
        existing_assignment = (
            db.query(ChildAssignmentModel).filter_by(child_terminal_id=child_terminal_id).first()
        )
        if existing_assignment is not None:
            existing_result = (
                db.query(DelegationResultModel)
                .filter_by(child_assignment_id=existing_assignment.id, delegation_kind="handoff")
                .first()
            )
            existing_submission = (
                _staged_handoff_submission(db, existing_result.id)
                if existing_result is not None
                else None
            )
            if (
                existing_result is not None
                and existing_submission is not None
                and existing_result.status == DelegationResultStatus.COMPLETE.value
            ):
                if hmac.compare_digest(
                    cast(str, existing_submission.content_sha256), content_sha256
                ):
                    response = _submission_response(existing_result.id, content_sha256, True)
                    response["finalized"] = True
                    return response
                _raise_submission_conflict(
                    db, existing_result.id, child_terminal_id, logical_turn_id, content_sha256
                )
        assignment, child_workflow, result = _submission_relation(
            db, child_terminal_id, logical_turn_id
        )

        existing = db.query(DelegationResultSubmissionModel).filter_by(result_id=result.id).first()
        if existing is not None:
            if hmac.compare_digest(cast(str, existing.content_sha256), content_sha256):
                return _submission_response(result.id, cast(str, existing.content_sha256), True)
            _raise_submission_conflict(
                db, result.id, child_terminal_id, logical_turn_id, content_sha256
            )

        effect_key = f"submit-handoff-result-v1:{result.id}"
        existing_effect = (
            db.query(WorkflowEffectModel)
            .filter_by(
                workflow_id=child_workflow.id,
                workflow_turn_id=logical_turn_id,
                effect_kind="submit_handoff_result_v1",
                effect_key=effect_key,
            )
            .first()
        )
        if existing_effect is not None:
            # A concurrent transaction can commit between the stage lookup
            # and this effect lookup. Re-read from a fresh transaction before
            # classifying a genuinely unpaired legacy effect as indeterminate.
            db.rollback()
            return _resolve_handoff_submission_race(token_digest, logical_turn_id, content_sha256)
        effect = WorkflowEffectModel(
            workflow_id=child_workflow.id,
            workflow_turn_id=logical_turn_id,
            effect_kind="submit_handoff_result_v1",
            effect_key=effect_key,
            state="completed",
            claim_token=uuid.uuid4().hex,
        )
        db.add(effect)
        db.flush()
        submission = DelegationResultSubmissionModel(
            result_id=result.id,
            child_terminal_id=child_terminal_id,
            workflow_turn_id=logical_turn_id,
            workflow_effect_id=effect.id,
            schema_version=1,
            document_json=document_json,
            content_sha256=content_sha256,
            content_bytes=content_bytes,
        )
        db.add(submission)
        # SessionLocal intentionally disables autoflush in production.  The
        # common finalizer below re-reads this staged row to select the
        # authoritative structured branch; make this write visible before
        # that query so it can never fall through to legacy terminal capture.
        db.flush()
        _record_result_event(
            db,
            result.id,
            f"submission-recorded:{result.id}",
            "submission_recorded",
            "child_mcp_submission",
            child_terminal_id,
            logical_turn_id,
            {"content_sha256": content_sha256, "content_bytes": content_bytes},
        )
        # The authenticated V1 submit is the authoritative managed completion
        # boundary.  Do not wait for provider-idle observation (or a second
        # child model turn) before making result, child workflow and parent
        # delivery mutually consistent.
        _inbox, duplicate, reason = _finalize_managed_delegation_result(
            db,
            assignment,
            HandoffResultDocumentV1.model_validate_json(document_json).body_markdown,
            "child_structured_submission",
            workflow_turn_id=logical_turn_id,
            workflow_effect_id=effect.id,
        )
        if reason not in {"ACCEPTED", "PARENT_NOT_ELIGIBLE"}:
            raise HandoffResultSubmissionError(409, reason.lower())
        db.commit()
        response = _submission_response(result.id, content_sha256, duplicate)
        response["finalized"] = True
        response["parent_delivery"] = "queued" if reason == "ACCEPTED" else "suppressed"
        return response


def _resolve_handoff_submission_race(
    token_digest: str, logical_turn_id: int, content_sha256: str
) -> Dict[str, Any]:
    """Resolve a unique-constraint race from the durable staged row."""
    with SessionLocal() as db:
        terminal = _terminal_for_handoff_submission(db, token_digest)
        child_terminal_id = cast(str, terminal.id)
        assignment = (
            db.query(ChildAssignmentModel).filter_by(child_terminal_id=child_terminal_id).first()
        )
        result = (
            db.query(DelegationResultModel).filter_by(child_assignment_id=assignment.id).first()
            if assignment is not None
            else None
        )
        if result is None:
            raise HandoffResultSubmissionError(409, "submission_indeterminate")
        staged = _staged_handoff_submission(db, result.id)
        if staged is None:
            raise HandoffResultSubmissionError(409, "submission_effect_indeterminate")
        if hmac.compare_digest(cast(str, staged.content_sha256), content_sha256):
            return _submission_response(
                result.id, cast(str, staged.content_sha256), True, result.status
            )
        _raise_submission_conflict(
            db, result.id, child_terminal_id, logical_turn_id, content_sha256
        )
    raise AssertionError("unreachable")


def _staged_handoff_submission(
    db: Any, result_id: str
) -> Optional[DelegationResultSubmissionModel]:
    return cast(
        Optional[DelegationResultSubmissionModel],
        db.query(DelegationResultSubmissionModel).filter_by(result_id=result_id).first(),
    )


def _purge_staged_handoff_submission(db: Any, result_id: str) -> None:
    """Delete a staging row only in the transaction ending its result path."""
    db.query(DelegationResultSubmissionModel).filter_by(result_id=result_id).delete(
        synchronize_session=False
    )


def _purge_staged_handoff_submissions_for_terminals(db: Any, terminal_ids: List[str]) -> None:
    if terminal_ids:
        result_ids = db.query(DelegationResultModel.id).filter(
            (DelegationResultModel.child_terminal_id.in_(terminal_ids))
            | (DelegationResultModel.parent_terminal_id.in_(terminal_ids))
        )
        db.query(DelegationResultSubmissionModel).filter(
            DelegationResultSubmissionModel.result_id.in_(result_ids)
        ).delete(synchronize_session=False)


def _finalize_result(
    db: Any,
    assignment: ChildAssignmentModel,
    body: str,
    authorship: str,
    workflow_turn_id: Optional[int] = None,
    workflow_effect_id: Optional[int] = None,
    reason_code: Optional[str] = None,
) -> DelegationResultModel:
    kind = "handoff" if assignment.status.startswith("handoff_") else "assign"
    workflow = _open_workflow(db, assignment.parent_terminal_id, create=False)
    result = _create_result_for_assignment(db, assignment, kind, workflow)
    staged = _staged_handoff_submission(db, result.id) if kind == "handoff" else None
    if staged is not None and result.status == DelegationResultStatus.AWAITING.value:
        # The child-authenticated document is the authority once the existing
        # F11 stable-completion gate has reached this finalizer.  Never derive
        # it from a possibly wrapped/replaced terminal capture.
        result.status = DelegationResultStatus.COMPLETE.value
        result.authorship = "child_structured_submission"
        result.reason_code = reason_code
        result.document_json = staged.document_json
        result.content_sha256 = staged.content_sha256
        result.content_bytes = staged.content_bytes
        result.capture_sha256 = hashlib.sha256(body.encode()).hexdigest()
        result.capture_bytes = len(body.encode())
        result.workflow_turn_id = staged.workflow_turn_id
        result.workflow_effect_id = staged.workflow_effect_id
        result.finalized_at = datetime.now()
        result.updated_at = datetime.now()
        _record_result_event(
            db,
            result.id,
            f"result-complete:submission:{result.id}:{staged.content_sha256}",
            "completed",
            "child_structured_submission",
            assignment.child_terminal_id,
            staged.workflow_turn_id,
            {"content_sha256": staged.content_sha256, "content_bytes": staged.content_bytes},
        )
        # Keep this immutable idempotency record until normal result retention.
        return result
    digest = hashlib.sha256(body.encode()).hexdigest()
    if result.status == DelegationResultStatus.COMPLETE.value:
        if result.content_sha256 != digest:
            _record_result_event(
                db,
                result.id,
                f"submission-conflict:{assignment.id}:{digest}",
                "submission_conflict",
                "cao_system",
                assignment.child_terminal_id,
                workflow_turn_id,
            )
        return result
    if result.status != DelegationResultStatus.AWAITING.value:
        return result
    result.status = DelegationResultStatus.COMPLETE.value
    result.authorship = authorship
    result.reason_code = reason_code
    result.document_json = _result_document(body)
    result.content_sha256 = digest
    result.content_bytes = len(body.encode())
    result.capture_sha256 = digest if kind == "handoff" else None
    result.capture_bytes = len(body.encode()) if kind == "handoff" else None
    result.workflow_turn_id = workflow_turn_id or (workflow.active_turn_id if workflow else None)
    result.workflow_effect_id = workflow_effect_id
    result.finalized_at = datetime.now()
    result.updated_at = datetime.now()
    _record_result_event(
        db,
        result.id,
        f"result-complete:{assignment.id}:{digest}",
        "completed",
        authorship,
        assignment.child_terminal_id,
        workflow_turn_id,
    )
    return result


def _terminalize_child_after_authoritative_result(
    db: Any, child_terminal_id: str, reason: str
) -> bool:
    """Make durable child completion part of result finalization.

    A delegated child no longer needs a second provider turn merely to call
    ``complete_workflow`` after it has already authenticated a final result.
    This is intentionally database-only; process exit remains a separate,
    observable resource operation.
    """
    workflow = _open_workflow(db, child_terminal_id, create=False)
    # Historical/unmanaged relations created before workflow tracking do not
    # acquire a new terminal state retroactively. Managed children always
    # have one and are terminalized below.
    if workflow is None:
        return True
    if workflow.status == WORKFLOW_TERMINAL:
        return True
    if workflow.status != WORKFLOW_OPEN:
        return False
    workflow.status = WORKFLOW_TERMINAL
    workflow.terminal_reason = reason
    workflow.updated_at = datetime.now()
    (
        db.query(WorkflowTurnModel)
        .filter(
            WorkflowTurnModel.workflow_id == workflow.id,
            WorkflowTurnModel.state.in_((TURN_QUEUED, TURN_CLAIMED)),
        )
        .update(
            {
                WorkflowTurnModel.state: TURN_CANCELLED,
                WorkflowTurnModel.claim_token: None,
                WorkflowTurnModel.claim_expires_at: None,
            },
            synchronize_session=False,
        )
    )
    return True


def _handoff_requires_structured_result(db: Any, assignment: ChildAssignmentModel) -> bool:
    """Whether this handoff has the injected authenticated V1 capability."""
    terminal = db.query(TerminalModel).filter_by(id=assignment.child_terminal_id).first()
    return bool(terminal and terminal.auth_token_sha256)


_MANAGED_PROGRESS_ONLY = re.compile(
    # F11's terminal-capture guard treats live Codex spinner chrome as
    # non-final.  The managed callback boundary must do the same.
    r"^\s*(?:[•*\-⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏◐◑◒◓◔◕◖◗]\s*)?(?:working|thinking|processing|starting|running|waiting|"
    r"i(?:'m| am|'ll| will) (?:about to |going to )?(?:run|start|check|work)|"
    r"let me check|tool(?:\s+call)?\s+(?:progress|pending)|please wait|›|❯)(?:\W.*)?$",
    re.IGNORECASE,
)
_MAX_HANDOFF_EXIT_RECOVERY_CYCLES = 2


def _handoff_recovery_count(db: Any, result_id: str) -> int:
    """Count every bounded same-child recovery path against one budget."""
    return cast(
        int,
        db.query(DelegationResultEventModel)
        .filter(
            DelegationResultEventModel.result_id == result_id,
            DelegationResultEventModel.event_type.in_(
                ("handoff-provider-exit-recovery", "handoff-continuation-recovery")
            ),
        )
        .count(),
    )


def _terminalize_handoff_recovery_exhausted(
    db: Any,
    assignment: ChildAssignmentModel,
    result: DelegationResultModel,
    terminal_id: str,
) -> None:
    """Terminalize one exhausted managed handoff without changing its identity."""
    assignment.status = ChildAssignmentStatus.HANDOFF_RESULT_FAILED.value
    assignment.updated_at = datetime.now()
    if result.status != DelegationResultStatus.AWAITING.value:
        return
    result.status = DelegationResultStatus.INCOMPLETE.value
    result.reason_code = "handoff_recovery_exhausted"
    blocker = (
        "status: blocked\nclassification: RECOVERABLE_EXECUTION\n"
        "reason_code: handoff_recovery_exhausted\n"
        "evidence: managed provider exited before authoritative result finalization"
    )
    result.document_json = _legacy_document(blocker)
    result.content_sha256 = hashlib.sha256(blocker.encode()).hexdigest()
    result.content_bytes = len(blocker.encode())
    result.finalized_at = result.updated_at = datetime.now()
    _record_result_event(
        db,
        result.id,
        f"handoff-recovery-exhausted:{assignment.id}",
        "incomplete",
        "cao_lifecycle",
        terminal_id,
        detail={"reason_code": "handoff_recovery_exhausted"},
    )


def managed_final_problem(body: object) -> Optional[str]:
    """Reject text which cannot be a delegated work result."""
    if not isinstance(body, str) or not body.strip():
        return "EMPTY_FINAL"
    # Validate the actual agent content: delivery suffixes are transport data
    # and must never make progress narration look like a result.
    content = "\n".join(
        line
        for line in body.splitlines()
        if not line.strip().startswith(("[Message from terminal", "[Assigned by terminal"))
        and line.strip() != "NO_TG_NOTIFY"
    )
    normalized = re.sub(
        r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\\\)|[()][0-2AB])",
        "",
        content,
    )
    normalized = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", normalized).strip()
    if not normalized or _MANAGED_PROGRESS_ONLY.fullmatch(normalized):
        return "NON_SUBSTANTIVE_FINAL"
    if normalized.endswith(("...", "…")):
        return "INTERRUPTED_FINAL"
    return None


def _finalize_managed_delegation_result(
    db: Any,
    assignment: ChildAssignmentModel,
    body: str,
    authorship: str,
    *,
    workflow_turn_id: Optional[int] = None,
    workflow_effect_id: Optional[int] = None,
) -> tuple[Optional[InboxMessage], bool, str]:
    """The single durable result-to-child-to-parent finalization path.

    It creates at most one immutable result, one delivery notice and one
    parent continuation.  The child workflow becomes terminal in the same
    transaction as the accepted result, fixing the former result/retirement
    split-brain state.
    """
    problem = managed_final_problem(body)
    if problem is not None:
        return None, False, problem
    kind = "handoff" if assignment.status.startswith("handoff_") else "assign"
    if assignment.result_message_id is not None:
        inbox = db.query(InboxModel).filter_by(id=assignment.result_message_id).first()
        # Repair the historical crash window instead of treating delivery as
        # sufficient evidence that the child workflow was terminalized.
        result = (
            db.query(DelegationResultModel).filter_by(child_assignment_id=assignment.id).first()
        )
        if result is not None and result.status == DelegationResultStatus.COMPLETE.value:
            _terminalize_child_after_authoritative_result(
                db, assignment.child_terminal_id, "repaired authoritative delegated result"
            )
        return (
            _inbox_model_to_message(inbox) if inbox is not None else None,
            True,
            "DUPLICATE_EFFECT",
        )

    result = _finalize_result(
        db,
        assignment,
        body,
        authorship,
        workflow_turn_id=workflow_turn_id,
        workflow_effect_id=workflow_effect_id,
    )
    document = json.loads(result.document_json) if result.document_json else {}
    result_body = document.get("body_markdown") if isinstance(document, dict) else None
    if result.status != DelegationResultStatus.COMPLETE.value or not isinstance(result_body, str):
        return None, False, "RESULT_NOT_COMPLETE"
    if not _terminalize_child_after_authoritative_result(
        db, assignment.child_terminal_id, "authoritative delegated result accepted"
    ):
        return None, False, "CHILD_WORKFLOW_NOT_ELIGIBLE"

    parent_workflow = _open_workflow(db, assignment.parent_terminal_id, create=False)
    if parent_workflow is None or parent_workflow.status != WORKFLOW_OPEN:
        # Retain the accepted child artifact and its terminal state, but never
        # resurrect a parent that an owner/cancellation transition fenced.
        return None, False, "PARENT_NOT_ELIGIBLE"

    inbox = InboxModel(
        sender_id=assignment.child_terminal_id,
        receiver_id=assignment.parent_terminal_id,
        message=result_body,
        status=MessageStatus.PENDING.value,
        result_id=result.id,
        kind="delegation_result_notice",
        superseded_at=datetime.now(),
    )
    db.add(inbox)
    db.flush()
    assignment.result_message_id = inbox.id
    assignment.status = (
        ChildAssignmentStatus.HANDOFF_RESULT_QUEUED.value
        if kind == "handoff"
        else ChildAssignmentStatus.RESULT_QUEUED.value
    )
    assignment.updated_at = datetime.now()
    # Results that become ready during one parent turn share its one safe
    # boundary callback. A result that arrives while another callback is
    # unadmitted must not mint a successor yet: retain its immutable Inbox
    # notice unbound until the active receiver admission clears that fence.
    turn = None
    boundary_key: Optional[str] = None
    defer_handoff_turn = kind == "handoff" and _workflow_has_unadmitted_active_continuation(
        db, parent_workflow
    )
    if kind == "handoff" and not defer_handoff_turn:
        boundary_key = f"handoff-result-boundary:{parent_workflow.active_turn_id}"
        turn = (
            db.query(WorkflowTurnModel)
            .filter(
                WorkflowTurnModel.workflow_id == parent_workflow.id,
                WorkflowTurnModel.kind == "handoff_result",
                WorkflowTurnModel.dedupe_key == boundary_key,
                WorkflowTurnModel.state == TURN_QUEUED,
            )
            .order_by(WorkflowTurnModel.id.asc())
            .first()
        )
    if turn is None and not defer_handoff_turn:
        turn = WorkflowTurnModel(
            workflow_id=parent_workflow.id,
            kind="handoff_result" if kind == "handoff" else "assigned_result",
            dedupe_key=(
                cast(str, boundary_key)
                if kind == "handoff"
                else f"assigned-result:{assignment.child_terminal_id}"
            ),
            payload=result_body,
            inbox_message_id=inbox.id,
            state=TURN_QUEUED,
        )
        db.add(turn)
        db.flush()
    if turn is not None:
        result.workflow_turn_id = turn.id
    elif defer_handoff_turn:
        # _finalize_result records the parent active turn for provenance by
        # default. A deferred callback must not retain that value as delivery
        # membership: it is intentionally turn-less until materialization.
        result.workflow_turn_id = None
    parent_workflow.no_progress_count = 0
    parent_workflow.updated_at = datetime.now()
    return _inbox_model_to_message(inbox), False, "ACCEPTED"


def finalize_delegation_result_incomplete(
    child_terminal_id: str, reason_code: str, partial: str = ""
) -> bool:
    """System/lifecycle-only terminalization of an awaiting result."""
    _ensure_delegation_result_schema()
    with SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel).filter_by(child_terminal_id=child_terminal_id).first()
        )
        if not assignment:
            return False
        kind = "handoff" if assignment.status.startswith("handoff_") else "assign"
        result = _create_result_for_assignment(
            db, assignment, kind, _open_workflow(db, assignment.parent_terminal_id, create=False)
        )
        if result.status != DelegationResultStatus.AWAITING.value:
            return result.status == DelegationResultStatus.INCOMPLETE.value
        result.status, result.reason_code = DelegationResultStatus.INCOMPLETE.value, reason_code
        if partial:
            result.document_json = _legacy_document(partial)
            result.content_sha256 = hashlib.sha256(partial.encode()).hexdigest()
            result.content_bytes = len(partial.encode())
        result.finalized_at = result.updated_at = datetime.now()
        _record_result_event(
            db,
            result.id,
            f"result-incomplete:{assignment.id}:{reason_code}",
            "incomplete",
            "cao_lifecycle",
            child_terminal_id,
            detail={"reason_code": reason_code},
        )
        db.commit()
        return True


def terminal_requires_result_snapshot(terminal_id: str) -> bool:
    """Return whether destroying this child could lose an awaiting artifact."""
    _ensure_child_assignment_schema()
    with SessionLocal() as db:
        return bool(
            db.query(ChildAssignmentModel)
            .filter(
                ChildAssignmentModel.child_terminal_id == terminal_id,
                ChildAssignmentModel.status.in_(
                    (
                        ChildAssignmentStatus.AWAITING_RESULT.value,
                        ChildAssignmentStatus.RESULT_QUEUED.value,
                        ChildAssignmentStatus.RESULT_DELIVERED.value,
                        ChildAssignmentStatus.RESULT_FAILED.value,
                        ChildAssignmentStatus.HANDOFF_AWAITING_RESULT.value,
                        ChildAssignmentStatus.HANDOFF_RECOVERY_AWAITING_RESULT.value,
                    )
                ),
            )
            .first()
        )


def persist_terminal_result_snapshot(terminal_id: str, partial: str) -> bool:
    """Terminalize active child relations before a destructive lifecycle action.

    The caller captures ``partial`` before signalling/killing the terminal and
    must abort destruction when this transaction cannot commit.  Completed
    handoff captures are deliberately left untouched.
    """
    _ensure_child_assignment_schema()
    _ensure_delegation_result_schema()
    with SessionLocal() as db:
        assignments = (
            db.query(ChildAssignmentModel)
            .filter(
                ChildAssignmentModel.child_terminal_id == terminal_id,
                ChildAssignmentModel.status.in_(
                    (
                        ChildAssignmentStatus.AWAITING_RESULT.value,
                        ChildAssignmentStatus.RESULT_QUEUED.value,
                        ChildAssignmentStatus.RESULT_DELIVERED.value,
                        ChildAssignmentStatus.RESULT_FAILED.value,
                        ChildAssignmentStatus.HANDOFF_AWAITING_RESULT.value,
                        ChildAssignmentStatus.HANDOFF_RECOVERY_AWAITING_RESULT.value,
                    )
                ),
            )
            .all()
        )
        now = datetime.now()
        for assignment in assignments:
            kind = "handoff" if assignment.status.startswith("handoff_") else "assign"
            result = _create_result_for_assignment(
                db,
                assignment,
                kind,
                _open_workflow(db, assignment.parent_terminal_id, create=False),
            )
            if result.status == DelegationResultStatus.AWAITING.value:
                result.status = DelegationResultStatus.INCOMPLETE.value
                result.reason_code = "child_destroyed_after_snapshot"
                if partial:
                    result.document_json = _legacy_document(partial)
                    result.content_sha256 = hashlib.sha256(partial.encode()).hexdigest()
                    result.content_bytes = len(partial.encode())
                result.finalized_at = result.updated_at = now
                _record_result_event(
                    db,
                    result.id,
                    f"result-destruction-snapshot:{assignment.id}",
                    "incomplete",
                    "cao_lifecycle",
                    terminal_id,
                    detail={"reason_code": result.reason_code},
                )
                _purge_staged_handoff_submission(db, result.id)
            assignment.status = ChildAssignmentStatus.CANCELLED.value
            assignment.updated_at = now
        if assignments:
            db.commit()
        return True


def purge_expired_delegation_results(cutoff: datetime) -> int:
    """Relational TTL GC that never removes an active barrier or pending notice."""
    _ensure_delegation_result_schema()
    removable_assignment_statuses = (
        ChildAssignmentStatus.RESULT_ACKNOWLEDGED.value,
        ChildAssignmentStatus.HANDOFF_RESULT_ACKNOWLEDGED.value,
        ChildAssignmentStatus.CANCELLED.value,
    )
    with SessionLocal() as db:
        rows = (
            db.query(DelegationResultModel)
            .filter(
                DelegationResultModel.finalized_at.isnot(None),
                DelegationResultModel.finalized_at < cutoff,
            )
            .all()
        )
        removed = 0
        for result in rows:
            assignment = (
                db.query(ChildAssignmentModel).filter_by(id=result.child_assignment_id).first()
            )
            if assignment is None or assignment.status not in removable_assignment_statuses:
                continue
            workflow = (
                db.query(WorkflowModel).filter_by(id=result.parent_workflow_id).first()
                if result.parent_workflow_id is not None
                else _open_workflow(db, assignment.parent_terminal_id, create=False)
            )
            if workflow is not None and workflow.status == WORKFLOW_OPEN:
                continue
            notice = db.query(InboxModel).filter_by(result_id=result.id).first()
            if notice is not None and notice.status in (
                MessageStatus.PENDING.value,
                MessageStatus.FAILED.value,
            ):
                continue
            if notice is not None and (
                db.query(WorkflowTurnModel)
                .filter(WorkflowTurnModel.inbox_message_id == notice.id)
                .first()
                is not None
            ):
                # Retaining a little more history is safe; deleting the
                # notice while its workflow turn remains would corrupt the
                # durable continuation graph.
                continue
            if notice is not None:
                db.delete(notice)
            db.query(DelegationResultEventModel).filter_by(result_id=result.id).delete()
            _purge_staged_handoff_submission(db, result.id)
            db.delete(result)
            db.delete(assignment)
            removed += 1
        if removed:
            db.commit()
        return removed


def register_child_assignment(parent_terminal_id: str, child_terminal_id: str) -> bool:
    """Persist a parent's expectation of one callback from an assigned child.

    Registration is idempotent for the same parent, but a child cannot be
    silently adopted by a second parent.
    """
    _ensure_child_assignment_schema()
    _ensure_delegation_result_schema()
    _ensure_workflow_schema()
    with SessionLocal() as db:
        if not _retirement_quiescence_allows_commit(db, parent_terminal_id):
            return False
        workflow = _open_workflow(db, parent_terminal_id, create=True)
        assert workflow is not None
        if workflow.status != WORKFLOW_OPEN:
            return False
        assignment = (
            db.query(ChildAssignmentModel)
            .filter(ChildAssignmentModel.child_terminal_id == child_terminal_id)
            .first()
        )
        if assignment:
            if assignment.parent_terminal_id != parent_terminal_id:
                raise ValueError(
                    f"Child terminal {child_terminal_id} already belongs to another parent"
                )
            return False
        assignment = ChildAssignmentModel(
            parent_terminal_id=parent_terminal_id,
            child_terminal_id=child_terminal_id,
            status=ChildAssignmentStatus.AWAITING_RESULT.value,
        )
        db.add(assignment)
        db.flush()
        _create_result_for_assignment(db, assignment, "assign", workflow)
        if not _retirement_quiescence_allows_commit(db, parent_terminal_id):
            db.rollback()
            return False
        db.commit()
        return True


def register_handoff_child(parent_terminal_id: str, child_terminal_id: str) -> bool:
    """Persist a blocking handoff before its task can produce a result.

    The distinct initial state needs no schema migration and lets restart
    reconciliation resume only ordinary handoffs, not callback-driven assigns.
    """
    _ensure_child_assignment_schema()
    _ensure_delegation_result_schema()
    _ensure_workflow_schema()
    with SessionLocal() as db:
        if not _retirement_quiescence_allows_commit(db, parent_terminal_id):
            return False
        workflow = _open_workflow(db, parent_terminal_id, create=True)
        assert workflow is not None
        if workflow.status != WORKFLOW_OPEN:
            return False
        assignment = (
            db.query(ChildAssignmentModel)
            .filter(ChildAssignmentModel.child_terminal_id == child_terminal_id)
            .first()
        )
        if assignment:
            if assignment.parent_terminal_id != parent_terminal_id:
                raise ValueError(
                    f"Child terminal {child_terminal_id} already belongs to another parent"
                )
            return False
        assignment = ChildAssignmentModel(
            parent_terminal_id=parent_terminal_id,
            child_terminal_id=child_terminal_id,
            status=ChildAssignmentStatus.HANDOFF_AWAITING_RESULT.value,
        )
        db.add(assignment)
        db.flush()
        _create_result_for_assignment(db, assignment, "handoff", workflow)
        if not _retirement_quiescence_allows_commit(db, parent_terminal_id):
            db.rollback()
            return False
        db.commit()
        return True


def mark_handoff_child_input_received(child_terminal_id: str) -> bool:
    """Persist the one direct-handoff input boundary after terminal delivery.

    The marker intentionally stays on the parent/child relation after a
    restart.  It lets a rehydrated Codex provider treat a final tail with no
    visible user row as a completion candidate, while an unmarked ordinary
    idle handoff remains IDLE.
    """
    _ensure_child_assignment_schema()
    with SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel)
            .filter(ChildAssignmentModel.child_terminal_id == child_terminal_id)
            .first()
        )
        if (
            assignment is None
            or assignment.status == ChildAssignmentStatus.CANCELLED.value
            or not assignment.status.startswith("handoff_")
        ):
            return False
        if assignment.handoff_input_received:
            return True
        assignment.handoff_input_received = True
        assignment.updated_at = datetime.now()
        db.commit()
        return True


def handoff_child_input_received(child_terminal_id: str) -> bool:
    """Return whether this live/recovery direct handoff received CAO input.

    Rolling upgrades before the relation marker existed can recover only one
    exact legacy lifecycle snapshot. That fallback is read-only: it never
    backfills a result, creates a receipt, or repairs ambiguous workflow state.
    """
    _ensure_child_assignment_schema()
    with SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel)
            .filter(ChildAssignmentModel.child_terminal_id == child_terminal_id)
            .first()
        )
        if (
            assignment is None
            or assignment.status == ChildAssignmentStatus.CANCELLED.value
            or not assignment.status.startswith("handoff_")
        ):
            return False
        if assignment.handoff_input_received:
            return True
        if assignment.direct_result_output is not None or assignment.cleanup_acknowledged:
            return False

        snapshots = (
            db.query(DelegationResultModel)
            .filter(
                DelegationResultModel.child_assignment_id == assignment.id,
                DelegationResultModel.delegation_kind == "handoff",
                DelegationResultModel.parent_terminal_id == assignment.parent_terminal_id,
                DelegationResultModel.child_terminal_id == assignment.child_terminal_id,
                DelegationResultModel.authorship == "cao_lifecycle_snapshot",
                DelegationResultModel.status == DelegationResultStatus.AWAITING.value,
                DelegationResultModel.workflow_turn_id.is_(None),
            )
            .all()
        )
        if len(snapshots) != 1:
            return False
        snapshot = snapshots[0]
        if snapshot.parent_workflow_id is None:
            return False

        parent_workflows = (
            db.query(WorkflowModel)
            .filter(
                WorkflowModel.id == snapshot.parent_workflow_id,
                WorkflowModel.root_terminal_id == assignment.parent_terminal_id,
                WorkflowModel.status == WORKFLOW_OPEN,
            )
            .all()
        )
        if len(parent_workflows) != 1:
            return False

        child_turns = (
            db.query(WorkflowTurnModel)
            .join(WorkflowModel, WorkflowTurnModel.workflow_id == WorkflowModel.id)
            .filter(
                WorkflowModel.root_terminal_id == assignment.child_terminal_id,
                WorkflowModel.status == WORKFLOW_OPEN,
                WorkflowModel.active_turn_id == WorkflowTurnModel.id,
                WorkflowTurnModel.kind == "external_input",
                WorkflowTurnModel.transport_binding.is_not(None),
            )
            .all()
        )
        if len(child_turns) != 1:
            return False
        receipts = (
            db.query(WorkflowTurnReceiptModel)
            .filter(
                WorkflowTurnReceiptModel.workflow_turn_id == child_turns[0].id,
                WorkflowTurnReceiptModel.receiver_terminal_id == assignment.child_terminal_id,
            )
            .all()
        )
        return len(receipts) == 1


def create_child_assignment_result_message(
    sender_id: str,
    receiver_id: str,
    message: str,
    workflow_effect_id: Optional[int] = None,
    workflow_turn_id: Optional[int] = None,
) -> tuple[Optional[InboxMessage], bool]:
    """Persist an assigned child's first result in the normal inbox.

    Returns ``(message, duplicate)``. A duplicate maps to the first persisted
    result row; ``None, True`` is a callback to a closed relation without a
    deliverable result and is deliberately not sent to a detached parent.
    """
    _ensure_child_assignment_schema()
    _ensure_delegation_result_schema()
    _ensure_workflow_schema()
    with SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel)
            .filter(
                ChildAssignmentModel.parent_terminal_id == receiver_id,
                ChildAssignmentModel.child_terminal_id == sender_id,
            )
            .first()
        )
        if assignment is None:
            return None, False
        if assignment.status == ChildAssignmentStatus.CANCELLED.value:
            return None, True
        workflow = _open_workflow(db, receiver_id, create=False)
        if workflow is None or workflow.status != WORKFLOW_OPEN:
            # A terminal/owner/cancel transition fences all parent edges in
            # one transaction.  Keep this check as the late-callback guard
            # for relations written by older rolling-upgrade processes.
            return None, True
        # A public Inbox request is not an authority to complete an assigned
        # child result.  Only the child's currently claimed, admitted
        # send_message effect can cross this boundary.  The effect is checked
        # in the same transaction as finalization, so a caller cannot forge a
        # callback by merely naming a registered child terminal.
        effect = (
            db.query(WorkflowEffectModel)
            .join(WorkflowModel, WorkflowModel.id == WorkflowEffectModel.workflow_id)
            .filter(
                WorkflowEffectModel.id == workflow_effect_id,
                WorkflowEffectModel.effect_kind == "send_message",
                WorkflowEffectModel.state == "claimed",
                WorkflowEffectModel.workflow_turn_id == workflow_turn_id,
                WorkflowModel.root_terminal_id == sender_id,
                WorkflowModel.status == WORKFLOW_OPEN,
                WorkflowModel.active_turn_id == workflow_turn_id,
            )
            .first()
        )
        if effect is None:
            raise PermissionError(
                "assigned result requires the registered child's admitted send_message effect"
            )
        # An existing delivery may have survived the former split transaction
        # while its child workflow remained OPEN.  Enter the common finalizer
        # before declaring this effect a replay so it can repair that edge
        # without duplicating the result, notice, or wake.
        if assignment.result_message_id is not None:
            inbox_msg, duplicate, _reason = _finalize_managed_delegation_result(
                db,
                assignment,
                message,
                "child_submission",
                workflow_turn_id=workflow_turn_id,
                workflow_effect_id=effect.id,
            )
            db.commit()
            return inbox_msg, duplicate
        if assignment.status != ChildAssignmentStatus.AWAITING_RESULT.value:
            return None, True
        problem = managed_final_problem(message)
        if problem is not None:
            raise ValueError(problem)

        inbox_msg, duplicate, _reason = _finalize_managed_delegation_result(
            db,
            assignment,
            message,
            "child_submission",
            workflow_turn_id=workflow_turn_id,
            workflow_effect_id=effect.id,
        )
        db.commit()
        return inbox_msg, duplicate


def create_assigned_child_completion_result_message(
    child_terminal_id: str,
    message: str,
    workflow_effect_id: int,
    workflow_turn_id: int,
) -> tuple[Optional[InboxMessage], bool]:
    """Finalize an assigned child that explicitly terminalized its own workflow.

    ``complete_workflow`` is a terminal child-side effect, so it may carry the
    concise completion report when a worker does not separately call
    ``send_message``.  The same admitted effect that terminalizes the child is
    required here; this preserves the result/effect authority boundary while
    routing the parent through the ordinary durable Inbox callback.
    """
    _ensure_child_assignment_schema()
    _ensure_delegation_result_schema()
    _ensure_workflow_schema()
    with SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel)
            .filter(ChildAssignmentModel.child_terminal_id == child_terminal_id)
            .first()
        )
        if assignment is None or assignment.status == ChildAssignmentStatus.CANCELLED.value:
            return None, False
        parent_workflow = _open_workflow(db, assignment.parent_terminal_id, create=False)
        if parent_workflow is None or parent_workflow.status != WORKFLOW_OPEN:
            return None, True
        effect = (
            db.query(WorkflowEffectModel)
            .join(WorkflowModel, WorkflowModel.id == WorkflowEffectModel.workflow_id)
            .filter(
                WorkflowEffectModel.id == workflow_effect_id,
                WorkflowEffectModel.effect_kind == "complete_workflow",
                WorkflowEffectModel.state == "claimed",
                WorkflowEffectModel.workflow_turn_id == workflow_turn_id,
                WorkflowModel.root_terminal_id == child_terminal_id,
                WorkflowModel.status == WORKFLOW_OPEN,
                WorkflowModel.active_turn_id == workflow_turn_id,
            )
            .first()
        )
        if effect is None:
            raise PermissionError(
                "assigned completion requires the child's admitted complete_workflow effect"
            )

        if assignment.result_message_id is not None:
            inbox_msg, duplicate, reason = _finalize_managed_delegation_result(
                db,
                assignment,
                message,
                "child_workflow_completion",
                workflow_turn_id=workflow_turn_id,
                workflow_effect_id=effect.id,
            )
            if reason not in {"ACCEPTED", "DUPLICATE_EFFECT"}:
                raise ValueError(reason)
            db.commit()
            return inbox_msg, duplicate
        if assignment.status != ChildAssignmentStatus.AWAITING_RESULT.value:
            return None, True

        inbox_msg, duplicate, reason = _finalize_managed_delegation_result(
            db,
            assignment,
            message,
            "child_workflow_completion",
            workflow_turn_id=workflow_turn_id,
            workflow_effect_id=effect.id,
        )
        if reason not in {"ACCEPTED", "DUPLICATE_EFFECT"}:
            raise ValueError(reason)
        db.commit()
        return inbox_msg, duplicate


def mark_child_assignment_result_delivered(message_id: int) -> bool:
    """Record that the Inbox submitted a result; parent acknowledgement still blocks."""
    _ensure_child_assignment_schema()
    with SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel)
            .filter(ChildAssignmentModel.result_message_id == message_id)
            .first()
        )
        if assignment is None or assignment.status not in (
            ChildAssignmentStatus.RESULT_QUEUED.value,
            ChildAssignmentStatus.HANDOFF_RESULT_QUEUED.value,
        ):
            return False
        assignment.status = (
            ChildAssignmentStatus.HANDOFF_RESULT_DELIVERED.value
            if assignment.status == ChildAssignmentStatus.HANDOFF_RESULT_QUEUED.value
            else ChildAssignmentStatus.RESULT_DELIVERED.value
        )
        assignment.updated_at = datetime.now()
        db.commit()
        return True


def get_child_assignment_result_child_id(message_id: int) -> Optional[str]:
    """Return the durable child identity for a registered result row.

    Inbox normally delivers only user message text. Assigned callbacks need a
    stable identity in that delivery so the owning parent can acknowledge the
    relation even when optional sender-ID text injection is disabled.
    """
    _ensure_child_assignment_schema()
    with SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel)
            .filter(ChildAssignmentModel.result_message_id == message_id)
            .first()
        )
        if assignment is None or assignment.status == ChildAssignmentStatus.CANCELLED.value:
            return None
        return assignment.child_terminal_id


def get_child_assignment_result_id(message_id: int) -> Optional[str]:
    """Return the immutable artifact linked to one Inbox result notice."""
    _ensure_delegation_result_schema()
    with SessionLocal() as db:
        message = db.query(InboxModel).filter_by(id=message_id).first()
        return message.result_id if message and message.kind == "delegation_result_notice" else None


def mark_child_assignment_result_failed(message_id: int) -> bool:
    """Retain a delivery failure as a visible parent completion barrier."""
    _ensure_child_assignment_schema()
    with SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel)
            .filter(ChildAssignmentModel.result_message_id == message_id)
            .first()
        )
        if assignment is None or assignment.status not in (
            ChildAssignmentStatus.RESULT_QUEUED.value,
            ChildAssignmentStatus.HANDOFF_RESULT_QUEUED.value,
        ):
            return False
        assignment.status = (
            ChildAssignmentStatus.HANDOFF_RESULT_FAILED.value
            if assignment.status == ChildAssignmentStatus.HANDOFF_RESULT_QUEUED.value
            else ChildAssignmentStatus.RESULT_FAILED.value
        )
        assignment.updated_at = datetime.now()
        db.commit()
        return True


def get_parent_completion_barrier(parent_terminal_id: str) -> tuple[int, int]:
    """Return ``(active, failed)`` callbacks that still await parent acknowledgement."""
    _ensure_child_assignment_schema()
    with SessionLocal() as db:
        assignments = (
            db.query(ChildAssignmentModel)
            .filter(ChildAssignmentModel.parent_terminal_id == parent_terminal_id)
            .all()
        )
        active_statuses = _active_child_assignment_statuses()
        active = sum(assignment.status in active_statuses for assignment in assignments)
        failed = sum(
            assignment.status == ChildAssignmentStatus.RESULT_FAILED.value
            or assignment.status == ChildAssignmentStatus.HANDOFF_RESULT_FAILED.value
            for assignment in assignments
        )
        return active, failed


def acknowledge_child_assignment_result(
    parent_terminal_id: str,
    child_terminal_id: Optional[str] = None,
    result_id: Optional[str] = None,
) -> bool:
    """Durably acknowledge one delivered child result from its owning parent.

    The terminal that created the assignment is the only terminal allowed to
    release its completion barrier.  The operation is intentionally idempotent
    so an agent can retry after a crash between incorporating the result and
    observing this acknowledgement's response.
    """
    outcome = acknowledge_child_assignment_result_outcome(
        parent_terminal_id, child_terminal_id, result_id
    )
    # Historical callers use this as an idempotent barrier release: replaying
    # an acknowledgement is therefore still ``True``. MCP deliberately calls
    # the typed function below and retains RESULT_ALREADY_ACKNOWLEDGED.
    return bool(outcome["accepted"]) or outcome["reason_code"] == "RESULT_ALREADY_ACKNOWLEDGED"


def acknowledge_child_assignment_result_outcome(
    parent_terminal_id: str,
    child_terminal_id: Optional[str] = None,
    result_id: Optional[str] = None,
) -> Dict[str, Optional[str] | bool]:
    """Apply one acknowledgement and return its durable typed outcome.

    The legacy boolean helper above remains for in-process compatibility. MCP
    callers use this outcome so an idempotent replay cannot be mistaken for a
    newly accepted acknowledgement.
    """
    _ensure_child_assignment_schema()
    _ensure_delegation_result_schema()
    if not child_terminal_id and not result_id:
        return {
            "accepted": False,
            "reason_code": "WRONG_DISPATCH_MODE",
            "workflow_state": None,
            "assignment_status": None,
        }
    with SessionLocal() as db:
        parent_workflow = _open_workflow(db, parent_terminal_id, create=False)
        if parent_workflow is None or parent_workflow.status != WORKFLOW_OPEN:
            return {
                "accepted": False,
                "reason_code": "PARENT_NOT_ELIGIBLE",
                "workflow_state": parent_workflow.status if parent_workflow else None,
                "assignment_status": None,
            }
        query = db.query(ChildAssignmentModel).filter(
            ChildAssignmentModel.parent_terminal_id == parent_terminal_id
        )
        if result_id:
            query = query.join(
                DelegationResultModel,
                DelegationResultModel.child_assignment_id == ChildAssignmentModel.id,
            ).filter(DelegationResultModel.id == result_id)
        elif child_terminal_id:
            query = query.filter(ChildAssignmentModel.child_terminal_id == child_terminal_id)
        assignment = query.first()
        if assignment is None:
            return {
                "accepted": False,
                "reason_code": "WRONG_DISPATCH_MODE",
                "workflow_state": parent_workflow.status,
                "assignment_status": None,
            }
        if assignment.status in (
            ChildAssignmentStatus.RESULT_ACKNOWLEDGED.value,
            ChildAssignmentStatus.HANDOFF_RESULT_ACKNOWLEDGED.value,
        ):
            return {
                "accepted": False,
                "reason_code": "RESULT_ALREADY_ACKNOWLEDGED",
                "workflow_state": parent_workflow.status,
                "assignment_status": assignment.status,
            }
        if assignment.status in (
            ChildAssignmentStatus.RESULT_QUEUED.value,
            ChildAssignmentStatus.HANDOFF_RESULT_QUEUED.value,
        ):
            return {
                "accepted": False,
                "reason_code": "RESULT_NOT_DELIVERED",
                "workflow_state": parent_workflow.status,
                "assignment_status": assignment.status,
            }
        if assignment.status not in (
            ChildAssignmentStatus.RESULT_DELIVERED.value,
            ChildAssignmentStatus.HANDOFF_RESULT_DELIVERED.value,
        ):
            return {
                "accepted": False,
                "reason_code": "WRONG_DISPATCH_MODE",
                "workflow_state": parent_workflow.status,
                "assignment_status": assignment.status,
            }
        assignment.status = (
            ChildAssignmentStatus.HANDOFF_RESULT_ACKNOWLEDGED.value
            if assignment.status == ChildAssignmentStatus.HANDOFF_RESULT_DELIVERED.value
            else ChildAssignmentStatus.RESULT_ACKNOWLEDGED.value
        )
        assignment.updated_at = datetime.now()
        db.commit()
        return {
            "accepted": True,
            "reason_code": None,
            "workflow_state": parent_workflow.status,
            "assignment_status": assignment.status,
        }


def describe_child_assignment_acknowledgement(
    parent_terminal_id: str,
    child_terminal_id: Optional[str] = None,
    result_id: Optional[str] = None,
) -> Dict[str, Optional[str]]:
    """Return the stable reason for an acknowledgement that cannot proceed."""
    _ensure_child_assignment_schema()
    _ensure_delegation_result_schema()
    with SessionLocal() as db:
        workflow = _open_workflow(db, parent_terminal_id, create=False)
        if workflow is None or workflow.status != WORKFLOW_OPEN:
            return {
                "reason_code": "PARENT_NOT_ELIGIBLE",
                "workflow_state": workflow.status if workflow else None,
            }
        query = db.query(ChildAssignmentModel).filter_by(parent_terminal_id=parent_terminal_id)
        if result_id:
            query = query.join(
                DelegationResultModel,
                DelegationResultModel.child_assignment_id == ChildAssignmentModel.id,
            ).filter(DelegationResultModel.id == result_id)
        elif child_terminal_id:
            query = query.filter_by(child_terminal_id=child_terminal_id)
        assignment = query.first()
        if assignment is None:
            return {"reason_code": "WRONG_DISPATCH_MODE", "workflow_state": workflow.status}
        if assignment.status in (
            ChildAssignmentStatus.RESULT_ACKNOWLEDGED.value,
            ChildAssignmentStatus.HANDOFF_RESULT_ACKNOWLEDGED.value,
        ):
            return {"reason_code": "RESULT_ALREADY_ACKNOWLEDGED", "workflow_state": workflow.status}
        if assignment.status in (
            ChildAssignmentStatus.RESULT_QUEUED.value,
            ChildAssignmentStatus.HANDOFF_RESULT_QUEUED.value,
        ):
            return {
                "reason_code": "RESULT_NOT_DELIVERED",
                "workflow_state": workflow.status,
                "assignment_status": assignment.status,
            }
        return {"reason_code": "WRONG_DISPATCH_MODE", "workflow_state": workflow.status}


def requeue_unacknowledged_child_assignment_results() -> int:
    """Make unacknowledged assigned results redeliverable after a server restart.

    Inbox ``delivered`` only records that input reached a terminal pane, not
    that the parent agent consumed it.  Replaying such rows on restart gives
    the normal Inbox transport at-least-once semantics until the parent emits
    its durable acknowledgement.  The single persisted result row keeps the
    logical child effect idempotent.
    """
    _ensure_child_assignment_schema()
    with SessionLocal() as db:
        assignments = (
            db.query(ChildAssignmentModel)
            .filter(
                ChildAssignmentModel.status.in_(
                    (
                        ChildAssignmentStatus.RESULT_QUEUED.value,
                        ChildAssignmentStatus.RESULT_DELIVERED.value,
                        ChildAssignmentStatus.RESULT_FAILED.value,
                        ChildAssignmentStatus.HANDOFF_RESULT_QUEUED.value,
                        ChildAssignmentStatus.HANDOFF_RESULT_DELIVERED.value,
                        ChildAssignmentStatus.HANDOFF_RESULT_FAILED.value,
                    )
                )
            )
            .all()
        )
        requeued = 0
        for assignment in assignments:
            if assignment.result_message_id is None:
                continue
            inbox_msg = (
                db.query(InboxModel).filter(InboxModel.id == assignment.result_message_id).first()
            )
            if inbox_msg is None:
                continue
            inbox_msg.status = MessageStatus.PENDING.value
            assignment.status = (
                ChildAssignmentStatus.HANDOFF_RESULT_QUEUED.value
                if assignment.status.startswith("handoff_")
                else ChildAssignmentStatus.RESULT_QUEUED.value
            )
            assignment.updated_at = datetime.now()
            requeued += 1
        if requeued:
            db.commit()
        return requeued


def cancel_child_assignments_for_terminal(terminal_id: str) -> int:
    """Cancel relationships involving an explicitly exited/deleted terminal."""
    _ensure_child_assignment_schema()
    _ensure_delegation_result_schema()
    with SessionLocal() as db:
        assignments = (
            db.query(ChildAssignmentModel)
            .filter(
                (ChildAssignmentModel.parent_terminal_id == terminal_id)
                | (ChildAssignmentModel.child_terminal_id == terminal_id)
            )
            .all()
        )
        changed = 0
        for assignment in assignments:
            active_statuses = (
                ChildAssignmentStatus.AWAITING_RESULT.value,
                ChildAssignmentStatus.RESULT_QUEUED.value,
                ChildAssignmentStatus.RESULT_DELIVERED.value,
                ChildAssignmentStatus.RESULT_FAILED.value,
                ChildAssignmentStatus.HANDOFF_AWAITING_RESULT.value,
                ChildAssignmentStatus.HANDOFF_RECOVERY_AWAITING_RESULT.value,
                ChildAssignmentStatus.HANDOFF_DIRECT_RESULT_CLAIMED.value,
                ChildAssignmentStatus.HANDOFF_RESULT_QUEUED.value,
                ChildAssignmentStatus.HANDOFF_RESULT_DELIVERED.value,
                ChildAssignmentStatus.HANDOFF_RESULT_FAILED.value,
            )
            # A completed direct-handoff result must survive child cleanup so
            # that its still-live parent can consume the one durable Inbox row.
            child_completed_handoff = (
                assignment.child_terminal_id == terminal_id
                and assignment.status
                in (
                    ChildAssignmentStatus.HANDOFF_DIRECT_RESULT_CLAIMED.value,
                    ChildAssignmentStatus.HANDOFF_RESULT_QUEUED.value,
                    ChildAssignmentStatus.HANDOFF_RESULT_DELIVERED.value,
                    ChildAssignmentStatus.HANDOFF_RESULT_FAILED.value,
                )
            )
            # A managed child may leave its current provider invocation before
            # producing an authority. Preserve the exact relation/result for
            # same-child bounded recovery instead of cancelling it on a
            # process observation alone (the 1510/1514 failure mode).
            if (
                assignment.child_terminal_id == terminal_id
                and _handoff_requires_structured_result(db, assignment)
                and assignment.status
                in (
                    ChildAssignmentStatus.HANDOFF_AWAITING_RESULT.value,
                    ChildAssignmentStatus.HANDOFF_RECOVERY_AWAITING_RESULT.value,
                )
            ):
                kind = "handoff"
                result = _create_result_for_assignment(
                    db,
                    assignment,
                    kind,
                    _open_workflow(db, assignment.parent_terminal_id, create=False),
                )
                recovery_count = _handoff_recovery_count(db, result.id)
                if recovery_count < _MAX_HANDOFF_EXIT_RECOVERY_CYCLES:
                    assignment.status = ChildAssignmentStatus.HANDOFF_RECOVERY_AWAITING_RESULT.value
                    assignment.updated_at = datetime.now()
                    _record_result_event(
                        db,
                        result.id,
                        f"handoff-provider-exit-recovery:{assignment.id}:{recovery_count + 1}",
                        "handoff-provider-exit-recovery",
                        "cao_lifecycle",
                        terminal_id,
                        detail={"cycle": recovery_count + 1},
                    )
                    changed += 1
                    continue
                # Do not feed an exhausted managed handoff back into the same
                # recoverable state forever.  Retain its identity and durable
                # evidence, but make the blocker terminal and observable.
                _terminalize_handoff_recovery_exhausted(db, assignment, result, terminal_id)
                changed += 1
                continue
            if assignment.status in active_statuses and not child_completed_handoff:
                kind = "handoff" if assignment.status.startswith("handoff_") else "assign"
                child_lifecycle_exit = (
                    assignment.child_terminal_id == terminal_id
                    and assignment.parent_terminal_id != terminal_id
                )
                assignment.status = ChildAssignmentStatus.CANCELLED.value
                assignment.updated_at = datetime.now()
                result = _create_result_for_assignment(
                    db,
                    assignment,
                    kind,
                    _open_workflow(db, assignment.parent_terminal_id, create=False),
                )
                if result.status == DelegationResultStatus.AWAITING.value:
                    result.status = (
                        DelegationResultStatus.INCOMPLETE.value
                        if child_lifecycle_exit
                        else DelegationResultStatus.CANCELLED.value
                    )
                    result.reason_code = (
                        "child_exited" if child_lifecycle_exit else "terminal_cancelled"
                    )
                    result.finalized_at = result.updated_at = datetime.now()
                    _record_result_event(
                        db,
                        result.id,
                        f"result-terminal:{assignment.id}:{terminal_id}",
                        result.status,
                        "cao_lifecycle",
                        terminal_id,
                    )
                    _purge_staged_handoff_submission(db, result.id)
                changed += 1
        if changed:
            db.commit()
        return changed


def get_handoff_child_status(child_terminal_id: str) -> Optional[str]:
    """Return a direct-handoff relation state, if this child has one."""
    _ensure_child_assignment_schema()
    with SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel)
            .filter(ChildAssignmentModel.child_terminal_id == child_terminal_id)
            .first()
        )
        if assignment is None:
            return None
        if assignment.status == ChildAssignmentStatus.CANCELLED.value:
            return None
        if not assignment.status.startswith("handoff_"):
            return None
        return assignment.status


def get_handoff_parent_terminal_id(child_terminal_id: str) -> Optional[str]:
    """Return the owning parent for a live/recovery direct handoff."""
    _ensure_child_assignment_schema()
    with SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel)
            .filter(ChildAssignmentModel.child_terminal_id == child_terminal_id)
            .first()
        )
        if (
            assignment is None
            or assignment.status == ChildAssignmentStatus.CANCELLED.value
            or not assignment.status.startswith("handoff_")
        ):
            return None
        return assignment.parent_terminal_id


def create_handoff_child_result_message(
    child_terminal_id: str, message: str
) -> tuple[Optional[InboxMessage], bool]:
    """Atomically persist one completed direct-handoff result for its parent."""
    _ensure_child_assignment_schema()
    _ensure_delegation_result_schema()
    _ensure_workflow_schema()
    with SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel)
            .filter(ChildAssignmentModel.child_terminal_id == child_terminal_id)
            .first()
        )
        if assignment is None or assignment.status == ChildAssignmentStatus.CANCELLED.value:
            return None, True
        workflow = _open_workflow(db, assignment.parent_terminal_id, create=False)
        if workflow is None or workflow.status != WORKFLOW_OPEN:
            # An explicit owner/cancel outcome wins over a late child result;
            # retain no Inbox wake that could revive the closed workflow.
            return None, True
        if assignment.status not in (
            ChildAssignmentStatus.HANDOFF_AWAITING_RESULT.value,
            ChildAssignmentStatus.HANDOFF_RECOVERY_AWAITING_RESULT.value,
        ):
            if assignment.result_message_id is None:
                return None, True
            inbox_msg = (
                db.query(InboxModel).filter(InboxModel.id == assignment.result_message_id).first()
            )
            return (
                (_inbox_model_to_message(inbox_msg), True)
                if inbox_msg is not None
                else (None, True)
            )

        # Registered handoffs are managed structured work.  A terminal tail,
        # even a stable-looking one, is not an authority to complete them.
        result = (
            db.query(DelegationResultModel).filter_by(child_assignment_id=assignment.id).first()
        )
        if _handoff_requires_structured_result(db, assignment) and (
            result is None or _staged_handoff_submission(db, result.id) is None
        ):
            return None, False
        inbox_msg, duplicate, _reason = _finalize_managed_delegation_result(
            db, assignment, message, "child_structured_submission"
        )
        db.commit()
        return inbox_msg, duplicate


def get_handoff_child_result_message(child_terminal_id: str) -> Optional[InboxMessage]:
    """Return the one durable recovery result, if it has already been created."""
    _ensure_child_assignment_schema()
    with SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel)
            .filter(ChildAssignmentModel.child_terminal_id == child_terminal_id)
            .first()
        )
        if assignment is None or assignment.result_message_id is None:
            return None
        inbox_msg = (
            db.query(InboxModel).filter(InboxModel.id == assignment.result_message_id).first()
        )
        return _inbox_model_to_message(inbox_msg) if inbox_msg is not None else None


def handoff_child_cleanup_acknowledged(child_terminal_id: str) -> bool:
    """Record the once-only cleanup receipt after a durable recovery result."""
    _ensure_child_assignment_schema()
    with SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel)
            .filter(ChildAssignmentModel.child_terminal_id == child_terminal_id)
            .first()
        )
        if assignment is None or not assignment.status.startswith("handoff_"):
            return False
        if assignment.result_message_id is None:
            return False
        if assignment.cleanup_acknowledged:
            return True
        assignment.cleanup_acknowledged = True
        assignment.updated_at = datetime.now()
        db.commit()
        return True


def handoff_child_cleanup_is_acknowledged(child_terminal_id: str) -> bool:
    """Return whether recovery may wake the parent without redoing cleanup."""
    _ensure_child_assignment_schema()
    with SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel)
            .filter(ChildAssignmentModel.child_terminal_id == child_terminal_id)
            .first()
        )
        return bool(assignment and assignment.cleanup_acknowledged)


def claim_handoff_child_result_direct(
    parent_terminal_id: Optional[str], child_terminal_id: str, output: str
) -> Optional[bool]:
    """Durably claim one validated live-handoff result before cleanup.

    ``None`` means this was not a registered direct handoff, ``True`` means
    the caller owns the same claim (including a retry), and ``False`` means a
    restart recovery already owns the result or the parent does not match.
    """
    _ensure_child_assignment_schema()
    _ensure_delegation_result_schema()
    with SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel)
            .filter(ChildAssignmentModel.child_terminal_id == child_terminal_id)
            .first()
        )
        if assignment is None or not assignment.status.startswith("handoff_"):
            return None
        # The injected bearer capability identifies new managed structured
        # handoffs.  Only old/unmanaged relations retain capture compatibility.
        if _handoff_requires_structured_result(db, assignment):
            return False
        if assignment.parent_terminal_id != parent_terminal_id:
            return False
        workflow = _open_workflow(db, assignment.parent_terminal_id, create=False)
        if workflow is None or workflow.status != WORKFLOW_OPEN:
            return False
        if assignment.status == ChildAssignmentStatus.HANDOFF_DIRECT_RESULT_CLAIMED.value:
            return True
        if assignment.status != ChildAssignmentStatus.HANDOFF_AWAITING_RESULT.value:
            return False
        assignment.status = ChildAssignmentStatus.HANDOFF_DIRECT_RESULT_CLAIMED.value
        assignment.direct_result_output = output
        _finalize_result(db, assignment, output, "cao_handoff_capture")
        assignment.updated_at = datetime.now()
        db.commit()
        return True


def claim_staged_handoff_result_direct(
    parent_terminal_id: Optional[str], child_terminal_id: str
) -> Optional[bool]:
    """Atomically claim an authenticated staged V1 handoff before capture parsing.

    The staged document is eligible only for its original, still-open direct
    handoff relation.  In particular, a terminal/cancel/owner transition or a
    missing admitted child receipt cannot be revived by a late staged row.
    ``None`` deliberately means that the normal terminal-capture fallback may
    proceed; ``False`` fences an existing relation owned by somebody else.
    """
    _ensure_child_assignment_schema()
    _ensure_delegation_result_schema()
    _ensure_workflow_schema()
    with SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel)
            .filter(ChildAssignmentModel.child_terminal_id == child_terminal_id)
            .first()
        )
        if assignment is None or not assignment.status.startswith("handoff_"):
            return None
        if assignment.parent_terminal_id != parent_terminal_id:
            return False
        if assignment.status == ChildAssignmentStatus.HANDOFF_DIRECT_RESULT_CLAIMED.value:
            return True
        if assignment.status != ChildAssignmentStatus.HANDOFF_AWAITING_RESULT.value:
            return False

        parent_workflow = _open_workflow(db, assignment.parent_terminal_id, create=False)
        child_workflow = _open_workflow(db, child_terminal_id, create=False)
        if (
            parent_workflow is None
            or parent_workflow.status != WORKFLOW_OPEN
            or child_workflow is None
            or child_workflow.status != WORKFLOW_OPEN
        ):
            return False
        result = (
            db.query(DelegationResultModel)
            .filter_by(child_assignment_id=assignment.id, delegation_kind="handoff")
            .first()
        )
        if result is None or result.status != DelegationResultStatus.AWAITING.value:
            return False
        staged = _staged_handoff_submission(db, result.id)
        if staged is None:
            return None
        if (
            staged.child_terminal_id != child_terminal_id
            or staged.schema_version != 1
            or db.query(WorkflowTurnReceiptModel)
            .filter_by(
                workflow_turn_id=staged.workflow_turn_id,
                receiver_terminal_id=child_terminal_id,
            )
            .first()
            is None
        ):
            return False

        # Submission records canonical strict V1 JSON.  Check it again at the
        # consumption boundary so arbitrary/corrupt staging cannot bypass the
        # parser; an invalid row leaves the ordinary no-stage fallback intact.
        try:
            document = HandoffResultDocumentV1.model_validate(json.loads(staged.document_json))
            canonical = canonical_handoff_result_v1_bytes(document)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if (
            canonical.decode("utf-8") != staged.document_json
            or not hmac.compare_digest(hashlib.sha256(canonical).hexdigest(), staged.content_sha256)
            or len(canonical) != staged.content_bytes
        ):
            return None

        _inbox, _duplicate, reason = _finalize_managed_delegation_result(
            db, assignment, document.body_markdown, "child_structured_submission"
        )
        if reason != "ACCEPTED":
            return False
        db.commit()
        return True


def get_acknowledged_handoff_child_result_direct(
    parent_terminal_id: Optional[str], child_terminal_id: str
) -> Optional[str]:
    """Return a direct result after delivery or parent acknowledgement.

    The direct handoff cleanup receipt means the result is ready to return from
    the MCP call, not that its parent has consumed it.  Keep the delivered
    relation visible to the completion barrier until the parent acknowledges
    the returned durable result.
    """
    _ensure_child_assignment_schema()
    with SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel)
            .filter(ChildAssignmentModel.child_terminal_id == child_terminal_id)
            .first()
        )
        if (
            assignment is None
            or assignment.parent_terminal_id != parent_terminal_id
            or assignment.status
            not in (
                ChildAssignmentStatus.HANDOFF_RESULT_DELIVERED.value,
                ChildAssignmentStatus.HANDOFF_RESULT_ACKNOWLEDGED.value,
            )
        ):
            return None
        result = (
            db.query(DelegationResultModel)
            .filter_by(
                child_assignment_id=assignment.id, status=DelegationResultStatus.COMPLETE.value
            )
            .first()
        )
        if result is None or not result.document_json:
            return None
        document = json.loads(result.document_json)
        body = document.get("body_markdown") if isinstance(document, dict) else None
        return body if isinstance(body, str) else None


def get_claimed_handoff_child_result_direct(
    parent_terminal_id: Optional[str], child_terminal_id: str
) -> Optional[str]:
    """Return the validated direct result retained while cleanup is retried.

    A claim is created only after two stable valid captures from a live
    completed child.  Once that receipt exists, a retry must not depend on a
    later terminal capture still being readable or byte-for-byte identical.
    """
    _ensure_child_assignment_schema()
    with SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel)
            .filter(ChildAssignmentModel.child_terminal_id == child_terminal_id)
            .first()
        )
        if (
            assignment is None
            or assignment.parent_terminal_id != parent_terminal_id
            or assignment.status != ChildAssignmentStatus.HANDOFF_DIRECT_RESULT_CLAIMED.value
        ):
            return None
        result = (
            db.query(DelegationResultModel)
            .filter_by(
                child_assignment_id=assignment.id, status=DelegationResultStatus.COMPLETE.value
            )
            .first()
        )
        if result is None or not result.document_json:
            return None
        document = json.loads(result.document_json)
        body = document.get("body_markdown") if isinstance(document, dict) else None
        return body if isinstance(body, str) else None


def acknowledge_handoff_child_result_direct(
    parent_terminal_id: Optional[str], child_terminal_id: str
) -> Optional[str]:
    """Record direct cleanup while retaining a parent acknowledgement barrier."""
    _ensure_child_assignment_schema()
    with SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel)
            .filter(ChildAssignmentModel.child_terminal_id == child_terminal_id)
            .first()
        )
        if assignment is None or assignment.parent_terminal_id != parent_terminal_id:
            return None
        if assignment.status in (
            ChildAssignmentStatus.HANDOFF_RESULT_DELIVERED.value,
            ChildAssignmentStatus.HANDOFF_RESULT_ACKNOWLEDGED.value,
        ):
            result = (
                db.query(DelegationResultModel)
                .filter_by(
                    child_assignment_id=assignment.id, status=DelegationResultStatus.COMPLETE.value
                )
                .first()
            )
            if result is None or not result.document_json:
                return None
            document = json.loads(result.document_json)
            body = document.get("body_markdown") if isinstance(document, dict) else None
            return body if isinstance(body, str) else None
        if assignment.status != ChildAssignmentStatus.HANDOFF_DIRECT_RESULT_CLAIMED.value:
            return None
        result = (
            db.query(DelegationResultModel)
            .filter_by(
                child_assignment_id=assignment.id, status=DelegationResultStatus.COMPLETE.value
            )
            .first()
        )
        if result is None or not result.document_json:
            return None
        document = json.loads(result.document_json)
        body = document.get("body_markdown") if isinstance(document, dict) else None
        if not isinstance(body, str):
            return None
        # Cleanup makes the output safe to return from the direct MCP call,
        # but only its parent can acknowledge incorporating that result.
        assignment.status = ChildAssignmentStatus.HANDOFF_RESULT_DELIVERED.value
        assignment.cleanup_acknowledged = True
        assignment.updated_at = datetime.now()
        db.commit()
        return body


def get_pending_handoff_child_terminal_ids() -> List[str]:
    """List direct handoffs needing a safe boundary/restart capture or cleanup."""
    _ensure_child_assignment_schema()
    with SessionLocal() as db:
        rows = (
            db.query(ChildAssignmentModel.child_terminal_id)
            .filter(
                (
                    ChildAssignmentModel.status.in_(
                        (
                            ChildAssignmentStatus.HANDOFF_AWAITING_RESULT.value,
                            ChildAssignmentStatus.HANDOFF_RECOVERY_AWAITING_RESULT.value,
                        )
                    )
                )
                | (
                    ChildAssignmentModel.status.in_(
                        (
                            ChildAssignmentStatus.HANDOFF_RESULT_QUEUED.value,
                            ChildAssignmentStatus.HANDOFF_RESULT_DELIVERED.value,
                        )
                    )
                    & (ChildAssignmentModel.cleanup_acknowledged.is_(False))
                )
            )
            .all()
        )
        return [row[0] for row in rows]


def arm_handoff_continuations_for_restart() -> int:
    """Make only pre-restart direct handoffs eligible for Inbox recovery."""
    _ensure_child_assignment_schema()
    with SessionLocal() as db:
        assignments = (
            db.query(ChildAssignmentModel)
            .filter(
                ChildAssignmentModel.status == ChildAssignmentStatus.HANDOFF_AWAITING_RESULT.value
            )
            .all()
        )
        for assignment in assignments:
            assignment.status = ChildAssignmentStatus.HANDOFF_RECOVERY_AWAITING_RESULT.value
            assignment.updated_at = datetime.now()
        if assignments:
            db.commit()
        return len(assignments)


def terminalize_missing_terminal_assignments_for_restart() -> int:
    """Close only restart relations proven unrecoverable by missing metadata.

    A missing child cannot produce a later callback or capture.  A missing
    parent has no lawful consumer.  Both cases are safe to terminalize; live
    relations remain untouched for the normal recovery paths.
    """
    _ensure_child_assignment_schema()
    _ensure_delegation_result_schema()
    with SessionLocal() as db:
        active = (
            ChildAssignmentStatus.AWAITING_RESULT.value,
            ChildAssignmentStatus.RESULT_QUEUED.value,
            ChildAssignmentStatus.RESULT_DELIVERED.value,
            ChildAssignmentStatus.RESULT_FAILED.value,
            ChildAssignmentStatus.HANDOFF_AWAITING_RESULT.value,
            ChildAssignmentStatus.HANDOFF_RECOVERY_AWAITING_RESULT.value,
            # A recovery result may already have been queued or delivered
            # when its child metadata disappears before the next restart.
            # These relations cannot complete cleanup, so terminalize them
            # while retaining their immutable COMPLETE artifact.
            ChildAssignmentStatus.HANDOFF_RESULT_QUEUED.value,
            ChildAssignmentStatus.HANDOFF_RESULT_DELIVERED.value,
            ChildAssignmentStatus.HANDOFF_RESULT_FAILED.value,
        )
        assignments = (
            db.query(ChildAssignmentModel).filter(ChildAssignmentModel.status.in_(active)).all()
        )
        changed = 0
        now = datetime.now()
        for assignment in assignments:
            child_exists = (
                db.query(TerminalModel).filter_by(id=assignment.child_terminal_id).first()
                is not None
            )
            parent_exists = (
                db.query(TerminalModel).filter_by(id=assignment.parent_terminal_id).first()
                is not None
            )
            if child_exists and parent_exists:
                continue
            kind = "handoff" if assignment.status.startswith("handoff_") else "assign"
            result = _create_result_for_assignment(
                db,
                assignment,
                kind,
                _open_workflow(db, assignment.parent_terminal_id, create=False),
            )
            if result.status == DelegationResultStatus.AWAITING.value:
                result.status = (
                    DelegationResultStatus.INCOMPLETE.value
                    if not child_exists
                    else DelegationResultStatus.CANCELLED.value
                )
                result.reason_code = (
                    "restart_missing_child_terminal"
                    if not child_exists
                    else "restart_missing_parent_terminal"
                )
                result.finalized_at = result.updated_at = now
                _record_result_event(
                    db,
                    result.id,
                    f"result-restart-terminal:{assignment.id}:{result.reason_code}",
                    result.status,
                    "cao_lifecycle",
                    (
                        assignment.child_terminal_id
                        if not child_exists
                        else assignment.parent_terminal_id
                    ),
                    detail={"reason_code": result.reason_code},
                )
                _purge_staged_handoff_submission(db, result.id)
            assignment.status = ChildAssignmentStatus.CANCELLED.value
            assignment.updated_at = now
            changed += 1
        if changed:
            db.commit()
        return changed


def get_pending_message_receiver_ids() -> List[str]:
    """List durable inbox destinations needing restart reconciliation."""
    with SessionLocal() as db:
        rows = (
            db.query(InboxModel.receiver_id)
            .filter(InboxModel.status == MessageStatus.PENDING.value)
            .distinct()
            .all()
        )
        return [row[0] for row in rows]


# Project registry database functions


def _project_from_row(project: ProjectModel) -> Project:
    return Project(
        projectId=project.id,
        name=project.name,
        path=project.path,
        description=project.description,
        isDefault=bool(project.is_default),
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def create_project(
    *,
    project_id: str,
    name: str,
    normalized_name: str,
    path: str,
    normalized_path: str,
    description: str | None,
    is_default: bool,
) -> Project:
    """Persist one project and atomically maintain the single-default invariant."""
    _ensure_project_schema()
    with SessionLocal() as db:
        duplicate = (
            db.query(ProjectModel.id)
            .filter(
                (ProjectModel.normalized_name == normalized_name)
                | (ProjectModel.normalized_path == normalized_path)
            )
            .first()
        )
        if duplicate:
            raise ValueError("A project with this name or path already exists")
        make_default = is_default or db.query(ProjectModel.id).first() is None
        if make_default:
            db.query(ProjectModel).update({ProjectModel.is_default: False})
        project = ProjectModel(
            id=project_id,
            name=name,
            normalized_name=normalized_name,
            path=path,
            normalized_path=normalized_path,
            description=description,
            is_default=make_default,
        )
        db.add(project)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ValueError("A project with this name or path already exists") from exc
        db.refresh(project)
        return _project_from_row(project)


def list_projects() -> List[Project]:
    _ensure_project_schema()
    with SessionLocal() as db:
        rows = (
            db.query(ProjectModel).order_by(ProjectModel.is_default.desc(), ProjectModel.name).all()
        )
        return [_project_from_row(row) for row in rows]


def get_project(project_id: str) -> Project | None:
    _ensure_project_schema()
    with SessionLocal() as db:
        project = db.query(ProjectModel).filter(ProjectModel.id == project_id).first()
        return _project_from_row(project) if project else None


def find_project_by_normalized_path(normalized_path: str) -> Project | None:
    """Return only an exact canonical path match; no fuzzy or default fallback."""
    _ensure_project_schema()
    with SessionLocal() as db:
        project = (
            db.query(ProjectModel).filter(ProjectModel.normalized_path == normalized_path).first()
        )
        return _project_from_row(project) if project else None


def get_session_project_id(session_name: str) -> str | None:
    """Recover the explicit launch project already established for a session."""
    _ensure_project_schema()
    with SessionLocal() as db:
        row = (
            db.query(TerminalModel.project_id)
            .filter(
                TerminalModel.tmux_session == session_name, TerminalModel.project_id.is_not(None)
            )
            .order_by(TerminalModel.last_active.asc(), TerminalModel.id.asc())
            .first()
        )
        return str(row[0]) if row and row[0] else None


def set_default_project(project_id: str) -> Project | None:
    _ensure_project_schema()
    with SessionLocal() as db:
        project = db.query(ProjectModel).filter(ProjectModel.id == project_id).first()
        if project is None:
            return None
        db.query(ProjectModel).update({ProjectModel.is_default: False})
        project.is_default = True
        db.commit()
        db.refresh(project)
        return _project_from_row(project)


def update_project(
    project_id: str,
    *,
    name: str,
    normalized_name: str,
    path: str,
    normalized_path: str,
    description: str | None,
    is_default: bool | None,
) -> Project | None:
    """Update registry metadata atomically; terminal and flow snapshots are untouched."""
    _ensure_project_schema()
    with SessionLocal() as db:
        project = db.query(ProjectModel).filter(ProjectModel.id == project_id).first()
        if project is None:
            return None
        duplicate = (
            db.query(ProjectModel.id)
            .filter(
                ProjectModel.id != project_id,
                (ProjectModel.normalized_name == normalized_name)
                | (ProjectModel.normalized_path == normalized_path),
            )
            .first()
        )
        if duplicate:
            raise ValueError("A project with this name or path already exists")
        project.name, project.normalized_name = name, normalized_name
        project.path, project.normalized_path = path, normalized_path
        project.description = description
        if is_default is True:
            db.query(ProjectModel).filter(ProjectModel.id != project_id).update(
                {ProjectModel.is_default: False}
            )
            project.is_default = True
        elif is_default is False and project.is_default:
            # The invariant is one default whenever the registry is non-empty.
            replacement = (
                db.query(ProjectModel)
                .filter(ProjectModel.id != project_id)
                .order_by(ProjectModel.name)
                .first()
            )
            if replacement is not None:
                project.is_default = False
                replacement.is_default = True
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise ValueError("A project with this name or path already exists") from exc
        db.refresh(project)
        return _project_from_row(project)


def delete_project(project_id: str) -> bool:
    """Delete registry metadata only; historical terminal/flow copies remain intact."""
    _ensure_project_schema()
    with SessionLocal() as db:
        project = db.query(ProjectModel).filter(ProjectModel.id == project_id).first()
        if project is None:
            return False
        was_default = bool(project.is_default)
        db.delete(project)
        db.flush()
        if was_default:
            replacement = db.query(ProjectModel).order_by(ProjectModel.name).first()
            if replacement is not None:
                replacement.is_default = True
        db.commit()
        return True


# Flow database functions


def create_flow(
    name: str,
    file_path: str,
    schedule: str,
    agent_profile: str,
    provider: str,
    script: str,
    next_run: datetime,
    project_id: str | None = None,
    project_name: str | None = None,
    project_path: str | None = None,
    project_description: str | None = None,
) -> Flow:
    """Create flow record."""
    with SessionLocal() as db:
        flow = FlowModel(
            name=name,
            file_path=file_path,
            schedule=schedule,
            agent_profile=agent_profile,
            provider=provider,
            script=script,
            next_run=next_run,
            project_id=project_id,
            project_name=project_name,
            project_path=project_path,
            project_description=project_description,
        )
        db.add(flow)
        db.commit()
        db.refresh(flow)
        return Flow(
            name=flow.name,
            file_path=flow.file_path,
            schedule=flow.schedule,
            agent_profile=flow.agent_profile,
            provider=flow.provider,
            script=flow.script,
            last_run=flow.last_run,
            next_run=flow.next_run,
            enabled=flow.enabled,
            projectId=flow.project_id,
            project_name=flow.project_name,
            project_path=flow.project_path,
            project_description=flow.project_description,
        )


def get_flow(name: str) -> Optional[Flow]:
    """Get flow by name."""
    with SessionLocal() as db:
        flow = db.query(FlowModel).filter(FlowModel.name == name).first()
        if not flow:
            return None
        return Flow(
            name=flow.name,
            file_path=flow.file_path,
            schedule=flow.schedule,
            agent_profile=flow.agent_profile,
            provider=flow.provider,
            script=flow.script,
            last_run=flow.last_run,
            next_run=flow.next_run,
            enabled=flow.enabled,
            projectId=flow.project_id,
            project_name=flow.project_name,
            project_path=flow.project_path,
            project_description=flow.project_description,
        )


def list_flows() -> List[Flow]:
    """List all flows."""
    with SessionLocal() as db:
        flows = db.query(FlowModel).order_by(FlowModel.next_run).all()
        return [
            Flow(
                name=f.name,
                file_path=f.file_path,
                schedule=f.schedule,
                agent_profile=f.agent_profile,
                provider=f.provider,
                script=f.script,
                last_run=f.last_run,
                next_run=f.next_run,
                enabled=f.enabled,
                projectId=f.project_id,
                project_name=f.project_name,
                project_path=f.project_path,
                project_description=f.project_description,
            )
            for f in flows
        ]


def update_flow_run_times(name: str, last_run: datetime, next_run: datetime) -> bool:
    """Update flow run times after execution."""
    with SessionLocal() as db:
        flow = db.query(FlowModel).filter(FlowModel.name == name).first()
        if flow:
            flow.last_run = last_run
            flow.next_run = next_run
            db.commit()
            return True
        return False


def update_flow_next_run(name: str, next_run: datetime) -> bool:
    """Advance scheduling after a failed attempt without inventing a successful run."""
    with SessionLocal() as db:
        flow = db.query(FlowModel).filter(FlowModel.name == name).first()
        if flow:
            flow.next_run = next_run
            db.commit()
            return True
        return False


def update_flow_enabled(name: str, enabled: bool, next_run: Optional[datetime] = None) -> bool:
    """Update flow enabled status and optionally next_run."""
    with SessionLocal() as db:
        flow = db.query(FlowModel).filter(FlowModel.name == name).first()
        if flow:
            flow.enabled = enabled
            if next_run is not None:
                flow.next_run = next_run
            db.commit()
            return True
        return False


def delete_flow(name: str) -> bool:
    """Delete flow."""
    with SessionLocal() as db:
        deleted = db.query(FlowModel).filter(FlowModel.name == name).delete()
        db.commit()
        return deleted > 0


def get_flows_to_run() -> List[Flow]:
    """Get enabled flows where next_run <= now."""
    with SessionLocal() as db:
        now = datetime.now()
        flows = (
            db.query(FlowModel).filter(FlowModel.enabled == True, FlowModel.next_run <= now).all()
        )
        return [
            Flow(
                name=f.name,
                file_path=f.file_path,
                schedule=f.schedule,
                agent_profile=f.agent_profile,
                provider=f.provider,
                script=f.script,
                last_run=f.last_run,
                next_run=f.next_run,
                enabled=f.enabled,
                projectId=f.project_id,
                project_name=f.project_name,
                project_path=f.project_path,
                project_description=f.project_description,
            )
            for f in flows
        ]
