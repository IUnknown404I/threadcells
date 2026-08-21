"""tmux 3.6 smoke coverage for the shared logical history capture boundary."""

import re
import shlex
import shutil
import subprocess
import time
import uuid

import pytest


def _soft_wrap_boundary_wire() -> tuple[str, str]:
    payload = "0123456789abcdef" * 20 + "TAIL"
    wire = (
        "CAO_RESULT_V1\n"
        '{"summary":"soft-wrap-boundary","body_markdown":"'
        + payload
        + '","changed_files":[],"checks":[],"risks":[],"blockers":[],"format":"v1"}'
    )
    assert len(payload.encode()) == 324
    assert len(wire.encode()) == 459
    return payload, wire


@pytest.mark.integration
def test_tmux_36_80_column_capture_joins_soft_wraps_but_keeps_v1_hard_newline():
    tmux_binary = shutil.which("tmux")
    if tmux_binary is None:
        pytest.skip("tmux is not installed")

    version = subprocess.run([tmux_binary, "-V"], capture_output=True, text=True, check=True).stdout
    match = re.search(r"(\d+)\.(\d+)", version)
    if match is None or tuple(map(int, match.groups())) < (3, 6):
        pytest.skip("requires tmux 3.6+")

    socket_name = f"cao-logical-{uuid.uuid4().hex}"
    session_name = f"logical-{uuid.uuid4().hex[:8]}"
    target = f"{session_name}:0"
    payload, wire = _soft_wrap_boundary_wire()
    shell_command = (
        "printf '%b\\n%s\\n' "
        + shlex.quote("\\033[36m• CAO_RESULT_V1\\033[0m")
        + " "
        + shlex.quote(wire.split("\n", 1)[1])
        + "; exec sleep 10"
    )

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
                "-x",
                "80",
                "-y",
                "24",
                "-s",
                session_name,
                shell_command,
            ],
            check=True,
        )
        deadline = time.monotonic() + 4
        history = ""
        while time.monotonic() < deadline:
            history = subprocess.run(
                [
                    tmux_binary,
                    "-L",
                    socket_name,
                    "capture-pane",
                    "-e",
                    "-p",
                    "-J",
                    "-S",
                    "-200",
                    "-t",
                    target,
                ],
                capture_output=True,
                check=True,
                text=True,
            ).stdout
            if "\x1b[36m• CAO_RESULT_V1" in history:
                break
            time.sleep(0.05)

        assert "\x1b[36m• CAO_RESULT_V1" in history
        logical_history = re.sub(r"\x1b\[[0-9;]*m", "", history)
        assert f"• CAO_RESULT_V1\n{wire.split(chr(10), 1)[1]}" in logical_history
        assert payload in logical_history
        assert f"{payload[:80]}\n" not in logical_history
    finally:
        subprocess.run(
            [tmux_binary, "-L", socket_name, "kill-server"],
            check=False,
            capture_output=True,
            text=True,
        )
