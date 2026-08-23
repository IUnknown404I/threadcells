import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { copyFile, mkdir, mkdtemp, rename, rm } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const webRoot = path.join(root, 'web')
const outputRoot = path.join(root, 'launch-media', 'output')
const screenshotRoot = path.join(outputRoot, 'screenshots')
const demoRoot = path.join(outputRoot, 'demo')
const websiteScreenshotRoot = path.join(root, 'website', 'public', 'media', 'screenshots')
const runtimeScreenshotRoot = path.join(root, 'web', 'public', 'media', 'screenshots')
const liveOrigin = new URL(process.env.THREADCELLS_LIVE_URL || 'http://127.0.0.1:9889')
const allowedHosts = new Set(['127.0.0.1', 'localhost', '::1'])
assert.equal(liveOrigin.protocol, 'http:', 'Public capture must use the loopback HTTP production listener')
assert(allowedHosts.has(liveOrigin.hostname), 'Public capture refuses a non-loopback ThreadCells origin')

const webRequire = createRequire(pathToFileURL(path.join(webRoot, 'package.json')))
const websiteRequire = createRequire(pathToFileURL(path.join(root, 'website', 'package.json')))
const playwrightModule = await import(pathToFileURL(webRequire.resolve('playwright')))
const { chromium } = playwrightModule.chromium ? playwrightModule : playwrightModule.default
const sharpModule = await import(pathToFileURL(websiteRequire.resolve('sharp')))
const sharp = sharpModule.default
const productRevision = execFileSync('git', ['rev-parse', 'HEAD'], {
  cwd: root,
  encoding: 'utf8',
}).trim()
const captureSelection = new Set(
  (process.env.THREADCELLS_CAPTURE_SET || 'home,session,agents,housekeeping,telegram,capacity,demo')
    .split(',')
    .map((value) => value.trim())
    .filter(Boolean),
)

const privatePatterns = [
  /\/(?:srv|home|var\/lib)\//i,
  /\bgithub_pat_[A-Za-z0-9_]+\b/i,
  /\bgh[pousr]_[A-Za-z0-9]+\b/i,
  /\bsk-[A-Za-z0-9_-]{16,}\b/i,
  /\b\d{8,10}:[A-Za-z0-9_-]{20,}\b/,
]

async function redactPrivatePaths(page) {
  await page.evaluate(() => {
    const replacements = [
      [/\/srv\/[^\s…]+/g, '[local managed worktree]'],
      [/\/home\/[^\s…]+/g, '[local path hidden]'],
      [/\/var\/lib\/[^\s…]+/g, '[local runtime path]'],
    ]
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
    const nodes = []
    while (walker.nextNode()) nodes.push(walker.currentNode)
    for (const node of nodes) {
      let value = node.nodeValue || ''
      for (const [pattern, replacement] of replacements) value = value.replace(pattern, replacement)
      node.nodeValue = value
    }
    for (const element of document.querySelectorAll('[title], [aria-label], [value]')) {
      for (const attribute of ['title', 'aria-label', 'value']) {
        if (!element.hasAttribute(attribute)) continue
        let value = element.getAttribute(attribute) || ''
        for (const [pattern, replacement] of replacements) value = value.replace(pattern, replacement)
        element.setAttribute(attribute, value)
      }
    }
  })
}

async function redactTelegramDestination(page) {
  await page.evaluate(() => {
    const labels = [
      ['Telegram chat ID', 'Destination redacted in public capture'],
      ['Telegram topic ID', 'Topic redacted in public capture'],
      ['Telegram bot token', 'Credential never exposed'],
    ]
    for (const [label, replacement] of labels) {
      const input = document.querySelector('input[aria-label="' + label + '"]')
      if (!input) continue
      const redaction = document.createElement('div')
      redaction.className = input.className
      redaction.setAttribute('data-public-redaction', label)
      redaction.textContent = replacement
      redaction.style.display = 'flex'
      redaction.style.alignItems = 'center'
      redaction.style.color = '#9ca3af'
      input.replaceWith(redaction)
    }
  })
}

async function assertPublicDom(page, surface) {
  const html = await page.locator('body').evaluate((element) => element.innerHTML)
  const finding = privatePatterns.find((pattern) => pattern.test(html))
  assert.equal(finding, undefined, surface + ' contains a private path or credential-shaped value')
  const inbox = await page.getByText(/OWNER UPDATE|CAO workflow input|BREAK-GLASS RECOVERY INPUT/).count()
  assert.equal(inbox, 0, surface + ' contains private workflow or Inbox copy')
}

async function writeScreenshot(page, name) {
  await redactPrivatePaths(page)
  await assertPublicDom(page, name)
  const png = path.join(screenshotRoot, name + '.png')
  const webp = path.join(websiteScreenshotRoot, name + '.webp')
  const runtimeWebp = path.join(runtimeScreenshotRoot, name + '.webp')
  await page.screenshot({ path: png, fullPage: false, animations: 'disabled' })
  await sharp(png).webp({ quality: 84, effort: 6 }).toFile(webp)
  await copyFile(webp, runtimeWebp)
  return { png, webp, runtimeWebp }
}

async function captureScreenshots(browser) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 960 },
    deviceScaleFactor: 1,
    reducedMotion: 'reduce',
    colorScheme: 'dark',
  })
  const page = await context.newPage()
  const pageErrors = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  const captured = []

  if (captureSelection.has('home') || captureSelection.has('session')) {
    await page.goto(liveOrigin.href, { waitUntil: 'networkidle' })
    await page.getByText('Sessions', { exact: true }).first().waitFor()
    if (captureSelection.has('home')) {
      await writeScreenshot(page, 'threadcells-home')
      captured.push('threadcells-home')
    }
    if (captureSelection.has('session')) {
      const firstSession = page.locator('[data-testid^="session-title-row-"]').first()
      await firstSession.click()
      await page.locator('[data-testid^="agent-detail-card-"]').first().waitFor()
      await writeScreenshot(page, 'threadcells-session-workflow')
      captured.push('threadcells-session-workflow')
    }
  }

  if (captureSelection.has('agents')) {
    await page.goto(new URL('/?tab=agents&agentView=statuses', liveOrigin).href, { waitUntil: 'networkidle' })
    await page.getByRole('heading', { name: /Matching agents/ }).waitFor()
    await writeScreenshot(page, 'threadcells-agents')
    captured.push('threadcells-agents')
  }

  if (captureSelection.has('housekeeping')) {
    await page.goto(new URL('/settings/housekeeping', liveOrigin).href, { waitUntil: 'networkidle' })
    await page.getByRole('heading', { name: 'Housekeeping', exact: true }).waitFor()
    await writeScreenshot(page, 'threadcells-housekeeping')
    captured.push('threadcells-housekeeping')
  }

  if (captureSelection.has('telegram')) {
    await page.goto(new URL('/settings/telegram', liveOrigin).href, { waitUntil: 'networkidle' })
    await page.getByRole('heading', { name: 'Telegram notifications', exact: true }).waitFor()
    await redactTelegramDestination(page)
    await writeScreenshot(page, 'threadcells-telegram')
    captured.push('threadcells-telegram')
  }

  if (captureSelection.has('capacity')) {
    await page.goto(new URL('/settings', liveOrigin).href, { waitUntil: 'networkidle' })
    await page.getByText('Orchestration Capacity', { exact: true }).waitFor()
    await writeScreenshot(page, 'threadcells-capacity')
    captured.push('threadcells-capacity')
  }

  assert.deepEqual(pageErrors, [], 'Product UI page errors: ' + pageErrors.join('; '))
  await context.close()
  return captured
}

async function captureDemo(browser) {
  const rawVideoDir = await mkdtemp(path.join(demoRoot, '.raw-'))
  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 },
    recordVideo: { dir: rawVideoDir, size: { width: 1280, height: 720 } },
    colorScheme: 'dark',
    reducedMotion: 'reduce',
  })
  try {
    const page = await context.newPage()
    const pageErrors = []
    page.on('pageerror', (error) => pageErrors.push(error.message))
    const video = page.video()

    await page.goto(liveOrigin.href, { waitUntil: 'networkidle' })
    await page.getByText('Sessions', { exact: true }).first().waitFor()
    await redactPrivatePaths(page)
    await assertPublicDom(page, 'demo home')
    await page.waitForTimeout(2200)

    await page.locator('[data-testid^="session-title-row-"]').first().click()
    await page.locator('[data-testid^="agent-detail-card-"]').first().waitFor()
    await redactPrivatePaths(page)
    await assertPublicDom(page, 'demo workflow')
    await page.waitForTimeout(2600)

    await page.goto(new URL('/settings/housekeeping', liveOrigin).href, { waitUntil: 'networkidle' })
    await page.getByRole('heading', { name: 'Housekeeping', exact: true }).waitFor()
    await redactPrivatePaths(page)
    await assertPublicDom(page, 'demo housekeeping')
    await page.waitForTimeout(2600)

    await page.goto(new URL('/settings', liveOrigin).href, { waitUntil: 'networkidle' })
    await page.getByText('Orchestration Capacity', { exact: true }).waitFor()
    await redactPrivatePaths(page)
    await assertPublicDom(page, 'demo capacity')
    await page.waitForTimeout(2600)

    assert.deepEqual(pageErrors, [], 'Product demo page errors: ' + pageErrors.join('; '))
    await context.close()
    const rawPath = await video.path()
    const outputPath = path.join(demoRoot, 'threadcells-demo.webm')
    await rename(rawPath, outputPath)
    return outputPath
  } finally {
    await context.close().catch(() => {})
    await rm(rawVideoDir, { recursive: true, force: true })
  }
}

await Promise.all([
  mkdir(screenshotRoot, { recursive: true }),
  mkdir(demoRoot, { recursive: true }),
  mkdir(websiteScreenshotRoot, { recursive: true }),
  mkdir(runtimeScreenshotRoot, { recursive: true }),
])

let browser
try {
  browser = await chromium.launch({ headless: true })
  const screenshots = await captureScreenshots(browser)
  const demo = captureSelection.has('demo') ? await captureDemo(browser) : null
  console.log(JSON.stringify({
    source: 'live-loopback-production',
    productRevision,
    liveOrigin: liveOrigin.origin,
    screenshots,
    demo,
  }))
} finally {
  await browser?.close()
}
