# Claude Code provider

Claude Code is a first-class ThreadCells provider adapter. Install and authenticate the Claude CLI through its normal operator workflow, then run preflight under **Settings → Providers**. Preflight reports executable availability, authentication, version, compatibility, and a stable reason code without exposing credentials.

The adapter supports interactive tmux lifecycle/status detection, start/cancel, profile instructions, model selection, trusted MCP configuration, and conditional resume, structured completion, session persistence, and usage. Token usage is present only when Claude's CLI reports it; ThreadCells does not estimate missing telemetry.

ThreadCells never changes Claude's settings during startup or preflight. If an operator chooses a permission-bypass mode, it must be configured explicitly through the provider's own trusted configuration and reviewed against the host/repository boundary.

New launches consume the exact immutable profile and provider configuration revisions snapshotted before provider start. An authenticated XHigh launch receives only a server-authored scoped attestation in system/developer instructions; no operator secret or grant reaches the provider.

## Launch

```bash
threadcells-server --host 127.0.0.1 --port 9889
threadcells launch --agents developer --provider claude_code
```

If preflight is `not_configured`, install or authenticate the native CLI for the service account and rerun preflight. Missing live authentication is not an adapter defect: deterministic command, state, result, and error tests remain usable without external credentials.
