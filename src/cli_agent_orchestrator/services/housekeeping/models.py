"""Immutable public models and canonical policy for Housekeeping.P2."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

HousekeepingMode = Literal["frequent", "weekly", "pressure"]
CandidateAction = Literal["preserve", "compress", "delete", "terminate", "prune"]
CandidateKind = Literal[
    "path",
    "browser_process_group",
    "docker_container",
    "docker_volume",
    "package_cache",
    "terminal_runtime",
    "retirement_cleanup",
]

DEFAULT_POLICY: dict[str, dict[str, Any]] = {
    "logs": {"enabled": True, "compress_after_minutes": 1440, "retain_minutes": 10080},
    "attachments": {"enabled": True, "retain_minutes": 10080},
    "ephemeral": {"enabled": True},
    "browser_cache": {"enabled": True, "retain_minutes": 10080},
    "package_cache": {"enabled": True},
    "releases": {"enabled": True, "retain_count": 2, "retain_minutes": 10080},
    "backups": {"enabled": False},
}

DEFAULT_SCHEDULE: dict[str, str] = {
    "frequent": "6h",
    "weekly": "Sun 04:00 UTC",
    "pressure": "on_red",
}


@dataclass(frozen=True)
class HousekeepingCandidate:
    category: str
    path: str
    canonical_identity: str
    fingerprint: str
    bytes: int
    estimated_reclaim_bytes: int
    action: CandidateAction
    retention_reason: str
    protection_reason: str | None = None
    resource_kind: CandidateKind = "path"
    attributes: tuple[tuple[str, str], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HousekeepingPlan:
    schema_version: int
    plan_id: str
    generated_at: float
    mode: HousekeepingMode
    root: str
    candidates: tuple[HousekeepingCandidate, ...]
    warnings: tuple[str, ...] = ()

    @property
    def reclaimable_bytes(self) -> int:
        return sum(item.estimated_reclaim_bytes for item in self.candidates)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "generated_at": self.generated_at,
            "mode": self.mode,
            "root": self.root,
            "reclaimable_bytes": self.reclaimable_bytes,
            "warnings": list(self.warnings),
            "candidates": [candidate.as_dict() for candidate in self.candidates],
        }


def candidate_fingerprint(path: Path) -> tuple[str, int]:
    metadata = path.lstat()
    entries: list[dict[str, Any]] = [
        {
            "relative": ".",
            "device": metadata.st_dev,
            "inode": metadata.st_ino,
            "mode": metadata.st_mode,
            "size": metadata.st_size,
            "mtime_ns": metadata.st_mtime_ns,
        }
    ]
    total = metadata.st_size if path.is_file() else 0
    if path.is_dir() and not path.is_symlink():
        for child in sorted(path.rglob("*"), key=lambda item: str(item.relative_to(path))):
            child_metadata = child.lstat()
            entries.append(
                {
                    "relative": str(child.relative_to(path)),
                    "device": child_metadata.st_dev,
                    "inode": child_metadata.st_ino,
                    "mode": child_metadata.st_mode,
                    "size": child_metadata.st_size,
                    "mtime_ns": child_metadata.st_mtime_ns,
                }
            )
            if child.is_file() and not child.is_symlink():
                total += child_metadata.st_size
    serialized = json.dumps(entries, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest(), total


def resource_fingerprint(payload: Mapping[str, Any]) -> str:
    """Return a stable identity digest for a non-filesystem cleanup resource."""
    serialized = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def finalize_plan(
    *,
    generated_at: float,
    mode: HousekeepingMode,
    root: Path,
    candidates: list[HousekeepingCandidate],
    warnings: list[str],
) -> HousekeepingPlan:
    identity_payload = {
        "schema_version": 1,
        "mode": mode,
        "root": str(root.resolve()),
        "candidates": [candidate.as_dict() for candidate in candidates],
        "warnings": warnings,
    }
    plan_id = hashlib.sha256(
        json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return HousekeepingPlan(
        schema_version=1,
        plan_id=plan_id,
        generated_at=generated_at,
        mode=mode,
        root=str(root.resolve()),
        candidates=tuple(candidates),
        warnings=tuple(warnings),
    )


def default_settings(config: Mapping[str, Any]) -> dict[str, Any]:
    policy = json.loads(json.dumps(DEFAULT_POLICY))
    policy["logs"]["compress_after_minutes"] = int(config.get("log_compress_after_minutes", 1440))
    retention = int(config.get("retention_minutes", 10080))
    for category in ("logs", "attachments", "browser_cache", "releases"):
        policy[category]["retain_minutes"] = retention
    return {"schema_version": 1, "policy": policy, "schedule": dict(DEFAULT_SCHEDULE)}


def validate_settings(raw: Mapping[str, Any]) -> dict[str, Any]:
    if raw.get("schema_version", 1) != 1:
        raise ValueError("housekeeping schema_version must be 1")
    policy = raw.get("policy")
    schedule = raw.get("schedule")
    if not isinstance(policy, Mapping) or set(policy) != set(DEFAULT_POLICY):
        raise ValueError("housekeeping policy must contain exactly the canonical classes")
    if not isinstance(schedule, Mapping) or set(schedule) != set(DEFAULT_SCHEDULE):
        raise ValueError("housekeeping schedule must contain frequent, weekly, and pressure")
    validated_policy: dict[str, dict[str, Any]] = {}
    for category, defaults in DEFAULT_POLICY.items():
        supplied = policy.get(category)
        if not isinstance(supplied, Mapping) or set(supplied) != set(defaults):
            raise ValueError(f"invalid housekeeping policy class: {category}")
        values = dict(supplied)
        if not isinstance(values.get("enabled"), bool):
            raise ValueError(f"{category}.enabled must be boolean")
        if category == "backups" and values["enabled"]:
            raise ValueError("backups.enabled must remain false; backups are inventory-only")
        for key, value in values.items():
            if key == "enabled":
                continue
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 525600:
                raise ValueError(f"{category}.{key} must be an integer from 1 to 525600")
        validated_policy[category] = values
    validated_schedule: dict[str, str] = {}
    frequent = schedule.get("frequent")
    weekly = schedule.get("weekly")
    pressure = schedule.get("pressure")
    if not isinstance(frequent, str) or not re.fullmatch(r"[1-9][0-9]{0,3}[mhd]", frequent.strip()):
        raise ValueError("invalid housekeeping schedule: frequent")
    frequent_seconds = (
        int(frequent.strip()[:-1])
        * {
            "m": 60,
            "h": 3600,
            "d": 86400,
        }[frequent.strip()[-1]]
    )
    if not 900 <= frequent_seconds <= 365 * 86400:
        raise ValueError("invalid housekeeping schedule: frequent")
    if not isinstance(weekly, str) or not re.fullmatch(
        r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) (?:[01][0-9]|2[0-3]):[0-5][0-9] UTC",
        weekly.strip(),
    ):
        raise ValueError("invalid housekeeping schedule: weekly")
    if pressure != "on_red":
        raise ValueError("invalid housekeeping schedule: pressure")
    validated_schedule.update(
        frequent=frequent.strip(),
        weekly=weekly.strip(),
        pressure="on_red",
    )
    return {
        "schema_version": 1,
        "policy": validated_policy,
        "schedule": validated_schedule,
    }
