# Security model

ThreadCells is intended for one trusted Linux host operated by one person or a small trusted team. It coordinates powerful native coding agents; it is not a sandbox for hostile users, prompts, repositories, or provider plugins.

## Practical trust boundaries

### Host and runtime user

Anything readable or executable by the ThreadCells operating-system account may be reachable by a native agent. Use a dedicated account, minimum filesystem permissions, reviewed provider installations, and repositories appropriate for automation.

Managed worktrees separate writers but do not contain them. Do not give the runtime account credentials or host access an agent does not need.

### Web access

The ordinary UI and packaged Docs do not implement general user login. Bind to `127.0.0.1`. Use an SSH tunnel for occasional access or Caddy plus Authelia for an authenticated HTTPS URL. Never expose the raw ThreadCells port publicly.

An installed PWA retains the same network trust boundary. Its service worker cannot provide offline operational state and does not cache APIs, terminals, authorization, workflows, results, or Statistics.

### Operator authorization

Sensitive control-plane mutations use a separate operator verifier provisioned by a distinct OS principal. The exact minimum secret length is five characters; longer randomly generated values are recommended.

ThreadCells stores a salted scrypt verifier, short-lived session/grant digests, scope, issuer, expiry, consumption, and audit records—not the plaintext. The verifier and every parent directory must not be replaceable by the service account. The five-minute browser session uses an HttpOnly, SameSite=Strict cookie.

This boundary protects configured mutations. It does not turn the whole Web UI into a multi-user authenticated application.

### Exceptional owner launch

Owner-executor/XHigh launches require a one-use capability bound to the exact immutable profile revision, provider configuration revision, project/worktree, session request, topology, issuer, and delegation depth. It is consumed atomically with terminal metadata.

The Web Create Session and Add Agent flows for the builtin `critical_sol_xhigh_owner` require the same exceptional warning, explicit confirmation, operator unlock, and scoped one-use capability. Add Agent binds the grant to the existing session and resolved canonical worktree rather than accepting a user-entered path. The local `critical_sol_xhigh_owner --owner-xhigh` path requires explicit interactive confirmation and loopback-only launch. None of these paths confers authority on children or unrelated Web Settings. Prompt text cannot mint or delegate owner authority.

### Providers and imported artifacts

Provider adapters are executable trusted packages and require operator review. Provider/profile JSON is untrusted declarative input: executable paths, commands, shell flags, environments, raw secrets, arbitrary MCP commands, and ungranted wildcard authority are rejected.

Provider authentication stays in the provider's own supported mechanism. Registry exports omit secret values and one-use grants.

Per-terminal control credentials are scoped to the terminal/provider process. ThreadCells starts the long-lived tmux server through a credential-free bootstrap, so its persistent process command line does not retain a terminal credential. Those credentials remain sensitive to processes running as the same trusted runtime account.

### Telegram notifications

Telegram delivery is optional, disabled by default, installation-global, and independent of project configuration. Its bot token is a write-only Web setting stored outside SQLite in the private ThreadCells state root as a runtime-owned `0600` regular file. Read APIs expose only safe configuration state. Operator authorization protects updates and explicit connection/test actions.

Lifecycle messages use fixed safe summaries and omit prompts, terminal output, exception bodies, paths, and credentials. External delivery is fail-open for workflow execution and durably de-duplicated; enabling notifications does not replay events observed while disabled.

## Data sensitivity

Treat the SQLite database, terminal logs, prompts, results, attachments, managed worktrees, backups, operator verifier, and provider-native rollout history as sensitive. They may contain proprietary code or user-supplied content even when ThreadCells itself avoids logging credentials.

Do not put plaintext operator/provider/Telegram secrets in repositories, environment dumps, support bundles, telemetry, browser storage, API responses, or screenshots.

## Destructive operations

Housekeeping is plan-first and fails closed. Unknown, unreadable, open, active, referenced, identity-changed, or metadata-incomplete resources remain protected. Backups are never automatically deleted.

Deployment preserves a rollback runtime and database backup. Publication, public network exposure, and destructive history changes remain separate owner decisions.

## Operator responsibilities

- Patch the OS, ThreadCells, provider CLIs, reverse proxy, and authentication layer.
- Review prompts, profiles, adapters, and repositories before granting write access.
- Keep the runtime user and service environment minimally privileged.
- Back up and test restore of durable state.
- Inspect diffs/results before merge, deployment, or publication.
- Preserve loopback or authenticated-proxy access controls.
- Rotate provider credentials and replace the operator verifier through a safe administrative process.

## Reporting a security issue

Follow [SECURITY.md](../SECURITY.md). Do not include live credentials, private state, or public exploit detail beyond what maintainers need for safe reproduction.
