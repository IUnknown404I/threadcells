"""Focused Projects P1 service tests."""

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from cli_agent_orchestrator.models.project import Project
from cli_agent_orchestrator.services import project_service


def test_project_authority_changes_hold_the_context_lifecycle_fence(tmp_path: Path):
    project = Project(
        projectId="6d7606b5-32ba-4f6b-8b1b-edc4ac0cbd80",
        name="Project",
        path=str(tmp_path.resolve()),
    )
    events: list[str] = []

    @contextmanager
    def fence():
        events.append("fence-enter")
        yield True
        events.append("fence-exit")

    def create(**_kwargs):
        events.append("create")
        return project

    def update(*_args, **_kwargs):
        events.append("update")
        return project

    with (
        patch(
            "cli_agent_orchestrator.services.operations_service.context_lifecycle_fence",
            fence,
        ),
        patch(
            "cli_agent_orchestrator.services.project_service.database.create_project",
            side_effect=create,
        ),
    ):
        project_service.create_project(name="Project", path=str(tmp_path))
    assert events == ["fence-enter", "create", "fence-exit"]

    events.clear()
    with (
        patch(
            "cli_agent_orchestrator.services.operations_service.context_lifecycle_fence",
            fence,
        ),
        patch(
            "cli_agent_orchestrator.services.project_service.database.get_project",
            return_value=project,
        ),
        patch(
            "cli_agent_orchestrator.services.project_service.database.update_project",
            side_effect=update,
        ),
    ):
        project_service.update_project(project.id, path=str(tmp_path))
    assert events == ["fence-enter", "update", "fence-exit"]


def test_final_component_creation_never_creates_missing_parents(tmp_path: Path):
    created = project_service.validate_project_path(
        str(tmp_path / "registered-project"), create_directory=True
    )
    assert created == str((tmp_path / "registered-project").resolve())
    with pytest.raises(ValueError, match="parent directory must already exist"):
        project_service.validate_project_path(
            str(tmp_path / "missing-parent" / "child"), create_directory=True
        )


def test_launch_context_requires_valid_authoritative_project_id(tmp_path: Path):
    project = Project(
        projectId="6d7606b5-32ba-4f6b-8b1b-edc4ac0cbd80",
        name="CAO",
        path=str(tmp_path),
        isDefault=True,
    )
    with patch(
        "cli_agent_orchestrator.services.project_service.database.get_project", return_value=project
    ):
        path, context = project_service.launch_context(project.id)
    assert path == str(tmp_path.resolve())
    assert context == {"id": project.id, "name": "CAO", "path": str(tmp_path.resolve())}
    with pytest.raises(project_service.ProjectResolutionError, match="Invalid projectId"):
        project_service.launch_context("not-a-project-id")


def test_omitted_project_id_preserves_legacy_working_directory_fallback():
    assert project_service.launch_context(None) == (None, None)


def test_add_agent_precedence_never_uses_global_default(tmp_path: Path):
    project_a = Project(
        projectId="6d7606b5-32ba-4f6b-8b1b-edc4ac0cbd80", name="A", path=str(tmp_path / "a")
    )
    project_b = Project(
        projectId="7d7606b5-32ba-4f6b-8b1b-edc4ac0cbd80",
        name="B",
        path=str(tmp_path / "b"),
        isDefault=True,
    )
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    projects = {project_a.id: project_a, project_b.id: project_b}
    with (
        patch(
            "cli_agent_orchestrator.services.project_service.database.get_project",
            side_effect=projects.get,
        ),
        patch(
            "cli_agent_orchestrator.services.project_service.database.get_session_project_id",
            return_value=project_a.id,
        ),
    ):
        # Session project A wins even when B is the global default.
        assert (
            project_service.resolve_add_agent_context("session", None, str(tmp_path / "b"))[1]["id"]
            == project_a.id
        )
    with (
        patch(
            "cli_agent_orchestrator.services.project_service.database.get_project",
            side_effect=projects.get,
        ),
        patch(
            "cli_agent_orchestrator.services.project_service.database.get_session_project_id",
            return_value=None,
        ),
        patch(
            "cli_agent_orchestrator.services.project_service.database.find_project_by_normalized_path",
            return_value=project_a,
        ),
    ):
        # Exact legacy cwd A wins; there is no default-project lookup.
        assert (
            project_service.resolve_add_agent_context("session", None, str(tmp_path / "a"))[1]["id"]
            == project_a.id
        )
        # A deliberate picker selection B overrides all inheritance.
        assert (
            project_service.resolve_add_agent_context("session", project_b.id, str(tmp_path / "a"))[
                1
            ]["id"]
            == project_b.id
        )


def test_stale_registry_path_is_maintainable_but_not_launchable(tmp_path: Path):
    project = Project(projectId="6d7606b5-32ba-4f6b-8b1b-edc4ac0cbd80", name="Stale", path="/gone")
    valid_path = str(tmp_path.resolve())
    with (
        patch(
            "cli_agent_orchestrator.services.project_service.database.get_project",
            return_value=project,
        ),
        patch(
            "cli_agent_orchestrator.services.project_service.validate_project_path",
            return_value=valid_path,
        ) as validate,
        patch(
            "cli_agent_orchestrator.services.project_service.database.update_project",
            return_value=project,
        ) as update,
    ):
        assert project_service.get_registered_project(project.id) == project
        project_service.update_project(project.id, path=valid_path)
        validate.assert_called_once_with(valid_path)
        assert update.call_args.kwargs["path"] == valid_path
    with (
        patch(
            "cli_agent_orchestrator.services.project_service.database.get_project",
            return_value=project,
        ),
        patch(
            "cli_agent_orchestrator.services.project_service.validate_project_path",
            side_effect=ValueError("missing"),
        ),
    ):
        with pytest.raises(ValueError, match="missing"):
            project_service.launch_context(project.id)
