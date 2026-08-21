"""Runtime branding persisted outside bundled web assets."""

from __future__ import annotations

import hashlib
import os
import struct
import zlib
from pathlib import Path

from cli_agent_orchestrator.clients.database import (
    RuntimeBrandingModel,
    SessionLocal,
    _ensure_runtime_branding_schema,
)
from cli_agent_orchestrator.constants import DB_DIR

DEFAULT_TITLE = "ThreadCells"
DEFAULT_SUBTITLE = "Multi-agent control plane"
LEGACY_DEFAULT_TITLE = "ThreadMesh"
MAX_LOGO_BYTES = 2 * 1024 * 1024
LOGO_DIR = DB_DIR / "runtime-branding"
MAX_LOGO_DIMENSION = 16_384
MAX_DECODED_LOGO_BYTES = 32 * 1024 * 1024


def _row() -> RuntimeBrandingModel:
    _ensure_runtime_branding_schema()
    with SessionLocal() as db:
        row = db.get(RuntimeBrandingModel, 1)
        if row is None:
            row = RuntimeBrandingModel(id=1, title=DEFAULT_TITLE, subtitle=DEFAULT_SUBTITLE)
            db.add(row)
            db.commit()
        elif row.title == LEGACY_DEFAULT_TITLE:
            # Migrate only the retired product default. Other operator-owned
            # branding remains untouched.
            row.title = DEFAULT_TITLE
            db.commit()
        db.refresh(row)
        db.expunge(row)
        return row


def _payload(row: RuntimeBrandingModel) -> dict[str, str | None]:
    logo_url = (
        f"/settings/branding/logo?v={row.logo_hash}" if row.logo_hash else "/threadcells-symbol.png"
    )
    return {
        "title": row.title,
        "subtitle": row.subtitle,
        "logoUrl": logo_url,
        "customLogo": bool(row.logo_hash),
    }


def get_branding() -> dict[str, str | None]:
    return _payload(_row())


def update_branding(
    *, title: str | None = None, subtitle: str | None = None
) -> dict[str, str | None]:
    _ensure_runtime_branding_schema()
    with SessionLocal() as db:
        row = db.get(RuntimeBrandingModel, 1) or RuntimeBrandingModel(
            id=1, title=DEFAULT_TITLE, subtitle=DEFAULT_SUBTITLE
        )
        if row not in db:
            db.add(row)
        if title is not None:
            value = " ".join(title.split())
            if not value or len(value) > 80:
                raise ValueError("Runtime title must contain 1–80 characters")
            row.title = value
        if subtitle is not None:
            value = " ".join(subtitle.split())
            if not value or len(value) > 160:
                raise ValueError("Runtime subtitle must contain 1–160 characters")
            row.subtitle = value
        db.commit()
        db.refresh(row)
        db.expunge(row)
        return _payload(row)


def _check_dimensions(width: int, height: int) -> None:
    if not 0 < width <= MAX_LOGO_DIMENSION or not 0 < height <= MAX_LOGO_DIMENSION:
        raise ValueError("Logo dimensions are invalid or exceed the allowed bounds")


def _png_scanline_bytes(
    width: int, height: int, bit_depth: int, color_type: int, interlace: int
) -> int:
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]

    def row_bytes(row_width: int) -> int:
        return 1 + (row_width * channels * bit_depth + 7) // 8

    if interlace == 0:
        return height * row_bytes(width)
    # Adam7 passes, including one filter byte per nonempty pass row.
    total = 0
    for x0, y0, dx, dy in (
        (0, 0, 8, 8),
        (4, 0, 8, 8),
        (0, 4, 4, 8),
        (2, 0, 4, 4),
        (0, 2, 2, 4),
        (1, 0, 2, 2),
        (0, 1, 1, 2),
    ):
        pass_width = (width - x0 + dx - 1) // dx if width > x0 else 0
        pass_height = (height - y0 + dy - 1) // dy if height > y0 else 0
        total += pass_height * row_bytes(pass_width)
    return total


def _validate_png_scanlines(
    decoded: bytes, width: int, height: int, bit_depth: int, color_type: int, interlace: int
) -> None:
    """Verify every decompressed scanline has a legal PNG filter byte."""
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]

    def validate_rows(row_width: int, row_count: int, offset: int) -> int:
        row_size = 1 + (row_width * channels * bit_depth + 7) // 8
        for _ in range(row_count):
            if decoded[offset] > 4:
                raise ValueError("Logo PNG image data is invalid")
            offset += row_size
        return offset

    if interlace == 0:
        validate_rows(width, height, 0)
        return
    offset = 0
    for x0, y0, dx, dy in (
        (0, 0, 8, 8),
        (4, 0, 8, 8),
        (0, 4, 4, 8),
        (2, 0, 4, 4),
        (0, 2, 2, 4),
        (1, 0, 2, 2),
        (0, 1, 1, 2),
    ):
        pass_width = (width - x0 + dx - 1) // dx if width > x0 else 0
        pass_height = (height - y0 + dy - 1) // dy if height > y0 else 0
        offset = validate_rows(pass_width, pass_height, offset)


def _validate_png(data: bytes) -> None:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Logo must be a valid PNG or WebP image")
    offset, seen_ihdr, seen_plte, seen_idat, seen_iend, idat_closed = (
        8,
        False,
        False,
        False,
        False,
        False,
    )
    idat_parts: list[bytes] = []
    width = height = bit_depth = color_type = interlace = 0
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError("Logo must be a complete PNG image")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        end = offset + 12 + length
        if end > len(data):
            raise ValueError("Logo must be a complete PNG image")
        kind, payload, checksum = (
            data[offset + 4 : offset + 8],
            data[offset + 8 : offset + 8 + length],
            data[offset + 8 + length : end],
        )
        if struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF) != checksum:
            raise ValueError("Logo PNG checksum is invalid")
        if not seen_ihdr:
            if kind != b"IHDR" or length != 13:
                raise ValueError("Logo must begin with a valid PNG header")
            width, height, bit_depth, color_type, compression, filter_method, interlace = (
                struct.unpack(">IIBBBBB", payload)
            )
            _check_dimensions(width, height)
            valid_depths = {
                0: {1, 2, 4, 8, 16},
                2: {8, 16},
                3: {1, 2, 4, 8},
                4: {8, 16},
                6: {8, 16},
            }
            if (
                bit_depth not in valid_depths.get(color_type, set())
                or compression
                or filter_method
                or interlace not in {0, 1}
            ):
                raise ValueError("Logo PNG header is invalid")
            seen_ihdr = True
        elif kind == b"IDAT":
            if seen_iend or idat_closed:
                raise ValueError("Logo PNG chunks are out of order")
            if color_type == 3 and not seen_plte:
                raise ValueError("Logo PNG palette is invalid")
            seen_idat = True
            idat_parts.append(payload)
        elif kind == b"IEND":
            if length != 0 or not seen_idat or end != len(data):
                raise ValueError("Logo must be a complete PNG image")
            seen_iend = True
        elif kind == b"PLTE":
            entries = len(payload) // 3
            if (
                seen_idat
                or not payload
                or len(payload) % 3
                or entries > 256
                or (color_type == 3 and entries > 1 << bit_depth)
            ):
                raise ValueError("Logo PNG palette is invalid")
            seen_plte = True
        elif kind[:1].isupper():
            raise ValueError("Logo PNG has an unsupported critical chunk")
        if seen_idat and kind != b"IDAT":
            idat_closed = True
        offset = end
    if not (seen_ihdr and seen_idat and seen_iend):
        raise ValueError("Logo must be a complete PNG image")
    expected = _png_scanline_bytes(width, height, bit_depth, color_type, interlace)
    if expected > MAX_DECODED_LOGO_BYTES:
        raise ValueError("Logo decoded image exceeds the allowed bounds")
    decoder = zlib.decompressobj()
    decoded = decoder.decompress(b"".join(idat_parts), expected + 1)
    if (
        len(decoded) != expected
        or decoder.unconsumed_tail
        or not decoder.eof
        or decoder.unused_data
    ):
        raise ValueError("Logo PNG image data is invalid")
    _validate_png_scanlines(decoded, width, height, bit_depth, color_type, interlace)


def _validate_webp(data: bytes) -> None:
    if (
        len(data) < 12
        or data[:4] != b"RIFF"
        or data[8:12] != b"WEBP"
        or struct.unpack("<I", data[4:8])[0] != len(data) - 8
    ):
        raise ValueError("Logo must be a complete WebP image")
    offset, image_seen = 12, False
    while offset < len(data):
        if offset + 8 > len(data):
            raise ValueError("Logo must be a complete WebP image")
        kind, length = (
            data[offset : offset + 4],
            struct.unpack("<I", data[offset + 4 : offset + 8])[0],
        )
        end = offset + 8 + length
        if end > len(data):
            raise ValueError("Logo must be a complete WebP image")
        payload = data[offset + 8 : end]
        if kind == b"VP8 ":
            if image_seen or length < 12 or payload[3:6] != b"\x9d\x01\x2a":
                raise ValueError("Logo WebP image data is invalid")
            frame_tag = int.from_bytes(payload[:3], "little")
            first_partition_size = frame_tag >> 5
            if frame_tag & 1 or not frame_tag & 16 or (frame_tag >> 1) & 7 > 3:
                raise ValueError("Logo WebP image data is invalid")
            # A key frame needs its uncompressed header plus an initialized
            # boolean decoder and the complete first partition.  This rejects
            # RIFF/WEBP/VP8 header-only payloads before persistence.
            if len(payload) < 12 + first_partition_size:
                raise ValueError("Logo WebP image data is incomplete")
            _check_dimensions(
                struct.unpack("<H", payload[6:8])[0] & 0x3FFF,
                struct.unpack("<H", payload[8:10])[0] & 0x3FFF,
            )
            image_seen = True
        elif kind == b"VP8L":
            if image_seen or length < 6 or payload[0] != 0x2F:
                raise ValueError("Logo WebP image data is invalid")
            bits = struct.unpack("<I", payload[1:5])[0]
            _check_dimensions((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
            image_seen = True
        offset = end + (length & 1)
        if offset > len(data):
            raise ValueError("Logo must be a complete WebP image")
    if offset != len(data) or not image_seen:
        raise ValueError("Logo must be a complete WebP image")


def _image_type(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        _validate_png(data)
        return "png", "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        _validate_webp(data)
        return "webp", "image/webp"
    raise ValueError("Logo must be a valid PNG or WebP image")


def upload_logo(data: bytes, claimed_content_type: str | None = None) -> dict[str, str | None]:
    if not data or len(data) > MAX_LOGO_BYTES:
        raise ValueError(f"Logo must be between 1 byte and {MAX_LOGO_BYTES} bytes")
    extension, content_type = _image_type(data)
    if claimed_content_type and claimed_content_type.split(";", 1)[0].strip().lower() not in {
        content_type,
        "application/octet-stream",
    }:
        raise ValueError("Logo content type does not match the image")
    digest = hashlib.sha256(data).hexdigest()
    LOGO_DIR.mkdir(parents=True, exist_ok=True)
    target = LOGO_DIR / f"logo-{digest}.{extension}"
    if not target.exists():
        temporary = LOGO_DIR / f".{target.name}.tmp"
        temporary.write_bytes(data)
        os.replace(temporary, target)
    _ensure_runtime_branding_schema()
    with SessionLocal() as db:
        row = db.get(RuntimeBrandingModel, 1) or RuntimeBrandingModel(
            id=1, title=DEFAULT_TITLE, subtitle=DEFAULT_SUBTITLE
        )
        if row not in db:
            db.add(row)
        old_filename = row.logo_filename
        row.logo_filename, row.logo_hash, row.logo_content_type = target.name, digest, content_type
        db.commit()
        db.refresh(row)
        db.expunge(row)
    if old_filename and old_filename != target.name:
        (LOGO_DIR / old_filename).unlink(missing_ok=True)
    return _payload(row)


def reset_logo() -> dict[str, str | None]:
    _ensure_runtime_branding_schema()
    with SessionLocal() as db:
        row = db.get(RuntimeBrandingModel, 1)
        if row is None:
            return _payload(_row())
        old_filename = row.logo_filename
        row.logo_filename = row.logo_hash = row.logo_content_type = None
        db.commit()
        db.refresh(row)
        db.expunge(row)
    if old_filename:
        (LOGO_DIR / old_filename).unlink(missing_ok=True)
    return _payload(row)


def logo_file() -> tuple[Path, str] | None:
    row = _row()
    if not row.logo_filename or not row.logo_content_type:
        return None
    candidate = LOGO_DIR / Path(row.logo_filename).name
    return (candidate, row.logo_content_type) if candidate.is_file() else None
