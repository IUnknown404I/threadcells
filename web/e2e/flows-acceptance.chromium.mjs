import assert from 'node:assert/strict'
import http from 'node:http'
import { fileURLToPath } from 'node:url'
import { createServer as createViteServer } from 'vite'
import { chromium } from 'playwright'

const webRoot = fileURLToPath(new URL('..', import.meta.url))
const runtimeBranding = { title: 'ThreadCells', subtitle: 'Multi-agent control plane', logoUrl: '/threadcells-symbol.png', customLogo: false }
const project = { projectId: 'project-flows', name: 'Flows Fixture', path: '/srv/flows-fixture', description: 'safe isolated fixture', isDefault: true }
const flow = (name, overrides = {}) => ({ name, file_path: `/var/lib/threadcells/flows/${name}.flow.md`, schedule: '0 * * * *', agent_profile: 'developer', provider: 'codex', script: null, last_run: null, next_run: '2026-08-22T03:00:00Z', enabled: true, prompt_template: 'Inspect the repository and report health.', projectId: project.projectId, project_name: project.name, project_path: project.path, ...overrides })
let flows = [flow('existing-health-check')]
let executionState = null
let listFailurePending = false
const requests = { lists: 0, creates: 0, runs: 0, toggles: 0 }

const vite = await createViteServer({ root: webRoot, configFile: false, plugins: [(await import('@vitejs/plugin-react')).default()], define: { __THREADCELLS_REVISION__: JSON.stringify('flows-acceptance'), __THREADCELLS_VERSION__: JSON.stringify('0.1.0-alpha.1') }, appType: 'spa', server: { middlewareMode: true, hmr: false } })
function json(response, value, status = 200) { response.writeHead(status, { 'content-type': 'application/json' }); response.end(JSON.stringify(value)) }
function readJson(request) { return new Promise((resolve, reject) => { const chunks = []; request.on('data', chunk => chunks.push(chunk)); request.on('end', () => { try { resolve(JSON.parse(Buffer.concat(chunks).toString())) } catch (error) { reject(error) } }); request.on('error', reject) }) }

const server = http.createServer(async (request, response) => {
  const url = new URL(request.url, 'http://localhost')
  if (request.method === 'GET' && url.pathname === '/ui/overview') return json(response, { sessions: executionState ? 1 : 0, agents: executionState ? 3 : 0, active: executionState === 'active' ? 1 : 0, waiting: 0, owner_gate: 0, cancelled: executionState ? 1 : 0, completed: executionState ? 1 : 0 })
  if (request.method === 'GET' && url.pathname === '/ui/sessions') {
    const items = executionState ? [{ id: 'flow-run-session', name: 'flow-run-session', status: 'active', created_at: '2026-08-22T02:00:00Z', agent_count: 3, active_agent_count: executionState === 'active' ? 1 : 0, workflow_counts: executionState === 'active' ? { active: 1, completed: 1, cancelled: 1 } : { completed: 2, cancelled: 1 }, activity_counts: executionState === 'active' ? { ready: 1, exited: 2 } : { exited: 3 }, project_name: project.name, last_active: '2026-08-22T02:00:00Z', first_agent: { id: `flow-agent-0-${executionState}`, activity: executionState === 'active' ? 'ready' : 'exited', execution_state: 'ready', lifecycle: executionState === 'active' ? 'running' : 'exited', workflow_state: executionState }, last_agent: { id: 'flow-agent-2-cancelled', activity: 'exited', execution_state: 'ready', lifecycle: 'exited', workflow_state: 'cancelled' } }] : []
    return json(response, { items, total: items.length, limit: 10, offset: 0, next_offset: null })
  }
  if (request.method === 'GET' && url.pathname === '/ui/agents') {
    const states = executionState ? [executionState, 'completed', 'cancelled'] : []
    const items = states.map((state, index) => ({ id: `flow-agent-${index}-${state}`, name: String(index), provider: 'codex', session_id: 'flow-run-session', session_name: 'flow-run-session', agent_profile: 'developer', activity: state === 'active' ? 'ready' : 'exited', execution_state: 'ready', lifecycle: state === 'active' ? 'running' : 'exited', workflow_state: state, workflow_status: null, assignment_status: null, result_status: state, delivery_status: null, context_role: index ? 'worker' : 'supervisor', launch_worktree: null, managed_worktree_kind: null, managed_worktree_commit: null, managed_worktree_branch: null, projectId: project.projectId, project_name: project.name, project_path: project.path, creation_order: index + 1, last_active: '2026-08-22T02:00:00Z' }))
    return json(response, { items, total: items.length, limit: 40, offset: 0, next_offset: null, facets: { activities: ['idle'], workflow_states: states, profiles: ['developer'] } })
  }
  if (request.method === 'GET' && url.pathname === '/flows') {
    requests.lists += 1
    if (listFailurePending) { listFailurePending = false; return json(response, { detail: 'isolated refresh failure' }, 503) }
    return json(response, flows)
  }
  if (request.method === 'POST' && url.pathname === '/flows') {
    const body = await readJson(request)
    requests.creates += 1
    const created = flow(body.name, { schedule: body.schedule, agent_profile: body.agent_profile, provider: body.provider, prompt_template: body.prompt_template, projectId: body.projectId })
    flows = [...flows, created]
    return json(response, created, 201)
  }
  const flowMatch = url.pathname.match(/^\/flows\/([^/]+)(?:\/(enable|disable|run))?$/)
  if (request.method === 'POST' && flowMatch?.[2] === 'run') {
    requests.runs += 1
    executionState = 'active'
    flows = flows.map(item => item.name === decodeURIComponent(flowMatch[1]) ? { ...item, last_run: '2026-08-22T02:00:00Z' } : item)
    return json(response, { executed: true })
  }
  if (request.method === 'POST' && (flowMatch?.[2] === 'enable' || flowMatch?.[2] === 'disable')) {
    requests.toggles += 1
    const enabled = flowMatch[2] === 'enable'
    flows = flows.map(item => item.name === decodeURIComponent(flowMatch[1]) ? { ...item, enabled } : item)
    return json(response, { success: true })
  }
  if (request.method === 'GET' && url.pathname === '/agents/profiles') return json(response, [{ name: 'developer', description: 'Implements and reviews code.', source: 'built-in' }])
  if (request.method === 'GET' && url.pathname === '/agents/providers') return json(response, [{ name: 'codex', binary: 'codex', installed: true, available: true, availability: 'AVAILABLE' }])
  if (request.method === 'GET' && url.pathname === '/projects') return json(response, [project])
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
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  const browserErrors = []
  page.on('pageerror', error => browserErrors.push(`page error: ${error.message}`))
  page.on('console', message => { if (message.type() === 'error' && !message.text().includes('503 (Service Unavailable)')) browserErrors.push(`console error: ${message.text()}`) })
  page.on('requestfailed', request => { if (request.failure()?.errorText !== 'net::ERR_ABORTED') browserErrors.push(`request failed: ${request.method()} ${request.url()} ${request.failure()?.errorText}`) })

  await page.goto(origin)
  await page.getByRole('link', { name: 'Flows' }).click()
  await page.getByText('existing-health-check', { exact: true }).waitFor()
  await page.getByText('existing-health-check', { exact: true }).click()
  await page.getByText('Inspect the repository and report health.', { exact: true }).waitFor()

  const createFlowOpener = page.getByRole('button', { name: 'Create Flow' })
  await createFlowOpener.click()
  await page.getByPlaceholder('my-daily-review').waitFor()
  await page.keyboard.press('Escape')
  await page.getByRole('dialog', { name: 'Create Flow' }).waitFor({ state: 'detached' })
  assert.equal(await createFlowOpener.evaluate(element => element === document.activeElement), true, 'Escape did not restore focus to Create Flow')
  await createFlowOpener.click()
  await page.getByPlaceholder('my-daily-review').fill('release-review')
  await page.getByText('Pick a schedule...', { exact: true }).click()
  await page.getByRole('button', { name: 'Every hour 0 * * * *', exact: true }).click()
  await page.getByText('Select a profile...', { exact: true }).click()
  await page.getByRole('button', { name: 'developer Implements and reviews code.', exact: true }).click()
  await page.getByPlaceholder('Describe what this flow should do...').fill('Run the bounded release review.')
  await page.getByRole('button', { name: 'Create Flow', exact: true }).last().click()
  await page.getByText('release-review', { exact: true }).waitFor()
  assert.equal(requests.creates, 1)

  await page.getByTitle('Disable flow').last().click()
  await page.getByText('disabled', { exact: true }).waitFor()
  await page.getByTitle('Enable flow').last().click()
  await page.getByText('enabled', { exact: true }).last().waitFor()
  assert.equal(requests.toggles, 2)

  await page.getByTitle('Run flow now').last().click()
  await page.getByText('Flow "release-review" launched an agent', { exact: true }).waitFor()
  assert.equal(requests.runs, 1)

  await page.reload()
  await page.getByRole('link', { name: 'Flows' }).click()
  await page.getByText('release-review', { exact: true }).waitFor()
  await page.getByText('release-review', { exact: true }).click()
  await page.getByText('Run the bounded release review.', { exact: true }).waitFor()

  listFailurePending = true
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')))
  await page.waitForTimeout(5_200)
  await page.getByText('release-review', { exact: true }).waitFor()
  await page.getByRole('alert').filter({ hasText: 'Unable to refresh Flows' }).waitFor()

  await page.getByRole('link', { name: 'Agents' }).click()
  await page.getByRole('button', { name: 'Expand flow-run-session' }).click()
  await page.getByTestId('agent-detail-card-flow-agent-0-active').getByText('In progress / Active', { exact: true }).waitFor()
  await page.getByTestId('agent-detail-card-flow-agent-1-completed').getByText('Completed', { exact: true }).waitFor()
  await page.getByTestId('agent-detail-card-flow-agent-2-cancelled').getByText('Cancelled', { exact: true }).waitFor()
  executionState = 'completed'
  await page.waitForTimeout(5_200)
  assert.equal(await page.getByTestId('agent-detail-card-flow-agent-0-active').count(), 0)
  assert(await page.getByText('Completed', { exact: true }).count() >= 1)

  const widths = []
  for (const width of [1440, 834, 390]) {
    await page.setViewportSize({ width, height: 900 })
    await page.getByRole('link', { name: 'Flows' }).click()
    await page.getByText('release-review', { exact: true }).waitFor()
    widths.push({ width, overflow: await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth) })
  }
  assert(widths.every(item => item.overflow === 0), `Flows viewport overflow: ${JSON.stringify(widths)}`)
  assert.deepEqual(browserErrors, [], `unexpected browser errors: ${JSON.stringify(browserErrors)}`)
  console.log(JSON.stringify({ flows: flows.map(item => ({ name: item.name, enabled: item.enabled, lastRun: item.last_run })), executionStates: ['active', 'completed', 'cancelled'], requests, widths }))
} finally {
  await browser?.close()
  await new Promise(resolve => server.close(resolve))
  await vite.close()
}
