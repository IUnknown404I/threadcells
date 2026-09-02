# ThreadCells v0.3.4-alpha

ThreadCells `v0.3.4-alpha` adds safe supervisor recovery takeover and isolated writable supervisor Sessions within the same Project. It also includes the workflow, provider-safety, and child-lifecycle reliability work accepted since `v0.3.3-alpha`. This remains an alpha technical preview for trusted operators on Linux hosts.

## Highlights

### Safe supervisor recovery takeover

An owner-authorized recovery action can now replace an unusable supervisor without creating a second valid writer for the same work context. The durable takeover saga fences the old supervisor and writer generation before admitting its successor, preserves the existing managed worktree—including dirty state—and resumes safely across service restarts. The old terminal remains visible as `recovery_fenced` for audit instead of being erased. See [#95](https://github.com/IUnknown404I/threadcells/issues/95).

### Multiple isolated supervisors per Project

Independent Sessions in one Project can now run separate supervisors subject to normal capacity. Every new Project-backed writable supervisor, including the first, receives a unique managed Git worktree, branch, work context, and writer lease. The registered Project root is canonical Git/source authority rather than the normal writable agent directory. Same-context replacement still uses recovery takeover; ordinary admission cannot acquire another context's writer lease. See [#97](https://github.com/IUnknown404I/threadcells/issues/97).

## Reliability

- Canonical session identity now survives child admission, so name collisions cannot silently join the wrong historical Session ([#85](https://github.com/IUnknown404I/threadcells/issues/85)).
- Assigned child agents retire through durable, restart-safe reconciliation only after their results are finalized and acknowledged; quiescent completed provider states no longer strand them ([#82](https://github.com/IUnknown404I/threadcells/issues/82)).
- Provider content-unavailable outcomes are structured and generation-fenced. ThreadCells preserves lifecycle evidence without retaining, reconstructing, or automatically retrying blocked provider content ([#89](https://github.com/IUnknown404I/threadcells/issues/89)).
- Queued Workflow Composer input is durable scheduling authority: ThreadCells autonomously wakes or reconnects an eligible provider, injects FIFO input exactly once into an existing execution when possible, and recovers rolling-upgrade and false-Ready boundaries without Raw Terminal intervention ([#92](https://github.com/IUnknown404I/threadcells/issues/92)).

## Workspace and Git authority

- One writable work context maps to one managed worktree and at most one active writer lease.
- Independent Project Sessions use distinct checkouts and branches while sharing canonical repository object authority.
- Recovery takeover preserves the original work context and worktree, rotates writer authority, and permanently fences the replaced supervisor generation.
- Read-only review does not require an unnecessary writable worktree, and operation-level locks continue to protect exclusive deployment and destructive actions.

## Upgrade and compatibility

Existing active legacy Sessions that use a shared or Project-root workspace remain in place. ThreadCells does not automatically move, copy, reset, clean, or stash their state during upgrade. New Project-backed writable supervisor Sessions receive isolated managed worktrees automatically.

For a new installation, follow [Quick Setup](QUICK_SETUP.md). Existing operators should follow [Upgrading](docs/UPGRADING.md): verify the exact tagged candidate, create and integrity-check a SQLite backup, preserve rollback during deployment acceptance, and activate only after health and workflow checks pass.

## Operational notes

- Recovery takeover is an explicit owner action and fails closed for healthy or actively Processing supervisors.
- Global Resident, Provider, and Work capacity limits still apply even though multiple independent Sessions may belong to one Project.
- Workflow owner gates remain distinct from queued-input liveness, provider reconnect, and dispatcher failure states.

## Known limitations

- ThreadCells remains a single-host technical preview for trusted operators; managed worktrees isolate Git checkouts, not operating-system, filesystem, or network access.
- Existing legacy shared-root Sessions are preserved rather than migrated automatically.
- Native provider capability and authentication reporting vary by installed CLI.
- The authenticated UI supports English and opt-in Russian. Public Docs remain available in all seven supported locales.

The OCI artifact at `ghcr.io/iunknown404i/threadcells-release-bundle:v0.3.4-alpha` is a distribution bundle, not a runtime container image. Verify `BUNDLE-SHA256SUMS` and the archive checksum before using its contents. `latest-alpha` may point to this exact artifact; the unqualified stable `latest` tag is not published.

All previous tags and immutable release artifacts remain unchanged. See the [public documentation](https://iunknown404i.github.io/threadcells/docs/) and [release process](docs/RELEASE_PROCESS.md) for the complete operating and distribution model.
