import os
from pathlib import Path

from cli_agent_orchestrator.constants import CAO_HOME_DIR, DATABASE_FILE, TERMINAL_LOG_DIR


def test_automated_tests_use_isolated_cao_state():
    isolated = Path(os.environ["CAO_HOME_DIR"]).resolve()
    assert CAO_HOME_DIR.resolve() == isolated
    assert DATABASE_FILE.resolve().is_relative_to(isolated)
    assert TERMINAL_LOG_DIR.resolve().is_relative_to(isolated)
    assert not isolated.is_relative_to(Path("/srv/agent-control/state/cao"))
