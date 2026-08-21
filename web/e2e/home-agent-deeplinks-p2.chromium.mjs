import assert from 'node:assert/strict'
import http from 'node:http'
import { fileURLToPath } from 'node:url'
import { createServer as createViteServer } from 'vite'
import { chromium } from 'playwright'

const webRoot = fileURLToPath(new URL('..', import.meta.url))
const evidenceDir = process.env.THREADCELLS_VISUAL_EVIDENCE_DIR
const sessions = [{ id: 'filter-session', name: 'filter-session', status: 'active', created_at: '1' }]
const terminals = {
  'filter-session': [
    { id: 'agent-active', tmux_session: 'filter-session', tmux_window: '0', provider: 'codex', agent_profile: 'developer', last_active: null },
    { id: 'agent-owner', tmux_session: 'filter-session', tmux_window: '1', provider: 'codex', agent_profile: 'developer', last_active: null },
    { id: 'agent-waiting', tmux_session: 'filter-session', tmux_window: '2', provider: 'codex', agent_profile: 'reviewer', last_active: null },
    { id: 'agent-cancelled', tmux_session: 'filter-session', tmux_window: '3', provider: 'codex', agent_profile: 'reviewer', last_active: null },
    { id: 'agent-completed', tmux_session: 'filter-session', tmux_window: '4', provider: 'codex', agent_profile: 'reviewer', last_active: null },
    { id: 'agent-processing', tmux_session: 'filter-session', tmux_window: '5', provider: 'codex', agent_profile: 'developer', last_active: null },
  ],
}
const statuses = {
  'agent-active': { status: 'idle', lifecycle: 'running', workflow_state: 'active' },
  'agent-owner': { status: 'idle', lifecycle: 'running', workflow_state: 'owner_gate' },
  'agent-waiting': { status: 'idle', lifecycle: 'running', workflow_state: 'waiting' },
  'agent-cancelled': { status: 'idle', lifecycle: 'exited', workflow_state: 'cancelled' },
  'agent-completed': { status: 'completed', lifecycle: 'running', workflow_state: 'completed' },
  'agent-processing': { status: 'processing', lifecycle: 'running', workflow_state: 'active' },
}
const branding = { title: 'ThreadCells', subtitle: 'Multi-agent control plane', logoUrl: '/threadcells-symbol.png', customLogo: false }
const vite = await createViteServer({ root: webRoot, configFile: false, plugins: [(await import('@vitejs/plugin-react')).default()], define: { __THREADCELLS_REVISION__: JSON.stringify('synthetic-evidence'), __THREADCELLS_VERSION__: JSON.stringify('0.1.0-alpha.1') }, appType: 'spa', server: { middlewareMode: true, hmr: false } })
const json = (response, value) => { response.writeHead(200, { 'content-type': 'application/json' }); response.end(JSON.stringify(value)) }
const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://localhost')
  if (request.method === 'GET' && url.pathname === '/sessions') return json(response, sessions)
  if (request.method === 'GET' && url.pathname === '/sessions/filter-session') return json(response, { session: sessions[0], terminals: terminals['filter-session'] })
  if (request.method === 'GET' && url.pathname.startsWith('/terminals/')) {
    const id = url.pathname.split('/')[2]
    return json(response, { id, name: id, provider: 'codex', session_name: 'filter-session', agent_profile: null, last_active: null, ...statuses[id] })
  }
  if (request.method === 'GET' && url.pathname === '/agents/providers') return json(response, [{ name: 'codex', binary: 'codex', installed: true }])
  if (request.method === 'GET' && url.pathname === '/agents/profiles') return json(response, [{ name: 'developer', description: 'Builds', source: 'built-in' }, { name: 'reviewer', description: 'Reviews', source: 'local' }])
  if (request.method === 'GET' && url.pathname === '/projects') return json(response, [])
  if (request.method === 'GET' && url.pathname === '/settings/branding') return json(response, branding)
  vite.middlewares(request, response)
})

await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const address = server.address()
assert(address && typeof address !== 'string')
const origin = `http://127.0.0.1:${address.port}`
let browser
try {
  browser = await chromium.launch({ headless: true })
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  await page.goto(origin)
  await page.getByRole('button', { name: 'View active agents' }).waitFor()

  await page.getByRole('button', { name: 'Create Session & Spawn Agent' }).click()
  await page.getByRole('heading', { name: 'Create Session & Spawn Agent' }).waitFor()
  assert.equal(await page.getByRole('link', { name: 'Agents' }).getAttribute('aria-current'), 'page', 'Home create action must route to Agents')
  await page.getByRole('button', { name: 'Cancel' }).click()
  assert.equal(await page.getByRole('heading', { name: 'Create Session & Spawn Agent' }).count(), 0, 'closing the modal must consume the navigation intent')
  await page.goBack()
  await page.getByRole('button', { name: 'View active agents' }).waitFor()
  await page.goForward()
  await page.getByRole('link', { name: 'Agents' }).waitFor()
  assert.equal(await page.getByRole('heading', { name: 'Create Session & Spawn Agent' }).count(), 0, 'history navigation must not reopen the modal')
  await page.reload()
  assert.equal(await page.getByRole('heading', { name: 'Create Session & Spawn Agent' }).count(), 0, 'refresh must not reopen the modal')

  const cases = [
    ['View sessions', 'Sessions', null, null],
    ['View all agents', 'Statuses', 'All agents', 6],
    ['View active agents', 'Statuses', 'Active agents', 4],
    ['View Waiting agents', 'Statuses', 'Waiting', 4],
    ['View Needs attention agents', 'Statuses', 'Needs attention', 1],
    ['View Force-terminated agents', 'Statuses', 'Force-terminated', 1],
    ['View Completed agents', 'Statuses', 'Completed', 1],
  ]
  for (const [card, view, selected, count] of cases) {
    await page.getByRole('link', { name: 'Home' }).click()
    await page.getByRole('button', { name: card }).click()
    assert.equal(await page.getByRole('link', { name: view }).getAttribute('aria-current'), 'page', `${card} must select ${view}`)
    if (selected) {
      assert.equal(await page.getByRole('button', { name: selected, exact: true }).getAttribute('aria-pressed'), 'true', `${card} must expose its selected status filter`)
      await page.getByText(`Found ${count} agents in 1 sessions`).waitFor()
    }
  }

  await page.reload()
  assert.equal(await page.getByRole('button', { name: 'Completed', exact: true }).getAttribute('aria-pressed'), 'true', 'reload must preserve the selected Home filter')
  await page.getByRole('button', { name: 'Force-terminated', exact: true }).click()
  assert.equal(await page.getByRole('button', { name: 'Force-terminated', exact: true }).getAttribute('aria-pressed'), 'true')
  await page.getByRole('button', { name: 'Completed', exact: true }).click()
  await page.goBack()
  await page.waitForFunction(() => [...document.querySelectorAll('button')].some(button => button.textContent === 'Force-terminated' && button.getAttribute('aria-pressed') === 'true'))
  assert.equal(await page.getByRole('button', { name: 'Force-terminated', exact: true }).getAttribute('aria-pressed'), 'true', 'back must restore the prior filter')
  await page.goForward()
  await page.waitForFunction(() => [...document.querySelectorAll('button')].some(button => button.textContent === 'Completed' && button.getAttribute('aria-pressed') === 'true'))
  assert.equal(await page.getByRole('button', { name: 'Completed', exact: true }).getAttribute('aria-pressed'), 'true', 'forward must restore the next filter')

  const widths = []
  for (const [width, height] of [[1440, 834], [834, 900], [390, 834]]) {
    await page.setViewportSize({ width, height })
    widths.push({ width, height, overflow: await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth) })
    if (evidenceDir) await page.screenshot({ path: `${evidenceDir}/home-agent-deeplinks-${width}x${height}.png`, fullPage: true })
  }
  assert(widths.every(item => item.overflow === 0), `viewport overflow: ${JSON.stringify(widths)}`)
  console.log(JSON.stringify({ widths, assertions: ['Home-to-Agents count equality', 'visible selected filter', 'URL reload/back-forward', 'responsive overflow'] }))
} finally {
  await browser?.close()
  await new Promise(resolve => server.close(resolve))
  await vite.close()
}
