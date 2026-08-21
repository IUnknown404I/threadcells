"""Focused runtime branding API contract tests."""

from unittest.mock import patch


def test_runtime_branding_read_update_upload_and_reset(client):
    current = {
        "title": "ThreadCells",
        "subtitle": "Multi-agent control plane",
        "logoUrl": "/threadcells-symbol.png",
        "customLogo": False,
    }
    changed = {**current, "title": "Team CAO"}
    custom = {**changed, "logoUrl": "/settings/branding/logo?v=abc", "customLogo": True}
    with patch("cli_agent_orchestrator.api.main.branding_service") as service:
        service.get_branding.return_value = current
        service.update_branding.return_value = changed
        service.upload_logo.return_value = custom
        service.reset_logo.return_value = changed
        assert client.get("/settings/branding").json() == current
        assert client.patch("/settings/branding", json={"title": "Team CAO"}).json() == changed
        assert (
            client.post(
                "/settings/branding/logo",
                data=b"\x89PNG\r\n\x1a\nbody",
                headers={"content-type": "image/png"},
            ).json()
            == custom
        )
        assert client.post("/settings/branding/logo/reset").json() == changed
    service.upload_logo.assert_called_once_with(b"\x89PNG\r\n\x1a\nbody", "image/png")


def test_runtime_branding_logo_rejects_invalid_payload(client):
    with patch("cli_agent_orchestrator.api.main.branding_service") as service:
        service.upload_logo.side_effect = ValueError("Logo must be a valid PNG or WebP image")
        response = client.post(
            "/settings/branding/logo", data=b"<svg/>", headers={"content-type": "image/png"}
        )
    assert response.status_code == 400
    assert response.json()["detail"] == "Logo must be a valid PNG or WebP image"
