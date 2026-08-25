import { lazy, Suspense, useState, useEffect, useRef } from 'react'
import { useStore } from '../store'
import { AgentSummary, AgentProfileInfo, OwnerLaunchGrant, Project, ProviderInfo, Session, SessionSummary, TerminalMeta, api } from '../api'
import { Bot, Play, Trash2, ChevronRight, Terminal as TermIcon, Monitor, Package, FolderOpen, Search, Mail, Plus, LogOut, FileText, X, LoaderCircle, ShieldAlert } from 'lucide-react'
import { ConfirmModal } from './ConfirmModal'
import { InboxPanel } from './InboxPanel'
import { CustomSelect, SelectOption } from './CustomSelect'
import { StatusBadge, lifecycleBadgeStatus } from './StatusBadge'
import { OutputViewer } from './OutputViewer'
import { ProfilePicker } from './ProfilePicker'
import { ProjectPicker } from './ProjectPicker'
import { AgentViewMode, AgentFilterState, HOME_FILTER_LABELS, applyAgentFilterState, parseAgentFilterState } from '../agentFilters'
import { sessionDisplayName } from '../sessionDisplayName'
import { providerIsAvailable, providerSelectOption } from '../providerAvailability'
import { useAgentSummaryFeed, useNearViewport, useSessionSummaryFeed } from '../uiReadModels'
import { AgentViewControls, type AgentViewLayout } from './AgentViewControls'

const TerminalView = lazy(() => import('./TerminalView').then(module => ({ default: module.TerminalView })))

const FALLBACK_PROVIDERS = ['kiro_cli', 'claude_code', 'q_cli', 'codex', 'gemini_cli', 'kimi_cli', 'copilot_cli']

function defaultProvider(providers: ProviderInfo[]): string {
  return providers.find(provider => provider.name === 'codex' && providerIsAvailable(provider))?.name
    || providers.find(providerIsAvailable)?.name
    || 'codex'
}

const UNAVAILABLE_PROVIDER_FALLBACK = FALLBACK_PROVIDERS.map(name => ({
  name,
  binary: null,
  installed: false,
  available: false,
  availability: 'UNKNOWN' as const,
}))

const SOURCE_LABELS: Record<string, string> = {
  'built-in': 'Built-in',
  'local': 'Local',
  'kiro': 'Kiro',
  'q_cli': 'Q CLI',
}

const XHIGH_AUTHORIZATION_ERROR = 'Confirm the exceptional XHigh launch and authenticate as operator.'

function XHighAuthorizationBlock({ confirmed, operatorSecret, onConfirmedChange, onOperatorSecretChange }: {
  confirmed: boolean
  operatorSecret: string
  onConfirmedChange: (confirmed: boolean) => void
  onOperatorSecretChange: (secret: string) => void
}) {
  return <div role="alert" className="rounded-lg border border-amber-500/50 bg-amber-950/30 p-3 text-sm text-amber-100">
    <div className="flex items-start gap-2"><ShieldAlert size={18} className="mt-0.5 shrink-0 text-amber-300"/><div><p className="font-semibold">Exceptional XHigh owner-executor</p><p className="mt-1 text-xs text-amber-200/80">Highest-capability profile for direct critical work. This authorization applies only to this one launch.</p></div></div>
    <label className="mt-3 flex min-h-11 items-center gap-2 text-xs"><input aria-label="Confirm exceptional XHigh launch" type="checkbox" checked={confirmed} onChange={event => onConfirmedChange(event.target.checked)}/>I explicitly authorize this XHigh owner launch</label>
    <label className="mt-2 block text-xs text-amber-200/80">Operator secret<input aria-label="Operator secret" type="password" autoComplete="current-password" value={operatorSecret} onChange={event => onOperatorSecretChange(event.target.value)} className="mt-1 min-h-11 w-full rounded-lg border border-amber-700/60 bg-gray-950 px-3 text-sm text-gray-100 focus:border-amber-400 focus:outline-none"/></label>
  </div>
}

function authorizeOwnerLaunch({ selectedProfile, provider, workingDirectory, requestedSessionName, projectId, confirmed, operatorSecret }: {
  selectedProfile: AgentProfileInfo | undefined
  provider: string
  workingDirectory?: string
  requestedSessionName?: string
  projectId?: string
  confirmed: boolean
  operatorSecret: string
}): Promise<OwnerLaunchGrant> | undefined {
  if (!selectedProfile?.owner_authorization_required) return undefined
  if (!confirmed || !operatorSecret) throw new Error(XHIGH_AUTHORIZATION_ERROR)
  return api.createOperatorSession(operatorSecret).then(() => api.createXHighGrant({
    agent_profile: selectedProfile.name,
    provider,
    working_directory: workingDirectory,
    requested_session_name: requestedSessionName,
    project_id: projectId,
    launch_mode: 'existing_session',
    confirmed: true,
  }))
}

function toggleFilter(values: string[], value: string): string[] {
  return values.includes(value) ? values.filter(item => item !== value) : [...values, value]
}

function terminalBadgeStatus(status: AgentSummary | null): string | null {
  return status ? lifecycleBadgeStatus(status.workflow_state, status.activity, status.lifecycle, status.execution_state) : null
}

function toTerminalMeta(agent: AgentSummary): TerminalMeta {
  return { id: agent.id, tmux_session: agent.session_name, tmux_window: agent.name, provider: agent.provider, agent_profile: agent.agent_profile, last_active: agent.last_active, lifecycle: agent.lifecycle, project_id: agent.projectId, project_name: agent.project_name, project_path: agent.project_path }
}

export function AgentPanel({
  navigationSearch = window.location.search,
  navigationIntent = null,
  onNavigationIntentConsumed,
}: {
  navigationSearch?: string
  navigationIntent?: 'create-session' | null
  onNavigationIntentConsumed?: () => void
}) {
  const { createSession, deleteSession, showSnackbar } = useStore()
  const [activeSession, setActiveSession] = useState<string | null>(null)
  const [provider, setProvider] = useState('codex')
  const [profile, setProfile] = useState('')
  const [creating, setCreating] = useState(false)
  const creatingRef = useRef(false)
  const [liveTerminal, setLiveTerminal] = useState<{ id: string; provider?: string; agentProfile?: string | null } | null>(null)
  const [profiles, setProfiles] = useState<AgentProfileInfo[]>([])
  const [loadingProfiles, setLoadingProfiles] = useState(true)
  const [providers, setProviders] = useState<ProviderInfo[]>([])
  const [projects, setProjects] = useState<Project[]>([])

  useEffect(() => {
    api.listProviders()
      .then(p => {
        setProviders(p)
        const preferredProvider = defaultProvider(p)
        setProvider(preferredProvider)
        setAddProvider(preferredProvider)
      })
      .catch(() => {})
  }, [])
  const [pendingClose, setPendingClose] = useState<TerminalMeta | null>(null)
  const [closingTerminal, setClosingTerminal] = useState<string | null>(null)
  const [pendingDeleteSession, setPendingDeleteSession] = useState<Session | null>(null)
  const [deletingSession, setDeletingSession] = useState<string | null>(null)
  const deletingSessionRef = useRef(false)
  const [sessionSearch, setSessionSearch] = useState('')
  const [sessionAgentViews, setSessionAgentViews] = useState<Record<string, AgentViewLayout>>({})
  const initialFilters = parseAgentFilterState(navigationSearch)
  const [agentViewMode, setAgentViewMode] = useState<AgentViewMode>(initialFilters.view)
  const [providerStatusFilters, setProviderStatusFilters] = useState<string[]>(initialFilters.providerStatuses)
  const [workflowStatusFilters, setWorkflowStatusFilters] = useState<string[]>(initialFilters.workflowStates)
  const [profileFilters, setProfileFilters] = useState<string[]>(initialFilters.profiles)
  const [homeFilter, setHomeFilter] = useState(initialFilters.homeFilter)
  const [inboxTerminalId, setInboxTerminalId] = useState<string | null>(null)
  const [projectId, setProjectId] = useState('')
  const [sessionRootWorkDir, setSessionRootWorkDir] = useState<string | null>(null)
  const [showAddAgent, setShowAddAgent] = useState(false)
  const [addProvider, setAddProvider] = useState('codex')
  const [addProfile, setAddProfile] = useState('')
  const [addProjectId, setAddProjectId] = useState('')
  const [addOwnerConfirmed, setAddOwnerConfirmed] = useState(false)
  const [addOperatorSecret, setAddOperatorSecret] = useState('')
  const [addError, setAddError] = useState<string | null>(null)
  const [addingAgent, setAddingAgent] = useState(false)
  const [pendingExit, setPendingExit] = useState<TerminalMeta | null>(null)
  const [exitingTerminal, setExitingTerminal] = useState<string | null>(null)
  const [outputTerminalId, setOutputTerminalId] = useState<string | null>(null)
  const [showSpawnModal, setShowSpawnModal] = useState(false)
  const [sessionName, setSessionName] = useState('')
  const [spawnError, setSpawnError] = useState<string | null>(null)
  const [ownerConfirmed, setOwnerConfirmed] = useState(false)
  const [operatorSecret, setOperatorSecret] = useState('')
  const consumedNavigationIntent = useRef<'create-session' | null>(null)
  const sessionFeed = useSessionSummaryFeed(sessionSearch, agentViewMode === 'sessions')
  const activeSessionRecord = sessionFeed.items.find(session => session.id === activeSession) || null
  const activeSessionIdentifier = activeSessionRecord?.id || null
  const activeSessionFeed = useAgentSummaryFeed(
    { sessionId: activeSession || undefined },
    agentViewMode === 'sessions' && activeSession !== null,
  )
  const filteredAgentFeed = useAgentSummaryFeed({
    activities: providerStatusFilters,
    workflowStates: workflowStatusFilters,
    profiles: profileFilters,
    homeFilter,
  }, agentViewMode !== 'sessions')
  const sessionSentinelRef = useNearViewport(sessionFeed.loadMore, sessionFeed.nextOffset !== null && !sessionFeed.loading)
  const activeAgentSentinelRef = useNearViewport(activeSessionFeed.loadMore, activeSessionFeed.nextOffset !== null && !activeSessionFeed.loading)
  const filteredAgentSentinelRef = useNearViewport(filteredAgentFeed.loadMore, filteredAgentFeed.nextOffset !== null && !filteredAgentFeed.loading)

  useEffect(() => {
    const filters = parseAgentFilterState(navigationSearch)
    setAgentViewMode(filters.view)
    setProviderStatusFilters(filters.providerStatuses)
    setWorkflowStatusFilters(filters.workflowStates)
    setProfileFilters(filters.profiles)
    setHomeFilter(filters.homeFilter)
  }, [navigationSearch])

  const updateUrlFilters = (next: Partial<AgentFilterState>) => {
    const filters: AgentFilterState = {
      view: agentViewMode,
      providerStatuses: providerStatusFilters,
      workflowStates: workflowStatusFilters,
      profiles: profileFilters,
      homeFilter,
      ...next,
    }
    const url = new URL(window.location.href)
    url.search = applyAgentFilterState(url.searchParams, filters).toString()
    window.history.pushState({}, '', url)
  }

  const setView = (view: AgentViewMode) => {
    setAgentViewMode(view)
    updateUrlFilters({ view })
  }

  const handleDeleteTerminal = async () => {
    if (!pendingClose) return
    const id = pendingClose.id
    setClosingTerminal(id)
    try {
      await api.deleteTerminal(id)
      if (liveTerminal?.id === id) setLiveTerminal(null)
      activeSessionFeed.reload()
      filteredAgentFeed.reload()
      sessionFeed.reload()
      showSnackbar({ type: 'success', message: `Exited terminal ${id} deleted` })
    } catch (error: any) {
      showSnackbar({ type: 'error', message: error.message || `Failed to close terminal ${id}` })
    }
    setClosingTerminal(null)
    setPendingClose(null)
  }

  const handleDeleteSession = async () => {
    if (!pendingDeleteSession || deletingSessionRef.current) return
    deletingSessionRef.current = true
    const id = pendingDeleteSession.id
    setDeletingSession(id)
    try {
      await deleteSession(pendingDeleteSession.id)
      if (activeSession === id) setActiveSession(null)
      sessionFeed.reload()
      filteredAgentFeed.reload()
      setPendingDeleteSession(null)
    } finally {
      deletingSessionRef.current = false
      setDeletingSession(null)
    }
  }

  const handleExitTerminal = async () => {
    if (!pendingExit) return
    const id = pendingExit.id
    setExitingTerminal(id)
    try {
      const result = await api.exitTerminal(id)
      if (!result.success) {
        showSnackbar({ type: 'error', message: result.message })
        return
      }
      activeSessionFeed.reload()
      filteredAgentFeed.reload()
      sessionFeed.reload()
      showSnackbar({ type: 'success', message: result.message })
      setPendingExit(null)
    } catch (error: any) {
      showSnackbar({ type: 'error', message: error.message || `Failed to send exit to terminal ${id}` })
    } finally {
      setExitingTerminal(null)
    }
  }

  useEffect(() => {
    api.listProfiles()
      .then(p => { setProfiles(p); setLoadingProfiles(false) })
      .catch(() => setLoadingProfiles(false))
  }, [])

  useEffect(() => { api.listProjects().then(setProjects).catch(() => setProjects([])) }, [])

  const handleCreate = async () => {
    if (!profile.trim() || creatingRef.current) return
    creatingRef.current = true
    setSpawnError(null)
    setCreating(true)
    try {
      const selectedSessionName = sessionName.trim().replace(/\./g, '_') || undefined
      const selectedProfile = profiles.find(item => item.name === profile.trim())
      let ownerGrant
      if (selectedProfile?.owner_authorization_required) {
        if (!ownerConfirmed || !operatorSecret) {
          throw new Error('Confirm the exceptional XHigh launch and authenticate as operator.')
        }
        await api.createOperatorSession(operatorSecret)
        ownerGrant = await api.createXHighGrant({
          agent_profile: profile.trim(),
          provider,
          requested_session_name: selectedSessionName,
          project_id: projectId || undefined,
          launch_mode: 'new_session',
          confirmed: true,
        })
      }
      if (ownerGrant) {
        await createSession(provider, profile.trim(), selectedSessionName, undefined, projectId, ownerGrant)
      } else if (projectId) {
        await createSession(provider, profile.trim(), selectedSessionName, undefined, projectId)
      } else {
        await createSession(provider, profile.trim(), selectedSessionName, undefined)
      }
      setShowSpawnModal(false)
      setProfile('')
      setSessionName('')
      setOwnerConfirmed(false)
      setOperatorSecret('')
      sessionFeed.reload()
    } catch (e: any) {
      setSpawnError(e.message || 'Failed to create session')
    } finally {
      creatingRef.current = false
      setCreating(false)
    }
  }

  const openSpawnModal = () => {
    setProjectId(projects.find(project => project.isDefault)?.projectId || '')
    setShowSpawnModal(true)
    setOwnerConfirmed(false)
    setOperatorSecret('')
  }

  useEffect(() => {
    if (navigationIntent !== 'create-session') {
      consumedNavigationIntent.current = null
      return
    }
    if (consumedNavigationIntent.current === navigationIntent) return
    consumedNavigationIntent.current = navigationIntent
    openSpawnModal()
    onNavigationIntentConsumed?.()
  }, [navigationIntent, projects, onNavigationIntentConsumed])

  const selectedSpawnProject = projects.find(project => project.projectId === projectId)
  const selectedSpawnProfile = profiles.find(item => item.name === profile.trim())
  const privilegedSpawn = selectedSpawnProfile?.owner_authorization_required === true

  const openTerminal = (terminalId: string, provider?: string, agentProfile?: string | null) => {
    setLiveTerminal({ id: terminalId, provider, agentProfile })
  }

  const currentSessionWorkingDirectory = sessionRootWorkDir || ''
  const selectedAddProfile = profiles.find(item => item.name === addProfile.trim())
  const selectedAddProject = projects.find(item => item.projectId === addProjectId)
  const resolvedAddWorkingDirectory = selectedAddProject?.path || currentSessionWorkingDirectory
  const privilegedAdd = selectedAddProfile?.owner_authorization_required === true

  const resetAddAuthorization = () => {
    setAddOwnerConfirmed(false)
    setAddOperatorSecret('')
    setAddError(null)
  }

  const openAddAgent = () => {
    setAddProvider(defaultProvider(providers))
    setAddProfile('')
    // Add Agent inherits server-side session/cwd context unless the user
    // deliberately picks a project.  A global default is not an explicit
    // override for an existing session.
    setAddProjectId('')
    resetAddAuthorization()
    setShowAddAgent(true)
  }

  useEffect(() => {
    if (!showAddAgent || !activeSessionIdentifier) {
      setSessionRootWorkDir(null)
      return
    }
    let disposed = false
    setSessionRootWorkDir(null)
    api.getSessionWorkingDirectory(activeSessionIdentifier)
      .then(result => { if (!disposed) setSessionRootWorkDir(result.working_directory) })
      .catch(() => { if (!disposed) setSessionRootWorkDir(null) })
    return () => { disposed = true }
  }, [showAddAgent, activeSessionIdentifier])

  const handleAddAgent = async () => {
    if (!addProfile.trim() || !activeSessionIdentifier || activeSessionRecord?.status !== 'active') return
    setAddingAgent(true)
    setAddError(null)
    try {
      const authorization = authorizeOwnerLaunch({
        selectedProfile: selectedAddProfile,
        provider: addProvider,
        workingDirectory: resolvedAddWorkingDirectory || undefined,
        requestedSessionName: activeSessionIdentifier,
        projectId: addProjectId || undefined,
        confirmed: addOwnerConfirmed,
        operatorSecret: addOperatorSecret,
      })
      const ownerGrant = authorization ? await authorization : undefined
      if (ownerGrant) {
        await api.addTerminalToSession(activeSessionIdentifier, addProvider, addProfile.trim(), resolvedAddWorkingDirectory || undefined, addProjectId || undefined, ownerGrant)
      } else if (addProjectId) {
        await api.addTerminalToSession(activeSessionIdentifier, addProvider, addProfile.trim(), resolvedAddWorkingDirectory || undefined, addProjectId)
      } else {
        await api.addTerminalToSession(activeSessionIdentifier, addProvider, addProfile.trim(), resolvedAddWorkingDirectory || undefined)
      }
      showSnackbar({ type: 'success', message: 'Agent added to session' })
      setShowAddAgent(false)
      setAddProfile('')
      resetAddAuthorization()
      activeSessionFeed.reload()
      filteredAgentFeed.reload()
      sessionFeed.reload()
    } catch (e: any) {
      const message = e.message || 'Failed to add agent'
      setAddError(message)
      showSnackbar({ type: 'error', message })
    }
    setAddingAgent(false)
  }

  // Group profiles by source
  const profilesBySource = profiles.reduce<Record<string, AgentProfileInfo[]>>((acc, p) => {
    const key = p.source || 'unknown'
    if (!acc[key]) acc[key] = []
    acc[key].push(p)
    return acc
  }, {})

  const providerStatusChoices = filteredAgentFeed.latestPage?.facets.activities || []
  const workflowStatusChoices = filteredAgentFeed.latestPage?.facets.workflow_states || []
  const canonicalProfileNames = filteredAgentFeed.latestPage?.facets.profiles || profiles.map(item => item.name)
  const matchingAgents = filteredAgentFeed.items
  const matchingSessionIds = Array.from(new Set(matchingAgents.map(item => item.session_id)))
  const hasActiveFilters = agentViewMode === 'statuses'
    ? providerStatusFilters.length > 0 || workflowStatusFilters.length > 0 || homeFilter !== null
    : profileFilters.length > 0
  const clearCurrentFilters = () => {
    if (agentViewMode === 'statuses') {
      setProviderStatusFilters([])
      setWorkflowStatusFilters([])
      setHomeFilter(null)
      updateUrlFilters({ providerStatuses: [], workflowStates: [], homeFilter: null })
    }
    if (agentViewMode === 'profiles') {
      setProfileFilters([])
      updateUrlFilters({ profiles: [] })
    }
  }

  const renderAgentCard = (terminal: AgentSummary, sessionName?: string, grid = false) => (
    <div key={terminal.id} data-testid={`agent-detail-card-${terminal.id}`} className="bg-gray-900/50 border border-gray-700/30 rounded-lg p-3 space-y-2">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 min-w-0">
          <TermIcon size={14} className="text-gray-400" />
          <span className="text-sm font-mono text-gray-300 truncate min-w-0 max-w-full" title={terminal.id}>{terminal.id}</span>
          <StatusBadge status={terminalBadgeStatus(terminal)} />
          <span className="text-xs text-gray-500 truncate max-w-full" title={terminal.provider}>{terminal.provider}</span>
          {terminal.agent_profile && <span className="text-xs text-emerald-400 truncate max-w-full" title={terminal.agent_profile}>{terminal.agent_profile}</span>}
          {sessionName && <span className="text-xs text-gray-600 truncate max-w-full" title={sessionDisplayName(sessionName)}>Session: {sessionDisplayName(sessionName)}</span>}
        </div>
        <div className={`grid grid-cols-2 gap-2 w-full ${grid ? '' : 'sm:flex sm:w-auto'}`}>
          <button onClick={() => setInboxTerminalId(terminal.id)} className="min-h-11 justify-center flex items-center gap-2 px-3 py-1.5 bg-gray-700 hover:bg-gray-600 text-white text-xs font-medium rounded-lg transition-colors" title="View inbox"><Mail size={14} />Inbox</button>
          <button onClick={() => openTerminal(terminal.id, terminal.provider, terminal.agent_profile)} className="min-h-11 justify-center flex items-center gap-2 px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-medium rounded-lg transition-colors" title="Open live terminal"><Monitor size={14} />Open Terminal</button>
          <button onClick={() => setOutputTerminalId(terminal.id)} className="min-h-11 justify-center flex items-center gap-2 px-3 py-1.5 bg-gray-700 hover:bg-gray-600 text-white text-xs font-medium rounded-lg transition-colors" title="View output"><FileText size={14} />Output</button>
          <button onClick={() => setPendingExit(toTerminalMeta(terminal))} disabled={exitingTerminal === terminal.id || terminal.lifecycle === 'exited'} className="min-h-11 justify-center flex items-center gap-2 px-3 py-1.5 bg-amber-600 hover:bg-amber-500 disabled:opacity-40 text-white text-xs font-medium rounded-lg transition-colors" title="Graceful exit"><LogOut size={14} />{exitingTerminal === terminal.id ? 'Exiting...' : 'Graceful Exit'}</button>
          <button onClick={() => setPendingClose(toTerminalMeta(terminal))} disabled={closingTerminal === terminal.id || terminal.lifecycle !== 'exited'} className="min-h-11 justify-center flex items-center gap-2 px-3 py-1.5 bg-red-600 hover:bg-red-500 disabled:opacity-40 text-white text-xs font-medium rounded-lg transition-colors" title={terminal.lifecycle === 'exited' ? 'Delete exited terminal history' : 'Gracefully exit this terminal before deleting it'}><Trash2 size={14} />{closingTerminal === terminal.id ? 'Deleting...' : 'Delete'}</button>
        </div>
      </div>
      {terminal.launch_worktree && <div className="flex items-center gap-1.5" title={terminal.launch_worktree}><FolderOpen size={12} className="text-gray-600 shrink-0" /><span className="text-xs font-mono text-gray-500 truncate max-w-[400px]">{terminal.launch_worktree}</span></div>}
      <button onClick={() => openTerminal(terminal.id, terminal.provider, terminal.agent_profile)} className="text-xs text-gray-500 hover:text-gray-300 transition-colors">Open Workflow Composer</button>
    </div>
  )

  const renderSessionDetail = (session: SessionSummary) => (
    <div
      id={`session-detail-${session.id}`}
      data-testid={`agent-session-detail-${session.id}`}
      className="border-t border-gray-700/50 px-3 pb-3 pt-2 sm:px-5 sm:pb-5"
    >
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-4">
        <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide break-words">
          Terminals in {sessionDisplayName(session.name)}
        </h3>
        <button
          onClick={showAddAgent ? () => setShowAddAgent(false) : openAddAgent}
          disabled={session.status !== 'active'}
          className="min-h-11 self-start sm:self-auto flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-gray-400 hover:text-emerald-400 bg-gray-900/50 hover:bg-gray-900 border border-gray-700/50 hover:border-emerald-700/50 rounded-lg transition-colors disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:text-gray-400"
          title={session.status === 'active' ? 'Add another agent to this session so they can collaborate' : 'Historical sessions cannot accept new agents'}
        >
          <Plus size={14} />
          Add Agent
        </button>
      </div>

      {/* Add Agent Inline Form */}
      {showAddAgent && session.status === 'active' && (
        <div className="mb-4 p-4 bg-gray-900/70 border border-gray-700/50 rounded-lg space-y-3">
          <p className="text-xs text-gray-500">
            Add another agent to this session. Agents in the same session can send messages to each other and coordinate on tasks. A supervisor can delegate work to agents you add here.
          </p>
          <div className="flex gap-3 items-end flex-wrap">
            <div className="min-w-[160px]">
              <label className="block text-xs text-gray-500 mb-1">Provider</label>
              <CustomSelect
                value={addProvider}
                onChange={setAddProvider}
                placeholder="Select provider..."
                options={(providers.length > 0 ? providers : UNAVAILABLE_PROVIDER_FALLBACK).map(providerSelectOption)}
              />
            </div>
            <div className="flex-1 min-w-[180px]">
              <label className="block text-xs text-gray-500 mb-1">Agent Profile</label>
              {profiles.length > 0 ? (
                <ProfilePicker
                  value={addProfile}
                  onChange={value => { setAddProfile(value); resetAddAuthorization() }}
                  profiles={profiles}
                />
              ) : (
                <input
                  type="text"
                  value={addProfile}
                  onChange={e => { setAddProfile(e.target.value); resetAddAuthorization() }}
                  placeholder="e.g. developer, reviewer"
                  className="w-full bg-gray-900 border border-gray-700 text-gray-200 text-sm rounded-lg px-3 py-2.5 focus:border-emerald-500 focus:outline-none"
                />
              )}
            </div>
            <button
              onClick={handleAddAgent}
              disabled={!addProfile.trim() || addingAgent || (privilegedAdd && (!addOwnerConfirmed || !addOperatorSecret))}
              className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white text-xs font-medium px-4 py-2 rounded-lg transition-colors"
            >
              <Plus size={14} />
              {addingAgent ? 'Adding...' : 'Add'}
            </button>
          </div>
          {privilegedAdd && (
            <XHighAuthorizationBlock confirmed={addOwnerConfirmed} operatorSecret={addOperatorSecret} onConfirmedChange={setAddOwnerConfirmed} onOperatorSecretChange={setAddOperatorSecret}/>
          )}
          <div>
            <label className="block text-xs text-gray-500 mb-1">Project <span className="text-gray-600">(optional)</span></label>
            <ProjectPicker projects={projects} value={addProjectId} onChange={setAddProjectId} />
            {projects.length === 0 && <p aria-live="polite" className="mt-1 text-xs text-amber-400">No projects are configured. This agent will use the legacy working directory.</p>}
            <p data-testid="add-agent-resolved-working-directory" className="mt-1 min-w-0 break-all font-mono text-xs text-gray-500">{resolvedAddWorkingDirectory || 'ThreadCells server default'}</p>
          </div>
          {addError && <div role="alert" className="whitespace-pre-line rounded-lg border border-red-700/50 bg-red-950/40 px-3 py-2 text-sm text-red-300">{addError}</div>}
        </div>
      )}

      {activeSessionFeed.error && <p role="alert" className="text-xs text-red-300">Unable to refresh this session’s agent summary.</p>}
      {activeSessionFeed.loading && activeSessionFeed.items.length === 0 ? <p className="text-sm text-gray-500">Loading agents…</p> : <div data-testid={`agent-session-agent-container-${session.id}`} className={sessionAgentViews[session.id] === 'grid' ? 'space-y-2 md:grid md:grid-cols-2 md:gap-2 md:space-y-0' : 'space-y-2'}>{activeSessionFeed.items.map(terminal => renderAgentCard(terminal, undefined, sessionAgentViews[session.id] === 'grid'))}</div>}
      {activeSessionFeed.nextOffset !== null && <div ref={activeAgentSentinelRef} className="flex justify-center pt-3"><button type="button" onClick={activeSessionFeed.loadMore} disabled={activeSessionFeed.loading} className="min-h-10 rounded-lg border border-gray-700 px-4 text-xs text-gray-300 hover:border-emerald-700 disabled:opacity-40">{activeSessionFeed.loading ? 'Loading…' : `Load more agents (${activeSessionFeed.items.length} of ${activeSessionFeed.total})`}</button></div>}
      {activeSessionFeed.limitReached && <p className="pt-3 text-center text-xs text-gray-500">Showing the 100 most recent agents in this session. Use the Status or Profile views to narrow older history.</p>}
    </div>
  )

  return (
    <div className="space-y-6">
      <div className="bg-gray-800/60 border border-gray-700/50 rounded-xl p-2" role="tablist" aria-label="Agent views">
        <div className="grid grid-cols-3 gap-1">
          {(['sessions', 'statuses', 'profiles'] as AgentViewMode[]).map(mode => (
            <button
              key={mode}
              role="tab"
              aria-selected={agentViewMode === mode}
              onClick={() => setView(mode)}
              className={`min-h-11 rounded-lg px-3 text-sm font-medium transition-colors ${agentViewMode === mode ? 'bg-emerald-600 text-white' : 'text-gray-400 hover:bg-gray-700/60 hover:text-gray-200'}`}
            >
              {mode[0].toUpperCase() + mode.slice(1)}
            </button>
          ))}
        </div>
      </div>

      {/* Sessions List */}
      {agentViewMode === 'sessions' && <div className="bg-gray-800/60 border border-gray-700/50 rounded-xl p-3 sm:p-5">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-1">
          <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">
            Sessions ({sessionFeed.total})
          </h3>
          <div className="flex flex-col sm:flex-row sm:items-center gap-2 w-full sm:w-auto">
            {(sessionFeed.total > 3 || sessionSearch) && (
              <div className="relative w-full sm:w-auto">
                <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-500" />
                <input
                  type="text"
                  value={sessionSearch}
                  onChange={e => setSessionSearch(e.target.value)}
                  placeholder="Filter sessions..."
                  className="bg-gray-900 border border-gray-700 text-gray-200 text-xs rounded-lg pl-8 pr-3 py-2.5 w-full sm:w-48 focus:border-emerald-500 focus:outline-none"
                />
              </div>
            )}
            <button
              onClick={openSpawnModal}
              className="min-h-11 justify-center flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
            >
              <Plus size={14} />
              Create Session &amp; Spawn Agent
            </button>
          </div>
        </div>
        <p className="text-xs text-gray-500 mb-4">
          A session is a workspace where agents collaborate. Each session can have multiple agents that communicate via messages. Click a session to see its agents.
        </p>
        {sessionFeed.error && <p role="alert" className="mb-3 text-xs text-red-300">Unable to refresh session summaries.</p>}
        {sessionFeed.loading && sessionFeed.items.length === 0 ? (
          <p className="text-gray-500 text-sm">Loading session summaries…</p>
        ) : sessionFeed.items.length === 0 ? (
          <p className="text-gray-500 text-sm">No matching sessions. Create a session above to start an agent.</p>
        ) : (
          <div className="space-y-2">
            {sessionFeed.items.map(s => {
              const expanded = activeSession === s.id
              const hasDetail = expanded
              const toggleSession = () => { setActiveSession(expanded ? null : s.id); setShowAddAgent(false) }
              const displayName = sessionDisplayName(s.name)
              return (
                <div
                  key={s.id}
                  data-testid={`agent-session-${s.id}`}
                  className={`rounded-lg transition-colors ${
                    expanded ? 'bg-emerald-900/30 border border-emerald-700/50' : 'bg-gray-900/50 border border-gray-700/30 hover:bg-gray-800/80'
                  }`}
                >
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-3 cursor-pointer" onClick={toggleSession}>
                    <div
                      role="button"
                      tabIndex={0}
                      aria-expanded={expanded}
                      aria-controls={hasDetail ? `session-detail-${s.id}` : undefined}
                      aria-label={`${expanded ? 'Collapse' : 'Expand'} ${displayName}`}
                      onKeyDown={event => {
                        if (event.key === 'Enter' || event.key === ' ') {
                          event.preventDefault()
                          toggleSession()
                        }
                      }}
                      className="flex min-w-0 w-full cursor-pointer items-center gap-2 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 sm:gap-3"
                    >
                      <Bot size={16} className="shrink-0 text-emerald-400" />
                      <span className="min-w-0 flex-1 truncate font-mono text-sm text-gray-200" title={displayName}>{displayName}</span>
                      <span data-testid={`agent-session-count-${s.id}`} className="shrink-0 text-xs text-gray-500">
                        Agents: {s.agent_count}
                      </span>
                      <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs ${s.status === 'active' ? 'bg-emerald-900/50 text-emerald-400' : 'bg-gray-700 text-gray-400'}`}>
                        {s.status}
                      </span>
                    </div>
                    <div data-testid={`agent-session-actions-${s.id}`} className="flex items-center gap-2 self-end sm:self-auto shrink-0" onClick={event => event.stopPropagation()}>
                      <AgentViewControls value={sessionAgentViews[s.id] || 'list'} onChange={value => setSessionAgentViews(current => ({ ...current, [s.id]: value }))} />
                      <button
                        onClick={event => { event.stopPropagation(); setPendingDeleteSession(s) }}
                        className="min-w-11 min-h-11 inline-flex items-center justify-center text-gray-500 hover:text-red-400 transition-colors rounded-lg hover:bg-gray-800"
                        title="Delete session"
                      >
                        <Trash2 size={14} />
                      </button>
                      <ChevronRight aria-hidden="true" size={14} className={`text-gray-500 transition-transform ${expanded ? 'rotate-90' : ''}`} />
                    </div>
                  </div>
                  {hasDetail && renderSessionDetail(s)}
                </div>
              )
            })}
            {sessionFeed.nextOffset !== null && <div ref={sessionSentinelRef} className="flex justify-center pt-3"><button type="button" onClick={sessionFeed.loadMore} disabled={sessionFeed.loading} className="min-h-11 rounded-lg border border-gray-700 px-5 text-xs text-gray-300 hover:border-emerald-700 disabled:opacity-40">{sessionFeed.loading ? 'Loading…' : `Load more sessions (${sessionFeed.items.length} of ${sessionFeed.total})`}</button></div>}
            {sessionFeed.limitReached && <p className="pt-3 text-center text-xs text-gray-500">Showing the 100 most recent matching sessions. Refine the search to inspect older history.</p>}
          </div>
        )}
      </div>}

      {agentViewMode !== 'sessions' && (
        <div className="space-y-4">
          <div className="bg-gray-800/60 border border-gray-700/50 rounded-xl p-3 sm:p-5 space-y-4">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">{agentViewMode === 'statuses' ? 'Status filters' : 'Profile filters'}</h3>
                <p className="text-xs text-gray-500 mt-1">{agentViewMode === 'statuses' ? 'Select any control-plane activity and any Workflow state. Summary activity comes from durable lifecycle, queue, and execution-lease state; open an agent for provider-native detail.' : 'Select one or more profiles from the current profile metadata.'}</p>
              </div>
              {hasActiveFilters && <button onClick={clearCurrentFilters} className="min-h-11 px-3 py-2 text-xs font-medium text-gray-300 border border-gray-700 rounded-lg hover:border-emerald-700 hover:text-emerald-400">Clear filters</button>}
            </div>
            {agentViewMode === 'statuses' ? (
              <div className="grid gap-4 md:grid-cols-3">
                <fieldset className="min-w-0">
                  <legend className="text-xs font-medium text-gray-400 mb-2">Home shortcut</legend>
                  <div className="flex flex-wrap gap-2" aria-label="Home status filters">
                    {(Object.keys(HOME_FILTER_LABELS) as Array<keyof typeof HOME_FILTER_LABELS>).map(value => <button key={value} aria-pressed={homeFilter === value} onClick={() => { const next = homeFilter === value ? null : value; setHomeFilter(next); updateUrlFilters({ homeFilter: next }) }} className={`min-h-9 rounded-lg border px-3 text-xs ${homeFilter === value ? 'border-emerald-600 bg-emerald-900/30 text-emerald-300' : 'border-gray-700 bg-gray-900/50 text-gray-400 hover:text-gray-200'}`}>{HOME_FILTER_LABELS[value]}</button>)}
                  </div>
                </fieldset>
                <fieldset className="min-w-0">
                  <legend className="text-xs font-medium text-gray-400 mb-2">Control-plane activity</legend>
                  <div className="flex flex-wrap gap-2" aria-label="Control-plane activity filters">
                    {providerStatusChoices.map(value => <button key={value} aria-pressed={providerStatusFilters.includes(value)} onClick={() => { const next = toggleFilter(providerStatusFilters, value); setProviderStatusFilters(next); updateUrlFilters({ providerStatuses: next }) }} className={`min-h-9 rounded-lg border px-3 text-xs font-mono ${providerStatusFilters.includes(value) ? 'border-emerald-600 bg-emerald-900/30 text-emerald-300' : 'border-gray-700 bg-gray-900/50 text-gray-400 hover:text-gray-200'}`}>{value}</button>)}
                    {!filteredAgentFeed.loading && providerStatusChoices.length === 0 && <span className="text-xs text-gray-500">No activity states available.</span>}
                  </div>
                </fieldset>
                <fieldset className="min-w-0">
                  <legend className="text-xs font-medium text-gray-400 mb-2">Workflow state</legend>
                  <div className="flex flex-wrap gap-2" aria-label="Workflow state filters">
                    {workflowStatusChoices.map(value => <button key={value} aria-pressed={workflowStatusFilters.includes(value)} onClick={() => { const next = toggleFilter(workflowStatusFilters, value); setWorkflowStatusFilters(next); updateUrlFilters({ workflowStates: next }) }} className={`min-h-9 rounded-lg border px-3 text-xs font-mono ${workflowStatusFilters.includes(value) ? 'border-emerald-600 bg-emerald-900/30 text-emerald-300' : 'border-gray-700 bg-gray-900/50 text-gray-400 hover:text-gray-200'}`}>{value}</button>)}
                    {!filteredAgentFeed.loading && workflowStatusChoices.length === 0 && <span className="text-xs text-gray-500">No workflow states available.</span>}
                  </div>
                </fieldset>
              </div>
            ) : (
              <fieldset>
                <legend className="text-xs font-medium text-gray-400 mb-2">Agent profiles</legend>
                <div className="flex flex-wrap gap-2" aria-label="Agent profile filters">
                  {canonicalProfileNames.map(name => <button key={name} aria-pressed={profileFilters.includes(name)} onClick={() => { const next = toggleFilter(profileFilters, name); setProfileFilters(next); updateUrlFilters({ profiles: next }) }} className={`min-h-9 rounded-lg border px-3 text-xs ${profileFilters.includes(name) ? 'border-emerald-600 bg-emerald-900/30 text-emerald-300' : 'border-gray-700 bg-gray-900/50 text-gray-400 hover:text-gray-200'}`}>{name}</button>)}
                  {!loadingProfiles && canonicalProfileNames.length === 0 && <span className="text-xs text-gray-500">No profiles available.</span>}
                </div>
              </fieldset>
            )}
          </div>

          <div className="bg-gray-800/60 border border-gray-700/50 rounded-xl p-3 sm:p-5">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-4">
              <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">Matching agents ({matchingAgents.length} of {filteredAgentFeed.total} agents)</h3>
              <p className="text-xs text-gray-500">Loaded {matchingAgents.length} agents in {matchingSessionIds.length} sessions</p>
            </div>
            {filteredAgentFeed.error && <p role="alert" className="mb-3 text-xs text-red-300">Unable to refresh agent summaries.</p>}
            {filteredAgentFeed.loading && matchingAgents.length === 0 ? <p className="text-sm text-gray-500">Loading agents...</p> : matchingAgents.length === 0 ? (
              <div className="py-6 text-center"><p className="text-sm text-gray-400">{hasActiveFilters ? 'No agents match the selected filters.' : 'No agents are available in the current sessions.'}</p>{hasActiveFilters && <button onClick={clearCurrentFilters} className="mt-3 min-h-11 px-3 text-xs font-medium text-emerald-400 hover:text-emerald-300">Clear filters</button>}</div>
            ) : (
              <div className="space-y-4">
                {matchingSessionIds.map(sessionId => {
                  const sessionName = matchingAgents.find(item => item.session_id === sessionId)?.session_name || sessionId
                  return (
                  <section key={sessionId} className="space-y-2" aria-label={`Matching agents in ${sessionDisplayName(sessionName)}`}>
                    <h4 className="text-xs font-mono text-gray-500 truncate" title={sessionDisplayName(sessionName)}>{sessionDisplayName(sessionName)}</h4>
                    {matchingAgents.filter(item => item.session_id === sessionId).map(item => renderAgentCard(item, sessionName))}
                  </section>
                  )
                })}
                {filteredAgentFeed.nextOffset !== null && <div ref={filteredAgentSentinelRef} className="flex justify-center pt-3"><button type="button" onClick={filteredAgentFeed.loadMore} disabled={filteredAgentFeed.loading} className="min-h-11 rounded-lg border border-gray-700 px-5 text-xs text-gray-300 hover:border-emerald-700 disabled:opacity-40">{filteredAgentFeed.loading ? 'Loading…' : `Load more agents (${matchingAgents.length} of ${filteredAgentFeed.total})`}</button></div>}
                {filteredAgentFeed.limitReached && <p className="pt-3 text-center text-xs text-gray-500">Showing the 100 most recent matching agents. Refine the server-side filters to inspect older history.</p>}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Inbox Panel */}
      {inboxTerminalId && (
        <InboxPanel terminalId={inboxTerminalId} onClose={() => setInboxTerminalId(null)} />
      )}

      {/* Live Terminal */}
      {liveTerminal && <Suspense fallback={null}>
        <TerminalView
          terminalId={liveTerminal.id}
          provider={liveTerminal.provider}
          agentProfile={liveTerminal.agentProfile}
          onClose={() => setLiveTerminal(null)}
        />
      </Suspense>}

      {/* Output Viewer Modal */}
      {outputTerminalId && (
        <OutputViewer
          terminalId={outputTerminalId}
          onClose={() => setOutputTerminalId(null)}
        />
      )}

      {/* Close Confirmation Modal */}
      <ConfirmModal
        open={!!pendingClose}
        title="Delete Exited Terminal"
        message="ThreadCells will revalidate exact runtime absence, then permanently delete this exited terminal’s metadata. This action cannot be undone."
        details={pendingClose ? [
          { label: 'Terminal ID', value: pendingClose.id },
          { label: 'Provider', value: pendingClose.provider },
          { label: 'Profile', value: pendingClose.agent_profile || 'none' },
          { label: 'Session', value: sessionDisplayName(pendingClose.tmux_session) },
        ] : []}
        confirmLabel="Delete Terminal"
        variant="danger"
        loading={!!closingTerminal}
        onConfirm={handleDeleteTerminal}
        onCancel={() => setPendingClose(null)}
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

      {/* Graceful Exit Confirmation Modal */}
      <ConfirmModal
        open={!!pendingExit}
        title="Graceful Exit"
        message="This will send the provider-specific exit command (e.g., /exit). The agent will shut down gracefully."
        details={pendingExit ? [
          { label: 'Terminal ID', value: pendingExit.id },
          { label: 'Provider', value: pendingExit.provider },
          { label: 'Profile', value: pendingExit.agent_profile || 'none' },
          { label: 'Session', value: sessionDisplayName(pendingExit.tmux_session) },
        ] : []}
        confirmLabel="Send Exit"
        variant="warning"
        loading={!!exitingTerminal}
        onConfirm={handleExitTerminal}
        onCancel={() => setPendingExit(null)}
      />

      {/* Create Session Modal */}
      {showSpawnModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setShowSpawnModal(false)} />
          <div className="relative bg-gray-800 border border-gray-700 rounded-2xl shadow-2xl shadow-black/50 w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto">
            {/* Modal header */}
            <div className="flex items-center justify-between p-5 border-b border-gray-700/50">
              <div>
                <h3 className="text-base font-semibold text-gray-200">Create Session &amp; Spawn Agent</h3>
                <p className="text-xs text-gray-500 mt-1">
                  Launch a new AI agent in its own isolated tmux session.
                </p>
              </div>
              <button
                onClick={() => setShowSpawnModal(false)}
                className="p-1.5 text-gray-500 hover:text-gray-300 transition-colors rounded-lg hover:bg-gray-700/50"
              >
                <X size={18} />
              </button>
            </div>

            {/* Modal body */}
            <div className="p-5 space-y-4">
              <div>
                <label className="block text-xs text-gray-500 mb-1">Provider</label>
                <CustomSelect
                  value={provider}
                  onChange={setProvider}
                  placeholder="Select provider..."
                  options={(providers.length > 0 ? providers : UNAVAILABLE_PROVIDER_FALLBACK).map(providerSelectOption)}
                />
              </div>

              <div>
                <label className="block text-xs text-gray-500 mb-1">Agent Profile</label>
                {loadingProfiles ? (
                  <div className="bg-gray-900 border border-gray-700 text-gray-500 text-sm rounded-lg px-3 py-2.5">Loading profiles...</div>
                ) : profiles.length > 0 ? (
                  <ProfilePicker
                    value={profile}
                    onChange={setProfile}
                    profiles={profiles}
                  />
                ) : (
                  <input
                    type="text"
                    value={profile}
                    onChange={e => setProfile(e.target.value)}
                    placeholder="e.g. developer, reviewer"
                    className="w-full bg-gray-900 border border-gray-700 text-gray-200 text-sm rounded-lg px-3 py-2.5 focus:border-emerald-500 focus:outline-none"
                  />
                )}
              </div>

              {privilegedSpawn && (
                <div role="alert" className="rounded-lg border border-amber-500/50 bg-amber-950/30 p-3 text-sm text-amber-100">
                  <div className="flex items-start gap-2">
                    <ShieldAlert size={18} className="mt-0.5 shrink-0 text-amber-300" aria-hidden="true" />
                    <div>
                      <p className="font-semibold">Exceptional XHigh owner-executor</p>
                      <p className="mt-1 text-xs text-amber-200/80">Highest-capability, high-cost profile for direct critical architecture and implementation. This authorization applies only to this one launch.</p>
                    </div>
                  </div>
                  <label className="mt-3 flex min-h-11 items-center gap-2 text-xs">
                    <input aria-label="Confirm exceptional XHigh launch" type="checkbox" checked={ownerConfirmed} onChange={event => setOwnerConfirmed(event.target.checked)} />
                    I explicitly authorize this XHigh owner launch
                  </label>
                  <label className="mt-2 block text-xs text-amber-200/80">Operator secret
                    <input aria-label="Operator secret" type="password" autoComplete="current-password" value={operatorSecret} onChange={event => setOperatorSecret(event.target.value)} className="mt-1 min-h-11 w-full rounded-lg border border-amber-700/60 bg-gray-950 px-3 text-sm text-gray-100 focus:border-amber-400 focus:outline-none" />
                  </label>
                </div>
              )}

              <div>
                <label className="block text-xs text-gray-500 mb-1">Session name <span className="text-gray-600">(optional)</span></label>
                <input
                  type="text"
                  value={sessionName}
                  onChange={e => setSessionName(e.target.value.replace(/\./g, '_'))}
                  onKeyDown={e => e.key === 'Enter' && handleCreate()}
                  placeholder="e.g. THREADCELLS-UI-IMPLEMENTATION"
                  className="w-full bg-gray-900 border border-gray-700 text-gray-200 text-sm font-mono rounded-lg px-3 py-2.5 focus:border-emerald-500 focus:outline-none"
                />
              </div>

              <div>
                <label className="block text-xs text-gray-500 mb-1">Project <span className="text-gray-600">(optional)</span></label>
                <ProjectPicker projects={projects} value={projectId} onChange={setProjectId} />
                {selectedSpawnProject ? (
                  <p aria-live="polite" className="mt-1 min-w-0 break-all font-mono text-xs text-gray-500" title={selectedSpawnProject.path}>{selectedSpawnProject.path}</p>
                ) : projects.length === 0 ? (
                  <p aria-live="polite" className="mt-1 text-xs text-amber-400">No projects are configured. This launch will use the default working directory.</p>
                ) : null}
              </div>

              {/* Quick-pick profiles */}
              {profiles.length > 0 && (
                <div>
                  <label className="block text-xs text-gray-500 mb-2">Quick pick</label>
                  <div className="grid grid-cols-2 gap-1.5 max-h-40 overflow-y-auto">
                    {profiles.slice(0, 12).map(p => (
                      <button
                        key={`${p.source}-${p.name}`}
                        onClick={() => setProfile(p.name)}
                        className={`text-left px-2.5 py-2 rounded-lg border text-xs transition-all ${
                          profile === p.name
                            ? 'bg-emerald-900/30 border-emerald-700/50 text-emerald-300'
                            : 'bg-gray-900/50 border-gray-700/30 hover:bg-gray-800/80 text-gray-300'
                        }`}
                      >
                        <span className="font-medium">{p.name}</span>
                        <span className="block text-[10px] text-gray-500">{p.description || 'No description provided'}</span>
                        <span className="text-[10px] text-gray-600">{SOURCE_LABELS[p.source] || p.source}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {spawnError && (
              <div role="alert" className="mx-5 mb-0 whitespace-pre-line rounded-lg border border-red-700/50 bg-red-950/40 px-3 py-2 text-sm text-red-300">
                {spawnError}
              </div>
            )}

            {/* Modal footer */}
            <div className="flex items-center justify-end gap-3 p-5 border-t border-gray-700/50">
              <button
                onClick={() => setShowSpawnModal(false)}
                className="px-4 py-2 text-sm text-gray-400 hover:text-gray-200 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleCreate}
                disabled={!profile.trim() || creating || (privilegedSpawn && (!ownerConfirmed || !operatorSecret))}
                aria-busy={creating}
                className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white text-sm font-medium px-5 py-2.5 rounded-lg transition-colors"
              >
                {creating ? <LoaderCircle size={14} className="animate-spin" /> : <Play size={14} />}
                {creating ? 'Creating...' : 'Create Session'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
