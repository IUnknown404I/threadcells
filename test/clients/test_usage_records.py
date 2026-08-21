"""Usage P1 persistence, aggregation and non-blocking observation tests."""

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.database import Base, UsageRecordModel
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.models.usage import UsageObservation
from cli_agent_orchestrator.services import terminal_service, usage_service


@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def _observation(identity: str) -> UsageObservation:
    return UsageObservation(
        source_run_identity=identity,
        extractor="codex_tui_completion_v1",
        input_tokens=10,
        cached_input_tokens=2,
        output_tokens=5,
        total_tokens=15,
    )


def test_usage_record_is_append_only_idempotent_and_aggregated(test_db, monkeypatch):
    monkeypatch.setattr(database, "SessionLocal", test_db)
    monkeypatch.setattr(database, "_ensure_usage_schema", lambda: None)
    kwargs = dict(
        provider="codex",
        agent_profile="developer",
        terminal_id="abc",
        terminal_name="developer-abc",
        session_id="session-one-id",
        session_name="cao-one",
        project_id="project-1",
        project_name="One",
        project_path="/one",
    )

    assert database.record_usage_observation(_observation("run-1"), **kwargs) is True
    assert database.record_usage_observation(_observation("run-1"), **kwargs) is False
    stats = database.get_usage_statistics()

    assert stats["global"] == {
        "provider_run_count": 1,
        "input_tokens": 10,
        "cached_input_tokens": 2,
        "cache_write_input_tokens": None,
        "output_tokens": 5,
        "reasoning_output_tokens": None,
        "total_tokens": 15,
    }
    assert stats["terminals"][0]["id"] == "abc"
    assert stats["terminals"][0]["label"] == "developer-abc"
    assert stats["sessions"][0]["id"] == "session-one-id"
    assert stats["sessions"][0]["label"] == "cao-one"
    assert stats["projects"][0]["id"] == "project-1"
    assert test_db().query(UsageRecordModel).count() == 1


def test_usage_statistics_aggregate_reported_cached_input_without_double_counting(
    test_db, monkeypatch
):
    monkeypatch.setattr(database, "SessionLocal", test_db)
    monkeypatch.setattr(database, "_ensure_usage_schema", lambda: None)
    kwargs = dict(
        provider="codex",
        agent_profile="developer",
        terminal_id="abc",
        terminal_name="developer-abc",
        session_id="session-one-id",
        session_name="cao-one",
        project_id="project-1",
        project_name="One",
        project_path="/one",
    )
    observations = [
        UsageObservation(
            source_run_identity="cached",
            extractor="fixture",
            input_tokens=100,
            cached_input_tokens=30,
            output_tokens=20,
            total_tokens=120,
        ),
        UsageObservation(
            source_run_identity="cache-miss",
            extractor="fixture",
            input_tokens=50,
            cached_input_tokens=0,
            output_tokens=5,
            total_tokens=55,
        ),
        UsageObservation(
            source_run_identity="historical-cache-absent",
            extractor="fixture",
            input_tokens=40,
            cached_input_tokens=None,
            output_tokens=3,
            total_tokens=43,
        ),
    ]
    for observation in observations:
        assert database.record_usage_observation(observation, **kwargs) is True

    stats = database.get_usage_statistics()
    expected = {
        "provider_run_count": 3,
        "input_tokens": 190,
        "cached_input_tokens": 30,
        "cache_write_input_tokens": None,
        "output_tokens": 28,
        "reasoning_output_tokens": None,
        "total_tokens": 218,
    }

    assert stats["global"] == expected
    assert {
        key: value for key, value in stats["terminals"][0].items() if key not in {"id", "label"}
    } == expected
    assert {
        key: value for key, value in stats["sessions"][0].items() if key not in {"id", "label"}
    } == expected
    assert {
        key: value for key, value in stats["projects"][0].items() if key not in {"id", "label"}
    } == expected
    assert stats["global"]["total_tokens"] == (
        stats["global"]["input_tokens"] + stats["global"]["output_tokens"]
    )


def test_usage_statistics_limits_and_orders_grouped_projections(test_db, monkeypatch):
    monkeypatch.setattr(database, "SessionLocal", test_db)
    monkeypatch.setattr(database, "_ensure_usage_schema", lambda: None)
    with test_db() as db:
        for index in range(12):
            db.add(
                UsageRecordModel(
                    source_run_identity=f"ranked-{index}",
                    extractor="fixture",
                    terminal_id=f"terminal-{index:02d}",
                    terminal_name=f"Terminal {index:02d}",
                    session_id=f"session-{index:02d}",
                    session_name=f"session-{index:02d}",
                    project_id=f"project-{index:02d}",
                    project_name=f"Project {index:02d}",
                    total_tokens=100 - index,
                )
            )
        for index, run_count in enumerate((2, 1)):
            for occurrence in range(run_count):
                db.add(
                    UsageRecordModel(
                        source_run_identity=f"unreported-{index}-{occurrence}",
                        extractor="fixture",
                        terminal_id=f"unreported-terminal-{index}",
                        terminal_name=f"Unreported terminal {index}",
                        session_id=f"unreported-session-{index}",
                        session_name=f"unreported-session-{index}",
                        project_id=f"unreported-project-{index}",
                        project_name=f"Unreported {index}",
                        total_tokens=None,
                    )
                )
        db.commit()

    stats = database.get_usage_statistics()

    for projection in (stats["terminals"], stats["sessions"], stats["projects"]):
        assert len(projection) == 10
        assert [row["total_tokens"] for row in projection] == list(range(100, 90, -1))
        assert [row["id"] for row in projection] == [
            f"{projection[0]['id'].rsplit('-', 1)[0]}-{index:02d}" for index in range(10)
        ]
    fallback = database._top_usage_rows(
        [
            {"id": "missing-b", "provider_run_count": 1, "total_tokens": None},
            {"id": "missing-a", "provider_run_count": 1, "total_tokens": None},
            {"id": "missing-runs", "provider_run_count": 2, "total_tokens": None},
        ]
    )
    assert [row["id"] for row in fallback] == ["missing-runs", "missing-a", "missing-b"]


def test_usage_project_projection_uses_historical_name_or_unknown_fallback(test_db, monkeypatch):
    monkeypatch.setattr(database, "SessionLocal", test_db)
    monkeypatch.setattr(database, "_ensure_usage_schema", lambda: None)
    with test_db() as db:
        db.add_all(
            [
                UsageRecordModel(
                    source_run_identity="known-project",
                    extractor="fixture",
                    project_id="project-known",
                    project_name="Persisted Project Name",
                    project_path="/historical/project",
                    total_tokens=10,
                ),
                UsageRecordModel(
                    source_run_identity="unknown-project",
                    extractor="fixture",
                    project_id="project-removed",
                    project_name=None,
                    project_path=None,
                    total_tokens=8,
                ),
            ]
        )
        db.commit()

    stats = database.get_usage_statistics()

    assert stats["global"]["total_tokens"] == 18
    assert stats["projects"] == [
        {
            "id": "project-known",
            "label": "Persisted Project Name",
            "provider_run_count": 1,
            "input_tokens": None,
            "cached_input_tokens": None,
            "cache_write_input_tokens": None,
            "output_tokens": None,
            "reasoning_output_tokens": None,
            "total_tokens": 10,
        },
        {
            "id": "project-removed",
            "label": "Unknown project",
            "provider_run_count": 1,
            "input_tokens": None,
            "cached_input_tokens": None,
            "cache_write_input_tokens": None,
            "output_tokens": None,
            "reasoning_output_tokens": None,
            "total_tokens": 8,
        },
    ]


def test_usage_project_rename_keeps_one_id_aggregate_with_current_title(test_db, monkeypatch):
    monkeypatch.setattr(database, "SessionLocal", test_db)
    monkeypatch.setattr(database, "_ensure_usage_schema", lambda: None)
    monkeypatch.setattr(database, "_ensure_project_schema", lambda: None)
    project_id = "project-threadcells-core"
    database.create_project(
        project_id=project_id,
        name="CAO core",
        normalized_name="cao core",
        path="/immutable/core/path",
        normalized_path="/immutable/core/path",
        description="core runtime",
        is_default=True,
    )
    with test_db() as db:
        db.add(
            UsageRecordModel(
                source_run_identity="before-project-rename",
                extractor="fixture",
                project_id=project_id,
                project_name="CAO core",
                project_path="/immutable/core/path",
                total_tokens=11,
            )
        )
        db.commit()

    database.update_project(
        project_id,
        name="ThreadCells core",
        normalized_name="threadcells core",
        path="/immutable/core/path",
        normalized_path="/immutable/core/path",
        description="core runtime",
        is_default=True,
    )
    with test_db() as db:
        db.add(
            UsageRecordModel(
                source_run_identity="after-project-rename",
                extractor="fixture",
                project_id=project_id,
                project_name="ThreadCells core",
                project_path="/immutable/core/path",
                total_tokens=13,
            )
        )
        db.commit()

    assert database.get_usage_statistics()["projects"] == [
        {
            "id": project_id,
            "label": "ThreadCells core",
            "provider_run_count": 2,
            "input_tokens": None,
            "cached_input_tokens": None,
            "cache_write_input_tokens": None,
            "output_tokens": None,
            "reasoning_output_tokens": None,
            "total_tokens": 24,
        }
    ]


def test_usage_project_projection_uses_unknown_label_for_null_id_aggregate(test_db, monkeypatch):
    monkeypatch.setattr(database, "SessionLocal", test_db)
    monkeypatch.setattr(database, "_ensure_usage_schema", lambda: None)
    with test_db() as db:
        db.add_all(
            [
                UsageRecordModel(
                    source_run_identity="null-project-first-name",
                    extractor="fixture",
                    project_id=None,
                    project_name="First unavailable project",
                    total_tokens=7,
                ),
                UsageRecordModel(
                    source_run_identity="null-project-second-name",
                    extractor="fixture",
                    project_id=None,
                    project_name="Second unavailable project",
                    total_tokens=5,
                ),
            ]
        )
        db.commit()

    assert database.get_usage_statistics()["projects"] == [
        {
            "label": "Unknown project",
            "provider_run_count": 2,
            "input_tokens": None,
            "cached_input_tokens": None,
            "cache_write_input_tokens": None,
            "output_tokens": None,
            "reasoning_output_tokens": None,
            "total_tokens": 12,
        }
    ]


def test_usage_schema_migration_is_additive(tmp_path, monkeypatch):
    database_file = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{database_file}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE terminals (id TEXT PRIMARY KEY)")
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "_usage_schema_ready", False)

    database._ensure_usage_schema()

    with engine.connect() as connection:
        tables = {row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master")}
        terminal_columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(terminals)")
        }
    assert "usage_records" in tables
    assert terminal_columns == {"id", "session_id"}


@pytest.mark.parametrize("terminal_schema", [None, "CREATE TABLE terminals (id TEXT PRIMARY KEY)"])
def test_pre_p2_usage_migration_keeps_projections_honest_without_live_terminal_evidence(
    tmp_path, monkeypatch, terminal_schema
):
    """The P2 columns exist even when legacy terminal evidence cannot backfill them."""
    database_file = tmp_path / "usage-p1-without-live-terminals.db"
    engine = create_engine(f"sqlite:///{database_file}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE usage_records ("
            "id INTEGER PRIMARY KEY, source_run_identity TEXT NOT NULL UNIQUE, "
            "extractor TEXT NOT NULL, input_tokens INTEGER, cached_input_tokens INTEGER, "
            "output_tokens INTEGER, total_tokens INTEGER, model TEXT, agent_profile TEXT, "
            "terminal_id TEXT, session_name TEXT, project_id TEXT, project_name TEXT, "
            "project_path TEXT, recorded_at DATETIME)"
        )
        connection.exec_driver_sql(
            "INSERT INTO usage_records "
            "(source_run_identity, extractor, terminal_id, session_name, project_id, "
            "project_name, total_tokens, recorded_at) VALUES "
            "('legacy-run', 'fixture', 'missing-terminal', 'reused-name', 'project-1', "
            "'Historical Project', 10, CURRENT_TIMESTAMP)"
        )
        if terminal_schema:
            connection.exec_driver_sql(terminal_schema)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(database, "_usage_schema_ready", False)

    database._ensure_usage_schema()
    database._migrate_usage_identity_columns()

    with engine.connect() as connection:
        usage_columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(usage_records)")
        }
    assert {
        "terminal_name",
        "session_id",
        "provider",
        "cache_write_input_tokens",
        "reasoning_output_tokens",
        "superseded_by_source_identity",
        "updated_at",
    }.issubset(usage_columns)
    stats = database.get_usage_statistics()
    assert stats["global"]["total_tokens"] == 10
    assert stats["global"]["cache_write_input_tokens"] is None
    assert stats["global"]["reasoning_output_tokens"] is None
    assert stats["terminals"][0]["id"] == "missing-terminal"
    assert stats["sessions"][0]["id"] == "legacy-session-record:1"
    assert stats["sessions"][0]["legacy"] is True
    assert stats["projects"][0]["id"] == "project-1"
    assert stats["providers"][0]["label"] == "Unknown provider"
    assert stats["profiles"][0]["label"] == "Unknown profile"


def test_usage_identity_backfill_requires_matching_live_terminal_evidence(tmp_path, monkeypatch):
    database_file = tmp_path / "usage-p1.db"
    engine = create_engine(f"sqlite:///{database_file}")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO terminals (id, tmux_session, tmux_window, provider) "
            "VALUES ('live-terminal', 'cao-live', 'developer-old', 'codex')"
        )
        connection.exec_driver_sql(
            "INSERT INTO usage_records "
            "(source_run_identity, extractor, terminal_id, session_name, total_tokens, recorded_at) "
            "VALUES ('matching-live', 'fixture', 'live-terminal', 'cao-live', 10, CURRENT_TIMESTAMP)"
        )
        connection.exec_driver_sql(
            "INSERT INTO usage_records "
            "(source_run_identity, extractor, terminal_id, session_name, total_tokens, recorded_at) "
            "VALUES ('deleted-terminal', 'fixture', 'missing-terminal', 'cao-live', 8, CURRENT_TIMESTAMP)"
        )
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "_usage_schema_ready", False)

    database._ensure_usage_schema()

    with engine.connect() as connection:
        live_session_id = connection.exec_driver_sql(
            "SELECT session_id FROM terminals WHERE id = 'live-terminal'"
        ).scalar_one()
        rows = connection.exec_driver_sql(
            "SELECT source_run_identity, session_id, terminal_name "
            "FROM usage_records ORDER BY source_run_identity"
        ).fetchall()
    assert str(live_session_id).startswith("legacy-session-v2:")
    assert rows == [
        ("deleted-terminal", None, None),
        ("matching-live", live_session_id, "developer-old"),
    ]


def test_usage_identity_backfill_requires_an_exact_recorded_session_name(tmp_path, monkeypatch):
    database_file = tmp_path / "usage-exact-session-name.db"
    engine = create_engine(f"sqlite:///{database_file}")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO terminals (id, tmux_session, tmux_window, provider) "
            "VALUES ('live-terminal', 'cao-live', 'developer-live', 'codex')"
        )
        connection.exec_driver_sql(
            "INSERT INTO usage_records "
            "(source_run_identity, extractor, terminal_id, session_name, total_tokens, recorded_at) "
            "VALUES ('matching-name', 'fixture', 'live-terminal', 'cao-live', 10, CURRENT_TIMESTAMP)"
        )
        connection.exec_driver_sql(
            "INSERT INTO usage_records "
            "(source_run_identity, extractor, terminal_id, session_name, total_tokens, recorded_at) "
            "VALUES ('mismatched-name', 'fixture', 'live-terminal', 'cao-other', 8, CURRENT_TIMESTAMP)"
        )
        connection.exec_driver_sql(
            "INSERT INTO usage_records "
            "(source_run_identity, extractor, terminal_id, session_name, total_tokens, recorded_at) "
            "VALUES ('missing-name', 'fixture', 'live-terminal', NULL, 6, CURRENT_TIMESTAMP)"
        )
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(database, "_usage_schema_ready", False)

    database._ensure_usage_schema()

    with engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT source_run_identity, session_id FROM usage_records ORDER BY source_run_identity"
        ).fetchall()
    assert rows[0][1] is not None
    assert rows[1:] == [("mismatched-name", None), ("missing-name", None)]

    legacy = [row for row in database.get_usage_statistics()["sessions"] if row.get("legacy")]
    assert {(row["label"], row["total_tokens"]) for row in legacy} == {
        ("cao-other", 8),
        ("Unknown session", 6),
    }


def test_legacy_session_rows_never_merge_reused_names(test_db, monkeypatch):
    monkeypatch.setattr(database, "SessionLocal", test_db)
    monkeypatch.setattr(database, "_ensure_usage_schema", lambda: None)
    with test_db() as db:
        db.add_all(
            [
                UsageRecordModel(
                    source_run_identity="legacy-first",
                    extractor="fixture",
                    session_name="cao-reused",
                    total_tokens=9,
                ),
                UsageRecordModel(
                    source_run_identity="legacy-second",
                    extractor="fixture",
                    session_name="cao-reused",
                    total_tokens=7,
                ),
            ]
        )
        db.commit()

    sessions = database.get_usage_statistics()["sessions"]
    assert [
        (row["id"], row["label"], row["total_tokens"], row.get("legacy")) for row in sessions
    ] == [
        ("legacy-session-record:1", "cao-reused", 9, True),
        ("legacy-session-record:2", "cao-reused", 7, True),
    ]


def test_usage_ledger_survives_terminal_session_and_project_deletion(test_db, monkeypatch):
    """Statistics must read ledger snapshots, never the deleted live metadata."""
    monkeypatch.setattr(database, "SessionLocal", test_db)
    monkeypatch.setattr(database, "_ensure_usage_schema", lambda: None)
    monkeypatch.setattr(database, "_ensure_project_schema", lambda: None)
    monkeypatch.setattr(database, "_terminal_authority_schema_ready", True)
    database.create_project(
        project_id="project-deleted",
        name="Historical Project",
        normalized_name="historical project",
        path="/historical-project",
        normalized_path="/historical-project",
        description=None,
        is_default=True,
    )
    terminal = database.create_terminal("agent-deleted", "cao-deleted", "developer-old", "codex")
    assert database.record_usage_observation(
        _observation("terminal-deleted"),
        provider="codex",
        agent_profile="developer",
        terminal_id=terminal["id"],
        terminal_name=terminal["tmux_window"],
        session_id=terminal["session_id"],
        session_name=terminal["tmux_session"],
        project_id="project-deleted",
        project_name="Historical Project",
        project_path="/historical-project",
    )
    assert database.delete_terminal("agent-deleted")

    session_terminal = database.create_terminal(
        "session-deleted", "cao-session-deleted", "developer-session", "codex"
    )
    assert database.record_usage_observation(
        _observation("session-deleted"),
        provider="codex",
        agent_profile="developer",
        terminal_id=session_terminal["id"],
        terminal_name=session_terminal["tmux_window"],
        session_id=session_terminal["session_id"],
        session_name=session_terminal["tmux_session"],
        project_id="project-deleted",
        project_name="Historical Project",
        project_path="/historical-project",
    )
    assert database.delete_terminals_by_session("cao-session-deleted") == 1
    assert database.delete_project("project-deleted")

    stats = database.get_usage_statistics()
    assert stats["global"]["total_tokens"] == 30
    assert {(row["id"], row["label"]) for row in stats["terminals"]} == {
        ("agent-deleted", "developer-old"),
        ("session-deleted", "developer-session"),
    }
    assert {(row["id"], row["label"]) for row in stats["sessions"]} == {
        (terminal["session_id"], "cao-deleted"),
        (session_terminal["session_id"], "cao-session-deleted"),
    }
    assert stats["projects"] == [
        {
            "id": "project-deleted",
            "label": "Historical Project",
            "provider_run_count": 2,
            "input_tokens": 20,
            "cached_input_tokens": 4,
            "cache_write_input_tokens": None,
            "output_tokens": 10,
            "reasoning_output_tokens": None,
            "total_tokens": 30,
        }
    ]


def test_same_session_name_recreation_keeps_distinct_historical_identities(test_db, monkeypatch):
    monkeypatch.setattr(database, "SessionLocal", test_db)
    monkeypatch.setattr(database, "_ensure_usage_schema", lambda: None)
    monkeypatch.setattr(database, "_terminal_authority_schema_ready", True)

    first = database.create_terminal("first", "cao-reused", "developer-first", "codex")
    assert database.record_usage_observation(
        _observation("first-lifetime"),
        provider="codex",
        agent_profile="developer",
        terminal_id=first["id"],
        terminal_name=first["tmux_window"],
        session_id=first["session_id"],
        session_name=first["tmux_session"],
        project_id=None,
        project_name=None,
        project_path=None,
    )
    assert database.delete_terminals_by_session("cao-reused") == 1

    second = database.create_terminal("second", "cao-reused", "developer-second", "codex")
    assert second["session_id"] != first["session_id"]
    assert database.record_usage_observation(
        _observation("second-lifetime"),
        provider="codex",
        agent_profile="developer",
        terminal_id=second["id"],
        terminal_name=second["tmux_window"],
        session_id=second["session_id"],
        session_name=second["tmux_session"],
        project_id=None,
        project_name=None,
        project_path=None,
    )

    sessions = database.get_usage_statistics()["sessions"]
    assert {(row["id"], row["label"], row["total_tokens"]) for row in sessions} == {
        (first["session_id"], "cao-reused", 15),
        (second["session_id"], "cao-reused", 15),
    }


def test_usage_extractor_failure_does_not_change_provider_completion():
    provider = MagicMock()
    provider.get_status.return_value = TerminalStatus.COMPLETED
    provider.extract_usage_observation.side_effect = ValueError("malformed usage capture")
    metadata = {
        "id": "abc",
        "runtime_lifecycle": "running",
        "provider": "codex",
        "tmux_session": "cao",
        "tmux_window": "worker",
        "agent_profile": "developer",
        "last_active": None,
    }
    with (
        patch.object(terminal_service, "get_terminal_metadata", return_value=metadata),
        patch.object(terminal_service.provider_manager, "get_provider", return_value=provider),
        patch.object(terminal_service, "reconcile_terminal_runtime", return_value=False),
        patch.object(
            terminal_service,
            "get_terminal_workflow_projection",
            return_value={
                "state": "active",
                "workflow_status": None,
                "assignment_status": None,
                "result_status": None,
                "delivery_status": None,
            },
        ),
        patch(
            "cli_agent_orchestrator.clients.tmux.tmux_client.get_history",
            return_value="capture",
        ),
    ):
        result = terminal_service.get_terminal("abc")

    assert result["status"] == "completed"


def test_usage_storage_failure_does_not_change_provider_completion():
    provider = MagicMock()
    provider.get_status.return_value = TerminalStatus.COMPLETED
    provider.extract_usage_observation.return_value = _observation("run-storage-failure")
    metadata = {
        "id": "abc",
        "runtime_lifecycle": "running",
        "provider": "codex",
        "tmux_session": "cao",
        "tmux_window": "worker",
        "agent_profile": "developer",
        "last_active": None,
    }
    with (
        patch.object(terminal_service, "get_terminal_metadata", return_value=metadata),
        patch.object(terminal_service.provider_manager, "get_provider", return_value=provider),
        patch.object(terminal_service, "reconcile_terminal_runtime", return_value=False),
        patch.object(
            terminal_service,
            "get_terminal_workflow_projection",
            return_value={
                "state": "active",
                "workflow_status": None,
                "assignment_status": None,
                "result_status": None,
                "delivery_status": None,
            },
        ),
        patch(
            "cli_agent_orchestrator.clients.tmux.tmux_client.get_history", return_value="capture"
        ),
        patch.object(
            usage_service, "record_usage_observation", side_effect=OSError("db unavailable")
        ),
    ):
        result = terminal_service.get_terminal("abc")

    assert result["status"] == "completed"
