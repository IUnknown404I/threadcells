"""Validation and persistence invariants for runtime-logo payloads."""

import base64
import hashlib
import struct
import zlib

import pytest

from cli_agent_orchestrator.clients.database import RuntimeBrandingModel, SessionLocal
from cli_agent_orchestrator.services.branding_service import MAX_LOGO_BYTES, _image_type


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _png() -> bytes:
    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\xff"))
        + _chunk(b"IEND", b"")
    )


def _indexed_png(
    palette: bytes | None, *, bit_depth: int = 1, palette_after_idat: bool = False
) -> bytes:
    header = struct.pack(">IIBBBBB", 1, 1, bit_depth, 3, 0, 0, 0)
    chunks = [_chunk(b"IHDR", header)]
    if palette is not None and not palette_after_idat:
        chunks.append(_chunk(b"PLTE", palette))
    chunks.append(_chunk(b"IDAT", zlib.compress(b"\x00\x00")))
    if palette is not None and palette_after_idat:
        chunks.append(_chunk(b"PLTE", palette))
    return b"\x89PNG\r\n\x1a\n" + b"".join(chunks) + _chunk(b"IEND", b"")


def _webp() -> bytes:
    # Deterministic 1x1 WebP emitted by Chromium; it exercises an extended
    # container and VP8L image payload rather than a signature-only shell.
    return base64.b64decode(
        "UklGRv4BAABXRUJQVlA4WAoAAAAgAAAAAAAAAAAASUNDUMgBAAAAAAHIAAAAAAQwAABtbnRyUkdCIFhZWiAH4AABAAEAAAAAAABhY3NwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAA9tYAAQAAAADTLQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAlkZXNjAAAA8AAAACRyWFlaAAABFAAAABRnWFlaAAABKAAAABRiWFlaAAABPAAAABR3dHB0AAABUAAAABRyVFJDAAABZAAAAChnVFJDAAABZAAAAChiVFJDAAABZAAAAChjcHJ0AAABjAAAADxtbHVjAAAAAAAAAAEAAAAMZW5VUwAAAAgAAAAcAHMAUgBHAEJYWVogAAAAAAAAb6IAADj1AAADkFhZWiAAAAAAAABimQAAt4UAABjaWFlaIAAAAAAAACSgAAAPhAAAts9YWVogAAAAAAAA9tYAAQAAAADTLXBhcmEAAAAAAAQAAAACZmYAAPKnAAANWQAAE9AAAApbAAAAAAAAAABtbHVjAAAAAAAAAAEAAAAMZW5VUwAAACAAAAAcAEcAbwBvAGcAbABlACAASQBuAGMALgAgADIAMAAxADZWUDhMDwAAAC8AAAAAB1DViP4HIqL/AQA="
    )


def _state() -> tuple[str | None, str | None, str | None]:
    with SessionLocal() as db:
        row = db.get(RuntimeBrandingModel, 1)
        assert row is not None
        return row.logo_filename, row.logo_hash, row.logo_content_type


def test_retired_default_title_is_migrated_without_changing_operator_logo() -> None:
    from cli_agent_orchestrator.services import branding_service

    branding_service.get_branding()
    with SessionLocal() as db:
        row = db.get(RuntimeBrandingModel, 1)
        assert row is not None
        row.title = branding_service.LEGACY_DEFAULT_TITLE
        row.subtitle = "Operator subtitle"
        row.logo_filename = "logo.png"
        row.logo_hash = "abc"
        row.logo_content_type = "image/png"
        db.commit()

    payload = branding_service.get_branding()

    assert payload == {
        "title": "ThreadCells",
        "subtitle": "Operator subtitle",
        "logoUrl": "/settings/branding/logo?v=abc",
        "customLogo": True,
    }
    with SessionLocal() as db:
        row = db.get(RuntimeBrandingModel, 1)
        assert row is not None
        assert row.title == "ThreadCells"
        assert row.subtitle == "Operator subtitle"
        assert row.logo_filename == "logo.png"


def test_indexed_png_with_valid_palette_uploads(tmp_path, monkeypatch):
    from cli_agent_orchestrator.services import branding_service

    monkeypatch.setattr(branding_service, "LOGO_DIR", tmp_path)
    uploaded = branding_service.upload_logo(_indexed_png(b"\x00\x00\x00\xff\xff\xff"), "image/png")

    assert uploaded["customLogo"] is True
    assert uploaded["logoUrl"] is not None
    assert len(list(tmp_path.iterdir())) == 1


def test_indexed_png_without_plte_preserves_state_and_files(tmp_path, monkeypatch):
    from cli_agent_orchestrator.services import branding_service

    monkeypatch.setattr(branding_service, "LOGO_DIR", tmp_path)
    uploaded = branding_service.upload_logo(_png(), "image/png")
    invalid = _indexed_png(None)
    before_state = _state()
    before_files = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    with pytest.raises(ValueError, match="palette"):
        branding_service.upload_logo(invalid, "image/png")

    assert _state() == before_state
    assert branding_service.get_branding() == uploaded
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before_files
    assert not (tmp_path / f"logo-{hashlib.sha256(invalid).hexdigest()}.png").exists()


@pytest.mark.parametrize(
    "payload",
    [
        _indexed_png(b"\x00"),
        _indexed_png(b"\x00\x00\x00", palette_after_idat=True),
        _indexed_png(b"\x00\x00\x00" * 3, bit_depth=1),
        _indexed_png(b"\x00\x00\x00" * 257, bit_depth=8),
    ],
    ids=["invalid-length", "plte-after-idat", "bit-depth-capacity", "more-than-256"],
)
def test_invalid_indexed_png_upload_preserves_existing_logo_state_and_file_bytes(
    tmp_path, monkeypatch, payload
):
    from cli_agent_orchestrator.services import branding_service

    monkeypatch.setattr(branding_service, "LOGO_DIR", tmp_path)
    uploaded = branding_service.upload_logo(_png(), "image/png")
    before_state = _state()
    before_files = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    with pytest.raises(ValueError, match="palette"):
        branding_service.upload_logo(payload, "image/png")

    assert branding_service.get_branding() == uploaded
    assert _state() == before_state
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before_files


def test_image_type_accepts_complete_png_and_webp():
    assert _image_type(_png()) == ("png", "image/png")
    assert _image_type(_webp()) == ("webp", "image/webp")


@pytest.mark.parametrize(
    "payload",
    [
        b"\x89PNG\r\n\x1a\nnot-an-image",
        b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", b"\x00" * 13),
        b"RIFF\x04\x00\x00\x00WEBP",
        # Exact 32-byte RIFF/WEBP/VP8 shell: dimensions/signature only, no
        # complete boolean-coded first partition.
        b"RIFF\x18\x00\x00\x00WEBPVP8 \x0b\x00\x00\x00\x00\x00\x00\x9d\x01\x2a\x01\x00\x01\x00\x00\x00",
        b"<svg xmlns='http://www.w3.org/2000/svg'/>",
        b"<html>not an image</html>",
        _png()[:-1],
        _png() + b"x",
    ],
)
def test_image_type_rejects_magic_junk_truncation_and_text(payload: bytes):
    with pytest.raises(ValueError):
        _image_type(payload)


def test_upload_size_limit_is_bounded():
    from cli_agent_orchestrator.services import branding_service

    with pytest.raises(ValueError):
        branding_service.upload_logo(_png() + b"x" * MAX_LOGO_BYTES)


def test_failed_upload_preserves_existing_logo_file_and_state(tmp_path, monkeypatch):
    from cli_agent_orchestrator.services import branding_service

    monkeypatch.setattr(branding_service, "LOGO_DIR", tmp_path)
    uploaded = branding_service.upload_logo(_png(), "image/png")
    before_state = _state()
    before_files = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    for invalid, content_type in (
        (b"<svg/>", "image/png"),
        (_png()[:-1], "image/png"),
        (_webp()[:-1], "image/webp"),
        (_png(), "image/webp"),
    ):
        with pytest.raises(ValueError):
            branding_service.upload_logo(invalid, content_type)
        assert branding_service.get_branding() == uploaded
        assert _state() == before_state
        assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before_files
