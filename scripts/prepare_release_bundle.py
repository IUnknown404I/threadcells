#!/usr/bin/env python3
"""Stage deterministic, public-safe layers for a ThreadCells OCI release bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

TAG_PATTERN = re.compile(r"^v0\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-alpha$")
LEGACY_TAG_PATTERN = re.compile(r"^v0\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-alpha\.([1-9][0-9]*)$")
PACKAGE = "ghcr.io/iunknown404i/threadcells-release-bundle"
REPOSITORY = "https://github.com/IUnknown404I/threadcells"
ARTIFACT_TYPE = "application/vnd.threadcells.release.bundle.v1"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def only(paths: list[Path], description: str) -> Path:
    if len(paths) != 1:
        raise ValueError(f"expected exactly one {description}, found {len(paths)}")
    return paths[0]


def checksum_inventory(path: Path) -> dict[str, str]:
    inventory: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            checksum, relative = line.split("  ", maxsplit=1)
        except ValueError as error:
            raise ValueError(f"malformed checksum line in {path.name}") from error
        if relative in inventory or not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise ValueError(f"invalid checksum entry in {path.name}: {relative}")
        inventory[relative] = checksum
    return inventory


def expected_python_version(release_tag: str) -> str:
    match = TAG_PATTERN.fullmatch(release_tag)
    if match:
        minor, patch = match.groups()
        return f"0.{minor}.{patch}a0"
    legacy_match = LEGACY_TAG_PATTERN.fullmatch(release_tag)
    if legacy_match:
        minor, patch, alpha = legacy_match.groups()
        return f"0.{minor}.{patch}a{alpha}"
    raise ValueError("release tag must match v0.X.Y-alpha")


def prepare(
    candidate: Path,
    archive: Path,
    release_tag: str,
    expected_source_revision: str,
    output: Path,
) -> dict:
    if output.exists():
        raise ValueError(f"refusing to overwrite output: {output}")
    if not candidate.is_dir() or not archive.is_file():
        raise ValueError("candidate directory and release archive must exist")

    version = expected_python_version(release_tag)
    if candidate.name != f"threadcells-{version}-local":
        raise ValueError("candidate directory name does not match the release version")
    if archive.name != f"{candidate.name}.tar.gz":
        raise ValueError("release archive name does not match the candidate")
    manifest_path = candidate / "candidate-manifest.json"
    checksum_path = candidate / "SHA256SUMS"
    sbom_path = candidate / "sbom.cdx.json"
    wheel = only(sorted((candidate / "packages").glob("threadcells-*.whl")), "wheel")
    for required in (manifest_path, checksum_path, sbom_path, wheel):
        if not required.is_file():
            raise ValueError(f"missing candidate artifact: {required}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    revision = manifest.get("source_revision")
    if manifest.get("kind") != "threadcells-local-source-candidate":
        raise ValueError("candidate manifest kind is not a ThreadCells source candidate")
    if manifest.get("version") != version:
        raise ValueError("release tag does not match the candidate version")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("candidate source revision is not an exact Git SHA")
    if revision != expected_source_revision:
        raise ValueError("candidate source revision does not match the release tag")
    if not wheel.name.startswith(f"threadcells-{version}-"):
        raise ValueError("wheel filename does not match the release version")

    sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
    if sbom.get("metadata", {}).get("component", {}).get("version") != version:
        raise ValueError("SBOM version does not match the release version")

    candidate_checksums = checksum_inventory(checksum_path)
    for path in (manifest_path, sbom_path, wheel):
        relative = path.relative_to(candidate).as_posix()
        if candidate_checksums.get(relative) != digest(path):
            raise ValueError(f"candidate checksum mismatch: {relative}")

    archive_sidecar = Path(f"{archive}.sha256")
    archive_checksums = checksum_inventory(archive_sidecar)
    if archive_checksums != {archive.name: digest(archive)}:
        raise ValueError("release archive checksum sidecar does not match the archive")

    output.mkdir(parents=True)
    sources = (
        archive,
        archive_sidecar,
        wheel,
        checksum_path,
        manifest_path,
        sbom_path,
    )
    staged: list[Path] = []
    for source in sources:
        destination = output / source.name
        if destination.exists():
            raise ValueError(f"duplicate bundle filename: {source.name}")
        shutil.copyfile(source, destination)
        staged.append(destination)

    metadata = {
        "schema": 1,
        "kind": "threadcells-release-bundle",
        "artifact_type": ARTIFACT_TYPE,
        "distribution_only": True,
        "runtime_container_image": False,
        "package": PACKAGE,
        "repository": REPOSITORY,
        "release": {
            "tag": release_tag,
            "url": f"{REPOSITORY}/releases/tag/{release_tag}",
        },
        "version": version,
        "source_revision": revision,
        "artifacts": [
            {"name": path.name, "bytes": path.stat().st_size, "sha256": digest(path)}
            for path in staged
        ],
    }
    metadata_path = output / "release-bundle.json"
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    readme_path = output / "README.md"
    readme_path.write_text(
        "\n".join(
            (
                "# ThreadCells release bundle",
                "",
                f"Immutable distribution evidence for `{release_tag}` at `{revision}`.",
                "",
                "This OCI artifact is a release distribution bundle, not a Docker image or a",
                "containerized ThreadCells runtime. It does not define or imply a supported",
                "container deployment mode.",
                "",
                "Verify the exposed layers with `BUNDLE-SHA256SUMS`. The release archive also",
                "contains its own `SHA256SUMS`, candidate manifest, SBOM, wheel, documentation,",
                "and installer evidence.",
                "",
            )
        ),
        encoding="utf-8",
    )

    bundle_checksums = output / "BUNDLE-SHA256SUMS"
    covered = sorted(path for path in output.iterdir() if path != bundle_checksums)
    bundle_checksums.write_text(
        "".join(f"{digest(path)}  {path.name}\n" for path in covered), encoding="utf-8"
    )
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--expected-source-revision", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    metadata = prepare(
        args.candidate,
        args.archive,
        args.release_tag,
        args.expected_source_revision,
        args.output,
    )
    print(
        f"release bundle: {args.output} "
        f"({metadata['release']['tag']} {metadata['source_revision']})"
    )


if __name__ == "__main__":
    main()
