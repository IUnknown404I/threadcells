import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import { chromium } from '@playwright/test'
import { startStaticServer } from './static-server.mjs'

const basePath = process.env.BASE_PATH ?? process.env.NEXT_PUBLIC_BASE_PATH ?? ''
const evidenceDir = process.env.WEBSITE_LOCALE_EVIDENCE_DIR || '/tmp/threadcells-locale-evidence'
const locales = [
  { locale: 'en', prefix: '', lang: 'en' },
  { locale: 'ru', prefix: '/ru', lang: 'ru' },
  { locale: 'zh-CN', prefix: '/zh-CN', lang: 'zh-CN' },
  { locale: 'es', prefix: '/es', lang: 'es' },
  { locale: 'pt-BR', prefix: '/pt-BR', lang: 'pt-BR' },
  { locale: 'de', prefix: '/de', lang: 'de' },
  { locale: 'ja', prefix: '/ja', lang: 'ja' },
]
const viewports = [
  { name: 'mobile', width: 390, height: 844 },
  { name: 'tablet', width: 834, height: 1112 },
  { name: 'desktop', width: 1440, height: 960 },
]

await mkdir(evidenceDir, { recursive: true })
const server = await startStaticServer({ basePath })
let browser

async function assertLocalizedPage(page, route, lang, label) {
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const response = await page.goto(`${server.origin}${route}`, { waitUntil: 'networkidle' })
  assert(response?.ok(), `${label} returned ${response?.status()}`)
  assert.equal(await page.locator('html').getAttribute('lang'), lang, `${label} html lang`)
  assert.equal(await page.getByRole('heading', { level: 1 }).count(), 1, `${label} has one h1`)
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), 0, `${label} has no horizontal overflow`)
  assert.equal(await page.locator('link[rel="canonical"]').count(), 1, `${label} has one canonical`)
  assert.equal(await page.locator('link[rel="alternate"][hreflang]').count(), locales.length, `${label} has every hreflang`)
  assert.equal(await page.locator('.language-menu [role="menuitem"]').count(), locales.length, `${label} has every locale selector entry`)
  assert.deepEqual(errors, [], `${label} has no page errors`)
}

try {
  browser = await chromium.launch({ headless: true })
  let routes = 0
  for (const viewport of viewports) {
    const context = await browser.newContext({ viewport, colorScheme: 'dark' })
    const page = await context.newPage()
    for (const { locale, prefix, lang } of locales) {
      await assertLocalizedPage(page, prefix, lang, `${locale} landing ${viewport.name}`)
      assert.equal(await page.locator('main').getAttribute('lang'), lang, `${locale} landing main language`)
      assert.equal(await page.locator('.language-menu [aria-current="page"]').getAttribute('hreflang'), lang, `${locale} landing selector state`)
      const footerLogo = page.locator('.footer-brand img')
      assert((await footerLogo.getAttribute('src'))?.endsWith('/threadcells-logo-horizontal-true-black.webp'), `${locale} footer uses true-black asset`)
      assert.equal(await footerLogo.evaluate(image => image.complete && image.naturalWidth > 0), true, `${locale} footer logo loads`)
      await page.screenshot({ path: path.join(evidenceDir, `${locale}-landing-${viewport.name}.png`), fullPage: true, animations: 'disabled' })
      routes += 1

      for (const slug of ['installation', 'housekeeping']) {
        const route = `${prefix}/docs/${slug}`
        await assertLocalizedPage(page, route, lang, `${locale} ${slug} ${viewport.name}`)
        assert.equal(await page.locator('.docs-language-switch a').count(), locales.length, `${locale} ${slug} same-slug selector`)
        for (const target of locales) {
          const expected = `${basePath}${target.prefix}/docs/${slug}` || '/docs/' + slug
          const href = await page.locator(`.docs-language-switch a[hreflang="${target.lang}"]`).getAttribute('href')
          assert.equal(href?.replace(/\/$/, ''), expected.replace(/\/$/, ''), `${locale} ${slug} preserves slug for ${target.locale}`)
        }
        await page.screenshot({ path: path.join(evidenceDir, `${locale}-${slug}-${viewport.name}.png`), fullPage: true, animations: 'disabled' })
        routes += 1
      }
    }
    await context.close()
  }
  console.log(JSON.stringify({ locales: locales.length, viewports: viewports.length, routes, evidenceDir, basePath }))
} finally {
  await browser?.close()
  await server.close()
}
