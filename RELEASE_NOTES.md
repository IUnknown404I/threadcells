# ThreadCells v0.2.0-alpha.1

This release consolidates the reliability work completed after `v0.1.0-alpha.2` and makes the public ThreadCells surface available in seven first-class languages. It remains an alpha technical preview for trusted operators on Linux hosts.

## What changed

- Provider and workflow continuation now preserve durable parent/child results across model turns, safely resume an admitted turn interrupted before its required durable effects finish, admit execution against exact provider capacity, and expose the reason when aggregate capacity is unavailable. Receipt, acknowledgement, and effect fencing remain exactly once across reconnects.
- Inbox FIFO lifecycle handling fences stale pending transport bound to a closed workflow without rebinding payload, delivery, result, or workflow identity. Live submit, restart reconciliation, terminal transitions, and repeat repair are covered.
- Sessions use a stable lifetime identity. Add Agent targets the existing lifetime, historical sessions remain distinguishable, and terminal/session deletion resolves exact exited runtimes while respecting writer authority.
- Housekeeping now covers managed worktrees, caches, releases, logs, orphaned workflow authority, backups, and protected runtime/source sets with inspect–revalidate–execute safety and disk-pressure recovery.
- Human-facing Full Output strips ANSI/VT control sequences. Authenticated manifest fetches use the supported credentials/CORS boundary.
- README, landing pages, and the complete curated Docs corpus are available in English, Russian, Simplified Chinese, Spanish, Brazilian Portuguese, German, and Japanese. Locale-aware canonical/hreflang metadata, same-slug Docs switching, deterministic translation fingerprints, and responsive validation prevent silent drift.
- The public footer now uses the canonical full ThreadCells wordmark on a verified true-black background.

## Install or upgrade

For a new installation, follow [Quick Setup](QUICK_SETUP.md). Existing operators should follow [Upgrading](docs/UPGRADING.md): build or verify the exact tagged candidate, back up the SQLite database, preserve the prior immutable release for rollback, stage the candidate, and activate only after focused health and integrity checks pass.

The OCI artifact at `ghcr.io/iunknown404i/threadcells-release-bundle:v0.2.0-alpha.1` is a distribution bundle, not a Docker runtime image. Verify `BUNDLE-SHA256SUMS` and the archive checksum before using its contents.

## Compatibility

- Linux, tmux, Git, Python 3.10–3.14, and Node.js remain the supported operating foundation described in the installation guide.
- Codex remains the reference adapter. Claude Code and other built-in adapters expose only capabilities supported by the installed provider version; unsupported behavior is never simulated.
- Existing `v0.1.0-alpha.1` and `v0.1.0-alpha.2` tags and release artifacts remain immutable.
- No unqualified OCI `latest` tag is published during alpha. The established `latest-alpha` convenience tag may move to this release.

## Known limitations

- ThreadCells coordinates powerful local tools; a Git worktree is not a security sandbox and hostile multi-tenancy is not supported.
- The supported remote preview boundary is loopback plus an operator-managed SSH tunnel. Internet exposure requires the authentication, proxy, and host controls in [Remote access](docs/REMOTE_ACCESS.md).
- Provider-native authentication and provider-specific feature availability remain the operator's responsibility.

See the [public documentation](https://iunknown404i.github.io/threadcells/docs/) and [release process](docs/RELEASE_PROCESS.md) for the complete operating and distribution model.
