# ThreadCells security policy

## Preview boundary

ThreadCells is a technical preview for one trusted Linux host. Keep it bound to loopback and use an SSH tunnel for remote access. Native coding agents can execute powerful commands. Profiles, worktrees, and local controls are operational safeguards, not a security sandbox or a hostile multi-tenant boundary.

The ordinary Web UI and Docs reader do not implement user authentication or authorization. Do not expose either beyond loopback. Designing an authorization boundary, configuring a reverse proxy, or enabling non-loopback access requires an explicit owner decision.

## Report vulnerabilities privately

Do not file vulnerabilities or security-sensitive reports as ordinary public Issues. Use GitHub's [private vulnerability reporting form](https://github.com/IUnknown404I/threadcells/security/advisories/new) for this repository.

Include only the information needed to understand and reproduce the problem. Do not put credentials, private infrastructure details, operational secrets, personal data, state databases, or unrelated exploit material in public comments, pull requests, screenshots, or logs. If a credential may have been disclosed, rotate it through its owning service rather than attaching it to a report.

## Evidence and review

The repository retains Apache-2.0 license and attribution evidence in [LICENSE](LICENSE), [NOTICE](NOTICE), and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Local candidates include a manifest, SHA-256 checksums, a direct-dependency SBOM, and evidence notes. These files support review but do not certify vulnerability status, licensing clearance, or security approval.
