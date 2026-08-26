#!/usr/bin/env python3
"""Build the allowlisted, packaged ThreadCells documentation bundle.

The browser never reads the checkout: this script is the sole bridge from
canonical Markdown to the static runtime artifact. It deliberately accepts only
the manifest's repository-relative Markdown paths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "DOCS_MANIFEST.json"
PRIVATE_SEGMENTS = {"agents", "memory", "handoffs", ".git"}
PRIVATE_MARKERS = ("private deployment path", "internal-only deployment")
PUBLIC_REPOSITORY_BLOB = "https://github.com/IUnknown404I/threadcells/blob/main"
APP_LOCALES = ("en", "ru")
FRONT_MATTER = re.compile(r"\A---\n(?P<header>.*?)\n---\n(?P<body>.*)\Z", re.S)


def git_identity() -> str:
    supplied = os.environ.get("THREADCELLS_SOURCE_REVISION")
    if supplied:
        if not re.fullmatch(r"[0-9a-f]{7,64}", supplied):
            raise ValueError("THREADCELLS_SOURCE_REVISION must be a Git revision")
        return supplied
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "source-export"


def version() -> str:
    match = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.MULTILINE)
    return match.group(1) if match else "unknown"


def title(markdown: str, fallback: str) -> str:
    match = re.search(r"^#\s+(.+?)\s*$", markdown, re.MULTILINE)
    return match.group(1) if match else fallback


def headings(markdown: str) -> list[dict[str, str]]:
    rows = []
    for level, text in re.findall(r"^(#{2,4})\s+(.+?)\s*$", markdown, re.MULTILINE):
        normalized = unicodedata.normalize("NFKD", text.lower())
        label = re.sub(r"[^\w]+", "-", normalized, flags=re.UNICODE).strip("-")
        rows.append({"level": str(len(level)), "text": text, "anchor": label})
    return rows


def rewrite_internal_links(markdown: str, source: Path, slugs: dict[Path, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        target = match.group(1)
        if re.match(r"^(?:https?://|mailto:|#)", target):
            return match.group(0)
        if target.startswith("/media/screenshots/"):
            return match.group(0)
        location, separator, fragment = target.partition("#")
        resolved = (source.parent / location).resolve()
        slug = slugs.get(resolved)
        if not slug:
            try:
                repository_path = resolved.relative_to(ROOT).as_posix()
            except ValueError:
                return match.group(0)
            suffix = f"#{fragment}" if separator else ""
            return f"]({PUBLIC_REPOSITORY_BLOB}/{repository_path}{suffix})"
        suffix = f"#{fragment}" if separator else ""
        return f"](/docs/{slug}{suffix})"

    return re.sub(r"\]\(([^)]+)\)", replace, markdown)


def translated_markdown(locale: str, item: dict, canonical_path: Path) -> str:
    path = ROOT / "docs" / locale / f"{item['slug']}.md"
    if not path.is_file():
        raise ValueError(f"missing {locale} documentation translation: {item['slug']}")
    value = path.read_text(encoding="utf-8")
    match = FRONT_MATTER.fullmatch(value)
    if not match:
        raise ValueError(f"invalid {locale} documentation metadata: {item['slug']}")
    metadata = {}
    for line in match.group("header").splitlines():
        key, separator, raw = line.partition(":")
        if not separator or not key.strip() or not raw.strip():
            raise ValueError(f"invalid {locale} documentation metadata: {item['slug']}")
        metadata[key.strip()] = raw.strip()
    expected = {
        "slug": item["slug"],
        "source": item["source"],
        "source_sha256": f"sha256:{hashlib.sha256(canonical_path.read_bytes()).hexdigest()}",
    }
    if metadata != expected:
        raise ValueError(f"stale or mismatched {locale} documentation translation: {item['slug']}")
    return match.group("body")


def build_locale(locale: str, manifest: dict, slugs: dict[Path, str]) -> list[dict]:
    docs = []
    for item in manifest["documents"]:
        source = item["source"]
        parts = Path(source).parts
        if Path(source).is_absolute() or ".." in parts or any(p in PRIVATE_SEGMENTS for p in parts):
            raise ValueError(f"unsafe documentation source: {source}")
        path = ROOT / source
        if not path.is_file() or path.suffix.lower() != ".md":
            raise ValueError(f"missing Markdown source: {source}")
        markdown = path.read_text(encoding="utf-8") if locale == "en" else translated_markdown(locale, item, path)
        lowered = markdown.lower()
        if "todo" in lowered or any(marker in lowered for marker in PRIVATE_MARKERS):
            raise ValueError(f"public-document validation failed: {source}")
        if locale == "en" and re.search(r"[\u0400-\u04ff]", markdown):
            raise ValueError(f"non-English public document: {source}")
        for target in re.findall(r"\]\(([^)#]+)(?:#[^)]+)?\)", markdown):
            if not re.match(r"^(?:https?://|mailto:)", target):
                if target.startswith("/media/screenshots/"):
                    target_path = ROOT / "web" / "public" / target.removeprefix("/")
                    if not target_path.is_file():
                        raise ValueError(f"missing public documentation media: {target}")
                    continue
                target_path = (path.parent / target).resolve()
                if not target_path.is_file() or ROOT not in target_path.parents:
                    raise ValueError(f"broken relative link in {source}: {target}")
        docs.append(
            {
                **item,
                "title": item.get("title") if locale == "en" and item.get("title") else title(markdown, item["slug"]),
                "headings": headings(markdown),
                "markdown": rewrite_internal_links(markdown, path, slugs),
                "sha256": hashlib.sha256(markdown.encode()).hexdigest(),
            }
        )
    return docs


def build() -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    slugs = {(ROOT / item["source"]).resolve(): item["slug"] for item in manifest["documents"]}
    return {
        "schema": 2,
        "product": "ThreadCells",
        "version": version(),
        "commit": git_identity(),
        "locales": {locale: build_locale(locale, manifest, slugs) for locale in APP_LOCALES},
    }


def matches_tracked_bundle(current: str, expected: str) -> bool:
    """Compare tracked docs content without requiring a self-referential commit hash."""
    current_payload = json.loads(current)
    expected_payload = json.loads(expected)
    current_payload["commit"] = expected_payload["commit"]
    return current_payload == expected_payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    payload = json.dumps(build(), indent=2, ensure_ascii=False) + "\n"
    if (
        args.check
        and args.output.exists()
        and not matches_tracked_bundle(args.output.read_text(encoding="utf-8"), payload)
    ):
        raise SystemExit("documentation bundle is stale; run build_docs_bundle.py")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")


if __name__ == "__main__":
    main()
