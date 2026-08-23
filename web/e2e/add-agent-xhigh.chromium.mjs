import assert from 'node:assert/strict'
import http from 'node:http'
import { fileURLToPath } from 'node:url'
import { createServer as createViteServer } from 'vite'
import { chromium } from 'playwright'

const webRoot = fileURLToPath(new URL('..', import.meta.url))
const session = { id: 'cao-existing', name: 'cao-existing', status: 'detached', created_at: '1' }
const existingAgent = { id: 'existing-agent', tmux_session: session.name, tmux_window: '0', provider: 'codex', agent_profile: 'developer', last_active: null }
const profiles = [
  { name: 'developer', description: 'Ordinary worker', source: 'built-in' },
  { name: 'critical_sol_xhigh_owner', description: 'Exceptional owner executor', source: 'built-in', execution_mode: 'owner_executor', owner_authorization_required: true },
]
const projects = [{ projectId: 'default-project', name: 'Sample Project', path: '/workspace/default-project', description: null, isDefault: true }]
const expectedWorkingDirectory = '/workspace/existing-session'
const operatorSecret = 'correct-browser-secret'
const grantRequests = []
const addRequests = []

const vite = await createViteServer({
  root: webRoot,
  configFile: false,
  plugins: [(await import('@vitejs/plugin-react')).default()],
  define: { __THREADCELLS_REVISION__: JSON.stringify('synthetic-evidence'), __THREADCELLS_VERSION__: JSON.stringify('0.1.0-alpha.2') },
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
  if (request.method === 'GET' && url.pathname === '/sessions') return json(response, [session])
  if (request.method === 'GET' && url.pathname === `/sessions/${session.name}`) return json(response, { session, terminals: [existingAgent] })
  if (request.method === 'GET' && url.pathname === `/sessions/${session.name}/working-directory`) return json(response, { working_directory: expectedWorkingDirectory })
  if (request.method === 'GET' && url.pathname === `/terminals/${existingAgent.id}`) return json(response, { ...existingAgent, status: 'idle', lifecycle: 'running', workflow_state: 'active' })
  if (request.method === 'GET' && url.pathname === `/terminals/${existingAgent.id}/working-directory`) return json(response, { working_directory: expectedWorkingDirectory })
  if (request.method === 'GET' && url.pathname === '/agents/providers') return json(response, [{ name: 'codex', binary: 'codex', installed: true }])
  if (request.method === 'GET' && url.pathname === '/agents/profiles') return json(response, profiles)
  if (request.method === 'GET' && url.pathname === '/projects') return json(response, projects)
  if (request.method === 'POST' && url.pathname === '/operator/session') {
    const body = await requestJson(request)
    if (body?.secret !== operatorSecret) return json(response, { detail: { reason_code: 'OPERATOR_AUTHENTICATION_FAILED' } }, 401)
    return json(response, { authenticated: true }, 200, { 'set-cookie': 'threadcells_operator_session=opaque-session; HttpOnly; SameSite=Strict; Path=/' })
  }
  if (request.method === 'POST' && url.pathname === '/operator/xhigh-grants') {
    grantRequests.push(await requestJson(request))
    return json(response, { launch_id: `browser-add-${grantRequests.length}`, grant: `one-use-browser-grant-${grantRequests.length}`, expires_in_seconds: 60 })
  }
  if (request.method === 'POST' && url.pathname === `/sessions/${session.name}/terminals`) {
    addRequests.push({
      profile: url.searchParams.get('agent_profile'),
      provider: url.searchParams.get('provider'),
      workingDirectory: url.searchParams.get('working_directory'),
      projectId: url.searchParams.get('projectId'),
      launchId: url.searchParams.get('owner_grant_launch_id'),
      grant: request.headers['x-threadcells-owner-grant'],
    })
    return json(response, { id: `owner-agent-${addRequests.length}`, name: 'owner-window', provider: 'codex', session_name: session.name, agent_profile: 'critical_sol_xhigh_owner', status: 'idle', last_active: null }, 201)
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
  for (const width of [1440, 834, 390]) {
    const page = await browser.newPage({ viewport: { width, height: 960 } })
    const pageErrors = []
    page.on('pageerror', error => pageErrors.push(error.message))
    await page.goto(origin)
    await page.waitForTimeout(1000)
    if (await page.getByRole('link', { name: 'Agents' }).count() === 0) {
      throw new Error(`Agents navigation did not render: ${JSON.stringify({ pageErrors, body: await page.locator('body').innerText() })}`)
    }
    await page.getByRole('link', { name: 'Agents' }).click()
    await page.getByRole('button', { name: 'Expand existing' }).click()
    await page.getByRole('button', { name: 'Add Agent' }).click()

    const form = page.getByText('Add another agent to this session.').locator('..')
    await page.getByTestId('add-agent-resolved-working-directory').waitFor()
    assert.equal(await page.getByText('Working Directory', { exact: true }).count(), 0)
    assert.equal(await page.getByTestId('add-agent-resolved-working-directory').textContent(), expectedWorkingDirectory)
    assert.equal(await page.getByRole('button', { name: 'Select a project to work in…' }).count(), 1)
    assert.equal(await page.getByRole('button', { name: 'Default · Sample Project' }).count(), 0, 'Add Agent selected the default project without user intent')

    await page.getByRole('button', { name: 'Select a profile...' }).click()
    await page.getByText('critical_sol_xhigh_owner', { exact: true }).click()
    await page.getByText('Exceptional XHigh owner-executor').waitFor()
    const add = page.getByRole('button', { name: 'Add', exact: true })
    assert.equal(await add.isDisabled(), true)
    await page.getByLabel('Confirm exceptional XHigh launch').check()
    assert.equal(await add.isDisabled(), true)
    await page.getByLabel('Operator secret').fill(operatorSecret)
    assert.equal(await add.isEnabled(), true)

    const geometry = await form.evaluate(element => ({ overflow: element.scrollWidth - element.clientWidth, width: element.getBoundingClientRect().width }))
    assert(geometry.width <= width, `Add Agent form exceeded the ${width}px viewport`)
    assert(geometry.overflow <= 1, `Add Agent form overflowed horizontally at ${width}px`)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), 0, `page overflowed horizontally at ${width}px`)

    await add.click()
    await page.getByText('Agent added to session').waitFor()
    assert.equal(await page.getByLabel('Operator secret').count(), 0, 'operator secret remained reflected after Add')
    const storage = await page.evaluate(() => ({ local: { ...localStorage }, session: { ...sessionStorage } }))
    assert(!JSON.stringify(storage).includes(operatorSecret), 'operator secret entered browser storage')
    await page.close()
  }

  assert.equal(grantRequests.length, 3)
  assert.equal(addRequests.length, 3)
  grantRequests.forEach(request => assert.deepEqual(request, {
    agent_profile: 'critical_sol_xhigh_owner',
    provider: 'codex',
    working_directory: expectedWorkingDirectory,
    requested_session_name: session.name,
    launch_mode: 'existing_session',
    confirmed: true,
  }))
  addRequests.forEach((request, index) => assert.deepEqual(request, {
    profile: 'critical_sol_xhigh_owner',
    provider: 'codex',
    workingDirectory: expectedWorkingDirectory,
    projectId: null,
    launchId: `browser-add-${index + 1}`,
    grant: `one-use-browser-grant-${index + 1}`,
  }))
  assert(!JSON.stringify(grantRequests).includes(operatorSecret), 'operator secret leaked into grant requests')
  assert(!JSON.stringify(addRequests).includes(operatorSecret), 'operator secret leaked into Add requests')
  console.log(JSON.stringify({ widths: [1440, 834, 390], assertions: ['existing-session XHigh grant parity', 'session path inheritance without editable workdir', 'no default project substitution', 'secret absent from grant/Add/storage', 'responsive layout has no horizontal overflow'] }))
} finally {
  await browser?.close()
  await new Promise(resolve => server.close(resolve))
  await vite.close()
}
