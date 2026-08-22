# Start here: What is ThreadCells?

ThreadCells is a self-hosted system for running several coding agents as one coordinated workflow on a Linux machine. It gives agents real terminals and Git worktrees, keeps open missions moving across model turns, and keeps the operator in control of capacity, write access, protected changes, and the final result.

If you can use Git, SSH, and a command-line coding agent, you have enough background to start. You do not need to understand ThreadCells's internal architecture before launching useful work.

## Why use it?

A single coding-agent terminal is easy to understand. Multiple terminals become harder: two agents can edit the same branch, a build can exhaust memory, a supervisor can disappear before collecting a review, and a completed terminal does not necessarily mean the requested mission is complete.

ThreadCells makes those relationships explicit and maintains its own operating environment. It is particularly useful when you want to:

- keep long-running agents visible and reconnectable;
- give parallel workers separate managed worktrees;
- let a supervisor delegate implementation and review;
- let results and Inbox messages return without manually copying between terminals;
- continue one logical mission across provider turns and normal restarts;
- limit model turns, active work, and heavy host tasks independently;
- preserve results even after a terminal exits;
- monitor host pressure and safely clean disposable ThreadCells runtime, log, cache, build, and release debris;
- require an owner decision before a sensitive or ambiguous step.

ThreadCells is designed for one trusted operator or a small trusted team on a host they control. It is not a hostile multi-tenant sandbox.

## The basic loop

```text
Create a session and choose a project and agent
        ↓
Give the agent or supervisor the job
        ↓
Watch the coordinated workflow and host state
        ↓
ThreadCells continues eligible work across model turns
        ↓
Step in only for an explicit owner decision or final review
```

The agent still runs through its native provider CLI. ThreadCells coordinates the surrounding work; it does not replace the provider. Housekeeping protects active work, durable state, backups, and current/recovery releases while reclaiming only candidates whose ownership and eligibility can be proven. That reduces manual babysitting of ThreadCells debris, but it is not a promise that the physical host can never fail.

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
