"""Synchronous managed Codex identity and compaction-authority hook."""

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
# The hook runs inside the managed tmux pane and therefore inherits the
# terminal launch generation, not the installed service compatibility hash
# used by the HTTP header below. Terminal generations are UUIDs minted at the
# exact pane/process launch boundary.
_TERMINAL_RUNTIME_GENERATION_PATTERN = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
_RESUME_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{40,64}$")
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
        or source not in {"startup", "resume", "compact"}
        or not isinstance(session_id, str)
        or _SESSION_ID_PATTERN.fullmatch(session_id) is None
        or not isinstance(transcript_path, str)
        or not Path(transcript_path).is_absolute()
        or not isinstance(cwd, str)
        or not Path(cwd).is_absolute()
        or _TERMINAL_ID_PATTERN.fullmatch(terminal_id) is None
        or not token
        or _TERMINAL_RUNTIME_GENERATION_PATTERN.fullmatch(runtime_generation) is None
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


def _compaction_output(result: dict[str, Any], terminal_id: str, body: dict[str, str]) -> str:
    """Build hidden developer context from one exact server-owned authority."""
    authority = result.get("continuation_authority")
    if authority is None:
        return ""
    if not isinstance(authority, dict):
        raise RuntimeError("continuation authority response was invalid")
    workflow_id = authority.get("workflow_id")
    logical_turn_id = authority.get("logical_turn_id")
    resume_token = authority.get("resume_token")
    if (
        authority.get("authority_version") != 1
        or not isinstance(workflow_id, int)
        or isinstance(workflow_id, bool)
        or workflow_id <= 0
        or not isinstance(logical_turn_id, int)
        or isinstance(logical_turn_id, bool)
        or logical_turn_id <= 0
        or not isinstance(resume_token, str)
        or _RESUME_TOKEN_PATTERN.fullmatch(resume_token) is None
        or authority.get("receiver_terminal_id") != terminal_id
        or authority.get("runtime_generation") != body["runtime_generation"]
    ):
        raise RuntimeError("continuation authority response was invalid")
    structured_authority = json.dumps(
        {
            "authority_version": 1,
            "claim_required": False,
            "logical_turn_id": logical_turn_id,
            "resume_token": resume_token,
            "workflow_id": workflow_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    additional_context = (
        "[ThreadCells admitted execution authority]\n"
        f"CAO_WORKFLOW_CONTINUATION_V1={structured_authority}\n"
        "Codex compacted only the model context. This workflow execution remains already "
        "admitted; do not make a fresh claim_workflow_turn_receipt call for this "
        f"continuation. Its current logical_turn_id is {logical_turn_id}. Use that exact ID "
        "for any privileged CAO operation. Completed or indeterminate privileged effects "
        "remain durably fenced and must not be replayed. If this admitted execution is later "
        "interrupted before the workflow is complete, resume it exactly once using the current "
        "logical_turn_id and resume_token in the structured authority above. A later explicit "
        "CAO workflow input with a different logical-turn supersedes this restored pair. Treat "
        "the resume token as a privileged bearer and never copy it into normal output or logs."
    )
    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": additional_context,
            }
        },
        separators=(",", ":"),
    )


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
        if body["source"] == "compact":
            sys.stdout.write(_compaction_output(result, terminal_id, body))
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError, requests.RequestException):
        sys.stdout.write(json.dumps(_STOP_OUTPUT, separators=(",", ":")))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
