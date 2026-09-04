import { useState, useEffect, useLayoutEffect, useMemo, useRef } from 'react'
import { api, type TerminalOutputResponse } from '../api'
import { X, RefreshCw, Copy, Check, FileText, Maximize2, Minimize2 } from 'lucide-react'
import { ModalLoadingBody } from './ModalLoadingBody'
import { useI18n } from '../i18n'

function stripAnsi(text: string): string {
  return text.replace(/\x1b\[[0-9;]*[a-zA-Z]/g, '').replace(/\x1b\][^\x07]*\x07/g, '')
}

interface OutputViewerProps {
  terminalId: string
  onClose: () => void
}

const MAX_CLIENT_OUTPUT_CHUNKS = 8
const MAX_CLIENT_OUTPUT_CHARACTERS = 2 * 1024 * 1024

type OutputMode = 'last' | 'full'
type OutputViewState = {
  chunks: TerminalOutputResponse[]
  availability: 'available' | 'unavailable' | 'error'
  loading: boolean
  loadingOlder: boolean
  olderLoadFailed: boolean
  discardedNewer: boolean
}

const emptyOutputState = (): OutputViewState => ({
  chunks: [],
  availability: 'available',
  loading: true,
  loadingOlder: false,
  olderLoadFailed: false,
  discardedNewer: false,
})

function boundedChunkWindow(chunks: TerminalOutputResponse[]): { chunks: TerminalOutputResponse[]; trimmed: boolean } {
  const retained = [...chunks]
  let characters = retained.reduce((total, chunk) => total + chunk.output.length, 0)
  let trimmed = false
  while (retained.length > 1 && (retained.length > MAX_CLIENT_OUTPUT_CHUNKS || characters > MAX_CLIENT_OUTPUT_CHARACTERS)) {
    const removed = retained.pop()
    characters -= removed?.output.length || 0
    trimmed = true
  }
  return { chunks: retained, trimmed }
}

function formatBytes(value?: number | null): string {
  if (value === undefined || value === null) return '—'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`
  return `${(value / (1024 * 1024)).toFixed(1)} MiB`
}

export function OutputViewer({ terminalId, onClose }: OutputViewerProps) {
  const { t } = useI18n()
  const [mode, setMode] = useState<OutputMode>('last')
  const [views, setViews] = useState<Record<OutputMode, OutputViewState>>({
    last: emptyOutputState(),
    full: emptyOutputState(),
  })
  const [copied, setCopied] = useState(false)
  const [fullscreen, setFullscreen] = useState(false)
  const outputRef = useRef<HTMLPreElement>(null)
  const dialogRef = useRef<HTMLDivElement>(null)
  const returnFocusRef = useRef<HTMLElement | null>(null)
  const scrollPositionRef = useRef<number | null>(null)
  const scrollIntentRef = useRef<
    | { kind: 'bottom' }
    | { kind: 'preserve'; height: number; top: number }
    | null
  >(null)
  const requestRef = useRef<AbortController | null>(null)
  const requestGenerationRef = useRef(0)
  const userScrolledUpRef = useRef(false)

  const view = views[mode]
  const { chunks, availability, loading, loadingOlder, olderLoadFailed, discardedNewer } = view
  const output = useMemo(() => chunks.map(chunk => chunk.output).join(''), [chunks])
  const oldestChunk = chunks[0]

  const updateView = (target: OutputMode, update: (current: OutputViewState) => OutputViewState) => {
    setViews(current => ({ ...current, [target]: update(current[target]) }))
  }

  const fetchOutput = async (m: OutputMode) => {
    requestRef.current?.abort()
    const controller = new AbortController()
    const generation = ++requestGenerationRef.current
    requestRef.current = controller
    updateView(m, () => emptyOutputState())
    try {
      const data = await api.getTerminalOutput(terminalId, m, undefined, controller.signal)
      if (controller.signal.aborted || generation !== requestGenerationRef.current) return
      userScrolledUpRef.current = false
      scrollIntentRef.current = { kind: 'bottom' }
      updateView(m, current => ({
        ...current,
        chunks: [{ ...data, output: data.output || '' }],
        availability: data.availability || 'available',
        loading: false,
      }))
    } catch {
      if (controller.signal.aborted || generation !== requestGenerationRef.current) return
      updateView(m, current => ({ ...current, chunks: [], availability: 'error', loading: false }))
    } finally {
      if (requestRef.current === controller) requestRef.current = null
    }
  }

  useEffect(() => {
    fetchOutput(mode)
    return () => requestRef.current?.abort()
  }, [mode, terminalId])

  useEffect(() => {
    returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    dialogRef.current?.focus()
    return () => returnFocusRef.current?.focus()
  }, [])

  useLayoutEffect(() => {
    const surface = outputRef.current
    const intent = scrollIntentRef.current
    if (!surface || !intent) return
    if (intent.kind === 'bottom') {
      if (!userScrolledUpRef.current) surface.scrollTop = surface.scrollHeight
    } else {
      surface.scrollTop = intent.top + (surface.scrollHeight - intent.height)
    }
    scrollIntentRef.current = null
  }, [chunks])

  useLayoutEffect(() => {
    if (scrollPositionRef.current !== null && outputRef.current) {
      outputRef.current.scrollTop = scrollPositionRef.current
      scrollPositionRef.current = null
    }
  }, [fullscreen])

  const toggleFullscreen = () => {
    scrollPositionRef.current = outputRef.current?.scrollTop ?? null
    setFullscreen(value => !value)
  }

  const closeViewer = () => {
    requestRef.current?.abort()
    onClose()
  }

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.preventDefault()
      if (fullscreen) toggleFullscreen()
      else closeViewer()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [fullscreen, onClose])

  const handleCopy = async () => {
    const clean = stripAnsi(output)
    try {
      await navigator.clipboard.writeText(clean)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // clipboard API may not be available
    }
  }

  const handleRefresh = () => {
    fetchOutput(mode)
  }

  const handleLoadOlder = async () => {
    if (mode !== 'full' || loadingOlder || requestRef.current || !oldestChunk?.has_older || !oldestChunk.cursor) return
    const controller = new AbortController()
    const generation = ++requestGenerationRef.current
    requestRef.current = controller
    updateView('full', current => ({ ...current, loadingOlder: true, olderLoadFailed: false }))
    const surface = outputRef.current
    scrollIntentRef.current = surface
      ? { kind: 'preserve', height: surface.scrollHeight, top: surface.scrollTop }
      : null
    try {
      const data = await api.getTerminalOutput(terminalId, 'full', oldestChunk.cursor, controller.signal)
      if (controller.signal.aborted || generation !== requestGenerationRef.current) return
      const bounded = boundedChunkWindow([{ ...data, output: data.output || '' }, ...chunks])
      updateView('full', current => ({
        ...current,
        chunks: bounded.chunks,
        discardedNewer: current.discardedNewer || bounded.trimmed,
        availability: data.availability || 'available',
        loadingOlder: false,
      }))
    } catch {
      if (!controller.signal.aborted && generation === requestGenerationRef.current) {
        scrollIntentRef.current = null
        updateView('full', current => ({ ...current, olderLoadFailed: true, loadingOlder: false }))
      }
    } finally {
      if (requestRef.current === controller) requestRef.current = null
    }
  }

  const switchMode = (nextMode: OutputMode) => {
    if (nextMode === mode) return
    requestRef.current?.abort()
    requestGenerationRef.current += 1
    scrollIntentRef.current = { kind: 'bottom' }
    userScrolledUpRef.current = false
    updateView(nextMode, () => emptyOutputState())
    setMode(nextMode)
  }

  const trackUserScroll = () => {
    const surface = outputRef.current
    if (!surface) return
    userScrolledUpRef.current = surface.scrollHeight - surface.scrollTop - surface.clientHeight > 24
  }

  const cleanOutput = stripAnsi(output)

  return (
    <div className={`fixed inset-0 z-[60] flex items-center justify-center ${fullscreen ? 'p-0' : 'p-3 sm:p-4'}`}>
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={closeViewer} />

      {/* Modal */}
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={t('output.dialog')}
        tabIndex={-1}
        className={`min-w-0 bg-gray-900 border border-gray-700/50 shadow-2xl w-full overflow-hidden animate-in fade-in zoom-in-95 flex flex-col min-h-0 outline-none ${
          fullscreen
            ? 'fixed inset-0 h-[100dvh] w-screen max-w-none rounded-none border-0'
            : 'relative h-[calc(100dvh-1.5rem)] sm:h-[80dvh] max-w-[800px] rounded-xl sm:rounded-2xl'
        }`}
      >
        {/* Header */}
        <div className="flex items-center justify-between gap-3 px-4 sm:px-6 py-3 sm:py-4 border-b border-gray-700/30 shrink-0">
          <div className="flex items-center gap-2 sm:gap-3 min-w-0">
            <FileText size={16} className="text-emerald-400" />
            <span className="text-sm font-semibold text-white whitespace-nowrap">{t('output.title')}</span>
            <span className="hidden sm:inline text-xs text-gray-500 font-mono bg-gray-800 px-2 py-0.5 rounded truncate max-w-56">{terminalId}</span>
          </div>
          <div className="flex items-center gap-1 shrink-0">
            {/* Copy button */}
            <button
              onClick={handleCopy}
              disabled={!cleanOutput}
              className="min-w-11 min-h-11 inline-flex items-center justify-center text-gray-400 hover:text-white disabled:opacity-30 transition-colors rounded-lg hover:bg-gray-800"
              title={t('output.copy')}
            >
              {copied ? <Check size={16} className="text-emerald-400" /> : <Copy size={16} />}
            </button>
            {copied && <span className="hidden sm:inline text-xs text-emerald-400">{t('common.copied')}</span>}
            {/* Refresh button */}
            <button
              onClick={handleRefresh}
              disabled={loading}
              className="min-w-11 min-h-11 inline-flex items-center justify-center text-gray-400 hover:text-white disabled:opacity-30 transition-colors rounded-lg hover:bg-gray-800"
              title={t('output.refresh')}
            >
              <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
            </button>
            <button
              onClick={toggleFullscreen}
              className="min-w-11 min-h-11 inline-flex items-center justify-center text-gray-400 hover:text-white transition-colors rounded-lg hover:bg-gray-800"
              title={fullscreen ? t('common.exitFullscreen') : t('common.fullscreen')}
              aria-label={fullscreen ? t('common.exitFullscreen') : t('common.fullscreen')}
              aria-pressed={fullscreen}
            >
              {fullscreen ? <Minimize2 size={17} /> : <Maximize2 size={17} />}
            </button>
            {/* Close button */}
            <button
              onClick={closeViewer}
              className="min-w-11 min-h-11 inline-flex items-center justify-center text-gray-500 hover:text-white transition-colors rounded-lg hover:bg-gray-800"
              title={t('common.close')}
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Tab Toggle */}
        <div className="flex items-center gap-2 px-4 sm:px-6 py-3 border-b border-gray-700/30 shrink-0 overflow-x-auto">
          <button
            onClick={() => switchMode('last')}
            className={`shrink-0 min-h-10 px-3 py-1.5 text-xs font-medium rounded-full transition-colors ${
              mode === 'last'
                ? 'bg-emerald-600 text-white'
                : 'bg-gray-800 text-gray-400 hover:text-gray-200 hover:bg-gray-700'
            }`}
          >
            {t('output.last')}
          </button>
          <button
            onClick={() => switchMode('full')}
            className={`shrink-0 min-h-10 px-3 py-1.5 text-xs font-medium rounded-full transition-colors ${
              mode === 'full'
                ? 'bg-emerald-600 text-white'
                : 'bg-gray-800 text-gray-400 hover:text-gray-200 hover:bg-gray-700'
            }`}
          >
            {t('output.full')}
          </button>
        </div>

        {/* Output Area */}
        <div className="flex min-h-0 min-w-0 w-full max-w-full flex-1 overflow-hidden px-3 py-3 sm:px-4">
          {loading ? (
            <ModalLoadingBody label={t('output.loading')} />
          ) : availability === 'unavailable' ? (
            <div className="flex h-full min-h-[200px] items-center justify-center rounded-lg border border-dashed border-gray-700 p-6 text-center">
              <div><p className="text-sm text-gray-300">{t('output.unavailable')}</p><p className="mt-1 max-w-md text-xs leading-5 text-gray-500">{t('output.unavailableHelp')}</p></div>
            </div>
          ) : availability === 'error' ? (
            <div className="flex h-full min-h-[200px] items-center justify-center"><p className="text-sm text-red-300">{t('output.loadFailed')}</p></div>
          ) : cleanOutput || (mode === 'full' && oldestChunk?.has_older) ? (
            <div className="flex h-full min-h-0 min-w-0 w-full max-w-full flex-1 flex-col gap-2">
              {mode === 'full' && (
                <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-gray-500">
                  <div className="flex items-center gap-2">
                    {oldestChunk?.has_older && (
                      <button
                        onClick={handleLoadOlder}
                        disabled={loadingOlder}
                        className="min-h-9 rounded-lg bg-gray-800 px-3 text-gray-300 hover:bg-gray-700 disabled:opacity-50"
                      >
                        {loadingOlder ? t('output.loadingOlder') : t('output.loadOlder')}
                      </button>
                    )}
                    {discardedNewer && (
                      <button
                        onClick={() => fetchOutput('full')}
                        className="min-h-9 rounded-lg px-3 text-emerald-400 hover:bg-gray-800"
                      >
                        {t('output.returnLatest')}
                      </button>
                    )}
                  </div>
                  <span data-testid="terminal-output-range">
                    {t('output.loadedRange', {
                      start: formatBytes(oldestChunk?.range_start),
                      end: formatBytes(chunks[chunks.length - 1]?.range_end),
                      total: formatBytes(oldestChunk?.snapshot_size),
                    })}
                  </span>
                </div>
              )}
              {olderLoadFailed && (
                <p className="text-xs text-red-300">{t('output.loadOlderFailed')}</p>
              )}
              <pre
                ref={outputRef}
                data-testid="terminal-output-surface"
                data-output-mode={mode}
                onScroll={trackUserScroll}
                className="block h-full min-h-0 min-w-0 w-full max-w-full flex-1 self-stretch overflow-x-hidden overflow-y-auto rounded-lg bg-gray-950 p-3 font-mono text-xs text-gray-300 whitespace-pre-wrap [overflow-wrap:anywhere] [word-break:break-word] sm:p-4 sm:text-sm"
              >
                {cleanOutput}
              </pre>
            </div>
          ) : (
            <div className="flex items-center justify-center h-full min-h-[200px]">
              <p className="text-gray-500 text-sm">{t('output.empty')}</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
