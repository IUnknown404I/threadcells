# Operations

Routine ThreadCells operation is mostly about preserving four kinds of truth: the running build identity, workflow ownership, available capacity, and recoverable state.

## Daily checks

Use Home, Agents, Settings → General, and Settings → Housekeeping to answer:

- Is the server healthy and is the expected build running?
- Are disk and capacity GREEN, YELLOW, or RED?
- Which supervisors and workers are genuinely active?
- Are any results delivered but not incorporated?
- Is a workflow waiting for an owner decision?
- If Telegram is enabled, does Settings → Telegram show the expected safe connection/test state?

The command-line capacity view is:

```bash
threadcells-resource-status
```

Use the local health endpoint for service monitoring:

```bash
curl -fsS http://127.0.0.1:9889/health
```

## Starting and stopping

Run `threadcells-server` on loopback or use the canonical installed service. A browser disconnect does not stop tmux-backed agents. A supported server restart preserves legitimately active terminal runtimes and then rehydrates durable open-workflow and Inbox delivery state. Closed runtimes are retired by exact terminal/process identity; historical session and result records do not depend on a tmux pane remaining alive.

Before a planned restart:

1. inspect active provider and heavy work;
2. avoid interrupting a mutation when possible;
3. record the current active and rollback build identities;
4. back up and integrity-check the database for an upgrade;
5. restart only the required ThreadCells services;
6. reconnect and verify workflows/results before retrying anything.

Use Graceful Exit for provider lifecycle. Killing tmux or deleting database rows manually can separate terminal state from durable workflow truth.

## Session and workflow hygiene

An exited child is not immediately disposable. Confirm that its durable result is delivered, read, incorporated, and acknowledged. Then retire its runtime resources while retaining history.

**Add Agent** targets the stable selected session lifetime. Historical session deletion and exited-terminal deletion target exact durable identities and are rejected while an active runtime, open/recovery workflow, writer lease, pending result, or other genuine lifecycle dependency remains. Retained logs, protected cleanup worktrees, and post-exit cleanup claims do not by themselves prevent logical deletion: ThreadCells preserves the resource authority, tombstones the exact session, and makes retries idempotent. A blocked deletion returns the specific lifecycle conflict rather than a generic missing/server error.

Within one session, Home and Agents preserve the backend's durable agent creation sequence in List and Grid views. Status, provider, profile, activity, polling, reconnect, and restart do not reorder agents; a newly created agent appends after earlier ones.

A provider final does not close an open mission. Explicitly complete a top-level workflow only after all owner-authorized work is finished. Use owner gate only for a genuine decision boundary.

## Capacity changes

Settings → Orchestration Capacity applies changes without server restart. Reductions drain; they do not kill active sessions. Change one constraint at a time and watch whether the intended queue improves.

Capacity mutations require an unlocked operator session and are audited. See [Capacity and resource model](RESOURCE_MODEL.md).

## Logs and evidence

Keep enough logs and result history to diagnose a failed run, but do not treat logs as the only durable truth. The database, workflow result, Git commit/diff, candidate manifest, and test evidence each answer different questions.

Avoid logging prompts or values that contain credentials. ThreadCells public/API errors should remain safe to display.

## Housekeeping

Housekeeping is always plan-first. Inspect the dry-run candidate list and plan identity, then explicitly execute the exact plan. The executor rebuilds current protection and revalidates every candidate before mutation. It may retire proven closed terminal runtimes and acknowledged cleanup-pending worktrees without erasing durable history.

Backups are inventory-only and never automatically deleted. Unknown or active resources remain protected. Full Cleanup is a separately confirmed operator action that runs only while all agents are idle, preserves Ready continuation authority, and intentionally removes every proven inactive local release so local rollback becomes unavailable. See [Housekeeping](HOUSEKEEPING.md).

## Production change discipline

For an upgrade:

1. build and verify an immutable candidate from an exact commit;
2. preserve the current installation as rollback;
3. back up and integrity-check the database;
4. stage through the canonical deployment mechanism;
5. promote the exact staged candidate;
6. restart only required services;
7. smoke-test health, UI, provider preflight, operator authorization, workflows, terminals, and configured global Telegram notifications.

Do not publish, push, tag, or change public exposure as an incidental part of local deployment. See [Upgrading](UPGRADING.md) and [Deployment](DEPLOYMENT.md).

## When something looks wrong

Preserve evidence before cleanup or retry. Record the build identity, session/terminal/workflow IDs, safe error message, relevant log window, Git status, and current capacity. Then use the symptom-oriented [Troubleshooting](TROUBLESHOOTING.md) guide.
