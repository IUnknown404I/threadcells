# Using the Web UI

The Web UI is the operator's live view of ThreadCells. It is designed for a loopback listener and works normally in a browser or as an installed basic PWA. Installing it does not add offline operational behavior or a new authentication boundary.

![Live ThreadCells Home with dense session, agent, and workflow summaries](/media/screenshots/threadcells-home.webp)

## Main areas

- **Home** summarizes durable session and agent history, current activity, owner attention, and First/Last/Total status counts without loading every terminal.
- **Agents** provides Sessions, Statuses, and Profiles views over terminals, profile/provider identity, execution state, workflow state, and durable results.
- **Flows** creates, enables, disables, inspects, and manually runs recurring agent schedules. The resulting agents and workflow lifecycle appear under Agents.
- **Statistics** displays provider-reported usage without invented metrics.
- **Settings** contains General, Orchestration Capacity, Profiles, Providers, Housekeeping, installation-global Telegram notifications, and About.
- **Docs** serves the public allowlisted documentation packaged with the running build.
- **Spawn Agent** starts a new session from a project, provider, and profile.
- **Add Agent** starts another terminal inside the exact selected session lifetime; it does not join a different historical session that happens to share a name.

Direct URLs are supported. Browser history should preserve the selected Settings and Docs page.

## A normal operating loop

1. Check Home for current session/workflow activity and Settings for host health, disk pressure, and available capacity.
2. Use Spawn Agent and confirm the selected provider is ready.
3. Watch the new session under Agents.
4. Use Flows for recurring schedules. Follow the agents they launch under Agents.
5. Read and incorporate durable results before retiring children.
6. Use Statistics to understand provider-reported usage.

Status labels come from durable control-plane truth. **Processing** means a turn is active; **Ready** means the provider runtime is alive and genuinely idle. Queued labels distinguish provider-capacity exhaustion, child-retirement barriers, and general workflow continuation. An owner-gated badge remains categorical, while the expanded Owner Decision panel shows the concrete durable reason.

Active and historical sessions remain separate durable lifetimes. Deleting a historical session removes only that exact eligible lifetime. Deleting an exited terminal likewise checks its exact runtime identity, writer lease, workflow/result protection, and session relationship before cleanup; ambiguous or active state remains protected. Retained cleanup resources do not create a false execution blocker: the exact lifetime can be tombstoned while protected filesystem authority remains available for later retirement, and repeating the same deletion is safe.

Agents inside a session always use their durable creation sequence. Home and Agents preserve that same order in List and Grid, across expansion, polling, reconnect, restart, and lifecycle changes. Status, ID, provider, profile, activity, and updated time are not presentation sort keys; a new agent appends to the session.

![Live Agents status view with local worktree paths removed from the public capture](/media/screenshots/threadcells-agents.webp)

## Protected settings

Sensitive mutations share one **Unlock operator changes** control. Missing, invalid, locked, unlocked, and expired states are distinct. The exact minimum secret length is five characters, and the default authenticated session lasts five minutes.

The UI sends the secret only for unlock, clears it immediately, and never puts it in browser persistence or exports. Capacity, privileged profile/provider changes, Telegram configuration/tests, Housekeeping execution, Full Cleanup execution, and applicable owner launches remain locked without the server session.

Settings → Housekeeping ends with the **Delete all system files — Full Cleanup** danger block. Its read-only preview shows reclaim estimates by class, protected reasons, idle status, releases/worktrees, and the warning that only the active release will remain. The existing confirmation modal is mandatory after unlock. Execution is disabled while any agent or filesystem-mutating execution is active, and the server rechecks that condition before deleting. The result reports planned/actual reclaim, skips, disk state, active release, and rollback availability.

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

## Full Output

Full Output renders retained provider text for human inspection after stripping ANSI/VT control sequences and terminal cursor manipulation. Sanitization prevents presentation controls from rewriting the visible history; it does not reinterpret, execute, or certify the provider's text. If Full Cleanup safely removed an exited agent's old log while retaining its metadata, the viewer reports that durable output is unavailable instead of showing an error or fabricated content.

## Install as an app

Supported Chromium browsers can install ThreadCells from the browser's install action. The manifest uses ThreadCells branding and opens in standalone display mode. iOS can use **Add to Home Screen**.

When operator access is protected by browser credentials, manifest and related same-origin requests use the same credential boundary. Cross-origin access remains limited to explicitly trusted origins; PWA metadata does not bypass operator or remote-access controls.

The conservative service worker caches only immutable fingerprinted static assets. It never caches HTML navigation, APIs, operator authorization, agents, sessions, workflows, results, Statistics, terminals, WebSockets, or mutations. If the server is unavailable, the installed app reports the real network failure instead of presenting stale operational state.

A new immutable build replaces old fingerprinted assets through the normal browser service-worker update lifecycle. ThreadCells does not hold the operator on a stale offline shell.

## Responsive and keyboard use

Primary navigation, Docs, Settings, tables, and terminal controls support phone, tablet, and desktop widths. Wide operational tables scroll horizontally on narrow screens rather than shrinking values into unreadable text.

On phones, each Home session header uses a dedicated name row and a separate metadata/action row. Agent cards always use the canonical one-column list; the List/Grid selector is hidden. Tablet and desktop layouts preserve their List/Grid choice.

Use normal Tab/Shift-Tab navigation and visible focus indicators. Code blocks in Docs scroll horizontally and provide a copy control. Terminal keyboard behavior remains provider-native; touch scrolling should not inject terminal input.

## Access boundary

The ordinary UI and Docs do not provide general user login. Keep ThreadCells on loopback. Use an SSH tunnel or an authenticated Caddy/Authelia proxy from [Remote access](REMOTE_ACCESS.md); never publish port 9889 directly.
