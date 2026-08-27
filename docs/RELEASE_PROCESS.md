# Release process

Build an isolated local candidate from a clean committed tree with `scripts/build_local_candidate.py --output <new-directory>`. It packages the generated Docs/UI and a local wheel. Verify `SHA256SUMS`, inspect `candidate-manifest.json`, `sbom.cdx.json`, and `EVIDENCE.md`, then perform the documented clean install using a new prefix. Publishing a tag, remote branch, package, image, or public release is never an ordinary implementation action.

## Release checklist

1. Finish implementation and one independent integrated review.
2. Run focused tests plus one meaningful production build/browser contour.
3. Run `git diff --check` and the public-surface audit.
4. Commit the exact accepted tree.
5. Build the candidate from that commit, never from an uncommitted worktree.
6. Verify manifest, checksums, SBOM, build identity, Docs routes, and clean install.
7. Preserve the previous runtime and a database backup before local promotion.
8. Treat any public push, tag, package, image, or release as a separate owner-approved action.

Release evidence proves what was tested and packaged; it does not itself approve publication or certify every dependency license/security property.

## OCI release distribution

Approved published alpha releases also have a public OCI distribution artifact at `ghcr.io/iunknown404i/threadcells-release-bundle`. It contains the verified release archive, Python wheel, checksum inventories, candidate manifest, SBOM, and release-bundle metadata for one exact release tag and source revision.

This package is a distribution bundle, not a Docker image or a supported container deployment environment. Use the normal candidate installation and deployment process after verifying its checksums; do not try to run the OCI artifact as a ThreadCells service.

`.github/workflows/publish-release-bundle.yml` publishes on an approved GitHub Release or by an explicit backfill dispatch. It accepts annotated `v0.X.Y-alpha` tags with an existing non-draft prerelease, retains compatibility for immutable historical `v0.X.Y-alpha.N` tags, rebuilds and verifies the exact tagged source, refuses to replace a mismatched version tag, and updates only `latest-alpha`. ThreadCells does not publish an unqualified `latest` tag during the technical preview.

## Version-line convention

ThreadCells follows normal SemVer prerelease ordering. During the alpha preview, every new publication advances the normal semantic version and keeps `alpha` as the prerelease stage. Python packaging normalizes an unsuffixed alpha such as `v0.3.3-alpha` to `0.3.3a0`.

- `v0.1.0-alpha.1` was the first public alpha.
- `v0.1.0-alpha.2` is an immutable published technical preview.
- `v0.2.0-alpha.1` is the consolidated multilingual and reliability release line.
- `v0.3.0-alpha.1` adds lifecycle consistency, durable creation order, Full Cleanup, and systemic routing policy.
- `v0.3.0-alpha.2` corrects Workflow Composer delivery and makes terminal exit final for executable workflow authority.
- `v0.3.3-alpha` adds English/Russian authenticated UI localization and one canonical locale-owned Docs corpus for both the app and public site.
- A later alpha publication increments the semantic version deliberately and keeps the unsuffixed `alpha` stage.

Never move an existing tag. Repository-governance changes alone do not trigger a version bump or release. Update all canonical version-bearing surfaces together only when the next meaningful implementation contour is ready for publication.
