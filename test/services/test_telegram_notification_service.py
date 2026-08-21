"""Native Telegram settings, secret, delivery, and lifecycle regressions."""

import logging
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import cli_agent_orchestrator.clients.database as database
from cli_agent_orchestrator.clients.database import (
    Base,
    ChildAssignmentModel,
    TelegramDeliveryModel,
    TerminalModel,
    WorkflowModel,
)
from cli_agent_orchestrator.services import telegram_notification_service as telegram
from cli_agent_orchestrator.services import terminal_service

TOKEN_ONE = "123456789:abcdefghijklmnopqrstuvwxyz"
TOKEN_TWO = "987654321:zyxwvutsrqponmlkjihgfedcba"


@pytest.fixture
def telegram_state(tmp_path, monkeypatch):
    test_engine = create_engine(
        f"sqlite:///{tmp_path / 'telegram.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=test_engine)
    monkeypatch.setattr(database, "engine", test_engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=test_engine))
    monkeypatch.setattr(database, "_telegram_settings_schema_ready", False)
    monkeypatch.setattr(database, "_telegram_settings_schema_engine_identity", None)
    secret_dir = tmp_path / "secrets"
    monkeypatch.setattr(telegram, "TELEGRAM_SECRET_DIR", secret_dir)
    monkeypatch.setattr(telegram, "TELEGRAM_TOKEN_FILE", secret_dir / "telegram-bot-token")
    return test_engine


def _workflow(root: str, *, project: str | None = "Release Project") -> None:
    with database.SessionLocal() as db:
        db.add(
            TerminalModel(
                id=root,
                tmux_session=f"cao-{root}",
                tmux_window="0",
                provider="codex",
                agent_profile="supervisor_terra_medium",
                project_name=project,
                runtime_lifecycle="running",
            )
        )
        db.add(WorkflowModel(root_terminal_id=root, status="open"))
        db.commit()


def _configure(*, enabled: bool = True, thread_id: int | None = None, token: str = TOKEN_ONE):
    return telegram.update_settings(
        {
            "enabled": enabled,
            "chat_id": "-1001234567890",
            "message_thread_id": thread_id,
            "bot_token": token,
        },
        actor="operator:test",
    )


def _response(ok: bool = True, status_code: int = 200):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = {"ok": ok}
    return response


def test_disabled_and_missing_configuration_do_not_send(telegram_state, monkeypatch):
    _workflow("disabled-root")
    post = MagicMock(return_value=_response())
    monkeypatch.setattr(telegram.requests, "post", post)

    missing = telegram.dispatch_workflow_notification("disabled-root", "completed")
    assert missing == {"ok": False, "status": "disabled", "reason_code": "TELEGRAM_DISABLED"}
    assert telegram.get_settings()["configuration_state"] == "not_configured"
    assert telegram.check_connection() == {
        "ok": False,
        "status": "not_configured",
        "reason_code": "TELEGRAM_NOT_CONFIGURED",
    }
    post.assert_not_called()

    _workflow("configured-disabled")
    _configure(enabled=False)
    disabled = telegram.dispatch_workflow_notification("configured-disabled", "completed")
    assert disabled["status"] == "disabled"
    post.assert_not_called()


def test_secret_set_replace_persists_but_is_never_returned(telegram_state):
    first = _configure(enabled=True, token=TOKEN_ONE)
    assert first["token_configured"] is True
    assert "bot_token" not in first
    assert TOKEN_ONE not in repr(first)
    assert telegram.TELEGRAM_TOKEN_FILE.stat().st_mode & 0o077 == 0

    second = _configure(enabled=True, token=TOKEN_TWO)
    assert second["token_configured"] is True
    assert TOKEN_TWO not in repr(second)
    assert telegram._load_token() == TOKEN_TWO
    assert telegram.get_settings()["configuration_state"] == "enabled"


def test_failed_destination_validation_does_not_mutate_secret(telegram_state):
    with pytest.raises(ValueError, match="Configure a bot token"):
        telegram.update_settings(
            {
                "enabled": True,
                "chat_id": None,
                "message_thread_id": None,
                "bot_token": TOKEN_ONE,
            },
            actor="operator:test",
        )

    assert not telegram.TELEGRAM_TOKEN_FILE.exists()


def test_connection_api_error_is_safe_and_does_not_log_token(telegram_state, monkeypatch, caplog):
    _configure()
    monkeypatch.setattr(
        telegram.requests,
        "post",
        MagicMock(side_effect=telegram.requests.ConnectionError(f"request used {TOKEN_ONE}")),
    )
    caplog.set_level(logging.WARNING)

    result = telegram.check_connection()

    assert result == {
        "status": "failed",
        "ok": False,
        "reason_code": "TELEGRAM_NETWORK_ERROR",
    }
    assert TOKEN_ONE not in caplog.text
    assert TOKEN_ONE not in repr(result)


def test_topic_propagation_and_explicit_test_notification(telegram_state, monkeypatch):
    _configure(thread_id=77)
    post = MagicMock(return_value=_response())
    monkeypatch.setattr(telegram.requests, "post", post)

    result = telegram.send_test_notification()

    assert result == {"status": "sent", "ok": True}
    payload = post.call_args.kwargs["data"]
    assert payload["chat_id"] == "-1001234567890"
    assert payload["message_thread_id"] == 77
    assert "ThreadCells · test notification" in payload["text"]
    assert TOKEN_ONE not in repr(payload)


@pytest.mark.parametrize(
    ("root", "state", "event_kind", "message_fragment"),
    [
        ("complete-root", "terminal", "completed", "ThreadCells · completed"),
        ("owner-root", "owner_gate", "owner_attention", "ThreadCells · owner attention required"),
    ],
)
def test_top_level_transition_notifies_exactly_once(
    telegram_state, monkeypatch, root, state, event_kind, message_fragment
):
    _configure()
    _workflow(root)
    post = MagicMock(return_value=_response())
    monkeypatch.setattr(telegram.requests, "post", post)

    assert database.set_workflow_terminal_state(root, state, "safe lifecycle reason") is True
    assert database.set_workflow_terminal_state(root, state, "duplicate observation") is True

    assert post.call_count == 1
    assert message_fragment in post.call_args.kwargs["data"]["text"]
    assert "safe lifecycle reason" not in post.call_args.kwargs["data"]["text"]
    with database.SessionLocal() as db:
        row = db.get(TelegramDeliveryModel, f"workflow:1:{event_kind}")
        assert row is not None
        assert row.state == "sent"
        assert row.attempt_count == 1


def test_child_completion_never_notifies(telegram_state, monkeypatch):
    _configure()
    _workflow("parent")
    _workflow("child")
    with database.SessionLocal() as db:
        db.add(
            ChildAssignmentModel(
                parent_terminal_id="parent",
                child_terminal_id="child",
                status="awaiting_result",
            )
        )
        db.commit()
    post = MagicMock(return_value=_response())
    monkeypatch.setattr(telegram.requests, "post", post)

    assert database.set_workflow_terminal_state("child", "terminal", "child done") is True
    assert (
        telegram.dispatch_workflow_notification("child", "completed")["status"] == "child_skipped"
    )
    post.assert_not_called()


def test_assigned_child_runtime_failure_keeps_historical_child_provenance(
    telegram_state, monkeypatch
):
    _configure()
    _workflow("parent")
    _workflow("failed-child")
    with database.SessionLocal() as db:
        db.add(
            ChildAssignmentModel(
                parent_terminal_id="parent",
                child_terminal_id="failed-child",
                status="awaiting_result",
            )
        )
        db.commit()
    post = MagicMock(return_value=_response())
    monkeypatch.setattr(telegram.requests, "post", post)
    monkeypatch.setattr(terminal_service, "_runtime_death_observation", lambda *_args: True)
    monkeypatch.setattr(terminal_service.provider_manager, "cleanup_provider", MagicMock())
    monkeypatch.setattr(terminal_service, "_wake_queued_provider_execution", MagicMock())

    assert terminal_service.reconcile_terminal_runtime("failed-child") is True

    assert database.get_workflow_status("failed-child") == "cancelled"
    post.assert_not_called()


def test_completion_claim_stays_bound_to_the_transitioned_workflow(telegram_state, monkeypatch):
    _configure()
    _workflow("race-root")
    post = MagicMock(return_value=_response())
    monkeypatch.setattr(telegram.requests, "post", post)
    with database.SessionLocal() as db:
        old_workflow_id = db.query(WorkflowModel.id).scalar()

    def reopen_before_delivery(root: str, event_kind: str, workflow_id: int) -> None:
        with database.SessionLocal() as db:
            db.add(WorkflowModel(root_terminal_id=root, status="open"))
            db.commit()
        telegram.dispatch_workflow_notification(root, event_kind, workflow_id=workflow_id)

    monkeypatch.setattr(
        database, "_dispatch_workflow_notification_fail_open", reopen_before_delivery
    )

    assert database.set_workflow_terminal_state("race-root", "terminal", "done") is True

    with database.SessionLocal() as db:
        latest = (
            db.query(WorkflowModel)
            .filter(WorkflowModel.root_terminal_id == "race-root")
            .order_by(WorkflowModel.id.desc())
            .first()
        )
        assert latest is not None and latest.status == "open"
        assert db.get(TelegramDeliveryModel, f"workflow:{old_workflow_id}:completed") is not None
        assert db.get(TelegramDeliveryModel, f"workflow:{latest.id}:completed") is None
    assert post.call_count == 1


def test_closed_workflow_rewrite_cannot_emit_a_different_terminal_event(
    telegram_state, monkeypatch
):
    _configure()
    _workflow("immutable-terminal-root")
    post = MagicMock(return_value=_response())
    monkeypatch.setattr(telegram.requests, "post", post)

    assert database.set_workflow_terminal_state("immutable-terminal-root", "terminal") is True
    assert database.set_workflow_terminal_state("immutable-terminal-root", "owner_gate") is True

    assert post.call_count == 1
    with database.SessionLocal() as db:
        assert (
            db.query(TelegramDeliveryModel)
            .filter(TelegramDeliveryModel.event_kind == "owner_attention")
            .count()
            == 0
        )


def test_unexpected_top_level_runtime_failure_notifies_once(telegram_state, monkeypatch):
    _configure()
    _workflow("failed-root", project=None)
    post = MagicMock(return_value=_response())
    monkeypatch.setattr(telegram.requests, "post", post)
    monkeypatch.setattr(terminal_service, "_runtime_death_observation", lambda *_args: True)
    monkeypatch.setattr(terminal_service.provider_manager, "cleanup_provider", MagicMock())
    monkeypatch.setattr(terminal_service, "_wake_queued_provider_execution", MagicMock())

    assert terminal_service.reconcile_terminal_runtime("failed-root") is True
    assert terminal_service.reconcile_terminal_runtime("failed-root") is True

    assert post.call_count == 1
    assert "ThreadCells · failed" in post.call_args.kwargs["data"]["text"]
    assert database.get_workflow_status("failed-root") == "cancelled"


def test_completion_winning_before_cancellation_suppresses_failure(telegram_state, monkeypatch):
    _configure()
    _workflow("completion-race-root", project=None)
    post = MagicMock(return_value=_response())
    monkeypatch.setattr(telegram.requests, "post", post)
    monkeypatch.setattr(terminal_service, "_runtime_death_observation", lambda *_args: True)
    monkeypatch.setattr(terminal_service.provider_manager, "cleanup_provider", MagicMock())
    monkeypatch.setattr(terminal_service, "_wake_queued_provider_execution", MagicMock())
    original_cancel = terminal_service.cancel_workflows_for_terminal_with_ids

    def complete_then_cancel(root: str) -> list[int]:
        assert database.set_workflow_terminal_state(root, "terminal", "completion won")
        return original_cancel(root)

    monkeypatch.setattr(
        terminal_service, "cancel_workflows_for_terminal_with_ids", complete_then_cancel
    )

    assert terminal_service.reconcile_terminal_runtime("completion-race-root") is True

    assert post.call_count == 1
    assert "ThreadCells · completed" in post.call_args.kwargs["data"]["text"]
    with database.SessionLocal() as db:
        assert (
            db.query(TelegramDeliveryModel)
            .filter(TelegramDeliveryModel.event_kind == "failed")
            .count()
            == 0
        )


def test_failure_claim_stays_bound_when_a_new_workflow_opens_after_cancellation(
    telegram_state, monkeypatch
):
    _configure()
    _workflow("failed-race-root", project=None)
    post = MagicMock(return_value=_response())
    monkeypatch.setattr(telegram.requests, "post", post)
    monkeypatch.setattr(terminal_service, "_runtime_death_observation", lambda *_args: True)
    monkeypatch.setattr(terminal_service.provider_manager, "cleanup_provider", MagicMock())
    monkeypatch.setattr(terminal_service, "_wake_queued_provider_execution", MagicMock())
    original_cancel = terminal_service.cancel_workflows_for_terminal_with_ids
    with database.SessionLocal() as db:
        old_workflow_id = db.query(WorkflowModel.id).scalar()

    def cancel_and_reopen(root: str) -> list[int]:
        cancelled = original_cancel(root)
        with database.SessionLocal() as db:
            db.add(WorkflowModel(root_terminal_id=root, status="open"))
            db.commit()
        return cancelled

    monkeypatch.setattr(
        terminal_service, "cancel_workflows_for_terminal_with_ids", cancel_and_reopen
    )

    assert terminal_service.reconcile_terminal_runtime("failed-race-root") is True

    with database.SessionLocal() as db:
        latest = (
            db.query(WorkflowModel)
            .filter(WorkflowModel.root_terminal_id == "failed-race-root")
            .order_by(WorkflowModel.id.desc())
            .first()
        )
        assert latest is not None and latest.status == "open"
        assert db.get(TelegramDeliveryModel, f"workflow:{old_workflow_id}:failed") is not None
        assert db.get(TelegramDeliveryModel, f"workflow:{latest.id}:failed") is None
    assert post.call_count == 1


def test_api_rejection_is_fail_open_and_duplicate_recovery_does_not_spam(
    telegram_state, monkeypatch
):
    _configure()
    _workflow("rejected-root")
    post = MagicMock(return_value=_response(ok=False, status_code=401))
    monkeypatch.setattr(telegram.requests, "post", post)

    first = telegram.dispatch_workflow_notification("rejected-root", "owner_attention")
    duplicate = telegram.dispatch_workflow_notification("rejected-root", "owner_attention")

    assert first == {"status": "failed", "ok": False, "reason_code": "TELEGRAM_API_REJECTED"}
    assert duplicate == {"ok": True, "status": "duplicate_skipped"}
    assert post.call_count == 1
    assert database.get_workflow_status("rejected-root") == "open"
