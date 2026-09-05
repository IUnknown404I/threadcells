"""Real libtmux coverage for exact session/window presence classification."""

import re
import shutil
import subprocess
import time
import uuid

import pytest

from cli_agent_orchestrator.clients.tmux import TmuxClient, _BoundedTmuxServer


def _wait_for_authoritative_server_absence(
    tmux_binary: str, socket_name: str, session_name: str
) -> None:
    """Wait out tmux's shutdown transition before asserting inventory absence."""
    deadline = time.monotonic() + 5
    result = None
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            result = subprocess.run(
                [
                    tmux_binary,
                    "-L",
                    socket_name,
                    "-f",
                    "/dev/null",
                    "has-session",
                    "-t",
                    f"={session_name}",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=remaining,
            )
        except subprocess.TimeoutExpired:
            pytest.fail("tmux absence probe exceeded its five-second deadline")
        stderr = result.stderr.strip()
        if result.returncode == 1 and (
            re.fullmatch(r"no server running on .+", stderr)
            or re.fullmatch(
                r"error connecting to .+ \((?:No such file or directory|Connection refused)\)",
                stderr,
            )
        ):
            return
        time.sleep(min(0.01, max(0, deadline - time.monotonic())))
    pytest.fail(
        "tmux server did not reach authoritative absence: "
        f"returncode={getattr(result, 'returncode', None)!r} "
        f"stderr={getattr(result, 'stderr', None)!r}"
    )


def test_authoritative_server_absence_wait_fails_explicitly_on_timeout(monkeypatch):
    def timeout_probe(*_args, **kwargs):
        assert 0 < kwargs["timeout"] <= 5
        raise subprocess.TimeoutExpired(["tmux", "has-session"], kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout_probe)

    with pytest.raises(pytest.fail.Exception, match="five-second deadline"):
        _wait_for_authoritative_server_absence("tmux", "private-socket", "target")


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
        _wait_for_authoritative_server_absence(tmux_binary, socket_name, session_name)
        assert client.session_exists(session_name) is False
        assert client.window_exists(session_name, window_name) is False
    finally:
        subprocess.run(
            [tmux_binary, "-L", socket_name, "kill-server"],
            check=False,
            capture_output=True,
            text=True,
        )
