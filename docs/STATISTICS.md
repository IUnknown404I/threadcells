# Statistics and provider usage

Statistics summarizes usage that supported provider CLIs actually emit. It helps answer which sessions, profiles, projects, and providers consumed model tokens; it is not a billing ledger and does not invent missing values.

## What the numbers mean

For Codex, ThreadCells records the cumulative provider-native counters available in rollout telemetry:

- input tokens;
- cached input tokens;
- output tokens;
- reasoning tokens;
- total tokens.

Cached input remains visible separately. It is not silently added again as fresh input. A metric the provider did not report appears as **Not reported**, not as a misleading zero.

The default tables omit cache-write tokens because no current adapter exposes that as a meaningful supported metric. The normalized API retains an optional compatibility field so a future adapter can add truthful support without a database migration.

Provider credit, price, and cost information is shown only when the adapter supplies a supported, authoritative value. ThreadCells does not estimate invoices from token totals.

## When usage appears

Usage is collected while a live session runs and stored durably. A session does not need to be deleted, retired, or cleaned up before it contributes to Statistics. Completed but retained sessions continue to count.

Codex emits cumulative snapshots. ThreadCells checkpoints those snapshots and updates the same canonical usage record, so polling, restart, replay, or resume does not count the same tokens twice.

## Reading the page

Start with the global totals, then use the dimension tables to locate usage by terminal, session, project, provider, or profile. Totals use the same normalized records as the detail views.

An example investigation:

1. Notice a rise in global output tokens.
2. Open the session dimension to identify the contributing session.
3. Compare its project, provider, and profile.
4. Open Agents to inspect the corresponding terminal and durable result.

## Historical data

Upgrades may recover historical usage only when retained provider-native evidence can be matched deterministically to a ThreadCells session. Ambiguous or absent source data remains unknown. A repair is idempotent: running it again must not create a duplicate record.

Legacy best-effort terminal parsing may remain in old databases for provenance. Once an exact provider-native record exists, the exact record supersedes the legacy approximation in visible totals.

## Troubleshooting

- **A live session is missing:** refresh the page, verify the provider supports usage collection, and confirm the provider rollout remains readable by the service account.
- **A field says Not reported:** the provider did not supply that metric. Do not interpret it as zero.
- **Totals look duplicated after restart:** compare the session and terminal dimensions and retain the database for diagnosis; replay should update a checkpoint, not insert a second cumulative total.
- **Billing differs:** use the provider's own billing system as the billing authority. ThreadCells reports operational telemetry.

For capacity—not token accounting—see [Capacity and resource model](RESOURCE_MODEL.md).
