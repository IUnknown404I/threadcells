import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { api, type WorkflowInputResponse } from '../api'
import { TerminalView } from '../components/TerminalView'
import { useStore } from '../store'

const testState = vi.hoisted(() => ({
  sockets: [] as Array<{ send: ReturnType<typeof vi.fn> }>,
  textareas: [] as HTMLTextAreaElement[],
  keyHandlers: [] as Array<(event: KeyboardEvent) => boolean>,
  onDataCalls: [] as string[],
  inputCalls: [] as string[],
  bracketedPasteMode: false,
  clipboardRead: vi.fn(),
}))

vi.mock('@xterm/xterm', () => ({
  Terminal: class {
    rows = 24
    cols = 80
    private dataHandler: ((data: string) => void) | null = null
    private textarea: HTMLTextAreaElement | null = null
    open(el: HTMLElement) {
      const textarea = document.createElement('textarea')
      textarea.addEventListener('keydown', event => {
        if (event.ctrlKey && event.key.toLowerCase() === 'v' && !event.defaultPrevented) {
          this.dataHandler?.('\x16')
        }
      })
      textarea.addEventListener('paste', event => {
        if (event.defaultPrevented) return
        this.dataHandler?.((event.clipboardData?.getData('text/plain') ?? ''))
      })
      el.append(textarea)
      this.textarea = textarea
      testState.textareas.push(textarea)
    }
    loadAddon() {}
    write() {}
    get modes() { return { bracketedPasteMode: testState.bracketedPasteMode } }
    options = { ignoreBracketedPasteMode: false }
    input(data: string) {
      testState.inputCalls.push(data)
      this.dataHandler?.(data)
    }
    focus() { this.textarea?.focus() }
    dispose() {}
    getSelection() { return '' }
    onSelectionChange() { return { dispose() {} } }
    attachCustomKeyEventHandler(handler: (event: KeyboardEvent) => boolean) { testState.keyHandlers.push(handler) }
    onData(handler: (data: string) => void) {
      this.dataHandler = data => {
        testState.onDataCalls.push(data)
        handler(data)
      }
      return { dispose() {} }
    }
  },
}))

vi.mock('@xterm/addon-fit', () => ({
  FitAddon: class { fit() {} },
}))

class MockWebSocket {
  static OPEN = 1
  readyState = MockWebSocket.OPEN
  binaryType = ''
  send = vi.fn()
  onopen: (() => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onclose: (() => void) | null = null

  constructor(_url: string) {
    testState.sockets.push(this)
  }

  close() {}
}

class MockResizeObserver {
  observe() {}
  disconnect() {}
}

Object.defineProperty(globalThis, 'WebSocket', { value: MockWebSocket, writable: true })
Object.defineProperty(globalThis, 'ResizeObserver', { value: MockResizeObserver, writable: true })
Object.defineProperty(globalThis, 'requestAnimationFrame', { value: (callback: FrameRequestCallback) => {
  callback(0)
  return 1
}, writable: true })
Object.defineProperty(globalThis, 'cancelAnimationFrame', { value: () => {}, writable: true })
Object.defineProperty(globalThis.navigator, 'clipboard', {
  value: { read: testState.clipboardRead, writeText: vi.fn() },
  configurable: true,
})

const file = (type: string, size = 1, name = type === 'text/markdown' ? 'notes.md' : 'image.png') => ({ type, size, name } as File)

function fileEvent(type: 'paste' | 'dragover' | 'drop', files: File[], text = '') {
  const event = new Event(type, { bubbles: true, cancelable: true })
  Object.defineProperty(event, type === 'paste' ? 'clipboardData' : 'dataTransfer', {
    value: type === 'paste'
      ? {
        files,
        items: files.length > 0
          ? files.map(file => ({ kind: 'file', type: file.type, getAsFile: () => file }))
          : [{ kind: 'string', type: 'text/plain' }],
        types: files.length > 0 ? files.map(file => file.type) : ['text/plain'],
        getData: () => text,
      }
      : { files },
  })
  return event
}

function clipboardItem(type: string, text: string | Blob) {
  const blob = typeof text === 'string'
    ? { text: () => Promise.resolve(text) } as Blob
    : text
  return {
    types: [type],
    getType: vi.fn().mockResolvedValue(blob),
  } as unknown as ClipboardItem
}

function ctrlVEvent() {
  return new KeyboardEvent('keydown', { bubbles: true, cancelable: true, key: 'v', ctrlKey: true })
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(res => {
    resolve = res
  })
  return { promise, resolve }
}

const admittedWorkflowInput = (overrides: Partial<WorkflowInputResponse> = {}): WorkflowInputResponse => ({
  success: true,
  accepted: true,
  duplicate: false,
  turn_id: 73,
  queued: false,
  status: 'provider_admitted',
  reason_code: null,
  ...overrides,
})

describe('TerminalView image attachments', () => {
  const upload = vi.spyOn(api, 'uploadTerminalImage')
  const uploadFile = vi.spyOn(api, 'uploadTerminalFile')
  const sendWorkflowInput = vi.spyOn(api, 'sendWorkflowInput')

  beforeEach(() => {
    testState.sockets.length = 0
    testState.textareas.length = 0
    testState.keyHandlers.length = 0
    testState.onDataCalls.length = 0
    testState.inputCalls.length = 0
    testState.bracketedPasteMode = false
    testState.clipboardRead.mockReset()
    upload.mockReset()
    uploadFile.mockReset()
    sendWorkflowInput.mockReset()
    useStore.setState({ snackbar: null })
  })

  afterEach(() => cleanup())

  function renderTerminal(terminalId = 'abcd1234') {
    render(<TerminalView terminalId={terminalId} onClose={() => {}} />)
    return screen.getByRole('application', { name: `Terminal ${terminalId}` })
  }

  it('defaults to the Workflow Composer and submits one multiline workflow input on Ctrl+Enter', async () => {
    sendWorkflowInput.mockResolvedValue(admittedWorkflowInput())
    renderTerminal()
    const composer = screen.getByRole('textbox', { name: 'Workflow Composer' })

    fireEvent.change(composer, { target: { value: 'line one\nline two' } })
    fireEvent.keyDown(composer, { key: 'Enter', ctrlKey: true })
    fireEvent.keyDown(composer, { key: 'Enter', ctrlKey: true })

    await waitFor(() => expect(sendWorkflowInput).toHaveBeenCalledTimes(1))
    expect(sendWorkflowInput).toHaveBeenCalledWith(
      'abcd1234',
      'line one\nline two',
      expect.any(String),
    )
    expect(testState.sockets[0].send).not.toHaveBeenCalledWith(
      JSON.stringify({ type: 'input', data: 'line one\nline two' }),
    )
  })

  it('reports a durable queued response as queued instead of sent', async () => {
    sendWorkflowInput.mockResolvedValue(admittedWorkflowInput({
      turn_id: 74,
      queued: true,
      status: 'queued_provider_execution',
      reason_code: 'WORKFLOW_CONTINUATION_PENDING',
    }))
    renderTerminal()
    const composer = screen.getByRole('textbox', { name: 'Workflow Composer' })

    fireEvent.change(composer, { target: { value: 'follow the current turn' } })
    fireEvent.keyDown(composer, { key: 'Enter', ctrlKey: true })

    await waitFor(() => expect(useStore.getState().snackbar).toEqual({
      type: 'info',
      message: 'Workflow input queued · turn 74',
    }))
  })

  it('reuses one request identity when the same draft is retried after a request error', async () => {
    sendWorkflowInput
      .mockRejectedValueOnce(new Error('connection interrupted'))
      .mockResolvedValueOnce(admittedWorkflowInput({ turn_id: 75 }))
    renderTerminal()
    const composer = screen.getByRole('textbox', { name: 'Workflow Composer' })

    fireEvent.change(composer, { target: { value: 'retry exactly once' } })
    fireEvent.keyDown(composer, { key: 'Enter', ctrlKey: true })
    await waitFor(() => expect(sendWorkflowInput).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(composer).not.toBeDisabled())
    fireEvent.keyDown(composer, { key: 'Enter', ctrlKey: true })
    await waitFor(() => expect(sendWorkflowInput).toHaveBeenCalledTimes(2))

    expect(sendWorkflowInput.mock.calls[0][2]).toBe(sendWorkflowInput.mock.calls[1][2])
  })

  it('uses a new request identity after a closed-turn conflict', async () => {
    sendWorkflowInput
      .mockRejectedValueOnce(Object.assign(new Error('turn closed before admission'), {
        reasonCode: 'WORKFLOW_INPUT_NO_LONGER_EXECUTABLE',
      }))
      .mockResolvedValueOnce(admittedWorkflowInput({ turn_id: 76 }))
    renderTerminal()
    const composer = screen.getByRole('textbox', { name: 'Workflow Composer' })

    fireEvent.change(composer, { target: { value: 'submit as a fresh task' } })
    fireEvent.keyDown(composer, { key: 'Enter', ctrlKey: true })
    await waitFor(() => expect(sendWorkflowInput).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(composer).not.toBeDisabled())
    fireEvent.keyDown(composer, { key: 'Enter', ctrlKey: true })
    await waitFor(() => expect(sendWorkflowInput).toHaveBeenCalledTimes(2))

    expect(sendWorkflowInput.mock.calls[0][2]).not.toBe(sendWorkflowInput.mock.calls[1][2])
  })

  it('does not submit with Ctrl+Enter while a Composer attachment upload is pending', async () => {
    const pendingUpload = deferred<{ path: string }>()
    upload.mockReturnValue(pendingUpload.promise)
    sendWorkflowInput.mockResolvedValue(admittedWorkflowInput())
    renderTerminal()
    const composer = screen.getByRole('textbox', { name: 'Workflow Composer' })

    fireEvent.change(composer, { target: { value: 'review this image' } })
    fireEvent(composer, fileEvent('paste', [file('image/png')]))
    await waitFor(() => expect(upload).toHaveBeenCalledTimes(1))

    fireEvent.keyDown(composer, { key: 'Enter', ctrlKey: true })
    expect(sendWorkflowInput).not.toHaveBeenCalled()

    pendingUpload.resolve({ path: '/runtime/terminal-attachments/abcd1234/image.png' })
    await waitFor(() => expect(screen.getByRole('list', { name: 'Workflow attachments' })).toBeInTheDocument())
    fireEvent.keyDown(composer, { key: 'Enter', ctrlKey: true })
    await waitFor(() => expect(sendWorkflowInput).toHaveBeenCalledWith(
      'abcd1234',
      'review this image\n\nAttached terminal paths:\n- /runtime/terminal-attachments/abcd1234/image.png',
      expect.any(String),
    ))
  })

  it.each([
    ['A then B', [0, 1]],
    ['B then A', [1, 0]],
  ])('keeps both concurrent Composer uploads when %s resolves', async (_order, resolutionOrder) => {
    const uploads = [deferred<{ path: string }>(), deferred<{ path: string }>()]
    upload.mockImplementationOnce(() => uploads[0].promise).mockImplementationOnce(() => uploads[1].promise)
    sendWorkflowInput.mockResolvedValue(admittedWorkflowInput())
    renderTerminal()
    const composer = screen.getByRole('textbox', { name: 'Workflow Composer' })
    const surface = screen.getByTestId('workflow-composer-surface')
    const paths = [
      '/runtime/terminal-attachments/abcd1234/a.png',
      '/runtime/terminal-attachments/abcd1234/b.png',
    ]

    fireEvent.change(composer, { target: { value: 'review both images' } })
    fireEvent(composer, fileEvent('paste', [file('image/png', 1, 'a.png')]))
    fireEvent(surface, fileEvent('drop', [file('image/png', 1, 'b.png')]))
    await waitFor(() => expect(upload).toHaveBeenCalledTimes(2))

    uploads[resolutionOrder[0]].resolve({ path: paths[resolutionOrder[0]] })
    await waitFor(() => expect(screen.getByRole('list', { name: 'Workflow attachments' })).toHaveTextContent(
      resolutionOrder[0] === 0 ? 'a.png' : 'b.png',
    ))
    fireEvent.keyDown(composer, { key: 'Enter', ctrlKey: true })
    expect(sendWorkflowInput).not.toHaveBeenCalled()

    uploads[resolutionOrder[1]].resolve({ path: paths[resolutionOrder[1]] })
    await waitFor(() => expect(screen.getByRole('list', { name: 'Workflow attachments' })).toHaveTextContent('a.png'))
    await waitFor(() => expect(screen.getByRole('list', { name: 'Workflow attachments' })).toHaveTextContent('b.png'))
    fireEvent.keyDown(composer, { key: 'Enter', ctrlKey: true })
    await waitFor(() => expect(sendWorkflowInput).toHaveBeenCalledTimes(1))
    expect(sendWorkflowInput).toHaveBeenCalledWith(
      'abcd1234',
      `review both images\n\nAttached terminal paths:\n- ${paths[resolutionOrder[0]]}\n- ${paths[resolutionOrder[1]]}`,
      expect.any(String),
    )
    await waitFor(() => expect(composer).toHaveValue(''))
    expect(screen.queryByRole('list', { name: 'Workflow attachments' })).not.toBeInTheDocument()
  })

  it('locks Composer mutations until a pending workflow send clears its submitted draft and attachments', async () => {
    upload.mockResolvedValue({ path: '/runtime/terminal-attachments/abcd1234/image.png' })
    const pendingSend = deferred<WorkflowInputResponse>()
    sendWorkflowInput.mockReturnValue(pendingSend.promise)
    renderTerminal()
    const composer = screen.getByRole('textbox', { name: 'Workflow Composer' })
    const surface = screen.getByTestId('workflow-composer-surface')

    fireEvent.change(composer, { target: { value: 'submitted draft' } })
    fireEvent(composer, fileEvent('paste', [file('image/png')]))
    await waitFor(() => expect(screen.getByRole('list', { name: 'Workflow attachments' })).toBeInTheDocument())
    fireEvent.keyDown(composer, { key: 'Enter', ctrlKey: true })
    await waitFor(() => expect(sendWorkflowInput).toHaveBeenCalledTimes(1))

    expect(composer).toBeDisabled()
    fireEvent.change(composer, { target: { value: 'new draft' } })
    const paste = fileEvent('paste', [file('image/png')])
    fireEvent(composer, paste)
    fireEvent(surface, fileEvent('drop', [file('image/png')]))
    fireEvent.click(screen.getByRole('button', { name: 'Remove image.png' }))
    expect(paste.defaultPrevented).toBe(true)
    expect(upload).toHaveBeenCalledTimes(1)
    expect(composer).toHaveValue('submitted draft')
    expect(screen.getByRole('button', { name: 'Remove image.png' })).toBeDisabled()

    pendingSend.resolve(admittedWorkflowInput())
    await waitFor(() => expect(composer).toHaveValue(''))
    expect(screen.queryByRole('list', { name: 'Workflow attachments' })).not.toBeInTheDocument()
  })

  it('keeps the Composer draft while Raw Terminal is open and makes raw mode explicit', () => {
    renderTerminal()
    const composer = screen.getByRole('textbox', { name: 'Workflow Composer' })
    fireEvent.change(composer, { target: { value: 'keep this draft' } })
    fireEvent.click(screen.getByRole('button', { name: /Raw Terminal/i }))
    expect(screen.queryByRole('textbox', { name: 'Workflow Composer' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Return to Workflow Composer/i }))
    expect(screen.getByRole('textbox', { name: 'Workflow Composer' })).toHaveValue('keep this draft')
  })

  it('attaches a pasted image to the Composer and permits removal before send', async () => {
    upload.mockResolvedValue({ path: '/runtime/terminal-attachments/abcd1234/image.png' })
    renderTerminal()
    const composer = screen.getByRole('textbox', { name: 'Workflow Composer' })

    fireEvent(composer, fileEvent('paste', [file('image/png')]))

    await waitFor(() => expect(upload).toHaveBeenCalledWith('abcd1234', expect.anything()))
    expect(screen.getByRole('list', { name: 'Workflow attachments' })).toHaveTextContent('image.png')
    fireEvent.click(screen.getByRole('button', { name: 'Remove image.png' }))
    expect(screen.queryByRole('list', { name: 'Workflow attachments' })).not.toBeInTheDocument()
  })

  it('uploads a dropped text file through the Composer attachment path', async () => {
    uploadFile.mockResolvedValue({ path: '/runtime/terminal-attachments/abcd1234/notes.md' })
    renderTerminal()
    fireEvent(screen.getByTestId('workflow-composer-surface'), fileEvent('drop', [file('text/markdown')]))

    await waitFor(() => expect(uploadFile).toHaveBeenCalledWith('abcd1234', expect.anything()))
    expect(screen.getByRole('list', { name: 'Workflow attachments' })).toHaveTextContent('notes.md')
  })

  it('adds an opaque ZIP attachment to the Workflow Composer message', async () => {
    uploadFile.mockResolvedValue({ path: '/runtime/terminal-attachments/abcd1234/bundle.zip' })
    const sendWorkflowInput = vi.spyOn(api, 'sendWorkflowInput').mockResolvedValue(admittedWorkflowInput())
    renderTerminal()

    fireEvent(screen.getByTestId('workflow-composer-surface'), fileEvent('drop', [file('application/x-zip-compressed', 1, 'bundle.zip')]))
    await waitFor(() => expect(uploadFile).toHaveBeenCalledWith('abcd1234', expect.anything()))
    expect(screen.getByRole('list', { name: 'Workflow attachments' })).toHaveTextContent('bundle.zip')

    fireEvent.change(screen.getByRole('textbox', { name: 'Workflow Composer' }), { target: { value: 'inspect archive' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send task' }))

    await waitFor(() => expect(sendWorkflowInput).toHaveBeenCalledWith(
      'abcd1234',
      'inspect archive\n\nAttached terminal paths:\n- /runtime/terminal-attachments/abcd1234/bundle.zip',
      expect.any(String),
    ))
  })

  it.each(['plain text', 'line one\nline two'])('routes native text/plain unchanged without raw Ctrl+V or Enter: %s', async (text) => {
    renderTerminal()
    const ctrlV = ctrlVEvent()
    testState.textareas[0].dispatchEvent(ctrlV)
    const nativePaste = fileEvent('paste', [], text)
    testState.textareas[0].dispatchEvent(nativePaste)

    expect(upload).not.toHaveBeenCalled()
    expect(ctrlV.defaultPrevented).toBe(false)
    expect(testState.keyHandlers[0](ctrlV)).toBe(false)
    expect(nativePaste.defaultPrevented).toBe(true)
    expect(testState.onDataCalls).toEqual(['\x16', text])
    expect(testState.inputCalls).toEqual([text])
    await waitFor(() => expect(testState.sockets[0].send).toHaveBeenCalledWith(
      JSON.stringify({ type: 'input', data: text }),
    ))
    expect(testState.sockets[0].send).not.toHaveBeenCalledWith(JSON.stringify({ type: 'input', data: '\x16' }))
    expect(testState.sockets[0].send).not.toHaveBeenCalledWith(JSON.stringify({ type: 'input', data: `${text}\r` }))
  })

  it('preserves LF exactly while using xterm input with the active bracketed-paste wrapper', async () => {
    renderTerminal()
    testState.bracketedPasteMode = true
    const text = 'line one\nline two'
    const nativePaste = fileEvent('paste', [], text)
    testState.textareas[0].dispatchEvent(nativePaste)

    const bracketed = `\x1b[200~${text}\x1b[201~`
    expect(nativePaste.defaultPrevented).toBe(true)
    expect(testState.inputCalls).toEqual([bracketed])
    expect(testState.onDataCalls).toEqual([bracketed])
    await waitFor(() => expect(testState.sockets[0].send).toHaveBeenCalledWith(
      JSON.stringify({ type: 'input', data: bracketed }),
    ))
    expect(testState.sockets[0].send).not.toHaveBeenCalledWith(
      JSON.stringify({ type: 'input', data: `${bracketed}\r` }),
    )
  })

  it('captures native image paste, uploads it, and inserts only its absolute path without Enter', async () => {
    upload.mockResolvedValue({ path: '/runtime/terminal-attachments/abcd1234/image.png' })
    renderTerminal()
    const ctrlV = ctrlVEvent()
    testState.textareas[0].dispatchEvent(ctrlV)
    const nativePaste = fileEvent('paste', [file('image/png')])
    testState.textareas[0].dispatchEvent(nativePaste)

    await waitFor(() => expect(upload).toHaveBeenCalledWith('abcd1234', expect.anything()))
    expect(ctrlV.defaultPrevented).toBe(false)
    expect(testState.keyHandlers[0](ctrlV)).toBe(false)
    expect(nativePaste.defaultPrevented).toBe(true)
    expect(testState.onDataCalls).toEqual(['\x16'])
    expect(testState.sockets[0].send).toHaveBeenCalledWith(
      JSON.stringify({ type: 'input', data: '/runtime/terminal-attachments/abcd1234/image.png' }),
    )
    expect(testState.sockets[0].send).not.toHaveBeenCalledWith(JSON.stringify({ type: 'input', data: '\x16' }))
    expect(testState.sockets[0].send).not.toHaveBeenCalledWith(
      JSON.stringify({ type: 'input', data: '/runtime/terminal-attachments/abcd1234/image.png\r' }),
    )
    expect(useStore.getState().snackbar).toEqual({ type: 'success', message: 'File attached' })
  })

  it('leaves Ctrl+C and Ctrl+X unchanged while the input boundary drops only raw Ctrl+V', () => {
    renderTerminal()

    const ctrlC = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, key: 'c', ctrlKey: true })
    const ctrlX = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, key: 'x', ctrlKey: true })
    testState.textareas[0].dispatchEvent(ctrlC)
    testState.textareas[0].dispatchEvent(ctrlX)

    expect(ctrlC.defaultPrevented).toBe(false)
    expect(ctrlX.defaultPrevented).toBe(false)
    expect(testState.onDataCalls).toEqual([])
  })

  it('does not inject input and shows a concise error when an image upload fails', async () => {
    upload.mockRejectedValue(new Error('network unavailable'))
    renderTerminal()

    fireEvent(testState.textareas[0], fileEvent('paste', [file('image/png')]))

    await waitFor(() => expect(useStore.getState().snackbar).toEqual({ type: 'error', message: 'Failed to attach file' }))
    expect(testState.sockets[0].send).not.toHaveBeenCalled()
  })

  it('shows a drop affordance and routes an image drop through the same upload path', async () => {
    upload.mockResolvedValue({ path: '/runtime/terminal-attachments/fedcba98/image.webp' })
    const surface = renderTerminal('fedcba98')

    fireEvent(surface, fileEvent('dragover', [file('image/webp')]))
    expect(screen.getByText('Drop a file to attach')).toBeInTheDocument()
    fireEvent(surface, fileEvent('drop', [file('image/webp')]))

    await waitFor(() => expect(upload).toHaveBeenCalledWith('fedcba98', expect.anything()))
    expect(testState.sockets[0].send).toHaveBeenCalledWith(
      JSON.stringify({ type: 'input', data: '/runtime/terminal-attachments/fedcba98/image.webp' }),
    )
  })

  it('uploads a markdown drop only inside the terminal and suppresses an outside file drop', async () => {
    uploadFile.mockResolvedValue({ path: '/runtime/terminal-attachments/abcd1234/file.md' })
    const surface = renderTerminal()
    const inside = fileEvent('drop', [file('text/markdown')])
    fireEvent(surface, inside)
    await waitFor(() => expect(uploadFile).toHaveBeenCalledWith('abcd1234', expect.anything()))
    expect(inside.defaultPrevented).toBe(true)

    const outside = fileEvent('drop', [file('text/markdown')])
    fireEvent(document.body, outside)
    expect(outside.defaultPrevented).toBe(true)
    expect(uploadFile).toHaveBeenCalledTimes(1)
  })

  it.each([
    ['unsupported', file('image/gif'), 'Supported file types: PNG, JPEG, WebP, MD, TXT, JSON, YAML, CSV, LOG, and ZIP'],
    ['oversized', file('image/png', 10 * 1024 * 1024 + 1), 'File must be 10 MiB or smaller'],
  ])('shows a visible concise error for an %s image drop', (_kind, droppedFile, message) => {
    const surface = renderTerminal()
    const event = fileEvent('drop', [droppedFile])

    fireEvent(surface, event)

    expect(event.defaultPrevented).toBe(true)
    expect(upload).not.toHaveBeenCalled()
    expect(useStore.getState().snackbar).toEqual({ type: 'error', message })
  })
})
