"""OS-provisioned operator authentication and exceptional launch grants.

The server never reads or stores the operator's plaintext secret.  An operator
provisions a scrypt verifier in a file owned by a different OS principal (or by
root).  Agent runtimes may be able to read that verifier on a shared host, but
cannot turn it into the high-entropy secret accepted by this boundary and must
not be able to replace it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import stat
import uuid
from pathlib import Path
from typing import Any, Mapping

from cli_agent_orchestrator.clients.database import issue_owner_launch_grant

OPERATOR_VERIFIER_FILE_ENV = "THREADCELLS_OPERATOR_VERIFIER_FILE"
LEGACY_OPERATOR_VERIFIER_FILE_ENV = "THREADMESH_OPERATOR_VERIFIER_FILE"
OPERATOR_SECRET_MIN_CHARACTERS = 5
OPERATOR_SECRET_MAX_CHARACTERS = 4096
# Scrub the removed plaintext-reference name as well during rolling upgrades.
LEGACY_OPERATOR_SECRET_FILE_ENV = "THREADMESH_OPERATOR_SECRET_FILE"
XHIGH_CONFIRMATION = "LAUNCH critical_sol_xhigh_owner"
_VERIFIER_SCHEMA_VERSION = 1
_SCRYPT_N = 1 << 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_DIGEST_BYTES = 32


class OperatorAuthUnavailable(RuntimeError):
    """The server has no safe operator-authentication authority."""


class OperatorAuthenticationError(RuntimeError):
    """Supplied operator credentials or explicit confirmation are invalid."""


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: object, *, expected_length: int) -> bytes:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise OperatorAuthUnavailable("operator verifier encoding is invalid")
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise OperatorAuthUnavailable("operator verifier encoding is invalid") from exc
    if len(decoded) != expected_length:
        raise OperatorAuthUnavailable("operator verifier length is invalid")
    return decoded


def build_operator_verifier(secret: str, *, salt: bytes | None = None) -> dict[str, Any]:
    """Build a public verifier document without retaining the plaintext secret."""
    try:
        encoded = secret.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise ValueError("operator secret must be UTF-8 text") from exc
    if not OPERATOR_SECRET_MIN_CHARACTERS <= len(secret) <= OPERATOR_SECRET_MAX_CHARACTERS:
        raise ValueError("operator secret must contain 5 to 4096 characters")
    salt = salt or os.urandom(_SALT_BYTES)
    if len(salt) != _SALT_BYTES:
        raise ValueError("operator verifier salt must contain 16 bytes")
    digest = hashlib.scrypt(
        encoded,
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_DIGEST_BYTES,
    )
    return {
        "schema_version": _VERIFIER_SCHEMA_VERSION,
        "algorithm": "scrypt",
        "n": _SCRYPT_N,
        "r": _SCRYPT_R,
        "p": _SCRYPT_P,
        "salt": _b64encode(salt),
        "digest": _b64encode(digest),
    }


def _read_operator_verifier_file(path: Path) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise OperatorAuthUnavailable("operator verifier is unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise OperatorAuthUnavailable("operator verifier is not a regular file")
        # If the non-root server principal owns this file, an arbitrary child
        # running as that same principal can replace it after chmod(2).  A
        # root/operator-owned, non-group/world-writable file is the required
        # distinct OS provisioning boundary.
        if (metadata.st_uid == os.geteuid() and os.geteuid() != 0) or metadata.st_mode & 0o022:
            raise OperatorAuthUnavailable(
                "operator verifier must be provisioned by a distinct OS principal"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            payload = handle.read(4097)
    finally:
        os.close(descriptor)
    if len(payload) > 4096:
        raise OperatorAuthUnavailable("operator verifier is too large")
    return payload, metadata


def _validate_operator_verifier_parent_chain(path: Path) -> None:
    """Require every parent to preserve the distinct-principal boundary.

    File ownership alone is insufficient when the service account can rename
    the file through a writable parent directory. A canonical root-owned (or
    otherwise distinct-principal-owned) directory chain prevents replacement
    between authentication requests.
    """
    effective_uid = os.geteuid()
    for parent in (path.parent, *path.parent.parents):
        try:
            metadata = os.lstat(parent)
        except OSError as exc:
            raise OperatorAuthUnavailable("operator verifier parent is unavailable") from exc
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise OperatorAuthUnavailable("operator verifier parent is unsafe")
        if (effective_uid != 0 and metadata.st_uid == effective_uid) or metadata.st_mode & 0o022:
            raise OperatorAuthUnavailable(
                "operator verifier parent must be controlled by a distinct OS principal"
            )


def load_operator_verifier() -> dict[str, Any]:
    """Load and validate one immutable, OS-provisioned scrypt verifier."""
    # Prefer the public ThreadCells contract. The retired name is a bounded
    # read-only bridge for an already deployed verifier reference; no current
    # documentation or generated configuration emits it.
    reference = os.environ.get(OPERATOR_VERIFIER_FILE_ENV) or os.environ.get(
        LEGACY_OPERATOR_VERIFIER_FILE_ENV
    )
    if not reference:
        raise OperatorAuthUnavailable("operator authentication is not configured")
    path = Path(reference)
    if not path.is_absolute() or path.name == ".env" or path.suffix.lower() == ".pem":
        raise OperatorAuthUnavailable("operator verifier reference is invalid")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise OperatorAuthUnavailable("operator verifier is unavailable") from exc
    if resolved != path:
        raise OperatorAuthUnavailable("operator verifier reference must be canonical")
    _validate_operator_verifier_parent_chain(path)
    payload, _metadata = _read_operator_verifier_file(path)
    try:
        document = json.loads(payload.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OperatorAuthUnavailable("operator verifier document is invalid") from exc
    expected_keys = {"schema_version", "algorithm", "n", "r", "p", "salt", "digest"}
    if not isinstance(document, dict) or set(document) != expected_keys:
        raise OperatorAuthUnavailable("operator verifier document is invalid")
    if (
        document["schema_version"] != _VERIFIER_SCHEMA_VERSION
        or document["algorithm"] != "scrypt"
        or document["n"] != _SCRYPT_N
        or document["r"] != _SCRYPT_R
        or document["p"] != _SCRYPT_P
    ):
        raise OperatorAuthUnavailable("operator verifier parameters are unsupported")
    _b64decode(document["salt"], expected_length=_SALT_BYTES)
    _b64decode(document["digest"], expected_length=_DIGEST_BYTES)
    return document


def authenticate_operator_secret(provided: str) -> None:
    """Verify an operator secret against the OS-owned verifier."""
    try:
        candidate = provided.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise OperatorAuthenticationError("operator authentication failed") from exc
    if not OPERATOR_SECRET_MIN_CHARACTERS <= len(provided) <= OPERATOR_SECRET_MAX_CHARACTERS:
        raise OperatorAuthenticationError("operator authentication failed")
    verifier = load_operator_verifier()
    salt = _b64decode(verifier["salt"], expected_length=_SALT_BYTES)
    expected = _b64decode(verifier["digest"], expected_length=_DIGEST_BYTES)
    actual = hashlib.scrypt(
        candidate,
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_DIGEST_BYTES,
    )
    if not hmac.compare_digest(expected, actual):
        raise OperatorAuthenticationError("operator authentication failed")


def mint_xhigh_launch_grant(
    *,
    auth_identity: str,
    agent_profile: str,
    provider: str,
    canonical_worktree: str,
    requested_session_name: str | None,
    confirmation: str,
    owner_grant_required: bool,
    grant_scope: Mapping[str, Any] | None = None,
) -> dict[str, str | int]:
    """Issue one exact manual launch capability after explicit confirmation."""
    if not owner_grant_required or confirmation != f"LAUNCH {agent_profile}":
        raise OperatorAuthenticationError("explicit XHigh confirmation is required")
    if not canonical_worktree.startswith("/"):
        raise ValueError("canonical_worktree must be absolute")
    launch_id = uuid.uuid4().hex
    ttl_seconds = 60
    token = issue_owner_launch_grant(
        launch_id=launch_id,
        agent_profile=agent_profile,
        provider=provider,
        canonical_worktree=canonical_worktree,
        requested_session_name=requested_session_name,
        issued_by=auth_identity,
        ttl_seconds=ttl_seconds,
        grant_scope=grant_scope,
    )
    return {"grant": token, "launch_id": launch_id, "expires_in_seconds": ttl_seconds}
