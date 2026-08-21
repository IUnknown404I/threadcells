# Codex provider

Codex is ThreadCells's reference provider adapter. Install and authenticate the Codex CLI with its normal operator workflow, then use **Settings → Providers → Codex → Preflight**. ThreadCells reports installation, authentication, version, and adapter capabilities without copying credentials.

The adapter retains interactive tmux lifecycle/status detection, message extraction, start/resume/cancel behavior, profile instructions, model/reasoning controls, trusted MCP capability injection, and provider-reported token usage. Usage is normalized only from Codex's own reported events; it is never estimated from prompt text.

New launches use the immutable profile and provider configuration revisions captured before provider start. For an authenticated XHigh launch, Codex receives a server-authored scoped authorization attestation in developer instructions. The one-use grant, operator secret, bearer header, and verifier reference are never included.

## Launch

```bash
threadcells-server --host 127.0.0.1 --port 9889
threadcells launch --agents supervisor_terra_medium --provider codex
```

Authenticate Codex itself before starting the ThreadCells service and confirm `codex --version` works for the service account. Provider configuration cannot supply an executable path, arguments, flags, environment, or API key.

## Troubleshooting

- A `not_configured` or `AUTHENTICATION_REQUIRED` preflight means the native CLI needs installation/authentication; ThreadCells does not repair it automatically.
- If status remains waiting or processing, inspect the tmux terminal for a provider-native prompt.
- If usage is absent, verify the installed Codex version emits the supported usage events.
- Repository approval/sandbox policy comes from the snapshotted profile and repository authority; provider login does not grant ThreadCells owner authorization.
