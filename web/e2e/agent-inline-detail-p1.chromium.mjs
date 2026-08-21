import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import http from 'node:http'
import { fileURLToPath } from 'node:url'
import { createServer as createViteServer } from 'vite'
import { chromium } from 'playwright'

const webRoot = fileURLToPath(new URL('..', import.meta.url))
const evidenceDir = process.env.CAO_VISUAL_EVIDENCE_DIR || '/tmp/cao-ui-agent-inline-detail-p1'
const sessions = [
  { id: 'cao-inline-a', name: 'cao-inline-a', status: 'active', created_at: '2' },
  { id: 'cao-inline-b', name: 'cao-inline-b', status: 'detached', created_at: '1' },
]
const terminals = Object.fromEntries(sessions.map(session => [session.name, [{
  id: `${session.id}-terminal`,
  tmux_session: session.name,
  tmux_window: '0',
  provider: 'codex',
  agent_profile: 'developer_terra_high',
  last_active: null,
}]]))
const runtimeBranding = { title: 'ThreadCells', subtitle: 'Multi-agent control plane', logoUrl: '/threadcells-symbol.png', customLogo: false }

await mkdir(evidenceDir, { recursive: true })
const vite = await createViteServer({ root: webRoot, configFile: false, plugins: [(await import('@vitejs/plugin-react')).default()], appType: 'spa', server: { middlewareMode: true, hmr: false } })
const json = (response, value) => { response.writeHead(200, { 'content-type': 'application/json' }); response.end(JSON.stringify(value)) }
const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://localhost')
  if (request.method === 'GET' && url.pathname === '/sessions') return json(response, sessions)
  const matchedSession = sessions.find(session => url.pathname === `/sessions/${session.name}`)
  if (request.method === 'GET' && matchedSession) return json(response, { session: matchedSession, terminals: terminals[matchedSession.name] })
  if (request.method === 'GET' && url.pathname.startsWith('/terminals/')) {
    const terminalId = url.pathname.split('/')[2]
    return json(response, { id: terminalId, provider: 'codex', status: 'idle', lifecycle: 'running', workflow_state: 'active', last_active: null })
  }
  if (request.method === 'GET' && url.pathname === '/agents/profiles') return json(response, [])
  if (request.method === 'GET' && url.pathname === '/projects') return json(response, [])
  if (request.method === 'GET' && url.pathname === '/settings/branding') return json(response, runtimeBranding)
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
  await page.addInitScript(() => {
    const original = window.setInterval
    window.setInterval = (handler, timeout, ...args) => original(handler, timeout === 5000 ? 80 : timeout === 3000 ? 60 : timeout, ...args)
  })
  await page.goto(origin)
  await page.getByRole('link', { name: 'Agents' }).click()
  const a = page.getByTestId(`agent-session-${sessions[0].id}`)
  const b = page.getByTestId(`agent-session-${sessions[1].id}`)
  await a.waitFor()
  await b.waitFor()

  await a.getByRole('button', { name: `Expand ${sessions[0].name}` }).press('Enter')
  const aDetail = a.getByTestId(`agent-session-detail-${sessions[0].id}`)
  await aDetail.waitFor()
  assert.equal(await page.getByTestId(/agent-session-detail-/).count(), 1, 'A detail must render exactly once')
  assert.equal(await aDetail.evaluate(detail => detail.parentElement?.dataset.testid), `agent-session-${sessions[0].id}`, 'A detail must be inline below A')

  await b.getByRole('button', { name: `Expand ${sessions[1].name}` }).click()
  const bDetail = b.getByTestId(`agent-session-detail-${sessions[1].id}`)
  await bDetail.waitFor()
  assert.equal(await aDetail.count(), 0, 'A detail must move away when B is selected')
  assert.equal(await page.getByTestId(/agent-session-detail-/).count(), 1, 'B detail must remain the only detail')
  assert.equal(await bDetail.evaluate(detail => detail.parentElement?.dataset.testid), `agent-session-${sessions[1].id}`, 'B detail must be inline below B')
  assert.equal(await b.evaluate(node => node.nextElementSibling?.dataset.testid || null), null, 'no detached detail may follow the Sessions list')

  await bDetail.getByTitle('Close terminal').click()
  await page.getByRole('heading', { name: 'Close Terminal' }).waitFor()
  assert.equal(await bDetail.count(), 1, 'terminal actions must not collapse the selected session')
  await page.getByRole('button', { name: 'Cancel' }).click()

  for (const width of [1440, 834, 390]) {
    await page.setViewportSize({ width, height: 960 })
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), 0, `horizontal overflow at ${width}px`)
    await b.screenshot({ path: `${evidenceDir}/${width}-b-inline.png` })
  }

  await b.getByRole('button', { name: `Collapse ${sessions[1].name}` }).click()
  assert.equal(await bDetail.count(), 0, 'clicking selected B must collapse it')
  assert.equal(await page.getByTestId(/agent-session-detail-/).count(), 0, 'no detached bottom detail may remain after collapse')
  console.log(JSON.stringify({ evidenceDir, widths: [1440, 834, 390], assertions: ['keyboard expansion of A inline', 'A to B detail move', 'single inline detail with no detached bottom copy', 'Close Terminal modal keeps B expanded', 'B collapse', 'no horizontal overflow'] }))
} finally {
  await browser?.close()
  await new Promise(resolve => server.close(resolve))
  await vite.close()
}
