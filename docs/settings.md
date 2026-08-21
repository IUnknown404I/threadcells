# Settings

ThreadCells Settings separates canonical control-plane state from legacy discovery compatibility.

- **Capacity** persists one versioned SQLite settings row plus append-only audit records. It is seeded once from the effective legacy operations JSON and thereafter becomes canonical.
- **Profiles** persists immutable built-in/custom revisions and enabled state; legacy Markdown directories are deterministic one-time migration inputs.
- **Providers** persists versioned declarative configurations that reference installed trusted adapters.
- **Housekeeping** persists validated class policy and effective frequent/weekly/pressure schedules.
- **Telegram** persists installation-global enabled/destination state in SQLite while keeping the bot token in restrictive filesystem secret storage.

The dedicated browser routes are `/settings/profiles`, `/settings/providers`, `/settings/housekeeping`, and `/settings/telegram`. Capacity configuration remains on the main Settings surface. All mutations use the same service validation as API/CLI; provider/profile exports and Telegram read responses omit secrets.

Legacy agent-directory settings remain available for discovery and rollback read compatibility. They do not shadow packaged built-ins and do not become a second writable authority for registry revisions. See [Profiles](PROFILES.md), [Providers](PROVIDERS.md), [Housekeeping](HOUSEKEEPING.md), [Telegram notifications](TELEGRAM_NOTIFICATIONS.md), and [API](api.md).
