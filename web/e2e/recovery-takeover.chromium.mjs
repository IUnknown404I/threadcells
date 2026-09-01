import assert from 'node:assert/strict'
import http from 'node:http'
import { fileURLToPath } from 'node:url'
import { createServer as createViteServer } from 'vite'
import { chromium } from 'playwright'

const webRoot = fileURLToPath(new URL('..', import.meta.url))
const session = { id: 'recovery-lifetime', name: 'cao-recovery-old', status: 'active', created_at: '1' }
const terminal = {
  id: 'a11ce001',
  tmux_session: session.name,
  tmux_window: 'old-owner',
  provider: 'codex',
  agent_profile: 'critical_sol_xhigh_owner',
  project_id: 'project-1',
  project_name: 'Recovery Project',
  project_path: '/workspace/recovery-project',
  last_active: null,
}
const authorityGeneration = 'a'.repeat(32)
const runtimeGeneration = '11111111-1111-4111-8111-111111111111'
const operatorSecret = 'correct-recovery-secret'
const grantRequests = []
const takeoverRequests = []
const sessionSummary = {
  ...session,
  agent_count: 1,
  active_agent_count: 1,
  workflow_counts: { recoverable: 1 },
  activity_counts: { idle: 1 },
  project_name: terminal.project_name,
  last_active: '1',
  first_agent: { id: terminal.id, activity: 'idle', execution_state: 'ready', lifecycle: 'running', workflow_state: 'recoverable', workflow_reason: 'provider unavailable' },
  last_agent: { id: terminal.id, activity: 'idle', execution_state: 'ready', lifecycle: 'running', workflow_state: 'recoverable', workflow_reason: 'provider unavailable' },
}
const agentSummary = {
  id: terminal.id,
  name: terminal.tmux_window,
  provider: terminal.provider,
  session_id: session.id,
  session_name: session.name,
  agent_profile: terminal.agent_profile,
  activity: 'idle',
  execution_state: 'ready',
  lifecycle: 'running',
  workflow_state: 'recoverable',
  workflow_reason: 'provider unavailable',
  context_role: 'supervisor',
  launch_worktree: terminal.project_path,
  projectId: terminal.project_id,
  project_name: terminal.project_name,
  project_path: terminal.project_path,
  last_active: null,
}

const vite = await createViteServer({
  root: webRoot,
  configFile: false,
  plugins: [(await import('@vitejs/plugin-react')).default()],
  define: { __THREADCELLS_REVISION__: JSON.stringify('recovery-evidence'), __THREADCELLS_VERSION__: JSON.stringify('0.3.3-alpha') },
  appType: 'spa',
  server: { middlewareMode: true, hmr: false },
})
const json = (response, value, status = 200, headers = {}) => {
  response.writeHead(status, { 'content-type': 'application/json', ...headers })
  response.end(JSON.stringify(value))
}
const requestJson = async request => {
  let body = ''
  for await (const chunk of request) body += chunk
  return body ? JSON.parse(body) : null
}

const server = http.createServer(async (request, response) => {
  const url = new URL(request.url, 'http://localhost')
  if (request.method === 'GET' && url.pathname === '/ui/sessions') return json(response, { items: [sessionSummary], total: 1, limit: 10, offset: 0, next_offset: null })
  if (request.method === 'GET' && url.pathname === '/ui/agents') return json(response, { items: [agentSummary], total: 1, limit: 40, offset: 0, next_offset: null, facets: { activities: ['idle'], workflow_states: ['recoverable'], profiles: [terminal.agent_profile] } })
  if (request.method === 'GET' && url.pathname === '/sessions') return json(response, [session])
  if (request.method === 'GET' && [session.name, session.id].some(id => url.pathname === `/sessions/${id}`)) return json(response, { session, terminals: [terminal] })
  if (request.method === 'GET' && url.pathname === `/terminals/${terminal.id}`) return json(response, { ...terminal, activity: 'idle', status: 'idle', lifecycle: 'running', context_role: 'supervisor', launch_worktree: terminal.project_path })
  if (request.method === 'GET' && url.pathname === `/terminals/${terminal.id}/recovery-takeover/preview`) return json(response, {
    eligible: true,
    reason_code: null,
    runtime_absent: false,
    terminal: {
      ...terminal,
      session_id: session.id,
      launch_worktree: terminal.project_path,
      runtime_lifecycle: 'running',
      writer_authority_generation: authorityGeneration,
      runtime_generation: runtimeGeneration,
    },
    worktree: { state: 'dirty', dirty: true, reason_code: null },
    consequence: 'OLD_SUPERVISOR_PERMANENTLY_LOSES_WRITER_AUTHORITY',
  })
  if (request.method === 'GET' && url.pathname === '/agents/providers') return json(response, [{ name: 'codex', binary: 'codex', installed: true }])
  if (request.method === 'GET' && url.pathname === '/agents/profiles') return json(response, [{ name: terminal.agent_profile, description: 'Exceptional owner executor', source: 'built-in', owner_authorization_required: true }])
  if (request.method === 'GET' && url.pathname === '/projects') return json(response, [])
  if (request.method === 'GET' && url.pathname === '/ui/overview') return json(response, { sessions: 1, agents: 1, active: 1, waiting: 1, owner_gate: 0, cancelled: 0, completed: 0 })
  if (request.method === 'POST' && url.pathname === '/operator/session') {
    const body = await requestJson(request)
    if (body?.secret !== operatorSecret) return json(response, { detail: { reason_code: 'OPERATOR_AUTHENTICATION_FAILED' } }, 401)
    return json(response, { authenticated: true }, 200, { 'set-cookie': 'threadcells_operator_session=opaque-session; HttpOnly; SameSite=Strict; Path=/' })
  }
  if (request.method === 'POST' && url.pathname === '/operator/xhigh-grants') {
    grantRequests.push(await requestJson(request))
    const index = grantRequests.length
    return json(response, { launch_id: `recovery-launch-${index}`, grant: `one-use-recovery-grant-${index}`, expires_in_seconds: 60 })
  }
  if (request.method === 'POST' && url.pathname === `/terminals/${terminal.id}/recovery-takeover`) {
    takeoverRequests.push({ body: await requestJson(request), grant: request.headers['x-threadcells-owner-grant'], url: request.url })
    const index = takeoverRequests.length
    return json(response, { id: `takeover-${index}`, request_id: takeoverRequests[index - 1].body.request_id, old_terminal_id: terminal.id, new_terminal_id: `b22ce00${index}`, state: 'completed', failure_reason: null }, 201)
  }
  vite.middlewares(request, response)
})

await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const address = server.address()
assert(address && typeof address !== 'string')
const origin = `http://127.0.0.1:${address.port}`
let browser
try {
  browser = await chromium.launch({ headless: true })
  for (const width of [390, 834, 1440]) {
    const page = await browser.newPage({ viewport: { width, height: 960 } })
    await page.goto(origin)
    await page.getByRole('link', { name: 'Agents' }).click()
    await page.getByRole('button', { name: 'Expand recovery-old' }).click()
    await page.getByTitle('Recover supervisor authority').click()
    const dialog = page.getByRole('dialog', { name: 'Recover supervisor authority' })
    await dialog.waitFor()
    await page.getByLabel('Operator secret').fill(operatorSecret)
    await page.getByRole('button', { name: 'Authenticate & inspect' }).click()
    await page.getByText('Dirty — uncommitted state will be preserved').waitFor()

    const geometry = await dialog.evaluate(element => ({
      overflow: element.scrollWidth - element.clientWidth,
      right: element.getBoundingClientRect().right,
      left: element.getBoundingClientRect().left,
    }))
    assert(geometry.left >= 0 && geometry.right <= width, `recovery dialog exceeded ${width}px viewport`)
    assert(geometry.overflow <= 1, `recovery dialog overflowed horizontally at ${width}px`)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), 0, `page overflowed horizontally at ${width}px`)

    await page.getByLabel('Confirm recovery takeover').check()
    await page.getByRole('button', { name: 'Take over supervisor' }).click()
    await page.getByText(/Recovery supervisor b22ce00\d is authoritative/).waitFor()
    const storage = await page.evaluate(() => ({ local: { ...localStorage }, session: { ...sessionStorage } }))
    assert(!JSON.stringify(storage).includes(operatorSecret), 'operator secret entered browser storage')
    await page.close()
  }

  assert.equal(grantRequests.length, 3)
  assert.equal(takeoverRequests.length, 3)
  grantRequests.forEach(request => assert.deepEqual(request, {
    agent_profile: terminal.agent_profile,
    provider: 'codex',
    project_id: terminal.project_id,
    launch_mode: 'recovery_takeover',
    target_terminal_id: terminal.id,
    expected_authority_generation: authorityGeneration,
    expected_runtime_generation: runtimeGeneration,
    confirmed: true,
  }))
  takeoverRequests.forEach((request, index) => {
    assert.equal(request.grant, `one-use-recovery-grant-${index + 1}`)
    assert(!request.url.includes(request.grant), 'owner grant leaked into takeover URL')
    assert.equal(request.body.owner_grant_launch_id, `recovery-launch-${index + 1}`)
    assert.equal(request.body.expected_authority_generation, authorityGeneration)
    assert.equal(request.body.expected_runtime_generation, runtimeGeneration)
  })
  assert(!JSON.stringify(grantRequests).includes(operatorSecret), 'operator secret leaked into grant requests')
  assert(!JSON.stringify(takeoverRequests).includes(operatorSecret), 'operator secret leaked into takeover requests')
  console.log(JSON.stringify({ widths: [390, 834, 1440], assertions: ['explicit recovery action', 'dirty-state disclosure', 'exact scoped grant', 'grant header secrecy', 'responsive dialog without horizontal overflow'] }))
} finally {
  await browser?.close()
  await new Promise(resolve => server.close(resolve))
  await vite.close()
}
