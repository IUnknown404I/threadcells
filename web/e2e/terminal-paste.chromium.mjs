import assert from 'node:assert/strict'
import http from 'node:http'
import { fileURLToPath } from 'node:url'
import { createServer as createViteServer } from 'vite'
import { WebSocketServer } from 'ws'
import { chromium } from 'playwright'

const webRoot = fileURLToPath(new URL('..', import.meta.url))
const terminalId = 'e2e-terminal'
const sessionId = 'cao-e2e-terminal-paste'
const receivedFrames = []
const uploads = []
const workflowInputs = []
const outputFixture = Array.from(
  { length: 180 },
  (_, index) => `line-${String(index).padStart(3, '0')} ${'x'.repeat(480)}`,
).join('\n')
let resolveWorkflowInput
const workflowInputDelivered = new Promise(resolve => { resolveWorkflowInput = resolve })
let terminalSocket
const wsServer = new WebSocketServer({ noServer: true })
const vite = await createViteServer({
  root: webRoot,
  configFile: false,
  plugins: [(await import('@vitejs/plugin-react')).default()],
  define: {
    __THREADCELLS_REVISION__: JSON.stringify('terminal-paste-evidence'),
    __THREADCELLS_VERSION__: JSON.stringify('0.1.0-alpha.2'),
  },
  appType: 'spa',
  server: { middlewareMode: true, hmr: false },
})

function json(response, value) {
  response.writeHead(200, { 'content-type': 'application/json' })
  response.end(JSON.stringify(value))
}

const server = http.createServer((request, response) => {
  const url = new URL(request.url, 'http://localhost')
  if (request.method === 'POST' && [
    `/terminals/${terminalId}/attachments/image`,
    `/terminals/${terminalId}/attachments/file`,
  ].includes(url.pathname)) {
    const chunks = []
    request.on('data', chunk => chunks.push(chunk))
    request.on('end', () => {
      uploads.push({
        body: Buffer.concat(chunks),
        filename: request.headers['x-terminal-filename'],
        route: url.pathname,
      })
      response.writeHead(201, { 'content-type': 'application/json' })
      const extension = url.pathname.endsWith('/file')
        ? (request.headers['x-terminal-filename']?.endsWith('.zip') ? '.zip' : '.md')
        : '.png'
      response.end(JSON.stringify({ path: `/runtime/terminal-attachments/${terminalId}/attachment-${uploads.length}${extension}` }))
    })
    return
  }
  if (request.method === 'POST' && url.pathname === `/terminals/${terminalId}/workflow-input`) {
    const chunks = []
    request.on('data', chunk => chunks.push(chunk))
    request.on('end', () => {
      workflowInputs.push({ url: request.url, body: JSON.parse(Buffer.concat(chunks).toString()) })
      resolveWorkflowInput()
      json(response, { success: true, accepted: true, duplicate: false, turn_id: 73, queued: false, status: 'provider_admitted', reason_code: null })
    })
    return
  }
  if (request.method === 'GET' && url.pathname === '/sessions') {
    return json(response, [{ id: sessionId, name: sessionId, status: 'active', created_at: '1' }])
  }
  if (request.method === 'GET' && url.pathname === `/sessions/${sessionId}`) {
    return json(response, {
      session: { id: sessionId, name: sessionId, status: 'active', created_at: '1' },
      terminals: [{ id: terminalId, tmux_session: sessionId, tmux_window: '0', provider: 'codex', agent_profile: 'developer', last_active: null }],
    })
  }
  if (request.method === 'GET' && url.pathname === '/agents/providers') return json(response, [])
  if (request.method === 'GET' && url.pathname === '/agents/profiles') return json(response, [])
  if (request.method === 'GET' && url.pathname === `/terminals/${terminalId}`) {
    return json(response, { id: terminalId, name: terminalId, provider: 'codex', session_name: sessionId, agent_profile: 'developer', status: 'idle', last_active: null })
  }
  if (request.method === 'GET' && url.pathname === `/terminals/${terminalId}/working-directory`) {
    return json(response, { working_directory: null })
  }
  if (request.method === 'GET' && url.pathname === `/terminals/${terminalId}/output`) {
    return json(response, { output: outputFixture, mode: url.searchParams.get('mode') })
  }
  vite.middlewares(request, response)
})

server.on('upgrade', (request, socket, head) => {
  if (request.url !== `/terminals/${terminalId}/ws`) return socket.destroy()
  wsServer.handleUpgrade(request, socket, head, socket => wsServer.emit('connection', socket, request))
})
wsServer.on('connection', socket => {
  terminalSocket = socket
  socket.on('message', frame => receivedFrames.push(JSON.parse(frame.toString())))
})

await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
const address = server.address()
assert(address && typeof address !== 'string')
const origin = `http://127.0.0.1:${address.port}`

let browser
try {
  browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin })
  const page = await context.newPage()
  page.on('pageerror', error => console.error(`pageerror=${error.message}`))
  page.on('console', message => {
    if (message.type() === 'error') console.error(`browser-console=${message.text()}`)
  })
  await page.addInitScript(() => {
    const original = WebSocket.prototype.send
    WebSocket.prototype.send = function (data) {
      ;(window.__terminalPasteE2EFrames ??= []).push(String(data))
      return original.call(this, data)
    }
    window.__terminalPasteE2EPastes = []
    window.addEventListener('paste', event => {
      const textarea = document.querySelector('.xterm textarea')
      const evidence = {
        activeTextarea: textarea === document.activeElement,
        targetInTerminal: document.querySelector('[role=application]')?.contains(event.target),
        types: Array.from(event.clipboardData?.types ?? []),
        defaultPrevented: false,
      }
      window.__terminalPasteE2EPastes.push(evidence)
      setTimeout(() => { evidence.defaultPrevented = event.defaultPrevented })
    }, true)
  })

  // Exercise the normal CAO Web UI navigation, not a standalone TerminalView.
  await page.goto(origin)
  await page.getByRole('link', { name: 'Agents' }).click()
  await page.getByText('e2e-terminal-paste', { exact: true }).click()
  await page.getByRole('button', { name: 'Open Terminal' }).click()
  const composer = page.getByRole('textbox', { name: 'Workflow Composer' })
  await composer.waitFor({ state: 'visible' })

  // Terminal Output keeps the normal readable cap, then lets both output
  // modes consume the full responsive container in fullscreen. Long commands,
  // paths, URLs, and hashes wrap without creating a horizontal scrollbar.
  await page.getByRole('button', { name: 'Open Output' }).click()
  const outputDialog = page.getByRole('dialog', { name: 'Terminal output' })
  const outputSurface = page.getByTestId('terminal-output-surface')
  await outputSurface.waitFor({ state: 'visible' })
  const outputGeometry = async () => page.evaluate(() => {
    const dialog = document.querySelector('[role="dialog"][aria-label="Terminal output"]')
    const surface = document.querySelector('[data-testid="terminal-output-surface"]')
    if (!(dialog instanceof HTMLElement) || !(surface instanceof HTMLElement)) throw new Error('output geometry unavailable')
    const dialogRect = dialog.getBoundingClientRect()
    const surfaceRect = surface.getBoundingClientRect()
    return {
      dialogWidth: dialogRect.width,
      surfaceWidth: surfaceRect.width,
      clientWidth: surface.clientWidth,
      scrollWidth: surface.scrollWidth,
      clientHeight: surface.clientHeight,
      scrollHeight: surface.scrollHeight,
      overflowX: getComputedStyle(surface).overflowX,
      overflowY: getComputedStyle(surface).overflowY,
      whiteSpace: getComputedStyle(surface).whiteSpace,
      overflowWrap: getComputedStyle(surface).overflowWrap,
      pageOverflow: document.documentElement.scrollWidth - window.innerWidth,
    }
  })
  const normalLast = await outputGeometry()
  assert(normalLast.dialogWidth <= 801 && normalLast.dialogWidth >= 760)
  assert(normalLast.surfaceWidth >= normalLast.dialogWidth - 40)
  assert(normalLast.scrollWidth <= normalLast.clientWidth + 1, 'long terminal lines must wrap without horizontal scrolling')
  assert(normalLast.scrollHeight > normalLast.clientHeight, 'long output should scroll vertically')
  assert.equal(normalLast.overflowX, 'hidden')
  assert.equal(normalLast.overflowY, 'auto')
  assert.equal(normalLast.whiteSpace, 'pre-wrap')
  assert.equal(normalLast.overflowWrap, 'anywhere')

  await outputDialog.getByRole('button', { name: 'Full Output' }).click()
  await page.waitForFunction(() => document.querySelector('[data-testid="terminal-output-surface"]')?.textContent?.includes('line-179'))
  const normalFull = await outputGeometry()
  assert(normalFull.surfaceWidth >= normalFull.dialogWidth - 40)
  assert(normalFull.scrollWidth <= normalFull.clientWidth + 1)

  await outputDialog.getByRole('button', { name: 'Fullscreen' }).click()
  const fullscreen1440 = await outputGeometry()
  assert(fullscreen1440.dialogWidth >= 1439)
  assert(fullscreen1440.surfaceWidth >= fullscreen1440.dialogWidth - 40)
  assert(fullscreen1440.scrollWidth <= fullscreen1440.clientWidth + 1)

  await page.setViewportSize({ width: 1920, height: 1080 })
  const fullscreenWide = await outputGeometry()
  assert(fullscreenWide.surfaceWidth >= fullscreenWide.dialogWidth - 40)
  assert(fullscreenWide.surfaceWidth > fullscreen1440.surfaceWidth + 400)
  assert(fullscreenWide.scrollWidth <= fullscreenWide.clientWidth + 1)

  await page.setViewportSize({ width: 1024, height: 768 })
  const fullscreenNarrowed = await outputGeometry()
  assert(fullscreenNarrowed.surfaceWidth >= fullscreenNarrowed.dialogWidth - 40)
  assert(fullscreenNarrowed.surfaceWidth < fullscreenWide.surfaceWidth)
  assert.equal(fullscreenNarrowed.pageOverflow, 0)
  assert(fullscreenNarrowed.scrollWidth <= fullscreenNarrowed.clientWidth + 1)
  assert(fullscreenNarrowed.scrollHeight > fullscreenNarrowed.clientHeight)
  await outputDialog.getByRole('button', { name: 'Exit fullscreen' }).click()
  await outputDialog.getByTitle('Close').click()
  await page.setViewportSize({ width: 1440, height: 900 })

  await composer.fill('composer line one\ncomposer line two')
  await page.locator('input[type=file]').setInputFiles({
    name: 'кириллица.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from('# composer attachment\n'),
  })
  await page.getByRole('list', { name: 'Workflow attachments' }).getByText('кириллица.md').waitFor({ state: 'visible' })
  await page.getByRole('button', { name: 'Send task' }).click()
  await workflowInputDelivered
  assert.equal(workflowInputs.length, 1, 'one Composer submission must deliver exactly once')
  assert.equal(
    workflowInputs[0].body.message,
    'composer line one\ncomposer line two\n\nAttached terminal paths:\n- /runtime/terminal-attachments/e2e-terminal/attachment-1.md',
  )
  assert.equal(new URL(workflowInputs[0].url, origin).search, '', 'Composer message must not be serialized into the URI')
  assert.match(workflowInputs[0].body.request_id, /^[0-9a-f-]{36}$/i, 'Composer submission must carry a stable request identity')
  await page.getByRole('button', { name: 'Send task' }).waitFor({ state: 'visible' })
  const composerSurface = page.getByTestId('workflow-composer-surface')
  const composerBox = await composerSurface.boundingBox()
  assert(composerBox, 'Composer surface must have a rendered client rectangle')
  const composerDrop = await page.evaluate(({ x, y }) => {
    const transfer = new DataTransfer()
    transfer.items.add(new File(['opaque archive bytes'], 'bundle.zip', { type: 'application/x-zip-compressed' }))
    const event = new DragEvent('drop', { bubbles: true, cancelable: true, clientX: x, clientY: y, dataTransfer: transfer })
    const dispatched = document.querySelector('[data-testid=workflow-composer-surface]').dispatchEvent(event)
    return { dispatched, defaultPrevented: event.defaultPrevented }
  }, { x: composerBox.x + composerBox.width / 2, y: composerBox.y + composerBox.height / 2 })
  assert.equal(composerDrop.dispatched, false)
  assert.equal(composerDrop.defaultPrevented, true)
  await page.getByRole('list', { name: 'Workflow attachments' }).getByText('bundle.zip').waitFor({ state: 'visible' })
  await page.getByRole('button', { name: /Raw Terminal/i }).click()
  const textarea = page.locator('.xterm textarea')
  await textarea.waitFor({ state: 'attached' })
  await textarea.evaluate(element => element.focus())
  assert.equal(await textarea.evaluate(element => element === document.activeElement), true)
  await page.waitForFunction(() => window.__terminalPasteE2EFrames?.some(frame => frame.includes('"resize"')))

  async function pasteText(text, shortcut = 'Control+V') {
    // Clipboard setup is only test-fixture preparation. Production code reads
    // the synchronous ClipboardEvent payload, which this real Ctrl+V creates.
    await page.evaluate(value => navigator.clipboard.writeText(value), text)
    await page.waitForTimeout(50)
    const priorPastes = await page.evaluate(() => window.__terminalPasteE2EPastes.length)
    await page.keyboard.press(shortcut)
    await page.waitForFunction(expected => window.__terminalPasteE2EFrames?.includes(expected), JSON.stringify({ type: 'input', data: text }))
    await page.waitForFunction(index => window.__terminalPasteE2EPastes[index]?.defaultPrevented === true, priorPastes)
    const evidence = await page.evaluate(index => window.__terminalPasteE2EPastes[index], priorPastes)
    assert.equal(evidence.activeTextarea, true)
    assert.equal(evidence.targetInTerminal, true)
    assert(evidence.types.includes('text/plain'))
  }

  await pasteText('plain text')
  await pasteText('line one\nline two', 'Control+Shift+V')

  // Set DECSET 2004 through the real terminal output path. The native
  // context-menu payload must retain LF while xterm's documented input API
  // emits the canonical bracketed-paste delimiters.
  assert(terminalSocket, 'terminal websocket must be connected')
  terminalSocket.send(Buffer.from('\x1b[?2004h'))
  await page.waitForTimeout(50)

  // Browser context menus cannot be opened headlessly without leaving the
  // page, so exercise the same trusted-paste capture path from the terminal
  // textarea with a DOM ClipboardEvent payload.
  const contextPaste = await textarea.evaluate(element => {
    const transfer = new DataTransfer()
    transfer.setData('text/plain', 'context line one\ncontext line two')
    const event = new ClipboardEvent('paste', { bubbles: true, cancelable: true, clipboardData: transfer })
    const dispatched = element.dispatchEvent(event)
    return { dispatched, defaultPrevented: event.defaultPrevented }
  })
  assert.equal(contextPaste.dispatched, false)
  assert.equal(contextPaste.defaultPrevented, true)
  const bracketedContextText = '\x1b[200~context line one\ncontext line two\x1b[201~'
  await page.waitForFunction(expected => window.__terminalPasteE2EFrames?.includes(expected), JSON.stringify({ type: 'input', data: bracketedContextText }))

  await page.keyboard.press('a')
  await page.keyboard.type('/model')
  await page.keyboard.press('Enter')
  await page.keyboard.press('Control+C')
  await page.keyboard.press('Control+X')
  await page.waitForFunction(() => {
    const inputs = window.__terminalPasteE2EFrames?.filter(frame => frame.includes('"type":"input"')) ?? []
    return inputs.some(frame => frame.includes('"data":"a"'))
      && inputs.some(frame => frame.includes('\\u0003'))
      && inputs.some(frame => frame.includes('\\u0018'))
  })

  let imageClipboard = 'native'
  try {
    await page.evaluate(async () => {
      if (!('ClipboardItem' in window)) throw new Error('ClipboardItem unavailable')
      const bytes = Uint8Array.from(
        atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9J1v0AAAAASUVORK5CYII='),
        byte => byte.charCodeAt(0),
      )
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': new Blob([bytes], { type: 'image/png' }) })])
    })
    await page.keyboard.press('Control+V')
    await page.waitForFunction(() => window.__terminalPasteE2EFrames?.some(frame => frame.includes('attachment-3.png')))
  } catch (error) {
    imageClipboard = 'synthetic-fallback'
    const synthetic = await textarea.evaluate(element => {
      const transfer = new DataTransfer()
      transfer.items.add(new File(['image-png'], 'clipboard.png', { type: 'image/png' }))
      const event = new ClipboardEvent('paste', { bubbles: true, cancelable: true, clipboardData: transfer })
      const dispatched = element.dispatchEvent(event)
      return { dispatched, defaultPrevented: event.defaultPrevented }
    })
    assert.equal(synthetic.dispatched, false)
    assert.equal(synthetic.defaultPrevented, true)
    await page.waitForFunction(() => window.__terminalPasteE2EFrames?.some(frame => frame.includes('attachment-3.png')))
    console.log(`imageClipboard=${imageClipboard}: ${error instanceof Error ? error.message : String(error)}`)
  }

  const surface = page.getByRole('application', { name: `Terminal ${terminalId}` })
  const box = await surface.boundingBox()
  assert(box, 'terminal surface must have a rendered client rectangle')
  const fileDrag = await page.evaluate(({ x, y }) => {
    const target = document.body
    const transfer = new DataTransfer()
    transfer.items.add(new File(['drop-png'], 'drop.png', { type: 'image/png' }))
    const dragover = new DragEvent('dragover', { bubbles: true, cancelable: true, clientX: x, clientY: y, dataTransfer: transfer })
    const dragDispatched = target.dispatchEvent(dragover)
    return { dragDispatched, dragDefaultPrevented: dragover.defaultPrevented }
  }, { x: box.x + box.width / 2, y: box.y + box.height / 2 })
  assert.equal(fileDrag.dragDispatched, false)
  assert.equal(fileDrag.dragDefaultPrevented, true)
  await page.getByText('Drop a file to attach').waitFor({ state: 'visible' })
  const imageDrop = await page.evaluate(({ x, y }) => {
    const transfer = new DataTransfer()
    transfer.items.add(new File(['drop-png'], 'drop.png', { type: 'image/png' }))
    const event = new DragEvent('drop', { bubbles: true, cancelable: true, clientX: x, clientY: y, dataTransfer: transfer })
    const dispatched = document.body.dispatchEvent(event)
    return { dispatched, defaultPrevented: event.defaultPrevented }
  }, { x: box.x + box.width / 2, y: box.y + box.height / 2 })
  assert.equal(imageDrop.dispatched, false)
  assert.equal(imageDrop.defaultPrevented, true)
  await page.waitForFunction(() => window.__terminalPasteE2EFrames?.some(frame => frame.includes('attachment-4.png')))

  const markdownDrop = await page.evaluate(({ x, y }) => {
    const transfer = new DataTransfer()
    transfer.items.add(new File(['# drop\n'], '日本語😀%&.md', { type: 'text/markdown' }))
    const event = new DragEvent('drop', { bubbles: true, cancelable: true, clientX: x, clientY: y, dataTransfer: transfer })
    const dispatched = document.body.dispatchEvent(event)
    return { dispatched, defaultPrevented: event.defaultPrevented }
  }, { x: box.x + box.width / 2, y: box.y + box.height / 2 })
  assert.equal(markdownDrop.dispatched, false)
  assert.equal(markdownDrop.defaultPrevented, true)
  await page.waitForFunction(() => window.__terminalPasteE2EFrames?.some(frame => frame.includes('attachment-5.md')))

  const asciiMarkdownDrop = await page.evaluate(({ x, y }) => {
    const transfer = new DataTransfer()
    transfer.items.add(new File(['# ASCII drop\n'], 'notes.md', { type: 'text/markdown' }))
    const event = new DragEvent('drop', { bubbles: true, cancelable: true, clientX: x, clientY: y, dataTransfer: transfer })
    const dispatched = document.body.dispatchEvent(event)
    return { dispatched, defaultPrevented: event.defaultPrevented }
  }, { x: box.x + box.width / 2, y: box.y + box.height / 2 })
  assert.equal(asciiMarkdownDrop.dispatched, false)
  assert.equal(asciiMarkdownDrop.defaultPrevented, true)
  await page.waitForFunction(() => window.__terminalPasteE2EFrames?.some(frame => frame.includes('attachment-6.md')))

  const uploadsBeforeOutsideDrop = await page.evaluate(() => window.__terminalPasteE2EFrames?.filter(frame => frame.includes('attachment-')).length ?? 0)
  const hrefBeforeOutsideDrop = page.url()
  const outsideDrop = await page.evaluate(() => {
    const transfer = new DataTransfer()
    transfer.items.add(new File(['outside'], 'outside.md', { type: 'text/markdown' }))
    const event = new DragEvent('drop', { bubbles: true, cancelable: true, clientX: 1, clientY: 1, dataTransfer: transfer })
    const dispatched = document.body.dispatchEvent(event)
    return { dispatched, defaultPrevented: event.defaultPrevented, href: location.href }
  })
  assert.equal(outsideDrop.dispatched, false)
  assert.equal(outsideDrop.defaultPrevented, true)
  assert.equal(outsideDrop.href, hrefBeforeOutsideDrop)
  await page.waitForTimeout(50)
  assert.equal(await page.evaluate(() => window.__terminalPasteE2EFrames?.filter(frame => frame.includes('attachment-')).length ?? 0), uploadsBeforeOutsideDrop)

  const unsupportedDrop = await page.evaluate(({ x, y }) => {
    const transfer = new DataTransfer()
    transfer.items.add(new File(['unsafe'], 'unsafe.exe', { type: 'application/octet-stream' }))
    const event = new DragEvent('drop', { bubbles: true, cancelable: true, clientX: x, clientY: y, dataTransfer: transfer })
    const dispatched = document.body.dispatchEvent(event)
    return { dispatched, defaultPrevented: event.defaultPrevented }
  }, { x: box.x + box.width / 2, y: box.y + box.height / 2 })
  assert.equal(unsupportedDrop.dispatched, false)
  assert.equal(unsupportedDrop.defaultPrevented, true)
  await page.getByText('Supported file types: PNG, JPEG, WebP, MD, TXT, JSON, YAML, CSV, LOG, and ZIP').waitFor({ state: 'visible' })
  await new Promise(resolve => setTimeout(resolve, 100))

  const inputs = receivedFrames.filter(frame => frame.type === 'input').map(frame => frame.data)
  assert.deepEqual(inputs.slice(0, 4), ['plain text', 'line one\nline two', bracketedContextText, 'a'])
  assert.equal(inputs.filter(input => input === 'plain text').length, 1, 'Ctrl+V text arrives exactly once')
  assert.equal(inputs.filter(input => input === 'line one\nline two').length, 1, 'Ctrl+Shift+V multiline text arrives exactly once')
  assert.equal(inputs.filter(input => input === bracketedContextText).length, 1, 'context paste arrives bracketed exactly once')
  assert(inputs.every(input => input !== '\x16'), 'raw Ctrl+V must never reach websocket input')
  assert(inputs.slice(0, 3).every(input => !input.includes('\r')), 'paste boundary must not append Enter')
  assert.equal(inputs.filter(input => input === 'a').length, 1, 'normal typing remains a single xterm input')
  assert(inputs.includes('\x03'), 'Ctrl+C must remain a normal terminal input')
  assert(inputs.includes('\x18'), 'Ctrl+X must remain a normal terminal input')
  assert.equal(workflowInputs.length, 1, 'raw PTY input, including /model, must not create workflow turns')
  assert.equal(uploads.length, 6, 'Composer attach/drop plus raw clipboard image and image/markdown drops upload exactly once')
  assert.deepEqual(uploads.map(upload => upload.route), [
    `/terminals/${terminalId}/attachments/file`,
    `/terminals/${terminalId}/attachments/file`,
    `/terminals/${terminalId}/attachments/image`,
    `/terminals/${terminalId}/attachments/image`,
    `/terminals/${terminalId}/attachments/file`,
    `/terminals/${terminalId}/attachments/file`,
  ])
  assert.deepEqual(uploads.map(upload => upload.filename), [
    '%D0%BA%D0%B8%D1%80%D0%B8%D0%BB%D0%BB%D0%B8%D1%86%D0%B0.md',
    'bundle.zip',
    undefined,
    undefined,
    '%E6%97%A5%E6%9C%AC%E8%AA%9E%F0%9F%98%80%25%26.md',
    'notes.md',
  ])
  assert(uploads.every(upload => !upload.filename || /^[\x00-\x7f]*$/.test(upload.filename)), 'file filename headers must be ASCII-only')
  console.log(`PASS CAO_TERMINAL_WORKFLOW_COMPOSER_READY default-composer/multiline/file/send/no-duplicate/raw-model/no-turn/live-xterm/output-responsive-width/ctrl-v/ctrl-shift-v/context-multiline-bracketed/no-raw-v/no-enter/typing/ctrl-c/ctrl-x/global-file-drop/overlay/image-md/outside/unsupported imageClipboard=${imageClipboard}`)
} finally {
  await browser?.close()
  await vite.close()
  wsServer.close()
  await new Promise(resolve => server.close(resolve))
}
