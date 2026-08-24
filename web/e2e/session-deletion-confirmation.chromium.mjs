import assert from 'node:assert/strict'
import http from 'node:http'
import { fileURLToPath } from 'node:url'
import { createServer as createViteServer } from 'vite'
import { chromium } from 'playwright'

const webRoot = fileURLToPath(new URL('..', import.meta.url))
const sessionId = 'stable-delete-confirmation-lifetime'
const sessions = [{ id: sessionId, name: 'cao-delete-confirmation-test', status: 'history', created_at: '1' }]
let deleteRequestCount = 0
let releaseDelete

const vite = await createViteServer({
  root: webRoot,
  configFile: false,
  plugins: [(await import('@vitejs/plugin-react')).default()],
  appType: 'spa',
  server: { middlewareMode: true, hmr: false },
})

function json(response, value, status = 200) {
  response.writeHead(status, { 'content-type': 'application/json' })
  response.end(JSON.stringify(value))
}

const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://localhost')
  if (request.method === 'GET' && url.pathname === '/ui/sessions') {
    const items = sessions.map(session => ({
      ...session,
      agent_count: 0,
      active_agent_count: 0,
      workflow_counts: {},
      activity_counts: {},
      project_name: null,
      last_active: session.created_at,
      first_agent: null,
      last_agent: null,
    }))
    return json(response, { items, total: items.length, limit: 10, offset: 0, next_offset: null })
  }
  if (request.method === 'GET' && url.pathname === '/ui/agents') return json(response, { items: [], total: 0, limit: 40, offset: 0, next_offset: null, facets: { activities: [], workflow_states: [], profiles: [] } })
  if (request.method === 'GET' && url.pathname === '/ui/overview') return json(response, { sessions: sessions.length, agents: 0, active: 0, waiting: 0, owner_gate: 0, cancelled: 0, completed: 0 })
  if (request.method === 'GET' && url.pathname === '/sessions') return json(response, sessions)
  if (request.method === 'GET' && url.pathname === '/agents/providers') return json(response, [])
  if (request.method === 'GET' && url.pathname === '/agents/profiles') return json(response, [])
  if (request.method === 'DELETE' && url.pathname === `/sessions/${sessionId}`) {
    deleteRequestCount += 1
    releaseDelete = () => {
      sessions.splice(0, sessions.length)
      json(response, { success: true, deleted: [sessionId], errors: [] })
    }
    return
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
  const page = await browser.newPage()
  await page.goto(origin)
  await page.getByRole('link', { name: 'Agents' }).click()

  await page.getByTitle('Delete session').click()
  await page.getByRole('heading', { name: 'Delete Session' }).waitFor({ state: 'visible' })
  assert.equal(deleteRequestCount, 0, 'opening confirmation must not delete')
  await page.getByRole('button', { name: 'Cancel' }).click()
  assert.equal(deleteRequestCount, 0, 'cancelling confirmation must not delete')

  await page.getByTitle('Delete session').click()
  const confirm = page.getByRole('button', { name: 'Delete Session', exact: true })
  await confirm.click()
  const closing = page.getByRole('button', { name: 'Closing...', exact: true })
  await closing.waitFor()
  assert.equal(deleteRequestCount, 1, 'confirmation must issue exactly one delete request')
  assert.equal(await closing.isDisabled(), true, 'pending delete must disable duplicate confirmation')

  releaseDelete()
  await page.getByText('No matching sessions. Create a session above to start an agent.').waitFor({ state: 'visible' })
  assert.equal(deleteRequestCount, 1, 'completed deletion must not issue a duplicate request')
} finally {
  await browser?.close()
  await new Promise(resolve => server.close(resolve))
  await vite.close()
}
