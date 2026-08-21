"""Public control-plane profile, provider, schema, and operator commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import click
import requests  # type: ignore[import-untyped]

from cli_agent_orchestrator.constants import API_BASE_URL


def _read_json(path: Path) -> dict[str, Any]:
    if path.suffix.lower() != ".json" or path.name == ".env":
        raise click.ClickException("Input must be an explicit .json document")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise click.ClickException(f"Could not read JSON document: {exc}") from exc
    if not isinstance(value, dict):
        raise click.ClickException("Document root must be an object")
    return value


def _request(method: str, path: str, **kwargs: Any) -> Any:
    try:
        response = requests.request(method, f"{API_BASE_URL}{path}", timeout=15, **kwargs)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        detail = None
        if exc.response is not None:
            try:
                detail = exc.response.json().get("detail")
            except (ValueError, AttributeError):
                detail = None
        raise click.ClickException(str(detail or exc)) from exc


def _write_json(value: Any, output: Optional[Path]) -> None:
    rendered = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if output is None:
        click.echo(rendered, nl=False)
        return
    try:
        output.write_text(rendered, encoding="utf-8")
    except OSError as exc:
        raise click.ClickException(f"Could not write {output}: {exc}") from exc


def _operator_headers() -> dict[str, str]:
    secret = click.prompt("Operator secret", hide_input=True)
    return {"Authorization": f"Bearer {secret}"}


def _artifact_commands(group_name: str, api_path: str):
    @click.group(name=group_name)
    def group() -> None:
        """Validate, import, export, and inspect versioned registry artifacts."""

    @group.command("list")
    def list_command() -> None:
        _write_json(_request("GET", api_path), None)

    @group.command("validate")
    @click.argument("document", type=click.Path(exists=True, dir_okay=False, path_type=Path))
    def validate_command(document: Path) -> None:
        result = _request("POST", f"{api_path}/validate", json={"document": _read_json(document)})
        _write_json(result, None)
        if not result.get("valid"):
            raise click.exceptions.Exit(1)

    @group.command("import")
    @click.argument("document", type=click.Path(exists=True, dir_okay=False, path_type=Path))
    @click.option("--duplicate-built-in", "duplicate_builtin", is_flag=True, default=False)
    def import_command(document: Path, duplicate_builtin: bool) -> None:
        result = _request(
            "POST",
            f"{api_path}/import",
            headers=_operator_headers(),
            json={
                "document": _read_json(document),
                "duplicate_builtin": duplicate_builtin,
            },
        )
        _write_json(result, None)

    @group.command("export")
    @click.argument("artifact_id")
    @click.option("--output", type=click.Path(dir_okay=False, path_type=Path))
    def export_command(artifact_id: str, output: Optional[Path]) -> None:
        _write_json(_request("GET", f"{api_path}/{artifact_id}/export"), output)

    @group.command("example")
    @click.option("--output", type=click.Path(dir_okay=False, path_type=Path))
    def example_command(output: Optional[Path]) -> None:
        name = "profile" if group_name == "profiles" else "provider-config"
        _write_json(_request("GET", f"/examples/v1/{name}"), output)

    @group.command("schema")
    @click.option("--output", type=click.Path(dir_okay=False, path_type=Path))
    def schema_command(output: Optional[Path]) -> None:
        name = "profile" if group_name == "profiles" else "provider-config"
        _write_json(_request("GET", f"/schemas/v1/{name}"), output)

    return group


profiles = _artifact_commands("profiles", "/api/v1/profiles")
providers = _artifact_commands("providers", "/api/v1/providers")


@click.group()
def operator() -> None:
    """Provision the local OS-backed operator boundary."""


@operator.command("create-verifier")
@click.option(
    "--output",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="New verifier JSON file; run as the OS operator/root principal.",
)
def create_operator_verifier(output: Path) -> None:
    """Create a scrypt verifier; plaintext is never written or printed."""
    from cli_agent_orchestrator.services.operator_auth_service import build_operator_verifier

    first = click.prompt("New operator secret", hide_input=True, confirmation_prompt=True)
    try:
        verifier = build_operator_verifier(first)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    rendered = json.dumps(verifier, indent=2, sort_keys=True) + "\n"
    try:
        with output.open("x", encoding="utf-8") as handle:
            handle.write(rendered)
        output.chmod(0o440)
    except OSError as exc:
        raise click.ClickException(f"Could not create verifier: {exc}") from exc
    click.echo(f"Created verifier at {output}; configure THREADCELLS_OPERATOR_VERIFIER_FILE.")
