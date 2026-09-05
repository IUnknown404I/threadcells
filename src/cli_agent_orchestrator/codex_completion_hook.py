"""Synchronous managed Codex completed-response persistence hook."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import requests

from cli_agent_orchestrator.constants import API_BASE_URL
from cli_agent_orchestrator.runtime_generation import (
    ACTIVE_RUNTIME_GENERATION,
    RUNTIME_GENERATION_ENV,
    RUNTIME_GENERATION_HEADER,
)

_TERMINAL_ID_PATTERN = re.compile(r"^[a-f0-9]{8}$")
_SESSION_ID_PATTERN = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
_TERMINAL_RUNTIME_GENERATION_PATTERN = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
_MAX_INPUT_BYTES = 8 * 1024 * 1024
_MAX_RESPONSE_CHARACTERS = 4 * 1024 * 1024
_CONTINUE_OUTPUT: dict[str, object] = {"continue": True}
_FAIL_CLOSED_OUTPUT: dict[str, object] = {
    "continue": False,
    "stopReason": "ThreadCells could not persist the completed Codex response.",
}


def _payload() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
    if len(raw) > _MAX_INPUT_BYTES:
        raise ValueError("hook input is too large")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("hook input is not an object")
    return value


def _validated_request(value: dict[str, Any]) -> tuple[str, str, dict[str, str]]:
    terminal_id = os.environ.get("CAO_TERMINAL_ID", "")
    token = os.environ.get("CAO_TERMINAL_AUTH_TOKEN", "")
    runtime_generation = os.environ.get(RUNTIME_GENERATION_ENV, "")
    session_id = value.get("session_id")
    transcript_path = value.get("transcript_path")
    cwd = value.get("cwd")
    turn_id = value.get("turn_id")
    response = value.get("last_assistant_message")
    if (
        value.get("hook_event_name") != "Stop"
        or not isinstance(value.get("stop_hook_active"), bool)
        or not isinstance(session_id, str)
        or _SESSION_ID_PATTERN.fullmatch(session_id) is None
        or not isinstance(transcript_path, str)
        or not Path(transcript_path).is_absolute()
        or len(transcript_path) > 4096
        or not isinstance(cwd, str)
        or not Path(cwd).is_absolute()
        or len(cwd) > 4096
        or not isinstance(turn_id, str)
        or not 1 <= len(turn_id) <= 256
        or not isinstance(response, str)
        or not response.strip()
        or len(response) > _MAX_RESPONSE_CHARACTERS
        or _TERMINAL_ID_PATTERN.fullmatch(terminal_id) is None
        or not token
        or _TERMINAL_RUNTIME_GENERATION_PATTERN.fullmatch(runtime_generation) is None
    ):
        raise ValueError("hook completion is malformed")
    body = {
        "session_id": session_id,
        "transcript_path": transcript_path,
        "cwd": cwd,
        "turn_id": turn_id,
        "last_assistant_message": response,
        "runtime_generation": runtime_generation,
    }
    return terminal_id, token, body


def main() -> int:
    """Persist a bounded response without exposing capabilities or server detail."""
    output = _FAIL_CLOSED_OUTPUT
    try:
        terminal_id, token, body = _validated_request(_payload())
        response = requests.post(
            f"{API_BASE_URL}/_internal/terminals/{terminal_id}/codex-turn-complete",
            json=body,
            headers={
                "Authorization": f"Bearer {token}",
                RUNTIME_GENERATION_HEADER: ACTIVE_RUNTIME_GENERATION,
            },
            timeout=20.0,
        )
        if response.status_code != 200:
            raise RuntimeError("completion persistence was rejected")
        result = response.json()
        if (
            not isinstance(result, dict)
            or result.get("session_id") != body["session_id"]
            or not isinstance(result.get("completion_offset"), int)
            or result["completion_offset"] < 0
        ):
            raise RuntimeError("completion persistence response was invalid")
        output = _CONTINUE_OUTPUT
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError, requests.RequestException):
        pass
    sys.stdout.write(json.dumps(output, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
