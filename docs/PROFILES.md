# Profiles

A profile is a reusable launch policy for an agent. It answers: which provider and model should run, how much reasoning should it use, what role and instructions should it receive, and what capabilities or authority are allowed?

Most users should start with a built-in profile and inspect its resolved preview. You do not need to author raw JSON for normal use.

## What a profile controls

A resolved profile can include:

- provider configuration, model, and reasoning effort;
- role such as supervisor, developer, reviewer, or specialist;
- instructions and skill references;
- allowed tools and MCP capabilities;
- timeouts and execution behavior;
- writer or owner-level authority constraints;
- whether it is intended to remain resident or complete bounded work.

Model power and orchestration role are separate. A strong model is not automatically a supervisor, and a profile's name does not determine how capacity is charged.

## Built-in profiles

ThreadCells ships immutable profiles for common roles, including everyday and stronger supervisors, developers, reviewers, architecture and strategy work, frontend/UI work, and a narrowly owner-authorized XHigh executor.

Examples:

- `supervisor_terra_medium`: the default ongoing orchestrator for ordinary and medium-risk workflows; it decomposes, delegates, reviews, accepts, and integrates.
- `supervisor_sol_medium`: the orchestration-first supervisor for risky, cross-module, architecture-sensitive, or lifecycle-sensitive workflows.
- `developer_terra_medium`: routine, bounded, low-ambiguity implementation.
- `developer_terra_high`: important product work, difficult bounded defects and refactors, and public semantic quality.
- `developer_sol_medium`: reasoning-heavy cross-subsystem and subtle invariant work.
- `reviewer_sol_high`: independent review for risky or integrated changes.
- `critical_sol_xhigh_owner`: an exceptional owner-executor profile with a separate authorization boundary.

Built-ins are immutable so a familiar ID cannot silently change meaning. To customize one, duplicate it; the copy receives a custom identity.

## Choosing a profile

Use the least specialized profile that can reliably own the task:

| Task | Starting point |
| --- | --- |
| Small bounded code change | developer |
| Independent acceptance review | reviewer |
| Several dependent workstreams | supervisor |
| Architecture or migration design | architecture/strategy specialist |
| Product UI implementation | frontend or UI/UX specialist |
| Critical frontier owner execution | owner-authorized XHigh only |

More reasoning and broader authority cost capacity and increase consequence. They should reflect the task, not become defaults.

A Sol supervisor does not imply a Sol developer. It should still route routine
implementation to Terra developers and reserve `developer_sol_medium` for work
whose correctness depends on subtle cross-system reasoning.

## Retry and escalation

ThreadCells classifies failed implementation attempts before selecting another
agent:

| Failure class | Canonical response |
| --- | --- |
| `OPERATIONAL_FAILURE` | A same-tier retry may be valid. |
| `MECHANICAL_INCOMPLETE` | Allow one bounded same-tier correction. |
| `SEMANTIC_QUALITY_FAILURE` | Escalate the implementation tier; never perform a third same-tier semantic attempt. |
| `BOUNDARY_COMPLEXITY_UNDERESTIMATED` | Select a stronger developer. |
| `CRITICAL_SYSTEMIC_BOUNDARY` | Use owner-authorized `critical_sol_xhigh_owner`. |

The normal escalation path is `developer_terra_medium` →
`developer_terra_high` → `developer_sol_medium`. XHigh is reserved for genuinely
critical systemic authority such as security, exactly-once concurrency,
destructive Housekeeping, migrations, or dangerous recovery. Passing tests are
necessary evidence, but do not by themselves prove semantic quality.

## Resolved preview

Settings → Profiles shows both the saved artifact and its **resolved preview**. Use the preview before launch to verify the actual provider, model, reasoning, role, tools, authority, timeouts, and instructions after defaults and references are applied.

New launches atomically capture that resolved revision. Editing the custom profile later creates another immutable revision and does not rewrite the historical meaning of an existing session.

Old sessions created before revision snapshots may show `legacy/unavailable snapshot`. ThreadCells does not fabricate past configuration.

## Create a custom profile

The safest path is:

1. Open Settings → Profiles.
2. Choose the closest built-in.
3. Duplicate it.
4. Give the copy a clear role-based name.
5. Change the smallest necessary fields.
6. Inspect the resolved preview.
7. Use it for a bounded test launch before broader work.

Custom edits create revisions. A profile referenced by history is disabled rather than destructively erased.

## Specialized and owner authority

Untrusted imports cannot create owner-executor, XHigh, unrestricted, or `danger-full-access` authority. An authenticated operator may create a privileged custom revision only through the protected control plane, and the server still requires the applicable one-use owner grant at launch.

The builtin `critical_sol_xhigh_owner` profile can be selected in both Web launch flows: creating a session or adding an agent to an existing session. Each shows the exceptional-authority block and requires explicit confirmation plus the short-lived operator unlock before minting and consuming one normal launch capability. Add Agent scopes that capability to the existing session and canonical inherited/project working directory. The local CLI offers the same authority class through `--owner-xhigh` and interactive confirmation. None of these paths creates a reusable API bypass or authorizes other profiles, child terminals, or unrelated Settings changes.

## Profiles and capacity

A top-level supervisor or owner session consumes resident-supervisor capacity. A delegated child consumes a work-context slot. Provider execution and heavy execution are charged separately based on activity, not merely because a profile contains `supervisor` or `reviewer` in its name.

See [Capacity and resource model](RESOURCE_MODEL.md) before raising concurrency for powerful profiles.

## Advanced import and export

The CLI exposes the current schema and examples:

```bash
threadcells profiles schema
threadcells profiles example
threadcells profiles export
threadcells profiles validate /path/to/profile.json
threadcells profiles import /path/to/profile.json
```

Validate before import. Imports use the same service validation as the UI and cannot introduce executable MCP commands. They may reference installed provider configurations and registered capability identifiers.

Do not hand-edit database rows or copy private instructions, filesystem paths, credentials, or internal owner state into a public profile artifact.

## Common mistakes

- Choosing a profile solely from its model name.
- Giving an everyday worker owner-level authority.
- Editing a custom profile without checking the resolved preview.
- Expecting an edit to mutate already-running sessions.
- Importing raw secret values instead of approved references.
- Treating a profile as provider installation; the selected CLI must still be ready.

Next, see [Workflows and durable results](WORKFLOWS_AND_RESULTS.md) for how supervisor and worker profiles cooperate.
