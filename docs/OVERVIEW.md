# Start here: What is ThreadCells?

ThreadCells is a self-hosted operations console for running several coding agents on one Linux machine. It gives those agents real terminals and Git worktrees, while keeping the operator in control of capacity, write access, protected changes, and the final result.

If you can use Git, SSH, and a command-line coding agent, you have enough background to start. You do not need to understand ThreadCells's internal architecture before launching useful work.

## Why use it?

A single coding-agent terminal is easy to understand. Multiple terminals become harder: two agents can edit the same branch, a build can exhaust memory, a supervisor can disappear before collecting a review, and a completed terminal does not necessarily mean the requested mission is complete.

ThreadCells makes those relationships explicit. It is particularly useful when you want to:

- keep long-running agents visible and reconnectable;
- give parallel workers separate managed worktrees;
- let a supervisor delegate implementation and review;
- limit model turns, active work, and heavy host tasks independently;
- preserve results even after a terminal exits;
- require an owner decision before a sensitive or ambiguous step.

ThreadCells is designed for one trusted operator or a small trusted team on a host they control. It is not a hostile multi-tenant sandbox.

## The basic loop

```text
Choose a project and profile
        ↓
Launch an agent in a tmux terminal
        ↓
Watch work, capacity, and results in the Web UI
        ↓
Agent finishes a bounded task or requests an owner decision
        ↓
Review the durable result and continue or close the workflow
```

The agent still runs through its native provider CLI. ThreadCells coordinates the surrounding work; it does not replace the provider.

## A useful first hour

1. Follow [Quick setup](../QUICK_SETUP.md) to build and verify a local candidate.
2. Use [Installation](INSTALLATION.md) if you want the reasoning behind each step or need help with prerequisites.
3. Follow [Your first project and agent](FIRST_AGENT.md).
4. Read [Core concepts](CONCEPTS.md) after you have seen one agent run.
5. Before using another machine, choose a safe method from [Remote access](REMOTE_ACCESS.md).

After that, [Providers](PROVIDERS.md), [Profiles](PROFILES.md), and [Workflows and durable results](WORKFLOWS_AND_RESULTS.md) explain the main operating model. [Operations](OPERATIONS.md) covers the routine checks that keep an installation healthy.

## What ThreadCells does not do

ThreadCells worktrees organize writes; they do not sandbox an agent from the host. ThreadCells also does not add general login protection to the Web UI. Keep the server loopback-only and use SSH forwarding or an authenticated reverse proxy for remote access.

The current release is a technical preview. Read [Security model](SECURITY_MODEL.md) and [Limitations](LIMITATIONS.md) before placing valuable repositories under agent control.

## Creator and maintainer

ThreadCells was created and is maintained by [Subaev Ruslan](https://github.com/IUnknown404I), with contributions from the ThreadCells community. It grew from the practical need to operate multiple native CLI coding agents with stronger operational control, durable results, and resource safety.
