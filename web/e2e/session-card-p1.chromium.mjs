import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import http from 'node:http'
import { fileURLToPath } from 'node:url'
import { createServer as createViteServer } from 'vite'
import { chromium } from 'playwright'

const webRoot = fileURLToPath(new URL('..', import.meta.url))
const evidenceDir = process.env.CAO_VISUAL_EVIDENCE_DIR || '/tmp/cao-ui-session-card-p1'
const fewSession = { id: 'few-badges', name: 'cao-one-agent-few-badges', status: 'active', created_at: '1' }
const longSession = {
  id: 'many-badges',
  name: 'cao-session-with-a-deliberately-long-title-that-must-truncate-cleanly-on-a-narrow-viewport',
  status: 'active',
  created_at: '2',
}
const displayName = session => session.name.startsWith('cao-') ? session.name.slice(4) : session.name
const makeTerminal = (session, index) => ({
  id: `${session.id}-terminal-${index}`,
  tmux_session: session.name,
  tmux_window: String(index),
  provider: 'codex',
  agent_profile: index % 2 ? 'reviewer_terra_high' : 'developer_terra_high',
  last_active: null,
})
const terminals = {
  [fewSession.name]: [makeTerminal(fewSession, 0)],
  [longSession.name]: Array.from({ length: 12 }, (_, index) => makeTerminal(longSession, index)),
}
const summarize = session => {
  const agents = terminals[session.name]
  const boundary = agent => ({ id: agent.id, activity: 'ready', execution_state: 'ready', lifecycle: 'running', workflow_state: 'active', workflow_reason: null })
  return {
    ...session,
    last_active: session.created_at,
    agent_count: agents.length,
    active_agent_count: agents.length,
    project_name: session.id === longSession.id ? 'ThreadCells' : null,
    activity_counts: { ready: agents.length },
    workflow_counts: { active: agents.length },
    first_agent: boundary(agents[0]),
    last_agent: boundary(agents.at(-1)),
  }
}
const summaries = [fewSession, longSession].map(summarize)
const agentSummary = (session, terminal, index) => ({
  id: terminal.id, name: terminal.tmux_window, provider: terminal.provider,
  session_id: session.id, session_name: session.name, agent_profile: terminal.agent_profile,
  activity: 'ready', execution_state: 'ready', lifecycle: 'running', workflow_state: 'active',
  workflow_status: 'open', workflow_reason: null, assignment_status: null, result_status: null,
  delivery_status: null, context_role: index ? 'work' : 'supervisor', launch_worktree: null,
  managed_worktree_kind: null, managed_worktree_commit: null, managed_worktree_branch: null,
  projectId: session.id === longSession.id ? 'threadcells' : null,
  project_name: session.id === longSession.id ? 'ThreadCells' : null,
  project_path: session.id === longSession.id ? '/fixture/threadcells' : null,
  creation_order: index + 1, last_active: session.created_at,
})
const agentSummaries = Object.fromEntries([fewSession, longSession].map(session => [
  session.id, terminals[session.name].map((terminal, index) => agentSummary(session, terminal, index)),
]))
const workflowStates = ['active', 'owner_gate', 'waiting', 'recoverable', 'result_ready', 'completed']
const runtimeBranding = { title: 'ThreadCells', subtitle: 'Multi-agent control plane', logoUrl: '/threadcells-symbol.png', customLogo: false }

await mkdir(evidenceDir, { recursive: true })
const vite = await createViteServer({ root: webRoot, configFile: false, plugins: [(await import('@vitejs/plugin-react')).default()], appType: 'spa', server: { middlewareMode: true, hmr: false } })
const json = (response, value) => { response.writeHead(200, { 'content-type': 'application/json' }); response.end(JSON.stringify(value)) }
const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://localhost')
  if (request.method === 'GET' && url.pathname === '/ui/overview') return json(response, { sessions: 2, agents: 13, active: 13, waiting: 0, owner_gate: 0, cancelled: 0, completed: 0 })
  if (request.method === 'GET' && url.pathname === '/ui/sessions') return json(response, { items: summaries, total: summaries.length, limit: 10, offset: 0, next_offset: null })
  if (request.method === 'GET' && url.pathname === '/ui/agents') {
    const items = agentSummaries[url.searchParams.get('session_id')] || []
    return json(response, { items, total: items.length, limit: 40, offset: 0, next_offset: null, facets: { activities: ['ready'], workflow_states: ['active'], profiles: ['developer_terra_high', 'reviewer_terra_high'] } })
  }
  if (request.method === 'GET' && url.pathname === '/sessions') return json(response, [fewSession, longSession])
  const matchedSession = [fewSession, longSession].find(session => url.pathname === `/sessions/${session.name}`)
  if (request.method === 'GET' && matchedSession) return json(response, { session: matchedSession, terminals: terminals[matchedSession.name] })
  if (request.method === 'GET' && url.pathname.startsWith('/terminals/')) {
    const terminalId = url.pathname.split('/')[2]
    const index = Number(terminalId.split('-').at(-1))
    const workflow_state = workflowStates[index % workflowStates.length]
    return json(response, { id: terminalId, provider: 'codex', status: workflow_state === 'completed' ? 'completed' : 'idle', lifecycle: workflow_state === 'completed' ? 'exited' : 'running', workflow_state, last_active: null })
  }
  if (request.method === 'GET' && url.pathname === '/agents/profiles') return json(response, [])
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
  const page = await browser.newPage()
  await page.addInitScript(() => {
    const original = window.setInterval
    window.setInterval = (handler, timeout, ...args) => original(handler, timeout === 5000 ? 80 : timeout === 3000 ? 60 : timeout, ...args)
  })
  await page.goto(origin)
  await page.getByTestId(`session-header-${fewSession.id}`).waitFor()
  await page.getByTestId(`session-header-${longSession.id}`).waitFor()

  for (const width of [1440, 834, 390]) {
    await page.setViewportSize({ width, height: 960 })
    const fewHeader = page.getByTestId(`session-header-${fewSession.id}`)
    const longHeader = page.getByTestId(`session-header-${longSession.id}`)
    const fewCard = page.getByTestId(`home-session-${fewSession.id}`)
    const longCard = page.getByTestId(`home-session-${longSession.id}`)
    if (await longHeader.getByRole('button', { name: `Expand ${displayName(longSession)} using chevron` }).isVisible()) {
      await longHeader.getByRole('button', { name: `Expand ${displayName(longSession)} using chevron` }).click()
    }
    const longTitle = longHeader.getByTitle(displayName(longSession))
    const longTitleRow = longHeader.getByTestId(`session-title-row-${longSession.id}`)
    const longMetadata = longHeader.getByTestId(`session-metadata-${longSession.id}`)
    const longActions = longHeader.getByTestId(`session-actions-${longSession.id}`)
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    const titleBox = await longTitle.boundingBox()
    assert.equal(overflow, 0, `horizontal overflow at ${width}px: ${overflow}`)
    assert(titleBox && titleBox.height < 30, `long title wrapped or collapsed at ${width}px: ${JSON.stringify(titleBox)}`)
    assert((await longCard.getAttribute('class')).includes('bg-emerald-900/30'), `expanded card did not use selected surface at ${width}px`)

    const fewSummary = fewCard.getByLabel('Session status')
    const agentLayout = fewSummary.getByRole('group', { name: 'Agent layout' })
    if (width < 640) {
      assert.equal(await agentLayout.isVisible(), false, 'mobile must hide the redundant List/Grid switch')
      const [titleRowBox, metadataBox, actionsBox] = await Promise.all([
        longTitleRow.boundingBox(), longMetadata.boundingBox(), longActions.boundingBox(),
      ])
      assert(titleRowBox && metadataBox && actionsBox, 'mobile session header rows must be measurable')
      assert(metadataBox.y >= titleRowBox.y + titleRowBox.height - 1, 'mobile metadata must be on row 2')
      assert(actionsBox.y >= titleRowBox.y + titleRowBox.height - 1, 'mobile actions must be on row 2')
      assert.equal(
        await longCard.getByTestId('session-agent-container').evaluate(node => getComputedStyle(node).display),
        'block',
        'mobile agent layout must remain the canonical list even when desktop Grid was selected',
      )
    } else {
      assert.equal(await agentLayout.isVisible(), true, 'tablet/desktop must retain List/Grid controls')
      await fewSummary.getByRole('button', { name: 'List view' }).click()
      assert.equal(await fewSummary.getByRole('button', { name: 'List view' }).getAttribute('aria-pressed'), 'true')
    }
    await fewCard.screenshot({ path: `${evidenceDir}/${width}-few-expanded-list.png` })
    await longCard.screenshot({ path: `${evidenceDir}/${width}-many-expanded-list.png` })

    const longSummary = longCard.getByLabel('Session status')
    if (width >= 640) {
      await longSummary.getByRole('button', { name: 'Grid view' }).click()
      assert.equal(await longSummary.getByRole('button', { name: 'Grid view' }).getAttribute('aria-pressed'), 'true')
    }
    await longCard.screenshot({ path: `${evidenceDir}/${width}-many-expanded-grid.png` })

    await longHeader.getByRole('button', { name: `Collapse ${displayName(longSession)} using chevron` }).click()
    assert((await longCard.getAttribute('class')).includes('bg-gray-800/60'), `collapsed card retained selected surface at ${width}px`)
    await longCard.screenshot({ path: `${evidenceDir}/${width}-many-collapsed-grid.png` })
    await longHeader.getByRole('button', { name: `Expand ${displayName(longSession)}`, exact: true }).press('Enter')
  }
  console.log(JSON.stringify({ evidenceDir, widths: [1440, 834, 390], assertions: ['no horizontal overflow', 'long title remains single-line', 'mobile two-row header', 'mobile List/Grid hidden', 'mobile canonical list layout', 'tablet/desktop List/Grid pressed state', 'emerald expanded surface', 'gray collapsed surface', 'title keyboard expansion'] }))
} finally {
  await browser?.close()
  await new Promise(resolve => server.close(resolve))
  await vite.close()
}
