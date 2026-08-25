# Troubleshooting

Start by preserving evidence: current build identity, safe error text, affected session/workflow, capacity status, recent logs, and Git status. Avoid cleanup, deletion, or blind retries until you know whether a durable operation already succeeded.

## Web UI does not start

**Checks:** run the server in the foreground, call `curl -fsS http://127.0.0.1:9889/health`, confirm the port is listening on loopback, and inspect Settings → About when available.

**Resolution:** fix the reported dependency/configuration error or port conflict. If health works but static files do not, verify the candidate and ensure Python code and packaged Web assets come from the same build.

## Browser on another machine cannot connect

This is expected when ThreadCells correctly listens on loopback. Do not change it to a public bind. Use the SSH tunnel or authenticated proxy in [Remote access](REMOTE_ACCESS.md).

## Provider shows CLI not installed

**Checks:** compare Settings → Providers with Spawn Agent, then run `command -v PROVIDER_COMMAND` as the ThreadCells runtime user.

**Resolution:** install the provider's canonical CLI for that account, correct its service `PATH`, or choose another ready provider. Adapter registration alone is not installation.

## Provider installed but not authenticated

**Checks:** run the provider's supported authentication status command as the runtime user.

**Resolution:** complete the provider-native login flow. ThreadCells does not copy another user's credentials or log in during preflight.

## Provider says readiness unverified

The command exists but cannot expose safe non-interactive authentication truth. Verify its version and perform a small native test. It may remain launchable; inspect the resulting terminal for a provider login prompt.

## Agent does not start

**Checks:** provider readiness, selected profile's resolved preview, project path/permissions, resident/Provider/Work capacity, tmux availability, and terminal startup output.

**Resolution:** correct the first failed admission or provider prerequisite. Do not repeatedly launch duplicates while a first session is still starting.

## Capacity exhausted

Open Orchestration Capacity and identify the exact full category. Retire safely completed work or wait for the corresponding provider/heavy task. Raise only that limit when the host and quota have measured headroom.

## Heavy execution slot unavailable

A build, browser test, scan, or recovery job holds the Heavy slot. Wait for it or investigate a stale lease through canonical status. Do not run an expensive command outside admission merely to bypass the queue.

## Workflow waiting for owner

Read the gate reason. Provide the requested decision only if it is a genuine publication, trust, destructive, cost, or product-semantics boundary. An ordinary provider final should leave eligible autonomous work open; report an automatic closure as a workflow defect.

## Result not incorporated

Confirm the child recorded a durable result and that it was delivered to the correct parent. The parent must read/use the immutable result, then acknowledge incorporation. Restart replay may deliver an unacknowledged result again; do not apply it twice.

## New owner input stays queued behind a closed workflow

Restart the supported runtime once and inspect the exact workflow and Inbox identities. Current builds reconcile a pending ordinary Inbox transport whose bound workflow is no longer open, then allow the newer open owner turn to continue. Do not rebind or manually edit the Inbox row; retain the database and report a defect if the stale transport remains pending or any payload crosses workflow identity.

## Operator authorization not configured

Confirm `THREADCELLS_OPERATOR_VERIFIER_FILE` reaches the real server process and restart. If configuration is invalid, check schema, absolute/canonical path, file owner/mode, readability, and every parent directory. The service account must not own or be able to replace the verifier.

## Correct operator secret fails

Confirm the server loaded the same verifier the CLI generated. The minimum is exactly five characters. Check for an old server process or a recently replaced verifier; do not log the entered secret.

## Telegram is not configured or a test fails

Open Settings → Telegram after unlocking operator changes. `Not configured` requires both a valid bot token and chat ID. `Invalid` means the private token file failed its ownership, regular-file, or mode checks. A successful connection check validates the bot credential; send an explicit test notification to validate the chat and optional topic ID. Check outbound HTTPS/DNS if either action fails. Safe errors intentionally omit Telegram response bodies and the token. See [Telegram notifications](TELEGRAM_NOTIFICATIONS.md).

## Statistics missing a current session

Refresh usage/status, verify the provider supports telemetry, and confirm its durable rollout evidence remains readable. Sessions do not need deletion before counting. Missing provider fields should say Not reported, not zero.

## Statistics total appears duplicated

Compare global, session, and terminal dimensions and preserve the database. Provider cumulative snapshots should update one stable checkpoint across poll/restart/replay. Do not delete rows manually before diagnosis.

## Docs/build identity mismatch

Settings → About, Docs footer, candidate manifest, and static asset revision should agree. Rebuild and verify one immutable candidate; do not combine Web output from one checkout with Python code from another.

## Disk pressure or Housekeeping cannot reclaim

Inspect a Housekeeping dry-run plan. Protected, active, unknown, backup, current, and rollback items are intentionally retained. Address the reported owner/reference or expand disk safely; never recursively delete the runtime root.

## Browser terminal does not reconnect after restart

Refresh once, confirm the server and tmux session are healthy, and check the browser's WebSocket connection through any reverse proxy. Ensure Caddy or another proxy is not stripping upgrade headers. An installed PWA does not cache terminal or WebSocket state.

## Still stuck

Retain the smallest reproducible evidence and run the focused component checks before broad suites. Include only public-safe paths and messages in issue reports. See [Contributing](../CONTRIBUTING.md) for report expectations.
