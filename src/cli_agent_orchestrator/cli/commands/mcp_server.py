"""MCP server command for ThreadCells."""

import click


def run_mcp_server() -> None:
    """Import FastMCP only for the command that actually starts it."""
    from cli_agent_orchestrator.mcp_server.server import main

    main()


@click.command(name="mcp-server")
def mcp_server():
    """Start the ThreadCells MCP server."""
    click.echo("Starting ThreadCells MCP server...")
    run_mcp_server()
