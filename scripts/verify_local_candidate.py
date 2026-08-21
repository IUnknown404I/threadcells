#!/usr/bin/env python3
"""Verify the local-only ThreadCells candidate acceptance chain without mutation."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

MANIFEST_EXCLUDED_PATHS = {Path("candidate-manifest.json"), Path("SHA256SUMS")}
PRIVATE_PARTS = {"agents", "memory", "source-pack", ".git"}
SCHEMA_NAMES = ("adapter-manifest", "capabilities", "profile", "provider-config")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative_path(root: Path, value: str) -> Path | None:
    path = (root / value).resolve()
    return path if root.resolve() in path.parents else None


def verify_brand_assets(candidate: Path) -> list[str]:
    errors: list[str] = []
    manifest_path = candidate / "brand" / "ASSET_MANIFEST.json"
    checksum_path = candidate / "brand" / "SHA256SUMS"
    if not manifest_path.is_file() or not checksum_path.is_file():
        return ["missing packaged brand integrity evidence"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pack_root_value = manifest.get("canonical_pack_root")
    pack_checksum_value = manifest.get("canonical_pack_checksum_file")
    if not isinstance(pack_root_value, str) or not isinstance(pack_checksum_value, str):
        return ["brand asset manifest is missing canonical pack integrity evidence"]
    pack_root = relative_path(candidate, pack_root_value)
    pack_checksum = relative_path(candidate, pack_checksum_value)
    if pack_root is None or pack_checksum is None or not pack_checksum.is_file():
        return ["canonical brand pack is unavailable"]
    readme = pack_root / "README.md"
    required_pack_directories = (
        "logos/bg-101622",
        "logos/bg-black",
        "symbols/bg-101622",
        "symbols/bg-black",
        "favicons/bg-101622",
        "favicons/bg-black",
    )
    if (
        not readme.is_file()
        or not readme.read_text(encoding="utf-8").startswith("# ThreadCells Brand Asset Pack")
        or any(not (pack_root / relative).is_dir() for relative in required_pack_directories)
    ):
        errors.append("canonical brand pack signature is invalid")
    pack_inventory: set[str] = set()
    for line in pack_checksum.read_text(encoding="utf-8").splitlines():
        try:
            checksum, relative = line.split("  ", maxsplit=1)
        except ValueError:
            errors.append("malformed canonical brand pack checksum entry")
            continue
        normalized = relative.removeprefix("./")
        path = relative_path(pack_root, normalized)
        if path is None or not path.is_file() or digest(path) != checksum:
            errors.append(f"canonical brand pack checksum mismatch: {relative}")
        if normalized in pack_inventory:
            errors.append(f"duplicate canonical brand pack checksum entry: {relative}")
        pack_inventory.add(normalized)
    assets = manifest.get("assets")
    if not isinstance(assets, list):
        return ["brand asset manifest is malformed"]
    expected_checksums: dict[str, str] = {}
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("destination"), str):
            errors.append("brand asset manifest has an invalid destination")
            continue
        destination = asset["destination"]
        source = asset.get("source")
        if not isinstance(source, str) or source not in pack_inventory:
            errors.append(f"brand asset source is not in the canonical pack: {destination}")
        path = relative_path(candidate, destination)
        if path is None or not path.is_file() or digest(path) != asset.get("sha256"):
            errors.append(f"brand asset mismatch: {destination}")
        expected_checksums[f"../{destination}"] = str(asset.get("sha256"))
    actual_checksums: dict[str, str] = {}
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        try:
            checksum, relative = line.split("  ", maxsplit=1)
        except ValueError:
            errors.append("malformed brand checksum entry")
            continue
        path = relative_path(candidate, str(Path("brand") / relative))
        if path is None or not path.is_file() or digest(path) != checksum:
            errors.append(f"brand checksum mismatch: {relative}")
        if relative in actual_checksums:
            errors.append(f"duplicate brand checksum entry: {relative}")
        actual_checksums[relative] = checksum
    if actual_checksums != expected_checksums:
        errors.append("brand checksum inventory does not match the brand asset manifest")
    return errors


def verify(candidate: Path) -> list[str]:
    errors: list[str] = []
    checksum_file = candidate / "SHA256SUMS"
    manifest_file = candidate / "candidate-manifest.json"
    required = [
        checksum_file,
        manifest_file,
        candidate / "sbom.cdx.json",
        candidate / "DEPENDENCY_REVIEW.md",
        candidate / "EVIDENCE.md",
        candidate / "scripts" / "verify_local_candidate.py",
        candidate / "examples" / "threadcells-starter" / "README.md",
        candidate / "examples" / "provider-adapters" / "threadcells-echo" / "README.md",
        candidate
        / "examples"
        / "provider-adapters"
        / "threadcells-echo"
        / "threadcells-provider.json",
        candidate / "docs" / "DOCS_MANIFEST.json",
        candidate / "web" / "public" / "docs-bundle.json",
        *(candidate / "schemas" / "v1" / f"{name}.schema.json" for name in SCHEMA_NAMES),
    ]
    for path in required:
        if not path.is_file():
            errors.append(f"missing required artifact: {path.name}")
    if errors:
        return errors
    checksums: dict[str, str] = {}
    for line in checksum_file.read_text(encoding="utf-8").splitlines():
        try:
            checksum, relative = line.split("  ", maxsplit=1)
        except ValueError:
            errors.append("malformed candidate checksum entry")
            continue
        path = relative_path(candidate, relative)
        if path is None or not path.is_file() or digest(path) != checksum:
            errors.append(f"checksum mismatch: {relative}")
        if relative in checksums:
            errors.append(f"duplicate candidate checksum entry: {relative}")
        checksums[relative] = checksum
    expected_checksum_paths = {
        path.relative_to(candidate).as_posix()
        for path in candidate.rglob("*")
        if path.is_file() and path.relative_to(candidate) != Path("SHA256SUMS")
    }
    if set(checksums) != expected_checksum_paths:
        errors.append("candidate checksum inventory does not cover every file except SHA256SUMS")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    coverage = manifest.get("coverage")
    if coverage != {
        "included": "every candidate file except the explicitly excluded derived files",
        "excluded": sorted(path.as_posix() for path in MANIFEST_EXCLUDED_PATHS),
    }:
        errors.append("candidate manifest coverage declaration is not truthful")
    expected = {entry["path"]: entry for entry in manifest["files"]}
    actual = {
        path.relative_to(candidate).as_posix(): path
        for path in candidate.rglob("*")
        if path.is_file() and path.relative_to(candidate) not in MANIFEST_EXCLUDED_PATHS
    }
    if set(expected) != set(actual):
        errors.append("candidate manifest file inventory does not match payload")
    for relative, entry in expected.items():
        if PRIVATE_PARTS.intersection(Path(relative).parts):
            errors.append(f"private candidate path: {relative}")
        path = actual.get(relative)
        if path and (entry["bytes"] != path.stat().st_size or entry["sha256"] != digest(path)):
            errors.append(f"candidate manifest mismatch: {relative}")
    docs_manifest = json.loads(
        (candidate / "docs" / "DOCS_MANIFEST.json").read_text(encoding="utf-8")
    )
    docs_bundle = json.loads(
        (candidate / "web" / "public" / "docs-bundle.json").read_text(encoding="utf-8")
    )
    source_paths = {item["source"] for item in docs_manifest.get("documents", [])}
    if not source_paths or any(not (candidate / source).is_file() for source in source_paths):
        errors.append("candidate is missing an allowlisted documentation source")
    bundled_documents = docs_bundle.get("documents", [])
    if not isinstance(bundled_documents, list):
        errors.append("packaged documentation bundle is malformed")
        bundled_documents = []
    bundled_sources = {item.get("source") for item in bundled_documents if isinstance(item, dict)}
    if bundled_sources != source_paths or docs_bundle.get("commit") != manifest.get(
        "source_revision"
    ):
        errors.append("packaged documentation bundle does not match the candidate manifest")
    else:
        bundled_by_source = {item["source"]: item for item in bundled_documents}
        for source in source_paths:
            if bundled_by_source[source].get("sha256") != digest(candidate / source):
                errors.append(f"packaged documentation content mismatch: {source}")
    sbom = json.loads((candidate / "sbom.cdx.json").read_text(encoding="utf-8"))
    if sbom.get("bomFormat") != "CycloneDX" or not sbom.get("components"):
        errors.append("SBOM is not a populated CycloneDX inventory")
    wheels = sorted((candidate / "packages").glob("threadcells-*.whl"))
    if len(wheels) != 1:
        errors.append("candidate must contain exactly one ThreadCells wheel")
    else:
        with ZipFile(wheels[0]) as wheel:
            names = set(wheel.namelist())
            for name in SCHEMA_NAMES:
                relative = f"cli_agent_orchestrator/public_schemas/v1/{name}.schema.json"
                if relative not in names:
                    errors.append(f"wheel is missing public schema: {name}")
            entry_points = [name for name in names if name.endswith(".dist-info/entry_points.txt")]
            if len(entry_points) != 1:
                errors.append("wheel entry-point metadata is unavailable")
            else:
                entry_text = wheel.read(entry_points[0]).decode("utf-8")
                for executable in ("threadcells", "threadcells-server", "threadcells-housekeeping"):
                    if f"{executable} =" not in entry_text:
                        errors.append(f"wheel is missing entry point: {executable}")
            api_source = "cli_agent_orchestrator/api/main.py"
            if api_source not in names or b'@app.get("/settings/{path:path}")' not in wheel.read(
                api_source
            ):
                errors.append("wheel is missing direct Settings SPA routing")
    errors.extend(verify_brand_assets(candidate))
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    args = parser.parse_args()
    errors = verify(args.candidate)
    if errors:
        raise SystemExit("candidate verification failed: " + "; ".join(errors))
    print(f"candidate verified: {args.candidate.name}")


if __name__ == "__main__":
    main()
