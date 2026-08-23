"""Synchronous managed Codex session-identity binding hook."""

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
_RUNTIME_GENERATION_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_INPUT_BYTES = 64 * 1024
_STOP_OUTPUT = {
    "continue": False,
    "stopReason": "ThreadCells could not bind the managed Codex session identity.",
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
    source = value.get("source")
    if (
        value.get("hook_event_name") != "SessionStart"
        or source not in {"startup", "resume"}
        or not isinstance(session_id, str)
        or _SESSION_ID_PATTERN.fullmatch(session_id) is None
        or not isinstance(transcript_path, str)
        or not Path(transcript_path).is_absolute()
        or not isinstance(cwd, str)
        or not Path(cwd).is_absolute()
        or _TERMINAL_ID_PATTERN.fullmatch(terminal_id) is None
        or not token
        or _RUNTIME_GENERATION_PATTERN.fullmatch(runtime_generation) is None
    ):
        raise ValueError("hook identity is malformed")
    body = {
        "session_id": session_id,
        "transcript_path": transcript_path,
        "cwd": cwd,
        "source": source,
        "runtime_generation": runtime_generation,
    }
    return terminal_id, token, body


def main() -> int:
    """Bind or stop Codex without printing capabilities or server detail."""
    try:
        terminal_id, token, body = _validated_request(_payload())
        response = requests.post(
            f"{API_BASE_URL}/_internal/terminals/{terminal_id}/codex-session-identity",
            json=body,
            headers={
                "Authorization": f"Bearer {token}",
                RUNTIME_GENERATION_HEADER: ACTIVE_RUNTIME_GENERATION,
            },
            timeout=20.0,
        )
        if response.status_code != 200:
            raise RuntimeError("identity binding was rejected")
        result = response.json()
        if not isinstance(result, dict) or result.get("session_id") != body["session_id"]:
            raise RuntimeError("identity binding response was invalid")
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError, requests.RequestException):
        sys.stdout.write(json.dumps(_STOP_OUTPUT, separators=(",", ":")))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
