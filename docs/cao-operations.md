# CAO dual-lane operations

Non-capacity host policy continues to come from the deployed operations JSON.
On first upgraded startup, its four legacy capacity values seed the versioned
runtime-database settings once. Later capacity reads and updates use that
canonical persisted row; legacy files are not a competing capacity authority.

Use `cao-resource-status` for a compact verdict or
`cao-resource-status --json` for the complete projection. `GREEN` and `YELLOW`
permit admission; `RED` denies new managed contexts and heavy starts after one
bounded housekeeping recovery and recheck. An explicit Composer decision that
resumes an already-resident owner gate may proceed under disk-only RED while
remaining subject to Provider capacity; non-disk RED still fails closed. A full
heavy slot is ordinary busy capacity and does not change resource health.

Capacity is explicit in four dimensions. The recommended small-host values are
resident supervisors `5` (persistent
supervisor processes in Ready, Processing, Waiting, queued, or owner-gated
states), provider executions `3` (model turns executing now), work contexts `2`
(live delegated workers and reviewers; supervisors excluded), and heavy
executions `1`; operators may configure `2..50 / 1..50 / 1..50 / 1..50` through
`PUT /settings/orchestration-capacity`. A Processing supervisor counts as both resident and executing;
an idle/waiting supervisor counts only as resident. Admission
failures retain stable reason codes through API, MCP, and CLI:
`WORKTREE_WRITER_LEASE_HELD`, `WORKTREE_AUTHORITY_UNRECONCILED`,
`PROJECT_SUPERVISOR_ALREADY_RESIDENT`,
`RESIDENT_SUPERVISOR_CAPACITY_EXHAUSTED`,
`PROVIDER_EXECUTION_CAPACITY_EXHAUSTED`,
`WORK_CONTEXT_CAPACITY_EXHAUSTED`, and `RESOURCE_HEALTH_REJECTED`.

Reducing a limit does not stop active work. Its status reports `draining: true`,
availability remains zero, and new admissions wait until usage falls within the
new limit. Every change is atomic and appends an actor/reason/settings audit row.

Provider execution is acquired immediately before the tmux input transport and
released idempotently when the provider reaches Ready/Completed, waits in a
blocking handoff, fails, is cancelled, or exits. When capacity is full, the
input remains in `workflow_turns`/Inbox with state
`queued_provider_execution`; the existing watchdog reconciliation is the
wakeup owner after a release. No separate scheduler or notification queue is
used.

Project-backed resident supervisors are unique by authoritative Project ID.
Launching another live supervisor for that Project returns
`PROJECT_SUPERVISOR_ALREADY_RESIDENT`; termination releases both the project
residency and the global resident slot. Legacy no-project supervisors retain
compatibility without participating in project uniqueness.

## Managed delegated worktrees

An owner can launch the supervisor in the canonical project worktree. With no
explicit child working directory, CAO creates a linked Git worktree automatically:

- execution owners use one task branch/worktree for their complete warm contour;
- reviewers use a detached worktree at the exact source commit;
- explicit isolated working directories bypass automatic management;
- cleanup removes only clean, verified worktrees and keeps task branches;
- dirty/unverifiable state retains its metadata and writer lease fail-closed.

Completed managed direct handoffs use the same durable post-exit cleanup saga
as assigned-child retirement, but retain handoff result states. Cleanup intent
is admitted only for an exact handoff relation with a complete, finalized
immutable result, positive runtime exit, and exact managed-worktree identity;
cancelled, incomplete, failed, or identity-uncertain rows remain retained.

This path runs `git worktree`, not `git clone`; it does not copy generated trees
or run package installation, builds, or dependency bootstrap.

Run locally expensive work through:

```text
cao-heavy-run -- <command> [args...]
```

The command process inherits one `flock` slot, so the kernel releases it after
normal exit, failure, or termination. The wrapped command's exit status is
preserved. No LLM turn or notification participates in waiting.

## Privileged owner launch

The XHigh owner profile is a server-owned privileged classification. Configure
an OS-owned operator verifier as described in `docs/RESOURCE_MODEL.md`; authentication
is unavailable when that reference is missing or unsafe. Browser clients may
establish a five-minute HttpOnly, SameSite=Strict operator session. CLI/API
clients authenticate to `POST /operator/xhigh-grants`, explicitly confirm the
profile, then consume the returned 60-second one-use launch grant through the
ordinary session endpoint. Grants are digest-only and scope-bound. Ordinary MCP
assign/handoff paths cannot mint them, and prompt text is never authorization.

## Housekeeping

`cao-housekeeping --dry-run` shows the frequent cleanup plan without mutation.
`cao-housekeeping --mode weekly --dry-run` also inventories package and browser
caches. Summary output is bounded; actual-run status is written atomically to
`state/cao/housekeeping-status.json` and appears in resource status.
Dead legacy UNKNOWN-authority rows are counted as
`legacy_authority_reconciled`, separately from writer lease reconciliation.

Browser revision cache handling is inventory-only: referenced revisions and
live open paths are preserved, and stale unreferenced candidates are reported
for explicit operator action. Housekeeping never removes browser revisions.

Unknown temporary directories are never deleted. A removable directory directly
under the configured ephemeral root needs `.cao-ephemeral.json`:

```json
{"version":1,"expires_at":1790000000,"owner_pid":12345}
```

Playwright profiles additionally require `"kind":"playwright"`; orphan
termination also requires the runtime user, an expired marker, a dead owner,
an old process reparented to PID 1, a strict temporary-profile signature, and a
dedicated process group. Docker fallback cleanup considers only resources with
all labels `cao.ephemeral=true`, `cao.expires_at=<unix-seconds>`, and
`cao.owner_pid=<pid>`. Persistent and unknown Docker resources are never prune
targets.

## Deployment

`deployment/stage-ops-p1.py --dry-run` validates host artifacts without writes.
Stage into a candidate filesystem, build/install the wheel in a new versioned
virtualenv, validate it, then atomically switch the configured `cao*` compatibility
links using the established deployment procedure. Install the staged root-owned
systemd unit files, run `systemctl daemon-reload`, enable both timers, and restart
only `agent-control-cao.service`. Enable and start
`agent-control-full-cleanup.socket`, then require it to be active and its
`/run/threadcells/full-cleanup.sock` endpoint to be an `agentctl:agentctl` mode
`0600` Unix socket before accepting the deployment. Preserve the current runtime
as the known-good rollback and do not delete backups or unclassified historical
deployments.
