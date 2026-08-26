# Capacity and resource model

ThreadCells separates capacity because coding-agent work can pressure different parts of a host at different times. A model turn consumes provider capacity; an assigned coding context can remain active while the model is idle; a build can saturate the machine after model output has stopped.

![Live Orchestration Capacity showing independent resident, provider, work, and heavy limits](/media/screenshots/threadcells-capacity.webp)

Increasing every number together is usually not faster. It can create model quota contention, memory pressure, disk churn, and several expensive builds competing for the same CPU.

## The four limits

### Resident supervisors

A resident slot holds a top-level supervisor or owner session that must remain available across delegation and callbacks. It consumes residency even while waiting for a worker result.

This is separate because terminating an idle-looking supervisor can lose the context responsible for integrating the mission.

### Provider executions

A provider-execution slot is used while a model/provider is actively producing a turn. The relevant constraints are provider concurrency, network activity, process count, and sometimes memory.

An agent waiting at a prompt does not need a provider-execution slot.

### Work contexts

A Work slot represents a delegated worker or reviewer that currently owns a bounded context. It may hold a managed worktree and writer authority while it waits between model turns.

A top-level session root consumes resident capacity, not Work capacity. A resident delegated child consumes Work capacity.

### Heavy executions

A Heavy slot is for host-intensive work such as a production build, Chromium run, large test suite, or repository-wide scan. Heavy admission protects CPU, memory, and I/O headroom.

Use the canonical heavy runner for commands that qualify. Ordinary small tests and file inspection do not need a Heavy slot.

## The default starting point

The packaged `5 resident / 3 provider / 2 Work / 1 Heavy` configuration is a conservative small-host starting point, not a benchmark or fixed product limit.

Allowed ranges are 2–50 resident slots and 1–50 for each other limit. Values are persisted in the runtime database and take effect without restarting the server.

## What should I set on my machine?

Start conservatively, observe memory/disk pressure and queueing, then change one limit at a time. These examples illustrate shape; they are not performance guarantees.

| Host example | Residents | Provider | Work | Heavy | Rationale |
| --- | ---: | ---: | ---: | ---: | --- |
| Small VPS | 2 | 1 | 1 | 1 | One supervisor and one bounded child; serialize expensive work. |
| Developer workstation | 5 | 3 | 2 | 1 | Useful parallel model turns while keeping builds serialized. |
| Larger shared host | 8 | 5 | 4 | 2 | More resident missions and workers, with measured headroom for two heavy tasks. |

Before increasing a limit, ask which queue is actually blocking progress:

- Provider full but CPU is idle: consider one more Provider slot if quotas allow.
- Work full with idle provider capacity: retire completed acknowledged children or cautiously raise Work.
- Heavy full during builds: a second Heavy slot helps only if CPU, RAM, and disk can support concurrent builds.
- Resident full: close completed top-level sessions; do not disguise abandoned supervisors by only raising the limit.

## Memory and disk pressure

ThreadCells observes host pressure alongside configured counts. Many native CLIs, tmux panes, browser processes, worktrees, build caches, and logs can outlive the short provider turn that created them.

Disk status uses exact thresholds:

- **GREEN:** below 70% used.
- **YELLOW:** 70% to below 85%.
- **RED:** 85% to below 92%.
- **CRITICAL:** 92% or above. Aggregate admission remains RED and includes the
  `DISK_CRITICAL` reason, while the disk-specific projection reports CRITICAL.

YELLOW is a prompt to inspect growth and plan Housekeeping. RED can deny risky new work and admit recovery-safe cleanup. Unknown state fails closed; ThreadCells does not assume an unreadable filesystem is healthy.

An explicit Workflow Composer decision for an already-resident owner-gated workflow is also a narrow recovery path under disk-only RED: it acquires ordinary Provider capacity but does not create a Work context. Memory, PSI, unknown, or mixed RED still rejects it, and the durable turn exposes a resource-recovery wait instead of consuming transport retries.

## Draining after a reduction

Lowering a limit never kills active work. If current usage is above the new value, that category becomes **draining** and denies new admissions until active usage falls within the limit.

Example: changing Work from 4 to 2 while three children are active leaves all three running. As children finish and retire, no replacement is admitted until usage reaches 2 or less.

Heavy inventory continues counting active higher-numbered slots after a reduction, so a limit change cannot hide an expensive process.

## When capacity is released

- Provider capacity releases when the active model turn ends.
- Heavy capacity releases when the registered heavy command exits.
- Work capacity releases only after the delegated context is safely retired.
- Resident capacity releases when the top-level supervisor/owner session closes.

A completed child's result must be recorded, delivered, incorporated, and acknowledged before resource retirement. History remains after runtime capacity is released.

Admission is rechecked at launch and continuation boundaries. A queued provider turn starts when a provider slot becomes available. Provider completion releases only provider execution capacity; it does not close an open workflow, discard its callback, or free a delegated Work context that still owns durable work.

## Configure and observe

Use Settings → Orchestration Capacity for current use, limits, recommendations, and draining state. Capacity changes are protected by [Operator authorization](OPERATOR_AUTHORIZATION.md) and are audited.

The command-line status view is:

```bash
threadcells-resource-status
```

After a change, verify the UI and CLI agree. A limit is an admission control, not a promise of throughput or a workload sandbox.

## Common mistakes

- Increasing every limit because one build is slow.
- Counting an idle worktree as a provider execution.
- Forgetting resident supervisors while sizing long-running missions.
- Reducing a limit and expecting active tasks to be terminated.
- Treating GREEN capacity as proof that provider quotas are available.
- Deleting runtime files to free a slot instead of retiring the owning workflow safely.

See [Housekeeping](HOUSEKEEPING.md) for disk recovery and [Workflows and durable results](WORKFLOWS_AND_RESULTS.md) for safe child retirement.
