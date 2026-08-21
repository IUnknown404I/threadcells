import assert from 'node:assert/strict'
import http from 'node:http'
import { fileURLToPath } from 'node:url'
import { createServer as createViteServer } from 'vite'
import { chromium } from 'playwright'

const webRoot = fileURLToPath(new URL('..', import.meta.url))
const sessions = [
  { id: 'filter-session-a', name: 'filter-session-a', status: 'active', created_at: '1' },
  { id: 'filter-session-b', name: 'filter-session-b', status: 'active', created_at: '2' },
]
const terminals = {
  'filter-session-a': [
    { id: 'filter-agent-a', tmux_session: 'filter-session-a', tmux_window: '0', provider: 'codex', agent_profile: 'developer', last_active: null },
    { id: 'filter-agent-b', tmux_session: 'filter-session-a', tmux_window: '1', provider: 'codex', agent_profile: 'reviewer', last_active: null },
  ],
  'filter-session-b': [
    { id: 'filter-agent-c', tmux_session: 'filter-session-b', tmux_window: '0', provider: 'claude_code', agent_profile: 'reviewer', last_active: null },
  ],
}
const statuses = {
  'filter-agent-a': { status: 'idle', workflow_state: 'active' },
  'filter-agent-b': { status: 'processing', workflow_state: 'active' },
  'filter-agent-c': { status: 'idle', workflow_state: 'waiting' },
}
const branding = { title: 'ThreadCells', subtitle: 'Multi-agent control plane', logoUrl: '/threadcells-symbol.png', customLogo: false }
const vite = await createViteServer({ root: webRoot, configFile: false, plugins: [(await import('@vitejs/plugin-react')).default()], appType: 'spa', server: { middlewareMode: true, hmr: false } })
const json = (response, value) => { response.writeHead(200, { 'content-type': 'application/json' }); response.end(JSON.stringify(value)) }
const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://localhost')
  if (request.method === 'GET' && url.pathname === '/sessions') return json(response, sessions)
  const session = sessions.find(value => url.pathname === `/sessions/${value.name}`)
  if (request.method === 'GET' && session) return json(response, { session, terminals: terminals[session.name] })
  if (request.method === 'GET' && url.pathname.startsWith('/terminals/')) {
    const id = url.pathname.split('/')[2]
    return json(response, { id, name: id, provider: id === 'filter-agent-c' ? 'claude_code' : 'codex', session_name: '', agent_profile: null, lifecycle: 'running', last_active: null, ...statuses[id] })
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
  await page.getByRole('link', { name: 'Agents' }).click()
  assert.equal(await page.getByRole('tab', { name: 'Sessions' }).getAttribute('aria-selected'), 'true', 'Sessions must remain the default view')
  await page.getByRole('tab', { name: 'Statuses' }).click()
  await page.getByRole('button', { name: 'idle', exact: true }).waitFor()
  await page.getByRole('button', { name: 'idle', exact: true }).click()
  await page.getByRole('button', { name: 'active', exact: true }).click()
  await page.getByText('Found 1 agents in 1 sessions').waitFor()
  assert.equal(await page.getByTestId(/agent-detail-card-/).count(), 1, 'status filter must hide non-matching agents')

  await page.getByRole('tab', { name: 'Profiles' }).click()
  await page.getByRole('button', { name: 'developer', exact: true }).waitFor()
  await page.getByRole('button', { name: 'developer', exact: true }).click()
  await page.getByRole('button', { name: 'reviewer', exact: true }).click()
  await page.getByText('Found 3 agents in 2 sessions').waitFor()
  await page.getByRole('tab', { name: 'Statuses' }).click()
  assert.equal(await page.getByRole('button', { name: 'idle', exact: true }).getAttribute('aria-pressed'), 'true', 'Statuses filters must persist per view')
  await page.getByRole('tab', { name: 'Profiles' }).click()
  assert.equal(await page.getByRole('button', { name: 'developer', exact: true }).getAttribute('aria-pressed'), 'true', 'Profiles filters must persist per view')
  await page.getByTitle('Close terminal').first().click()
  await page.getByRole('heading', { name: 'Close Terminal' }).waitFor()
  await page.getByRole('button', { name: 'Cancel' }).click()

  const widths = []
  for (const width of [1440, 834, 390]) {
    await page.setViewportSize({ width, height: 900 })
    await page.getByRole('tab', { name: 'Statuses' }).click()
    const clear = page.getByRole('button', { name: 'Clear filters' }).first()
    if (await clear.count()) await clear.click()
    await page.getByRole('button', { name: 'processing', exact: true }).click()
    await page.getByRole('button', { name: 'waiting', exact: true }).click()
    await page.getByText('No agents match the selected filters.').waitFor()
    widths.push({ width, overflow: await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth) })
    await page.getByRole('button', { name: 'Clear filters' }).first().click()
  }
  assert(widths.every(item => item.overflow === 0), `viewport overflow: ${JSON.stringify(widths)}`)
  console.log(JSON.stringify({ widths, assertions: ['Sessions default', 'status OR/AND projection', 'profile multi-select', 'clear/empty', 'mode state retention', 'close action', 'matching-only cards'] }))
} finally {
  await browser?.close()
  await new Promise(resolve => server.close(resolve))
  await vite.close()
}
