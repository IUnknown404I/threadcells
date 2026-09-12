"""Global test isolation from the canonical CAO runtime state."""

import json
import os
import tempfile
from pathlib import Path

import pytest

# This file is loaded before test modules import CAO constants/database
# singletons. Every automated test process therefore gets an isolated home,
# database, logs, attachments, and managed-worktree root. E2E calls to an
# already-running external server remain external and do not use these paths.
_TEST_CAO_HOME = tempfile.TemporaryDirectory(prefix="cao-pytest-state-")
os.environ["CAO_HOME_DIR"] = _TEST_CAO_HOME.name

# Operational flock paths are deployment-global and intentionally live outside
# CAO_HOME.  Inherited host configuration must never make tests contend with a
# running control plane (or, worse, acquire its production admission fences).
# Override only the lock namespace; the loader still supplies every other
# canonical default from the packaged policy.
_TEST_OPERATIONS_CONFIG = Path(_TEST_CAO_HOME.name) / "cao-operations.json"
_TEST_OPERATIONS_CONFIG.write_text(
    json.dumps({"lock_dir": str(Path(_TEST_CAO_HOME.name) / "locks")}),
    encoding="utf-8",
)
os.environ["CAO_OPERATIONS_CONFIG"] = str(_TEST_OPERATIONS_CONFIG)


@pytest.fixture
def short_unix_socket_path():
    """Provide an AF_UNIX path below Linux's fixed sockaddr_un limit."""
    with tempfile.TemporaryDirectory(prefix="cao-pytest-socket-") as root:
        yield Path(root) / "full-cleanup.sock"


# Tests that mock ``init_db`` during application startup still need a complete
# isolated schema for unmocked background reconciliation paths.
from cli_agent_orchestrator.clients.database import init_db  # noqa: E402

init_db()
