# ThreadCells release media

The canonical public capture set comes from the real loopback production
instance. It preserves authentic session, agent, workflow, capacity, and host
scale while the capture tool removes local filesystem paths and replaces
Telegram destination and credential fields with explicit public-redaction
labels.

The capture tool refuses non-loopback origins and fails if the rendered DOM
contains credential-shaped values, private workflow/Inbox copy, or common
private host paths.

Generate the complete screenshot set and WebM tour:

~~~bash
cao-heavy-run node launch-media/capture-product.mjs
~~~

The default source is `http://127.0.0.1:9889`. A different loopback listener
may be selected explicitly:

~~~bash
THREADCELLS_LIVE_URL=http://127.0.0.1:4173 \
cao-heavy-run node launch-media/capture-product.mjs
~~~

To refresh only selected surfaces, set a comma-separated capture set. Valid
values are `home`, `session`, `agents`, `housekeeping`, `telegram`, `capacity`,
and `demo`.

~~~bash
THREADCELLS_CAPTURE_SET=home,session,housekeeping \
cao-heavy-run node launch-media/capture-product.mjs
~~~

PNG screenshot masters and the WebM master live under `launch-media/output/`.
The script writes optimized WebP screenshot derivatives directly to
`website/public/media/screenshots/` and the runtime Docs asset directory.

Generate the compressed MP4 fallback and synchronize both video formats to the
website:

~~~bash
cao-heavy-run bash launch-media/encode-demo.sh
~~~

Before committing captures, inspect every frame/screenshot for accidental
private content and confirm that real operational metrics remain truthful.
