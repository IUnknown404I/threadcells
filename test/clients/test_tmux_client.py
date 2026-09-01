"""Tests for TmuxClient methods (mocked libtmux — no real tmux required)."""

import os
from unittest.mock import MagicMock, call, patch

import pytest

from cli_agent_orchestrator.clients.tmux import (
    TMUX_COMMAND_TIMEOUT_SECONDS,
    TmuxCommandTimeout,
    _BoundedTmuxServer,
)


@pytest.fixture
def tmux():
    """Create a TmuxClient with a mocked libtmux.Server."""
    with patch("cli_agent_orchestrator.clients.tmux.libtmux") as mock_libtmux:
        mock_server = MagicMock()
        mock_libtmux.Server.return_value = mock_server

        from cli_agent_orchestrator.clients.tmux import TmuxClient

        client = TmuxClient()
        client.server = mock_server
        client._start_credential_free_bootstrap = MagicMock(return_value="cao-bootstrap-test")
        yield client


def test_tmux_server_control_commands_have_a_secret_safe_deadline():
    server = _BoundedTmuxServer(socket_name="bounded-test", config_file="/dev/null")
    expired = __import__("subprocess").TimeoutExpired(["tmux"], 10)
    with (
        patch("cli_agent_orchestrator.clients.tmux.shutil.which", return_value="/usr/bin/tmux"),
        patch("cli_agent_orchestrator.clients.tmux.subprocess.run", side_effect=expired) as run,
        pytest.raises(TmuxCommandTimeout, match="exceeded 10 seconds") as raised,
    ):
        server.cmd("list-sessions")

    assert "list-sessions" not in str(raised.value)
    assert run.call_args.kwargs["timeout"] == TMUX_COMMAND_TIMEOUT_SECONDS


# ── _resolve_and_validate_working_directory ──────────────────────────


class TestResolveAndValidateWorkingDirectory:
    def test_defaults_to_cwd(self, tmux, tmp_path):
        with patch("os.getcwd", return_value=str(tmp_path)):
            result = tmux._resolve_and_validate_working_directory(None)
        assert result == os.path.realpath(str(tmp_path))

    def test_valid_directory(self, tmux, tmp_path):
        result = tmux._resolve_and_validate_working_directory(str(tmp_path))
        assert result == os.path.realpath(str(tmp_path))

    def test_blocked_root(self, tmux):
        with pytest.raises(ValueError, match="blocked system path"):
            tmux._resolve_and_validate_working_directory("/")

    def test_blocked_etc(self, tmux):
        with pytest.raises(ValueError, match="blocked system path"):
            tmux._resolve_and_validate_working_directory("/etc")

    def test_nonexistent_directory(self, tmux):
        with pytest.raises(ValueError, match="does not exist"):
            tmux._resolve_and_validate_working_directory("/nonexistent/dir/xyz")


# ── create_session ───────────────────────────────────────────────────


class TestCreateSession:
    def test_create_session_success(self, tmux, tmp_path):
        mock_window = MagicMock()
        mock_window.name = "my-window"
        mock_session = MagicMock()
        mock_session.windows = [mock_window]
        tmux.server.new_session.return_value = mock_session

        result = tmux.create_session("ses", "my-window", "tid1", str(tmp_path))

        assert result == "my-window"
        tmux._start_credential_free_bootstrap.assert_called_once_with(str(tmp_path))
        tmux.server.new_session.assert_called_once()
        assert tmux.server.new_session.call_args.kwargs["session_name"] == "ses"
        tmux.server.cmd.assert_called_once_with("kill-session", "-t", "cao-bootstrap-test")

    def test_create_session_bootstraps_tmux_without_terminal_credentials(
        self, tmux, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("CAO_TERMINAL_AUTH_TOKEN", "parent-secret")
        mock_window = MagicMock(name="window")
        mock_window.name = "my-window"
        mock_session = MagicMock(windows=[mock_window])
        tmux.server.new_session.return_value = mock_session

        tmux.create_session(
            "ses",
            "my-window",
            "tid1",
            str(tmp_path),
            terminal_auth_token="child-secret",
            runtime_generation="generation-1",
        )

        tmux._start_credential_free_bootstrap.assert_called_once_with(str(tmp_path))
        environment = tmux.server.new_session.call_args.kwargs["environment"]
        assert environment["CAO_TERMINAL_AUTH_TOKEN"] == "child-secret"

    def test_bootstrap_client_uses_a_minimal_environment(self, tmux, tmp_path, monkeypatch):
        monkeypatch.setenv("CAO_TERMINAL_AUTH_TOKEN", "parent-secret")
        monkeypatch.setenv("CAO_TERMINAL_ID", "parent-terminal")
        monkeypatch.setenv("CAO_RUNTIME_GENERATION", "parent-generation")
        monkeypatch.setenv("PROVIDER_SECRET", "provider-secret")
        tmux.server.socket_path = None
        tmux.server.socket_name = "safe-bootstrap"
        tmux.server.config_file = "/dev/null"

        with patch("cli_agent_orchestrator.clients.tmux.subprocess.run") as run:
            from cli_agent_orchestrator.clients.tmux import TmuxClient

            bootstrap = TmuxClient._start_credential_free_bootstrap(tmux, str(tmp_path))

        command = run.call_args.args[0]
        environment = run.call_args.kwargs["env"]
        assert bootstrap.startswith("cao-bootstrap-")
        assert command[1:5] == ["-L", "safe-bootstrap", "-f", "/dev/null"]
        assert bootstrap in command
        assert environment
        assert not any(key.startswith("CAO_") for key in environment)
        assert "PROVIDER_SECRET" not in environment

    def test_bootstrap_failure_creates_no_real_session(self, tmux, tmp_path):
        tmux._start_credential_free_bootstrap.side_effect = RuntimeError("bootstrap failed")

        with pytest.raises(RuntimeError, match="bootstrap failed"):
            tmux.create_session("ses", "w", "tid1", str(tmp_path))

        tmux.server.new_session.assert_not_called()

    def test_create_session_never_injects_operator_secret_reference(
        self, tmux, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("THREADCELLS_OPERATOR_SECRET_FILE", "/private/operator.secret")
        monkeypatch.setenv("THREADCELLS_OPERATOR_VERIFIER_FILE", "/private/operator.json")
        monkeypatch.setenv("THREADMESH_OPERATOR_SECRET_FILE", "/private/legacy.secret")
        monkeypatch.setenv("THREADMESH_OPERATOR_VERIFIER_FILE", "/private/legacy.json")
        mock_window = MagicMock(name="window")
        mock_window.name = "my-window"
        mock_session = MagicMock(windows=[mock_window])
        tmux.server.new_session.return_value = mock_session

        tmux.create_session("ses", "my-window", "tid1", str(tmp_path))

        environment = tmux.server.new_session.call_args.kwargs["environment"]
        assert "THREADCELLS_OPERATOR_SECRET_FILE" not in environment
        assert "THREADCELLS_OPERATOR_VERIFIER_FILE" not in environment
        assert "THREADMESH_OPERATOR_SECRET_FILE" not in environment
        assert "THREADMESH_OPERATOR_VERIFIER_FILE" not in environment

    def test_create_session_window_name_none(self, tmux, tmp_path):
        mock_window = MagicMock()
        mock_window.name = None
        mock_session = MagicMock()
        mock_session.windows = [mock_window]
        tmux.server.new_session.return_value = mock_session

        with pytest.raises(ValueError, match="Window name is None"):
            tmux.create_session("ses", "w", "tid1", str(tmp_path))

    def test_create_session_raises_on_failure(self, tmux, tmp_path):
        tmux.server.new_session.side_effect = Exception("tmux error")

        with pytest.raises(Exception, match="tmux error"):
            tmux.create_session("ses", "w", "tid1", str(tmp_path))


# ── create_window ────────────────────────────────────────────────────


class TestCreateWindow:
    def test_create_window_success(self, tmux, tmp_path):
        mock_window = MagicMock()
        mock_window.name = "agent-window"
        mock_session = MagicMock()
        mock_session.new_window.return_value = mock_window
        tmux.server.sessions.get.return_value = mock_session

        result = tmux.create_window("ses", "agent-window", "tid2", str(tmp_path))

        assert result == "agent-window"
        assert tmux.server.cmd.call_args_list == [
            call("set-environment", "-t", "ses", "-u", "THREADCELLS_OPERATOR_SECRET_FILE"),
            call("set-environment", "-t", "ses", "-u", "THREADCELLS_OPERATOR_VERIFIER_FILE"),
            call("set-environment", "-t", "ses", "-u", "THREADMESH_OPERATOR_SECRET_FILE"),
            call("set-environment", "-t", "ses", "-u", "THREADMESH_OPERATOR_VERIFIER_FILE"),
        ]

    def test_create_window_session_not_found(self, tmux, tmp_path):
        tmux.server.sessions.get.return_value = None

        with pytest.raises(ValueError, match="not found"):
            tmux.create_window("nonexistent", "w", "tid2", str(tmp_path))

    def test_create_window_name_none(self, tmux, tmp_path):
        mock_window = MagicMock()
        mock_window.name = None
        mock_session = MagicMock()
        mock_session.new_window.return_value = mock_window
        tmux.server.sessions.get.return_value = mock_session

        with pytest.raises(ValueError, match="Window name is None"):
            tmux.create_window("ses", "w", "tid2", str(tmp_path))


# ── send_keys ────────────────────────────────────────────────────────


class TestSendKeys:
    @patch("cli_agent_orchestrator.clients.tmux.time")
    @patch("cli_agent_orchestrator.clients.tmux.subprocess")
    def test_send_keys_success(self, mock_subprocess, mock_time, tmux):
        mock_subprocess.run.return_value = MagicMock(returncode=0)
        tmux.send_keys("ses", "win", "hello", enter_count=1)

        # load-buffer, paste-buffer, send-keys Enter, delete-buffer
        assert mock_subprocess.run.call_count == 4

    @patch("cli_agent_orchestrator.clients.tmux.time")
    @patch("cli_agent_orchestrator.clients.tmux.subprocess")
    def test_send_keys_multiple_enters(self, mock_subprocess, mock_time, tmux):
        mock_subprocess.run.return_value = MagicMock(returncode=0)
        tmux.send_keys("ses", "win", "hello", enter_count=3)

        # load-buffer + paste-buffer + 3 send-keys Enter + delete-buffer = 6
        assert mock_subprocess.run.call_count == 6

    @patch("cli_agent_orchestrator.clients.tmux.time")
    @patch("cli_agent_orchestrator.clients.tmux.subprocess")
    def test_send_keys_raises_on_failure(self, mock_subprocess, mock_time, tmux):
        mock_subprocess.run.side_effect = Exception("tmux send failed")

        with pytest.raises(Exception, match="tmux send failed"):
            tmux.send_keys("ses", "win", "hello")


# ── send_keys_via_paste ──────────────────────────────────────────────


class TestSendKeysViaPaste:
    @patch("cli_agent_orchestrator.clients.tmux.time")
    def test_send_keys_via_paste_success(self, mock_time, tmux):
        mock_pane = MagicMock()
        mock_window = MagicMock()
        mock_window.active_pane = mock_pane
        mock_session = MagicMock()
        mock_session.windows.get.return_value = mock_window
        tmux.server.sessions.get.return_value = mock_session

        tmux.send_keys_via_paste("ses", "win", "hello")

        tmux.server.cmd.assert_any_call("set-buffer", "-b", "cao_paste", "hello")
        mock_pane.cmd.assert_called_once_with("paste-buffer", "-p", "-b", "cao_paste")
        mock_pane.send_keys.assert_called_once_with("C-m", enter=False)

    @patch("cli_agent_orchestrator.clients.tmux.time")
    def test_send_keys_via_paste_session_not_found(self, mock_time, tmux):
        tmux.server.sessions.get.return_value = None

        with pytest.raises(ValueError, match="not found"):
            tmux.send_keys_via_paste("nonexistent", "win", "hello")

    @patch("cli_agent_orchestrator.clients.tmux.time")
    def test_send_keys_via_paste_window_not_found(self, mock_time, tmux):
        mock_session = MagicMock()
        mock_session.windows.get.return_value = None
        tmux.server.sessions.get.return_value = mock_session

        with pytest.raises(ValueError, match="not found"):
            tmux.send_keys_via_paste("ses", "nonexistent", "hello")


# ── send_special_key ─────────────────────────────────────────────────


class TestSendSpecialKey:
    def test_send_special_key_success(self, tmux):
        mock_pane = MagicMock()
        mock_window = MagicMock()
        mock_window.active_pane = mock_pane
        mock_session = MagicMock()
        mock_session.windows.get.return_value = mock_window
        tmux.server.sessions.get.return_value = mock_session

        tmux.send_special_key("ses", "win", "C-d")

        mock_pane.send_keys.assert_called_once_with("C-d", enter=False)

    def test_send_special_key_session_not_found(self, tmux):
        tmux.server.sessions.get.return_value = None

        with pytest.raises(ValueError, match="not found"):
            tmux.send_special_key("nonexistent", "win", "C-d")

    def test_send_special_key_window_not_found(self, tmux):
        mock_session = MagicMock()
        mock_session.windows.get.return_value = None
        tmux.server.sessions.get.return_value = mock_session

        with pytest.raises(ValueError, match="not found"):
            tmux.send_special_key("ses", "nonexistent", "C-d")


class TestExactPaneTarget:
    def _target(self, tmux, panes):
        window = MagicMock()
        window.panes = panes
        session = MagicMock()
        session.windows.get.return_value = window
        tmux.server.sessions.get.return_value = session

    def test_resolves_one_live_pane(self, tmux):
        pane = MagicMock()
        pane.pane_id = "%7"
        pane.cmd.return_value.stdout = ["0 codex"]
        self._target(tmux, [pane])

        target = tmux.exact_pane_target("ses", "win")

        assert target.pane_id == "%7"
        assert target.current_command == "codex"

    def test_rejects_ambiguous_split_window(self, tmux):
        from cli_agent_orchestrator.clients.tmux import PaneTargetError

        self._target(tmux, [MagicMock(), MagicMock()])
        with pytest.raises(PaneTargetError) as raised:
            tmux.exact_pane_target("ses", "win")
        assert raised.value.reason_code == "EXIT_PANE_AMBIGUOUS"

    def test_preserves_inventory_uncertainty(self, tmux):
        from cli_agent_orchestrator.clients.tmux import PaneTargetError

        tmux.server.sessions.get.side_effect = RuntimeError("server unavailable")
        with pytest.raises(PaneTargetError) as raised:
            tmux.exact_pane_target("ses", "win")
        assert raised.value.reason_code == "EXIT_INVENTORY_UNCERTAIN"

    def test_runtime_target_binds_exact_pane_pid_and_terminal_identity(self, tmux, tmp_path):
        pane = MagicMock()
        pane.pane_id = "%7"
        pane.cmd.return_value.stdout = ["0 bash"]
        self._target(tmux, [pane])
        process = tmp_path / "4242"
        process.mkdir()
        (process / "environ").write_bytes(
            b"PATH=/bin\0CAO_TERMINAL_ID=closed00\0CAO_RUNTIME_GENERATION=gen-1\0"
        )
        (process / "stat").write_text(
            "4242 (bash) " + " ".join(["S", "1", "4242", "4242", *(["0"] * 15), "777"]),
            encoding="utf-8",
        )
        completed = MagicMock(stdout="4242\tgen-1\n")

        with patch("cli_agent_orchestrator.clients.tmux.subprocess.run", return_value=completed):
            target = tmux.exact_runtime_target("ses", "win", proc_root=tmp_path)

        assert target.pane_id == "%7"
        assert target.pane_pid == 4242
        assert target.current_command == "bash"
        assert target.terminal_id == "closed00"
        assert target.runtime_generation == "gen-1"
        assert target.process_start_ticks == 777
        assert target.process_group_id == 4242
        assert target.process_session_id == 4242

    def test_runtime_target_fails_closed_without_terminal_identity(self, tmux, tmp_path):
        from cli_agent_orchestrator.clients.tmux import PaneTargetError

        pane = MagicMock()
        pane.pane_id = "%7"
        pane.cmd.return_value.stdout = ["0 bash"]
        self._target(tmux, [pane])
        process = tmp_path / "4242"
        process.mkdir()
        (process / "environ").write_bytes(b"PATH=/bin\0CAO_RUNTIME_GENERATION=gen-1\0")
        (process / "stat").write_text(
            "4242 (bash) " + " ".join(["S", "1", "4242", "4242", *(["0"] * 15), "777"]),
            encoding="utf-8",
        )
        with (
            patch(
                "cli_agent_orchestrator.clients.tmux.subprocess.run",
                return_value=MagicMock(stdout="4242\tgen-1\n"),
            ),
            pytest.raises(PaneTargetError) as raised,
        ):
            tmux.exact_runtime_target("ses", "win", proc_root=tmp_path)
        assert raised.value.reason_code == "RUNTIME_IDENTITY_UNKNOWN"

    def test_legacy_runtime_generation_is_bound_to_same_process_identity(self, tmux, tmp_path):
        pane = MagicMock()
        pane.pane_id = "%7"
        pane.cmd.return_value.stdout = ["0 codex"]
        self._target(tmux, [pane])
        process = tmp_path / "4242"
        process.mkdir()
        (process / "environ").write_bytes(b"CAO_TERMINAL_ID=legacy00\0")
        (process / "stat").write_text(
            "4242 (bash) " + " ".join(["S", "1", "4242", "4242", *(["0"] * 15), "777"]),
            encoding="utf-8",
        )
        results = [
            MagicMock(stdout="4242\n"),
            MagicMock(returncode=0),
            MagicMock(stdout="4242\tgen-legacy\n"),
        ]
        with patch(
            "cli_agent_orchestrator.clients.tmux.subprocess.run", side_effect=results
        ) as run:
            target = tmux.bind_legacy_runtime_generation(
                "ses", "win", "legacy00", "gen-legacy", proc_root=tmp_path
            )
        assert target.runtime_generation == "gen-legacy"
        assert target.process_start_ticks == 777
        assert target.process_group_id == 4242
        assert target.process_session_id == 4242
        assert target.generation_inherited is False
        assert run.call_args_list[1].args[0] == [
            "tmux",
            "if-shell",
            "-F",
            "-t",
            "%7",
            "#{==:#{pane_pid},4242}",
            "set-option -p -t %7 @cao_runtime_generation gen-legacy",
            "",
        ]

    def test_runtime_retirement_uses_generation_fence_and_confirms_pane_absent(
        self, tmux, tmp_path
    ):
        from cli_agent_orchestrator.clients.tmux import RuntimePaneTarget

        target = RuntimePaneTarget(
            "%7",
            4242,
            "bash",
            "closed00",
            "gen-1",
            777,
            process_group_id=4242,
            process_session_id=4242,
        )
        process = tmp_path / "4242"
        process.mkdir()
        (process / "environ").write_bytes(
            b"CAO_TERMINAL_ID=closed00\0CAO_RUNTIME_GENERATION=gen-1\0"
        )
        (process / "stat").write_text(
            "4242 (bash) " + " ".join(["S", "1", "4242", "4242", *(["0"] * 15), "777"]),
            encoding="utf-8",
        )
        # Killing the pane can also destroy its last tmux session, in which
        # case if-shell itself may report nonzero even though retirement won.
        results = [
            MagicMock(returncode=0, stdout="4242\tbash\tgen-1\n"),
            MagicMock(returncode=1),
            MagicMock(returncode=0, stdout="%8 4343\n"),
        ]
        with patch(
            "cli_agent_orchestrator.clients.tmux.subprocess.run", side_effect=results
        ) as run:
            assert tmux.retire_runtime_pane(target, proc_root=tmp_path) is True

        tmux.server.cmd.assert_not_called()
        assert run.call_args_list[1].args[0] == [
            "tmux",
            "if-shell",
            "-F",
            "-t",
            "%7",
            "#{&&:#{&&:#{==:#{pane_pid},4242},#{==:#{pane_current_command},bash}},#{==:#{@cao_runtime_generation},gen-1}}",
            "pipe-pane -t %7 ; kill-pane -t %7",
            "",
        ]
        assert run.call_args_list[2].args[0] == [
            "tmux",
            "list-panes",
            "-a",
            "-F",
            "#{pane_id} #{pane_pid}",
        ]

    @pytest.mark.parametrize(
        "display,start_ticks",
        [
            ("4343\tbash\tgen-1\n", 777),
            ("4242\tcodex\tgen-1\n", 777),
            ("4242\tbash\tgen-2\n", 777),
            ("4242\tbash\tgen-1\n", 778),
        ],
    )
    def test_runtime_retirement_preserves_replacement_without_mutation(
        self, tmux, tmp_path, display, start_ticks
    ):
        from cli_agent_orchestrator.clients.tmux import RuntimePaneTarget

        target = RuntimePaneTarget(
            "%7",
            4242,
            "bash",
            "closed00",
            "gen-1",
            777,
            process_group_id=4242,
            process_session_id=4242,
        )
        process = tmp_path / "4242"
        process.mkdir()
        (process / "environ").write_bytes(
            b"CAO_TERMINAL_ID=closed00\0CAO_RUNTIME_GENERATION=gen-1\0"
        )
        (process / "stat").write_text(
            "4242 (bash) " + " ".join(["S", "1", "4242", "4242", *(["0"] * 15), str(start_ticks)]),
            encoding="utf-8",
        )
        with patch(
            "cli_agent_orchestrator.clients.tmux.subprocess.run",
            return_value=MagicMock(returncode=0, stdout=display),
        ) as run:
            assert tmux.retire_runtime_pane(target, proc_root=tmp_path) is False
        assert run.call_count == 1
        tmux.server.cmd.assert_not_called()

    def test_runtime_retirement_predicate_failure_has_no_unconditional_side_effect(
        self, tmux, tmp_path
    ):
        from cli_agent_orchestrator.clients.tmux import RuntimePaneTarget

        target = RuntimePaneTarget(
            "%7",
            4242,
            "bash",
            "closed00",
            "gen-1",
            777,
            process_group_id=4242,
            process_session_id=4242,
        )
        process = tmp_path / "4242"
        process.mkdir()
        (process / "environ").write_bytes(
            b"CAO_TERMINAL_ID=closed00\0CAO_RUNTIME_GENERATION=gen-1\0"
        )
        (process / "stat").write_text(
            "4242 (bash) " + " ".join(["S", "1", "4242", "4242", *(["0"] * 15), "777"]),
            encoding="utf-8",
        )
        results = [
            MagicMock(returncode=0, stdout="4242\tbash\tgen-1\n"),
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="%7 4343\n"),
        ]
        with patch(
            "cli_agent_orchestrator.clients.tmux.subprocess.run", side_effect=results
        ) as run:
            assert tmux.retire_runtime_pane(target, proc_root=tmp_path) is False
        assert run.call_args_list[1].args[0][-2] == ("pipe-pane -t %7 ; kill-pane -t %7")
        tmux.server.cmd.assert_not_called()


# ── get_history ──────────────────────────────────────────────────────


class TestGetHistory:
    def test_get_history_success(self, tmux):
        mock_pane = MagicMock()
        mock_result = MagicMock()
        mock_result.stdout = ["line1", "line2", "line3"]
        mock_pane.cmd.return_value = mock_result
        mock_window = MagicMock()
        mock_window.panes = [mock_pane]
        mock_session = MagicMock()
        mock_session.windows.get.return_value = mock_window
        tmux.server.sessions.get.return_value = mock_session

        result = tmux.get_history("ses", "win")

        assert result == "line1\nline2\nline3"
        mock_pane.cmd.assert_called_once_with("capture-pane", "-e", "-p", "-J", "-S", "-200")

    def test_get_history_preserves_ansi_and_hard_newline_in_logical_v1_capture(self, tmux):
        payload = "0123456789abcdef" * 20 + "TAIL"
        wire = (
            "CAO_RESULT_V1\n"
            '{"summary":"soft-wrap-boundary","body_markdown":"'
            + payload
            + '","changed_files":[],"checks":[],"risks":[],"blockers":[],"format":"v1"}'
        )
        assert len(payload.encode()) == 324
        assert len(wire.encode()) == 459

        marker = "\x1b[36m• CAO_RESULT_V1\x1b[0m"
        mock_pane = MagicMock()
        mock_pane.cmd.return_value.stdout = [marker, wire.split("\n", 1)[1]]
        mock_window = MagicMock()
        mock_window.panes = [mock_pane]
        mock_session = MagicMock()
        mock_session.windows.get.return_value = mock_window
        tmux.server.sessions.get.return_value = mock_session

        result = tmux.get_history("ses", "win")

        assert result == f"{marker}\n{wire.split(chr(10), 1)[1]}"
        assert payload in result
        assert "\n" not in payload
        mock_pane.cmd.assert_called_once_with("capture-pane", "-e", "-p", "-J", "-S", "-200")

    def test_get_history_empty_output(self, tmux):
        mock_pane = MagicMock()
        mock_result = MagicMock()
        mock_result.stdout = []
        mock_pane.cmd.return_value = mock_result
        mock_window = MagicMock()
        mock_window.panes = [mock_pane]
        mock_session = MagicMock()
        mock_session.windows.get.return_value = mock_window
        tmux.server.sessions.get.return_value = mock_session

        result = tmux.get_history("ses", "win")

        assert result == ""

    def test_get_history_session_not_found(self, tmux):
        tmux.server.sessions.get.return_value = None

        with pytest.raises(ValueError, match="not found"):
            tmux.get_history("nonexistent", "win")

    def test_get_history_window_not_found(self, tmux):
        mock_session = MagicMock()
        mock_session.windows.get.return_value = None
        tmux.server.sessions.get.return_value = mock_session

        with pytest.raises(ValueError, match="not found"):
            tmux.get_history("ses", "nonexistent")

    def test_get_history_custom_tail_lines(self, tmux):
        mock_pane = MagicMock()
        mock_result = MagicMock()
        mock_result.stdout = ["line"]
        mock_pane.cmd.return_value = mock_result
        mock_window = MagicMock()
        mock_window.panes = [mock_pane]
        mock_session = MagicMock()
        mock_session.windows.get.return_value = mock_window
        tmux.server.sessions.get.return_value = mock_session

        tmux.get_history("ses", "win", tail_lines=50)

        mock_pane.cmd.assert_called_once_with("capture-pane", "-e", "-p", "-J", "-S", "-50")


# ── list_sessions ────────────────────────────────────────────────────


class TestListSessions:
    def test_list_sessions_success(self, tmux):
        mock_session = MagicMock()
        mock_session.name = "cao-test"
        mock_session.attached_sessions = []
        mock_session.session_created = 1720000000
        tmux.server.sessions = [mock_session]

        result = tmux.list_sessions()

        assert len(result) == 1
        assert result[0]["name"] == "cao-test"
        assert result[0]["status"] == "detached"
        assert result[0]["created_at"] == "1720000000"

    def test_list_sessions_attached(self, tmux):
        mock_session = MagicMock()
        mock_session.name = "cao-test"
        mock_session.attached_sessions = [MagicMock()]
        mock_session.session_created = 1720000000
        tmux.server.sessions = [mock_session]

        result = tmux.list_sessions()

        assert result[0]["status"] == "active"

    def test_list_sessions_returns_empty_on_error(self, tmux):
        tmux.server.sessions = MagicMock(side_effect=Exception("no server"))
        tmux.server.sessions.__iter__ = MagicMock(side_effect=Exception("no server"))

        result = tmux.list_sessions()

        assert result is None


# ── get_session_windows ──────────────────────────────────────────────


class TestGetSessionWindows:
    def test_get_session_windows_success(self, tmux):
        mock_window = MagicMock()
        mock_window.name = "agent-win"
        mock_window.index = 0
        mock_session = MagicMock()
        mock_session.windows = [mock_window]
        tmux.server.sessions.get.return_value = mock_session

        result = tmux.get_session_windows("ses")

        assert len(result) == 1
        assert result[0]["name"] == "agent-win"

    def test_get_session_windows_session_not_found(self, tmux):
        tmux.server.sessions.get.return_value = None

        result = tmux.get_session_windows("nonexistent")

        assert result == []

    def test_get_session_windows_error(self, tmux):
        tmux.server.sessions.get.side_effect = Exception("tmux error")

        result = tmux.get_session_windows("ses")

        assert result == []


# ── kill_session ─────────────────────────────────────────────────────


class TestKillSession:
    def test_kill_session_success(self, tmux):
        mock_session = MagicMock()
        tmux.server.sessions.get.return_value = mock_session

        result = tmux.kill_session("ses")

        assert result is True
        mock_session.kill.assert_called_once()

    def test_kill_session_not_found(self, tmux):
        tmux.server.sessions.get.return_value = None

        result = tmux.kill_session("nonexistent")

        assert result is False

    def test_kill_session_error(self, tmux):
        tmux.server.sessions.get.side_effect = Exception("tmux error")

        result = tmux.kill_session("ses")

        assert result is False


# ── kill_window ──────────────────────────────────────────────────────


class TestKillWindow:
    def test_kill_window_success(self, tmux):
        mock_window = MagicMock()
        mock_session = MagicMock()
        mock_session.windows.get.return_value = mock_window
        tmux.server.sessions.get.return_value = mock_session

        result = tmux.kill_window("ses", "win")

        assert result is True
        mock_window.kill.assert_called_once()

    def test_kill_window_session_not_found(self, tmux):
        tmux.server.sessions.get.return_value = None

        result = tmux.kill_window("ses", "win")

        assert result is False

    def test_kill_window_window_not_found(self, tmux):
        mock_session = MagicMock()
        mock_session.windows.get.return_value = None
        tmux.server.sessions.get.return_value = mock_session

        result = tmux.kill_window("ses", "nonexistent")

        assert result is False

    def test_kill_window_error(self, tmux):
        tmux.server.sessions.get.side_effect = Exception("tmux error")

        result = tmux.kill_window("ses", "win")

        assert result is False


# ── session_exists ───────────────────────────────────────────────────


class TestSessionExists:
    def test_session_exists_true(self, tmux):
        tmux.server.sessions.get.return_value = MagicMock()

        assert tmux.session_exists("ses") is True

    def test_session_exists_false(self, tmux):
        tmux.server.sessions.get.return_value = None

        assert tmux.session_exists("ses") is False

    def test_session_exists_treats_real_libtmux_absence_as_false(self, tmux):
        """libtmux's authoritative missing-object signal is not an inventory failure."""
        from libtmux._internal.query_list import ObjectDoesNotExist

        tmux.server.sessions.get.side_effect = ObjectDoesNotExist("ses")

        assert tmux.session_exists("ses") is False

    def test_session_exists_error(self, tmux):
        tmux.server.sessions.get.side_effect = Exception("tmux error")

        assert tmux.session_exists("ses") is None


# ── get_pane_working_directory ───────────────────────────────────────


class TestGetPaneWorkingDirectory:
    def test_get_pane_working_directory_success(self, tmux):
        mock_pane = MagicMock()
        mock_result = MagicMock()
        mock_result.stdout = ["/home/user/project"]
        mock_pane.cmd.return_value = mock_result
        mock_window = MagicMock()
        mock_window.active_pane = mock_pane
        mock_session = MagicMock()
        mock_session.windows.get.return_value = mock_window
        tmux.server.sessions.get.return_value = mock_session

        result = tmux.get_pane_working_directory("ses", "win")

        assert result == "/home/user/project"

    def test_get_pane_working_directory_session_not_found(self, tmux):
        tmux.server.sessions.get.return_value = None

        result = tmux.get_pane_working_directory("ses", "win")

        assert result is None

    def test_get_pane_working_directory_window_not_found(self, tmux):
        mock_session = MagicMock()
        mock_session.windows.get.return_value = None
        tmux.server.sessions.get.return_value = mock_session

        result = tmux.get_pane_working_directory("ses", "win")

        assert result is None

    def test_get_pane_working_directory_error(self, tmux):
        tmux.server.sessions.get.side_effect = Exception("tmux error")

        result = tmux.get_pane_working_directory("ses", "win")

        assert result is None


# ── pipe_pane / stop_pipe_pane ───────────────────────────────────────


class TestPipePane:
    def test_pipe_pane_success(self, tmux):
        mock_pane = MagicMock()
        mock_window = MagicMock()
        mock_window.active_pane = mock_pane
        mock_session = MagicMock()
        mock_session.windows.get.return_value = mock_window
        tmux.server.sessions.get.return_value = mock_session

        tmux.pipe_pane("ses", "win", "/tmp/log.txt")

        mock_pane.cmd.assert_called_once_with("pipe-pane", "-o", "cat >> /tmp/log.txt")

    def test_pipe_pane_session_not_found(self, tmux):
        tmux.server.sessions.get.return_value = None

        with pytest.raises(ValueError, match="not found"):
            tmux.pipe_pane("nonexistent", "win", "/tmp/log.txt")

    def test_pipe_pane_window_not_found(self, tmux):
        mock_session = MagicMock()
        mock_session.windows.get.return_value = None
        tmux.server.sessions.get.return_value = mock_session

        with pytest.raises(ValueError, match="not found"):
            tmux.pipe_pane("ses", "nonexistent", "/tmp/log.txt")


class TestStopPipePane:
    def test_stop_pipe_pane_success(self, tmux):
        mock_pane = MagicMock()
        mock_window = MagicMock()
        mock_window.active_pane = mock_pane
        mock_session = MagicMock()
        mock_session.windows.get.return_value = mock_window
        tmux.server.sessions.get.return_value = mock_session

        tmux.stop_pipe_pane("ses", "win")

        mock_pane.cmd.assert_called_once_with("pipe-pane")

    def test_stop_pipe_pane_session_not_found(self, tmux):
        tmux.server.sessions.get.return_value = None

        with pytest.raises(ValueError, match="not found"):
            tmux.stop_pipe_pane("nonexistent", "win")

    def test_stop_pipe_pane_window_not_found(self, tmux):
        mock_session = MagicMock()
        mock_session.windows.get.return_value = None
        tmux.server.sessions.get.return_value = mock_session

        with pytest.raises(ValueError, match="not found"):
            tmux.stop_pipe_pane("ses", "nonexistent")
