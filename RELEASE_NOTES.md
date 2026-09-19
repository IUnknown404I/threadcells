# ThreadCells v0.4.1-alpha

ThreadCells `v0.4.1-alpha` is a focused post-alpha reliability release. It closes the remaining protected-workspace deletion cases, moves recovery takeover onto the normal authenticated owner session, adds truthful retirement for an unknown external effect, and adds safe diagnostics and bounded Housekeeping history. It remains a technical preview for trusted operators on one Linux host.

## Highlights

### Durable work from admission to acknowledgement

Accepted workflow input now remains tied to an exact durable turn and effect through queueing, provider-capacity waits, reconnects, service restarts, and model compaction. Exact-revision review keeps its inspected commit identity, durable result, delivery/read/ack state, and parent continuation. ThreadCells does not interpret a silent provider or an uncertain transport as successful completion, and it does not replay an indeterminate send.

Current and History expose this lineage without relying on terminal output as the source of truth. Results can be delivered, read, incorporated, and acknowledged before child resources retire; a provider final alone still does not close an open top-level workflow.

### Recovery and deletion tell the same story

Recovery replacement continues to fence the predecessor writer before a successor becomes writable. Once takeover is durably complete, historical `recovery_fenced` metadata no longer blocks deletion of an otherwise eligible predecessor Session—even after the successor has exited and its work context is retired. Deletion remains idempotent and preserves the successor branch and durable ownership evidence. Receipt-only Sessions whose legacy cleanup provenance is missing can remove Session history only after the operator explicitly chooses non-destructive preservation; ThreadCells never treats that confirmation as workspace cleanup authority.

### Housekeeping with explainable evidence

Housekeeping now persists the newest 50 non-preview runs and exposes them in bounded cursor pages. Success, partial completion, and failure retain their time, outcome, duration, reclaimed bytes, warnings, and detailed bounded report without adding a polling or logging subsystem. Dry runs remain transient.

The privileged protected-inventory request is pathless, bounded, serialized, and bound to the exact trusted root configuration. Full Cleanup retains its exact-plan, all-idle, permanent-confirmation, and execute-time revalidation gates. A disconnect or restart recovers the one durable operation result rather than starting another destructive pass.

### A steadier operating surface

- Agents keeps History, Inbox, and Output readable on desktop while Terminal, Finish, and Delete use equal icon-only controls. The six primary actions stay in one stable right-aligned row without an empty Recovery slot.
- Home, Agents, Current, History, Full Output, status badges, and Session actions use consistent lifecycle projections.
- Absolute timestamps support browser Auto or a selected IANA time zone without changing durable server values.
- About can copy or download an explainable safe diagnostic summary. It is assembled from named scalar fields only and excludes secrets, cookies, prompts, transcripts, environment values, and unknown/nested input.
- Mobile and intermediate-width layouts preserve usable labels, touch targets, and page bounds.

## Public experience

The landing page now states who ThreadCells is for, the supported Ubuntu/Debian baseline, how the eight built-in provider adapters are qualified, and where the single-host trusted-operator boundary stops. Installation, provider compatibility, limitations, release identity, and documentation are linked directly rather than hidden behind product slogans.

Public screenshots and the release tour are recaptured from the real release system after deployment. Private paths, destinations, credentials, and workflow content are excluded or irreversibly redacted; operational counts and lifecycle states remain real.

## Upgrade and compatibility

For a new installation, follow [Quick Setup](QUICK_SETUP.md). Existing operators should follow [Upgrading](docs/UPGRADING.md): build and verify the exact candidate, create and integrity-check a SQLite backup, preserve rollback during acceptance, and activate only after health and workflow checks pass.

Existing active legacy Sessions are preserved rather than moved, reset, cleaned, or stashed. New Project-backed writable supervisor Sessions continue to use isolated managed worktrees. Existing durable workflow, result, recovery, and deletion records are reconciled through product lifecycle mechanisms; do not edit the database to clear a fence.

## Known limitations

- ThreadCells remains a single-host technical preview for a trusted operator. Worktrees isolate Git checkouts, not operating-system, filesystem, or network access.
- Provider readiness, resume, usage, authentication, and model controls depend on the installed native CLI. Codex is the release-acceptance reference; other adapters are conditional where documented.
- Full Cleanup requires authoritative all-idle state and may correctly preserve backups, tools, dirty/unpublished worktrees, or other ambiguous resources.
- An authenticated owner can retire one exactly scoped indeterminate external effect. The result remains `unknown_preserved`; ThreadCells neither replays it nor reports success, failure, or acknowledgement.
- The authenticated UI supports English and opt-in Russian. Public documentation remains available in all seven supported locales.

The OCI artifact at `ghcr.io/iunknown404i/threadcells-release-bundle:v0.4.1-alpha` is a distribution bundle, not a runtime container image. Verify `BUNDLE-SHA256SUMS` and the archive checksum before using its contents. `latest-alpha` may point to this exact artifact; the unqualified stable `latest` tag is not published.

All previous tags and immutable release artifacts remain unchanged. See the [public documentation](https://iunknown404i.github.io/threadcells/docs/) and [release process](docs/RELEASE_PROCESS.md) for the complete operating and distribution model.
