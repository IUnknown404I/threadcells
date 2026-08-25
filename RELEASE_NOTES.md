# ThreadCells v0.3.0-alpha.1

ThreadCells `v0.3.0-alpha.1` advances the accepted `v0.2.0-alpha.1` production line with lifecycle reliability, durable presentation order, maximum proven-safe cleanup, and clearer orchestration routing. It remains an alpha technical preview for trusted operators on Linux hosts.

## What changed

- Completed sessions can be deleted by their stable lifetime identity even when post-exit cleanup authority must retain a protected worktree or filesystem artifact. Active runtimes and genuine workflow/writer dependencies still return explicit lifecycle conflicts; durable receipts make retries safe.
- The bounded delete/remove audit corrected two truthful-UI defects: removing a Flow now states that its registration and schedule are removed while its definition file is retained, and provider-owned agent directories are visibly read-only instead of appearing to save a removal that cannot persist.
- Home and Agents now preserve the backend's durable agent creation sequence in List and Grid. Status, ID, provider, profile, activity, polling, reconnect, restart, expansion, and lifecycle changes no longer reorder agents; new agents append.
- Settings → Housekeeping adds **Delete all system files — Full Cleanup**. It previews maximum proven-safe reclaim across old releases, rollback, logs, reproducible caches, candidates, build/temp evidence, and safely retirable managed worktrees while protecting current/Ready authority and every ambiguous resource.
- Full Cleanup reuses the existing short-lived operator unlock and existing permanent-action confirmation. No new password or client-side secret exists. The backend serializes with launch/provider/Heavy admission and revalidates that every relevant agent is idle immediately before mutation.
- A successful Full Cleanup leaves only the active immutable local ThreadCells release and truthfully reports that local rollback is unavailable. Ready agents remain usable; historical records whose old output was cleaned return a clear output-unavailable state.
- The canonical routing catalog distinguishes Terra and Sol supervisors from developer tiers, permits routine Terra implementation under a Sol supervisor, classifies retry failures, prevents a third same-tier semantic retry, and reserves XHigh for critical systemic authority.
- Canonical English Docs, all six localized Docs sets, localized READMEs, packaged Docs, and release-facing version examples are updated for the `v0.3.0-alpha.1` contract.
- Post-`v0.2.0-alpha.1` production fixes remain included: provider admission/continuation, interrupted-turn receipts, stable session lifetimes, Add Agent repairs, exited-terminal safety, child-retirement projection, Housekeeping protected-set/pressure recovery, and the seven-locale public surface.

## Full Cleanup safety boundary

Preview does not mutate anything. Execution requires an unlocked operator session, explicit confirmation of the exact inspected plan, and authoritative all-idle state. Working, Processing, Starting, queued filesystem mutation, active provider execution, Heavy work, runtime operations, or unknown lifecycle identity block the operation. If activity begins after preview, execution aborts before deletion.

The active release, active pointer, SQLite database, Ready-agent context/worktree/writer authority, current source/tool authority, credentials/provider state, backups without explicit disposable authority, dirty or unpublished worktrees, path escapes, symlinks, and unknown resources remain protected. Full means maximum proven-safe reclaim, never disabled safety.

## Install or upgrade

For a new installation, follow [Quick Setup](QUICK_SETUP.md). Existing operators should follow [Upgrading](docs/UPGRADING.md): verify the exact tagged candidate, back up and integrity-check SQLite, preserve rollback for normal deployment acceptance, and activate only after health and product checks pass. Full Cleanup removes that local rollback only after a separate explicit operator-authorized execution.

The OCI artifact at `ghcr.io/iunknown404i/threadcells-release-bundle:v0.3.0-alpha.1` is a distribution bundle, not a Docker runtime image. Verify `BUNDLE-SHA256SUMS` and the archive checksum before using its contents. The established `latest-alpha` convenience tag may move to this release; no unqualified stable `latest` tag is published.

## Compatibility and limitations

- Linux, tmux, Git, Python 3.10–3.14, and Node.js remain the supported operating foundation.
- Codex remains the reference adapter. Other built-in adapters expose only capabilities supported by the installed provider version.
- ThreadCells coordinates powerful local tools; worktrees are not security sandboxes and hostile multi-tenancy is unsupported.
- All previous tags and published artifacts, including immutable `v0.2.0-alpha.1`, remain unchanged.

See the [public documentation](https://iunknown404i.github.io/threadcells/docs/) and [release process](docs/RELEASE_PROCESS.md) for the complete operating and distribution model.
