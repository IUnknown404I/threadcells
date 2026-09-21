# ThreadCells v0.4.2-alpha

ThreadCells `v0.4.2-alpha` is a focused Housekeeping reliability release. It makes the installed frequent timer also serve as the event poller for RED root-disk recovery, while preserving exact-plan execution, retention boundaries, resource admission, and fail-closed ownership checks. It remains a technical preview for trusted operators on one Linux host.

## Highlights

### Automatic recovery on real disk pressure

The frequent scheduled tick now reads the canonical root-disk projection before Heavy admission. A RED or CRITICAL root disk promotes that tick to the existing `pressure` Housekeeping mode; non-RED ticks remain ordinary frequent maintenance, and weekly retention keeps its independent schedule.

Automatic pressure work still acquires the one Heavy slot and the canonical Housekeeping singleton. After any wait, it rechecks disk health inside those boundaries. If another operation has already cleared pressure, ThreadCells performs no inventory or cleanup and records `ROOT_DISK_PRESSURE_CLEARED` with current post-run disk evidence.

### One accounting and retention model

Automatic, CLI, API, and Web UI cleanup use the same planner and executor. Class overlap remains fail-closed, protected and inventory-only bytes never become reclaimable, retention policy stays mode-specific, and execution is bound to the inspected content-addressed plan. Actual reclaimed bytes, observed disk delta, protected resources, skips, failures, and warnings remain distinct in the report and bounded Housekeeping history.

### Operator flow remains explicit

Settings → Housekeeping continues to expose disk pressure, mode selection, a read-only dry-run plan, operator-gated execution of that exact plan, and the resulting report. A changed plan or busy singleton fails closed. Full Cleanup remains a separate permanent action and still requires authoritative all-idle lifecycle truth; this release does not weaken or automatically invoke it.

## Upgrade and compatibility

For a new installation, follow [Quick Setup](QUICK_SETUP.md). Existing operators should follow [Upgrading](docs/UPGRADING.md): build and verify the exact candidate, create and integrity-check a SQLite backup, preserve rollback during acceptance, and activate only after health and workflow checks pass.

Existing policies and schedule values remain compatible. Installed deployments receive the corrected frequent-timer behavior when the new release is activated; no database or lock editing is required.

## Known limitations

- ThreadCells remains a single-host technical preview for a trusted operator. Worktrees isolate Git checkouts, not operating-system, filesystem, or network access.
- Automatic pressure recovery can reclaim only resources whose ownership, retention eligibility, identity, and current inactivity are proven. Backups, active resources, ambiguous paths, and unknown state remain protected.
- A YELLOW but non-RED disk is reported truthfully and does not trigger pressure-mode cleanup. Operators can inspect a manual plan without executing it.
- Full Cleanup requires authoritative all-idle state and may correctly preserve backups, tools, dirty or unpublished worktrees, and other ambiguous resources.
- The authenticated UI supports English and opt-in Russian. Public documentation remains available in all seven supported locales.

The OCI artifact at `ghcr.io/iunknown404i/threadcells-release-bundle:v0.4.2-alpha` is a distribution bundle, not a runtime container image. Verify `BUNDLE-SHA256SUMS` and the archive checksum before using its contents. `latest-alpha` may point to this exact artifact only after an approved publication; the unqualified stable `latest` tag is not published.

All previous tags and immutable release artifacts remain unchanged. See the [public documentation](https://iunknown404i.github.io/threadcells/docs/) and [release process](docs/RELEASE_PROCESS.md) for the complete operating and distribution model.
