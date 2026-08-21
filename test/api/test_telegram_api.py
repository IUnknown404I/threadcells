"""Safe global Telegram HTTP contract regressions."""

from unittest.mock import patch

SAFE_SETTINGS = {
    "schema_version": 1,
    "enabled": True,
    "chat_id": "-1001234567890",
    "message_thread_id": 77,
    "token_configured": True,
    "token_state": "configured",
    "configuration_state": "enabled",
    "last_result": None,
    "last_result_at": None,
    "updated_at": "2026-08-21T12:00:00",
}


def test_read_contract_never_returns_the_bot_token(client):
    with patch(
        "cli_agent_orchestrator.api.main.telegram_notification_service.get_settings",
        return_value=SAFE_SETTINGS,
    ):
        response = client.get("/api/v1/telegram")

    assert response.status_code == 200
    assert response.json() == SAFE_SETTINGS
    assert "bot_token" not in response.text


def test_operator_can_set_or_replace_token_without_response_reflection(client):
    token = "123456789:abcdefghijklmnopqrstuvwxyz"
    payload = {
        "enabled": True,
        "chat_id": "-1001234567890",
        "message_thread_id": 77,
        "bot_token": token,
    }
    with (
        patch(
            "cli_agent_orchestrator.api.main._require_operator",
            return_value="operator_session:test",
        ),
        patch(
            "cli_agent_orchestrator.api.main.telegram_notification_service.update_settings",
            return_value=SAFE_SETTINGS,
        ) as update,
    ):
        response = client.put("/api/v1/telegram", json=payload)

    assert response.status_code == 200
    assert token not in response.text
    update.assert_called_once_with(
        {**payload, "clear_bot_token": False}, actor="operator_session:test"
    )


def test_operator_can_clear_token_without_response_reflection(client):
    cleared = {
        **SAFE_SETTINGS,
        "enabled": False,
        "token_configured": False,
        "token_state": "missing",
        "configuration_state": "not_configured",
    }
    payload = {
        "enabled": False,
        "chat_id": "-1001234567890",
        "message_thread_id": 77,
        "bot_token": None,
        "clear_bot_token": True,
    }
    with (
        patch(
            "cli_agent_orchestrator.api.main._require_operator",
            return_value="operator_session:test",
        ),
        patch(
            "cli_agent_orchestrator.api.main.telegram_notification_service.update_settings",
            return_value=cleared,
        ) as update,
    ):
        response = client.put("/api/v1/telegram", json=payload)

    assert response.status_code == 200
    assert response.json() == cleared
    assert "bot_token" not in response.text
    update.assert_called_once_with(payload, actor="operator_session:test")


def test_check_and_test_are_explicit_operator_actions(client):
    with (
        patch(
            "cli_agent_orchestrator.api.main._require_operator",
            return_value="operator_session:test",
        ) as authorize,
        patch(
            "cli_agent_orchestrator.api.main.telegram_notification_service.check_connection",
            return_value={"ok": True, "status": "connected"},
        ) as check,
        patch(
            "cli_agent_orchestrator.api.main.telegram_notification_service.send_test_notification",
            return_value={"ok": True, "status": "sent"},
        ) as send,
    ):
        checked = client.post("/api/v1/telegram/check")
        sent = client.post("/api/v1/telegram/test")

    assert checked.json() == {"ok": True, "status": "connected"}
    assert sent.json() == {"ok": True, "status": "sent"}
    assert authorize.call_count == 2
    check.assert_called_once_with()
    send.assert_called_once_with()


def test_invalid_settings_fail_without_reflecting_secret(client):
    token = "123456789:abcdefghijklmnopqrstuvwxyz"
    with (
        patch(
            "cli_agent_orchestrator.api.main._require_operator",
            return_value="operator_session:test",
        ),
        patch(
            "cli_agent_orchestrator.api.main.telegram_notification_service.update_settings",
            side_effect=ValueError("Configure a bot token and chat ID before enabling Telegram"),
        ),
    ):
        response = client.put(
            "/api/v1/telegram",
            json={
                "enabled": True,
                "chat_id": None,
                "message_thread_id": None,
                "bot_token": token,
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"]["reason_code"] == "VALIDATION_FAILED"
    assert token not in response.text


def test_invalid_token_shape_is_rejected_without_response_reflection(client):
    token = "short-secret"
    with patch(
        "cli_agent_orchestrator.api.main._require_operator",
        return_value="operator_session:test",
    ):
        response = client.put(
            "/api/v1/telegram",
            json={
                "enabled": False,
                "chat_id": None,
                "message_thread_id": None,
                "bot_token": token,
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "reason_code": "VALIDATION_FAILED",
        "message": "Telegram bot token is invalid",
    }
    assert token not in response.text


def test_malformed_token_type_is_rejected_without_response_reflection(client):
    token = "123456789:abcdefghijklmnopqrstuvwxyz"
    with patch(
        "cli_agent_orchestrator.api.main._require_operator",
        return_value="operator_session:test",
    ):
        response = client.put(
            "/api/v1/telegram",
            json={
                "enabled": False,
                "chat_id": None,
                "message_thread_id": None,
                "bot_token": {"value": token},
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "reason_code": "VALIDATION_FAILED",
        "message": "Telegram bot token is invalid",
    }
    assert token not in response.text


def test_missing_required_field_never_reflects_the_secret_body(client):
    token = "123456789:abcdefghijklmnopqrstuvwxyz"
    response = client.put(
        "/api/v1/telegram",
        json={
            "chat_id": "-1001234567890",
            "message_thread_id": None,
            "bot_token": token,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == [
        {
            "type": "value_error",
            "loc": ["body"],
            "msg": "Invalid Telegram settings request",
        }
    ]
    assert token not in response.text
