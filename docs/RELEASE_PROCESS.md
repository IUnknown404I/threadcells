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

`.github/workflows/publish-release-bundle.yml` publishes on an approved GitHub Release or by an explicit backfill dispatch. It accepts only annotated `v0.1.X-alpha.N` tags with an existing non-draft prerelease, rebuilds and verifies the exact tagged source, refuses to replace a mismatched version tag, and updates only `latest-alpha`. ThreadCells does not publish an unqualified `latest` tag during the technical preview.

## Version-line convention

ThreadCells follows normal SemVer prerelease ordering. During the alpha preview, `0.1.X` identifies a meaningful product, reliability, or documentation iteration; `alpha.N` identifies additional publications within that same iteration when they are genuinely required.

- `v0.1.0-alpha.1` was the first public alpha.
- `v0.1.0-alpha.2` is the immutable current published technical preview.
- The next meaningful release line is `v0.1.1-alpha.1`.
- Use `v0.1.1-alpha.2` only if another publication is required within the same `0.1.1` iteration; the next meaningful iteration after that is `v0.1.2-alpha.1`.

Do not continue the `v0.1.0-alpha.N` sequence or move an existing tag without explicit owner direction. Repository-governance changes alone do not trigger a version bump or release. Update all canonical version-bearing surfaces together only when the next meaningful implementation contour is ready for publication.
