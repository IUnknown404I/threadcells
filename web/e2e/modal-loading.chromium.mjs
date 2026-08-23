import assert from 'node:assert/strict'
import http from 'node:http'
import { fileURLToPath } from 'node:url'
import { createServer as createViteServer } from 'vite'
import { chromium } from 'playwright'

const webRoot = fileURLToPath(new URL('..', import.meta.url))
const terminalId = 'modal-loading-agent'
const session = {
  id: 'modal-loading-session', name: 'modal-loading-session', status: 'active',
  created_at: '2026-08-22T00:00:00Z', last_active: '2026-08-22T00:00:00Z',
  agent_count: 1, active_agent_count: 1, project_name: null,
  activity_counts: { ready: 1 }, workflow_counts: { active: 1 },
  first_agent: { id: terminalId, activity: 'ready', execution_state: 'ready', lifecycle: 'running', workflow_state: 'active' },
  last_agent: { id: terminalId, activity: 'ready', execution_state: 'ready', lifecycle: 'running', workflow_state: 'active' },
}
const agent = {
  id: terminalId, name: 'owner', provider: 'codex', session_id: session.id,
  session_name: session.name, agent_profile: 'owner', activity: 'ready',
  execution_state: 'ready', lifecycle: 'running', workflow_state: 'active',
  workflow_status: 'open', assignment_status: null, result_status: null,
  delivery_status: null, context_role: 'supervisor', launch_worktree: null,
  managed_worktree_kind: null, managed_worktree_commit: null,
  managed_worktree_branch: null, projectId: null, project_name: null,
  project_path: null, creation_order: 1, last_active: session.last_active,
}

const vite = await createViteServer({
  root: webRoot, configFile: false,
  plugins: [(await import('@vitejs/plugin-react')).default()],
  define: { __THREADCELLS_REVISION__: JSON.stringify('modal-loading-acceptance'), __THREADCELLS_VERSION__: JSON.stringify('0.1.0-alpha.2') },
  appType: 'spa', server: { middlewareMode: true, hmr: false },
})
const json = (response, value) => { response.writeHead(200, { 'content-type': 'application/json' }); response.end(JSON.stringify(value)) }
const delayedJson = (response, value) => setTimeout(() => json(response, value), 350)
const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://localhost')
  if (url.pathname === '/ui/overview') return json(response, { sessions: 1, agents: 1, active: 1, waiting: 1, owner_gate: 0, cancelled: 0, completed: 0 })
  if (url.pathname === '/ui/sessions') return json(response, { items: [session], total: 1, limit: 10, offset: 0, next_offset: null })
  if (url.pathname === '/ui/agents') return json(response, { items: [agent], total: 1, limit: 40, offset: 0, next_offset: null, facets: { activities: ['ready'], workflow_states: ['active'], profiles: ['owner'] } })
  if (url.pathname === '/sessions') return json(response, [session])
  if (url.pathname === `/terminals/${terminalId}/output`) return delayedJson(response, { output: 'Loaded terminal output', mode: url.searchParams.get('mode') })
  if (url.pathname === `/terminals/${terminalId}/inbox/messages`) return delayedJson(response, [])
  if (url.pathname === '/delegation-results') return json(response, [])
  if (url.pathname === '/agents/profiles' || url.pathname === '/agents/providers' || url.pathname === '/projects') return json(response, [])
  if (url.pathname === '/settings/branding') return json(response, { title: 'ThreadCells', subtitle: 'Multi-agent control plane', logoUrl: '/threadcells-symbol.png', customLogo: false })
  vite.middlewares(request, response)
})

function rect(locator) {
  return locator.evaluate(element => {
    const value = element.getBoundingClientRect()
    return { x: value.x, y: value.y, width: value.width, height: value.height }
  })
}

async function assertCentered(loader, width, surface) {
  const geometry = await loader.evaluate(element => {
    const body = element.parentElement?.getBoundingClientRect()
    const spinner = element.querySelector('svg')?.getBoundingClientRect()
    const loaderRect = element.getBoundingClientRect()
    if (!body || !spinner) return null
    return {
      horizontalDelta: Math.abs((spinner.left + spinner.width / 2) - (body.left + body.width / 2)),
      verticalDelta: Math.abs((spinner.top + spinner.height / 2) - (body.top + body.height / 2)),
      loaderHeight: loaderRect.height,
      viewportOverflow: document.documentElement.scrollWidth - window.innerWidth,
    }
  })
  assert(geometry, `${surface} loader geometry unavailable at ${width}px`)
  assert(geometry.horizontalDelta <= 1, `${surface} loader was horizontally offset at ${width}px: ${JSON.stringify(geometry)}`)
  assert(geometry.verticalDelta <= 1, `${surface} loader was vertically offset at ${width}px: ${JSON.stringify(geometry)}`)
  assert(geometry.loaderHeight >= 192, `${surface} loader body was unstable at ${width}px: ${JSON.stringify(geometry)}`)
  assert.equal(geometry.viewportOverflow, 0, `${surface} overflowed at ${width}px`)
  return geometry
}

await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const address = server.address()
assert(address && typeof address !== 'string')
const origin = `http://127.0.0.1:${address.port}`
let browser
try {
  browser = await chromium.launch({ headless: true })
  const page = await browser.newPage()
  const evidence = []
  for (const width of [1440, 834, 390]) {
    await page.setViewportSize({ width, height: 900 })
    await page.goto(origin)
    await page.getByRole('button', { name: `Expand ${session.name}`, exact: true }).click()
    const card = page.getByTestId(`agent-detail-card-${terminalId}`)
    await card.waitFor()

    await card.getByRole('button', { name: 'Output' }).click()
    const outputDialog = page.getByRole('dialog', { name: 'Terminal output' })
    const outputBefore = await rect(outputDialog)
    const outputGeometry = await assertCentered(outputDialog.getByRole('status'), width, 'Terminal Output')
    await outputDialog.getByText('Loaded terminal output').waitFor()
    const outputAfter = await rect(outputDialog)
    assert.deepEqual(outputAfter, outputBefore, `Terminal Output dialog jumped after load at ${width}px`)
    await outputDialog.getByTitle('Close').click()

    await card.getByRole('button', { name: 'Inbox', exact: true }).click()
    const inboxDialog = page.getByRole('dialog', { name: 'Agent inbox' })
    const inboxBefore = await rect(inboxDialog)
    const inboxGeometry = await assertCentered(inboxDialog.getByRole('status'), width, 'Inbox')
    await inboxDialog.getByText('No messages yet').waitFor()
    const inboxAfter = await rect(inboxDialog)
    assert.deepEqual(inboxAfter, inboxBefore, `Inbox dialog jumped after load at ${width}px`)
    await inboxDialog.getByTitle('Close').click()
    evidence.push({ width, outputGeometry, inboxGeometry })
  }
  console.log(JSON.stringify({ evidence }))
} finally {
  await browser?.close()
  await new Promise(resolve => server.close(resolve))
  await vite.close()
}
