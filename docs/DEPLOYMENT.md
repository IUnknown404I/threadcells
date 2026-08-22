# Local deployment

ThreadCells deployment promotes a verified immutable candidate into the local runtime. It does not imply publication, a Git push/tag, package release, or public network exposure.

## Candidate discipline

Build from one exact clean source commit, then verify the candidate before staging:

```bash
python3 scripts/build_local_candidate.py --output "$PWD/threadcells-candidate"
python3 scripts/verify_local_candidate.py \
  --candidate "$PWD/threadcells-candidate/threadcells-0.1.0a1-local"
```

The candidate should contain Python code, packaged Web assets, the allowlisted Docs bundle, build identity, checksums, and release metadata from the same revision.

Host staging uses a dedicated release-maintenance group so the running control plane can read but cannot replace an immutable candidate, while the Housekeeping services can remove an explicitly unprotected release. Create that system group once before the first host stage:

```bash
sudo groupadd --system threadcells-release-admin
```

The staging command fails closed if this group is unavailable. It accepts production candidate paths only as direct children of the configured ThreadCells release root and refuses symbolic-link or alternate lock/metadata targets.

## Safe promotion sequence

1. Record the current active runtime and its health.
2. Preserve it as the verified rollback target.
3. Create and integrity-check a database backup.
4. Stage the exact verified candidate with the repository's canonical deployment mechanism.
5. Verify the staged candidate again.
6. Promote the staged identity atomically.
7. Restart only the required ThreadCells services.
8. Perform production acceptance on loopback or through the existing protected access path.

Do not overwrite the active directory in place. A release pointer/symlink or equivalent canonical mechanism should identify active, rollback, and staged candidates unambiguously.

## Acceptance

Check at least:

- health and Settings → About build identity;
- Home, Agents, Flows, Statistics, Settings, Docs, and Spawn Agent;
- provider inventory and one safe preflight;
- operator configured/locked/unlock/protected mutation behavior;
- global Telegram safe configuration state and, only when native credentials are already configured, explicit connection/test behavior;
- terminal connection and reconnect;
- workflow/result continuation;
- database integrity and no usage replay duplication;
- PWA manifest/icons/service-worker registration without dynamic caching.

## Rollback

Rollback switches to the preserved prior candidate and restarts only required services. Restore the database only when the new version performed an incompatible or damaging migration; an unnecessary database restore can discard valid work completed after promotion.

After rollback, verify build identity, health, schema compatibility, active workflows, and terminals. Retain the failed candidate and logs until the root cause is understood.

## Boundaries

Local deployment authority does not grant permission to publish packages, push a remote, create a tag/release, or expose a raw service port. Those remain separate owner decisions.
