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
    open_path_ancestors: frozenset[Path]
    open_paths_certain: bool
    active_terminal_ids: frozenset[str]
    terminal_inventory_certain: bool
    release_roots: tuple[Path, ...]
    protected_releases: frozenset[Path]
    protected_release_reasons: tuple[tuple[Path, str], ...]
    release_reference_reasons: tuple[tuple[Path, str], ...]
    release_metadata_certain: bool
    warnings: tuple[str, ...]

    def reason(self, path: Path, category: str) -> str | None:
        resolved = path.resolve()
        backups = (self.root / "backups").resolve()
        if resolved == backups or _within(resolved, backups):
            return "BACKUP_PROTECTED"
        if category == "releases":
            if not self.release_metadata_certain:
                return "RELEASE_METADATA_UNKNOWN"
            if any(
                release == resolved or _within(resolved, release)
                for release in self.protected_releases
            ):
                return next(
                    (
                        reason
                        for release, reason in self.protected_release_reasons
                        if release == resolved or _within(resolved, release)
                    ),
                    "RELEASE_REFERENCE_PROTECTED",
                )
        if not self.open_paths_certain:
            return "PROCESS_INVENTORY_UNKNOWN"
        if resolved in self.open_path_ancestors:
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
        if category == "logs":
            terminal_log_root = (self.root / "state" / "cao" / "logs" / "terminal").resolve()
            try:
                terminal_id = resolved.relative_to(terminal_log_root).parts[0].split(".", 1)[0]
            except (ValueError, IndexError):
                terminal_id = ""
            if terminal_id:
                if not self.terminal_inventory_certain:
                    return "ACTIVE_TERMINAL_INVENTORY_UNKNOWN"
                if terminal_id in self.active_terminal_ids:
                    return "ACTIVE_TERMINAL_OUTPUT"
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
    root: Path, config: Mapping[str, Any], *, full_cleanup: bool = False
) -> tuple[tuple[Path, ...], dict[Path, str], dict[Path, str], bool, list[str]]:
    configured_release_roots = tuple(
        Path(str(value)) for value in config.get("release_roots", [str(root / "tools")])
    )
    release_roots = tuple(path.resolve() for path in configured_release_roots)
    metadata_path = Path(
        str(config.get("release_metadata", root / "state" / "cao" / "release-metadata.json"))
    )
    active_link_path = Path(str(config.get("active_release_link", metadata_path.parent / "active")))
    warnings: list[str] = []
    try:
        if metadata_path.is_symlink():
            raise ValueError("missing release metadata")
        release_group = grp.getgrnam(str(config["release_admin_group"]))
        release_control_uid = int(config["release_control_uid"])
        if (
            not metadata_path.is_absolute()
            or metadata_path.parent.is_symlink()
            or metadata_path.parent.stat().st_uid != release_control_uid
            or metadata_path.parent.stat().st_mode & 0o022
            or not active_link_path.is_absolute()
            or active_link_path.parent != metadata_path.parent
        ):
            raise ValueError("release control parent is invalid")
        for configured_root, release_root in zip(configured_release_roots, release_roots):
            if (
                not configured_root.is_absolute()
                or configured_root != release_root
                or configured_root.is_symlink()
                or not release_root.is_dir()
            ):
                raise ValueError("release root identity is invalid")
            release_stat = release_root.stat()
            parent_stat = release_root.parent.stat()
            if (
                release_stat.st_uid != release_control_uid
                or release_stat.st_gid != release_group.gr_gid
                or release_stat.st_mode & 0o002
                or release_root.parent.is_symlink()
                or parent_stat.st_uid != release_control_uid
                or parent_stat.st_mode & 0o022
            ):
                raise ValueError("release root ownership is invalid")
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
        if not isinstance(raw.get("rollback_releases"), list) or not isinstance(
            raw.get("candidate_releases"), list
        ):
            raise ValueError("invalid rollback release inventory")
        values = [
            (raw.get("active_release"), "ACTIVE_RELEASE"),
            *(
                (value, "CANONICAL_ROLLBACK_RELEASE" if index == 0 else "RECOVERY_RELEASE")
                for index, value in enumerate(raw.get("rollback_releases") or [])
            ),
            *((value, "CANDIDATE_RELEASE") for value in raw.get("candidate_releases") or []),
        ]
        references: dict[Path, str] = {}
        for value, reason in values:
            if value is None:
                continue
            if not isinstance(value, str) or not Path(value).is_absolute():
                raise ValueError("release reference must be absolute")
            candidate = Path(value).resolve()
            if candidate.parent not in release_roots:
                raise ValueError("release reference is outside configured roots")
            references.setdefault(candidate, reason)
        protected = {
            path: reason
            for path, reason in references.items()
            if not full_cleanup or reason == "ACTIVE_RELEASE"
        }
        configured_active = raw.get("active_release")
        if active_link_path.exists() or active_link_path.is_symlink():
            if not active_link_path.is_symlink():
                raise ValueError("active release link is invalid")
            active_stat = active_link_path.lstat()
            if (
                active_stat.st_uid != release_control_uid
                or active_stat.st_gid != release_group.gr_gid
            ):
                raise ValueError("active release link ownership is invalid")
            active_target = active_link_path.resolve(strict=True)
            if active_target.parent not in release_roots or not active_target.is_dir():
                raise ValueError("active release target is invalid")
            target_stat = active_target.stat()
            if (
                target_stat.st_uid != release_control_uid
                or target_stat.st_gid != release_group.gr_gid
                or target_stat.st_mode & 0o002
            ):
                raise ValueError("active release target ownership is invalid")
            protected[active_target] = "ACTIVE_RELEASE"
            if configured_active != str(active_target):
                warnings.append("active_release_metadata_diverged")
        elif configured_active is not None:
            raise ValueError("active release link is missing")
        return release_roots, protected, references, True, warnings
    except (KeyError, OSError, ValueError, TypeError, json.JSONDecodeError):
        warnings.append("release_metadata_inventory_uncertain")
        return release_roots, {}, {}, False, warnings


def resolve_protected_set(
    root: Path,
    config: Mapping[str, Any],
    *,
    open_inventory: Callable[[], tuple[set[Path], bool]],
    full_cleanup: bool = False,
) -> ProtectedSet:
    open_paths, open_certain = open_inventory()
    terminals, terminals_certain = _active_terminal_inventory()
    (
        release_roots,
        protected_releases,
        release_references,
        releases_certain,
        warnings,
    ) = _release_metadata(root, config, full_cleanup=full_cleanup)
    if not open_certain:
        warnings.append("process_inventory_uncertain")
    if not terminals_certain:
        warnings.append("active_terminal_inventory_uncertain")
    resolved_open_paths = frozenset(path.resolve() for path in open_paths)
    open_path_ancestors = frozenset(
        ancestor
        for open_path in resolved_open_paths
        for ancestor in (open_path, *open_path.parents)
    )
    return ProtectedSet(
        root=root.resolve(),
        open_paths=resolved_open_paths,
        open_path_ancestors=open_path_ancestors,
        open_paths_certain=open_certain,
        active_terminal_ids=frozenset(terminals),
        terminal_inventory_certain=terminals_certain,
        release_roots=release_roots,
        protected_releases=frozenset(protected_releases),
        protected_release_reasons=tuple(
            sorted(protected_releases.items(), key=lambda item: str(item[0]))
        ),
        release_reference_reasons=tuple(
            sorted(release_references.items(), key=lambda item: str(item[0]))
        ),
        release_metadata_certain=releases_certain,
        warnings=tuple(warnings),
    )
