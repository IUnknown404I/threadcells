import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import http from 'node:http'
import { fileURLToPath } from 'node:url'
import { createServer as createViteServer } from 'vite'
import { chromium } from 'playwright'

const webRoot = fileURLToPath(new URL('..', import.meta.url))
const evidenceDir = process.env.CAO_VISUAL_EVIDENCE_DIR || '/tmp/threadcells-session-status-acceptance'
const runtimeBranding = { title: 'ThreadCells', subtitle: 'Multi-agent control plane', logoUrl: '/threadcells-symbol.png', customLogo: false }

const boundary = (id, activity, lifecycle, workflowState) => ({
  id,
  activity,
  execution_state: activity === 'processing' ? 'processing' : 'ready',
  lifecycle,
  workflow_state: workflowState,
})

const fewSession = {
  id: 'few-badges', name: 'cao-few-badges', status: 'history',
  created_at: '2026-08-22T01:00:00Z', last_active: '2026-08-22T01:05:00Z',
  agent_count: 1, active_agent_count: 0, project_name: null,
  activity_counts: { exited: 1 }, workflow_counts: { completed: 1 },
  first_agent: boundary('few-agent-0', 'exited', 'exited', 'completed'),
  last_agent: boundary('few-agent-0', 'exited', 'exited', 'completed'),
}

const manySession = {
  id: 'many-badges', name: 'cao-many-badges', status: 'active',
  created_at: '2026-08-22T02:00:00Z', last_active: '2026-08-22T03:00:00Z',
  agent_count: 72, active_agent_count: 5, project_name: 'ThreadCells',
  activity_counts: { processing: 2, ready: 3, exited: 67 },
  workflow_counts: { active: 2, waiting: 6, recoverable: 2, completed: 60, cancelled: 2 },
  first_agent: boundary('many-agent-00', 'processing', 'running', 'active'),
  last_agent: boundary('many-agent-71', 'exited', 'exited', 'completed'),
}

const manyAgents = Array.from({ length: 72 }, (_, index) => {
  const activity = index < 2 ? 'processing' : index < 5 ? 'ready' : 'exited'
  const workflowState = index < 2 ? 'active' : index < 8 ? 'waiting' : index < 10 ? 'recoverable' : index < 70 ? 'completed' : 'cancelled'
  return {
    id: `many-agent-${String(index).padStart(2, '0')}`,
    name: String(index), provider: 'codex', session_id: manySession.id,
    session_name: manySession.name, agent_profile: 'developer', activity,
    execution_state: activity === 'processing' ? 'processing' : 'ready',
    lifecycle: activity === 'exited' ? 'exited' : 'running', workflow_state: workflowState,
    workflow_status: null, assignment_status: null, result_status: null,
    delivery_status: null, context_role: index ? 'work' : 'supervisor',
    launch_worktree: null, managed_worktree_kind: null, managed_worktree_commit: null,
    managed_worktree_branch: null, projectId: 'threadcells', project_name: 'ThreadCells',
    project_path: '/fixture/threadcells', creation_order: index + 1,
    last_active: `2026-08-22T02:${String(71 - index).padStart(2, '0')}:00Z`,
  }
})
const fewAgents = [{
  ...manyAgents[0], id: 'few-agent-0', name: '0', session_id: fewSession.id,
  session_name: fewSession.name, activity: 'exited', execution_state: 'ready',
  lifecycle: 'exited', workflow_state: 'completed', creation_order: 1,
}]

function pageResult(items, url, defaultLimit) {
  const limit = Number(url.searchParams.get('limit') || defaultLimit)
  const offset = Number(url.searchParams.get('offset') || 0)
  const selected = items.slice(offset, offset + limit)
  return { items: selected, total: items.length, limit, offset, next_offset: offset + selected.length < items.length ? offset + selected.length : null }
}

await mkdir(evidenceDir, { recursive: true })
const vite = await createViteServer({
  root: webRoot, configFile: false,
  plugins: [(await import('@vitejs/plugin-react')).default()],
  define: { __THREADCELLS_REVISION__: JSON.stringify('session-status-acceptance'), __THREADCELLS_VERSION__: JSON.stringify('0.1.0-alpha.2') },
  appType: 'spa', server: { middlewareMode: true, hmr: false },
})
const json = (response, value) => { response.writeHead(200, { 'content-type': 'application/json' }); response.end(JSON.stringify(value)) }
const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://localhost')
  if (request.method === 'GET' && url.pathname === '/ui/overview') return json(response, { sessions: 2, agents: 73, active: 5, waiting: 3, owner_gate: 0, cancelled: 2, completed: 61 })
  if (request.method === 'GET' && url.pathname === '/ui/sessions') return json(response, pageResult([manySession, fewSession], url, 10))
  if (request.method === 'GET' && url.pathname === '/ui/agents') {
    const agents = url.searchParams.get('session_id') === fewSession.id ? fewAgents : manyAgents
    return json(response, { ...pageResult(agents, url, 40), facets: { activities: ['processing', 'ready', 'exited'], workflow_states: ['active', 'waiting', 'recoverable', 'completed', 'cancelled'], profiles: ['developer'] } })
  }
  if (request.method === 'GET' && url.pathname === '/sessions') return json(response, [manySession, fewSession])
  if (request.method === 'GET' && url.pathname === '/agents/profiles') return json(response, [])
  if (request.method === 'GET' && url.pathname === '/agents/providers') return json(response, [])
  if (request.method === 'GET' && url.pathname === '/projects') return json(response, [])
  if (request.method === 'GET' && url.pathname === '/settings/branding') return json(response, runtimeBranding)
  vite.middlewares(request, response)
})

const terminalIds = locator => locator.locator('[data-terminal-id]').evaluateAll(elements => elements.map(element => element.dataset.terminalId))

await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const address = server.address()
assert(address && typeof address !== 'string')
const origin = `http://127.0.0.1:${address.port}`
let browser
try {
  browser = await chromium.launch({ headless: true })
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } })
  await page.goto(origin)

  const fewFirst = page.getByTestId(`session-status-first-${fewSession.id}`)
  const fewLast = page.getByTestId(`session-status-last-${fewSession.id}`)
  const manyFirst = page.getByTestId(`session-status-first-${manySession.id}`)
  const manyLast = page.getByTestId(`session-status-last-${manySession.id}`)
  const manyTotal = page.getByTestId(`session-status-total-${manySession.id}`)
  await manyFirst.waitFor()

  assert.deepEqual(await terminalIds(fewFirst), ['few-agent-0'])
  assert.deepEqual(await terminalIds(fewLast), ['few-agent-0'])
  assert.deepEqual(await terminalIds(manyFirst), ['many-agent-00'])
  assert.deepEqual(await terminalIds(manyLast), ['many-agent-71'])
  assert.equal(await page.getByTestId(`session-status-agent-${manySession.id}-processing`).getByText('×2').count(), 1)
  assert.equal(await page.getByTestId(`session-status-agent-${manySession.id}-ready`).getByText('×3').count(), 1)
  assert.equal(await page.getByTestId(`session-status-agent-${manySession.id}-exited`).getByText('×67').count(), 1)
  assert.equal(await page.getByTestId(`session-status-workflow-${manySession.id}-waiting`).getByText('×8').count(), 1)
  assert.equal(await page.getByTestId(`session-status-workflow-${manySession.id}-completed`).getByText('×60').count(), 1)
  assert.equal(await manyTotal.locator('[data-terminal-id]').count(), 0, 'Total rendered per-agent badges')
  assert.equal(await page.getByTestId(`session-status-badges-${fewSession.id}`).getByText('×1').count(), 0)

  for (const width of [1440, 834, 390]) {
    await page.setViewportSize({ width, height: 960 })
    const geometry = await manyTotal.evaluate(element => ({
      overflow: element.scrollWidth - element.clientWidth,
      documentOverflow: document.documentElement.scrollWidth - window.innerWidth,
    }))
    assert(geometry.overflow <= 1, `Total status overflowed at ${width}px`)
    assert.equal(geometry.documentOverflow, 0, `document overflowed at ${width}px`)
    await page.getByTestId(`home-session-${manySession.id}`).screenshot({ path: `${evidenceDir}/session-status-${width}.png` })
  }

  await page.getByRole('button', { name: 'Expand many-badges', exact: true }).click()
  const cards = page.locator('[data-testid^="agent-detail-card-many-agent-"]')
  await cards.nth(39).waitFor()
  const loadMore = page.getByRole('button', { name: /Load more agents \(40 of 72\)/ })
  if (await loadMore.count()) await loadMore.click()
  await cards.nth(71).waitFor()
  assert.deepEqual(
    await cards.evaluateAll(elements => elements.map(element => element.getAttribute('data-testid')?.replace('agent-detail-card-', ''))),
    manyAgents.map(agent => agent.id),
    'expanded agents did not preserve oldest-to-newest creation order',
  )
  await page.reload()
  await page.getByTestId(`session-status-first-${manySession.id}`).waitFor()
  assert.deepEqual(await terminalIds(page.getByTestId(`session-status-first-${manySession.id}`)), ['many-agent-00'])
  assert.deepEqual(await terminalIds(page.getByTestId(`session-status-last-${manySession.id}`)), ['many-agent-71'])

  console.log(JSON.stringify({ evidenceDir, widths: [1440, 834, 390], agentCount: 72, totalGroups: 7, order: 'oldest-first', first: 'many-agent-00', last: 'many-agent-71' }))
} finally {
  await browser?.close()
  await new Promise(resolve => server.close(resolve))
  await vite.close()
}
