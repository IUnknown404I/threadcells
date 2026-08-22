"""Flow service for scheduled agent sessions."""

import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple, cast

import frontmatter  # type: ignore
from apscheduler.triggers.cron import CronTrigger  # type: ignore

from cli_agent_orchestrator.clients.database import create_flow as db_create_flow
from cli_agent_orchestrator.clients.database import delete_flow as db_delete_flow
from cli_agent_orchestrator.clients.database import get_flow as db_get_flow
from cli_agent_orchestrator.clients.database import get_flows_to_run as db_get_flows_to_run
from cli_agent_orchestrator.clients.database import list_flows as db_list_flows
from cli_agent_orchestrator.clients.database import update_flow_enabled as db_update_flow_enabled
from cli_agent_orchestrator.clients.database import update_flow_next_run as db_update_flow_next_run
from cli_agent_orchestrator.clients.database import (
    update_flow_run_times as db_update_flow_run_times,
)
from cli_agent_orchestrator.constants import DEFAULT_PROVIDER, PROVIDERS
from cli_agent_orchestrator.models.flow import Flow
from cli_agent_orchestrator.services import project_service
from cli_agent_orchestrator.services.terminal_service import create_terminal, send_input
from cli_agent_orchestrator.utils.template import render_template
from cli_agent_orchestrator.utils.terminal import generate_session_name

logger = logging.getLogger(__name__)


def _get_next_run_time(cron_expression: str) -> datetime:
    """Calculate next run time from cron expression."""
    trigger = CronTrigger.from_crontab(cron_expression)
    next_time = trigger.get_next_fire_time(None, datetime.now())
    if next_time is None:
        raise ValueError(
            f"Could not calculate next run time for cron expression: {cron_expression}"
        )
    return cast(datetime, next_time)


def _parse_flow_file(file_path: Path) -> Tuple[Dict, str]:
    """Parse flow file and return metadata and prompt template.

    Returns:
        Tuple of (metadata dict, prompt template string)
    """
    if not file_path.exists():
        raise ValueError(f"Flow file not found: {file_path}")

    with open(file_path, "r") as f:
        post = frontmatter.load(f)

    return post.metadata, post.content


def add_flow(file_path: str, project_context: dict[str, str] | None = None) -> Flow:
    """Add flow from file."""
    try:
        path = Path(file_path).resolve()
        metadata, _ = _parse_flow_file(path)

        # Validate required fields
        required_fields = ["name", "schedule", "agent_profile"]
        for field in required_fields:
            if field not in metadata:
                raise ValueError(f"Missing required field: {field}")

        name = metadata["name"]
        schedule = metadata["schedule"]
        agent_profile = metadata["agent_profile"]
        provider = metadata.get(
            "provider", DEFAULT_PROVIDER
        )  # Optional, defaults to DEFAULT_PROVIDER
        script = metadata.get("script", "")  # Optional
        project_id = metadata.get("project_id")
        project_name = metadata.get("project_name")
        project_path = metadata.get("project_path")
        project_description = metadata.get("project_description")
        if project_context is not None:
            project_id = project_context["id"]
            project_name = project_context["name"]
            project_path = project_context["path"]
            project_description = project_context.get("description")
        elif project_id:
            # CLI-created flow files receive the same authoritative resolution
            # as the HTTP/UI path before their historical context is persisted.
            _, resolved_context = project_service.launch_context(str(project_id))
            assert resolved_context is not None
            project_name = resolved_context["name"]
            project_path = resolved_context["path"]
            project_description = resolved_context.get("description")

        # Validate cron expression and calculate next run
        try:
            next_run = _get_next_run_time(schedule)
        except Exception as e:
            raise ValueError(f"Invalid cron expression '{schedule}': {e}")

        # Create flow in database
        flow = db_create_flow(
            name=name,
            file_path=str(path),
            schedule=schedule,
            agent_profile=agent_profile,
            provider=provider,
            script=script,
            next_run=next_run,
            project_id=project_id,
            project_name=project_name,
            project_path=project_path,
            project_description=project_description,
        )

        logger.info(f"Added flow: {name}")
        return flow

    except Exception as e:
        logger.error(f"Failed to add flow from {file_path}: {e}")
        raise


def _enrich_flow_with_prompt(flow: Flow) -> Flow:
    """Read the prompt template from the flow file and attach it."""
    try:
        _, prompt = _parse_flow_file(Path(flow.file_path))
        flow.prompt_template = prompt.strip()
    except Exception:
        flow.prompt_template = None
    return flow


def list_flows() -> List[Flow]:
    """List all flows."""
    return [_enrich_flow_with_prompt(f) for f in db_list_flows()]


def get_flow(name: str) -> Flow:
    """Get flow by name."""
    flow = db_get_flow(name)
    if not flow:
        raise ValueError(f"Flow '{name}' not found")
    return _enrich_flow_with_prompt(flow)


def remove_flow(name: str) -> bool:
    """Remove flow."""
    if not db_delete_flow(name):
        raise ValueError(f"Flow '{name}' not found")
    logger.info(f"Removed flow: {name}")
    return True


def disable_flow(name: str) -> bool:
    """Disable flow."""
    if not db_update_flow_enabled(name, enabled=False):
        raise ValueError(f"Flow '{name}' not found")
    logger.info(f"Disabled flow: {name}")
    return True


def enable_flow(name: str) -> bool:
    """Enable flow and recalculate next_run."""
    flow = get_flow(name)

    # Recalculate next_run from now
    next_run = _get_next_run_time(flow.schedule)

    if not db_update_flow_enabled(name, enabled=True, next_run=next_run):
        raise ValueError(f"Failed to enable flow '{name}'")

    logger.info(f"Enabled flow: {name}")
    return True


def execute_flow(name: str) -> bool:
    """Execute flow: run script, render prompt, launch session."""
    try:
        logger.info(f"Executing flow: {name}")
        flow = get_flow(name)

        # Read flow file
        file_path = Path(flow.file_path)
        _, prompt_template = _parse_flow_file(file_path)

        # If no script, always execute with empty output
        if not flow.script:
            output = {"execute": True, "output": {}}
        else:
            # Execute script
            script_path = Path(flow.script)
            if not script_path.is_absolute():
                script_path = file_path.parent / script_path

            if not script_path.exists():
                raise ValueError(f"Script not found: {script_path}")

            result = subprocess.run([str(script_path)], capture_output=True, text=True, timeout=30)

            if result.returncode != 0:
                logger.error(f"Script failed: {result.stderr}")
                raise ValueError(
                    f"Script failed with exit code {result.returncode}: {result.stderr}"
                )

            # Parse JSON output
            try:
                output = json.loads(result.stdout)
            except json.JSONDecodeError as e:
                raise ValueError(f"Script output is not valid JSON: {e}")

            if "execute" not in output:
                raise ValueError("Script output missing 'execute' field")

            if "output" not in output:
                raise ValueError("Script output missing 'output' field")

        # Calculate the next schedule now, but record last_run only after a
        # skip decision or a successfully admitted agent launch.
        now = datetime.now()
        next_run = _get_next_run_time(flow.schedule)

        # Check if we should execute
        if not output["execute"]:
            db_update_flow_run_times(name, last_run=now, next_run=next_run)
            logger.info(f"Flow {name}: skipped (execute=false)")
            return False

        # Render prompt template
        if not isinstance(output["output"], dict):
            raise ValueError("Script output 'output' field must be a dictionary")
        output_dict: Dict[str, Any] = output["output"]  # type: ignore[assignment]
        rendered_prompt = render_template(prompt_template, output_dict)

        # Launch session
        session_name = generate_session_name()
        # A flow keeps its own registration snapshot. A current registry row
        # remains authoritative for a new execution; if that row was deleted,
        # the durable historical context is the only safe launch fallback.
        flow_context = None
        working_directory = flow.project_path or None
        if flow.project_id:
            try:
                _, flow_context = project_service.launch_context(flow.project_id)
                working_directory = flow_context["path"] if flow_context else working_directory
            except project_service.ProjectResolutionError:
                # Do not recreate a deleted registry row.  Preserve the exact
                # frozen name/path/comment in the terminal context instead.
                if project_service.database.get_project(flow.project_id) is not None:
                    raise
                if flow.project_name and flow.project_path:
                    flow_context = {
                        "id": flow.project_id,
                        "name": flow.project_name,
                        "path": flow.project_path,
                    }
                    if flow.project_description:
                        flow_context["description"] = flow.project_description
        terminal = create_terminal(
            session_name=session_name,
            provider=flow.provider,
            agent_profile=flow.agent_profile,
            new_session=True,
            working_directory=working_directory,
            project_context=flow_context,
        )

        # Scheduled prompts use the same durable workflow/provider admission
        # as UI and CAO transports, so a full execution pool retains the turn.
        from cli_agent_orchestrator.clients.database import queue_workflow_input_for_provider
        from cli_agent_orchestrator.services import workflow_service
        from cli_agent_orchestrator.services.operations_service import AdmissionDenied

        prepared = workflow_service.prepare_external_input(terminal.id, rendered_prompt)
        turn_id = prepared["turn_id"]
        if prepared["queued"]:
            db_update_flow_run_times(name, last_run=now, next_run=next_run)
            logger.info("Flow %s: launch input queued behind runtime recovery", name)
            return True
        try:
            send_input(
                terminal.id,
                workflow_service.admission_message(rendered_prompt, turn_id),
                logical_turn_id=turn_id,
            )
        except AdmissionDenied:
            if not queue_workflow_input_for_provider(terminal.id, turn_id, rendered_prompt):
                raise

        db_update_flow_run_times(name, last_run=now, next_run=next_run)

        logger.info(f"Flow {name}: launched session {session_name}")
        return True

    except Exception as e:
        # A scheduled failure must not hot-loop every daemon tick. Preserve the
        # previous truthful last_run and advance only the next attempt.
        try:
            if "flow" in locals() and flow is not None:
                db_update_flow_next_run(name, _get_next_run_time(flow.schedule))
        except Exception:
            logger.warning("Flow %s next-run recovery failed", name)
        logger.error(f"Flow {name} failed: {e}", exc_info=True)
        raise


def get_flows_to_run() -> List[Flow]:
    """Get flows that should run now."""
    return db_get_flows_to_run()
