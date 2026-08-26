# ThreadCells v0.3.0-alpha.3

ThreadCells `v0.3.0-alpha.3` adds English and opt-in Russian localization to the authenticated Web UI and makes the public site and authenticated Docs consume one canonical, manifest-governed Markdown corpus. It remains an alpha technical preview for trusted operators on Linux hosts.

## What changed

- The authenticated global header now includes the same shared language-selector primitive used by the public site. Authenticated P1 offers English and Русский; the independently available public site keeps all seven supported locales.
- English remains the deterministic authenticated default. Russian is enabled only when the operator selects it, and the namespaced browser preference survives reloads. Browser, OS, IP, provider, and agent language are never auto-detected.
- A typed `AppLocale` catalog provides interpolation, Russian plural categories through `Intl.PluralRules`, catalog and placeholder parity validation, safe English fallback, and one shared localized status mapping.
- First-party authenticated chrome is localized across navigation, Home, Agents, Flows, Statistics, Docs, Settings, Orchestration Capacity, Housekeeping, Full Cleanup, Inbox, dialogs, controls, loading/empty/error states, tooltips, and accessible labels.
- Known product-safe errors select localized copy by stable `reason_code`; reason codes and bounded diagnostic IDs remain canonical technical metadata. Unknown errors use localized generic copy instead of exposing raw browser or backend exceptions.
- Inbox and terminal surroundings follow the selected UI locale, while prompts, durable messages, delegated results, provider output, raw terminal contents, API values, IDs, paths, profile/model/provider names, and machine fields remain unchanged.
- Localized Docs and README sources now live under `docs/<locale>/`. Root `README.md` and the allowlisted manifest sources remain canonical English, and source-hash validation continues to reject stale translations.
- Authenticated English and Russian Docs are generated from the same repository Markdown and `docs/DOCS_MANIFEST.json` used by the public seven-locale Docs site. Switching authenticated locale preserves the current slug and anchor.

## Install or upgrade

For a new installation, follow [Quick Setup](QUICK_SETUP.md). Existing operators should follow [Upgrading](docs/UPGRADING.md): verify the exact tagged candidate, create and integrity-check a SQLite backup, preserve rollback during deployment acceptance, and activate only after health and workflow checks pass.

The OCI artifact at `ghcr.io/iunknown404i/threadcells-release-bundle:v0.3.0-alpha.3` is a distribution bundle, not a Docker runtime image. Verify `BUNDLE-SHA256SUMS` and the archive checksum before using its contents. The established `latest-alpha` convenience tag may move to this release; no unqualified stable `latest` tag is published.

## Compatibility and limitations

- Linux, tmux, Git, Python 3.10–3.14, and Node.js remain the supported operating foundation.
- Codex remains the reference adapter. Other built-in adapters expose only capabilities supported by the installed provider version.
- ThreadCells coordinates powerful local tools; worktrees are not security sandboxes and hostile multi-tenancy is unsupported.
- Authenticated localization in this P1 release is English and Russian only. Public Docs remain available in English, Russian, Simplified Chinese, Spanish, Brazilian Portuguese, German, and Japanese.
- UI locale is presentation-only and never changes provider/model behavior or translates durable agent, provider, prompt, result, or machine content.
- All previous tags and published artifacts remain unchanged.

See the [public documentation](https://iunknown404i.github.io/threadcells/docs/) and [release process](docs/RELEASE_PROCESS.md) for the complete operating and distribution model.
