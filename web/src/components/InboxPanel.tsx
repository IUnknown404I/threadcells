import { useState, useEffect, useLayoutEffect, useRef } from 'react'
import { api, DelegationResult, InboxMessage } from '../api'
import { useStore } from '../store'
import { X, Send, Mail, Loader2, Maximize2, Minimize2 } from 'lucide-react'

interface InboxPanelProps {
  terminalId: string
  onClose: () => void
}

type StatusFilter = 'all' | 'pending' | 'delivered' | 'failed'

const STATUS_FILTERS: { key: StatusFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'pending', label: 'Pending' },
  { key: 'delivered', label: 'Delivered' },
  { key: 'failed', label: 'Failed' },
]

const OWNER_MESSAGE_PREVIEW_LENGTH = 1028

export function OwnerMessageBody({ message }: { message: string }) {
  const [expanded, setExpanded] = useState(false)
  const characters = Array.from(message)
  if (characters.length <= OWNER_MESSAGE_PREVIEW_LENGTH) return <p className="text-sm text-gray-200 whitespace-pre-wrap break-words">{message}</p>
  return <div className="text-sm text-gray-200 whitespace-pre-wrap break-words">{expanded ? message : characters.slice(0, OWNER_MESSAGE_PREVIEW_LENGTH).join('')}<button type="button" onClick={() => setExpanded(value => !value)} className="ml-1 text-sky-300 hover:text-sky-100">{expanded ? 'Show less' : 'Show more…'}</button></div>
}

function formatRelativeTime(dateStr: string | null): string {
  if (!dateStr) return ''
  const now = Date.now()
  const then = new Date(dateStr).getTime()
  const diffSec = Math.floor((now - then) / 1000)
  if (diffSec < 0) return 'just now'
  if (diffSec < 60) return `${diffSec}s ago`
  const diffMin = Math.floor(diffSec / 60)
  if (diffMin < 60) return `${diffMin}m ago`
  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 24) return `${diffHr}h ago`
  const diffDay = Math.floor(diffHr / 24)
  return `${diffDay}d ago`
}

function MessageStatusBadge({ status }: { status: InboxMessage['status'] }) {
  const config = {
    delivered: { bg: 'bg-emerald-400/10', text: 'text-emerald-400', label: 'Delivered' },
    pending: { bg: 'bg-amber-400/10', text: 'text-amber-400', label: 'Pending' },
    failed: { bg: 'bg-red-400/10', text: 'text-red-400', label: 'Failed' },
  }
  const c = config[status] || config.pending
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium ${c.bg} ${c.text}`}>
      {c.label}
    </span>
  )
}

export function resultLifecycleLabel(status: string, delivery?: string): string {
  if (status === 'awaiting' && delivery?.includes('awaiting_result')) return 'Waiting for result'
  if (status === 'awaiting') return 'Waiting for result'
  if (status === 'complete' && delivery?.includes('failed')) return 'Delivery failed'
  if (status === 'complete' && delivery?.includes('acknowledged')) return 'Incorporated / Acknowledged'
  if (status === 'complete' && delivery?.includes('delivered')) return 'Delivered'
  if (status === 'complete' && delivery?.includes('queued')) return 'Result ready'
  if (status === 'incomplete') return 'Incomplete'
  if (status === 'cancelled') return 'Cancelled'
  return status === 'complete' ? 'Complete' : 'Waiting for result'
}

export function InboxPanel({ terminalId, onClose }: InboxPanelProps) {
  const showSnackbar = useStore(state => state.showSnackbar)
  const [messages, setMessages] = useState<InboxMessage[]>([])
  const [resultHistory, setResultHistory] = useState<DelegationResult[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<StatusFilter>('all')
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const [fullscreen, setFullscreen] = useState(false)
  const [expandedResults, setExpandedResults] = useState<Record<string, boolean>>({})
  const [resultCache, setResultCache] = useState<Record<string, DelegationResult>>({})
  const [resultLoading, setResultLoading] = useState<Record<string, boolean>>({})
  const [resultErrors, setResultErrors] = useState<Record<string, boolean>>({})
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const messagesRef = useRef<HTMLDivElement>(null)
  const scrollPositionRef = useRef<number | null>(null)
  const pinnedToBottomRef = useRef(true)
  const initialScrollRef = useRef(true)
  const messageIdsRef = useRef('')

  const fetchMessages = async () => {
    try {
      const status = filter === 'all' ? undefined : filter
      const [data, history] = await Promise.all([
        api.getInboxMessages(terminalId, 50, status),
        api.listDelegationResults({ terminalId }),
      ])
      const ids = data.map(message => message.id).join(',')
      const newMessages = ids !== messageIdsRef.current
      messageIdsRef.current = ids
      setMessages(previous => JSON.stringify(previous) === JSON.stringify(data) ? previous : data)
      setResultHistory(history)
      if (newMessages && pinnedToBottomRef.current) {
        const scrollLatest = () => messagesEndRef.current?.scrollIntoView?.()
        if (typeof requestAnimationFrame === 'function') requestAnimationFrame(scrollLatest)
        else scrollLatest()
      }
    } catch {
      // silently fail — will retry
    } finally {
      setLoading(false)
    }
  }

  const toggleFullscreen = () => {
    scrollPositionRef.current = messagesRef.current?.scrollTop ?? null
    setFullscreen(value => !value)
  }

  useEffect(() => {
    setLoading(true)
    fetchMessages()
    const interval = setInterval(fetchMessages, 5000)
    return () => clearInterval(interval)
  }, [terminalId, filter])

  useEffect(() => { if (initialScrollRef.current && !loading) { initialScrollRef.current = false; messagesEndRef.current?.scrollIntoView?.() } }, [loading])

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

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  useLayoutEffect(() => {
    if (scrollPositionRef.current !== null && messagesRef.current) {
      messagesRef.current.scrollTop = scrollPositionRef.current
      scrollPositionRef.current = null
    }
  }, [fullscreen])

  const handleSend = async () => {
    const text = draft.trim()
    if (!text || sending) return
    setSending(true)
    try {
      await api.sendInboxMessage(terminalId, 'ui', text)
      setDraft('')
      await fetchMessages()
    } catch (error: any) {
      showSnackbar({ type: 'error', message: error.message || 'Unable to send the Inbox message. Try again.' })
    }
    setSending(false)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault()
      handleSend()
    }
  }

  const isReceiver = (msg: InboxMessage) => msg.receiver_id === terminalId
  const toggleResult = async (id: string) => {
    if (expandedResults[id]) { setExpandedResults(current => ({ ...current, [id]: false })); return }
    setExpandedResults(current => ({ ...current, [id]: true }))
    if (resultCache[id] || resultLoading[id]) return
    setResultLoading(current => ({ ...current, [id]: true }))
    try {
      const result = await api.getDelegationResult(id)
      setResultCache(current => ({ ...current, [id]: result }))
    }
    catch { setResultErrors(current => ({ ...current, [id]: true })) }
    finally { setResultLoading(current => ({ ...current, [id]: false })) }
  }

  return (
    <div className={`fixed inset-0 z-[60] flex items-center justify-center ${fullscreen ? 'p-0' : 'p-3 sm:p-4'}`}>
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />

      {/* Modal */}
      <div role="dialog" aria-modal="true" aria-label="Agent inbox" className={`bg-gray-900 border border-gray-700/50 shadow-2xl w-full min-h-0 overflow-hidden flex flex-col ${fullscreen ? 'fixed inset-0 h-[100dvh] w-screen max-w-none rounded-none border-0' : 'relative h-[calc(100dvh-1.5rem)] sm:h-[80dvh] max-w-[800px] rounded-xl sm:rounded-2xl'}`}>
        {/* Header */}
        <div className="flex items-center justify-between gap-3 px-4 sm:px-5 py-3 sm:py-4 border-b border-gray-700/50 shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            <div className="w-8 h-8 rounded-lg bg-emerald-900/50 flex items-center justify-center">
              <Mail size={16} className="text-emerald-400" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-white">Agent Inbox</h3>
              <p className="text-[11px] text-gray-500 truncate">Messages between agents in this session <span className="font-mono">({terminalId})</span></p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <button onClick={toggleFullscreen} className="min-w-11 min-h-11 inline-flex items-center justify-center rounded-lg text-gray-400 transition-colors hover:bg-gray-800 hover:text-white" title={fullscreen ? 'Exit fullscreen' : 'Fullscreen'} aria-label={fullscreen ? 'Exit fullscreen' : 'Fullscreen'} aria-pressed={fullscreen}>
              {fullscreen ? <Minimize2 size={17} /> : <Maximize2 size={17} />}
            </button>
            <button
              onClick={onClose}
              className="min-w-11 min-h-11 inline-flex items-center justify-center text-gray-500 hover:text-white transition-colors rounded-lg hover:bg-gray-800"
              title="Close"
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Filter Tabs */}
        <div className="px-4 sm:px-5 py-3 border-b border-gray-700/30 shrink-0 overflow-x-auto">
          <div className="flex gap-2">
            {STATUS_FILTERS.map(f => (
              <button
                key={f.key}
                onClick={() => setFilter(f.key)}
                className={`shrink-0 min-h-10 px-3 py-1.5 text-xs font-medium rounded-full whitespace-nowrap transition-colors ${
                  filter === f.key
                    ? 'bg-emerald-600 text-white'
                    : 'bg-gray-800 text-gray-400 hover:text-white hover:bg-gray-700'
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>

        {resultHistory.length > 0 && (
          <div className="px-5 py-2 border-b border-gray-700/30 shrink-0">
            <p className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">Result History</p>
            <div className="flex gap-2 overflow-x-auto">
              {resultHistory.slice(0, 6).map(result => (
                <button
                  key={result.id}
                  onClick={() => api.getDelegationResult(result.id).then(value => navigator.clipboard?.writeText(JSON.stringify(value, null, 2)))}
                  className="shrink-0 rounded border border-sky-800/60 bg-sky-950/20 px-2 py-1 text-[10px] text-sky-300 hover:text-sky-100"
                  title="Copy authoritative durable result JSON"
                >
                  {resultLifecycleLabel(result.status, result.delivery_status)} · {result.id.slice(0, 8)}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Messages */}
        <div ref={messagesRef} data-testid="inbox-message-list" onScroll={() => { const node = messagesRef.current; if (node) pinnedToBottomRef.current = node.scrollHeight - node.scrollTop - node.clientHeight < 24 }} className="flex-1 overflow-y-auto px-4 sm:px-5 py-4 space-y-3 min-h-[200px]">
          {loading && messages.length === 0 ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 size={20} className="animate-spin text-gray-500" />
            </div>
          ) : messages.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-gray-500">
              <Mail size={32} className="mb-3 opacity-40" />
              <p className="text-sm">No messages yet</p>
              <p className="text-xs text-gray-600 mt-1">Messages appear here when agents communicate via handoff, assign, or send_message. You can also send a message manually below.</p>
            </div>
          ) : (
            messages.map(msg => {
              const incoming = isReceiver(msg)
              return (
                <div
                  key={msg.id}
                  className={`flex flex-col ${incoming ? 'items-start' : 'items-end'}`}
                >
                  <div
                    className={`max-w-[85%] rounded-xl px-3.5 py-2.5 ${
                      incoming
                        ? 'bg-gray-800 border border-gray-700/40'
                        : 'bg-emerald-900/30 border border-emerald-700/30'
                    }`}
                  >
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-[10px] font-mono text-gray-500">
                        {incoming ? msg.sender_id.slice(0, 8) : msg.receiver_id.slice(0, 8)}
                      </span>
                      <MessageStatusBadge status={msg.status} />
                      {msg.result_id && <span className="text-[10px] text-sky-400">Result</span>}
                      {msg.superseded_at && <span className="text-[10px] text-gray-500">authoritative</span>}
                    </div>
                    {msg.result_id ? (
                      <button
                        onClick={() => void toggleResult(msg.result_id!)}
                        className="text-sm text-sky-300 hover:text-sky-200 underline"
                        title="Copy authoritative structured result JSON"
                      >
                        Open durable result {msg.result_id.slice(0, 8)}
                      </button>
                    ) : <OwnerMessageBody message={msg.message} />}
                    {msg.result_id && expandedResults[msg.result_id] && (
                      <div className="mt-3 border-t border-sky-800/40 pt-3 text-sm text-gray-200">
                        {resultLoading[msg.result_id] && <p className="text-xs text-gray-400">Loading durable result…</p>}
                        {resultErrors[msg.result_id] && <p className="text-xs text-red-300">Unable to load durable result.</p>}
                        {resultCache[msg.result_id] && <>
                          <p className="mb-2 text-[10px] font-mono text-sky-300">{resultCache[msg.result_id].id} · {resultLifecycleLabel(resultCache[msg.result_id].status, resultCache[msg.result_id].delivery_status)}</p>
                          {resultCache[msg.result_id].document?.summary && <p className="mb-2 font-medium">{resultCache[msg.result_id].document?.summary}</p>}
                          <div className="whitespace-pre-wrap break-words text-gray-300">{resultCache[msg.result_id].document?.body_markdown || 'No result body available.'}</div>
                        </>}
                        <button onClick={() => setExpandedResults(current => ({ ...current, [msg.result_id!]: false }))} className="mt-3 text-xs text-sky-300 hover:text-sky-100">Hide text</button>
                      </div>
                    )}
                    {msg.created_at && (
                      <p className="text-[10px] text-gray-600 mt-1">{formatRelativeTime(msg.created_at)}</p>
                    )}
                  </div>
                </div>
              )
            })
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Send Form */}
        <div className="px-4 sm:px-5 py-4 border-t border-gray-700/50 shrink-0">
          <div className="flex flex-col gap-2">
            <textarea
              ref={inputRef}
              aria-label="Inbox draft"
              value={draft}
              onChange={e => setDraft(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Write a message for this terminal…"
              className="min-h-20 w-full resize-y bg-gray-800 border border-gray-700 text-gray-200 text-sm rounded-lg px-3 py-2.5 focus:border-emerald-500 focus:outline-none placeholder-gray-600"
            />
            <div className="flex items-center justify-between gap-3">
              <span className="text-xs text-gray-500">Enter for newline · Ctrl/Cmd+Enter to send</span>
              <button
                onClick={handleSend}
                disabled={!draft.trim() || sending}
                className="min-h-11 justify-center flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white text-sm font-medium px-4 py-2.5 rounded-lg transition-colors"
              >
                {sending ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
                Send
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
