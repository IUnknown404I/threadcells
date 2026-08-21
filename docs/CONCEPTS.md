# Core concepts

ThreadCells adds structure around native coding-agent terminals. This page introduces one idea at a time and then shows how the pieces fit together.

## Agent

An **agent** is a provider CLI running with a prompt, role, profile, and project context. It can inspect files, use tools, write code when authorized, and return a result.

An agent is not just the model name. Two agents can use the same model but have different roles, permissions, reasoning settings, and worktrees.

## Terminal

A **terminal** is the real tmux-backed process environment in which an agent runs. It preserves native provider output and allows the operator to reconnect after closing the browser.

The terminal can exit while its durable result remains. Conversely, a terminal that still exists is not proof that useful work is still progressing.

## Session

A **session** is ThreadCells's durable record of an agent run: identity, lifecycle, terminal, provider, profile, project, usage, and result relationships. Sessions let Statistics and workflows reason about runs that are active, completed, or retained.

## Project

A **project** identifies the Git repository in which work belongs. It gives ThreadCells a stable scope for sessions, worktrees, and results; it is not a replacement for Git remotes or repository permissions.

## Managed worktree

A **managed worktree** is a Git worktree created for a bounded agent context. It lets parallel workers operate on different branches without editing the same checkout.

Worktrees reduce collisions; they are not security sandboxes. An agent may still reach anything its operating-system account can reach.

## Writer authority

**Writer authority** answers who may mutate a particular work context. ThreadCells keeps that ownership explicit so two independently active agents are not accidentally treated as safe concurrent writers to the same worktree.

A reviewer often needs read access but not writer authority. A developer performing an implementation does.

## Provider

A **provider** connects ThreadCells to a native coding-agent CLI such as Codex or Claude Code. Three states matter:

1. ThreadCells contains a provider adapter.
2. The corresponding CLI is installed for the runtime user.
3. That CLI is healthy and authenticated enough to launch.

An adapter being listed in Settings does not imply the external CLI is installed. See [Providers](PROVIDERS.md).

## Profile

A **profile** is a reusable launch policy. It selects a provider/model and reasoning level, supplies instructions and capabilities, defines a role, and can constrain how an agent participates in orchestration.

Built-in profiles provide safe known roles. Custom profiles let operators adapt those roles without changing application code.

## Supervisor and worker

A **supervisor** owns a larger mission. It can divide that mission into bounded tasks, send them to workers, collect their durable results, request review, and decide when the mission is truly complete.

A **worker** or **delegated agent** owns one of those bounded tasks. A worker should report its evidence to its parent; it does not silently decide the top-level outcome.

```text
Owner
  ↓
Supervisor
  ├── Developer ── implementation result ──┐
  └── Reviewer  ── acceptance result ──────┤
                                           ↓
                              Supervisor incorporates results
                                           ↓
                                  Top-level completion
```

A **resident supervisor** may remain available while workers take turns. Its residency consumes a supervisor slot even when the model is not currently producing output.

## Workflow

A **workflow** is the durable coordination record for a mission or delegated task. It tracks who owns the work, which logical input is current, whether results have been delivered and incorporated, and whether completion or an owner decision is required.

Provider/model turn completion is not workflow completion. A supervisor may finish one turn, receive a worker result later, and continue the same open mission.

## Durable result

A **durable result** is the structured completion evidence produced by delegated work. It can include a summary, changed files, checks, risks, and blockers. ThreadCells stores and delivers it even if the worker terminal is later retired.

Delivery is not the same as incorporation. The supervisor acknowledges a result only after it has actually used or evaluated it.

## Owner gate

An **owner gate** pauses autonomous continuation because the next decision requires the human owner—for example, publication, a new external trust boundary, an irreversible destructive action, or a product decision that was not previously authorized.

An ordinary model turn ending or a difficult implementation step is not an owner gate.

## Three kinds of capacity

ThreadCells separates three capacity limits because they constrain different parts of the machine.

### Provider execution

The model is actively producing a turn. Provider quotas, process limits, and network activity constrain this category.

### Work context

A delegated coding context currently owns work. It may hold a worktree and writer authority even while waiting for a command or callback.

### Heavy execution

A build, Chromium run, large test suite, or similarly expensive host task occupies a heavy slot. CPU, memory, and I/O pressure constrain it.

One agent can hold a work context while not using a provider or heavy slot. Raising every limit together can therefore overload the host without making the workflow faster. See [Capacity and resource model](RESOURCE_MODEL.md).

## A complete example

An owner launches a supervisor for a repository. The supervisor assigns a developer a managed worktree and writer authority. The developer uses a provider execution while generating code, then a heavy slot for the production build. Its durable result returns to the supervisor. A reviewer reads the worktree and reports a blocking regression. The supervisor starts another turn, asks the developer to correct it, incorporates both results, and explicitly completes the workflow.

The terminal, session, worktree, workflow, and result are separate because each has a different lifetime and truth to preserve.

Next: [Workflows and durable results](WORKFLOWS_AND_RESULTS.md) turns this vocabulary into an operating tutorial.
