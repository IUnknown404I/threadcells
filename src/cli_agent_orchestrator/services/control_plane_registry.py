"""Canonical provider configuration and immutable profile registries."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from importlib import resources
from typing import Any, Mapping, Optional, cast

from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.models.agent_profile import (
    AgentProfile,
    ProfileAuthority,
    ProfileDefinition,
)
from cli_agent_orchestrator.providers.contracts import (
    ProviderConfiguration,
    ProviderConfigurationError,
    default_provider_configuration,
)
from cli_agent_orchestrator.providers.registry import (
    ProviderAdapterRegistry,
    build_provider_adapter_registry,
)
from cli_agent_orchestrator.utils.mcp_runtime import (
    TRUSTED_MCP_SERVER_IDS,
    resolve_trusted_mcp_server_refs,
)
from cli_agent_orchestrator.utils.tool_mapping import resolve_allowed_tools

logger = logging.getLogger(__name__)

# Durable compatibility identifiers: changing these would replay bootstrap
# migrations against an existing installation during the brand transition.
PROFILE_BOOTSTRAP_RECEIPT = "threadmesh-profile-builtins-v1"
PROFILE_INVENTORY_RECEIPT = "threadmesh-profile-builtins-v2"
PROVIDER_BOOTSTRAP_RECEIPT = "threadmesh-provider-config-builtins-v1"
_registry_initialized = False
_registry_engine_identity: int | None = None


class RegistryValidationError(ValueError):
    """Portable JSON-pointer validation result for API, UI, and CLI."""

    def __init__(self, issues: list[dict[str, str]]):
        self.issues = issues
        super().__init__("registry document is invalid")


class RegistryConflictError(RuntimeError):
    pass


class RegistryNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class LaunchResolution:
    profile: AgentProfile
    profile_revision_id: str
    provider_configuration: dict[str, Any]
    provider_config_revision_id: str
    provider_adapter_id: str
    owner_grant_required: bool
    snapshot: dict[str, Any]


def registry_is_initialized() -> bool:
    return _registry_initialized and _registry_engine_identity == id(database.engine)


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(serialized: str) -> str:
    return hashlib.sha256(serialized.encode("utf-8", "strict")).hexdigest()


def _pointer(part: object) -> str:
    return str(part).replace("~", "~0").replace("/", "~1")


def _pydantic_issues(error: ValidationError) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for item in error.errors(include_url=False, include_input=False):
        location = "/".join(_pointer(part) for part in item["loc"])
        issues.append(
            {
                "pointer": f"/{location}" if location else "/",
                "code": str(item["type"]),
                "message": str(item["msg"]),
            }
        )
    return issues


def validate_profile_document(
    raw: Mapping[str, Any], *, trusted_operator: bool = False, trusted_builtin: bool = False
) -> ProfileDefinition:
    try:
        profile = ProfileDefinition.model_validate(raw)
    except ValidationError as error:
        raise RegistryValidationError(_pydantic_issues(error)) from None
    issues: list[dict[str, str]] = []
    if set(profile.mcp_server_refs) - TRUSTED_MCP_SERVER_IDS:
        issues.append(
            {
                "pointer": "/mcp_server_refs",
                "code": "unregistered_capability",
                "message": "contains an MCP capability not registered by this installation",
            }
        )
    if "*" in profile.allowed_tools and not trusted_operator:
        issues.append(
            {
                "pointer": "/allowed_tools",
                "code": "trusted_operator_grant_required",
                "message": "unrestricted tools require a separate trusted operator grant",
            }
        )
    if profile.authority.unrestricted_tools_authorized and not trusted_operator:
        issues.append(
            {
                "pointer": "/authority/unrestricted_tools_authorized",
                "code": "trusted_operator_grant_required",
                "message": "unrestricted authority cannot be introduced by an ordinary import",
            }
        )
    privileged_capability = bool(
        profile.execution_mode == "owner_executor"
        or profile.reasoning_level == "xhigh"
        or (profile.authority.sandbox_mode == "danger-full-access" and not trusted_builtin)
        or profile.authority.unrestricted_tools_authorized
        or "*" in profile.allowed_tools
    )
    if (profile.owner_authorization_required or privileged_capability) and not trusted_operator:
        issues.append(
            {
                "pointer": "/owner_authorization_required",
                "code": "trusted_operator_grant_required",
                "message": "privileged launch authority requires a trusted operator import",
            }
        )
    if "*" in profile.allowed_tools and not profile.authority.unrestricted_tools_authorized:
        issues.append(
            {
                "pointer": "/authority/unrestricted_tools_authorized",
                "code": "authority_mismatch",
                "message": "must explicitly authorize an unrestricted tool policy",
            }
        )
    if privileged_capability and not profile.owner_authorization_required:
        issues.append(
            {
                "pointer": "/owner_authorization_required",
                "code": "owner_boundary_required",
                "message": "privileged capabilities must require owner authorization",
            }
        )
    if issues:
        raise RegistryValidationError(issues)
    return profile


def _validate_provider_document(
    raw: Mapping[str, Any], registry: ProviderAdapterRegistry
) -> ProviderConfiguration:
    try:
        return registry.validate_configuration(raw).artifact
    except ProviderConfigurationError as error:
        raise RegistryValidationError(
            [issue.model_dump(mode="json") for issue in error.issues]
        ) from None


def _provider_revision_dict(row: database.ProviderConfigRevisionModel) -> dict[str, Any]:
    orm_row: Any = row
    return {
        "revision_id": orm_row.id,
        "revision_number": orm_row.revision_number,
        "fingerprint": orm_row.fingerprint,
        "created_by": orm_row.created_by,
        "created_at": orm_row.created_at.isoformat(),
        "document": json.loads(orm_row.document_json),
    }


def _profile_revision_dict(row: database.ProfileRevisionModel) -> dict[str, Any]:
    orm_row: Any = row
    return {
        "revision_id": orm_row.id,
        "revision_number": orm_row.revision_number,
        "fingerprint": orm_row.fingerprint,
        "created_by": orm_row.created_by,
        "created_at": orm_row.created_at.isoformat(),
        "document": json.loads(orm_row.document_json),
    }


def save_provider_configuration(
    raw: Mapping[str, Any],
    *,
    actor: str,
    registry: Optional[ProviderAdapterRegistry] = None,
    built_in: bool = False,
) -> dict[str, Any]:
    database._ensure_control_plane_schema()
    adapter_registry = registry or build_provider_adapter_registry()
    artifact = _validate_provider_document(raw, adapter_registry)
    serialized = _canonical_json(artifact.model_dump(mode="json"))
    fingerprint = _fingerprint(serialized)
    for _attempt in range(2):
        with database.SessionLocal() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            record = cast(Any, db.get(database.ProviderConfigRecordModel, artifact.config_id))
            if record is not None:
                existing = (
                    db.query(database.ProviderConfigRevisionModel)
                    .filter_by(config_id=artifact.config_id, fingerprint=fingerprint)
                    .first()
                )
                if existing is not None:
                    if record.active_revision_id != existing.id:
                        record.active_revision_id = existing.id
                        record.enabled = artifact.enabled
                        record.updated_at = datetime.now()
                        db.commit()
                    return _provider_revision_dict(existing)
                revision_number = (
                    int(
                        db.query(func.max(database.ProviderConfigRevisionModel.revision_number))
                        .filter_by(config_id=artifact.config_id)
                        .scalar()
                        or 0
                    )
                    + 1
                )
            else:
                revision_number = 1
            revision = database.ProviderConfigRevisionModel(
                id=str(uuid.uuid4()),
                config_id=artifact.config_id,
                revision_number=revision_number,
                document_json=serialized,
                fingerprint=fingerprint,
                created_by=actor,
                created_at=datetime.now(),
            )
            db.add(revision)
            if record is None:
                record = database.ProviderConfigRecordModel(
                    config_id=artifact.config_id,
                    adapter_id=artifact.adapter_id,
                    display_name=artifact.display_name,
                    active_revision_id=revision.id,
                    enabled=artifact.enabled,
                    built_in=built_in,
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                )
                db.add(record)
            else:
                if record.adapter_id != artifact.adapter_id:
                    raise RegistryConflictError("provider config adapter identity is immutable")
                record.display_name = artifact.display_name
                record.active_revision_id = revision.id
                record.enabled = artifact.enabled
                record.updated_at = datetime.now()
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                continue
            return _provider_revision_dict(revision)
    raise RegistryConflictError("concurrent provider configuration update did not converge")


def get_provider_configuration(config_id: str) -> dict[str, Any]:
    database._ensure_control_plane_schema()
    with database.SessionLocal() as db:
        record = db.get(database.ProviderConfigRecordModel, config_id)
        if record is None:
            raise RegistryNotFoundError(f"provider configuration not found: {config_id}")
        revision = db.get(database.ProviderConfigRevisionModel, record.active_revision_id)
        if revision is None:
            raise RuntimeError("provider configuration active revision is unavailable")
        return {
            "config_id": record.config_id,
            "adapter_id": record.adapter_id,
            "display_name": record.display_name,
            "enabled": bool(record.enabled),
            "built_in": bool(record.built_in),
            **_provider_revision_dict(revision),
        }


def get_provider_configuration_revision(revision_id: str) -> dict[str, Any]:
    """Resolve an immutable revision for restart-safe terminal recreation."""
    database._ensure_control_plane_schema()
    with database.SessionLocal() as db:
        revision = db.get(database.ProviderConfigRevisionModel, revision_id)
        if revision is None:
            raise RegistryNotFoundError(f"provider configuration revision not found: {revision_id}")
        return _provider_revision_dict(revision)


def list_provider_configurations(*, redact_secret_refs: bool = False) -> list[dict[str, Any]]:
    database._ensure_control_plane_schema()
    with database.SessionLocal() as db:
        ids = [
            row[0]
            for row in db.query(database.ProviderConfigRecordModel.config_id)
            .order_by(database.ProviderConfigRecordModel.config_id)
            .all()
        ]
    configurations = [get_provider_configuration(str(config_id)) for config_id in ids]
    if redact_secret_refs:
        for configuration in configurations:
            document = dict(configuration["document"])
            document["secret_refs"] = {key: "configured" for key in document.get("secret_refs", {})}
            configuration["document"] = document
    return configurations


def save_profile(
    raw: Mapping[str, Any],
    *,
    actor: str,
    trusted_operator: bool = False,
    built_in: bool = False,
    duplicate_builtin: bool = False,
) -> dict[str, Any]:
    database._ensure_control_plane_schema()
    source_builtin = False
    raw_profile_id = raw.get("profile_id")
    if isinstance(raw_profile_id, str):
        with database.SessionLocal() as db:
            existing_record = db.get(database.ProfileRecordModel, raw_profile_id)
            source_builtin = bool(existing_record and existing_record.built_in)
        if source_builtin and not built_in and not duplicate_builtin:
            raise RegistryConflictError("built-in profiles are immutable")
    profile = validate_profile_document(
        raw,
        trusted_operator=trusted_operator or built_in,
        trusted_builtin=built_in or source_builtin,
    )
    with database.SessionLocal() as db:
        provider_record = db.get(database.ProviderConfigRecordModel, profile.provider_config_id)
        if provider_record is None:
            raise RegistryValidationError(
                [
                    {
                        "pointer": "/provider_config_id",
                        "code": "not_found",
                        "message": "must reference an installed provider configuration",
                    }
                ]
            )
        current = db.get(database.ProfileRecordModel, profile.profile_id)
        if current is not None and current.built_in and not built_in:
            if not duplicate_builtin:
                raise RegistryConflictError("built-in profiles are immutable")
            raw = dict(profile.model_dump(mode="json"))
            raw["profile_id"] = f"{profile.profile_id}-copy-{uuid.uuid4().hex[:8]}"
            raw["display_name"] = f"{profile.display_name} copy"
            from cli_agent_orchestrator.services.launch_authority import (
                requires_owner_launch_grant,
            )

            if requires_owner_launch_grant(raw):
                raw["owner_authorization_required"] = True
            profile = validate_profile_document(raw, trusted_operator=trusted_operator)
    serialized = _canonical_json(profile.model_dump(mode="json"))
    fingerprint = _fingerprint(serialized)
    for _attempt in range(2):
        with database.SessionLocal() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            record = cast(Any, db.get(database.ProfileRecordModel, profile.profile_id))
            if record is not None:
                if record.built_in and not built_in:
                    raise RegistryConflictError("built-in profiles are immutable")
                existing = (
                    db.query(database.ProfileRevisionModel)
                    .filter_by(profile_id=profile.profile_id, fingerprint=fingerprint)
                    .first()
                )
                if existing is not None:
                    if record.active_revision_id != existing.id:
                        record.active_revision_id = existing.id
                        record.enabled = profile.enabled
                    if built_in:
                        record.built_in = True
                    record.display_name = profile.display_name
                    record.description = profile.description
                    record.updated_at = datetime.now()
                    db.commit()
                    return _profile_revision_dict(existing)
                revision_number = (
                    int(
                        db.query(func.max(database.ProfileRevisionModel.revision_number))
                        .filter_by(profile_id=profile.profile_id)
                        .scalar()
                        or 0
                    )
                    + 1
                )
            else:
                revision_number = 1
            revision = database.ProfileRevisionModel(
                id=str(uuid.uuid4()),
                profile_id=profile.profile_id,
                revision_number=revision_number,
                document_json=serialized,
                fingerprint=fingerprint,
                created_by=actor,
                created_at=datetime.now(),
            )
            db.add(revision)
            if record is None:
                record = database.ProfileRecordModel(
                    profile_id=profile.profile_id,
                    display_name=profile.display_name,
                    description=profile.description,
                    active_revision_id=revision.id,
                    enabled=profile.enabled,
                    built_in=built_in,
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                )
                db.add(record)
            else:
                if built_in:
                    record.built_in = True
                record.display_name = profile.display_name
                record.description = profile.description
                record.active_revision_id = revision.id
                record.enabled = profile.enabled
                record.updated_at = datetime.now()
            for reference in profile.mcp_server_refs:
                db.add(
                    database.ProfileReferenceModel(
                        profile_revision_id=revision.id,
                        reference_kind="mcp_server",
                        reference_id=reference,
                    )
                )
            db.add(
                database.ProfileReferenceModel(
                    profile_revision_id=revision.id,
                    reference_kind="provider_config",
                    reference_id=profile.provider_config_id,
                )
            )
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                continue
            return _profile_revision_dict(revision)
    raise RegistryConflictError("concurrent profile update did not converge")


def get_profile(profile_id: str) -> dict[str, Any]:
    database._ensure_control_plane_schema()
    with database.SessionLocal() as db:
        record = cast(Any, db.get(database.ProfileRecordModel, profile_id))
        if record is None:
            raise RegistryNotFoundError(f"profile not found: {profile_id}")
        revision = cast(Any, db.get(database.ProfileRevisionModel, record.active_revision_id))
        if revision is None:
            raise RuntimeError("profile active revision is unavailable")
        document = json.loads(revision.document_json)
        from cli_agent_orchestrator.services.launch_authority import requires_owner_launch_grant

        return {
            "profile_id": record.profile_id,
            "name": record.profile_id,
            "display_name": record.display_name,
            "description": record.description,
            "source": "built-in" if record.built_in else "custom",
            "enabled": bool(record.enabled),
            "built_in": bool(record.built_in),
            **_profile_revision_dict(revision),
            "execution_mode": document.get("execution_mode"),
            "owner_authorization_required": requires_owner_launch_grant(
                document, trusted_builtin=bool(record.built_in)
            ),
        }


def list_profiles(*, include_disabled: bool = False) -> list[dict[str, Any]]:
    database._ensure_control_plane_schema()
    with database.SessionLocal() as db:
        query = db.query(database.ProfileRecordModel.profile_id)
        if not include_disabled:
            query = query.filter(database.ProfileRecordModel.enabled.is_(True))
        ids = [row[0] for row in query.order_by(database.ProfileRecordModel.profile_id).all()]
    return [get_profile(str(profile_id)) for profile_id in ids]


def set_profile_enabled(profile_id: str, enabled: bool, *, actor: str) -> dict[str, Any]:
    database._ensure_control_plane_schema()
    with database.SessionLocal() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        record = cast(Any, db.get(database.ProfileRecordModel, profile_id))
        if record is None:
            raise RegistryNotFoundError(f"profile not found: {profile_id}")
        if record.built_in and not enabled:
            raise RegistryConflictError("built-in profiles cannot be disabled")
        record.enabled = enabled
        record.updated_at = datetime.now()
        db.commit()
    return get_profile(profile_id)


def _profile_from_legacy(profile: AgentProfile) -> ProfileDefinition:
    provider_id = profile.provider or "codex"
    mcp_refs = list(profile.mcpServers or {})
    allowed = resolve_allowed_tools(profile.allowedTools, profile.role, mcp_refs)
    codex = profile.codexConfig or {}
    reasoning = codex.get("model_reasoning_effort")
    if reasoning not in {"minimal", "low", "medium", "high", "xhigh"}:
        reasoning = None
    role = (
        profile.role
        if profile.role in {"supervisor", "developer", "reviewer", "worker"}
        else "worker"
    )
    mode = profile.execution_mode
    if mode is None:
        mode = (
            "orchestrator"
            if role == "supervisor"
            else ("reviewer" if role == "reviewer" else "executor")
        )
    unrestricted = "*" in allowed
    timeouts: dict[str, int] = {}
    for server in (profile.mcpServers or {}).values():
        if not isinstance(server, Mapping):
            continue
        timeout = server.get("tool_timeout_sec")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            continue
        timeout_value = int(timeout)
        if timeout_value == timeout and 1 <= timeout_value <= 86_400:
            timeouts["mcp_tool"] = timeout_value
    return ProfileDefinition(
        profile_id=profile.name,
        display_name=profile.name,
        description=profile.description or f"Imported profile {profile.name}",
        provider_config_id=f"builtin-{provider_id}",
        role=cast(Any, role),
        execution_mode=mode,
        enabled=True,
        owner_authorization_required=profile.owner_authorization_required,
        authority=ProfileAuthority(
            approval_policy=codex.get("approval_policy", "on-request"),
            sandbox_mode=codex.get("sandbox_mode", "workspace-write"),
            unrestricted_tools_authorized=unrestricted,
        ),
        model=profile.model,
        reasoning_level=reasoning,
        allowed_tools=allowed,
        mcp_server_refs=mcp_refs,
        timeouts=timeouts,
        instructions=profile.system_prompt or profile.prompt or f"You are {profile.name}.",
    )


def resolve_launch(profile_id: str, fallback_provider: str) -> LaunchResolution:
    if not registry_is_initialized():
        initialize_control_plane_registries()
    profile_record = get_profile(profile_id)
    if not profile_record["enabled"]:
        raise RegistryConflictError("profile is disabled")
    # Registry revisions have already crossed either the packaged-built-in or
    # authenticated import boundary. Revalidate their structure while keeping
    # the server-owned launch policy separate below.
    definition = validate_profile_document(
        profile_record["document"],
        trusted_operator=True,
        trusted_builtin=bool(profile_record["built_in"]),
    )
    provider_record = get_provider_configuration(definition.provider_config_id)
    if not provider_record["enabled"]:
        raise RegistryConflictError("provider configuration is disabled")
    provider_document = dict(provider_record["document"])
    adapter_id = str(provider_document.get("adapter_id") or fallback_provider)
    mcp_servers = resolve_trusted_mcp_server_refs(definition.mcp_server_refs)
    if "mcp_tool" in definition.timeouts:
        for server in mcp_servers.values():
            server["tool_timeout_sec"] = float(definition.timeouts["mcp_tool"])
    codex_config: dict[str, Any] = {
        "approval_policy": definition.authority.approval_policy,
        "sandbox_mode": definition.authority.sandbox_mode,
    }
    if definition.reasoning_level:
        codex_config["model_reasoning_effort"] = definition.reasoning_level
    runtime_profile = AgentProfile(
        name=definition.profile_id,
        description=definition.description,
        provider=adapter_id,
        system_prompt=definition.instructions,
        role=definition.role,
        execution_mode=definition.execution_mode,
        owner_authorization_required=definition.owner_authorization_required,
        mcpServers=mcp_servers or None,
        allowedTools=list(definition.allowed_tools),
        model=definition.model,
        codexConfig=codex_config,
    )
    from cli_agent_orchestrator.services.launch_authority import requires_owner_launch_grant

    owner_grant_required = requires_owner_launch_grant(
        definition, trusted_builtin=bool(profile_record["built_in"])
    )
    snapshot = {
        "schema_version": 1,
        "profile_id": definition.profile_id,
        "profile_revision_id": profile_record["revision_id"],
        "profile_fingerprint": profile_record["fingerprint"],
        "provider_config_id": definition.provider_config_id,
        "provider_config_revision_id": provider_record["revision_id"],
        "provider_config_fingerprint": provider_record["fingerprint"],
        "adapter_id": adapter_id,
        "model": definition.model,
        "reasoning_level": definition.reasoning_level,
        "tools": list(definition.allowed_tools),
        "mcp_server_refs": list(definition.mcp_server_refs),
        "authority": {
            **definition.authority.model_dump(mode="json"),
            "owner_authorization_required": definition.owner_authorization_required,
            "execution_mode": definition.execution_mode,
        },
        "launch_policy": {"owner_grant_required": owner_grant_required},
        "timeouts": dict(definition.timeouts),
        "instructions": definition.instructions,
    }
    return LaunchResolution(
        profile=runtime_profile,
        profile_revision_id=str(profile_record["revision_id"]),
        provider_configuration=provider_document,
        provider_config_revision_id=str(provider_record["revision_id"]),
        provider_adapter_id=adapter_id,
        owner_grant_required=owner_grant_required,
        snapshot=snapshot,
    )


def initialize_control_plane_registries(
    registry: Optional[ProviderAdapterRegistry] = None,
) -> None:
    """Seed installed adapters and packaged profiles into canonical SQLite state."""
    global _registry_engine_identity, _registry_initialized
    database._ensure_control_plane_schema()
    if registry_is_initialized():
        return
    adapter_registry = registry or build_provider_adapter_registry()
    for manifest in adapter_registry.manifests():
        raw = default_provider_configuration(manifest.adapter_id, manifest.display_name)
        save_provider_configuration(
            raw,
            actor="packaged_builtin",
            registry=adapter_registry,
            built_in=True,
        )
    store = resources.files("cli_agent_orchestrator.agent_store")
    from cli_agent_orchestrator.utils.agent_profiles import parse_agent_profile_text

    for item in sorted(store.iterdir(), key=lambda entry: entry.name):
        if not item.name.endswith(".md"):
            continue
        profile_id = item.name[:-3]
        legacy = parse_agent_profile_text(item.read_text(encoding="utf-8"), profile_id)
        definition = _profile_from_legacy(legacy)
        save_profile(
            definition.model_dump(mode="json"),
            actor="packaged_builtin",
            trusted_operator=True,
            built_in=True,
        )
    imported = 0
    skipped = 0
    with database.SessionLocal() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        legacy_import_required = (
            db.get(database.MigrationReceiptModel, PROFILE_BOOTSTRAP_RECEIPT) is None
        )
        if legacy_import_required:
            db.add(
                database.MigrationReceiptModel(
                    name=PROFILE_BOOTSTRAP_RECEIPT,
                    schema_version=1,
                    detail_json=_canonical_json({"status": "legacy_import_claimed"}),
                    created_at=datetime.now(),
                )
            )
        db.commit()
    from pathlib import Path

    from cli_agent_orchestrator.constants import LOCAL_AGENT_STORE_DIR
    from cli_agent_orchestrator.services.settings_service import (
        get_agent_dirs,
        get_extra_agent_dirs,
    )

    if legacy_import_required:
        candidates: dict[str, Path] = {}
        directories = [
            LOCAL_AGENT_STORE_DIR,
            *(Path(value) for value in get_agent_dirs().values()),
            *(Path(value) for value in get_extra_agent_dirs()),
        ]
        for directory in directories:
            if not directory.is_dir():
                continue
            for path in sorted(directory.iterdir(), key=lambda entry: entry.name):
                if path.is_file() and path.suffix == ".md":
                    candidates.setdefault(path.stem, path)
                elif path.is_dir() and (path / "agent.md").is_file():
                    candidates.setdefault(path.name, path / "agent.md")
        packaged_ids = {item.name[:-3] for item in store.iterdir() if item.name.endswith(".md")}
        for profile_id, path in candidates.items():
            if profile_id in packaged_ids:
                continue
            try:
                legacy = parse_agent_profile_text(path.read_text(encoding="utf-8"), profile_id)
                definition = _profile_from_legacy(legacy)
                save_profile(
                    definition.model_dump(mode="json"),
                    actor="legacy_file_migration",
                    trusted_operator=False,
                    built_in=False,
                )
                imported += 1
            except Exception as error:
                skipped += 1
                logger.warning(
                    "Skipped unsafe or invalid legacy profile %s (%s)",
                    profile_id,
                    type(error).__name__,
                )
    now = datetime.now()
    with database.SessionLocal() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        if db.get(database.MigrationReceiptModel, PROFILE_INVENTORY_RECEIPT) is None:
            packaged_inventory_ids = sorted(
                item.name[:-3] for item in store.iterdir() if item.name.endswith(".md")
            )
            db.add(
                database.MigrationReceiptModel(
                    name=PROFILE_INVENTORY_RECEIPT,
                    schema_version=2,
                    detail_json=_canonical_json(
                        {
                            "status": "seeded",
                            "built_in_count": len(packaged_inventory_ids),
                            "built_in_ids": packaged_inventory_ids,
                        }
                    ),
                    created_at=now,
                )
            )
        if db.get(database.MigrationReceiptModel, PROVIDER_BOOTSTRAP_RECEIPT) is None:
            db.add(
                database.MigrationReceiptModel(
                    name=PROVIDER_BOOTSTRAP_RECEIPT,
                    schema_version=1,
                    detail_json=_canonical_json({"status": "seeded"}),
                    created_at=now,
                )
            )
        if legacy_import_required:
            receipt = cast(Any, db.get(database.MigrationReceiptModel, PROFILE_BOOTSTRAP_RECEIPT))
            if receipt is None:
                raise RuntimeError("profile bootstrap claim disappeared")
            receipt.detail_json = _canonical_json(
                {
                    "status": "seeded",
                    "legacy_imported": imported,
                    "legacy_skipped": skipped,
                }
            )
        db.commit()
    _registry_initialized = True
    _registry_engine_identity = id(database.engine)
