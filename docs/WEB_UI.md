# Using the Web UI

The Web UI is the operator's live view of ThreadCells. It is designed for a loopback listener and works normally in a browser or as an installed basic PWA. Installing it does not add offline operational behavior or a new authentication boundary.

## Main areas

- **Home** summarizes current health, capacity, and recent activity.
- **Agents** shows sessions, statuses, terminals, profile/provider identity, and durable results.
- **Flows** shows supervisor/worker relationships and workflow progress.
- **Statistics** displays provider-reported usage without invented metrics.
- **Settings** contains General, Orchestration Capacity, Profiles, Providers, Housekeeping, installation-global Telegram notifications, and About.
- **Docs** serves the public allowlisted documentation packaged with the running build.
- **Spawn Agent** starts a new session from a project, provider, and profile.

Direct URLs are supported. Browser history should preserve the selected Settings and Docs page.

## A normal operating loop

1. Check Home for health, disk pressure, and available capacity.
2. Use Spawn Agent and confirm the selected provider is ready.
3. Watch the new session under Agents.
4. Use Flows when a supervisor delegates work.
5. Read and incorporate durable results before retiring children.
6. Use Statistics to understand provider-reported usage.

## Protected settings

Sensitive mutations share one **Unlock operator changes** control. Missing, invalid, locked, unlocked, and expired states are distinct. The exact minimum secret length is five characters, and the default authenticated session lasts five minutes.

The UI sends the secret only for unlock, clears it immediately, and never puts it in browser persistence or exports. Capacity, privileged profile/provider changes, Telegram configuration/tests, Housekeeping execution, and applicable owner launches remain locked without the server session.

Follow [Operator authorization](OPERATOR_AUTHORIZATION.md) to provision the verifier safely.

## Provider and profile selection

Provider labels distinguish **Built-in adapter** from **CLI ready**, **CLI not installed**, **Authentication required**, **Installed but unhealthy**, or **Readiness unverified**. Spawn disables only a provider proven unavailable and uses the same server preflight as Settings.

Profiles prioritize searchable built-in/custom discovery and resolved previews. Raw artifact import/export is intentionally under Advanced. Selecting the exceptional owner XHigh profile displays an authority warning and requires its separate grant path.

## Telegram notifications

Settings → Telegram configures one installation-global destination independently of projects. The bot token is write-only in the UI; connection and test-message actions are explicit, and the separate confirmed clear action disables delivery while removing the credential. Enabled delivery covers only top-level completion, owner-attention gates, and unexpected top-level terminal failure, with durable duplicate suppression and fail-open delivery. See [Telegram notifications](TELEGRAM_NOTIFICATIONS.md).

## Statistics

Statistics includes active, completed, and retained non-deleted sessions as soon as durable provider telemetry is available. Cached input and reasoning output remain separate; unavailable fields read **Not reported**. See [Statistics and provider usage](STATISTICS.md).

## Docs reader

Docs navigation is grouped by the learning journey, searchable, and accompanied by an on-page outline on wide screens. Previous/next links follow the published manifest order. The reader exposes only packaged allowlisted Markdown; it has no arbitrary filesystem browser or edit endpoint.

## Install as an app

Supported Chromium browsers can install ThreadCells from the browser's install action. The manifest uses ThreadCells branding and opens in standalone display mode. iOS can use **Add to Home Screen**.

The conservative service worker caches only immutable fingerprinted static assets. It never caches HTML navigation, APIs, operator authorization, agents, sessions, workflows, results, Statistics, terminals, WebSockets, or mutations. If the server is unavailable, the installed app reports the real network failure instead of presenting stale operational state.

A new immutable build replaces old fingerprinted assets through the normal browser service-worker update lifecycle. ThreadCells does not hold the operator on a stale offline shell.

## Responsive and keyboard use

Primary navigation, Docs, Settings, tables, and terminal controls support phone, tablet, and desktop widths. Wide operational tables scroll horizontally on narrow screens rather than shrinking values into unreadable text.

Use normal Tab/Shift-Tab navigation and visible focus indicators. Code blocks in Docs scroll horizontally and provide a copy control. Terminal keyboard behavior remains provider-native; touch scrolling should not inject terminal input.

## Access boundary

The ordinary UI and Docs do not provide general user login. Keep ThreadCells on loopback. Use an SSH tunnel or an authenticated Caddy/Authelia proxy from [Remote access](REMOTE_ACCESS.md); never publish port 9889 directly.
