import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.services.operations_service import (
    AdmissionDenied,
    _active_context_ids,
    _attempt_pressure_recovery,
    _heavy_utilization,
    acquire_heavy_slot,
    context_launch_admission,
    get_resource_status,
    require_resource_admission,
)


def test_heavy_inventory_keeps_active_slots_above_a_lowered_limit(tmp_path):
    config = _config(tmp_path, max_heavy_execution_slots=1)
    lock_dir = Path(config["lock_dir"])
    lock_dir.mkdir(parents=True)
    handles = [(lock_dir / f"heavy-{slot}.lock").open("a+") for slot in (0, 1)]
    try:
        for handle in handles:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert _heavy_utilization(config) == (2, 1)
    finally:
        for handle in handles:
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()


def test_lowered_heavy_limit_drains_before_reusing_a_low_slot(tmp_path, monkeypatch):
    config = _config(
        tmp_path,
        max_heavy_execution_slots=1,
        heavy_slot_wait_timeout_seconds=0.01,
    )
    lock_dir = Path(config["lock_dir"])
    lock_dir.mkdir(parents=True)
    high_slot = (lock_dir / "heavy-1.lock").open("a+")
    fcntl.flock(high_slot, fcntl.LOCK_EX | fcntl.LOCK_NB)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.require_resource_admission",
        lambda *args, **kwargs: {},
    )
    try:
        with pytest.raises(AdmissionDenied, match="HEAVY_SLOT_WAIT_TIMEOUT"):
            with acquire_heavy_slot(config):
                pytest.fail("draining capacity must not admit a replacement slot")
    finally:
        fcntl.flock(high_slot, fcntl.LOCK_UN)
        high_slot.close()


def test_active_contexts_exclude_post_exit_shells(monkeypatch):
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.list_all_terminals",
        lambda: [
            {"id": "agent", "tmux_session": "s", "tmux_window": "agent"},
            {"id": "shell", "tmux_session": "s", "tmux_window": "shell"},
        ],
    )
    outputs = iter(("0 codex.direct\n", "0 bash\n"))
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=next(outputs)),
    )
    assert _active_context_ids() == ["agent"]


def test_live_top_level_and_profile_names_do_not_override_durable_context_role(monkeypatch):
    monkeypatch.setattr(
        "cli_agent_orchestrator.clients.database.list_all_terminals",
        lambda: [
            {
                "id": "solo-developer",
                "tmux_session": "solo",
                "tmux_window": "developer",
                "agent_profile": "code_supervisor",
                "context_role": "work",
                "runtime_lifecycle": "running",
            },
            {
                "id": "named-developer",
                "tmux_session": "orchestrated",
                "tmux_window": "conductor",
                "agent_profile": "developer",
                "context_role": "supervisor",
                "runtime_lifecycle": "running",
            },
            {
                "id": "legacy-unknown",
                "tmux_session": "legacy",
                "tmux_window": "supervisor",
                "agent_profile": "supervisor",
                "context_role": None,
                "runtime_lifecycle": "running",
            },
        ],
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="0 codex.direct\n"),
    )

    from cli_agent_orchestrator.services.operations_service import _active_contexts

    assert _active_contexts() == [
        {"id": "solo-developer", "context_role": "work", "project_id": ""},
        {"id": "named-developer", "context_role": "supervisor", "project_id": ""},
        {"id": "legacy-unknown", "context_role": "work", "project_id": ""},
    ]


def _config(tmp_path: Path, **overrides):
    config = {
        "max_resident_supervisors": 5,
        "max_provider_executions": 3,
        "max_work_contexts": 2,
        "max_heavy_execution_slots": 1,
        "memory_green_mib": 1536,
        "memory_red_mib": 800,
        "root_used_yellow_percent": 70,
        "root_used_red_percent": 85,
        "root_used_critical_percent": 92,
        "root_free_green_gib": 10,
        "memory_pressure_some_yellow_avg10": 5.0,
        "memory_pressure_full_red_avg10": 1.0,
        "log_compress_after_minutes": 1440,
        "retention_minutes": 10080,
        "pressure_recovery_timeout_seconds": 1,
        "subprocess_timeout_seconds": 1,
        "context_launch_lock_timeout_seconds": 1,
        "heavy_slot_wait_timeout_seconds": 2,
        "root": str(tmp_path),
        "lock_dir": str(tmp_path / "locks"),
    }
    config.update(overrides)
    return config


def _status(tmp_path, *, memory_mib, used_percent, free_gib=20, pressure=""):
    total = 100 * 1024**3
    used = int(total * used_percent / 100)
    disk = shutil._ntuple_diskusage(total, used, int(free_gib * 1024**3))
    return get_resource_status(
        _config(tmp_path),
        meminfo_text=f"MemAvailable: {memory_mib * 1024} kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n",
        pressure_text=pressure
        or "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n",
        loadavg_text="0.00 0.00 0.00 0/0 1\n",
        cpu_count=1,
        disk_usage=disk,
        active_context_ids=[],
        provider_execution_ids=[],
        heavy_utilization=(0, 1),
    )


@pytest.mark.parametrize(
    ("memory", "disk", "pressure", "expected"),
    [
        (2048, 50, "", "GREEN"),
        (1200, 50, "", "YELLOW"),
        (2048, 70, "", "YELLOW"),
        (2048, 84.9, "", "YELLOW"),
        (799, 50, "", "RED"),
        (2048, 85, "", "RED"),
        (2048, 92, "", "RED"),
        (2048, 50, "some avg10=6.00\nfull avg10=1.00\n", "RED"),
    ],
)
def test_resource_health_states_are_deterministic(tmp_path, memory, disk, pressure, expected):
    assert (
        _status(tmp_path, memory_mib=memory, used_percent=disk, pressure=pressure)["resource_state"]
        == expected
    )


def test_disk_pressure_reasons_distinguish_red_and_critical(tmp_path):
    red = _status(tmp_path, memory_mib=2048, used_percent=85)
    critical = _status(tmp_path, memory_mib=2048, used_percent=92)

    assert red["reasons"] == ["ROOT_DISK_PRESSURE"]
    assert critical["reasons"] == ["ROOT_DISK_PRESSURE", "DISK_CRITICAL"]
    assert red["root_disk"]["state"] == "RED"
    assert critical["root_disk"]["state"] == "CRITICAL"


def test_red_aggregate_reports_non_disk_reason_while_disk_is_yellow(tmp_path):
    status = _status(
        tmp_path,
        memory_mib=2048,
        used_percent=77.5,
        pressure="some avg10=6.00\nfull avg10=1.00\n",
    )

    assert status["resource_state"] == "RED"
    assert status["root_disk"]["state"] == "YELLOW"
    assert status["reasons"] == ["critical_memory_pressure", "ROOT_DISK_PRESSURE"]


def test_resource_status_projects_explicit_linux_cpu_load(tmp_path):
    status = get_resource_status(
        _config(tmp_path),
        meminfo_text="MemAvailable: 2097152 kB\n",
        pressure_text="",
        loadavg_text="1.75 0.50 0.25 2/100 123\n",
        cpu_count=8,
        disk_usage=shutil._ntuple_diskusage(100, 50, 50 * 1024**3),
        active_contexts=[],
        provider_execution_ids=[],
        heavy_utilization=(0, 1),
    )
    assert status["cpu_load"] == {"one_minute": 1.75, "cpu_count": 8}


@pytest.mark.parametrize(
    ("contexts", "executions", "resident", "work"),
    [
        ([], [], (0, 5), (0, 2)),
        ([{"id": "supervisor", "context_role": "supervisor"}], [], (1, 5), (0, 2)),
        (
            [
                {"id": "supervisor", "context_role": "supervisor"},
                {"id": "worker", "context_role": "work"},
            ],
            ["worker"],
            (1, 5),
            (1, 2),
        ),
        (
            [
                {"id": "supervisor", "context_role": "supervisor"},
                {"id": "worker", "context_role": "work"},
                {"id": "reviewer", "context_role": "work"},
            ],
            ["worker", "reviewer"],
            (1, 5),
            (2, 2),
        ),
    ],
)
def test_context_capacity_projection(tmp_path, contexts, executions, resident, work):
    status = get_resource_status(
        _config(tmp_path),
        meminfo_text="MemAvailable: 2097152 kB\n",
        pressure_text="",
        disk_usage=shutil._ntuple_diskusage(100, 50, 50 * 1024**3),
        active_contexts=contexts,
        provider_execution_ids=executions,
        heavy_utilization=(0, 1),
    )
    assert status["resident_supervisors"] == {
        "active": resident[0],
        "limit": resident[1],
        "available": resident[1] - resident[0],
        "draining": resident[0] > resident[1],
        "certain": True,
    }
    assert status["provider_executions"] == {
        "active": len(executions),
        "limit": 3,
        "available": 3 - len(executions),
        "draining": len(executions) > 3,
        "certain": True,
    }
    assert status["work_contexts"] == {
        "active": work[0],
        "limit": work[1],
        "available": work[1] - work[0],
        "draining": work[0] > work[1],
        "certain": True,
    }
    if len(executions) == 3:
        with pytest.raises(AdmissionDenied, match="PROVIDER_EXECUTION_CAPACITY_EXHAUSTED"):
            require_resource_admission(
                _config(tmp_path), include_provider_capacity=True, status_probe=lambda: status
            )
    if work[0] == work[1]:
        with pytest.raises(AdmissionDenied, match="WORK_CONTEXT_CAPACITY_EXHAUSTED"):
            require_resource_admission(
                _config(tmp_path), include_work_capacity=True, status_probe=lambda: status
            )


def test_idle_delegated_context_consumes_residency_not_provider_execution(tmp_path):
    status = get_resource_status(
        _config(tmp_path),
        meminfo_text="MemAvailable: 2097152 kB\n",
        pressure_text="",
        disk_usage=shutil._ntuple_diskusage(100, 50, 50 * 1024**3),
        active_contexts=[
            {"id": "owner", "context_role": "supervisor"},
            {"id": "ready-child", "context_role": "work"},
        ],
        provider_execution_ids=[],
        heavy_utilization=(0, 1),
    )

    assert status["resident_supervisors"]["active"] == 1
    assert status["work_contexts"]["active"] == 1
    assert status["provider_executions"]["active"] == 0


@pytest.mark.parametrize(
    ("executions", "work_count", "heavy", "expected"),
    [
        ([], 0, 0, (5, 0, 0, 0)),  # A: five idle resident supervisors
        (["supervisor-0"], 0, 0, (5, 1, 0, 0)),  # B
        (["writer"], 1, 0, (5, 1, 1, 0)),  # C
        (["writer", "reviewer"], 2, 0, (5, 2, 2, 0)),  # D
        (["writer", "reviewer", "supervisor-1"], 2, 0, (5, 3, 2, 0)),  # E
        (["writer"], 1, 1, (5, 1, 1, 1)),  # I: heavy is orthogonal
    ],
)
def test_capacity_acceptance_matrix_a_through_e_and_i(
    tmp_path, executions, work_count, heavy, expected
):
    contexts = [
        {
            "id": f"supervisor-{index}",
            "context_role": "supervisor",
            "project_id": f"project-{index}",
        }
        for index in range(5)
    ] + [
        {"id": name, "context_role": "work", "project_id": ""}
        for name in ("writer", "reviewer")[:work_count]
    ]
    status = get_resource_status(
        _config(tmp_path),
        meminfo_text="MemAvailable: 2097152 kB\n",
        pressure_text="",
        disk_usage=shutil._ntuple_diskusage(100, 50, 50 * 1024**3),
        active_contexts=contexts,
        provider_execution_ids=executions,
        heavy_utilization=(heavy, 1),
    )
    assert (
        status["resident_supervisors"]["active"],
        status["provider_executions"]["active"],
        status["work_contexts"]["active"],
        status["heavy_executions"]["active"],
    ) == expected


def test_completed_context_frees_capacity(tmp_path):
    full = _status(tmp_path, memory_mib=2048, used_percent=50)
    full["provider_executions"] = {"active": 3, "limit": 3, "available": 0, "certain": True}
    freed = json.loads(json.dumps(full))
    freed["provider_executions"] = {"active": 2, "limit": 3, "available": 1, "certain": True}
    states = iter((full, freed))
    with pytest.raises(AdmissionDenied):
        require_resource_admission(
            _config(tmp_path), include_context_capacity=True, status_probe=lambda: next(states)
        )
    assert (
        require_resource_admission(
            _config(tmp_path), include_context_capacity=True, status_probe=lambda: freed
        )["provider_executions"]["available"]
        == 1
    )


def test_red_recovery_rechecks_and_remains_fail_closed(tmp_path, monkeypatch):
    red = _status(tmp_path, memory_mib=700, used_percent=50)
    probes = []
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service._attempt_pressure_recovery",
        lambda config: probes.append("recovery"),
    )
    with pytest.raises(AdmissionDenied, match="RESOURCE_HEALTH_REJECTED"):
        require_resource_admission(_config(tmp_path), status_probe=lambda: red)
    assert probes == ["recovery"]


def test_pressure_recovery_subprocess_is_bounded_and_temp_config_is_removed(tmp_path, monkeypatch):
    observed = {}

    def timed_out(command, **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        config_path = Path(kwargs["env"]["CAO_OPERATIONS_CONFIG"])
        observed["config_path"] = config_path
        assert config_path.is_file()
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.subprocess.run", timed_out
    )
    _attempt_pressure_recovery(_config(tmp_path))
    assert observed["timeout"] == 1
    assert "run_pressure_recovery" in observed["command"][2]
    assert not observed["config_path"].exists()


def test_heavy_admission_recovery_runs_without_holding_admission_lock(tmp_path, monkeypatch):
    config = _config(tmp_path)
    lock_dir = Path(config["lock_dir"])
    lock_dir.mkdir(parents=True)
    statuses = iter(
        (
            _status(tmp_path, memory_mib=2048, used_percent=90),
            _status(tmp_path, memory_mib=2048, used_percent=50),
            _status(tmp_path, memory_mib=2048, used_percent=50),
        )
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.get_resource_status",
        lambda _config: next(statuses),
    )
    recovery_lock_available = []

    def recovery(_config):
        with (lock_dir / "heavy-admission.lock").open("a+") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                recovery_lock_available.append(False)
            else:
                recovery_lock_available.append(True)
                fcntl.flock(handle, fcntl.LOCK_UN)

    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service._attempt_pressure_recovery",
        recovery,
    )

    with acquire_heavy_slot(config) as slot:
        assert slot == 0
    assert recovery_lock_available == [True]


def test_heavy_limit_one_waits_then_proceeds_and_releases_on_failure(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.require_resource_admission",
        lambda *args, **kwargs: {},
    )
    first_acquired = threading.Event()
    release_first = threading.Event()
    second_acquired = threading.Event()

    def first():
        with acquire_heavy_slot(config):
            first_acquired.set()
            release_first.wait(2)

    def second():
        with acquire_heavy_slot(config):
            second_acquired.set()

    a = threading.Thread(target=first)
    b = threading.Thread(target=second)
    a.start()
    assert first_acquired.wait(1)
    b.start()
    assert not second_acquired.wait(0.2)
    release_first.set()
    assert second_acquired.wait(1)
    a.join(1)
    b.join(1)
    with pytest.raises(RuntimeError):
        with acquire_heavy_slot(config):
            raise RuntimeError("synthetic failure")
    with acquire_heavy_slot(config) as slot:
        assert slot == 0


def test_heavy_slot_rechecks_health_after_lock_acquisition(tmp_path, monkeypatch):
    admitted = []

    def admission(*args, **kwargs):
        admitted.append("probe")
        if len(admitted) == 2:
            raise AdmissionDenied("RESOURCE_RED", {"resource_state": "RED"})
        return {"resource_state": "GREEN"}

    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.require_resource_admission",
        admission,
    )
    with pytest.raises(AdmissionDenied, match="RESOURCE_RED"):
        with acquire_heavy_slot(_config(tmp_path)):
            pytest.fail("unhealthy work must never execute")
    assert admitted == ["probe", "probe"]
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.require_resource_admission",
        lambda *args, **kwargs: {},
    )
    with acquire_heavy_slot(_config(tmp_path)) as slot:
        assert slot == 0


def test_recovery_safe_heavy_slot_counts_capacity_without_recursive_health_gate(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.require_resource_admission",
        lambda *args, **kwargs: pytest.fail("RED recovery must not re-enter the health gate"),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.get_resource_status",
        lambda _config: {
            "resource_state": "RED",
            "reasons": ["ROOT_DISK_PRESSURE", "DISK_CRITICAL"],
            "heavy_executions": {"active": 0, "limit": 1},
        },
    )

    with acquire_heavy_slot(_config(tmp_path), recovery_safe=True) as slot:
        assert slot == 0


def test_recovery_safe_heavy_slot_does_not_bypass_non_disk_red(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.require_resource_admission",
        lambda *args, **kwargs: pytest.fail("recovery mode must not recurse"),
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.get_resource_status",
        lambda _config: {
            "resource_state": "RED",
            "reasons": ["memory_below_red"],
            "heavy_executions": {"active": 0, "limit": 1},
        },
    )

    with pytest.raises(AdmissionDenied, match="RESOURCE_HEALTH_REJECTED"):
        with acquire_heavy_slot(_config(tmp_path), recovery_safe=True):
            pytest.fail("non-disk RED must remain fail-closed")


def test_heavy_limit_two_is_configurable(tmp_path, monkeypatch):
    config = _config(tmp_path, max_heavy_execution_slots=2)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.require_resource_admission",
        lambda *args, **kwargs: {},
    )
    with acquire_heavy_slot(config) as first:
        with acquire_heavy_slot(config) as second:
            assert {first, second} == {0, 1}


def test_heavy_lock_is_released_when_holder_is_terminated(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.require_resource_admission",
        lambda *args, **kwargs: {},
    )
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        with acquire_heavy_slot(config):
            os.write(write_fd, b"1")
            time.sleep(10)
        os._exit(0)
    os.close(write_fd)
    try:
        assert os.read(read_fd, 1) == b"1"
        os.kill(child, signal.SIGTERM)
        os.waitpid(child, 0)
        with acquire_heavy_slot(config) as slot:
            assert slot == 0
    finally:
        os.close(read_fd)


def test_heavy_wrapper_preserves_command_exit_status(tmp_path):
    config = _config(tmp_path)
    config_path = tmp_path / "operations.json"
    config_path.write_text(json.dumps(config))
    code = (
        "from cli_agent_orchestrator.services.operations_service import heavy_run_main;"
        "raise SystemExit(heavy_run_main())"
    )
    environment = dict(os.environ)
    environment["CAO_OPERATIONS_CONFIG"] = str(config_path)
    completed = subprocess.run(
        [sys.executable, "-c", code, "--", sys.executable, "-c", "raise SystemExit(7)"],
        env=environment,
        check=False,
    )
    assert completed.returncode == 7


def test_context_admission_does_not_use_racy_worktree_inventory(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.require_resource_admission",
        lambda *args, **kwargs: {"resource_state": "GREEN"},
    )
    config = _config(tmp_path)

    def forbidden_probe():
        raise AssertionError("writer ownership must be acquired atomically in the database")

    with context_launch_admission(
        config,
        canonical_worktree="/worktree",
        write_enabled=True,
        active_worktree_lanes_probe=forbidden_probe,
    ):
        pass


def test_resident_supervisor_limit_and_project_uniqueness_release_after_exit(tmp_path, monkeypatch):
    """G: sixth residency is rejected; project reuse conflicts until exit."""
    full = {
        "resource_state": "GREEN",
        "resident_supervisors": {"active": 5, "limit": 5, "available": 0, "certain": True},
        "work_contexts": {"active": 0, "limit": 2, "available": 2, "certain": True},
    }
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.require_resource_admission",
        lambda *args, **kwargs: full,
    )
    with pytest.raises(AdmissionDenied, match="RESIDENT_SUPERVISOR_CAPACITY_EXHAUSTED"):
        with context_launch_admission(_config(tmp_path), context_role="supervisor"):
            pytest.fail("sixth resident must not launch")

    available = json.loads(json.dumps(full))
    available["resident_supervisors"] = {
        "active": 4,
        "limit": 5,
        "available": 1,
        "certain": True,
    }
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.require_resource_admission",
        lambda *args, **kwargs: available,
    )
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service._active_contexts",
        lambda: [{"id": "resident", "context_role": "supervisor", "project_id": "project-a"}],
    )
    with pytest.raises(AdmissionDenied, match="PROJECT_SUPERVISOR_ALREADY_RESIDENT"):
        with context_launch_admission(
            _config(tmp_path), context_role="supervisor", project_id="project-a"
        ):
            pytest.fail("duplicate project supervisor must not launch")

    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service._active_contexts", lambda: []
    )
    with context_launch_admission(
        _config(tmp_path), context_role="supervisor", project_id="project-a"
    ):
        pass


def test_launch_fence_wait_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.require_resource_admission",
        lambda *args, **kwargs: {"resource_state": "GREEN"},
    )
    config = _config(tmp_path, context_launch_lock_timeout_seconds=0.05)
    entered = threading.Event()
    release = threading.Event()

    def holder():
        with context_launch_admission(config):
            entered.set()
            release.wait(1)

    thread = threading.Thread(target=holder)
    thread.start()
    assert entered.wait(1)
    try:
        with pytest.raises(AdmissionDenied, match="ADMISSION_FENCE_TIMEOUT"):
            with context_launch_admission(config):
                pytest.fail("timed-out launch must not enter the fence")
    finally:
        release.set()
        thread.join(1)


def test_terminal_creation_enters_operational_admission(monkeypatch):
    from cli_agent_orchestrator.services import terminal_service

    events = []
    admitted_kwargs = {}
    creation_kwargs = {}

    @contextmanager
    def admitted(**kwargs):
        admitted_kwargs.update(kwargs)
        events.append("admitted")
        yield {}

    monkeypatch.setattr(
        "cli_agent_orchestrator.services.operations_service.context_launch_admission",
        admitted,
    )
    monkeypatch.setattr(
        terminal_service,
        "_create_terminal_after_admission",
        lambda **kwargs: creation_kwargs.update(kwargs) or "created",
    )
    monkeypatch.setattr(terminal_service, "_canonical_worktree", lambda _: "/launch/worktree")
    monkeypatch.setattr(terminal_service, "_write_enabled_lane", lambda *_: True)
    assert terminal_service.create_terminal("codex", "developer") == "created"
    assert events == ["admitted"]
    assert admitted_kwargs["canonical_worktree"] == "/launch/worktree"
    assert admitted_kwargs["write_enabled"] is True
    assert creation_kwargs["launch_worktree"] == "/launch/worktree"
    assert creation_kwargs["write_enabled"] is True
