import assert from 'node:assert/strict'
import http from 'node:http'
import { fileURLToPath } from 'node:url'
import { createServer as createViteServer } from 'vite'
import { chromium } from 'playwright'

const webRoot = fileURLToPath(new URL('..', import.meta.url))
const session = {
  id: 'recovery-actions', name: 'cao-recovery-actions', status: 'active', created_at: '1',
  agent_count: 2, active_agent_count: 2, workflow_counts: { completed: 2 }, activity_counts: { idle: 2 },
  project_name: 'Recovery Project', last_active: '1',
  first_agent: { id: 'a11ce001', activity: 'idle', execution_state: 'ready', lifecycle: 'running', workflow_state: 'completed', workflow_reason: null },
  last_agent: { id: 'b22ce001', activity: 'idle', execution_state: 'ready', lifecycle: 'running', workflow_state: 'completed', workflow_reason: null },
}
const agents = [
  { id: 'a11ce001', name: 'healthy-owner', eligible: false, reason: 'RECOVERY_HEALTHY_RUNTIME_ACTIVE' },
  { id: 'b22ce001', name: 'stale-owner', eligible: true, reason: null },
].map((item, index) => ({
  id: item.id, name: item.name, provider: 'codex', session_id: session.id, session_name: session.name,
  agent_profile: 'critical_sol_xhigh_owner', activity: 'idle', execution_state: 'ready', lifecycle: 'running',
  workflow_state: 'completed', workflow_status: 'completed', workflow_reason: null, assignment_status: null,
  result_status: null, delivery_status: null, context_role: 'supervisor', launch_worktree: `/managed/${item.id}`,
  managed_worktree_kind: 'supervisor', managed_worktree_commit: 'a'.repeat(40), managed_worktree_branch: `session/${item.id}`,
  projectId: 'project-1', project_name: 'Recovery Project', project_path: '/source', creation_order: index + 1,
  last_active: null, recovery: { eligible: item.eligible, reason_code: item.reason },
}))

const vite = await createViteServer({
  root: webRoot, configFile: false,
  plugins: [(await import('@vitejs/plugin-react')).default()],
  define: { __THREADCELLS_REVISION__: JSON.stringify('recovery-action-evidence'), __THREADCELLS_VERSION__: JSON.stringify('0.3.4-alpha') },
  appType: 'spa', server: { middlewareMode: true, hmr: false },
})
const json = (response, value) => { response.writeHead(200, { 'content-type': 'application/json' }); response.end(JSON.stringify(value)) }
const requestJson = async request => { let body = ''; for await (const chunk of request) body += chunk; return body ? JSON.parse(body) : null }
const server = http.createServer(async (request, response) => {
  const url = new URL(request.url, 'http://localhost')
  if (request.method === 'GET' && url.pathname === '/ui/overview') return json(response, { sessions: 1, agents: 2, active: 2, waiting: 0, owner_gate: 0, cancelled: 0, completed: 2 })
  if (request.method === 'GET' && url.pathname === '/ui/sessions') return json(response, { items: [session], total: 1, limit: 10, offset: 0, next_offset: null })
  if (request.method === 'GET' && url.pathname === '/ui/agents') return json(response, { items: agents, total: 2, limit: 40, offset: 0, next_offset: null, facets: { activities: ['idle'], workflow_states: ['completed'], profiles: ['critical_sol_xhigh_owner'] } })
  if (request.method === 'POST' && url.pathname === '/recovery-takeovers/capabilities') {
    const body = await requestJson(request)
    return json(response, { capabilities: agents.filter(agent => body.terminal_ids.includes(agent.id)).map(agent => ({ terminal_id: agent.id, ...agent.recovery })) })
  }
  if (request.method === 'GET' && url.pathname === '/agents/providers') return json(response, [{ name: 'codex', binary: 'codex', installed: true }])
  if (request.method === 'GET' && url.pathname === '/agents/profiles') return json(response, [{ name: 'critical_sol_xhigh_owner', description: '', source: 'built-in', owner_authorization_required: true }])
  if (request.method === 'GET' && url.pathname === '/projects') return json(response, [])
  if (request.method === 'GET' && url.pathname === '/sessions') return json(response, [session])
  vite.middlewares(request, response)
})

await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const address = server.address()
assert(address && typeof address !== 'string')
const origin = `http://127.0.0.1:${address.port}`
const copy = {
  en: { agents: 'Agents', homeExpand: 'Expand recovery-actions', agentsExpand: 'Expand recovery-actions', action: 'Recover agent' },
  ru: { agents: 'Агенты', homeExpand: 'Развернуть: recovery-actions', agentsExpand: 'Развернуть recovery-actions', action: 'Восстановить агента' },
}
let browser
try {
  browser = await chromium.launch({ headless: true })
  for (const locale of ['en', 'ru']) {
    for (const width of [390, 834, 1440]) {
      const context = await browser.newContext({ viewport: { width, height: 960 } })
      await context.addInitScript(value => localStorage.setItem('threadcells.app.locale', value), locale)
      const page = await context.newPage()
      await page.goto(origin)
      await page.getByRole('button', { name: copy[locale].homeExpand, exact: true }).click()
      await page.getByRole('button', { name: copy[locale].action }).waitFor()
      assert.equal(await page.getByRole('button', { name: copy[locale].action }).count(), 1, `Home ${locale} must expose only the eligible recovery action`)
      assert.equal(await page.locator('[data-testid="agent-detail-card-a11ce001"]').getByRole('button', { name: copy[locale].action }).count(), 0, `Home ${locale} exposed healthy recovery`)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), 0, `Home ${locale} overflowed at ${width}px`)

      await page.getByRole('link', { name: copy[locale].agents }).click()
      await page.getByRole('button', { name: copy[locale].agentsExpand, exact: true }).click()
      await page.getByRole('button', { name: copy[locale].action }).waitFor()
      assert.equal(await page.getByRole('button', { name: copy[locale].action }).count(), 1, `Agents ${locale} must expose only the eligible recovery action`)
      assert.equal(await page.locator('[data-testid="agent-detail-card-a11ce001"]').getByRole('button', { name: copy[locale].action }).count(), 0, `Agents ${locale} exposed healthy recovery`)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), 0, `Agents ${locale} overflowed at ${width}px`)
      await context.close()
    }
  }
  console.log(JSON.stringify({ locales: ['en', 'ru'], widths: [390, 834, 1440], surfaces: ['Home', 'Agents'], assertions: ['backend-derived eligibility', 'healthy action hidden', 'eligible action shared', 'no horizontal overflow'] }))
} finally {
  await browser?.close()
  await new Promise(resolve => server.close(resolve))
  await vite.close()
}
