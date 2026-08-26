# ThreadCells v0.3.0-alpha.2

ThreadCells `v0.3.0-alpha.2` is a corrective prerelease for the accepted `v0.3.0-alpha.1` production line. It repairs Workflow Composer delivery and makes durable terminal exit final for executable workflow authority. It remains an alpha technical preview for trusted operators on Linux hosts.

## What changed

- Workflow Composer requests now carry a stable client request identity. The server persists each executable turn exactly once, retains the payload before provider transport, and safely replays the same turn after an interruption or runtime reconnect.
- API and Web UI submission results distinguish immediate admission, durable queueing, runtime recovery, duplicate acceptance, and lifecycle conflicts. A generic green success message is no longer shown for input that is only queued.
- A valid Ready resident terminal remains wakeable after normal completion, a prior completed workflow, runtime-generation change, or interrupted provider work. Distinct sequential inputs retain FIFO order without duplicate provider execution.
- Durable terminal exit now atomically closes executable workflows, cancels queued or claimed turns, terminally fails pending Inbox transport, releases provider and writer authority, and fences parent/child execution edges while preserving historical rows and results.
- New Inbox or Composer input targeting an Exited terminal is rejected or retained only as failed, non-executable history. A rolling reconciliation repairs stale rows created by older runtimes before provider admission or Full Cleanup planning.
- Unexpected-runtime-exit notifications are bound to the exact workflow that won the atomic cancellation transition, including a workflow replacement racing terminal exit.
- Housekeeping dry-run plans and Full Cleanup previews use the established bounded long-operation window instead of the ordinary 10-second Web timeout. Both actions expose progress, prevent duplicate submissions, retain fail-closed execution controls, and translate timeout or network failures into operator-safe product errors.
- Interactive inventory requests share the canonical Housekeeping lock with execution, so a disconnected client cannot start a competing planner; a retry receives an explicit busy state until the original read-only scan finishes.

## Lifecycle invariant

A Ready resident terminal can receive valid new work through Workflow Composer without manual provider-terminal input or process recreation. An Exited terminal cannot acquire or retain executable provider, workflow, Inbox, writer, or filesystem-mutation authority. Durable history remains queryable in both cases.

## Install or upgrade

For a new installation, follow [Quick Setup](QUICK_SETUP.md). Existing operators should follow [Upgrading](docs/UPGRADING.md): verify the exact tagged candidate, create and integrity-check a SQLite backup, preserve rollback during deployment acceptance, and activate only after health and workflow checks pass.

The OCI artifact at `ghcr.io/iunknown404i/threadcells-release-bundle:v0.3.0-alpha.2` is a distribution bundle, not a Docker runtime image. Verify `BUNDLE-SHA256SUMS` and the archive checksum before using its contents. The established `latest-alpha` convenience tag may move to this release; no unqualified stable `latest` tag is published.

## Compatibility and limitations

- Linux, tmux, Git, Python 3.10–3.14, and Node.js remain the supported operating foundation.
- Codex remains the reference adapter. Other built-in adapters expose only capabilities supported by the installed provider version.
- ThreadCells coordinates powerful local tools; worktrees are not security sandboxes and hostile multi-tenancy is unsupported.
- All previous tags and published artifacts, including immutable `v0.3.0-alpha.1`, remain unchanged.
- Full Cleanup still requires the existing operator unlock, a fresh fingerprinted preview, authoritative global idle state, and explicit permanent-action confirmation.

See the [public documentation](https://iunknown404i.github.io/threadcells/docs/) and [release process](docs/RELEASE_PROCESS.md) for the complete operating and distribution model.
