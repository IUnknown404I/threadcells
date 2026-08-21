import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import { writeFile } from 'node:fs/promises'
import { chromium } from '@playwright/test'
import lighthouse from 'lighthouse'
import { launch } from 'chrome-launcher'
import { startStaticServer } from './static-server.mjs'

const basePath = process.env.BASE_PATH || ''
const server = await startStaticServer({ basePath })
const playwrightExecutable = chromium.executablePath()
const headlessExecutable = playwrightExecutable.replace(
  /\/chromium-(\d+)\/chrome-linux64\/chrome$/,
  '/chromium_headless_shell-$1/chrome-headless-shell-linux64/chrome-headless-shell',
)
const chromePath = existsSync(playwrightExecutable) ? playwrightExecutable : headlessExecutable
assert(existsSync(chromePath), `No Playwright Chromium executable found at ${playwrightExecutable} or ${headlessExecutable}`)
let chrome
try {
  chrome = await launch({ chromePath, chromeFlags: ['--headless', '--no-sandbox', '--disable-gpu'] })
  const result = await lighthouse(server.origin, { port: chrome.port, output: 'json', logLevel: 'error', onlyCategories: ['performance', 'accessibility', 'best-practices', 'seo'] })
  assert(result?.lhr, 'Lighthouse returned no report')
  const scores = Object.fromEntries(Object.entries(result.lhr.categories).map(([key, value]) => [key, value.score]))
  await writeFile('lighthouse-report.json', JSON.stringify(result.lhr, null, 2))
  assert((scores.performance ?? 0) >= 0.85, `Performance score ${scores.performance}`)
  assert((scores.accessibility ?? 0) >= 0.95, `Accessibility score ${scores.accessibility}`)
  assert((scores['best-practices'] ?? 0) >= 0.95, `Best practices score ${scores['best-practices']}`)
  assert((scores.seo ?? 0) >= 0.95, `SEO score ${scores.seo}`)
  console.log(JSON.stringify({ url: server.origin, basePath, scores }))
} finally {
  await chrome?.kill()
  await server.close()
}
