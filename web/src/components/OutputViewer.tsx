import { useState, useEffect, useLayoutEffect, useRef } from 'react'
import { api } from '../api'
import { X, RefreshCw, Copy, Check, FileText, Maximize2, Minimize2 } from 'lucide-react'
import { ModalLoadingBody } from './ModalLoadingBody'

function stripAnsi(text: string): string {
  return text.replace(/\x1b\[[0-9;]*[a-zA-Z]/g, '').replace(/\x1b\][^\x07]*\x07/g, '')
}

interface OutputViewerProps {
  terminalId: string
  onClose: () => void
}

export function OutputViewer({ terminalId, onClose }: OutputViewerProps) {
  const [mode, setMode] = useState<'last' | 'full'>('last')
  const [output, setOutput] = useState('')
  const [loading, setLoading] = useState(true)
  const [copied, setCopied] = useState(false)
  const [fullscreen, setFullscreen] = useState(false)
  const outputRef = useRef<HTMLPreElement>(null)
  const dialogRef = useRef<HTMLDivElement>(null)
  const returnFocusRef = useRef<HTMLElement | null>(null)
  const scrollPositionRef = useRef<number | null>(null)

  const fetchOutput = async (m: 'last' | 'full') => {
    setLoading(true)
    try {
      const data = await api.getTerminalOutput(terminalId, m)
      setOutput(data.output || '')
    } catch {
      setOutput('')
    }
    setLoading(false)
  }

  useEffect(() => {
    fetchOutput(mode)
  }, [mode, terminalId])

  useEffect(() => {
    returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    dialogRef.current?.focus()
    return () => returnFocusRef.current?.focus()
  }, [])

  // Auto-scroll to bottom on full output mode
  useEffect(() => {
    if (mode === 'full' && outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight
    }
  }, [output, mode])

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

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.preventDefault()
      if (fullscreen) toggleFullscreen()
      else onClose()
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

  const cleanOutput = stripAnsi(output)

  return (
    <div className={`fixed inset-0 z-[60] flex items-center justify-center ${fullscreen ? 'p-0' : 'p-3 sm:p-4'}`}>
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />

      {/* Modal */}
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="Terminal output"
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
            <span className="text-sm font-semibold text-white whitespace-nowrap">Terminal Output</span>
            <span className="hidden sm:inline text-xs text-gray-500 font-mono bg-gray-800 px-2 py-0.5 rounded truncate max-w-56">{terminalId}</span>
          </div>
          <div className="flex items-center gap-1 shrink-0">
            {/* Copy button */}
            <button
              onClick={handleCopy}
              disabled={!cleanOutput}
              className="min-w-11 min-h-11 inline-flex items-center justify-center text-gray-400 hover:text-white disabled:opacity-30 transition-colors rounded-lg hover:bg-gray-800"
              title="Copy to clipboard"
            >
              {copied ? <Check size={16} className="text-emerald-400" /> : <Copy size={16} />}
            </button>
            {copied && <span className="hidden sm:inline text-xs text-emerald-400">Copied!</span>}
            {/* Refresh button */}
            <button
              onClick={handleRefresh}
              disabled={loading}
              className="min-w-11 min-h-11 inline-flex items-center justify-center text-gray-400 hover:text-white disabled:opacity-30 transition-colors rounded-lg hover:bg-gray-800"
              title="Refresh output"
            >
              <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
            </button>
            <button
              onClick={toggleFullscreen}
              className="min-w-11 min-h-11 inline-flex items-center justify-center text-gray-400 hover:text-white transition-colors rounded-lg hover:bg-gray-800"
              title={fullscreen ? 'Exit fullscreen' : 'Fullscreen'}
              aria-label={fullscreen ? 'Exit fullscreen' : 'Fullscreen'}
              aria-pressed={fullscreen}
            >
              {fullscreen ? <Minimize2 size={17} /> : <Maximize2 size={17} />}
            </button>
            {/* Close button */}
            <button
              onClick={onClose}
              className="min-w-11 min-h-11 inline-flex items-center justify-center text-gray-500 hover:text-white transition-colors rounded-lg hover:bg-gray-800"
              title="Close"
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Tab Toggle */}
        <div className="flex items-center gap-2 px-4 sm:px-6 py-3 border-b border-gray-700/30 shrink-0 overflow-x-auto">
          <button
            onClick={() => setMode('last')}
            className={`shrink-0 min-h-10 px-3 py-1.5 text-xs font-medium rounded-full transition-colors ${
              mode === 'last'
                ? 'bg-emerald-600 text-white'
                : 'bg-gray-800 text-gray-400 hover:text-gray-200 hover:bg-gray-700'
            }`}
          >
            Last Response
          </button>
          <button
            onClick={() => setMode('full')}
            className={`shrink-0 min-h-10 px-3 py-1.5 text-xs font-medium rounded-full transition-colors ${
              mode === 'full'
                ? 'bg-emerald-600 text-white'
                : 'bg-gray-800 text-gray-400 hover:text-gray-200 hover:bg-gray-700'
            }`}
          >
            Full Output
          </button>
        </div>

        {/* Output Area */}
        <div className="flex min-h-0 min-w-0 w-full max-w-full flex-1 overflow-hidden px-3 py-3 sm:px-4">
          {loading ? (
            <ModalLoadingBody label="Loading terminal output" />
          ) : cleanOutput ? (
            <pre
              ref={outputRef}
              data-testid="terminal-output-surface"
              className="block h-full min-h-0 min-w-0 w-full max-w-full flex-1 self-stretch overflow-x-hidden overflow-y-auto rounded-lg bg-gray-950 p-3 font-mono text-xs text-gray-300 whitespace-pre-wrap [overflow-wrap:anywhere] [word-break:break-word] sm:p-4 sm:text-sm"
            >
              {cleanOutput}
            </pre>
          ) : (
            <div className="flex items-center justify-center h-full min-h-[200px]">
              <p className="text-gray-500 text-sm">No output available</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
