# ThreadCells website

The public ThreadCells landing is a Next.js App Router site that exports to plain static files. It requires no production Node server, API, database, authentication, CMS, analytics, or ThreadCells runtime.

```bash
cd website
npm ci
npm run check
npm run test:links
npm run test:browser
npm run test:lighthouse
```

`npm run build` writes the deployable site to `website/out/`.

The public Docs pages are statically generated from the repository-root `docs/DOCS_MANIFEST.json` allowlist and its canonical Markdown sources. `THREADCELLS_PRODUCT_ROOT` may point a reconciliation build at another accepted ThreadCells worktree; no second website copy of article content exists.

`SITE_URL` is the externally reachable deployment root, including any repository Pages subpath. The current public authority is `https://iunknown404i.github.io/threadcells`; a future owner-approved custom domain can replace it without changing routes. The canonical repository is `https://github.com/IUnknown404I/threadcells`. Set `NEXT_PUBLIC_BASE_PATH=/threadcells` for repository Pages hosting; public Docs always remain part of this same static export.

Launch-media regeneration is documented in [`../launch-media/README.md`](../launch-media/README.md).

## GitHub Pages

The checked-in workflow deploys automatically from canonical `main` and also supports an explicit manual run. Before publication, the repository owner must:

1. Enable GitHub Pages with **GitHub Actions** as the source.
2. Confirm `actions/configure-pages` supplies `https://iunknown404i.github.io/threadcells` and `/threadcells`.
3. Push the accepted release to `main`, or run **Deploy ThreadCells website** from the Actions tab when an explicit redeploy is needed.

The workflow builds with the exact Pages base URL/path, uploads `website/out/`, and deploys through the protected `github-pages` environment.
