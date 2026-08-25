# Workflows and durable results

A workflow represents work that must remain coherent across several model turns, terminals, or delegated agents. It prevents a provider's final message from being mistaken for completion of the larger mission.

![Expanded live ThreadCells session showing active and completed workflow participants](/media/screenshots/threadcells-session-workflow.webp)

## Top-level and delegated work

The **top-level workflow** belongs to the agent or supervisor launched for the owner's mission. A **delegated workflow** belongs to a child assigned one bounded task.

```text
Top-level: "Prepare the release candidate"
  ├── Delegated: "Fix the statistics parser"
  ├── Delegated: "Review operator authorization"
  └── Owner gate: "Approve public publication"
```

Each workflow has its own current logical input and completion state. A worker can complete its delegated workflow while the top-level workflow remains open.

## Assign and handoff

**Assign** starts independent bounded work and lets the parent continue. The child's result is delivered later. This is useful for parallel investigation, implementation, or review.

**Handoff** transfers one bounded task and waits for its validated result before the parent continues. It is useful when the next parent step directly depends on that answer.

Both forms preserve parent/child identity and a durable result. Neither gives a child broader owner authority than the parent explicitly delegated.

A transient pre-launch admission denial, such as exhausted work-context capacity, is recorded as not admitted rather than as an executed assignment. The same logical effect can be retried once capacity is available; after a child launch is admitted or its outcome becomes uncertain, normal duplicate protection remains in force.

## Result lifecycle

```text
Task admitted
   ↓
Child works
   ↓
Structured result recorded
   ↓
Result delivered to parent
   ↓
Parent reads and incorporates it
   ↓
Parent acknowledges incorporation
   ↓
Eligible child resources can retire
```

A result normally includes a concise summary, changed files, checks performed, remaining risks, and blockers. This is operational evidence, not a replacement for examining the diff or test output.

Delivery is at-least-once. If the parent restarts before acknowledging a delivered result, ThreadCells can deliver it again. The parent should use the immutable result identity to avoid incorporating the same work twice.

Inbox delivery is FIFO within a terminal and bound to the exact workflow and logical turn that created it. A pending transport is delivery state, not authority to move a payload or result to another workflow. If its bound workflow is no longer open, ThreadCells terminalizes that stale transport and lets newer open owner work proceed without rebinding the payload, workflow, delivery, receipt, or effect identity. The same reconciliation runs after restart and is idempotent.

## Provider completion versus workflow completion

A provider turn ends whenever the model returns control. The mission may still have eligible work: another test, a pending child, a correction pass, or a deployment step.

ThreadCells therefore keeps a top-level workflow open until one of these explicit outcomes occurs:

- the owner-authorized mission is complete;
- an owner gate is genuinely required;
- the owner cancels it;
- a real unrecoverable failure exhausts its bounded recovery path.

Repeated ordinary provider finals use durable one-turn-at-a-time continuation with capped backoff. ThreadCells keeps admitting the next logical turn while the workflow is open. If a provider settles directly on Ready instead of exposing a repeatable completed frame, ThreadCells durably debounces that state across restart and advances the same open workflow; a later Processing observation cancels a transient Ready candidate. Direct owner input and durable child results reset the no-progress counter. As a paid-loop safeguard, 65 consecutive finals with no durable progress place the workflow in an explicit, owner-visible gate. Provider completion never becomes mission completion, and normal autonomous continuation does not require an owner wake.

## Owner gates

Use an owner gate when the next step needs authority the mission did not grant. Good examples include publishing to a public remote, exposing a new network service, paying for a resource, or choosing between materially different product semantics.

Do not use an owner gate merely because work is slow, a test failed, or one provider turn ended. Continue any independent eligible work first.

## Recovery

On restart, ThreadCells reconstructs workflow ownership from durable state. Delivered-but-unacknowledged results remain available. A waiting handoff can be resumed against the same child instead of launching a duplicate. Once a newer logical turn is admitted for an open workflow, an older pending continuation is durably superseded and cannot later replay as independent work after compaction or interruption.

Direct completion, failure, cancellation, owner-gating, child terminalization, and central protected-workflow cancellation fence pending Inbox transports in the same database transaction. This prevents a terminal transition from leaving ordinary delivery state that can suppress a later owner turn.

A same-build service restart keeps the provider-side control connection compatible. After a promoted build changes privileged orchestration code, an old connection is fenced before it can create an effect. If the active identity is temporarily unavailable during restart, the operation is rejected without an effect and retried after the service returns. For Codex, ThreadCells binds the exact provider conversation to the managed terminal and runtime generation at launch readiness, then persists that identity as reconnect authority. Other open rollout files cannot make that managed terminal ambiguous. A missing, stale, wrong, or unprovable identity fails closed before provider dispatch. The durable resume identity makes a service restart safe even between exit and relaunch. Input transport, reconnect and retirement share one durable per-terminal mutation claim, so text cannot be pasted into the reconnect shell gap and a stale reconnect cannot relaunch after retirement wins. The already durable logical turn is retried rather than replaced.

If a terminal disappears, inspect the workflow and result records before retrying. A new terminal must not silently duplicate a mutation already completed by the old one.

## Concrete example

1. The owner launches a supervisor to add a feature and validate it.
2. The supervisor assigns implementation to a developer and continues inspecting tests.
3. The developer commits the change and records a result.
4. ThreadCells delivers it; the supervisor reads the diff and acknowledges incorporation.
5. The supervisor assigns an independent reviewer.
6. The reviewer finds a blocking browser regression and records evidence.
7. The supervisor continues the same open top-level workflow, requests a correction, and reruns acceptance.
8. Only after the accepted build and authorized deployment does the supervisor explicitly complete the workflow.

At steps 3, 4, and 6, individual model turns have ended. The mission has not.

## Common mistakes

- Treating a final terminal message as top-level completion.
- Acknowledging a result before reading or using it.
- Launching a replacement child without checking for a durable prior result.
- Letting two children mutate the same worktree.
- Using an owner gate as a generic pause button.

See [Projects and managed worktrees](PROJECTS_AND_WORKTREES.md) for write isolation and [Capacity and resource model](RESOURCE_MODEL.md) for admission limits.
