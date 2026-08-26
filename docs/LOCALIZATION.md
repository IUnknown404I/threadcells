# Localization guide

English is the canonical authority for ThreadCells public documentation, the root README, and product claims. A translation may improve natural phrasing, but it must not omit or invent behavior, weaken a safety boundary, change a limit, or alter a command.

## Locale model

Release locales are `en`, `ru`, `zh-CN`, `es`, `pt-BR`, `de`, and `ja`. Canonical English Markdown remains in the source named by `docs/DOCS_MANIFEST.json`; policy-owned root documents keep their established paths. Each non-English document lives at `docs/LOCALE/SLUG.md` and records:

```yaml
---
slug: overview
source: docs/OVERVIEW.md
source_sha256: sha256:EXACT_ENGLISH_SOURCE_HASH
---
```

Slugs, manifest order, and navigation membership are shared across locales. Do not create a second locale-specific Docs manifest or renderer.

## Update a translation

1. Update and accept the canonical English document first.
2. Translate every claim and heading without changing code or identifiers.
3. Refresh `source_sha256` from the exact canonical source bytes.
4. Run `python3 scripts/validate_localizations.py`.
5. Build the website and inspect the affected routes at desktop, tablet, and mobile widths.

The validator rejects missing, stale, unknown, duplicate, or mismatched translated slugs. A supported locale must not silently publish an old translation after English changes.

## Add a locale

Add the locale once in `website/lib/locales.ts`, provide its complete landing/UI metadata, add one translated document for every manifest slug, add its localized README, and extend the deterministic route/browser checks. Preserve the same public slug when switching language.

Adding a future locale such as `fr` or `ko` should be a bounded content change. It must not require another application, manifest, or Docs architecture.

## Technical text

Keep these exact unless the canonical English source changes them:

- fenced code blocks and shell commands;
- inline code identifiers;
- API paths, config keys, environment variables, reason codes, profile/provider IDs, package names, and file paths;
- product and provider names such as ThreadCells, Codex, Claude Code, Git, Git worktree, and tmux;
- Markdown link destinations and media paths.

Translate explanations around those values naturally. Avoid literal calques that make developer guidance harder to read.

## README files

`README.md` is canonical English. Every localized README follows the same section structure, links to the same evidence, and begins with the compact seven-language selector. Highlight the current language in bold and use repository-relative links for the other six.

## Visual acceptance

Translations do not need identical line breaks or section heights. They must preserve hierarchy, readable typography, working CTAs, media, tables, code blocks, header/footer behavior, and zero horizontal overflow. Pay special attention to German expansion, Russian wrapping, Spanish and Portuguese navigation, and Chinese/Japanese line breaking.

Semantic review by a fluent developer-facing reader remains required. Passing Markdown, hash, route, and browser checks proves structural freshness; it does not prove translation quality.
