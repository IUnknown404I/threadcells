# ThreadCells security policy

## Preview boundary

ThreadCells is a technical preview for one trusted Linux host. Keep it bound to loopback and use an SSH tunnel for remote access. Native coding agents can execute powerful commands. Profiles, worktrees, and local controls are operational safeguards, not a security sandbox or a hostile multi-tenant boundary.

The ordinary web UI and its Docs reader do not implement user authentication or authorization. Do not expose either beyond loopback. Designing an authorization boundary, configuring a reverse proxy, or enabling non-loopback access requires an explicit owner decision.

## Reporting

There is no owner-approved public vulnerability-reporting contact for this preview. Do not post vulnerabilities, credentials, private paths, state databases, or terminal transcripts in public issues. A private reporting channel and response policy must be approved before publication.

## Evidence and review

The repository retains Apache-2.0 license and attribution evidence in [LICENSE](LICENSE), [NOTICE](NOTICE), and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Local candidates include a manifest, SHA-256 checksums, a direct-dependency SBOM, and evidence notes. These files support review but do not certify vulnerability status, licensing clearance, or security approval.
