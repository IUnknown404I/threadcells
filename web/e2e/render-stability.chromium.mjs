import assert from 'node:assert/strict'
import http from 'node:http'
import { fileURLToPath } from 'node:url'
import { createServer as createViteServer } from 'vite'
import { chromium } from 'playwright'

const webRoot = fileURLToPath(new URL('..', import.meta.url))
const sessionId = 'render-stability-session'
const terminalId = 'render-stability-terminal'
const fixtureTerminalId = `${terminalId}-0`
const fixtureResultId = 'result-70'
const session = { id: sessionId, name: sessionId, status: 'active', created_at: '1' }
const terminals = Array.from({ length: 10 }, (_, index) => ({ id: `${terminalId}-${index}`, tmux_session: sessionId, tmux_window: String(index), provider: 'codex', agent_profile: 'developer', last_active: null }))
const runtimeBranding = { title: 'ThreadCells', subtitle: 'Multi-agent control plane', logoUrl: '/threadcells-symbol.png', customLogo: false }
const projects = [{ projectId: 'project-fixture', name: 'Fixture Project', path: '/srv/fixture-project', description: 'render fixture', isDefault: true }]
let messages = Array.from({ length: 80 }, (_, index) => ({
  id: `message-${index}`,
  sender_id: index % 2 ? fixtureTerminalId : 'owner',
  receiver_id: index % 2 ? 'owner' : fixtureTerminalId,
  message: `Fixture message ${index}: ${'stable scroll ownership '.repeat(12)}`,
  status: 'delivered',
  result_id: index === 70 ? fixtureResultId : null,
  created_at: new Date(1_700_000_000_000 + index * 1000).toISOString(),
}))

const vite = await createViteServer({ root: webRoot, configFile: false, plugins: [(await import('@vitejs/plugin-react')).default()], define: { __THREADCELLS_REVISION__: JSON.stringify('render-stability-evidence'), __THREADCELLS_VERSION__: JSON.stringify('0.1.0-alpha.1') }, appType: 'spa', server: { middlewareMode: true, hmr: false } })
function json(response, value) { response.writeHead(200, { 'content-type': 'application/json' }); response.end(JSON.stringify(value)) }
const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://localhost')
  if (request.method === 'GET' && url.pathname === '/sessions') return json(response, [session])
  if (request.method === 'GET' && url.pathname === `/sessions/${sessionId}`) return json(response, { session, terminals })
  if (request.method === 'GET' && url.pathname === `/terminals/${fixtureTerminalId}/inbox/messages`) return json(response, messages)
  if (request.method === 'GET' && url.pathname === '/delegation-results') return json(response, [{ id: fixtureResultId, delegation_kind: 'assign', status: 'complete', delivery_status: 'result_delivered', authorship: 'fixture', document: { summary: 'fixture result', body_markdown: 'durable result body' }, created_at: null, finalized_at: null }])
  if (request.method === 'GET' && url.pathname === `/delegation-results/${fixtureResultId}`) return json(response, { id: fixtureResultId, delegation_kind: 'assign', status: 'complete', delivery_status: 'result_delivered', authorship: 'fixture', document: { summary: 'fixture result', body_markdown: 'durable result body' }, created_at: null, finalized_at: null })
  if (request.method === 'GET' && url.pathname.startsWith('/terminals/')) return json(response, { id: url.pathname.split('/')[2], name: terminalId, provider: 'codex', session_name: sessionId, agent_profile: 'developer', status: 'idle', lifecycle: 'running', workflow_state: 'active', last_active: null })
  if (request.method === 'GET' && url.pathname === '/agents/providers') return json(response, [{ name: 'codex', binary: 'codex', installed: true }])
  if (request.method === 'GET' && url.pathname === '/agents/profiles') return json(response, [{ name: 'developer', description: '', source: 'built-in' }])
  if (request.method === 'GET' && url.pathname === '/settings/branding') return json(response, runtimeBranding)
  if (request.method === 'GET' && url.pathname === '/settings/agent-dirs') return json(response, { agent_dirs: {}, extra_dirs: [] })
  if (request.method === 'GET' && url.pathname === '/settings/orchestration-capacity') return json(response, { resource_state: 'GREEN', reasons: [], resident_supervisors: { active: 0, limit: 5, available: 5, certain: true }, provider_executions: { active: 0, limit: 3, available: 3, certain: true }, work_contexts: { active: 0, limit: 2, available: 2, certain: true }, heavy_executions: { active: 0, limit: 1, available: 1, waiting: null }, memory: { available_mib: 1024, swap_total_mib: 0, swap_free_mib: 0 }, root_disk: { used_percent: 1, free_gib: 100 }, memory_pressure: { some_avg10: 0, full_avg10: 0 }, cpu_load: { one_minute: 0, cpu_count: 1 }, housekeeping: { ok: true } })
  if (request.method === 'GET' && url.pathname === '/api/v1/profiles') return json(response, [{ profile_id: 'supervisor_sol_medium', display_name: 'Sol supervisor', description: 'High-reasoning orchestration', enabled: true, built_in: true, revision_id: 'profile-rev-1', revision_number: 1, fingerprint: 'abc', document: { execution_mode: 'orchestrator' } }])
  if (request.method === 'GET' && url.pathname === '/api/v1/providers') return json(response, { api_version: '1.0', entry_point_group: 'threadcells.provider_adapters.v1', adapters: [{ adapter_id: 'codex', description: 'Reference adapter', plugin_api_version: '1.0' }], configurations: [{ config_id: 'builtin-codex', display_name: 'Codex', enabled: true, built_in: true, revision_id: 'provider-rev-1', revision_number: 1, fingerprint: 'def', document: { adapter_id: 'codex' } }], load_failures: [] })
  if (request.method === 'GET' && url.pathname === '/api/v1/housekeeping') return json(response, { schema_version: 1, policy: { logs: { enabled: true, compress_after_minutes: 1440, retain_minutes: 10080 }, attachments: { enabled: true, retain_minutes: 10080 }, ephemeral: { enabled: true }, browser_cache: { enabled: true, retain_minutes: 10080 }, package_cache: { enabled: true }, releases: { enabled: true, retain_count: 2, retain_minutes: 10080 }, backups: { enabled: false } }, schedule: { frequent: 'every 6 hours', weekly: 'weekly', pressure: 'on RED disk recovery' } })
  if (request.method === 'GET' && url.pathname === '/api/v1/housekeeping/report') return json(response, { status: 'never_run' })
  if (request.method === 'GET' && url.pathname === '/projects') return json(response, projects)
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
  page.on('pageerror', error => console.error(`browser page error: ${error.message}`))
  page.on('requestfailed', request => console.error(`browser request failed: ${request.method()} ${request.url()} ${request.failure()?.errorText}`))
  await page.addInitScript(() => {
    const original = window.setInterval
    window.setInterval = (handler, timeout, ...args) => original(handler, timeout === 5000 ? 80 : timeout === 3000 ? 60 : timeout, ...args)
  })
  await page.goto(origin)
  await page.getByText(sessionId, { exact: true }).waitFor()
  const widths = []
  const terminalDetail = page.getByTestId(`agent-detail-card-${fixtureTerminalId}`)
  const restoreAgentDetail = async () => {
    if (!await terminalDetail.isVisible()) await page.getByRole('button', { name: `Expand ${sessionId}` }).click()
    await terminalDetail.waitFor()
  }
  for (const width of [1440, 834, 390]) {
    await page.setViewportSize({ width, height: 900 })
    await page.getByRole('link', { name: 'Home' }).click()
    await page.getByText(sessionId, { exact: true }).waitFor()
    widths.push({ width, surface: 'Home', overflow: await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth) })
    await page.getByRole('link', { name: 'Agents' }).click()
    await restoreAgentDetail()
    await terminalDetail.getByRole('button', { name: 'Inbox', exact: true }).waitFor()
    widths.push({ width, surface: 'Agents', overflow: await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth) })
    await page.getByRole('link', { name: 'Settings' }).click()
    await page.getByRole('heading', { name: 'Runtime Branding' }).waitFor()
    await page.getByRole('heading', { name: 'Projects' }).waitFor()
    widths.push({ width, surface: 'Settings branding/projects', overflow: await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth) })
    for (const [name, heading] of [['Profiles', 'Profile Registry'], ['Providers', 'Provider Adapters'], ['Housekeeping', 'Housekeeping']]) {
      await page.getByRole('navigation', { name: 'Settings sections' }).getByRole('link', { name }).click()
      await page.getByRole('heading', { name: heading }).waitFor()
      widths.push({ width, surface: `Settings ${name}`, overflow: await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth) })
    }
  }
  assert(widths.every(item => item.overflow === 0), `viewport overflow: ${JSON.stringify(widths)}`)

  await page.setViewportSize({ width: 834, height: 900 })
  await page.getByRole('link', { name: 'Agents' }).click()
  await restoreAgentDetail()
  await terminalDetail.getByRole('button', { name: 'Inbox', exact: true }).click()
  const list = page.getByTestId('inbox-message-list')
  await list.waitFor()
  await page.waitForTimeout(150)
  const metric = () => list.evaluate(node => ({ top: node.scrollTop, max: node.scrollHeight - node.clientHeight }))
  const position = () => list.evaluate(node => ({ top: node.scrollTop }))
  const initial = await metric()
  assert(initial.max > 300, `fixture was not scrollable: ${JSON.stringify(initial)}`)
  assert(initial.max - initial.top < 24, `initial open must be at latest: ${JSON.stringify(initial)}`)
  await list.evaluate(node => { node.scrollTop = 120; node.dispatchEvent(new Event('scroll', { bubbles: true })) })
  const upward = await position()
  await page.waitForTimeout(160)
  const identicalPoll = await position()
  messages = messages.map(message => message.id === 'message-10' ? { ...message, status: 'failed' } : message)
  await page.waitForTimeout(160)
  const statusUpdate = await position()
  await page.getByRole('button', { name: `Open durable result ${fixtureResultId.slice(0, 8)}` }).dispatchEvent('click')
  await page.getByText('durable result body').waitFor()
  const resultExpand = await position()
  messages = [...messages, { id: 'message-new-unpinned', sender_id: 'owner', receiver_id: fixtureTerminalId, message: 'new message while unpinned', status: 'delivered', result_id: null, created_at: new Date().toISOString() }]
  await page.waitForTimeout(160)
  const newUnpinned = await position()
  assert.deepEqual(identicalPoll, upward, 'identical poll changed scroll position')
  assert.deepEqual(statusUpdate, upward, 'status update changed scroll position')
  assert.deepEqual(resultExpand, upward, 'durable result toggle changed scroll position')
  assert.deepEqual(newUnpinned, upward, 'new message changed unpinned scroll position')
  await list.evaluate(node => { node.scrollTop = node.scrollHeight; node.dispatchEvent(new Event('scroll', { bubbles: true })) })
  messages = [...messages, { id: 'message-new-pinned', sender_id: 'owner', receiver_id: fixtureTerminalId, message: 'new message while pinned', status: 'delivered', result_id: null, created_at: new Date().toISOString() }]
  await page.waitForTimeout(160)
  const newPinned = await metric()
  assert(newPinned.max - newPinned.top < 24, `pinned new message did not follow latest: ${JSON.stringify(newPinned)}`)
  console.log(JSON.stringify({ viewportEvidence: widths, scrollTopEvidence: { initial, upward, identicalPoll, statusUpdate, resultExpand, newUnpinned, newPinned } }))
} finally {
  await browser?.close()
  await new Promise(resolve => server.close(resolve))
  await vite.close()
}
