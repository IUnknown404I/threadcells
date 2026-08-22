"""Performance contracts for the Home/Agents and legacy operational APIs."""

from __future__ import annotations

import asyncio
import statistics
import threading
import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from cli_agent_orchestrator.api import main as api_main
from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.plugins import PluginRegistry
from cli_agent_orchestrator.services import control_plane_registry


def _terminal_response(terminal_id: str) -> dict:
    return {
        "id": terminal_id,
        "name": terminal_id,
        "provider": "codex",
        "session_name": "cao-performance",
        "agent_profile": "developer",
        "status": "idle",
        "execution_state": "ready",
        "lifecycle": "running",
        "workflow_state": "active",
        "last_active": None,
    }


@pytest.mark.asyncio
async def test_legacy_terminal_observation_cannot_stall_health_or_grow_workers_unbounded(
    monkeypatch,
):
    """The old polling path is isolated even if a compatibility client still calls it.

    Provider status observation captures tmux output and may inventory processes.
    It must never run on the ASGI event loop or consume a worker per historical
    terminal while requests are queued.
    """

    entered = threading.Event()
    release = threading.Event()
    state_lock = threading.Lock()
    active = 0
    maximum_active = 0
    worker_ids: set[int] = set()

    def blocking_terminal_observation(terminal_id: str) -> dict:
        nonlocal active, maximum_active
        with state_lock:
            active += 1
            maximum_active = max(maximum_active, active)
            worker_ids.add(threading.get_ident())
            if active == api_main.OPERATIONAL_IO_MAX_CONCURRENCY:
                entered.set()
        try:
            assert release.wait(timeout=2)
            return _terminal_response(terminal_id)
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(api_main.terminal_service, "get_terminal", blocking_terminal_observation)
    api_main.app.state.plugin_registry = PluginRegistry()
    transport = httpx.ASGITransport(app=api_main.app)
    requests = []
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            requests = [
                asyncio.create_task(
                    client.get(f"/terminals/{index:08x}", headers={"Host": "localhost"})
                )
                for index in range(12)
            ]
            assert await asyncio.to_thread(entered.wait, 1)

            started = time.perf_counter()
            health = await asyncio.wait_for(
                client.get("/health", headers={"Host": "localhost"}), timeout=0.2
            )
            health_elapsed = time.perf_counter() - started
            await asyncio.sleep(0.03)

            assert health.status_code == 200
            assert health_elapsed < 0.15
            assert maximum_active == api_main.OPERATIONAL_IO_MAX_CONCURRENCY
            assert active == api_main.OPERATIONAL_IO_MAX_CONCURRENCY
            assert len(worker_ids) <= api_main.OPERATIONAL_IO_MAX_CONCURRENCY

            release.set()
            responses = await asyncio.wait_for(asyncio.gather(*requests), timeout=2)
    finally:
        release.set()
        if requests:
            await asyncio.gather(*requests, return_exceptions=True)

    assert all(response.status_code == 200 for response in responses)
    assert maximum_active == api_main.OPERATIONAL_IO_MAX_CONCURRENCY
    assert len(worker_ids) <= api_main.OPERATIONAL_IO_MAX_CONCURRENCY


@pytest.mark.asyncio
async def test_workflow_reconciliation_cannot_block_the_request_event_loop(monkeypatch):
    """The one-second durable workflow loop may observe tmux, but never inline."""

    entered = threading.Event()
    release = threading.Event()

    def blocking_reconciliation(_registry=None) -> None:
        entered.set()
        assert release.wait(timeout=2)

    monkeypatch.setattr(
        api_main.inbox_service,
        "reconcile_handoff_continuations",
        blocking_reconciliation,
    )
    monkeypatch.setattr(api_main.workflow_service, "reconcile_open_workflows", lambda *_a: 0)
    api_main.app.state.plugin_registry = PluginRegistry()
    daemon = asyncio.create_task(api_main.workflow_daemon())
    transport = httpx.ASGITransport(app=api_main.app)
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            started = time.perf_counter()
            response = await asyncio.wait_for(
                client.get("/health", headers={"Host": "localhost"}), timeout=0.2
            )
            elapsed = time.perf_counter() - started
        assert response.status_code == 200
        assert elapsed < 0.15
    finally:
        release.set()
        daemon.cancel()
        with pytest.raises(asyncio.CancelledError):
            await daemon


@pytest.mark.asyncio
async def test_cancelled_caller_does_not_release_worker_lane_before_completion():
    """A disconnected request cannot manufacture capacity for another worker."""

    first_entered = threading.Event()
    second_entered = threading.Event()
    release_first = threading.Event()
    release_second = threading.Event()
    call_lock = threading.Lock()
    call_count = 0

    def blocking_reconciliation() -> None:
        nonlocal call_count
        with call_lock:
            call_count += 1
            current = call_count
        if current == 1:
            first_entered.set()
            assert release_first.wait(timeout=2)
        else:
            second_entered.set()
            assert release_second.wait(timeout=2)

    first = asyncio.create_task(api_main._run_workflow_io(blocking_reconciliation))
    second = None
    try:
        assert await asyncio.to_thread(first_entered.wait, 1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

        second = asyncio.create_task(api_main._run_workflow_io(blocking_reconciliation))
        assert not await asyncio.to_thread(second_entered.wait, 0.1)
        assert call_count == 1

        release_first.set()
        assert await asyncio.to_thread(second_entered.wait, 1)
        assert call_count == 2
        release_second.set()
        await asyncio.wait_for(second, timeout=1)
    finally:
        release_first.set()
        release_second.set()
        if second is not None:
            await asyncio.gather(second, return_exceptions=True)


@pytest.mark.asyncio
async def test_blocked_restart_recovery_begins_only_after_health_is_available(
    monkeypatch,
):
    """Tmux-capable restart recovery must not delay ASGI startup or health."""

    runtime_entered = threading.Event()
    workflow_entered = threading.Event()
    release = threading.Event()

    def blocked_runtime_recovery() -> int:
        runtime_entered.set()
        assert release.wait(timeout=2)
        return 0

    def blocked_workflow_recovery(_registry=None) -> int:
        workflow_entered.set()
        assert release.wait(timeout=2)
        return 0

    async def dormant_flow_daemon() -> None:
        await asyncio.Event().wait()

    observer = MagicMock()
    monkeypatch.setattr(api_main, "setup_logging", lambda: None)
    monkeypatch.setattr(api_main, "init_db", lambda: None)
    monkeypatch.setattr(api_main, "PollingObserver", lambda **_kwargs: observer)
    monkeypatch.setattr(api_main, "flow_daemon", dormant_flow_daemon)
    monkeypatch.setattr(
        control_plane_registry,
        "initialize_control_plane_registries",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        api_main.terminal_service,
        "reconcile_legacy_runtime_identities",
        blocked_runtime_recovery,
    )
    monkeypatch.setattr(api_main.terminal_service, "reconcile_terminal_context_roles", lambda: 0)
    monkeypatch.setattr(
        api_main.inbox_service, "reconcile_pending_messages", blocked_workflow_recovery
    )
    monkeypatch.setattr(api_main.workflow_service, "reconcile_open_workflows", lambda *_a: 0)
    monkeypatch.setattr(PluginRegistry, "load", AsyncMock())
    monkeypatch.setattr(PluginRegistry, "teardown", AsyncMock())

    started = time.perf_counter()
    try:
        async with api_main.lifespan(api_main.app):
            startup_elapsed = time.perf_counter() - started
            assert startup_elapsed < 0.15
            assert await asyncio.to_thread(runtime_entered.wait, 1)
            assert await asyncio.to_thread(workflow_entered.wait, 1)

            transport = httpx.ASGITransport(app=api_main.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://localhost"
            ) as client:
                health_started = time.perf_counter()
                health = await asyncio.wait_for(
                    client.get("/health", headers={"Host": "localhost"}), timeout=0.2
                )
                health_elapsed = time.perf_counter() - health_started
            assert health.status_code == 200
            assert health_elapsed < 0.15
            release.set()
    finally:
        release.set()


def _install_large_history(monkeypatch, tmp_path, *, terminal_count: int = 2000):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ui-performance.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    database.Base.metadata.create_all(engine)
    now = datetime(2026, 8, 22, 10, 0, 0)
    with engine.begin() as connection:
        connection.execute(
            database.TerminalModel.__table__.insert(),
            [
                {
                    "id": f"agent-{index:05d}",
                    "tmux_session": f"cao-session-{index % 100:03d}",
                    "session_id": f"lifetime-{index % 100:03d}",
                    "tmux_window": f"window-{index:05d}",
                    "provider": "codex",
                    "agent_profile": "reviewer" if index % 3 == 0 else "developer",
                    "runtime_lifecycle": "running" if index % 11 == 0 else "exited",
                    "creation_order": index + 1,
                    "last_active": now - timedelta(seconds=index),
                }
                for index in range(terminal_count)
            ],
        )
        connection.execute(
            database.WorkflowModel.__table__.insert(),
            [
                {
                    "root_terminal_id": f"agent-{index:05d}",
                    "status": ("open", "owner_gate", "terminal", "cancelled")[index % 4],
                    "created_at": now,
                    "updated_at": now,
                }
                for index in range(terminal_count)
            ],
        )

    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(
        database,
        "SessionLocal",
        sessionmaker(autocommit=False, autoflush=False, bind=engine),
    )
    for name in (
        "_ensure_terminal_worktree_authority_schema",
        "_ensure_provider_execution_schema",
        "_ensure_workflow_schema",
        "_ensure_child_assignment_schema",
        "_ensure_delegation_result_schema",
    ):
        monkeypatch.setattr(database, name, lambda: None)
    monkeypatch.setattr(database, "_terminal_ui_projection_schema_ready", True)
    monkeypatch.setattr(database, "_terminal_ui_projection_schema_engine_identity", id(engine))
    return engine


@pytest.mark.asyncio
async def test_realistic_home_agents_history_keeps_health_and_database_work_bounded(
    monkeypatch, tmp_path
):
    """Exercise current Home/Agents snapshots against 100 sessions/2,000 agents."""

    engine = _install_large_history(monkeypatch, tmp_path)
    query_durations: list[float] = []
    query_threads: set[int] = set()

    def before_cursor_execute(_conn, _cursor, statement, _params, context, _many):
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            context._ui_query_started = time.perf_counter()

    def after_cursor_execute(_conn, _cursor, statement, _params, context, _many):
        started = getattr(context, "_ui_query_started", None)
        if started is not None and statement.lstrip().upper().startswith(("SELECT", "WITH")):
            query_durations.append(time.perf_counter() - started)
            query_threads.add(threading.get_ident())

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    event.listen(engine, "after_cursor_execute", after_cursor_execute)
    api_main.app.state.plugin_registry = PluginRegistry()
    transport = httpx.ASGITransport(app=api_main.app)
    endpoint_durations: list[float] = []
    health_durations: list[float] = []

    async def timed_get(client: httpx.AsyncClient, path: str) -> httpx.Response:
        started = time.perf_counter()
        response = await client.get(path, headers={"Host": "localhost"})
        endpoint_durations.append(time.perf_counter() - started)
        return response

    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        navigation_done = asyncio.Event()

        async def navigate_home_and_agents() -> None:
            # One navigation refresh owns three bounded snapshots. Eight cycles
            # intentionally overlap the same work more often than production's
            # 5/10-second polling cadence without manufacturing an N+1 storm.
            for _ in range(8):
                responses = await asyncio.gather(
                    timed_get(client, "/ui/overview"),
                    timed_get(client, "/ui/sessions?limit=10"),
                    timed_get(client, "/ui/agents?limit=40"),
                )
                assert all(response.status_code == 200 for response in responses)
                assert len(responses[1].json()["items"]) == 10
                assert len(responses[2].json()["items"]) == 40
            navigation_done.set()

        async def sample_health() -> None:
            while not navigation_done.is_set():
                started = time.perf_counter()
                response = await asyncio.wait_for(
                    client.get("/health", headers={"Host": "localhost"}), timeout=0.2
                )
                health_durations.append(time.perf_counter() - started)
                assert response.status_code == 200
                await asyncio.sleep(0.003)

        await asyncio.gather(navigate_home_and_agents(), sample_health())

    # Each read-model request is exactly one SQLite statement. No per-session
    # or per-terminal query can appear as history grows.
    assert len(query_durations) == 24
    assert len(query_threads) <= api_main.UI_READ_MAX_CONCURRENCY
    assert max(query_durations) < 0.35
    assert max(endpoint_durations) < 0.75
    assert len(health_durations) >= 8
    assert max(health_durations) < 0.15
    assert statistics.median(health_durations) < 0.03
