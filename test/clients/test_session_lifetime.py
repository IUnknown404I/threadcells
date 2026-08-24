"""Stable session identity and idempotent retirement persistence tests."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.database import (
    AmbiguousSessionIdentity,
    Base,
    SessionDeletionReceiptModel,
    TerminalModel,
    WorktreeWriterLeaseModel,
)


def _install_database(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(database, "_ensure_terminal_worktree_authority_schema", lambda: None)
    monkeypatch.setattr(database, "_ensure_usage_schema", lambda: None)
    monkeypatch.setattr(database, "_ensure_session_deletion_receipt_schema", lambda: None)


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

    assert first == {"deleted": 1, "already_deleted": False}
    assert second == {"deleted": 0, "already_deleted": True}
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
