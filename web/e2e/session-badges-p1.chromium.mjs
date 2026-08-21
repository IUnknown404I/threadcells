import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import http from 'node:http'
import { fileURLToPath } from 'node:url'
import { createServer as createViteServer } from 'vite'
import { chromium } from 'playwright'

const webRoot = fileURLToPath(new URL('..', import.meta.url))
const evidenceDir = process.env.CAO_VISUAL_EVIDENCE_DIR || '/tmp/cao-ui-session-badges-p1'
const fewSession = { id: 'few-badges', name: 'cao-few-badges', status: 'detached', created_at: '1' }
const manySession = { id: 'many-badges', name: 'cao-many-badges', status: 'active', created_at: '2' }
const makeTerminal = (session, index) => ({
  id: `${session.id}-terminal-${index}`,
  tmux_session: session.name,
  tmux_window: String(index),
  provider: 'codex',
  agent_profile: 'developer_terra_high',
  last_active: null,
})
const terminals = {
  [fewSession.name]: [makeTerminal(fewSession, 0)],
  [manySession.name]: Array.from({ length: 72 }, (_, index) => makeTerminal(manySession, index)),
}
const runtimeStatus = terminalId => {
  if (terminalId === `${fewSession.id}-terminal-0`) return { status: 'idle', workflow_state: 'completed' }
  if (terminalId === `${manySession.id}-terminal-0`) return { status: 'processing', workflow_state: 'active' }
  if (terminalId === `${manySession.id}-terminal-71`) return { status: 'idle', workflow_state: 'failed' }
  const index = Number(terminalId.split('-').at(-1))
  return index % 2 === 0
    ? { status: 'idle', workflow_state: 'waiting' }
    : { status: 'idle', workflow_state: 'owner_gate' }
}
const runtimeBranding = { title: 'ThreadCells', subtitle: 'Multi-agent control plane', logoUrl: '/threadcells-symbol.png', customLogo: false }

await mkdir(evidenceDir, { recursive: true })
const vite = await createViteServer({
  root: webRoot,
  configFile: false,
  plugins: [(await import('@vitejs/plugin-react')).default()],
  define: { __THREADCELLS_REVISION__: JSON.stringify('session-badges-evidence'), __THREADCELLS_VERSION__: JSON.stringify('0.1.0-alpha.1') },
  appType: 'spa',
  server: { middlewareMode: true, hmr: false },
})
const json = (response, value) => { response.writeHead(200, { 'content-type': 'application/json' }); response.end(JSON.stringify(value)) }
const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://localhost')
  if (request.method === 'GET' && url.pathname === '/sessions') return json(response, [fewSession, manySession])
  const matchedSession = [fewSession, manySession].find(session => url.pathname === `/sessions/${session.name}`)
  if (request.method === 'GET' && matchedSession) return json(response, { session: matchedSession, terminals: terminals[matchedSession.name] })
  if (request.method === 'GET' && url.pathname.startsWith('/terminals/')) {
    const terminalId = url.pathname.split('/')[2]
    return json(response, { id: terminalId, provider: 'codex', lifecycle: 'running', last_active: null, ...runtimeStatus(terminalId) })
  }
  if (request.method === 'GET' && url.pathname === '/agents/profiles') return json(response, [])
  if (request.method === 'GET' && url.pathname === '/settings/branding') return json(response, runtimeBranding)
  vite.middlewares(request, response)
})

const assertNoClippedBadges = async badges => {
  const result = await badges.evaluate(element => {
    const bounds = element.getBoundingClientRect()
    return Array.from(element.children).map(child => {
      const rect = child.getBoundingClientRect()
      return rect.bottom <= bounds.bottom + 1 || rect.top >= bounds.bottom - 1
    })
  })
  assert(result.every(Boolean), 'a status badge was partially clipped')
}

const terminalIds = badges => badges.locator('[data-terminal-id]').evaluateAll(elements => elements.map(element => element.dataset.terminalId))

await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const address = server.address()
assert(address && typeof address !== 'string')
const origin = `http://127.0.0.1:${address.port}`
let browser
try {
  browser = await chromium.launch({ headless: true })
  const page = await browser.newPage()
  await page.addInitScript(() => {
    const original = window.setInterval
    window.setInterval = (handler, timeout, ...args) => original(handler, timeout === 5000 ? 80 : timeout === 3000 ? 60 : timeout, ...args)
  })
  await page.goto(origin)
  const fewCard = page.getByTestId(`session-header-${fewSession.id}`).locator('..')
  const manyCard = page.getByTestId(`session-header-${manySession.id}`).locator('..')
  const fewSummary = fewCard.getByLabel('Session status')
  const manySummary = manyCard.getByLabel('Session status')
  const fewGroups = page.getByTestId(`session-status-groups-${fewSession.id}`)
  const manyGroups = page.getByTestId(`session-status-groups-${manySession.id}`)
  const fewFirst = page.getByTestId(`session-status-first-${fewSession.id}`)
  const fewLast = page.getByTestId(`session-status-last-${fewSession.id}`)
  const fewTotal = page.getByTestId(`session-status-total-${fewSession.id}`)
  const manyFirst = page.getByTestId(`session-status-first-${manySession.id}`)
  const manyLast = page.getByTestId(`session-status-last-${manySession.id}`)
  const manyBadges = page.getByTestId(`session-status-badges-${manySession.id}`)

  await fewCard.waitFor()
  await manyCard.waitFor()
  await fewFirst.getByText('Completed').waitFor()
  await manyFirst.getByText('In progress / Active').waitFor()
  await manyLast.getByText('Failed').waitFor()
  for (const width of [1440, 834, 390]) {
    await page.setViewportSize({ width, height: 960 })
    const showCollapsed = manySummary.getByRole('button', { name: /^Show \d+ collapsed agents$/ })
    const compactSummary = page.getByTestId(`session-status-badge-summary-${manySession.id}`)
    await showCollapsed.waitFor()
    assert.equal(await fewSummary.getByRole('button', { name: /^Show \d+ collapsed agents$/ }).count(), 0, `few badges unexpectedly toggled at ${width}px`)
    assert.deepEqual(await terminalIds(fewFirst), [`${fewSession.id}-terminal-0`], `one-agent First status changed at ${width}px`)
    assert.deepEqual(await terminalIds(fewLast), [`${fewSession.id}-terminal-0`], `one-agent Last status changed at ${width}px`)
    assert.deepEqual(await terminalIds(fewTotal), [`${fewSession.id}-terminal-0`], `one-agent Total status changed at ${width}px`)
    assert.deepEqual(await terminalIds(manyFirst), [`${manySession.id}-terminal-0`], `mixed-status First agent changed at ${width}px`)
    assert.deepEqual(await terminalIds(manyLast), [`${manySession.id}-terminal-71`], `mixed-status Last agent changed at ${width}px`)
    assert.equal(await showCollapsed.getAttribute('aria-expanded'), 'false', `many badges were not collapsed at ${width}px`)
    const hiddenMatch = (await showCollapsed.textContent()).match(/^Show (\d+) collapsed agents$/)
    assert(hiddenMatch, `missing hidden-agent count at ${width}px`)
    const hiddenCount = Number(hiddenMatch[1])
    const collapsedIds = [...await terminalIds(manyBadges), ...await terminalIds(compactSummary)]
    assert.equal(hiddenCount, manySession ? terminals[manySession.name].length - collapsedIds.length + 1 : 0, `hidden-agent count mismatch at ${width}px`)
    assert.equal(collapsedIds.at(-1), `${manySession.id}-terminal-71`, `final agent badge missing at ${width}px`)
    assert.equal(new Set(collapsedIds).size, collapsedIds.length, `final agent badge duplicated at ${width}px`)
    await assertNoClippedBadges(manyBadges)
    await assertNoClippedBadges(compactSummary)
    for (const [groupName, groups] of [['few', fewGroups], ['many', manyGroups]]) {
      const geometry = await groups.evaluate(element => ({
        overflow: element.scrollWidth - element.clientWidth,
        flexWrap: getComputedStyle(element).flexWrap,
        rowCount: new Set(Array.from(element.children).map(child => Math.round(child.getBoundingClientRect().top))).size,
        children: Array.from(element.children).map(child => ({
          testId: child.getAttribute('data-testid'),
          clientWidth: child.clientWidth,
          scrollWidth: child.scrollWidth,
          width: child.getBoundingClientRect().width,
        })),
      }))
      assert(geometry.overflow <= 1, `${groupName} semantic status groups overflowed horizontally at ${width}px: ${JSON.stringify(geometry)}`)
      assert.equal(geometry.flexWrap, 'wrap', `semantic status groups did not flex-wrap at ${width}px`)
      if (width === 390) assert(geometry.rowCount > 1, 'semantic status groups did not wrap at the narrow viewport')
    }
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), 0, `horizontal overflow at ${width}px`)
    await fewCard.screenshot({ path: `${evidenceDir}/${width}-few-no-toggle.png` })
    await manyCard.screenshot({ path: `${evidenceDir}/${width}-many-collapsed.png` })

    await showCollapsed.click()
    const hide = manySummary.getByRole('button', { name: '< Hide rows' })
    assert.equal(await hide.getAttribute('aria-expanded'), 'true', `many badges did not expand at ${width}px`)
    assert.deepEqual(await terminalIds(manyBadges), terminals[manySession.name].map(terminal => terminal.id), `expanded agents lost canonical order at ${width}px`)
    await manyCard.screenshot({ path: `${evidenceDir}/${width}-many-expanded.png` })

    await hide.click()
    await manySummary.getByRole('button', { name: /^Show \d+ collapsed agents$/ }).waitFor()
    await assertNoClippedBadges(manyBadges)
    await manyCard.screenshot({ path: `${evidenceDir}/${width}-many-hidden-again.png` })
  }
  console.log(JSON.stringify({ evidenceDir, widths: [1440, 834, 390], assertions: ['one-agent sessions repeat First, Last, and Total intentionally', 'mixed statuses use canonical first/last agent order', 'many sessions keep the existing two-row Total plus compact summary', 'hidden count and final Total badge are accurate', 'expand and hide preserve canonical Total order', 'semantic groups wrap without partial badge clipping or horizontal overflow'] }))
} finally {
  await browser?.close()
  await new Promise(resolve => server.close(resolve))
  await vite.close()
}
