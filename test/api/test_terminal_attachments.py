from unittest.mock import patch

import pytest

from cli_agent_orchestrator.services.terminal_attachments import MAX_ARCHIVE_BYTES, MAX_IMAGE_BYTES

PNG = b"\x89PNG\r\n\x1a\nfixture"


def test_image_attachment_validates_terminal_and_returns_generated_absolute_path(client):
    with (
        patch(
            "cli_agent_orchestrator.api.main.get_terminal_metadata", return_value={"id": "abcd1234"}
        ),
        patch(
            "cli_agent_orchestrator.api.main.terminal_attachments.store_terminal_image",
            return_value="/runtime/terminal-attachments/abcd1234/generated.png",
        ) as store,
    ):
        response = client.post(
            "/terminals/abcd1234/attachments/image",
            content=PNG,
            headers={"content-type": "image/png"},
        )

    assert response.status_code == 201
    assert response.json() == {"path": "/runtime/terminal-attachments/abcd1234/generated.png"}
    store.assert_called_once_with("abcd1234", "image/png", PNG)


def test_image_attachment_returns_404_for_unknown_terminal(client):
    with patch("cli_agent_orchestrator.api.main.get_terminal_metadata", return_value=None):
        response = client.post(
            "/terminals/abcd1234/attachments/image",
            content=PNG,
            headers={"content-type": "image/png"},
        )

    assert response.status_code == 404


def test_image_attachment_rejects_unsupported_mime_before_reading(client):
    with patch(
        "cli_agent_orchestrator.api.main.get_terminal_metadata", return_value={"id": "abcd1234"}
    ):
        response = client.post(
            "/terminals/abcd1234/attachments/image",
            content=PNG,
            headers={"content-type": "image/gif"},
        )

    assert response.status_code == 415


def test_image_attachment_rejects_stream_larger_than_ten_mebibytes(client):
    with patch(
        "cli_agent_orchestrator.api.main.get_terminal_metadata", return_value={"id": "abcd1234"}
    ):
        response = client.post(
            "/terminals/abcd1234/attachments/image",
            content=b"x" * (MAX_IMAGE_BYTES + 1),
            headers={"content-type": "image/png"},
        )

    assert response.status_code == 413


def test_text_attachment_validates_terminal_and_returns_generated_absolute_path(client):
    with (
        patch(
            "cli_agent_orchestrator.api.main.get_terminal_metadata", return_value={"id": "abcd1234"}
        ),
        patch(
            "cli_agent_orchestrator.api.main.terminal_attachments.store_terminal_file",
            return_value="/runtime/terminal-attachments/abcd1234/generated.md",
        ) as store,
    ):
        response = client.post(
            "/terminals/abcd1234/attachments/file",
            content=b"# notes\n",
            headers={"content-type": "text/markdown", "x-terminal-filename": "notes.md"},
        )

    assert response.status_code == 201
    assert response.json() == {"path": "/runtime/terminal-attachments/abcd1234/generated.md"}
    store.assert_called_once_with("abcd1234", "notes.md", b"# notes\n")


@pytest.mark.parametrize(
    "content_type", ["application/zip", "application/x-zip-compressed", "application/octet-stream"]
)
def test_zip_attachment_accepts_canonical_and_browser_mime_forms(client, content_type):
    content = b"PK\x03\x04\x00opaque archive bytes\x00"
    with (
        patch(
            "cli_agent_orchestrator.api.main.get_terminal_metadata", return_value={"id": "abcd1234"}
        ),
        patch(
            "cli_agent_orchestrator.api.main.terminal_attachments.store_terminal_file",
            return_value="/runtime/terminal-attachments/abcd1234/generated.zip",
        ) as store,
    ):
        response = client.post(
            "/terminals/abcd1234/attachments/file",
            content=content,
            headers={"content-type": content_type, "x-terminal-filename": "bundle.zip"},
        )

    assert response.status_code == 201
    assert response.json() == {"path": "/runtime/terminal-attachments/abcd1234/generated.zip"}
    store.assert_called_once_with("abcd1234", "bundle.zip", content)


def test_zip_attachment_accepts_stream_larger_than_ten_mebibytes(client):
    content = b"PK\\x03\\x04" + b"x" * (MAX_IMAGE_BYTES + 1)
    with (
        patch(
            "cli_agent_orchestrator.api.main.get_terminal_metadata", return_value={"id": "abcd1234"}
        ),
        patch(
            "cli_agent_orchestrator.api.main.terminal_attachments.store_terminal_file",
            return_value="/runtime/terminal-attachments/abcd1234/generated.zip",
        ) as store,
    ):
        response = client.post(
            "/terminals/abcd1234/attachments/file",
            content=content,
            headers={"content-type": "application/zip", "x-terminal-filename": "bundle.zip"},
        )

    assert response.status_code == 201
    store.assert_called_once_with("abcd1234", "bundle.zip", content)


def test_zip_attachment_rejects_stream_larger_than_twenty_five_mebibytes(client):
    with patch(
        "cli_agent_orchestrator.api.main.get_terminal_metadata", return_value={"id": "abcd1234"}
    ):
        response = client.post(
            "/terminals/abcd1234/attachments/file",
            content=b"PK\\x03\\x04" + b"x" * (MAX_ARCHIVE_BYTES + 1),
            headers={"content-type": "application/zip", "x-terminal-filename": "bundle.zip"},
        )

    assert response.status_code == 413
    assert response.json()["detail"] == "Archive attachment exceeds the 25 MiB limit"


def test_text_attachment_returns_404_for_unknown_terminal(client):
    with patch("cli_agent_orchestrator.api.main.get_terminal_metadata", return_value=None):
        response = client.post(
            "/terminals/abcd1234/attachments/file",
            content=b"# notes\n",
            headers={"x-terminal-filename": "notes.md"},
        )

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("encoded_filename", "filename"),
    [
        ("notes.md", "notes.md"),
        ("%D0%BF%D1%80%D0%B8%D0%B2%D0%B5%D1%82.md", "привет.md"),
        ("Sample%20Project%20%E2%80%94%20title.md", "Sample Project — title.md"),
        ("%E6%97%A5%E6%9C%AC%E8%AA%9E.md", "日本語.md"),
        ("emoji%20%F0%9F%98%80%25%26.md", "emoji 😀%&.md"),
    ],
)
def test_text_attachment_decodes_filename_header_exactly_once(client, encoded_filename, filename):
    with (
        patch(
            "cli_agent_orchestrator.api.main.get_terminal_metadata", return_value={"id": "abcd1234"}
        ),
        patch(
            "cli_agent_orchestrator.api.main.terminal_attachments.store_terminal_file",
            return_value="/runtime/terminal-attachments/abcd1234/generated.md",
        ) as store,
    ):
        response = client.post(
            "/terminals/abcd1234/attachments/file",
            content=b"# notes\n",
            headers={"x-terminal-filename": encoded_filename},
        )

    assert response.status_code == 201
    store.assert_called_once_with("abcd1234", filename, b"# notes\n")


@pytest.mark.parametrize("encoded_filename", ["bad%", "%FF.md", "%00notes.md"])
def test_text_attachment_rejects_malformed_or_invalid_filename_metadata(client, encoded_filename):
    with patch(
        "cli_agent_orchestrator.api.main.get_terminal_metadata", return_value={"id": "abcd1234"}
    ):
        response = client.post(
            "/terminals/abcd1234/attachments/file",
            content=b"# notes\n",
            headers={"x-terminal-filename": encoded_filename},
        )

    assert response.status_code == 400


def test_text_attachment_rejects_unsupported_unicode_extension(client):
    with patch(
        "cli_agent_orchestrator.api.main.get_terminal_metadata", return_value={"id": "abcd1234"}
    ):
        response = client.post(
            "/terminals/abcd1234/attachments/file",
            content=b"# notes\n",
            headers={
                "x-terminal-filename": "%D0%B7%D0%B0%D0%BC%D0%B5%D1%82%D0%BA%D0%B0.%D1%82%D1%85%D1%82"
            },
        )

    assert response.status_code == 415
