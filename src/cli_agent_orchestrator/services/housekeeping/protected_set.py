"""Fail-closed protected-set resolution for destructive housekeeping classes."""

from __future__ import annotations

import grp
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class ProtectedSet:
    root: Path
    open_paths: frozenset[Path]
    open_paths_certain: bool
    active_terminal_ids: frozenset[str]
    terminal_inventory_certain: bool
    release_roots: tuple[Path, ...]
    protected_releases: frozenset[Path]
    release_metadata_certain: bool
    warnings: tuple[str, ...]

    def reason(self, path: Path, category: str) -> str | None:
        resolved = path.resolve()
        backups = (self.root / "backups").resolve()
        if resolved == backups or _within(resolved, backups):
            return "BACKUP_PROTECTED"
        if not self.open_paths_certain:
            return "PROCESS_INVENTORY_UNKNOWN"
        if any(
            open_path == resolved or _within(open_path, resolved) for open_path in self.open_paths
        ):
            return "OPEN_BY_ACTIVE_PROCESS"
        if category == "attachments":
            attachment_root = (
                self.root / "state" / "cao" / "runtime" / "terminal-attachments"
            ).resolve()
            try:
                terminal_id = resolved.relative_to(attachment_root).parts[0]
            except (ValueError, IndexError):
                return "ATTACHMENT_IDENTITY_UNKNOWN"
            if not self.terminal_inventory_certain:
                return "ACTIVE_TERMINAL_INVENTORY_UNKNOWN"
            if terminal_id in self.active_terminal_ids:
                return "ACTIVE_TERMINAL_ATTACHMENT"
        if category == "releases":
            if not self.release_metadata_certain:
                return "RELEASE_METADATA_UNKNOWN"
            if any(
                release == resolved or _within(resolved, release)
                for release in self.protected_releases
            ):
                return "ACTIVE_OR_ROLLBACK_RELEASE"
        return None


def _active_terminal_inventory() -> tuple[set[str], bool]:
    try:
        from cli_agent_orchestrator.clients.database import list_all_terminals

        rows = list_all_terminals()
        return {
            str(row["id"]) for row in rows if row.get("runtime_lifecycle") not in {"exited"}
        }, True
    except Exception:
        return set(), False


def _release_metadata(
    root: Path, config: Mapping[str, Any]
) -> tuple[tuple[Path, ...], set[Path], bool, list[str]]:
    release_roots = tuple(
        Path(str(value)).resolve() for value in config.get("release_roots", [str(root / "tools")])
    )
    metadata_path = Path(
        str(config.get("release_metadata", root / "state" / "cao" / "release-metadata.json"))
    )
    warnings: list[str] = []
    try:
        if metadata_path.is_symlink():
            raise ValueError("missing release metadata")
        release_group = grp.getgrnam(str(config["release_admin_group"]))
        release_control_uid = int(config["release_control_uid"])
        descriptor = os.open(metadata_path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            metadata_stat = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata_stat.st_mode)
                or metadata_stat.st_uid != release_control_uid
                or metadata_stat.st_gid != release_group.gr_gid
                or metadata_stat.st_mode & 0o022
            ):
                raise ValueError("release metadata ownership is invalid")
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                descriptor = -1
                raw = json.load(handle)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if raw.get("schema_version") != 1:
            raise ValueError("unknown release metadata version")
        values = [
            raw.get("active_release"),
            *(raw.get("rollback_releases") or []),
            *(raw.get("candidate_releases") or []),
        ]
        if not isinstance(raw.get("rollback_releases"), list) or not isinstance(
            raw.get("candidate_releases"), list
        ):
            raise ValueError("invalid rollback release inventory")
        protected: set[Path] = set()
        for value in values:
            if value is None:
                continue
            if not isinstance(value, str) or not Path(value).is_absolute():
                raise ValueError("release reference must be absolute")
            candidate = Path(value).resolve()
            if not any(
                candidate == release_root or _within(candidate, release_root)
                for release_root in release_roots
            ):
                raise ValueError("release reference is outside configured roots")
            protected.add(candidate)
        return release_roots, protected, True, warnings
    except (KeyError, OSError, ValueError, TypeError, json.JSONDecodeError):
        warnings.append("release_metadata_inventory_uncertain")
        return release_roots, set(), False, warnings


def resolve_protected_set(
    root: Path,
    config: Mapping[str, Any],
    *,
    open_inventory: Callable[[], tuple[set[Path], bool]],
) -> ProtectedSet:
    open_paths, open_certain = open_inventory()
    terminals, terminals_certain = _active_terminal_inventory()
    release_roots, protected_releases, releases_certain, warnings = _release_metadata(root, config)
    if not open_certain:
        warnings.append("process_inventory_uncertain")
    if not terminals_certain:
        warnings.append("active_terminal_inventory_uncertain")
    return ProtectedSet(
        root=root.resolve(),
        open_paths=frozenset(path.resolve() for path in open_paths),
        open_paths_certain=open_certain,
        active_terminal_ids=frozenset(terminals),
        terminal_inventory_certain=terminals_certain,
        release_roots=release_roots,
        protected_releases=frozenset(protected_releases),
        release_metadata_certain=releases_certain,
        warnings=tuple(warnings),
    )
