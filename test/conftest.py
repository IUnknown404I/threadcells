"""Global test isolation from the canonical CAO runtime state."""

import os
import tempfile

# This file is loaded before test modules import CAO constants/database
# singletons. Every automated test process therefore gets an isolated home,
# database, logs, attachments, and managed-worktree root. E2E calls to an
# already-running external server remain external and do not use these paths.
_TEST_CAO_HOME = tempfile.TemporaryDirectory(prefix="cao-pytest-state-")
os.environ["CAO_HOME_DIR"] = _TEST_CAO_HOME.name

# Tests that mock ``init_db`` during application startup still need a complete
# isolated schema for unmocked background reconciliation paths.
from cli_agent_orchestrator.clients.database import init_db  # noqa: E402

init_db()
