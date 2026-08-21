#!/usr/bin/env python3
"""Build a reproducible, local-only ThreadCells source candidate from HEAD."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PATH_PARTS = {"agents", "memory", "source-pack", ".git"}
METADATA_FILES = {
    "candidate-manifest.json",
    "sbom.cdx.json",
    "DEPENDENCY_REVIEW.md",
    "EVIDENCE.md",
    "SHA256SUMS",
}
# Only the derived integrity artifacts at the candidate root are excluded.  A
# nested checksum file, such as brand/SHA256SUMS, is payload and must remain
# covered by the root inventory and checksum chain.
MANIFEST_EXCLUDED_PATHS = {Path("candidate-manifest.json"), Path("SHA256SUMS")}
INSTALLER_FILES = {"install-threadcells.sh", "verify_local_candidate.py"}
PUBLIC_ROOT_FILES = {
    "README.md",
    "QUICK_SETUP.md",
    "SECURITY.md",
    "SUPPORT.md",
    "OWNER_DECISIONS.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "NOTICE",
    "THIRD_PARTY_NOTICES.md",
    "pyproject.toml",
    "uv.lock",
}


def command(
    *args: str, cwd: Path = ROOT, text: bool = True, **kwargs: Any
) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, check=True, text=text, **kwargs)


def source_version() -> str:
    content = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', content, re.MULTILINE)
    if not match:
        raise ValueError("project version is missing")
    return match.group(1)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_extract(archive: tarfile.TarFile, destination: Path) -> None:
    root = destination.resolve()
    for member in archive.getmembers():
        path = (destination / member.name).resolve()
        if root != path and root not in path.parents:
            raise ValueError(f"unsafe archive member: {member.name}")
    archive.extractall(destination)


def dependency_components(candidate: Path, version: str) -> list[dict[str, Any]]:
    pyproject = tomllib.loads((candidate / "pyproject.toml").read_text(encoding="utf-8"))
    python_requirements = pyproject["project"]["dependencies"]
    if not isinstance(python_requirements, list) or not all(
        isinstance(requirement, str) for requirement in python_requirements
    ):
        raise ValueError("project.dependencies must be a list of strings")
    components: list[dict[str, Any]] = [
        {
            "type": "application",
            "name": "threadcells",
            "version": version,
            "licenses": [{"license": {"id": "Apache-2.0"}}],
        }
    ]
    for requirement in python_requirements:
        name = requirement.strip()
        for delimiter in " \t[<>=!~;@":
            name = name.split(delimiter, maxsplit=1)[0]
        canonical_name = name.lower().replace("_", "-").replace(".", "-")
        components.append(
            {
                "type": "library",
                "bom-ref": f"urn:threadcells:direct-python:{canonical_name}:{hashlib.sha256(requirement.encode()).hexdigest()}",
                "name": name,
                "version": requirement,
                "properties": [
                    {"name": "threadcells:source", "value": "pyproject direct requirement"}
                ],
            }
        )
    package = json.loads((candidate / "web" / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((candidate / "web" / "package-lock.json").read_text(encoding="utf-8"))
    packages = lock.get("packages", {})
    for name, declared in sorted(package.get("dependencies", {}).items()):
        resolved = packages.get(f"node_modules/{name}", {}).get("version", declared)
        components.append(
            {
                "type": "library",
                "name": name,
                "version": resolved,
                "properties": [{"name": "threadcells:declared-version", "value": declared}],
            }
        )
    return components


def direct_node_license_evidence(candidate: Path) -> dict[str, str]:
    """Capture direct lockfile license labels before the install candidate is pruned."""
    package = json.loads((candidate / "web" / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((candidate / "web" / "package-lock.json").read_text(encoding="utf-8"))
    packages = lock.get("packages", {})
    return {
        name: entry["license"]
        for name in package.get("dependencies", {})
        if isinstance((entry := packages.get(f"node_modules/{name}", {})), dict)
        and isinstance(entry.get("license"), str)
    }


def write_metadata(
    candidate: Path,
    revision: str,
    version: str,
    components: list[dict[str, Any]],
    node_licenses: dict[str, str],
) -> None:
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {"type": "application", "name": "threadcells", "version": version}
        },
        "components": components,
    }
    (candidate / "sbom.cdx.json").write_text(json.dumps(sbom, indent=2) + "\n", encoding="utf-8")
    write_dependency_review_packet(candidate, sbom, node_licenses)
    evidence = f"""# Local candidate evidence

- Source revision: `{revision}`
- Version: `{version}`
- Scope: local candidate only; no release, publication, remote push, or production activation occurred.
- Integrity: `sha256sum -c SHA256SUMS` verifies every candidate file except `SHA256SUMS` itself. `candidate-manifest.json` inventories every candidate file except itself and `SHA256SUMS`; those two derived files are covered by `SHA256SUMS`.
- Branding: `(cd brand && sha256sum -c SHA256SUMS)` verifies the packaged runtime assets named by `brand/ASSET_MANIFEST.json`.
- Documentation: `web/public/docs-bundle.json`, its allowlisted `docs/DOCS_MANIFEST.json` sources and their public assets are packaged for this exact source revision. The safe starter is in `examples/threadcells-starter/`.
- Public extension surface: versioned JSON Schemas are in `schemas/v1/`; the installed-adapter example and manifest are in `examples/provider-adapters/threadcells-echo/`.
- Dependency evidence: `sbom.cdx.json` records direct Python declarations and resolved direct web-package versions. `DEPENDENCY_REVIEW.md` is a deterministic owner-review packet derived from that inventory. Neither is a license clearance or vulnerability attestation.

Before any public distribution, the owner must approve the publication target, vulnerability-reporting contact, branding provenance, and dependency/license review.
"""
    (candidate / "EVIDENCE.md").write_text(evidence, encoding="utf-8")
    payload = []
    for path in sorted(path for path in candidate.rglob("*") if path.is_file()):
        relative = path.relative_to(candidate)
        if relative in MANIFEST_EXCLUDED_PATHS:
            continue
        if PRIVATE_PATH_PARTS.intersection(relative.parts):
            raise ValueError(f"private path in candidate: {relative}")
        payload.append(
            {"path": relative.as_posix(), "bytes": path.stat().st_size, "sha256": sha256(path)}
        )
    manifest = {
        "schema": 2,
        "kind": "threadcells-local-source-candidate",
        "version": version,
        "source_revision": revision,
        "coverage": {
            "included": "every candidate file except the explicitly excluded derived files",
            "excluded": sorted(path.as_posix() for path in MANIFEST_EXCLUDED_PATHS),
        },
        "files": payload,
    }
    (candidate / "candidate-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    checksums = []
    for path in sorted(
        path
        for path in candidate.rglob("*")
        if path.is_file() and path.relative_to(candidate) != Path("SHA256SUMS")
    ):
        checksums.append(f"{sha256(path)}  {path.relative_to(candidate).as_posix()}\n")
    (candidate / "SHA256SUMS").write_text("".join(checksums), encoding="utf-8")


def write_dependency_review_packet(
    candidate: Path, sbom: dict[str, Any], node_licenses: dict[str, str] | None = None
) -> None:
    """Write a deterministic owner packet without asserting unverified licenses."""
    node_licenses = node_licenses or {}
    lines = [
        "# Dependency and license owner review packet",
        "",
        "This packet is generated deterministically from `sbom.cdx.json`. It is an inventory for owner review, not a license clearance, vulnerability attestation, or publication approval.",
        "",
        "| Component | Version evidence | Source | License evidence |",
        "| --- | --- | --- | --- |",
    ]
    components = sorted(
        sbom["components"],
        key=lambda component: (str(component["name"]).lower(), str(component["version"])),
    )
    for component in components:
        name = str(component["name"])
        version = str(component["version"])
        properties = {item["name"]: item["value"] for item in component.get("properties", [])}
        source = properties.get("threadcells:source", "package-lock resolved direct dependency")
        license_evidence = "owner review required"
        if name != "threadcells":
            if name in node_licenses:
                license_evidence = f"package-lock: {node_licenses[name]}"
        else:
            license_evidence = "project: Apache-2.0"
        lines.append(f"| `{name}` | `{version}` | {source} | {license_evidence} |")
    lines.extend(
        [
            "",
            "## Owner action required before distribution",
            "",
            "1. Review every direct dependency, its transitive obligations, and the applicable license texts.",
            "2. Review vulnerability status using an owner-approved process and current sources.",
            "3. Approve the candidate manifest, checksum evidence, branding provenance, and distribution target separately.",
            "",
        ]
    )
    (candidate / "DEPENDENCY_REVIEW.md").write_text("\n".join(lines), encoding="utf-8")


def prune_to_install_candidate(candidate: Path) -> None:
    """Retain the install surface plus the canonical public evidence corpus."""
    manifest = json.loads((candidate / "docs" / "DOCS_MANIFEST.json").read_text(encoding="utf-8"))
    canonical_docs = {Path("docs/DOCS_MANIFEST.json"), Path("docs/CHANGES_FROM_UPSTREAM.md")}
    canonical_docs.update(Path(item["source"]) for item in manifest["documents"])
    shutil.copytree(
        candidate / "src" / "cli_agent_orchestrator" / "public_schemas" / "v1",
        candidate / "schemas" / "v1",
    )
    for name in (".github", "agents", "deployment", "memory", "src", "test"):
        path = candidate / name
        if path.exists():
            shutil.rmtree(path)
    for path in candidate.iterdir():
        if path.is_file() and path.name not in PUBLIC_ROOT_FILES:
            path.unlink()
        elif path.is_dir() and path.name not in {
            "brand",
            "docs",
            "examples",
            "packages",
            "scripts",
            "schemas",
            "web",
            "website",
        }:
            shutil.rmtree(path)
    docs = candidate / "docs"
    for path in list(docs.rglob("*")):
        if (
            path.is_file()
            and path.relative_to(candidate) not in canonical_docs
            and "assets" not in path.parts
        ):
            path.unlink()
    for path in sorted(docs.rglob("*"), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    examples = candidate / "examples"
    for path in examples.iterdir():
        if path.name not in {"threadcells-starter", "provider-adapters"}:
            shutil.rmtree(path)
    provider_adapters = examples / "provider-adapters"
    for path in provider_adapters.iterdir():
        if path.name != "threadcells-echo":
            shutil.rmtree(path) if path.is_dir() else path.unlink()
    web = candidate / "web"
    for path in web.iterdir():
        if path.name != "public":
            shutil.rmtree(path) if path.is_dir() else path.unlink()
    website = candidate / "website"
    for path in website.iterdir():
        if path.name != "public":
            shutil.rmtree(path) if path.is_dir() else path.unlink()
    scripts = candidate / "scripts"
    for path in scripts.iterdir():
        if path.name not in INSTALLER_FILES:
            shutil.rmtree(path) if path.is_dir() else path.unlink()


def write_archive(candidate: Path, archive: Path, epoch: int) -> None:
    with (
        archive.open("wb") as output,
        gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=epoch) as compressed,
    ):
        with tarfile.open(fileobj=compressed, mode="w") as tar:
            for path in sorted(path for path in candidate.rglob("*") if path.is_file()):
                info = tar.gettarinfo(
                    str(path), arcname=f"{candidate.name}/{path.relative_to(candidate).as_posix()}"
                )
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = epoch
                with path.open("rb") as handle:
                    tar.addfile(info, handle)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", required=True, type=Path, help="new directory for candidate artifacts"
    )
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite existing output: {args.output}")
    if command("git", "status", "--porcelain", capture_output=True).stdout:
        raise SystemExit("candidate must be built from a clean committed tree")
    revision = command("git", "rev-parse", "HEAD", capture_output=True).stdout.strip()
    epoch = int(
        command("git", "show", "-s", "--format=%ct", "HEAD", capture_output=True).stdout.strip()
    )
    version = source_version()
    name = f"threadcells-{version}-local"
    args.output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(dir=args.output) as temporary:
        staging = Path(temporary)
        stream = command(
            "git", "archive", "--format=tar", "HEAD", stdout=subprocess.PIPE, text=False
        ).stdout
        with tarfile.open(fileobj=io.BytesIO(stream), mode="r:") as source:
            safe_extract(source, staging)
        for private_root in ("agents", "memory"):
            path = staging / private_root
            if path.exists():
                shutil.rmtree(path)
        candidate = args.output / name
        shutil.move(str(staging), candidate)
        environment = {**os.environ, "THREADCELLS_SOURCE_REVISION": revision}
        command(
            "npm", "ci", "--offline", "--ignore-scripts", cwd=candidate / "web", env=environment
        )
        command("npm", "run", "build", cwd=candidate / "web", env=environment)
        shutil.rmtree(candidate / "web" / "node_modules")
        command(
            "uv",
            "build",
            "--offline",
            "--wheel",
            "--out-dir",
            "packages",
            cwd=candidate,
            env=environment,
        )
        components = dependency_components(candidate, version)
        node_licenses = direct_node_license_evidence(candidate)
        prune_to_install_candidate(candidate)
        write_metadata(candidate, revision, version, components, node_licenses)
        archive = args.output / f"{name}.tar.gz"
        write_archive(candidate, archive, epoch)
        (args.output / f"{archive.name}.sha256").write_text(
            f"{sha256(archive)}  {archive.name}\n", encoding="utf-8"
        )
    print(f"candidate: {candidate}")
    print(f"archive: {archive}")


if __name__ == "__main__":
    main()
