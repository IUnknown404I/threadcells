import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import http from 'node:http'
import { fileURLToPath } from 'node:url'
import { createServer as createViteServer } from 'vite'
import { chromium } from 'playwright'

const webRoot = fileURLToPath(new URL('..', import.meta.url))
const evidenceDir = process.env.CAO_VISUAL_EVIDENCE_DIR || '/tmp/cao-ui-agent-inline-detail-p1'
const sessions = [
  { id: 'cao-inline-a', name: 'cao-inline-a', status: 'active', created_at: '2' },
  { id: 'cao-inline-b', name: 'cao-inline-b', status: 'detached', created_at: '1' },
]
const displayName = session => session.name.startsWith('cao-') ? session.name.slice(4) : session.name
const terminalSpecs = {
  [sessions[0].name]: [
    { id: `${sessions[0].id}-terminal`, profile: 'developer_terra_high', activity: 'idle', execution: 'ready', lifecycle: 'running', workflow: 'active', workflowStatus: 'open' },
  ],
  [sessions[1].name]: [
    { id: `${sessions[1].id}-terminal`, profile: 'critical_sol_xhigh_owner_with_long_profile', activity: 'ready', execution: 'ready', lifecycle: 'running', workflow: 'owner_gate', workflowStatus: 'open' },
    { id: `${sessions[1].id}-exited`, profile: 'developer_terra_high_with_long_profile', activity: 'exited', execution: 'exited', lifecycle: 'exited', workflow: 'completed', workflowStatus: 'completed' },
    { id: `${sessions[1].id}-fenced`, profile: 'reviewer_terra_high_with_long_profile', activity: 'recovery_fenced', execution: 'recovery_fenced', lifecycle: 'recovery_fenced', workflow: 'cancelled', workflowStatus: 'completed' },
    { id: `${sessions[1].id}-recovery`, profile: 'critical_sol_xhigh_owner_with_long_profile', activity: 'ready', execution: 'ready', lifecycle: 'running', workflow: 'active', workflowStatus: 'open', recoveryEligible: true, queued: 1 },
  ],
}
const terminals = Object.fromEntries(sessions.map(session => [session.name, terminalSpecs[session.name].map((spec, index) => ({
  id: spec.id,
  tmux_session: session.name,
  tmux_window: String(index),
  provider: 'codex',
  agent_profile: spec.profile,
  project_id: spec.recoveryEligible ? 'project-recovery' : null,
  last_active: null,
}))]))
const sessionSummaries = sessions.map(session => ({
  ...session,
  agent_count: terminalSpecs[session.name].length,
  active_agent_count: terminalSpecs[session.name].filter(item => !['exited', 'recovery_fenced'].includes(item.lifecycle)).length,
  workflow_counts: Object.fromEntries([...new Set(terminalSpecs[session.name].map(item => item.workflow))].map(value => [value, terminalSpecs[session.name].filter(item => item.workflow === value).length])),
  activity_counts: Object.fromEntries([...new Set(terminalSpecs[session.name].map(item => item.activity))].map(value => [value, terminalSpecs[session.name].filter(item => item.activity === value).length])),
  project_name: null,
  last_active: session.created_at,
  first_agent: null,
  last_agent: null,
}))
const agentSummaries = Object.fromEntries(sessions.map(session => [session.id, terminals[session.name].map((terminal, index) => {
  const spec = terminalSpecs[session.name][index]
  return {
  id: terminal.id,
  name: terminal.tmux_window,
  provider: terminal.provider,
  session_id: session.id,
  session_name: session.name,
  agent_profile: terminal.agent_profile,
  activity: spec.activity,
  execution_state: spec.execution,
  lifecycle: spec.lifecycle,
  workflow_state: spec.workflow,
  workflow_status: spec.workflowStatus,
  workflow_reason: null,
  assignment_status: null,
  result_status: null,
  delivery_status: null,
  context_role: spec.recoveryEligible ? 'supervisor' : 'work',
  launch_worktree: null,
  managed_worktree_kind: null,
  managed_worktree_commit: null,
  managed_worktree_branch: null,
  projectId: spec.recoveryEligible ? 'project-recovery' : null,
  project_name: spec.recoveryEligible ? 'Recovery Project With Long Name' : null,
  project_path: spec.recoveryEligible ? '/managed/recovery-project' : null,
  creation_order: index + 1,
  last_active: terminal.last_active,
  queued_task_count: spec.queued || 0,
  }
})]))
const runtimeBranding = { title: 'ThreadCells', subtitle: 'Multi-agent control plane', logoUrl: '/threadcells-symbol.png', customLogo: false }

await mkdir(evidenceDir, { recursive: true })
const vite = await createViteServer({ root: webRoot, configFile: false, plugins: [(await import('@vitejs/plugin-react')).default()], appType: 'spa', server: { middlewareMode: true, hmr: false } })
const json = (response, value) => { response.writeHead(200, { 'content-type': 'application/json' }); response.end(JSON.stringify(value)) }
const requestJson = async request => { let body = ''; for await (const chunk of request) body += chunk; return body ? JSON.parse(body) : null }
const server = http.createServer(async (request, response) => {
  const url = new URL(request.url, 'http://localhost')
  if (request.method === 'GET' && url.pathname === '/ui/overview') return json(response, { sessions: 2, agents: 2, active: 2, waiting: 0, owner_gate: 0, cancelled: 0, completed: 0 })
  if (request.method === 'GET' && url.pathname === '/ui/sessions') return json(response, { items: sessionSummaries, total: sessionSummaries.length, limit: 10, offset: 0, next_offset: null })
  if (request.method === 'GET' && url.pathname === '/ui/agents') {
    const items = agentSummaries[url.searchParams.get('session_id')] || []
    return json(response, { items, total: items.length, limit: 40, offset: 0, next_offset: null, facets: { activities: ['idle'], workflow_states: ['active'], profiles: ['developer_terra_high'] } })
  }
  if (request.method === 'GET' && url.pathname === '/sessions') return json(response, sessions)
  const matchedSession = sessions.find(session => url.pathname === `/sessions/${session.name}`)
  if (request.method === 'GET' && matchedSession) return json(response, { session: matchedSession, terminals: terminals[matchedSession.name] })
  if (request.method === 'GET' && url.pathname.startsWith('/terminals/')) {
    const terminalId = url.pathname.split('/')[2]
    return json(response, { id: terminalId, provider: 'codex', status: 'idle', lifecycle: 'running', workflow_state: 'active', last_active: null })
  }
  if (request.method === 'POST' && url.pathname === '/recovery-takeovers/capabilities') {
    const body = await requestJson(request)
    const eligibleId = `${sessions[1].id}-recovery`
    return json(response, { capabilities: (body?.terminal_ids || []).map(terminalId => ({
      terminal_id: terminalId,
      eligible: terminalId === eligibleId,
      reason_code: terminalId === eligibleId ? null : 'RECOVERY_HEALTHY_RUNTIME_ACTIVE',
    })) })
  }
  if (request.method === 'GET' && url.pathname === '/agents/profiles') return json(response, [])
  if (request.method === 'GET' && url.pathname === '/projects') return json(response, [])
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
    localStorage.setItem('threadcells.app.locale', 'ru')
    const original = window.setInterval
    window.setInterval = (handler, timeout, ...args) => original(handler, timeout === 5000 ? 80 : timeout === 3000 ? 60 : timeout, ...args)
  })
  await page.goto(origin)
  await page.getByRole('link', { name: 'Агенты' }).click()
  const a = page.getByTestId(`agent-session-${sessions[0].id}`)
  const b = page.getByTestId(`agent-session-${sessions[1].id}`)
  await a.waitFor()
  await b.waitFor()

  await a.getByRole('button', { name: `Развернуть ${displayName(sessions[0])}` }).press('Enter')
  const aDetail = a.getByTestId(`agent-session-detail-${sessions[0].id}`)
  await aDetail.waitFor()
  assert.equal(await page.getByTestId(/agent-session-detail-/).count(), 1, 'A detail must render exactly once')
  assert.equal(await aDetail.evaluate(detail => detail.parentElement?.dataset.testid), `agent-session-${sessions[0].id}`, 'A detail must be inline below A')

  await b.getByRole('button', { name: `Развернуть ${displayName(sessions[1])}` }).click()
  const bDetail = b.getByTestId(`agent-session-detail-${sessions[1].id}`)
  await bDetail.waitFor()
  assert.equal(await aDetail.count(), 0, 'A detail must move away when B is selected')
  assert.equal(await page.getByTestId(/agent-session-detail-/).count(), 1, 'B detail must remain the only detail')
  assert.equal(await bDetail.evaluate(detail => detail.parentElement?.dataset.testid), `agent-session-${sessions[1].id}`, 'B detail must be inline below B')
  assert.equal(await b.evaluate(node => node.nextElementSibling?.dataset.testid || null), null, 'no detached detail may follow the Sessions list')

  const mainActionTestIds = terminalSpecs[sessions[1].name].map(item => `agent-actions-${item.id}`)
  for (const width of [1440, 1200, 834, 390]) {
    await page.setViewportSize({ width, height: 960 })
    const layouts = []
    for (const testId of mainActionTestIds) {
      const actionStrip = bDetail.getByTestId(testId)
      layouts.push(await actionStrip.evaluate(element => {
        const bounds = element.getBoundingClientRect()
        const card = element.closest('[data-testid^="agent-detail-card-"]')
        const cardBounds = card?.getBoundingClientRect()
        const buttons = [...element.querySelectorAll(':scope > button')]
        const buttonBounds = buttons.map(button => button.getBoundingClientRect())
        const labels = buttons.slice(0, 3).map(button => button.querySelector('span'))
        return {
          columns: getComputedStyle(element).gridTemplateColumns.split(' ').length,
          rightGap: cardBounds ? Math.round(cardBounds.right - 12 - bounds.right) : null,
          rows: new Set(buttonBounds.map(button => Math.round(button.top))).size,
          text: buttons.map(button => button.innerText.trim()),
          labelsVisible: labels.map(label => Boolean(label && getComputedStyle(label).display !== 'none')),
          labelsClipped: labels.map(label => Boolean(label && label.scrollWidth > label.clientWidth + 1)),
          gaps: buttonBounds.slice(1).map((button, index) => Math.round(button.left - buttonBounds[index].right)),
          minButtonWidth: Math.min(...buttonBounds.map(button => button.width)),
          left: Math.round(bounds.left),
          right: Math.round(bounds.right),
          width: Math.round(bounds.width),
          cardWidth: Math.round(cardBounds?.width || 0),
          contained: cardBounds ? bounds.left >= cardBounds.left + 12 - 1 && bounds.right <= cardBounds.right - 12 + 1 : false,
        }
      }))
    }
    for (const actionLayout of layouts) {
      assert.equal(actionLayout.columns, 6, `agent actions must contain exactly six main columns at ${width}px`)
      assert.equal(Math.abs(actionLayout.rightGap) <= 1, true, `agent actions must remain flush right at ${width}px`)
      assert.equal(actionLayout.rows, 1, `agent actions must remain on one row at ${width}px`)
      assert.equal(actionLayout.contained, true, `agent actions must remain inside the card at ${width}px`)
      assert.equal(actionLayout.minButtonWidth >= 44, true, `agent action touch targets must remain at least 44px at ${width}px`)
      assert.equal(new Set(actionLayout.gaps).size, 1, `agent action gaps must be even at ${width}px`)
      assert.equal(Math.max(...actionLayout.gaps) <= 4, true, `agent action gaps must stay compact at ${width}px`)
      assert.deepEqual(actionLayout.text.slice(-3), ['', '', ''], `colored actions must be icon-only at ${width}px`)
      if (actionLayout.cardWidth >= 480) {
        assert.deepEqual(actionLayout.text.slice(0, 3), ['История', 'Почта', 'Вывод'], `desktop labels must be complete at ${width}px`)
        assert.deepEqual(actionLayout.labelsVisible, [true, true, true], `desktop labels must be visible at ${width}px`)
        assert.deepEqual(actionLayout.labelsClipped, [false, false, false], `desktop labels must not be ellipsized at ${width}px`)
      } else {
        assert.deepEqual(actionLayout.text.slice(0, 3), ['', '', ''], `narrow labels must compact as a group at ${width}px`)
        assert.deepEqual(actionLayout.labelsVisible, [false, false, false], `narrow labels must be hidden as a group at ${width}px`)
      }
    }
    const stableGeometry = layouts.map(item => [item.left, item.right, item.width, item.gaps.join(',')])
    assert.equal(new Set(stableGeometry.map(item => JSON.stringify(item))).size, 1, `neighboring lifecycle states must align at ${width}px`)
    const recoveryAction = bDetail.getByTestId(`agent-recovery-action-${sessions[1].id}-recovery`)
    await recoveryAction.waitFor()
    assert.equal(await bDetail.getByRole('button', { name: 'Восстановить агента' }).count(), 1, `only eligible recovery must render at ${width}px`)
    assert.equal(await recoveryAction.locator('[data-testid^="agent-actions-"]').count(), 0, 'Recovery must remain outside the six-action strip')
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), 0, `horizontal overflow at ${width}px`)
    await b.screenshot({ path: `${evidenceDir}/${width}-b-inline.png` })
  }

  await page.setViewportSize({ width: 834, height: 960 })
  await b.getByRole('button', { name: 'Сетка', exact: true }).click()
  const gridLayout = await bDetail.getByTestId(`agent-actions-${sessions[1].id}-terminal`).evaluate(element => ({
    cardWidth: element.closest('[data-testid^="agent-detail-card-"]')?.getBoundingClientRect().width || 0,
    columns: getComputedStyle(element).gridTemplateColumns.split(' ').length,
    visibleText: [...element.querySelectorAll(':scope > button')].map(button => button.innerText.trim()),
  }))
  assert.equal(gridLayout.cardWidth < 480, true, 'grid card must exercise the compact container-width mode')
  assert.equal(gridLayout.columns, 6, 'grid card must retain all six main actions')
  assert.deepEqual(gridLayout.visibleText, ['', '', '', '', '', ''], 'grid card must compact all labels together based on card width')
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), 0, 'grid mode must not create page overflow at 834px')
  await b.screenshot({ path: `${evidenceDir}/834-b-grid.png` })
  await b.getByRole('button', { name: 'Список', exact: true }).click()

  await bDetail.getByTestId(`agent-detail-card-${sessions[1].id}-terminal`).getByTitle('Завершение терминала').click()
  await page.getByRole('heading', { name: 'Завершить' }).waitFor()
  assert.equal(await bDetail.count(), 1, 'terminal actions must not collapse the selected session')
  await page.getByRole('button', { name: 'Отмена' }).click()

  await b.getByRole('button', { name: `Свернуть ${displayName(sessions[1])}` }).click()
  assert.equal(await bDetail.count(), 0, 'clicking selected B must collapse it')
  assert.equal(await page.getByTestId(/agent-session-detail-/).count(), 0, 'no detached bottom detail may remain after collapse')
  console.log(JSON.stringify({ evidenceDir, widths: [1440, 1200, 834, 390], assertions: ['mixed Ready, exited, owner_gate, and recovery states', 'six main actions without an empty Recovery slot', 'container-width compact mode including 834px grid cards', 'stable one-row right-aligned action strip', 'complete unclipped desktop labels', 'even compact gaps', '44px touch targets', 'Recovery outside the main strip', 'colored actions icon-only', 'no horizontal overflow'] }))
} finally {
  await browser?.close()
  await new Promise(resolve => server.close(resolve))
  await vite.close()
}
