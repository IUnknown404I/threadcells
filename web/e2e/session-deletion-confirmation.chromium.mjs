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
  if (request.method === 'GET' && url.pathname === `/sessions/${sessionId}/deletion-preflight`) return json(response, {
    eligible: false,
    deletion_mode: 'eligible_with_historical_indeterminate_retirement',
    cancellable: false,
    can_resolve_and_delete: true,
    already_deleted: false,
    requires_cancellation_confirmation: false,
    requires_historical_indeterminate_confirmation: true,
    requires_dirty_confirmation: false,
    modified_files: 0,
    untracked_files: 0,
    reason_code: 'HISTORICAL_EFFECT_OUTCOME_UNKNOWN',
    reason_codes: ['HISTORICAL_EFFECT_OUTCOME_UNKNOWN'],
    plan_token: 'b'.repeat(64),
    current_queue_count: 4,
    cancellable_count: 0,
    historical_indeterminate_count: 4,
    unsafe_count: 0,
    live_unsafe_count: 0,
    active_runtime_count: 0,
    active_execution_count: 0,
    plan_limit: 500,
    blockers: [{ category: 'historical_indeterminate_effects', count: 4, disposition: 'historical_indeterminate', reason_codes: ['HISTORICAL_EFFECT_OUTCOME_UNKNOWN'] }],
    cancellable_blockers: [],
    historical_indeterminate_blockers: [{ category: 'historical_indeterminate_effects', count: 4, disposition: 'historical_indeterminate', reason_codes: ['HISTORICAL_EFFECT_OUTCOME_UNKNOWN'] }],
    unsafe_blockers: [],
    cancellation_plan: { count: 0, categories: [] },
    historical_indeterminate_plan: { count: 4, categories: [{ category: 'historical_indeterminate_effects', count: 4, disposition: 'historical_indeterminate', reason_codes: ['HISTORICAL_EFFECT_OUTCOME_UNKNOWN'] }] },
  })
  if (request.method === 'DELETE' && url.pathname === `/sessions/${sessionId}`) {
    assert.equal(url.searchParams.get('retire_historical_indeterminate'), 'true')
    assert.equal(url.searchParams.get('cancel_unresolved_work'), 'false')
    assert.equal(url.searchParams.get('cancellation_plan_token'), 'b'.repeat(64))
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
  const locales = [
    { code: 'en', agents: 'Agents', action: 'Delete session', dialog: 'Delete Session?', cancel: 'Cancel' },
    { code: 'ru', agents: 'Агенты', action: 'Удалить сессию', dialog: 'Удалить Session?', cancel: 'Отмена' },
  ]
  for (const locale of locales) {
    await page.goto(origin)
    await page.evaluate(code => localStorage.setItem('threadcells.app.locale', code), locale.code)
    await page.reload()
    await page.getByRole('link', { name: locale.agents }).click()
    for (const width of [1440, 834, 390]) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 960 })
      const action = page.getByTitle(locale.action)
      await action.click()
      const dialog = page.getByRole('dialog', { name: locale.dialog })
      await dialog.waitFor({ state: 'visible' })
      const [dialogBox, viewport] = await Promise.all([dialog.boundingBox(), page.viewportSize()])
      assert(dialogBox && viewport)
      assert(dialogBox.x >= 0 && dialogBox.x + dialogBox.width <= viewport.width + 1, `${locale.code} dialog escaped viewport at ${width}px`)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), 0, `${locale.code} horizontal overflow at ${width}px`)
      assert.equal(deleteRequestCount, 0, 'opening confirmation must not delete')
      await page.getByRole('button', { name: locale.cancel }).click()
      assert.equal(await action.evaluate(node => document.activeElement === node), true, 'cancelling must restore focus')
      assert.equal(deleteRequestCount, 0, 'cancelling confirmation must not delete')
    }
  }

  await page.goto(origin)
  await page.evaluate(() => localStorage.setItem('threadcells.app.locale', 'en'))
  await page.reload()
  await page.getByRole('link', { name: 'Agents' }).click()
  await page.getByTitle('Delete session').click()
  const confirm = page.getByRole('button', { name: 'Delete Session', exact: true })
  await confirm.click()
  const closing = page.getByRole('button', { name: 'Working…', exact: true })
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
