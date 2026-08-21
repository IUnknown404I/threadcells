import assert from 'node:assert/strict'
import { mkdirSync } from 'node:fs'
import http from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createServer as createViteServer } from 'vite'
import { chromium } from 'playwright'

const screenshotDir = process.env.CAO_SCREENSHOT_DIR
if (!screenshotDir) throw new Error('CAO_SCREENSHOT_DIR is required')
mkdirSync(screenshotDir, { recursive: true })

const webRoot = fileURLToPath(new URL('..', import.meta.url))
const projects = [
  { projectId: 'project-a', name: 'Project A', path: '/workspace/project-a', description: null, isDefault: true },
  { projectId: 'project-b', name: 'Project B', path: '/workspace/project-b', description: null, isDefault: false },
]
const profile = { name: 'developer', description: 'Implements and fixes code.', source: 'built-in' }
const createRequests = []

const vite = await createViteServer({
  root: webRoot,
  configFile: false,
  plugins: [(await import('@vitejs/plugin-react')).default()],
  define: { __THREADCELLS_REVISION__: JSON.stringify('synthetic-evidence'), __THREADCELLS_VERSION__: JSON.stringify('0.1.0-alpha.1') },
  appType: 'spa',
  server: { middlewareMode: true, hmr: false },
})

function json(response, value, status = 200) {
  response.writeHead(status, { 'content-type': 'application/json' })
  response.end(JSON.stringify(value))
}

const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://localhost')
  if (request.method === 'GET' && url.pathname === '/sessions') return json(response, [])
  if (request.method === 'GET' && url.pathname === '/agents/providers') return json(response, [{ name: 'codex', binary: 'codex', installed: true }])
  if (request.method === 'GET' && url.pathname === '/agents/profiles') return json(response, [profile])
  if (request.method === 'GET' && url.pathname === '/projects') return json(response, projects)
  if (request.method === 'GET' && url.pathname === '/settings/agent-dirs') return json(response, { agent_dirs: {}, extra_dirs: [] })
  if (request.method === 'GET' && url.pathname === '/settings/orchestration-capacity') return json(response, { resource_state: 'GREEN', reasons: [], resident_supervisors: { active: 0, limit: 5, available: 5, certain: true }, provider_executions: { active: 0, limit: 3, available: 3, certain: true }, work_contexts: { active: 0, limit: 2, available: 2, certain: true }, heavy_executions: { active: 0, limit: 1, available: 1, certain: true }, memory: { available_mib: 1, swap_total_mib: 0, swap_free_mib: 0 }, root_disk: { used_percent: 1, free_gib: 1 }, memory_pressure: { some_avg10: 0, full_avg10: 0 }, cpu_load: { one_minute: 0, cpu_count: 1 }, housekeeping: null })
  if (request.method === 'POST' && url.pathname === '/sessions') {
    createRequests.push(url)
    return json(response, { id: `terminal-${createRequests.length}`, name: 'terminal', provider: 'codex', session_name: 'cao-test', agent_profile: 'developer', status: 'idle', last_active: null }, 201)
  }
  vite.middlewares(request, response)
})

await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const address = server.address()
assert(address && typeof address !== 'string')
const origin = `http://127.0.0.1:${address.port}`

async function openSpawn(page) {
  await page.goto(origin)
  await page.getByRole('link', { name: 'Agents' }).click()
  await page.getByRole('button', { name: 'Create Session & Spawn Agent' }).click()
  await page.getByText('/workspace/project-a').waitFor()
  await page.getByText(profile.description, { exact: true }).waitFor()
  assert.doesNotMatch(await page.locator('div.fixed.inset-0.z-50').last().textContent() || '', /[\u0400-\u052f]/, 'Spawn modal must not contain Cyrillic UI copy')
  assert.equal(await page.getByText('Working Directory', { exact: true }).count(), 0)
}

let browser
try {
  browser = await chromium.launch({ headless: true })

  const wide = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  await openSpawn(wide)
  await wide.screenshot({ path: path.join(screenshotDir, 'spawn-project-p1-1440.png'), fullPage: true })

  const medium = await browser.newPage({ viewport: { width: 834, height: 1112 } })
  await openSpawn(medium)
  await medium.getByRole('button', { name: 'Default · Project A' }).click()
  await medium.getByText('Project B', { exact: true }).click()
  await medium.getByText('/workspace/project-b').waitFor()
  await medium.screenshot({ path: path.join(screenshotDir, 'spawn-project-p1-834.png'), fullPage: true })
  await medium.getByText('developer', { exact: true }).click()
  await medium.getByRole('button', { name: 'Create Session' }).last().click()
  await medium.getByText('Session created').waitFor()
  assert.equal(createRequests.at(-1)?.searchParams.get('projectId'), 'project-b')
  assert.equal(createRequests.at(-1)?.searchParams.has('working_directory'), false)

  projects.length = 0
  const narrow = await browser.newPage({ viewport: { width: 390, height: 844 } })
  await narrow.goto(origin)
  await narrow.getByRole('link', { name: 'Agents' }).click()
  await narrow.getByRole('button', { name: 'Create Session & Spawn Agent' }).click()
  await narrow.getByText('No projects are configured. This launch will use the default working directory.').waitFor()
  await narrow.getByText(profile.description, { exact: true }).waitFor()
  assert.doesNotMatch(await narrow.locator('div.fixed.inset-0.z-50').last().textContent() || '', /[\u0400-\u052f]/, 'Narrow Spawn modal must not contain Cyrillic UI copy')
  assert.equal(await narrow.getByText('Working Directory', { exact: true }).count(), 0)
  await narrow.screenshot({ path: path.join(screenshotDir, 'spawn-project-p1-390.png'), fullPage: true })
  await narrow.getByText('developer', { exact: true }).click()
  await narrow.getByRole('button', { name: 'Create Session' }).last().click()
  await narrow.getByText('Session created').waitFor()
  assert.equal(createRequests.at(-1)?.searchParams.has('projectId'), false)
  assert.equal(createRequests.at(-1)?.searchParams.has('working_directory'), false)
  await narrow.getByRole('link', { name: 'Settings' }).click()
  await narrow.getByText('Available agent profiles and their descriptions').click()
  const profilesDialog = narrow.getByRole('dialog', { name: 'Profiles' })
  await profilesDialog.waitFor()
  assert.equal(await profilesDialog.getByText(profile.description, { exact: true }).count(), 1)
  assert.doesNotMatch(await profilesDialog.textContent() || '', /[\u0400-\u052f]/, 'Profiles modal must not contain Cyrillic UI copy')
  await narrow.screenshot({ path: path.join(screenshotDir, 'profile-settings-p1-390.png'), fullPage: true })
} finally {
  await browser?.close()
  await new Promise(resolve => server.close(resolve))
  await vite.close()
}
