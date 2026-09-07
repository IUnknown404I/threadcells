import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import http from 'node:http'
import { fileURLToPath } from 'node:url'
import { createServer as createViteServer } from 'vite'
import { chromium } from 'playwright'

const webRoot = fileURLToPath(new URL('..', import.meta.url))
const evidenceDir = process.env.THREADCELLS_HISTORY_EVIDENCE_DIR || '/tmp/threadcells-interaction-history-p1'
const session = {
  id: 'history-session', name: 'cao-history-session', status: 'active', created_at: '2026-09-07T08:00:00Z',
  agent_count: 1, active_agent_count: 1, current_queue_count: 4,
  workflow_counts: { active: 1 }, activity_counts: { ready: 1 }, project_name: 'ThreadCells',
  last_active: '2026-09-07T08:10:00Z',
  first_agent: { id: 'history-agent', activity: 'ready', execution_state: 'ready', lifecycle: 'running', workflow_state: 'active', workflow_reason: null },
  last_agent: { id: 'history-agent', activity: 'ready', execution_state: 'ready', lifecycle: 'running', workflow_state: 'active', workflow_reason: null },
}
const agent = {
  id: 'history-agent', name: 'owner', provider: 'codex', session_id: session.id, session_name: session.name,
  agent_profile: 'developer_sol_high', activity: 'ready', execution_state: 'ready', lifecycle: 'running',
  workflow_state: 'active', workflow_status: 'open', workflow_reason: null, assignment_status: null,
  result_status: null, delivery_status: null, context_role: 'supervisor', launch_worktree: null,
  managed_worktree_kind: null, managed_worktree_commit: null, managed_worktree_branch: null,
  projectId: 'threadcells', project_name: 'ThreadCells', project_path: null, creation_order: 1,
  last_active: session.last_active,
}
const base = {
  source: { kind: 'operator', terminal_id: 'ui', target_terminal_id: agent.id },
  input_preview: 'Inspect durable lifecycle state', created_at: '2026-09-07T08:00:00Z', updated_at: '2026-09-07T08:01:00Z',
  current: true, queue: { state: 'queued', wait_reason: 'provider_capacity', admission_pending: true },
  workflow: { id: 1, turn_id: 11, status: 'open', reason: null, turn_state: 'queued', turn_kind: 'external_input', provider_outcome_code: null, provider_outcome_detail: null, effect_kind: null, effect_state: null, turn_count: 0, superseded_turn_count: 0 },
  result: { id: null, status: null, summary: null, available: false },
  delivery: { status: null, pending: false, acknowledged: false }, final_disposition: null,
  diagnostics: { interaction_id: 'workflow-turn:0001', durable_id: '11', assignment_id: null },
}
const currentItems = [
  { ...base, id: 'workflow-turn:0001', interaction_type: 'workflow_turn', task_type: 'external_input' },
  { ...base, id: 'inbox:0002', interaction_type: 'inbox', task_type: 'message', source: { kind: 'agent', terminal_id: 'peer', target_terminal_id: agent.id }, queue: { state: 'pending', wait_reason: 'delivery', admission_pending: true }, workflow: { ...base.workflow, id: null, turn_id: null, status: null, turn_state: null }, diagnostics: { interaction_id: 'inbox:0002', durable_id: '2', assignment_id: null } },
  { ...base, id: 'assignment:0003', interaction_type: 'delegation', task_type: 'assign', source: { kind: 'agent', terminal_id: agent.id, target_terminal_id: 'child' }, queue: { state: 'result_delivered', wait_reason: 'acknowledgement', admission_pending: false }, result: { id: 'result-current', status: 'complete', summary: 'Child result ready', available: true }, delivery: { status: 'result_delivered', pending: true, acknowledged: false }, diagnostics: { interaction_id: 'assignment:0003', durable_id: 'attempt-current', assignment_id: 3 } },
  { ...base, id: 'runtime:history-agent', interaction_type: 'runtime_authority', task_type: 'runtime_recovery', source: { kind: 'system', terminal_id: agent.id, target_terminal_id: agent.id }, queue: { state: 'recovery_required', wait_reason: 'writer_recovery_authority', admission_pending: true }, workflow: { ...base.workflow, id: null, turn_id: null, status: null, turn_state: null }, diagnostics: { interaction_id: 'runtime:history-agent', durable_id: agent.id, assignment_id: null } },
]
const historyItem = {
  ...base, id: 'assignment:0004', interaction_type: 'delegation', task_type: 'assign', current: false,
  created_at: '2026-09-06T08:00:00Z', updated_at: '2026-09-06T08:04:00Z',
  queue: { state: 'result_acknowledged', wait_reason: null, admission_pending: false },
  result: { id: 'result-history', status: 'complete', summary: 'Immutable reviewed result', available: true },
  delivery: { status: 'result_acknowledged', pending: false, acknowledged: true }, final_disposition: 'acknowledged',
  diagnostics: { interaction_id: 'assignment:0004', durable_id: 'attempt-history', assignment_id: 4 },
}

let historyRequests = 0
let terminalOutputRequests = 0
await mkdir(evidenceDir, { recursive: true })
const vite = await createViteServer({ root: webRoot, configFile: false, plugins: [(await import('@vitejs/plugin-react')).default()], appType: 'spa', server: { middlewareMode: true, hmr: false } })
const json = (response, value) => { response.writeHead(200, { 'content-type': 'application/json' }); response.end(JSON.stringify(value)) }
const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://localhost')
  if (request.method === 'GET' && url.pathname === '/ui/overview') return json(response, { sessions: 1, agents: 1, active: 1, waiting: 1, owner_gate: 0, cancelled: 0, completed: 0 })
  if (request.method === 'GET' && url.pathname === '/ui/sessions') return json(response, { items: [session], total: 1, limit: 10, offset: 0, next_offset: null })
  if (request.method === 'GET' && url.pathname === '/ui/agents') return json(response, { items: [agent], total: 1, limit: 40, offset: 0, next_offset: null, facets: { activities: ['ready'], workflow_states: ['active'], profiles: ['developer_sol_high'] } })
  if (request.method === 'GET' && url.pathname === '/ui/interactions') {
    if (url.searchParams.get('mode') === 'history') {
      historyRequests += 1
      return json(response, { items: [historyItem], total: null, limit: 20, next_cursor: null, snapshot_at: '2026-09-07T09:00:00Z' })
    }
    return json(response, { items: currentItems, total: 4, limit: 20, next_cursor: null, snapshot_at: '2026-09-07T09:00:00Z' })
  }
  if (request.method === 'GET' && url.pathname === '/delegation-results/result-history') return json(response, { id: 'result-history', delegation_kind: 'assign', status: 'complete', delivery_status: 'result_acknowledged', authorship: 'child_submission', document: { summary: 'Immutable reviewed result', body_markdown: 'Canonical result body from durable storage.' }, created_at: historyItem.created_at, finalized_at: historyItem.updated_at })
  if (request.method === 'GET' && url.pathname.includes('/output')) { terminalOutputRequests += 1; return json(response, {}) }
  if (request.method === 'GET' && url.pathname === '/settings/branding') return json(response, { title: 'ThreadCells', subtitle: 'Multi-agent control plane', logoUrl: '/threadcells-symbol.png', customLogo: false })
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
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto(origin)
  const sessionCard = page.getByTestId(`home-session-${session.id}`)
  await sessionCard.waitFor()
  assert.equal(await sessionCard.getByText('Queued: 4', { exact: true }).count(), 1)
  assert(await sessionCard.getByText('Ready', { exact: true }).count() >= 1, 'physical Ready must remain visible independently')

  for (const width of [1440, 834, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 960 })
    const action = page.getByTestId(`session-header-${session.id}`).getByRole('button', { name: 'Open work and interaction history' })
    await action.click()
    const dialog = page.getByRole('dialog', { name: 'Work & interaction history' })
    await dialog.waitFor()
    await dialog.getByText('provider capacity', { exact: true }).waitFor()
    assert.equal(await dialog.getByRole('tab', { name: 'Current Queue · 4' }).count(), 1)
    assert.equal(historyRequests, width === 1440 ? 0 : width === 834 ? 1 : 2, 'History must remain lazy for each newly opened drawer')
    const [dialogBox, viewport] = await Promise.all([dialog.boundingBox(), page.viewportSize()])
    assert(dialogBox && viewport)
    assert(dialogBox.x >= 0 && dialogBox.x + dialogBox.width <= viewport.width + 1, `drawer escaped viewport at ${width}px`)
    if (width === 390) assert.equal(Math.round(dialogBox.width), 390, 'mobile drawer must fill the viewport')
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), 0, `horizontal overflow at ${width}px`)
    await page.screenshot({ path: `${evidenceDir}/${width}-current.png`, fullPage: true })

    await dialog.getByRole('tab', { name: 'History' }).click()
    await dialog.getByText('Immutable reviewed result', { exact: false }).waitFor()
    assert.equal(historyRequests, width === 1440 ? 1 : width === 834 ? 2 : 3)
    assert.equal(terminalOutputRequests, 0, 'history must never reconstruct results from terminal output')
    await dialog.getByRole('button', { name: 'Show details' }).click()
    await dialog.getByText('Canonical result body from durable storage.', { exact: true }).waitFor()
    await page.screenshot({ path: `${evidenceDir}/${width}-history.png`, fullPage: true })
    await page.keyboard.press('Escape')
    assert.equal(await dialog.count(), 0)
    assert.equal(await action.evaluate(node => document.activeElement === node), true, 'closing the drawer must restore focus')
  }
  assert.deepEqual(errors, [])
  console.log(JSON.stringify({ evidenceDir, widths: [1440, 834, 390], assertions: ['positive queue count', 'physical Ready remains primary', 'lazy History', 'canonical durable result', 'no terminal-output reads', 'responsive drawer', 'no horizontal overflow', 'Escape and focus restoration'] }))
} finally {
  await browser?.close()
  await new Promise(resolve => server.close(resolve))
  await vite.close()
}
