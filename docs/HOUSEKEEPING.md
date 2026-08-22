# Housekeeping

Housekeeping reclaims runtime artifacts only when ThreadCells can prove they are eligible. It is intentionally conservative: an unknown, unreadable, active, referenced, or changed resource is protected rather than guessed safe to delete.

## What can be cleaned

Depending on age and ownership evidence, a plan can include:

- expired temporary paths carrying ThreadCells ownership markers;
- old terminal attachments not referenced by an active terminal;
- logs eligible for compression or retention cleanup;
- orphaned browser process groups identified by exact process identity;
- browser revisions and caches not referenced by active metadata;
- ThreadCells-labelled containers and volumes whose owner is dead and unreferenced;
- trusted package caches with a measurable reclaim action;
- inactive candidates/releases represented by canonical staging metadata.
- exact closed-terminal runtime panes and process descendants whose durable terminal is already closed and whose process identity still matches;
- cleanup-pending managed child worktrees after their durable result/retirement boundary is acknowledged and revalidated.

Housekeeping does not blindly delete source repositories, active or unknown worktrees, running terminals, open files, current/rollback releases, staged candidates, or backups. Retiring a closed terminal runtime does not delete its durable session, agent, Inbox, result, or workflow history.

## Plan first, execute second

A dry-run plan is read-only. Each candidate includes its category, canonical identity/fingerprint, proposed action, estimated bytes when known, retention reason, and protection reason.

```text
Inspect current state
      ↓
Build immutable plan and plan_id
      ↓ operator reviews
Execute exact plan_id
      ↓
Rebuild protected set under lock
      ↓
Revalidate each candidate immediately before action
      ↓
Report reclaimed, skipped, changed, and failed items
```

If the candidate set changes between plan and execution, manual execution rejects the stale plan without changing resources. Every remaining candidate is checked again just before mutation.

## Safe manual example

From the installed environment, first request JSON output:

```bash
threadcells-housekeeping --dry-run --json
```

Review every candidate and copy the returned `plan_id`. Execute only that inspected plan:

```bash
threadcells-housekeeping --plan-id PLAN_ID_FROM_DRY_RUN
```

Do not script `plan_id` extraction and immediate execution until you understand the plan. A dry-run never implies approval to delete.

## Protected-set philosophy

The protected set combines active terminals and worktrees, writer/workflow ownership, current source/runtime lineage, active and rollback releases, staged candidates, referenced browser revisions, open files, live process start identity and terminal identity, container reference metadata, backups, and shared locks.

The details matter to the implementation, but the operator rule is simple: **absence of evidence is not evidence that a resource is dead**. If protection cannot be established accurately, Housekeeping skips it and reports why.

## Schedules

Settings → Housekeeping separates policy, schedule, planning, execution, and reports. Supported schedule shapes include:

- a frequent interval from 15 minutes through 365 days, such as `6h`;
- a weekly UTC schedule, such as `Sun 04:00 UTC`;
- disk-pressure cleanup using `on_red`.

Installed timers may poll every 15 minutes, with staggered initial activation so the frequent and weekly checks do not normally collide. Durable receipts keep a schedule class from running twice before it is due. A scheduled poll that finds the canonical Housekeeping engine already active exits successfully as skipped and tries again later; manual lock contention remains an error. A scheduled run creates and executes its due plan under one service lock; it does not reuse a human-approved manual plan.

Housekeeping changes and manual execution are protected by [Operator authorization](OPERATOR_AUTHORIZATION.md).

## Disk-pressure behavior

At YELLOW, inspect growth and run a dry plan. At RED, ThreadCells can admit a recovery-safe Housekeeping heavy lease even though ordinary heavy work may be denied. The cleanup still counts as one Heavy execution and does not bypass candidate protection.

Package-cache reclaim is reported as unknown/zero when the command cannot prove bytes; ThreadCells does not advertise guessed recovery.

## Reports and partial failure

The latest report records plan/run identity, resource state, estimates, actual results, per-candidate outcomes, and stable reason codes. One candidate failure does not weaken protection for later candidates or hide independent successes.

After a run, verify disk pressure and inspect skipped/failed entries. Re-plan before another execution; do not reuse an old plan after state changes.

## Backups and releases

Backups are inventory-only. Retention decisions for backup media belong to the operator's backup policy, not automatic Housekeeping.

Release and candidate cleanup shares the canonical staging lock and requires trusted reference metadata. The active and rollback runtimes remain protected. See [Upgrading](UPGRADING.md).

Installed scheduled Housekeeping services receive the narrow release-maintenance group needed to reclaim an eligible immutable release. The main control plane and ordinary agent processes do not. A manual/API run without that authority skips release deletion with `RELEASE_ADMIN_GROUP_REQUIRED`, continues independent safe cleanup, and leaves the scheduled service to reclaim the release later through the same plan/execute engine.

Open-path protection inventories every process owned by the ThreadCells runtime account. Other host accounts are outside the ownership boundary for disposable ThreadCells state; unreadable private `/proc` entries from those accounts do not disable cleanup for the whole host. Any uncertainty while inspecting a runtime-account process still fails closed.

## Common mistakes

- Deleting a worktree directory directly to recover space.
- Treating an estimated byte count as guaranteed reclaim.
- Executing a plan that was not inspected.
- Assuming a stopped PID is sufficient proof that a browser/process group is the old one.
- Expecting Housekeeping to delete backups.
- Raising disk thresholds instead of addressing sustained growth.
