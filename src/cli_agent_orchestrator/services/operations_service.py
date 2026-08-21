"""Deterministic operational capacity, resource health, and heavy execution."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence, cast

PACKAGE_CONFIG = Path(__file__).parents[1] / "config" / "cao-operations.json"
SYSTEM_CONFIG = Path("/etc/agent-control/cao-operations.json")


class AdmissionDenied(RuntimeError):
    """A deterministic capacity or health gate rejected new work."""

    def __init__(self, reason_code: str, status: Mapping[str, Any]):
        self.reason_code = reason_code
        self.status = dict(status)
        super().__init__(f"{reason_code}: operational admission denied")


def _canonical_capacity_config(config: Mapping[str, Any]) -> dict[str, Any]:
    cfg = dict(config)
    cfg.setdefault("max_resident_supervisors", 5)
    if "max_provider_executions" not in cfg and "max_total_provider_contexts" in cfg:
        cfg["max_provider_executions"] = cfg["max_total_provider_contexts"]
    if "max_work_contexts" not in cfg and "max_active_work_contexts" in cfg:
        cfg["max_work_contexts"] = cfg["max_active_work_contexts"]
    return cfg


def _load_legacy_operations_config(path: Path | None = None) -> dict[str, Any]:
    """Load the deployment policy used to seed canonical capacity exactly once."""
    selected = path
    if selected is None:
        override = os.environ.get("CAO_OPERATIONS_CONFIG")
        selected = Path(override) if override else SYSTEM_CONFIG
        if not selected.is_file():
            selected = PACKAGE_CONFIG
    data = cast(dict[str, Any], json.loads(PACKAGE_CONFIG.read_text(encoding="utf-8")))
    if selected != PACKAGE_CONFIG:
        override_data = _canonical_capacity_config(
            cast(dict[str, Any], json.loads(selected.read_text(encoding="utf-8")))
        )
        data.update(override_data)
    # Rolling-upgrade aliases are input compatibility only. Every projection
    # and new deployment uses the execution/residency terminology below.
    data = _canonical_capacity_config(data)
    # Disk health is a product invariant, not a host-tunable capacity value.
    # Ignore legacy 80/85 files during rolling upgrade and project 70/85/92.
    data.update(
        root_used_yellow_percent=70,
        root_used_red_percent=85,
        root_used_critical_percent=92,
    )
    integer_keys = (
        "max_resident_supervisors",
        "max_provider_executions",
        "max_work_contexts",
        "max_heavy_execution_slots",
        "memory_green_mib",
        "memory_red_mib",
        "root_used_yellow_percent",
        "root_used_red_percent",
        "root_used_critical_percent",
        "root_free_green_gib",
        "log_compress_after_minutes",
        "retention_minutes",
        "pressure_recovery_timeout_seconds",
        "subprocess_timeout_seconds",
        "context_launch_lock_timeout_seconds",
        "heavy_slot_wait_timeout_seconds",
    )
    if any(not isinstance(data.get(key), int) or data[key] < 1 for key in integer_keys):
        raise ValueError("operations config contains an invalid positive integer")
    if data["memory_red_mib"] >= data["memory_green_mib"]:
        raise ValueError("memory_red_mib must be lower than memory_green_mib")
    if data["root_used_yellow_percent"] >= data["root_used_red_percent"]:
        raise ValueError("root disk yellow threshold must be lower than red")
    if data["root_used_red_percent"] >= data["root_used_critical_percent"]:
        raise ValueError("root disk critical threshold must be higher than red")
    return data


def load_operations_config(path: Path | None = None) -> dict[str, Any]:
    """Load non-capacity policy plus dynamically persisted canonical limits."""
    data = _load_legacy_operations_config(path)
    from cli_agent_orchestrator.clients.database import ensure_capacity_settings

    persisted = ensure_capacity_settings(data)
    for key in (
        "max_resident_supervisors",
        "max_provider_executions",
        "max_work_contexts",
        "max_heavy_execution_slots",
    ):
        data[key] = persisted[key]
    return data


def _parse_meminfo(text: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        match = re.fullmatch(r"([A-Za-z_()]+):\s+(\d+)\s+kB", line.strip())
        if match:
            values[match.group(1)] = int(match.group(2)) * 1024
    return values


def _parse_pressure(text: str) -> dict[str, float]:
    result: dict[str, float] = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        prefix = parts[0]
        for item in parts[1:]:
            if item.startswith("avg10="):
                result[f"{prefix}_avg10"] = float(item.split("=", 1)[1])
    return result


def _parse_loadavg(text: str) -> float:
    """Read the explicit one-minute Linux load average from /proc/loadavg."""
    first_field = text.split(maxsplit=1)[0] if text.strip() else "0"
    return float(first_field)


def _available_cpu_count() -> int:
    """Return CPUs available to this process, respecting Linux affinity when present."""
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:
        return os.cpu_count() or 1


def _active_contexts() -> list[dict[str, str]] | None:
    """Inventory live resident/work contexts, not post-exit login shells.

    A provider may leave its tmux window at a shell after it has exited.  That
    shell is neither an agent context nor a writer owner, and counting it here
    would permanently consume admission capacity until an operator intervenes.
    """
    try:
        from cli_agent_orchestrator.clients.database import list_all_terminals
    except Exception:
        return None
    active: list[dict[str, str]] = []
    try:
        terminals = list_all_terminals()
    except Exception:
        return None
    for terminal in terminals:
        if terminal.get("runtime_lifecycle") == "exited":
            continue
        target = f"{terminal['tmux_session']}:{terminal['tmux_window']}"
        completed = subprocess.run(
            ["tmux", "list-panes", "-t", target, "-F", "#{pane_dead} #{pane_current_command}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        live_agent = any(
            len(parts := line.split(maxsplit=1)) == 2
            and parts[0] == "0"
            and parts[1].strip() not in {"bash", "sh", "dash", "zsh", "fish"}
            for line in completed.stdout.splitlines()
        )
        if completed.returncode == 0 and live_agent:
            role = terminal.get("context_role")
            active.append(
                {
                    "id": str(terminal["id"]),
                    # Unknown legacy roles consume work capacity fail-closed.
                    "context_role": "supervisor" if role == "supervisor" else "work",
                    "project_id": str(terminal["project_id"]) if terminal.get("project_id") else "",
                }
            )
    return active


def _active_context_ids() -> list[str]:
    """Compatibility projection for callers that only need provider IDs."""
    return [context["id"] for context in (_active_contexts() or [])]


def _heavy_utilization(config: Mapping[str, Any]) -> tuple[int, int]:
    limit = int(config["max_heavy_execution_slots"])
    lock_dir = Path(str(config["lock_dir"]))
    lock_dir.mkdir(parents=True, exist_ok=True)
    slots = set(range(limit))
    for path in lock_dir.glob("heavy-*.lock"):
        match = re.fullmatch(r"heavy-(\d+)\.lock", path.name)
        if match:
            slot = int(match.group(1))
            if 0 <= slot < 50:
                slots.add(slot)
    active = 0
    for slot in sorted(slots):
        with (lock_dir / f"heavy-{slot}.lock").open("a+") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                active += 1
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
    return active, limit


def get_resource_status(
    config: Mapping[str, Any] | None = None,
    *,
    meminfo_text: str | None = None,
    pressure_text: str | None = None,
    loadavg_text: str | None = None,
    cpu_count: int | None = None,
    disk_usage: shutil._ntuple_diskusage | None = None,
    active_context_ids: Sequence[str] | None = None,
    active_contexts: Sequence[Mapping[str, str]] | None = None,
    provider_execution_ids: Sequence[str] | None = None,
    heavy_utilization: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Return the single machine-readable operational truth projection."""
    cfg = _canonical_capacity_config(config or load_operations_config())
    if meminfo_text is None:
        meminfo_text = Path("/proc/meminfo").read_text(encoding="utf-8")
    if pressure_text is None:
        pressure_path = Path("/proc/pressure/memory")
        pressure_text = pressure_path.read_text(encoding="utf-8") if pressure_path.exists() else ""
    if loadavg_text is None:
        loadavg_text = Path("/proc/loadavg").read_text(encoding="utf-8")
    memory = _parse_meminfo(meminfo_text)
    pressure = _parse_pressure(pressure_text)
    one_minute_load = _parse_loadavg(loadavg_text)
    available_cpus = cpu_count if cpu_count is not None else _available_cpu_count()
    disk = disk_usage or shutil.disk_usage("/")
    mem_available_mib = memory.get("MemAvailable", 0) // (1024 * 1024)
    root_used_percent = round((disk.used * 100 / disk.total) if disk.total else 100.0, 1)
    root_free_gib = round(disk.free / (1024**3), 2)
    full_avg10 = pressure.get("full_avg10", 0.0)
    some_avg10 = pressure.get("some_avg10", 0.0)

    red_reasons: list[str] = []
    yellow_reasons: list[str] = []
    if mem_available_mib < int(cfg["memory_red_mib"]):
        red_reasons.append("memory_below_red")
    elif mem_available_mib < int(cfg["memory_green_mib"]):
        yellow_reasons.append("memory_below_green")
    if root_used_percent >= int(cfg["root_used_critical_percent"]):
        red_reasons.extend(("ROOT_DISK_PRESSURE", "DISK_CRITICAL"))
    elif root_used_percent >= int(cfg["root_used_red_percent"]):
        red_reasons.append("ROOT_DISK_PRESSURE")
    elif root_used_percent >= int(cfg["root_used_yellow_percent"]):
        yellow_reasons.append("ROOT_DISK_PRESSURE")
    if root_free_gib < int(cfg["root_free_green_gib"]):
        yellow_reasons.append("root_free_below_green")
    if full_avg10 >= float(cfg["memory_pressure_full_red_avg10"]):
        red_reasons.append("critical_memory_pressure")
    elif some_avg10 >= float(cfg["memory_pressure_some_yellow_avg10"]):
        yellow_reasons.append("sustained_memory_pressure")
    state = "RED" if red_reasons else "YELLOW" if yellow_reasons else "GREEN"

    if active_contexts is not None:
        contexts = [dict(context) for context in active_contexts]
        context_inventory_certain = True
    elif active_context_ids is not None:
        # Legacy test/probe callers provide no role authority and therefore
        # consume work slots conservatively.
        contexts = [
            {"id": item, "context_role": "work", "project_id": ""} for item in active_context_ids
        ]
        context_inventory_certain = True
    else:
        observed_contexts = _active_contexts()
        context_inventory_certain = observed_contexts is not None
        contexts = observed_contexts or []
    active_heavy, heavy_limit = heavy_utilization or _heavy_utilization(cfg)
    if provider_execution_ids is None:
        try:
            from cli_agent_orchestrator.clients.database import list_provider_execution_leases

            provider_execution_ids = [
                str(lease["terminal_id"]) for lease in list_provider_execution_leases()
            ]
            execution_inventory_certain = True
        except Exception:
            provider_execution_ids = []
            execution_inventory_certain = False
    else:
        execution_inventory_certain = True
    resident_limit = int(cfg["max_resident_supervisors"])
    provider_limit = int(cfg["max_provider_executions"])
    work_limit = int(cfg["max_work_contexts"])
    active_resident = sum(context.get("context_role") == "supervisor" for context in contexts)
    active_work = sum(context.get("context_role") != "supervisor" for context in contexts)
    active_provider = len(provider_execution_ids)
    status_path = Path(str(cfg["root"])) / "state" / "cao" / "housekeeping-status.json"
    housekeeping = None
    if status_path.is_file():
        try:
            housekeeping = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            housekeeping = {"ok": False, "error": "status_unreadable"}
    return {
        "resource_state": state,
        "reasons": red_reasons + yellow_reasons,
        "resident_supervisors": {
            "active": active_resident,
            "limit": resident_limit,
            "draining": active_resident > resident_limit,
            "available": (
                max(0, resident_limit - active_resident) if context_inventory_certain else 0
            ),
            "certain": context_inventory_certain,
        },
        "provider_executions": {
            "active": active_provider,
            "limit": provider_limit,
            "draining": active_provider > provider_limit,
            "available": (
                max(0, provider_limit - active_provider) if execution_inventory_certain else 0
            ),
            "certain": execution_inventory_certain,
        },
        # Compatibility alias: this now intentionally carries provider-turn
        # execution semantics, never live-process/context semantics.
        "provider_contexts": {
            "active": active_provider,
            "limit": provider_limit,
            "draining": active_provider > provider_limit,
            "available": (
                max(0, provider_limit - active_provider) if execution_inventory_certain else 0
            ),
            "certain": execution_inventory_certain,
        },
        "work_contexts": {
            "active": active_work,
            "limit": work_limit,
            "draining": active_work > work_limit,
            "available": max(0, work_limit - active_work) if context_inventory_certain else 0,
            "certain": context_inventory_certain,
        },
        "heavy_executions": {
            "active": active_heavy,
            "limit": heavy_limit,
            "draining": active_heavy > heavy_limit,
            "available": max(0, heavy_limit - active_heavy),
            "waiting": None,
        },
        "memory": {
            "available_mib": mem_available_mib,
            "swap_total_mib": memory.get("SwapTotal", 0) // (1024 * 1024),
            "swap_free_mib": memory.get("SwapFree", 0) // (1024 * 1024),
        },
        "root_disk": {"used_percent": root_used_percent, "free_gib": root_free_gib},
        "memory_pressure": {"some_avg10": some_avg10, "full_avg10": full_avg10},
        "cpu_load": {"one_minute": one_minute_load, "cpu_count": available_cpus},
        "housekeeping": housekeeping,
        "capacity_settings": {
            "schema_version": 1,
            "configured": {
                "resident_supervisors": resident_limit,
                "provider_executions": provider_limit,
                "work_contexts": work_limit,
                "heavy_executions": heavy_limit,
            },
            "recommended": {
                "resident_supervisors": 5,
                "provider_executions": 3,
                "work_contexts": 2,
                "heavy_executions": 1,
            },
        },
    }


def _attempt_pressure_recovery(config: Mapping[str, Any]) -> None:
    """Run one recovery attempt without allowing it to pin an admission fence."""
    temporary: Path | None = None
    try:
        lock_dir = Path(str(config["lock_dir"]))
        lock_dir.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="pressure-recovery-", suffix=".json", dir=lock_dir)
        temporary = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(dict(config), handle)
        environment = dict(os.environ)
        environment["CAO_OPERATIONS_CONFIG"] = str(temporary)
        subprocess.run(
            [
                sys.executable,
                "-c",
                "from cli_agent_orchestrator.services.housekeeping_service import housekeeping_main; raise SystemExit(housekeeping_main())",
                "--mode",
                "pressure",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=float(config["pressure_recovery_timeout_seconds"]),
            env=environment,
        )
    except Exception:
        # Admission remains fail-closed on the mandatory recheck.
        return
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def require_resource_admission(
    config: Mapping[str, Any] | None = None,
    *,
    include_provider_capacity: bool = False,
    include_work_capacity: bool = False,
    include_context_capacity: bool | None = None,
    status_probe: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Recover once from RED, recheck, then enforce health and optional capacity."""
    cfg = _canonical_capacity_config(config or load_operations_config())
    probe = status_probe or (lambda: get_resource_status(cfg))
    status = probe()
    if status["resource_state"] == "RED":
        _attempt_pressure_recovery(cfg)
        status = probe()
    if status["resource_state"] == "RED":
        raise AdmissionDenied("RESOURCE_HEALTH_REJECTED", status)
    if include_context_capacity is not None:
        include_provider_capacity = include_provider_capacity or include_context_capacity
    provider_capacity = status.get("provider_executions", status.get("provider_contexts", {}))
    if include_provider_capacity and not provider_capacity.get("certain", True):
        raise AdmissionDenied("CONTEXT_INVENTORY_UNAVAILABLE", status)
    if include_work_capacity and not status["work_contexts"].get("certain", True):
        raise AdmissionDenied("CONTEXT_INVENTORY_UNAVAILABLE", status)
    if include_provider_capacity and provider_capacity.get("available", 0) < 1:
        raise AdmissionDenied("PROVIDER_EXECUTION_CAPACITY_EXHAUSTED", status)
    if include_work_capacity and status["work_contexts"]["available"] < 1:
        raise AdmissionDenied("WORK_CONTEXT_CAPACITY_EXHAUSTED", status)
    return status


def _lock_with_timeout(handle: Any, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise AdmissionDenied("ADMISSION_FENCE_TIMEOUT", {})
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))


@contextmanager
def context_launch_admission(
    config: Mapping[str, Any] | None = None,
    *,
    canonical_worktree: str | None = None,
    write_enabled: bool = False,
    context_role: str = "work",
    project_id: str | None = None,
    active_worktree_lanes_probe: Callable[[], Sequence[tuple[str, bool]] | None] | None = None,
) -> Iterator[dict[str, Any]]:
    """Atomically admit host capacity before terminal construction.

    Writer exclusivity is deliberately not decided from a read-then-act
    terminal inventory here.  Terminal metadata creation acquires the durable
    database uniqueness lease in the same transaction before provider start.
    """
    cfg = _canonical_capacity_config(config or load_operations_config())
    lock_dir = Path(str(cfg["lock_dir"]))
    lock_dir.mkdir(parents=True, exist_ok=True)
    with (lock_dir / "context-launch.lock").open("a+") as handle:
        _lock_with_timeout(handle, float(cfg["context_launch_lock_timeout_seconds"]))
        if config is None:
            cfg = _canonical_capacity_config(load_operations_config())
        status = require_resource_admission(cfg, include_work_capacity=context_role == "work")
        if context_role == "supervisor":
            residents = status["resident_supervisors"]
            if not residents.get("certain", True):
                raise AdmissionDenied("CONTEXT_INVENTORY_UNAVAILABLE", status)
            if project_id:
                contexts = _active_contexts()
                if contexts is None:
                    raise AdmissionDenied("CONTEXT_INVENTORY_UNAVAILABLE", status)
                if any(
                    context.get("context_role") == "supervisor"
                    and context.get("project_id") == project_id
                    for context in contexts
                ):
                    raise AdmissionDenied(
                        "PROJECT_SUPERVISOR_ALREADY_RESIDENT",
                        {**status, "project_id": project_id},
                    )
            if residents["available"] < 1:
                raise AdmissionDenied("RESIDENT_SUPERVISOR_CAPACITY_EXHAUSTED", status)
        yield status


def acquire_provider_execution_slot(
    terminal_id: str,
    workflow_turn_id: int,
    config: Mapping[str, Any] | None = None,
) -> None:
    """Acquire provider-turn authority immediately before physical transport."""
    cfg = _canonical_capacity_config(config or load_operations_config())
    status = require_resource_admission(cfg)
    from cli_agent_orchestrator.clients.database import acquire_provider_execution

    if config is None:
        from cli_agent_orchestrator.clients.database import ensure_capacity_settings

        ensure_capacity_settings(cfg)

    if not acquire_provider_execution(
        terminal_id,
        workflow_turn_id,
        int(cfg["max_provider_executions"]) if config is not None else None,
    ):
        raise AdmissionDenied("PROVIDER_EXECUTION_CAPACITY_EXHAUSTED", status)


def set_capacity_settings(values: Mapping[str, Any], *, actor: str) -> dict[str, Any]:
    """Serialize policy changes with context and heavy admission boundaries."""
    cfg = load_operations_config()
    lock_dir = Path(str(cfg["lock_dir"]))
    lock_dir.mkdir(parents=True, exist_ok=True)
    with (
        (lock_dir / "context-launch.lock").open("a+") as context_handle,
        (lock_dir / "heavy-admission.lock").open("a+") as heavy_handle,
    ):
        _lock_with_timeout(context_handle, float(cfg["context_launch_lock_timeout_seconds"]))
        _lock_with_timeout(heavy_handle, float(cfg["context_launch_lock_timeout_seconds"]))
        from cli_agent_orchestrator.clients.database import update_capacity_settings

        updated = update_capacity_settings(values, actor=actor)
    return updated


def _recovery_safe_heavy_status(config: Mapping[str, Any]) -> dict[str, Any]:
    """Admit only the disk-pressure recovery exception without recursion.

    Housekeeping may need a heavy slot precisely while the root filesystem is
    RED.  That exception must not turn memory or PSI RED into a global bypass.
    Calling ``require_resource_admission`` here would recursively launch the
    same pressure recovery, so inspect the current projection directly.
    """
    status = get_resource_status(config)
    reasons = status.get("reasons")
    if not isinstance(reasons, list):
        reasons = []
    non_disk_reasons = [
        reason for reason in reasons if reason not in {"ROOT_DISK_PRESSURE", "DISK_CRITICAL"}
    ]
    if status.get("resource_state") == "RED" and (not reasons or non_disk_reasons):
        raise AdmissionDenied("RESOURCE_HEALTH_REJECTED", status)
    return status


@contextmanager
def acquire_heavy_slot(
    config: Mapping[str, Any] | None = None,
    *,
    inherit_on_exec: bool = False,
    recovery_safe: bool = False,
) -> Iterator[int]:
    """Acquire one crash-safe flock slot, with an explicit RED-recovery mode."""
    cfg = _canonical_capacity_config(config or load_operations_config())
    lock_dir = Path(str(cfg["lock_dir"]))
    lock_dir.mkdir(parents=True, exist_ok=True)
    acquired: tuple[int, Any] | None = None
    deadline = time.monotonic() + float(cfg["heavy_slot_wait_timeout_seconds"])
    handles: list[Any] = []
    try:
        while acquired is None:
            with (lock_dir / "heavy-admission.lock").open("a+") as admission_handle:
                _lock_with_timeout(
                    admission_handle, float(cfg["context_launch_lock_timeout_seconds"])
                )
                if config is None:
                    cfg = _canonical_capacity_config(load_operations_config())
                if recovery_safe:
                    status = _recovery_safe_heavy_status(cfg)
                    heavy = status.get("heavy_executions", {})
                else:
                    status = require_resource_admission(cfg)
                    heavy = status.get("heavy_executions", {})
                active_heavy, configured_heavy = _heavy_utilization(cfg)
                if not isinstance(heavy.get("active"), int):
                    heavy = {"active": active_heavy, "limit": configured_heavy}
                limit = int(cfg["max_heavy_execution_slots"])
                # Inventory includes active high-numbered slots from the old
                # limit.  While draining, do not reuse a low slot merely
                # because one happens to be free.
                if heavy.get("active", 0) < limit:
                    handles = [
                        (lock_dir / f"heavy-{slot}.lock").open("a+") for slot in range(limit)
                    ]
                    for slot, handle in enumerate(handles):
                        try:
                            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            continue
                        acquired = (slot, handle)
                        if inherit_on_exec:
                            os.set_inheritable(handle.fileno(), True)
                        break
            if acquired is None:
                for handle in handles:
                    handle.close()
                handles = []
                if time.monotonic() >= deadline:
                    raise AdmissionDenied("HEAVY_SLOT_WAIT_TIMEOUT", {})
                time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        # Health can degrade while this process waits. The acquired slot must
        # not be handed to the executable until the current state is admitted.
        if recovery_safe:
            _recovery_safe_heavy_status(config or load_operations_config())
        else:
            require_resource_admission(config or load_operations_config())
        yield acquired[0]
    finally:
        if acquired is not None:
            fcntl.flock(acquired[1], fcntl.LOCK_UN)
        for handle in handles:
            handle.close()


def resource_status_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="threadcells-resource-status")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    status = get_resource_status()
    if args.json:
        print(json.dumps(status, sort_keys=True, separators=(",", ":")))
    else:
        residents = status["resident_supervisors"]
        providers = status["provider_executions"]
        work = status["work_contexts"]
        heavy = status["heavy_executions"]
        print(f"RESOURCE={status['resource_state']}")
        print(
            f"RESIDENT_SUPERVISORS={residents['active']}/{residents['limit']} "
            f"available={residents['available']}"
        )
        print(
            f"PROVIDER_EXECUTIONS={providers['active']}/{providers['limit']} "
            f"available={providers['available']}"
        )
        print(f"WORK={work['active']}/{work['limit']} available={work['available']}")
        print(f"HEAVY={heavy['active']}/{heavy['limit']} available={heavy['available']}")
        print(f"MEM_AVAILABLE_MIB={status['memory']['available_mib']}")
        print(
            f"ROOT_DISK_USED_PERCENT={status['root_disk']['used_percent']} "
            f"ROOT_FREE_GIB={status['root_disk']['free_gib']}"
        )
        print(
            f"MEMORY_PRESSURE_SOME_AVG10={status['memory_pressure']['some_avg10']} "
            f"FULL_AVG10={status['memory_pressure']['full_avg10']}"
        )
    return 0 if status["resource_state"] != "RED" else 2


def heavy_run_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="threadcells-heavy-run")
    parser.add_argument(
        "--recovery-safe",
        action="store_true",
        help="count heavy capacity but bypass only the RED health rejection for recovery",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("a command is required after --")
    try:
        # Keep the flock descriptor inherited while replacing the wrapper.
        # The command therefore owns the slot until its own exit/crash and the
        # kernel releases it without a cleanup callback or surviving wrapper.
        with acquire_heavy_slot(inherit_on_exec=True, recovery_safe=args.recovery_safe):
            os.execvp(command[0], command)
        return 127
    except AdmissionDenied as exc:
        print(f"CAO_HEAVY_DENIED reason={exc.reason_code}", file=sys.stderr)
        return 75
    except OSError as exc:
        print(f"CAO_HEAVY_EXEC_FAILED error={exc}", file=sys.stderr)
        return 127
