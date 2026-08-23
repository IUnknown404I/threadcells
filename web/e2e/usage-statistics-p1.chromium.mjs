import assert from 'node:assert/strict'
import { mkdtemp } from 'node:fs/promises'
import http from 'node:http'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createServer as createViteServer } from 'vite'
import { chromium } from 'playwright'

const webRoot = fileURLToPath(new URL('..', import.meta.url))
const screenshotDir = await mkdtemp(path.join(os.tmpdir(), 'cao-usage-statistics-p1-'))
const runtimeBranding = { title: 'ThreadCells', subtitle: 'Multi-agent control plane', logoUrl: '/threadcells-symbol.png', customLogo: false }
const aggregate = (id, total) => ({ id, provider_run_count: 1, input_tokens: total === null ? null : total - 4, cached_input_tokens: null, cache_write_input_tokens: 99, output_tokens: total === null ? null : 4, reasoning_output_tokens: total === null ? null : 2, total_tokens: total })
const statistics = {
  label: 'Provider-reported usage — not a billing statement',
  global: { provider_run_count: 12, input_tokens: 1200, cached_input_tokens: 120, cache_write_input_tokens: 0, output_tokens: 300, reasoning_output_tokens: 40, total_tokens: 1500 },
  terminals: Array.from({ length: 10 }, (_, index) => aggregate(`terminal-${index + 1}`, 500 - index * 10)),
  sessions: [
    ...Array.from({ length: 9 }, (_, index) => aggregate(`session-${index + 1}`, 400 - index * 10)),
    { ...aggregate('legacy-session-record:42', 305), label: 'cao-reused', legacy: true },
  ],
  projects: [
    { ...aggregate('project-active', 300), label: 'Active Project' },
    { ...aggregate('project-historical-removed', null), label: 'Unknown project' },
  ],
  providers: [{ ...aggregate('codex', 1500), label: 'codex' }],
  profiles: [{ ...aggregate('developer', 1500), label: 'developer' }],
}

const vite = await createViteServer({ root: webRoot, configFile: false, plugins: [(await import('@vitejs/plugin-react')).default()], define: { __THREADCELLS_REVISION__: JSON.stringify('usage-statistics-evidence'), __THREADCELLS_VERSION__: JSON.stringify('0.1.0-alpha.2') }, appType: 'spa', server: { middlewareMode: true, hmr: false } })
function json(response, value) { response.writeHead(200, { 'content-type': 'application/json' }); response.end(JSON.stringify(value)) }
const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://localhost')
  if (request.method === 'GET' && url.pathname === '/sessions') return json(response, [])
  if (request.method === 'GET' && url.pathname === '/settings/branding') return json(response, runtimeBranding)
  if (request.method === 'GET' && url.pathname === '/usage/statistics') return json(response, statistics)
  vite.middlewares(request, response)
})

await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const address = server.address()
assert(address && typeof address !== 'string')
const origin = `http://127.0.0.1:${address.port}`
let browser
try {
  browser = await chromium.launch({ headless: true })
  const page = await browser.newPage()
  const pageErrors = []
  page.on('pageerror', error => pageErrors.push(error.message))
  await page.goto(origin)
  const statisticsLink = page.getByRole('link', { name: 'Statistics' })
  try {
    await statisticsLink.click()
  } catch (error) {
    throw new Error(`Statistics navigation unavailable: ${JSON.stringify({ pageErrors, body: (await page.locator('body').innerText()).slice(0, 500) })}`, { cause: error })
  }
  await page.getByText('Unknown project').waitFor()
  await page.getByText('By project').scrollIntoViewIfNeeded()
  for (const width of [1440, 834, 390]) {
    await page.setViewportSize({ width, height: 900 })
    await page.evaluate(() => window.scrollTo(0, 0))
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), 0, `Statistics must not create page overflow at ${width}px`)
    await page.screenshot({ path: path.join(screenshotDir, `statistics-${width}.png`), fullPage: true })
  }
  assert.equal(await page.getByText('Scope').count(), 0, 'global projection must not use a pseudo-scope')
  assert.equal(await page.getByText('Cache write').count(), 0, 'unsupported cache-write telemetry must stay out of default tables')
  for (const metric of ['Reports', 'Input', 'Cached input', 'Output', 'Reasoning', 'Total']) {
    assert.equal(await page.getByRole('columnheader', { name: metric, exact: true }).count(), 6, `${metric} must remain in all six tables`)
  }
  assert(await page.locator('section').nth(1).locator('.overflow-x-auto').evaluate(node => node.scrollWidth > node.clientWidth), 'detail tables must remain horizontally scrollable on mobile')
  assert.equal(await page.getByText('Unknown project').count(), 1, 'historical project fallback must be visible')
  assert.equal(await page.getByText('Legacy session: reused').count(), 1, 'unreconciled legacy session remains visibly separate')
  assert.equal(await page.getByText('Showing top 10 by total where reported.').count(), 5)
  console.log(JSON.stringify({ screenshotDir, screenshots: [1440, 834, 390].map(width => path.join(screenshotDir, `statistics-${width}.png`)) }))
} finally {
  await browser?.close()
  await new Promise(resolve => server.close(resolve))
  await vite.close()
}
