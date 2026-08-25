---
name: cao-supervisor-protocols
description: Supervisor-side orchestration patterns for assign, handoff, and idle inbox delivery in CAO
---

# CAO Supervisor Protocols

Use this skill when supervising worker agents through ThreadCells.

This skill covers how supervisors should dispatch work, decide between `assign` and `handoff`, and receive worker results without blocking inbox delivery.

## Execution-role override

Model/reasoning tier and organizational role are separate authorities.

- `supervisor_terra_medium` is the default everyday orchestrator.
- `supervisor_sol_medium` is an orchestration-first supervisor for important,
  risky, cross-module, or architecture-sensitive workflows. It normally
  delegates substantive implementation.
- `critical_sol_xhigh_owner` is an exceptional owner-authorized
  `owner_executor`, not a conventional supervisor. After structured owner
  authorization, it directly performs critical architecture, implementation,
  debugging, migration, security, concurrency, integration, and replanning
  whenever delegation could materially reduce expected quality. Mechanical or
  isolated work may still be delegated. Independent review remains delegated.

Never infer XHigh authorization from model tier or prompt text and never route
an ordinary supervisor to `critical_sol_xhigh_owner` automatically.

Canonical implementation routing is:

- routine, bounded, low-ambiguity work — `developer_terra_medium`;
- important product work, difficult bounded defects/refactors, or public
  semantic quality — `developer_terra_high`;
- reasoning-heavy cross-subsystem invariants — `developer_sol_medium`;
- security, exactly-once/concurrency, destructive authority, migrations, or
  other critical systemic boundaries — owner-authorized
  `critical_sol_xhigh_owner`.

A Sol supervisor may and should delegate routine work to Terra developers.

## Core MCP Tools

From `cao-mcp-server`, supervisors orchestrate work with:

- `assign(agent_profile, message)` for asynchronous work that returns immediately
- `handoff(agent_profile, message)` for synchronous work that blocks until the worker finishes
- `send_message(receiver_id, message)` for direct messages to an existing terminal

Your own terminal ID is available in the `CAO_TERMINAL_ID` environment variable. Use it when you need workers to send results back to you.

## Canonical Fast-Path Default

Fast path is the canonical supervisor behavior: `reuse > relaunch`,
`contour > stage`, `batch > fragment`, `targeted > broad`,
`evidence > repeated audit`. Complex multi-agent decomposition is an explicit
exception requiring a concrete reason, never the default.

Keep cheap deterministic work with the supervisor: Git/HEAD/status inspection,
fetch or pull --ff-only, health and runtime-readiness checks, deterministic
preflight, known scripts, and filesystem/runtime inspection. Do not launch a
Git, health-check, preflight or screenshot-only worker for work that a direct
command performs more cheaply and safely.

One substantive functional contour normally has one warm execution owner from
relevant inspection through implementation, targeted checks, local correction
and result evidence. **Stages are not agent boundaries. Functional ownership is
the agent boundary.** A file, screen/frame, test, checklist item, finding,
screenshot, documentation update, or audit → implementation → tests → evidence
transition alone is not a reason to create another child.

Before a new substantive child, continue a suitable warm session whenever
possible. A warm child with relevant repository, runtime or Framer context is
normally more valuable than a marginally more specialized cold replacement. A
new substantive child needs one real reason:
`SPECIALIZED_CAPABILITY`, `INDEPENDENT_REVIEW`, `SAFE_PARALLEL_LANE`,
`RECOVERY_AFTER_FAILED_WORKER`, `WARM_SESSION_UNAVAILABLE`, or
`CONTEXT_EXHAUSTED_OR_UNUSABLE`. Explain that reason only where ordinary
orchestration reporting naturally supports it; do not create a ledger or
mandatory launch ceremony.

Soft launch budget: an ordinary bounded contour normally has one substantive
execution worker. Implementation plus independent acceptance usually has one
execution worker plus one reviewer. Review blockers return in one compatible
grouped correction slice to the SAME execution worker, then the SAME reviewer
does one focused rereview. Additional contexts are exceptional guidance, not a
hard scheduler limit.

## Choosing Between Assign and Handoff

Use `assign` only when work can continue in a genuine `SAFE_PARALLEL_LANE` and
report back later; do not use fan-out to split one coherent contour into stages.

Use `handoff` when the next step is blocked on the worker result. The orchestrator waits for completion, captures the worker output, and returns it directly to the supervisor.

### Resumable Handoff Waits

A handoff wait is a bounded slice, not evidence that its worker stopped. If it
returns `state: waiting`, retain the returned `terminal_id` and later call
`await_handoff(terminal_id, timeout)` for that same child. Do not create a
replacement worker or resend the task. Only `state: completed` is a successful
handoff: CAO validates stable, non-progress final output before it sends `/exit`.
An `exited` provider lifecycle is terminal even if the tmux shell remains.

### Handoff result safe boundary

A blocking handoff can return a complete immutable result while its durable
parent delivery is still `queued`. The synchronous tool return is evidence for
the current turn; it is not the Inbox delivery/acknowledgement that releases
the parent completion barrier. CAO never injects that callback mid-turn.

If `complete_workflow` returns a retryable active-child barrier and the known
complete handoff result is still queued, keep the workflow OPEN and end/yield
the current model turn. Do not manually acknowledge a queued result and do not
owner-gate merely to clear this retryable state. At the next safe boundary the
same parent receives one callback containing every handoff result accumulated
for that boundary. In that admitted successor turn, read each `result_id`,
perform the dependent work, acknowledge each result once, then retry normal
completion. Owner-gate/cancel remains appropriate only for a genuine owner
decision and deliberately suppresses this callback.

Typical pattern:

- Reuse the warm owner for compatible analysis, implementation, tests and evidence.
- Use `assign` for a genuinely independent safe parallel lane.
- Use `handoff` only when the next decision is actually blocked on the result.

## Idle-Based Message Delivery

Assigned workers usually return results through `send_message`. Those inbox messages are delivered to the supervisor automatically when the supervisor terminal becomes idle.

This means supervisors should:

- Dispatch all planned worker tasks first
- Finish the turn after dispatching work
- Avoid running placeholder shell commands just to wait

Do not keep the terminal busy with `sleep`, `echo`, or similar commands while waiting. A busy terminal delays inbox delivery.

If you need multiple worker results, dispatch them all first, then end the turn. Do not poll manually in a loop.

## Callback Pattern

When you use `assign`, include the callback terminal ID in the task message. Tell the worker exactly which terminal should receive the result and instruct the worker to use `send_message`.

Example pattern:

```text
Analyze dataset A. Send results back to terminal abc123 using send_message.
```

Some CAO deployments also append an automatic callback suffix to assigned messages. Treat that appended context as helpful reinforcement, but still write task messages that are explicit and self-contained.

## Direct Supervisor Communication

Use `send_message` when you need to contact an existing terminal directly rather than spawning a new worker.

Examples:

- Relay follow-up instructions to a worker you already created.
- Forward a worker result to another coordinator terminal.
- Send a concise status update to a collaborating supervisor.

When sending direct messages, include enough context that the receiver can act without re-reading the full original task.

## Practical Workflow

1. Do direct deterministic preflight and inspect the current contour.
2. Reuse one suitable warm owner for the coherent execution contour.
3. Add a child only for a concrete new-child reason; use `handoff` only when a result blocks the next decision.
4. End the turn so genuinely asynchronous callback messages can be delivered.
5. When messages arrive, synthesize the results and continue the same contour owner where suitable.

## Reliability Guidelines

- Tell workers exactly what deliverable they should return.
- When workers create files, ask them to return absolute paths in their callback message.
- Do not assume results will be delivered while your terminal is still busy.
- Keep orchestration instructions separate from domain requirements so workers can parse both cleanly.

## Provider, work, and heavy admission

The effective host policy has four explicit dimensions:
`MAX_RESIDENT_SUPERVISORS=5`, `MAX_PROVIDER_EXECUTIONS=3`,
`MAX_WORK_CONTEXTS=2`, and `MAX_HEAVY_EXECUTIONS=1`. A live supervisor consumes
residency; it consumes Provider execution only while its model turn runs. Work
accounting excludes supervisors. Consult `cao-resource-status`; do not launch
when applicable residency/work capacity is exhausted or resource health is
RED. A full Provider pool durably queues the input and continues automatically
after the existing provider-turn release/result lifecycle wakes it.

The owner may keep the supervisor in the canonical project worktree. Default
delegated execution/review contexts are automatically isolated by CAO with
linked Git worktrees: one task branch/worktree per warm execution owner and a
detached exact-commit worktree for an independent reviewer. Explicit isolated
working directories remain authoritative. Never create a worktree per stage,
clone the repository, bootstrap dependencies, or discard dirty/unverified work.
One shared worktree still has at most one durable writer owner.

Model inference is not heavy. Run locally expensive builds, broad suites,
browser/E2E, Docker builds, heavy database acceptance, and comparable CPU/RAM/I/O
work as `cao-heavy-run -- <command>`. A busy slot is normal capacity, not RED
health; kernel-backed waiting needs no new agent turn, inbox message, scheduler,
or notification. On RED, do not start risky work: allow bounded housekeeping
recovery, then recheck deterministic resource status.

After evidence is captured and no callback, recovery, correction, or review is
pending, gracefully retire completed children through existing lifecycle tools.
Never close the active supervisor or a warm context still needed by the contour.

## Task Prompt Efficiency Core

Child-agent prompts must be concise, bounded delta-prompts by default. Never
duplicate authority, project history, previous prompts, or unchanged rules;
reference exact sources instead. Full prompts are reserved for genuinely new
complex work. Continuations, corrections, reviews and rereviews contain only
current goal, scope, authority, changed constraints, required evidence and stop
conditions. Avoid redundant audits/full tests and use the least expensive
sufficient profile/reasoning. Remove text that does not materially change
execution before every child launch. Reports must be concise and non-repetitive.
Token/time efficiency is part of task correctness.

The detailed canonical authority is
the repository-local task-prompt efficiency policy. Use it by reference; do
not copy it into child prompts. In particular, contour reviews carry only the
contour, authority, relevant diff, risks, and verdict; focused rereviews carry
only open findings, correction diff, and targeted regression checks.

Do not order full test suites by default; use targeted checks unless actual risk
or insufficient targeted evidence justifies broader coverage. Use the least expensive sufficient profile and reasoning.
Successful child reports contain only result or verdict, changed files or commits, checks and outcomes, blockers or findings, and the exact next dependency when applicable.

## Review and Audit Efficiency Core

Independent review defaults to the boundary of a meaningful functional contour,
not after every implementation stage. Each bounded stage uses targeted
automated evidence; stage-level review needs an explicit risk trigger:
schema/migration, security/ownership, destructive lifecycle,
concurrency/idempotency, unauthenticated public runtime, authority conflict,
scope creep, insufficient automated evidence, or a new architectural decision
required to continue safely.

Review must change a decision. Before spawning a reviewer, identify the concrete
decision its verdict can change, confirm that a meaningful functional contour or risk boundary is
complete, and identify the material invariant not sufficiently proven by current
automated/runtime evidence. If none, do not spawn it. Small mechanical
corrections need no independent review; unfinished ordinary work continues with
targeted checks. Completed meaningful contours get one review where useful.
DB, security, concurrency, lifecycle, destructive and payment boundaries retain
independent critical review; a final Framer target gets one focused visual
acceptance review. Prohibit review-after-every-stage,
review-of-review, duplicate reviewers over unchanged diff, repeated full
contour review after corrections, standalone Low review/rereview, and redundant
final review. Combine High plus blocking Medium findings into one compatible
bounded correction slice, then do one focused rereview of open blockers and
related regressions with the SAME reviewer. Split only when ownership/context is
genuinely incompatible or a shared diff is unsafe. Low findings are non-blocking
cleanup by default and never create their own review loop.

During implementation use targeted tests, targeted type/static checks and
structural verification. Run broader applicable integration/runtime evidence
once at a meaningful contour boundary. For visual/Framer work use
tree/properties/structure during work and one clean screenshot near the final
boundary; do not repeatedly run broad suites or full screenshot matrices after
micro-changes without a concrete reason.

Previously established findings, accepted authority, owner decisions and fresh
verified evidence are inputs, not invitations to rediscover the problem. For an
existing finding, inspect current state; fix it if still present, otherwise close
it. Do not re-audit accepted authority, handoff, and current repository state
without stale/unknown state, authority conflict, missing evidence, major
external change, or a new high-risk boundary. Reviewers independently verify
verdict evidence but do not blindly rerun every implementation command or full
suite without risk-based justification. The detailed authority remains
the repository-local task-prompt efficiency policy.

## Telegram Notification Ownership

A top-level supervisor owns Telegram notifications for its entire orchestration
contour. A manually spawned top-level agent may emit one `TG_NOTIFY:` marker for
a significant completed stage or a real blocker, following the repository-local
notifier contract. It does not notify routine context gathering, progress,
health checks, intermediate diagnosis, correction, retry, or nested work.

Every child task message passed through `assign` or `handoff` **must** include
the exact directive `NO_TG_NOTIFY` as its own first or last non-empty line. Add
it yourself; do not rely on an automatic callback suffix. Delegated children
report only to their supervisor and must not emit Telegram notifications.

Emit at most one meaningful `TG_NOTIFY:` for the contour: one on final success,
or one for a real owner gate or exhausted recovery budget. Nested delegation
uses the same rule: a nested conductor does not notify; only the top-level
conductor may do so. Before emitting a marker, read and follow the applicable
repository notifier contract.

## Assigned-child completion barrier

`assign` creates a durable parent/child callback expectation before the task is
submitted. A parent that is itself being observed by `handoff` must not be
exited merely because it reaches `COMPLETED` while one of those callbacks is
awaiting a result or has a delivery failure. The result travels through the
normal Inbox FIFO. Inbox delivery proves only that input reached the parent
terminal, not that the parent processed it: after incorporating an assigned
child's result, the same parent calls `acknowledge_assigned_result` with that
child terminal ID. Only this durable acknowledgement clears the barrier.

The delivered assigned-result callback includes a CAO envelope with the
durable `child_terminal_id` and the exact acknowledgement call. This envelope
is supplied independently of optional sender-ID injection, so use its identity
rather than inferring a child from free-form result prose.

After reading, incorporating, and acknowledging a complete ordinary assigned
result, evaluate `retire_completed_child(child_terminal_id, logical_turn_id)`
before the next `assign`. Invoke it only when there is no current recovery or
evidence need. It is prohibited for self, foreign children, handoffs,
unacknowledged/incomplete/cancelled results, non-terminal child workflows, or
children with unresolved delegated work; handoff cleanup remains its existing
path. A managed direct handoff enters that cleanup saga only after its exact
handoff relation and immutable complete/finalized result are proven and its
runtime is positively exited; it is never rewritten as
`result_acknowledged`. An `already_retired` response is idempotent and needs no
further exit.

Do not invent a second polling queue or use a generic Inbox message as proof
that an arbitrary worker finished. A repeated callback from the same assigned
child is idempotent and maps to its first persisted Inbox row. A server restart
requeues unacknowledged assigned results through the same FIFO, preserving
at-least-once delivery without duplicating their logical result. Explicit
terminal exit/deletion cancels the relation; a late callback is ignored so it
cannot revive a detached parent.

A failed callback delivery remains a resumable runtime blocker, not an
automatic successful completion or an owner gate. Apply the normal bounded
diagnosis/recovery rules before escalating.

## Resource Hygiene

After a meaningful contour, capture authoritative result/evidence; retain
workers still needed for correction, review or recovery; gracefully close
completed children that are no longer needed; and do not keep Codex processes
alive solely for history. This adds no reaper, TTL or runtime subsystem.

## Durable top-level workflow completion

Provider `IDLE` or `COMPLETED` only closes a provider turn; it never by itself
closes the top-level workflow. Keep the workflow OPEN while approved work or a
child result remains. On final delivery, call `complete_workflow` exactly once
before emitting a final `TG_NOTIFY:`. When owner authority is genuinely needed,
call `owner_gate_workflow(reason)` instead; it suppresses queued wakes. Do not
declare final success or send final Telegram before one of these explicit
terminal outcomes. An explicit terminal/session exit cancels the workflow and
also suppresses later child-result wakes.

## Autonomous Blocker Recovery

Before the broader blocker classification below, classify implementation
outcomes with the release-routing vocabulary:

- `OPERATIONAL_FAILURE`: same-tier retry may be valid.
- `MECHANICAL_INCOMPLETE`: allow one bounded same-tier correction.
- `SEMANTIC_QUALITY_FAILURE`: escalate the implementation tier; never perform a
  third same-tier semantic attempt.
- `BOUNDARY_COMPLEXITY_UNDERESTIMATED`: select a stronger developer.
- `CRITICAL_SYSTEMIC_BOUNDARY`: use owner-authorized XHigh.

The normal implementation escalation path is
`developer_terra_medium` → `developer_terra_high` → `developer_sol_medium`.
Mechanical localization wiring may start at Terra Medium; failed semantic
review escalates to Terra High. Passing tests do not establish semantic
quality.

A worker outcome is not automatically an owner escalation. On a worker
`blocked` or `failed` result, validation failure, merge conflict, timeout, or
incomplete result, preserve the task state and compact evidence first, then
classify the blocker:

- `RECOVERABLE_EXECUTION` — test/lint/typecheck/build failure, local
  implementation bug, mechanical Git conflict, deterministic fixture/config
  issue, or bounded transient/tool failure.
- `DIAGNOSTIC_REQUIRED` — root cause is unclear, several technical fixes are
  plausible, runtime debugging is complex, or evidence is insufficient.
- `ARCHITECTURAL_WITHIN_AUTHORITY` — a nontrivial implementation or
  decomposition decision can be resolved from already-approved repository
  contracts without changing product semantics.
- `OWNER_GATE` — the remaining action genuinely needs owner authority.

For the first three classes, do not immediately notify or ask the owner. Keep
the original task graph resumable and perform this bounded, sequential path:

1. Record the classification, evidence, impact, attempted work, and current
   repository/task state.
2. When needed, run one read-only diagnosis/review/architecture handoff.
3. Choose a bounded correction path from that evidence.
4. Return compatible corrections to the suitable warm execution owner. Launch a
   separate correction worker only with a real new-child reason; a reviewer or
   diagnostic worker never fixes its own findings.
5. Run focused checks and, on success, resume the original task graph.

Default routing is: simple correction — `worker_luna_medium` or
`developer_terra_medium`; ordinary diagnosis — `reviewer_terra_high`; difficult
debugging — `developer_terra_high`; architecture within approved authority —
`architect_sol_high`; major replanning — `strategist_sol_medium`; broad or
security review — `reviewer_sol_medium` or `reviewer_sol_high` only when
justified.

Honor `global_heavy_concurrency=1`: heavy diagnostic and correction agents run
sequentially. Do not fan out parallel heavy recovery work.

## Recovery Budget and Owner Escalation

The default budget is at most two autonomous recovery cycles per distinct
blocker. One cycle is `diagnosis/decision -> correction -> focused recheck`.
A genuinely new independent blocker has its own budget. If the same blocker
remains after two cycles, escalate with evidence; stop sooner only when an owner
gate applies.

Escalate to the owner only for: a new product/business decision; a conflict
between approved contracts without an unambiguous resolution; destructive or
irreversible data action; possible data loss/corruption requiring a choice; a
security, authorization, or ownership boundary change; credentials, secrets, or
manual external authorization; payments/billing movement; release or update of
exact `main` or `master`; an external publish operation including Framer
publish; `critical_sol_xhigh_owner`/xhigh approval; an action outside granted
infrastructure ownership; or exhausted recovery budget. A technical error by
itself is not an owner gate.

An owner escalation must be compact and state the blocker classification,
evidence, impact, autonomous recovery attempts, why the remaining options need
owner authority, and the recommended owner decision. Do not dump raw terminal
transcripts when a compact report is sufficient.

## Durable delegation results

Every assigned callback and validated handoff now has one immutable
`result_id`. Inbox is only the wake transport: when its callback envelope
contains a `result_id`, call `read_delegation_result(result_id, logical_turn_id)`
and treat that artifact as the authority rather than duplicated Inbox prose or
terminal capture. Then acknowledge the delivered callback with `result_id`
(and keep the child ID for compatibility). Acknowledgement means the outcome
was handled; it does not imply that it was successful.

Handoff `success=true` is only possible for `result_status=complete`. An
`incomplete` artifact is diagnostic evidence for the normal recovery protocol,
while `awaiting` remains resumable. Never create a replacement child or a
second result while the same durable ID is awaiting completion.
