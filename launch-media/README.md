# ThreadCells launch media

Product captures use an isolated in-memory fixture and the real `web/` application. The fixture contains only synthetic project, session, terminal, provider, and capacity data.

`output/screenshots/threadcells-home.png` is the owner-authorized isolated synthetic product image. It is the master for `website/public/media/screenshots/threadcells-home.webp`; the normal synthetic capture run preserves it.

`capture-product.mjs` derives the rendered build identity from the selected product worktree. Point `THREADCELLS_PRODUCT_ROOT` at the exact accepted production source revision when launch media is reconciled from a separate website worktree.

Regenerate the four synthetic product screenshots and the short WebM master from the repository root:

```bash
cao-heavy-run -- node launch-media/capture-product.mjs
```

To refresh only surfaces changed by a later product contour, provide a comma-separated selection. For example:

```bash
THREADCELLS_PRODUCT_ROOT=/path/to/threadcells \
THREADCELLS_CAPTURE_SET=agents,demo \
cao-heavy-run -- node launch-media/capture-product.mjs
```

Valid selections are `home`, `agents`, `capacity`, `docs`, `spawn`, and `demo`. Unselected masters and website derivatives remain byte-for-byte unchanged.

Replacing the owner-authoritative Home master is deliberately opt-in. Only do this when a newer owner-approved image supersedes it:

```bash
THREADCELLS_CAPTURE_SET=home \
THREADCELLS_ALLOW_HOME_REPLACE=1 \
cao-heavy-run -- node launch-media/capture-product.mjs
```

Generate MP4 and README GIF derivatives when `ffmpeg` is installed:

```bash
cao-heavy-run -- bash launch-media/encode-demo.sh
```

The website consumes optimized WebP copies from `website/public/media/screenshots/`. PNG masters and video derivatives live under `launch-media/output/`.
