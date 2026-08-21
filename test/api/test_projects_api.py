"""Projects P1 HTTP contract tests."""

from pathlib import Path
from unittest.mock import patch

from cli_agent_orchestrator.models.project import Project


def test_projects_api_creates_and_lists_projects(client, tmp_path: Path):
    project = Project(
        projectId="6d7606b5-32ba-4f6b-8b1b-edc4ac0cbd80",
        name="CAO",
        path=str(tmp_path),
        isDefault=True,
    )
    with patch("cli_agent_orchestrator.api.main.project_service") as service:
        service.create_project.return_value = project
        service.list_projects.return_value = [project]
        response = client.post(
            "/projects", json={"name": "CAO", "path": str(tmp_path), "isDefault": True}
        )
        assert response.status_code == 201
        assert response.json()["projectId"] == project.id
        assert client.get("/projects").json()[0]["isDefault"] is True


def test_explicit_invalid_project_id_fails_before_session_creation(client):
    with patch("cli_agent_orchestrator.api.main.session_service") as sessions:
        response = client.post(
            "/sessions",
            params={"provider": "codex", "agent_profile": "developer", "projectId": "invalid"},
        )
    assert response.status_code == 400
    assert "Invalid projectId" in response.json()["detail"]
    sessions.create_session.assert_not_called()


def test_project_id_overrides_legacy_working_directory_for_canonical_admission(
    client, tmp_path: Path
):
    project = Project(
        projectId="6d7606b5-32ba-4f6b-8b1b-edc4ac0cbd80",
        name="CAO",
        path=str(tmp_path),
        isDefault=True,
    )
    with (
        patch(
            "cli_agent_orchestrator.api.main.project_service.launch_context",
            return_value=(
                str(tmp_path),
                {"id": project.id, "name": project.name, "path": str(tmp_path)},
            ),
        ),
        patch("cli_agent_orchestrator.api.main.session_service") as sessions,
    ):
        from cli_agent_orchestrator.models.terminal import Terminal

        sessions.create_session.return_value = Terminal(
            id="abcd1234", name="window", provider="codex", session_name="cao-test"
        )
        response = client.post(
            "/sessions",
            params={
                "provider": "codex",
                "agent_profile": "developer",
                "working_directory": "/ignored",
                "projectId": project.id,
            },
        )
    assert response.status_code == 201
    assert sessions.create_session.call_args.kwargs["working_directory"] == str(tmp_path)
    assert sessions.create_session.call_args.kwargs["project_context"]["id"] == project.id


def test_project_update_is_transactional_registry_only(client, tmp_path: Path):
    original = Project(
        projectId="6d7606b5-32ba-4f6b-8b1b-edc4ac0cbd80",
        name="Before",
        path=str(tmp_path),
        description="old",
        isDefault=True,
    )
    updated = original.model_copy(update={"name": "After", "description": "new"})
    with patch("cli_agent_orchestrator.api.main.project_service") as service:
        service.get_registered_project.return_value = original
        service.update_project.return_value = updated
        response = client.put(
            f"/projects/{original.id}", json={"name": "After", "description": "new"}
        )
    assert response.status_code == 200
    assert response.json()["name"] == "After"
    assert service.update_project.call_args.kwargs["path"] == str(tmp_path)


def test_stale_registry_path_can_list_update_and_delete_without_launch_validation(client):
    stale = Project(projectId="6d7606b5-32ba-4f6b-8b1b-edc4ac0cbd80", name="Stale", path="/gone")
    repaired = stale.model_copy(update={"path": "/valid"})
    with patch("cli_agent_orchestrator.api.main.project_service") as service:
        service.list_projects.return_value = [stale]
        service.get_registered_project.return_value = stale
        service.update_project.return_value = repaired
        service.delete_project.return_value = True
        assert client.get("/projects").status_code == 200
        assert client.put(f"/projects/{stale.id}", json={"path": "/valid"}).status_code == 200
        assert client.delete(f"/projects/{stale.id}").json() == {"success": True}
    service.resolve_project.assert_not_called()
