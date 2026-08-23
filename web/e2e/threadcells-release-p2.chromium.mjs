import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import http from 'node:http'
import { fileURLToPath } from 'node:url'
import { createServer as createViteServer } from 'vite'
import { chromium } from 'playwright'

const webRoot = fileURLToPath(new URL('..', import.meta.url))
const evidenceDir = process.env.THREADCELLS_VISUAL_EVIDENCE_DIR || '/tmp/threadcells-visual-p2'
const branding = { title: 'ThreadCells', subtitle: 'Multi-agent control plane', logoUrl: '/threadcells-symbol.png', customLogo: false }
const homeSessions = [
  { id: 'home-newest', name: 'cao-home-newest', status: 'active', created_at: '2026-08-21T12:00:00' },
  { id: 'home-older', name: 'cao-home-older', status: 'active', created_at: '2026-08-21T11:00:00' },
]
const homeTerminals = {
  'cao-home-newest': [{ id: 'home-terminal-newest', tmux_session: 'cao-home-newest', tmux_window: '0', provider: 'codex', agent_profile: 'supervisor_terra_medium', last_active: null }],
  'cao-home-older': [{ id: 'home-terminal-older', tmux_session: 'cao-home-older', tmux_window: '0', provider: 'codex', agent_profile: 'developer', last_active: null }],
}
let projects = [{ projectId: 'project-core-immutable', name: 'CAO core', path: '/workspace/core', description: 'Browser evidence fixture', isDefault: true }]
const projectUpdates = []
const capacity = { resource_state: 'GREEN', reasons: [], resident_supervisors: { active: 0, limit: 5, available: 5, certain: true }, provider_executions: { active: 0, limit: 3, available: 3, certain: true }, work_contexts: { active: 0, limit: 2, available: 2, certain: true }, heavy_executions: { active: 0, limit: 1, available: 1, waiting: null }, memory: { available_mib: 1024, swap_total_mib: 0, swap_free_mib: 0 }, root_disk: { used_percent: 1, free_gib: 100 }, memory_pressure: { some_avg10: 0, full_avg10: 0 }, cpu_load: { one_minute: 0, cpu_count: 1 }, housekeeping: { ok: true } }

await mkdir(evidenceDir, { recursive: true })
const vite = await createViteServer({ root: webRoot, configFile: false, plugins: [(await import('@vitejs/plugin-react')).default()], define: { __THREADCELLS_REVISION__: JSON.stringify('synthetic-evidence'), __THREADCELLS_VERSION__: JSON.stringify('0.1.0-alpha.2') }, appType: 'spa', server: { middlewareMode: true, hmr: false } })
const json = (response, value) => { response.writeHead(200, { 'content-type': 'application/json' }); response.end(JSON.stringify(value)) }
const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://localhost')
  if (request.method === 'GET' && url.pathname === '/sessions') return json(response, homeSessions)
  if (request.method === 'GET' && url.pathname.startsWith('/sessions/')) {
    const name = decodeURIComponent(url.pathname.slice('/sessions/'.length))
    const session = homeSessions.find(item => item.name === name)
    if (session) return json(response, { session, terminals: homeTerminals[name] || [] })
  }
  if (request.method === 'GET' && url.pathname.startsWith('/terminals/')) {
    const id = decodeURIComponent(url.pathname.slice('/terminals/'.length))
    const terminal = Object.values(homeTerminals).flat().find(item => item.id === id)
    if (terminal) return json(response, { id, name: terminal.tmux_window, provider: terminal.provider, session_name: terminal.tmux_session, agent_profile: terminal.agent_profile, status: 'idle', execution_state: 'ready', lifecycle: 'running', workflow_state: 'active', last_active: null })
  }
  if (request.method === 'GET' && url.pathname === '/settings/branding') return json(response, branding)
  if (request.method === 'GET' && url.pathname === '/settings/agent-dirs') return json(response, { agent_dirs: {}, extra_dirs: [] })
  if (request.method === 'GET' && url.pathname === '/settings/orchestration-capacity') return json(response, capacity)
  if (request.method === 'GET' && url.pathname === '/api/v1/telegram') return json(response, { schema_version: 1, enabled: false, chat_id: '-1001234567890', message_thread_id: 77, token_configured: true, token_state: 'configured', configuration_state: 'disabled', last_result: null, last_result_at: null, updated_at: '2026-08-21T12:00:00' })
  if (request.method === 'GET' && url.pathname === '/agents/profiles') return json(response, [])
  if (request.method === 'GET' && url.pathname === '/projects') return json(response, projects)
  if (request.method === 'PUT' && url.pathname === '/projects/project-core-immutable') {
    let body = ''
    request.on('data', chunk => { body += chunk })
    request.on('end', () => {
      const update = JSON.parse(body)
      projectUpdates.push(update)
      projects = [{ ...projects[0], ...update }]
      json(response, projects[0])
    })
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
  await page.goto(`${origin}/settings/about`)
  await page.locator('#about-heading').waitFor()
  assert((await page.getByText('ThreadCells', { exact: true }).count()) >= 3, 'runtime, build identity, and immutable footer retain the product name')
  assert.equal(await page.getByText('synthetic-evidence', { exact: true }).count(), 1)
  const footer = page.getByRole('contentinfo')
  assert.equal(await footer.getByRole('img', { name: 'ThreadCells' }).getAttribute('src'), '/threadcells-logo-horizontal.png')
  assert.equal(await footer.getByText('Contributions are welcome.', { exact: true }).count(), 1)
  assert.equal(await footer.getByText('© 2026 ThreadCells', { exact: true }).count(), 1)
  assert.equal(await footer.getByRole('link', { name: 'GitHub' }).count(), 1)
  assert.equal(await footer.getByRole('link', { name: 'ThreadCells' }).count(), 1)
  assert.equal(await footer.getByRole('link', { name: 'Docs' }).getAttribute('href'), '/docs')
  assert.deepEqual((await footer.getByRole('link').allTextContents()).map(label => label.trim()), ['GitHub', 'Docs', 'ThreadCells'])
  assert.equal(await footer.getByRole('link', { name: 'GitHub' }).locator(':scope > svg').count(), 1, 'GitHub has a leading icon')
  assert.equal(await footer.getByRole('link', { name: 'Docs' }).locator(':scope > svg').count(), 1, 'Docs has a leading icon')
  assert.equal((await footer.locator('nav > span[aria-hidden="true"]').textContent()).trim(), '·', 'a centered dot divides internal and external links')
  assert.equal(await footer.getByText('Apache-2.0', { exact: true }).count(), 0, 'license stays absent without explicit footer configuration')
  await page.goto(`${origin}/settings/telegram`)
  await page.getByRole('heading', { name: 'Telegram notifications' }).waitFor()
  for (const { width, height } of [{ width: 1440, height: 834 }, { width: 834, height: 1112 }, { width: 390, height: 844 }]) {
    await page.setViewportSize({ width, height })
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    assert.equal(overflow, 0, `Telegram Settings horizontal overflow at ${width}px: ${overflow}`)
    assert.equal(await page.getByText('Configured · disabled', { exact: true }).count(), 1)
    assert.equal(await page.getByRole('button', { name: 'Send test notification' }).isDisabled(), true, 'locked operator state protects test delivery')
    await page.screenshot({ path: `${evidenceDir}/telegram-settings-${width}.png`, fullPage: true })
  }
  await page.goto(origin)
  const firstHomeSession = page.getByRole('button', { name: 'Collapse home-newest', exact: true })
  const secondHomeSession = page.getByRole('button', { name: 'Expand home-older', exact: true })
  await firstHomeSession.waitFor()
  assert.equal(await firstHomeSession.getAttribute('aria-expanded'), 'true', 'Home expands only its canonical top session initially')
  assert.equal(await secondHomeSession.getAttribute('aria-expanded'), 'false', 'Home leaves later sessions collapsed')
  assert.equal(await page.getByTestId('agent-detail-card-home-terminal-newest').count(), 1)
  assert.equal(await page.getByTestId('agent-detail-card-home-terminal-older').count(), 0)
  for (const { width, height } of [{ width: 834, height: 1112 }, { width: 390, height: 844 }]) {
    await page.setViewportSize({ width, height })
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    assert.equal(overflow, 0, `Home default-expanded session horizontal overflow at ${width}px: ${overflow}`)
  }
  await firstHomeSession.click()
  await page.waitForTimeout(3200)
  assert.equal(await page.getByRole('button', { name: 'Expand home-newest', exact: true }).getAttribute('aria-expanded'), 'false', 'status polling does not reopen an owner-collapsed Home session')
  await page.goto(`${origin}/settings`)
  await page.getByRole('button', { name: 'Edit CAO core' }).waitFor()
  assert.equal(await page.getByRole('button', { name: 'Edit CAO core' }).count(), 1)
  assert.equal(await page.getByRole('button', { name: 'Set CAO core as default' }).isDisabled(), true)
  assert.equal(await page.getByRole('button', { name: 'Remove CAO core' }).count(), 1)
  await page.getByRole('button', { name: 'Edit CAO core' }).click()
  const editDialog = page.getByRole('dialog', { name: 'Edit project' })
  await editDialog.getByLabel('Project name').fill('ThreadCells core')
  await editDialog.getByRole('button', { name: 'Save project' }).click()
  await page.waitForTimeout(500)
  assert.equal(projectUpdates.length, 1, 'project rename reaches the immutable-ID update route')
  await page.getByRole('button', { name: 'Edit ThreadCells core' }).waitFor()
  assert.deepEqual(projectUpdates, [{ name: 'ThreadCells core', path: '/workspace/core', description: 'Browser evidence fixture' }], 'rename preserves path and changes metadata through the immutable project ID route')
  assert.equal(projects[0].projectId, 'project-core-immutable')
  assert.equal(projects[0].isDefault, true)
  const widths = []
  for (const { width, height } of [{ width: 1440, height: 834 }, { width: 834, height: 1112 }, { width: 640, height: 960 }, { width: 390, height: 844 }]) {
    await page.setViewportSize({ width, height })
    const footerLayout = await footer.evaluate(element => {
      const style = getComputedStyle(element)
      const content = element.firstElementChild
      const contentStyle = getComputedStyle(content)
      const logo = element.querySelector('img').getBoundingClientRect()
      const left = content.firstElementChild.getBoundingClientRect()
      const right = content.lastElementChild.getBoundingClientRect()
      return {
        background: style.backgroundColor,
        paddingTop: parseFloat(contentStyle.paddingTop),
        paddingBottom: parseFloat(contentStyle.paddingBottom),
        logoHeight: logo.height,
        direction: contentStyle.flexDirection,
        alignment: contentStyle.alignItems,
        justification: contentStyle.justifyContent,
        contentWidth: content.getBoundingClientRect().width,
        leftEdge: left.left,
        rightEdge: right.right,
      }
    })
    const headerLayout = await page.locator('header').evaluate(element => {
      const content = element.firstElementChild
      const contentStyle = getComputedStyle(content)
      const icon = element.querySelector('img').getBoundingClientRect()
      const brand = element.querySelector('h1').parentElement.getBoundingClientRect()
      const status = content.lastElementChild.getBoundingClientRect()
      return {
        iconWidth: icon.width,
        iconHeight: icon.height,
        paddingTop: parseFloat(contentStyle.paddingTop),
        paddingBottom: parseFloat(contentStyle.paddingBottom),
        brandCenterDelta: Number(Math.abs((brand.top + brand.bottom) / 2 - (icon.top + icon.bottom) / 2).toFixed(3)),
        statusCenterDelta: Number(Math.abs((status.top + status.bottom) / 2 - (icon.top + icon.bottom) / 2).toFixed(3)),
      }
    })
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    assert.equal(overflow, 0, `Settings horizontal overflow at ${width}px: ${overflow}`)
    assert.equal(footerLayout.background, 'rgb(12, 20, 33)', `footer branding background at ${width}px`)
    assert.equal(footerLayout.paddingTop, 2, `footer top padding at ${width}px`)
    assert.equal(footerLayout.paddingBottom, 2, `footer bottom padding at ${width}px`)
    assert(footerLayout.logoHeight >= 96, `footer logo size at ${width}px`)
    assert.equal(footerLayout.alignment, 'center', `footer content alignment at ${width}px`)
    assert.equal(footerLayout.justification, 'space-between', `footer left/right group distribution at ${width}px`)
    assert.equal(headerLayout.iconWidth, 64, `header icon width at ${width}px`)
    assert.equal(headerLayout.iconHeight, 64, `header icon height at ${width}px`)
    assert.equal(headerLayout.paddingTop, 0, `header top padding at ${width}px`)
    assert.equal(headerLayout.paddingBottom, 0, `header bottom padding at ${width}px`)
    assert(headerLayout.brandCenterDelta < 1, `brand content is vertically centered at ${width}px`)
    assert(headerLayout.statusCenterDelta < 1, `status content is vertically centered at ${width}px`)
    if (width >= 768) assert(footerLayout.rightEdge - footerLayout.leftEdge > footerLayout.contentWidth * 0.8, `footer groups occupy opposite sides at ${width}px`)
    await page.screenshot({ path: `${evidenceDir}/settings-footer-${width}.png`, fullPage: true })
    widths.push({ width, height, overflow, headerLayout, footerLayout })
  }
  await page.getByRole('button', { name: 'Register New Project' }).click()
  const projectDialog = page.getByRole('dialog', { name: 'Register New Project' })
  await projectDialog.getByText('Used both as a human-readable description and as project-scoped operational guidance for agents and flows.').waitFor()
  for (const { width, height } of [{ width: 1440, height: 834 }, { width: 834, height: 1112 }, { width: 640, height: 960 }, { width: 390, height: 844 }]) {
    await page.setViewportSize({ width, height })
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    assert.equal(overflow, 0, `Project registration modal horizontal overflow at ${width}px: ${overflow}`)
    await page.screenshot({ path: `${evidenceDir}/register-project-${width}.png`, fullPage: true })
  }
  console.log(JSON.stringify({ synthetic: true, evidenceDir, widths, projectUpdates, assertions: ['ThreadCells immutable footer identity', 'Home canonical first-session initial expansion', 'Home collapse survives status polling', 'Home expanded-card responsive containment', 'Telegram Settings responsive containment and operator lock', 'header 64px icon and zero vertical padding', 'header vertical centering', 'footer exact background, spacing, and justify-between groups', 'enlarged responsive horizontal logo', 'footer product links and icons', 'identity-safe CAO core to ThreadCells core rename', 'license conditionality', 'responsive Settings and footer layout', 'responsive project registration modal'] }))
} finally {
  await browser?.close()
  await new Promise(resolve => server.close(resolve))
  await vite.close()
}
