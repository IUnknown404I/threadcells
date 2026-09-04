"""Deterministic Housekeeping.P2 candidate discovery and immutable planning."""

from __future__ import annotations

import grp
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import zlib
from concurrent.futures import ThreadPoolExecutor
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
    measure_preserved: bool = False,
    preserved_size: int | None = None,
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
        size = (
            preserved_size
            if preserved_size is not None
            else (
                metadata.st_size if path.is_file() else _tree_size(path) if measure_preserved else 0
            )
        )
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
            size if resolved_action in {"delete", "terminate", "prune", "retire"} else 0
        ),
        action=resolved_action,  # type: ignore[arg-type]
        retention_reason=retention_reason,
        protection_reason=protection_reason,
        resource_kind=resource_kind,  # type: ignore[arg-type]
        attributes=tuple(sorted(attributes.items())),
    )


def _tree_size(path: Path) -> int:
    return _tree_size_inventory(path)[0]


def _tree_size_inventory(path: Path) -> tuple[int, bool]:
    if path.is_file() and not path.is_symlink():
        try:
            return path.lstat().st_size, True
        except OSError:
            return 0, False
    try:
        completed = subprocess.run(
            ["du", "-sb", "--apparent-size", "--", str(path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0, False
    first_line = completed.stdout.splitlines()[0] if completed.stdout else ""
    raw_size, separator, _name = first_line.partition("\t")
    try:
        size = int(raw_size) if separator else 0
    except ValueError:
        size = 0
    return size, completed.returncode == 0 and bool(separator)


def _parallel_tree_sizes(paths: list[Path]) -> dict[Path, tuple[int, bool]]:
    """Measure independent bounded roots concurrently and preserve input identity."""
    workers = max(1, min(8, len(paths) or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(zip(paths, pool.map(_tree_size_inventory, paths), strict=True))


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
    full_cleanup: bool = False,
) -> list[HousekeepingCandidate]:
    if not policy.get("enabled") and not full_cleanup:
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
            elif not full_cleanup and float(marker["expires_at"]) > now:
                reason = "EPHEMERAL_NOT_EXPIRED"
                retention_reason = "within_marker_lifetime"
            elif _pid_alive(int(marker["owner_pid"]), proc_root):
                reason = "EPHEMERAL_OWNER_ACTIVE"
                retention_reason = "marker_owner_active"
            else:
                action = "delete"
                retention_reason = (
                    "full_cleanup_marker_dead_owner"
                    if full_cleanup
                    else "expired_marker_dead_owner"
                )
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
    full_cleanup: bool = False,
) -> tuple[list[HousekeepingCandidate], list[str]]:
    if not policy.get("enabled") and not full_cleanup:
        return [], []
    configured_caches = config.get("playwright_browser_caches")
    if configured_caches is None:
        configured_caches = [config.get("playwright_browser_cache")]
    if not isinstance(configured_caches, list):
        return [], ["browser_cache_roots_invalid"]
    referenced, certain = _referenced_browser_revisions(
        [str(item) for item in config.get("playwright_manifest_roots", [])]
    )
    warnings = [] if certain else ["browser_manifest_inventory_uncertain"]
    retention = int(policy["retain_minutes"])
    result: list[HousekeepingCandidate] = []
    for cache_value in configured_caches:
        if not isinstance(cache_value, str) or not cache_value:
            warnings.append("browser_cache_root_invalid")
            continue
        cache = Path(cache_value)
        if not cache.is_absolute() or cache.is_symlink() or not cache.is_dir():
            continue
        try:
            inventory_paths = [
                path
                for path in sorted(cache.iterdir())
                if re.fullmatch(r"[a-zA-Z_-]+-(\d+)", path.name)
                and not path.is_symlink()
                and path.is_dir()
            ]
        except OSError:
            warnings.append("browser_cache_root_unreadable")
            continue
        measured = _parallel_tree_sizes(inventory_paths)
        for path in inventory_paths:
            try:
                match = re.fullmatch(r"[a-zA-Z_-]+-(\d+)", path.name)
                assert match is not None
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
                elif not full_cleanup and not _older_than(path, now, retention):
                    reason = "BROWSER_WITHIN_RETENTION"
                    action = "preserve"
                    retention_reason = "within_retention_window"
                size, size_certain = measured[path]
                if not size_certain:
                    warnings.append("browser_cache_inventory_incomplete")
                    reason = reason or "BROWSER_SIZE_INVENTORY_UNKNOWN"
                    action = "preserve"
                result.append(
                    _candidate(
                        path,
                        category="browser_cache",
                        action=action,
                        retention_reason=retention_reason,
                        protection=protection,
                        forced_protection=reason,
                        measure_preserved=True,
                        preserved_size=size,
                    )
                )
            except FileNotFoundError:
                continue
    return result, warnings


def _reproducible_marker(path: Path) -> dict[str, Any] | None:
    marker = path / ".threadcells-reproducible.json"
    try:
        if marker.is_symlink() or not marker.is_file():
            return None
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    if (
        value.get("schema_version") != 1
        or value.get("owner") != "threadcells"
        or value.get("kind") not in {"cache", "generated", "test_evidence", "candidate"}
        or isinstance(value.get("created_at"), bool)
        or not isinstance(value.get("created_at"), (int, float))
        or isinstance(value.get("owner_pid"), bool)
        or not isinstance(value.get("owner_pid"), int)
    ):
        return None
    return value


def _plan_reproducible_caches(
    config: Mapping[str, Any],
    protection: ProtectedSet,
    *,
    now: float,
    proc_root: Path,
    full_cleanup: bool = False,
) -> tuple[list[HousekeepingCandidate], list[str]]:
    """Inventory only direct children of explicitly approved cache roots."""
    values = config.get("reproducible_cache_roots", [])
    if not isinstance(values, list):
        return [], ["reproducible_cache_roots_invalid"]
    try:
        runtime_uid = pwd.getpwnam(str(config["runtime_user"])).pw_uid
    except (KeyError, TypeError):
        return [], ["reproducible_cache_owner_unknown"]
    retention = int(config.get("reproducible_cache_retain_minutes", 1440))
    prefixes_raw = config.get("reproducible_cache_owned_prefixes", [])
    prefixes: tuple[str, ...] = ()
    prefix_warning: str | None = None
    if (
        isinstance(prefixes_raw, list)
        and len(prefixes_raw) == len(set(prefixes_raw))
        and all(
            isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{1,62}-", value)
            for value in prefixes_raw
        )
    ):
        prefixes = tuple(prefixes_raw)
    else:
        prefix_warning = "reproducible_cache_owned_prefixes_invalid"
    configured_browser_roots = config.get("playwright_browser_caches") or [
        config.get("playwright_browser_cache")
    ]
    if not isinstance(configured_browser_roots, list):
        configured_browser_roots = []
    specialized_roots = {
        Path(value).resolve(strict=False)
        for value in configured_browser_roots
        if isinstance(value, str) and Path(value).is_absolute()
    }
    specialized_roots.update(
        Path(str(entry.get("path", ""))).resolve(strict=False)
        for entry in config.get("package_caches", [])
        if isinstance(entry, Mapping) and Path(str(entry.get("path", ""))).is_absolute()
    )
    result: list[HousekeepingCandidate] = []
    warnings: list[str] = [prefix_warning] if prefix_warning else []
    for value in values:
        root = Path(str(value))
        try:
            resolved_root = root.resolve(strict=True)
            if (
                not root.is_absolute()
                or root != resolved_root
                or root.is_symlink()
                or not root.is_dir()
                or root.stat().st_uid != runtime_uid
            ):
                warnings.append("reproducible_cache_root_invalid")
                continue
            children = sorted(root.iterdir())
        except OSError:
            warnings.append("reproducible_cache_root_unreadable")
            continue
        inventory_paths: list[tuple[Path, Path]] = []
        for path in children:
            try:
                if path.is_symlink():
                    result.append(
                        HousekeepingCandidate(
                            category="reproducible_cache",
                            path=str(path.absolute()),
                            canonical_identity=f"reproducible_cache:{path.absolute()}",
                            fingerprint=resource_fingerprint(
                                {"path": str(path.absolute()), "symlink": True}
                            ),
                            bytes=path.lstat().st_size,
                            estimated_reclaim_bytes=0,
                            action="preserve",
                            retention_reason="path_identity_invalid",
                            protection_reason="REPRODUCIBLE_PATH_SYMLINK",
                            resource_kind="reproducible_cache",
                        )
                    )
                    continue
                resolved = path.resolve(strict=True)
                if resolved in specialized_roots:
                    continue
                if (
                    not path.is_dir()
                    or resolved.parent != resolved_root
                    or path.stat().st_uid != runtime_uid
                ):
                    continue
                inventory_paths.append((path, resolved))
            except FileNotFoundError:
                continue
            except OSError:
                warnings.append("reproducible_cache_candidate_unreadable")
        measured = _parallel_tree_sizes([path for path, _resolved in inventory_paths])
        for path, resolved in inventory_paths:
            try:
                marker = _reproducible_marker(path)
                owned_prefix = next(
                    (
                        prefix
                        for prefix in prefixes
                        if path.name.startswith(prefix) and len(path.name) > len(prefix)
                    ),
                    None,
                )
                reason: str | None = None
                action = "delete"
                retention_reason = f"marked_older_than_{retention}_minutes"
                if marker is None and owned_prefix is None:
                    reason = "REPRODUCIBLE_MARKER_UNKNOWN"
                    action = "preserve"
                    retention_reason = "marker_unknown"
                elif (
                    not full_cleanup
                    and (
                        float(marker["created_at"]) if marker is not None else path.lstat().st_mtime
                    )
                    > now - retention * 60
                ):
                    reason = "REPRODUCIBLE_WITHIN_RETENTION"
                    action = "preserve"
                    retention_reason = "within_retention_window"
                elif marker is not None and _pid_alive(int(marker["owner_pid"]), proc_root):
                    reason = "REPRODUCIBLE_OWNER_ACTIVE"
                    action = "preserve"
                    retention_reason = "marker_owner_active"
                elif owned_prefix is not None:
                    retention_reason = f"owned_prefix_older_than_{retention}_minutes"
                reason = reason or protection.reason(path, "reproducible_cache")
                if reason is not None:
                    action = "preserve"
                size, size_certain = measured[path]
                if action == "delete":
                    fingerprint, size = candidate_fingerprint(path)
                else:
                    if not size_certain:
                        warnings.append("reproducible_cache_inventory_incomplete")
                    metadata = path.lstat()
                    fingerprint = resource_fingerprint(
                        {
                            "path": str(resolved),
                            "device": metadata.st_dev,
                            "inode": metadata.st_ino,
                            "mode": metadata.st_mode,
                            "size": size,
                            "mtime_ns": metadata.st_mtime_ns,
                        }
                    )
                result.append(
                    HousekeepingCandidate(
                        category="reproducible_cache",
                        path=str(resolved),
                        canonical_identity=f"reproducible_cache:{resolved}",
                        fingerprint=fingerprint,
                        bytes=size,
                        estimated_reclaim_bytes=size if action == "delete" else 0,
                        action=action,  # type: ignore[arg-type]
                        retention_reason=retention_reason,
                        protection_reason=reason,
                        resource_kind="reproducible_cache",
                        attributes=(("root", str(resolved_root)),),
                    )
                )
            except FileNotFoundError:
                continue
            except OSError:
                warnings.append("reproducible_cache_candidate_unreadable")
    return result, warnings


def _plan_full_cleanup_artifacts(
    config: Mapping[str, Any],
    protection: ProtectedSet,
    *,
    proc_root: Path,
    claimed_paths: set[Path] | None = None,
) -> tuple[list[HousekeepingCandidate], list[str]]:
    """Inventory direct children of explicit ThreadCells artifact roots.

    Names are owned only by an exact configured name or prefix.  Git
    authorities, symlinks, active package owners, foreign owners, and unknown
    names remain visible protected resources.  Specialized planners may claim
    paths first to prevent contradictory duplicate candidates.
    """
    entries = config.get("full_cleanup_artifact_roots", [])
    if not isinstance(entries, list):
        return [], ["full_cleanup_artifact_roots_invalid"]
    try:
        runtime_uid = pwd.getpwnam(str(config["runtime_user"])).pw_uid
    except (KeyError, TypeError):
        return [], ["full_cleanup_artifact_owner_unknown"]
    claimed = claimed_paths or set()
    candidates: list[HousekeepingCandidate] = []
    warnings: list[str] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            warnings.append("full_cleanup_artifact_root_invalid")
            continue
        root = Path(str(entry.get("path", "")))
        names = entry.get("owned_names", [])
        prefixes = entry.get("owned_prefixes", [])
        process_names = entry.get("process_names", {})
        if (
            not root.is_absolute()
            or not isinstance(names, list)
            or not isinstance(prefixes, list)
            or not isinstance(process_names, Mapping)
            or any(
                not isinstance(value, str)
                or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{1,126}", value)
                for value in names
            )
            or any(
                not isinstance(value, str)
                or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{1,62}-", value)
                for value in prefixes
            )
            or any(
                not isinstance(key, str)
                or key not in names
                or not isinstance(value, str)
                or not re.fullmatch(r"[a-zA-Z0-9._+-]{2,64}", value)
                for key, value in process_names.items()
            )
        ):
            warnings.append("full_cleanup_artifact_policy_invalid")
            continue
        try:
            resolved_root = root.resolve(strict=True)
            root_stat = root.stat()
            if (
                resolved_root != root
                or root.is_symlink()
                or not root.is_dir()
                or root_stat.st_uid != runtime_uid
            ):
                warnings.append("full_cleanup_artifact_root_invalid")
                continue
            children = sorted(root.iterdir())
        except OSError:
            warnings.append("full_cleanup_artifact_root_unreadable")
            continue
        measurements = _parallel_tree_sizes([path for path in children if not path.is_symlink()])
        for path in children:
            lexical = path.absolute()
            if lexical in claimed:
                continue
            owned = path.name in names or any(path.name.startswith(prefix) for prefix in prefixes)
            reason: str | None = None
            size = path.lstat().st_size if path.is_symlink() else measurements[path][0]
            size_certain = True if path.is_symlink() else measurements[path][1]
            if path.is_symlink():
                reason = "ARTIFACT_SYMLINK_PROTECTED"
            elif path.lstat().st_uid != runtime_uid:
                reason = "ARTIFACT_OWNER_UNKNOWN"
            elif not owned:
                reason = "ARTIFACT_OWNERSHIP_UNKNOWN"
            elif not size_certain:
                reason = "ARTIFACT_SIZE_UNKNOWN"
            elif path.is_dir() and ((path / ".git").exists() or (path / ".git").is_symlink()):
                reason = "ARTIFACT_GIT_AUTHORITY"
            elif path.name in process_names:
                running = package_command_running(
                    str(process_names[path.name]), proc_root, runtime_uid
                )
                if running is None:
                    reason = "ARTIFACT_PROCESS_INVENTORY_UNKNOWN"
                elif running:
                    reason = "ARTIFACT_OWNER_ACTIVE"
            if path.is_symlink():
                metadata = path.lstat()
                candidate = HousekeepingCandidate(
                    category="build_artifact",
                    path=str(lexical),
                    canonical_identity=f"build_artifact:{lexical}",
                    fingerprint=resource_fingerprint(
                        {
                            "path": str(lexical),
                            "device": metadata.st_dev,
                            "inode": metadata.st_ino,
                            "mode": metadata.st_mode,
                            "size": metadata.st_size,
                            "mtime_ns": metadata.st_mtime_ns,
                        }
                    ),
                    bytes=size,
                    estimated_reclaim_bytes=0,
                    action="preserve",
                    retention_reason="full_cleanup_owned_artifact",
                    protection_reason=reason,
                )
            else:
                candidate = _candidate(
                    path,
                    category="build_artifact",
                    action="delete",
                    retention_reason="full_cleanup_owned_artifact",
                    protection=protection,
                    forced_protection=reason,
                    measure_preserved=True,
                    preserved_size=size,
                )
            candidates.append(candidate)
    return candidates, warnings


def _plan_protected_inventory(
    config: Mapping[str, Any],
) -> tuple[list[HousekeepingCandidate], list[str]]:
    result: list[HousekeepingCandidate] = []
    warnings: list[str] = []
    values = config.get("protected_inventory_roots", [])
    if not isinstance(values, list):
        return [], ["protected_inventory_roots_invalid"]
    inventories: list[tuple[Mapping[str, Any], Path, Path, str, str]] = []
    for entry in values:
        if not isinstance(entry, Mapping):
            warnings.append("protected_inventory_entry_invalid")
            continue
        path = Path(str(entry.get("path", "")))
        category = str(entry.get("category", "protected_storage"))
        reason = str(entry.get("reason", "RETENTION_AUTHORITY_UNKNOWN"))
        try:
            if not path.is_absolute() or path.is_symlink() or not path.exists():
                continue
            inventories.append((entry, path, path.resolve(strict=True), category, reason))
        except OSError:
            warnings.append(f"protected_inventory_unreadable:{category}")
    measured = _parallel_tree_sizes(
        [path for _entry, path, _resolved, _category, _reason in inventories]
    )
    for entry, path, resolved, category, reason in inventories:
        try:
            size, certain = measured[path]
            if not certain:
                warnings.append(f"protected_inventory_incomplete:{category}")
            result.append(
                _resource_candidate(
                    category=category,
                    resource_kind="inventory",
                    identity=str(resolved),
                    fingerprint_payload={
                        "path": str(resolved),
                        "size": size,
                        "mtime_ns": path.lstat().st_mtime_ns,
                    },
                    size=size,
                    action="preserve",
                    retention_reason="inventory_only",
                    protection_reason=reason,
                    attributes={"purpose": str(entry.get("purpose", "protected storage"))},
                )
            )
        except OSError:
            warnings.append(f"protected_inventory_unreadable:{category}")
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
    full_cleanup: bool = False,
) -> tuple[list[HousekeepingCandidate], list[str]]:
    if not policy.get("enabled") and not full_cleanup:
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
    full_cleanup: bool = False,
) -> tuple[list[HousekeepingCandidate], list[str]]:
    if not policy.get("enabled") and not full_cleanup:
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


def package_command_running(name: str, proc_root: Path, runtime_uid: int) -> bool | None:
    """Return whether the runtime owner has the package command or its script resident."""
    script_names = {name, f"{name}.js", f"{name}.cjs", f"{name}-cli.js", f"{name}-cli.cjs"}
    try:
        processes = list(proc_root.iterdir())
    except OSError:
        return None
    for process in processes:
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != runtime_uid:
                continue
            arguments = (process / "cmdline").read_bytes().split(b"\0")
            if any(
                Path(os.fsdecode(argument)).name in script_names
                for argument in arguments
                if argument
            ):
                return True
        except FileNotFoundError:
            continue
        except OSError:
            return None
    return False


def package_command_bound_to_path(
    command: object,
    *,
    path_argument: object,
    path: Path,
) -> bool:
    """Require an explicit command option to bind pruning to the planned root."""
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(argument, str) and argument for argument in command)
        or not isinstance(path_argument, str)
        or not path_argument.startswith("--")
        or command.count(path_argument) != 1
    ):
        return False
    index = command.index(path_argument)
    if index + 1 >= len(command):
        return False
    value = Path(command[index + 1])
    return value.is_absolute() and value.resolve(strict=False) == path


def _plan_package_caches(
    config: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    proc_root: Path,
    full_cleanup: bool = False,
) -> list[HousekeepingCandidate]:
    if not policy.get("enabled") and not full_cleanup:
        return []
    threshold = int(config.get("cache_prune_threshold_gib", 1)) * 1024**3
    try:
        runtime_uid = pwd.getpwnam(str(config["runtime_user"])).pw_uid
    except (KeyError, TypeError):
        runtime_uid = -1
    browser_values = config.get("playwright_browser_caches")
    if browser_values is None:
        browser_values = [config.get("playwright_browser_cache")]
    browser_roots = (
        {
            Path(value).resolve(strict=False)
            for value in browser_values
            if isinstance(value, str) and Path(value).is_absolute()
        }
        if isinstance(browser_values, list)
        else set()
    )
    protected_inventory_roots = {
        Path(str(entry.get("path", ""))).resolve(strict=False)
        for entry in config.get("protected_inventory_roots", [])
        if isinstance(entry, Mapping) and Path(str(entry.get("path", ""))).is_absolute()
    }
    result: list[HousekeepingCandidate] = []
    for entry in config.get("package_caches", []):
        path = Path(str(entry.get("path", "")))
        command = entry.get("command")
        path_argument = entry.get("path_argument")
        name = str(entry.get("name", ""))
        entry_threshold = entry.get("minimum_bytes", threshold)
        full_reclaim = entry.get("full_reclaim", False)
        if (
            not name
            or not path.is_absolute()
            or path.is_symlink()
            or not path.is_dir()
            or not isinstance(command, list)
            or not command
            or not all(isinstance(argument, str) and argument for argument in command)
            or isinstance(entry_threshold, bool)
            or not isinstance(entry_threshold, int)
            or entry_threshold < 1
            or not isinstance(full_reclaim, bool)
        ):
            continue
        resolved = path.resolve(strict=True)
        if resolved != path:
            continue
        size, size_certain = _tree_size_inventory(path)
        executable = shutil.which(command[0])
        running = (
            package_command_running(Path(executable).name, proc_root, runtime_uid)
            if executable is not None and runtime_uid >= 0
            else None
        )
        reason: str | None = None
        if not size_certain:
            reason = "PACKAGE_CACHE_SIZE_UNKNOWN"
        elif any(
            resolved == other or resolved in other.parents or other in resolved.parents
            for other in (*browser_roots, *protected_inventory_roots)
        ):
            reason = "PACKAGE_CACHE_CLASS_OVERLAP"
        elif not package_command_bound_to_path(
            command,
            path_argument=path_argument,
            path=resolved,
        ):
            reason = "PACKAGE_CACHE_COMMAND_UNBOUND"
        elif executable is None:
            reason = "PACKAGE_CACHE_COMMAND_UNAVAILABLE"
        elif running is None:
            reason = "PACKAGE_CACHE_PROCESS_INVENTORY_UNKNOWN"
        elif running:
            reason = "PACKAGE_CACHE_OWNER_ACTIVE"
        elif not full_cleanup and size < entry_threshold:
            reason = "PACKAGE_CACHE_BELOW_THRESHOLD"
        if reason is None:
            fingerprint, _ = candidate_fingerprint(path)
        else:
            metadata = path.lstat()
            fingerprint = resource_fingerprint(
                {
                    "path": str(resolved),
                    "device": metadata.st_dev,
                    "inode": metadata.st_ino,
                    "mode": metadata.st_mode,
                    "size": size,
                    "mtime_ns": metadata.st_mtime_ns,
                }
            )
        result.append(
            HousekeepingCandidate(
                category="package_cache",
                path=str(resolved),
                canonical_identity=f"package_cache:{name}:{resolved}",
                fingerprint=fingerprint,
                bytes=size,
                # A command explicitly declared as full-reclaim owns this
                # bounded cache root and may truthfully advertise its entire
                # footprint. Prune commands retain the conservative zero.
                estimated_reclaim_bytes=size if full_reclaim and reason is None else 0,
                action="preserve" if reason else "prune",
                retention_reason=f"over_{entry_threshold}_bytes",
                protection_reason=reason,
                resource_kind="package_cache",
                attributes=(("full_reclaim", str(full_reclaim).lower()), ("name", name)),
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
    full_cleanup: bool = False,
) -> tuple[list[HousekeepingCandidate], list[str]]:
    """Inventory non-release runtime resources without mutating them."""
    protection = protection or resolve_protected_set(
        root, config, open_inventory=lambda: (set(), False)
    )
    candidates = _plan_ephemeral_paths(
        root,
        policy["ephemeral"],
        protection,
        now=now,
        proc_root=proc_root,
        full_cleanup=full_cleanup,
    )
    browsers, browser_warnings = _plan_orphan_browsers(
        config,
        policy["ephemeral"],
        now=now,
        proc_root=proc_root,
        full_cleanup=full_cleanup,
    )
    docker, docker_warnings = _plan_docker(
        policy["ephemeral"],
        now=now,
        proc_root=proc_root,
        runner=runner,
        timeout_seconds=float(config.get("subprocess_timeout_seconds", 20)),
        full_cleanup=full_cleanup,
    )
    candidates.extend(browsers)
    candidates.extend(docker)
    terminal_runtimes, terminal_warnings = _plan_closed_terminal_runtimes(proc_root=proc_root)
    candidates.extend(terminal_runtimes)
    workflow_authorities, workflow_authority_warnings = _plan_orphan_workflow_authorities()
    candidates.extend(workflow_authorities)
    retirement_cleanups, retirement_warnings = _plan_retirement_cleanups(protection=protection)
    candidates.extend(retirement_cleanups)
    return candidates, [
        *browser_warnings,
        *docker_warnings,
        *terminal_warnings,
        *workflow_authority_warnings,
        *retirement_warnings,
    ]


def _plan_orphan_workflow_authorities() -> tuple[list[HousekeepingCandidate], list[str]]:
    """Expose missing-root workflow authority as an explicit zero-byte repair."""
    from cli_agent_orchestrator.clients.database import (
        list_orphaned_protected_workflow_authorities,
    )

    try:
        rows = list_orphaned_protected_workflow_authorities()
    except Exception:
        return [], ["workflow_authority_inventory_uncertain"]
    candidates: list[HousekeepingCandidate] = []
    warnings: list[str] = []
    for row in rows:
        root_terminal_id = row.get("root_terminal_id")
        workflows = row.get("workflows")
        if (
            not isinstance(root_terminal_id, str)
            or not root_terminal_id
            or not isinstance(workflows, list)
            or not workflows
            or any(
                not isinstance(item, Mapping) or not isinstance(item.get("id"), int)
                for item in workflows
            )
        ):
            warnings.append("workflow_authority_identity_unknown")
            continue
        snapshot = json.dumps(row, sort_keys=True, separators=(",", ":"))
        candidates.append(
            _resource_candidate(
                category="retirement_cleanup",
                resource_kind="workflow_authority",
                identity=f"workflow-authority:{root_terminal_id}",
                fingerprint_payload={"snapshot": snapshot},
                size=0,
                action="prune",
                retention_reason="missing_root_terminal",
                attributes={
                    "root_terminal_id": root_terminal_id,
                    "workflow_ids": json.dumps(
                        sorted(int(item["id"]) for item in workflows),
                        separators=(",", ":"),
                    ),
                    "direct_assignment_ids": json.dumps(
                        sorted(
                            int(item["id"])
                            for item in row.get("active_assignments", [])
                            if item.get("status") == "handoff_direct_result_claimed"
                        ),
                        separators=(",", ":"),
                    ),
                    "writer_lease_path": str(row.get("writer_lease_path") or ""),
                },
            )
        )
    return candidates, warnings


def revalidate_runtime_candidate(
    candidate: HousekeepingCandidate,
    *,
    root: Path,
    config: Mapping[str, Any],
    settings: Mapping[str, Any],
    now: float,
    open_inventory: Callable[[], tuple[set[Path], bool]],
    proc_root: Path,
    runner: Callable[..., Any],
    full_cleanup: bool = False,
) -> HousekeepingCandidate | None:
    """Rebuild only the lifecycle class needed for one execute-time check."""
    policy = settings["policy"]
    protection = resolve_protected_set(
        root,
        config,
        open_inventory=open_inventory,
        full_cleanup=full_cleanup,
    )
    if candidate.category == "browser_cache":
        current, _warnings = _plan_browser_cache(
            config,
            policy["browser_cache"],
            protection,
            now=now,
            full_cleanup=full_cleanup,
        )
    elif candidate.category == "package_cache":
        current = _plan_package_caches(
            config,
            policy["package_cache"],
            proc_root=proc_root,
            full_cleanup=full_cleanup,
        )
    elif candidate.category == "reproducible_cache":
        current, _warnings = _plan_reproducible_caches(
            config,
            protection,
            now=now,
            proc_root=proc_root,
            full_cleanup=full_cleanup,
        )
    elif candidate.category == "build_artifact" and full_cleanup:
        current, _warnings = _plan_full_cleanup_artifacts(
            config,
            protection,
            proc_root=proc_root,
        )
    else:
        current, _warnings = discover_runtime_candidates(
            root=root,
            config=config,
            policy=policy,
            now=now,
            proc_root=proc_root,
            runner=runner,
            protection=protection,
            full_cleanup=full_cleanup,
        )
    return next(
        (item for item in current if item.canonical_identity == candidate.canonical_identity),
        None,
    )


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
    config: Mapping[str, Any],
    policy: Mapping[str, Any],
    protection: ProtectedSet,
    *,
    now: float,
    full_cleanup: bool = False,
) -> list[HousekeepingCandidate]:
    if not policy.get("enabled") and not full_cleanup:
        return []
    logs = root / "state" / "cao" / "logs"
    if not logs.is_dir():
        return []
    result: list[HousekeepingCandidate] = []
    retention = int(policy["retain_minutes"])
    compress_after = int(policy["compress_after_minutes"])
    try:
        runtime_uid = pwd.getpwnam(str(config["runtime_user"])).pw_uid
    except (KeyError, TypeError):
        runtime_uid = -1
    for quarantine in sorted(logs.rglob(".threadcells-housekeeping-*")):
        try:
            entries = list(quarantine.iterdir())
            metadata = quarantine.lstat()
            exact_residue = (
                not quarantine.is_symlink()
                and quarantine.is_dir()
                and metadata.st_uid == runtime_uid
                and stat.S_IMODE(metadata.st_mode) == 0o700
                and len(entries) == 1
                and entries[0].name == "candidate"
                and not entries[0].is_symlink()
            )
            result.append(
                _candidate(
                    quarantine,
                    category="logs",
                    action="delete",
                    retention_reason="interrupted_housekeeping_quarantine",
                    protection=protection,
                    forced_protection=(
                        None if exact_residue else "HOUSEKEEPING_QUARANTINE_AMBIGUOUS"
                    ),
                    measure_preserved=not exact_residue,
                )
            )
        except OSError:
            continue
    for path in sorted(logs.rglob("*.log*")):
        try:
            if path.is_symlink() or not path.is_file():
                continue
            if full_cleanup or _older_than(path, now, retention):
                result.append(
                    _candidate(
                        path,
                        category="logs",
                        action="delete",
                        retention_reason=(
                            "full_cleanup_closed_output"
                            if full_cleanup
                            else f"older_than_{retention}_minutes"
                        ),
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
    full_cleanup: bool = False,
) -> list[HousekeepingCandidate]:
    if not policy.get("enabled") and not full_cleanup:
        return []
    attachments = root / "state" / "cao" / "runtime" / "terminal-attachments"
    if not attachments.is_dir():
        return []
    retention = int(policy["retain_minutes"])
    result: list[HousekeepingCandidate] = []
    for path in sorted(attachments.glob("*/*")):
        try:
            if (
                path.is_symlink()
                or not path.is_file()
                or (not full_cleanup and not _older_than(path, now, retention))
            ):
                continue
            result.append(
                _candidate(
                    path,
                    category="attachments",
                    action="delete",
                    retention_reason=(
                        "full_cleanup_closed_attachment"
                        if full_cleanup
                        else f"older_than_{retention}_minutes"
                    ),
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


def _release_deletion_authority(config: Mapping[str, Any], *, full_cleanup: bool) -> str | None:
    """Return why this planner cannot authorize ordinary release deletion."""
    if full_cleanup or os.geteuid() == 0:
        return None
    try:
        release_group = grp.getgrnam(str(config["release_admin_group"]))
    except (KeyError, TypeError):
        return "RELEASE_CONTROL_CONFIG_INVALID"
    if release_group.gr_gid not in {os.getegid(), *os.getgroups()}:
        return "RELEASE_ADMIN_GROUP_REQUIRED"
    return None


def _plan_releases(
    policy: Mapping[str, Any],
    protection: ProtectedSet,
    config: Mapping[str, Any],
    *,
    now: float,
    full_cleanup: bool = False,
) -> list[HousekeepingCandidate]:
    if not policy.get("enabled") and not full_cleanup:
        return []
    retention = int(policy["retain_minutes"])
    retain_count = int(policy["retain_count"])
    release_authority_reason = _release_deletion_authority(config, full_cleanup=full_cleanup)
    directories: set[Path] = set()
    unknown_entries: set[Path] = set()
    for release_root in protection.release_roots:
        if release_root.is_dir():
            for path in release_root.iterdir():
                if path.is_dir() and not path.is_symlink():
                    directories.add(path.resolve())
                else:
                    unknown_entries.add(path.absolute())
    ordered_directories = sorted(
        directories, key=lambda path: (path.lstat().st_mtime_ns, path.name), reverse=True
    )
    measured = _parallel_tree_sizes(ordered_directories)
    result: list[HousekeepingCandidate] = []
    for path in sorted(unknown_entries, key=str):
        metadata = path.lstat()
        result.append(
            HousekeepingCandidate(
                category="releases",
                path=str(path),
                canonical_identity=f"releases:{path}",
                fingerprint=resource_fingerprint(
                    {
                        "path": str(path),
                        "device": metadata.st_dev,
                        "inode": metadata.st_ino,
                        "mode": metadata.st_mode,
                        "size": metadata.st_size,
                        "mtime_ns": metadata.st_mtime_ns,
                    }
                ),
                bytes=metadata.st_size,
                estimated_reclaim_bytes=0,
                action="preserve",
                retention_reason="release_entry_identity_unknown",
                protection_reason="RELEASE_ENTRY_IDENTITY_UNKNOWN",
                resource_kind="inventory",
                attributes=(("release_role", "UNKNOWN_RELEASE_ENTRY"),),
            )
        )
    for index, path in enumerate(ordered_directories):
        marker_valid = _release_marker(path)
        if not marker_valid:
            item = _candidate(
                path,
                category="releases",
                action="preserve",
                retention_reason="release_marker_unknown",
                protection=protection,
                measure_preserved=True,
                preserved_size=measured[path][0],
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
        if full_cleanup and protection.reason(path, "releases") is None:
            action = "delete"
            reason = "full_cleanup_non_active_release"
        elif index >= retain_count and _older_than(path, now, retention):
            action = "delete"
            reason = f"unreferenced_older_than_{retention}_minutes"
        item = _candidate(
            path,
            category="releases",
            action=action,
            retention_reason=reason,
            protection=protection,
            forced_protection=(release_authority_reason if action == "delete" else None),
            measure_preserved=True,
            preserved_size=measured[path][0],
        )
        release_role = next(
            (
                role
                for referenced, role in protection.release_reference_reasons
                if referenced == path
            ),
            "UNREFERENCED_RELEASE",
        )
        result.append(
            HousekeepingCandidate(
                **{
                    **item.as_dict(),
                    "attributes": (("release_role", release_role),),
                }
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
    full_cleanup = mode == "full"
    protection = resolve_protected_set(
        root,
        config,
        open_inventory=open_inventory,
        full_cleanup=full_cleanup,
    )
    candidates = [
        *_plan_logs(
            root,
            config,
            policy["logs"],
            protection,
            now=now,
            full_cleanup=full_cleanup,
        ),
        *_plan_attachments(
            root,
            policy["attachments"],
            protection,
            now=now,
            full_cleanup=full_cleanup,
        ),
    ]
    runtime_candidates, runtime_warnings = discover_runtime_candidates(
        root=root,
        config=config,
        policy=policy,
        now=now,
        proc_root=proc_root,
        runner=runner,
        protection=protection,
        full_cleanup=full_cleanup,
    )
    candidates.extend(runtime_candidates)
    try:
        from cli_agent_orchestrator.services.workspace_retirement_service import (
            plan_session_workspaces,
        )

        candidates.extend(
            plan_session_workspaces(
                allow_dirty=bool(config.get("_retire_dirty_session_workspaces", False))
            )
        )
    except Exception:
        # Missing or uncertain durable authority can never turn into deletion.
        runtime_warnings.append("session_workspace_inventory_failed")
    if mode in {"weekly", "pressure", "full"}:
        from .worktrees import plan_worktrees

        worktrees, worktree_warnings = plan_worktrees(
            config=config,
            protection=protection,
            runner=runner,
        )
        candidates.extend(worktrees)
        runtime_warnings.extend(worktree_warnings)
        candidates.extend(
            _plan_releases(
                policy["releases"],
                protection,
                config,
                now=now,
                full_cleanup=full_cleanup,
            )
        )
        browser_candidates, browser_warnings = _plan_browser_cache(
            config,
            policy["browser_cache"],
            protection,
            now=now,
            full_cleanup=full_cleanup,
        )
        candidates.extend(browser_candidates)
        runtime_warnings.extend(browser_warnings)
        candidates.extend(
            _plan_package_caches(
                config,
                policy["package_cache"],
                proc_root=proc_root,
                full_cleanup=full_cleanup,
            )
        )
        reproducible, reproducible_warnings = _plan_reproducible_caches(
            config,
            protection,
            now=now,
            proc_root=proc_root,
            full_cleanup=full_cleanup,
        )
        candidates.extend(reproducible)
        runtime_warnings.extend(reproducible_warnings)
        if full_cleanup:
            specialized_paths = {
                Path(item.path)
                for item in candidates
                if item.resource_kind
                in {"git_worktree", "session_workspace", "package_cache", "reproducible_cache"}
                or item.category == "browser_cache"
            }
            artifacts, artifact_warnings = _plan_full_cleanup_artifacts(
                config,
                protection,
                proc_root=proc_root,
                claimed_paths=specialized_paths,
            )
            candidates.extend(artifacts)
            runtime_warnings.extend(artifact_warnings)
    backups = root / "backups"
    if backups.exists():
        backup_size, backup_inventory_certain = _tree_size_inventory(backups)
        candidates.append(
            _resource_candidate(
                category="backups",
                resource_kind="inventory",
                identity=str(backups.resolve()),
                fingerprint_payload={
                    "path": str(backups.resolve()),
                    "size": backup_size,
                    "mtime_ns": backups.lstat().st_mtime_ns,
                },
                size=backup_size,
                action="preserve",
                retention_reason="inventory_only",
                protection_reason="BACKUP_PROTECTED",
                attributes={"purpose": "recovery points and source snapshots"},
            )
        )
        if not backup_inventory_certain:
            runtime_warnings.append("backup_inventory_incomplete")
    protected_inventory, protected_warnings = _plan_protected_inventory(config)
    candidates.extend(protected_inventory)
    runtime_warnings.extend(protected_warnings)
    if mode == "pressure":
        candidates.sort(
            key=lambda item: (
                item.action == "preserve",
                -item.estimated_reclaim_bytes if item.action != "preserve" else -item.bytes,
                item.category,
                item.canonical_identity,
            )
        )
    else:
        candidates.sort(key=lambda item: (item.category, item.canonical_identity))
    return finalize_plan(
        generated_at=now,
        mode=mode,
        root=root,
        candidates=candidates,
        warnings=[*protection.warnings, *runtime_warnings],
    )
