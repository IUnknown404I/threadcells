import assert from 'node:assert/strict'
import { mkdtemp, readFile, stat } from 'node:fs/promises'
import http from 'node:http'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const webRoot = fileURLToPath(new URL('..', import.meta.url))
const distRoot = path.resolve(webRoot, '../src/cli_agent_orchestrator/web_ui')
const screenshotDir = await mkdtemp(path.join(os.tmpdir(), 'threadcells-docs-pwa-'))
const docsBundle = JSON.parse(await readFile(path.join(distRoot, 'docs-bundle.json'), 'utf8'))
const contentTypes = new Map([
  ['.css', 'text/css'], ['.html', 'text/html'], ['.ico', 'image/x-icon'],
  ['.js', 'text/javascript'], ['.json', 'application/json'],
  ['.png', 'image/png'], ['.webmanifest', 'application/manifest+json'],
])
let probeCount = 0

function sendJson(response, value) {
  response.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' })
  response.end(JSON.stringify(value))
}

const server = http.createServer(async (request, response) => {
  const url = new URL(request.url, 'http://localhost')
  if (url.pathname === '/sessions') return sendJson(response, [])
  if (url.pathname === '/settings/branding') return sendJson(response, { title: 'ThreadCells', subtitle: 'Multi-agent control plane', logoUrl: '/threadcells-symbol.png', customLogo: false })
  if (url.pathname === '/api/pwa-probe' || url.pathname === '/operator/session') return sendJson(response, { sequence: ++probeCount })

  const relative = url.pathname === '/' ? 'index.html' : url.pathname.replace(/^\//, '')
  let candidate = path.resolve(distRoot, relative)
  if (!candidate.startsWith(`${distRoot}${path.sep}`) && candidate !== path.join(distRoot, 'index.html')) {
    response.writeHead(400); response.end(); return
  }
  try {
    if (!(await stat(candidate)).isFile()) throw new Error('not a file')
  } catch {
    candidate = path.join(distRoot, 'index.html')
  }
  const body = await readFile(candidate)
  response.writeHead(200, {
    'content-type': contentTypes.get(path.extname(candidate)) || 'application/octet-stream',
    'cache-control': candidate.endsWith('index.html') ? 'no-store' : 'public, max-age=60',
  })
  response.end(body)
})

await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const address = server.address()
assert(address && typeof address !== 'string')
const origin = `http://127.0.0.1:${address.port}`
const viewports = [{ width: 1440, height: 900 }, { width: 834, height: 1112 }, { width: 390, height: 844 }]
let browser
try {
  browser = await chromium.launch({ headless: true })
  const desktop = await browser.newContext({ viewport: viewports[0] })
  const page = await desktop.newPage()
  const pageErrors = []
  page.on('pageerror', error => pageErrors.push(error.message))
  await page.goto(`${origin}/docs/overview`)
  await page.getByRole('heading', { name: 'Start here: What is ThreadCells?' }).waitFor()

  const cdp = await desktop.newCDPSession(page)
  await cdp.send('Page.enable')
  const appManifest = await cdp.send('Page.getAppManifest')
  assert.equal(appManifest.url, `${origin}/manifest.webmanifest`)
  assert.equal(appManifest.errors.length, 0, JSON.stringify(appManifest.errors))
  const installability = await cdp.send('Page.getInstallabilityErrors')
  assert.deepEqual(installability.installabilityErrors, [])

  const metadata = await page.evaluate(async () => {
    const manifest = await fetch('/manifest.webmanifest').then(response => response.json())
    const registration = await navigator.serviceWorker.ready
    return {
      manifest,
      worker: registration.active?.scriptURL,
      appleCapable: document.querySelector('meta[name="apple-mobile-web-app-capable"]')?.getAttribute('content'),
      appleIcon: document.querySelector('link[rel="apple-touch-icon"]')?.getAttribute('href'),
    }
  })
  assert.equal(metadata.worker, `${origin}/sw.js`)
  assert.equal(metadata.manifest.display, 'standalone')
  assert.equal(metadata.manifest.start_url, '/')
  assert.equal(metadata.manifest.scope, '/')
  assert.equal(metadata.appleCapable, 'yes')
  assert.equal(metadata.appleIcon, '/apple-touch-icon.png')

  await page.reload()
  await page.getByRole('heading', { name: 'Start here: What is ThreadCells?' }).waitFor()
  await page.waitForFunction(() => Boolean(navigator.serviceWorker.controller))
  const dynamic = await page.evaluate(async () => [
    await fetch('/api/pwa-probe').then(response => response.json()),
    await fetch('/api/pwa-probe').then(response => response.json()),
    await fetch('/operator/session').then(response => response.json()),
  ])
  assert.equal(dynamic[1].sequence, dynamic[0].sequence + 1, 'API responses must come from the network')
  assert.equal(dynamic[2].sequence, dynamic[1].sequence + 1, 'operator state must come from the network')
  const cachedRequests = await page.evaluate(async () => {
    const keys = await caches.keys()
    const requests = []
    for (const key of keys) {
      for (const request of await (await caches.open(key)).keys()) requests.push(new URL(request.url).pathname)
    }
    return requests
  })
  assert(cachedRequests.length > 0, 'fingerprinted static assets should be cached after a controlled reload')
  assert(cachedRequests.every(value => value.startsWith('/assets/')), JSON.stringify(cachedRequests))

  for (const document of docsBundle.documents) {
    await page.goto(`${origin}/docs/${document.slug}`)
    await page.locator('article h1').first().waitFor()
    assert.equal(await page.getByText('Document not found', { exact: true }).count(), 0, `route /docs/${document.slug}`)
  }

  for (const viewport of viewports) {
    await page.setViewportSize(viewport)
    await page.goto(`${origin}/docs/${viewport.width === 390 ? 'remote-access' : 'concepts'}`)
    await page.locator('article').waitFor()
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    assert(overflow <= 0, `Docs overflow at ${viewport.width}px: ${overflow}`)
    await page.screenshot({ path: path.join(screenshotDir, `docs-${viewport.width}.png`), fullPage: true })
  }
  assert.deepEqual(pageErrors, [])
  await desktop.close()

  const mobile = await browser.newContext({ viewport: viewports[2], isMobile: true, hasTouch: true, userAgent: 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/126 Mobile Safari/537.36' })
  const mobilePage = await mobile.newPage()
  await mobilePage.goto(`${origin}/docs/first-agent`)
  await mobilePage.getByRole('heading', { name: 'Your first project and agent' }).waitFor()
  assert.equal(await mobilePage.getByRole('button', { name: 'Browse docs' }).count(), 1)
  await mobile.close()

  console.log(JSON.stringify({ screenshotDir, screenshots: viewports.map(({ width }) => path.join(screenshotDir, `docs-${width}.png`)), docRoutes: docsBundle.documents.length, cachedRequests, installabilityErrors: installability.installabilityErrors, assertions: ['all allowlisted Docs routes', 'manifest and 192/512/maskable icons', 'production service worker', 'dynamic requests network-only', 'fingerprinted assets only', 'Apple metadata', 'desktop and mobile Docs'] }))
} finally {
  await browser?.close()
  await new Promise(resolve => server.close(resolve))
}
