# Release process

Build an isolated local candidate from a clean committed tree with `scripts/build_local_candidate.py --output <new-directory>`. It packages the generated Docs/UI and a local wheel. Verify `SHA256SUMS`, inspect `candidate-manifest.json`, `sbom.cdx.json`, and `EVIDENCE.md`, then perform the documented clean install using a new prefix. Publication automation is intentionally absent. Publishing a tag, remote branch, package, image, or public release is never an ordinary implementation action.

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
