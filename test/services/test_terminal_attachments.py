import os
import stat
import time

import pytest

from cli_agent_orchestrator.services import terminal_attachments

PNG = b"\x89PNG\r\n\x1a\nfixture"
JPEG = b"\xff\xd8\xfffixture"
WEBP = b"RIFF\x00\x00\x00\x00WEBPfixture"


@pytest.fixture(autouse=True)
def attachment_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(terminal_attachments, "TERMINAL_ATTACHMENTS_DIR", tmp_path / "attachments")


@pytest.mark.parametrize(
    ("mime_type", "content", "extension"),
    [("image/png", PNG, "png"), ("image/jpeg", JPEG, "jpg"), ("image/webp", WEBP, "webp")],
)
def test_stores_matching_supported_image_in_private_terminal_directory(
    mime_type, content, extension
):
    path = terminal_attachments.store_terminal_image("abcd1234", mime_type, content)

    assert path.is_absolute()
    assert path.parent.name == "abcd1234"
    assert path.suffix == f".{extension}"
    assert path.read_bytes() == content
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o750
    assert path.stat().st_uid == os.geteuid()


@pytest.mark.parametrize(
    ("mime_type", "content"),
    [
        ("image/gif", PNG),
        ("image/png", JPEG),
        ("image/jpeg", b"not an image"),
        ("image/webp", b"RIFF\x00\x00\x00\x00NOPE"),
    ],
)
def test_rejects_unsupported_mime_or_mismatched_magic(mime_type, content):
    with pytest.raises(terminal_attachments.UnsupportedTerminalImage):
        terminal_attachments.store_terminal_image("abcd1234", mime_type, content)


def test_rejects_oversized_image_before_writing():
    with pytest.raises(terminal_attachments.TerminalImageTooLarge):
        terminal_attachments.store_terminal_image(
            "abcd1234", "image/png", PNG + b"x" * terminal_attachments.MAX_IMAGE_BYTES
        )


@pytest.mark.parametrize(
    "filename",
    [
        "notes.md",
        "привет.md",
        "Sample Project — title.md",
        "日本語.md",
        "emoji 😀%&.md",
        "notes.txt",
        "data.json",
        "config.yaml",
        "config.yml",
        "data.csv",
        "run.log",
    ],
)
def test_stores_utf8_text_file_at_a_generated_private_path(filename):
    content = (
        b'\xef\xbb\xbf{"valid": true}\n' if filename == "data.json" else b"\xef\xbb\xbfhello\n"
    )
    path = terminal_attachments.store_terminal_file("abcd1234", filename, content)

    assert path.is_absolute()
    assert path.parent.name == "abcd1234"
    assert path.suffix == filename[filename.rfind(".") :]
    assert path.name != filename
    assert path.read_bytes() == content
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_stores_zip_as_opaque_bytes_at_a_generated_private_path():
    content = b"PK\x03\x04\x00\xff\x00opaque archive bytes\x00"

    path = terminal_attachments.store_terminal_file("abcd1234", "bundle.zip", content)

    assert path.is_absolute()
    assert path.parent.name == "abcd1234"
    assert path.suffix == ".zip"
    assert path.read_bytes() == content
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_accepts_zip_larger_than_ten_mebibytes_and_rejects_zip_larger_than_twenty_five():
    assert (
        terminal_attachments.validate_terminal_file(
            "bundle.zip", b"PK\\x03\\x04" + b"x" * (terminal_attachments.MAX_IMAGE_BYTES + 1)
        )
        == ".zip"
    )
    with pytest.raises(terminal_attachments.TerminalImageTooLarge, match="25 MiB"):
        terminal_attachments.validate_terminal_file(
            "bundle.zip", b"x" * (terminal_attachments.MAX_ARCHIVE_BYTES + 1)
        )


@pytest.mark.parametrize(
    ("filename", "content", "message"),
    [
        ("unsafe.exe", b"hello", "Only MD"),
        ("notes.md", b"binary\x00data", "binary data"),
        ("notes.txt", b"\xff", "UTF-8"),
        ("data.json", b"{", "JSON attachment is invalid"),
    ],
)
def test_rejects_unsafe_text_files(filename, content, message):
    with pytest.raises(terminal_attachments.UnsupportedTerminalFile, match=message):
        terminal_attachments.store_terminal_file("abcd1234", filename, content)


def test_rejects_oversized_text_file_before_writing():
    with pytest.raises(terminal_attachments.TerminalImageTooLarge):
        terminal_attachments.store_terminal_file(
            "abcd1234", "notes.md", b"x" * (terminal_attachments.MAX_IMAGE_BYTES + 1)
        )


def test_text_file_name_cannot_control_the_generated_path():
    path = terminal_attachments.store_terminal_file("abcd1234", "../../outside.md", b"hello")
    assert path.parent.name == "abcd1234"
    assert path.name != "outside.md"


@pytest.mark.parametrize("terminal_id", ["../outside", "abcd1234/../outside", "not-a-terminal"])
def test_rejects_terminal_identifiers_that_could_escape_the_attachment_root(terminal_id):
    with pytest.raises(ValueError, match="Invalid terminal identifier"):
        terminal_attachments.store_terminal_image(terminal_id, "image/png", PNG)


def test_does_not_follow_a_terminal_directory_symlink(tmp_path):
    root = terminal_attachments.TERMINAL_ATTACHMENTS_DIR
    root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "abcd1234").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeError, match="not a directory"):
        terminal_attachments.store_terminal_image("abcd1234", "image/png", PNG)

    assert list(outside.iterdir()) == []


def test_opportunistic_cleanup_removes_only_expired_regular_files():
    terminal_dir = terminal_attachments.TERMINAL_ATTACHMENTS_DIR / "abcd1234"
    terminal_dir.mkdir(parents=True)
    expired = terminal_dir / "expired.png"
    fresh = terminal_dir / "fresh.png"
    expired.write_bytes(PNG)
    fresh.write_bytes(PNG)
    cutoff = time.time() - terminal_attachments.ATTACHMENT_MAX_AGE_SECONDS - 1
    os.utime(expired, (cutoff, cutoff))

    assert terminal_attachments.cleanup_expired_terminal_attachments() == 1
    assert not expired.exists()
    assert fresh.exists()


def test_cleanup_does_not_follow_an_attachment_root_symlink(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "expired.png"
    outside_file.write_bytes(PNG)
    cutoff = time.time() - terminal_attachments.ATTACHMENT_MAX_AGE_SECONDS - 1
    os.utime(outside_file, (cutoff, cutoff))
    unsafe_root = tmp_path / "unsafe-root"
    unsafe_root.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(terminal_attachments, "TERMINAL_ATTACHMENTS_DIR", unsafe_root)

    assert terminal_attachments.cleanup_expired_terminal_attachments() == 0
    assert outside_file.exists()
