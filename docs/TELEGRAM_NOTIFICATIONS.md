# Telegram notifications

ThreadCells can send low-noise lifecycle notifications to one Telegram destination. This is an installation-global ThreadCells capability: it does not belong to, read configuration from, or depend on the currently selected project.

![Live Telegram notification settings with destination and credential fields explicitly redacted](/media/screenshots/threadcells-telegram.webp)

## Configure the destination

1. Create or choose a Telegram bot using Telegram's supported bot-management flow.
2. Obtain the destination chat ID. For a forum topic, also obtain its positive message-thread ID.
3. Open **Settings → Telegram** and unlock operator changes.
4. Enter the bot token, chat ID, and optional topic/thread ID.
5. Save while notifications are disabled.
6. Use **Check connection** to validate the bot credential, then **Send test notification** to validate the destination.
7. Enable notifications and save again.

The test action is explicit; opening Settings never contacts Telegram. Disabling notifications retains the configured destination and token so they can be re-enabled later. **Clear bot token** is a separate confirmed operator action: it removes the credential, disables notifications, and retains the non-secret destination fields.

## Secret handling

The Web UI sends a new token only on a protected update and clears its password field afterward. Read APIs report only `Configured`, `Not configured`, or `Invalid`; they never return the token. ThreadCells does not put the token in browser storage, terminal prompts, session or agent metadata, normal logs, or the SQLite settings row.

The server stores the token at:

```text
$CAO_HOME_DIR/secrets/telegram-bot-token
```

The parent directory is restricted to the runtime account and the token file uses mode `0600`. Replacement uses an atomic filesystem rename; clearing unlinks the credential without following it and synchronizes the secret directory. `CAO_HOME_DIR` is the installation's private mutable state root, not a public repository path.

Treat this file as a credential. Do not copy it into source control, ordinary support bundles, database exports, shell history, or screenshots. Rotate it through Telegram if disclosure is suspected.

## Notification policy

The first-release policy sends at most one attempt for each durable top-level workflow event:

- successful top-level completion;
- a top-level owner-attention gate;
- unexpected failure of a top-level terminal while its workflow is open.

ThreadCells does not notify for child completion, delegation, polling, progress updates, internal retry cycles, or every model/tool turn. Durable event keys prevent a repeated observation or restart from duplicating an already claimed delivery.

Messages contain only concise safe context: ThreadCells identity, session, project display name when present, lifecycle state, a fixed summary, and UTC timestamp. They do not include prompts, model output, filesystem dumps, exception bodies, operator secrets, or the bot token.

## Failure behavior

Telegram delivery is fail-open for agent work. A timeout, rejected credential, or unavailable Telegram service records a safe result code but cannot fail or reopen the workflow. Delivery has a bounded single attempt; ThreadCells does not endlessly retry or replay historical events after notifications are enabled.

**Check connection** validates the bot token with Telegram. **Send test notification** also validates the configured chat/topic routing. A successful connection check does not prove that the bot can write to the chosen destination, so use both actions when configuring a new destination.

## Backup and restore

The non-secret enabled/destination state and the delivery ledger are in the ThreadCells database. The bot token is separate. If notifications must survive disaster recovery, back up the token as a separately encrypted credential with ownership and mode preserved; do not add it to a routine plaintext database archive.

After restore, verify the secret path and permissions, leave notifications disabled initially, run both explicit checks, then enable delivery. Restoring the database without the token safely reports `Not configured`.

## Troubleshooting

- **Not configured:** supply both a valid bot token and chat ID before enabling.
- **Invalid token storage:** verify the token is a regular, non-symlink file owned by the runtime account with no group/other permissions.
- **Connection failed:** check outbound HTTPS/DNS and rotate or replace a rejected bot token; safe UI errors deliberately omit Telegram response details.
- **Connection works but test fails:** confirm the bot belongs to the destination and can post there; check the chat and optional topic IDs.
- **No lifecycle message:** confirm Enabled is on and remember that only top-level completion, owner attention, and unexpected top-level failure notify. Events that occurred while disabled are not replayed.
