"""Tests for the database client."""

import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database as database_client
from cli_agent_orchestrator.clients.database import (
    Base,
    ChildAssignmentModel,
    DelegationResultModel,
    FlowModel,
    InboxModel,
    ProviderUsageBindingModel,
    SessionPrimarySupervisorConflict,
    TerminalModel,
    UnreconciledTerminalAuthority,
    WorktreeWriterLeaseConflict,
    WorktreeWriterLeaseModel,
    WritableWorkContextAuditModel,
    WritableWorkContextConflict,
    WritableWorkContextModel,
    _migrate_terminal_worktree_authority_columns,
    bind_terminal_provider_resume_identity,
    create_flow,
    create_inbox_message,
    create_terminal,
    delete_flow,
    delete_terminal,
    delete_terminals_by_session,
    get_flow,
    get_inbox_messages,
    get_pending_messages,
    get_terminal_metadata,
    init_db,
    list_flows,
    list_terminals_by_session,
    reserve_writable_work_context,
    terminal_auth_token_matches,
    transition_writable_work_context,
    update_flow_enabled,
    update_flow_run_times,
    update_last_active,
    update_message_status,
)
from cli_agent_orchestrator.models.inbox import MessageStatus


@pytest.fixture
def test_db():
    """Create an in-memory test database."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    return TestSession


@pytest.fixture(autouse=True)
def _isolate_control_plane_schema_bootstrap(monkeypatch):
    """Keep mocked SessionLocal unit tests out of the real migration boundary."""
    monkeypatch.setattr(database_client, "_control_plane_schema_ready", True)
    monkeypatch.setattr(
        database_client, "_control_plane_schema_engine_identity", id(database_client.engine)
    )


class TestTerminalOperations:
    """Tests for terminal database operations."""

    def test_worktree_authority_migration_is_additive(self, tmp_path, monkeypatch):
        database_file = tmp_path / "legacy.db"
        with sqlite3.connect(database_file) as connection:
            connection.execute("CREATE TABLE terminals (id TEXT PRIMARY KEY)")
            connection.execute("INSERT INTO terminals (id) VALUES ('legacy-owner')")
        monkeypatch.setattr(
            "cli_agent_orchestrator.constants.DATABASE_FILE",
            database_file,
        )

        assert _migrate_terminal_worktree_authority_columns()
        with sqlite3.connect(database_file) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(terminals)")}
            lease_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(worktree_writer_leases)")
            }
            classification = connection.execute(
                "SELECT workspace_classification FROM terminals WHERE id = 'legacy-owner'"
            ).fetchone()[0]
        assert {
            "id",
            "creation_order",
            "launch_worktree",
            "write_enabled",
            "writer_authority_generation",
            "writable_work_context_id",
            "workspace_classification",
            "runtime_lifecycle",
            "runtime_exit_requested_at",
            "runtime_exited_at",
            "runtime_operation_kind",
            "runtime_operation_token",
            "runtime_operation_claimed_at",
            "runtime_operation_expires_at",
            "provider_resume_identity",
            "provider_resume_runtime_generation",
            "recovery_fenced_at",
            "recovery_fenced_reason",
            "recovery_takeover_id",
            "replaced_by_terminal_id",
        } <= columns
        assert {
            "canonical_worktree",
            "terminal_id",
            "authority_generation",
            "created_at",
        } <= lease_columns
        assert classification == "legacy_shared_root"

    def test_runtime_identity_migration_preserves_one_exact_live_codex_binding(
        self, tmp_path, monkeypatch
    ):
        database_file = tmp_path / "legacy-codex-resume.db"
        identity = "01234567-89ab-cdef-0123-456789abcdef"
        with sqlite3.connect(database_file) as connection:
            connection.execute(
                "CREATE TABLE terminals ("
                "id TEXT PRIMARY KEY, provider TEXT, runtime_lifecycle TEXT, "
                "runtime_generation TEXT)"
            )
            connection.execute(
                "CREATE TABLE provider_usage_bindings ("
                "provider TEXT, provider_session_id TEXT, terminal_id TEXT, source TEXT)"
            )
            connection.execute(
                "INSERT INTO terminals VALUES ('managed', 'codex', 'running', 'generation-1')"
            )
            connection.execute(
                "INSERT INTO provider_usage_bindings VALUES (?, ?, ?, ?)",
                ("codex", identity, "managed", "live_process_fd_v1"),
            )
        monkeypatch.setattr("cli_agent_orchestrator.constants.DATABASE_FILE", database_file)

        assert _migrate_terminal_worktree_authority_columns()

        with sqlite3.connect(database_file) as connection:
            bound = connection.execute(
                "SELECT provider_resume_identity, provider_resume_runtime_generation "
                "FROM terminals WHERE id = 'managed'"
            ).fetchone()
        assert bound == (identity, "generation-1")

    def test_runtime_identity_migration_rejects_inexact_or_stale_bindings(
        self, tmp_path, monkeypatch
    ):
        database_file = tmp_path / "legacy-codex-resume-rejected.db"
        identities = {
            "multiple": "01234567-89ab-cdef-0123-456789abcdef",
            "wrong-source": "11234567-89ab-cdef-0123-456789abcdef",
            "exited": "21234567-89ab-cdef-0123-456789abcdef",
            "missing-generation": "31234567-89ab-cdef-0123-456789abcdef",
        }
        with sqlite3.connect(database_file) as connection:
            connection.execute(
                "CREATE TABLE terminals ("
                "id TEXT PRIMARY KEY, provider TEXT, runtime_lifecycle TEXT, "
                "runtime_generation TEXT)"
            )
            connection.execute(
                "CREATE TABLE provider_usage_bindings ("
                "provider TEXT, provider_session_id TEXT, terminal_id TEXT, source TEXT)"
            )
            connection.executemany(
                "INSERT INTO terminals VALUES (?, 'codex', ?, ?)",
                [
                    ("multiple", "running", "generation-1"),
                    ("wrong-source", "running", "generation-1"),
                    ("exited", "exited", "generation-1"),
                    ("missing-generation", "running", None),
                ],
            )
            connection.executemany(
                "INSERT INTO provider_usage_bindings VALUES ('codex', ?, ?, ?)",
                [
                    (identities["multiple"], "multiple", "live_process_fd_v1"),
                    (
                        "fedcba98-7654-3210-fedc-ba9876543210",
                        "multiple",
                        "live_process_fd_v1",
                    ),
                    (identities["wrong-source"], "wrong-source", "capture_birth_cwd_v1"),
                    (identities["exited"], "exited", "live_process_fd_v1"),
                    (
                        identities["missing-generation"],
                        "missing-generation",
                        "live_process_fd_v1",
                    ),
                ],
            )
        monkeypatch.setattr("cli_agent_orchestrator.constants.DATABASE_FILE", database_file)

        assert _migrate_terminal_worktree_authority_columns()

        with sqlite3.connect(database_file) as connection:
            rows = connection.execute(
                "SELECT id, provider_resume_identity, provider_resume_runtime_generation "
                "FROM terminals ORDER BY id"
            ).fetchall()
        assert rows == [
            ("exited", None, None),
            ("missing-generation", None, None),
            ("multiple", None, None),
            ("wrong-source", None, None),
        ]

    def test_worktree_authority_migration_backfills_stable_creation_order(
        self, tmp_path, monkeypatch
    ):
        database_file = tmp_path / "legacy-creation-order.db"
        with sqlite3.connect(database_file) as connection:
            connection.execute("CREATE TABLE terminals (id TEXT PRIMARY KEY)")
            connection.executemany(
                "INSERT INTO terminals (id) VALUES (?)",
                [("z-created-first",), ("a-created-second",), ("m-created-last",)],
            )
        monkeypatch.setattr("cli_agent_orchestrator.constants.DATABASE_FILE", database_file)

        assert _migrate_terminal_worktree_authority_columns()
        with sqlite3.connect(database_file) as connection:
            first_pass = list(
                connection.execute(
                    "SELECT id, creation_order FROM terminals ORDER BY creation_order, id"
                )
            )
        assert [row[0] for row in first_pass] == [
            "z-created-first",
            "a-created-second",
            "m-created-last",
        ]

        assert _migrate_terminal_worktree_authority_columns()
        with sqlite3.connect(database_file) as connection:
            second_pass = list(
                connection.execute(
                    "SELECT id, creation_order FROM terminals ORDER BY creation_order, id"
                )
            )
        assert second_pass == first_pass

    def test_worktree_authority_migration_backfills_one_deterministic_partial_p1_owner(
        self, tmp_path, monkeypatch
    ):
        database_file = tmp_path / "legacy-writers.db"
        with sqlite3.connect(database_file) as connection:
            connection.execute(
                "CREATE TABLE terminals ("
                "id TEXT PRIMARY KEY, launch_worktree TEXT, write_enabled BOOLEAN)"
            )
            connection.executemany(
                "INSERT INTO terminals VALUES (?, ?, 1)",
                [("writer-b", "/worktree"), ("writer-a", "/worktree")],
            )
        monkeypatch.setattr("cli_agent_orchestrator.constants.DATABASE_FILE", database_file)

        assert _migrate_terminal_worktree_authority_columns()
        with sqlite3.connect(database_file) as connection:
            owners = list(
                connection.execute(
                    "SELECT l.canonical_worktree, l.terminal_id, "
                    "l.authority_generation, t.writer_authority_generation "
                    "FROM worktree_writer_leases l JOIN terminals t ON t.id = l.terminal_id"
                )
            )
        assert len(owners) == 1
        assert owners[0][0:2] == ("/worktree", "writer-a")
        assert owners[0][2] == owners[0][3]
        assert len(owners[0][2]) == 32

    def test_pre_p1_live_terminal_migration_fences_new_writer_until_reconciled(
        self, tmp_path, monkeypatch
    ):
        database_file = tmp_path / "pre-p1-live.db"
        with sqlite3.connect(database_file) as connection:
            connection.execute(
                "CREATE TABLE terminals ("
                "id TEXT PRIMARY KEY, tmux_session TEXT NOT NULL, "
                "tmux_window TEXT NOT NULL, provider TEXT NOT NULL, "
                "agent_profile TEXT, allowed_tools TEXT, "
                "auth_token_sha256 TEXT, last_active DATETIME)"
            )
            connection.execute(
                "INSERT INTO terminals "
                "(id, tmux_session, tmux_window, provider, agent_profile) "
                "VALUES ('live-pre-p1', 'cao-live', 'developer', 'codex', 'developer')"
            )
        monkeypatch.setattr("cli_agent_orchestrator.constants.DATABASE_FILE", database_file)

        # Mirror init_db's rolling-upgrade order: create newly introduced
        # tables first, then add columns SQLite cannot add via create_all.
        migrated_engine = create_engine(f"sqlite:///{database_file}")
        migrated_session = sessionmaker(bind=migrated_engine)
        monkeypatch.setattr(database_client, "engine", migrated_engine)
        monkeypatch.setattr(database_client, "SessionLocal", migrated_session)
        monkeypatch.setattr(database_client, "_control_plane_schema_ready", False)
        monkeypatch.setattr(database_client, "_control_plane_schema_engine_identity", None)
        Base.metadata.create_all(bind=migrated_engine)
        assert _migrate_terminal_worktree_authority_columns()
        database_client._ensure_project_schema()
        database_client._ensure_control_plane_schema()
        monkeypatch.setattr(
            "cli_agent_orchestrator.clients.database._terminal_authority_schema_ready", True
        )

        with migrated_session() as db:
            legacy = db.query(TerminalModel).filter_by(id="live-pre-p1").one()
            assert legacy.launch_worktree is None
            assert legacy.write_enabled is None
            assert db.query(WorktreeWriterLeaseModel).count() == 0

        with pytest.raises(UnreconciledTerminalAuthority, match="live-pre-p1"):
            create_terminal(
                "new-writer",
                "cao-new",
                "developer",
                "codex",
                launch_worktree="/srv/worktree",
                write_enabled=True,
            )

        assert delete_terminal("live-pre-p1")
        create_terminal(
            "new-writer",
            "cao-new",
            "developer",
            "codex",
            launch_worktree="/srv/worktree",
            write_enabled=True,
        )

    @patch("cli_agent_orchestrator.clients.database.SessionLocal")
    def test_create_terminal(self, mock_session_class):
        """Test creating a terminal record."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session_class.return_value = mock_session
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
            None
        )

        result = create_terminal(
            "test123",
            "cao-session",
            "window-0",
            "kiro_cli",
            "developer",
            launch_worktree="/srv/worktree",
            write_enabled=True,
        )

        assert result["id"] == "test123"
        assert result["launch_worktree"] == "/srv/worktree"
        assert result["write_enabled"] is True
        persisted, lease = [call.args[0] for call in mock_session.add.call_args_list]
        assert persisted.launch_worktree == "/srv/worktree"
        assert persisted.write_enabled is True
        assert lease.canonical_worktree == "/srv/worktree"
        assert lease.terminal_id == "test123"
        mock_session.commit.assert_called_once()

    def test_provider_resume_identity_binds_once_at_exact_ready_generation(
        self, test_db, monkeypatch
    ):
        identity = "01234567-89ab-cdef-0123-456789abcdef"
        monkeypatch.setattr(database_client, "SessionLocal", test_db)
        monkeypatch.setattr(database_client, "_terminal_authority_schema_ready", True)
        monkeypatch.setattr(database_client, "_usage_schema_ready", True)
        with test_db() as db:
            db.add(
                TerminalModel(
                    id="managed",
                    tmux_session="cao-managed",
                    tmux_window="managed",
                    provider="codex",
                    runtime_lifecycle="starting",
                    runtime_generation="generation-1",
                )
            )
            db.commit()

        assert bind_terminal_provider_resume_identity(
            "managed",
            provider="codex",
            resume_identity=identity,
            runtime_generation="generation-1",
        )
        assert bind_terminal_provider_resume_identity(
            "managed",
            provider="codex",
            resume_identity=identity,
            runtime_generation="generation-1",
            require_existing_binding=True,
        )
        assert not bind_terminal_provider_resume_identity(
            "managed",
            provider="codex",
            resume_identity="fedcba98-7654-3210-fedc-ba9876543210",
            runtime_generation="generation-1",
        )
        assert not bind_terminal_provider_resume_identity(
            "managed",
            provider="codex",
            resume_identity=identity,
            runtime_generation="generation-stale",
        )
        metadata = get_terminal_metadata("managed")
        assert metadata is not None
        assert metadata["provider_resume_identity"] == identity
        assert metadata["provider_resume_runtime_generation"] == "generation-1"
        with test_db() as db:
            terminal = db.get(TerminalModel, "managed")
            assert terminal.provider_resume_identity == identity
            assert terminal.provider_resume_runtime_generation == "generation-1"
            bindings = db.query(ProviderUsageBindingModel).all()
            assert [(row.provider_session_id, row.terminal_id, row.source) for row in bindings] == [
                (identity, "managed", "managed_runtime_ready_v1")
            ]

    def test_provider_resume_identity_existing_only_cannot_introduce_identity(
        self, test_db, monkeypatch
    ):
        identity = "01234567-89ab-cdef-0123-456789abcdef"
        monkeypatch.setattr(database_client, "SessionLocal", test_db)
        monkeypatch.setattr(database_client, "_terminal_authority_schema_ready", True)
        monkeypatch.setattr(database_client, "_usage_schema_ready", True)
        with test_db() as db:
            db.add(
                TerminalModel(
                    id="managed-unbound",
                    tmux_session="cao-managed",
                    tmux_window="managed",
                    provider="codex",
                    runtime_lifecycle="running",
                    runtime_generation="generation-1",
                )
            )
            db.commit()

        assert not bind_terminal_provider_resume_identity(
            "managed-unbound",
            provider="codex",
            resume_identity=identity,
            runtime_generation="generation-1",
            require_existing_binding=True,
        )
        with test_db() as db:
            terminal = db.get(TerminalModel, "managed-unbound")
            assert terminal.provider_resume_identity is None
            assert db.query(ProviderUsageBindingModel).count() == 0

    @pytest.mark.parametrize("corruption", [None, "writer_lease", "work_context"])
    def test_provider_resume_identity_requires_exact_managed_worktree_authority(
        self, test_db, monkeypatch, corruption
    ):
        identity = "01234567-89ab-cdef-0123-456789abcdef"
        authority = "writer-generation"
        monkeypatch.setattr(database_client, "SessionLocal", test_db)
        monkeypatch.setattr(database_client, "_terminal_authority_schema_ready", True)
        monkeypatch.setattr(database_client, "_usage_schema_ready", True)
        with test_db() as db:
            db.add(
                WritableWorkContextModel(
                    id="managed-context",
                    request_id="request-managed-context",
                    project_id="project-managed",
                    session_id="session-managed",
                    terminal_id="managed-authority",
                    canonical_source="/source/project",
                    canonical_worktree=(
                        "/managed/other" if corruption == "work_context" else "/managed/context"
                    ),
                    branch="cao/session/managed-authority",
                    base_revision="a" * 40,
                    state="admitted",
                    writer_authority_generation=authority,
                )
            )
            db.add(
                TerminalModel(
                    id="managed-authority",
                    tmux_session="cao-managed",
                    session_id="session-managed",
                    tmux_window="managed",
                    provider="codex",
                    write_enabled=True,
                    writer_authority_generation=authority,
                    managed_worktree_kind="supervisor",
                    managed_worktree_source="/source/project",
                    managed_worktree_branch="cao/session/managed-authority",
                    managed_worktree_commit="a" * 40,
                    launch_worktree="/managed/context",
                    writable_work_context_id="managed-context",
                    project_id="project-managed",
                    runtime_lifecycle="running",
                    runtime_generation="generation-1",
                )
            )
            db.add(
                WorktreeWriterLeaseModel(
                    canonical_worktree="/managed/context",
                    terminal_id="managed-authority",
                    authority_generation=(
                        "stale-writer-generation" if corruption == "writer_lease" else authority
                    ),
                )
            )
            db.commit()

        accepted = bind_terminal_provider_resume_identity(
            "managed-authority",
            provider="codex",
            resume_identity=identity,
            runtime_generation="generation-1",
        )
        assert accepted is (corruption is None)
        with test_db() as db:
            terminal = db.get(TerminalModel, "managed-authority")
            assert terminal.provider_resume_identity == (identity if accepted else None)
            assert db.query(ProviderUsageBindingModel).count() == (1 if accepted else 0)

    def test_concurrent_provider_identity_binding_selects_one_identity(self, tmp_path, monkeypatch):
        engine = create_engine(
            f"sqlite:///{tmp_path / 'provider-identity.db'}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=engine)
        sessions = sessionmaker(bind=engine)
        monkeypatch.setattr(database_client, "SessionLocal", sessions)
        monkeypatch.setattr(
            database_client, "_ensure_terminal_worktree_authority_schema", lambda: None
        )
        monkeypatch.setattr(database_client, "_ensure_usage_schema", lambda: None)
        identities = (
            "01234567-89ab-cdef-0123-456789abcdef",
            "fedcba98-7654-3210-fedc-ba9876543210",
        )
        with sessions() as db:
            db.add(
                TerminalModel(
                    id="managed-concurrent",
                    tmux_session="cao-managed",
                    tmux_window="managed",
                    provider="codex",
                    runtime_lifecycle="running",
                    runtime_generation="generation-1",
                )
            )
            db.commit()

        def bind(index):
            identity = identities[index % len(identities)]
            accepted = bind_terminal_provider_resume_identity(
                "managed-concurrent",
                provider="codex",
                resume_identity=identity,
                runtime_generation="generation-1",
            )
            return identity, accepted

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(bind, range(8)))

        accepted_identities = {identity for identity, accepted in results if accepted}
        assert len(accepted_identities) == 1
        assert any(not accepted for _identity, accepted in results)
        with sessions() as db:
            terminal = db.get(TerminalModel, "managed-concurrent")
            assert terminal.provider_resume_identity in accepted_identities
            bindings = db.query(ProviderUsageBindingModel).all()
            assert len(bindings) == 1
            assert bindings[0].provider_session_id == terminal.provider_resume_identity
            assert bindings[0].terminal_id == "managed-concurrent"

    def test_provider_resume_identity_binds_after_idle_runtime_is_running(
        self, test_db, monkeypatch
    ):
        identity = "01234567-89ab-cdef-0123-456789abcdef"
        monkeypatch.setattr(database_client, "SessionLocal", test_db)
        monkeypatch.setattr(database_client, "_terminal_authority_schema_ready", True)
        monkeypatch.setattr(database_client, "_usage_schema_ready", True)
        with test_db() as db:
            db.add(
                TerminalModel(
                    id="managed-running",
                    tmux_session="cao-managed",
                    tmux_window="managed",
                    provider="codex",
                    runtime_lifecycle="running",
                    runtime_generation="generation-1",
                )
            )
            db.commit()

        assert bind_terminal_provider_resume_identity(
            "managed-running",
            provider="codex",
            resume_identity=identity,
            runtime_generation="generation-1",
        )
        with test_db() as db:
            terminal = db.get(TerminalModel, "managed-running")
            assert terminal.provider_resume_identity == identity
            assert terminal.provider_resume_runtime_generation == "generation-1"

    def test_terminal_auth_token_matches_exact_terminal_capability(self, test_db, monkeypatch):
        import hashlib

        token = "terminal-capability"
        monkeypatch.setattr(database_client, "SessionLocal", test_db)
        with test_db() as db:
            db.add(
                TerminalModel(
                    id="auth-test",
                    tmux_session="cao-managed",
                    tmux_window="managed",
                    provider="codex",
                    auth_token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                )
            )
            db.commit()

        assert terminal_auth_token_matches("auth-test", token)
        assert not terminal_auth_token_matches("auth-test", "wrong")
        assert not terminal_auth_token_matches("missing", token)

    def test_writer_lease_is_db_enforced_and_read_only_lane_remains_available(
        self, test_db, monkeypatch
    ):
        monkeypatch.setattr("cli_agent_orchestrator.clients.database.SessionLocal", test_db)
        monkeypatch.setattr(
            "cli_agent_orchestrator.clients.database._terminal_authority_schema_ready", True
        )

        create_terminal(
            "writer-1",
            "cao-session",
            "writer-1",
            "gemini_cli",
            launch_worktree="/srv/worktree",
            write_enabled=True,
        )
        with pytest.raises(WorktreeWriterLeaseConflict):
            create_terminal(
                "writer-2",
                "cao-session",
                "writer-2",
                "gemini_cli",
                launch_worktree="/srv/worktree",
                write_enabled=True,
            )
        create_terminal(
            "reader",
            "cao-session",
            "reader",
            "gemini_cli",
            launch_worktree="/srv/worktree",
            write_enabled=False,
        )

        with test_db() as db:
            db.add(
                TerminalModel(
                    id="legacy-writer",
                    tmux_session="cao-session",
                    tmux_window="legacy-writer",
                    provider="gemini_cli",
                    launch_worktree="/srv/worktree",
                    write_enabled=True,
                )
            )
            db.commit()
            leases = db.query(WorktreeWriterLeaseModel).all()
            assert [(lease.canonical_worktree, lease.terminal_id) for lease in leases] == [
                ("/srv/worktree", "writer-1")
            ]

        assert delete_terminal("writer-1")
        with test_db() as db:
            lease = db.query(WorktreeWriterLeaseModel).one()
            replacement = db.get(TerminalModel, "legacy-writer")
            assert lease.terminal_id == "legacy-writer"
            assert replacement.writer_authority_generation
            assert lease.authority_generation == replacement.writer_authority_generation
        with pytest.raises(WorktreeWriterLeaseConflict):
            create_terminal(
                "writer-2",
                "cao-session",
                "writer-2",
                "gemini_cli",
                launch_worktree="/srv/worktree",
                write_enabled=True,
            )
        assert delete_terminal("legacy-writer")
        create_terminal(
            "writer-2",
            "cao-session",
            "writer-2",
            "gemini_cli",
            launch_worktree="/srv/worktree",
            write_enabled=True,
        )

    def test_independent_project_sessions_bind_distinct_work_contexts_and_leases(
        self, test_db, monkeypatch
    ):
        monkeypatch.setattr(database_client, "SessionLocal", test_db)
        monkeypatch.setattr(database_client, "_terminal_authority_schema_ready", True)
        base = "a" * 40

        for suffix in ("1", "2"):
            terminal_id = f"context{suffix}"
            session_id = f"session-{suffix}"
            worktree = f"/managed/{terminal_id}"
            branch = f"cao/session/{terminal_id}"
            reserve_writable_work_context(
                context_id=terminal_id,
                request_id=f"00000000-0000-4000-8000-00000000000{suffix}",
                project_id="project-a",
                session_id=session_id,
                terminal_id=terminal_id,
                canonical_source="/source/project-a",
                canonical_worktree=worktree,
                branch=branch,
                base_revision=base,
            )
            assert transition_writable_work_context(
                terminal_id,
                expected_states=("reserved",),
                state="provisioned",
                event_type="managed_worktree_provisioned",
            )
            created = create_terminal(
                terminal_id,
                f"cao-session-{suffix}",
                terminal_id,
                "codex",
                launch_worktree=worktree,
                write_enabled=True,
                context_role="supervisor",
                managed_worktree_kind="supervisor",
                managed_worktree_source="/source/project-a",
                managed_worktree_branch=branch,
                managed_worktree_commit=base,
                writable_work_context_id=terminal_id,
                workspace_classification="managed_isolated",
                project_id="project-a",
                session_lifetime_id=session_id,
            )
            assert created["writable_work_context_id"] == terminal_id

        with test_db() as db:
            contexts = (
                db.query(WritableWorkContextModel).order_by(WritableWorkContextModel.id).all()
            )
            leases = (
                db.query(WorktreeWriterLeaseModel)
                .order_by(WorktreeWriterLeaseModel.canonical_worktree)
                .all()
            )
            assert [row.state for row in contexts] == ["launching", "launching"]
            assert [row.project_id for row in contexts] == ["project-a", "project-a"]
            assert [row.terminal_id for row in contexts] == ["context1", "context2"]
            assert [row.terminal_id for row in leases] == ["context1", "context2"]
            assert contexts[0].writer_authority_generation == leases[0].authority_generation
            assert contexts[1].writer_authority_generation == leases[1].authority_generation

        with pytest.raises(WritableWorkContextConflict):
            create_terminal(
                "intruder",
                "cao-session-intruder",
                "intruder",
                "codex",
                launch_worktree="/managed/context1",
                write_enabled=True,
                context_role="supervisor",
                managed_worktree_kind="supervisor",
                managed_worktree_source="/source/project-a",
                managed_worktree_branch="cao/session/context1",
                managed_worktree_commit=base,
                writable_work_context_id="context1",
                workspace_classification="managed_isolated",
                project_id="project-a",
                session_lifetime_id="session-intruder",
            )
        with test_db() as db:
            assert db.query(WorktreeWriterLeaseModel).count() == 2
            rejected = (
                db.query(WritableWorkContextAuditModel)
                .filter_by(event_type="writer_conflict_rejected")
                .one()
            )
            assert rejected.work_context_id == "context1"
            assert rejected.terminal_id == "intruder"
            assert rejected.reason_code == "WORK_CONTEXT_AUTHORITY_CHANGED"

        with pytest.raises(SessionPrimarySupervisorConflict):
            create_terminal(
                "context3",
                "cao-session-1",
                "context3",
                "codex",
                launch_worktree="/managed/context3",
                write_enabled=True,
                context_role="supervisor",
                managed_worktree_kind="supervisor",
                session_lifetime_id="session-1",
            )

    def test_concurrent_work_context_reservation_has_one_durable_winner(
        self, tmp_path, monkeypatch
    ):
        engine = create_engine(
            f"sqlite:///{tmp_path / 'work-context.db'}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=engine)
        sessions = sessionmaker(bind=engine)
        monkeypatch.setattr(database_client, "engine", engine)
        monkeypatch.setattr(database_client, "SessionLocal", sessions)
        monkeypatch.setattr(
            database_client, "_ensure_terminal_worktree_authority_schema", lambda: None
        )
        request_id = "00000000-0000-4000-8000-000000000099"

        def reserve(_index):
            return reserve_writable_work_context(
                context_id="context-a",
                request_id=request_id,
                project_id="project-a",
                session_id="session-a",
                terminal_id="terminal-a",
                canonical_source="/source/project-a",
                canonical_worktree="/managed/context-a",
                branch="cao/session/context-a",
                base_revision="a" * 40,
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(reserve, range(8)))

        assert {row["id"] for row in results} == {"context-a"}
        with sessions() as db:
            assert db.query(WritableWorkContextModel).count() == 1
            assert db.query(WritableWorkContextAuditModel).count() == 1

    def test_repeated_recovery_admission_audits_each_successor_generation(
        self, test_db, monkeypatch
    ):
        monkeypatch.setattr(database_client, "SessionLocal", test_db)
        monkeypatch.setattr(database_client, "_terminal_authority_schema_ready", True)
        context_id = "reusable-recovery-context"
        reserve_writable_work_context(
            context_id=context_id,
            request_id="00000000-0000-4000-8000-000000000097",
            project_id="project-a",
            session_id="session-a",
            terminal_id="supervisor-a",
            canonical_source="/source/project-a",
            canonical_worktree="/managed/supervisor-a",
            branch="cao/session/supervisor-a",
            base_revision="a" * 40,
        )
        assert transition_writable_work_context(
            context_id,
            expected_states=("reserved",),
            state="launching",
            event_type="writer_lease_granted",
        )
        assert transition_writable_work_context(
            context_id,
            expected_states=("launching",),
            state="admitted",
            event_type="recovery_supervisor_admitted",
        )
        with test_db() as db:
            context = db.get(WritableWorkContextModel, context_id)
            context.state = "launching"
            context.terminal_id = "supervisor-a2"
            db.commit()
        assert transition_writable_work_context(
            context_id,
            expected_states=("launching",),
            state="admitted",
            event_type="recovery_supervisor_admitted",
        )
        with test_db() as db:
            recovery_events = (
                db.query(WritableWorkContextAuditModel)
                .filter_by(event_type="recovery_supervisor_admitted")
                .order_by(WritableWorkContextAuditModel.id)
                .all()
            )
            assert [event.terminal_id for event in recovery_events] == [
                "supervisor-a",
                "supervisor-a2",
            ]

    def test_work_context_transition_rejects_stale_terminal_generation(self, test_db, monkeypatch):
        monkeypatch.setattr(database_client, "SessionLocal", test_db)
        monkeypatch.setattr(database_client, "_terminal_authority_schema_ready", True)
        reserve_writable_work_context(
            context_id="fenced-context",
            request_id="00000000-0000-4000-8000-000000000096",
            project_id="project-a",
            session_id="session-a",
            terminal_id="supervisor-a",
            canonical_source="/source/project-a",
            canonical_worktree="/managed/supervisor-a",
            branch="cao/session/supervisor-a",
            base_revision="a" * 40,
        )
        with test_db() as db:
            context = db.get(WritableWorkContextModel, "fenced-context")
            context.state = "preserved"
            context.terminal_id = "supervisor-a2"
            context.writer_authority_generation = "generation-a2"
            db.commit()

        assert not transition_writable_work_context(
            "fenced-context",
            expected_states=("launching", "preserved"),
            state="admitted",
            event_type="recovery_supervisor_admitted",
            expected_terminal_id="supervisor-a",
            expected_writer_authority_generation="generation-a",
        )
        with test_db() as db:
            context = db.get(WritableWorkContextModel, "fenced-context")
            assert context.state == "preserved"
            assert context.terminal_id == "supervisor-a2"
            assert context.writer_authority_generation == "generation-a2"

    @patch("cli_agent_orchestrator.clients.database.SessionLocal")
    def test_get_terminal_metadata_found(self, mock_session_class):
        """Test getting terminal metadata that exists."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        mock_terminal = MagicMock()
        mock_terminal.id = "test123"
        mock_terminal.tmux_session = "cao-session"
        mock_terminal.tmux_window = "window-0"
        mock_terminal.provider = "kiro_cli"
        mock_terminal.agent_profile = "developer"
        mock_terminal.profile_revision_id = None
        mock_terminal.provider_config_revision_id = None
        mock_terminal.launch_snapshot_status = "legacy_unavailable"
        mock_terminal.launch_snapshot_json = None
        mock_terminal.allowed_tools = None
        mock_terminal.launch_worktree = "/srv/worktree"
        mock_terminal.write_enabled = True
        mock_terminal.provider_resume_identity = "01234567-89ab-cdef-0123-456789abcdef"
        mock_terminal.provider_resume_runtime_generation = "generation-1"
        mock_terminal.last_active = datetime.now()

        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = mock_terminal
        mock_session.query.return_value = mock_query
        mock_session_class.return_value = mock_session

        result = get_terminal_metadata("test123")

        assert result is not None
        assert result["id"] == "test123"
        assert result["launch_worktree"] == "/srv/worktree"
        assert result["write_enabled"] is True
        assert result["provider_resume_identity"] == mock_terminal.provider_resume_identity
        assert result["provider_resume_runtime_generation"] == "generation-1"

    @patch("cli_agent_orchestrator.clients.database.SessionLocal")
    def test_get_terminal_metadata_not_found(self, mock_session_class):
        """Test getting terminal metadata that doesn't exist."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = None
        mock_session.query.return_value = mock_query
        mock_session_class.return_value = mock_session

        result = get_terminal_metadata("nonexistent")

        assert result is None

    @patch("cli_agent_orchestrator.clients.database.SessionLocal")
    def test_update_last_active(self, mock_session_class):
        """Test updating last active timestamp."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        mock_terminal = MagicMock()
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = mock_terminal
        mock_session.query.return_value = mock_query
        mock_session_class.return_value = mock_session

        update_last_active("test123")

        mock_session.commit.assert_called_once()

    @patch("cli_agent_orchestrator.clients.database.SessionLocal")
    def test_delete_terminal(self, mock_session_class):
        """Test deleting a terminal."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        mock_query = MagicMock()
        mock_query.filter.return_value.delete.return_value = 1
        mock_session.query.return_value = mock_query
        mock_session_class.return_value = mock_session

        result = delete_terminal("test123")

        assert result is True
        mock_session.commit.assert_called_once()

    @patch("cli_agent_orchestrator.clients.database.SessionLocal")
    def test_delete_terminal_not_found(self, mock_session_class):
        """Test deleting a terminal that doesn't exist."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        mock_query = MagicMock()
        mock_query.filter.return_value.delete.return_value = 0
        mock_session.query.return_value = mock_query
        mock_session_class.return_value = mock_session

        result = delete_terminal("nonexistent")

        assert result is False

    @patch("cli_agent_orchestrator.clients.database.SessionLocal")
    def test_list_terminals_by_session(self, mock_session_class):
        """Test listing terminals by session."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        mock_terminal = MagicMock()
        mock_terminal.id = "test123"
        mock_terminal.tmux_session = "cao-session"
        mock_terminal.tmux_window = "window-0"
        mock_terminal.provider = "kiro_cli"
        mock_terminal.agent_profile = "developer"
        mock_terminal.last_active = datetime.now()

        mock_query = MagicMock()
        mock_query.filter.return_value.all.return_value = [mock_terminal]
        mock_session.query.return_value = mock_query
        mock_session_class.return_value = mock_session

        result = list_terminals_by_session("cao-session")

        assert len(result) == 1
        assert result[0]["id"] == "test123"

    @patch("cli_agent_orchestrator.clients.database.SessionLocal")
    def test_delete_terminals_by_session(self, mock_session_class):
        """Test deleting all terminals in a session."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        mock_query = MagicMock()
        mock_query.filter.return_value.delete.return_value = 2
        mock_session.query.return_value = mock_query
        mock_session_class.return_value = mock_session

        result = delete_terminals_by_session("cao-session")

        assert result == 2


class TestInboxOperations:
    """Tests for inbox database operations."""

    @patch("cli_agent_orchestrator.clients.database.SessionLocal")
    def test_update_message_status(self, mock_session_class):
        """Test updating message status."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        mock_message = MagicMock()
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = mock_message
        mock_session.query.return_value = mock_query
        mock_session_class.return_value = mock_session

        update_message_status(1, MessageStatus.DELIVERED)

        mock_session.commit.assert_called_once()


class TestFlowOperations:
    """Tests for flow database operations."""

    @patch("cli_agent_orchestrator.clients.database.SessionLocal")
    def test_get_flow_not_found(self, mock_session_class):
        """Test getting a flow that doesn't exist."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = None
        mock_session.query.return_value = mock_query
        mock_session_class.return_value = mock_session

        result = get_flow("nonexistent")

        assert result is None

    @patch("cli_agent_orchestrator.clients.database.SessionLocal")
    def test_update_flow_enabled(self, mock_session_class):
        """Test updating flow enabled status."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        mock_flow = MagicMock()
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = mock_flow
        mock_session.query.return_value = mock_query
        mock_session_class.return_value = mock_session

        update_flow_enabled("test-flow", False)

        mock_session.commit.assert_called_once()

    @patch("cli_agent_orchestrator.clients.database.SessionLocal")
    def test_update_flow_run_times(self, mock_session_class):
        """Test updating flow run times."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        mock_flow = MagicMock()
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = mock_flow
        mock_session.query.return_value = mock_query
        mock_session_class.return_value = mock_session

        result = update_flow_run_times("test-flow", datetime.now(), datetime.now())

        assert result is True
        mock_session.commit.assert_called_once()

    @patch("cli_agent_orchestrator.clients.database.SessionLocal")
    def test_update_flow_run_times_not_found(self, mock_session_class):
        """Test updating flow run times when flow doesn't exist."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = None
        mock_session.query.return_value = mock_query
        mock_session_class.return_value = mock_session

        result = update_flow_run_times("nonexistent", datetime.now(), datetime.now())

        assert result is False

    @patch("cli_agent_orchestrator.clients.database.SessionLocal")
    def test_update_flow_enabled_not_found(self, mock_session_class):
        """Test updating flow enabled when flow doesn't exist."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = None
        mock_session.query.return_value = mock_query
        mock_session_class.return_value = mock_session

        result = update_flow_enabled("nonexistent", False)

        assert result is False

    @patch("cli_agent_orchestrator.clients.database.SessionLocal")
    def test_update_flow_enabled_with_next_run(self, mock_session_class):
        """Test updating flow enabled with next_run."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        mock_flow = MagicMock()
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = mock_flow
        mock_session.query.return_value = mock_query
        mock_session_class.return_value = mock_session

        next_run = datetime.now()
        result = update_flow_enabled("test-flow", True, next_run=next_run)

        assert result is True
        assert mock_flow.next_run == next_run

    @patch("cli_agent_orchestrator.clients.database.SessionLocal")
    def test_create_flow(self, mock_session_class):
        """Test creating a flow."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session_class.return_value = mock_session

        # Setup mock to update flow attributes on refresh
        def mock_refresh(flow):
            flow.name = "test-flow"
            flow.file_path = "/path/to/file.yaml"
            flow.schedule = "0 * * * *"
            flow.agent_profile = "developer"
            flow.provider = "kiro_cli"
            flow.script = "echo test"
            flow.next_run = datetime.now()
            flow.last_run = None
            flow.enabled = True

        mock_session.refresh.side_effect = mock_refresh

        from cli_agent_orchestrator.clients.database import get_flows_to_run

        next_run = datetime.now()
        result = create_flow(
            name="test-flow",
            file_path="/path/to/file.yaml",
            schedule="0 * * * *",
            agent_profile="developer",
            provider="kiro_cli",
            script="echo test",
            next_run=next_run,
        )

        assert result.name == "test-flow"
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()

    @patch("cli_agent_orchestrator.clients.database.SessionLocal")
    def test_get_flow_found(self, mock_session_class):
        """Test getting a flow that exists."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        mock_flow = MagicMock()
        mock_flow.name = "test-flow"
        mock_flow.file_path = "/path/to/file.yaml"
        mock_flow.schedule = "0 * * * *"
        mock_flow.agent_profile = "developer"
        mock_flow.provider = "kiro_cli"
        mock_flow.script = "echo test"
        mock_flow.last_run = None
        mock_flow.next_run = datetime.now()
        mock_flow.enabled = True
        mock_flow.project_id = None
        mock_flow.project_name = None
        mock_flow.project_path = None
        mock_flow.project_description = None

        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = mock_flow
        mock_session.query.return_value = mock_query
        mock_session_class.return_value = mock_session

        result = get_flow("test-flow")

        assert result is not None
        assert result.name == "test-flow"

    @patch("cli_agent_orchestrator.clients.database.SessionLocal")
    def test_list_flows(self, mock_session_class):
        """Test listing all flows."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        mock_flow = MagicMock()
        mock_flow.name = "test-flow"
        mock_flow.file_path = "/path/to/file.yaml"
        mock_flow.schedule = "0 * * * *"
        mock_flow.agent_profile = "developer"
        mock_flow.provider = "kiro_cli"
        mock_flow.script = "echo test"
        mock_flow.last_run = None
        mock_flow.next_run = datetime.now()
        mock_flow.enabled = True
        mock_flow.project_id = None
        mock_flow.project_name = None
        mock_flow.project_path = None
        mock_flow.project_description = None

        mock_query = MagicMock()
        mock_query.order_by.return_value.all.return_value = [mock_flow]
        mock_session.query.return_value = mock_query
        mock_session_class.return_value = mock_session

        result = list_flows()

        assert len(result) == 1
        assert result[0].name == "test-flow"

    @patch("cli_agent_orchestrator.clients.database.SessionLocal")
    def test_delete_flow(self, mock_session_class):
        """Test deleting a flow."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        mock_query = MagicMock()
        mock_query.filter.return_value.delete.return_value = 1
        mock_session.query.return_value = mock_query
        mock_session_class.return_value = mock_session

        result = delete_flow("test-flow")

        assert result is True
        mock_session.commit.assert_called_once()

    @patch("cli_agent_orchestrator.clients.database.SessionLocal")
    def test_delete_flow_not_found(self, mock_session_class):
        """Test deleting a flow that doesn't exist."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        mock_query = MagicMock()
        mock_query.filter.return_value.delete.return_value = 0
        mock_session.query.return_value = mock_query
        mock_session_class.return_value = mock_session

        result = delete_flow("nonexistent")

        assert result is False

    @patch("cli_agent_orchestrator.clients.database.SessionLocal")
    def test_get_flows_to_run(self, mock_session_class):
        """Test getting flows that are due to run."""
        from cli_agent_orchestrator.clients.database import get_flows_to_run

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        mock_flow = MagicMock()
        mock_flow.name = "due-flow"
        mock_flow.file_path = "/path/to/file.yaml"
        mock_flow.schedule = "0 * * * *"
        mock_flow.agent_profile = "developer"
        mock_flow.provider = "kiro_cli"
        mock_flow.script = "echo test"
        mock_flow.last_run = None
        mock_flow.next_run = datetime.now()
        mock_flow.enabled = True
        mock_flow.project_id = None
        mock_flow.project_name = None
        mock_flow.project_path = None
        mock_flow.project_description = None

        mock_query = MagicMock()
        mock_query.filter.return_value.all.return_value = [mock_flow]
        mock_session.query.return_value = mock_query
        mock_session_class.return_value = mock_session

        result = get_flows_to_run()

        assert len(result) == 1
        assert result[0].name == "due-flow"

    @patch("cli_agent_orchestrator.clients.database.SessionLocal")
    def test_update_last_active_not_found(self, mock_session_class):
        """Test updating last active when terminal doesn't exist."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = None
        mock_session.query.return_value = mock_query
        mock_session_class.return_value = mock_session

        result = update_last_active("nonexistent")

        assert result is False

    @patch("cli_agent_orchestrator.clients.database.SessionLocal")
    def test_update_message_status_not_found(self, mock_session_class):
        """Test updating message status when message doesn't exist."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = None
        mock_session.query.return_value = mock_query
        mock_session_class.return_value = mock_session

        result = update_message_status(999, MessageStatus.DELIVERED)

        assert result is False

    @patch("cli_agent_orchestrator.clients.database.SessionLocal")
    def test_create_inbox_message(self, mock_session_class):
        """Test creating an inbox message."""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session_class.return_value = mock_session
        mock_session.get.return_value = MagicMock(runtime_lifecycle="running")

        # Setup mock to update message attributes on refresh
        def mock_refresh(msg):
            msg.id = 1
            msg.sender_id = "sender-123"
            msg.receiver_id = "receiver-456"
            msg.message = "Hello"
            msg.status = MessageStatus.PENDING.value
            msg.created_at = datetime.now()

        mock_session.refresh.side_effect = mock_refresh

        result = create_inbox_message("sender-123", "receiver-456", "Hello")

        assert result.sender_id == "sender-123"
        assert result.receiver_id == "receiver-456"
        assert result.message == "Hello"
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()


class TestInitDb:
    """Tests for init_db function."""

    @staticmethod
    def _bind_isolated_database(database_file, monkeypatch):
        isolated_engine = create_engine(f"sqlite:///{database_file}")
        isolated_session = sessionmaker(bind=isolated_engine)
        monkeypatch.setattr(database_client, "engine", isolated_engine)
        monkeypatch.setattr(database_client, "SessionLocal", isolated_session)
        monkeypatch.setattr("cli_agent_orchestrator.constants.DATABASE_FILE", database_file)
        monkeypatch.setattr(database_client, "_child_assignment_schema_ready", False)
        monkeypatch.setattr(database_client, "_delegation_result_schema_ready", False)
        monkeypatch.setattr(database_client, "_terminal_authority_schema_ready", False)
        return isolated_engine, isolated_session

    def test_init_db_upgrades_pre_r1_schema_with_history_idempotently(self, tmp_path, monkeypatch):
        """The exact 813b8c5 assignment shape upgrades before F14 backfill queries it."""
        database_file = tmp_path / "pre-r1.db"
        isolated_engine, isolated_session = self._bind_isolated_database(database_file, monkeypatch)
        for table in Base.metadata.sorted_tables:
            if table.name != ChildAssignmentModel.__tablename__:
                table.create(bind=isolated_engine)
        with isolated_engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE child_assignments ("
                "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
                "parent_terminal_id VARCHAR NOT NULL, "
                "child_terminal_id VARCHAR NOT NULL UNIQUE, "
                "status VARCHAR NOT NULL, result_message_id INTEGER, "
                "cleanup_acknowledged BOOLEAN NOT NULL DEFAULT 0, "
                "direct_result_output TEXT, "
                "handoff_input_received BOOLEAN NOT NULL DEFAULT 0, "
                "retirement_claim_token TEXT, retirement_claimed_at DATETIME, "
                "retirement_exit_dispatched_at DATETIME, "
                "retirement_completed_at DATETIME, "
                "created_at DATETIME, updated_at DATETIME)"
            )
            connection.exec_driver_sql(
                "INSERT INTO terminals "
                "(id, tmux_session, tmux_window, provider, agent_profile) VALUES "
                "('parent-history', 'cao-history', 'parent', 'codex', 'developer'), "
                "('child-history', 'cao-history', 'child', 'codex', 'developer')"
            )
            connection.exec_driver_sql(
                "INSERT INTO inbox (id, sender_id, receiver_id, message, status, kind) "
                "VALUES (41, 'child-history', 'parent-history', "
                "'durable history', 'delivered', 'message')"
            )
            connection.exec_driver_sql(
                "INSERT INTO child_assignments "
                "(id, parent_terminal_id, child_terminal_id, status, result_message_id) "
                "VALUES (7, 'parent-history', 'child-history', 'result_delivered', 41)"
            )

        init_db()
        expected_columns = {column.name for column in ChildAssignmentModel.__table__.columns}
        with sqlite3.connect(database_file) as connection:
            actual_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(child_assignments)")
            }
            history_before_restart = connection.execute(
                "SELECT id, parent_terminal_id, child_terminal_id, status, result_message_id "
                "FROM child_assignments"
            ).fetchall()
        assert actual_columns == expected_columns
        assert history_before_restart == [
            (7, "parent-history", "child-history", "result_delivered", 41)
        ]
        with isolated_session() as db:
            result = db.query(DelegationResultModel).filter_by(child_assignment_id=7).one()
            assert result.status == "complete"
            assert result.content_bytes == len("durable history")
            assert db.query(DelegationResultModel).count() == 1

        # Model a second process startup, including re-running schema checks.
        monkeypatch.setattr(database_client, "_child_assignment_schema_ready", False)
        monkeypatch.setattr(database_client, "_delegation_result_schema_ready", False)
        monkeypatch.setattr(database_client, "_terminal_authority_schema_ready", False)
        init_db()
        with sqlite3.connect(database_file) as connection:
            history_after_restart = connection.execute(
                "SELECT id, parent_terminal_id, child_terminal_id, status, result_message_id "
                "FROM child_assignments"
            ).fetchall()
        with isolated_session() as db:
            assert db.query(DelegationResultModel).count() == 1
        assert history_after_restart == history_before_restart

    def test_init_db_fresh_database_has_complete_assignment_schema(self, tmp_path, monkeypatch):
        database_file = tmp_path / "fresh.db"
        self._bind_isolated_database(database_file, monkeypatch)

        init_db()
        init_db()

        with sqlite3.connect(database_file) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(child_assignments)")}
        assert columns == {column.name for column in ChildAssignmentModel.__table__.columns}

    def test_workspace_retirement_authority_column_upgrades_idempotently(
        self, tmp_path, monkeypatch
    ):
        database_file = tmp_path / "pre-workspace-retirement.db"
        isolated_engine, _isolated_session = self._bind_isolated_database(
            database_file, monkeypatch
        )
        Base.metadata.create_all(isolated_engine)
        with isolated_engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE writable_work_contexts DROP COLUMN retirement_allow_dirty"
            )
            connection.exec_driver_sql(
                "ALTER TABLE writable_work_contexts DROP COLUMN retirement_plan_json"
            )
            connection.exec_driver_sql(
                "INSERT INTO writable_work_contexts "
                "(id, request_id, project_id, session_id, terminal_id, canonical_source, "
                "canonical_worktree, branch, base_revision, state, writer_authority_generation, "
                "created_at, updated_at) "
                "VALUES ('context-history', 'request-history', 'project-history', "
                "'session-history', 'terminal-history', '/source', '/worktree', "
                "'cao/session/history', 'abc123', 'admitted', 'writer-history', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )

        database_client._ensure_terminal_worktree_authority_schema()
        monkeypatch.setattr(database_client, "_terminal_authority_schema_ready", False)
        database_client._ensure_terminal_worktree_authority_schema()

        with sqlite3.connect(database_file) as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(writable_work_contexts)")
            }
            history = connection.execute(
                "SELECT id, state, retirement_allow_dirty, retirement_plan_json "
                "FROM writable_work_contexts"
            ).fetchall()
        assert "retirement_allow_dirty" in columns
        assert "retirement_plan_json" in columns
        assert history == [("context-history", "admitted", 0, None)]

    @patch("cli_agent_orchestrator.clients.database.Base")
    def test_init_db(self, mock_base):
        """Test database initialization."""
        init_db()

        mock_base.metadata.create_all.assert_called_once()
