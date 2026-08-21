import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { chromium } from '@playwright/test'
import { startStaticServer } from './static-server.mjs'

const basePath = process.env.BASE_PATH ?? process.env.NEXT_PUBLIC_BASE_PATH ?? ''
const productRoot = path.resolve(process.env.THREADCELLS_PRODUCT_ROOT || path.join(process.cwd(), '..'))
const manifest = JSON.parse(await readFile(path.join(productRoot, 'docs', 'DOCS_MANIFEST.json'), 'utf8'))
const routes = ['', '/docs', ...manifest.documents.map(document => `/docs/${document.slug}`)]
const server = await startStaticServer({ basePath })
let browser

try {
  browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } })
  const page = await context.newPage()
  const checkedResources = new Set()
  const routeHtml = new Map()
  const checkedRoutes = new Set()
  let links = 0

  for (const route of routes) {
    const response = await page.goto(`${server.origin}${route}`, { waitUntil: 'domcontentloaded' })
    assert(response?.ok(), `${route || '/'} returned ${response?.status()}`)
    assert.equal(await page.getByRole('heading', { level: 1 }).count(), 1, `${route || '/'} has one h1`)
    checkedRoutes.add(new URL(page.url()).pathname)

    const anchors = await page.locator('a').evaluateAll(elements => elements.map(anchor => ({ href: anchor.getAttribute('href'), text: anchor.textContent?.trim() || '' })))
    links += anchors.length
    for (const link of anchors) {
      assert(link.href, `Link has no href on ${route || '/'}: ${link.text}`)
      assert(!['#', 'javascript:void(0)'].includes(link.href), `Fake link on ${route || '/'}: ${link.text}`)
      assert(!/TODO|example\.com/i.test(link.href), `Placeholder link on ${route || '/'}: ${link.href}`)
      const target = new URL(link.href, page.url())
      if (target.origin !== new URL(server.origin).origin) continue
      if (target.hash && target.pathname === new URL(page.url()).pathname) {
        const id = decodeURIComponent(target.hash.slice(1)).replaceAll('"', '\\"')
        assert.equal(await page.locator(`[id="${id}"]`).count(), 1, `Missing fragment target on ${route || '/'}: ${target.hash}`)
      }
      const requestUrl = new URL(target.pathname, server.origin).toString()
      if (!checkedResources.has(requestUrl)) {
        const targetResponse = await page.request.get(requestUrl)
        assert(targetResponse.ok(), `Internal link failed: ${target.pathname} (${targetResponse.status()})`)
        routeHtml.set(requestUrl, await targetResponse.text())
        checkedResources.add(requestUrl)
      }
      if (target.hash && target.pathname !== new URL(page.url()).pathname) {
        const id = decodeURIComponent(target.hash.slice(1))
        assert(routeHtml.get(requestUrl)?.includes(`id="${id}"`), `Missing cross-page fragment target: ${target.pathname}${target.hash}`)
      }
    }

    const resources = await page.locator('img, video source, link[rel="icon"], link[rel="manifest"]').evaluateAll(elements => elements.map(element => element.getAttribute('src') || element.getAttribute('href')).filter(Boolean))
    for (const resource of resources) {
      const requestUrl = new URL(resource, page.url()).toString()
      if (checkedResources.has(requestUrl)) continue
      const resourceResponse = await page.request.get(requestUrl)
      assert(resourceResponse.ok(), `Resource failed on ${route || '/'}: ${resource} (${resourceResponse.status()})`)
      checkedResources.add(requestUrl)
    }
  }

  assert.equal(routes.length, manifest.documents.length + 2, 'index and every manifest document are included')
  assert.equal(checkedRoutes.size, routes.length, 'every public route resolves to a distinct static route')
  assert(links > 150, 'the public site exposes a navigable documentation corpus')
  console.log(JSON.stringify({ routes: routes.length, documents: manifest.documents.length, links, resourcesAndInternalRoutes: checkedResources.size, basePath }))
} finally {
  await browser?.close()
  await server.close()
}
