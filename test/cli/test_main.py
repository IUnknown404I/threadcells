"""Tests for CLI main entry point."""

import os
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from cli_agent_orchestrator.cli.commands.doctor import doctor_report
from cli_agent_orchestrator.cli.main import cli


class TestCliMain:
    """Tests for main CLI group."""

    def test_cli_help(self):
        """Test CLI help command."""
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])

        assert result.exit_code == 0
        assert "ThreadCells" in result.output

    def test_non_mcp_cli_import_does_not_inspect_unreadable_cwd(self, tmp_path):
        restricted = tmp_path / "restricted"
        restricted.mkdir()
        source_root = Path(__file__).resolve().parents[2] / "src"
        previous = Path.cwd()
        os.chdir(restricted)
        restricted.chmod(0)
        try:
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(source_root)
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from cli_agent_orchestrator.cli.main import cli; "
                    "assert cli.get_command(None, 'operator') is not None",
                ],
                env=environment,
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
        finally:
            restricted.chmod(0o700)
            os.chdir(previous)

        assert result.returncode == 0, result.stderr

    def test_cli_has_read_only_doctor_command(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["doctor", "--json"])

        assert result.exit_code in {0, 1}
        assert '"read_only": true' in result.output

    def test_doctor_requires_codex_and_labels_missing_tools_consistently(self, monkeypatch):
        monkeypatch.setattr(
            "cli_agent_orchestrator.cli.commands.doctor.shutil.which",
            lambda command: "/usr/bin/git" if command == "git" else None,
        )

        report = doctor_report()
        checks = {check["name"]: check for check in report["checks"]}

        assert checks["codex"] == {"name": "codex", "ok": False, "detail": "missing from PATH"}
        assert checks["git"] == {"name": "git", "ok": True, "detail": "found on PATH"}
        assert report["ready"] is False

    def test_cli_has_launch_command(self):
        """Test CLI has launch command."""
        runner = CliRunner()
        result = runner.invoke(cli, ["launch", "--help"])

        assert result.exit_code == 0
        assert "Launch" in result.output or "launch" in result.output.lower()

    def test_cli_has_init_command(self):
        """Test CLI has init command."""
        runner = CliRunner()
        result = runner.invoke(cli, ["init", "--help"])

        assert result.exit_code == 0

    def test_cli_has_install_command(self):
        """Test CLI has install command."""
        runner = CliRunner()
        result = runner.invoke(cli, ["install", "--help"])

        assert result.exit_code == 0

    def test_cli_has_shutdown_command(self):
        """Test CLI has shutdown command."""
        runner = CliRunner()
        result = runner.invoke(cli, ["shutdown", "--help"])

        assert result.exit_code == 0

    def test_cli_has_flow_command(self):
        """Test CLI has flow command group."""
        runner = CliRunner()
        result = runner.invoke(cli, ["flow", "--help"])

        assert result.exit_code == 0

    def test_cli_has_skills_command(self):
        """Test CLI has skills command group."""
        runner = CliRunner()
        result = runner.invoke(cli, ["skills", "--help"])

        assert result.exit_code == 0

    def test_cli_has_skills_add_help(self):
        """Test CLI has skills add subcommand help."""
        runner = CliRunner()
        result = runner.invoke(cli, ["skills", "add", "--help"])

        assert result.exit_code == 0

    def test_cli_has_skills_remove_help(self):
        """Test CLI has skills remove subcommand help."""
        runner = CliRunner()
        result = runner.invoke(cli, ["skills", "remove", "--help"])

        assert result.exit_code == 0

    def test_cli_has_skills_list_help(self):
        """Test CLI has skills list subcommand help."""
        runner = CliRunner()
        result = runner.invoke(cli, ["skills", "list", "--help"])

        assert result.exit_code == 0

    def test_cli_unknown_command(self):
        """Test CLI with unknown command."""
        runner = CliRunner()
        result = runner.invoke(cli, ["unknown-command"])

        assert result.exit_code != 0
