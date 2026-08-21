"""Installation-global Telegram notifications driven by durable ThreadCells state."""

from __future__ import annotations

import logging
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

import requests

from cli_agent_orchestrator.clients.database import (
    claim_telegram_delivery,
    finish_telegram_delivery,
)
from cli_agent_orchestrator.clients.database import get_telegram_settings as get_persisted_settings
from cli_agent_orchestrator.clients.database import (
    get_workflow_notification_context,
    record_telegram_result,
)
from cli_agent_orchestrator.clients.database import (
    update_telegram_settings as persist_telegram_settings,
)
from cli_agent_orchestrator.constants import CAO_HOME_DIR

logger = logging.getLogger(__name__)

TELEGRAM_SECRET_DIR = CAO_HOME_DIR / "secrets"
TELEGRAM_TOKEN_FILE = TELEGRAM_SECRET_DIR / "telegram-bot-token"
TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_TIMEOUT = (3.05, 5.0)
EVENT_KINDS = {"completed", "owner_attention", "failed"}


class TelegramSecretError(RuntimeError):
    """The private token is missing or does not satisfy the local file boundary."""


def _validate_token(token: str) -> str:
    value = token.strip()
    if not 20 <= len(value) <= 512 or any(character.isspace() for character in value):
        raise ValueError("Telegram bot token is invalid")
    return value


def _validate_destination(chat_id: Optional[str]) -> Optional[str]:
    if chat_id is None or not chat_id.strip():
        return None
    value = chat_id.strip()
    if len(value) > 128 or any(ord(character) < 33 or ord(character) == 127 for character in value):
        raise ValueError("Telegram chat ID is invalid")
    return value


def _validate_thread_id(message_thread_id: Optional[int]) -> Optional[int]:
    if message_thread_id is None:
        return None
    if isinstance(message_thread_id, bool) or not 1 <= message_thread_id <= 2_147_483_647:
        raise ValueError("Telegram topic/thread ID must be a positive integer")
    return message_thread_id


def _ensure_secret_directory() -> None:
    if TELEGRAM_SECRET_DIR.exists():
        if TELEGRAM_SECRET_DIR.is_symlink() or not TELEGRAM_SECRET_DIR.is_dir():
            raise TelegramSecretError("Telegram secret storage is unsafe")
    else:
        TELEGRAM_SECRET_DIR.mkdir(parents=True, mode=0o700)
    os.chmod(TELEGRAM_SECRET_DIR, 0o700)


def _write_token(token: str) -> None:
    value = _validate_token(token)
    _ensure_secret_directory()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".telegram-token-", dir=TELEGRAM_SECRET_DIR
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, TELEGRAM_TOKEN_FILE)
        os.chmod(TELEGRAM_TOKEN_FILE, 0o600)
        directory_fd = os.open(TELEGRAM_SECRET_DIR, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def _clear_token() -> None:
    """Remove the configured credential without following an unsafe path."""
    _ensure_secret_directory()
    try:
        metadata = TELEGRAM_TOKEN_FILE.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISDIR(metadata.st_mode):
        raise TelegramSecretError("Telegram token storage is unsafe")
    TELEGRAM_TOKEN_FILE.unlink()
    directory_fd = os.open(TELEGRAM_SECRET_DIR, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _load_token() -> str:
    try:
        metadata = TELEGRAM_TOKEN_FILE.lstat()
    except FileNotFoundError as exc:
        raise TelegramSecretError("Telegram bot token is not configured") from exc
    if TELEGRAM_TOKEN_FILE.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise TelegramSecretError("Telegram token storage is unsafe")
    if metadata.st_mode & 0o077 or metadata.st_uid != os.geteuid():
        raise TelegramSecretError("Telegram token storage permissions are unsafe")
    try:
        return _validate_token(TELEGRAM_TOKEN_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise TelegramSecretError("Telegram bot token is unavailable") from exc


def _token_state() -> str:
    if not TELEGRAM_TOKEN_FILE.exists():
        return "missing"
    try:
        _load_token()
    except TelegramSecretError:
        return "invalid"
    return "configured"


def get_settings() -> dict[str, Any]:
    settings = get_persisted_settings()
    token_state = _token_state()
    if token_state == "invalid":
        configuration_state = "invalid"
    elif token_state != "configured" or not settings.get("chat_id"):
        configuration_state = "not_configured"
    elif settings.get("enabled"):
        configuration_state = "enabled"
    else:
        configuration_state = "disabled"
    return {
        **settings,
        "token_configured": token_state == "configured",
        "token_state": token_state,
        "configuration_state": configuration_state,
    }


def update_settings(values: Mapping[str, Any], *, actor: str) -> dict[str, Any]:
    if not actor or len(actor) > 120:
        raise ValueError("Telegram settings actor is required")
    required_keys = {"enabled", "chat_id", "message_thread_id", "bot_token"}
    allowed_keys = required_keys | {"clear_bot_token"}
    if not required_keys.issubset(values) or not set(values).issubset(allowed_keys):
        raise ValueError("Telegram settings payload is invalid")
    enabled = values["enabled"]
    if not isinstance(enabled, bool):
        raise ValueError("Telegram enabled must be a boolean")
    chat_id = _validate_destination(values.get("chat_id"))
    thread_id = _validate_thread_id(values.get("message_thread_id"))
    token = values.get("bot_token")
    clear_token = values.get("clear_bot_token", False)
    if not isinstance(clear_token, bool):
        raise ValueError("Telegram token clear flag must be a boolean")
    token_value: Optional[str] = None
    if token is not None:
        if not isinstance(token, str):
            raise ValueError("Telegram bot token is invalid")
        token_value = _validate_token(token)
    if clear_token and token_value is not None:
        raise ValueError("Replace or clear the Telegram bot token, not both")
    if clear_token and enabled:
        raise ValueError("Disable Telegram notifications before clearing the bot token")
    if enabled and chat_id is None:
        raise ValueError("Configure a bot token and chat ID before enabling Telegram")
    if enabled and token_value is None and _token_state() != "configured":
        raise ValueError("Configure a bot token and chat ID before enabling Telegram")
    if clear_token:
        _clear_token()
    elif token_value is not None:
        _write_token(token_value)
    persist_telegram_settings(
        enabled=enabled,
        chat_id=chat_id,
        message_thread_id=thread_id,
    )
    return get_settings()


def _telegram_request(
    method: str, token: str, payload: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    try:
        response = requests.post(
            f"{TELEGRAM_API_BASE}/bot{token}/{method}",
            data=payload or {},
            timeout=TELEGRAM_TIMEOUT,
        )
        response_payload = response.json()
    except (requests.RequestException, ValueError):
        return {"ok": False, "reason_code": "TELEGRAM_NETWORK_ERROR"}
    if (
        response.status_code != 200
        or not isinstance(response_payload, dict)
        or response_payload.get("ok") is not True
    ):
        return {"ok": False, "reason_code": "TELEGRAM_API_REJECTED"}
    return {"ok": True}


def check_connection() -> dict[str, Any]:
    try:
        token = _load_token()
    except TelegramSecretError:
        record_telegram_result("not_configured")
        return {"ok": False, "status": "not_configured", "reason_code": "TELEGRAM_NOT_CONFIGURED"}
    result = _telegram_request("getMe", token)
    record_telegram_result("connection_ok" if result["ok"] else "connection_failed")
    return {"status": "connected" if result["ok"] else "failed", **result}


def _send_message(text: str, *, require_enabled: bool) -> dict[str, Any]:
    settings = get_settings()
    if require_enabled and not settings["enabled"]:
        return {"ok": False, "status": "disabled", "reason_code": "TELEGRAM_DISABLED"}
    if settings["token_state"] != "configured" or not settings.get("chat_id"):
        return {"ok": False, "status": "not_configured", "reason_code": "TELEGRAM_NOT_CONFIGURED"}
    try:
        token = _load_token()
    except TelegramSecretError:
        return {"ok": False, "status": "not_configured", "reason_code": "TELEGRAM_NOT_CONFIGURED"}
    payload: dict[str, Any] = {"chat_id": settings["chat_id"], "text": text}
    if settings.get("message_thread_id") is not None:
        payload["message_thread_id"] = settings["message_thread_id"]
    result = _telegram_request("sendMessage", token, payload)
    return {"status": "sent" if result["ok"] else "failed", **result}


def send_test_notification() -> dict[str, Any]:
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result = _send_message(
        f"ThreadCells · test notification\nConfiguration is working.\nTime: {timestamp}",
        require_enabled=False,
    )
    if result["status"] == "not_configured":
        record_telegram_result("not_configured")
    else:
        record_telegram_result("test_sent" if result["ok"] else "test_failed")
    return result


def _safe_label(value: Any, fallback: str) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "")).strip()
    return normalized[:96] or fallback


def _lifecycle_message(event_kind: str, context: Mapping[str, Any]) -> str:
    labels = {
        "completed": ("completed", "Top-level work completed successfully."),
        "owner_attention": ("owner attention required", "The workflow requires an owner decision."),
        "failed": ("failed", "The top-level agent runtime exited unexpectedly."),
    }
    state, summary = labels[event_kind]
    lines = [
        f"ThreadCells · {state}",
        f"Session: {_safe_label(context.get('session_name'), 'Unknown session')}",
    ]
    if context.get("project_name"):
        lines.append(f"Project: {_safe_label(context['project_name'], 'Unknown project')}")
    lines.extend(
        [
            f"Summary: {summary}",
            f"Time: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        ]
    )
    return "\n".join(lines)


def dispatch_workflow_notification(
    root_terminal_id: str, event_kind: str, *, workflow_id: Optional[int] = None
) -> dict[str, Any]:
    """Attempt one fail-open, idempotent notification for a top-level lifecycle event."""
    if event_kind not in EVENT_KINDS:
        raise ValueError("Invalid Telegram workflow event")
    context = get_workflow_notification_context(root_terminal_id, workflow_id)
    if context is None:
        return {"ok": False, "status": "workflow_missing"}
    if context["delegated_child"]:
        return {"ok": False, "status": "child_skipped"}
    event_key = f"workflow:{context['workflow_id']}:{event_kind}"
    if not claim_telegram_delivery(
        event_key=event_key,
        event_kind=event_kind,
        workflow_id=context["workflow_id"],
        root_terminal_id=root_terminal_id,
    ):
        return {"ok": True, "status": "duplicate_skipped"}

    result = _send_message(_lifecycle_message(event_kind, context), require_enabled=True)
    if result["ok"]:
        finish_telegram_delivery(event_key, "sent")
    elif result["status"] in {"disabled", "not_configured"}:
        finish_telegram_delivery(event_key, "skipped", result.get("reason_code"))
    else:
        finish_telegram_delivery(event_key, "failed", result.get("reason_code"))
        logger.warning(
            "Telegram lifecycle delivery failed for event %s (%s)",
            event_kind,
            result.get("reason_code", "TELEGRAM_DELIVERY_FAILED"),
        )
    return result
