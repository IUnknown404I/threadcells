import assert from 'node:assert/strict'
import http from 'node:http'
import { fileURLToPath } from 'node:url'
import { createServer as createViteServer } from 'vite'
import { chromium } from 'playwright'

const webRoot = fileURLToPath(new URL('..', import.meta.url))
const sessionId = 'cao-delete-confirmation-test'
const sessions = [{ id: sessionId, name: sessionId, status: 'detached', created_at: '1' }]
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
  await page.waitForFunction(() => document.querySelector('button[disabled]') !== null)
  assert.equal(deleteRequestCount, 1, 'confirmation must issue exactly one delete request')
  assert.equal(await confirm.isDisabled(), true, 'pending delete must disable duplicate confirmation')

  releaseDelete()
  await page.getByText('No active sessions. Spawn an agent above to create one.').waitFor({ state: 'visible' })
  assert.equal(deleteRequestCount, 1, 'completed deletion must not issue a duplicate request')
} finally {
  await browser?.close()
  await new Promise(resolve => server.close(resolve))
  await vite.close()
}
