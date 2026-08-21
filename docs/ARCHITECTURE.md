# Architecture

ThreadCells is a local control plane around native coding-agent processes. It deliberately keeps the provider terminal, Git repository, durable coordination state, and browser UI as separate components with explicit boundaries.

Start with [Core concepts](CONCEPTS.md) if the terms below are new.

## System view

```text
Browser or installed PWA
        ↓ HTTP / WebSocket on loopback
FastAPI ThreadCells server
  ├── SQLite durable state
  ├── provider/profile registries
  ├── workflow and result service
  ├── capacity and Housekeeping service
  └── tmux/provider adapter control
               ↓
        Native provider CLIs
               ↓
      Git repositories/worktrees
```

## Server and Web UI

The FastAPI server exposes the application/API and serves one production Web build. The React UI reads live operational state and connects to terminal streams through WebSockets.

The basic PWA worker caches only fingerprinted static assets. HTML, APIs, authorization, sessions, workflows, Statistics, terminals, mutations, and WebSockets remain network-dependent so the UI cannot invent an offline control-plane state.

The Docs bundle is generated at build time from `DOCS_MANIFEST.json`. Only allowlisted public Markdown enters the runtime.

## Durable state

SQLite holds sessions, terminals, projects, profile/provider revisions, resource leases, workflows, results, usage records, audit events, and scheduling receipts. Operations that must be exactly-once or replay-safe use stable identities and database transactions instead of relying on transient terminal output.

Provider processes and tmux sessions are external runtime facts. Startup/recovery reconciles them with the database; it must not assume that one side's existence proves the other is current.

## Provider execution

An adapter translates a normalized ThreadCells launch into a reviewed native CLI invocation. The provider still renders its own terminal UI and maintains its own authentication. Adapters report capabilities and preflight truth rather than simulating unsupported behavior.

Structured provider telemetry is normalized into durable usage records. Cumulative counters use stable checkpoints so polling and restart do not duplicate totals.

## Git work contexts

Managed worktrees share the repository object database but isolate checkout paths and branches. Writer authority keeps mutation ownership explicit. Worktrees are concurrency tools, not operating-system sandboxes.

## Workflows and results

Workflow state survives individual provider turns. Delegated results are recorded, delivered at least once, incorporated by the parent, and acknowledged before eligible child retirement. Explicit completion—not a model final—closes the top-level mission.

## Admission and pressure

Resident supervisors, provider executions, Work contexts, and Heavy executions have independent leases and limits. Disk pressure and Housekeeping protection are additional runtime constraints. Cross-process fences ensure two processes cannot both believe they acquired the final slot.

## Security boundary

ThreadCells assumes one trusted host and operator environment. General UI access is protected externally by loopback/SSH or an authenticated reverse proxy. Sensitive Settings mutations use a distinct operator-verifier/session boundary, but that is not a general login system.

Provider packages and native CLIs are trusted executable code. Imported configuration is constrained declarative data. See [Security model](SECURITY_MODEL.md).
