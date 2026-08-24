import { lazy, ReactNode, Suspense, useRef, useState } from 'react'
import { useStore } from '../store'
import { AgentSummary, Session, SessionSummary, TerminalMeta, UiOverview, api } from '../api'
import { Bot, ChevronDown, ChevronRight, FileText, Grid2X2, List, LogOut, Mail, MessageSquareWarning, Monitor, Package, Search, Terminal as TermIcon, Trash2, Users, Zap } from 'lucide-react'
import { ConfirmModal } from './ConfirmModal'
import { InboxPanel } from './InboxPanel'
import { StatusBadge, lifecycleBadgeStatus } from './StatusBadge'
import { SessionStatusSummary } from './SessionStatusSummary'
import { OutputViewer } from './OutputViewer'
import { HomeAgentFilter } from '../agentFilters'
import { sessionDisplayName } from '../sessionDisplayName'
import { useAgentSummaryFeed, useNearViewport, useSessionSummaryFeed, useUiOverview } from '../uiReadModels'

const TerminalView = lazy(() => import('./TerminalView').then(module => ({ default: module.TerminalView })))

export type HomeNavigation = string | { tab: 'agents'; filter?: HomeAgentFilter; intent?: 'create-session' }

function AgentViewControls({ value, onChange }: { value: 'list' | 'grid'; onChange: (value: 'list' | 'grid') => void }) {
  const controls = [
    { value: 'list' as const, label: 'List view', icon: List },
    { value: 'grid' as const, label: 'Grid view', icon: Grid2X2 },
  ]
  return <div className="inline-flex shrink-0 items-center gap-1" role="group" aria-label="Agent layout">{controls.map(({ value: controlValue, label, icon: Icon }) => <button key={controlValue} type="button" aria-label={label} aria-pressed={value === controlValue} onClick={() => onChange(controlValue)} className={`inline-flex h-9 w-9 items-center justify-center rounded-md transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${value === controlValue ? 'bg-emerald-600 text-white' : 'text-gray-400 hover:bg-gray-800 hover:text-gray-200'}`} title={controlValue === 'list' ? 'List' : 'Grid'}><Icon size={15} aria-hidden="true" /></button>)}</div>
}

function terminalBadge(agent: AgentSummary): string {
  return lifecycleBadgeStatus(agent.workflow_state, agent.activity, agent.lifecycle, agent.execution_state)
}

function toTerminalMeta(agent: AgentSummary): TerminalMeta {
  return {
    id: agent.id,
    tmux_session: agent.session_name,
    tmux_window: agent.name,
    provider: agent.provider,
    agent_profile: agent.agent_profile,
    last_active: agent.last_active,
    lifecycle: agent.lifecycle,
    project_id: agent.projectId,
    project_name: agent.project_name,
    project_path: agent.project_path,
  }
}

function ExpandedSessionAgents({
  session,
  view,
  onInbox,
  onOutput,
  onTerminal,
  onExit,
  onClose,
  exitingTerminal,
  closingTerminal,
  refreshKey,
}: {
  session: SessionSummary
  view: 'list' | 'grid'
  onInbox: (id: string) => void
  onOutput: (id: string) => void
  onTerminal: (agent: AgentSummary) => void
  onExit: (agent: AgentSummary) => void
  onClose: (agent: AgentSummary) => void
  exitingTerminal: string | null
  closingTerminal: string | null
  refreshKey: number
}) {
  const feed = useAgentSummaryFeed({ sessionId: session.id, refreshKey })
  const sentinelRef = useNearViewport(feed.loadMore, feed.nextOffset !== null && !feed.loading)
  return <div id={`home-session-detail-${session.id}`} className="space-y-2 border-t border-gray-700/30 px-3 pb-4 pt-3 sm:px-4">
    {feed.error && <p role="alert" className="text-xs text-red-300">Unable to load this session’s agents.</p>}
    {feed.loading && feed.items.length === 0 ? <p className="text-xs text-gray-400">Loading agent details…</p> : <div data-testid="session-agent-container" className={view === 'grid' ? 'space-y-2 md:grid md:grid-cols-2 md:gap-2 md:space-y-0' : 'space-y-2'}>{feed.items.map(agent => {
      const badge = terminalBadge(agent)
      const ownerGated = agent.workflow_state === 'owner_gate'
      return <div key={agent.id} data-testid={`agent-detail-card-${agent.id}`} className="flex flex-col space-y-2 rounded-lg border border-gray-700/30 bg-gray-900/50 p-3">
        <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1"><TermIcon size={14} className="shrink-0 text-gray-400"/><span className="min-w-0 max-w-full truncate text-sm font-medium text-gray-200" title={agent.agent_profile || 'default'}>{agent.agent_profile || 'default'}</span><span className="min-w-0 max-w-full truncate font-mono text-xs text-gray-400" title={agent.id}>{agent.id}</span><StatusBadge status={badge}/><span className="max-w-full truncate text-[10px] text-gray-400" title={agent.provider}>{agent.provider}</span>{agent.project_name && <span className="max-w-full truncate text-[10px] text-gray-400">{agent.project_name}</span>}</div>
          <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-t border-gray-700/30 pt-2 sm:border-0 sm:pt-0">
            <button onClick={() => onInbox(agent.id)} className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-white" title="Inbox"><Mail size={14}/></button>
            <button onClick={() => onOutput(agent.id)} className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-white" title="Output"><FileText size={14}/></button>
            <button onClick={() => onTerminal(agent)} className="flex min-h-11 items-center gap-1.5 rounded-lg bg-emerald-600 px-3 text-xs font-medium text-white hover:bg-emerald-500"><Monitor size={12}/>Terminal</button>
            <button onClick={() => onExit(agent)} disabled={exitingTerminal === agent.id || agent.lifecycle === 'exited'} className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-amber-400 disabled:opacity-30" title="Graceful Exit"><LogOut size={14}/></button>
            <button onClick={() => onClose(agent)} disabled={closingTerminal === agent.id || agent.lifecycle !== 'exited'} className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-red-400 disabled:opacity-30" title={agent.lifecycle === 'exited' ? 'Delete exited terminal history' : 'Gracefully exit this terminal before deleting it'}><Trash2 size={14}/></button>
          </div>
        </div>
        {ownerGated && <div data-testid={`owner-decision-${agent.id}`} className="flex flex-col gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 sm:flex-row sm:items-center sm:justify-between"><div className="flex items-start gap-2 text-xs text-amber-100"><MessageSquareWarning size={15} className="mt-0.5 shrink-0 text-amber-400"/><span><strong className="font-semibold">This workflow is waiting for an owner decision.</strong>{agent.workflow_reason ? ` ${agent.workflow_reason}` : ''} The agent is {agent.execution_state === 'processing' ? 'currently processing.' : agent.lifecycle === 'exited' ? 'exited.' : 'ready.'}</span></div><button type="button" onClick={() => onTerminal(agent)} className="min-h-10 shrink-0 rounded-lg border border-amber-500/50 px-3 text-xs font-medium text-amber-200 hover:bg-amber-500/10">Continue workflow</button></div>}
        <button type="button" onClick={() => onInbox(agent.id)} className="inline-flex items-center gap-1.5 text-xs text-gray-400 hover:text-emerald-300"><Mail size={13}/>Message via Inbox</button>
      </div>
    })}</div>}
    {feed.nextOffset !== null && <div ref={sentinelRef} className="flex justify-center py-2"><button type="button" onClick={feed.loadMore} disabled={feed.loading} className="min-h-10 rounded-lg border border-gray-700 px-4 text-xs text-gray-300 hover:border-emerald-700 disabled:opacity-40">{feed.loading ? 'Loading…' : `Load more agents (${feed.items.length} of ${feed.total})`}</button></div>}
    {feed.limitReached && <p className="py-2 text-center text-xs text-gray-400">Showing the 100 most recent agents in this session. Use Agents filters to narrow older history.</p>}
  </div>
}

export function DashboardHome({ onNavigate, overviewState }: { onNavigate: (destination: HomeNavigation) => void; overviewState?: { overview: UiOverview | null; error: string | null } }) {
  const { deleteSession, showSnackbar } = useStore()
  const localOverviewState = useUiOverview(!overviewState)
  const { overview, error: overviewError } = overviewState || localOverviewState
  const [sessionSearch, setSessionSearch] = useState('')
  const sessionFeed = useSessionSummaryFeed(sessionSearch)
  const sessionSentinelRef = useNearViewport(sessionFeed.loadMore, sessionFeed.nextOffset !== null && !sessionFeed.loading)
  const [expandedSession, setExpandedSession] = useState<string | null>(null)
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
  const [agentRefreshKey, setAgentRefreshKey] = useState(0)
  const deletingSessionRef = useRef(false)

  // One expanded session owns one bounded agent feed. Historical browsing can
  // never multiply into a polling stream per card.
  const toggleSession = (name: string) => setExpandedSession(previous => previous === name ? null : name)
  const openTerminal = (agent: AgentSummary) => setLiveTerminal({ id: agent.id, provider: agent.provider, agentProfile: agent.agent_profile })

  const handleDeleteTerminal = async () => {
    if (!pendingClose) return
    setClosingTerminal(pendingClose.id)
    try {
      await api.deleteTerminal(pendingClose.id)
      if (liveTerminal?.id === pendingClose.id) setLiveTerminal(null)
      setAgentRefreshKey(value => value + 1)
      sessionFeed.reload()
      showSnackbar({ type: 'success', message: `Exited terminal ${pendingClose.id} deleted` })
    } catch (reason: any) {
      showSnackbar({ type: 'error', message: reason.message || 'Failed to close terminal' })
    } finally {
      setClosingTerminal(null)
      setPendingClose(null)
    }
  }

  const handleExitTerminal = async () => {
    if (!pendingExit) return
    setExitingTerminal(pendingExit.id)
    try {
      const result = await api.exitTerminal(pendingExit.id)
      showSnackbar({ type: result.success ? 'success' : 'error', message: result.message })
      if (result.success) {
        setPendingExit(null)
        setAgentRefreshKey(value => value + 1)
        sessionFeed.reload()
      }
    } catch (reason: any) {
      showSnackbar({ type: 'error', message: reason.message || 'Failed to send exit' })
    } finally {
      setExitingTerminal(null)
    }
  }

  const handleDeleteSession = async () => {
    if (!pendingDeleteSession || deletingSessionRef.current) return
    deletingSessionRef.current = true
    setDeletingSession(pendingDeleteSession.id)
    try { await deleteSession(pendingDeleteSession.id); sessionFeed.reload(); setPendingDeleteSession(null) } finally { deletingSessionRef.current = false; setDeletingSession(null) }
  }

  const summaryValue = (key: 'sessions' | 'agents' | 'active' | 'waiting' | 'owner_gate' | 'cancelled' | 'completed') => overview ? overview[key] : '—'
  return <div className="space-y-6">
    {(overviewError || sessionFeed.error) && <p role="alert" className="rounded-lg border border-red-800/50 bg-red-950/20 p-3 text-xs text-red-300">ThreadCells could not refresh all Home summaries. Existing data remains visible while it retries.</p>}
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
      <SummaryCard label="Sessions" value={summaryValue('sessions')} icon={<Users size={20} className="text-emerald-400"/>} tone="emerald" onClick={() => onNavigate('agents')}/>
      <SummaryCard label="Total agents" value={summaryValue('agents')} icon={<TermIcon size={20} className="text-cyan-400"/>} tone="cyan" onClick={() => onNavigate({ tab: 'agents', filter: 'all' })}/>
      <SummaryCard label="Active agents" value={summaryValue('active')} icon={<Package size={20} className="text-blue-400"/>} tone="blue" onClick={() => onNavigate({ tab: 'agents', filter: 'active' })}/>
    </div>
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4"><SummaryAlert label="Ready / waiting" value={summaryValue('waiting')} tone="emerald" onClick={() => onNavigate({ tab: 'agents', filter: 'waiting' })}/><SummaryAlert label="Needs attention" value={summaryValue('owner_gate')} tone="amber" onClick={() => onNavigate({ tab: 'agents', filter: 'owner_gate' })}/><SummaryAlert label="Cancelled" value={summaryValue('cancelled')} tone="red" onClick={() => onNavigate({ tab: 'agents', filter: 'cancelled' })}/><SummaryAlert label="Completed" value={summaryValue('completed')} tone="purple" onClick={() => onNavigate({ tab: 'agents', filter: 'completed' })}/></div>
    <div className="flex flex-wrap gap-3"><button onClick={() => onNavigate({ tab: 'agents', intent: 'create-session' })} className="flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-emerald-500"><Bot size={16}/>Create Session &amp; Spawn Agent</button><button onClick={() => onNavigate('flows')} className="flex items-center gap-2 rounded-lg bg-gray-700 px-4 py-2.5 text-sm font-medium text-white hover:bg-gray-600"><Zap size={16}/>Manage Flows</button></div>
    <div className="mb-1 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between"><div><h3 className="text-sm font-semibold uppercase tracking-wide text-gray-300">Sessions</h3><p className="mt-1 text-xs text-gray-400">Session cards load in bounded pages. Agent details load only when you expand a session.</p></div><div className="relative w-full sm:w-56"><Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400"/><input value={sessionSearch} onChange={event => setSessionSearch(event.target.value)} placeholder="Filter sessions…" className="w-full rounded-lg border border-gray-700 bg-gray-900 py-2 pl-8 pr-3 text-xs text-gray-200 focus:border-emerald-500 focus:outline-none"/></div></div>
    {sessionFeed.loading && sessionFeed.items.length === 0 ? <div className="rounded-xl border border-gray-700/50 bg-gray-800/60 p-8 text-center text-sm text-gray-400">Loading session summaries…</div> : sessionFeed.items.length === 0 ? <div className="rounded-xl border border-gray-700/50 bg-gray-800/60 p-8 text-center"><Bot size={32} className="mx-auto mb-3 text-gray-400"/><p className="text-sm text-gray-400">No matching sessions.</p></div> : <div className="space-y-3">{sessionFeed.items.map(session => {
      const expanded = expandedSession === session.id
      const displayName = sessionDisplayName(session.name)
      return <div key={session.id} data-testid={`home-session-${session.id}`} className={`overflow-hidden rounded-xl border transition-colors ${expanded ? 'border-emerald-700/50 bg-emerald-900/30' : 'border-gray-700/50 bg-gray-800/60'}`}>
        <div data-testid={`session-header-${session.id}`} className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-x-2 gap-y-1 p-3 sm:grid-cols-[minmax(0,1fr)_auto_auto] sm:gap-x-3 sm:px-4"><div data-testid={`session-title-row-${session.id}`} role="button" tabIndex={0} aria-expanded={expanded} aria-controls={`home-session-detail-${session.id}`} aria-label={`${expanded ? 'Collapse' : 'Expand'} ${displayName}`} onClick={() => toggleSession(session.id)} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); toggleSession(session.id) } }} className="col-span-2 flex min-w-0 w-full cursor-pointer items-center gap-2 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 sm:col-span-1 sm:gap-3"><Users size={14} className="shrink-0 text-emerald-400"/><span className="min-w-0 flex-1 truncate font-mono text-sm text-gray-200" title={displayName}>{displayName}</span></div><div data-testid={`session-metadata-${session.id}`} className="flex min-w-0 items-center gap-2"><span className="shrink-0 text-xs text-gray-400">{session.agent_count} agent{session.agent_count === 1 ? '' : 's'}</span>{session.project_name && <span className="min-w-0 max-w-[14rem] truncate rounded-full bg-gray-700/50 px-2 py-0.5 text-xs text-gray-300">Project: {session.project_name}</span>}</div><div data-testid={`session-actions-${session.id}`} className="col-span-2 flex min-w-0 flex-wrap items-center justify-end gap-1 sm:col-span-1 sm:shrink-0 sm:flex-nowrap"><AgentViewControls value={agentView} onChange={setAgentView}/><button type="button" onClick={event => { event.stopPropagation(); setPendingDeleteSession(session) }} className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg text-gray-400 hover:bg-gray-800 hover:text-red-400" title="Delete session" aria-label={`Delete ${displayName}`}><Trash2 size={14}/></button><button type="button" onClick={() => toggleSession(session.id)} className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg text-gray-400 hover:bg-gray-800 hover:text-gray-300" aria-expanded={expanded} aria-controls={`home-session-detail-${session.id}`} aria-label={`${expanded ? 'Collapse' : 'Expand'} ${displayName} using chevron`}>{expanded ? <ChevronDown size={14}/> : <ChevronRight size={14}/>}</button></div></div>
        <div className="flex min-w-0 items-start gap-2 border-t border-gray-700/30 px-3 py-2 sm:items-center sm:px-4" aria-label="Session status"><SessionStatusSummary session={session}/></div>
        {expanded && (
          <ExpandedSessionAgents
            session={session}
            view={agentView}
            onInbox={setInboxTerminalId}
            onOutput={setOutputTerminalId}
            onTerminal={openTerminal}
            onExit={agent => setPendingExit(toTerminalMeta(agent))}
            onClose={agent => setPendingClose(toTerminalMeta(agent))}
            exitingTerminal={exitingTerminal}
            closingTerminal={closingTerminal}
            refreshKey={agentRefreshKey}
          />
        )}
      </div>
    })}{sessionFeed.nextOffset !== null && <div ref={sessionSentinelRef} className="flex justify-center py-3"><button type="button" onClick={sessionFeed.loadMore} disabled={sessionFeed.loading} className="min-h-11 rounded-lg border border-gray-700 px-5 text-xs text-gray-300 hover:border-emerald-700 disabled:opacity-40">{sessionFeed.loading ? 'Loading…' : `Load more sessions (${sessionFeed.items.length} of ${sessionFeed.total})`}</button></div>}{sessionFeed.limitReached && <p className="py-3 text-center text-xs text-gray-400">Showing the 100 most recent matching sessions. Refine the search to inspect older history.</p>}</div>}
    {inboxTerminalId && <InboxPanel terminalId={inboxTerminalId} onClose={() => setInboxTerminalId(null)}/>}
    {liveTerminal && <Suspense fallback={null}><TerminalView terminalId={liveTerminal.id} provider={liveTerminal.provider} agentProfile={liveTerminal.agentProfile} onClose={() => setLiveTerminal(null)} /></Suspense>}
    {outputTerminalId && <OutputViewer terminalId={outputTerminalId} onClose={() => setOutputTerminalId(null)}/>}
    <ConfirmModal open={!!pendingClose} title="Delete Exited Terminal" message="ThreadCells will revalidate exact runtime absence, then permanently delete this exited terminal’s metadata." details={pendingClose ? [{ label: 'Terminal', value: `${pendingClose.agent_profile || 'default'} (${pendingClose.id})` }, { label: 'Session', value: sessionDisplayName(pendingClose.tmux_session) }] : []} confirmLabel="Delete Terminal" variant="danger" loading={!!closingTerminal} onConfirm={handleDeleteTerminal} onCancel={() => setPendingClose(null)}/>
    <ConfirmModal open={!!pendingExit} title="Graceful Exit" message="This will send the provider-specific exit command (e.g., /exit)." details={pendingExit ? [{ label: 'Terminal', value: `${pendingExit.agent_profile || 'default'} (${pendingExit.id})` }, { label: 'Provider', value: pendingExit.provider }] : []} confirmLabel="Send Exit" variant="warning" loading={!!exitingTerminal} onConfirm={handleExitTerminal} onCancel={() => setPendingExit(null)}/>
    <ConfirmModal open={!!pendingDeleteSession} title="Delete Session" message="This will permanently delete this session and all of its terminals. This action cannot be undone." details={pendingDeleteSession ? [{ label: 'Session', value: sessionDisplayName(pendingDeleteSession.name) }, { label: 'Status', value: pendingDeleteSession.status }] : []} confirmLabel="Delete Session" variant="danger" loading={!!deletingSession} onConfirm={handleDeleteSession} onCancel={() => setPendingDeleteSession(null)}/>
  </div>
}

const SUMMARY_ALERT_CLASSES = { emerald: 'border-emerald-700/40 bg-emerald-900/10 text-emerald-300', amber: 'border-amber-700/40 bg-amber-900/10 text-amber-300', red: 'border-red-700/40 bg-red-900/10 text-red-300', purple: 'border-purple-700/40 bg-purple-900/10 text-purple-300' } as const
const SUMMARY_CARD_CLASSES = { emerald: 'hover:border-emerald-700/50', cyan: 'hover:border-cyan-700/50', blue: 'hover:border-blue-700/50' } as const
function SummaryCard({ label, value, icon, tone, onClick }: { label: string; value: number | string; icon: ReactNode; tone: keyof typeof SUMMARY_CARD_CLASSES; onClick: () => void }) { return <button onClick={onClick} className={`rounded-xl border border-gray-700/50 bg-gradient-to-br from-gray-800/80 to-gray-900/80 p-5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${SUMMARY_CARD_CLASSES[tone]}`} aria-label={`View ${label.toLowerCase()}`}><div className="flex items-center gap-3"><div className="flex h-10 w-10 items-center justify-center rounded-lg bg-gray-900/60">{icon}</div><div><div className="text-2xl font-bold text-white">{value}</div><div className="text-xs uppercase tracking-wide text-gray-400">{label}</div></div></div></button> }
function SummaryAlert({ label, value, tone, onClick }: { label: string; value: number | string; tone: keyof typeof SUMMARY_ALERT_CLASSES; onClick: () => void }) { return <button type="button" onClick={onClick} className={`rounded-xl border p-4 text-left hover:brightness-125 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${SUMMARY_ALERT_CLASSES[tone]}`} aria-label={`View ${label} agents`}><div className="text-2xl font-bold">{value}</div><div className="mt-1 text-xs uppercase tracking-wide text-gray-400">{label}</div></button> }
