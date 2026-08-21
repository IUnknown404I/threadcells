import { useState, useEffect, useLayoutEffect, useRef } from 'react'
import { useStore } from '../store'
import { api, Session, TerminalMeta } from '../api'
import { Bot, Zap, Package, Monitor, Terminal as TermIcon, Trash2, Mail, FileText, LogOut, ChevronRight, ChevronDown, Users, MessageSquareWarning, Search, List, Grid2X2 } from 'lucide-react'
import { TerminalView } from './TerminalView'
import { ConfirmModal } from './ConfirmModal'
import { InboxPanel } from './InboxPanel'
import { StatusBadge, lifecycleBadgeStatus } from './StatusBadge'
import { OutputViewer } from './OutputViewer'
import { HomeAgentFilter, matchesHomeAgentFilter } from '../agentFilters'
import { sessionDisplayName } from '../sessionDisplayName'

interface SessionWithTerminals {
  id: string
  name: string
  status: string
  terminals: TerminalMeta[]
}

function resolvedSessionProjectTitle(terminals: TerminalMeta[]): string | null {
  if (!terminals.length) return null
  const contexts = terminals.map(terminal => ({
    id: terminal.project_id,
    name: terminal.project_name,
    path: terminal.project_path,
  }))
  if (contexts.some(context => !context.id?.trim() || !context.name?.trim() || !context.path?.trim())) return null
  const [first] = contexts
  if (contexts.some(context => context.id !== first.id || context.name !== first.name || context.path !== first.path)) return null
  return first.name!
}

export type HomeNavigation = string | { tab: 'agents'; filter?: HomeAgentFilter; intent?: 'create-session' }

function AgentViewControls({ value, onChange }: { value: 'list' | 'grid'; onChange: (value: 'list' | 'grid') => void }) {
  const controls = [
    { value: 'list' as const, label: 'List view', icon: List },
    { value: 'grid' as const, label: 'Grid view', icon: Grid2X2 },
  ]

  return (
    <div className="inline-flex shrink-0 items-center gap-1" role="group" aria-label="Agent layout">
      {controls.map(({ value: controlValue, label, icon: Icon }) => (
        <button
          key={controlValue}
          type="button"
          aria-label={label}
          aria-pressed={value === controlValue}
          onClick={() => onChange(controlValue)}
          className={`inline-flex h-9 w-9 items-center justify-center rounded-md transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 focus-visible:ring-offset-2 focus-visible:ring-offset-gray-800 ${value === controlValue ? 'bg-emerald-600 text-white' : 'text-gray-400 hover:bg-gray-800 hover:text-gray-200'}`}
          title={controlValue === 'list' ? 'List' : 'Grid'}
        >
          <Icon size={15} aria-hidden="true" />
        </button>
      ))}
    </div>
  )
}

const MAX_VISIBLE_STATUS_ROWS = 2

function SessionStatusBadges({ sessionId, terminals, terminalStatuses }: { sessionId: string; terminals: TerminalMeta[]; terminalStatuses: Record<string, string> }) {
  const measurementRef = useRef<HTMLDivElement>(null)
  const [layout, setLayout] = useState({ exceedsMaxRows: false, visibleTerminalCount: terminals.length })
  const [expanded, setExpanded] = useState(false)
  const badgeSignature = terminals.map(terminal => `${terminal.id}:${terminalStatuses[terminal.id] || ''}`).join('|')

  useLayoutEffect(() => {
    const container = measurementRef.current
    if (!container) return

    const measure = () => {
      const rows: Array<{ bottom: number }> = []
      let visibleTerminalCount = 0

      Array.from(container.children).forEach(badge => {
        const rect = badge.getBoundingClientRect()
        const currentRow = rows[rows.length - 1]
        if (!currentRow || rect.top > currentRow.bottom + 1) {
          rows.push({ bottom: rect.bottom })
        } else {
          currentRow.bottom = Math.max(currentRow.bottom, rect.bottom)
        }
        if (rows.length <= MAX_VISIBLE_STATUS_ROWS) visibleTerminalCount += 1
      })

      const exceedsMaxRows = rows.length > MAX_VISIBLE_STATUS_ROWS
      const next = { exceedsMaxRows, visibleTerminalCount: exceedsMaxRows ? visibleTerminalCount : terminals.length }
      setLayout(previous => previous.exceedsMaxRows === next.exceedsMaxRows && previous.visibleTerminalCount === next.visibleTerminalCount ? previous : next)
    }

    measure()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    observer?.observe(container)
    window.addEventListener('resize', measure)
    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', measure)
    }
  }, [badgeSignature])

  const isCollapsed = layout.exceedsMaxRows && !expanded
  const visibleTerminals = isCollapsed ? terminals.slice(0, layout.visibleTerminalCount) : terminals
  const hiddenTerminalCount = terminals.length - layout.visibleTerminalCount
  const finalTerminal = terminals[terminals.length - 1]
  const finalTerminalIsHidden = isCollapsed && terminals.length > layout.visibleTerminalCount

  return (
    <div className="relative min-w-0 flex-1">
      <div
        ref={measurementRef}
        data-testid={`session-status-badges-measure-${sessionId}`}
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 invisible flex flex-wrap items-center gap-2"
      >
        {terminals.map(terminal => (
          <span key={terminal.id} data-measure-status-badge className="min-w-0 max-w-full [&>span]:max-w-full">
            <StatusBadge status={terminalStatuses[terminal.id] || null} />
          </span>
        ))}
      </div>
      <div data-testid={`session-status-badges-${sessionId}`} className="flex flex-wrap items-center gap-2">
        {visibleTerminals.map(terminal => (
          <span key={terminal.id} data-status-badge data-terminal-id={terminal.id} className="min-w-0 max-w-full [&>span]:max-w-full">
            <StatusBadge status={terminalStatuses[terminal.id] || null} />
          </span>
        ))}
      </div>
      {isCollapsed && (
        <div data-testid={`session-status-badge-summary-${sessionId}`} className="mt-2 flex min-w-0 flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => setExpanded(true)}
            aria-expanded="false"
            className="shrink-0 text-xs font-medium text-emerald-400 transition-colors hover:text-emerald-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
          >
            Show {hiddenTerminalCount} collapsed agent{hiddenTerminalCount === 1 ? '' : 's'}
          </button>
          {finalTerminalIsHidden && (
            <span data-status-badge data-terminal-id={finalTerminal.id} className="min-w-0 max-w-full [&>span]:max-w-full">
              <StatusBadge status={terminalStatuses[finalTerminal.id] || null} />
            </span>
          )}
        </div>
      )}
      {layout.exceedsMaxRows && expanded && (
        <button
          type="button"
          onClick={() => setExpanded(false)}
          aria-expanded="true"
          className="mt-2 text-xs font-medium text-emerald-400 transition-colors hover:text-emerald-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
        >
          {'< Hide rows'}
        </button>
      )}
    </div>
  )
}

function SessionStatusSummary({ sessionId, terminals, terminalStatuses }: { sessionId: string; terminals: TerminalMeta[]; terminalStatuses: Record<string, string> }) {
  const firstAgent = terminals[0]
  const lastAgent = terminals[terminals.length - 1]

  return (
    <div
      data-testid={`session-status-groups-${sessionId}`}
      className="flex min-w-0 max-w-full flex-1 flex-wrap items-start gap-x-2 gap-y-2 overflow-hidden sm:items-center"
    >
      <span
        data-testid={`session-status-first-${sessionId}`}
        aria-label="First agent status"
        className="inline-flex w-full min-w-0 max-w-full flex-wrap items-center gap-1.5 sm:w-auto"
      >
        <span className="shrink-0 text-xs font-medium text-gray-500">First:</span>
        <span data-status-badge data-terminal-id={firstAgent.id} className="inline-flex min-w-0 max-w-full flex-1 flex-wrap [&>span]:max-w-full sm:flex-none">
          <StatusBadge status={terminalStatuses[firstAgent.id] || null} />
        </span>
      </span>
      <span
        data-testid={`session-status-last-${sessionId}`}
        aria-label="Last agent status"
        className="inline-flex w-full min-w-0 max-w-full flex-wrap items-center gap-1.5 sm:w-auto"
      >
        <span aria-hidden="true" className="shrink-0 text-xs text-gray-700">·</span>
        <span className="shrink-0 text-xs font-medium text-gray-500">Last:</span>
        <span data-status-badge data-terminal-id={lastAgent.id} className="inline-flex min-w-0 max-w-full flex-1 flex-wrap [&>span]:max-w-full sm:flex-none">
          <StatusBadge status={terminalStatuses[lastAgent.id] || null} />
        </span>
      </span>
      <div
        data-testid={`session-status-total-${sessionId}`}
        aria-label="Total agent statuses"
        className="flex min-w-0 max-w-full flex-[1_1_20rem] items-start gap-1.5"
      >
        <span aria-hidden="true" className="shrink-0 text-xs leading-5 text-gray-700">·</span>
        <span className="shrink-0 text-xs font-medium leading-5 text-gray-500">Total:</span>
        <SessionStatusBadges sessionId={sessionId} terminals={terminals} terminalStatuses={terminalStatuses} />
      </div>
    </div>
  )
}

export function DashboardHome({ onNavigate }: { onNavigate: (destination: HomeNavigation) => void }) {
  const { sessions, connected, terminalStatuses, setTerminalStatuses, clearTerminalStatuses, deleteSession, showSnackbar } = useStore()
  const [profileCount, setProfileCount] = useState<number | null>(null)
  const [runningAgentCount, setRunningAgentCount] = useState<number | null>(null)
  const [sessionSearch, setSessionSearch] = useState('')
  const [terminalStates, setTerminalStates] = useState<Record<string, { lifecycle?: string | null; workflow_state?: string | null; status?: string | null }>>({})
  const [sessionData, setSessionData] = useState<SessionWithTerminals[]>([])
  const [expandedSessions, setExpandedSessions] = useState<Set<string>>(new Set())
  const initializedExpansion = useRef(false)
  const [agentView, setAgentView] = useState<'list' | 'grid'>('list')
  const [liveTerminal, setLiveTerminal] = useState<{ id: string; provider?: string; agentProfile?: string | null } | null>(null)
  const [pendingClose, setPendingClose] = useState<TerminalMeta | null>(null)
  const [closingTerminal, setClosingTerminal] = useState<string | null>(null)
  const [inboxTerminalId, setInboxTerminalId] = useState<string | null>(null)
  const [outputTerminalId, setOutputTerminalId] = useState<string | null>(null)
  const [pendingExit, setPendingExit] = useState<TerminalMeta | null>(null)
  const [exitingTerminal, setExitingTerminal] = useState<string | null>(null)
  const [pendingDeleteSession, setPendingDeleteSession] = useState<Session | null>(null)
  const [deletingSession, setDeletingSession] = useState<string | null>(null)
  const deletingSessionRef = useRef(false)

  // Fetch session details with terminals
  useEffect(() => {
    const fetchAll = async () => {
      try {
        const sessionDetails = await Promise.all(
          sessions.map(async s => {
            try {
              const detail = await api.getSession(s.name)
              return { id: s.id, name: s.name, status: s.status, terminals: detail.terminals || [] }
            } catch {
              return { id: s.id, name: s.name, status: s.status, terminals: [] }
            }
          })
        )
        setSessionData(sessionDetails)
        if (!initializedExpansion.current && connected) {
          if (sessionDetails.length) setExpandedSessions(new Set([sessionDetails[0].name]))
          initializedExpansion.current = true
        }
      } catch {}
    }
    fetchAll()
    const interval = setInterval(fetchAll, 5000)
    return () => clearInterval(interval)
  }, [connected, sessions.map(s => s.id).join(',')])

  // Poll statuses
  useEffect(() => {
    const allIds = sessionData.flatMap(s => s.terminals.map(t => t.id))
    if (!allIds.length) {
      setRunningAgentCount(0)
      return
    }
    clearTerminalStatuses(allIds)
    let cancelled = false
    setRunningAgentCount(null)
    let inFlight = false
    const fetch = async () => {
      if (inFlight) return
      inFlight = true
      const terminals = await Promise.all(allIds.map(async id => {
        try {
          const terminal = await api.getTerminalStatus(id)
          return terminal
        } catch {
          return null
        }
      }))
      if (!cancelled) {
        const states = Object.fromEntries(terminals.filter(Boolean).map(terminal => [terminal!.id, terminal!]))
        setTerminalStates(previous => JSON.stringify(previous) === JSON.stringify(states) ? previous : states)
        setTerminalStatuses(Object.fromEntries(terminals.filter(terminal => Boolean(terminal?.status)).map(terminal => [terminal!.id, lifecycleBadgeStatus(terminal!.workflow_state, terminal!.status, terminal!.lifecycle, terminal!.execution_state)])))
        setRunningAgentCount(terminals.filter((terminal): terminal is NonNullable<typeof terminal> => Boolean(terminal && matchesHomeAgentFilter(terminal, 'active'))).length)
      }
      inFlight = false
    }
    fetch()
    const interval = setInterval(fetch, 3000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [sessionData.flatMap(s => s.terminals.map(t => t.id)).join(',')])

  useEffect(() => {
    api.listProfiles().then(p => setProfileCount(p.length)).catch(() => {})
  }, [])

  const terminals = sessionData.flatMap(session => session.terminals)
  const agentCount = (filter: HomeAgentFilter) => terminals.filter(terminal => matchesHomeAgentFilter(terminalStates[terminal.id] || {}, filter)).length
  const visibleSessions = sessionData.filter(session => {
    const query = sessionSearch.trim().toLowerCase()
    return !query || session.name.toLowerCase().includes(query) || sessionDisplayName(session.name).toLowerCase().includes(query)
  })

  const handleDeleteTerminal = async () => {
    if (!pendingClose) return
    setClosingTerminal(pendingClose.id)
    try {
      await api.deleteTerminal(pendingClose.id)
      if (liveTerminal?.id === pendingClose.id) setLiveTerminal(null)
      showSnackbar({ type: 'success', message: `Terminal ${pendingClose.id} closed` })
    } catch (error: any) {
      showSnackbar({ type: 'error', message: error.message || 'Failed to close terminal' })
    }
    setClosingTerminal(null)
    setPendingClose(null)
  }

  const handleExitTerminal = async () => {
    if (!pendingExit) return
    setExitingTerminal(pendingExit.id)
    try {
      const result = await api.exitTerminal(pendingExit.id)
      if (!result.success) {
        showSnackbar({ type: 'error', message: result.message })
        return
      }
      showSnackbar({ type: 'success', message: result.message })
      setPendingExit(null)
    } catch (error: any) {
      showSnackbar({ type: 'error', message: error.message || 'Failed to send exit' })
    } finally {
      setExitingTerminal(null)
    }
  }

  const toggleSession = (name: string) => {
    setExpandedSessions(prev => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  const isOwnerGated = (terminalId: string) => terminalStatuses[terminalId]?.startsWith('WORKFLOW_OWNER_GATE')

  const handleDeleteSession = async () => {
    if (!pendingDeleteSession || deletingSessionRef.current) return
    deletingSessionRef.current = true
    setDeletingSession(pendingDeleteSession.id)
    try {
      await deleteSession(pendingDeleteSession.id)
      setPendingDeleteSession(null)
    } finally {
      deletingSessionRef.current = false
      setDeletingSession(null)
    }
  }

  return (
    <div className="space-y-6">
      {/* Stats Row */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <button
          onClick={() => onNavigate('agents')}
          className="bg-gradient-to-br from-gray-800/80 to-gray-900/80 rounded-xl p-5 border border-gray-700/50 text-left transition-colors hover:border-emerald-700/50 hover:bg-gray-800/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
          aria-label="View sessions"
        >
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-emerald-900/50 flex items-center justify-center">
              <Users size={20} className="text-emerald-400" />
            </div>
            <div>
              <div className="text-2xl font-bold text-white">{sessions.length}</div>
              <div className="text-xs text-gray-400 uppercase tracking-wide">Sessions</div>
            </div>
          </div>
        </button>
        <button
          onClick={() => onNavigate({ tab: 'agents', filter: 'all' })}
          className="bg-gradient-to-br from-gray-800/80 to-gray-900/80 rounded-xl p-5 border border-gray-700/50 text-left transition-colors hover:border-cyan-700/50 hover:bg-gray-800/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400"
          aria-label="View all agents"
        >
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-cyan-900/50 flex items-center justify-center">
              <TermIcon size={20} className="text-cyan-400" />
            </div>
            <div>
              <div className="text-2xl font-bold text-white">{terminals.length}</div>
              <div className="text-xs text-gray-400 uppercase tracking-wide">Total agents</div>
            </div>
          </div>
        </button>
        <button
          onClick={() => onNavigate({ tab: 'agents', filter: 'active' })}
          className="bg-gradient-to-br from-gray-800/80 to-gray-900/80 rounded-xl p-5 border border-gray-700/50 text-left transition-colors hover:border-blue-700/50 hover:bg-gray-800/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400"
          aria-label="View active agents"
          title="View active agents"
        >
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-blue-900/50 flex items-center justify-center">
              <Package size={20} className="text-blue-400" />
            </div>
            <div>
              <div className="text-2xl font-bold text-white">{runningAgentCount === null ? '—' : agentCount('active')}</div>
              <div className="text-xs text-gray-400 uppercase tracking-wide">Active agents</div>
            </div>
          </div>
        </button>
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <SummaryAlert label="Waiting" value={agentCount('waiting')} tone="emerald" onClick={() => onNavigate({ tab: 'agents', filter: 'waiting' })} />
        <SummaryAlert label="Needs attention" value={agentCount('owner_gate')} tone="amber" onClick={() => onNavigate({ tab: 'agents', filter: 'owner_gate' })} />
        <SummaryAlert label="Force-terminated" value={agentCount('cancelled')} tone="red" onClick={() => onNavigate({ tab: 'agents', filter: 'cancelled' })} />
        <SummaryAlert label="Completed" value={agentCount('completed')} tone="purple" onClick={() => onNavigate({ tab: 'agents', filter: 'completed' })} />
      </div>

      {/* Quick Actions */}
      <div className="flex gap-3 flex-wrap">
        <button
          onClick={() => onNavigate({ tab: 'agents', intent: 'create-session' })}
          className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-medium px-4 py-2.5 rounded-lg transition-colors"
        >
          <Bot size={16} /> Create Session &amp; Spawn Agent
        </button>
        <button
          onClick={() => onNavigate('flows')}
          className="flex items-center gap-2 bg-gray-700 hover:bg-gray-600 text-white text-sm font-medium px-4 py-2.5 rounded-lg transition-colors"
        >
          <Zap size={16} /> Manage Flows
        </button>
      </div>

      {/* Sessions — grouped view */}
      <div className="mb-1 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
        <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">Active Sessions</h3>
        <p className="text-xs text-gray-500 mt-1">
          Each session is a workspace where one or more AI agents run and collaborate. Agents within a session can send each other messages, hand off tasks, and work in parallel. Open a terminal to interact with any agent directly.
        </p>
        </div>
        <div className="relative w-full sm:w-56"><Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-500" /><input value={sessionSearch} onChange={e => setSessionSearch(e.target.value)} placeholder="Filter sessions..." className="w-full rounded-lg border border-gray-700 bg-gray-900 py-2 pl-8 pr-3 text-xs text-gray-200 focus:border-emerald-500 focus:outline-none" /></div>
      </div>
      {visibleSessions.length === 0 ? (
        <div className="bg-gray-800/60 border border-gray-700/50 rounded-xl p-8 text-center">
          <Bot size={32} className="mx-auto text-gray-600 mb-3" />
          <p className="text-gray-400 text-sm">No active sessions.</p>
          <p className="text-gray-600 text-xs mt-1">Go to the <span className="text-emerald-400 cursor-pointer" onClick={() => onNavigate('agents')}>Agents tab</span> to spawn your first agent.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {visibleSessions.map(session => {
            const projectTitle = resolvedSessionProjectTitle(session.terminals)
            const expanded = expandedSessions.has(session.name)
            const displayName = sessionDisplayName(session.name)
            return (
            <div
              key={session.name}
              data-testid={`home-session-${session.id}`}
              className={`rounded-xl border overflow-hidden transition-colors ${
                expanded ? 'bg-emerald-900/30 border-emerald-700/50' : 'bg-gray-800/60 border-gray-700/50'
              }`}
            >
              <div data-testid={`session-header-${session.id}`} className="flex min-w-0 items-center gap-2 p-3 sm:gap-3 sm:px-4">
                <div
                  role="button"
                  tabIndex={0}
                  aria-expanded={expanded}
                  aria-controls={`home-session-detail-${session.id}`}
                  aria-label={`${expanded ? 'Collapse' : 'Expand'} ${displayName}`}
                  onClick={() => toggleSession(session.name)}
                  onKeyDown={event => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      toggleSession(session.name)
                    }
                  }}
                  className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 sm:gap-3"
                >
                  <Users size={14} className="text-emerald-400" />
                  <span className="min-w-0 truncate text-sm font-mono text-gray-200" title={displayName}>{displayName}</span>
                  <span className="text-xs text-gray-500 shrink-0">{session.terminals.length} agent{session.terminals.length !== 1 ? 's' : ''}</span>
                  {projectTitle && <span className="min-w-0 max-w-[14rem] truncate rounded-full bg-gray-700/50 px-2 py-0.5 text-xs text-gray-300" title={`Project: ${projectTitle}`}>Project: {projectTitle}</span>}
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    type="button"
                    onClick={event => {
                      event.stopPropagation()
                      setPendingDeleteSession({ id: session.id, name: session.name, status: session.status, created_at: null })
                    }}
                    className="min-h-11 min-w-11 inline-flex items-center justify-center rounded-lg text-gray-500 transition-colors hover:bg-gray-800 hover:text-red-400"
                    title="Delete session"
                    aria-label={`Delete ${displayName}`}
                  >
                    <Trash2 size={14} />
                  </button>
                  <button
                    type="button"
                    onClick={event => {
                      event.stopPropagation()
                      toggleSession(session.name)
                    }}
                    className="min-h-11 min-w-11 inline-flex items-center justify-center rounded-lg text-gray-500 transition-colors hover:bg-gray-800 hover:text-gray-300"
                    aria-expanded={expanded}
                    aria-controls={`home-session-detail-${session.id}`}
                    aria-label={`${expanded ? 'Collapse' : 'Expand'} ${displayName} using chevron`}
                  >
                    {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  </button>
                </div>
              </div>
              <div className="flex min-w-0 items-start gap-2 border-t border-gray-700/30 px-3 py-2 sm:items-center sm:px-4" aria-label="Session status">
                {session.terminals.length === 0 ? (
                  <div className="min-w-0 flex-1"><span className="text-xs text-gray-600">No agents yet</span></div>
                ) : <SessionStatusSummary sessionId={session.id} terminals={session.terminals} terminalStatuses={terminalStatuses} />}
                <AgentViewControls value={agentView} onChange={setAgentView} />
              </div>

              {/* Terminals inside session */}
              {expanded && (
                  <div id={`home-session-detail-${session.id}`} className="border-t border-gray-700/30 px-3 sm:px-4 pb-4 space-y-2 pt-3">
                  <div data-testid="session-agent-container" className={agentView === 'grid' ? 'grid grid-cols-1 lg:grid-cols-2 gap-2' : 'space-y-2'}>{session.terminals.map(t => (
                    <div key={t.id} data-testid={`agent-detail-card-${t.id}`} className="flex flex-col bg-gray-900/50 border border-gray-700/30 rounded-lg p-3 space-y-2">
                      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 min-w-0">
                          <TermIcon size={14} className="text-gray-400 shrink-0" />
                          <span className="text-sm font-medium text-gray-200 truncate min-w-0 max-w-full" title={t.agent_profile || 'default'}>{t.agent_profile || 'default'}</span>
                          <span className="text-xs font-mono text-gray-500 truncate min-w-0 max-w-full" title={t.id}>{t.id}</span>
                          <StatusBadge status={terminalStatuses[t.id] || null} />
                          <span className="text-[10px] text-gray-600 truncate max-w-full" title={t.provider}>{t.provider}</span>
                        </div>
                        <div className="flex items-center gap-1.5 shrink-0 flex-wrap border-t border-gray-700/30 pt-2">
                          <button
                            onClick={() => setInboxTerminalId(t.id)}
                            className="min-w-11 min-h-11 inline-flex items-center justify-center text-gray-400 hover:text-white bg-gray-800 hover:bg-gray-700 rounded-lg transition-colors"
                            title="Inbox"
                          >
                            <Mail size={14} />
                          </button>
                          <button
                            onClick={() => setOutputTerminalId(t.id)}
                            className="min-w-11 min-h-11 inline-flex items-center justify-center text-gray-400 hover:text-white bg-gray-800 hover:bg-gray-700 rounded-lg transition-colors"
                            title="Output"
                          >
                            <FileText size={14} />
                          </button>
                          <button
                            onClick={() => setLiveTerminal({ id: t.id, provider: t.provider, agentProfile: t.agent_profile })}
                            className="min-h-11 flex items-center gap-1.5 px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-medium rounded-lg transition-colors"
                          >
                            <Monitor size={12} />
                            Terminal
                          </button>
                          <button
                            onClick={() => setPendingExit(t)}
                            disabled={exitingTerminal === t.id}
                            className="min-w-11 min-h-11 inline-flex items-center justify-center text-gray-400 hover:text-amber-400 bg-gray-800 hover:bg-gray-700 rounded-lg transition-colors"
                            title="Graceful Exit"
                          >
                            <LogOut size={14} />
                          </button>
                          <button
                            onClick={() => setPendingClose(t)}
                            disabled={closingTerminal === t.id}
                            className="min-w-11 min-h-11 inline-flex items-center justify-center text-gray-400 hover:text-red-400 bg-gray-800 hover:bg-gray-700 rounded-lg transition-colors"
                            title="Close"
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </div>
                      {isOwnerGated(t.id) && (
                        <div className="flex flex-col gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 sm:flex-row sm:items-center sm:justify-between">
                          <div className="flex items-start gap-2 text-xs text-amber-100">
                            <MessageSquareWarning size={15} className="mt-0.5 shrink-0 text-amber-400" />
                            <span><strong className="font-semibold">This workflow is waiting for an owner decision.</strong> The agent is {terminalStatuses[t.id]?.endsWith('::Processing') ? 'currently processing an Inbox message.' : terminalStatuses[t.id]?.endsWith('::Ready') ? 'ready.' : 'not currently active.'}</span>
                          </div>
                          <button
                            type="button"
                            onClick={() => setLiveTerminal({ id: t.id, provider: t.provider, agentProfile: t.agent_profile })}
                            className="min-h-10 shrink-0 rounded-lg border border-amber-500/50 px-3 text-xs font-medium text-amber-200 hover:bg-amber-500/10"
                          >
                            Continue workflow
                          </button>
                        </div>
                      )}
                      <button
                        type="button"
                        onClick={() => setInboxTerminalId(t.id)}
                        className="inline-flex items-center gap-1.5 text-xs text-gray-500 transition-colors hover:text-emerald-300"
                      >
                        <Mail size={13} /> Message via Inbox
                      </button>
                    </div>
                  ))}</div>
                </div>
              )}
            </div>
            )
          })}
        </div>
      )}

      {/* Modals */}
      {inboxTerminalId && <InboxPanel terminalId={inboxTerminalId} onClose={() => setInboxTerminalId(null)} />}
      {liveTerminal && (
        <TerminalView terminalId={liveTerminal.id} provider={liveTerminal.provider} agentProfile={liveTerminal.agentProfile} onClose={() => setLiveTerminal(null)} />
      )}
      {outputTerminalId && <OutputViewer terminalId={outputTerminalId} onClose={() => setOutputTerminalId(null)} />}
      <ConfirmModal
        open={!!pendingClose}
        title="Close Terminal"
        message="This will kill the tmux window and terminate the agent process."
        details={pendingClose ? [
          { label: 'Terminal', value: `${pendingClose.agent_profile || 'default'} (${pendingClose.id})` },
          { label: 'Session', value: sessionDisplayName(pendingClose.tmux_session) },
        ] : []}
        confirmLabel="Close Terminal"
        variant="danger"
        loading={!!closingTerminal}
        onConfirm={handleDeleteTerminal}
        onCancel={() => setPendingClose(null)}
      />
      <ConfirmModal
        open={!!pendingExit}
        title="Graceful Exit"
        message="This will send the provider-specific exit command (e.g., /exit)."
        details={pendingExit ? [
          { label: 'Terminal', value: `${pendingExit.agent_profile || 'default'} (${pendingExit.id})` },
          { label: 'Provider', value: pendingExit.provider },
        ] : []}
        confirmLabel="Send Exit"
        variant="warning"
        loading={!!exitingTerminal}
        onConfirm={handleExitTerminal}
        onCancel={() => setPendingExit(null)}
      />
      <ConfirmModal
        open={!!pendingDeleteSession}
        title="Delete Session"
        message="This will permanently delete this session and all of its terminals. This action cannot be undone."
        details={pendingDeleteSession ? [
          { label: 'Session', value: sessionDisplayName(pendingDeleteSession.name) },
          { label: 'Status', value: pendingDeleteSession.status },
        ] : []}
        confirmLabel="Delete Session"
        variant="danger"
        loading={!!deletingSession}
        onConfirm={handleDeleteSession}
        onCancel={() => setPendingDeleteSession(null)}
      />
    </div>
  )
}

const SUMMARY_ALERT_CLASSES = {
  emerald: 'border-emerald-700/40 bg-emerald-900/10 text-emerald-300',
  amber: 'border-amber-700/40 bg-amber-900/10 text-amber-300',
  red: 'border-red-700/40 bg-red-900/10 text-red-300',
  purple: 'border-purple-700/40 bg-purple-900/10 text-purple-300',
} as const

function SummaryAlert({ label, value, tone, onClick }: { label: string; value: number; tone: keyof typeof SUMMARY_ALERT_CLASSES; onClick: () => void }) {
  return <button type="button" onClick={onClick} className={`rounded-xl border p-4 text-left transition-colors hover:brightness-125 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${SUMMARY_ALERT_CLASSES[tone]}`} aria-label={`View ${label} agents`}><div className="text-2xl font-bold">{value}</div><div className="mt-1 text-xs uppercase tracking-wide text-gray-400">{label}</div></button>
}
