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
const displayName = session => session.name.startsWith('cao-') ? session.name.slice(4) : session.name
const terminals = Object.fromEntries(sessions.map(session => [session.name, [{
  id: `${session.id}-terminal`,
  tmux_session: session.name,
  tmux_window: '0',
  provider: 'codex',
  agent_profile: 'developer_terra_high',
  last_active: null,
}]]))
const sessionSummaries = sessions.map(session => ({
  ...session,
  agent_count: 1,
  active_agent_count: 1,
  workflow_counts: { active: 1 },
  activity_counts: { idle: 1 },
  project_name: null,
  last_active: session.created_at,
  first_agent: null,
  last_agent: null,
}))
const agentSummaries = Object.fromEntries(sessions.map(session => [session.id, terminals[session.name].map((terminal, index) => ({
  id: terminal.id,
  name: terminal.tmux_window,
  provider: terminal.provider,
  session_id: session.id,
  session_name: session.name,
  agent_profile: terminal.agent_profile,
  activity: 'idle',
  execution_state: 'ready',
  lifecycle: 'running',
  workflow_state: 'active',
  workflow_status: 'open',
  workflow_reason: null,
  assignment_status: null,
  result_status: null,
  delivery_status: null,
  context_role: index === 0 ? 'supervisor' : 'work',
  launch_worktree: null,
  managed_worktree_kind: null,
  managed_worktree_commit: null,
  managed_worktree_branch: null,
  projectId: null,
  project_name: null,
  project_path: null,
  creation_order: index + 1,
  last_active: terminal.last_active,
}))]))
const runtimeBranding = { title: 'ThreadCells', subtitle: 'Multi-agent control plane', logoUrl: '/threadcells-symbol.png', customLogo: false }

await mkdir(evidenceDir, { recursive: true })
const vite = await createViteServer({ root: webRoot, configFile: false, plugins: [(await import('@vitejs/plugin-react')).default()], appType: 'spa', server: { middlewareMode: true, hmr: false } })
const json = (response, value) => { response.writeHead(200, { 'content-type': 'application/json' }); response.end(JSON.stringify(value)) }
const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://localhost')
  if (request.method === 'GET' && url.pathname === '/ui/overview') return json(response, { sessions: 2, agents: 2, active: 2, waiting: 0, owner_gate: 0, cancelled: 0, completed: 0 })
  if (request.method === 'GET' && url.pathname === '/ui/sessions') return json(response, { items: sessionSummaries, total: sessionSummaries.length, limit: 10, offset: 0, next_offset: null })
  if (request.method === 'GET' && url.pathname === '/ui/agents') {
    const items = agentSummaries[url.searchParams.get('session_id')] || []
    return json(response, { items, total: items.length, limit: 40, offset: 0, next_offset: null, facets: { activities: ['idle'], workflow_states: ['active'], profiles: ['developer_terra_high'] } })
  }
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

  await a.getByRole('button', { name: `Expand ${displayName(sessions[0])}` }).press('Enter')
  const aDetail = a.getByTestId(`agent-session-detail-${sessions[0].id}`)
  await aDetail.waitFor()
  assert.equal(await page.getByTestId(/agent-session-detail-/).count(), 1, 'A detail must render exactly once')
  assert.equal(await aDetail.evaluate(detail => detail.parentElement?.dataset.testid), `agent-session-${sessions[0].id}`, 'A detail must be inline below A')

  await b.getByRole('button', { name: `Expand ${displayName(sessions[1])}` }).click()
  const bDetail = b.getByTestId(`agent-session-detail-${sessions[1].id}`)
  await bDetail.waitFor()
  assert.equal(await aDetail.count(), 0, 'A detail must move away when B is selected')
  assert.equal(await page.getByTestId(/agent-session-detail-/).count(), 1, 'B detail must remain the only detail')
  assert.equal(await bDetail.evaluate(detail => detail.parentElement?.dataset.testid), `agent-session-${sessions[1].id}`, 'B detail must be inline below B')
  assert.equal(await b.evaluate(node => node.nextElementSibling?.dataset.testid || null), null, 'no detached detail may follow the Sessions list')

  for (const width of [1440, 834, 390]) {
    await page.setViewportSize({ width, height: 960 })
    const actionStrip = bDetail.getByTestId(`agent-actions-${sessions[1].id}-terminal`)
    const actionLayout = await actionStrip.evaluate(element => {
      const bounds = element.getBoundingClientRect()
      const parentBounds = element.parentElement?.getBoundingClientRect()
      const buttons = [...element.querySelectorAll('button')]
      return {
        columns: getComputedStyle(element).gridTemplateColumns.split(' ').length,
        rightGap: parentBounds ? Math.round(parentBounds.right - bounds.right) : null,
        rows: new Set(buttons.map(button => Math.round(button.getBoundingClientRect().top))).size,
        widths: new Set(buttons.map(button => Math.round(button.getBoundingClientRect().width))).size,
        text: buttons.map(button => button.innerText.trim()),
        contained: parentBounds ? bounds.left >= parentBounds.left && bounds.right <= parentBounds.right + 1 : false,
      }
    })
    assert.equal(actionLayout.columns, 7, `agent actions must reserve seven columns at ${width}px`)
    assert.equal(actionLayout.rightGap, 0, `agent actions must remain flush right at ${width}px`)
    assert.equal(actionLayout.rows, 1, `agent actions must remain on one row at ${width}px`)
    assert.equal(actionLayout.contained, true, `agent actions must remain inside the card at ${width}px`)
    assert.deepEqual(actionLayout.text.slice(-3), ['', '', ''], `colored actions must be icon-only at ${width}px`)
    if (width >= 1024) {
      assert.deepEqual(actionLayout.text.slice(0, 3), ['History', 'Inbox', 'Output'], 'desktop secondary labels must remain visible')
      assert.equal(actionLayout.widths, 2, 'desktop secondary and colored actions use stable width groups')
    } else {
      assert.deepEqual(actionLayout.text.slice(0, 3), ['', '', ''], `narrow secondary labels must compact at ${width}px`)
      assert.equal(actionLayout.widths, 1, `narrow actions must use equal columns at ${width}px`)
    }
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), 0, `horizontal overflow at ${width}px`)
    await b.screenshot({ path: `${evidenceDir}/${width}-b-inline.png` })
  }

  await bDetail.getByTitle('Finish terminal').click()
  await page.getByRole('heading', { name: 'Finish' }).waitFor()
  assert.equal(await bDetail.count(), 1, 'terminal actions must not collapse the selected session')
  await page.getByRole('button', { name: 'Cancel' }).click()

  await b.getByRole('button', { name: `Collapse ${displayName(sessions[1])}` }).click()
  assert.equal(await bDetail.count(), 0, 'clicking selected B must collapse it')
  assert.equal(await page.getByTestId(/agent-session-detail-/).count(), 0, 'no detached bottom detail may remain after collapse')
  console.log(JSON.stringify({ evidenceDir, widths: [1440, 834, 390], assertions: ['keyboard expansion of A inline', 'A to B detail move', 'single inline detail with no detached bottom copy', 'stable one-row right-aligned action strip', 'desktop secondary labels retained', 'colored actions icon-only', 'Finish modal keeps B expanded', 'B collapse', 'no horizontal overflow'] }))
} finally {
  await browser?.close()
  await new Promise(resolve => server.close(resolve))
  await vite.close()
}
