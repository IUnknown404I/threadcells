# Workflows and durable results

A workflow represents work that must remain coherent across several model turns, terminals, or delegated agents. It prevents a provider's final message from being mistaken for completion of the larger mission.

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

## Provider completion versus workflow completion

A provider turn ends whenever the model returns control. The mission may still have eligible work: another test, a pending child, a correction pass, or a deployment step.

ThreadCells therefore keeps a top-level workflow open until one of these explicit outcomes occurs:

- the owner-authorized mission is complete;
- an owner gate is genuinely required;
- the owner cancels it;
- a real unrecoverable failure exhausts its bounded recovery path.

Repeated ordinary provider finals use bounded continuation backoff. After the bounded automatic retries are exhausted, the workflow stays open but idle; a direct owner input or a real child result resumes it. Provider completion never becomes an owner gate or mission-complete signal by itself.

## Owner gates

Use an owner gate when the next step needs authority the mission did not grant. Good examples include publishing to a public remote, exposing a new network service, paying for a resource, or choosing between materially different product semantics.

Do not use an owner gate merely because work is slow, a test failed, or one provider turn ended. Continue any independent eligible work first.

## Recovery

On restart, ThreadCells reconstructs workflow ownership from durable state. Delivered-but-unacknowledged results remain available. A waiting handoff can be resumed against the same child instead of launching a duplicate.

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
