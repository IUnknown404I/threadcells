"""Owner-authorized, single-writer supervisor recovery takeover."""

from __future__ import annotations

import logging
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, Optional

from cli_agent_orchestrator.clients.database import (
    RecoveryTakeoverRejected,
    claim_recovery_takeover,
    claim_recovery_takeover_dispatch,
    fence_claimed_recovery_takeover,
    get_recovery_takeover,
    get_recovery_takeover_by_request_id,
    get_terminal_metadata,
    list_reconcilable_recovery_takeovers,
    mark_recovery_takeover_completed,
    mark_recovery_takeover_dispatch_uncertain,
    mark_terminal_runtime_running,
    record_recovery_takeover_claim_wait,
    record_recovery_takeover_rejection,
    recovery_takeover_durable_eligibility,
    reset_recovery_takeover_after_confirmed_prestart_failure,
    validate_owner_launch_grant,
)
from cli_agent_orchestrator.clients.tmux import PaneTargetError, tmux_client
from cli_agent_orchestrator.constants import SESSION_PREFIX
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.manager import provider_manager
from cli_agent_orchestrator.services.control_plane_registry import resolve_launch
from cli_agent_orchestrator.services.managed_worktree_service import managed_worktree_status
from cli_agent_orchestrator.services.operations_service import context_launch_admission
from cli_agent_orchestrator.services.terminal_service import (
    SHELL_COMMANDS,
    _create_terminal_after_admission,
)
from cli_agent_orchestrator.utils.terminal import generate_terminal_id, generate_window_name

logger = logging.getLogger(__name__)


class RecoveryTakeoverError(RuntimeError):
    """Stable fail-closed recovery-takeover outcome."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


def _proc_stat_identity(path: Path) -> tuple[str, int, int, int]:
    text = path.read_text(encoding="utf-8")
    suffix = text[text.rfind(")") + 2 :].split()
    return suffix[0], int(suffix[2]), int(suffix[3]), int(suffix[19])


def _runtime_process_tree_absent(
    metadata: Mapping[str, Any], *, proc_root: Path = Path("/proc")
) -> tuple[bool, str | None]:
    """Prove the persisted pane process group/session has no live writer."""
    pane_pid = metadata.get("runtime_pane_pid")
    start_ticks = metadata.get("runtime_process_start_ticks")
    process_group_id = metadata.get("runtime_process_group_id")
    process_session_id = metadata.get("runtime_process_session_id")
    if not all(
        isinstance(value, int) and value > 1
        for value in (pane_pid, start_ticks, process_group_id, process_session_id)
    ):
        return False, "RECOVERY_RUNTIME_AUTHORITY_AMBIGUOUS"
    try:
        state, _group, _session, observed_ticks = _proc_stat_identity(
            proc_root / str(pane_pid) / "stat"
        )
    except FileNotFoundError:
        pass
    except (OSError, ValueError, IndexError):
        return False, "RECOVERY_RUNTIME_INVENTORY_UNAVAILABLE"
    else:
        if observed_ticks == start_ticks and state != "Z":
            return False, "RECOVERY_RUNTIME_PROCESS_TREE_ACTIVE"
    try:
        processes = list(proc_root.iterdir())
    except OSError:
        return False, "RECOVERY_RUNTIME_INVENTORY_UNAVAILABLE"
    for process in processes:
        if not process.name.isdigit():
            continue
        try:
            state, group_id, session_id, _ticks = _proc_stat_identity(process / "stat")
        except FileNotFoundError:
            continue
        except (OSError, ValueError, IndexError):
            return False, "RECOVERY_RUNTIME_INVENTORY_UNAVAILABLE"
        if state != "Z" and (group_id == process_group_id or session_id == process_session_id):
            return False, "RECOVERY_RUNTIME_PROCESS_TREE_ACTIVE"
    return True, None


def _request_result(result: dict[str, Any]) -> dict[str, Any]:
    """Never project a fenced-but-failed successor as authoritative."""
    if result.get("state") in {"failed", "dispatch_uncertain"}:
        raise RecoveryTakeoverError(
            str(result.get("failure_reason") or "RECOVERY_TAKEOVER_INCOMPLETE")
        )
    return result


def _physical_runtime_absence(
    metadata: Mapping[str, Any], *, proc_root: Path = Path("/proc")
) -> tuple[bool, str | None]:
    """Classify a missing or exactly idle old runtime as safely retireable."""
    session_name = str(metadata.get("tmux_session") or "")
    window_name = str(metadata.get("tmux_window") or "")
    exists = tmux_client.window_exists(session_name, window_name)
    if exists is False:
        return _runtime_process_tree_absent(metadata, proc_root=proc_root)
    if exists is None:
        return False, "RECOVERY_RUNTIME_INVENTORY_UNAVAILABLE"
    try:
        target = tmux_client.exact_runtime_target(session_name, window_name)
    except PaneTargetError as exc:
        if exc.reason_code in {
            "EXIT_SESSION_MISSING",
            "EXIT_WINDOW_MISSING",
            "EXIT_PANE_MISSING",
            "EXIT_PANE_DEAD",
        }:
            return _runtime_process_tree_absent(metadata, proc_root=proc_root)
        return False, "RECOVERY_RUNTIME_AUTHORITY_AMBIGUOUS"
    except Exception:
        return False, "RECOVERY_RUNTIME_INVENTORY_UNAVAILABLE"
    durable = (
        metadata.get("id"),
        metadata.get("runtime_pane_id"),
        metadata.get("runtime_pane_pid"),
        metadata.get("runtime_generation"),
        metadata.get("runtime_process_start_ticks"),
        metadata.get("runtime_process_group_id"),
        metadata.get("runtime_process_session_id"),
    )
    observed = (
        target.terminal_id,
        target.pane_id,
        target.pane_pid,
        target.runtime_generation,
        target.process_start_ticks,
        target.process_group_id,
        target.process_session_id,
    )
    if any(value in (None, "") for value in durable) or durable != observed:
        return False, "RECOVERY_RUNTIME_AUTHORITY_AMBIGUOUS"
    origin = metadata.get("runtime_generation_origin")
    if origin not in {"launch", "reconciled"} or (
        (origin == "launch") != bool(target.generation_inherited)
    ):
        return False, "RECOVERY_RUNTIME_AUTHORITY_AMBIGUOUS"
    if target.current_command in SHELL_COMMANDS or target.current_command == "":
        return True, None
    return False, "RECOVERY_HEALTHY_RUNTIME_ACTIVE"


def _retire_recovery_runtime(
    metadata: Mapping[str, Any], *, proc_root: Path = Path("/proc")
) -> tuple[bool, str | None]:
    """Permanently remove the exact idle old pane before writer-lease fencing."""
    session_name = str(metadata.get("tmux_session") or "")
    window_name = str(metadata.get("tmux_window") or "")
    exists = tmux_client.window_exists(session_name, window_name)
    if exists is False:
        return _runtime_process_tree_absent(metadata, proc_root=proc_root)
    if exists is None:
        return False, "RECOVERY_RUNTIME_INVENTORY_UNAVAILABLE"
    try:
        target = tmux_client.exact_runtime_target(session_name, window_name)
    except PaneTargetError as exc:
        if exc.reason_code in {
            "EXIT_SESSION_MISSING",
            "EXIT_WINDOW_MISSING",
            "EXIT_PANE_MISSING",
            "EXIT_PANE_DEAD",
        }:
            return _runtime_process_tree_absent(metadata, proc_root=proc_root)
        return False, "RECOVERY_RUNTIME_AUTHORITY_AMBIGUOUS"
    except Exception:
        return False, "RECOVERY_RUNTIME_INVENTORY_UNAVAILABLE"
    durable = (
        metadata.get("id"),
        metadata.get("runtime_pane_id"),
        metadata.get("runtime_pane_pid"),
        metadata.get("runtime_generation"),
        metadata.get("runtime_process_start_ticks"),
        metadata.get("runtime_process_group_id"),
        metadata.get("runtime_process_session_id"),
    )
    observed = (
        target.terminal_id,
        target.pane_id,
        target.pane_pid,
        target.runtime_generation,
        target.process_start_ticks,
        target.process_group_id,
        target.process_session_id,
    )
    origin = metadata.get("runtime_generation_origin")
    if (
        any(value in (None, "") for value in durable)
        or durable != observed
        or origin not in {"launch", "reconciled"}
        or ((origin == "launch") != bool(target.generation_inherited))
    ):
        return False, "RECOVERY_RUNTIME_AUTHORITY_AMBIGUOUS"
    if target.current_command not in SHELL_COMMANDS and target.current_command != "":
        return False, "RECOVERY_HEALTHY_RUNTIME_ACTIVE"
    if not tmux_client.retire_runtime_pane(target):
        return False, "RECOVERY_RUNTIME_RETIREMENT_FAILED"
    for _attempt in range(20):
        absent, reason = _runtime_process_tree_absent(metadata, proc_root=proc_root)
        if absent:
            return True, None
        if reason != "RECOVERY_RUNTIME_PROCESS_TREE_ACTIVE":
            return False, reason
        time.sleep(0.05)
    return False, "RECOVERY_RUNTIME_PROCESS_TREE_ACTIVE"


def _worktree_snapshot(metadata: Mapping[str, Any]) -> dict[str, Any]:
    if metadata.get("managed_worktree_kind"):
        status = managed_worktree_status(metadata)
        if not status.get("safe"):
            return {
                "state": "unknown",
                "dirty": None,
                "reason_code": status.get("reason_code") or "RECOVERY_WORKTREE_AUTHORITY_AMBIGUOUS",
            }
        return {
            "state": "dirty" if not status.get("clean") else "clean",
            "dirty": not bool(status.get("clean")),
            "reason_code": None,
        }
    path = metadata.get("launch_worktree")
    if not isinstance(path, str) or not path.startswith("/"):
        return {
            "state": "unknown",
            "dirty": None,
            "reason_code": "RECOVERY_WORKTREE_AUTHORITY_AMBIGUOUS",
        }
    try:
        completed = subprocess.run(
            ["git", "-C", str(Path(path)), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return {
            "state": "unknown",
            "dirty": None,
            "reason_code": "RECOVERY_WORKTREE_STATUS_UNAVAILABLE",
        }
    if completed.returncode != 0:
        return {
            "state": "unknown",
            "dirty": None,
            "reason_code": "RECOVERY_WORKTREE_STATUS_UNAVAILABLE",
        }
    dirty = bool(completed.stdout)
    return {"state": "dirty" if dirty else "clean", "dirty": dirty, "reason_code": None}


def preview_recovery_takeover(
    old_terminal_id: str,
    *,
    expected_authority_generation: Optional[str] = None,
    expected_runtime_generation: Optional[str] = None,
) -> dict[str, Any]:
    durable = recovery_takeover_durable_eligibility(
        old_terminal_id,
        expected_authority_generation=expected_authority_generation,
        expected_runtime_generation=expected_runtime_generation,
    )
    terminal = durable.get("terminal")
    if terminal is None:
        return {**durable, "runtime_absent": None, "worktree": None}
    worktree = _worktree_snapshot(terminal)
    if durable["eligible"] and worktree["state"] == "unknown":
        durable = {
            **durable,
            "eligible": False,
            "reason_code": worktree["reason_code"],
        }
    absent, runtime_reason = _physical_runtime_absence(terminal)
    if durable["eligible"] and not absent:
        durable = {**durable, "eligible": False, "reason_code": runtime_reason}
    return {
        **durable,
        "runtime_absent": absent,
        "worktree": worktree,
        "consequence": "OLD_SUPERVISOR_PERMANENTLY_LOSES_WRITER_AUTHORITY",
    }


def request_recovery_takeover(
    *,
    request_id: str,
    old_terminal_id: str,
    expected_authority_generation: str,
    expected_runtime_generation: str,
    agent_profile: str,
    provider: str,
    owner_grant_token: str,
    owner_grant_launch_id: str,
    registry=None,
) -> dict[str, Any]:
    existing = get_recovery_takeover_by_request_id(request_id)
    if existing is not None:
        if (
            existing.get("old_terminal_id") != old_terminal_id
            or existing.get("expected_authority_generation") != expected_authority_generation
            or existing.get("expected_runtime_generation") != expected_runtime_generation
            or existing.get("agent_profile") != agent_profile
            or existing.get("provider") != provider
        ):
            raise RecoveryTakeoverError("RECOVERY_REQUEST_ID_REUSED")
        return _request_result(reconcile_recovery_takeover(str(existing["id"]), registry=registry))
    preview = preview_recovery_takeover(
        old_terminal_id,
        expected_authority_generation=expected_authority_generation,
        expected_runtime_generation=expected_runtime_generation,
    )
    if not preview["eligible"]:
        reason = str(preview.get("reason_code") or "RECOVERY_TAKEOVER_REJECTED")
        record_recovery_takeover_rejection(
            request_id=request_id, old_terminal_id=old_terminal_id, reason_code=reason
        )
        raise RecoveryTakeoverError(reason)
    terminal = preview["terminal"]
    resolution = resolve_launch(agent_profile, fallback_provider=provider)
    if not resolution.owner_grant_required or resolution.provider_adapter_id != provider:
        reason = "RECOVERY_PROFILE_AUTHORITY_MISMATCH"
        record_recovery_takeover_rejection(
            request_id=request_id, old_terminal_id=old_terminal_id, reason_code=reason
        )
        raise RecoveryTakeoverError(reason)
    owner_scope = {
        "profile_revision_id": resolution.profile_revision_id,
        "provider_config_revision_id": resolution.provider_config_revision_id,
        "project_id": terminal["project_id"],
        "launch_mode": "recovery_takeover",
        "delegation_depth": 0,
        "target_terminal_id": old_terminal_id,
        "expected_authority_generation": expected_authority_generation,
        "expected_runtime_generation": expected_runtime_generation,
    }
    if not validate_owner_launch_grant(
        owner_grant_token,
        launch_id=owner_grant_launch_id,
        agent_profile=agent_profile,
        provider=provider,
        canonical_worktree=str(terminal["launch_worktree"]),
        requested_session_name=None,
        grant_scope=owner_scope,
    ):
        reason = "OWNER_GRANT_INVALID_OR_EXPIRED"
        record_recovery_takeover_rejection(
            request_id=request_id,
            old_terminal_id=old_terminal_id,
            reason_code=reason,
        )
        raise RecoveryTakeoverError(reason)
    new_terminal_id = generate_terminal_id()
    new_session_name = f"{SESSION_PREFIX}recovery-{old_terminal_id}-{uuid.uuid4().hex[:6]}"
    new_session_id = str(uuid.uuid4())
    new_window_name = generate_window_name(agent_profile)
    new_runtime_generation = str(uuid.uuid4())
    try:
        with context_launch_admission(
            canonical_worktree=terminal["launch_worktree"],
            write_enabled=True,
            context_role="supervisor",
            project_id=terminal["project_id"],
        ):
            # Runtime absence was observed before waiting for capacity. Re-prove
            # it under the same launch fence immediately before the DB CAS.
            refreshed = preview_recovery_takeover(
                old_terminal_id,
                expected_authority_generation=expected_authority_generation,
                expected_runtime_generation=expected_runtime_generation,
            )
            if not refreshed["eligible"]:
                raise RecoveryTakeoverError(
                    str(refreshed.get("reason_code") or "RECOVERY_TAKEOVER_REJECTED")
                )
            # Win the durable claim before any runtime mutation. A daemon can
            # resume this exact request if the process dies before fencing.
            takeover = claim_recovery_takeover(
                request_id=request_id,
                old_terminal_id=old_terminal_id,
                expected_authority_generation=expected_authority_generation,
                expected_runtime_generation=expected_runtime_generation,
                agent_profile=agent_profile,
                provider=provider,
                profile_revision_id=resolution.profile_revision_id,
                provider_config_revision_id=resolution.provider_config_revision_id,
                owner_grant_token=owner_grant_token,
                owner_grant_launch_id=owner_grant_launch_id,
                owner_grant_scope=owner_scope,
                new_terminal_id=new_terminal_id,
                new_session_name=new_session_name,
                new_session_id=new_session_id,
                new_window_name=new_window_name,
                new_runtime_generation=new_runtime_generation,
            )
        return _request_result(reconcile_recovery_takeover(takeover["id"], registry=registry))
    except RecoveryTakeoverRejected as exc:
        record_recovery_takeover_rejection(
            request_id=request_id,
            old_terminal_id=old_terminal_id,
            reason_code=exc.reason_code,
        )
        raise RecoveryTakeoverError(exc.reason_code) from exc
    except RecoveryTakeoverError as exc:
        record_recovery_takeover_rejection(
            request_id=request_id,
            old_terminal_id=old_terminal_id,
            reason_code=exc.reason_code,
        )
        raise


def _launch_claimed_takeover(takeover: Mapping[str, Any], *, registry=None) -> None:
    old = get_terminal_metadata(str(takeover["old_terminal_id"]))
    if old is None or old.get("runtime_lifecycle") != "recovery_fenced":
        mark_recovery_takeover_dispatch_uncertain(str(takeover["id"]), "RECOVERY_OLD_FENCE_LOST")
        return
    resolution = resolve_launch(
        str(takeover["agent_profile"]), fallback_provider=str(takeover["provider"])
    )
    if (
        resolution.profile_revision_id != takeover.get("profile_revision_id")
        or resolution.provider_config_revision_id != takeover.get("provider_config_revision_id")
        or resolution.provider_adapter_id != takeover.get("provider")
    ):
        mark_recovery_takeover_dispatch_uncertain(
            str(takeover["id"]), "RECOVERY_LAUNCH_REVISION_CHANGED"
        )
        return
    _create_terminal_after_admission(
        provider=str(takeover["provider"]),
        agent_profile=str(takeover["agent_profile"]),
        session_name=str(takeover["new_session_name"]),
        new_session=True,
        working_directory=str(takeover["canonical_worktree"]),
        allowed_tools=list(resolution.profile.allowedTools or []),
        registry=registry,
        launch_worktree=str(takeover["canonical_worktree"]),
        write_enabled=True,
        context_role="supervisor",
        managed_worktree_kind=old.get("managed_worktree_kind"),
        managed_worktree_source=old.get("managed_worktree_source"),
        managed_worktree_branch=old.get("managed_worktree_branch"),
        managed_worktree_commit=old.get("managed_worktree_commit"),
        managed_worktree_origin_terminal_id=(
            old.get("managed_worktree_origin_terminal_id") or old.get("id")
        ),
        project_context={
            "id": str(old["project_id"]),
            "name": str(old.get("project_name") or old["project_id"]),
            "path": str(old.get("project_path") or takeover["canonical_worktree"]),
            "description": str(old.get("project_description") or ""),
        },
        terminal_id_override=str(takeover["new_terminal_id"]),
        privileged_launch=False,
        structured_owner_authorized=True,
        profile_revision_id=resolution.profile_revision_id,
        provider_config_revision_id=resolution.provider_config_revision_id,
        launch_snapshot={
            **resolution.snapshot,
            "tools": list(resolution.profile.allowedTools or []),
        },
        provider_configuration=resolution.provider_configuration,
        resolved_profile=resolution.profile,
        session_lifetime_id=str(takeover["new_session_id"]),
        window_name_override=str(takeover["new_window_name"]),
        runtime_generation_override=str(takeover["new_runtime_generation"]),
        recovery_takeover_id=str(takeover["id"]),
    )


def _recover_dispatching_takeover(takeover: Mapping[str, Any]) -> bool:
    """Return True when the caller may claim a fresh, positively safe retry."""
    new = get_terminal_metadata(str(takeover["new_terminal_id"]))
    session_name = str(takeover["new_session_name"])
    window_name = str(takeover["new_window_name"])
    if new is None:
        exists = tmux_client.session_exists(session_name)
        if exists is False:
            return reset_recovery_takeover_after_confirmed_prestart_failure(str(takeover["id"]))
        if exists is None:
            mark_recovery_takeover_dispatch_uncertain(
                str(takeover["id"]), "RECOVERY_PROVIDER_DISPATCH_UNCERTAIN"
            )
            return False
        try:
            target = tmux_client.exact_runtime_target(session_name, window_name)
        except PaneTargetError as exc:
            if exc.reason_code in {
                "EXIT_WINDOW_MISSING",
                "EXIT_PANE_MISSING",
                "EXIT_PANE_DEAD",
            } and tmux_client.kill_session(session_name):
                return reset_recovery_takeover_after_confirmed_prestart_failure(str(takeover["id"]))
            mark_recovery_takeover_dispatch_uncertain(
                str(takeover["id"]), "RECOVERY_PROVIDER_DISPATCH_UNCERTAIN"
            )
            return False
        if (
            target.terminal_id == takeover["new_terminal_id"]
            and target.runtime_generation == takeover["new_runtime_generation"]
            and target.generation_inherited
            and tmux_client.kill_session(session_name)
            and tmux_client.session_exists(session_name) is False
        ):
            return reset_recovery_takeover_after_confirmed_prestart_failure(str(takeover["id"]))
        mark_recovery_takeover_dispatch_uncertain(
            str(takeover["id"]), "RECOVERY_PROVIDER_DISPATCH_UNCERTAIN"
        )
        return False
    if new.get("runtime_lifecycle") == "running":
        mark_recovery_takeover_completed(str(takeover["id"]))
        return False
    if new.get("runtime_lifecycle") != "starting":
        mark_recovery_takeover_dispatch_uncertain(
            str(takeover["id"]), "RECOVERY_PROVIDER_DISPATCH_UNCERTAIN"
        )
        return False
    try:
        target = tmux_client.exact_runtime_target(session_name, window_name)
    except Exception:
        mark_recovery_takeover_dispatch_uncertain(
            str(takeover["id"]), "RECOVERY_PROVIDER_DISPATCH_UNCERTAIN"
        )
        return False
    durable = (
        new.get("id"),
        new.get("runtime_pane_id"),
        new.get("runtime_pane_pid"),
        new.get("runtime_generation"),
        new.get("runtime_process_start_ticks"),
        new.get("runtime_process_group_id"),
        new.get("runtime_process_session_id"),
    )
    observed = (
        target.terminal_id,
        target.pane_id,
        target.pane_pid,
        target.runtime_generation,
        target.process_start_ticks,
        target.process_group_id,
        target.process_session_id,
    )
    if durable != observed:
        mark_recovery_takeover_dispatch_uncertain(
            str(takeover["id"]), "RECOVERY_PROVIDER_DISPATCH_UNCERTAIN"
        )
        return False
    provider = provider_manager.get_provider(str(takeover["new_terminal_id"]))
    if provider is None:
        mark_recovery_takeover_dispatch_uncertain(
            str(takeover["id"]), "RECOVERY_PROVIDER_START_UNCLASSIFIED"
        )
        return False
    if target.current_command in SHELL_COMMANDS or target.current_command == "":
        # DB insertion precedes provider initialization. A current exact shell
        # is positive pre-start evidence, so one initialization is safe.
        provider.initialize()
    else:
        status = provider.get_status()
        if status not in {
            TerminalStatus.IDLE,
            TerminalStatus.PROCESSING,
            TerminalStatus.WAITING_USER_ANSWER,
        }:
            mark_recovery_takeover_dispatch_uncertain(
                str(takeover["id"]), "RECOVERY_PROVIDER_START_UNCLASSIFIED"
            )
            return False
    if not mark_terminal_runtime_running(str(takeover["new_terminal_id"])):
        mark_recovery_takeover_dispatch_uncertain(
            str(takeover["id"]), "RECOVERY_PROVIDER_START_UNCLASSIFIED"
        )
        return False
    mark_recovery_takeover_completed(str(takeover["id"]))
    return False


def reconcile_recovery_takeover(takeover_id: str, *, registry=None) -> dict[str, Any]:
    takeover = get_recovery_takeover(takeover_id)
    if takeover is None:
        raise RecoveryTakeoverError("RECOVERY_TAKEOVER_NOT_FOUND")
    if takeover["state"] == "completed":
        return takeover
    if takeover["state"] == "claimed":
        old = get_terminal_metadata(str(takeover["old_terminal_id"]))
        if old is None:
            return (
                record_recovery_takeover_claim_wait(
                    takeover_id, "RECOVERY_TARGET_NOT_FOUND", terminal=True
                )
                or takeover
            )
        absent, reason = _physical_runtime_absence(old)
        if not absent:
            return (
                record_recovery_takeover_claim_wait(
                    takeover_id,
                    reason or "RECOVERY_RUNTIME_AUTHORITY_AMBIGUOUS",
                    terminal=reason != "RECOVERY_RUNTIME_INVENTORY_UNAVAILABLE",
                )
                or takeover
            )
        with context_launch_admission(
            canonical_worktree=str(takeover["canonical_worktree"]),
            write_enabled=True,
            context_role="supervisor",
            project_id=str(takeover["project_id"]),
        ):
            # Re-prove and retire the exact old runtime under the sole launch
            # fence, then atomically move the DB writer epoch. A crash between
            # these steps leaves a durable claimed row for startup recovery.
            old = get_terminal_metadata(str(takeover["old_terminal_id"]))
            if old is None:
                return takeover
            retired, reason = _retire_recovery_runtime(old)
            if not retired:
                return (
                    record_recovery_takeover_claim_wait(
                        takeover_id,
                        reason or "RECOVERY_RUNTIME_RETIREMENT_FAILED",
                        terminal=reason != "RECOVERY_RUNTIME_INVENTORY_UNAVAILABLE",
                    )
                    or takeover
                )
            takeover = fence_claimed_recovery_takeover(takeover_id) or takeover
    if takeover["state"] in {"fenced", "dispatching", "admitted"}:
        with context_launch_admission(
            canonical_worktree=str(takeover["canonical_worktree"]),
            write_enabled=True,
            context_role="supervisor",
            project_id=str(takeover["project_id"]),
        ):
            # Re-read under the cross-process context launch lock. Both the
            # original provider launch and crash recovery use this same lock,
            # so only one process may initialize an admitted shell or claim a
            # retry. A waiter observes the completed transition instead of
            # replaying provider initialization.
            takeover = get_recovery_takeover(takeover_id) or takeover
            if takeover["state"] == "admitted":
                _recover_dispatching_takeover(takeover)
                return get_recovery_takeover(takeover_id) or takeover
            if takeover["state"] == "dispatching":
                if not _recover_dispatching_takeover(takeover):
                    return get_recovery_takeover(takeover_id) or takeover
                takeover = get_recovery_takeover(takeover_id) or takeover
            if takeover["state"] == "fenced":
                claimed = claim_recovery_takeover_dispatch(takeover_id)
                if claimed is not None and claimed["state"] == "dispatching":
                    _launch_claimed_takeover(claimed, registry=registry)
    return get_recovery_takeover(takeover_id) or takeover


def reconcile_recovery_takeovers(*, registry=None) -> int:
    """Startup/daemon reconciliation for all nonterminal takeover claims."""
    changed = 0
    for takeover in list_reconcilable_recovery_takeovers():
        before = takeover["state"]
        try:
            after = reconcile_recovery_takeover(str(takeover["id"]), registry=registry)
        except Exception as exc:
            logger.warning("Recovery takeover %s reconciliation deferred: %s", takeover["id"], exc)
            continue
        if after["state"] != before:
            changed += 1
    return changed
