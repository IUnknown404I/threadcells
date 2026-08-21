#!/usr/bin/env python3
"""Check source, candidate, and wheel public surfaces for unsafe legacy residue."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FILES = (
    "README.md",
    "QUICK_SETUP.md",
    "SECURITY.md",
    "SUPPORT.md",
    "OWNER_DECISIONS.md",
    "web/index.html",
    "examples/threadcells-starter/README.md",
    "examples/threadcells-starter/DEMO.md",
    "examples/provider-adapters/threadcells-echo/README.md",
    "examples/provider-adapters/threadcells-echo/threadcells-provider.json",
    "src/cli_agent_orchestrator/config/cao-operations.json",
    "src/cli_agent_orchestrator/public_schemas/v1/adapter-manifest.schema.json",
    "src/cli_agent_orchestrator/public_schemas/v1/capabilities.schema.json",
    "src/cli_agent_orchestrator/public_schemas/v1/profile.schema.json",
    "src/cli_agent_orchestrator/public_schemas/v1/provider-config.schema.json",
)
FORBIDDEN = ("CLI Agent Orchestrator", "/srv/", "/home/")
CANONICAL_CREATOR_NAME = "Subaev Ruslan"
FORBIDDEN_LEGACY_IDENTITY = "subaev"
FORBIDDEN_FLOATING_SOURCES = ("git+https://github.com/awslabs/cli-agent-orchestrator.git@main",)
DISALLOWED_DOWNSTREAM_CONTACTS = (
    "opensource-codeofconduct@amazon.com",
    "aws.amazon.com/security/vulnerability-reporting",
)
TEXT_SUFFIXES = {".css", ".html", ".js", ".json", ".md", ".txt"}


def canonical_public_files(root: Path) -> tuple[str, ...]:
    manifest = json.loads((root / "docs" / "DOCS_MANIFEST.json").read_text(encoding="utf-8"))
    files = set(PUBLIC_FILES)
    files.add("docs/DOCS_MANIFEST.json")
    files.update(item["source"] for item in manifest["documents"])
    # Audit the complete tracked public-document contour, not only the in-app
    # allowlist. Provenance, detailed provider guides, examples, authoring
    # skills, and developer-facing READMEs are distribution surfaces too.
    for pattern in (
        "docs/**/*.md",
        "examples/**/*",
        "skills/**/*.md",
        "src/cli_agent_orchestrator/skills/**/*.md",
        "**/README.md",
    ):
        for path in root.glob(pattern):
            if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
                continue
            relative = path.relative_to(root)
            if any(
                part in {"agents", "memory", ".git", ".venv", "node_modules"}
                for part in relative.parts
            ):
                continue
            files.add(relative.as_posix())
    return tuple(sorted(files))


def forbidden_findings(relative: str, text: str) -> list[str]:
    findings: list[str] = []
    for term in FORBIDDEN:
        inspected = text
        # Required upstream attribution is provenance, not retained product naming.
        if term == "CLI Agent Orchestrator":
            inspected = inspected.replace("AWS Labs CLI Agent Orchestrator", "")
        if term.lower() in inspected.lower():
            findings.append(f"{relative}: {term}")
    # The exact public creator attribution is owner-approved. Keep rejecting
    # the former lowercase identity in paths, internal prose, or other residue.
    creator_inspected = text.replace(CANONICAL_CREATOR_NAME, "")
    if FORBIDDEN_LEGACY_IDENTITY in creator_inspected.lower():
        findings.append(f"{relative}: {FORBIDDEN_LEGACY_IDENTITY}")
    for contact in DISALLOWED_DOWNSTREAM_CONTACTS:
        if contact in text.lower():
            findings.append(f"{relative}: upstream contact presented as downstream guidance")
    for source in FORBIDDEN_FLOATING_SOURCES:
        if source in text:
            findings.append(f"{relative}: unpinned upstream executable source")
    return findings


def audit_tree(root: Path, *, candidate: bool) -> list[str]:
    findings: list[str] = []
    files = (
        tuple(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and path.suffix in TEXT_SUFFIXES
        )
        if candidate
        else canonical_public_files(root)
    )
    for relative in files:
        path = root / relative
        if not path.is_file():
            findings.append(f"missing public file: {relative}")
            continue
        findings.extend(forbidden_findings(relative, path.read_text(encoding="utf-8")))
    if candidate:
        legacy_installer = root / "tmux-install.sh"
        if legacy_installer.exists():
            findings.append("candidate retains destructive legacy tmux installer")
    return findings


def audit_wheel(wheel: Path) -> list[str]:
    findings: list[str] = []
    with ZipFile(wheel) as archive:
        names = set(archive.namelist())
        for schema in ("adapter-manifest", "capabilities", "profile", "provider-config"):
            name = f"cli_agent_orchestrator/public_schemas/v1/{schema}.schema.json"
            if name not in names:
                findings.append(f"wheel missing public schema: {schema}")
        operations_config = "cli_agent_orchestrator/config/cao-operations.json"
        if operations_config not in names:
            findings.append("wheel missing public operations config")
        else:
            findings.extend(
                forbidden_findings(
                    operations_config,
                    archive.read(operations_config).decode("utf-8"),
                )
            )
        for member in archive.infolist():
            name = member.filename
            if "subaev" in name.lower():
                findings.append(f"wheel legacy asset: {name}")
            if "/web_ui/" not in name or Path(name).suffix not in TEXT_SUFFIXES:
                continue
            findings.extend(forbidden_findings(name, archive.read(member).decode("utf-8")))
    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--wheel", type=Path)
    args = parser.parse_args()
    if args.wheel and not args.wheel.is_file():
        raise SystemExit(f"wheel does not exist: {args.wheel}")
    root = args.candidate if args.candidate else ROOT
    findings = audit_tree(root, candidate=args.candidate is not None)
    if args.wheel:
        findings.extend(audit_wheel(args.wheel))
    if findings:
        raise SystemExit("public surface audit failed: " + "; ".join(findings))
    print(f"public surface verified: {len(canonical_public_files(root))} files")


if __name__ == "__main__":
    main()
