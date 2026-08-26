import assert from 'node:assert/strict'
import http from 'node:http'
import { fileURLToPath } from 'node:url'
import { createServer as createViteServer } from 'vite'
import { chromium } from 'playwright'
import { WebSocketServer } from 'ws'

const webRoot = fileURLToPath(new URL('..', import.meta.url))
const sessionId = 'cao-slow-codex'
const terminalId = 'codex-terminal'
const sessions = []
let createRequestCount = 0
let requestAborted = false
let releaseCreate
let deliveredMessage = null
let resolveMessageDelivered
const messageDelivered = new Promise(resolve => { resolveMessageDelivered = resolve })
const wsServer = new WebSocketServer({ noServer: true })

const vite = await createViteServer({
  root: webRoot,
  configFile: false,
  plugins: [(await import('@vitejs/plugin-react')).default()],
  define: { __THREADCELLS_REVISION__: JSON.stringify('synthetic-evidence'), __THREADCELLS_VERSION__: JSON.stringify('0.1.0-alpha.2') },
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
  if (request.method === 'GET' && url.pathname === `/sessions/${sessionId}`) {
    return json(response, {
      session: { id: sessionId, name: sessionId, status: 'active', created_at: '1' },
      terminals: [{ id: terminalId, tmux_session: sessionId, tmux_window: '0', provider: 'codex', agent_profile: 'developer', last_active: null }],
    })
  }
  if (request.method === 'GET' && url.pathname === '/agents/providers') return json(response, [{ name: 'codex', binary: 'codex', installed: true }])
  if (request.method === 'GET' && url.pathname === '/agents/profiles') return json(response, [{ name: 'developer', description: 'Implements and fixes code.', source: 'built-in' }])
  if (request.method === 'GET' && url.pathname === `/terminals/${terminalId}`) return json(response, { id: terminalId, status: 'idle' })
  if (request.method === 'GET' && url.pathname === `/terminals/${terminalId}/working-directory`) return json(response, { working_directory: null })
  if (request.method === 'POST' && url.pathname === '/sessions') {
    createRequestCount += 1
    request.on('aborted', () => { requestAborted = true })
    releaseCreate = () => {
      sessions.push({ id: sessionId, name: sessionId, status: 'active', created_at: '1' })
      json(response, { id: terminalId, name: terminalId, provider: 'codex', session_name: sessionId, agent_profile: 'developer', status: 'idle', last_active: null }, 201)
    }
    return
  }
  if (request.method === 'POST' && url.pathname === `/terminals/${terminalId}/workflow-input`) {
    const chunks = []
    request.on('data', chunk => chunks.push(chunk))
    request.on('end', () => {
      deliveredMessage = JSON.parse(Buffer.concat(chunks).toString()).message
      resolveMessageDelivered()
      json(response, { success: true, accepted: true, duplicate: false, turn_id: 74, queued: false, status: 'provider_admitted', reason_code: null })
    })
    return
  }
  vite.middlewares(request, response)
})

server.on('upgrade', (request, socket, head) => {
  if (request.url !== `/terminals/${terminalId}/ws`) return socket.destroy()
  wsServer.handleUpgrade(request, socket, head, socket => wsServer.emit('connection', socket, request))
})

await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const address = server.address()
assert(address && typeof address !== 'string')
const origin = `http://127.0.0.1:${address.port}`

let browser
try {
  browser = await chromium.launch({ headless: true })
  const page = await browser.newPage()
  await page.clock.install()
  await page.goto(origin)
  await page.getByRole('link', { name: 'Agents' }).click()
  await page.getByRole('button', { name: 'Create Session & Spawn Agent' }).click()
  await page.getByText('Select a profile...').click()
  await page.getByRole('button', { name: 'developer Implements and fixes code.', exact: true }).click()
  await page.getByRole('button', { name: 'Create Session' }).last().click()
  await page.waitForFunction(() => window.fetch !== undefined)

  assert.equal(createRequestCount, 1)
  await page.getByRole('button', { name: 'Creating...' }).waitFor({ state: 'visible' })
  await page.clock.fastForward(91_000)
  assert.equal(requestAborted, false, 'a delayed session create must not be client-aborted')
  assert.equal(createRequestCount, 1, 'pending state must prevent duplicate creates')
  assert.equal(await page.getByRole('heading', { name: 'Create Session & Spawn Agent' }).count(), 1, 'create-session modal remains visible once while pending')

  releaseCreate()
  await page.getByText(sessionId, { exact: true }).waitFor({ state: 'visible' })
  await page.getByText(sessionId, { exact: true }).click()
  await page.getByRole('button', { name: 'Open Workflow Composer' }).click()
  await page.getByRole('textbox', { name: 'Workflow Composer' }).fill('first Codex task')
  await page.getByRole('button', { name: 'Send task' }).click()
  await messageDelivered
  assert.equal(deliveredMessage, 'first Codex task')
} finally {
  await browser?.close()
  wsServer.close()
  await new Promise(resolve => server.close(resolve))
  await vite.close()
}
