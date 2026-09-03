"""Real libtmux coverage for exact session/window presence classification."""

import shutil
import subprocess
import uuid

import pytest

from cli_agent_orchestrator.clients.tmux import TmuxClient, _BoundedTmuxServer


@pytest.mark.integration
def test_real_libtmux_presence_distinguishes_absence_from_healthy_inventory():
    tmux_binary = shutil.which("tmux")
    if tmux_binary is None:
        pytest.skip("tmux is not installed")

    socket_name = f"cao-presence-{uuid.uuid4().hex}"
    session_name = f"presence-{uuid.uuid4().hex[:8]}"
    sentinel_name = f"sentinel-{uuid.uuid4().hex[:8]}"
    window_name = "present"
    client = TmuxClient()
    client.server = _BoundedTmuxServer(socket_name=socket_name, config_file="/dev/null")

    try:
        subprocess.run(
            [
                tmux_binary,
                "-L",
                socket_name,
                "-f",
                "/dev/null",
                "new-session",
                "-d",
                "-s",
                session_name,
                "-n",
                window_name,
                "sleep 30",
            ],
            check=True,
        )
        subprocess.run(
            [
                tmux_binary,
                "-L",
                socket_name,
                "-f",
                "/dev/null",
                "new-session",
                "-d",
                "-s",
                sentinel_name,
                "sleep 30",
            ],
            check=True,
        )

        assert client.session_exists(session_name) is True
        assert client.window_exists(session_name, window_name) is True
        assert client.window_exists(session_name, "missing") is False

        subprocess.run(
            [tmux_binary, "-L", socket_name, "kill-session", "-t", session_name],
            check=True,
        )
        assert client.session_exists(session_name) is False
        assert client.window_exists(session_name, window_name) is False

        subprocess.run(
            [tmux_binary, "-L", socket_name, "kill-server"],
            check=True,
        )
        assert client.session_exists(session_name) is None
        assert client.window_exists(session_name, window_name) is None
    finally:
        subprocess.run(
            [tmux_binary, "-L", socket_name, "kill-server"],
            check=False,
            capture_output=True,
            text=True,
        )
