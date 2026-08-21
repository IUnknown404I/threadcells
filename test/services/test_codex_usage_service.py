"""Codex durable usage ingestion and deterministic repair coverage."""

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.clients.database import (
    Base,
    ProviderUsageBindingModel,
    UsageRecordModel,
)
from cli_agent_orchestrator.models.usage import UsageObservation
from cli_agent_orchestrator.services import codex_usage_service

SESSION_ID = "01a0212b-a290-7641-9866-3d4306d3c105"
CHILD_SESSION_ID = "01a0212b-a290-7641-9866-3d4306d3c106"


@pytest.fixture
def test_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setattr(database, "_ensure_usage_schema", lambda: None)
    return factory


def _meta(session_id: str, *, timestamp: str = "2026-08-20T12:00:05Z", source="cli"):
    return {
        "timestamp": timestamp,
        "type": "session_meta",
        "payload": {
            "id": session_id,
            "session_id": session_id,
            "timestamp": timestamp,
            "cwd": "/work/repository",
            "originator": "codex-tui",
            "source": source,
        },
    }


def _token_count(total: int, *, input_tokens: int, cached: int, output: int, reasoning: int):
    return {
        "timestamp": "2026-08-20T12:00:06Z",
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached,
                    "cache_write_input_tokens": 0,
                    "output_tokens": output,
                    "reasoning_output_tokens": reasoning,
                    "total_tokens": total,
                }
            },
        },
    }


def _write_rollout(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_rollout_chunk_uses_latest_cumulative_totals_without_summing_checkpoints(tmp_path):
    path = tmp_path / f"rollout-2026-08-20T12-00-05-{SESSION_ID}.jsonl"
    _write_rollout(
        path,
        [
            _meta(SESSION_ID),
            {"type": "turn_context", "payload": {"model": "gpt-test"}},
            _token_count(100, input_tokens=80, cached=30, output=20, reasoning=5),
            _token_count(160, input_tokens=125, cached=60, output=35, reasoning=8),
        ],
    )

    first = codex_usage_service.read_codex_usage_chunk(
        path, provider_session_id=SESSION_ID, byte_offset=0, byte_budget=1024 * 1024
    )

    assert first.observation == UsageObservation(
        source_run_identity=first.observation.source_run_identity,
        extractor="codex_rollout_session_v1",
        model="gpt-test",
        input_tokens=125,
        cached_input_tokens=60,
        cache_write_input_tokens=0,
        output_tokens=35,
        reasoning_output_tokens=8,
        total_tokens=160,
    )
    assert first.next_byte_offset == path.stat().st_size

    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(_token_count(190, input_tokens=150, cached=70, output=40, reasoning=9))
            + "\n"
        )
    second = codex_usage_service.read_codex_usage_chunk(
        path,
        provider_session_id=SESSION_ID,
        byte_offset=first.next_byte_offset,
        byte_budget=1024 * 1024,
    )
    assert second.observation.total_tokens == 190
    assert second.observation.model is None


def test_checkpoint_is_restart_idempotent_and_supersedes_same_terminal_tui_rows(test_db):
    assert database.bind_provider_usage_session(
        provider="codex",
        provider_session_id=SESSION_ID,
        terminal_id="terminal-one",
        source="fixture",
    )
    with test_db() as db:
        db.add(
            UsageRecordModel(
                source_run_identity="legacy-pane-row",
                extractor="codex_tui_completion_v2",
                provider="codex",
                agent_profile="developer",
                terminal_id="terminal-one",
                terminal_name="developer-one",
                session_id="threadcells-session",
                session_name="cao-one",
                total_tokens=40,
            )
        )
        db.commit()

    observation = UsageObservation(
        source_run_identity="ignored-provider-identity",
        extractor="codex_rollout_session_v1",
        model="gpt-test",
        input_tokens=100,
        cached_input_tokens=50,
        cache_write_input_tokens=0,
        output_tokens=25,
        reasoning_output_tokens=7,
        total_tokens=125,
    )
    kwargs = dict(
        provider="codex",
        provider_session_id=SESSION_ID,
        terminal_id="terminal-one",
        terminal_name="developer-one",
        session_id="threadcells-session",
        session_name="cao-one",
        agent_profile="developer",
        project_id="project-one",
        project_name="Project One",
        project_path="/work/repository",
        next_byte_offset=900,
    )
    assert database.record_provider_usage_checkpoint(observation, **kwargs) == "updated"
    assert database.record_provider_usage_checkpoint(observation, **kwargs) == "updated"

    with test_db() as db:
        assert db.query(UsageRecordModel).count() == 2
        legacy = db.query(UsageRecordModel).filter_by(source_run_identity="legacy-pane-row").one()
        assert legacy.superseded_by_source_identity is not None
        binding = db.query(ProviderUsageBindingModel).one()
        assert binding.byte_offset == 900

    stats = database.get_usage_statistics()
    assert stats["global"]["total_tokens"] == 125
    assert stats["global"]["cached_input_tokens"] == 50
    assert stats["global"]["reasoning_output_tokens"] == 7
    assert stats["providers"][0]["id"] == "codex"
    assert stats["profiles"][0]["id"] == "developer"


def test_retained_running_terminal_refreshes_without_completion_or_duplicate(
    test_db, tmp_path, monkeypatch
):
    path = tmp_path / f"rollout-live-{SESSION_ID}.jsonl"
    _write_rollout(
        path,
        [
            _meta(SESSION_ID),
            {"type": "turn_context", "payload": {"model": "gpt-test"}},
            _token_count(125, input_tokens=100, cached=50, output=25, reasoning=7),
        ],
    )
    meta = codex_usage_service.CodexSessionMeta(
        SESSION_ID,
        1005.0,
        Path("/work/repository"),
        "cli",
        "codex-tui",
        None,
    )
    monkeypatch.setattr(codex_usage_service, "_rollout_index", lambda: {SESSION_ID: (path, meta)})
    assert database.bind_provider_usage_session(
        provider="codex",
        provider_session_id=SESSION_ID,
        terminal_id="terminal-live",
        source="fixture",
    )
    metadata = {
        "id": "terminal-live",
        "provider": "codex",
        "tmux_session": "cao-live",
        "tmux_window": "developer-live",
        "session_id": "threadcells-live",
        "agent_profile": "developer",
        "runtime_lifecycle": "running",
    }

    first = codex_usage_service.observe_codex_terminal_usage(metadata)
    replay = codex_usage_service.observe_codex_terminal_usage(metadata)

    assert first["records_updated"] == 1
    assert replay["bytes_processed"] == 0
    assert database.get_usage_statistics()["global"]["total_tokens"] == 125
    with test_db() as db:
        assert db.query(UsageRecordModel).count() == 1

    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(_token_count(150, input_tokens=120, cached=60, output=30, reasoning=8))
            + "\n"
        )
    resumed = codex_usage_service.observe_codex_terminal_usage(metadata)
    assert resumed["records_updated"] == 1
    assert database.get_usage_statistics()["global"]["total_tokens"] == 150
    with test_db() as db:
        assert db.query(UsageRecordModel).count() == 1


def test_historical_repair_requires_unique_birth_cwd_and_binds_native_child(
    test_db, tmp_path, monkeypatch
):
    root_path = tmp_path / f"rollout-root-{SESSION_ID}.jsonl"
    child_path = tmp_path / f"rollout-child-{CHILD_SESSION_ID}.jsonl"
    root_meta = codex_usage_service.CodexSessionMeta(
        SESSION_ID, 1005.0, Path("/work/repository"), "cli", "codex-tui", None
    )
    child_meta = codex_usage_service.CodexSessionMeta(
        CHILD_SESSION_ID,
        1006.0,
        Path("/work/repository"),
        {"subagent": {"thread_spawn": {"parent_thread_id": SESSION_ID}}},
        "codex-tui",
        SESSION_ID,
    )
    monkeypatch.setattr(
        codex_usage_service,
        "_rollout_index",
        lambda: {SESSION_ID: (root_path, root_meta), CHILD_SESSION_ID: (child_path, child_meta)},
    )
    monkeypatch.setattr(
        codex_usage_service,
        "list_all_terminals",
        lambda: [
            {
                "id": "terminal-one",
                "provider": "codex",
                "launch_worktree": "/work/repository",
            }
        ],
    )
    monkeypatch.setattr(codex_usage_service, "_file_birth_timestamp", lambda _path: 1000.0)

    result = codex_usage_service.repair_codex_usage_bindings()

    assert result["root_bindings"] == 1
    assert result["child_bindings"] == 1
    bindings = database.list_provider_usage_bindings(provider="codex")
    assert {row["provider_session_id"] for row in bindings} == {
        SESSION_ID,
        CHILD_SESSION_ID,
    }
    assert {row["terminal_id"] for row in bindings} == {"terminal-one"}


def test_live_refresh_discovers_child_created_after_initial_root_binding(
    test_db, tmp_path, monkeypatch
):
    root_path = tmp_path / f"rollout-root-{SESSION_ID}.jsonl"
    child_path = tmp_path / f"rollout-child-{CHILD_SESSION_ID}.jsonl"
    _write_rollout(
        root_path,
        [_meta(SESSION_ID), _token_count(100, input_tokens=80, cached=30, output=20, reasoning=5)],
    )
    _write_rollout(
        child_path,
        [
            _meta(
                CHILD_SESSION_ID,
                source={"subagent": {"thread_spawn": {"parent_thread_id": SESSION_ID}}},
            ),
            _token_count(40, input_tokens=30, cached=10, output=10, reasoning=2),
        ],
    )
    root_meta = codex_usage_service.CodexSessionMeta(
        SESSION_ID, 1005.0, Path("/work/repository"), "cli", "codex-tui", None
    )
    child_meta = codex_usage_service.CodexSessionMeta(
        CHILD_SESSION_ID,
        1006.0,
        Path("/work/repository"),
        {"subagent": {"thread_spawn": {"parent_thread_id": SESSION_ID}}},
        "codex-tui",
        SESSION_ID,
    )
    index = {SESSION_ID: (root_path, root_meta)}
    monkeypatch.setattr(codex_usage_service, "_rollout_index", lambda: dict(index))
    assert database.bind_provider_usage_session(
        provider="codex",
        provider_session_id=SESSION_ID,
        terminal_id="terminal-live-child",
        source="fixture",
    )
    metadata = {
        "id": "terminal-live-child",
        "provider": "codex",
        "tmux_session": "cao-live-child",
        "tmux_window": "developer-live-child",
        "session_id": "threadcells-live-child",
        "agent_profile": "developer",
        "runtime_lifecycle": "running",
    }

    initial = codex_usage_service.observe_codex_terminal_usage(metadata)
    assert initial["binding_count"] == 1
    index[CHILD_SESSION_ID] = (child_path, child_meta)

    discovered = codex_usage_service.observe_codex_terminal_usage(metadata)
    replay = codex_usage_service.observe_codex_terminal_usage(metadata)

    assert discovered["binding_count"] == 2
    assert discovered["records_updated"] == 1
    assert replay["bytes_processed"] == 0
    assert database.get_usage_statistics()["global"]["total_tokens"] == 140
    with test_db() as db:
        assert db.query(ProviderUsageBindingModel).count() == 2
        assert db.query(UsageRecordModel).count() == 2
