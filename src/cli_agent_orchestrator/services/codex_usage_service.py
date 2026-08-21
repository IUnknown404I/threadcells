"""Truthful, restart-safe Codex usage ingestion from durable rollout events.

Codex's terminal UI is a presentation surface, not its telemetry contract. The
CLI persists cumulative token counts in its rollout JSONL. This module binds an
exact rollout session to its ThreadCells terminal and advances a durable byte
cursor while updating one cumulative snapshot per provider session.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from cli_agent_orchestrator.clients.database import (
    bind_provider_usage_session,
    list_all_terminals,
    list_provider_usage_bindings,
    record_provider_usage_checkpoint,
)
from cli_agent_orchestrator.clients.tmux import tmux_client
from cli_agent_orchestrator.constants import TERMINAL_LOG_DIR
from cli_agent_orchestrator.models.usage import UsageObservation

logger = logging.getLogger(__name__)

CODEX_PROVIDER = "codex"
CODEX_EXTRACTOR = "codex_rollout_session_v1"
HISTORICAL_MATCH_WINDOW_SECONDS = 15.0
MAX_SESSION_META_BYTES = 4 * 1024 * 1024
MAX_JSONL_LINE_BYTES = 4 * 1024 * 1024
DEFAULT_TERMINAL_READ_BUDGET = 8 * 1024 * 1024
DEFAULT_REFRESH_READ_BUDGET = 64 * 1024 * 1024
_SESSION_ID_PATTERN = re.compile(r"^[0-9a-fA-F][0-9a-fA-F-]{19,63}$")

_repair_lock = threading.Lock()
_historical_repair_attempted = False


@dataclass(frozen=True)
class CodexSessionMeta:
    session_id: str
    timestamp: float
    cwd: Path
    source: Any
    originator: str
    parent_session_id: Optional[str]


@dataclass(frozen=True)
class CodexUsageChunk:
    next_byte_offset: int
    observation: Optional[UsageObservation]


def _codex_sessions_root() -> Path:
    """Return the state root used by ThreadCells-launched Codex processes."""
    return (Path.home() / ".codex" / "sessions").resolve(strict=False)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _parse_timestamp(value: Any) -> Optional[float]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _parent_session_id(source: Any) -> Optional[str]:
    if not isinstance(source, dict):
        return None
    subagent = source.get("subagent")
    if not isinstance(subagent, dict):
        return None
    for value in subagent.values():
        if isinstance(value, dict):
            parent = value.get("parent_thread_id")
            if isinstance(parent, str) and _SESSION_ID_PATTERN.fullmatch(parent):
                return parent
    return None


def _read_session_meta(path: Path) -> Optional[CodexSessionMeta]:
    """Read only the bounded first JSONL record and validate its identity."""
    try:
        with path.open("rb") as handle:
            raw = handle.readline(MAX_SESSION_META_BYTES + 1)
        if not raw.endswith(b"\n") or len(raw) > MAX_SESSION_META_BYTES:
            return None
        row = json.loads(raw)
        if not isinstance(row, dict) or row.get("type") != "session_meta":
            return None
        payload = row.get("payload")
        if not isinstance(payload, dict):
            return None
        session_id = payload.get("id") or payload.get("session_id")
        timestamp = _parse_timestamp(payload.get("timestamp") or row.get("timestamp"))
        cwd_value = payload.get("cwd")
        if (
            not isinstance(session_id, str)
            or not _SESSION_ID_PATTERN.fullmatch(session_id)
            or timestamp is None
            or not isinstance(cwd_value, str)
            or not os.path.isabs(cwd_value)
            or not path.name.endswith(f"-{session_id}.jsonl")
        ):
            return None
        return CodexSessionMeta(
            session_id=session_id,
            timestamp=timestamp,
            cwd=Path(cwd_value).resolve(strict=False),
            source=payload.get("source"),
            originator=str(payload.get("originator") or ""),
            parent_session_id=_parent_session_id(payload.get("source")),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _rollout_index() -> dict[str, tuple[Path, CodexSessionMeta]]:
    root = _codex_sessions_root()
    index: dict[str, tuple[Path, CodexSessionMeta]] = {}
    duplicates: set[str] = set()
    if not root.is_dir():
        return index
    for path in root.glob("*/*/*/rollout-*.jsonl"):
        resolved = path.resolve(strict=False)
        if not _is_within(resolved, root):
            continue
        meta = _read_session_meta(resolved)
        if meta is None:
            continue
        if meta.session_id in index:
            duplicates.add(meta.session_id)
        else:
            index[meta.session_id] = (resolved, meta)
    for session_id in duplicates:
        index.pop(session_id, None)
    return index


def _file_birth_timestamp(path: Path) -> Optional[float]:
    """Read an immutable file creation time without treating mtime as launch time."""
    try:
        stat_result = path.stat()
    except OSError:
        return None
    birth = getattr(stat_result, "st_birthtime", None)
    if isinstance(birth, (int, float)) and birth > 0:
        return float(birth)
    # GNU stat exposes statx birth time as %W even where Python's stat_result
    # does not. argv execution and the trusted exact log path avoid a shell.
    try:
        result = subprocess.run(
            ["stat", "-c", "%W", "--", str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        birth = float(result.stdout.strip())
        return birth if birth > 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _terminal_log_path(terminal_id: str) -> Optional[Path]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", terminal_id):
        return None
    root = TERMINAL_LOG_DIR.resolve(strict=False)
    candidate = (root / f"{terminal_id}.log").resolve(strict=False)
    return candidate if _is_within(candidate, root) else None


def _cwd_matches_worktree(cwd: Path, worktree_value: Any) -> bool:
    if not isinstance(worktree_value, str) or not os.path.isabs(worktree_value):
        return False
    worktree = Path(worktree_value).resolve(strict=False)
    return cwd == worktree or _is_within(cwd, worktree)


def repair_codex_usage_bindings() -> dict[str, int]:
    """Bind retained Codex sessions only where launch evidence is unique.

    Linux/macOS file birth time is immutable launch evidence for ThreadCells's
    per-terminal capture log. It is combined with canonical worktree, Codex's
    own session timestamp/source/originator, one-candidate cardinality, and a
    reverse-uniqueness check. Ambiguous history remains unknown.
    """
    index = _rollout_index()
    root_sessions = {
        session_id: (path, meta)
        for session_id, (path, meta) in index.items()
        if meta.source == "cli" and meta.originator == "codex-tui"
    }
    terminals = [row for row in list_all_terminals() if row.get("provider") == CODEX_PROVIDER]
    previously_bound = {
        row["provider_session_id"] for row in list_provider_usage_bindings(provider=CODEX_PROVIDER)
    }
    candidates: dict[str, list[str]] = {}
    reverse: dict[str, list[str]] = {}
    for terminal in terminals:
        terminal_id = str(terminal.get("id") or "")
        log_path = _terminal_log_path(terminal_id)
        born_at = _file_birth_timestamp(log_path) if log_path is not None else None
        if born_at is None:
            continue
        matches = [
            session_id
            for session_id, (_path, meta) in root_sessions.items()
            if abs(meta.timestamp - born_at) <= HISTORICAL_MATCH_WINDOW_SECONDS
            and _cwd_matches_worktree(meta.cwd, terminal.get("launch_worktree"))
        ]
        candidates[terminal_id] = matches
        for session_id in matches:
            reverse.setdefault(session_id, []).append(terminal_id)

    bound_roots = 0
    ambiguous = 0
    for terminal_id, matches in candidates.items():
        if len(matches) != 1 or len(reverse.get(matches[0], [])) != 1:
            if matches:
                ambiguous += 1
            continue
        if bind_provider_usage_session(
            provider=CODEX_PROVIDER,
            provider_session_id=matches[0],
            terminal_id=terminal_id,
            source="capture_birth_cwd_v1",
        ):
            if matches[0] not in previously_bound:
                bound_roots += 1
                previously_bound.add(matches[0])

    bound_children = _bind_codex_child_sessions(index)
    return {
        "root_bindings": bound_roots,
        "child_bindings": bound_children,
        "ambiguous_terminals": ambiguous,
        "indexed_sessions": len(index),
    }


def _bind_codex_child_sessions(index: dict[str, tuple[Path, CodexSessionMeta]]) -> int:
    """Incrementally bind provider-native descendants to their bound root owner."""
    existing_bindings = list_provider_usage_bindings(provider=CODEX_PROVIDER)
    previously_bound = {row["provider_session_id"] for row in existing_bindings}
    session_owner = {row["provider_session_id"]: row["terminal_id"] for row in existing_bindings}
    bound_children = 0
    changed = True
    while changed:
        changed = False
        for session_id, (_path, meta) in index.items():
            if session_id in session_owner or meta.parent_session_id not in session_owner:
                continue
            owner = session_owner[meta.parent_session_id]
            if bind_provider_usage_session(
                provider=CODEX_PROVIDER,
                provider_session_id=session_id,
                terminal_id=owner,
                source="codex_parent_session_v1",
            ):
                session_owner[session_id] = owner
                if session_id not in previously_bound:
                    bound_children += 1
                    previously_bound.add(session_id)
                changed = True
    return bound_children


def ensure_historical_codex_bindings() -> dict[str, int]:
    global _historical_repair_attempted
    with _repair_lock:
        if _historical_repair_attempted:
            return {"already_attempted": 1}
        result = repair_codex_usage_bindings()
        _historical_repair_attempted = True
        logger.info(
            "Codex usage binding repair: roots=%d children=%d ambiguous=%d",
            result["root_bindings"],
            result["child_bindings"],
            result["ambiguous_terminals"],
        )
        return result


def _descendant_process_ids(root_process_id: int) -> set[int]:
    if root_process_id <= 1 or not Path("/proc").is_dir():
        return set()
    discovered = {root_process_id}
    pending = [root_process_id]
    while pending and len(discovered) <= 1024:
        process_id = pending.pop()
        children_path = Path(f"/proc/{process_id}/task/{process_id}/children")
        try:
            children = children_path.read_text(encoding="ascii").split()
        except OSError:
            continue
        for value in children:
            if value.isdigit() and int(value) > 1 and int(value) not in discovered:
                discovered.add(int(value))
                pending.append(int(value))
    return discovered


def _live_root_rollout(metadata: dict[str, Any]) -> Optional[tuple[Path, CodexSessionMeta]]:
    process_id = tmux_client.get_pane_process_id(
        str(metadata.get("tmux_session") or ""), str(metadata.get("tmux_window") or "")
    )
    if process_id is None:
        return None
    sessions_root = _codex_sessions_root()
    candidates: dict[str, tuple[Path, CodexSessionMeta]] = {}
    for descendant in _descendant_process_ids(process_id):
        fd_root = Path(f"/proc/{descendant}/fd")
        try:
            descriptors = list(fd_root.iterdir())
        except OSError:
            continue
        for descriptor in descriptors:
            try:
                target_value = os.readlink(descriptor)
                if target_value.endswith(" (deleted)"):
                    continue
                target = Path(target_value).resolve(strict=False)
            except OSError:
                continue
            if target.suffix != ".jsonl" or not _is_within(target, sessions_root):
                continue
            meta = _read_session_meta(target)
            if meta is None or meta.source != "cli":
                continue
            candidates[meta.session_id] = (target, meta)
    if len(candidates) != 1:
        return None
    return next(iter(candidates.values()))


def bind_live_codex_session(metadata: dict[str, Any]) -> Optional[str]:
    """Bind a live terminal from its exact process-owned rollout descriptor."""
    live = _live_root_rollout(metadata)
    if live is None:
        return None
    _path, meta = live
    terminal_id = str(metadata.get("id") or "")
    if not terminal_id or not bind_provider_usage_session(
        provider=CODEX_PROVIDER,
        provider_session_id=meta.session_id,
        terminal_id=terminal_id,
        source="live_process_fd_v1",
    ):
        return None
    return meta.session_id


def _usage_value(usage: dict[str, Any], name: str) -> Optional[int]:
    value = usage.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def read_codex_usage_chunk(
    path: Path, *, provider_session_id: str, byte_offset: int, byte_budget: int
) -> CodexUsageChunk:
    """Read complete JSONL records and return the latest cumulative checkpoint."""
    if byte_offset < 0 or byte_budget <= 0:
        return CodexUsageChunk(byte_offset, None)
    latest_usage: Optional[dict[str, Any]] = None
    latest_model: Optional[str] = None
    next_offset = byte_offset
    try:
        with path.open("rb") as handle:
            file_size = os.fstat(handle.fileno()).st_size
            if byte_offset > file_size:
                return CodexUsageChunk(byte_offset, None)
            handle.seek(byte_offset)
            while handle.tell() - byte_offset < byte_budget:
                line_start = handle.tell()
                raw = handle.readline(MAX_JSONL_LINE_BYTES + 1)
                if not raw:
                    break
                if len(raw) > MAX_JSONL_LINE_BYTES or not raw.endswith(b"\n"):
                    handle.seek(line_start)
                    break
                next_offset = handle.tell()
                try:
                    row = json.loads(raw)
                except (UnicodeError, json.JSONDecodeError):
                    continue
                if not isinstance(row, dict):
                    continue
                if row.get("type") == "session_meta" and line_start == 0:
                    payload = row.get("payload")
                    if (
                        not isinstance(payload, dict)
                        or (payload.get("id") or payload.get("session_id")) != provider_session_id
                    ):
                        return CodexUsageChunk(byte_offset, None)
                if row.get("type") == "turn_context":
                    payload = row.get("payload")
                    model = payload.get("model") if isinstance(payload, dict) else None
                    if isinstance(model, str) and model.strip():
                        latest_model = model.strip()
                    continue
                if row.get("type") != "event_msg":
                    continue
                payload = row.get("payload")
                if not isinstance(payload, dict) or payload.get("type") != "token_count":
                    continue
                info = payload.get("info")
                usage = info.get("total_token_usage") if isinstance(info, dict) else None
                if isinstance(usage, dict):
                    latest_usage = usage
    except OSError:
        return CodexUsageChunk(byte_offset, None)

    observation = None
    if latest_usage is not None:
        observation = UsageObservation(
            source_run_identity=hashlib.sha256(
                f"{CODEX_EXTRACTOR}\0{provider_session_id}".encode("utf-8", "strict")
            ).hexdigest(),
            extractor=CODEX_EXTRACTOR,
            model=latest_model,
            input_tokens=_usage_value(latest_usage, "input_tokens"),
            cached_input_tokens=_usage_value(latest_usage, "cached_input_tokens"),
            cache_write_input_tokens=_usage_value(latest_usage, "cache_write_input_tokens"),
            output_tokens=_usage_value(latest_usage, "output_tokens"),
            reasoning_output_tokens=_usage_value(latest_usage, "reasoning_output_tokens"),
            total_tokens=_usage_value(latest_usage, "total_tokens"),
        )
    return CodexUsageChunk(next_offset, observation)


def _record_chunk(
    metadata: dict[str, Any], binding: dict[str, Any], path: Path, byte_budget: int
) -> tuple[int, str]:
    current_offset = int(binding.get("byte_offset") or 0)
    chunk = read_codex_usage_chunk(
        path,
        provider_session_id=str(binding["provider_session_id"]),
        byte_offset=current_offset,
        byte_budget=byte_budget,
    )
    processed = max(chunk.next_byte_offset - current_offset, 0)
    if processed == 0 and chunk.observation is None:
        return 0, "unchanged"
    result = record_provider_usage_checkpoint(
        chunk.observation,
        provider=CODEX_PROVIDER,
        provider_session_id=str(binding["provider_session_id"]),
        terminal_id=str(metadata.get("id") or ""),
        terminal_name=metadata.get("tmux_window"),
        session_id=metadata.get("session_id"),
        session_name=metadata.get("tmux_session"),
        agent_profile=metadata.get("agent_profile"),
        project_id=metadata.get("project_id"),
        project_name=metadata.get("project_name"),
        project_path=metadata.get("project_path"),
        next_byte_offset=chunk.next_byte_offset,
    )
    return processed, result


def observe_codex_terminal_usage(
    metadata: dict[str, Any], *, byte_budget: int = DEFAULT_TERMINAL_READ_BUDGET
) -> dict[str, int]:
    """Refresh all exact Codex sessions owned by one retained terminal."""
    terminal_id = str(metadata.get("id") or "")
    if not terminal_id or metadata.get("provider") != CODEX_PROVIDER:
        return {"bytes_processed": 0, "records_updated": 0, "binding_count": 0}
    bindings = list_provider_usage_bindings(terminal_id=terminal_id, provider=CODEX_PROVIDER)
    live_path: Optional[Path] = None
    if not bindings:
        live = _live_root_rollout(metadata)
        if live is not None:
            live_path, meta = live
            if bind_provider_usage_session(
                provider=CODEX_PROVIDER,
                provider_session_id=meta.session_id,
                terminal_id=terminal_id,
                source="live_process_fd_v1",
            ):
                bindings = list_provider_usage_bindings(
                    terminal_id=terminal_id, provider=CODEX_PROVIDER
                )
    index = _rollout_index()
    # Codex can create native child sessions after the root was initially
    # discovered. Bind those descendants on every refresh; the one-time
    # historical root repair is deliberately not responsible for live growth.
    _bind_codex_child_sessions(index)
    bindings = list_provider_usage_bindings(terminal_id=terminal_id, provider=CODEX_PROVIDER)
    processed = 0
    updated = 0
    for binding in bindings:
        remaining = byte_budget - processed
        if remaining <= 0:
            break
        indexed = index.get(str(binding["provider_session_id"]))
        path = indexed[0] if indexed is not None else live_path
        if path is None:
            continue
        amount, result = _record_chunk(metadata, binding, path, remaining)
        processed += amount
        if result == "updated":
            updated += 1
    return {
        "bytes_processed": processed,
        "records_updated": updated,
        "binding_count": len(bindings),
    }


def refresh_all_codex_usage(*, byte_budget: int = DEFAULT_REFRESH_READ_BUDGET) -> dict[str, int]:
    """Repair exact bindings and refresh retained terminals within one budget."""
    ensure_historical_codex_bindings()
    terminals = {
        str(row["id"]): row for row in list_all_terminals() if row.get("provider") == CODEX_PROVIDER
    }
    index = _rollout_index()
    _bind_codex_child_sessions(index)
    bindings = list_provider_usage_bindings(provider=CODEX_PROVIDER)
    processed = 0
    updated = 0
    for binding in bindings:
        remaining = byte_budget - processed
        if remaining <= 0:
            break
        metadata = terminals.get(str(binding["terminal_id"]))
        indexed = index.get(str(binding["provider_session_id"]))
        if metadata is None or indexed is None:
            continue
        amount, result = _record_chunk(metadata, binding, indexed[0], remaining)
        processed += amount
        if result == "updated":
            updated += 1
    return {
        "bytes_processed": processed,
        "records_updated": updated,
        "binding_count": len(bindings),
    }
