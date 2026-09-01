# Projects and managed worktrees

A ThreadCells project is a registered Git repository and canonical source authority. It gives sessions, profiles, statistics, and workflows a stable place to belong, but it is not the normal writable directory of a new supervisor. ThreadCells never makes a repository safe merely by registering it, so start with a clean status and understand the write boundary you grant.

## Registering a project

Use the project selector in Spawn Agent to choose an existing repository or add the repository through the supported project control. Use an absolute canonical path and confirm that the ThreadCells runtime user can read it.

Before the first agent:

```bash
git -C /path/to/project status --short
git -C /path/to/project worktree list
```

Expected result: you can distinguish pre-existing changes and worktrees from anything ThreadCells creates later. Existing uncommitted work belongs to the operator; agents must not discard it.

## Why managed worktrees exist

Two writers in one checkout can overwrite each other's edits even if their prompts are unrelated. A managed Git worktree gives each bounded writer its own checkout and branch while sharing the repository's object database.

```text
Canonical repository
  ├── operator checkout
  ├── Session A supervisor worktree
  ├── Session B supervisor worktree
  ├── developer worktree
  └── reviewer worktree or read-only context
```

ThreadCells records the relationship instead of treating temporary directories as anonymous. That makes cleanup and result attribution safer.

Every new Project-backed supervisor Session, including the first one, receives a unique managed worktree and branch at an exact recorded base revision. A second Session in the same Project receives another worktree; resident capacity remains global. One Session still has one primary supervisor, and one writable context/worktree still has at most one writer lease. Replacing an unusable supervisor for the same context uses explicit recovery takeover and preserves that context's worktree rather than creating an independent one.

Active legacy Sessions that predate this contract remain in their existing workspace. ThreadCells does not move, reset, clean, stash, or copy their dirty state during upgrade; new Sessions use managed worktrees.

## Writer authority

Only the context holding writer authority should modify a managed worktree. Reviewers can inspect diffs and run safe checks without becoming an untracked second writer.

Do not manually edit a managed worktree while its agent is active. If an emergency intervention is necessary, stop or coordinate the writer first and record what changed.

## Bringing work back

A durable result should name changed files and checks, but Git remains the source of truth for code. Review the worktree's status, diff, and commits before merging or cherry-picking through your normal repository process.

ThreadCells does not grant publication authority. A successful worker result does not authorize pushing, tagging, deploying, or rewriting history.

## Cleanup

Housekeeping removes a managed worktree only when it can prove the worktree is no longer protected by an active terminal, workflow, writer lease, or unincorporated result. Unknown ownership fails closed.

If disk usage is high, plan Housekeeping first. Do not delete a worktree directory directly; doing so can leave Git metadata and ThreadCells state inconsistent.

## Common mistakes

- Starting from a dirty repository without recording existing changes.
- Giving two agents writer authority to the same checkout.
- Treating a worktree as a security sandbox.
- Deleting a worktree before its result and commits are incorporated.
- Assuming a managed branch is automatically merged or pushed.

See [Workflows and durable results](WORKFLOWS_AND_RESULTS.md) for how worktree results reach a supervisor.
