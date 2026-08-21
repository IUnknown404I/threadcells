# Current limitations

ThreadCells is a technical preview focused on trustworthy local operations for coding agents on one Linux host. These boundaries are intentional product facts, not promises about unimplemented enterprise features.

## Platform and scale

- The supported host baseline is Ubuntu/Debian Linux.
- One local control plane and SQLite database coordinate a modest fleet on one host.
- Capacity limits reduce contention but do not create hard CPU/memory containers or guarantee throughput.
- Very large multi-host, highly available, or horizontally scaled installations are outside the current contract.

## Trust and isolation

- Native agents execute with the runtime user's operating-system access.
- Worktrees isolate Git checkouts, not filesystem or network security.
- Provider adapters are trusted executable packages.
- The system is not designed for hostile multi-tenancy or untrusted public sign-up.

## Web access

- The ordinary UI has no built-in general user login.
- The server must remain loopback-only unless protected by an external authenticated HTTPS proxy.
- Operator authorization protects sensitive settings; it is not a replacement for external access control.
- The installable PWA is network-dependent and does not provide offline agent control.

## Providers and telemetry

- Built-in adapter availability varies with CLI installation, compatibility, and provider authentication.
- Some providers cannot report authentication state non-interactively.
- Usage fields exist only when the provider supplies truthful telemetry.
- Statistics is operational telemetry, not a billing statement; historical unknowns remain unknown.

## Recovery and automation

- Recovery reconciles durable state with external tmux/provider processes but cannot make a non-idempotent external command reversible.
- Backups and restore require operator discipline and should be rehearsed.
- Housekeeping intentionally leaves ambiguous artifacts in place.
- Publication and remote release automation are intentionally not part of ordinary local deployment.

Evaluate ThreadCells on non-critical repositories first, keep verified backups, and inspect agent output before consequential actions.
