"""Exact Session-owned terminal artifact cleanup tests."""

from __future__ import annotations

import pytest

from cli_agent_orchestrator.services import terminal_artifact_service


@pytest.fixture
def artifact_roots(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    attachments = tmp_path / "attachments"
    logs.mkdir()
    attachments.mkdir()
    monkeypatch.setattr(terminal_artifact_service, "TERMINAL_LOG_DIR", logs)
    monkeypatch.setattr(terminal_artifact_service, "TERMINAL_ATTACHMENTS_DIR", attachments)
    return logs, attachments


def test_purges_exact_logs_indexes_temps_and_attachments_idempotently(artifact_roots):
    logs, attachments = artifact_roots
    terminal_id = "abcd1234"
    other_id = "dcba4321"
    owned_logs = [
        f"{terminal_id}.log",
        f"{terminal_id}.log.gz",
        f"{terminal_id}.log.tci",
        f"{terminal_id}.log.tcd",
        f"{terminal_id}.log.output-index.lock",
        f".{terminal_id}.0123456789abcdef.tci",
        f".{terminal_id}.0123456789abcdef.tcd",
        f".{terminal_id}.log.compression-temp",
    ]
    for name in owned_logs:
        (logs / name).write_bytes(b"owned")
    unrelated_log = logs / f"{other_id}.log"
    unrelated_log.write_bytes(b"other")

    terminal_dir = attachments / terminal_id
    terminal_dir.mkdir()
    (terminal_dir / "image.png").write_bytes(b"image")
    outside = attachments / "outside.txt"
    outside.write_bytes(b"outside")
    (terminal_dir / "outside-link").symlink_to(outside)
    other_dir = attachments / other_id
    other_dir.mkdir()
    (other_dir / "keep.txt").write_bytes(b"other")

    result = terminal_artifact_service.purge_session_terminal_artifacts([terminal_id])

    assert result == {
        "runtime_artifacts_absent": True,
        "terminals": [
            {
                "terminal_id": terminal_id,
                "logs_removed": len(owned_logs),
                "attachments_removed": 2,
            }
        ],
    }
    assert not terminal_dir.exists()
    assert unrelated_log.read_bytes() == b"other"
    assert (other_dir / "keep.txt").read_bytes() == b"other"
    assert outside.read_bytes() == b"outside"

    repeated = terminal_artifact_service.purge_session_terminal_artifacts([terminal_id])
    assert repeated["runtime_artifacts_absent"] is True
    assert repeated["terminals"][0]["logs_removed"] == 0
    assert repeated["terminals"][0]["attachments_removed"] == 0


def test_rejects_unsafe_log_identity_without_following_it(artifact_roots):
    logs, _attachments = artifact_roots
    outside = logs.parent / "outside.log"
    outside.write_bytes(b"preserve")
    (logs / "abcd1234.log").symlink_to(outside)

    with pytest.raises(
        terminal_artifact_service.TerminalArtifactCleanupError,
        match="TERMINAL_LOG_IDENTITY_UNSAFE",
    ):
        terminal_artifact_service.purge_session_terminal_artifacts(["abcd1234"])

    assert outside.read_bytes() == b"preserve"


def test_rejects_attachment_directory_symlink_without_following_it(artifact_roots):
    _logs, attachments = artifact_roots
    outside = attachments.parent / "outside"
    outside.mkdir()
    (outside / "preserve.txt").write_bytes(b"preserve")
    (attachments / "abcd1234").symlink_to(outside, target_is_directory=True)

    with pytest.raises(
        terminal_artifact_service.TerminalArtifactCleanupError,
        match="TERMINAL_ATTACHMENT_DIRECTORY_UNSAFE",
    ):
        terminal_artifact_service.purge_session_terminal_artifacts(["abcd1234"])

    assert (outside / "preserve.txt").read_bytes() == b"preserve"


def test_rejects_symlinked_shared_root_without_following_it(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "abcd1234.log").write_bytes(b"preserve")
    linked_logs = tmp_path / "linked-logs"
    linked_logs.symlink_to(outside, target_is_directory=True)
    attachments = tmp_path / "attachments"
    attachments.mkdir()
    monkeypatch.setattr(terminal_artifact_service, "TERMINAL_LOG_DIR", linked_logs)
    monkeypatch.setattr(terminal_artifact_service, "TERMINAL_ATTACHMENTS_DIR", attachments)

    with pytest.raises(
        terminal_artifact_service.TerminalArtifactCleanupError,
        match="TERMINAL_ARTIFACT_ROOT_UNSAFE",
    ):
        terminal_artifact_service.purge_session_terminal_artifacts(["abcd1234"])

    assert (outside / "abcd1234.log").read_bytes() == b"preserve"


def test_inventory_bounds_and_terminal_identity_fail_closed(artifact_roots, monkeypatch):
    logs, _attachments = artifact_roots
    (logs / "unrelated.log").write_bytes(b"other")
    monkeypatch.setattr(terminal_artifact_service, "_LOG_INVENTORY_LIMIT", 0)

    with pytest.raises(
        terminal_artifact_service.TerminalArtifactCleanupError,
        match="TERMINAL_LOG_INVENTORY_UNBOUNDED",
    ):
        terminal_artifact_service.purge_session_terminal_artifacts(["abcd1234"])

    with pytest.raises(
        terminal_artifact_service.TerminalArtifactCleanupError,
        match="TERMINAL_ARTIFACT_IDENTITY_INVALID",
    ):
        terminal_artifact_service.purge_session_terminal_artifacts(["../escape"])
