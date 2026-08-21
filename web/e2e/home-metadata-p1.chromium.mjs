import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import http from 'node:http'
import { fileURLToPath } from 'node:url'
import { createServer as createViteServer } from 'vite'
import { chromium } from 'playwright'

const webRoot = fileURLToPath(new URL('..', import.meta.url))
const evidenceDir = process.env.CAO_VISUAL_EVIDENCE_DIR || '/tmp/cao-ui-home-metadata-p1'
const runtimeBranding = { title: 'ThreadCells', subtitle: 'Multi-agent control plane', logoUrl: '/threadcells-symbol.png', customLogo: false }
const projectTitle = 'Project with a deliberately long authoritative title that must truncate cleanly at narrow viewports'
const profileDescription = 'Implements and fixes code.'
const sessions = [
  { id: 'project-session', name: 'cao-project-session', status: 'active', created_at: '3' },
  { id: 'no-project-session', name: 'cao-no-project-session', status: 'active', created_at: '2' },
  { id: 'long-project-session', name: 'cao-long-project-session', status: 'active', created_at: '1' },
  { id: 'partial-project-session', name: 'cao-partial-project-session', status: 'active', created_at: '0' },
  { id: 'mixed-project-session', name: 'cao-mixed-project-session', status: 'active', created_at: '-1' },
]
const terminal = (id, session, project = null) => ({ id, tmux_session: session.name, tmux_window: '0', provider: 'codex', agent_profile: 'developer_terra_high', last_active: null, ...(project || {}) })
const terminals = {
  [sessions[0].name]: [
    terminal('project-a', sessions[0], { project_id: 'project-a', project_name: 'Project A', project_path: '/work/project-a' }),
    terminal('project-b', sessions[0], { project_id: 'project-a', project_name: 'Project A', project_path: '/work/project-a' }),
  ],
  [sessions[1].name]: [terminal('no-project-a', sessions[1])],
  [sessions[2].name]: [terminal('long-project-a', sessions[2], { project_id: 'project-long', project_name: projectTitle, project_path: '/work/project-long' })],
  [sessions[3].name]: [terminal('partial-project-a', sessions[3], { project_id: 'project-partial', project_name: 'Partial Project', project_path: null })],
  [sessions[4].name]: [
    terminal('mixed-project-a', sessions[4], { project_id: 'project-a', project_name: 'Project A', project_path: '/work/project-a' }),
    terminal('mixed-project-b', sessions[4], { project_id: 'project-b', project_name: 'Project B', project_path: '/work/project-b' }),
  ],
}
const capacity = { resource_state: 'GREEN', reasons: [], resident_supervisors: { active: 5, limit: 5, available: 0, certain: true }, provider_executions: { active: 2, limit: 3, available: 1, certain: true }, work_contexts: { active: 1, limit: 2, available: 1, certain: true }, heavy_executions: { active: 0, limit: 1, available: 1, waiting: null }, memory: { available_mib: 1024, swap_total_mib: 0, swap_free_mib: 0 }, root_disk: { used_percent: 1, free_gib: 100 }, memory_pressure: { some_avg10: 0, full_avg10: 0 }, cpu_load: { one_minute: 1.75, cpu_count: 8 }, housekeeping: { ok: true } }

await mkdir(evidenceDir, { recursive: true })
const vite = await createViteServer({ root: webRoot, configFile: false, plugins: [(await import('@vitejs/plugin-react')).default()], appType: 'spa', server: { middlewareMode: true, hmr: false } })
const json = (response, value) => { response.writeHead(200, { 'content-type': 'application/json' }); response.end(JSON.stringify(value)) }
const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://localhost')
  if (request.method === 'GET' && url.pathname === '/sessions') return json(response, sessions)
  const matchedSession = sessions.find(session => url.pathname === `/sessions/${session.name}`)
  if (request.method === 'GET' && matchedSession) return json(response, { session: matchedSession, terminals: terminals[matchedSession.name] })
  if (request.method === 'GET' && url.pathname.startsWith('/terminals/')) return json(response, { id: url.pathname.split('/')[2], provider: 'codex', status: 'idle', lifecycle: 'running', workflow_state: 'active', last_active: null })
  if (request.method === 'GET' && url.pathname === '/agents/profiles') return json(response, [{ name: 'developer', description: profileDescription, source: 'built-in' }])
  if (request.method === 'GET' && url.pathname === '/settings/agent-dirs') return json(response, { agent_dirs: {}, extra_dirs: [] })
  if (request.method === 'GET' && url.pathname === '/settings/orchestration-capacity') return json(response, capacity)
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
    const original = window.setInterval
    window.setInterval = (handler, timeout, ...args) => original(handler, timeout === 5000 ? 80 : timeout === 3000 ? 60 : timeout, ...args)
  })
  await page.goto(origin)
  const projectHeader = page.getByTestId('session-header-project-session')
  const noProjectHeader = page.getByTestId('session-header-no-project-session')
  const longProjectHeader = page.getByTestId('session-header-long-project-session')
  const partialProjectHeader = page.getByTestId('session-header-partial-project-session')
  const mixedProjectHeader = page.getByTestId('session-header-mixed-project-session')
  await projectHeader.waitFor()
  await noProjectHeader.waitFor()
  await longProjectHeader.waitFor()
  await partialProjectHeader.waitFor()
  await mixedProjectHeader.waitFor()

  for (const width of [1440, 834, 390]) {
    await page.setViewportSize({ width, height: 960 })
    assert.equal(await noProjectHeader.getByText(/^Project:/).count(), 0, `no-project badge at ${width}px`)
    assert.equal(await partialProjectHeader.getByText(/^Project:/).count(), 0, `partial-project badge at ${width}px`)
    assert.equal(await mixedProjectHeader.getByText(/^Project:/).count(), 0, `mixed-project badge at ${width}px`)
    const longBadge = longProjectHeader.getByTitle(`Project: ${projectTitle}`)
    const longBadgeBox = await longBadge.boundingBox()
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    assert.equal(overflow, 0, `Home horizontal overflow at ${width}px: ${overflow}`)
    assert(longBadgeBox && longBadgeBox.height < 30, `long project title wrapped at ${width}px: ${JSON.stringify(longBadgeBox)}`)
    await page.screenshot({ path: `${evidenceDir}/home-${width}.png`, fullPage: true })

    await page.getByRole('link', { name: 'Settings' }).click()
    const profiles = page.getByRole('button', { name: 'Profiles', exact: true })
    await profiles.waitFor()
    assert.match(await profiles.textContent() || '', /1/, `Profiles count at ${width}px`)
    assert.equal(await page.getByLabel('Orchestration capacity details').getByRole('button').count(), 0)
    await page.getByText('Resident supervisors', { exact: true }).waitFor()
    await page.getByText('Provider executions', { exact: true }).waitFor()
    await page.getByText('CPU load', { exact: true }).waitFor()
    assert.match(await page.getByText('CPU load', { exact: true }).locator('..').textContent() || '', /1\.75 \/ 8 CPUs/, `CPU load at ${width}px`)
    assert.equal(await page.getByText('Provider contexts', { exact: true }).count(), 0)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), 0, `Settings horizontal overflow at ${width}px`)
    await page.screenshot({ path: `${evidenceDir}/settings-${width}-grid.png`, fullPage: true })
    if (width === 1440) {
      await profiles.focus()
      await page.keyboard.press('Enter')
    } else {
      await profiles.click()
    }
    const dialog = page.getByRole('dialog', { name: 'Profiles' })
    await dialog.waitFor()
    assert.equal(await dialog.getByText(profileDescription, { exact: true }).count(), 1, `English profile description at ${width}px`)
    assert.doesNotMatch(await dialog.textContent() || '', /[\u0400-\u052f]/, `Profiles modal must not contain Cyrillic copy at ${width}px`)
    await dialog.screenshot({ path: `${evidenceDir}/settings-${width}-profiles-modal.png` })
    await dialog.getByRole('button', { name: 'Close' }).click()
    await page.getByRole('link', { name: 'Home' }).click()
  }
  console.log(JSON.stringify({ evidenceDir, widths: [1440, 834, 390], assertions: ['Resident supervisors and Provider executions labels', 'no misleading Provider contexts label', 'Home and Settings no horizontal overflow', 'English Profiles descriptions with no Cyrillic UI copy', 'Profiles grid card outside capacity details with keyboard/click modal access'] }))
} finally {
  await browser?.close()
  await new Promise(resolve => server.close(resolve))
  await vite.close()
}
