import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { chromium } from '@playwright/test'
import { startStaticServer } from './static-server.mjs'

const basePath = process.env.BASE_PATH ?? process.env.NEXT_PUBLIC_BASE_PATH ?? ''
const evidenceDir = process.env.WEBSITE_EVIDENCE_DIR || '/tmp/threadcells-website-evidence'
const verificationFile = 'googlee9d638a5abf39da9.html'
const verificationBody = `google-site-verification: ${verificationFile}`
const verificationToken = 'AWUVlfzo2OLGNrlvn0Ji59Lsn5bqA0Moh6lrtiS6zkc'
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
  browser = await chromium.launch({ headless: true, args: ['--host-resolver-rules=MAP threadcells.test 127.0.0.1'] })
  const verificationUrl = `${server.origin}/${verificationFile}`
  const verificationResponse = await fetch(verificationUrl, { redirect: 'manual' })
  assert.equal(verificationResponse.status, 200, 'Google verification file returns 200')
  assert.equal(verificationResponse.redirected, false, 'Google verification file does not redirect')
  assert.equal(verificationResponse.url, verificationUrl, 'Google verification file keeps the exact URL')
  assert.equal(await verificationResponse.text(), verificationBody, 'Google verification file has the exact issued body')

  const discoveryContext = await browser.newContext({ viewport: { width: 1440, height: 960 } })
  const discoveryPage = await discoveryContext.newPage()
  const landingResponse = await discoveryPage.goto(server.origin, { waitUntil: 'networkidle' })
  assert.equal(landingResponse?.status(), 200, 'landing page returns 200')
  assert.equal(await discoveryPage.locator(`meta[name="google-site-verification"][content="${verificationToken}"]`).count(), 1, 'landing head has the exact Google verification meta tag')
  assert.equal(await discoveryPage.locator('meta[name="robots"][content*="noindex" i]').count(), 0, 'landing page is not marked noindex')
  assert.equal(await discoveryPage.locator('link[rel="canonical"]').count(), 1, 'landing page has one canonical URL')
  const canonical = await discoveryPage.locator('link[rel="canonical"]').getAttribute('href')
  assert.equal(new URL(canonical).href, 'https://iunknown404i.github.io/threadcells/', 'landing canonical is the public Pages URL')
  const sitemapResponse = await fetch(`${server.origin}/sitemap.xml`)
  assert.equal(sitemapResponse.status, 200, 'sitemap returns 200')
  const sitemap = await sitemapResponse.text()
  const sitemapUrls = [...sitemap.matchAll(/<loc>([^<]+)<\/loc>/g)].map(match => match[1])
  assert(sitemapUrls.length > 1, 'sitemap contains public routes')
  for (const value of sitemapUrls) {
    const url = new URL(value)
    assert.equal(url.origin, 'https://iunknown404i.github.io', `sitemap URL uses public origin: ${value}`)
    assert(url.pathname.startsWith('/threadcells'), `sitemap URL uses Pages base path: ${value}`)
  }
  await discoveryContext.close()

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

    const productImage = page.locator('img[alt^="ThreadCells Home showing"]')
    assert.equal(await productImage.evaluate(image => image.complete && image.naturalWidth === 1440), true, `${viewport.name} current product image loads at its native capture width`)
    const demo = page.getByLabel('Live ThreadCells release-system tour')
    const demoState = await demo.evaluate(video => ({
      autoplay: video.autoplay,
      muted: video.muted,
      loop: video.loop,
      playsInline: video.playsInline,
      sources: Array.from(video.querySelectorAll('source')).map(source => source.getAttribute('src')),
    }))
    assert.deepEqual(
      { autoplay: demoState.autoplay, muted: demoState.muted, loop: demoState.loop, playsInline: demoState.playsInline },
      { autoplay: true, muted: true, loop: true, playsInline: true },
      `${viewport.name} live tour uses native autoplay, muted, loop, and playsinline behavior`,
    )
    assert.equal(demoState.sources.length, 2, `${viewport.name} live tour has WebM and MP4 sources`)
    assert.equal(await page.getByRole('link', { name: 'ThreadCells — GitHub' }).getAttribute('href'), 'https://github.com/IUnknown404I/threadcells', `${viewport.name} uses the official repository`)
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

    const screenshotTrigger = page.getByRole('button', { name: /Click to expand: ThreadCells Home showing/ })
    await screenshotTrigger.scrollIntoViewIfNeeded()
    await screenshotTrigger.click()
    const dialog = page.getByRole('dialog', { name: /Expanded screenshot: ThreadCells Home showing/ })
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

  const analyticsOrigin = server.origin.replace('127.0.0.1', 'threadcells.test')
  const consentContext = await browser.newContext({ viewport: { width: 1440, height: 960 } })
  const consentPage = await consentContext.newPage()
  let analyticsRequests = 0
  await consentPage.route('https://www.googletagmanager.com/gtag/js?id=G-WWBZSZ4N7T', async route => {
    analyticsRequests += 1
    await route.fulfill({ status: 200, contentType: 'application/javascript', body: '' })
  })
  await consentPage.goto(analyticsOrigin, { waitUntil: 'networkidle' })
  assert.equal(analyticsRequests, 0, 'analytics is not requested before consent')
  assert.equal(await consentPage.locator('script[data-threadcells-analytics]').count(), 0, 'no GA script exists before consent')
  await consentPage.getByRole('button', { name: 'Decline' }).click()
  await consentPage.reload({ waitUntil: 'networkidle' })
  assert.equal(analyticsRequests, 0, 'declined analytics remains unloaded after reload')
  await consentPage.getByRole('button', { name: 'Privacy & analytics settings' }).click()
  const privacyDialog = consentPage.getByRole('dialog', { name: 'Your analytics choice' })
  await privacyDialog.waitFor({ state: 'visible' })
  await privacyDialog.getByRole('button', { name: 'Allow analytics' }).click()
  await consentPage.waitForFunction(() => document.querySelectorAll('script[data-threadcells-analytics="G-WWBZSZ4N7T"]').length === 1)
  assert.equal(analyticsRequests, 1, 'allowing analytics loads the exact GA4 tag once')
  await consentPage.getByRole('button', { name: 'Privacy & analytics settings' }).click()
  await privacyDialog.getByRole('button', { name: 'Allow analytics' }).click()
  assert.equal(await consentPage.locator('script[data-threadcells-analytics="G-WWBZSZ4N7T"]').count(), 1, 'settings cannot duplicate the GA4 page view tag')
  assert.equal(await consentPage.evaluate(() => window.dataLayer?.filter(event => Array.isArray(event) && event[0] === 'config' && event[1] === 'G-WWBZSZ4N7T').length), 1, 'the local guard permits one GA4 config/page_view per page')
  assert.equal(await consentPage.evaluate(() => localStorage.getItem('threadcells.analytics-consent.v1')), 'accepted', 'accepted analytics choice persists')
  await consentPage.reload({ waitUntil: 'networkidle' })
  assert.equal(analyticsRequests, 2, 'persisted allow loads GA4 once on the next page view')
  assert.equal(await consentPage.evaluate(() => window.dataLayer?.filter(event => Array.isArray(event) && event[0] === 'config' && event[1] === 'G-WWBZSZ4N7T').length), 1, 'each page lifecycle emits exactly one GA4 config/page_view')
  await consentPage.getByRole('button', { name: 'Privacy & analytics settings' }).click()
  await privacyDialog.waitFor({ state: 'visible' })
  await privacyDialog.getByRole('button', { name: 'Decline analytics' }).click()
  await consentPage.reload({ waitUntil: 'networkidle' })
  assert.equal(analyticsRequests, 2, 'persisted decline prevents GA4 on the next page view')
  await consentContext.close()

  const localhostContext = await browser.newContext({ viewport: { width: 1440, height: 960 } })
  const localhostPage = await localhostContext.newPage()
  let localhostAnalyticsRequests = 0
  await localhostPage.route('https://www.googletagmanager.com/**', async route => {
    localhostAnalyticsRequests += 1
    await route.abort()
  })
  await localhostPage.goto(server.origin, { waitUntil: 'networkidle' })
  await localhostPage.getByRole('button', { name: 'Allow analytics' }).click()
  assert.equal(localhostAnalyticsRequests, 0, 'localhost guard prevents Google Analytics requests after allow')
  assert.equal(await localhostPage.locator('script[data-threadcells-analytics]').count(), 0, 'localhost guard prevents GA script injection')
  await localhostContext.close()
  console.log(JSON.stringify({ basePath, evidenceDir, results, lightbox: true, docs: true, reducedMotion: true, discovery: true, analyticsConsent: true, localhostGuard: true }))
} finally {
  await browser?.close()
  await server.close()
}
