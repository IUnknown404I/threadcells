"""Launch command for CLI Agent Orchestrator CLI."""

import os
import subprocess
import time

import click
import requests

from cli_agent_orchestrator.constants import (
    API_BASE_URL,
    DEFAULT_PROVIDER,
    PROVIDERS,
    SERVER_HOST,
    SERVER_PORT,
)
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.utils.terminal import poll_until_done, wait_until_terminal_status

# Providers that require workspace folder access
PROVIDERS_REQUIRING_WORKSPACE_ACCESS = {
    "claude_code",
    "codex",
    "copilot_cli",
    "gemini_cli",
    "kimi_cli",
    "kiro_cli",
    "opencode_cli",
}


def _mint_local_owner_xhigh_grant(
    *,
    agent_profile: str,
    provider: str,
    working_directory: str,
    requested_session_name: str | None,
) -> dict[str, str | int]:
    """Mint one normal scoped grant from the trusted interactive local CLI.

    The CLI writes the one-use capability directly to the same local control-plane
    database as the server.  The HTTP API therefore never exposes a reusable
    loopback bypass: session creation consumes the capability through the normal
    privileged-launch validation path.
    """
    if agent_profile != "critical_sol_xhigh_owner":
        raise ValueError("local owner authorization is limited to the built-in XHigh profile")
    if SERVER_HOST not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("local owner authorization requires a loopback ThreadCells server")

    from cli_agent_orchestrator.clients.database import init_db
    from cli_agent_orchestrator.providers.manager import provider_manager
    from cli_agent_orchestrator.services.control_plane_registry import (
        initialize_control_plane_registries,
        resolve_launch,
    )
    from cli_agent_orchestrator.services.operator_auth_service import mint_xhigh_launch_grant
    from cli_agent_orchestrator.services.terminal_service import _canonical_worktree

    init_db()
    initialize_control_plane_registries(provider_manager.adapter_registry)
    resolution = resolve_launch(agent_profile, fallback_provider=provider)
    if resolution.provider_adapter_id != provider:
        raise ValueError("provider does not match the active profile revision")
    if not resolution.owner_grant_required:
        raise ValueError("the active profile revision does not require owner authorization")

    return mint_xhigh_launch_grant(
        auth_identity="local_cli_interactive",
        agent_profile=agent_profile,
        provider=provider,
        canonical_worktree=_canonical_worktree(working_directory),
        requested_session_name=requested_session_name,
        confirmation=f"LAUNCH {agent_profile}",
        owner_grant_required=True,
        grant_scope={
            "profile_revision_id": resolution.profile_revision_id,
            "provider_config_revision_id": resolution.provider_config_revision_id,
            "project_id": None,
            "launch_mode": "new_session",
            "delegation_depth": 0,
        },
    )


@click.command()
@click.argument("message", required=False, default=None)
@click.option("--agents", required=True, help="Agent profile to launch")
@click.option("--session-name", help="Name of the session (default: auto-generated)")
@click.option("--headless", is_flag=True, help="Launch in detached mode")
@click.option(
    "--provider",
    default=None,
    help=f"Provider to use (default: profile provider or {DEFAULT_PROVIDER})",
)
@click.option(
    "--allowed-tools",
    multiple=True,
    help="Override allowedTools (CAO format: execute_bash, fs_read, @cao-mcp-server). Repeatable.",
)
@click.option(
    "--async",
    "is_async",
    is_flag=True,
    help="Send message and return immediately without waiting for completion",
)
@click.option(
    "--auto-approve",
    is_flag=True,
    help="Skip confirmation prompt (restrictions still enforced).",
)
@click.option(
    "--yolo",
    is_flag=True,
    help="[DANGEROUS] Unrestricted tool access AND skip confirmation prompts. "
    "Agent can execute ANY command including aws, rm, curl.",
)
@click.option(
    "--working-directory",
    default=None,
    help="Working directory for the session (default: current directory)",
)
@click.option(
    "--owner-xhigh",
    is_flag=True,
    help="Manually authorize one short-lived launch of the privileged XHigh owner profile.",
)
def launch(
    message,
    agents,
    session_name,
    headless,
    is_async,
    provider,
    allowed_tools,
    auto_approve,
    yolo,
    working_directory,
    owner_xhigh,
):
    """Launch a ThreadCells session with the specified agent profile."""
    try:
        display_dir = working_directory or os.path.realpath(os.getcwd())

        from cli_agent_orchestrator.services.launch_authority import is_privileged_profile

        privileged_profile = is_privileged_profile(agents)
        try:
            from cli_agent_orchestrator.services.control_plane_registry import get_profile

            privileged_profile = privileged_profile or bool(
                get_profile(agents)["owner_authorization_required"]
            )
        except Exception:
            # A server may not have bootstrapped this CLI process's local DB.
            # Preserve the built-in fail-safe; the server independently applies
            # the immutable-revision policy before any launch is admitted.
            pass
        if privileged_profile and not owner_xhigh:
            raise click.ClickException(
                "This privileged profile requires an explicit manual --owner-xhigh launch"
            )
        if owner_xhigh and not privileged_profile:
            raise click.ClickException("--owner-xhigh is valid only for a privileged profile")
        if owner_xhigh:
            if os.environ.get("CAO_TERMINAL_ID"):
                raise click.ClickException(
                    "Delegated agent terminals cannot issue privileged owner launch grants"
                )
            click.echo(
                "Privileged owner launch: one profile, provider, worktree, and session scope; "
                "grant expires after 60 seconds."
            )
            if not click.confirm("Authorize this one XHigh launch?", default=False):
                raise click.ClickException("Privileged launch cancelled")

        # Resolve allowedTools: --yolo > --allowed-tools CLI > profile/role defaults
        from cli_agent_orchestrator.utils.agent_profiles import load_agent_profile
        from cli_agent_orchestrator.utils.tool_mapping import (
            format_tool_summary,
            get_disallowed_tools,
            resolve_allowed_tools,
        )

        resolved_allowed_tools = None
        no_role_set = False
        if yolo:
            resolved_allowed_tools = ["*"]
        elif allowed_tools:
            resolved_allowed_tools = list(allowed_tools)
        else:
            # Load profile to get role-based defaults
            try:
                profile = load_agent_profile(agents)
                mcp_server_names = list(profile.mcpServers.keys()) if profile.mcpServers else None
                no_role_set = not profile.role and not profile.allowedTools
                resolved_allowed_tools = resolve_allowed_tools(
                    profile.allowedTools, profile.role, mcp_server_names
                )
                # Honour profile.provider when --provider not explicitly passed
                if provider is None:
                    from cli_agent_orchestrator.utils.agent_profiles import resolve_provider

                    provider = resolve_provider(agents, DEFAULT_PROVIDER)
            except (FileNotFoundError, RuntimeError):
                # Profile not found — use developer defaults (backward compatible)
                no_role_set = True
                resolved_allowed_tools = resolve_allowed_tools(None, None, None)

        # Fall back to DEFAULT_PROVIDER when --provider was not given and
        # profile resolution didn't set it (yolo, --allowed-tools, or missing profile)
        if provider is None:
            provider = DEFAULT_PROVIDER

        # Validate provider
        if provider not in PROVIDERS:
            raise click.ClickException(
                f"Invalid provider '{provider}'. Available providers: {', '.join(PROVIDERS)}"
            )
        # Confirmation / warning prompts
        if provider in PROVIDERS_REQUIRING_WORKSPACE_ACCESS:
            if yolo:
                # --yolo: warn but don't block
                click.echo(click.style("\n[WARNING] --yolo mode enabled", fg="yellow", bold=True))
                click.echo(
                    f"  Agent '{agents}' launching UNRESTRICTED on {provider}.\n"
                    f"  Agent can execute ANY command (aws, rm, curl, read credentials).\n"
                    f"  Directory: {display_dir}\n"
                )
                if provider == "kiro_cli":
                    # kiro-cli 2.0.1 TUI blocks on an interactive "Yes, I accept"
                    # consent dialog when --trust-all-tools is set. CAO cannot
                    # answer it headlessly, so yolo launches use --legacy-ui.
                    click.echo(
                        "  Note: kiro_cli will launch in --legacy-ui mode so "
                        "--trust-all-tools can be applied non-interactively.\n"
                    )
                elif provider == "opencode_cli":
                    # opencode's TUI has no runtime skip-permissions flag
                    # (tracked upstream in sst/opencode#8463). Permissions are
                    # install-time only, so --yolo cannot loosen them here.
                    click.echo(
                        click.style(
                            "  Note: --yolo has no runtime effect on opencode_cli.\n"
                            "  Permissions are set at cao install time. To get unrestricted\n"
                            "  access, set 'allowedTools: [\"*\"]' in the profile and re-run\n"
                            "  'cao install'. See docs/opencode-cli.md for details.\n",
                            fg="yellow",
                        )
                    )
            else:
                # Normal launch: show tool summary and confirm
                tool_summary = format_tool_summary(resolved_allowed_tools)
                blocked = get_disallowed_tools(provider, resolved_allowed_tools)
                blocked_summary = ", ".join(blocked) if blocked else "(none)"

                click.echo(
                    f"\nAgent '{agents}' launching on {provider}:\n"
                    f"  Allowed:  {tool_summary}\n"
                    f"  Blocked:  {blocked_summary}\n"
                    f"  Directory: {display_dir}\n"
                )
                if no_role_set:
                    click.echo(
                        "  Note: No role or allowedTools set — defaulting to 'developer'.\n"
                        "  Add 'role' or 'allowedTools' to your agent profile to control tool access.\n"
                        "  Docs: https://github.com/IUnknown404I/threadcells/blob/main/docs/tool-restrictions.md\n"
                    )
                click.echo(
                    "  To skip this prompt next time, relaunch with --auto-approve\n"
                    "  To remove all restrictions, relaunch with --yolo\n"
                )
                if not auto_approve and not click.confirm("Proceed?", default=True):
                    raise click.ClickException("Launch cancelled by user")

        # Call API to create session — pass working_directory only if explicitly
        # provided. When omitted, the server defaults to its own CWD.
        url = f"http://{SERVER_HOST}:{SERVER_PORT}/sessions"
        params = {
            "provider": provider,
            "agent_profile": agents,
            "working_directory": working_directory or os.getcwd(),
        }
        if session_name:
            params["session_name"] = session_name
        if resolved_allowed_tools:
            # Pass as comma-separated string for query param
            params["allowed_tools"] = ",".join(resolved_allowed_tools)
        headers = None
        if agents == "critical_sol_xhigh_owner":
            grant_document = _mint_local_owner_xhigh_grant(
                agent_profile=agents,
                provider=provider,
                working_directory=params["working_directory"],
                requested_session_name=session_name.strip() if session_name else None,
            )
            params["owner_grant_launch_id"] = grant_document["launch_id"]
            headers = {"X-ThreadCells-Owner-Grant": grant_document["grant"]}
        elif privileged_profile:
            from cli_agent_orchestrator.services.terminal_service import _canonical_worktree

            operator_secret = click.prompt("Operator secret", hide_input=True)
            grant_response = requests.post(
                f"{API_BASE_URL}/operator/xhigh-grants",
                headers={"Authorization": f"Bearer {operator_secret}"},
                json={
                    "agent_profile": agents,
                    "provider": provider,
                    "working_directory": _canonical_worktree(params["working_directory"]),
                    "requested_session_name": session_name.strip() if session_name else None,
                    "project_id": None,
                    "launch_mode": "new_session",
                    "confirmation": f"LAUNCH {agents}",
                },
            )
            grant_response.raise_for_status()
            grant_document = grant_response.json()
            params["owner_grant_launch_id"] = grant_document["launch_id"]
            # The plaintext capability exists only in this local request object.
            headers = {"X-ThreadCells-Owner-Grant": grant_document["grant"]}

        response = requests.post(url, params=params, headers=headers)
        status_code = getattr(response, "status_code", None)
        if isinstance(status_code, int) and status_code >= 400:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = None
            if isinstance(detail, dict) and isinstance(detail.get("reason_code"), str):
                raise click.ClickException(f"Launch denied: reason_code={detail['reason_code']}")
            response.raise_for_status()

        terminal = response.json()

        click.echo(f"Session created: {terminal['session_name']}")
        click.echo(f"Terminal created: {terminal['name']}")

        # Attach to tmux session unless headless
        if not headless:
            subprocess.run(["tmux", "attach-session", "-t", terminal["session_name"]])
        elif message:
            ready = wait_until_terminal_status(
                terminal["id"],
                {TerminalStatus.IDLE, TerminalStatus.COMPLETED},
                timeout=120,
            )
            if not ready:
                raise click.ClickException(
                    f"Conductor {terminal['id']} did not become ready within 120s"
                )
            response = requests.post(
                f"{API_BASE_URL}/terminals/{terminal['id']}/input",
                params={"message": message},
            )
            response.raise_for_status()
            time.sleep(3)
            if is_async:
                click.echo(f"Message sent to {terminal['name']}. Running in background.")
                return
            poll_until_done(terminal["id"], timeout=300)
            output_resp = requests.get(
                f"{API_BASE_URL}/terminals/{terminal['id']}/output",
                params={"mode": "last"},
            )
            output_resp.raise_for_status()
            output = output_resp.json().get("output", "")
            if output:
                click.echo(output)

    except requests.exceptions.RequestException as e:
        raise click.ClickException(f"Failed to connect to ThreadCells server: {str(e)}")
    except click.ClickException:
        raise
    except Exception as e:
        raise click.ClickException(str(e))
