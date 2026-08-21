import { chromium } from '@playwright/test'
import { startStaticServer } from './static-server.mjs'

const server = await startStaticServer()
let browser
try {
  browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ viewport: { width: 1200, height: 630 }, deviceScaleFactor: 1, reducedMotion: 'reduce', colorScheme: 'dark' })
  const page = await context.newPage()
  await page.goto(server.origin, { waitUntil: 'networkidle' })
  await page.screenshot({ path: 'public/media/threadcells-social.png', clip: { x: 0, y: 0, width: 1200, height: 630 }, animations: 'disabled' })
  await context.close()
  console.log(JSON.stringify({ output: 'public/media/threadcells-social.png', width: 1200, height: 630 }))
} finally {
  await browser?.close()
  await server.close()
}
