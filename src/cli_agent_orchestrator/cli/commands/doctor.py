"""Read-only local prerequisite check for ThreadCells."""

from __future__ import annotations

import json
import platform
import shutil
import sys
from importlib.metadata import PackageNotFoundError, version

import click


def _package_version() -> str:
    try:
        return version("threadcells")
    except PackageNotFoundError:
        return "source checkout"


def _command_check(name: str) -> dict[str, object]:
    path = shutil.which(name)
    return {
        "name": name,
        "ok": path is not None,
        "detail": "found on PATH" if path else "missing from PATH",
    }


def doctor_report() -> dict[str, object]:
    """Return deterministic, non-mutating local prerequisite evidence."""
    checks = [
        {"name": "platform", "ok": platform.system() == "Linux", "detail": platform.system()},
        {
            "name": "python",
            "ok": sys.version_info >= (3, 10),
            "detail": ".".join(map(str, sys.version_info[:3])),
        },
        _command_check("codex"),
        _command_check("tmux"),
        _command_check("git"),
        _command_check("uv"),
    ]
    return {
        "product": "ThreadCells",
        "version": _package_version(),
        "read_only": True,
        "checks": checks,
        "ready": all(bool(check["ok"]) for check in checks),
    }


@click.command()
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable evidence.")
def doctor(as_json: bool) -> None:
    """Check local prerequisites without starting or changing anything."""
    report = doctor_report()
    if as_json:
        click.echo(json.dumps(report, sort_keys=True))
    else:
        click.echo(f"{report['product']} doctor ({report['version']})")
        for check in report["checks"]:
            state = "ok" if check["ok"] else "missing"
            click.echo(f"{state:7} {check['name']}: {check['detail']}")
        click.echo("read-only: no service, state, credential, or network change was made")
    if not report["ready"]:
        raise click.exceptions.Exit(1)
