import hashlib
import threading

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.database import (
    Base,
    CapacitySettingsAuditModel,
    MigrationReceiptModel,
    OperatorSessionModel,
    OwnerGrantRejected,
    OwnerLaunchGrantModel,
    TerminalModel,
)


@pytest.fixture
def control_plane_db(tmp_path, monkeypatch):
    db_engine = create_engine(
        f"sqlite:///{tmp_path / 'control-plane.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    Base.metadata.create_all(bind=db_engine)
    sessions = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(database, "engine", db_engine)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    yield sessions
    db_engine.dispose()


def _legacy(**overrides):
    values = {
        "max_resident_supervisors": 5,
        "max_provider_executions": 3,
        "max_work_contexts": 2,
        "max_heavy_execution_slots": 1,
    }
    values.update(overrides)
    return values


def test_capacity_migration_seeds_legacy_once_with_audit_and_receipt(control_plane_db):
    first = database.ensure_capacity_settings(_legacy())
    second = database.ensure_capacity_settings(_legacy(max_provider_executions=9))

    assert first["max_provider_executions"] == 3
    assert second["max_provider_executions"] == 3
    with control_plane_db() as db:
        assert db.query(CapacitySettingsAuditModel).count() == 1
        receipt = db.get(MigrationReceiptModel, database.CAPACITY_MIGRATION_RECEIPT)
        assert receipt is not None
        assert receipt.schema_version == 1


def test_control_plane_upgrade_from_baseline_is_additive_and_idempotent(tmp_path, monkeypatch):
    db_engine = create_engine(f"sqlite:///{tmp_path / 'baseline.db'}")
    with db_engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE terminals ("
            "id TEXT PRIMARY KEY, tmux_session TEXT NOT NULL, tmux_window TEXT NOT NULL, "
            "provider TEXT NOT NULL, agent_profile TEXT NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO terminals VALUES "
            "('legacy-terminal', 'legacy-session', 'legacy-window', 'codex', 'developer')"
        )
    sessions = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(database, "engine", db_engine)
    monkeypatch.setattr(database, "SessionLocal", sessions)
    monkeypatch.setattr(database, "_control_plane_schema_ready", False)
    monkeypatch.setattr(database, "_control_plane_schema_engine_identity", None)

    database._ensure_control_plane_schema()
    database._ensure_control_plane_schema()

    with db_engine.begin() as connection:
        legacy = connection.exec_driver_sql(
            "SELECT id, profile_revision_id, provider_config_revision_id, "
            "launch_snapshot_json, launch_snapshot_status FROM terminals"
        ).one()
        receipts = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM migration_receipts WHERE name = ?",
            (database.CONTROL_PLANE_MIGRATION_RECEIPT,),
        ).scalar_one()
        schema_version = connection.exec_driver_sql(
            "SELECT schema_version FROM control_plane_schema WHERE id = 1"
        ).scalar_one()

    assert legacy == ("legacy-terminal", None, None, None, "legacy_unavailable")
    assert receipts == 1
    assert schema_version == database.CONTROL_PLANE_SCHEMA_VERSION
    db_engine.dispose()


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_resident_supervisors", True),
        ("max_resident_supervisors", 1),
        ("max_provider_executions", 51),
        ("max_work_contexts", 0),
        ("max_heavy_execution_slots", 1.5),
    ],
)
def test_capacity_validation_rejects_bools_non_integers_and_out_of_range(
    control_plane_db, field, value
):
    payload = _legacy()
    payload[field] = value
    with pytest.raises(ValueError):
        database.validate_capacity_settings(payload)


def test_capacity_update_and_provider_count_share_one_transaction_boundary(control_plane_db):
    database.ensure_capacity_settings(_legacy())
    with control_plane_db() as db:
        for index in range(5):
            db.add(
                TerminalModel(
                    id=f"term-{index}",
                    tmux_session="capacity",
                    tmux_window=f"window-{index}",
                    provider="codex",
                    agent_profile="supervisor",
                    context_role="supervisor",
                    runtime_lifecycle="running",
                )
            )
        db.commit()

    assert database.acquire_provider_execution("term-0", 100)
    assert database.acquire_provider_execution("term-1", 101)
    barrier = threading.Barrier(2)
    outcomes = {}

    def lower_limit():
        barrier.wait()
        database.update_capacity_settings(_legacy(max_provider_executions=1), actor="test-operator")

    def contend():
        barrier.wait()
        outcomes["admitted"] = database.acquire_provider_execution("term-2", 102)

    threads = [threading.Thread(target=lower_limit), threading.Thread(target=contend)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)

    assert all(not thread.is_alive() for thread in threads)
    assert database.get_capacity_settings()["max_provider_executions"] == 1
    # Existing work survives either serialization order; every later admission is fenced.
    assert database.acquire_provider_execution("term-3", 103) is False
    assert len(database.list_provider_execution_leases()) in {2, 3}


def test_owner_grant_is_digest_only_scoped_and_consumed_with_terminal_metadata(
    control_plane_db, tmp_path
):
    worktree = str(tmp_path.resolve())
    token = database.issue_owner_launch_grant(
        launch_id="manual-launch-1",
        agent_profile="critical_sol_xhigh_owner",
        provider="codex",
        canonical_worktree=worktree,
        requested_session_name=None,
    )
    assert database.validate_owner_launch_grant(
        token,
        launch_id="manual-launch-1",
        agent_profile="critical_sol_xhigh_owner",
        provider="codex",
        canonical_worktree=worktree,
        requested_session_name=None,
    )
    assert not database.validate_owner_launch_grant(
        token,
        launch_id="different-launch",
        agent_profile="critical_sol_xhigh_owner",
        provider="codex",
        canonical_worktree=worktree,
        requested_session_name=None,
    )
    assert not database.validate_owner_launch_grant(
        "OWNER_GATE: APPROVED_XHIGH",
        launch_id="manual-launch-1",
        agent_profile="critical_sol_xhigh_owner",
        provider="codex",
        canonical_worktree=worktree,
        requested_session_name=None,
    )

    created = database.create_terminal(
        "owner-terminal",
        "cao-owner",
        "owner-window",
        "codex",
        "critical_sol_xhigh_owner",
        launch_worktree=worktree,
        write_enabled=False,
        context_role="supervisor",
        privileged_launch=True,
        owner_grant_token=token,
        owner_grant_launch_id="manual-launch-1",
    )
    with control_plane_db() as db:
        grant = db.query(OwnerLaunchGrantModel).one()
        terminal = db.get(TerminalModel, "owner-terminal")
        assert grant.token_sha256 == hashlib.sha256(token.encode()).hexdigest()
        assert token not in " ".join(
            str(value)
            for value in (
                grant.id,
                grant.token_sha256,
                grant.launch_id,
                grant.agent_profile,
                grant.provider,
                grant.canonical_worktree,
                grant.issued_by,
            )
        )
        assert grant.consumed_terminal_id == "owner-terminal"
        assert terminal.owner_grant_id == grant.id
        assert created["id"] == "owner-terminal"

    with pytest.raises(OwnerGrantRejected):
        database.create_terminal(
            "replay-terminal",
            "cao-owner",
            "replay-window",
            "codex",
            "critical_sol_xhigh_owner",
            launch_worktree=worktree,
            write_enabled=False,
            context_role="supervisor",
            privileged_launch=True,
            owner_grant_token=token,
            owner_grant_launch_id="manual-launch-1",
        )


def test_owner_grant_authorizes_one_exact_existing_session_add_without_secret_metadata(
    control_plane_db, tmp_path
):
    worktree = str(tmp_path.resolve())
    scope = {
        "profile_revision_id": "profile-revision",
        "provider_config_revision_id": "provider-revision",
        "project_id": None,
        "launch_mode": "existing_session",
        "delegation_depth": 0,
    }
    database.create_terminal(
        "ordinary-terminal",
        "cao-existing",
        "ordinary-window",
        "codex",
        "developer",
        launch_worktree=worktree,
        write_enabled=False,
        context_role="work",
    )
    token = database.issue_owner_launch_grant(
        launch_id="existing-add-launch",
        agent_profile="critical_sol_xhigh_owner",
        provider="codex",
        canonical_worktree=worktree,
        requested_session_name="cao-existing",
        grant_scope=scope,
    )

    created = database.create_terminal(
        "owner-add-terminal",
        "cao-existing",
        "owner-window",
        "codex",
        "critical_sol_xhigh_owner",
        launch_worktree=worktree,
        write_enabled=False,
        context_role="work",
        privileged_launch=True,
        owner_grant_token=token,
        owner_grant_launch_id="existing-add-launch",
        owner_grant_requested_session_name="cao-existing",
        owner_grant_scope=scope,
        owner_grant_canonical_worktree=worktree,
    )

    assert created["id"] == "owner-add-terminal"
    assert token not in repr(created)
    with control_plane_db() as db:
        owner = db.get(TerminalModel, "owner-add-terminal")
        ordinary = db.get(TerminalModel, "ordinary-terminal")
        grant = db.query(OwnerLaunchGrantModel).filter_by(launch_id="existing-add-launch").one()
        assert owner.session_id == ordinary.session_id
        assert owner.owner_grant_id == grant.id
        assert grant.consumed_terminal_id == owner.id
        assert token not in repr(owner.__dict__)


def test_operator_session_is_short_lived_digest_only_and_revocable(control_plane_db):
    token = database.create_operator_session(ttl_seconds=30)
    session_id = database.authenticate_operator_session(token)

    assert session_id is not None
    with control_plane_db() as db:
        row = db.get(OperatorSessionModel, session_id)
        assert row.token_sha256 == hashlib.sha256(token.encode()).hexdigest()
        assert token not in row.token_sha256
    assert database.revoke_operator_session(token)
    assert database.authenticate_operator_session(token) is None


def test_residency_reconciliation_uses_session_topology_and_child_parentage(
    control_plane_db,
):
    roots = (
        ("terra-root", "supervisor_terra_medium"),
        ("sol-root", "supervisor_sol_medium"),
        ("owner-root", "critical_sol_xhigh_owner"),
    )
    for terminal_id, profile in roots:
        database.create_terminal(
            terminal_id,
            f"cao-{terminal_id}",
            f"window-{terminal_id}",
            "codex",
            profile,
            context_role="work",
        )
    for child_id, profile in (("worker", "developer"), ("reviewer", "reviewer")):
        database.create_terminal(
            child_id,
            "cao-owner-root",
            f"window-{child_id}",
            "codex",
            profile,
            context_role="supervisor",
        )
        assert database.register_child_assignment("owner-root", child_id)

    assert database.reconcile_terminal_context_roles_by_topology(dry_run=True) == 5
    assert database.reconcile_terminal_context_roles_by_topology() == 5
    assert database.reconcile_terminal_context_roles_by_topology() == 0

    with control_plane_db() as db:
        roles = {
            str(row.id): str(row.context_role)
            for row in db.query(TerminalModel).order_by(TerminalModel.id).all()
        }
    assert roles == {
        "owner-root": "supervisor",
        "reviewer": "work",
        "sol-root": "supervisor",
        "terra-root": "supervisor",
        "worker": "work",
    }


def test_concurrent_owner_grant_consumption_creates_exactly_one_terminal(
    control_plane_db, tmp_path
):
    worktree = str(tmp_path.resolve())
    token = database.issue_owner_launch_grant(
        launch_id="concurrent-launch",
        agent_profile="critical_sol_xhigh_owner",
        provider="codex",
        canonical_worktree=worktree,
        requested_session_name=None,
    )
    barrier = threading.Barrier(2)
    outcomes = []

    def consume(index):
        barrier.wait()
        try:
            database.create_terminal(
                f"concurrent-{index}",
                "cao-owner",
                f"window-{index}",
                "codex",
                "critical_sol_xhigh_owner",
                launch_worktree=worktree,
                write_enabled=False,
                context_role="supervisor",
                privileged_launch=True,
                owner_grant_token=token,
                owner_grant_launch_id="concurrent-launch",
            )
        except OwnerGrantRejected:
            outcomes.append("rejected")
        else:
            outcomes.append("created")

    threads = [threading.Thread(target=consume, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)

    assert sorted(outcomes) == ["created", "rejected"]
    with control_plane_db() as db:
        assert db.query(TerminalModel).count() == 1
