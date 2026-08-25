# Backup and restore

A useful ThreadCells backup preserves the durable coordination state and the configuration needed to interpret it. Installed code and build caches are usually rebuildable; the database, operator-owned configuration, and provider-native evidence may not be.

## What matters

Back up, as applicable:

- the ThreadCells SQLite database and its associated SQLite files;
- configuration and service environment, excluding plaintext secrets from ad hoc archives;
- the operator verifier file as a separately protected secret-adjacent artifact;
- the Telegram bot-token file, if configured, as a separately encrypted credential with ownership and mode preserved;
- agent context, attachments, and logs required by your retention policy;
- managed-worktree and release metadata needed to interpret active work;
- the exact active and rollback candidate manifests/identities;
- external provider state only according to that provider's own supported backup policy.

Git repositories should already have their own backup/remote strategy. A ThreadCells database backup is not a substitute for preserving commits.

## What is rebuildable

Downloaded Web dependencies, browser revisions, package caches, temporary build directories, and verified candidate contents can usually be recreated from source and lockfiles. Do not enlarge every backup with caches merely because they exist under runtime paths.

## Consistent backup sequence

1. Record the active source/candidate identity and current service state.
2. Avoid starting new sessions or mutations during the snapshot window.
3. Use the canonical database backup mechanism rather than copying a live SQLite file blindly.
4. Run SQLite integrity verification on the backup.
5. Copy required configuration, verifier, and configured Telegram-token artifacts with permissions preserved and without printing their contents.
6. Record checksums and store the archive outside the live state root.
7. Test that the backup can be listed and read by the intended recovery principal.

If the deployment tooling provides a backup command, use it: it understands the actual database path and service coordination. Never place plaintext provider or operator secrets in shell history to build an archive.

## Verification

At minimum, verify the copied SQLite database:

```bash
sqlite3 /path/to/backup.db 'PRAGMA integrity_check;'
```

Expected result: `ok`. Also record a checksum and confirm that the archive contains the expected configuration, verifier, and build identity without exposing their content in logs.

An untested backup is only a hypothesis. Periodically rehearse restore into an isolated path and local-only port.

## Restore order

1. Stop or isolate the target ThreadCells service.
2. Preserve the current failed state for forensic rollback.
3. Install or select the exact compatible candidate.
4. Restore the database and mutable state with the runtime account's expected ownership.
5. Restore service configuration.
6. Restore the operator verifier with a distinct trusted owner, service-readable mode, and trustworthy parent directory chain; restore an applicable Telegram token to `$CAO_HOME_DIR/secrets/telegram-bot-token` as a runtime-owned regular file with mode `0600`.
7. Run integrity checks before start.
8. Start on loopback and verify health/build identity.
9. Inspect active workflows, results, terminals, projects, provider preflight, and Statistics before retrying work.

Do not restore only the database while leaving mismatched code or stale service environment. Do not assume tmux/provider processes survived consistently; reconcile each live process with durable session state.

## Recovery validation

After restore, verify:

- Settings → About matches the intended candidate;
- `/health` is successful;
- projects and session history are present;
- delivered results remain attributable;
- provider availability reflects the restored runtime user's actual installation;
- operator authorization reports configured and unlocks with the existing secret;
- Telegram reports its expected safe configuration state and, if restored, passes explicit connection and test-message checks before enablement;
- Statistics totals replay without duplication;
- active/rollback releases remain correctly identified.

Backups are protected from automatic Housekeeping. Apply a separate, reviewed retention policy to backup storage.

Full Cleanup does not replace backup retention. It protects the canonical database and any backup whose disposability is not proven, but deliberately removes every inactive local release and rollback represented by trusted release metadata. Before authorizing it, confirm that any recovery point you require exists outside the local release set and is integrity-checked. Afterward, the operator must expect local rollback to be unavailable until another verified release is staged.
