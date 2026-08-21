"""Project registry, path validation, and authoritative launch resolution."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from cli_agent_orchestrator.clients import database
from cli_agent_orchestrator.models.project import Project


class ProjectResolutionError(ValueError):
    """A supplied project ID is invalid or no longer exists."""


def _normalized_name(name: str) -> str:
    normalized = " ".join(name.split()).casefold()
    if not normalized:
        raise ValueError("Project name is required")
    return normalized


def validate_project_path(path: str, *, create_directory: bool = False) -> str:
    """Return a canonical existing project directory, optionally creating one final leaf.

    Creation deliberately never creates parents: callers can only add one safe
    final path component under an existing directory. This prevents a registry
    request from implicitly materializing an arbitrary tree.
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError("Project path is required")
    candidate = Path(os.path.expanduser(path.strip()))
    if not candidate.is_absolute():
        raise ValueError("Project path must be absolute")
    if candidate.exists():
        resolved = candidate.resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError("Project path must be a directory")
        return str(resolved)
    if not create_directory:
        raise ValueError(
            "Project path does not exist; enable final-directory creation to create it"
        )
    if candidate.name in {"", ".", ".."}:
        raise ValueError("Project path must name one final directory component")
    parent = candidate.parent
    if not parent.exists() or not parent.is_dir():
        raise ValueError(
            "Project parent directory must already exist; CAO creates only the final component"
        )
    resolved_parent = parent.resolve(strict=True)
    target = resolved_parent / candidate.name
    # A resolved target must remain a direct child; this also rejects path
    # traversal that survives textual normalization.
    if target.parent != resolved_parent:
        raise ValueError("Project path may create only one final directory component")
    target.mkdir(mode=0o755)
    return str(target.resolve(strict=True))


def create_project(
    *,
    name: str,
    path: str,
    description: str | None = None,
    is_default: bool = False,
    create_directory: bool = False,
) -> Project:
    clean_name = " ".join(name.split())
    normalized_name = _normalized_name(clean_name)
    resolved_path = validate_project_path(path, create_directory=create_directory)
    clean_description = (
        description.strip() if isinstance(description, str) and description.strip() else None
    )
    return database.create_project(
        project_id=str(uuid.uuid4()),
        name=clean_name,
        normalized_name=normalized_name,
        path=resolved_path,
        normalized_path=os.path.normcase(resolved_path),
        description=clean_description,
        is_default=is_default,
    )


def list_projects() -> list[Project]:
    return database.list_projects()


def get_registered_project(project_id: str) -> Project:
    """Read a project row without touching its filesystem path.

    Registry maintenance must remain possible after a directory has gone
    away.  Call :func:`resolve_project` at launch boundaries instead.
    """
    try:
        uuid.UUID(project_id)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ProjectResolutionError(
            f"Invalid projectId '{project_id}'. Select a registered project."
        ) from exc
    project = database.get_project(project_id)
    if project is None:
        raise ProjectResolutionError(
            f"Unknown projectId '{project_id}'. Select a registered project."
        )
    return project


def resolve_project(project_id: str) -> Project:
    """Resolve a registered project for a launch, including path validation."""
    project = get_registered_project(project_id)
    # A historical project may point to a now-missing path. Refuse a new
    # launch rather than silently falling back to a different directory.
    resolved_path = validate_project_path(project.path)
    return project.model_copy(update={"path": resolved_path})


def set_default_project(project_id: str) -> Project:
    get_registered_project(project_id)
    project = database.set_default_project(project_id)
    if project is None:
        raise ProjectResolutionError(
            f"Unknown projectId '{project_id}'. Select a registered project."
        )
    return project


def delete_project(project_id: str) -> bool:
    get_registered_project(project_id)
    return database.delete_project(project_id)


def update_project(
    project_id: str,
    *,
    name: str | None = None,
    path: str | None = None,
    description: str | None = None,
    is_default: bool | None = None,
) -> Project:
    """Edit registry metadata only.  No filesystem move or historical rewrite occurs."""
    current = get_registered_project(project_id)
    clean_name = " ".join((name if name is not None else current.name).split())
    normalized_name = _normalized_name(clean_name)
    # Existing metadata is deliberately not revalidated: a stale row must be
    # repairable or removable.  Only a newly requested path is a filesystem
    # action and therefore requires launch-grade validation.
    resolved_path = validate_project_path(path) if path is not None else current.path
    clean_description = (
        description.strip() if isinstance(description, str) and description.strip() else None
    )
    updated = database.update_project(
        project_id,
        name=clean_name,
        normalized_name=normalized_name,
        path=resolved_path,
        normalized_path=os.path.normcase(resolved_path),
        description=clean_description,
        is_default=is_default,
    )
    if updated is None:
        raise ProjectResolutionError(
            f"Unknown projectId '{project_id}'. Select a registered project."
        )
    return updated


def launch_context(project_id: str | None) -> tuple[str | None, dict[str, str] | None]:
    """Resolve an explicit project ID; missing ID preserves legacy cwd behavior."""
    if project_id is None:
        return None, None
    project = resolve_project(project_id)
    context = {"id": project.id, "name": project.name, "path": project.path}
    if project.description:
        context["description"] = project.description
    return project.path, context


def resolve_add_agent_context(
    session_name: str,
    explicit_project_id: str | None,
    legacy_working_directory: str | None,
) -> tuple[str | None, dict[str, str] | None]:
    """Apply the explicit/session/exact-path/legacy-cwd precedence without defaults."""
    if explicit_project_id is not None:
        return launch_context(explicit_project_id)
    session_project_id = database.get_session_project_id(session_name)
    if session_project_id:
        try:
            return launch_context(session_project_id)
        except ProjectResolutionError:
            # A deleted registry entry must not make a new terminal inherit an
            # unrelated default project.  Continue only through legacy rules.
            pass
    if legacy_working_directory:
        try:
            canonical = validate_project_path(legacy_working_directory)
            match = database.find_project_by_normalized_path(os.path.normcase(canonical))
            if match is not None:
                context = {"id": match.id, "name": match.name, "path": match.path}
                if match.description:
                    context["description"] = match.description
                return match.path, context
        except ValueError:
            pass
    return legacy_working_directory, None
