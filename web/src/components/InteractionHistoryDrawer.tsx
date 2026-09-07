import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { AlertTriangle, ArrowRight, ChevronDown, ChevronUp, CircleCheck, History as HistoryIcon, ListTodo, Loader2, RefreshCw, X } from 'lucide-react'
import { api, type DelegationResult, type InteractionItem, type InteractionMode } from '../api'
import { useI18n, type AppLocale, type TranslationKey } from '../i18n'
import { sessionDisplayName } from '../sessionDisplayName'

type InteractionHistoryDrawerProps = {
  sessionId: string
  sessionName: string
  terminalId?: string
  initialMode?: InteractionMode
  onClose: () => void
}

const TYPE_KEYS: Record<InteractionItem['interaction_type'], TranslationKey> = {
  workflow_turn: 'interactions.type.workflowTurn',
  workflow: 'interactions.type.workflow',
  effect: 'interactions.type.effect',
  delegation: 'interactions.type.delegation',
  inbox: 'interactions.type.inbox',
  recovery: 'interactions.type.recovery',
  runtime_authority: 'interactions.type.runtimeAuthority',
}

const SOURCE_KEYS: Record<string, TranslationKey> = {
  operator: 'interactions.source.operator',
  inbox: 'interactions.source.inbox',
  agent: 'interactions.source.agent',
  child_result: 'interactions.source.childResult',
  recovery: 'interactions.source.recovery',
  system: 'interactions.source.system',
}

const WAIT_KEYS: Record<string, TranslationKey> = {
  current_provider_turn: 'interactions.wait.providerTurn',
  provider_capacity: 'interactions.wait.providerCapacity',
  reconnect: 'interactions.wait.reconnect',
  child_result: 'interactions.wait.childResult',
  owner_gate: 'interactions.wait.ownerGate',
  delivery: 'interactions.wait.delivery',
  acknowledgement: 'interactions.wait.acknowledgement',
  writer_recovery_authority: 'interactions.wait.writerRecovery',
  resource_health: 'interactions.wait.resourceHealth',
  admission: 'interactions.wait.admission',
  claimed_effect: 'interactions.wait.claimedEffect',
  indeterminate_effect: 'interactions.wait.indeterminateEffect',
  workflow_continuation: 'interactions.wait.workflowContinuation',
}

const DISPOSITION_KEYS: Record<string, TranslationKey> = {
  processed: 'interactions.disposition.processed',
  completed: 'interactions.disposition.completed',
  delivered: 'interactions.disposition.delivered',
  acknowledged: 'interactions.disposition.acknowledged',
  failed: 'interactions.disposition.failed',
  cancelled: 'interactions.disposition.cancelled',
  superseded: 'interactions.disposition.superseded',
  operator_retired_unknown_outcome: 'interactions.disposition.operatorRetiredUnknown',
}

const STATE_KEYS: Record<string, TranslationKey> = {
  queued: 'interactions.state.queued',
  claimed: 'interactions.state.claimed',
  sent: 'interactions.state.sent',
  executing: 'interactions.state.executing',
  owner_gate: 'interactions.state.ownerGate',
  open: 'interactions.state.open',
  pending: 'interactions.state.pending',
  delivered: 'interactions.state.delivered',
  failed: 'interactions.state.failed',
  admitted: 'interactions.state.admitted',
  dispatching: 'interactions.state.dispatching',
  fenced: 'interactions.state.fenced',
  recovery_required: 'interactions.state.recoveryRequired',
  awaiting_result: 'interactions.state.awaitingResult',
  handoff_awaiting_result: 'interactions.state.awaitingResult',
  handoff_recovery_awaiting_result: 'interactions.state.recoveryRequired',
  result_queued: 'interactions.state.resultQueued',
  handoff_result_queued: 'interactions.state.resultQueued',
  result_delivered: 'interactions.state.resultDelivered',
  handoff_result_delivered: 'interactions.state.resultDelivered',
  result_failed: 'interactions.state.deliveryFailed',
  handoff_result_failed: 'interactions.state.deliveryFailed',
  handoff_direct_result_claimed: 'interactions.state.resultClaimed',
  complete: 'interactions.state.complete',
  incomplete: 'interactions.state.incomplete',
  cancelled: 'interactions.state.cancelled',
  superseded: 'interactions.state.superseded',
  terminal: 'interactions.state.terminal',
  result_acknowledged: 'interactions.state.resultAcknowledged',
  handoff_result_acknowledged: 'interactions.state.resultAcknowledged',
  result_superseded: 'interactions.state.superseded',
  operator_retired_indeterminate: 'interactions.state.operatorRetiredIndeterminate',
}

const TASK_KEYS: Record<string, TranslationKey> = {
  external_input: 'interactions.task.externalInput',
  inbox_message: 'interactions.task.inboxMessage',
  message: 'interactions.task.message',
  delegation_result_notice: 'interactions.task.resultNotice',
  assigned_result: 'interactions.task.assignedResult',
  handoff_result: 'interactions.task.handoffResult',
  handoff_recovery: 'interactions.task.handoffRecovery',
  handoff_recovery_continuation: 'interactions.task.handoffRecovery',
  execution_resume: 'interactions.task.executionResume',
  open_final: 'interactions.task.workflowContinuation',
  workflow: 'interactions.task.workflow',
  assign: 'interactions.task.assign',
  handoff: 'interactions.task.handoff',
  recovery_takeover: 'interactions.task.recoveryTakeover',
  runtime_recovery: 'interactions.task.runtimeRecovery',
  provider_execution: 'interactions.task.providerExecution',
  writer_authority: 'interactions.task.writerAuthority',
}

function translatedValue(value: string | null, keys: Record<string, TranslationKey>, t: (key: TranslationKey) => string) {
  if (!value) return null
  return keys[value] ? t(keys[value]) : value.replace(/_/g, ' ')
}

function formatTimestamp(value: string | null, locale: AppLocale) {
  if (!value) return '—'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(parsed)
}

function itemTone(item: InteractionItem) {
  const state = item.final_disposition || item.queue.state || ''
  if (state.includes('failed') || state === 'cancelled') return 'border-red-800/60 bg-red-950/10'
  if (state === 'owner_gate' || state === 'operator_retired_unknown_outcome' || item.queue.wait_reason === 'owner_gate') return 'border-amber-700/60 bg-amber-950/10'
  if (state === 'processed' || state === 'acknowledged' || state === 'completed') return 'border-emerald-800/50 bg-emerald-950/10'
  if (state === 'superseded') return 'border-violet-800/50 bg-violet-950/10'
  return 'border-gray-700/60 bg-gray-900/70'
}

function statusTone(item: InteractionItem) {
  const state = item.final_disposition || item.queue.state || ''
  if (state.includes('failed') || state === 'cancelled') return 'bg-red-400/10 text-red-300'
  if (state === 'owner_gate' || state === 'operator_retired_unknown_outcome') return 'bg-amber-400/10 text-amber-300'
  if (state === 'processed' || state === 'acknowledged' || state === 'completed') return 'bg-emerald-400/10 text-emerald-300'
  if (state === 'superseded') return 'bg-violet-400/10 text-violet-300'
  return 'bg-sky-400/10 text-sky-300'
}

function InteractionCard({
  item,
  expanded,
  result,
  resultLoading,
  resultError,
  onToggle,
}: {
  item: InteractionItem
  expanded: boolean
  result?: DelegationResult
  resultLoading: boolean
  resultError: boolean
  onToggle: () => void
}) {
  const { locale, t } = useI18n()
  const state = item.current
    ? translatedValue(item.queue.state, STATE_KEYS, t)
    : translatedValue(item.final_disposition, DISPOSITION_KEYS, t)
      || translatedValue(item.queue.state, STATE_KEYS, t)
  const source = translatedValue(item.source.kind, SOURCE_KEYS, t)
  const wait = translatedValue(item.queue.wait_reason, WAIT_KEYS, t)
  const hasDetails = Boolean(
    item.input_preview || item.workflow.id || item.result.id || item.delivery.status || item.diagnostics.durable_id,
  )
  const resultState = translatedValue(item.result.status, STATE_KEYS, t) || item.result.status
  const deliveryState = translatedValue(item.delivery.status, STATE_KEYS, t) || item.delivery.status

  return <article data-testid={`interaction-${item.id}`} className={`rounded-xl border p-3 sm:p-4 ${itemTone(item)}`}>
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="text-xs font-semibold text-gray-100">{t(TYPE_KEYS[item.interaction_type])}</span>
          <span className="max-w-full truncate text-[11px] text-gray-400">{translatedValue(item.task_type, TASK_KEYS, t)}</span>
        </div>
        <p className="mt-1 text-[11px] text-gray-500">{formatTimestamp(item.created_at, locale)}</p>
      </div>
      {state && <span className={`shrink-0 rounded-full px-2 py-1 text-[10px] font-medium ${statusTone(item)}`}>{state}</span>}
    </div>

    <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
      <div className="min-w-0"><span className="text-gray-500">{t('common.source')}</span><p className="mt-0.5 truncate text-gray-200" title={item.source.terminal_id || undefined}>{source}{item.source.terminal_id ? ` · ${item.source.terminal_id}` : ''}</p></div>
      {wait && <div className="min-w-0"><span className="text-gray-500">{t('interactions.waitingFor')}</span><p className="mt-0.5 text-amber-200">{wait}</p></div>}
      {item.queue.admission_pending && <div><span className="text-gray-500">{t('interactions.admission')}</span><p className="mt-0.5 text-sky-200">{t('interactions.pending')}</p></div>}
      {item.delivery.pending && <div><span className="text-gray-500">{t('interactions.delivery')}</span><p className="mt-0.5 text-sky-200">{deliveryState || t('interactions.pending')}</p></div>}
    </div>
    {item.result.id ? <p className="mt-3 flex items-start gap-1.5 text-xs text-emerald-200"><CircleCheck size={13} className="mt-0.5 shrink-0"/><span><span className="text-gray-500">{t('interactions.canonicalResult')} · </span>{item.result.summary || resultState || t('interactions.resultProduced')}</span></p> : !item.current && <p className="mt-3 text-xs text-gray-500">{t('interactions.noCanonicalResult')}</p>}

    {hasDetails && <button type="button" onClick={onToggle} aria-expanded={expanded} className="mt-3 inline-flex min-h-9 items-center gap-1.5 rounded-lg text-xs text-gray-400 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400">
      {expanded ? <ChevronUp size={14}/> : <ChevronDown size={14}/>}{t(expanded ? 'interactions.hideDetails' : 'interactions.showDetails')}
    </button>}

    {expanded && <div className="mt-3 space-y-3 border-t border-gray-700/50 pt-3 text-xs">
      {item.input_preview && <div><p className="mb-1 text-[10px] uppercase tracking-wide text-gray-500">{t('interactions.input')}</p><p className="whitespace-pre-wrap break-words text-gray-300">{item.input_preview}</p></div>}
      {(item.workflow.id || item.workflow.status) && <div>
        <p className="mb-1 text-[10px] uppercase tracking-wide text-gray-500">{t('interactions.workflow')}</p>
        <div className="flex flex-wrap items-center gap-1.5 text-gray-300">
          <span>{translatedValue(item.workflow.status, STATE_KEYS, t) || item.workflow.status || t('common.unknown')}</span>
          {item.workflow.turn_state && <><ArrowRight size={12} className="text-gray-600"/><span>{translatedValue(item.workflow.turn_state, STATE_KEYS, t)}</span></>}
          {item.workflow.effect_state && <><ArrowRight size={12} className="text-gray-600"/><span>{item.workflow.effect_kind} · {item.workflow.effect_state}</span></>}
        </div>
        {item.workflow.reason && <p className="mt-1 text-amber-200">{item.workflow.reason}</p>}
        {item.workflow.provider_outcome_code && <p className="mt-1 text-gray-400">{t('interactions.providerOutcome')} · {item.workflow.provider_outcome_code}</p>}
      </div>}
      <div>
        <p className="mb-1 text-[10px] uppercase tracking-wide text-gray-500">{t('interactions.canonicalResult')}</p>
        {item.result.id ? <>
          <p className="flex items-center gap-1.5 text-gray-200"><CircleCheck size={13} className="text-emerald-400"/>{resultState || t('interactions.resultProduced')}{item.delivery.acknowledged ? ` · ${t('interactions.disposition.acknowledged')}` : deliveryState ? ` · ${deliveryState}` : ''}</p>
          {resultLoading && <p className="mt-2 inline-flex items-center gap-1.5 text-gray-400"><Loader2 size={13} className="animate-spin"/>{t('interactions.loadingResult')}</p>}
          {resultError && <p role="alert" className="mt-2 text-red-300">{t('interactions.resultLoadFailed')}</p>}
          {result && <div className="mt-2 whitespace-pre-wrap break-words rounded-lg bg-gray-950/70 p-3 text-gray-300">{result.document?.body_markdown || t('interactions.noResultBody')}</div>}
        </> : <p className="text-gray-500">{t('interactions.noCanonicalResult')}</p>}
      </div>
      <details className="text-[10px] text-gray-500"><summary className="cursor-pointer select-none">{t('interactions.diagnostics')}</summary><p className="mt-1 break-all font-mono">{item.diagnostics.interaction_id}{item.diagnostics.durable_id ? ` · ${item.diagnostics.durable_id}` : ''}{item.workflow.id ? ` · workflow ${item.workflow.id}` : ''}{item.workflow.turn_id ? ` · turn ${item.workflow.turn_id}` : ''}</p></details>
    </div>}
  </article>
}

export function InteractionHistoryDrawer({ sessionId, sessionName, terminalId, initialMode = 'current', onClose }: InteractionHistoryDrawerProps) {
  const { t } = useI18n()
  const [mode, setMode] = useState<InteractionMode>(initialMode)
  const [currentItems, setCurrentItems] = useState<InteractionItem[]>([])
  const [currentTotal, setCurrentTotal] = useState(0)
  const [currentCursor, setCurrentCursor] = useState<string | null>(null)
  const [historyItems, setHistoryItems] = useState<InteractionItem[]>([])
  const [historyTotal, setHistoryTotal] = useState<number | null>(null)
  const [historyCursor, setHistoryCursor] = useState<string | null>(null)
  const [historyLoaded, setHistoryLoaded] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [resultCache, setResultCache] = useState<Record<string, DelegationResult>>({})
  const [resultLoading, setResultLoading] = useState<Record<string, boolean>>({})
  const [resultErrors, setResultErrors] = useState<Record<string, boolean>>({})
  const drawerRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const previousFocusRef = useRef<HTMLElement | null>(null)
  const currentInFlightRef = useRef(false)
  const currentExtendedRef = useRef(false)
  const currentControllerRef = useRef<AbortController | null>(null)
  const historyControllerRef = useRef<AbortController | null>(null)

  const loadCurrent = useCallback(async (cursor?: string, initial = false) => {
    if (currentInFlightRef.current) return
    currentInFlightRef.current = true
    const append = Boolean(cursor)
    const controller = new AbortController()
    currentControllerRef.current = controller
    if (append) setLoadingMore(true)
    else if (initial) setLoading(true)
    try {
      const page = await api.listInteractions({ sessionId, terminalId, mode: 'current', limit: 20, cursor }, controller.signal)
      setCurrentItems(previous => append ? [...previous, ...page.items.filter(item => !previous.some(known => known.id === item.id))] : page.items)
      setCurrentTotal(page.total ?? page.items.length)
      setCurrentCursor(page.next_cursor)
      if (append) currentExtendedRef.current = true
      setError(null)
    } catch (reason) {
      if ((reason as { name?: string })?.name !== 'AbortError') setError(reason instanceof Error ? reason.message : t('interactions.loadFailed'))
    } finally {
      if (currentControllerRef.current === controller) currentControllerRef.current = null
      currentInFlightRef.current = false
      if (initial) setLoading(false)
      if (append) setLoadingMore(false)
    }
  }, [sessionId, terminalId, t])

  const loadHistory = useCallback(async (cursor?: string) => {
    const append = Boolean(cursor)
    if (append) setLoadingMore(true)
    else setLoading(true)
    const controller = new AbortController()
    historyControllerRef.current = controller
    try {
      const page = await api.listInteractions({ sessionId, terminalId, mode: 'history', limit: 20, cursor }, controller.signal)
      setHistoryItems(previous => append ? [...previous, ...page.items.filter(item => !previous.some(known => known.id === item.id))] : page.items)
      setHistoryTotal(page.total)
      setHistoryCursor(page.next_cursor)
      setHistoryLoaded(true)
      setError(null)
    } catch (reason) {
      if ((reason as { name?: string })?.name !== 'AbortError') setError(reason instanceof Error ? reason.message : t('interactions.loadFailed'))
    } finally {
      if (historyControllerRef.current === controller) historyControllerRef.current = null
      setLoading(false)
      setLoadingMore(false)
    }
  }, [sessionId, terminalId, t])

  useEffect(() => {
    void loadCurrent(undefined, true)
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible' && !currentExtendedRef.current) void loadCurrent()
    }, 5_000)
    return () => { window.clearInterval(timer); currentControllerRef.current?.abort() }
  }, [loadCurrent])

  useEffect(() => {
    if (mode === 'history' && !historyLoaded) void loadHistory()
  }, [historyLoaded, loadHistory, mode])

  useEffect(() => () => historyControllerRef.current?.abort(), [])

  useLayoutEffect(() => {
    previousFocusRef.current = document.activeElement as HTMLElement | null
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    closeRef.current?.focus()
    return () => {
      document.body.style.overflow = previousOverflow
      previousFocusRef.current?.focus?.()
    }
  }, [])

  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); onClose(); return }
      if (event.key !== 'Tab' || !drawerRef.current) return
      const focusable = [...drawerRef.current.querySelectorAll<HTMLElement>('button:not([disabled]), [href], input:not([disabled]), [tabindex]:not([tabindex="-1"])')]
      if (!focusable.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [onClose])

  const toggleItem = async (item: InteractionItem) => {
    const next = !expanded[item.id]
    setExpanded(current => ({ ...current, [item.id]: next }))
    const resultId = item.result.id
    if (!next || !resultId || !item.result.available || resultCache[resultId] || resultLoading[resultId]) return
    setResultLoading(current => ({ ...current, [resultId]: true }))
    try {
      const result = await api.getDelegationResult(resultId)
      setResultCache(current => ({ ...current, [resultId]: result }))
    } catch {
      setResultErrors(current => ({ ...current, [resultId]: true }))
    } finally {
      setResultLoading(current => ({ ...current, [resultId]: false }))
    }
  }

  const items = mode === 'current' ? currentItems : historyItems
  const total = mode === 'current' ? currentTotal : historyTotal

  return <div className="fixed inset-0 z-[70]">
    <button type="button" tabIndex={-1} aria-label={t('common.close')} onClick={onClose} className="absolute inset-0 h-full w-full cursor-default bg-black/60 backdrop-blur-sm"/>
    <div ref={drawerRef} role="dialog" aria-modal="true" aria-labelledby="interaction-drawer-title" className="absolute inset-0 flex min-h-0 flex-col overflow-hidden bg-gray-950 shadow-2xl sm:left-auto sm:w-[min(42rem,100vw)] sm:border-l sm:border-gray-700/60">
      <header className="flex shrink-0 items-start justify-between gap-3 border-b border-gray-700/50 px-4 py-4 sm:px-5">
        <div className="min-w-0">
          <h2 id="interaction-drawer-title" className="flex items-center gap-2 text-sm font-semibold text-white"><HistoryIcon size={17} className="text-emerald-400"/>{t('interactions.title')}</h2>
          <p className="mt-1 truncate text-xs text-gray-400">{sessionDisplayName(sessionName)}{terminalId ? ` · ${terminalId}` : ''}</p>
        </div>
        <button ref={closeRef} type="button" onClick={onClose} className="inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-lg text-gray-400 hover:bg-gray-800 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400" aria-label={t('common.close')}><X size={18}/></button>
      </header>

      <div role="tablist" aria-label={t('interactions.modes')} className="flex shrink-0 gap-1 border-b border-gray-700/40 px-4 pt-3 sm:px-5">
        <button type="button" role="tab" aria-selected={mode === 'current'} aria-controls="interaction-current-panel" onClick={() => setMode('current')} className={`min-h-11 border-b-2 px-3 text-xs font-medium ${mode === 'current' ? 'border-emerald-400 text-emerald-300' : 'border-transparent text-gray-400 hover:text-white'}`}><span className="inline-flex items-center gap-1.5"><ListTodo size={14}/>{t('interactions.currentQueue')}{currentTotal > 0 ? ` · ${currentTotal}` : ''}</span></button>
        <button type="button" role="tab" aria-selected={mode === 'history'} aria-controls="interaction-history-panel" onClick={() => setMode('history')} className={`min-h-11 border-b-2 px-3 text-xs font-medium ${mode === 'history' ? 'border-emerald-400 text-emerald-300' : 'border-transparent text-gray-400 hover:text-white'}`}><span className="inline-flex items-center gap-1.5"><HistoryIcon size={14}/>{t('interactions.history')}</span></button>
        <button type="button" onClick={() => { if (mode === 'current') { currentExtendedRef.current = false; void loadCurrent(undefined, true) } else void loadHistory() }} className="ml-auto inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg text-gray-400 hover:bg-gray-800 hover:text-white" aria-label={t('common.refresh')}><RefreshCw size={14}/></button>
      </div>

      <div id={mode === 'current' ? 'interaction-current-panel' : 'interaction-history-panel'} role="tabpanel" className="min-h-0 flex-1 overflow-y-auto px-3 py-4 sm:px-5" aria-live="polite">
        <div className="mb-3 flex items-center justify-between gap-3"><p className="text-xs text-gray-400">{mode === 'current' ? t('interactions.currentHelp') : t('interactions.historyHelp')}</p>{total !== null && total > 0 && <span className="shrink-0 text-[10px] text-gray-500">{t('interactions.items', { count: total })}</span>}</div>
        {error && <div role="alert" className="mb-3 flex items-start gap-2 rounded-lg border border-red-800/50 bg-red-950/20 p-3 text-xs text-red-300"><AlertTriangle size={14} className="mt-0.5 shrink-0"/><span>{error}</span></div>}
        {loading && items.length === 0 ? <div className="flex min-h-48 items-center justify-center gap-2 text-sm text-gray-400"><Loader2 size={18} className="animate-spin"/>{t('common.loading')}</div> : items.length === 0 ? <div className="flex min-h-48 flex-col items-center justify-center text-center"><CircleCheck size={28} className="mb-3 text-emerald-500/70"/><p className="text-sm text-gray-300">{t(mode === 'current' ? 'interactions.currentEmpty' : 'interactions.historyEmpty')}</p></div> : <div className="space-y-3">{items.map(item => <InteractionCard key={item.id} item={item} expanded={Boolean(expanded[item.id])} result={item.result.id ? resultCache[item.result.id] : undefined} resultLoading={Boolean(item.result.id && resultLoading[item.result.id])} resultError={Boolean(item.result.id && resultErrors[item.result.id])} onToggle={() => void toggleItem(item)}/>)}</div>}
        {mode === 'current' && currentCursor && <div className="flex justify-center pt-4"><button type="button" disabled={loadingMore} onClick={() => void loadCurrent(currentCursor)} className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-gray-700 px-4 text-xs text-gray-300 hover:border-emerald-700 disabled:opacity-50">{loadingMore && <Loader2 size={13} className="animate-spin"/>}{t('interactions.loadMoreCurrent')}</button></div>}
        {mode === 'history' && historyCursor && <div className="flex justify-center pt-4"><button type="button" disabled={loadingMore} onClick={() => void loadHistory(historyCursor)} className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-gray-700 px-4 text-xs text-gray-300 hover:border-emerald-700 disabled:opacity-50">{loadingMore && <Loader2 size={13} className="animate-spin"/>}{t('interactions.loadMore')}</button></div>}
      </div>
    </div>
  </div>
}
