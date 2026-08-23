import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import http from 'node:http'
import { fileURLToPath } from 'node:url'
import { createServer as createViteServer } from 'vite'
import { chromium } from 'playwright'

const webRoot = fileURLToPath(new URL('..', import.meta.url))
const evidenceDir = process.env.THREADCELLS_SETTINGS_EVIDENCE_DIR || '/tmp/threadcells-settings-final-evidence'
const profileIds = [
  'architect_sol_high', 'code_supervisor', 'critical_sol_xhigh_owner', 'developer',
  'developer_sol_medium', 'developer_terra_high', 'developer_terra_medium',
  'framer_connect_luna_low', 'frontend_sol_medium', 'reviewer', 'reviewer_sol_high',
  'reviewer_sol_medium', 'reviewer_terra_high', 'strategist_sol_medium',
  'supervisor_sol_medium', 'supervisor_terra_medium', 'uiux_sol_high', 'worker_luna_medium',
]
const registryProfiles = profileIds.map((profileId, index) => ({
  profile_id: profileId,
  display_name: profileId,
  description: profileId === 'critical_sol_xhigh_owner'
    ? 'OWNER ONLY — exceptional direct critical architecture and implementation.'
    : profileId === 'developer_sol_medium'
      ? 'Precise implementation with unusually high reasoning density.'
      : `${profileId} operational profile.`,
  enabled: true,
  built_in: true,
  source: 'built-in',
  revision_id: `profile-revision-${index + 1}`,
  revision_number: 1,
  fingerprint: `fingerprint-${index + 1}`,
  owner_authorization_required: profileId === 'critical_sol_xhigh_owner',
  document: {
    provider_config_id: 'builtin-codex',
    model: profileId.includes('terra') ? 'gpt-5.6-terra' : 'gpt-5.6-sol',
    reasoning_level: profileId.includes('high') ? 'high' : 'medium',
    execution_mode: profileId.includes('supervisor') ? 'orchestrator' : profileId === 'critical_sol_xhigh_owner' ? 'owner_executor' : profileId.includes('reviewer') ? 'reviewer' : 'executor',
  },
}))
const spawnProfiles = registryProfiles.map(profile => ({
  name: profile.profile_id,
  description: profile.description,
  source: profile.source,
  enabled: true,
  built_in: true,
  revision_id: profile.revision_id,
  execution_mode: profile.document.execution_mode,
  owner_authorization_required: profile.owner_authorization_required,
}))
const capacity = {
  resource_state: 'GREEN', reasons: [],
  resident_supervisors: { active: 1, limit: 5, available: 4, certain: true },
  provider_executions: { active: 0, limit: 3, available: 3, certain: true },
  work_contexts: { active: 0, limit: 2, available: 2, certain: true },
  heavy_executions: { active: 0, limit: 1, available: 1, waiting: 0 },
  memory: { available_mib: 2048, swap_total_mib: 0, swap_free_mib: 0 },
  root_disk: { used_percent: 64, free_gib: 42 },
  memory_pressure: { some_avg10: 0, full_avg10: 0 },
  cpu_load: { one_minute: 0.5, cpu_count: 8 }, housekeeping: { ok: true },
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
const report = {
  ok: true, freed_bytes: 1536, logs_compressed: 2, logs_deleted: 1,
  attachments_deleted: 0, ephemeral_resources_removed: 1, browser_revisions_removed: 0,
  cache_pruned: 1, skipped_open: 1, skipped_unknown: 0, execution_failures: [], warnings: [],
}
const telegram = {
  schema_version: 1, enabled: false, chat_id: null, message_thread_id: null,
  token_configured: false, token_state: 'missing', configuration_state: 'not_configured',
  last_result: null, last_result_at: null, updated_at: null,
}

await mkdir(evidenceDir, { recursive: true })
const vite = await createViteServer({
  root: webRoot, configFile: false, plugins: [(await import('@vitejs/plugin-react')).default()],
  define: { __THREADCELLS_REVISION__: JSON.stringify('settings-evidence-revision'), __THREADCELLS_VERSION__: JSON.stringify('0.1.0-alpha.2') },
  appType: 'spa', server: { middlewareMode: true, hmr: false },
})
const json = (response, value) => {
  response.writeHead(200, { 'content-type': 'application/json' })
  response.end(JSON.stringify(value))
}
const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://localhost')
  if (request.method === 'GET' && url.pathname === '/ui/overview') return json(response, { sessions: 2, agents: 4, active: 1, waiting: 0, owner_gate: 0, cancelled: 0, completed: 3 })
  if (request.method === 'GET' && url.pathname === '/sessions') return json(response, [])
  if (request.method === 'GET' && url.pathname === '/settings/branding') return json(response, { title: 'ThreadCells', subtitle: 'Multi-agent control plane', logoUrl: '/threadcells-symbol.png', customLogo: false })
  if (request.method === 'GET' && url.pathname === '/settings/agent-dirs') return json(response, { agent_dirs: {}, extra_dirs: [] })
  if (request.method === 'GET' && url.pathname === '/settings/orchestration-capacity') return json(response, capacity)
  if (request.method === 'GET' && url.pathname === '/operator/session') return json(response, { configured: true, authenticated: false, expires_in_seconds: 0, session_ttl_seconds: 300, verifier_reference: 'THREADCELLS_OPERATOR_VERIFIER_FILE' })
  if (request.method === 'GET' && url.pathname === '/agents/profiles') return json(response, spawnProfiles)
  if (request.method === 'GET' && url.pathname === '/agents/providers') return json(response, [{ name: 'codex', binary: 'codex', installed: true }])
  if (request.method === 'GET' && url.pathname === '/projects') return json(response, [])
  if (request.method === 'GET' && url.pathname === '/api/v1/profiles') return json(response, registryProfiles)
  if (request.method === 'GET' && url.pathname === '/api/v1/providers') return json(response, { api_version: '1.0', entry_point_group: 'threadcells.provider_adapters.v1', adapters: [], configurations: [], load_failures: [] })
  if (request.method === 'GET' && url.pathname === '/api/v1/housekeeping') return json(response, housekeeping)
  if (request.method === 'GET' && url.pathname === '/api/v1/housekeeping/report') return json(response, report)
  if (request.method === 'GET' && url.pathname === '/api/v1/telegram') return json(response, telegram)
  vite.middlewares(request, response)
})

await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const address = server.address()
assert(address && typeof address !== 'string')
const origin = `http://127.0.0.1:${address.port}`
const viewports = [{ width: 1440, height: 900 }, { width: 834, height: 1112 }, { width: 390, height: 844 }]
const evidence = []
let browser
try {
  browser = await chromium.launch({ headless: true })
  for (const viewport of viewports) {
    const context = await browser.newContext({ viewport, hasTouch: true, isMobile: viewport.width === 390 })
    for (const surface of [
      { path: '/settings/general', heading: 'Orchestration Capacity', name: 'capacity' },
      { path: '/settings/profiles', heading: 'Profile Registry', name: 'profiles' },
      { path: '/settings/providers', heading: 'Provider Adapters', name: 'providers' },
      { path: '/settings/housekeeping', heading: 'Housekeeping', name: 'housekeeping' },
      { path: '/settings/telegram', heading: 'Telegram notifications', name: 'telegram' },
      { path: '/settings/about', heading: 'ThreadCells', name: 'about' },
    ]) {
      const page = await context.newPage()
      await page.goto(`${origin}${surface.path}`)
      await page.getByRole('heading', { name: surface.heading, exact: true }).last().waitFor()
      await page.getByRole('link', { name: 'Telegram', exact: true }).waitFor()
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
      assert(overflow <= 0, `${surface.name} horizontal overflow at ${viewport.width}px: ${overflow}`)
      assert.equal(await page.getByRole('link', { name: 'Telegram', exact: true }).count(), 1)
      const lowContrastText = await page.locator('main').evaluate(main => Array.from(main.querySelectorAll('.text-gray-500, .text-gray-600'))
        .filter(node => {
          const element = /** @type {HTMLElement} */ (node)
          const style = getComputedStyle(element)
          const rect = element.getBoundingClientRect()
          return Boolean(element.textContent?.trim()) && style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0
        })
        .map(node => node.textContent?.trim().slice(0, 80)))
      assert.deepEqual(lowContrastText, [], `${surface.name} has low-contrast operational text at ${viewport.width}px`)
      await page.screenshot({ path: `${evidenceDir}/${surface.name}-${viewport.width}.png`, fullPage: true })
      evidence.push({ surface: surface.name, width: viewport.width, overflow, lowContrastText })
      await page.close()
    }
    const page = await context.newPage()
    await page.goto(`${origin}/settings/profiles`)
    await page.getByText('18 of 18', { exact: true }).waitFor()
    await page.getByLabel('Search profiles').fill('critical_sol_xhigh_owner')
    await page.getByText('1 of 18', { exact: true }).waitFor()
    assert.equal(await page.getByText('OWNER ONLY — exceptional direct critical architecture and implementation.', { exact: true }).count(), 1)
    const advanced = page.getByRole('button', { name: /Advanced import and validation/ })
    await advanced.focus()
    await page.keyboard.press('Enter')
    assert.equal(await page.getByLabel('Profile JSON').count(), 1, `keyboard expansion at ${viewport.width}px`)

    await page.goto(`${origin}/settings/housekeeping`)
    await page.getByText('Cleanup policy', { exact: true }).waitFor()
    assert.equal(await page.getByText('retain_minutes', { exact: true }).count(), 0)
    assert.equal(await page.getByText('Protected · inventory only', { exact: true }).count(), 1)
    assert.equal(await page.getByText('1.5 KiB', { exact: true }).count(), 1)
    if (viewport.width < 1000) await page.getByRole('link', { name: 'About' }).tap()
    else await page.getByRole('link', { name: 'About' }).click()
    await page.getByText('settings-evidence-revision', { exact: true }).waitFor()
    assert.equal(await page.getByText('Licensed under Apache-2.0.', { exact: true }).count(), 1)

    await page.getByRole('link', { name: 'Agents' }).click()
    await page.getByRole('button', { name: 'Create Session & Spawn Agent' }).click()
    await page.getByRole('heading', { name: 'Create Session & Spawn Agent' }).waitFor()
    await page.getByText('Precise implementation with unusually high reasoning density.', { exact: true }).waitFor()
    assert.equal(await page.getByText('OWNER ONLY — exceptional direct critical architecture and implementation.', { exact: true }).count(), 1)
    await context.close()
  }
  console.log(JSON.stringify({ evidenceDir, profileCount: profileIds.length, viewports, evidence, assertions: ['current Capacity and Telegram navigation', 'registry and Spawn inventory', 'operator-owned XHigh copy', 'Profiles keyboard access', 'Housekeeping human labels and structured report', 'About identity', 'touch navigation', 'WCAG-AA operational helper colors', 'no horizontal overflow'] }))
} finally {
  await browser?.close()
  await new Promise(resolve => server.close(resolve))
  await vite.close()
}
