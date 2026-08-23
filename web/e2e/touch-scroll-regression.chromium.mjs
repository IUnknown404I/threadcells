import assert from 'node:assert/strict'
import http from 'node:http'
import { fileURLToPath } from 'node:url'
import { createServer as createViteServer } from 'vite'
import react from '@vitejs/plugin-react'
import { chromium } from 'playwright'

const sourceRoot = fileURLToPath(new URL('..', import.meta.url))
const webRoot = process.env.THREADCELLS_TOUCH_WEB_ROOT || sourceRoot
const servingBuiltCandidate = webRoot !== sourceRoot
const branding = { title: 'ThreadCells', subtitle: 'Multi-agent control plane', logoUrl: '/threadcells-symbol.png', customLogo: false }
const capacity = { resource_state: 'GREEN', reasons: [], resident_supervisors: { active: 1, limit: 5, available: 4, certain: true }, provider_executions: { active: 0, limit: 3, available: 3, certain: true }, work_contexts: { active: 0, limit: 2, available: 2, certain: true }, heavy_executions: { active: 0, limit: 1, available: 1, waiting: null }, memory: { available_mib: 1024, swap_total_mib: 0, swap_free_mib: 0 }, root_disk: { used_percent: 1, free_gib: 100 }, memory_pressure: { some_avg10: 0, full_avg10: 0 }, cpu_load: { one_minute: 0, cpu_count: 1 }, housekeeping: { ok: true } }

const vite = await createViteServer({
  root: webRoot,
  configFile: false,
  plugins: servingBuiltCandidate ? [] : [react()],
  define: servingBuiltCandidate ? undefined : { __THREADCELLS_REVISION__: JSON.stringify('touch-scroll-regression'), __THREADCELLS_VERSION__: JSON.stringify('0.1.0-alpha.2') },
  appType: 'spa',
  server: { middlewareMode: true, hmr: false },
})

function json(response, value) {
  response.writeHead(200, { 'content-type': 'application/json' })
  response.end(JSON.stringify(value))
}

const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://localhost')
  if (request.method === 'GET' && url.pathname === '/sessions') return json(response, [])
  if (request.method === 'GET' && url.pathname === '/agents/providers') return json(response, [{ name: 'codex', binary: 'codex', installed: true }])
  if (request.method === 'GET' && url.pathname === '/agents/profiles') return json(response, [{ name: 'supervisor_terra_medium', description: 'Default orchestration', source: 'built-in' }])
  if (request.method === 'GET' && url.pathname === '/projects') return json(response, [])
  if (request.method === 'GET' && url.pathname === '/settings/branding') return json(response, branding)
  if (request.method === 'GET' && url.pathname === '/settings/agent-dirs') return json(response, { agent_dirs: {}, extra_dirs: [] })
  if (request.method === 'GET' && url.pathname === '/settings/orchestration-capacity') return json(response, capacity)
  if (request.method === 'GET' && url.pathname === '/api/v1/profiles') return json(response, [])
  if (request.method === 'GET' && url.pathname === '/api/v1/providers') return json(response, { api_version: '1.0', entry_point_group: 'threadcells.provider_adapters.v1', adapters: [], configurations: [], load_failures: [] })
  if (request.method === 'GET' && url.pathname === '/api/v1/housekeeping') return json(response, { schema_version: 1, policy: {}, schedule: {} })
  if (request.method === 'GET' && url.pathname === '/api/v1/housekeeping/report') return json(response, { status: 'never_run' })
  vite.middlewares(request, response)
})

await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const address = server.address()
assert(address && typeof address !== 'string')
const origin = `http://127.0.0.1:${address.port}`

const surfaces = [
  { name: 'Home', path: '/', active: 'Home' },
  { name: 'Agents', path: '/?tab=agents', active: 'Agents' },
  { name: 'Settings', path: '/settings', active: 'Settings' },
  { name: 'Docs', path: '/docs', active: 'Docs' },
]

const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds))

async function touchDrag(cdp, width, fromY, toY, resize) {
  const x = Math.round(width / 2)
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y: fromY, radiusX: 2, radiusY: 2, force: 1 }] })
  const steps = 6
  for (let step = 1; step <= steps; step += 1) {
    if (resize && step === 2) await resize()
    const y = Math.round(fromY + ((toY - fromY) * step / steps))
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x, y, radiusX: 2, radiusY: 2, force: 1 }] })
    await delay(18)
  }
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
  await delay(80)
}

async function exerciseSurface(browser, surface, viewport) {
  const context = await browser.newContext({ viewport, hasTouch: true, isMobile: true, deviceScaleFactor: 1 })
  const page = await context.newPage()
  const cdp = await context.newCDPSession(page)
  try {
    await page.goto(`${origin}${surface.path}`, { waitUntil: 'networkidle' })
    await page.getByTestId('primary-navigation-rail').getByRole('link', { name: surface.active, exact: true }).waitFor()
    await page.evaluate(() => {
      const spacer = document.createElement('div')
      spacer.dataset.touchScrollFixture = 'true'
      spacer.style.height = '3200px'
      spacer.style.pointerEvents = 'none'
      document.querySelector('main')?.append(spacer)
      window.scrollTo(0, 700)
    })
    await page.waitForFunction(() => window.scrollY >= 650)

    const initial = await page.evaluate(() => window.scrollY)
    await touchDrag(cdp, viewport.width, Math.round(viewport.height * 0.75), Math.round(viewport.height * 0.48))
    const afterDown = await page.evaluate(() => window.scrollY)
    assert(afterDown > initial + 80, `${surface.name} ${viewport.width}: first downward-page touch did not scroll: ${initial} -> ${afterDown}`)

    let resizedHeight = viewport.height
    await touchDrag(
      cdp,
      viewport.width,
      Math.round(viewport.height * 0.48),
      Math.round(viewport.height * 0.55),
      async () => {
        resizedHeight = viewport.height - 28
        await cdp.send('Emulation.setDeviceMetricsOverride', {
          width: viewport.width,
          height: resizedHeight,
          deviceScaleFactor: 1,
          mobile: true,
          screenWidth: viewport.width,
          screenHeight: viewport.height,
        })
      },
    )
    const afterUp = await page.evaluate(() => window.scrollY)
    assert(afterUp > 200, `${surface.name} ${viewport.width}: viewport resize during reverse touch jumped near the document top: ${afterDown} -> ${afterUp}`)

    await touchDrag(cdp, viewport.width, Math.round(resizedHeight * 0.75), Math.round(resizedHeight * 0.48))
    const afterSecondDown = await page.evaluate(() => window.scrollY)
    assert(afterSecondDown > afterUp + 80, `${surface.name} ${viewport.width}: subsequent downward-page touch was resisted: ${afterUp} -> ${afterSecondDown}`)
    await delay(650)
    const settled = await page.evaluate(() => window.scrollY)
    assert(settled > 200, `${surface.name} ${viewport.width}: delayed snap-back reached the document top: ${afterSecondDown} -> ${settled}`)

    await page.mouse.wheel(0, 240)
    await delay(80)
    const afterWheel = await page.evaluate(() => window.scrollY)
    assert(afterWheel > settled, `${surface.name} ${viewport.width}: mouse wheel scrolling did not remain native: ${settled} -> ${afterWheel}`)

    let afterOverlayRecovery = null
    if (surface.name === 'Settings') {
      await page.getByRole('button', { name: 'Configure', exact: true }).click()
      await page.getByRole('dialog', { name: 'Configure orchestration capacity' }).waitFor()
      await page.getByRole('button', { name: 'Close capacity configuration' }).click()
      const beforeRecoveryTouch = await page.evaluate(() => window.scrollY)
      await touchDrag(cdp, viewport.width, Math.round(resizedHeight * 0.75), Math.round(resizedHeight * 0.48))
      afterOverlayRecovery = await page.evaluate(() => window.scrollY)
      assert(afterOverlayRecovery > beforeRecoveryTouch + 80, `${surface.name} ${viewport.width}: closing the modal left document touch scrolling locked: ${beforeRecoveryTouch} -> ${afterOverlayRecovery}`)
    }
    if (surface.name === 'Docs' && viewport.width === 390) {
      await page.getByRole('button', { name: 'Browse docs' }).click()
      await page.getByRole('button', { name: 'Close documentation navigation' }).click()
      const beforeRecoveryTouch = await page.evaluate(() => window.scrollY)
      await touchDrag(cdp, viewport.width, Math.round(resizedHeight * 0.75), Math.round(resizedHeight * 0.48))
      afterOverlayRecovery = await page.evaluate(() => window.scrollY)
      assert(afterOverlayRecovery > beforeRecoveryTouch + 80, `${surface.name} ${viewport.width}: closing the drawer left document touch scrolling locked: ${beforeRecoveryTouch} -> ${afterOverlayRecovery}`)
    }

    return { surface: surface.name, viewport, initial, afterDown, afterUp, afterSecondDown, settled, afterWheel, afterOverlayRecovery }
  } finally {
    await context.close()
  }
}

let browser
try {
  browser = await chromium.launch({ headless: true })
  const evidence = []
  for (const viewport of [{ width: 390, height: 844 }, { width: 834, height: 1112 }]) {
    for (const surface of surfaces) evidence.push(await exerciseSurface(browser, surface, viewport))
  }
  console.log(JSON.stringify({ touch: true, servingBuiltCandidate, webRoot, sequence: 'down -> up with mobile viewport resize -> down -> settle -> wheel', evidence }))
} finally {
  await browser?.close()
  await new Promise(resolve => server.close(resolve))
  await vite.close()
}
