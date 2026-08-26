import { ChangeEvent, DragEvent as ReactDragEvent, KeyboardEvent as ReactKeyboardEvent, useEffect, useRef, useState } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { ChevronDown, Paperclip, Send, Terminal as TermIcon, Trash2, X, Mail, FileText } from 'lucide-react'
import { api } from '../api'
import { useStore } from '../store'
import {
  sendTerminalAttachmentPath,
  terminalClipboardFiles,
  supportedTerminalTextFile,
  supportedTerminalImage,
  terminalFileValidationError,
  terminalImageValidationError,
} from './terminal-image-attachments'
import { InboxPanel } from './InboxPanel'
import { OutputViewer } from './OutputViewer'

interface TerminalViewProps {
  terminalId: string
  provider?: string
  agentProfile?: string | null
  onClose: () => void
}

interface ComposerAttachment {
  id: string
  name: string
  path: string
}

function newWorkflowRequestId(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') return globalThis.crypto.randomUUID()
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, token => {
    const random = Math.floor(Math.random() * 16)
    return (token === 'x' ? random : (random & 0x3) | 0x8).toString(16)
  })
}

export function TerminalView({ terminalId, provider, agentProfile, onClose }: TerminalViewProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const terminalRef = useRef<Terminal | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const [isDraggingFile, setIsDraggingFile] = useState(false)
  const [isComposerDraggingFile, setIsComposerDraggingFile] = useState(false)
  const [rawTerminalOpen, setRawTerminalOpen] = useState(false)
  const [inboxOpen, setInboxOpen] = useState(false)
  const [outputOpen, setOutputOpen] = useState(false)
  const [draft, setDraft] = useState('')
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([])
  const [uploading, setUploading] = useState(false)
  const [sending, setSending] = useState(false)
  const activeUploadCountRef = useRef(0)
  const composerRequestRef = useRef<{ message: string; requestId: string } | null>(null)
  const showSnackbar = useStore(state => state.showSnackbar)

  useEffect(() => {
    composerRef.current?.focus()
  }, [])

  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const term = new Terminal({
      cursorBlink: true,
      fontSize: 14,
      fontFamily: 'JetBrains Mono, Menlo, Monaco, Consolas, monospace',
      scrollback: 10000,
      theme: {
        background: '#0d1117',
        foreground: '#c9d1d9',
        cursor: '#58a6ff',
        selectionBackground: '#264f78',
        black: '#0d1117',
        red: '#ff7b72',
        green: '#3fb950',
        yellow: '#d29922',
        blue: '#58a6ff',
        magenta: '#bc8cff',
        cyan: '#39d353',
        white: '#c9d1d9',
      },
    })
    terminalRef.current = term

    const fitAddon = new FitAddon()
    term.loadAddon(fitAddon)
    term.open(el)

    // Connect WebSocket
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${location.host}/terminals/${terminalId}/ws`)
    ws.binaryType = 'arraybuffer'

    ws.onopen = () => {
      // Fit once the connection is live so we send correct dimensions
      fitAddon.fit()
      ws.send(JSON.stringify({ type: 'resize', rows: term.rows, cols: term.cols }))
    }

    ws.onmessage = (e) => {
      if (e.data instanceof ArrayBuffer) {
        term.write(new Uint8Array(e.data))
      }
    }

    ws.onclose = () => {
      term.write('\r\n\x1b[33m[Connection closed]\x1b[0m\r\n')
    }

    // Copy selection to clipboard on mouse-up
    term.onSelectionChange(() => {
      const selection = term.getSelection()
      if (selection) {
        navigator.clipboard.writeText(selection).catch(() => {})
      }
    })

    // This is the single terminal-to-WebSocket input boundary. Native paste is
    // handled synchronously below, but some xterm/browser combinations first
    // emit the raw Ctrl+V control byte. Only that exact byte is discarded here;
    // every other printable and control input remains owned by xterm as usual.
    term.onData((data) => {
      if (data === '\x16') return
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'input', data }))
      }
    })

    const uploadAttachment = async (file: File) => {
      try {
        const attachment = supportedTerminalImage([file])
          ? await api.uploadTerminalImage(terminalId, file)
          : await api.uploadTerminalFile(terminalId, file)
        if (!attachment.path.startsWith('/')) throw new Error('Terminal attachment path must be absolute')
        // Deliberately no newline/Enter: the terminal editor remains in control of submission.
        sendTerminalAttachmentPath(ws, attachment.path)
        showSnackbar({ type: 'success', message: 'File attached' })
      } catch {
        // Failed uploads leave normal keyboard/text input untouched.
        showSnackbar({ type: 'error', message: 'Failed to attach file' })
      }
    }

    const suppressNativePaste = (event: Event) => {
      event.preventDefault()
      event.stopImmediatePropagation()
      event.stopPropagation()
    }

    term.attachCustomKeyEventHandler((e) => {
      // xterm otherwise prevents this keydown before Chromium can dispatch its
      // trusted paste event. The capture-phase paste handler below is the only
      // clipboard payload owner for keyboard and context-menu paste alike.
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'v') return false
      if (e.ctrlKey && e.shiftKey && e.key === 'C') {
        const selection = term.getSelection()
        if (selection) navigator.clipboard.writeText(selection).catch(() => {})
        return false
      }
      return true
    })

    const terminalOwnsPaste = (event: ClipboardEvent) => {
      const activeElement = document.activeElement
      return term.textarea === activeElement
        || (activeElement instanceof Node && el.contains(activeElement))
        || (event.target instanceof Node && el.contains(event.target))
    }

    const hasPlainText = (clipboardData: DataTransfer) =>
      Array.from(clipboardData.items).some(item => item.kind === 'string' && item.type === 'text/plain')
      || Array.from(clipboardData.types).includes('text/plain')

    const onTerminalPasteCapture = (event: ClipboardEvent) => {
      if (!terminalOwnsPaste(event)) return

      // `clipboardData` is synchronously available during the trusted native
      // paste event. Do not make navigator.clipboard the primary path: it is
      // asynchronous and can lose the original browser gesture.
      const clipboardData = event.clipboardData
      if (!clipboardData) return

      const files = terminalClipboardFiles(clipboardData)
      if (files.length > 0) {
        suppressNativePaste(event)
        // Capture before xterm's hidden textarea listener so image data never
        // becomes terminal input.
        const error = terminalImageValidationError(files)
        if (error) {
          showSnackbar({ type: 'error', message: error })
          return
        }
        const image = supportedTerminalImage(files)
        if (!image) return
        void uploadAttachment(image)
        return
      }

      if (!hasPlainText(clipboardData)) return
      suppressNativePaste(event)
      const text = clipboardData.getData('text/plain')
      // xterm 6's public `paste` API normalizes LF to CR. Preserve the native
      // text/plain payload exactly, while applying the same DECSET 2004 wrapper
      // that xterm uses when bracketed paste is enabled. `input` is xterm's
      // supported application-input API, so onData remains the sole WebSocket
      // boundary rather than treating the payload as direct PTY keystrokes.
      const data = term.modes.bracketedPasteMode && term.options.ignoreBracketedPasteMode !== true
        ? `\x1b[200~${text}\x1b[201~`
        : text
      term.input(data)
    }

    const hasDraggedFiles = (event: DragEvent) => {
      const transfer = event.dataTransfer
      return Boolean(transfer && (transfer.files.length > 0 || Array.from(transfer.types).includes('Files')))
    }

    const isOverTerminal = (event: DragEvent) => {
      const rect = el.getBoundingClientRect()
      // Layout-less test environments have no usable client rectangle; their
      // event target still provides the same ownership boundary.
      if (rect.width === 0 || rect.height === 0) {
        return event.target instanceof Node && el.contains(event.target)
      }
      return event.clientX >= rect.left && event.clientX <= rect.right
        && event.clientY >= rect.top && event.clientY <= rect.bottom
    }

    const onGlobalFileDragOver = (event: DragEvent) => {
      if (!hasDraggedFiles(event)) return
      // Prevent Explorer/Chromium from opening a dropped file anywhere.
      event.preventDefault()
      setIsDraggingFile(isOverTerminal(event))
    }

    const onGlobalFileDrop = (event: DragEvent) => {
      if (!hasDraggedFiles(event)) return
      // Suppress a browser navigation outside the terminal too, but upload
      // only when the physical pointer position is inside this terminal.
      event.preventDefault()
      const insideTerminal = isOverTerminal(event)
      setIsDraggingFile(false)
      if (!insideTerminal) return
      const files = event.dataTransfer?.files ?? []
      const error = terminalFileValidationError(files)
      if (error) {
        showSnackbar({ type: 'error', message: error })
        return
      }
      const file = supportedTerminalImage(files) ?? supportedTerminalTextFile(files)
      if (!file) return
      void uploadAttachment(file)
    }

    // Native capture-phase paste owns both keyboard and context-menu pastes for
    // the active xterm textarea before xterm's own listener can consume them.
    document.addEventListener('paste', onTerminalPasteCapture, true)
    window.addEventListener('dragover', onGlobalFileDragOver, true)
    window.addEventListener('drop', onGlobalFileDrop, true)

    // Handle resize — debounce to avoid flooding
    let resizeTimer: ReturnType<typeof setTimeout>
    const resizeObserver = new ResizeObserver(() => {
      clearTimeout(resizeTimer)
      resizeTimer = setTimeout(() => {
        fitAddon.fit()
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'resize', rows: term.rows, cols: term.cols }))
        }
      }, 50)
    })
    resizeObserver.observe(el)

    // Initial fit after layout settles
    const initialFit = requestAnimationFrame(() => {
      fitAddon.fit()
    })

    return () => {
      cancelAnimationFrame(initialFit)
      clearTimeout(resizeTimer)
      resizeObserver.disconnect()
      document.removeEventListener('paste', onTerminalPasteCapture, true)
      window.removeEventListener('dragover', onGlobalFileDragOver, true)
      window.removeEventListener('drop', onGlobalFileDrop, true)
      ws.close()
      term.dispose()
      terminalRef.current = null
    }
  }, [showSnackbar, terminalId])

  const selectedAttachment = (files: FileList | File[]) =>
    supportedTerminalImage(files) ?? supportedTerminalTextFile(files)

  const addComposerAttachment = async (file: File) => {
    if (sending) return
    const error = terminalFileValidationError([file])
    if (error) {
      showSnackbar({ type: 'error', message: error })
      return
    }
    activeUploadCountRef.current += 1
    setUploading(true)
    try {
      const attachment = supportedTerminalImage([file])
        ? await api.uploadTerminalImage(terminalId, file)
        : await api.uploadTerminalFile(terminalId, file)
      if (!attachment.path.startsWith('/')) throw new Error('Terminal attachment path must be absolute')
      setAttachments(current => [...current, {
        id: attachment.path,
        name: file.name,
        path: attachment.path,
      }])
    } catch {
      showSnackbar({ type: 'error', message: 'Failed to attach file' })
    } finally {
      activeUploadCountRef.current = Math.max(0, activeUploadCountRef.current - 1)
      setUploading(activeUploadCountRef.current > 0)
    }
  }

  const addComposerFiles = (files: FileList | File[]) => {
    if (sending) return
    const error = terminalFileValidationError(files)
    if (error) {
      showSnackbar({ type: 'error', message: error })
      return
    }
    const file = selectedAttachment(files)
    if (file) void addComposerAttachment(file)
  }

  const onComposerPaste = (event: React.ClipboardEvent<HTMLTextAreaElement>) => {
    if (sending) {
      event.preventDefault()
      return
    }
    const files = terminalClipboardFiles(event.clipboardData)
    if (files.length === 0) return
    event.preventDefault()
    addComposerFiles(files)
  }

  const onComposerDrop = (event: ReactDragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setIsComposerDraggingFile(false)
    if (sending) return
    addComposerFiles(event.dataTransfer.files)
  }

  const submitWorkflowInput = async () => {
    if (sending || activeUploadCountRef.current > 0 || !draft.trim()) return
    const message = attachments.length === 0
      ? draft
      : `${draft}\n\nAttached terminal paths:\n${attachments.map(attachment => `- ${attachment.path}`).join('\n')}`
    setSending(true)
    try {
      const priorRequest = composerRequestRef.current
      const requestId = priorRequest?.message === message
        ? priorRequest.requestId
        : newWorkflowRequestId()
      composerRequestRef.current = { message, requestId }
      const result = await api.sendWorkflowInput(terminalId, message, requestId)
      if (!result.success || !result.accepted) throw new Error('Workflow input was not accepted')
      setDraft('')
      setAttachments([])
      composerRequestRef.current = null
      showSnackbar({
        type: result.queued || result.duplicate ? 'info' : 'success',
        message: result.queued
          ? `Workflow input queued · turn ${result.turn_id}`
          : result.duplicate
            ? `Workflow input already accepted · turn ${result.turn_id}`
            : `Workflow input admitted · turn ${result.turn_id}`,
      })
      composerRef.current?.focus()
    } catch (error: any) {
      if (
        error?.reasonCode === 'WORKFLOW_INPUT_IDEMPOTENCY_CONFLICT'
        || error?.reasonCode === 'WORKFLOW_INPUT_NO_LONGER_EXECUTABLE'
      ) {
        composerRequestRef.current = null
      }
      showSnackbar({ type: 'error', message: error.message || 'Failed to send workflow input' })
    } finally {
      setSending(false)
    }
  }

  const onComposerKeyDown = (event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
      event.preventDefault()
      void submitWorkflowInput()
    }
  }

  const openRawTerminal = () => {
    setRawTerminalOpen(true)
    requestAnimationFrame(() => terminalRef.current?.focus())
  }

  const onBrowseAttachment = (event: ChangeEvent<HTMLInputElement>) => {
    if (!sending && event.target.files) addComposerFiles(event.target.files)
    event.target.value = ''
  }

  return (
    <div className="fixed inset-0 z-50 flex flex-col" style={{ background: '#0d1117' }}>
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 bg-gray-900 border-b border-gray-700/50 shrink-0">
        <div className="flex items-center gap-3">
          <TermIcon size={16} className="text-emerald-400" />
          <span className="text-sm font-mono text-gray-300">{terminalId}</span>
          {provider && <span className="text-xs text-gray-500 bg-gray-800 px-2 py-0.5 rounded">{provider}</span>}
          {agentProfile && <span className="text-xs text-emerald-400 bg-emerald-900/30 px-2 py-0.5 rounded">{agentProfile}</span>}
        </div>
        <div className="flex items-center gap-3">
          <span className="text-[10px] text-gray-600">Click X to close</span>
          <button
            onClick={onClose}
            className="p-1 text-gray-500 hover:text-white transition-colors rounded"
            title="Close terminal"
          >
            <X size={18} />
          </button>
        </div>
      </div>
      {/* Terminal output remains live; PTY input is deliberately secondary. */}
      <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
        <div
          ref={containerRef}
          role="application"
          aria-label={`Terminal ${terminalId}`}
          data-cao-ui-bundle="CAO.UI.ATTACHMENTS.C5"
          style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 }}
        />
        {isDraggingFile && (
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-3 flex items-center justify-center rounded-lg border-2 border-dashed border-emerald-400 bg-emerald-950/80 text-sm font-medium text-emerald-200"
          >
            Drop a file to attach
          </div>
        )}
      </div>
      {!rawTerminalOpen ? (
        <div
          className="shrink-0 border-t border-gray-700 bg-gray-900 p-4"
          data-cao-ui-bundle="CAO.UI.WORKFLOW.INPUT.P1"
          data-testid="workflow-composer-surface"
          onDragOver={event => {
            if (Array.from(event.dataTransfer.types).includes('Files')) {
              event.preventDefault()
              if (sending) return
              setIsComposerDraggingFile(true)
            }
          }}
          onDragLeave={() => {
            if (!sending) setIsComposerDraggingFile(false)
          }}
          onDrop={onComposerDrop}
        >
          <div className="mb-2 flex items-center justify-between gap-3">
            <label htmlFor={`workflow-composer-${terminalId}`} className="text-sm font-medium text-gray-100">
              Workflow Composer
            </label>
            <div className="flex items-center gap-1">
              <button type="button" aria-label="Open Inbox" onClick={() => setInboxOpen(true)} className="inline-flex min-h-9 items-center gap-1 rounded px-2 text-xs text-gray-400 hover:bg-gray-800 hover:text-white" title="Open Inbox">
                <Mail size={14} /><span className="hidden sm:inline">Inbox</span>
              </button>
              <button type="button" aria-label="Open Output" onClick={() => setOutputOpen(true)} className="inline-flex min-h-9 items-center gap-1 rounded px-2 text-xs text-gray-400 hover:bg-gray-800 hover:text-white" title="Open Output">
                <FileText size={14} /><span className="hidden sm:inline">Output</span>
              </button>
              <button
                type="button"
                aria-label="Raw Terminal"
                onClick={openRawTerminal}
                className="flex min-h-9 items-center gap-1 rounded px-2 text-xs text-gray-400 hover:bg-gray-800 hover:text-white"
              >
                <span className="hidden sm:inline">Raw Terminal</span><ChevronDown size={14} />
              </button>
            </div>
          </div>
          <textarea
            ref={composerRef}
            id={`workflow-composer-${terminalId}`}
            aria-label="Workflow Composer"
            value={draft}
            onChange={event => {
              if (!sending) setDraft(event.target.value)
            }}
            onKeyDown={onComposerKeyDown}
            onPaste={onComposerPaste}
            disabled={sending}
            placeholder="Describe the work, add context, or paste a long prompt…"
            className="min-h-32 w-full resize-y rounded-lg border border-gray-700 bg-gray-950 px-3 py-2 font-mono text-sm text-gray-100 outline-none placeholder:text-gray-600 focus:border-emerald-500"
          />
          {isComposerDraggingFile && (
            <div className="mt-2 rounded border border-dashed border-emerald-400 bg-emerald-950/60 px-3 py-2 text-sm text-emerald-200">
              Drop a file to attach to this workflow input
            </div>
          )}
          {attachments.length > 0 && (
            <ul aria-label="Workflow attachments" className="mt-2 space-y-1">
              {attachments.map(attachment => (
                <li key={attachment.id} className="flex items-center justify-between gap-3 rounded bg-gray-800 px-2 py-1 text-xs text-gray-300">
                  <span className="truncate" title={attachment.path}>{attachment.name}</span>
                  <button
                    type="button"
                    aria-label={`Remove ${attachment.name}`}
                    onClick={() => {
                      if (!sending) setAttachments(current => current.filter(item => item.id !== attachment.id))
                    }}
                    disabled={sending}
                    className="text-gray-500 hover:text-red-300"
                  >
                    <Trash2 size={14} />
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="mt-3 flex items-center justify-between gap-3">
            <div>
              <input
                ref={fileInputRef}
                type="file"
                className="hidden"
                accept="image/png,image/jpeg,image/webp,.md,.txt,.json,.yaml,.yml,.csv,.log,.zip"
                onChange={onBrowseAttachment}
              />
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={sending}
                className="flex items-center gap-1.5 text-xs text-gray-300 hover:text-white disabled:opacity-50"
              >
                <Paperclip size={14} /> {uploading ? 'Attaching…' : 'Attach file'}
              </button>
            </div>
            <div className="flex items-center gap-3">
              <span className="hidden text-xs text-gray-500 sm:inline">Enter for newline · Ctrl/Cmd+Enter to send</span>
              <button
                type="button"
                onClick={() => void submitWorkflowInput()}
                disabled={sending || uploading || !draft.trim()}
                className="flex items-center gap-1.5 rounded bg-emerald-600 px-3 py-2 text-xs font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
              >
                <Send size={14} /> {sending ? 'Sending…' : 'Send task'}
              </button>
            </div>
          </div>
        </div>
      ) : (
        <div className="shrink-0 border-t border-gray-700 bg-gray-900 px-4 py-2">
          <button type="button" onClick={() => setRawTerminalOpen(false)} className="text-xs text-gray-400 hover:text-white">
            Return to Workflow Composer
          </button>
        </div>
      )}
      {inboxOpen && <InboxPanel terminalId={terminalId} onClose={() => setInboxOpen(false)} />}
      {outputOpen && <OutputViewer terminalId={terminalId} onClose={() => setOutputOpen(false)} />}
    </div>
  )
}
