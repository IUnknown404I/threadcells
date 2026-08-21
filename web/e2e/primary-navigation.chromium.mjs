import assert from 'node:assert/strict'
import http from 'node:http'
import { fileURLToPath } from 'node:url'
import { createServer as createViteServer } from 'vite'
import { chromium } from 'playwright'

const webRoot = fileURLToPath(new URL('..', import.meta.url))
const branding = { title: 'ThreadCells', subtitle: 'Multi-agent control plane', logoUrl: '/threadcells-symbol.png', customLogo: false }
const sections = [
  ['Home', '/'], ['Agents', '/?tab=agents'], ['Flows', '/?tab=flows'],
  ['Statistics', '/?tab=statistics'], ['Docs', '/docs'], ['Settings', '/settings'],
]
const viewportWidths = [1440, 834, 640, 390]
const thresholdWidths = [968, 967]

const vite = await createViteServer({ root: webRoot, configFile: false, plugins: [(await import('@vitejs/plugin-react')).default()], define: { __THREADCELLS_REVISION__: JSON.stringify('navigation-evidence'), __THREADCELLS_VERSION__: JSON.stringify('0.1.0-alpha.1') }, appType: 'spa', server: { middlewareMode: true, hmr: false } })
const json = (response, value) => { response.writeHead(200, { 'content-type': 'application/json' }); response.end(JSON.stringify(value)) }
const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://localhost')
  if (request.method === 'GET' && url.pathname === '/sessions') return json(response, [])
  if (request.method === 'GET' && url.pathname === '/settings/branding') return json(response, branding)
  vite.middlewares(request, response)
})

const measureRail = rail => rail.evaluate(element => {
  const links = [...element.querySelectorAll('a')].map(node => node.getBoundingClientRect())
  const railRect = element.getBoundingClientRect()
  const occupiedWidth = links.at(-1).right - links[0].left
  return {
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
    scrollLeft: element.scrollLeft,
    maxScrollLeft: element.scrollWidth - element.clientWidth,
    itemWidths: links.map(rect => Number(rect.width.toFixed(3))),
    occupiedWidth: Number(occupiedWidth.toFixed(3)),
    unusedWidth: Number(Math.max(0, railRect.width - occupiedWidth).toFixed(3)),
    overflow: element.scrollWidth > element.clientWidth,
    wrapped: element.scrollHeight > element.clientHeight,
    activeVisible: (() => { const node = element.querySelector('[aria-current="page"]'); const rect = node?.getBoundingClientRect(); return Boolean(rect && rect.left >= railRect.left - 1 && rect.right <= railRect.right + 1) })(),
    pageOverflow: document.documentElement.scrollWidth - window.innerWidth,
    scrollbarHeight: parseFloat(getComputedStyle(element, '::-webkit-scrollbar').height),
  }
})

const measureArrowSlots = async navigation => {
  const previous = navigation.getByRole('button', { name: 'Show previous application sections' })
  const next = navigation.getByRole('button', { name: 'Show next application sections' })
  const dimensions = async button => button.evaluate(node => {
    const slot = node.parentElement.getBoundingClientRect()
    return { width: slot.width, height: slot.height, disabled: node.disabled, tabIndex: node.tabIndex }
  })
  return { previous: await dimensions(previous), next: await dimensions(next) }
}

await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const address = server.address()
assert(address && typeof address !== 'string')
const origin = `http://127.0.0.1:${address.port}`
let browser
try {
  browser = await chromium.launch({ headless: true })
  const page = await browser.newPage()
  const evidence = []
  for (const width of viewportWidths) {
    await page.setViewportSize({ width, height: 844 })
    await page.goto(origin)
    const navigation = page.getByRole('navigation', { name: 'Application sections' })
    const rail = navigation.getByTestId('primary-navigation-rail')
    await navigation.getByRole('link', { name: 'Home' }).waitFor()
    assert.equal(await page.locator('[role="tab"], [role="tablist"]').count(), 0, 'primary navigation must not use tab ARIA')
    assert.equal(await navigation.getByRole('link').count(), sections.length)
    assert.deepEqual((await navigation.getByRole('link').allTextContents()).map(label => label.trim()), sections.map(([name]) => name), 'navigation link order')
    for (const [name, href] of sections) assert.equal(await navigation.getByRole('link', { name }).getAttribute('href'), href, `${name} route target`)

    const initial = await measureRail(rail)
    assert.equal(initial.wrapped, false, `navigation items stay on one line at ${width}px`)
    assert.equal(initial.activeVisible, true, `initial active route is visible at ${width}px`)
    assert.equal(Math.max(...initial.itemWidths) - Math.min(...initial.itemWidths) < 0.25, true, `navigation columns are equal at ${width}px`)
    assert.equal(initial.pageOverflow, 0, `page horizontal overflow at ${width}px`)
    assert.equal(initial.scrollbarHeight, 4, `navigation scrollbar stays slim at ${width}px`)
    assert.equal(initial.scrollLeft, 0, `overflow rail starts aligned at ${width}px`)

    let interactions = null
    if (initial.overflow) {
      assert(initial.itemWidths.every(itemWidth => itemWidth >= 148 && itemWidth <= 152), `overflow items stay 148–152px at ${width}px`)
      const next = navigation.getByRole('button', { name: 'Show next application sections' })
      const previous = navigation.getByRole('button', { name: 'Show previous application sections' })
      assert.equal(await next.getAttribute('aria-controls'), 'primary-navigation-rail', `next arrow controls the rail at ${width}px`)
      const startSlots = await measureArrowSlots(navigation)
      assert.deepEqual(startSlots.previous, { width: 28, height: 28, disabled: true, tabIndex: -1 }, `left edge previous slot at ${width}px`)
      assert.deepEqual(startSlots.next, { width: 28, height: 28, disabled: false, tabIndex: 0 }, `left edge next slot at ${width}px`)
      await next.focus()
      await page.keyboard.press('Enter')
      await page.waitForFunction(node => node.scrollLeft > 0, await rail.elementHandle())
      const postArrow = await measureRail(rail)

      for (let attempt = 0; attempt < 8 && !await next.isDisabled(); attempt += 1) {
        await next.click()
        await page.waitForTimeout(600)
      }
      const repeatedArrow = await measureRail(rail)
      assert(repeatedArrow.scrollLeft >= repeatedArrow.maxScrollLeft - 2, `repeated arrow scrolling reaches the end at ${width}px: ${JSON.stringify(repeatedArrow)}`)
      const endSlots = await measureArrowSlots(navigation)
      assert.deepEqual(endSlots.previous, { width: 28, height: 28, disabled: false, tabIndex: 0 }, `right edge previous slot at ${width}px`)
      assert.deepEqual(endSlots.next, { width: 28, height: 28, disabled: true, tabIndex: -1 }, `right edge next slot at ${width}px`)

      await rail.evaluate(node => { node.scrollLeft = Math.floor((node.scrollWidth - node.clientWidth) / 2); node.dispatchEvent(new Event('scroll')) })
      await page.waitForFunction(node => node.scrollLeft > 1 && node.scrollLeft < node.scrollWidth - node.clientWidth - 1, await rail.elementHandle())
      const directScroll = await measureRail(rail)
      assert.equal(await navigation.getByRole('button').count(), 2, `both controls show after direct middle scroll at ${width}px`)
      const middleSlots = await measureArrowSlots(navigation)
      assert.deepEqual(middleSlots.previous, { width: 28, height: 28, disabled: false, tabIndex: 0 }, `middle previous slot at ${width}px`)
      assert.deepEqual(middleSlots.next, { width: 28, height: 28, disabled: false, tabIndex: 0 }, `middle next slot at ${width}px`)

      await rail.evaluate(node => { node.scrollLeft = 0; node.dispatchEvent(new Event('scroll')) })
      await rail.hover()
      await page.mouse.wheel(120, 0)
      await page.waitForFunction(node => node.scrollLeft > 0, await rail.elementHandle())
      const nativeWheel = await measureRail(rail)

      await navigation.getByRole('link', { name: 'Settings' }).click()
      await page.waitForFunction(node => node.querySelector('[aria-current="page"]')?.textContent?.includes('Settings'), await rail.elementHandle())
      await page.waitForFunction(node => { const active = node.querySelector('[aria-current="page"]')?.getBoundingClientRect(); const rect = node.getBoundingClientRect(); return active && active.left >= rect.left - 1 && active.right <= rect.right + 1 }, await rail.elementHandle())
      const activeReveal = await measureRail(rail)
      interactions = { startSlots, postArrow, repeatedArrow, endSlots, directScroll, middleSlots, nativeWheel, activeReveal }
    } else {
      assert.equal(initial.scrollWidth, initial.clientWidth, `fitted rail occupies its complete width at ${width}px`)
      assert(initial.unusedWidth < 1, `fitted columns leave no unused rail space at ${width}px`)
      assert.equal(await navigation.getByRole('button').count(), 0, `controls stay hidden without overflow at ${width}px`)
    }
    evidence.push({ width, initial, interactions })
  }

  const threshold = []
  for (const width of thresholdWidths) {
    await page.setViewportSize({ width, height: 844 })
    await page.goto(origin)
    const rail = page.getByTestId('primary-navigation-rail')
    await rail.waitFor()
    const measured = await measureRail(rail)
    threshold.push({ width, ...measured })
  }
  assert.equal(threshold[0].overflow, false, '968px is the exact fitted threshold')
  assert.equal(threshold[0].clientWidth, 920, 'threshold client width matches six 150px columns plus five 4px gaps')
  assert.equal(threshold[0].scrollWidth, 920, 'threshold scroll width fits exactly')
  assert.equal(threshold[1].overflow, true, '967px crosses into geometric overflow')
  assert.equal(threshold[1].scrollWidth, 920, 'overflow content width remains stable')

  console.log(JSON.stringify({ assertions: ['route links and canonical order', 'no tab ARIA', 'full-width six-column fit', '148–152px overflow items', 'geometric overflow threshold', 'fixed 28px left/middle/right arrow slots', 'disabled arrows are unfocusable', 'start alignment', 'arrow and repeated-arrow scrolling', 'direct-scroll control state', 'native horizontal wheel', 'active reveal', '4px scrollbar', 'no page overflow'], evidence, threshold }))
} finally {
  await browser?.close()
  await new Promise(resolve => server.close(resolve))
  await vite.close()
}
