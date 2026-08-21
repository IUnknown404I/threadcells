"""Operator verifier filesystem-boundary tests."""

import json
import os
import stat
from types import SimpleNamespace

import pytest

from cli_agent_orchestrator.services import operator_auth_service


def test_verifier_parent_owned_by_service_is_rejected(monkeypatch):
    monkeypatch.setattr(operator_auth_service.os, "geteuid", lambda: 123)
    monkeypatch.setattr(
        operator_auth_service.os,
        "lstat",
        lambda _path: SimpleNamespace(st_mode=stat.S_IFDIR | 0o750, st_uid=123),
    )
    with pytest.raises(
        operator_auth_service.OperatorAuthUnavailable,
        match="controlled by a distinct OS principal",
    ):
        operator_auth_service._validate_operator_verifier_parent_chain(
            operator_auth_service.Path("/safe/verifier.json")
        )


def test_verifier_parent_chain_rejects_group_writes(monkeypatch):
    monkeypatch.setattr(
        operator_auth_service.os,
        "lstat",
        lambda _path: SimpleNamespace(st_mode=stat.S_IFDIR | 0o770, st_uid=os.geteuid() + 1),
    )

    with pytest.raises(
        operator_auth_service.OperatorAuthUnavailable,
        match="controlled by a distinct OS principal",
    ):
        operator_auth_service._validate_operator_verifier_parent_chain(
            operator_auth_service.Path("/safe/verifier.json")
        )


def test_verifier_reference_rejects_symlink_even_when_target_is_valid(tmp_path, monkeypatch):
    target = tmp_path / "canonical.json"
    target.write_text(
        json.dumps(operator_auth_service.build_operator_verifier("A7!qz")), encoding="utf-8"
    )
    target.chmod(0o440)
    reference = tmp_path / "linked.json"
    reference.symlink_to(target)
    monkeypatch.setenv(operator_auth_service.OPERATOR_VERIFIER_FILE_ENV, str(reference))
    monkeypatch.setattr(
        operator_auth_service, "_validate_operator_verifier_parent_chain", lambda _path: None
    )

    with pytest.raises(
        operator_auth_service.OperatorAuthUnavailable, match="reference must be canonical"
    ):
        operator_auth_service.load_operator_verifier()
