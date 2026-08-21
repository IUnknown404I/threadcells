import asyncio
import json
import threading

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.database import Base
from cli_agent_orchestrator.providers.contracts import default_provider_configuration
from cli_agent_orchestrator.providers.registry import build_provider_adapter_registry
from cli_agent_orchestrator.services import control_plane_registry as registry_service

EXPECTED_BUILTIN_PROFILE_IDS = {
    "architect_sol_high",
    "code_supervisor",
    "critical_sol_xhigh_owner",
    "developer",
    "developer_sol_medium",
    "developer_terra_high",
    "developer_terra_medium",
    "framer_connect_luna_low",
    "frontend_sol_medium",
    "reviewer",
    "reviewer_sol_high",
    "reviewer_sol_medium",
    "reviewer_terra_high",
    "strategist_sol_medium",
    "supervisor_sol_medium",
    "supervisor_terra_medium",
    "uiux_sol_high",
    "worker_luna_medium",
}


@pytest.fixture
def registry_db(tmp_path, monkeypatch):
    db_engine = create_engine(
        f"sqlite:///{tmp_path / 'registry.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(bind=db_engine)
    sessions = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(database, "engine", db_engine)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setattr(registry_service, "_registry_initialized", False)
    monkeypatch.setattr(registry_service, "_registry_engine_identity", None)
    monkeypatch.setattr(
        "cli_agent_orchestrator.constants.LOCAL_AGENT_STORE_DIR", tmp_path / "missing-store"
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.settings_service.get_agent_dirs", lambda: {}
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.settings_service.get_extra_agent_dirs", lambda: []
    )
    yield sessions
    db_engine.dispose()


def _profile(**overrides):
    document = {
        "schema_version": 1,
        "profile_id": "custom-reviewer",
        "display_name": "Custom reviewer",
        "description": "Reviews a bounded change.",
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
        "allowed_tools": ["@builtin", "fs_read"],
        "mcp_server_refs": [],
        "timeouts": {"turn": 120},
        "instructions": "Review the assigned bounded change.",
    }
    document.update(overrides)
    return document


def test_registry_revisions_are_immutable_and_launch_snapshot_is_exact(registry_db):
    adapters = build_provider_adapter_registry(discover=False)
    registry_service.initialize_control_plane_registries(adapters)
    first = registry_service.save_profile(_profile(), actor="operator", trusted_operator=True)
    second = registry_service.save_profile(
        _profile(instructions="Review the assigned bounded change and report findings."),
        actor="operator",
        trusted_operator=True,
    )

    assert first["revision_number"] == 1
    assert second["revision_number"] == 2
    assert first["revision_id"] != second["revision_id"]
    resolution = registry_service.resolve_launch("custom-reviewer", fallback_provider="codex")
    assert resolution.profile_revision_id == second["revision_id"]
    assert resolution.snapshot["instructions"].endswith("report findings.")
    assert resolution.snapshot["provider_config_revision_id"]

    database.create_terminal(
        "snapshot-terminal",
        "cao-snapshot",
        "reviewer",
        "codex",
        "custom-reviewer",
        context_role="supervisor",
        profile_revision_id=resolution.profile_revision_id,
        provider_config_revision_id=resolution.provider_config_revision_id,
        launch_snapshot=resolution.snapshot,
    )
    metadata = database.get_terminal_metadata("snapshot-terminal")
    assert metadata["launch_snapshot_status"] == "available"
    assert metadata["launch_snapshot"] == resolution.snapshot

    with registry_db() as db:
        revisions = (
            db.query(database.ProfileRevisionModel)
            .filter_by(profile_id="custom-reviewer")
            .order_by(database.ProfileRevisionModel.revision_number)
            .all()
        )
        assert [row.id for row in revisions] == [first["revision_id"], second["revision_id"]]


def test_packaged_bootstrap_restores_complete_accepted_profile_inventory(registry_db):
    registry_service.initialize_control_plane_registries(
        build_provider_adapter_registry(discover=False)
    )

    records = registry_service.list_profiles(include_disabled=True)
    assert {record["profile_id"] for record in records} == EXPECTED_BUILTIN_PROFILE_IDS
    assert len(records) == 18
    assert all(record["built_in"] and record["enabled"] for record in records)

    specialist = registry_service.resolve_launch("developer_sol_medium", fallback_provider="codex")
    assert specialist.profile.model == "gpt-5.6-sol"
    assert specialist.profile.execution_mode == "executor"
    assert specialist.profile.codexConfig["model_reasoning_effort"] == "medium"
    assert specialist.snapshot["timeouts"] == {"mcp_tool": 1800}
    assert specialist.profile.mcpServers["cao-mcp-server"]["tool_timeout_sec"] == 1800.0

    with registry_db() as db:
        receipt = db.get(database.MigrationReceiptModel, registry_service.PROFILE_INVENTORY_RECEIPT)
        detail = json.loads(receipt.detail_json)
        assert detail == {
            "status": "seeded",
            "built_in_count": 18,
            "built_in_ids": sorted(EXPECTED_BUILTIN_PROFILE_IDS),
        }


def test_registry_and_spawn_profile_apis_share_the_exact_canonical_inventory(registry_db):
    registry_service.initialize_control_plane_registries(
        build_provider_adapter_registry(discover=False)
    )
    from cli_agent_orchestrator.api.main import (
        list_agent_profiles_endpoint,
        list_profile_settings_endpoint,
    )

    registry_records = asyncio.run(list_profile_settings_endpoint(include_disabled=True))
    spawn_records = asyncio.run(list_agent_profiles_endpoint())

    assert {record["profile_id"] for record in registry_records} == (EXPECTED_BUILTIN_PROFILE_IDS)
    assert {record["name"] for record in spawn_records} == EXPECTED_BUILTIN_PROFILE_IDS
    assert len(registry_records) == len(spawn_records) == 18
    assert all(record["built_in"] and record["enabled"] for record in spawn_records)
    specialist = next(
        record for record in spawn_records if record["name"] == "developer_sol_medium"
    )
    assert specialist["execution_mode"] == "executor"
    assert specialist["owner_authorization_required"] is False


def test_packaged_inventory_upgrades_after_legacy_receipt_without_rescan(
    registry_db, tmp_path, monkeypatch
):
    with registry_db() as db:
        db.add(
            database.MigrationReceiptModel(
                name=registry_service.PROFILE_BOOTSTRAP_RECEIPT,
                schema_version=1,
                detail_json=json.dumps({"status": "seeded", "legacy_skipped": 12}),
            )
        )
        db.commit()
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    (legacy_dir / "should-not-rescan.md").write_text(
        "---\nname: should-not-rescan\ndescription: not part of the package\n---\nNo rescan.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("cli_agent_orchestrator.constants.LOCAL_AGENT_STORE_DIR", legacy_dir)

    registry_service.initialize_control_plane_registries(
        build_provider_adapter_registry(discover=False)
    )

    assert {item["profile_id"] for item in registry_service.list_profiles()} == (
        EXPECTED_BUILTIN_PROFILE_IDS
    )
    with pytest.raises(registry_service.RegistryNotFoundError):
        registry_service.get_profile("should-not-rescan")


def test_untrusted_profile_import_rejects_executable_capability_and_wildcard(registry_db):
    registry_service.initialize_control_plane_registries(
        build_provider_adapter_registry(discover=False)
    )
    unsafe = _profile(
        allowed_tools=["*"],
        mcp_server_refs=["arbitrary-command-server"],
        authority={
            "approval_policy": "never",
            "sandbox_mode": "danger-full-access",
            "unrestricted_tools_authorized": True,
        },
    )

    with pytest.raises(registry_service.RegistryValidationError) as raised:
        registry_service.save_profile(unsafe, actor="import", trusted_operator=False)

    pointers = {issue["pointer"] for issue in raised.value.issues}
    assert "/allowed_tools" in pointers
    assert "/mcp_server_refs" in pointers
    assert "/authority/unrestricted_tools_authorized" in pointers


@pytest.mark.parametrize(
    "overrides",
    [
        {"execution_mode": "owner_executor", "owner_authorization_required": True},
        {"reasoning_level": "xhigh", "owner_authorization_required": True},
        {
            "authority": {
                "approval_policy": "never",
                "sandbox_mode": "danger-full-access",
                "unrestricted_tools_authorized": False,
            },
            "owner_authorization_required": True,
        },
    ],
)
def test_untrusted_profile_import_rejects_every_privileged_semantic(registry_db, overrides):
    registry_service.initialize_control_plane_registries(
        build_provider_adapter_registry(discover=False)
    )

    with pytest.raises(registry_service.RegistryValidationError) as raised:
        registry_service.save_profile(
            _profile(**overrides), actor="legacy_file_migration", trusted_operator=False
        )

    assert any(issue["code"] == "trusted_operator_grant_required" for issue in raised.value.issues)


def test_custom_privileged_revision_gets_server_owned_launch_policy(registry_db):
    registry_service.initialize_control_plane_registries(
        build_provider_adapter_registry(discover=False)
    )
    created = registry_service.save_profile(
        _profile(
            profile_id="custom-owner",
            role="supervisor",
            execution_mode="owner_executor",
            reasoning_level="xhigh",
            owner_authorization_required=True,
        ),
        actor="operator_session:test",
        trusted_operator=True,
    )

    resolution = registry_service.resolve_launch("custom-owner", fallback_provider="codex")

    assert resolution.profile_revision_id == created["revision_id"]
    assert resolution.owner_grant_required is True
    assert resolution.snapshot["launch_policy"] == {"owner_grant_required": True}
    assert registry_service.get_profile("custom-owner")["owner_authorization_required"] is True


def test_public_provider_projection_redacts_secret_reference_values(registry_db):
    adapters = build_provider_adapter_registry(discover=False)
    registry_service.initialize_control_plane_registries(adapters)
    registry_service.save_provider_configuration(
        {
            "schema_version": 1,
            "config_id": "custom-codex",
            "adapter_id": "codex",
            "display_name": "Custom Codex",
            "enabled": True,
            "settings": {},
            "secret_refs": {"authentication": "secret-store/private-codex"},
        },
        actor="operator_session:test",
        registry=adapters,
    )

    raw = registry_service.list_provider_configurations()
    public = registry_service.list_provider_configurations(redact_secret_refs=True)

    assert next(item for item in raw if item["config_id"] == "custom-codex")["document"][
        "secret_refs"
    ] == {"authentication": "secret-store/private-codex"}
    assert next(item for item in public if item["config_id"] == "custom-codex")["document"][
        "secret_refs"
    ] == {"authentication": "configured"}


def test_legacy_import_is_receipt_gated_and_not_rescanned(registry_db, tmp_path, monkeypatch):
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    monkeypatch.setattr("cli_agent_orchestrator.constants.LOCAL_AGENT_STORE_DIR", legacy_dir)
    adapters = build_provider_adapter_registry(discover=False)
    registry_service.initialize_control_plane_registries(adapters)

    (legacy_dir / "late-owner.md").write_text(
        "---\n"
        "name: late-owner\n"
        "description: unsafe late profile\n"
        "provider: codex\n"
        "role: supervisor\n"
        "execution_mode: owner_executor\n"
        "owner_authorization_required: true\n"
        "codexConfig:\n"
        "  model_reasoning_effort: xhigh\n"
        "  sandbox_mode: danger-full-access\n"
        "---\nLate profile.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(registry_service, "_registry_initialized", False)
    registry_service.initialize_control_plane_registries(adapters)

    with pytest.raises(registry_service.RegistryNotFoundError):
        registry_service.get_profile("late-owner")
    with registry_db() as db:
        receipt = db.get(database.MigrationReceiptModel, registry_service.PROFILE_BOOTSTRAP_RECEIPT)
        assert json.loads(receipt.detail_json)["status"] == "seeded"


def test_builtins_are_immutable_and_duplicate_creates_custom_identity(registry_db):
    registry_service.initialize_control_plane_registries(
        build_provider_adapter_registry(discover=False)
    )
    built_in = registry_service.get_profile("supervisor_sol_medium")["document"]

    with pytest.raises(registry_service.RegistryConflictError):
        registry_service.save_profile(built_in, actor="operator", trusted_operator=True)
    with pytest.raises(registry_service.RegistryConflictError):
        registry_service.set_profile_enabled("supervisor_sol_medium", False, actor="operator")

    duplicate = registry_service.save_profile(
        built_in,
        actor="operator",
        trusted_operator=True,
        duplicate_builtin=True,
    )
    assert duplicate["document"]["profile_id"].startswith("supervisor_sol_medium-copy-")
    assert registry_service.get_profile(duplicate["document"]["profile_id"])["built_in"] is False


def test_concurrent_identical_imports_converge_on_one_revision(registry_db):
    adapters = build_provider_adapter_registry(discover=False)
    registry_service.save_provider_configuration(
        default_provider_configuration("codex", "Codex"),
        actor="packaged_builtin",
        registry=adapters,
        built_in=True,
    )
    barrier = threading.Barrier(2)
    outcomes = []

    def import_profile():
        barrier.wait()
        outcomes.append(
            registry_service.save_profile(
                _profile(), actor="concurrent-import", trusted_operator=True
            )["revision_id"]
        )

    threads = [threading.Thread(target=import_profile) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)

    assert all(not thread.is_alive() for thread in threads)
    assert len(outcomes) == 2
    assert len(set(outcomes)) == 1
    with registry_db() as db:
        assert db.query(database.ProfileRevisionModel).count() == 1
