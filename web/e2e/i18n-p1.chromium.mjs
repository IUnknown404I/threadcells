import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import http from 'node:http'
import { fileURLToPath } from 'node:url'
import { createServer as createViteServer } from 'vite'
import { chromium } from 'playwright'

const webRoot = fileURLToPath(new URL('..', import.meta.url))
const evidenceDir = process.env.THREADCELLS_I18N_EVIDENCE_DIR || '/tmp/threadcells-i18n-p1-evidence'
const session = {
  id: 'i18n-session', name: 'LOCALIZATION SESSION', status: 'active', created_at: '2026-08-26T00:00:00Z',
  agent_count: 1, active_agent_count: 1, workflow_counts: { completed: 1 }, activity_counts: { idle: 1 },
  project_name: 'ThreadCells', last_active: '2026-08-26T00:00:00Z',
  first_agent: { id: 'i18n-terminal', activity: 'idle', execution_state: 'ready', lifecycle: 'running', workflow_state: 'completed', workflow_reason: null },
  last_agent: { id: 'i18n-terminal', activity: 'idle', execution_state: 'ready', lifecycle: 'running', workflow_state: 'completed', workflow_reason: null },
}
const agent = {
  id: 'i18n-terminal', name: '0', provider: 'codex', session_id: session.id, session_name: session.name,
  agent_profile: 'developer_sol_medium', activity: 'idle', execution_state: 'ready', lifecycle: 'running',
  workflow_state: 'completed', workflow_status: 'completed', workflow_reason: null, assignment_status: null,
  result_status: 'complete', delivery_status: 'acknowledged', context_role: 'owner', launch_worktree: '/technical/path',
  managed_worktree_kind: 'managed', managed_worktree_commit: '0123456789abcdef', managed_worktree_branch: 'feat/i18n',
  projectId: 'project-threadcells', project_name: 'ThreadCells', project_path: '/technical/project', creation_order: 1,
  last_active: '2026-08-26T00:00:00Z',
}
const rawMessage = 'RAW_PROVIDER_PAYLOAD status=exited reason_code=TERMINAL_RUNTIME_ACTIVE'
const capacity = {
  resource_state: 'GREEN', reasons: [],
  resident_supervisors: { active: 1, limit: 5, available: 4, certain: true },
  provider_executions: { active: 0, limit: 3, available: 3, certain: true },
  work_contexts: { active: 0, limit: 2, available: 2, certain: true },
  heavy_executions: { active: 0, limit: 1, available: 1, waiting: 0 },
  memory: { available_mib: 4096, swap_total_mib: 0, swap_free_mib: 0 },
  root_disk: { state: 'GREEN', used_percent: 42, free_gib: 28 },
  memory_pressure: { some_avg10: 0, full_avg10: 0 }, cpu_load: { one_minute: 0.4, cpu_count: 8 },
  housekeeping: { ok: true },
}
const housekeeping = {
  schema_version: 1,
  policy: {
    logs: { enabled: true, compress_after_minutes: 1440, retain_minutes: 10080 },
    attachments: { enabled: true, retain_minutes: 10080 }, ephemeral: { enabled: true },
    browser_cache: { enabled: true, retain_minutes: 10080 }, package_cache: { enabled: true },
    releases: { enabled: true, retain_count: 2, retain_minutes: 10080 }, backups: { enabled: false },
  },
  schedule: { frequent: '6h', weekly: 'Sun 04:00 UTC', pressure: 'on_red' },
}
const profiles = [{
  name: 'developer_sol_medium', profile_id: 'developer_sol_medium', display_name: 'developer_sol_medium',
  description: 'Technical profile copy stays canonical.', source: 'built-in', enabled: true, built_in: true,
  revision_id: 'profile-revision-1', execution_mode: 'executor', owner_authorization_required: false,
}]

await mkdir(evidenceDir, { recursive: true })
const vite = await createViteServer({
  root: webRoot,
  configFile: false,
  plugins: [(await import('@vitejs/plugin-react')).default()],
  define: {
    __THREADCELLS_REVISION__: JSON.stringify('i18n-p1-browser-evidence'),
    __THREADCELLS_VERSION__: JSON.stringify('0.3.3-alpha'),
  },
  appType: 'spa',
  server: { middlewareMode: true, hmr: false },
})
const json = (response, value) => {
  response.writeHead(200, { 'content-type': 'application/json' })
  response.end(JSON.stringify(value))
}
const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://localhost')
  if (request.method === 'GET' && url.pathname === '/ui/overview') return json(response, { sessions: 1, agents: 1, active: 1, waiting: 0, owner_gate: 0, cancelled: 0, completed: 1 })
  if (request.method === 'GET' && url.pathname === '/ui/sessions') return json(response, { items: [session], total: 1, limit: 10, offset: 0, next_offset: null })
  if (request.method === 'GET' && url.pathname === '/ui/agents') return json(response, { items: [agent], total: 1, limit: 40, offset: 0, next_offset: null, facets: { activities: ['idle'], workflow_states: ['completed'], profiles: ['developer_sol_medium'] } })
  if (request.method === 'GET' && url.pathname === '/sessions') return json(response, [session])
  if (request.method === 'GET' && url.pathname === `/sessions/${encodeURIComponent(session.name)}`) return json(response, { session, terminals: [{ id: agent.id, tmux_session: session.name, tmux_window: '0', provider: agent.provider, agent_profile: agent.agent_profile, last_active: agent.last_active }] })
  if (request.method === 'GET' && url.pathname === `/sessions/${encodeURIComponent(session.name)}/working-directory`) return json(response, { working_directory: '/technical/project' })
  if (request.method === 'GET' && url.pathname === `/terminals/${agent.id}`) return json(response, { id: agent.id, name: agent.name, provider: agent.provider, session_name: session.name, agent_profile: agent.agent_profile, status: 'idle', execution_state: 'ready', lifecycle: 'running', workflow_state: 'completed', workflow_status: 'completed' })
  if (request.method === 'GET' && url.pathname === `/terminals/${agent.id}/inbox/messages`) return json(response, [{ id: 'message-1', sender_id: 'owner', receiver_id: agent.id, message: rawMessage, status: 'delivered', created_at: '2026-08-26T00:00:00Z' }])
  if (request.method === 'GET' && url.pathname === '/delegation-results') return json(response, [])
  if (request.method === 'GET' && url.pathname === '/settings/branding') return json(response, { title: 'ThreadCells', subtitle: 'Multi-agent control plane', logoUrl: '/threadcells-symbol.png', customLogo: false })
  if (request.method === 'GET' && url.pathname === '/agents/providers') return json(response, [{ name: 'codex', binary: 'codex', installed: true, available: true, availability: 'INSTALLED_AND_READY' }])
  if (request.method === 'GET' && url.pathname === '/agents/profiles') return json(response, profiles)
  if (request.method === 'GET' && url.pathname === '/projects') return json(response, [])
  if (request.method === 'GET' && url.pathname === '/operator/session') return json(response, { configured: true, authenticated: false, expires_in_seconds: 0, session_ttl_seconds: 300, verifier_reference: 'THREADCELLS_OPERATOR_VERIFIER_FILE' })
  if (request.method === 'GET' && url.pathname === '/api/v1/housekeeping') return json(response, housekeeping)
  if (request.method === 'GET' && url.pathname === '/api/v1/housekeeping/report') return json(response, {})
  if (request.method === 'GET' && url.pathname === '/settings/orchestration-capacity') return json(response, capacity)
  vite.middlewares(request, response)
})

await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const address = server.address()
assert(address && typeof address !== 'string')
const origin = `http://127.0.0.1:${address.port}`
const viewports = [{ width: 1440, height: 900 }, { width: 834, height: 1112 }, { width: 390, height: 844 }]

const labels = {
  en: {
    lang: 'Language', home: 'Home', agents: 'Agents', settings: 'Settings', docs: 'Docs', sessions: 'Sessions',
    create: 'Create Session & Spawn Agent', sessionName: 'Session name', cancel: 'Cancel', inbox: 'Inbox',
    inboxTitle: 'Agent Inbox', housekeeping: 'Housekeeping', full: 'Delete all system files — Full Cleanup',
    docsTitle: 'Start here: What is ThreadCells?', targetLocale: 'English',
  },
  ru: {
    lang: 'Язык', home: 'Главная', agents: 'Агенты', settings: 'Настройки', docs: 'Документация', sessions: 'Сессии',
    create: 'Создать сессию и запустить агента', sessionName: 'Название сессии', cancel: 'Отмена', inbox: 'Входящие',
    inboxTitle: 'Входящие агента', housekeeping: 'Обслуживание', full: 'Удалить все системные файлы — полная очистка',
    docsTitle: 'Начните здесь: что такое ThreadCells?', targetLocale: 'Русский',
  },
}

function overflow(page, surface, locale, width) {
  return page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth).then(value => {
    assert(value <= 0, `${surface} ${locale} horizontal overflow at ${width}px: ${value}`)
    return value
  })
}

async function selectLocale(page, locale) {
  const copy = labels[locale]
  const summary = page.locator('summary').filter({ hasText: locale.toUpperCase() })
  await summary.click()
  await page.getByRole('menuitem', { name: copy.targetLocale, exact: true }).click()
  await page.waitForFunction(expected => document.documentElement.lang === expected, locale)
}

async function assertSurfaceSet(page, locale, viewport) {
  const copy = labels[locale]
  await page.goto(origin)
  await page.getByRole('link', { name: copy.home, exact: true }).waitFor()
  assert.equal(await page.locator('html').getAttribute('lang'), locale)
  await page.getByText(copy.sessions, { exact: true }).first().waitFor()
  await overflow(page, 'home', locale, viewport.width)
  await page.screenshot({ path: `${evidenceDir}/${locale}-home-${viewport.width}.png`, fullPage: true })

  await page.getByRole('link', { name: new RegExp(`^${copy.agents}`) }).click()
  await page.getByText(`${copy.sessions} (1)`, { exact: true }).waitFor()
  await page.getByRole('button', { name: new RegExp(`^(Expand|Развернуть) ${session.name}$`) }).click()
  await page.getByRole('button', { name: copy.inbox, exact: true }).click()
  await page.getByRole('heading', { name: copy.inboxTitle, exact: true }).waitFor()
  assert.equal(await page.getByText(rawMessage, { exact: true }).count(), 1, 'Inbox content must remain byte-equivalent')
  await overflow(page, 'inbox', locale, viewport.width)
  await page.getByRole('button', { name: /^(Close|Закрыть)$/ }).click()
  await page.getByRole('button', { name: copy.create, exact: true }).click()
  await page.getByRole('heading', { name: copy.create, exact: true }).waitFor()
  await page.getByText(copy.sessionName, { exact: false }).last().waitFor()
  await overflow(page, 'agent-dialog', locale, viewport.width)
  await page.screenshot({ path: `${evidenceDir}/${locale}-agents-dialog-${viewport.width}.png`, fullPage: true })
  await page.getByRole('button', { name: copy.cancel, exact: true }).click()

  await page.goto(`${origin}/settings/housekeeping`)
  await page.getByRole('heading', { name: copy.housekeeping, exact: true }).last().waitFor()
  await page.getByRole('heading', { name: copy.full, exact: true }).waitFor()
  await overflow(page, 'housekeeping', locale, viewport.width)
  await page.screenshot({ path: `${evidenceDir}/${locale}-housekeeping-${viewport.width}.png`, fullPage: true })

  await page.goto(`${origin}/docs/overview#what-threadcells-controls`)
  await page.getByRole('heading', { name: copy.docsTitle, exact: true }).waitFor()
  assert.equal(new URL(page.url()).pathname, '/docs/overview')
  await overflow(page, 'docs', locale, viewport.width)
  await page.screenshot({ path: `${evidenceDir}/${locale}-docs-${viewport.width}.png`, fullPage: true })
}

let browser
try {
  browser = await chromium.launch({ headless: true })
  for (const viewport of viewports) {
    const context = await browser.newContext({ viewport, hasTouch: true, isMobile: viewport.width === 390, locale: 'ru-RU' })
    const page = await context.newPage()
    const errors = []
    page.on('pageerror', error => errors.push(error.message))

    await page.goto(origin)
    await page.getByRole('link', { name: labels.en.home, exact: true }).waitFor()
    assert.equal(await page.locator('html').getAttribute('lang'), 'en', 'Russian browser locale must not opt in to Russian UI')
    await assertSurfaceSet(page, 'en', viewport)

    await page.goto(origin)
    await page.locator(`summary[aria-label="${labels.en.lang}"]`).click()
    await page.getByRole('menuitem', { name: labels.ru.targetLocale, exact: true }).click()
    await page.waitForFunction(() => document.documentElement.lang === 'ru')
    await assertSurfaceSet(page, 'ru', viewport)

    await page.reload()
    await page.getByRole('heading', { name: labels.ru.docsTitle, exact: true }).waitFor()
    assert.equal(await page.locator('html').getAttribute('lang'), 'ru')
    assert.equal(await page.evaluate(() => localStorage.getItem('threadcells.app.locale')), 'ru')
    await page.locator(`summary[aria-label="${labels.ru.lang}"]`).click()
    await page.getByRole('menuitem', { name: labels.en.targetLocale, exact: true }).click()
    await page.getByRole('heading', { name: labels.en.docsTitle, exact: true }).waitFor()
    assert.equal(await page.locator('html').getAttribute('lang'), 'en')
    assert.equal(await page.evaluate(() => localStorage.getItem('threadcells.app.locale')), 'en')
    assert.deepEqual(errors, [])
    await context.close()
  }
  console.log(JSON.stringify({
    evidenceDir,
    viewports: viewports.map(({ width }) => width),
    locales: ['en', 'ru'],
    assertions: ['English default despite ru-RU browser', 'immediate EN/RU switch', 'preference reload', 'Home', 'Agents', 'Inbox raw content', 'dialog', 'Housekeeping and Full Cleanup', 'same-slug Docs', 'no horizontal overflow'],
  }))
} finally {
  await browser?.close()
  await new Promise(resolve => server.close(resolve))
  await vite.close()
}
