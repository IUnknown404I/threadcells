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
const ownerReason = `Owner approval is required. ${'This intentionally very long durable explanation must stay in the dedicated Owner Decision panel. '.repeat(16)}`
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
const workflowState = (session, index) => session.id === longSession.id && (index === 0 || index === terminals[session.name].length - 1)
  ? 'owner_gate'
  : 'active'
const summarize = session => {
  const agents = terminals[session.name]
  const boundary = (agent, index) => {
    const state = workflowState(session, index)
    return { id: agent.id, activity: 'ready', execution_state: 'ready', lifecycle: 'running', workflow_state: state, workflow_reason: state === 'owner_gate' ? ownerReason : null }
  }
  const ownerCount = agents.filter((_, index) => workflowState(session, index) === 'owner_gate').length
  return {
    ...session,
    last_active: session.created_at,
    agent_count: agents.length,
    active_agent_count: agents.length,
    project_name: session.id === longSession.id ? 'ThreadCells' : null,
    activity_counts: { ready: agents.length },
    workflow_counts: ownerCount ? { active: agents.length - ownerCount, owner_gate: ownerCount } : { active: agents.length },
    first_agent: boundary(agents[0], 0),
    last_agent: boundary(agents.at(-1), agents.length - 1),
  }
}
const summaries = [fewSession, longSession].map(summarize)
const agentSummary = (session, terminal, index) => {
  const state = workflowState(session, index)
  return {
    id: terminal.id, name: terminal.tmux_window, provider: terminal.provider,
    session_id: session.id, session_name: session.name, agent_profile: terminal.agent_profile,
    activity: 'ready', execution_state: 'ready', lifecycle: 'running', workflow_state: state,
    workflow_status: state === 'owner_gate' ? 'owner_gate' : 'open', workflow_reason: state === 'owner_gate' ? ownerReason : null, assignment_status: null, result_status: null,
    delivery_status: null, context_role: index ? 'work' : 'supervisor', launch_worktree: null,
    managed_worktree_kind: null, managed_worktree_commit: null, managed_worktree_branch: null,
    projectId: session.id === longSession.id ? 'threadcells' : null,
    project_name: session.id === longSession.id ? 'ThreadCells' : null,
    project_path: session.id === longSession.id ? '/fixture/threadcells' : null,
    creation_order: index + 1, last_active: session.created_at,
  }
}
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
  if (request.method === 'GET' && url.pathname === '/ui/overview') return json(response, { sessions: 2, agents: 13, active: 13, waiting: 0, owner_gate: 2, cancelled: 0, completed: 0 })
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
    for (const badge of await longCard.locator('[data-status-badge]').all()) {
      const text = await badge.textContent()
      assert(!text?.includes(ownerReason), `free-form owner reason leaked into badge at ${width}px`)
      const attributes = await badge.locator('[title], [aria-label]').evaluateAll(nodes => nodes.flatMap(node => [node.getAttribute('title'), node.getAttribute('aria-label')]).filter(Boolean))
      assert(!attributes.some(value => value.includes(ownerReason)), `free-form owner reason leaked into badge metadata at ${width}px`)
      const [badgeBox, cardBox] = await Promise.all([badge.boundingBox(), longCard.boundingBox()])
      assert(badgeBox && cardBox && badgeBox.width < cardBox.width, `status badge exceeded its card at ${width}px`)
    }
    assert.equal(await longCard.getByTestId(`owner-decision-${longSession.id}-terminal-0`).count(), 1, 'first owner reason must remain in its dedicated panel')
    assert((await longCard.getByTestId(`owner-decision-${longSession.id}-terminal-0`).textContent())?.includes(ownerReason), 'dedicated Owner Decision panel lost its reason')
    assert.equal(await longCard.getByTestId(`owner-decision-${longSession.id}-terminal-11`).count(), 1, 'last owner reason must remain in its dedicated panel')
    for (const boundary of ['first', 'last']) {
      assert.equal(await longCard.getByTestId(`session-status-${boundary}-${longSession.id}`).getByText('Needs owner decision').count(), 1, `${boundary} badge lost owner status at ${width}px`)
    }
    assert.equal(await longCard.getByTestId(`session-status-workflow-${longSession.id}-owner_gate`).getByText('×2').count(), 1, `Total owner-gate aggregation changed at ${width}px`)
    assert.equal(await longCard.getByTestId(`session-status-agent-${longSession.id}-ready`).getByText('×12').count(), 1, `Total Ready aggregation changed at ${width}px`)

    const fewSummary = fewCard.getByLabel('Session status')
    const fewActions = fewHeader.getByTestId(`session-actions-${fewSession.id}`)
    const agentLayout = fewSummary.getByRole('group', { name: 'Agent layout' })
    assert.equal(await agentLayout.isVisible(), true, `session List/Grid controls must remain available at ${width}px`)
    assert.equal(await fewActions.getByRole('group', { name: 'Agent layout' }).count(), 0, 'layout controls must not remain in the Home action row')
    assert.equal(await fewSummary.evaluate((summary, sessionId) => {
      const badges = summary.querySelector(`[data-testid="session-status-badges-${sessionId}"]`)
      const actions = summary.querySelector(`[data-testid="session-status-actions-${sessionId}"]`)
      return Boolean(badges && actions && (badges.compareDocumentPosition(actions) & Node.DOCUMENT_POSITION_FOLLOWING))
    }, fewSession.id), true, 'Home layout controls must follow the status badges')
    await agentLayout.getByRole('button', { name: 'List view' }).click()
    assert.equal(await agentLayout.getByRole('button', { name: 'List view' }).getAttribute('aria-pressed'), 'true')
    if (width < 768) {
      const [titleRowBox, metadataBox, actionsBox] = await Promise.all([
        longTitleRow.boundingBox(), longMetadata.boundingBox(), longActions.boundingBox(),
      ])
      assert(titleRowBox && metadataBox && actionsBox, 'mobile session header rows must be measurable')
      assert(metadataBox.y >= titleRowBox.y + titleRowBox.height - 1, 'mobile metadata must be on row 2')
      assert(actionsBox.y >= metadataBox.y + metadataBox.height - 1, 'mobile session controls must wrap to a clean secondary row')
    }
    await fewCard.screenshot({ path: `${evidenceDir}/${width}-few-expanded-list.png` })
    await longCard.screenshot({ path: `${evidenceDir}/${width}-many-expanded-list.png` })

    const longLayout = longCard.getByLabel('Session status').getByRole('group', { name: 'Agent layout' })
    await longLayout.getByRole('button', { name: 'Grid view' }).click()
    assert.equal(await longLayout.getByRole('button', { name: 'Grid view' }).getAttribute('aria-pressed'), 'true')
    const gridEvidence = await longCard.getByTestId('session-agent-container').evaluate(node => ({
      display: getComputedStyle(node).display,
      columns: getComputedStyle(node).gridTemplateColumns.split(' ').filter(Boolean).length,
      agentIds: Array.from(node.querySelectorAll('[data-testid^="agent-detail-card-"]')).map(card => card.getAttribute('data-testid')),
      terminalActions: node.querySelectorAll('button').length,
    }))
    assert.deepEqual(gridEvidence.agentIds, Array.from({ length: 12 }, (_, index) => `agent-detail-card-${longSession.id}-terminal-${index}`), `Grid changed durable agent order at ${width}px`)
    assert(gridEvidence.terminalActions >= 12 * 6, `Grid lost agent actions at ${width}px`)
    if (width < 768) {
      assert.equal(gridEvidence.display, 'block', 'mobile Grid preference must preserve the canonical single-column physical layout')
    } else {
      assert.equal(gridEvidence.display, 'grid', `tablet/desktop Grid must use CSS grid at ${width}px`)
      assert.equal(gridEvidence.columns, 2, `tablet/desktop Grid must render exactly two columns at ${width}px`)
    }
    await page.waitForTimeout(240)
    assert.equal(await longLayout.getByRole('button', { name: 'Grid view' }).getAttribute('aria-pressed'), 'true', `polling reset Grid preference at ${width}px`)
    await longCard.screenshot({ path: `${evidenceDir}/${width}-many-expanded-grid.png` })

    await longHeader.getByRole('button', { name: `Collapse ${displayName(longSession)} using chevron` }).click()
    assert((await longCard.getAttribute('class')).includes('bg-gray-800/60'), `collapsed card retained selected surface at ${width}px`)
    assert.equal(await longCard.getByText(ownerReason, { exact: false }).count(), 0, `collapsed card exposed a free-form owner reason at ${width}px`)
    for (const badge of await longCard.locator('[data-status-badge]').all()) {
      assert(!(await badge.textContent())?.includes(ownerReason), `collapsed badge exposed a free-form owner reason at ${width}px`)
    }
    await longCard.screenshot({ path: `${evidenceDir}/${width}-many-collapsed-grid.png` })
    await longHeader.getByRole('button', { name: `Expand ${displayName(longSession)}`, exact: true }).press('Enter')

    await page.getByRole('link', { name: 'Agents' }).click()
    const agentSession = page.getByTestId(`agent-session-${longSession.id}`)
    const otherSession = page.getByTestId(`agent-session-${fewSession.id}`)
    await agentSession.waitFor()
    const agentActions = agentSession.getByTestId(`agent-session-actions-${longSession.id}`)
    const agentsLayout = agentActions.getByRole('group', { name: 'Agent layout' })
    assert.equal(await agentsLayout.isVisible(), true, `Agents List/Grid controls must remain available at ${width}px`)
    assert.equal(await otherSession.getByRole('button', { name: 'List view' }).getAttribute('aria-pressed'), 'true', 'another session must retain its own List preference')
    await agentsLayout.getByRole('button', { name: 'Grid view' }).click()
    assert.equal(await agentSession.getByTestId(`agent-session-detail-${longSession.id}`).count(), 0, 'changing a collapsed session preference must not expand it')
    await agentSession.getByRole('button', { name: `Expand ${displayName(longSession)}` }).click()
    const agentContainer = agentSession.getByTestId(`agent-session-agent-container-${longSession.id}`)
    await agentContainer.waitFor()
    const agentsGridEvidence = await agentContainer.evaluate(node => ({
      display: getComputedStyle(node).display,
      columns: getComputedStyle(node).gridTemplateColumns.split(' ').filter(Boolean).length,
      agentIds: Array.from(node.querySelectorAll('[data-testid^="agent-detail-card-"]')).map(card => card.getAttribute('data-testid')),
    }))
    assert.deepEqual(agentsGridEvidence.agentIds, Array.from({ length: 12 }, (_, index) => `agent-detail-card-${longSession.id}-terminal-${index}`), `Agents Grid changed durable agent order at ${width}px`)
    if (width < 768) {
      assert.equal(agentsGridEvidence.display, 'block', 'Agents mobile Grid preference must remain one physical column')
    } else {
      assert.equal(agentsGridEvidence.display, 'grid', `Agents Grid must use CSS grid at ${width}px`)
      assert.equal(agentsGridEvidence.columns, 2, `Agents Grid must render two columns at ${width}px`)
    }
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), 0, `Agents horizontal overflow at ${width}px`)
    await agentSession.screenshot({ path: `${evidenceDir}/${width}-agents-grid.png` })
    await agentsLayout.getByRole('button', { name: 'List view' }).click()
    assert.equal(await agentContainer.evaluate(node => getComputedStyle(node).display), 'block', `Agents List must use the compact list flow at ${width}px`)
    assert.equal(await agentSession.locator(':scope > div').first().getByText('active', { exact: true }).count(), 1, 'layout toggling must not change session lifecycle state')

    await page.getByRole('link', { name: 'Home' }).click()
    await page.getByTestId(`session-header-${longSession.id}`).waitFor()
  }
  console.log(JSON.stringify({ evidenceDir, widths: [1440, 834, 390], assertions: ['no horizontal overflow', 'Home List/Grid follows status badges', 'Home action row excludes layout controls', 'responsive session-header controls', 'mobile canonical single-column layout', 'tablet/desktop two-column Grid', 'Agents session-local List/Grid', 'Agents lifecycle state unchanged', 'stable agent order and actions', 'polling preserves view preference', 'status-only badges', 'owner reason dedicated panel', 'First/Last/Total aggregation', 'emerald expanded surface', 'gray collapsed surface', 'title keyboard expansion'] }))
} finally {
  await browser?.close()
  await new Promise(resolve => server.close(resolve))
  await vite.close()
}
