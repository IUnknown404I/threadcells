# Upgrading ThreadCells

An upgrade is a controlled candidate promotion with a verified rollback, not an in-place overwrite of whatever files happen to be running.

## Before the upgrade

- Read the release notes and [Limitations](LIMITATIONS.md).
- Confirm current health and active/rollback build identities.
- Let critical provider/heavy operations reach a safe boundary.
- Inspect open workflows and delivered results.
- Create a consistent backup and run database integrity checks.
- Preserve the current candidate as rollback.

## Build and verify

From the intended source commit:

```bash
python3 scripts/build_local_candidate.py --output "$PWD/threadcells-candidate"
candidate="$PWD/threadcells-candidate/threadcells-0.1.0a1-local"
python3 scripts/verify_local_candidate.py --candidate "$candidate"
```

Do not promote if the candidate identity differs from the reviewed commit or if docs/Web/build checks fail.

## Stage and promote

Use the canonical local deployment tooling to stage the candidate without changing the active pointer. Verify the staged files, then promote atomically and restart only ThreadCells services that consume the release.

Expected result: Settings → About, the Docs footer, and release metadata identify the same candidate revision.

## Post-upgrade checks

1. `curl -fsS http://127.0.0.1:9889/health`
2. Open Home and inspect capacity/disk status.
3. Open existing Agents/Flows and confirm durable relationships remain.
4. Compare provider readiness in Settings and Spawn.
5. Confirm operator authorization is configured and protected mutations remain locked until unlock.
6. Open Statistics and confirm a refresh/restart does not duplicate usage.
7. Open Docs routes and verify the packaged build identity.
8. Check terminal streaming/reconnect.
9. Verify the PWA manifest and service worker do not cache dynamic requests.
10. Open Settings → Telegram and confirm its safe configuration state; if native credentials were already configured, run the explicit connection and test-message checks.

## Historical repairs

An upgrade may include a bounded data repair. Run it only when source evidence is deterministic, keep it idempotent, and record before/after counts. Missing provider telemetry must remain missing; never invent historical usage.

## Rollback

If acceptance fails materially:

1. preserve the failed candidate and relevant safe logs;
2. switch the canonical active pointer to the verified rollback candidate;
3. restart only required services;
4. verify the rollback build and core surfaces;
5. restore the pre-upgrade database only if schema/data compatibility requires it.

Do not use destructive Git reset or delete newer runtime evidence to simulate rollback.

See [Local deployment](DEPLOYMENT.md) and [Backup and restore](BACKUP_AND_RESTORE.md).
