"""Conservative, marker-aware CAO housekeeping with a mandatory dry-run mode."""

from __future__ import annotations

import argparse
import fcntl
import gzip
import json
import os
import pwd
import re
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence, cast

from cli_agent_orchestrator.services.operations_service import load_operations_config


@dataclass
class HousekeepingSummary:
    ok: bool = True
    dry_run: bool = False
    mode: str = "frequent"
    disk_before: int = 0
    disk_after: int = 0
    freed_bytes: int = 0
    logs_compressed: int = 0
    logs_deleted: int = 0
    attachments_deleted: int = 0
    orphan_processes_closed: int = 0
    ephemeral_resources_removed: int = 0
    browser_revisions_removed: int = 0
    browser_revision_candidates: int = 0
    writer_leases_reconciled: int = 0
    retirement_cleanups_reconciled: int = 0
    legacy_authority_reconciled: int = 0
    supervisor_roles_reconciled: int = 0
    cache_pruned: int = 0
    skipped_open: int = 0
    skipped_unknown: int = 0
    plan_id: str | None = None
    planned_candidates: int = 0
    reclaimable_bytes: int = 0
    execution_failures: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _tree_size(root: Path) -> int:
    total = 0
    if not root.exists():
        return 0
    for directory, _, files in os.walk(root):
        for name in files:
            try:
                total += (Path(directory) / name).lstat().st_size
            except OSError:
                continue
    return total


def _open_paths_inventory(proc_root: Path = Path("/proc")) -> tuple[set[Path], bool]:
    """Return live process paths and whether the complete inventory was readable."""
    result: set[Path] = set()
    try:
        processes = list(proc_root.iterdir())
    except OSError:
        return result, False
    for process in processes:
        if not process.name.isdigit():
            continue
        fd_root = process / "fd"
        try:
            descriptors = list(fd_root.iterdir())
        except FileNotFoundError:
            continue
        except OSError:
            return result, False
        for descriptor in descriptors:
            try:
                target = os.readlink(descriptor)
            except FileNotFoundError:
                continue
            except OSError:
                return result, False
            if target.startswith("/"):
                result.add(Path(target.removesuffix(" (deleted)")))
        try:
            executable = os.readlink(process / "exe")
            if executable.startswith("/"):
                result.add(Path(executable.removesuffix(" (deleted)")))
        except FileNotFoundError:
            pass
        except OSError:
            return result, False
        try:
            for line in (process / "maps").read_text(encoding="utf-8").splitlines():
                fields = line.split(maxsplit=5)
                if len(fields) == 6 and fields[5].startswith("/"):
                    result.add(Path(fields[5].removesuffix(" (deleted)")))
        except FileNotFoundError:
            pass
        except OSError:
            return result, False
    return result, True


def _open_paths(proc_root: Path = Path("/proc")) -> set[Path]:
    """Compatibility projection for non-destructive callers."""
    return _open_paths_inventory(proc_root)[0]


def _older_than(path: Path, now: float, minutes: int) -> bool:
    return path.lstat().st_mtime <= now - minutes * 60


def _safe_unlink(path: Path, *, dry_run: bool) -> int:
    size = path.lstat().st_size
    if not dry_run:
        path.unlink()
    return size


def _compress_log(path: Path, *, dry_run: bool) -> int:
    """Compress unchanged input and preserve its source timestamp."""
    source = path.lstat()
    destination = path.with_suffix(path.suffix + ".gz")
    if destination.exists():
        return 0
    if dry_run:
        return 0
    temporary: Path | None = None
    try:
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(temporary_name)
        with (
            os.fdopen(fd, "wb") as raw,
            gzip.GzipFile(
                filename=path.name, mode="wb", fileobj=raw, mtime=int(source.st_mtime)
            ) as compressed,
            path.open("rb") as original,
        ):
            shutil.copyfileobj(original, compressed)
        current = path.lstat()
        if (current.st_ino, current.st_size, current.st_mtime_ns) != (
            source.st_ino,
            source.st_size,
            source.st_mtime_ns,
        ):
            raise RuntimeError(f"log changed during compression: {path}")
        os.chmod(temporary, source.st_mode & 0o777)
        os.utime(temporary, ns=(source.st_atime_ns, source.st_mtime_ns))
        os.replace(temporary, destination)
        temporary = None
        path.unlink()
        return max(0, source.st_size - destination.lstat().st_size)
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _cleanup_logs(
    root: Path,
    config: Mapping[str, Any],
    summary: HousekeepingSummary,
    *,
    now: float,
    open_paths: set[Path],
    open_paths_certain: bool = True,
) -> None:
    if not open_paths_certain:
        summary.skipped_unknown += 1
        summary.warnings.append("log_attachment_process_inventory_uncertain")
        return
    logs = root / "state" / "cao" / "logs"
    compress_minutes = int(config["log_compress_after_minutes"])
    retention_minutes = int(config["retention_minutes"])
    if logs.is_dir():
        for candidate in sorted(logs.glob("cao_*.log")):
            try:
                resolved = candidate.resolve()
                if resolved in open_paths:
                    summary.skipped_open += 1
                elif _older_than(candidate, now, retention_minutes):
                    summary.freed_bytes += _safe_unlink(candidate, dry_run=summary.dry_run)
                    summary.logs_deleted += 1
                elif _older_than(candidate, now, compress_minutes):
                    summary.freed_bytes += _compress_log(candidate, dry_run=summary.dry_run)
                    summary.logs_compressed += 1
            except FileNotFoundError:
                continue
        for candidate in sorted(logs.glob("cao_*.log.gz")):
            try:
                if candidate.resolve() in open_paths:
                    summary.skipped_open += 1
                elif _older_than(candidate, now, retention_minutes):
                    summary.freed_bytes += _safe_unlink(candidate, dry_run=summary.dry_run)
                    summary.logs_deleted += 1
            except FileNotFoundError:
                continue
        terminal_logs = logs / "terminal"
        if terminal_logs.is_dir():
            for candidate in sorted(terminal_logs.glob("*.log*")):
                try:
                    if candidate.resolve() in open_paths:
                        summary.skipped_open += 1
                    elif _older_than(candidate, now, retention_minutes):
                        summary.freed_bytes += _safe_unlink(candidate, dry_run=summary.dry_run)
                        summary.logs_deleted += 1
                except FileNotFoundError:
                    continue

    attachments = root / "state" / "cao" / "runtime" / "terminal-attachments"
    if attachments.is_dir():
        for candidate in sorted(attachments.glob("*/*")):
            try:
                metadata = candidate.lstat()
                if not candidate.is_file() or candidate.is_symlink():
                    continue
                if candidate.resolve() in open_paths:
                    summary.skipped_open += 1
                elif _older_than(candidate, now, retention_minutes):
                    summary.freed_bytes += _safe_unlink(candidate, dry_run=summary.dry_run)
                    summary.attachments_deleted += 1
            except FileNotFoundError:
                continue


def _pid_alive(pid: int, proc_root: Path = Path("/proc")) -> bool:
    return pid > 1 and (proc_root / str(pid)).exists()


def _ephemeral_marker(directory: Path) -> dict[str, Any] | None:
    marker = directory / ".cao-ephemeral.json"
    try:
        if marker.is_symlink() or not marker.is_file():
            return None
        data = cast(dict[str, Any], json.loads(marker.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None
    if data.get("version") != 1 or not isinstance(data.get("expires_at"), (int, float)):
        return None
    if not isinstance(data.get("owner_pid"), int):
        return None
    return data


def _cleanup_ephemeral_dirs(
    root: Path,
    summary: HousekeepingSummary,
    *,
    now: float,
    proc_root: Path = Path("/proc"),
) -> None:
    temp_root = root / "tmp"
    if not temp_root.is_dir():
        return
    for directory in sorted(temp_root.iterdir()):
        try:
            if directory.is_symlink() or not directory.is_dir():
                continue
            marker = _ephemeral_marker(directory)
            if marker is None:
                summary.skipped_unknown += 1
                continue
            if float(marker["expires_at"]) > now or _pid_alive(int(marker["owner_pid"]), proc_root):
                continue
            summary.ephemeral_resources_removed += 1
            summary.freed_bytes += _tree_size(directory)
            if not summary.dry_run:
                shutil.rmtree(directory)
        except FileNotFoundError:
            continue


def _process_start_epoch(process: Path, proc_root: Path = Path("/proc")) -> float | None:
    try:
        fields = (process / "stat").read_text(encoding="utf-8").split()
        ticks = int(fields[21])
        uptime = float((proc_root / "uptime").read_text(encoding="utf-8").split()[0])
        return time.time() - uptime + ticks / os.sysconf(os.sysconf_names["SC_CLK_TCK"])
    except (OSError, ValueError, IndexError):
        return None


def _process_group_id(process: Path) -> int | None:
    try:
        return int((process / "stat").read_text(encoding="utf-8").split()[4])
    except (OSError, ValueError, IndexError):
        return None


def _cleanup_marked_orphan_browsers(
    config: Mapping[str, Any],
    summary: HousekeepingSummary,
    *,
    now: float,
    proc_root: Path = Path("/proc"),
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    """Terminate only expired, marker-owned Playwright process groups."""
    try:
        runtime_uid = pwd.getpwnam(str(config["runtime_user"])).pw_uid
    except KeyError:
        return
    minimum_age = int(config["orphan_browser_age_minutes"]) * 60
    for process in sorted(proc_root.iterdir()) if proc_root.exists() else ():
        if not process.name.isdigit():
            continue
        pid = int(process.name)
        try:
            if process.stat().st_uid != runtime_uid:
                continue
            cmdline = (
                (process / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            )
            status = (process / "status").read_text(encoding="utf-8")
            parent_match = re.search(r"^PPid:\s+(\d+)$", status, re.MULTILINE)
            profile_match = re.search(r"--user-data-dir=(/tmp/(?:playwright|pw-)[^ ]+)", cmdline)
            started = _process_start_epoch(process, proc_root)
            if (
                not parent_match
                or int(parent_match.group(1)) != 1
                or not profile_match
                or started is None
            ):
                continue
            profile = Path(profile_match.group(1))
            marker = _ephemeral_marker(profile)
            process_group = _process_group_id(process)
            if (
                marker is None
                or marker.get("kind") != "playwright"
                or _pid_alive(int(marker["owner_pid"]), proc_root)
                or float(marker["expires_at"]) > now
                or now - started < minimum_age
                or process_group != pid
            ):
                continue
            summary.orphan_processes_closed += 1
            if summary.dry_run:
                continue
            os.killpg(process_group, signal.SIGTERM)
            sleeper(5)
            if _pid_alive(pid, proc_root):
                os.killpg(process_group, signal.SIGKILL)
                sleeper(1)
            if not _pid_alive(pid, proc_root) and profile.is_dir() and not profile.is_symlink():
                shutil.rmtree(profile)
        except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
            continue


def _referenced_browser_revisions(manifest_roots: Iterable[str]) -> tuple[set[str], bool]:
    """Return revisions and whether every configured manifest inventory was trustworthy."""
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
                data = json.loads(manifest.read_text(encoding="utf-8"))
                browsers = data.get("browsers")
                if not isinstance(browsers, list):
                    return revisions, False
                for browser in browsers:
                    if not isinstance(browser, dict):
                        return revisions, False
                    revision = browser.get("revision")
                    if isinstance(revision, str):
                        revisions.add(revision)
                    else:
                        return revisions, False
            except (OSError, ValueError):
                return revisions, False
    return revisions, manifests_found > 0


def _cleanup_browser_cache(
    config: Mapping[str, Any],
    summary: HousekeepingSummary,
    *,
    now: float,
    proc_root: Path = Path("/proc"),
    inventory_probe: Callable[[Path], tuple[set[Path], bool]] = _open_paths_inventory,
) -> None:
    """Inventory stale unreferenced browser revisions without deleting them."""
    cache = Path(str(config["playwright_browser_cache"]))
    if not cache.is_dir():
        return
    referenced, manifest_certain = _referenced_browser_revisions(
        config.get("playwright_manifest_roots", [])
    )
    if not manifest_certain:
        summary.skipped_unknown += 1
        summary.warnings.append("browser_manifest_inventory_uncertain")
        return
    open_paths, process_inventory_certain = inventory_probe(proc_root)
    if not process_inventory_certain:
        summary.skipped_unknown += 1
        summary.warnings.append("browser_process_inventory_uncertain")
        return
    retention = int(config["retention_minutes"])
    for candidate in sorted(cache.iterdir()):
        match = re.fullmatch(r"[a-zA-Z_-]+-(\d+)", candidate.name)
        if not match or candidate.is_symlink() or not candidate.is_dir():
            continue
        if match.group(1) in referenced or not _older_than(candidate, now, retention):
            continue
        summary.browser_revision_candidates += 1
        resolved = candidate.resolve()
        if any(path == resolved or resolved in path.parents for path in open_paths):
            summary.skipped_open += 1
    if summary.browser_revision_candidates:
        summary.warnings.append(
            f"browser_revision_cleanup_candidates:{summary.browser_revision_candidates}"
        )


def _reconcile_writer_leases(summary: HousekeepingSummary) -> None:
    """Retire runtimes only after exact positive process/tmux death evidence."""
    from cli_agent_orchestrator.clients.database import (
        cancel_child_assignments_for_terminal,
        cancel_workflows_for_terminal,
        list_worktree_writer_leases,
        mark_terminal_runtime_exited,
    )
    from cli_agent_orchestrator.clients.tmux import tmux_client

    try:
        leases = list_worktree_writer_leases()
    except Exception:
        summary.skipped_unknown += 1
        summary.warnings.append("writer_lease_inventory_uncertain")
        return
    for lease in leases:
        session_name = lease.get("tmux_session")
        window_name = lease.get("tmux_window")
        if not isinstance(session_name, str) or not isinstance(window_name, str):
            summary.skipped_unknown += 1
            summary.warnings.append(f"writer_lease_target_unknown:{lease['terminal_id']}")
            continue
        exists = tmux_client.window_exists(session_name, window_name)
        if exists is None:
            summary.skipped_unknown += 1
            summary.warnings.append(f"writer_lease_tmux_inventory_uncertain:{lease['terminal_id']}")
            continue
        positively_dead = exists is False
        if exists and lease.get("runtime_lifecycle") != "starting":
            command = tmux_client.get_pane_current_command(session_name, window_name)
            positively_dead = isinstance(command, str) and (
                command == "" or command in {"bash", "sh", "dash", "zsh", "fish"}
            )
        if not positively_dead:
            continue
        terminal_id = str(lease["terminal_id"])
        if summary.dry_run:
            summary.writer_leases_reconciled += 1
            continue
        if mark_terminal_runtime_exited(terminal_id):
            summary.writer_leases_reconciled += 1
            try:
                cancel_child_assignments_for_terminal(terminal_id)
                cancel_workflows_for_terminal(terminal_id)
                from cli_agent_orchestrator.services.inbox_service import (
                    wake_provider_execution_queue,
                )

                wake_provider_execution_queue()
            except Exception:
                summary.warnings.append(f"runtime_history_finalization_failed:{terminal_id}")


def _reconcile_supervisor_context_roles(summary: HousekeepingSummary) -> None:
    """Run the same exact-profile role repair used during API startup."""
    from cli_agent_orchestrator.services.terminal_service import (
        reconcile_terminal_context_roles,
    )

    try:
        repaired = reconcile_terminal_context_roles(dry_run=summary.dry_run)
    except Exception:
        summary.skipped_unknown += 1
        summary.warnings.append("supervisor_role_reconciliation_uncertain")
        return
    summary.supervisor_roles_reconciled += repaired


def _reconcile_retirement_cleanups(summary: HousekeepingSummary) -> None:
    """Resume exact post-exit cleanup intents without redispatching provider exit."""
    from cli_agent_orchestrator.clients.database import (
        claim_completed_child_retirement,
        complete_child_retirement,
        list_legacy_child_retirements_for_cleanup,
        list_pending_child_retirement_cleanups,
    )
    from cli_agent_orchestrator.services.terminal_service import cleanup_managed_worktree

    try:
        legacy = list_legacy_child_retirements_for_cleanup()
    except Exception:
        summary.skipped_unknown += 1
        summary.warnings.append("retirement_cleanup_inventory_uncertain")
        return
    for item in legacy:
        child = str(item["child_terminal_id"])
        if not item["identity_proven"]:
            summary.skipped_unknown += 1
            summary.warnings.append(f"retirement_cleanup_identity_unproven:{child}")
            continue
        if summary.dry_run:
            summary.retirement_cleanups_reconciled += 1
            continue
        claimed = claim_completed_child_retirement(
            str(item["parent_terminal_id"]),
            child,
            str(item["delegation_kind"]),
        )
        if not claimed.get("eligible"):
            summary.skipped_unknown += 1
            summary.warnings.append(f"retirement_cleanup_legacy_claim_failed:{child}")
    try:
        pending = list_pending_child_retirement_cleanups()
    except Exception:
        summary.skipped_unknown += 1
        summary.warnings.append("retirement_cleanup_pending_inventory_uncertain")
        return
    for item in pending:
        child = str(item["child_terminal_id"])
        token = item.get("claim_token")
        if not isinstance(token, str) or not token:
            summary.skipped_unknown += 1
            summary.warnings.append(f"retirement_cleanup_claim_unknown:{child}")
            continue
        if summary.dry_run:
            summary.retirement_cleanups_reconciled += 1
            continue
        try:
            cleanup_managed_worktree(item["intent"])
        except Exception:
            summary.skipped_unknown += 1
            summary.warnings.append(f"retirement_cleanup_unconfirmed:{child}")
            continue
        if complete_child_retirement(child, token, item["intent"], str(item["delegation_kind"])):
            summary.retirement_cleanups_reconciled += 1
        else:
            summary.skipped_unknown += 1
            summary.warnings.append(f"retirement_cleanup_finalization_raced:{child}")


def _reconcile_legacy_terminal_authority(summary: HousekeepingSummary) -> None:
    """Retire UNKNOWN rows only after their exact tmux target is proven absent."""
    from cli_agent_orchestrator.clients.database import (
        list_unreconciled_terminal_authorities,
        retire_unreconciled_terminal_authority,
    )
    from cli_agent_orchestrator.clients.tmux import tmux_client

    try:
        terminals = list_unreconciled_terminal_authorities()
    except Exception:
        summary.skipped_unknown += 1
        summary.warnings.append("legacy_authority_inventory_uncertain")
        return
    for terminal in terminals:
        terminal_id = str(terminal["id"])
        session_name = terminal.get("tmux_session")
        window_name = terminal.get("tmux_window")
        if not isinstance(session_name, str) or not isinstance(window_name, str):
            summary.skipped_unknown += 1
            summary.warnings.append(f"legacy_authority_target_unknown:{terminal_id}")
            continue
        exists = tmux_client.window_exists(session_name, window_name)
        if exists is None:
            summary.skipped_unknown += 1
            summary.warnings.append(f"legacy_authority_tmux_inventory_uncertain:{terminal_id}")
            continue
        if exists:
            summary.skipped_open += 1
            continue
        if summary.dry_run or retire_unreconciled_terminal_authority(terminal_id):
            summary.legacy_authority_reconciled += 1


def _command_running(name: str, proc_root: Path = Path("/proc")) -> bool:
    needle = name.encode()
    for process in proc_root.iterdir() if proc_root.exists() else ():
        if not process.name.isdigit():
            continue
        try:
            command = (process / "cmdline").read_bytes().split(b"\0", 1)[0]
            if Path(os.fsdecode(command)).name.encode() == needle:
                return True
        except OSError:
            continue
    return False


def _cleanup_labelled_docker_resources(
    summary: HousekeepingSummary,
    *,
    now: float,
    proc_root: Path = Path("/proc"),
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    timeout_seconds: float = 20,
) -> None:
    """Remove only explicitly labelled, expired resources with a dead owner."""
    docker = shutil.which("docker")
    if docker is None:
        return
    catalogs = (
        (
            "container",
            [docker, "ps", "-a", "--filter", "label=cao.ephemeral=true", "--format", "{{.ID}}"],
        ),
        (
            "volume",
            [
                docker,
                "volume",
                "ls",
                "--filter",
                "label=cao.ephemeral=true",
                "--format",
                "{{.Name}}",
            ],
        ),
    )
    for kind, list_command in catalogs:
        try:
            listed = runner(
                list_command,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            summary.warnings.append(f"docker_{kind}_inventory_timeout")
            continue
        if listed.returncode:
            summary.warnings.append(f"docker_{kind}_inventory_failed")
            continue
        for identifier in filter(None, (line.strip() for line in listed.stdout.splitlines())):
            inspect_command = (
                [docker, "inspect", "--format", "{{json .Config.Labels}}", identifier]
                if kind == "container"
                else [docker, "volume", "inspect", "--format", "{{json .Labels}}", identifier]
            )
            try:
                inspected = runner(
                    inspect_command,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                summary.skipped_unknown += 1
                continue
            try:
                labels = json.loads(inspected.stdout) if inspected.returncode == 0 else {}
                expires_at = float(labels.get("cao.expires_at", "inf"))
                owner_pid = int(labels.get("cao.owner_pid", "-1"))
            except (TypeError, ValueError, json.JSONDecodeError):
                summary.skipped_unknown += 1
                continue
            if (
                labels.get("cao.ephemeral") != "true"
                or expires_at > now
                or _pid_alive(owner_pid, proc_root)
            ):
                continue
            if kind == "container":
                try:
                    running = runner(
                        [docker, "inspect", "--format", "{{.State.Running}}", identifier],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=timeout_seconds,
                    )
                except subprocess.TimeoutExpired:
                    summary.skipped_unknown += 1
                    continue
                if running.returncode != 0 or running.stdout.strip().lower() != "false":
                    summary.skipped_unknown += 1
                    continue
            else:
                try:
                    references = runner(
                        [
                            docker,
                            "ps",
                            "-a",
                            "--filter",
                            f"volume={identifier}",
                            "--format",
                            "{{.ID}}",
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=timeout_seconds,
                    )
                except subprocess.TimeoutExpired:
                    summary.skipped_unknown += 1
                    continue
                if references.returncode != 0 or references.stdout.strip():
                    summary.skipped_unknown += 1
                    continue
            summary.ephemeral_resources_removed += 1
            if summary.dry_run:
                continue
            remove_command = (
                [docker, "rm", identifier]
                if kind == "container"
                else [docker, "volume", "rm", identifier]
            )
            try:
                removed = runner(
                    remove_command,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                summary.ok = False
                summary.warnings.append(f"docker_{kind}_remove_timeout:{identifier}")
                continue
            if removed.returncode:
                summary.ok = False
                summary.warnings.append(f"docker_{kind}_remove_failed:{identifier}")


def _maintain_package_caches(config: Mapping[str, Any], summary: HousekeepingSummary) -> None:
    threshold = int(config["cache_prune_threshold_gib"]) * 1024**3
    for entry in config.get("package_caches", []):
        path = Path(str(entry.get("path", "")))
        command = entry.get("command")
        if not isinstance(command, list) or not command or _tree_size(path) < threshold:
            continue
        executable = shutil.which(str(command[0]))
        name = str(entry.get("name", Path(str(command[0])).name))
        if executable is None or _command_running(name):
            summary.warnings.append(f"cache_prune_skipped:{name}")
            continue
        summary.cache_pruned += 1
        if not summary.dry_run:
            try:
                completed = subprocess.run(
                    [executable, *map(str, command[1:])],
                    check=False,
                    timeout=float(config["subprocess_timeout_seconds"]),
                )
            except subprocess.TimeoutExpired:
                summary.ok = False
                summary.warnings.append(f"cache_prune_timeout:{name}")
                continue
            if completed.returncode:
                summary.ok = False
                summary.warnings.append(f"cache_prune_failed:{name}:{completed.returncode}")


def _inventory_warnings(
    root: Path, config: Mapping[str, Any], summary: HousekeepingSummary
) -> None:
    gib = 1024**3
    if _tree_size(root / "state" / "cao" / "logs") > int(config["log_tree_warning_gib"]) * gib:
        summary.warnings.append("cao_log_tree_over_threshold")
    if _tree_size(root / "backups") > int(config["backup_tree_warning_gib"]) * gib:
        summary.warnings.append("backups_over_threshold_preserved")
    # Deployment cleanup is intentionally inventory-only until active and rollback
    # identities are represented by explicit metadata. Unknown deployments are recovery assets.
    tools = root / "tools"
    if tools.is_dir() and len(list(tools.glob("cli-agent-orchestrator*"))) > 2:
        summary.warnings.append("deployment_inventory_requires_retention_metadata")


def _write_status(root: Path, summary: HousekeepingSummary) -> None:
    status_path = root / "state" / "cao" / "housekeeping-status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = status_path.with_name(f".{status_path.name}.{os.getpid()}")
    temporary.write_text(json.dumps(summary.as_dict(), sort_keys=True), encoding="utf-8")
    os.replace(temporary, status_path)


def _schedule_receipt_path(root: Path) -> Path:
    return root / "state" / "cao" / "housekeeping-schedule-receipts.json"


def _read_schedule_receipts(root: Path) -> tuple[dict[str, float], bool]:
    path = _schedule_receipt_path(root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        runs = value.get("last_successful_run")
        if value.get("schema_version") != 1 or not isinstance(runs, dict):
            raise ValueError("invalid schedule receipt")
        result: dict[str, float] = {}
        for mode in ("frequent", "weekly"):
            item = runs.get(mode)
            if item is not None:
                if isinstance(item, bool) or not isinstance(item, (int, float)) or item < 0:
                    raise ValueError("invalid schedule receipt time")
                result[mode] = float(item)
        return result, True
    except FileNotFoundError:
        return {}, True
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        # Running a safely planned cleanup is preferable to silently disabling
        # maintenance forever because scheduling metadata became unreadable.
        return {}, False


def _write_schedule_receipt(root: Path, mode: str, completed_at: float) -> None:
    receipts, _certain = _read_schedule_receipts(root)
    receipts[mode] = completed_at
    path = _schedule_receipt_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}")
    temporary.write_text(
        json.dumps(
            {"schema_version": 1, "last_successful_run": receipts},
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _scheduled_mode_due(
    root: Path, mode: str, schedule: Mapping[str, str], *, now: float
) -> tuple[bool, str | None]:
    receipts, certain = _read_schedule_receipts(root)
    warning = None if certain else "schedule_receipt_inventory_uncertain"
    previous = receipts.get(mode)
    if mode == "frequent":
        value = schedule["frequent"]
        amount = int(value[:-1])
        seconds = amount * {"m": 60, "h": 3600, "d": 86400}[value[-1]]
        return previous is None or now - previous >= seconds, warning
    if mode == "weekly":
        day_text, clock, _utc = schedule["weekly"].split()
        hour, minute = (int(part) for part in clock.split(":"))
        weekday = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun").index(day_text)
        current = datetime.fromtimestamp(now, tz=timezone.utc)
        most_recent = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        most_recent -= timedelta(days=(current.weekday() - weekday) % 7)
        if most_recent > current:
            most_recent -= timedelta(days=7)
        boundary = most_recent.timestamp()
        return previous is None or previous < boundary, warning
    return False, "pressure_schedule_is_event_driven"


def get_housekeeping_settings(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    from cli_agent_orchestrator.clients.database import (
        ensure_housekeeping_settings,
        update_housekeeping_settings,
    )
    from cli_agent_orchestrator.services.housekeeping.models import (
        default_settings,
        validate_settings,
    )

    cfg = dict(config or load_operations_config())
    stored = ensure_housekeeping_settings(default_settings(cfg))
    if stored.get("schedule") == {
        "frequent": "every 6 hours",
        "weekly": "weekly",
        "pressure": "on RED disk recovery",
    }:
        stored = update_housekeeping_settings(
            {**stored, "schedule": default_settings(cfg)["schedule"]},
            actor="migration:housekeeping-schedule-v1",
            reason="normalize_legacy_schedule",
        )
    return validate_settings(stored)


def set_housekeeping_settings(values: Mapping[str, Any], *, actor: str) -> dict[str, Any]:
    from cli_agent_orchestrator.clients.database import (
        ensure_housekeeping_settings,
        update_housekeeping_settings,
    )
    from cli_agent_orchestrator.services.housekeeping.models import (
        default_settings,
        validate_settings,
    )

    cfg = load_operations_config()
    ensure_housekeeping_settings(default_settings(cfg))
    validated = validate_settings(values)
    return validate_settings(update_housekeeping_settings(validated, actor=actor))


def plan_housekeeping(
    *,
    config: Mapping[str, Any] | None = None,
    mode: str,
    now: float | None = None,
    proc_root: Path = Path("/proc"),
):
    from cli_agent_orchestrator.services.housekeeping.models import HousekeepingMode
    from cli_agent_orchestrator.services.housekeeping.planner import build_plan

    if mode not in {"frequent", "weekly", "pressure"}:
        raise ValueError("invalid housekeeping mode")
    cfg = dict(config or load_operations_config())
    settings = get_housekeeping_settings(cfg)
    current = time.time() if now is None else now
    return build_plan(
        root=Path(str(cfg["root"])),
        config=cfg,
        settings=settings,
        mode=cast(HousekeepingMode, mode),
        now=current,
        open_inventory=lambda: _open_paths_inventory(proc_root),
        proc_root=proc_root,
    )


def run_housekeeping(
    *,
    config: Mapping[str, Any] | None = None,
    dry_run: bool,
    mode: str,
    now: float | None = None,
    proc_root: Path = Path("/proc"),
    scheduled: bool = False,
    expected_plan_id: str | None = None,
) -> HousekeepingSummary:
    if not dry_run and not scheduled and expected_plan_id is None:
        raise RuntimeError("HOUSEKEEPING_PLAN_REQUIRED")
    cfg = dict(config or load_operations_config())
    if mode in {"weekly", "pressure"} and not cfg.get("_housekeeping_heavy_slot"):
        from cli_agent_orchestrator.services.operations_service import acquire_heavy_slot

        with acquire_heavy_slot(cfg, recovery_safe=True):
            nested = dict(cfg)
            nested["_housekeeping_heavy_slot"] = True
            return run_housekeeping(
                config=nested,
                dry_run=dry_run,
                mode=mode,
                now=now,
                proc_root=proc_root,
                scheduled=scheduled,
                expected_plan_id=expected_plan_id,
            )
    root = Path(str(cfg["root"]))
    lock_dir = Path(str(cfg["lock_dir"]))
    lock_dir.mkdir(parents=True, exist_ok=True)
    summary = HousekeepingSummary(dry_run=dry_run, mode=mode)
    current = time.time() if now is None else now
    with (lock_dir / "housekeeping.lock").open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("HOUSEKEEPING_BUSY") from exc
        settings = get_housekeeping_settings(cfg)
        if scheduled:
            due, schedule_warning = _scheduled_mode_due(
                root, mode, settings["schedule"], now=current
            )
            if schedule_warning:
                summary.warnings.append(schedule_warning)
            if not due:
                summary.warnings.append("schedule_not_due")
                return summary
        summary.disk_before = shutil.disk_usage("/").free
        from cli_agent_orchestrator.services.housekeeping.executor import execute_plan

        plan = plan_housekeeping(config=cfg, mode=mode, now=current, proc_root=proc_root)
        if expected_plan_id is not None and plan.plan_id != expected_plan_id:
            raise RuntimeError("HOUSEKEEPING_PLAN_CHANGED")
        summary.plan_id = plan.plan_id
        summary.planned_candidates = len(plan.candidates)
        summary.reclaimable_bytes = plan.reclaimable_bytes
        summary.warnings.extend(plan.warnings)
        actionable = [candidate for candidate in plan.candidates if candidate.action != "preserve"]
        if dry_run:
            summary.freed_bytes += plan.reclaimable_bytes
            summary.logs_compressed += sum(
                candidate.category == "logs" and candidate.action == "compress"
                for candidate in actionable
            )
            summary.logs_deleted += sum(
                candidate.category == "logs" and candidate.action == "delete"
                for candidate in actionable
            )
            summary.attachments_deleted += sum(
                candidate.category == "attachments" for candidate in actionable
            )
            summary.orphan_processes_closed += sum(
                candidate.resource_kind == "browser_process_group" for candidate in actionable
            )
            summary.ephemeral_resources_removed += sum(
                candidate.category == "ephemeral"
                and candidate.resource_kind != "browser_process_group"
                for candidate in actionable
            )
            summary.browser_revision_candidates += sum(
                candidate.category == "browser_cache" for candidate in actionable
            )
            summary.browser_revisions_removed += sum(
                candidate.category == "browser_cache" for candidate in actionable
            )
            summary.cache_pruned += sum(
                candidate.category == "package_cache" for candidate in actionable
            )
        else:
            report = execute_plan(
                plan,
                config=cfg,
                open_inventory=lambda: _open_paths_inventory(proc_root),
                settings=settings,
                proc_root=proc_root,
            )
            summary.ok = summary.ok and report.ok
            summary.freed_bytes += report.freed_bytes
            summary.execution_failures.extend(report.failures)
            summary.warnings.extend(
                f"{item['reason_code']}:{item['candidate']}" for item in report.failures
            )
            executed = set(report.executed)
            summary.logs_compressed += sum(
                candidate.canonical_identity in executed
                and candidate.category == "logs"
                and candidate.action == "compress"
                for candidate in actionable
            )
            summary.logs_deleted += sum(
                candidate.canonical_identity in executed
                and candidate.category == "logs"
                and candidate.action == "delete"
                for candidate in actionable
            )
            summary.attachments_deleted += sum(
                candidate.canonical_identity in executed and candidate.category == "attachments"
                for candidate in actionable
            )
            summary.orphan_processes_closed += sum(
                candidate.canonical_identity in executed
                and candidate.resource_kind == "browser_process_group"
                for candidate in actionable
            )
            summary.ephemeral_resources_removed += sum(
                candidate.canonical_identity in executed
                and candidate.category == "ephemeral"
                and candidate.resource_kind != "browser_process_group"
                for candidate in actionable
            )
            summary.browser_revision_candidates += sum(
                candidate.category == "browser_cache" for candidate in actionable
            )
            summary.browser_revisions_removed += sum(
                candidate.canonical_identity in executed and candidate.category == "browser_cache"
                for candidate in actionable
            )
            summary.cache_pruned += sum(
                candidate.canonical_identity in executed and candidate.category == "package_cache"
                for candidate in actionable
            )
        _reconcile_supervisor_context_roles(summary)
        _reconcile_writer_leases(summary)
        _reconcile_retirement_cleanups(summary)
        _reconcile_legacy_terminal_authority(summary)
        _inventory_warnings(root, cfg, summary)
        summary.disk_after = shutil.disk_usage("/").free
        summary.freed_bytes = max(summary.freed_bytes, summary.disk_after - summary.disk_before, 0)
        if not dry_run:
            if summary.ok and mode in {"frequent", "weekly"}:
                _write_schedule_receipt(root, mode, current)
            _write_status(root, summary)
        return summary


def housekeeping_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cao-housekeeping")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mode", choices=("frequent", "weekly", "pressure"), default="frequent")
    parser.add_argument(
        "--plan-id",
        help="content-addressed plan ID from an inspected --dry-run",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help="honour the canonical persisted schedule before running",
    )
    args = parser.parse_args(argv)
    try:
        summary = run_housekeeping(
            dry_run=args.dry_run,
            mode=args.mode,
            scheduled=args.scheduled,
            expected_plan_id=args.plan_id,
        )
    except Exception as exc:
        try:
            config = load_operations_config()
            failed = HousekeepingSummary(ok=False, dry_run=args.dry_run, mode=args.mode)
            failed.warnings.append(f"{type(exc).__name__}:{exc}")
            if not args.dry_run and str(exc) not in {
                "HOUSEKEEPING_PLAN_REQUIRED",
                "HOUSEKEEPING_PLAN_CHANGED",
            }:
                _write_status(Path(str(config["root"])), failed)
        except Exception:
            pass
        print(f"HOUSEKEEPING_FAILED error={type(exc).__name__}:{exc}")
        return 1
    data = summary.as_dict()
    if args.json:
        print(json.dumps(data, sort_keys=True, separators=(",", ":")))
    else:
        print("HOUSEKEEPING_OK" if summary.ok else "HOUSEKEEPING_FAILED")
        for key, value in data.items():
            if key != "ok":
                rendered = ",".join(value) if isinstance(value, list) else value
                print(f"{key}={rendered}")
    return 0 if summary.ok else 1
