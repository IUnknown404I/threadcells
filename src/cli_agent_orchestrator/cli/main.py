"""Main CLI entry point for ThreadCells."""

import click

from cli_agent_orchestrator.cli.commands.control_plane import operator, profiles, providers
from cli_agent_orchestrator.cli.commands.doctor import doctor
from cli_agent_orchestrator.cli.commands.env import env
from cli_agent_orchestrator.cli.commands.flow import flow
from cli_agent_orchestrator.cli.commands.info import info
from cli_agent_orchestrator.cli.commands.init import init
from cli_agent_orchestrator.cli.commands.install import install
from cli_agent_orchestrator.cli.commands.launch import launch
from cli_agent_orchestrator.cli.commands.mcp_server import mcp_server
from cli_agent_orchestrator.cli.commands.session import session
from cli_agent_orchestrator.cli.commands.shutdown import shutdown
from cli_agent_orchestrator.cli.commands.skills import skills


@click.group()
def cli():
    """ThreadCells — self-hosted coding-agent operations console."""


# Register commands
cli.add_command(launch)
cli.add_command(init)
cli.add_command(install)
cli.add_command(shutdown)
cli.add_command(flow)
cli.add_command(env)
cli.add_command(doctor)
cli.add_command(mcp_server)
cli.add_command(info)
cli.add_command(skills)
cli.add_command(session)
cli.add_command(profiles)
cli.add_command(providers)
cli.add_command(operator)


if __name__ == "__main__":
    cli()
