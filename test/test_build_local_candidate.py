from __future__ import annotations

import io
import stat
import tarfile
import warnings
from pathlib import Path

import pytest

from scripts.build_local_candidate import safe_extract


def create_tar_archive(entries: dict[str, bytes]) -> io.BytesIO:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in entries.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    buffer.seek(0)
    return buffer


def test_safe_extract_valid_archive_without_deprecation_warnings(tmp_path: Path):
    archive_data = create_tar_archive(
        {
            "manifest.json": b'{"name": "test"}',
            "nested/script.py": b"print('hello')\n",
        }
    )

    with warnings.catch_warnings(record=True) as recorded_warnings:
        warnings.simplefilter("always")
        with tarfile.open(fileobj=archive_data, mode="r:gz") as archive:
            safe_extract(archive, tmp_path)

    tar_warnings = [
        w
        for w in recorded_warnings
        if issubclass(w.category, DeprecationWarning)
        and "tar archive" in str(w.message).lower()
        and "filter" in str(w.message).lower()
    ]
    assert len(tar_warnings) == 0
    assert (tmp_path / "manifest.json").read_text() == '{"name": "test"}'
    assert (tmp_path / "nested" / "script.py").read_text() == "print('hello')\n"


@pytest.mark.skipif(
    not hasattr(tarfile, "data_filter"), reason="extraction filters are unavailable"
)
def test_safe_extract_rejects_symlink_target_outside_destination(tmp_path: Path):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo(name="outside-link")
        info.type = tarfile.SYMTYPE
        info.linkname = "../outside"
        archive.addfile(info)
    buffer.seek(0)

    with tarfile.open(fileobj=buffer, mode="r:gz") as archive:
        # Model the pre-3.14 default; only safe_extract's explicit policy should reject it.
        archive.extraction_filter = tarfile.fully_trusted_filter
        with pytest.raises(tarfile.LinkOutsideDestinationError):
            safe_extract(archive, tmp_path)

    assert not (tmp_path / "outside-link").exists()


@pytest.mark.skipif(
    not hasattr(tarfile, "data_filter"), reason="extraction filters are unavailable"
)
def test_safe_extract_applies_data_filter_metadata_policy(tmp_path: Path):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo(name="mode.txt")
        payload = b"mode policy"
        info.size = len(payload)
        info.mode = 0o666
        archive.addfile(info, io.BytesIO(payload))
    buffer.seek(0)

    with tarfile.open(fileobj=buffer, mode="r:gz") as archive:
        # A fully trusted default preserves 0o666; the explicit data filter must
        # remove group and other write permissions.
        archive.extraction_filter = tarfile.fully_trusted_filter
        safe_extract(archive, tmp_path)

    assert stat.S_IMODE((tmp_path / "mode.txt").stat().st_mode) == 0o644


def test_safe_extract_supports_python_without_extraction_filters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    class LegacyArchive:
        extracted_to: Path | None = None

        def getmembers(self) -> list[tarfile.TarInfo]:
            return [tarfile.TarInfo(name="payload.txt")]

        def extractall(self, destination: Path) -> None:
            self.extracted_to = destination

    archive = LegacyArchive()
    monkeypatch.delattr(tarfile, "data_filter")

    safe_extract(archive, tmp_path)  # type: ignore[arg-type]

    assert archive.extracted_to == tmp_path


def test_safe_extract_rejects_parent_traversal(tmp_path: Path):
    archive_data = create_tar_archive(
        {
            "../escape.txt": b"malicious content",
        }
    )

    with tarfile.open(fileobj=archive_data, mode="r:gz") as archive:
        with pytest.raises(ValueError, match="unsafe archive member"):
            safe_extract(archive, tmp_path)


def test_safe_extract_rejects_nested_parent_traversal(tmp_path: Path):
    archive_data = create_tar_archive(
        {
            "nested/../../escape.txt": b"malicious content",
        }
    )

    with tarfile.open(fileobj=archive_data, mode="r:gz") as archive:
        with pytest.raises(ValueError, match="unsafe archive member"):
            safe_extract(archive, tmp_path)


def test_safe_extract_rejects_absolute_path(tmp_path: Path):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo(name="/absolute/path.txt")
        payload = b"absolute payload"
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    buffer.seek(0)

    with tarfile.open(fileobj=buffer, mode="r:gz") as archive:
        with pytest.raises(ValueError, match="unsafe archive member"):
            safe_extract(archive, tmp_path)


def test_safe_extract_preserves_content_and_structure(tmp_path: Path):
    archive_data = create_tar_archive(
        {
            "dir/subdir/file.txt": b"nested file content",
            "README.md": b"# Readme",
        }
    )

    with tarfile.open(fileobj=archive_data, mode="r:gz") as archive:
        safe_extract(archive, tmp_path)

    assert (tmp_path / "dir" / "subdir" / "file.txt").read_bytes() == b"nested file content"
    assert (tmp_path / "README.md").read_bytes() == b"# Readme"
