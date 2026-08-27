from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.prepare_release_bundle import digest, expected_python_version, prepare


def write_candidate(root: Path, *, version: str = "0.2.0a1") -> tuple[Path, Path]:
    candidate = root / f"threadcells-{version}-local"
    packages = candidate / "packages"
    packages.mkdir(parents=True)
    revision = "a" * 40
    wheel = packages / f"threadcells-{version}-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    sbom = candidate / "sbom.cdx.json"
    sbom.write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "metadata": {"component": {"version": version}},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = candidate / "candidate-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": 2,
                "kind": "threadcells-local-source-candidate",
                "version": version,
                "source_revision": revision,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    checksums = candidate / "SHA256SUMS"
    checksums.write_text(
        "".join(
            f"{digest(path)}  {path.relative_to(candidate).as_posix()}\n"
            for path in (manifest, sbom, wheel)
        ),
        encoding="utf-8",
    )
    archive = root / f"{candidate.name}.tar.gz"
    archive.write_bytes(b"release archive")
    Path(f"{archive}.sha256").write_text(f"{digest(archive)}  {archive.name}\n", encoding="utf-8")
    return candidate, archive


def directory_digests(path: Path) -> dict[str, str]:
    return {item.name: hashlib.sha256(item.read_bytes()).hexdigest() for item in path.iterdir()}


def test_prepare_release_bundle_is_deterministic_and_self_describing(tmp_path: Path) -> None:
    candidate, archive = write_candidate(tmp_path, version="0.3.3a0")
    first = tmp_path / "first"
    second = tmp_path / "second"

    metadata = prepare(candidate, archive, "v0.3.3-alpha", "a" * 40, first)
    prepare(candidate, archive, "v0.3.3-alpha", "a" * 40, second)

    assert directory_digests(first) == directory_digests(second)
    assert metadata["source_revision"] == "a" * 40
    assert metadata["version"] == "0.3.3a0"
    assert metadata["distribution_only"] is True
    assert metadata["runtime_container_image"] is False
    assert {item["name"] for item in metadata["artifacts"]} == {
        archive.name,
        f"{archive.name}.sha256",
        "threadcells-0.3.3a0-py3-none-any.whl",
        "SHA256SUMS",
        "candidate-manifest.json",
        "sbom.cdx.json",
    }
    inventory = {
        relative: checksum
        for checksum, relative in (
            line.split("  ", maxsplit=1)
            for line in (first / "BUNDLE-SHA256SUMS").read_text().splitlines()
        )
    }
    assert set(inventory) == {path.name for path in first.iterdir()} - {"BUNDLE-SHA256SUMS"}
    assert all(
        inventory[path.name] == digest(path) for path in first.iterdir() if path.name in inventory
    )
    assert "not a Docker image" in (first / "README.md").read_text()


@pytest.mark.parametrize(
    ("release_tag", "version"),
    (("v0.3.3-alpha", "0.3.3a1"), ("v0.3.4-alpha", "0.3.3a0")),
)
def test_prepare_release_bundle_rejects_tag_version_mismatch(
    tmp_path: Path, release_tag: str, version: str
) -> None:
    candidate, archive = write_candidate(tmp_path, version=version)
    with pytest.raises(ValueError, match="does not match the release version"):
        prepare(candidate, archive, release_tag, "a" * 40, tmp_path / "output")


def test_prepare_release_bundle_rejects_archive_checksum_mismatch(tmp_path: Path) -> None:
    candidate, archive = write_candidate(tmp_path)
    Path(f"{archive}.sha256").write_text(f"{'0' * 64}  {archive.name}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="archive checksum"):
        prepare(candidate, archive, "v0.2.0-alpha.1", "a" * 40, tmp_path / "output")


def test_prepare_release_bundle_rejects_wrong_source_revision(tmp_path: Path) -> None:
    candidate, archive = write_candidate(tmp_path)
    with pytest.raises(ValueError, match="source revision does not match"):
        prepare(candidate, archive, "v0.2.0-alpha.1", "b" * 40, tmp_path / "output")


@pytest.mark.parametrize(
    ("release_tag", "version"),
    (("v0.1.0-alpha.1", "0.1.0a1"), ("v0.1.0-alpha.2", "0.1.0a2"), ("v0.2.0-alpha.1", "0.2.0a1")),
)
def test_expected_python_version_preserves_all_alpha_release_lines(
    release_tag: str, version: str
) -> None:
    assert expected_python_version(release_tag) == version


@pytest.mark.parametrize(
    ("release_tag", "version"),
    (("v0.3.3-alpha", "0.3.3a0"), ("v0.3.4-alpha", "0.3.4a0")),
)
def test_expected_python_version_uses_semantic_release_with_alpha_stage(
    release_tag: str, version: str
) -> None:
    assert expected_python_version(release_tag) == version


@pytest.mark.parametrize("release_tag", ("v0.3.3-alpha.0", "v0.3-alpha", "0.3.3-alpha"))
def test_expected_python_version_rejects_invalid_alpha_tags(release_tag: str) -> None:
    with pytest.raises(ValueError, match="v0.X.Y-alpha"):
        expected_python_version(release_tag)


def test_publication_workflow_admits_semantic_alpha_release_events() -> None:
    workflow = (
        Path(__file__).resolve().parents[1] / ".github/workflows/publish-release-bundle.yml"
    ).read_text(encoding="utf-8")

    assert "endsWith(github.event.release.tag_name, '-alpha')" in workflow
    assert "contains(github.event.release.tag_name, '-alpha.')" in workflow
    assert "s/-alpha$/a0/" in workflow
