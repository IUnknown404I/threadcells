"""Stable session identity and idempotent retirement persistence tests."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.database import (
    AmbiguousSessionIdentity,
    AmbiguousTerminalIdentity,
    Base,
    ProviderExecutionLeaseModel,
    SessionDeletionOperationModel,
    SessionDeletionReceiptModel,
    SessionLifetimeAuthorityError,
    TerminalDeletionReceiptModel,
    TerminalModel,
    WorktreeWriterLeaseModel,
    WritableWorkContextAuditModel,
    WritableWorkContextConflict,
    WritableWorkContextModel,
)


def _install_database(monkeypatch, url: str = "sqlite:///:memory:"):
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(database, "_ensure_terminal_worktree_authority_schema", lambda: None)
    monkeypatch.setattr(database, "_ensure_provider_execution_schema", lambda: None)
    monkeypatch.setattr(database, "_ensure_usage_schema", lambda: None)
    monkeypatch.setattr(database, "_ensure_session_deletion_receipt_schema", lambda: None)
    monkeypatch.setattr(database, "_ensure_terminal_deletion_receipt_schema", lambda: None)
    return engine


def _terminal(
    terminal_id: str,
    lifetime: str,
    name: str,
    worktree: str,
    *,
    project_id: str | None = None,
) -> TerminalModel:
    return TerminalModel(
        id=terminal_id,
        tmux_session=name,
        session_id=lifetime,
        tmux_window=f"developer-{terminal_id}",
        provider="codex",
        launch_worktree=worktree,
        project_id=project_id,
        write_enabled=True,
        runtime_lifecycle="exited",
    )


def test_stable_identity_separates_reused_session_names(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add_all(
            [
                _terminal("old", "lifetime-old", "cao-reused", "/work/old"),
                _terminal("new", "lifetime-new", "cao-reused", "/work/new"),
            ]
        )
        db.commit()

    assert database.resolve_session_lifetime("lifetime-old")["terminals"][0]["id"] == "old"
    assert database.resolve_session_lifetime("lifetime-new")["terminals"][0]["id"] == "new"
    try:
        database.resolve_session_lifetime("cao-reused")
    except AmbiguousSessionIdentity:
        pass
    else:
        raise AssertionError("a reused raw session name must remain ambiguous")


def test_current_lifetime_preserves_exact_workspace_retirement_projection(monkeypatch):
    _install_database(monkeypatch)
    terminals = [
        _terminal("supervisor", "lifetime", "cao-session", "/work/supervisor"),
        _terminal("reviewer", "lifetime", "cao-session", "/work/reviewer"),
        _terminal("legacy-null", "lifetime", "cao-session", "/work/legacy-null"),
    ]
    generations = {
        "supervisor": "writer-generation-supervisor",
        "reviewer": "writer-generation-reviewer",
        "legacy-null": None,
    }
    for terminal in terminals:
        terminal.managed_worktree_kind = "reviewer" if terminal.id == "reviewer" else "supervisor"
        terminal.managed_worktree_source = "/source/project"
        terminal.managed_worktree_branch = (
            None if terminal.id == "reviewer" else f"cao/session/{terminal.id}"
        )
        terminal.managed_worktree_commit = terminal.id.ljust(40, "0")
        terminal.managed_worktree_origin_terminal_id = terminal.id
        terminal.writable_work_context_id = terminal.id if terminal.id != "reviewer" else None
        terminal.writer_authority_generation = generations[terminal.id]
    with database.SessionLocal() as db:
        db.add_all(terminals)
        db.commit()

    resolved = database.resolve_session_lifetime("lifetime")

    assert resolved is not None
    by_id = {row["id"]: row for row in resolved["terminals"]}
    assert set(by_id) == set(generations)
    assert all(
        set(database._SESSION_WORKSPACE_RETIREMENT_TERMINAL_FIELDS) <= set(row)
        for row in by_id.values()
    )
    assert {
        terminal_id: row["writer_authority_generation"] for terminal_id, row in by_id.items()
    } == generations


def test_undeleted_legacy_session_resolves_by_raw_name(monkeypatch):
    _install_database(monkeypatch)
    legacy = _terminal("legacy", "placeholder", "cao-legacy", "/work/legacy")
    legacy.session_id = None
    with database.SessionLocal() as db:
        db.add(legacy)
        db.commit()

    resolved = database.resolve_session_lifetime("cao-legacy")

    assert resolved is not None
    assert resolved["session_id"] == "legacy:cao-legacy"
    assert resolved["terminals"][0]["id"] == "legacy"


def test_unrelated_receipt_does_not_hide_legacy_raw_name(monkeypatch):
    _install_database(monkeypatch)
    legacy = _terminal("legacy", "placeholder", "cao-legacy", "/work/legacy")
    legacy.session_id = None
    with database.SessionLocal() as db:
        db.add_all(
            [
                legacy,
                SessionDeletionReceiptModel(
                    session_id="unrelated-lifetime",
                    session_name="cao-unrelated",
                    retained_resources_json="[]",
                ),
            ]
        )
        db.commit()

    resolved = database.resolve_session_lifetime("cao-legacy")

    assert resolved is not None
    assert resolved["session_id"] == "legacy:cao-legacy"
    assert resolved["terminals"][0]["id"] == "legacy"


def test_existing_receipt_schema_adds_replayable_retained_resources(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionDeletionReceiptModel.__table__.drop(bind=engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE session_deletion_receipts ("
            "session_id VARCHAR PRIMARY KEY, session_name VARCHAR NOT NULL, "
            "deleted_at DATETIME NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO session_deletion_receipts "
            "(session_id, session_name, deleted_at) "
            "VALUES ('legacy-receipt', 'cao-deleted', CURRENT_TIMESTAMP)"
        )
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(database, "_ensure_terminal_worktree_authority_schema", lambda: None)
    monkeypatch.setattr(database, "_ensure_usage_schema", lambda: None)

    resolved = database.resolve_session_lifetime("legacy-receipt")

    assert resolved is not None
    assert resolved["deleted"] is True
    assert resolved["retained_resources"] == []
    assert resolved["workspace_disposition"] == "retired"
    assert len(resolved["workspace_evidence_sha256"]) == 64
    with engine.connect() as connection:
        columns = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(session_deletion_receipts)"
            ).fetchall()
        }
        operation_tables = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type = 'table' AND name = 'session_deletion_operations'"
        ).scalar_one()
        operation_columns = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(session_deletion_operations)"
            ).fetchall()
        }
    assert {
        "retained_resources_json",
        "deletion_reason",
        "authority_fingerprint",
        "terminal_fences_json",
        "workspace_disposition",
        "workspace_evidence_json",
        "receipt_version",
    }.issubset(columns)
    assert operation_tables == 1
    assert {
        "workspace_authority_json",
        "workspace_authority_sha256",
        "workspace_disposition",
        "workspace_evidence_json",
        "workspace_evidence_sha256",
    }.issubset(operation_columns)
    with database.SessionLocal() as db:
        receipt = db.get(SessionDeletionReceiptModel, "legacy-receipt")
        assert receipt.receipt_version == 1

    # A restart after any subset of additive DDL must re-inspect the schema,
    # not trust process-local completion state.
    database._ensure_session_deletion_receipt_schema()
    database._ensure_session_deletion_receipt_schema()
    with database.SessionLocal() as db:
        receipt = db.get(SessionDeletionReceiptModel, "legacy-receipt")
        assert receipt.receipt_version == 1


def test_existing_deletion_operation_schema_adds_current_workspace_authority(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionDeletionOperationModel.__table__.drop(bind=engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE session_deletion_operations ("
            "session_id VARCHAR PRIMARY KEY, session_name VARCHAR NOT NULL, "
            "state VARCHAR NOT NULL, terminal_ids_json TEXT NOT NULL, "
            "allow_dirty_workspace BOOLEAN NOT NULL, "
            "authority_fingerprint VARCHAR NOT NULL, "
            "workspace_evidence_sha256 VARCHAR, created_at DATETIME NOT NULL, "
            "updated_at DATETIME NOT NULL)"
        )
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(database, "_ensure_terminal_worktree_authority_schema", lambda: None)
    monkeypatch.setattr(database, "_ensure_usage_schema", lambda: None)

    database._ensure_session_deletion_receipt_schema()

    with engine.connect() as connection:
        columns = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(session_deletion_operations)"
            ).fetchall()
        }
    assert {"workspace_authority_json", "workspace_authority_sha256"}.issubset(columns)


def test_existing_terminal_receipt_schema_adds_digest_only_late_callback_fence(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TerminalDeletionReceiptModel.__table__.drop(bind=engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE terminal_deletion_receipts ("
            "terminal_id VARCHAR PRIMARY KEY, session_id VARCHAR, "
            "session_name VARCHAR NOT NULL, window_name VARCHAR NOT NULL, "
            "deleted_at DATETIME NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO terminal_deletion_receipts "
            "(terminal_id, session_id, session_name, window_name, deleted_at) "
            "VALUES ('legacy-terminal', 'legacy-session', 'cao-legacy-receipt', "
            "'legacy-window', CURRENT_TIMESTAMP)"
        )
        connection.exec_driver_sql(
            "INSERT INTO terminal_deletion_receipts "
            "(terminal_id, session_id, session_name, window_name, deleted_at) VALUES "
            "('name-only', NULL, 'cao-name-only', 'name-only', CURRENT_TIMESTAMP), "
            "('conflict-a', 'conflicting-session', 'cao-conflict-a', 'a', CURRENT_TIMESTAMP), "
            "('conflict-b', 'conflicting-session', 'cao-conflict-b', 'b', CURRENT_TIMESTAMP)"
        )
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))

    database._ensure_terminal_deletion_receipt_schema()

    with engine.connect() as connection:
        columns = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(terminal_deletion_receipts)"
            ).fetchall()
        }
        indexes = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA index_list(terminal_deletion_receipts)"
            ).fetchall()
        }
    assert {
        "auth_token_sha256",
        "session_lifetime_authority_version",
        "workspace_cleanup_authority_version",
        "managed_worktree_kind",
        "managed_worktree_source",
        "managed_worktree_path",
        "managed_worktree_branch",
        "managed_worktree_branch_object_id",
        "managed_worktree_identity",
    } <= columns
    assert "ix_terminal_deletion_receipts_auth_token_sha256" in indexes
    with engine.connect() as connection:
        versions = dict(
            connection.exec_driver_sql(
                "SELECT terminal_id, session_lifetime_authority_version "
                "FROM terminal_deletion_receipts"
            ).all()
        )
        assert versions == {
            "conflict-a": None,
            "conflict-b": None,
            "legacy-terminal": 1,
            "name-only": None,
        }
        assert (
            connection.exec_driver_sql(
                "SELECT COUNT(*) FROM migration_receipts WHERE name = ?",
                (database.SESSION_LIFETIME_RECEIPT_MIGRATION,),
            ).scalar_one()
            == 1
        )


def test_terminal_receipt_lifetime_backfill_resumes_after_schema_only_crash(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TerminalDeletionReceiptModel.__table__.drop(bind=engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE terminal_deletion_receipts ("
            "terminal_id VARCHAR PRIMARY KEY, session_id VARCHAR, "
            "session_name VARCHAR NOT NULL, window_name VARCHAR NOT NULL, "
            "deleted_at DATETIME NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO terminal_deletion_receipts "
            "(terminal_id, session_id, session_name, window_name, deleted_at) VALUES "
            "('legacy-terminal', 'legacy-session', 'cao-legacy', 'owner', CURRENT_TIMESTAMP), "
            "('legacy-child', 'legacy-session', 'cao-legacy', 'child', CURRENT_TIMESTAMP)"
        )
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    original_backfill = database._backfill_terminal_deletion_session_lifetime_authority

    def interrupt_backfill(_connection):
        raise RuntimeError("simulated lifetime backfill interruption")

    monkeypatch.setattr(
        database,
        "_backfill_terminal_deletion_session_lifetime_authority",
        interrupt_backfill,
    )
    with pytest.raises(RuntimeError, match="simulated lifetime backfill interruption"):
        database._ensure_terminal_deletion_receipt_schema()

    with engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT COUNT(*) FROM pragma_table_info('terminal_deletion_receipts') "
                "WHERE name = 'session_lifetime_authority_version'"
            ).scalar_one()
            == 1
        )
        assert (
            connection.exec_driver_sql(
                "SELECT session_lifetime_authority_version "
                "FROM terminal_deletion_receipts WHERE terminal_id = 'legacy-terminal'"
            ).scalar_one()
            is None
        )
        assert (
            connection.exec_driver_sql(
                "SELECT COUNT(*) FROM migration_receipts WHERE name = ?",
                (database.SESSION_LIFETIME_RECEIPT_MIGRATION,),
            ).scalar_one()
            == 0
        )

    # Model a process that durably repaired one exact row before dying. The
    # receipt-less retry must preserve it and deterministically repair the
    # remainder.
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE terminal_deletion_receipts "
            "SET session_lifetime_authority_version = 1 "
            "WHERE terminal_id = 'legacy-terminal'"
        )

    monkeypatch.setattr(
        database,
        "_backfill_terminal_deletion_session_lifetime_authority",
        original_backfill,
    )
    database._ensure_terminal_deletion_receipt_schema()
    with engine.connect() as connection:
        assert set(
            connection.exec_driver_sql(
                "SELECT terminal_id FROM terminal_deletion_receipts "
                "WHERE session_lifetime_authority_version = 1"
            ).scalars()
        ) == {"legacy-child", "legacy-terminal"}
        assert (
            connection.exec_driver_sql(
                "SELECT COUNT(*) FROM migration_receipts WHERE name = ?",
                (database.SESSION_LIFETIME_RECEIPT_MIGRATION,),
            ).scalar_one()
            == 1
        )

    monkeypatch.setattr(
        database,
        "_backfill_terminal_deletion_session_lifetime_authority",
        interrupt_backfill,
    )
    database._ensure_terminal_deletion_receipt_schema()

    database._ensure_terminal_deletion_receipt_schema()
    database._ensure_terminal_deletion_receipt_schema()
    with engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT COUNT(*) FROM pragma_table_info('terminal_deletion_receipts') "
                "WHERE name = 'auth_token_sha256'"
            ).scalar_one()
            == 1
        )


def test_last_terminal_deletion_preserves_receipt_only_session_lifetime(monkeypatch):
    _install_database(monkeypatch)
    first = _terminal("first", "lifetime", "cao-receipt-only", "/work/first")
    second = _terminal("second", "lifetime", "cao-receipt-only", "/work/second")
    with database.SessionLocal() as db:
        db.add_all([first, second])
        db.commit()
        first_identity = {
            field: getattr(first, field) for field in database._TERMINAL_DELETION_IDENTITY_FIELDS
        }
        second_identity = {
            field: getattr(second, field) for field in database._TERMINAL_DELETION_IDENTITY_FIELDS
        }

    assert (
        database.delete_exited_terminal("first", expected_identity=first_identity)["deleted"] == 1
    )
    assert (
        database.delete_exited_terminal("second", expected_identity=second_identity)["deleted"] == 1
    )

    by_id = database.resolve_session_lifetime("lifetime")
    by_name = database.resolve_session_lifetime("cao-receipt-only")
    assert by_id is not None and by_name is not None
    assert by_id["session_id"] == by_name["session_id"] == "lifetime"
    assert by_id["session_name"] == by_name["session_name"] == "cao-receipt-only"
    assert by_id["terminals"] == by_name["terminals"] == []
    assert by_id["receipt_terminal_ids"] == ["first", "second"]
    assert by_id["lifetime_authority"] == "terminal_deletion_receipts"
    with database.SessionLocal() as db:
        assert {
            row.session_lifetime_authority_version
            for row in db.query(TerminalDeletionReceiptModel).all()
        } == {1}


def test_receipt_only_session_lifetime_survives_database_restart(monkeypatch, tmp_path):
    state_path = tmp_path / "receipt-only.sqlite3"
    engine = _install_database(monkeypatch, f"sqlite:///{state_path}")
    terminal = _terminal(
        "former-owner",
        "restart-lifetime",
        "cao-receipt-restart",
        "/work/former-owner",
    )
    with database.SessionLocal() as db:
        db.add(terminal)
        db.commit()
        expected_identity = {
            field: getattr(terminal, field) for field in database._TERMINAL_DELETION_IDENTITY_FIELDS
        }

    assert (
        database.delete_exited_terminal("former-owner", expected_identity=expected_identity)[
            "deleted"
        ]
        == 1
    )
    engine.dispose()

    restarted = _install_database(monkeypatch, f"sqlite:///{state_path}")
    try:
        resolved = database.resolve_session_lifetime("restart-lifetime")
        assert resolved is not None
        assert resolved["session_name"] == "cao-receipt-restart"
        assert resolved["terminals"] == []
        assert resolved["receipt_terminal_ids"] == ["former-owner"]
        assert resolved["lifetime_authority"] == "terminal_deletion_receipts"
    finally:
        restarted.dispose()


def test_receipt_only_undeleted_session_name_cannot_be_recreated(monkeypatch):
    _install_database(monkeypatch)
    terminal = _terminal(
        "former-owner",
        "undeleted-lifetime",
        "cao-receipt-resurrection-fence",
        "/work/former-owner",
    )
    with database.SessionLocal() as db:
        db.add(terminal)
        db.commit()
        expected_identity = {
            field: getattr(terminal, field) for field in database._TERMINAL_DELETION_IDENTITY_FIELDS
        }
    assert (
        database.delete_exited_terminal("former-owner", expected_identity=expected_identity)[
            "deleted"
        ]
        == 1
    )

    with pytest.raises(WritableWorkContextConflict, match="SESSION_HISTORY_INELIGIBLE"):
        database.create_terminal(
            "replacement-owner",
            "cao-receipt-resurrection-fence",
            "owner",
            "codex",
            session_lifetime_id="replacement-lifetime",
        )

    assert (
        database.resolve_session_lifetime("undeleted-lifetime")["lifetime_authority"]
        == "terminal_deletion_receipts"
    )


def test_retired_child_receipt_does_not_block_same_live_session_agent_creation(monkeypatch):
    _install_database(monkeypatch)
    active = _terminal("active-owner", "live-lifetime", "cao-live", "/work/active")
    active.runtime_lifecycle = "running"
    retired = _terminal("retired-child", "live-lifetime", "cao-live", "/work/retired")
    with database.SessionLocal() as db:
        db.add_all([active, retired])
        db.commit()
        expected_identity = {
            field: getattr(retired, field) for field in database._TERMINAL_DELETION_IDENTITY_FIELDS
        }
    assert (
        database.delete_exited_terminal("retired-child", expected_identity=expected_identity)[
            "deleted"
        ]
        == 1
    )

    created = database.create_terminal(
        "new-child",
        "cao-live",
        "new-child",
        "codex",
        session_lifetime_id="live-lifetime",
    )

    assert created["session_id"] == "live-lifetime"
    resolved = database.resolve_session_lifetime("live-lifetime")
    assert resolved is not None
    assert {terminal["id"] for terminal in resolved["terminals"]} == {
        "active-owner",
        "new-child",
    }


def test_new_legacy_terminal_receipt_persists_explicit_canonical_lifetime(monkeypatch):
    _install_database(monkeypatch)
    terminal = _terminal(
        "legacy-owner", "placeholder", "cao-legacy-receipt-only", "/work/legacy-owner"
    )
    terminal.session_id = None
    with database.SessionLocal() as db:
        db.add(terminal)
        db.commit()
        expected_identity = {
            field: getattr(terminal, field) for field in database._TERMINAL_DELETION_IDENTITY_FIELDS
        }

    assert (
        database.delete_exited_terminal("legacy-owner", expected_identity=expected_identity)[
            "deleted"
        ]
        == 1
    )
    resolved = database.resolve_session_lifetime("legacy:cao-legacy-receipt-only")
    assert resolved is not None
    assert resolved["session_id"] == "legacy:cao-legacy-receipt-only"
    assert resolved["terminals"] == []
    with database.SessionLocal() as db:
        receipt = db.get(TerminalDeletionReceiptModel, "legacy-owner")
        assert receipt.session_id == "legacy:cao-legacy-receipt-only"
        assert receipt.session_lifetime_authority_version == 1


def test_name_only_legacy_receipt_cannot_borrow_current_terminal_authority(monkeypatch):
    _install_database(monkeypatch)
    terminal = _terminal(
        "current-owner", "placeholder", "cao-legacy-conflict", "/work/current-owner"
    )
    terminal.session_id = None
    with database.SessionLocal() as db:
        db.add(terminal)
        db.add(
            TerminalDeletionReceiptModel(
                terminal_id="unknown-former-owner",
                session_id=None,
                session_name="cao-legacy-conflict",
                window_name="unknown-former-owner",
                session_lifetime_authority_version=None,
                workspace_cleanup_authority_version=1,
            )
        )
        db.commit()
        expected_identity = {
            field: getattr(terminal, field) for field in database._TERMINAL_DELETION_IDENTITY_FIELDS
        }

    with pytest.raises(AmbiguousTerminalIdentity):
        database.delete_exited_terminal("current-owner", expected_identity=expected_identity)
    with pytest.raises(SessionLifetimeAuthorityError) as error:
        database.resolve_session_lifetime("cao-legacy-conflict")
    assert error.value.reason_code == "SESSION_LIFETIME_AUTHORITY_INCOMPLETE"
    with database.SessionLocal() as db:
        assert db.get(TerminalModel, "current-owner") is not None
        receipt = db.get(TerminalDeletionReceiptModel, "unknown-former-owner")
        assert receipt.session_lifetime_authority_version is None


@pytest.mark.parametrize(
    ("receipts", "reason_code"),
    [
        (
            [
                {
                    "terminal_id": "legacy",
                    "session_id": "lifetime",
                    "session_name": "cao-receipt-only",
                    "session_lifetime_authority_version": None,
                }
            ],
            "SESSION_LIFETIME_AUTHORITY_INCOMPLETE",
        ),
        (
            [
                {
                    "terminal_id": "one",
                    "session_id": "lifetime",
                    "session_name": "cao-one",
                    "session_lifetime_authority_version": 1,
                },
                {
                    "terminal_id": "two",
                    "session_id": "lifetime",
                    "session_name": "cao-two",
                    "session_lifetime_authority_version": 1,
                },
            ],
            "SESSION_LIFETIME_RECEIPT_CONFLICT",
        ),
    ],
)
def test_receipt_only_lifetime_missing_or_conflicting_authority_fails_closed(
    monkeypatch, receipts, reason_code
):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add_all(
            TerminalDeletionReceiptModel(
                **receipt,
                window_name=receipt["terminal_id"],
                workspace_cleanup_authority_version=1,
            )
            for receipt in receipts
        )
        db.commit()

    with pytest.raises(SessionLifetimeAuthorityError) as error:
        database.resolve_session_lifetime("lifetime")
    assert error.value.reason_code == reason_code


def test_current_terminal_and_receipt_ownership_mismatch_fails_closed(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add(_terminal("current", "lifetime", "cao-current", "/work/current"))
        db.add(
            TerminalDeletionReceiptModel(
                terminal_id="retired",
                session_id="lifetime",
                session_name="cao-conflicting",
                window_name="retired",
                session_lifetime_authority_version=1,
                workspace_cleanup_authority_version=1,
            )
        )
        db.commit()

    with pytest.raises(SessionLifetimeAuthorityError) as error:
        database.resolve_session_lifetime("lifetime")
    assert error.value.reason_code == "SESSION_LIFETIME_RECEIPT_CONFLICT"


def test_exact_lifetime_delete_preserves_reused_name_and_is_idempotent(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add_all(
            [
                _terminal("old", "lifetime-old", "cao-reused", "/work/old"),
                _terminal("new", "lifetime-new", "cao-reused", "/work/new"),
                WorktreeWriterLeaseModel(canonical_worktree="/work/old", terminal_id="old"),
                WorktreeWriterLeaseModel(canonical_worktree="/work/new", terminal_id="new"),
            ]
        )
        db.commit()

    first = database.delete_terminals_by_session_lifetime(
        "lifetime-old", "cao-reused", expected_terminal_ids=["old"]
    )
    second = database.delete_terminals_by_session_lifetime(
        "lifetime-old", "cao-reused", expected_terminal_ids=["old"]
    )

    assert first == {
        "deleted": 1,
        "logical_deleted": 1,
        "retained": 0,
        "retained_resources": [],
        "already_deleted": False,
    }
    assert second == {
        "deleted": 0,
        "logical_deleted": 0,
        "retained": 0,
        "retained_resources": [],
        "already_deleted": True,
    }
    with database.SessionLocal() as db:
        assert [row.id for row in db.query(TerminalModel).all()] == ["new"]
        assert [row.terminal_id for row in db.query(WorktreeWriterLeaseModel).all()] == ["new"]
        receipt = db.get(SessionDeletionReceiptModel, "lifetime-old")
        assert receipt is not None
        assert receipt.session_name == "cao-reused"

    resolved = database.resolve_session_lifetime("lifetime-old")
    assert resolved["deleted"] is True
    assert resolved["terminals"] == []
    try:
        database.resolve_session_lifetime("cao-reused")
    except AmbiguousSessionIdentity:
        pass
    else:
        raise AssertionError("a raw name reused after deletion must remain ambiguous")


def test_exact_lifetime_tombstone_hides_retained_cleanup_authority(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add_all(
            [
                _terminal("clean", "lifetime", "cao-retained", "/work/clean"),
                _terminal("protected", "lifetime", "cao-retained", "/work/protected"),
                WorktreeWriterLeaseModel(
                    canonical_worktree="/work/protected", terminal_id="protected"
                ),
            ]
        )
        db.commit()

    result = database.delete_terminals_by_session_lifetime(
        "lifetime",
        "cao-retained",
        expected_terminal_ids=["clean", "protected"],
        retained_resources=[{"terminal_id": "protected", "reason_code": "MANAGED_WORKTREE_DIRTY"}],
    )
    replay = database.delete_terminals_by_session_lifetime(
        "lifetime",
        "cao-retained",
        expected_terminal_ids=["clean", "protected"],
    )

    assert result == {
        "deleted": 1,
        "logical_deleted": 2,
        "retained": 1,
        "retained_resources": [
            {"terminal_id": "protected", "reason_code": "MANAGED_WORKTREE_DIRTY"}
        ],
        "already_deleted": False,
    }
    assert replay == {
        "deleted": 0,
        "logical_deleted": 0,
        "retained": 1,
        "retained_resources": [
            {"terminal_id": "protected", "reason_code": "MANAGED_WORKTREE_DIRTY"}
        ],
        "already_deleted": True,
    }
    resolved = database.resolve_session_lifetime("lifetime")
    assert resolved["deleted"] is True
    assert resolved["retained_resources"] == result["retained_resources"]
    with database.SessionLocal() as db:
        assert db.get(TerminalModel, "clean") is None
        assert db.get(TerminalModel, "protected") is not None
        assert db.query(WorktreeWriterLeaseModel).count() == 0


def test_exact_exited_terminal_delete_reconciles_stale_leases_and_is_idempotent(monkeypatch):
    _install_database(monkeypatch)
    terminal = _terminal("exited", "lifetime", "cao-session", "/work/exited")
    with database.SessionLocal() as db:
        db.add_all(
            [
                terminal,
                WorktreeWriterLeaseModel(canonical_worktree="/work/exited", terminal_id="exited"),
                ProviderExecutionLeaseModel(terminal_id="exited", workflow_turn_id=77),
            ]
        )
        db.commit()
        expected = {
            field: getattr(terminal, field) for field in database._TERMINAL_DELETION_IDENTITY_FIELDS
        }
    first = database.delete_exited_terminal("exited", expected_identity=expected)
    second = database.delete_exited_terminal("exited", expected_identity=expected)

    assert first == {"deleted": 1, "already_deleted": False, "missing": False}
    assert second == {"deleted": 0, "already_deleted": True, "missing": False}
    assert database.terminal_deletion_receipt_exists("exited") is True
    with database.SessionLocal() as db:
        assert db.get(TerminalModel, "exited") is None
        assert db.get(TerminalDeletionReceiptModel, "exited") is not None
        assert db.query(WorktreeWriterLeaseModel).count() == 0
        assert db.query(ProviderExecutionLeaseModel).count() == 0


def test_exact_exited_terminal_delete_retires_managed_work_context_atomically(monkeypatch):
    _install_database(monkeypatch)
    terminal = _terminal("exited", "lifetime", "cao-session", "/work/exited")
    terminal.writable_work_context_id = "context-a"
    terminal.writer_authority_generation = "generation-a"
    terminal.managed_worktree_kind = "supervisor"
    terminal.managed_worktree_source = "/source/project-a"
    terminal.managed_worktree_branch = "cao/session/context-a"
    terminal.managed_worktree_commit = "b" * 40
    with database.SessionLocal() as db:
        db.add_all(
            [
                terminal,
                WritableWorkContextModel(
                    id="context-a",
                    request_id="00000000-0000-4000-8000-000000000095",
                    project_id="project-a",
                    session_id="lifetime",
                    terminal_id="exited",
                    canonical_source="/source/project-a",
                    canonical_worktree="/work/exited",
                    branch="cao/session/exited",
                    base_revision="a" * 40,
                    state="admitted",
                    writer_authority_generation="generation-a",
                ),
                WorktreeWriterLeaseModel(
                    canonical_worktree="/work/exited",
                    terminal_id="exited",
                    authority_generation="generation-a",
                ),
            ]
        )
        db.commit()
        expected = {
            field: getattr(terminal, field) for field in database._TERMINAL_DELETION_IDENTITY_FIELDS
        }

    assert (
        database.delete_exited_terminal(
            "exited",
            expected_identity=expected,
            workspace_cleanup_authority={
                "version": 1,
                "managed": True,
                "kind": "supervisor",
                "source": "/source/project-a",
                "path": "/work/exited",
                "branch": "cao/session/context-a",
                "branch_object_id": "b" * 40,
                "identity": "context-a",
                "path_absent": True,
                "git_unregistered": True,
            },
        )["deleted"]
        == 1
    )

    with database.SessionLocal() as db:
        assert db.get(TerminalModel, "exited") is None
        context = db.get(WritableWorkContextModel, "context-a")
        assert context.state == "retired"
        assert context.failure_reason is None
        audit = db.query(WritableWorkContextAuditModel).one()
        assert audit.event_type == "managed_worktree_retired"
        assert audit.terminal_id == "exited"
        assert db.query(WorktreeWriterLeaseModel).count() == 0


def test_exact_exited_terminal_delete_rejects_changed_identity(monkeypatch):
    _install_database(monkeypatch)
    terminal = _terminal("exited", "lifetime", "cao-session", "/work/exited")
    with database.SessionLocal() as db:
        db.add(terminal)
        db.commit()
        expected = {
            field: getattr(terminal, field) for field in database._TERMINAL_DELETION_IDENTITY_FIELDS
        }
    expected["tmux_window"] = "reused-window"
    try:
        database.delete_exited_terminal("exited", expected_identity=expected)
    except AmbiguousTerminalIdentity:
        pass
    else:
        raise AssertionError("changed terminal identity must remain protected")

    with database.SessionLocal() as db:
        assert db.get(TerminalModel, "exited") is not None
        assert db.get(TerminalDeletionReceiptModel, "exited") is None


def test_exact_terminal_delete_cannot_change_a_fenced_session_identity(monkeypatch):
    _install_database(monkeypatch)
    terminal = _terminal("exited", "lifetime", "cao-session", "/work/exited")
    with database.SessionLocal() as db:
        db.add(terminal)
        db.commit()
        expected = {
            field: getattr(terminal, field) for field in database._TERMINAL_DELETION_IDENTITY_FIELDS
        }

    started = database.begin_session_hard_deletion(
        "lifetime",
        "cao-session",
        expected_terminal_ids=["exited"],
        allow_dirty_workspace=False,
    )
    assert started["started"] is True

    with pytest.raises(AmbiguousTerminalIdentity):
        database.delete_exited_terminal("exited", expected_identity=expected)

    with database.SessionLocal() as db:
        assert db.get(TerminalModel, "exited") is not None
        assert db.get(TerminalDeletionReceiptModel, "exited") is None
        assert db.get(SessionDeletionOperationModel, "lifetime") is not None


def test_exited_terminal_reconciliation_transfers_a_legacy_shared_writer_lease(monkeypatch):
    _install_database(monkeypatch)
    exited = _terminal("exited", "lifetime", "cao-session", "/work/shared")
    replacement = _terminal("active", "lifetime", "cao-session", "/work/shared")
    replacement.runtime_lifecycle = "running"
    with database.SessionLocal() as db:
        db.add_all(
            [
                exited,
                replacement,
                WorktreeWriterLeaseModel(canonical_worktree="/work/shared", terminal_id="exited"),
            ]
        )
        db.commit()

    assert database.mark_terminal_runtime_exited("exited") is True
    with database.SessionLocal() as db:
        lease = db.get(WorktreeWriterLeaseModel, "/work/shared")
        assert lease is not None
        assert lease.terminal_id == "active"


def test_legacy_writer_release_never_reactivates_recovery_required_authority(monkeypatch):
    _install_database(monkeypatch)
    exited = _terminal("exited", "lifetime", "cao-session", "/work/shared")
    recovery = _terminal("recovery", "lifetime", "cao-session", "/work/shared")
    recovery.runtime_lifecycle = "recovery_required"
    with database.SessionLocal() as db:
        db.add_all(
            [
                exited,
                recovery,
                WorktreeWriterLeaseModel(canonical_worktree="/work/shared", terminal_id="exited"),
            ]
        )
        db.commit()

    assert database.mark_terminal_runtime_exited("exited") is True
    with database.SessionLocal() as db:
        assert db.get(WorktreeWriterLeaseModel, "/work/shared") is None
        assert db.get(TerminalModel, "recovery").runtime_lifecycle == "recovery_required"


def test_terminal_deletion_receipt_never_authorizes_a_reused_id(monkeypatch):
    _install_database(monkeypatch)
    original = _terminal("reused", "old-lifetime", "cao-old", "/work/old")
    with database.SessionLocal() as db:
        db.add(original)
        db.commit()
        expected = {
            field: getattr(original, field) for field in database._TERMINAL_DELETION_IDENTITY_FIELDS
        }
    assert database.delete_exited_terminal("reused", expected_identity=expected)["deleted"] == 1

    replacement = _terminal("reused", "new-lifetime", "cao-new", "/work/new")
    with database.SessionLocal() as db:
        db.add(replacement)
        db.commit()
        replacement_identity = {
            field: getattr(replacement, field)
            for field in database._TERMINAL_DELETION_IDENTITY_FIELDS
        }

    try:
        database.delete_exited_terminal("reused", expected_identity=replacement_identity)
    except AmbiguousTerminalIdentity:
        pass
    else:
        raise AssertionError("an old deletion receipt must not authorize a reused terminal ID")

    with database.SessionLocal() as db:
        assert db.get(TerminalModel, "reused") is not None


def test_changed_lifetime_aborts_before_deletion_or_receipt(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add_all(
            [
                _terminal("known", "lifetime-one", "cao-changing", "/work/known"),
                _terminal("late", "lifetime-one", "cao-changing", "/work/late"),
                WorktreeWriterLeaseModel(canonical_worktree="/work/known", terminal_id="known"),
                WorktreeWriterLeaseModel(canonical_worktree="/work/late", terminal_id="late"),
            ]
        )
        db.commit()

    try:
        database.delete_terminals_by_session_lifetime(
            "lifetime-one", "cao-changing", expected_terminal_ids=["known"]
        )
    except AmbiguousSessionIdentity:
        pass
    else:
        raise AssertionError("a changed lifetime inventory must abort transactionally")

    with database.SessionLocal() as db:
        assert {row.id for row in db.query(TerminalModel).all()} == {"known", "late"}
        assert {row.terminal_id for row in db.query(WorktreeWriterLeaseModel).all()} == {
            "known",
            "late",
        }
        assert db.get(SessionDeletionReceiptModel, "lifetime-one") is None


def test_project_inheritance_is_scoped_to_stable_lifetime(monkeypatch):
    _install_database(monkeypatch)
    with database.SessionLocal() as db:
        db.add_all(
            [
                _terminal(
                    "old",
                    "lifetime-old",
                    "cao-reused",
                    "/work/old",
                    project_id="project-old",
                ),
                _terminal(
                    "new",
                    "lifetime-new",
                    "cao-reused",
                    "/work/new",
                    project_id="project-new",
                ),
            ]
        )
        db.commit()

    assert database.get_session_project_id("lifetime-old") == "project-old"
    assert database.get_session_project_id("lifetime-new") == "project-new"
