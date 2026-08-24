"""Deterministic Housekeeping.P2 candidate discovery and immutable planning."""

from __future__ import annotations

import json
import os
import pwd
import re
import shutil
import subprocess
import zlib
from pathlib import Path
from typing import Any, Callable, Mapping

from .models import (
    HousekeepingCandidate,
    HousekeepingMode,
    HousekeepingPlan,
    candidate_fingerprint,
    finalize_plan,
    resource_fingerprint,
)
from .protected_set import ProtectedSet, resolve_protected_set


def _older_than(path: Path, now: float, minutes: int) -> bool:
    return path.lstat().st_mtime <= now - minutes * 60


def _compression_reclaim(path: Path) -> int:
    """Compute a bounded-memory, truthful dry-run gzip estimate."""
    source_bytes = path.lstat().st_size
    compressor = zlib.compressobj(level=9, wbits=31)
    compressed_bytes = 0
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            compressed_bytes += len(compressor.compress(block))
    compressed_bytes += len(compressor.flush())
    return max(0, source_bytes - compressed_bytes)


def _candidate(
    path: Path,
    *,
    category: str,
    action: str,
    retention_reason: str,
    protection: ProtectedSet,
    estimated_reclaim: int | None = None,
    forced_protection: str | None = None,
) -> HousekeepingCandidate:
    protection_reason = forced_protection or protection.reason(path, category)
    resolved_action = "preserve" if protection_reason else action
    if resolved_action == "preserve":
        metadata = path.lstat()
        fingerprint = resource_fingerprint(
            {
                "device": metadata.st_dev,
                "inode": metadata.st_ino,
                "mode": metadata.st_mode,
                "size": metadata.st_size,
                "mtime_ns": metadata.st_mtime_ns,
            }
        )
        size = metadata.st_size if path.is_file() else 0
    else:
        fingerprint, size = candidate_fingerprint(path)
    return HousekeepingCandidate(
        category=category,
        path=str(path.resolve()),
        canonical_identity=f"{category}:{path.resolve()}",
        fingerprint=fingerprint,
        bytes=size,
        estimated_reclaim_bytes=(
            0
            if resolved_action == "preserve"
            else size if estimated_reclaim is None else estimated_reclaim
        ),
        action=resolved_action,  # type: ignore[arg-type]
        retention_reason=retention_reason,
        protection_reason=protection_reason,
    )


def _resource_candidate(
    *,
    category: str,
    resource_kind: str,
    identity: str,
    fingerprint_payload: Mapping[str, Any],
    size: int,
    action: str,
    retention_reason: str,
    attributes: Mapping[str, str],
    protection_reason: str | None = None,
) -> HousekeepingCandidate:
    resolved_action = "preserve" if protection_reason else action
    return HousekeepingCandidate(
        category=category,
        path=identity,
        canonical_identity=f"{category}:{identity}",
        fingerprint=resource_fingerprint(fingerprint_payload),
        bytes=size,
        estimated_reclaim_bytes=(
            size if resolved_action in {"delete", "terminate", "prune"} else 0
        ),
        action=resolved_action,  # type: ignore[arg-type]
        retention_reason=retention_reason,
        protection_reason=protection_reason,
        resource_kind=resource_kind,  # type: ignore[arg-type]
        attributes=tuple(sorted(attributes.items())),
    )


def _tree_size(path: Path) -> int:
    total = 0
    try:
        for child in path.rglob("*"):
            if child.is_file() and not child.is_symlink():
                total += child.lstat().st_size
    except OSError:
        return 0
    return total


def _ephemeral_marker(path: Path) -> dict[str, Any] | None:
    marker = path / ".cao-ephemeral.json"
    try:
        if marker.is_symlink() or not marker.is_file():
            return None
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    if (
        value.get("version") != 1
        or isinstance(value.get("expires_at"), bool)
        or not isinstance(value.get("expires_at"), (int, float))
        or isinstance(value.get("owner_pid"), bool)
        or not isinstance(value.get("owner_pid"), int)
    ):
        return None
    return value


def _pid_alive(pid: int, proc_root: Path) -> bool:
    return pid > 1 and (proc_root / str(pid)).exists()


def _plan_ephemeral_paths(
    root: Path,
    policy: Mapping[str, Any],
    protection: ProtectedSet,
    *,
    now: float,
    proc_root: Path,
) -> list[HousekeepingCandidate]:
    if not policy.get("enabled"):
        return []
    temp_root = root / "tmp"
    if not temp_root.is_dir():
        return []
    result: list[HousekeepingCandidate] = []
    for path in sorted(temp_root.iterdir()):
        try:
            if path.is_symlink() or not path.is_dir():
                continue
            marker = _ephemeral_marker(path)
            reason = None
            action = "preserve"
            retention_reason = "marker_unknown"
            if marker is None:
                reason = "EPHEMERAL_MARKER_UNKNOWN"
            elif float(marker["expires_at"]) > now:
                reason = "EPHEMERAL_NOT_EXPIRED"
                retention_reason = "within_marker_lifetime"
            elif _pid_alive(int(marker["owner_pid"]), proc_root):
                reason = "EPHEMERAL_OWNER_ACTIVE"
                retention_reason = "marker_owner_active"
            else:
                action = "delete"
                retention_reason = "expired_marker_dead_owner"
            result.append(
                _candidate(
                    path,
                    category="ephemeral",
                    action=action,
                    retention_reason=retention_reason,
                    protection=protection,
                    forced_protection=reason,
                )
            )
        except FileNotFoundError:
            continue
    return result


def _referenced_browser_revisions(manifest_roots: list[str]) -> tuple[set[str], bool]:
    revisions: set[str] = set()
    manifests_found = 0
    for root_name in manifest_roots:
        root = Path(root_name)
        if not root.is_dir():
            return revisions, False
        try:
            manifests = list(root.rglob("browsers.json"))
        except OSError:
            return revisions, False
        for manifest in manifests:
            if "playwright-core" not in manifest.parts:
                continue
            manifests_found += 1
            try:
                browsers = json.loads(manifest.read_text(encoding="utf-8")).get("browsers")
                if not isinstance(browsers, list):
                    return revisions, False
                for browser in browsers:
                    revision = browser.get("revision") if isinstance(browser, dict) else None
                    if not isinstance(revision, str):
                        return revisions, False
                    revisions.add(revision)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                return revisions, False
    return revisions, manifests_found > 0


def _plan_browser_cache(
    config: Mapping[str, Any],
    policy: Mapping[str, Any],
    protection: ProtectedSet,
    *,
    now: float,
) -> tuple[list[HousekeepingCandidate], list[str]]:
    if not policy.get("enabled"):
        return [], []
    cache_value = config.get("playwright_browser_cache")
    if not isinstance(cache_value, str) or not cache_value:
        return [], []
    cache = Path(cache_value)
    if not cache.is_absolute() or not cache.is_dir():
        return [], []
    referenced, certain = _referenced_browser_revisions(
        [str(item) for item in config.get("playwright_manifest_roots", [])]
    )
    warnings = [] if certain else ["browser_manifest_inventory_uncertain"]
    retention = int(policy["retain_minutes"])
    result: list[HousekeepingCandidate] = []
    for path in sorted(cache.iterdir()):
        try:
            match = re.fullmatch(r"[a-zA-Z_-]+-(\d+)", path.name)
            if not match or path.is_symlink() or not path.is_dir():
                continue
            if not _older_than(path, now, retention):
                continue
            reason = None
            retention_reason = f"unreferenced_older_than_{retention}_minutes"
            action = "delete"
            if not certain:
                reason = "BROWSER_MANIFEST_INVENTORY_UNKNOWN"
                action = "preserve"
            elif match.group(1) in referenced:
                reason = "BROWSER_REVISION_REFERENCED"
                action = "preserve"
                retention_reason = "referenced_by_installed_playwright"
            result.append(
                _candidate(
                    path,
                    category="browser_cache",
                    action=action,
                    retention_reason=retention_reason,
                    protection=protection,
                    forced_protection=reason,
                )
            )
        except FileNotFoundError:
            continue
    return result, warnings


def _process_start_epoch(process: Path, proc_root: Path, now: float) -> float | None:
    try:
        fields = (process / "stat").read_text(encoding="utf-8").split()
        ticks = int(fields[21])
        uptime = float((proc_root / "uptime").read_text(encoding="utf-8").split()[0])
        return now - uptime + ticks / os.sysconf(os.sysconf_names["SC_CLK_TCK"])
    except (OSError, ValueError, IndexError):
        return None


def _plan_orphan_browsers(
    config: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    now: float,
    proc_root: Path,
) -> tuple[list[HousekeepingCandidate], list[str]]:
    if not policy.get("enabled"):
        return [], []
    try:
        runtime_uid = pwd.getpwnam(str(config["runtime_user"])).pw_uid
    except (KeyError, TypeError):
        return [], ["browser_runtime_identity_unknown"]
    minimum_age = int(config.get("orphan_browser_age_minutes", 120)) * 60
    result: list[HousekeepingCandidate] = []
    try:
        processes = sorted(proc_root.iterdir())
    except OSError:
        return [], ["browser_process_inventory_uncertain"]
    for process in processes:
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != runtime_uid:
                continue
            cmdline_bytes = (process / "cmdline").read_bytes()
            cmdline = cmdline_bytes.replace(b"\0", b" ").decode(errors="replace")
            status = (process / "status").read_text(encoding="utf-8")
            stat_text = (process / "stat").read_text(encoding="utf-8")
            fields = stat_text.split()
            parent = re.search(r"^PPid:\s+(\d+)$", status, re.MULTILINE)
            profile_match = re.search(r"--user-data-dir=(/tmp/(?:playwright|pw-)[^ ]+)", cmdline)
            started = _process_start_epoch(process, proc_root, now)
            process_group = int(fields[4])
            if (
                not parent
                or int(parent.group(1)) != 1
                or not profile_match
                or started is None
                or process_group != int(process.name)
            ):
                continue
            profile = Path(profile_match.group(1))
            marker = _ephemeral_marker(profile)
            if (
                marker is None
                or marker.get("kind") != "playwright"
                or _pid_alive(int(marker["owner_pid"]), proc_root)
                or float(marker["expires_at"]) > now
                or now - started < minimum_age
            ):
                continue
            payload = {
                "pid": int(process.name),
                "process_group": process_group,
                "stat": stat_text,
                "cmdline_sha256": resource_fingerprint({"cmdline": cmdline_bytes.hex()}),
                "profile_fingerprint": candidate_fingerprint(profile)[0],
            }
            result.append(
                _resource_candidate(
                    category="ephemeral",
                    resource_kind="browser_process_group",
                    identity=f"process-group:{process_group}",
                    fingerprint_payload=payload,
                    size=_tree_size(profile),
                    action="terminate",
                    retention_reason="expired_marker_dead_owner_orphan",
                    attributes={
                        "pid": process.name,
                        "profile": str(profile),
                        "profile_fingerprint": str(payload["profile_fingerprint"]),
                    },
                )
            )
        except (FileNotFoundError, PermissionError, ValueError, IndexError):
            continue
    return result, []


def _plan_docker(
    policy: Mapping[str, Any],
    *,
    now: float,
    proc_root: Path,
    runner: Callable[..., Any],
    timeout_seconds: float,
) -> tuple[list[HousekeepingCandidate], list[str]]:
    if not policy.get("enabled"):
        return [], []
    docker = shutil.which("docker")
    if docker is None:
        return [], []
    result: list[HousekeepingCandidate] = []
    warnings: list[str] = []
    catalogs = {
        "docker_container": [
            docker,
            "ps",
            "-a",
            "--filter",
            "label=cao.ephemeral=true",
            "--format",
            "{{.ID}}",
        ],
        "docker_volume": [
            docker,
            "volume",
            "ls",
            "--filter",
            "label=cao.ephemeral=true",
            "--format",
            "{{.Name}}",
        ],
    }
    for kind, command in catalogs.items():
        try:
            listed = runner(
                command, capture_output=True, text=True, check=False, timeout=timeout_seconds
            )
        except (OSError, subprocess.TimeoutExpired):
            warnings.append(f"{kind}_inventory_failed")
            continue
        if listed.returncode:
            warnings.append(f"{kind}_inventory_failed")
            continue
        for identifier in filter(None, (line.strip() for line in listed.stdout.splitlines())):
            inspect = (
                [docker, "inspect", "--format", "{{json .Config.Labels}}", identifier]
                if kind == "docker_container"
                else [docker, "volume", "inspect", "--format", "{{json .Labels}}", identifier]
            )
            try:
                inspected = runner(
                    inspect, capture_output=True, text=True, check=False, timeout=timeout_seconds
                )
                labels = json.loads(inspected.stdout) if inspected.returncode == 0 else {}
                expires_at = float(labels.get("cao.expires_at", "inf"))
                owner_pid = int(labels.get("cao.owner_pid", "-1"))
            except (
                OSError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
                subprocess.TimeoutExpired,
            ):
                warnings.append(f"{kind}_metadata_unknown:{identifier}")
                continue
            if (
                labels.get("cao.ephemeral") != "true"
                or expires_at > now
                or _pid_alive(owner_pid, proc_root)
            ):
                continue
            if kind == "docker_container":
                state_command = [docker, "inspect", "--format", "{{.State.Running}}", identifier]
            else:
                state_command = [
                    docker,
                    "ps",
                    "-a",
                    "--filter",
                    f"volume={identifier}",
                    "--format",
                    "{{.ID}}",
                ]
            try:
                state = runner(
                    state_command,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=timeout_seconds,
                )
            except (OSError, subprocess.TimeoutExpired):
                warnings.append(f"{kind}_references_unknown:{identifier}")
                continue
            safe = (
                state.returncode == 0 and state.stdout.strip().lower() == "false"
                if kind == "docker_container"
                else state.returncode == 0 and not state.stdout.strip()
            )
            if not safe:
                continue
            payload = {"identifier": identifier, "kind": kind, "labels": labels}
            result.append(
                _resource_candidate(
                    category="ephemeral",
                    resource_kind=kind,
                    identity=f"docker:{kind.removeprefix('docker_')}:{identifier}",
                    fingerprint_payload=payload,
                    size=0,
                    action="delete",
                    retention_reason="expired_label_dead_owner_unreferenced",
                    attributes={"identifier": identifier},
                )
            )
    return result, warnings


def _plan_package_caches(
    config: Mapping[str, Any], policy: Mapping[str, Any]
) -> list[HousekeepingCandidate]:
    if not policy.get("enabled"):
        return []
    threshold = int(config.get("cache_prune_threshold_gib", 1)) * 1024**3
    result: list[HousekeepingCandidate] = []
    for entry in config.get("package_caches", []):
        path = Path(str(entry.get("path", "")))
        command = entry.get("command")
        name = str(entry.get("name", ""))
        if (
            not name
            or not path.is_absolute()
            or path.is_symlink()
            or not path.is_dir()
            or not isinstance(command, list)
            or not command
        ):
            continue
        size = _tree_size(path)
        if size < threshold:
            continue
        fingerprint, _ = candidate_fingerprint(path)
        result.append(
            HousekeepingCandidate(
                category="package_cache",
                path=str(path.resolve()),
                canonical_identity=f"package_cache:{name}:{path.resolve()}",
                fingerprint=fingerprint,
                bytes=size,
                # Trusted cache commands decide their own safe subset. The
                # footprint is known, but claiming all bytes as reclaimable
                # would make a dry-run estimate dishonest.
                estimated_reclaim_bytes=0,
                action="prune",
                retention_reason=f"over_{threshold}_bytes",
                resource_kind="package_cache",
                attributes=(("name", name),),
            )
        )
    return result


def discover_runtime_candidates(
    *,
    root: Path,
    config: Mapping[str, Any],
    policy: Mapping[str, Any],
    now: float,
    proc_root: Path,
    runner: Callable[..., Any] = subprocess.run,
    protection: ProtectedSet | None = None,
) -> tuple[list[HousekeepingCandidate], list[str]]:
    """Inventory non-release runtime resources without mutating them."""
    protection = protection or resolve_protected_set(
        root, config, open_inventory=lambda: (set(), False)
    )
    candidates = _plan_ephemeral_paths(
        root, policy["ephemeral"], protection, now=now, proc_root=proc_root
    )
    browsers, browser_warnings = _plan_orphan_browsers(
        config, policy["ephemeral"], now=now, proc_root=proc_root
    )
    docker, docker_warnings = _plan_docker(
        policy["ephemeral"],
        now=now,
        proc_root=proc_root,
        runner=runner,
        timeout_seconds=float(config.get("subprocess_timeout_seconds", 20)),
    )
    candidates.extend(browsers)
    candidates.extend(docker)
    terminal_runtimes, terminal_warnings = _plan_closed_terminal_runtimes(proc_root=proc_root)
    candidates.extend(terminal_runtimes)
    retirement_cleanups, retirement_warnings = _plan_retirement_cleanups(protection=protection)
    candidates.extend(retirement_cleanups)
    return candidates, [
        *browser_warnings,
        *docker_warnings,
        *terminal_warnings,
        *retirement_warnings,
    ]


def _plan_retirement_cleanups(
    *, protection: ProtectedSet
) -> tuple[list[HousekeepingCandidate], list[str]]:
    """Plan exact durable child-cleanup intents without inferring authority."""
    from cli_agent_orchestrator.clients.database import (
        list_legacy_child_retirements_for_cleanup,
        list_pending_child_retirement_cleanups,
    )
    from cli_agent_orchestrator.services.terminal_service import (
        validate_managed_worktree_cleanup,
    )

    candidates: list[HousekeepingCandidate] = []
    warnings: list[str] = []
    try:
        legacy = list_legacy_child_retirements_for_cleanup()
        pending = list_pending_child_retirement_cleanups()
    except Exception:
        return [], ["retirement_cleanup_inventory_uncertain"]

    rows = [
        *(dict(item, stage="legacy") for item in legacy),
        *(dict(item, stage="pending") for item in pending),
    ]
    for item in rows:
        child = str(item.get("child_terminal_id") or "")
        intent = item.get("intent")
        stage = str(item["stage"])
        if not child:
            warnings.append("retirement_cleanup_identity_unknown")
            continue
        if stage == "legacy" and not item.get("identity_proven"):
            warnings.append(f"retirement_cleanup_identity_unproven:{child}")
            continue
        if not isinstance(intent, dict) or intent.get("terminal_id") != child:
            warnings.append(f"retirement_cleanup_intent_invalid:{child}")
            continue
        token = item.get("claim_token")
        if stage == "pending" and (not isinstance(token, str) or not token):
            parent = item.get("parent_terminal_id")
            delegation_kind = item.get("delegation_kind")
            if (
                not isinstance(parent, str)
                or not parent
                or delegation_kind not in {"assign", "handoff"}
            ):
                warnings.append(f"retirement_cleanup_claim_unknown:{child}")
                continue
            # The exact persisted relation can re-enter the same atomic claim
            # boundary used by normal retirement. The executor must still win
            # every result, workflow, child-barrier, and cleanup-identity
            # predicate before it receives a token or mutates anything.
            stage = "unclaimed"

        protection_reason = None
        if intent.get("managed"):
            launch_worktree = intent.get("launch_worktree")
            if not isinstance(launch_worktree, str) or not launch_worktree.startswith("/"):
                protection_reason = "RETIREMENT_CLEANUP_IDENTITY_UNPROVEN"
            else:
                worktree = Path(launch_worktree)
                protection_reason = protection.reason(worktree, "ephemeral")
                if protection_reason is None:
                    try:
                        validate_managed_worktree_cleanup(intent)
                    except Exception:
                        protection_reason = "MANAGED_WORKTREE_CLEANUP_UNSAFE"
        payload = {
            "stage": stage,
            "parent_terminal_id": str(item.get("parent_terminal_id") or ""),
            "child_terminal_id": child,
            "delegation_kind": str(item.get("delegation_kind") or ""),
            "claim_token": str(token or ""),
            "intent": json.dumps(intent, sort_keys=True, separators=(",", ":")),
        }
        size = 0
        launch_worktree = intent.get("launch_worktree")
        if (
            intent.get("managed")
            and isinstance(launch_worktree, str)
            and Path(launch_worktree).is_dir()
            and not Path(launch_worktree).is_symlink()
        ):
            size = _tree_size(Path(launch_worktree))
        candidates.append(
            _resource_candidate(
                category="retirement_cleanup",
                resource_kind="retirement_cleanup",
                identity=f"retirement-cleanup:{child}",
                fingerprint_payload=payload,
                size=size,
                action="prune",
                retention_reason="durably_completed_child_cleanup",
                protection_reason=protection_reason,
                attributes=payload,
            )
        )
    return candidates, warnings


def _plan_closed_terminal_runtimes(
    *, proc_root: Path
) -> tuple[list[HousekeepingCandidate], list[str]]:
    """Plan exact exited-terminal panes while preserving durable history."""
    from cli_agent_orchestrator.clients.database import list_all_terminals
    from cli_agent_orchestrator.clients.tmux import PaneTargetError, tmux_client

    try:
        terminals = list_all_terminals()
    except Exception:
        return [], ["terminal_runtime_inventory_uncertain"]
    candidates: list[HousekeepingCandidate] = []
    warnings: list[str] = []
    absent = {"EXIT_SESSION_MISSING", "EXIT_WINDOW_MISSING", "EXIT_PANE_MISSING", "EXIT_PANE_DEAD"}
    for terminal in terminals:
        if terminal.get("runtime_lifecycle") != "exited":
            continue
        terminal_id = str(terminal.get("id") or "")
        if not terminal_id:
            continue
        try:
            target = tmux_client.exact_runtime_target(
                str(terminal["tmux_session"]), str(terminal["tmux_window"]), proc_root=proc_root
            )
        except PaneTargetError as exc:
            if exc.reason_code not in absent:
                warnings.append(f"terminal_runtime_preserved:{terminal_id}:{exc.reason_code}")
            continue
        except Exception:
            warnings.append(f"terminal_runtime_preserved:{terminal_id}:INVENTORY_UNCERTAIN")
            continue
        protection_reason = None
        action = "terminate"
        durable_identity = (
            terminal.get("runtime_pane_id"),
            terminal.get("runtime_pane_pid"),
            terminal.get("runtime_generation"),
            terminal.get("runtime_process_start_ticks"),
        )
        observed_identity = (
            target.pane_id,
            target.pane_pid,
            target.runtime_generation,
            target.process_start_ticks,
        )
        if target.terminal_id != terminal_id:
            protection_reason = "TERMINAL_RUNTIME_IDENTITY_MISMATCH"
            action = "preserve"
        elif any(value in (None, "") for value in durable_identity):
            protection_reason = "TERMINAL_RUNTIME_GENERATION_UNRECORDED"
            action = "preserve"
        elif durable_identity != observed_identity:
            protection_reason = "TERMINAL_RUNTIME_GENERATION_MISMATCH"
            action = "preserve"
        elif terminal.get("runtime_generation_origin") not in {"launch", "reconciled"} or (
            (terminal.get("runtime_generation_origin") == "launch")
            != bool(target.generation_inherited)
        ):
            protection_reason = "TERMINAL_RUNTIME_GENERATION_PROVENANCE_MISMATCH"
            action = "preserve"
        elif target.current_command not in {"bash", "sh", "dash", "zsh", "fish"}:
            protection_reason = "TERMINAL_RUNTIME_NOT_IDLE_SHELL"
            action = "preserve"
        payload = {
            "terminal_id": terminal_id,
            "pane_id": target.pane_id,
            "pane_pid": target.pane_pid,
            "current_command": target.current_command,
            "runtime_generation": target.runtime_generation,
            "process_start_ticks": target.process_start_ticks,
            "runtime_lifecycle": "exited",
        }
        candidates.append(
            _resource_candidate(
                category="terminal_runtime",
                resource_kind="terminal_runtime",
                identity=f"terminal-runtime:{terminal_id}",
                fingerprint_payload=payload,
                size=0,
                action=action,
                retention_reason="durably_exited_runtime",
                protection_reason=protection_reason,
                attributes={key: str(value) for key, value in payload.items()},
            )
        )
    return candidates, warnings


def _plan_logs(
    root: Path,
    policy: Mapping[str, Any],
    protection: ProtectedSet,
    *,
    now: float,
) -> list[HousekeepingCandidate]:
    if not policy.get("enabled"):
        return []
    logs = root / "state" / "cao" / "logs"
    if not logs.is_dir():
        return []
    result: list[HousekeepingCandidate] = []
    retention = int(policy["retain_minutes"])
    compress_after = int(policy["compress_after_minutes"])
    for path in sorted(logs.rglob("*.log*")):
        try:
            if path.is_symlink() or not path.is_file():
                continue
            if _older_than(path, now, retention):
                result.append(
                    _candidate(
                        path,
                        category="logs",
                        action="delete",
                        retention_reason=f"older_than_{retention}_minutes",
                        protection=protection,
                    )
                )
            elif path.suffix == ".log" and _older_than(path, now, compress_after):
                protection_reason = protection.reason(path, "logs")
                result.append(
                    _candidate(
                        path,
                        category="logs",
                        action="compress",
                        retention_reason=f"older_than_{compress_after}_minutes",
                        protection=protection,
                        estimated_reclaim=(0 if protection_reason else _compression_reclaim(path)),
                        forced_protection=protection_reason,
                    )
                )
        except FileNotFoundError:
            continue
    return result


def _plan_attachments(
    root: Path,
    policy: Mapping[str, Any],
    protection: ProtectedSet,
    *,
    now: float,
) -> list[HousekeepingCandidate]:
    if not policy.get("enabled"):
        return []
    attachments = root / "state" / "cao" / "runtime" / "terminal-attachments"
    if not attachments.is_dir():
        return []
    retention = int(policy["retain_minutes"])
    result: list[HousekeepingCandidate] = []
    for path in sorted(attachments.glob("*/*")):
        try:
            if path.is_symlink() or not path.is_file() or not _older_than(path, now, retention):
                continue
            result.append(
                _candidate(
                    path,
                    category="attachments",
                    action="delete",
                    retention_reason=f"older_than_{retention}_minutes",
                    protection=protection,
                )
            )
        except FileNotFoundError:
            continue
    return result


def _release_marker(path: Path) -> bool:
    for marker_name in (".threadcells-release.json", ".threadmesh-release.json"):
        marker = path / marker_name
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return False
        return (
            not marker.is_symlink()
            and value.get("schema_version") == 1
            and value.get("release_id") == path.name
            and isinstance(value.get("source_commit"), str)
        )
    return False


def _plan_releases(
    policy: Mapping[str, Any],
    protection: ProtectedSet,
    *,
    now: float,
) -> list[HousekeepingCandidate]:
    if not policy.get("enabled"):
        return []
    retention = int(policy["retain_minutes"])
    retain_count = int(policy["retain_count"])
    directories: set[Path] = set()
    for release_root in protection.release_roots:
        if release_root.is_dir():
            directories.update(
                path.resolve()
                for path in release_root.iterdir()
                if path.is_dir() and not path.is_symlink()
            )
    ordered_directories = sorted(
        directories, key=lambda path: (path.lstat().st_mtime_ns, path.name), reverse=True
    )
    result: list[HousekeepingCandidate] = []
    for index, path in enumerate(ordered_directories):
        marker_valid = _release_marker(path)
        if not marker_valid:
            item = _candidate(
                path,
                category="releases",
                action="preserve",
                retention_reason="release_marker_unknown",
                protection=protection,
            )
            result.append(
                HousekeepingCandidate(
                    **{
                        **item.as_dict(),
                        "action": "preserve",
                        "estimated_reclaim_bytes": 0,
                        "protection_reason": item.protection_reason or "RELEASE_MARKER_UNKNOWN",
                    }
                )
            )
            continue
        reason = "newest_retained_release" if index < retain_count else "within_retention_window"
        action = "preserve"
        if index >= retain_count and _older_than(path, now, retention):
            action = "delete"
            reason = f"unreferenced_older_than_{retention}_minutes"
        result.append(
            _candidate(
                path,
                category="releases",
                action=action,
                retention_reason=reason,
                protection=protection,
            )
        )
    return result


def build_plan(
    *,
    root: Path,
    config: Mapping[str, Any],
    settings: Mapping[str, Any],
    mode: HousekeepingMode,
    now: float,
    open_inventory: Callable[[], tuple[set[Path], bool]],
    proc_root: Path = Path("/proc"),
    runner: Callable[..., Any] = subprocess.run,
) -> HousekeepingPlan:
    policy = settings["policy"]
    protection = resolve_protected_set(root, config, open_inventory=open_inventory)
    candidates = [
        *_plan_logs(root, policy["logs"], protection, now=now),
        *_plan_attachments(root, policy["attachments"], protection, now=now),
    ]
    runtime_candidates, runtime_warnings = discover_runtime_candidates(
        root=root,
        config=config,
        policy=policy,
        now=now,
        proc_root=proc_root,
        runner=runner,
        protection=protection,
    )
    candidates.extend(runtime_candidates)
    if mode in {"weekly", "pressure"}:
        candidates.extend(_plan_releases(policy["releases"], protection, now=now))
        browser_candidates, browser_warnings = _plan_browser_cache(
            config, policy["browser_cache"], protection, now=now
        )
        candidates.extend(browser_candidates)
        runtime_warnings.extend(browser_warnings)
        candidates.extend(_plan_package_caches(config, policy["package_cache"]))
    backups = root / "backups"
    if backups.exists():
        candidates.append(
            _candidate(
                backups,
                category="backups",
                action="preserve",
                retention_reason="inventory_only",
                protection=protection,
            )
        )
    candidates.sort(key=lambda item: (item.category, item.canonical_identity))
    return finalize_plan(
        generated_at=now,
        mode=mode,
        root=root,
        candidates=candidates,
        warnings=[*protection.warnings, *runtime_warnings],
    )
