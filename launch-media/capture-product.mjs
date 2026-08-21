import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { mkdir, mkdtemp, rename, rm } from 'node:fs/promises'
import http from 'node:http'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const productRoot = path.resolve(process.env.THREADCELLS_PRODUCT_ROOT || root)
const webRoot = path.join(productRoot, 'web')
process.chdir(webRoot)
const outputRoot = path.join(root, 'launch-media', 'output')
const screenshotRoot = path.join(outputRoot, 'screenshots')
const demoRoot = path.join(outputRoot, 'demo')
const websiteScreenshotRoot = path.join(root, 'website', 'public', 'media', 'screenshots')
const webRequire = createRequire(pathToFileURL(path.join(webRoot, 'package.json')))
const websiteRequire = createRequire(pathToFileURL(path.join(root, 'website', 'package.json')))
const { createServer: createViteServer } = await import(pathToFileURL(webRequire.resolve('vite')))
const react = (await import(pathToFileURL(webRequire.resolve('@vitejs/plugin-react')))).default
const playwrightModule = await import(pathToFileURL(webRequire.resolve('playwright')))
const { chromium } = playwrightModule.chromium ? playwrightModule : playwrightModule.default
const sharpModule = await import(pathToFileURL(websiteRequire.resolve('sharp')))
const sharp = sharpModule.default
const finalProductCommit = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: productRoot, encoding: 'utf8' }).trim()
const captureSelection = new Set(
  (process.env.THREADCELLS_CAPTURE_SET || 'agents,capacity,docs,spawn,demo')
    .split(',')
    .map(value => value.trim())
    .filter(Boolean),
)

if (captureSelection.has('home') && process.env.THREADCELLS_ALLOW_HOME_REPLACE !== '1') {
  throw new Error('threadcells-home.png is the owner-authoritative final product image; set THREADCELLS_ALLOW_HOME_REPLACE=1 to replace it intentionally')
}

if (captureSelection.has('docs')) {
  execFileSync(
    'python3',
    [path.join(productRoot, 'scripts', 'build_docs_bundle.py'), '--output', path.join(webRoot, 'public', 'docs-bundle.json')],
    { env: { ...process.env, THREADCELLS_SOURCE_REVISION: finalProductCommit }, stdio: 'inherit' },
  )
}

const branding = { title: 'ThreadCells', subtitle: 'Coding-agent operations console', logoUrl: '/threadcells-symbol.png', customLogo: false }
const projects = [{ projectId: 'project-atlas', name: 'Atlas Compiler', path: '/workspace/atlas-compiler', description: 'Synthetic launch-media fixture', isDefault: true }]
const sessions = [
  { id: 'session-atlas', name: 'cao-atlas-control', status: 'active', created_at: '2026-08-20T01:00:00Z' },
  { id: 'session-signal', name: 'cao-signal-gateway', status: 'active', created_at: '2026-08-20T00:20:00Z' },
]
const terminal = (id, session, profile, window) => ({
  id,
  tmux_session: session.name,
  tmux_window: String(window),
  provider: 'codex',
  agent_profile: profile,
  last_active: '2026-08-20T02:10:00Z',
  project_id: 'project-atlas',
  project_name: 'Atlas Compiler',
  project_path: '/workspace/atlas-compiler',
})
const terminalGroups = {
  'cao-atlas-control': [
    terminal('tm-sup-01', sessions[0], 'supervisor_terra_medium', 0),
    terminal('tm-web-02', sessions[0], 'frontend_sol_medium', 1),
    terminal('tm-review-03', sessions[0], 'reviewer_sol_high', 2),
  ],
  'cao-signal-gateway': [
    terminal('tm-dev-04', sessions[1], 'developer_terra_high', 0),
    terminal('tm-check-05', sessions[1], 'reviewer_terra_high', 1),
  ],
}
const profiles = [
  { name: 'supervisor_terra_medium', description: 'Coordinates bounded work and durable result delivery.', source: 'built-in', enabled: true, built_in: true, execution_mode: 'orchestrator' },
  { name: 'frontend_sol_medium', description: 'Implements design-critical production frontend contours.', source: 'built-in', enabled: true, built_in: true, execution_mode: 'executor' },
  { name: 'developer_terra_high', description: 'Owns substantive production implementation and debugging.', source: 'built-in', enabled: true, built_in: true, execution_mode: 'executor' },
  { name: 'reviewer_sol_high', description: 'Reviews critical security, lifecycle, and concurrency boundaries.', source: 'built-in', enabled: true, built_in: true, execution_mode: 'reviewer' },
  { name: 'reviewer_terra_high', description: 'Performs independent production acceptance review.', source: 'built-in', enabled: true, built_in: true, execution_mode: 'reviewer' },
]
const providers = [
  { name: 'codex', binary: 'codex', adapter_available: true, installed: true, available: true, availability: 'INSTALLED_AND_READY', state: 'available', authentication: 'authenticated', version: 'codex-cli 0.146.0', capabilities: { usage: 'supported' } },
  { name: 'claude_code', binary: 'claude', adapter_available: true, installed: false, available: false, availability: 'NOT_INSTALLED', state: 'unavailable', authentication: 'unknown', version: null, capabilities: { usage: 'conditional' } },
]
const capacity = {
  resource_state: 'GREEN', reasons: [],
  resident_supervisors: { active: 2, limit: 5, available: 3, certain: true, draining: false },
  provider_executions: { active: 2, limit: 3, available: 1, certain: true, draining: false },
  work_contexts: { active: 2, limit: 2, available: 0, certain: true, draining: false },
  heavy_executions: { active: 0, limit: 1, available: 1, waiting: null, draining: false },
  memory: { available_mib: 6144, swap_total_mib: 4096, swap_free_mib: 4096 },
  root_disk: { used_percent: 42.8, free_gib: 183.6 },
  memory_pressure: { some_avg10: 0.02, full_avg10: 0 },
  cpu_load: { one_minute: 1.8, cpu_count: 12 },
  housekeeping: { ok: true },
}

let demoStartedAt = 0
const staticStatus = {
  'tm-sup-01': { status: 'processing', lifecycle: 'running', workflow_state: 'active', execution_state: 'processing', context_role: 'supervisor' },
  'tm-web-02': { status: 'idle', lifecycle: 'running', workflow_state: 'result_ready', execution_state: 'ready', context_role: 'work' },
  'tm-review-03': { status: 'processing', lifecycle: 'running', workflow_state: 'active', execution_state: 'processing', context_role: 'work' },
  'tm-dev-04': { status: 'idle', lifecycle: 'running', workflow_state: 'completed', execution_state: 'ready', context_role: 'work' },
  'tm-check-05': { status: 'idle', lifecycle: 'running', workflow_state: 'waiting', execution_state: 'ready', context_role: 'work' },
}

function statusFor(id) {
  if (!demoStartedAt || !['tm-sup-01', 'tm-web-02', 'tm-review-03'].includes(id)) return staticStatus[id]
  const elapsed = (Date.now() - demoStartedAt) / 1000
  if (elapsed < 2.2) {
    if (id === 'tm-sup-01') return { status: 'processing', lifecycle: 'running', workflow_state: 'active', execution_state: 'processing', context_role: 'supervisor' }
    if (id === 'tm-web-02') return { status: 'idle', lifecycle: 'running', workflow_state: 'open', execution_state: 'queued_provider_execution', context_role: 'work' }
    return { status: 'idle', lifecycle: 'running', workflow_state: 'waiting', execution_state: 'ready', context_role: 'work' }
  }
  if (elapsed < 5) {
    if (id === 'tm-web-02') return { status: 'processing', lifecycle: 'running', workflow_state: 'active', execution_state: 'processing', context_role: 'work' }
    return id === 'tm-sup-01'
      ? { status: 'idle', lifecycle: 'running', workflow_state: 'waiting', execution_state: 'ready', context_role: 'supervisor' }
      : { status: 'idle', lifecycle: 'running', workflow_state: 'waiting', execution_state: 'ready', context_role: 'work' }
  }
  if (elapsed < 7.7) {
    if (id === 'tm-web-02') return { status: 'idle', lifecycle: 'running', workflow_state: 'result_ready', execution_state: 'ready', context_role: 'work' }
    if (id === 'tm-review-03') return { status: 'processing', lifecycle: 'running', workflow_state: 'active', execution_state: 'processing', context_role: 'work' }
    return { status: 'idle', lifecycle: 'running', workflow_state: 'waiting', execution_state: 'ready', context_role: 'supervisor' }
  }
  return id === 'tm-sup-01'
    ? { status: 'idle', lifecycle: 'running', workflow_state: 'completed', execution_state: 'ready', context_role: 'supervisor' }
    : { status: 'idle', lifecycle: 'running', workflow_state: 'completed', execution_state: 'ready', context_role: 'work' }
}

function activityFor(status) {
  if (status.lifecycle === 'exited') return 'exited'
  if (status.execution_state === 'processing') return 'processing'
  if (status.execution_state === 'queued_provider_execution') return 'queued'
  return 'ready'
}

function agentSummaries() {
  return Object.values(terminalGroups).flat().map(meta => {
    const status = statusFor(meta.id)
    return {
      id: meta.id,
      name: meta.id,
      provider: meta.provider,
      session_name: meta.tmux_session,
      agent_profile: meta.agent_profile,
      activity: activityFor(status),
      execution_state: status.execution_state,
      lifecycle: status.lifecycle,
      workflow_state: status.workflow_state,
      workflow_status: status.workflow_state,
      assignment_status: status.workflow_state === 'active' ? 'accepted' : null,
      result_status: status.workflow_state === 'result_ready' ? 'ready' : null,
      delivery_status: status.workflow_state === 'completed' ? 'acknowledged' : null,
      context_role: status.context_role,
      launch_worktree: null,
      managed_worktree_kind: null,
      managed_worktree_commit: null,
      managed_worktree_branch: null,
      projectId: meta.project_id,
      project_name: meta.project_name,
      project_path: meta.project_path,
      last_active: meta.last_active,
    }
  })
}

function countBy(items, field) {
  return items.reduce((counts, item) => {
    const value = item[field]
    if (value) counts[value] = (counts[value] || 0) + 1
    return counts
  }, {})
}

function sessionSummaries() {
  const agents = agentSummaries()
  return sessions.map(session => {
    const members = agents.filter(agent => agent.session_name === session.name)
    return {
      ...session,
      agent_count: members.length,
      active_agent_count: members.filter(agent => agent.activity !== 'exited').length,
      workflow_counts: countBy(members, 'workflow_state'),
      activity_counts: countBy(members, 'activity'),
      project_name: members[0]?.project_name || null,
      last_active: members.map(agent => agent.last_active).filter(Boolean).sort().at(-1) || null,
    }
  })
}

function page(items, limit, offset) {
  const selected = items.slice(offset, offset + limit)
  return { items: selected, total: items.length, limit, offset, next_offset: offset + selected.length < items.length ? offset + selected.length : null }
}

const json = (response, value, status = 200) => {
  response.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' })
  response.end(JSON.stringify(value))
}

async function createFixtureServer() {
  const vite = await createViteServer({
    root: webRoot,
    // The fixture owns API routing. Loading the development config here would
    // proxy deep UI routes such as /settings to a live backend instead of the
    // isolated synthetic application.
    configFile: false,
    plugins: [react()],
    define: { __THREADCELLS_REVISION__: JSON.stringify(finalProductCommit), __THREADCELLS_VERSION__: JSON.stringify('0.1.0a1') },
    appType: 'spa',
    server: { middlewareMode: true, hmr: false },
  })
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://localhost')
    if (request.method === 'GET' && url.pathname === '/ui/overview') {
      const agents = agentSummaries()
      return json(response, {
        sessions: sessions.length,
        agents: agents.length,
        active: agents.filter(agent => agent.activity !== 'exited').length,
        waiting: agents.filter(agent => agent.workflow_state === 'waiting').length,
        owner_gate: agents.filter(agent => agent.workflow_state === 'owner_gate').length,
        cancelled: agents.filter(agent => agent.workflow_state === 'cancelled').length,
        completed: agents.filter(agent => agent.workflow_state === 'completed').length,
      })
    }
    if (request.method === 'GET' && url.pathname === '/ui/sessions') {
      const query = (url.searchParams.get('query') || '').toLowerCase()
      const summaries = sessionSummaries().filter(session => !query || session.name.toLowerCase().includes(query))
      return json(response, page(summaries, Number(url.searchParams.get('limit') || 10), Number(url.searchParams.get('offset') || 0)))
    }
    if (request.method === 'GET' && url.pathname === '/ui/agents') {
      const query = (url.searchParams.get('query') || '').toLowerCase()
      const sessionName = url.searchParams.get('session_name')
      const activities = (url.searchParams.get('activity') || '').split(',').filter(Boolean)
      const workflowStates = (url.searchParams.get('workflow_state') || '').split(',').filter(Boolean)
      const requestedProfiles = (url.searchParams.get('profile') || '').split(',').filter(Boolean)
      const allAgents = agentSummaries()
      const filtered = allAgents.filter(agent =>
        (!sessionName || agent.session_name === sessionName)
        && (!query || `${agent.name} ${agent.session_name} ${agent.agent_profile}`.toLowerCase().includes(query))
        && (!activities.length || activities.includes(agent.activity))
        && (!workflowStates.length || workflowStates.includes(agent.workflow_state))
        && (!requestedProfiles.length || requestedProfiles.includes(agent.agent_profile)),
      )
      return json(response, {
        ...page(filtered, Number(url.searchParams.get('limit') || 40), Number(url.searchParams.get('offset') || 0)),
        facets: {
          activities: [...new Set(allAgents.map(agent => agent.activity).filter(Boolean))].sort(),
          workflow_states: [...new Set(allAgents.map(agent => agent.workflow_state).filter(Boolean))].sort(),
          profiles: [...new Set(allAgents.map(agent => agent.agent_profile).filter(Boolean))].sort(),
        },
      })
    }
    if (request.method === 'GET' && url.pathname === '/sessions') return json(response, sessions)
    const matchedSession = sessions.find((session) => url.pathname === `/sessions/${session.name}`)
    if (request.method === 'GET' && matchedSession) return json(response, { session: matchedSession, terminals: terminalGroups[matchedSession.name] })
    if (request.method === 'GET' && /^\/terminals\/[^/]+$/.test(url.pathname)) {
      const id = url.pathname.split('/')[2]
      const meta = Object.values(terminalGroups).flat().find((item) => item.id === id)
      if (!meta) return json(response, { detail: 'Not found' }, 404)
      return json(response, { id, name: id, provider: meta.provider, session_name: meta.tmux_session, agent_profile: meta.agent_profile, last_active: meta.last_active, ...statusFor(id) })
    }
    if (request.method === 'GET' && url.pathname === '/agents/profiles') return json(response, profiles)
    if (request.method === 'GET' && url.pathname === '/agents/providers') return json(response, providers)
    if (request.method === 'GET' && url.pathname === '/projects') return json(response, projects)
    if (request.method === 'GET' && url.pathname === '/settings/branding') return json(response, branding)
    if (request.method === 'GET' && url.pathname === '/settings/agent-dirs') return json(response, { agent_dirs: {}, extra_dirs: [] })
    if (request.method === 'GET' && url.pathname === '/settings/orchestration-capacity') return json(response, capacity)
    if (request.method === 'GET' && url.pathname === '/operator/session') return json(response, { configured: true, configuration_state: 'ready', authenticated: false, expires_in_seconds: 0, session_ttl_seconds: 300, verifier_reference: 'THREADCELLS_OPERATOR_VERIFIER_FILE' })
    if (request.method === 'GET' && url.pathname === '/usage/statistics') return json(response, { label: 'Provider-reported usage — not a billing statement', global: { provider_run_count: 8, input_tokens: 184200, cached_input_tokens: 93600, cache_write_input_tokens: 0, output_tokens: 21800, reasoning_output_tokens: 6200, total_tokens: 206000 }, terminals: [], sessions: [], projects: [], providers: [], profiles: [] })
    if (request.method === 'GET' && url.pathname === '/flows') return json(response, [])
    vite.middlewares(request, response)
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  assert(address && typeof address !== 'string')
  return {
    vite,
    server,
    origin: `http://127.0.0.1:${address.port}`,
    close: async () => {
      await new Promise((resolve) => server.close(resolve))
      await vite.close()
    },
  }
}

async function writeScreenshot(page, name, options = {}) {
  const png = path.join(screenshotRoot, `${name}.png`)
  const webp = path.join(websiteScreenshotRoot, `${name}.webp`)
  await page.screenshot({ path: png, fullPage: false, animations: 'disabled', ...options })
  await sharp(png).webp({ quality: 86, effort: 6 }).toFile(webp)
  return { png, webp }
}

async function captureScreenshots(browser, origin) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 }, deviceScaleFactor: 1, reducedMotion: 'reduce', colorScheme: 'dark' })
  const page = await context.newPage()
  const pageErrors = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  const captured = []
  if (captureSelection.has('home')) {
    await page.goto(origin, { waitUntil: 'networkidle' })
    await page.getByText('Sessions', { exact: true }).first().waitFor()
    await page.waitForTimeout(700)
    await writeScreenshot(page, 'threadcells-home')
    captured.push('threadcells-home')
  }
  if (captureSelection.has('agents')) {
    await page.goto(`${origin}/?tab=agents`, { waitUntil: 'networkidle' })
    await page.getByRole('tab', { name: 'Profiles' }).click()
    await page.getByText(/Matching agents/).waitFor()
    await page.waitForTimeout(500)
    await writeScreenshot(page, 'threadcells-agents')
    captured.push('threadcells-agents')
  }
  if (captureSelection.has('capacity')) {
    await page.goto(`${origin}/settings`, { waitUntil: 'networkidle' })
    await page.getByText('Orchestration Capacity', { exact: true }).waitFor()
    await page.waitForTimeout(500)
    await writeScreenshot(page, 'threadcells-capacity')
    captured.push('threadcells-capacity')
  }
  if (captureSelection.has('docs')) {
    await page.goto(`${origin}/docs/overview`, { waitUntil: 'networkidle' })
    await page.getByRole('heading', { name: 'What is ThreadCells?' }).waitFor()
    await page.waitForTimeout(400)
    await writeScreenshot(page, 'threadcells-docs')
    captured.push('threadcells-docs')
  }
  if (captureSelection.has('spawn')) {
    await page.goto(`${origin}/?tab=agents`, { waitUntil: 'networkidle' })
    await page.getByRole('button', { name: 'Create Session & Spawn Agent' }).click()
    await page.getByRole('heading', { name: 'Create Session & Spawn Agent' }).waitFor()
    await page.waitForTimeout(350)
    await writeScreenshot(page, 'threadcells-spawn')
    captured.push('threadcells-spawn')
  }

  assert.deepEqual(pageErrors, [], `Product UI page errors: ${pageErrors.join('; ')}`)
  await context.close()
  return captured
}

async function captureDemo(browser, origin) {
  const rawVideoDir = await mkdtemp(path.join(demoRoot, '.raw-'))
  const context = await browser.newContext({ viewport: { width: 1280, height: 720 }, recordVideo: { dir: rawVideoDir, size: { width: 1280, height: 720 } }, colorScheme: 'dark' })
  try {
    const page = await context.newPage()
    await page.addInitScript(() => {
      const original = window.setInterval
      window.setInterval = (handler, timeout, ...args) => original(handler, timeout === 3000 ? 600 : timeout === 5000 ? 800 : timeout, ...args)
    })
    const video = page.video()
    demoStartedAt = Date.now()
    await page.goto(origin, { waitUntil: 'networkidle' })
    await page.getByText('Sessions', { exact: true }).first().waitFor()
    await page.waitForTimeout(9200)
    await context.close()
    const rawPath = await video.path()
    const outputPath = path.join(demoRoot, 'threadcells-demo.webm')
    await rename(rawPath, outputPath)
    return outputPath
  } finally {
    demoStartedAt = 0
    await context.close().catch(() => {})
    await rm(rawVideoDir, { recursive: true, force: true })
  }
}

await Promise.all([mkdir(screenshotRoot, { recursive: true }), mkdir(demoRoot, { recursive: true }), mkdir(websiteScreenshotRoot, { recursive: true })])
const fixture = await createFixtureServer()
let browser
try {
  browser = await chromium.launch({ headless: true })
  const screenshots = await captureScreenshots(browser, fixture.origin)
  const demo = captureSelection.has('demo') ? await captureDemo(browser, fixture.origin) : null
  console.log(JSON.stringify({ synthetic: true, productRevision: finalProductCommit, productRoot, screenshotRoot, websiteScreenshotRoot, demo, screenshots }))
} finally {
  await browser?.close()
  await fixture.close()
}
