#!/usr/bin/env python3
"""Validate first-class ThreadCells public locales against canonical English."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "DOCS_MANIFEST.json"
LOCALES = ("en", "ru", "zh-CN", "es", "pt-BR", "de", "ja")
TRANSLATED_LOCALES = LOCALES[1:]
README_FILES = {
    "en": "README.md",
    "ru": "README.ru.md",
    "zh-CN": "README.zh-CN.md",
    "es": "README.es.md",
    "pt-BR": "README.pt-BR.md",
    "de": "README.de.md",
    "ja": "README.ja.md",
}
README_LABELS = {
    "en": "English",
    "ru": "Русский",
    "zh-CN": "简体中文",
    "es": "Español",
    "pt-BR": "Português (Brasil)",
    "de": "Deutsch",
    "ja": "日本語",
}
PUBLIC_SITE_ROOT = "https://iunknown404i.github.io/threadcells/"
PLACEHOLDER = re.compile(r"\b(?:TODO|TBD|TRANSLATE(?:D|\s+ME)?)\b|(?i:\bLOREM\s+IPSUM\b)")
FRONT_MATTER = re.compile(r"\A---\n(?P<header>.*?)\n---\n(?P<body>.*)\Z", re.S)
HEADING = re.compile(r"^(#{1,4})\s+.+?\s*$", re.M)
FENCE = re.compile(r"^```[^\n]*\n.*?^```\s*$", re.M | re.S)
INLINE_CODE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
LIST_ITEM = re.compile(r"^\s*[-*+]\s+", re.M)
ORDERED_ITEM = re.compile(r"^\s*\d+\.\s+", re.M)
TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.M)
BLOCKQUOTE = re.compile(r"^\s*>\s?", re.M)
LOCALE_MINIMUM_RATIO = {"ru": 0.55, "zh-CN": 0.28, "es": 0.65, "pt-BR": 0.65, "de": 0.65, "ja": 0.32}


def digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def manifest_documents() -> list[dict[str, object]]:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    documents = data.get("documents")
    if data.get("schema") != 1 or not isinstance(documents, list):
        raise ValueError("docs manifest must use schema 1 and a documents list")
    return documents


def parse_translation(path: Path) -> tuple[dict[str, str], str]:
    value = path.read_text(encoding="utf-8")
    match = FRONT_MATTER.fullmatch(value)
    if not match:
        raise ValueError(f"{path.relative_to(ROOT)}: missing exact translation front matter")
    header: dict[str, str] = {}
    for line in match.group("header").splitlines():
        key, separator, raw = line.partition(":")
        if not separator or not key.strip() or not raw.strip() or key.strip() in header:
            raise ValueError(f"{path.relative_to(ROOT)}: malformed translation front matter")
        header[key.strip()] = raw.strip()
    if set(header) != {"slug", "source", "source_sha256"}:
        raise ValueError(f"{path.relative_to(ROOT)}: unexpected translation metadata")
    return header, match.group("body")


def fenced_blocks(markdown: str) -> Counter[str]:
    return Counter(block.rstrip() for block in FENCE.findall(markdown))


def without_fences(markdown: str) -> str:
    return FENCE.sub("", markdown)


def inline_codes(markdown: str) -> Counter[str]:
    return Counter(INLINE_CODE.findall(without_fences(markdown)))


def link_targets(markdown: str) -> Counter[str]:
    return Counter(target.strip() for target in LINK.findall(markdown))


def heading_levels(markdown: str) -> list[int]:
    return [len(prefix) for prefix in HEADING.findall(markdown)]


def prose_blocks(markdown: str) -> list[str]:
    blocks: list[str] = []
    for block in without_fences(markdown).split("\n\n"):
        value = block.strip()
        if not value or re.match(r"^(?:#{1,4}\s|[-*+]\s|\d+\.\s|\||>)", value):
            continue
        blocks.append(re.sub(r"\s+", " ", value))
    return blocks


def validate_translation(
    *, locale: str, document: dict[str, object], fingerprints: dict[str, str]
) -> list[str]:
    errors: list[str] = []
    slug = str(document["slug"])
    source_name = str(document["source"])
    source_path = ROOT / source_name
    path = ROOT / "docs" / "i18n" / locale / f"{slug}.md"
    relative = path.relative_to(ROOT)
    if not path.is_file():
        return [f"{relative}: missing translation"]
    try:
        header, body = parse_translation(path)
    except ValueError as error:
        return [str(error)]
    expected = {"slug": slug, "source": source_name, "source_sha256": fingerprints[slug]}
    for key, value in expected.items():
        if header.get(key) != value:
            errors.append(f"{relative}: {key} is {header.get(key)!r}, expected {value!r}")
    source = source_path.read_text(encoding="utf-8")
    if PLACEHOLDER.search(body):
        errors.append(f"{relative}: placeholder text is not allowed")
    minimum_ratio = LOCALE_MINIMUM_RATIO[locale]
    if len(body.strip()) < max(80, int(len(source.strip()) * minimum_ratio)):
        errors.append(f"{relative}: translation is unexpectedly short")
    if heading_levels(body) != heading_levels(source):
        errors.append(f"{relative}: heading-level sequence differs from English")
    if fenced_blocks(body) != fenced_blocks(source):
        errors.append(f"{relative}: fenced code blocks differ from English")
    missing_inline = inline_codes(source) - inline_codes(body)
    if missing_inline:
        errors.append(f"{relative}: missing inline code identifiers {sorted(missing_inline.elements())}")
    if link_targets(body) != link_targets(source):
        errors.append(f"{relative}: Markdown link/media targets differ from English")
    for label, pattern in (
        ("unordered-list items", LIST_ITEM),
        ("ordered-list items", ORDERED_ITEM),
        ("table rows", TABLE_ROW),
        ("blockquotes", BLOCKQUOTE),
    ):
        if len(pattern.findall(body)) != len(pattern.findall(source)):
            errors.append(f"{relative}: {label} differ from English")
    source_paragraphs = prose_blocks(source)
    translated_paragraphs = prose_blocks(body)
    if len(translated_paragraphs) != len(source_paragraphs):
        errors.append(f"{relative}: prose-block count differs from English")
    repeated = [text for text, count in Counter(translated_paragraphs).items() if count > 1 and len(text) >= 60]
    if repeated:
        errors.append(f"{relative}: repeated prose blocks indicate omitted content")
    source_long_lines = {
        line.strip() for line in without_fences(source).splitlines() if len(line.strip()) >= 80
    }
    copied = [line for line in without_fences(body).splitlines() if line.strip() in source_long_lines]
    if copied:
        errors.append(f"{relative}: long English prose was copied without translation")
    return errors


def validate_readmes() -> list[str]:
    errors: list[str] = []
    canonical = (ROOT / README_FILES["en"]).read_text(encoding="utf-8")
    canonical_levels = heading_levels(canonical)
    canonical_fences = fenced_blocks(canonical)
    for locale, filename in README_FILES.items():
        path = ROOT / filename
        if not path.is_file():
            errors.append(f"{filename}: missing localized README")
            continue
        value = path.read_text(encoding="utf-8")
        first_block = value.split("\n\n", 1)[0]
        public_prefix = "" if locale == "en" else f"{locale}/"
        hero = "\n".join(value.splitlines()[:16])
        for label, target in (
            ("website", f"{PUBLIC_SITE_ROOT}{public_prefix}"),
            ("documentation", f"{PUBLIC_SITE_ROOT}{public_prefix}docs/"),
        ):
            if f"]({target})" not in hero:
                errors.append(f"{filename}: primary {label} link does not preserve locale")
        if f"**{README_LABELS[locale]}**" not in first_block:
            errors.append(f"{filename}: current language is not highlighted")
        for target_locale, target in README_FILES.items():
            if target_locale == locale:
                continue
            if f"]({target})" not in first_block:
                errors.append(f"{filename}: selector does not link {target}")
        if PLACEHOLDER.search(value):
            errors.append(f"{filename}: placeholder text is not allowed")
        if heading_levels(value) != canonical_levels:
            errors.append(f"{filename}: heading-level sequence differs from README.md")
        if fenced_blocks(value) != canonical_fences:
            errors.append(f"{filename}: fenced code blocks differ from README.md")
    return errors


def validate() -> tuple[list[str], dict[str, str]]:
    errors: list[str] = []
    documents = manifest_documents()
    slugs = [str(document.get("slug", "")) for document in documents]
    sources = [str(document.get("source", "")) for document in documents]
    if len(slugs) != len(set(slugs)) or not all(slugs):
        errors.append("docs manifest contains an empty or duplicate slug")
    if len(sources) != len(set(sources)) or not all(sources):
        errors.append("docs manifest contains an empty or duplicate source")
    fingerprints: dict[str, str] = {}
    for slug, source in zip(slugs, sources, strict=True):
        path = ROOT / source
        if not path.is_file():
            errors.append(f"{source}: canonical source is missing")
        else:
            fingerprints[slug] = digest(path)
    expected_names = {f"{slug}.md" for slug in slugs}
    for locale in TRANSLATED_LOCALES:
        root = ROOT / "docs" / "i18n" / locale
        actual_names = {path.name for path in root.glob("*.md")} if root.is_dir() else set()
        for unknown in sorted(actual_names - expected_names):
            errors.append(f"docs/i18n/{locale}/{unknown}: unknown translated slug")
        if len(fingerprints) == len(slugs):
            for document in documents:
                errors.extend(
                    validate_translation(locale=locale, document=document, fingerprints=fingerprints)
                )
    errors.extend(validate_readmes())
    return errors, fingerprints


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print-fingerprints", action="store_true")
    args = parser.parse_args()
    errors, fingerprints = validate()
    if args.print_fingerprints:
        print(json.dumps(fingerprints, indent=2, sort_keys=True))
    if errors:
        for error in errors:
            print(f"LOCALIZATION_ERROR {error}")
        print(f"LOCALIZATION_FAILED errors={len(errors)}")
        return 1
    print(
        f"LOCALIZATION_OK locales={len(LOCALES)} documents={len(fingerprints)} "
        f"translations={len(fingerprints) * len(TRANSLATED_LOCALES)} readmes={len(README_FILES)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
