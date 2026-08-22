"""Process-level proof that a first tmux server retains no terminal credential."""

import shutil
import subprocess
import uuid
from pathlib import Path

import libtmux
import pytest

from cli_agent_orchestrator.clients.tmux import TmuxClient


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")
def test_first_tmux_server_argv_is_credential_free(tmp_path):
    if not Path("/proc").is_dir():
        pytest.skip("process command-line inventory requires /proc")

    socket_name = f"cao-secret-bootstrap-{uuid.uuid4().hex}"
    session_name = f"cao-secret-session-{uuid.uuid4().hex}"
    terminal_token = f"terminal-secret-sentinel-{uuid.uuid4().hex}"
    client = TmuxClient()
    client.server = libtmux.Server(socket_name=socket_name, config_file="/dev/null")

    try:
        client.create_session(
            session_name=session_name,
            window_name="agent",
            terminal_id="terminal-secret-bootstrap",
            terminal_auth_token=terminal_token,
            runtime_generation="generation-secret-bootstrap",
            working_directory=str(tmp_path),
        )
        server_pid = subprocess.run(
            ["tmux", "-L", socket_name, "display-message", "-p", "#{pid}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        command_line = Path(f"/proc/{int(server_pid)}/cmdline").read_bytes()
        server_environment = Path(f"/proc/{int(server_pid)}/environ").read_bytes()

        assert b"cao-bootstrap-" in command_line
        assert b"CAO_TERMINAL_AUTH_TOKEN" not in command_line
        assert terminal_token.encode() not in command_line
        assert b"terminal-secret-bootstrap" not in command_line
        assert b"generation-secret-bootstrap" not in command_line
        assert b"CAO_TERMINAL_AUTH_TOKEN" not in server_environment
        assert terminal_token.encode() not in server_environment
        assert b"CAO_TERMINAL_ID" not in server_environment
        assert b"CAO_RUNTIME_GENERATION" not in server_environment
    finally:
        subprocess.run(
            ["tmux", "-L", socket_name, "kill-server"],
            check=False,
            capture_output=True,
            text=True,
        )
