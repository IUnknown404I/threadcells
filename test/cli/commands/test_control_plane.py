import json
import stat
from unittest.mock import patch

from click.testing import CliRunner

from cli_agent_orchestrator.cli.main import cli


def test_create_operator_verifier_accepts_exactly_five_characters(tmp_path):
    output = tmp_path / "operator-verifier.json"

    result = CliRunner().invoke(
        cli,
        ["operator", "create-verifier", "--output", str(output)],
        input="A7!qz\nA7!qz\n",
    )

    assert result.exit_code == 0
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert document["algorithm"] == "scrypt"
    assert "A7!qz" not in output.read_text(encoding="utf-8")
    assert stat.S_IMODE(output.stat().st_mode) == 0o440


def test_create_operator_verifier_rejects_four_characters(tmp_path):
    output = tmp_path / "operator-verifier.json"

    result = CliRunner().invoke(
        cli,
        ["operator", "create-verifier", "--output", str(output)],
        input="A7!q\nA7!q\n",
    )

    assert result.exit_code != 0
    assert "5 to 4096 characters" in result.output
    assert not output.exists()


def test_profile_validate_uses_versioned_api_and_surfaces_pointer_issues(tmp_path):
    document = tmp_path / "profile.json"
    document.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    result_document = {
        "valid": False,
        "issues": [{"pointer": "/profile_id", "code": "missing", "message": "required"}],
    }
    with patch(
        "cli_agent_orchestrator.cli.commands.control_plane._request",
        return_value=result_document,
    ) as request:
        result = CliRunner().invoke(cli, ["profiles", "validate", str(document)])

    assert result.exit_code == 1
    assert '"pointer": "/profile_id"' in result.output
    request.assert_called_once_with(
        "POST", "/api/v1/profiles/validate", json={"document": {"schema_version": 1}}
    )


def test_provider_import_uses_same_versioned_service_and_operator_boundary(tmp_path):
    document = tmp_path / "provider.json"
    artifact = {"schema_version": 1, "config_id": "custom", "adapter_id": "codex"}
    document.write_text(json.dumps(artifact), encoding="utf-8")
    with (
        patch(
            "cli_agent_orchestrator.cli.commands.control_plane._operator_headers",
            return_value={"Authorization": "Bearer operator"},
        ),
        patch(
            "cli_agent_orchestrator.cli.commands.control_plane._request",
            return_value={"revision_id": "revision-1"},
        ) as request,
    ):
        result = CliRunner().invoke(cli, ["providers", "import", str(document)])

    assert result.exit_code == 0
    request.assert_called_once_with(
        "POST",
        "/api/v1/providers/import",
        headers={"Authorization": "Bearer operator"},
        json={"document": artifact, "duplicate_builtin": False},
    )


def test_profile_example_and_schema_are_portable_json_commands():
    with patch(
        "cli_agent_orchestrator.cli.commands.control_plane._request",
        side_effect=[{"profile_id": "example"}, {"title": "ProfileDefinition V1"}],
    ) as request:
        example = CliRunner().invoke(cli, ["profiles", "example"])
        schema = CliRunner().invoke(cli, ["profiles", "schema"])

    assert example.exit_code == 0 and '"profile_id": "example"' in example.output
    assert schema.exit_code == 0 and '"title": "ProfileDefinition V1"' in schema.output
    assert [call.args for call in request.call_args_list] == [
        ("GET", "/examples/v1/profile"),
        ("GET", "/schemas/v1/profile"),
    ]


def test_registry_cli_refuses_non_json_secret_style_input(tmp_path):
    document = tmp_path / ".env"
    document.write_text("SECRET=value\n", encoding="utf-8")

    result = CliRunner().invoke(cli, ["profiles", "validate", str(document)])

    assert result.exit_code != 0
    assert "explicit .json document" in result.output
