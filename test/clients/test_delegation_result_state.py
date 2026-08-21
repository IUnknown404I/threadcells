"""Focused F14 durable-result state and idempotency coverage."""

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import cli_agent_orchestrator.clients.database as database
from cli_agent_orchestrator.clients.database import (
    Base,
    ChildAssignmentModel,
    TerminalModel,
    WorkflowModel,
    acknowledge_child_assignment_result,
    acknowledge_child_assignment_result_outcome,
    cancel_child_assignments_for_terminal,
    claim_handoff_child_result_direct,
    claim_workflow_effect,
    claim_workflow_turn_receipt,
    create_assigned_child_completion_result_message,
    create_child_assignment_result_message,
    create_handoff_child_result_message,
    get_claimed_handoff_child_result_direct,
    get_delegation_result,
    get_delegation_result_for_assignment,
    get_pending_handoff_child_terminal_ids,
    managed_final_problem,
    mark_child_assignment_result_delivered,
    mark_child_assignment_result_failed,
    parse_v1_result_capture,
    persist_terminal_result_snapshot,
    purge_expired_delegation_results,
    register_child_assignment,
    register_handoff_child,
    start_workflow_input,
    terminalize_missing_terminal_assignments_for_restart,
)
from cli_agent_orchestrator.providers.codex import CodexProvider

_PROVIDER_FIXTURES = Path(__file__).parents[1] / "providers" / "fixtures"


def _live_undecorated_last_capture() -> str:
    return (_PROVIDER_FIXTURES / "codex_live_undecorated_last_v1_output.txt").read_text()


def _soft_wrap_boundary_vector() -> tuple[str, str, dict, str]:
    """The approved exact logical-capture vector, deliberately wider than 80 columns."""
    payload = "0123456789abcdef" * 20 + "TAIL"
    wire = (
        "CAO_RESULT_V1\n"
        '{"summary":"soft-wrap-boundary","body_markdown":"'
        + payload
        + '","changed_files":[],"checks":[],"risks":[],"blockers":[],"format":"v1"}'
    )
    document = json.loads(wire.split("\n", 1)[1])
    capture = (
        "• Called mcp__cao_mcp_server__send_message\n"
        "  └ delivered\n"
        "\x1b[36m• CAO_RESULT_V1\x1b[0m\n" + wire.split("\n", 1)[1]
    )
    assert len(payload.encode()) == 324
    assert len(wire.encode()) == 459
    assert hashlib.sha256(payload.encode()).hexdigest() == (
        "4292f453e89a114c265494aa305cf213e3b7b7f3eabf827539787ef73b045849"
    )
    assert hashlib.sha256(wire.encode()).hexdigest() == (
        "55e41118a866be59d55d996fd9a45a243575e96d85cb155c56fdfdb24a8be42b"
    )
    return payload, wire, document, capture


def _isolated_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(database, "_child_assignment_schema_ready", True)
    monkeypatch.setattr(database, "_delegation_result_schema_ready", True)


def _authorized_callback(child_id: str):
    turn_id = start_workflow_input(child_id)
    assert turn_id is not None
    assert claim_workflow_turn_receipt(child_id, turn_id)
    effect = claim_workflow_effect(child_id, turn_id, "send_message", "result-state")
    assert effect is not None
    return {"workflow_effect_id": effect["id"], "workflow_turn_id": turn_id}


@pytest.mark.parametrize(
    "body",
    [
        "⠋ Working (...)",
        "◐ Working (...)",
        "• Working (...)",
        "Working",
        "I'll run the tests now",
        "Let me check...",
        "Tool call pending",
        "›",
        "",
        "partial progress…",
    ],
)
def test_managed_final_rejects_codex_progress_before_transport_metadata(body):
    assert managed_final_problem(body) is not None
    transported = body + "\n[Message from terminal child]\nNO_TG_NOTIFY"
    assert managed_final_problem(transported) is not None


def test_assigned_callback_rejects_spinner_through_public_entry(monkeypatch):
    _isolated_db(monkeypatch)
    assert register_child_assignment("parent", "child")
    with pytest.raises(ValueError, match="NON_SUBSTANTIVE_FINAL"):
        create_child_assignment_result_message(
            "child",
            "parent",
            "⠋ Working (...)\n[Message from terminal child]",
            **_authorized_callback("child"),
        )


def test_acknowledgement_outcome_distinguishes_queue_delivery_and_replay(monkeypatch):
    _isolated_db(monkeypatch)
    assert register_child_assignment("parent", "child")
    notice, _ = create_child_assignment_result_message(
        "child", "parent", "substantive report", **_authorized_callback("child")
    )
    assert notice and notice.result_id
    queued = acknowledge_child_assignment_result_outcome("parent", result_id=notice.result_id)
    assert queued["reason_code"] == "RESULT_NOT_DELIVERED"
    assert mark_child_assignment_result_delivered(notice.id)
    accepted = acknowledge_child_assignment_result_outcome("parent", result_id=notice.result_id)
    assert accepted["accepted"] is True
    replay = acknowledge_child_assignment_result_outcome("parent", result_id=notice.result_id)
    assert replay["reason_code"] == "RESULT_ALREADY_ACKNOWLEDGED"


def test_assigned_completion_replay_repairs_complete_notice_with_open_child(monkeypatch):
    _isolated_db(monkeypatch)
    assert register_child_assignment("parent", "child")
    notice, duplicate = create_child_assignment_result_message(
        "child", "parent", "authoritative report", **_authorized_callback("child")
    )
    assert notice is not None and duplicate is False
    # Reproduce the old split transaction exactly: result and existing parent
    # notice survived, but child terminalization did not.
    with database.SessionLocal() as db:
        child_workflow = db.query(WorkflowModel).filter_by(root_terminal_id="child").one()
        child_workflow.status = "open"
        db.commit()
    completion_turn = start_workflow_input("child")
    assert completion_turn is not None and claim_workflow_turn_receipt("child", completion_turn)
    completion_effect = claim_workflow_effect(
        "child", completion_turn, "complete_workflow", "repair-complete"
    )
    assert completion_effect is not None
    repaired, replay = create_assigned_child_completion_result_message(
        "child", "authoritative report", completion_effect["id"], completion_turn
    )
    assert replay is True and repaired is not None and repaired.id == notice.id
    with database.SessionLocal() as db:
        assert (
            db.query(WorkflowModel).filter_by(root_terminal_id="child").one().status == "terminal"
        )
        assert (
            db.query(ChildAssignmentModel)
            .filter_by(child_terminal_id="child")
            .one()
            .result_message_id
            == notice.id
        )


def test_result_without_assignment_does_not_fabricate_superseded(monkeypatch):
    _isolated_db(monkeypatch)
    assert register_child_assignment("parent", "child")
    notice, _ = create_child_assignment_result_message(
        "child", "parent", "authoritative report", **_authorized_callback("child")
    )
    assert notice is not None and notice.result_id
    with database.SessionLocal() as db:
        db.query(ChildAssignmentModel).filter_by(child_terminal_id="child").delete()
        db.commit()
    result = get_delegation_result(notice.result_id)
    assert result is not None
    assert "delivery_status" not in result


def test_assign_result_is_immutable_and_acknowledgeable_by_result_id(monkeypatch):
    _isolated_db(monkeypatch)
    assert register_child_assignment("parent", "child")
    notice, duplicate = create_child_assignment_result_message(
        "child", "parent", "first report", **_authorized_callback("child")
    )
    assert notice is not None and duplicate is False and notice.result_id
    artifact = get_delegation_result(notice.result_id)
    assert artifact is not None
    assert artifact["status"] == "complete"
    assert artifact["authorship"] == "child_submission"
    assert artifact["document"]["body_markdown"] == "first report"

    retry, duplicate = create_child_assignment_result_message(
        "child", "parent", "changed report", **_authorized_callback("child")
    )
    assert retry is not None and duplicate is True and retry.id == notice.id
    assert get_delegation_result(notice.result_id)["content_sha256"] == artifact["content_sha256"]

    assert mark_child_assignment_result_delivered(notice.id)
    assert acknowledge_child_assignment_result("parent", result_id=notice.result_id)


def test_handoff_capture_survives_child_lifecycle_cleanup(monkeypatch):
    _isolated_db(monkeypatch)
    assert register_handoff_child("parent", "child")
    assert claim_handoff_child_result_direct("parent", "child", "stable final") is True
    result = get_delegation_result_for_assignment("child")
    assert result is not None
    assert result["status"] == "complete"
    assert result["authorship"] == "cao_handoff_capture"

    # A child removed after a validated capture must not overwrite completion.
    assert cancel_child_assignments_for_terminal("child") == 0
    assert get_delegation_result(result["id"])["status"] == "complete"


def test_logical_v1_capture_is_byte_exact_without_legacy_dewrap_and_survives_replay_cleanup(
    monkeypatch,
):
    """The tmux -J path reaches F14 as logical text, not parser-repaired wraps."""
    _isolated_db(monkeypatch)
    payload, wire, document, capture = _soft_wrap_boundary_vector()
    extracted = CodexProvider("child", "session", "window").extract_last_message_from_script(
        capture
    )

    assert "Called mcp__cao_mcp_server__send_message" not in extracted
    assert extracted.startswith("• CAO_RESULT_V1\n")
    is_v1, parsed = parse_v1_result_capture(extracted)
    assert is_v1 is True
    assert parsed is not None
    assert parsed.model_dump() == document
    assert parsed.body_markdown.encode() == payload.encode()

    def _legacy_dewrap_must_not_run(*_args, **_kwargs):
        raise AssertionError("logical capture must not invoke legacy V1 dewrap")

    monkeypatch.setattr(database, "_dewrap_codex_json_string_folds", _legacy_dewrap_must_not_run)
    assert register_handoff_child("parent", "logical-child")
    assert claim_handoff_child_result_direct("parent", "logical-child", extracted) is True
    first = get_delegation_result_for_assignment("logical-child")
    assert first is not None
    assert first["document"] == document
    assert first["document"]["body_markdown"].encode() == payload.encode()

    # A later capture cannot change the first durable result, including after child cleanup.
    replay = wire.replace(payload, "different capture must not replace the first result")
    assert claim_handoff_child_result_direct("parent", "logical-child", replay) is True
    assert cancel_child_assignments_for_terminal("logical-child") == 0
    persisted = get_delegation_result(first["id"])
    assert persisted is not None
    assert persisted["document"] == document
    assert persisted["document"]["body_markdown"].encode() == payload.encode()


def test_handoff_capture_requires_explicit_cao_result_v1_channel(monkeypatch):
    """Transcript JSON is legacy text; only the dedicated channel is structured."""
    _isolated_db(monkeypatch)
    mixed_transcript = (
        "Ran a tool that returned this JSON:\n"
        '{"summary": "tool output", "body_markdown": "not a handoff result"}\n'
        "Finished the task."
    )
    assert register_handoff_child("parent", "mixed-child")
    assert claim_handoff_child_result_direct("parent", "mixed-child", mixed_transcript) is True
    mixed = get_delegation_result_for_assignment("mixed-child")
    assert mixed is not None
    assert mixed["document"] == {"body_markdown": mixed_transcript, "format": "legacy_text"}

    document = {
        "summary": "implemented",
        "body_markdown": "The handoff report.",
        "changed_files": ["src/example.py"],
        "checks": [{"command": "pytest", "result": "passed"}],
        "risks": [],
        "blockers": [],
        "format": "v1",
    }
    assert register_handoff_child("parent", "structured-child")
    assert (
        claim_handoff_child_result_direct(
            "parent", "structured-child", f"CAO_RESULT_V1\n{json.dumps(document)}"
        )
        is True
    )
    structured = get_delegation_result_for_assignment("structured-child")
    assert structured is not None
    assert structured["document"] == document


def test_handoff_capture_normalizes_decorated_codex_v1_without_relaxing_channel(monkeypatch):
    """The actual Codex final-block extraction retains its rendered bullet."""
    _isolated_db(monkeypatch)
    document = {
        "summary": "implemented",
        "body_markdown": "The handoff report.",
        "changed_files": ["src/example.py"],
        "checks": [{"command": "pytest", "result": "passed"}],
        "risks": [],
        "blockers": [],
        "format": "v1",
    }
    capture = (
        "\x1b[36m› [CAO Handoff] Complete the task.\x1b[0m\n"
        "\x1b[36m• CAO_RESULT_V1\x1b[0m\n"
        + "\n".join(f"  • {line}" for line in json.dumps(document, indent=2).splitlines())
        + "\n\n› \n"
    )
    extracted = CodexProvider("child", "session", "window").extract_last_message_from_script(
        capture
    )

    assert extracted.startswith("• CAO_RESULT_V1\n")
    assert register_handoff_child("parent", "decorated-child")
    assert claim_handoff_child_result_direct("parent", "decorated-child", extracted) is True
    assert get_delegation_result_for_assignment("decorated-child")["document"] == document


def test_handoff_capture_malformed_decorated_v1_fails_closed_to_legacy_text(monkeypatch):
    _isolated_db(monkeypatch)
    malformed = '• CAO_RESULT_V1\n  • {"summary": "missing closing brace"'

    assert register_handoff_child("parent", "malformed-child")
    assert claim_handoff_child_result_direct("parent", "malformed-child", malformed) is True
    result = get_delegation_result_for_assignment("malformed-child")
    assert result is not None
    assert result["document"] == {"body_markdown": malformed, "format": "legacy_text"}


def test_handoff_capture_preserves_raw_c0_inside_decorated_v1_json(monkeypatch):
    """Presentation cleanup must not repair malformed V1 JSON content."""
    _isolated_db(monkeypatch)
    document = {
        "summary": "implemented",
        "body_markdown": "The handoff report.",
        "changed_files": [],
        "checks": [],
        "risks": [],
        "blockers": [],
        "format": "v1",
    }
    valid = "• CAO_RESULT_V1\n" + "\n".join(
        f"  • {line}" for line in json.dumps(document, indent=2).splitlines()
    )
    malformed = valid.replace("implemented", "imple\x01mented")

    assert register_handoff_child("parent", "valid-decorated-child")
    assert claim_handoff_child_result_direct("parent", "valid-decorated-child", valid) is True
    assert get_delegation_result_for_assignment("valid-decorated-child")["document"] == document

    assert register_handoff_child("parent", "raw-c0-child")
    assert claim_handoff_child_result_direct("parent", "raw-c0-child", malformed) is True
    result = get_delegation_result_for_assignment("raw-c0-child")
    assert result is not None
    assert result["document"] == {"body_markdown": malformed, "format": "legacy_text"}


def test_handoff_capture_recovers_bf6f_shaped_codex_v1_footer_and_string_folds(monkeypatch):
    """The Codex completion footer permits only its known display recovery."""
    _isolated_db(monkeypatch)
    capture = """• CAO_RESULT_V1
  • {
  •   "summary": "implemented",
  •   "body_markdown": "Restored a physical fold in the handoff re-
  •     port without changing the report.",
  •   "changed_files": ["clients/database.py"],
  •   "checks": [{"command": "pytest", "result": "passed"}],
  •   "risks": [],
  •   "blockers": [],
  •   "format": "v1"
  • }
─ Worked for 14s ─────────────────────────────────────────────────────────────
"""

    assert register_handoff_child("parent", "footer-folded-child")
    assert claim_handoff_child_result_direct("parent", "footer-folded-child", capture) is True
    result = get_delegation_result_for_assignment("footer-folded-child")
    assert result is not None
    assert result["document"] == {
        "summary": "implemented",
        "body_markdown": "Restored a physical fold in the handoff re-port without changing the report.",
        "changed_files": ["clients/database.py"],
        "checks": [{"command": "pytest", "result": "passed"}],
        "risks": [],
        "blockers": [],
        "format": "v1",
    }


def test_handoff_capture_recovers_undecorated_worked_footer_and_plain_indent_folds(monkeypatch):
    """The managed Codex final capture may omit bullets but retain its footer."""
    _isolated_db(monkeypatch)
    capture = """CAO_RESULT_V1
  {"summary":"managed handoff complete","body_markdown":"The terminal re-
  wrapped this exact report.","changed_files":[],"checks":[],"risks":[],"blockers":[],"format":"v1"}
─ Worked for 3m 18s ───────────────────────────────────────────────────────────
"""

    assert register_handoff_child("parent", "undecorated-worked-footer-child")
    assert (
        claim_handoff_child_result_direct("parent", "undecorated-worked-footer-child", capture)
        is True
    )
    result = get_delegation_result_for_assignment("undecorated-worked-footer-child")
    assert result is not None
    assert result["document"] == {
        "summary": "managed handoff complete",
        "body_markdown": "The terminal re-wrapped this exact report.",
        "changed_files": [],
        "checks": [],
        "risks": [],
        "blockers": [],
        "format": "v1",
    }


def test_handoff_capture_recovers_called_plain_indent_separator_through_extraction(monkeypatch):
    """The diagnosed Called-frame capture recovers only after exact dewrapping."""
    _isolated_db(monkeypatch)
    capture = (
        "› [CAO Handoff] Complete the task.\n"
        "• Called mcp__cao_mcp_server__send_message\n"
        "  └ delivered\n"
        "• CAO_RESULT_V1\n"
        '  {"summary":"implemented","body_markdown":"Recovered a plain re-\n'
        '  port wrap.","changed_files":["clients/database.py"],"checks":[],"risks":[],"blockers":[],"format":"v1"}\n'
        "────────────────────────────────────────────────────────────────────────────────\n"
        "\n›\n"
    )
    extracted = CodexProvider("child", "session", "window").extract_last_message_from_script(
        capture
    )

    assert extracted.startswith("• CAO_RESULT_V1\n")
    assert "Called mcp__cao_mcp_server__send_message" not in extracted
    assert register_handoff_child("parent", "called-plain-indent-child")
    assert (
        claim_handoff_child_result_direct("parent", "called-plain-indent-child", extracted) is True
    )
    result = get_delegation_result_for_assignment("called-plain-indent-child")
    assert result is not None
    assert result["document"] == {
        "summary": "implemented",
        "body_markdown": "Recovered a plain re-port wrap.",
        "changed_files": ["clients/database.py"],
        "checks": [],
        "risks": [],
        "blockers": [],
        "format": "v1",
    }


def test_handoff_capture_uses_live_undecorated_last_fixture_idempotently_after_child_cancel(
    monkeypatch,
):
    """The first valid live capture remains durable across retry and child cleanup."""
    _isolated_db(monkeypatch)
    extracted = CodexProvider("child", "session", "window").extract_last_message_from_script(
        _live_undecorated_last_capture()
    )

    assert extracted.startswith("CAO_RESULT_V1\n")
    assert register_handoff_child("parent", "undecorated-last-child")
    assert claim_handoff_child_result_direct("parent", "undecorated-last-child", extracted) is True
    first = get_delegation_result_for_assignment("undecorated-last-child")
    assert first is not None
    assert first["document"]["body_markdown"] == "Recovered the exact live re-port fold."

    assert (
        claim_handoff_child_result_direct("parent", "undecorated-last-child", "CAO_RESULT_V1\n{}")
        is True
    )
    assert cancel_child_assignments_for_terminal("undecorated-last-child") == 0
    persisted = get_delegation_result(first["id"])
    assert persisted is not None
    assert persisted["document"] == first["document"]


@pytest.mark.parametrize(
    ("child_id", "payload"),
    [
        (
            "undecorated-plain-indent-malformed-child",
            '{"summary":"missing closing brace","format":"v1"',
        ),
        (
            "undecorated-unindented-child",
            '{"summary":"unindented","body_markdown":"must stay legacy.","format":"v1"}',
        ),
    ],
)
def test_handoff_capture_rejects_malformed_or_unindented_undecorated_plain_separator(
    monkeypatch, child_id, payload
):
    """Undecorated recovery remains constrained by the existing exact helper."""
    _isolated_db(monkeypatch)
    capture = (
        "CAO_RESULT_V1\n"
        f"  {payload}\n"
        "────────────────────────────────────────────────────────────────────────────────\n"
    )
    if child_id == "undecorated-unindented-child":
        capture = capture.replace(f"  {payload}", payload)

    assert register_handoff_child("parent", child_id)
    assert claim_handoff_child_result_direct("parent", child_id, capture) is True
    result = get_delegation_result_for_assignment(child_id)
    assert result is not None
    assert result["document"] == {"body_markdown": capture, "format": "legacy_text"}


@pytest.mark.parametrize(
    ("child_id", "payload"),
    [
        (
            "called-plain-indent-malformed-child",
            '{"summary":"missing closing brace","format":"v1"',
        ),
        (
            "called-unindented-child",
            '{"summary":"unindented","body_markdown":"must stay legacy.","format":"v1"}',
        ),
    ],
)
def test_handoff_capture_rejects_malformed_or_unindented_called_plain_separator(
    monkeypatch, child_id, payload
):
    _isolated_db(monkeypatch)
    capture = (
        "› [CAO Handoff] Complete the task.\n"
        "• Called mcp__cao_mcp_server__send_message\n"
        "  └ delivered\n"
        "• CAO_RESULT_V1\n"
        f"  {payload}\n"
        "────────────────────────────────────────────────────────────────────────────────\n"
        "\n›\n"
    )
    if child_id == "called-unindented-child":
        capture = capture.replace(f"  {payload}", payload)
    extracted = CodexProvider("child", "session", "window").extract_last_message_from_script(
        capture
    )

    assert register_handoff_child("parent", child_id)
    assert claim_handoff_child_result_direct("parent", child_id, extracted) is True
    result = get_delegation_result_for_assignment(child_id)
    assert result is not None
    assert result["document"] == {"body_markdown": extracted, "format": "legacy_text"}


@pytest.mark.parametrize(
    ("child_id", "capture"),
    [
        (
            "no-footer-folded-child",
            """• CAO_RESULT_V1
  • {"body_markdown": "An arbitrary physical
  •   fold must stay legacy.", "format": "v1"}
""",
        ),
        (
            "full-footer-indented-malformed-child",
            """• CAO_RESULT_V1
  • {"body_markdown": "An arbitrary physical
    fold must stay legacy.", "format": "v1"}
─ Worked for 14s ─────────────────────────────────────────────────────────────
""",
        ),
    ],
)
def test_handoff_capture_rejects_unqualified_or_arbitrary_codex_string_folds(
    monkeypatch, child_id, capture
):
    _isolated_db(monkeypatch)

    assert register_handoff_child("parent", child_id)
    assert claim_handoff_child_result_direct("parent", child_id, capture) is True
    result = get_delegation_result_for_assignment(child_id)
    assert result is not None
    assert result["document"] == {"body_markdown": capture, "format": "legacy_text"}


def test_unfinished_child_exit_is_incomplete_not_success(monkeypatch):
    _isolated_db(monkeypatch)
    assert register_child_assignment("parent", "child")
    assert cancel_child_assignments_for_terminal("child") == 1
    result = get_delegation_result_for_assignment("child")
    assert result is not None
    assert result["status"] == "incomplete"
    assert result["reason_code"] == "child_exited"


def test_assigned_result_rejects_forged_sender_without_admitted_effect(monkeypatch):
    _isolated_db(monkeypatch)
    assert register_child_assignment("parent", "child")

    with pytest.raises(PermissionError, match="admitted send_message effect"):
        create_child_assignment_result_message("child", "parent", "forged")

    assert get_delegation_result_for_assignment("child")["status"] == "awaiting"


def test_destruction_snapshot_persists_partial_before_cancellation(monkeypatch):
    _isolated_db(monkeypatch)
    assert register_child_assignment("parent", "child")

    assert persist_terminal_result_snapshot("child", "partial report")
    result = get_delegation_result_for_assignment("child")
    assert result is not None
    assert result["status"] == "incomplete"
    assert result["document"]["body_markdown"] == "partial report"


def test_handoff_returns_durable_artifact_body_not_mutable_legacy_capture(monkeypatch):
    _isolated_db(monkeypatch)
    assert register_handoff_child("parent", "child")
    assert claim_handoff_child_result_direct("parent", "child", "durable final") is True
    with database.SessionLocal() as db:
        db.query(ChildAssignmentModel).filter_by(child_terminal_id="child").update(
            {ChildAssignmentModel.direct_result_output: "tampered legacy value"}
        )
        db.commit()

    assert get_claimed_handoff_child_result_direct("parent", "child") == "durable final"


def test_retention_keeps_terminal_result_while_owning_workflow_is_open(monkeypatch):
    _isolated_db(monkeypatch)
    assert register_child_assignment("parent", "child")
    assert cancel_child_assignments_for_terminal("child") == 1
    result = get_delegation_result_for_assignment("child")
    assert result is not None
    with database.SessionLocal() as db:
        db.query(database.DelegationResultModel).filter_by(id=result["id"]).update(
            {database.DelegationResultModel.finalized_at: datetime.now() - timedelta(days=90)}
        )
        db.commit()

    assert purge_expired_delegation_results(datetime.now() - timedelta(days=30)) == 0
    assert get_delegation_result(result["id"]) is not None


def test_restart_terminalizes_awaiting_relation_with_missing_child(monkeypatch):
    _isolated_db(monkeypatch)
    assert register_child_assignment("parent", "missing-child")
    with database.SessionLocal() as db:
        db.add(
            TerminalModel(
                id="parent",
                tmux_session="cao-test",
                tmux_window="parent",
                provider="codex",
            )
        )
        db.commit()

    assert terminalize_missing_terminal_assignments_for_restart() == 1
    result = get_delegation_result_for_assignment("missing-child")
    assert result is not None
    assert result["status"] == "incomplete"
    assert result["reason_code"] == "restart_missing_child_terminal"


@pytest.mark.parametrize(
    ("transport_transition", "expected_status"),
    [
        (None, "handoff_result_queued"),
        (mark_child_assignment_result_delivered, "handoff_result_delivered"),
        (mark_child_assignment_result_failed, "handoff_result_failed"),
    ],
)
def test_restart_terminalizes_missing_child_after_handoff_result_capture_without_losing_complete_artifact(
    monkeypatch, transport_transition, expected_status
):
    """A captured handoff cannot leave cleanup retries live after child metadata is gone."""
    _isolated_db(monkeypatch)
    assert register_handoff_child("parent", "missing-child")
    with database.SessionLocal() as db:
        db.add(
            TerminalModel(
                id="parent",
                tmux_session="cao-test",
                tmux_window="parent",
                provider="codex",
            )
        )
        db.commit()

    notice, duplicate = create_handoff_child_result_message("missing-child", "durable final")
    assert notice is not None and duplicate is False
    if transport_transition is not None:
        assert transport_transition(notice.id)

    with database.SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel).filter_by(child_terminal_id="missing-child").one()
        )
        assert assignment.status == expected_status

    assert terminalize_missing_terminal_assignments_for_restart() == 1
    assert get_pending_handoff_child_terminal_ids() == []
    result = get_delegation_result_for_assignment("missing-child")
    assert result is not None
    assert result["status"] == "complete"
    assert result["document"]["body_markdown"] == "durable final"
    assert result["reason_code"] is None
    with database.SessionLocal() as db:
        assignment = (
            db.query(ChildAssignmentModel).filter_by(child_terminal_id="missing-child").one()
        )
        assert assignment.status == "cancelled"
