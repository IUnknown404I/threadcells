## Dual-lane concurrency and autonomous resource hygiene

Effective operational limits are independently configurable in
`/etc/agent-control/cao-operations.json`:

```text
MAX_RESIDENT_SUPERVISORS = 5
MAX_PROVIDER_EXECUTIONS = 3
MAX_WORK_CONTEXTS = 2
MAX_HEAVY_EXECUTION_SLOTS = 1
```

Resident supervisors count live supervisor contexts, including idle, waiting,
processing, and owner-gated contexts. Provider executions count only model
turns executing now; a processing supervisor consumes both counters, while an
idle or waiting supervisor consumes residency only. Work contexts count live
delegated workers/reviewers and exclude supervisors. Heavy execution remains a
separate kernel-backed slot.

The owner may launch a project supervisor in the canonical project worktree.
Default delegated work is automatically isolated by CAO: one warm execution
owner reuses one managed task branch/worktree, while an independent reviewer
gets a temporary detached worktree at the exact reviewed commit. Explicit
isolated working-directory requests remain authoritative. CAO uses `git
worktree`, never clones or automatic installs/builds, and never discards a
dirty or unverifiable worktree. Task branches are retained for deterministic
integration; CAO does not auto-merge merely to clean up.

A shared worktree has at most one writer. Writer ownership is a durable database
uniqueness lease acquired before provider execution and released only after
positive terminal death and safe managed-worktree cleanup. All current provider
lanes remain writer-capable until provider-enforced read-only isolation exists.

Before launching another managed child, use `cao-resource-status`; launch only
when work capacity remains and resource health is not RED. Provider-turn input
is admitted separately and waits in the existing durable workflow/Inbox
continuation path when all execution slots are occupied. Provider Ready,
waiting-child suspension, failure, cancellation, and exit release the exact
execution lease and wake those existing continuations. RED permits
bounded deterministic housekeeping recovery followed by one recheck; it does
not permit a risky new launch.

## Safe parallelism

Default execution mode is one delegated worker for one coherent implementation.
Exploit safe parallelism, not maximum parallelism. Parallel delegation is an
optimization that requires affirmative evidence of independence and meaningful
expected wall-clock benefit. Before serializing pending tasks, identify
dependency edges, write-scope overlap, heavy-operation requirements, and
review-order requirements. Free Provider or Work capacity alone is never a
reason to launch another worker.

Parallelize only when there are two genuinely independent bounded tasks,
non-overlapping authority is proven, dependency/review/heavy constraints permit
it, and parallel execution is expected to materially reduce completion time.
If coordination, duplicated context reconstruction, merge/reconciliation, or
review overhead is comparable to or greater than the expected saving, keep the
work sequential. Do not artificially split one coherent implementation merely
to occupy multiple Work slots. Otherwise, serialize.

Up to two delegated Work contexts may run concurrently when safe. Multiple
write-enabled children are permitted only when their managed worktrees and
write scopes are demonstrably independent; shared files or overlapping
authority remain serialized. A read-only reviewer or analysis lane may run
alongside an independent writer only when it reviews stable inputs rather than
a moving implementation target. Final contour review remains sequential after
implementation is complete.

Heavy execution remains globally limited to one: never launch two heavy jobs concurrently.
A resident supervisor waiting for children or results consumes residency, not
Provider execution capacity. Optimize wall-clock time by using free capacity
for genuinely independent work while preserving writer authority, dependency
order, review correctness, and the single-heavy-execution invariant.

Decision rule: default → one coherent worker; parallelize only when independent
+ non-overlapping + capacity available + material wall-clock benefit;
dependency / shared write scope / moving review target / heavy conflict /
insufficient expected benefit → serialize.

Model/reasoning is not locally heavy. Browser/E2E, production builds, broad test
suites, Docker builds, heavy database acceptance, large scans, and comparable
CPU/RAM/I/O work must use `cao-heavy-run -- <command>`. A busy heavy slot is not
RED resource health. Waiting remains in the kernel/process layer and creates no
LLM turn, inbox message, notification, queue, scheduler, or daemon.

After result/evidence capture, when no callback/recovery/review is pending and a
warm context is no longer needed, retire completed children using existing CAO
lifecycle controls. Preserve active supervisors, running/awaited children, and
contexts required for correction or recovery.

`cao-housekeeping` is the deterministic operational fallback. It preserves open
and unknown resources, uses exact minute TTLs, requires explicit ownership/TTL
markers for temporary/browser/Docker cleanup, retains all referenced Playwright
revisions, inventories and warns about stale unreferenced browser revisions
without deleting them, never generically prunes Docker, does not delete backups,
and treats unproven deployment history as rollback material.
UNKNOWN legacy terminal authority remains fenced while tmux/process death is
uncertain and is retired only after its exact managed target is positively
absent. This is reported separately as `legacy_authority_reconciled`; UNKNOWN is
never rewritten as read-only and no worktree authority is fabricated.
