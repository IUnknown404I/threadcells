# Housekeeping

Housekeeping reclaims runtime artifacts only when ThreadCells can prove they are eligible. It is intentionally conservative: an unknown, unreadable, active, referenced, or changed resource is protected rather than guessed safe to delete.

![Live ThreadCells Housekeeping with disk health, protected backups, schedules, and cleanup policy](/media/screenshots/threadcells-housekeeping.webp)

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
- clean inactive linked worktrees whose HEAD is already contained in an explicitly configured durable Git ref;
- marked reproducible caches/generated evidence located directly under an approved cache root, after their owner is dead and retention elapsed.

Housekeeping does not blindly delete source repositories, active or unknown worktrees, running terminals, open files, current/rollback releases, staged candidates, or backups. Linked worktrees are retired through `git worktree remove` and `git worktree prune`, never generic recursive deletion. Retiring a closed terminal runtime does not delete its durable session, agent, Inbox, result, or workflow history.

A reproducible directory must be an immediate child of a configured root and carry `.threadcells-reproducible.json`:

```json
{"schema_version":1,"owner":"threadcells","kind":"cache","created_at":1790000000,"owner_pid":12345}
```

Supported kinds are `cache`, `generated`, `test_evidence`, and `candidate`. Missing or invalid markers, symlinks, path escapes, live owners, and paths inside the retention window remain protected.

Deployments may additionally name exact ThreadCells-owned cache prefixes for backward-compatible CI caches. Those entries remain constrained to direct children of the approved runtime-owned root and require elapsed retention plus the same active-process and execute-time identity checks. Unlisted prefixes, including ambiguous release-candidate artifacts, remain protected.

## Plan first, execute second

A dry-run plan is read-only. Each candidate includes its category, canonical identity/fingerprint, proposed action, total bytes, estimated reclaim bytes when known, retention reason, and protection reason. Class summaries separately report actionable/reclaimable and preserved/protected footprints, so a large protected class is not hidden as zero bytes.

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

## Full Cleanup

The final danger-zone action in Settings → Housekeeping is **Delete all system files — Full Cleanup**. It uses the same canonical inventory, protected set, immutable plan identity, and execute-time identity checks as normal Housekeeping, but applies maximum proven-safe retention: reproducible caches, old logs, build/candidate/temp artifacts, safely retirable worktrees, and every inactive local release can become eligible. Unknown ownership or ambiguous authority remains protected and is explained in the plan and report.

Full Cleanup is available only when backend lifecycle truth proves every relevant agent is Ready, Exited, or an explicitly equivalent non-executing state. Working, Processing, Starting, queued filesystem mutation, provider execution, Heavy work, runtime operations, and unknown lifecycle identity block execution. The server acquires the canonical admission fences and rechecks this idle gate immediately before mutation; an agent becoming active after preview aborts the run without deleting anything.

Preview is read-only. Execution requires the existing short-lived operator unlock and the existing permanent-action confirmation modal; there is no Full Cleanup password or client-stored secret. The request confirms one exact 64-character `plan_id` and carries no arbitrary path.

Every pathname-based Full Cleanup candidate is executed by the narrow, socket-activated root helper after it independently reauthenticates the operator, rebuilds the exact plan, proves the idle gate, and verifies the control plane still holds every admission fence. The helper moves each candidate into a root-exclusive same-filesystem quarantine, locks the captured directory tree against runtime-user mutation, and then deletes only the verified identities through directory descriptors. A changed identity is retained and reported; execution never falls back to a weaker runtime-user path deletion. Non-filesystem lifecycle resources continue through their canonical transactional executors.

On a successful Full Cleanup, only the active immutable local ThreadCells release remains. All proven inactive rollback/recovery releases are removed, release metadata is reconciled atomically, and local rollback is reported as unavailable. The active release and active pointer can never be candidates. Ready agents remain usable: their worktrees, writer authority, current context, current output, and other continuation state stay protected. Exited history may remain in SQLite after its safe filesystem output is cleaned; Full Output then reports that durable output is unavailable instead of failing or fabricating text.

Backups, current source/tool authority, provider credentials/state, the SQLite database, and any unproven resource remain protected. A second Full Cleanup safely produces a near-zero actionable plan except for newly eligible or previously protected items.

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

Protected workflow authority is derived from durable root-terminal identity. Startup and frequent reconciliation cancel orphaned non-recovery workflows whose root terminal no longer exists, then regenerate the protected set. Until that relationship is reconciled, worktree retirement fails closed for the entire uncertain inventory.

## Schedules

Settings → Housekeeping separates policy, schedule, planning, execution, and reports. Supported schedule shapes include:

- a frequent interval from 15 minutes through 365 days, such as `6h`;
- a weekly UTC schedule, such as `Sun 04:00 UTC`;
- disk-pressure cleanup using `on_red`.

Installed timers may poll every 15 minutes, with staggered initial activation so the frequent and weekly checks do not normally collide. Durable receipts keep a schedule class from running twice before it is due. A scheduled poll that finds the canonical Housekeeping engine already active exits successfully as skipped and tries again later; manual lock contention remains an error. A scheduled run creates and executes its due plan under one service lock; it does not reuse a human-approved manual plan.

Housekeeping changes and manual execution are protected by [Operator authorization](OPERATOR_AUTHORIZATION.md).

## Disk-pressure behavior

At YELLOW, inspect growth and run a dry plan. At RED, ThreadCells can admit a recovery-safe Housekeeping heavy lease even though ordinary heavy work may be denied. Pressure plans order the largest proven-safe candidates first and show dominant protected classes, but the cleanup still counts as one Heavy execution and does not bypass any candidate protection.

YELLOW is an inspection state, not permission to manufacture reclaimable bytes. When all remaining large classes are protected, create external capacity or document the protected footprint rather than weakening the predicates.

Package-cache reclaim is reported as unknown/zero when the command cannot prove bytes; ThreadCells does not advertise guessed recovery.

## Reports and partial failure

The latest report records plan/run identity, resource state, estimates, actual results, per-candidate outcomes, and stable reason codes. One candidate failure does not weaken protection for later candidates or hide independent successes.

After a run, verify disk pressure and inspect skipped/failed entries. Re-plan before another execution; do not reuse an old plan after state changes.

## Backups and releases

Backups are inventory-only. Retention decisions for backup media belong to the operator's backup policy, not automatic Housekeeping.

Release and candidate cleanup shares the canonical staging lock and requires trusted reference metadata. Normal Housekeeping protects the active and rollback runtimes. Full Cleanup protects only the active release and deliberately removes every proven inactive local rollback release after explicit operator confirmation. See [Upgrading](UPGRADING.md).

Installed scheduled Housekeeping services receive the narrow release-maintenance group needed to reclaim an eligible immutable release. The main control plane and ordinary agent processes do not. A manual/API run without that authority skips release deletion with `RELEASE_ADMIN_GROUP_REQUIRED`, continues independent safe cleanup, and leaves the scheduled service to reclaim the release later through the same plan/execute engine.

Open-path protection inventories every process owned by the configured ThreadCells runtime account, regardless of which authorized account invokes a manual plan. Other host accounts are outside the ownership boundary for disposable ThreadCells state; unreadable private `/proc` entries from those accounts do not disable cleanup for the whole host. An unknown runtime identity or any uncertainty while inspecting a runtime-account process still fails closed.

## Common mistakes

- Deleting a worktree directory directly to recover space.
- Treating an estimated byte count as guaranteed reclaim.
- Executing a plan that was not inspected.
- Assuming a stopped PID is sufficient proof that a browser/process group is the old one.
- Expecting Housekeeping to delete backups.
- Raising disk thresholds instead of addressing sustained growth.
