---
name: cao-worker-protocols
description: Worker-side callback and completion rules for assigned and handed-off tasks in CAO
---

# CAO Worker Protocols

Use this skill when acting as a worker agent inside ThreadCells.

This skill explains how workers should interpret assigned versus handed-off work, when to call `send_message`, and how to report results back cleanly.

## Understand the Dispatch Mode

Workers receive tasks through one of two orchestration modes:

- `handoff`: blocking work where the orchestrator captures your final output automatically
- `assign`: non-blocking work where you must actively return results to the requesting terminal

Depending on provider and CAO behavior, a handoff may be made explicit in the task text. For example, Codex workers currently receive a `[CAO Handoff]` prefix for blocking handoffs. Other providers may rely on the task wording and orchestration context instead.

## Rules for Handoff Tasks

When the task is a blocking handoff, complete the work and present the result in your normal response. The orchestrator captures that response automatically.

Do not call `send_message` for ordinary handoff completion unless the task explicitly asks for additional side-channel communication.

## Rules for Assigned Tasks

When the task came through `assign`, the task message should include a callback terminal ID. After you finish the work:

1. Extract the callback terminal ID from the task message.
2. Format the result clearly and concisely.
3. Call `send_message(receiver_id=..., message=...)` with the completed result.

Do not stop after writing a normal response if the assignment explicitly requires a callback. The requesting terminal depends on `send_message` to receive the result.

Assigned tasks may include callback instructions directly in the main message or in an appended suffix such as `[Assigned by terminal ...]`. Treat that callback terminal ID as authoritative.

Your own `CAO_TERMINAL_ID` identifies your terminal, not the callback target. Send results to the receiver specified in the task.

## Message Formatting

Return results that are easy for the supervisor to merge into a larger workflow:

- Identify what task or dataset the result belongs to
- Include the requested output or deliverable
- Keep the message specific enough to act on without re-reading the whole task

If the task asks for progress updates, use `send_message` for those updates too. Otherwise prefer one final callback with the completed deliverable.

## Filesystem and Reporting Discipline

If the task asks you to create files, write them before reporting completion. When sending results back to a supervisor, include absolute file paths so the supervisor can continue the workflow without ambiguity.

For successful work, report only the result or verdict, changed files or commits,
checks and outcomes, blockers or findings, and the exact next dependency when
applicable. Do not repeat the task, authority contents, or raw terminal
transcripts when a compact report is enough. This is the report portion of the
canonical task-efficiency policy:
the repository-local task-prompt efficiency policy.

## Reliability Guidelines

- Parse the callback terminal ID before you start expensive work.
- If `send_message` is available and the task requires a callback, call it directly rather than ending with prose alone.
- Keep callback messages structured so the supervisor can merge them into a larger workflow.
- For handoff tasks, return the completed output directly and let the orchestrator handle delivery.

## Telegram Notification Ownership

If your assigned or handed-off task contains `NO_TG_NOTIFY` as its own first or
last non-empty line, do not emit `TG_NOTIFY:`. Report the completed result,
blocker report, or callback only to the requesting supervisor. Delegated child
work never sends Telegram notifications, including diagnosis, correction, retry,
or nested delegation.

If you delegate further work through `assign` or `handoff`, that child message
must itself include the exact `NO_TG_NOTIFY` directive as its first or last
non-empty line. Only the manually spawned top-level conductor owns a possible
single meaningful notifier marker for final contour success or a real owner
gate/exhausted recovery budget.

## Workflow terminal state

Provider final text is not permission to end a top-level workflow. A conductor
calls `complete_workflow` only after the approved workflow is truly complete;
for a real owner decision it calls `owner_gate_workflow(reason)` instead. Do
not emit a final `TG_NOTIFY:` before that explicit terminal state. Handoff and
assigned workers still return their results through their normal protocols.

## Blocked and Failed Handoff Contract

For a blocked or failed assigned task or handoff, return a compact structured
report instead of an unclassified escalation. Use these fields when applicable:

```text
status: blocked | failed
classification: RECOVERABLE_EXECUTION | DIAGNOSTIC_REQUIRED |
  ARCHITECTURAL_WITHIN_AUTHORITY | OWNER_GATE
evidence: <concise facts, paths, error summary, or reproducible observation>
impact: <what cannot proceed and what remains safe>
checks_or_commands: <focused checks or commands run>
attempted_fixes: <what was tried and outcome>
remaining_options: <bounded technical options or owner-gated option>
recommendation: <best next action>
owner_decision_needed: yes | no
```

Do not include secrets or raw terminal dumps. Preserve enough evidence for the
supervisor to classify and recover the task. A normal completed task keeps the
usual concise completion report; use this contract only for blocked or failed
work.

## Durable result artifacts

Your first assigned `send_message` callback is recorded as one immutable
delegation result. Send the final structured report once; a transport retry may
repeat it safely, but do not send a changed replacement after completion. For a
handoff, return the final report normally: CAO records it only after its stable
final-output validation succeeds. The supervisor reads the resulting `result_id`
from its callback envelope; workers do not invent result IDs or rely on Inbox
text as the durable source of truth.

Reviewer, architect, and strategist reports should use this v1 JSON object when
their provider can emit JSON directly: `summary`, `body_markdown`,
`changed_files`, `checks` (objects with `command` and `outcome`), `risks`, and
`blockers`. Omit unknown fields instead of inventing them. Ordinary prose remains
compatible and is retained as `legacy_text`; CAO never attempts to infer a
fictional structured report from it.
