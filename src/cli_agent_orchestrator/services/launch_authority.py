"""Server-owned classification for exceptional manual launch authority."""

from __future__ import annotations

from typing import Any, Mapping

PRIVILEGED_PROFILE_IDS = frozenset({"critical_sol_xhigh_owner"})

STRUCTURED_OWNER_AUTHORIZATION_INSTRUCTION = """\
THREADCELLS STRUCTURED OWNER AUTHORIZATION
The server atomically consumed a short-lived, one-use owner grant for this exact
profile revision, provider configuration revision, launch topology, project,
and canonical worktree. The XHigh owner gate is satisfied for this terminal
only. This attestation contains no reusable credential and grants no child or
delegated terminal authority. Do not require the compatibility magic text.
"""


def requires_owner_launch_grant(
    profile: Mapping[str, Any] | object, *, trusted_builtin: bool = False
) -> bool:
    """Apply the server-owned launch policy to one resolved immutable revision.

    The document supplies capability facts; it cannot redefine this predicate.
    Callers must pass registry-resolved data rather than request or prompt text.
    """
    model_dump = getattr(profile, "model_dump", None)
    if callable(model_dump):
        document = model_dump(mode="python")
    elif isinstance(profile, Mapping):
        document = dict(profile)
    else:
        raise TypeError("resolved profile must be a model or mapping")
    authority = document.get("authority")
    authority = dict(authority) if isinstance(authority, Mapping) else {}
    codex = document.get("codexConfig")
    codex = dict(codex) if isinstance(codex, Mapping) else {}
    allowed_tools = document.get("allowed_tools")
    if allowed_tools is None:
        allowed_tools = document.get("allowedTools")
    if allowed_tools is None:
        allowed_tools = document.get("tools")
    return bool(
        document.get("owner_authorization_required")
        or authority.get("owner_authorization_required")
        or document.get("execution_mode") == "owner_executor"
        or authority.get("execution_mode") == "owner_executor"
        or document.get("reasoning_level") == "xhigh"
        or codex.get("model_reasoning_effort") == "xhigh"
        or (
            not trusted_builtin
            and (
                authority.get("sandbox_mode") == "danger-full-access"
                or codex.get("sandbox_mode") == "danger-full-access"
            )
        )
        or authority.get("unrestricted_tools_authorized") is True
        or (isinstance(allowed_tools, list) and "*" in allowed_tools)
    )


def is_privileged_profile(agent_profile: str) -> bool:
    """Retain the built-in compatibility fail-safe for pre-registry callers."""
    return agent_profile in PRIVILEGED_PROFILE_IDS
