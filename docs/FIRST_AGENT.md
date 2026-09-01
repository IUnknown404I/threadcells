# Your first project and agent

This tutorial starts one deliberately small agent and shows where to find its terminal and result. Complete [Quick setup](../QUICK_SETUP.md) first and leave the ThreadCells server running.

## 1. Prepare a safe repository

Use a disposable or clean Git repository for the first run. ThreadCells identifies a project by its repository and can create managed worktrees beside it.

```bash
mkdir -p /tmp/threadcells-first-project
cd /tmp/threadcells-first-project
git init
printf '# First project\n' > README.md
git add README.md
git commit -m 'Create first project'
```

Expected result: `git status --short` prints nothing. Starting clean makes the agent's changes easy to inspect.

## 2. Open ThreadCells

Open `http://127.0.0.1:9889` on the machine running ThreadCells. If the host is remote, establish the SSH tunnel described in [Remote access](REMOTE_ACCESS.md) first.

Open **Spawn Agent**, select the repository as the project, and choose an installed provider. A provider marked **CLI not installed** cannot launch; see [Providers](PROVIDERS.md) if your expected provider is unavailable.

Choose a general-purpose worker profile for this first task. Enter a bounded prompt such as:

```text
Add a short Usage section to README.md. Do not change any other file.
Run git diff --check and report the changed file.
```

Start the agent.

## 3. Watch the terminal

The new agent appears under **Agents**. Its terminal is a real tmux session, so the provider's native output remains visible and reconnectable. ThreadCells records the project, profile, provider, and session identity around that terminal.

Expected result: the status changes from starting to running, provider output appears, and capacity reflects one active provider execution while the model is producing a turn.

If the agent never starts, check the provider's availability label and the capacity cards. [Troubleshooting](TROUBLESHOOTING.md) has symptom-based checks.

## 4. Inspect the work

When the agent finishes, inspect its durable result and the repository diff. A terminal reaching a final provider message is evidence, but it is not permission to merge, publish, or deploy.

```bash
cd /tmp/threadcells-first-project
git status --short
git diff -- README.md
```

The Project-backed agent works in the managed worktree path shown by ThreadCells, not the registered source root. Use that path for inspection. The worktree keeps concurrent writers separate until their commits are deliberately reconciled.

## 5. Try supervision

Once a single worker makes sense, launch a supervisor profile on another small task. Ask it to assign one implementation task and one independent review. The relationship should look like this:

```text
Owner
  └── Supervisor
        ├── Developer
        └── Reviewer
              ↓
        Durable results return to the supervisor
```

The supervisor remains responsible for incorporating those results and completing the top-level workflow. A worker finishing does not close the supervisor's mission.

## Next steps

- Learn the names used in the UI: [Core concepts](CONCEPTS.md).
- Understand profiles before creating custom ones: [Profiles](PROFILES.md).
- Learn how delegation survives terminal completion: [Workflows and durable results](WORKFLOWS_AND_RESULTS.md).
- Size the machine conservatively: [Capacity and resource model](RESOURCE_MODEL.md).
