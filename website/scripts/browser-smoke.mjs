import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { chromium } from '@playwright/test'
import { startStaticServer } from './static-server.mjs'

const basePath = process.env.BASE_PATH || ''
const evidenceDir = process.env.WEBSITE_EVIDENCE_DIR || '/tmp/threadcells-website-evidence'
await mkdir(evidenceDir, { recursive: true })
const server = await startStaticServer({ basePath })
let browser

async function assertPage(page, route, viewportName) {
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const response = await page.goto(`${server.origin}${route}`, { waitUntil: 'networkidle' })
  assert(response?.ok(), `${viewportName} ${route} returned ${response?.status()}`)
  assert.equal(await page.getByRole('heading', { level: 1 }).count(), 1, `${viewportName} ${route} has one h1`)
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), 0, `${viewportName} ${route} has no horizontal overflow`)
  assert.deepEqual(errors, [], `${viewportName} ${route} page errors`)
}

try {
  browser = await chromium.launch({ headless: true })
  const results = []
  for (const viewport of [
    { name: 'mobile', width: 390, height: 844 },
    { name: 'tablet', width: 834, height: 1112 },
    { name: 'desktop', width: 1440, height: 960 },
    { name: 'wide', width: 1728, height: 1117 },
  ]) {
    const context = await browser.newContext({ viewport, colorScheme: 'dark' })
    const page = await context.newPage()
    await assertPage(page, '', viewport.name)

    const productImage = page.locator('img[alt^="ThreadCells Home screen"]')
    assert.equal(await productImage.evaluate(image => image.complete && image.naturalWidth === 1440), true, `${viewport.name} current product image loads at its native capture width`)
    assert.equal(await page.getByRole('link', { name: 'ThreadCells on GitHub' }).getAttribute('href'), 'https://github.com/IUnknown404I/threadcells', `${viewport.name} uses the official repository`)
    const footer = page.locator('footer')
    assert.equal(await footer.getByRole('link', { name: 'ThreadCells home' }).getAttribute('href'), `${basePath}/#top`, `${viewport.name} footer brand uses the Pages-aware landing root`)
    assert.equal(await footer.getByRole('link', { name: 'Docs' }).getAttribute('href'), `${basePath}/docs`, `${viewport.name} footer Docs uses the Pages-aware public route`)
    assert.equal(await footer.getByRole('link', { name: /GitHub/ }).getAttribute('href'), 'https://github.com/IUnknown404I/threadcells', `${viewport.name} footer uses the official repository`)

    if (viewport.width > 560) {
      assert.equal(await page.locator('.desktop-brand .brand-name').textContent(), 'ThreadCells', `${viewport.name} uses the current public brand`)
      assert.equal(await page.locator('.desktop-brand').isVisible(), true, `${viewport.name} brand is visible`)
    } else {
      assert.equal(await page.locator('.mobile-brand').isVisible(), true, 'mobile keeps compact canonical branding')
      assert.equal(await page.locator('.header-github-link').isVisible(), true, 'mobile keeps GitHub in the header')
    }

    const screenshotTrigger = page.getByRole('button', { name: /Click to expand: ThreadCells Home screen/ })
    await screenshotTrigger.scrollIntoViewIfNeeded()
    await screenshotTrigger.click()
    const dialog = page.getByRole('dialog', { name: /Expanded screenshot: ThreadCells Home screen/ })
    await dialog.waitFor({ state: 'visible' })
    assert.equal(await page.evaluate(() => document.body.style.overflow), 'hidden', `${viewport.name} lightbox locks background scrolling`)
    await page.keyboard.press('Escape')
    await dialog.waitFor({ state: 'hidden' })
    await page.waitForFunction(element => document.activeElement === element, await screenshotTrigger.elementHandle())
    assert.equal(await screenshotTrigger.evaluate(element => document.activeElement === element), true, `${viewport.name} lightbox restores focus`)

    await page.evaluate(() => window.scrollTo(0, 0))
    await page.screenshot({ path: path.join(evidenceDir, `landing-${viewport.name}.png`), animations: 'disabled' })

    for (const route of ['/docs', '/docs/overview', '/docs/remote-access', '/docs/troubleshooting']) {
      await assertPage(page, route, viewport.name)
      const imageName = route === '/docs' ? 'index' : route.split('/').pop()
      await page.screenshot({ path: path.join(evidenceDir, `docs-${imageName}-${viewport.name}.png`), animations: 'disabled' })
    }
    results.push(viewport)
    await context.close()
  }

  const reduced = await browser.newContext({ viewport: { width: 1440, height: 960 }, reducedMotion: 'reduce' })
  const reducedPage = await reduced.newPage()
  await reducedPage.goto(server.origin, { waitUntil: 'networkidle' })
  const initialPhase = await reducedPage.locator('.mesh-stage').getAttribute('data-phase')
  await reducedPage.waitForTimeout(2100)
  assert.equal(await reducedPage.locator('.mesh-stage').getAttribute('data-phase'), initialPhase, 'reduced motion keeps a stable mesh frame')
  assert.equal(await reducedPage.evaluate(() => getComputedStyle(document.documentElement).scrollBehavior), 'auto', 'reduced motion disables smooth scroll')
  await reduced.close()
  console.log(JSON.stringify({ basePath, evidenceDir, results, lightbox: true, docs: true, reducedMotion: true }))
} finally {
  await browser?.close()
  await server.close()
}
