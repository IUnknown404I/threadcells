import json

import pytest

from cli_agent_orchestrator.clients.database import (
    FullCleanupOperationModel,
    SessionLocal,
    admit_full_cleanup_operation,
    claim_full_cleanup_operation,
    complete_full_cleanup_operation,
    get_full_cleanup_operation,
    list_active_full_cleanup_operations,
    terminalize_full_cleanup_operation,
    update_full_cleanup_operation_progress,
)
from cli_agent_orchestrator.services.full_cleanup_operation_service import (
    public_operation,
    reconcile_interrupted_full_cleanup_operations,
    report_from_operation,
)
from cli_agent_orchestrator.services.housekeeping_service import HousekeepingSummary
from cli_agent_orchestrator.services.operations_service import (
    AdmissionDenied,
    require_resource_admission,
    workflow_execution_admission_fence,
)


@pytest.fixture(autouse=True)
def isolated_full_cleanup_operations():
    with SessionLocal() as db:
        db.query(FullCleanupOperationModel).delete()
        db.commit()
    yield
    with SessionLocal() as db:
        db.query(FullCleanupOperationModel).delete()
        db.commit()


def test_full_cleanup_operation_is_single_claimed_and_terminally_idempotent():
    operation_id = "a" * 32
    plan_id = "b" * 64
    token = "operation-token-with-at-least-thirty-two-bytes"

    admitted = admit_full_cleanup_operation(
        operation_id,
        plan_id,
        retire_dirty_worktrees=False,
        actor_kind="operator_session",
        operation_token=token,
    )
    assert admitted["created"] is True
    assert admitted["state"] == "admitted"
    assert admitted["report"] is None
    public = public_operation(admitted)
    assert "operation_token" not in public
    assert "actor_kind" not in public
    assert "helper_pid" not in public
    assert "helper_process_start_ticks" not in public

    duplicate = admit_full_cleanup_operation(
        operation_id,
        plan_id,
        retire_dirty_worktrees=False,
        actor_kind="operator_bearer",
        operation_token="a-different-token-that-must-not-gain-authority",
    )
    assert duplicate["created"] is False
    with pytest.raises(RuntimeError, match="FULL_CLEANUP_OPERATION_ACTIVE"):
        admit_full_cleanup_operation(
            "c" * 32,
            "d" * 64,
            retire_dirty_worktrees=False,
            actor_kind="operator_session",
            operation_token="another-operation-token-with-enough-entropy",
        )

    assert claim_full_cleanup_operation(
        operation_id,
        token,
        helper_pid=321,
        helper_process_start_ticks=654,
    )
    assert not claim_full_cleanup_operation(
        operation_id,
        token,
        helper_pid=321,
        helper_process_start_ticks=654,
    )
    assert update_full_cleanup_operation_progress(
        operation_id,
        helper_pid=321,
        helper_process_start_ticks=654,
        progress={
            "schema_version": 1,
            "sequence": 1,
            "phase": "runtime",
            "processed_candidates": 1,
            "executed_candidates": 1,
            "skipped_candidates": 0,
            "failed_candidates": 0,
            "freed_bytes": 123,
            "last_candidate_sha256": "a" * 64,
            "last_outcome": "executed",
        },
    )
    assert not update_full_cleanup_operation_progress(
        operation_id,
        helper_pid=321,
        helper_process_start_ticks=654,
        progress={
            "schema_version": 1,
            "sequence": 1,
            "phase": "runtime",
            "processed_candidates": 1,
            "executed_candidates": 1,
            "skipped_candidates": 0,
            "failed_candidates": 0,
            "freed_bytes": 123,
            "last_candidate_sha256": "a" * 64,
            "last_outcome": "executed",
        },
    )
    summary = HousekeepingSummary(mode="full", full_cleanup=True, plan_id=plan_id, freed_bytes=123)
    assert complete_full_cleanup_operation(
        operation_id,
        helper_pid=321,
        helper_process_start_ticks=654,
        report=summary.as_dict(),
    )
    assert not complete_full_cleanup_operation(
        operation_id,
        helper_pid=321,
        helper_process_start_ticks=654,
        report=summary.as_dict(),
    )
    current = get_full_cleanup_operation(operation_id)
    assert current is not None
    assert current["state"] == "completed"
    assert current["progress"]["sequence"] == 1
    assert report_from_operation(current).freed_bytes == 123
    assert list_active_full_cleanup_operations() == []


def test_full_cleanup_operation_rejects_wrong_token_generation_and_plan():
    operation_id = "e" * 32
    plan_id = "f" * 64
    token = "operation-token-with-at-least-thirty-two-bytes"
    admit_full_cleanup_operation(
        operation_id,
        plan_id,
        retire_dirty_worktrees=True,
        actor_kind="operator_bearer",
        operation_token=token,
    )

    with pytest.raises(RuntimeError, match="FULL_CLEANUP_OPERATION_AUTHORITY_CHANGED"):
        admit_full_cleanup_operation(
            operation_id,
            "0" * 64,
            retire_dirty_worktrees=True,
            actor_kind="operator_bearer",
            operation_token=token,
        )
    assert not claim_full_cleanup_operation(
        operation_id,
        "wrong-token-with-at-least-thirty-two-bytes",
        helper_pid=10,
        helper_process_start_ticks=11,
    )
    assert claim_full_cleanup_operation(
        operation_id,
        token,
        helper_pid=10,
        helper_process_start_ticks=11,
    )
    assert not update_full_cleanup_operation_progress(
        operation_id,
        helper_pid=10,
        helper_process_start_ticks=12,
        progress={
            "schema_version": 1,
            "sequence": 1,
            "phase": "runtime",
            "processed_candidates": 1,
            "executed_candidates": 1,
            "skipped_candidates": 0,
            "failed_candidates": 0,
            "freed_bytes": 0,
            "last_candidate_sha256": "0" * 64,
            "last_outcome": "executed",
        },
    )
    assert terminalize_full_cleanup_operation(
        operation_id,
        reason_code="FULL_CLEANUP_EXECUTION_FAILED",
        indeterminate=True,
        helper_pid=10,
        helper_process_start_ticks=11,
    )
    current = get_full_cleanup_operation(operation_id)
    assert current is not None
    assert current["state"] == "indeterminate"
    assert current["report"] is None


def test_restart_reconciliation_distinguishes_unstarted_running_and_live_helper(tmp_path):
    plan_id = "1" * 64
    token = "operation-token-with-at-least-thirty-two-bytes"
    admitted_id = "2" * 32
    admit_full_cleanup_operation(
        admitted_id,
        plan_id,
        retire_dirty_worktrees=False,
        actor_kind="operator_session",
        operation_token=token,
    )
    result = reconcile_interrupted_full_cleanup_operations(
        proc_root=tmp_path / "proc", include_admitted=True
    )
    assert result == {"inspected": 1, "active": 0, "terminalized": 1}
    assert get_full_cleanup_operation(admitted_id)["state"] == "failed"

    running_id = "3" * 32
    admit_full_cleanup_operation(
        running_id,
        plan_id,
        retire_dirty_worktrees=False,
        actor_kind="operator_session",
        operation_token=token,
    )
    assert claim_full_cleanup_operation(
        running_id, token, helper_pid=41, helper_process_start_ticks=99
    )
    result = reconcile_interrupted_full_cleanup_operations(
        proc_root=tmp_path / "proc", include_admitted=True
    )
    assert result == {"inspected": 1, "active": 0, "terminalized": 1}
    assert get_full_cleanup_operation(running_id)["state"] == "indeterminate"

    live_id = "4" * 32
    admit_full_cleanup_operation(
        live_id,
        plan_id,
        retire_dirty_worktrees=False,
        actor_kind="operator_session",
        operation_token=token,
    )
    assert claim_full_cleanup_operation(
        live_id, token, helper_pid=42, helper_process_start_ticks=100
    )
    process = tmp_path / "proc/42"
    process.mkdir(parents=True)
    process.joinpath("stat").write_text(
        "42 (helper) S " + " ".join(["0"] * 18 + ["100"] + ["0"] * 5),
        encoding="utf-8",
    )
    result = reconcile_interrupted_full_cleanup_operations(
        proc_root=tmp_path / "proc", include_admitted=True
    )
    assert result == {"inspected": 1, "active": 1, "terminalized": 0}
    assert get_full_cleanup_operation(live_id)["state"] == "running"


def test_full_cleanup_operation_documents_are_bounded():
    operation_id = "5" * 32
    token = "operation-token-with-at-least-thirty-two-bytes"
    admit_full_cleanup_operation(
        operation_id,
        "6" * 64,
        retire_dirty_worktrees=False,
        actor_kind="operator_session",
        operation_token=token,
    )
    assert claim_full_cleanup_operation(
        operation_id, token, helper_pid=50, helper_process_start_ticks=51
    )
    with pytest.raises(ValueError, match="invalid Full Cleanup operation progress"):
        update_full_cleanup_operation_progress(
            operation_id,
            helper_pid=50,
            helper_process_start_ticks=51,
            progress={"value": "x" * (65 * 1024)},
        )
    with pytest.raises(ValueError, match="too large"):
        complete_full_cleanup_operation(
            operation_id,
            helper_pid=50,
            helper_process_start_ticks=51,
            report={"ok": True, "warnings": ["x" * (8 * 1024 * 1024)]},
        )
    current = get_full_cleanup_operation(operation_id)
    assert current is not None
    assert json.dumps(public_operation(current), default=str).find(token) == -1


def test_admitted_full_cleanup_fences_new_resource_authority():
    admit_full_cleanup_operation(
        "7" * 32,
        "8" * 64,
        retire_dirty_worktrees=False,
        actor_kind="operator_session",
        operation_token="operation-token-with-at-least-thirty-two-bytes",
    )
    with pytest.raises(AdmissionDenied) as denied:
        require_resource_admission(
            {},
            status_probe=lambda: {
                "resource_state": "GREEN",
                "provider_executions": {"certain": True, "available": 1},
                "work_contexts": {"certain": True, "available": 1},
            },
            attempt_pressure_recovery=False,
        )
    assert denied.value.reason_code == "FULL_CLEANUP_OPERATION_ACTIVE"
    with workflow_execution_admission_fence(nonblocking=True) as admitted:
        assert admitted is False
    with pytest.raises(AdmissionDenied) as workflow_denied:
        with workflow_execution_admission_fence():
            pytest.fail("active cleanup must fence new workflow execution")
    assert workflow_denied.value.reason_code == "FULL_CLEANUP_OPERATION_ACTIVE"
