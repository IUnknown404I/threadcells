import { lazy, Suspense, useState, useEffect, useRef } from 'react'
import { useStore } from '../store'
import { AgentSummary, AgentProfileInfo, OwnerLaunchGrant, Project, ProviderInfo, RecoveryTakeoverPreview, Session, SessionSummary, TerminalMeta, api } from '../api'
import { Bot, Play, Trash2, ChevronRight, Terminal as TermIcon, Monitor, Package, FolderOpen, Search, Mail, Plus, LogOut, FileText, X, LoaderCircle, ShieldAlert } from 'lucide-react'
import { ConfirmModal } from './ConfirmModal'
import { InboxPanel } from './InboxPanel'
import { CustomSelect, SelectOption } from './CustomSelect'
import { StatusBadge, lifecycleBadgeStatus, sessionStatusTranslationKey, statusTranslationKey } from './StatusBadge'
import { OutputViewer } from './OutputViewer'
import { ProfilePicker } from './ProfilePicker'
import { ProjectPicker } from './ProjectPicker'
import { AgentViewMode, AgentFilterState, type HomeAgentFilter, applyAgentFilterState, parseAgentFilterState } from '../agentFilters'
import { sessionDisplayName } from '../sessionDisplayName'
import { providerIsAvailable, providerSelectOption } from '../providerAvailability'
import { useAgentSummaryFeed, useNearViewport, useSessionSummaryFeed } from '../uiReadModels'
import { AgentViewControls, type AgentViewLayout } from './AgentViewControls'
import { useI18n, type TranslationKey } from '../i18n'
import { ProviderOutcomeNotice } from './ProviderOutcomeNotice'

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

const SOURCE_LABELS: Record<string, TranslationKey> = {
  'built-in': 'profiles.source.builtIn',
  'local': 'profiles.source.local',
}

const HOME_FILTER_KEYS: Record<HomeAgentFilter, TranslationKey> = {
  all: 'agents.home.all',
  active: 'agents.home.active',
  waiting: 'agents.home.waiting',
  owner_gate: 'agents.home.attention',
  cancelled: 'agents.home.cancelled',
  completed: 'agents.home.completed',
}

function XHighAuthorizationBlock({ confirmed, operatorSecret, onConfirmedChange, onOperatorSecretChange }: {
  confirmed: boolean
  operatorSecret: string
  onConfirmedChange: (confirmed: boolean) => void
  onOperatorSecretChange: (secret: string) => void
}) {
  const { t } = useI18n()
  return <div role="alert" className="rounded-lg border border-amber-500/50 bg-amber-950/30 p-3 text-sm text-amber-100">
    <div className="flex items-start gap-2"><ShieldAlert size={18} className="mt-0.5 shrink-0 text-amber-300"/><div><p className="font-semibold">{t('agents.xhighTitle')}</p><p className="mt-1 text-xs text-amber-200/80">{t('agents.xhighHelp')}</p></div></div>
    <label className="mt-3 flex min-h-11 items-center gap-2 text-xs"><input aria-label={t('agents.xhighConfirmAria')} type="checkbox" checked={confirmed} onChange={event => onConfirmedChange(event.target.checked)}/>{t('agents.xhighConfirm')}</label>
    <label className="mt-2 block text-xs text-amber-200/80">{t('agents.operatorSecret')}<input aria-label={t('agents.operatorSecret')} type="password" autoComplete="current-password" value={operatorSecret} onChange={event => onOperatorSecretChange(event.target.value)} className="mt-1 min-h-11 w-full rounded-lg border border-amber-700/60 bg-gray-950 px-3 text-sm text-gray-100 focus:border-amber-400 focus:outline-none"/></label>
  </div>
}

function authorizeOwnerLaunch({ selectedProfile, provider, workingDirectory, requestedSessionName, projectId, confirmed, operatorSecret, authorizationError }: {
  selectedProfile: AgentProfileInfo | undefined
  provider: string
  workingDirectory?: string
  requestedSessionName?: string
  projectId?: string
  confirmed: boolean
  operatorSecret: string
  authorizationError: string
}): Promise<OwnerLaunchGrant> | undefined {
  if (!selectedProfile?.owner_authorization_required) return undefined
  if (!confirmed || !operatorSecret) throw new Error(authorizationError)
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
  const { t } = useI18n()
  const [activeSession, setActiveSession] = useState<string | null>(null)
  const [provider, setProvider] = useState('codex')
  const [profile, setProfile] = useState('')
  const [creating, setCreating] = useState(false)
  const creatingRef = useRef(false)
  const workContextRequestRef = useRef<{ key: string; id: string } | null>(null)
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
  const [pendingRecovery, setPendingRecovery] = useState<AgentSummary | null>(null)
  const [recoveryPreview, setRecoveryPreview] = useState<RecoveryTakeoverPreview | null>(null)
  const [recoveryOperatorSecret, setRecoveryOperatorSecret] = useState('')
  const [recoveryProfile, setRecoveryProfile] = useState('critical_sol_xhigh_owner')
  const [recoveryProvider, setRecoveryProvider] = useState('codex')
  const [recoveryConfirmed, setRecoveryConfirmed] = useState(false)
  const [recoveryInspecting, setRecoveryInspecting] = useState(false)
  const [recoverySubmitting, setRecoverySubmitting] = useState(false)
  const [recoveryError, setRecoveryError] = useState<string | null>(null)
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
      showSnackbar({ type: 'success', message: t('agents.deleted', { id }) })
    } catch (error: any) {
      showSnackbar({ type: 'error', message: error.message || t('agents.closeFailed', { id }) })
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
      showSnackbar({ type: 'error', message: error.message || t('agents.exitFailed', { id }) })
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
      const workContextRequestKey = projectId
        ? JSON.stringify([projectId, provider, profile.trim(), selectedSessionName || ''])
        : null
      if (
        workContextRequestKey
        && workContextRequestRef.current?.key !== workContextRequestKey
      ) {
        workContextRequestRef.current = {
          key: workContextRequestKey,
          id: crypto.randomUUID(),
        }
      }
      const workContextRequestId = workContextRequestKey
        ? workContextRequestRef.current?.id
        : undefined
      const selectedProfile = profiles.find(item => item.name === profile.trim())
      let ownerGrant
      if (selectedProfile?.owner_authorization_required) {
        if (!ownerConfirmed || !operatorSecret) {
          throw new Error(t('agents.xhighError'))
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
        await createSession(provider, profile.trim(), selectedSessionName, undefined, projectId, ownerGrant, workContextRequestId)
      } else if (projectId) {
        await createSession(provider, profile.trim(), selectedSessionName, undefined, projectId, undefined, workContextRequestId)
      } else {
        await createSession(provider, profile.trim(), selectedSessionName, undefined)
      }
      setShowSpawnModal(false)
      workContextRequestRef.current = null
      setProfile('')
      setSessionName('')
      setOwnerConfirmed(false)
      setOperatorSecret('')
      sessionFeed.reload()
    } catch (e: any) {
      setSpawnError(e.message || t('agents.createFailed'))
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

  const openRecoveryTakeover = (terminal: AgentSummary) => {
    setPendingRecovery(terminal)
    setRecoveryPreview(null)
    setRecoveryOperatorSecret('')
    setRecoveryProfile('critical_sol_xhigh_owner')
    setRecoveryProvider(defaultProvider(providers))
    setRecoveryConfirmed(false)
    setRecoveryError(null)
  }

  const inspectRecoveryTakeover = async () => {
    if (!pendingRecovery || !recoveryOperatorSecret || recoveryInspecting) return
    setRecoveryInspecting(true)
    setRecoveryError(null)
    try {
      await api.createOperatorSession(recoveryOperatorSecret)
      setRecoveryPreview(await api.getRecoveryTakeoverPreview(pendingRecovery.id))
    } catch (error: any) {
      setRecoveryError(error.message || t('agents.recoverFailed'))
    } finally {
      setRecoveryOperatorSecret('')
      setRecoveryInspecting(false)
    }
  }

  const submitRecoveryTakeover = async () => {
    if (!pendingRecovery || !recoveryPreview?.eligible || !recoveryPreview.terminal || !recoveryConfirmed || recoverySubmitting) return
    setRecoverySubmitting(true)
    setRecoveryError(null)
    try {
      const ownerGrant = await api.createXHighGrant({
        agent_profile: recoveryProfile,
        provider: recoveryProvider,
        project_id: recoveryPreview.terminal.project_id,
        launch_mode: 'recovery_takeover',
        target_terminal_id: pendingRecovery.id,
        expected_authority_generation: recoveryPreview.terminal.writer_authority_generation,
        expected_runtime_generation: recoveryPreview.terminal.runtime_generation,
        confirmed: true,
      })
      const takeover = await api.createRecoveryTakeover(pendingRecovery.id, {
        request_id: crypto.randomUUID(),
        expected_authority_generation: recoveryPreview.terminal.writer_authority_generation,
        expected_runtime_generation: recoveryPreview.terminal.runtime_generation,
        agent_profile: recoveryProfile,
        provider: recoveryProvider,
        owner_grant_launch_id: ownerGrant.launch_id,
      }, ownerGrant)
      if (takeover.state !== 'completed') {
        throw new Error(t('agents.recoverPending', { state: takeover.state }))
      }
      showSnackbar({ type: 'success', message: t('agents.recoverSucceeded', { id: takeover.new_terminal_id }) })
      setPendingRecovery(null)
      activeSessionFeed.reload()
      filteredAgentFeed.reload()
      sessionFeed.reload()
    } catch (error: any) {
      setRecoveryError(error.message || t('agents.recoverFailed'))
    } finally {
      setRecoverySubmitting(false)
    }
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
        authorizationError: t('agents.xhighError'),
      })
      const ownerGrant = authorization ? await authorization : undefined
      if (ownerGrant) {
        await api.addTerminalToSession(activeSessionIdentifier, addProvider, addProfile.trim(), resolvedAddWorkingDirectory || undefined, addProjectId || undefined, ownerGrant)
      } else if (addProjectId) {
        await api.addTerminalToSession(activeSessionIdentifier, addProvider, addProfile.trim(), resolvedAddWorkingDirectory || undefined, addProjectId)
      } else {
        await api.addTerminalToSession(activeSessionIdentifier, addProvider, addProfile.trim(), resolvedAddWorkingDirectory || undefined)
      }
      showSnackbar({ type: 'success', message: t('agents.added') })
      setShowAddAgent(false)
      setAddProfile('')
      resetAddAuthorization()
      activeSessionFeed.reload()
      filteredAgentFeed.reload()
      sessionFeed.reload()
    } catch (e: any) {
      const message = e.message || t('agents.addFailed')
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
          {sessionName && <span className="text-xs text-gray-600 truncate max-w-full" title={sessionDisplayName(sessionName)}>{t('agents.sessionLabel', { name: sessionDisplayName(sessionName) })}</span>}
        </div>
        <div className={`grid grid-cols-2 gap-2 w-full ${grid ? '' : 'sm:flex sm:w-auto'}`}>
          <button onClick={() => setInboxTerminalId(terminal.id)} className="min-h-11 justify-center flex items-center gap-2 px-3 py-1.5 bg-gray-700 hover:bg-gray-600 text-white text-xs font-medium rounded-lg transition-colors" title={t('agents.viewInbox')}><Mail size={14} />{t('agents.inbox')}</button>
          <button onClick={() => openTerminal(terminal.id, terminal.provider, terminal.agent_profile)} className="min-h-11 justify-center flex items-center gap-2 px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-medium rounded-lg transition-colors" title={t('agents.openLiveTerminal')}><Monitor size={14} />{t('agents.openTerminal')}</button>
          <button onClick={() => setOutputTerminalId(terminal.id)} className="min-h-11 justify-center flex items-center gap-2 px-3 py-1.5 bg-gray-700 hover:bg-gray-600 text-white text-xs font-medium rounded-lg transition-colors" title={t('agents.viewOutput')}><FileText size={14} />{t('agents.output')}</button>
          {terminal.context_role === 'supervisor' && terminal.projectId && terminal.lifecycle !== 'exited' && terminal.lifecycle !== 'recovery_fenced' && <button onClick={() => openRecoveryTakeover(terminal)} className="min-h-11 justify-center flex items-center gap-2 px-3 py-1.5 bg-indigo-700 hover:bg-indigo-600 text-white text-xs font-medium rounded-lg transition-colors" title={t('agents.recoverTitle')}><ShieldAlert size={14} />{t('agents.recoverTakeover')}</button>}
          <button onClick={() => setPendingExit(toTerminalMeta(terminal))} disabled={exitingTerminal === terminal.id || terminal.lifecycle === 'exited' || terminal.lifecycle === 'recovery_fenced'} className="min-h-11 justify-center flex items-center gap-2 px-3 py-1.5 bg-amber-600 hover:bg-amber-500 disabled:opacity-40 text-white text-xs font-medium rounded-lg transition-colors" title={t('agents.gracefulExitTitle')}><LogOut size={14} />{exitingTerminal === terminal.id ? t('agents.exiting') : t('agents.gracefulExit')}</button>
          <button onClick={() => setPendingClose(toTerminalMeta(terminal))} disabled={closingTerminal === terminal.id || terminal.lifecycle !== 'exited'} className="min-h-11 justify-center flex items-center gap-2 px-3 py-1.5 bg-red-600 hover:bg-red-500 disabled:opacity-40 text-white text-xs font-medium rounded-lg transition-colors" title={terminal.lifecycle === 'exited' ? t('home.deleteExited') : t('home.exitBeforeDelete')}><Trash2 size={14} />{closingTerminal === terminal.id ? t('agents.deleting') : t('common.delete')}</button>
        </div>
      </div>
      <ProviderOutcomeNotice code={terminal.provider_outcome_code} />
      {terminal.launch_worktree && <div className="flex items-center gap-1.5" title={terminal.launch_worktree}><FolderOpen size={12} className="text-gray-600 shrink-0" /><span className="text-xs text-gray-500">{t(terminal.workspace_classification === 'managed_isolated' ? 'agents.isolatedWorkspace' : 'agents.legacyWorkspace')} ·</span><span className="text-xs font-mono text-gray-500 truncate max-w-[400px]">{terminal.launch_worktree}</span></div>}
      {terminal.writable_work_context_id && <div className="text-xs text-gray-600 font-mono truncate" title={`${terminal.writable_work_context_id}${terminal.writer_authority_generation ? ` · ${terminal.writer_authority_generation}` : ''}`}>{t('agents.writerAuthority')} · {terminal.writable_work_context_id.slice(0, 12)}{terminal.writer_authority_generation ? ` · ${terminal.writer_authority_generation.slice(0, 12)}` : ''}</div>}
      <button onClick={() => openTerminal(terminal.id, terminal.provider, terminal.agent_profile)} className="text-xs text-gray-500 hover:text-gray-300 transition-colors">{t('agents.openComposer')}</button>
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
          {t('agents.terminalsIn', { name: sessionDisplayName(session.name) })}
        </h3>
        <button
          onClick={showAddAgent ? () => setShowAddAgent(false) : openAddAgent}
          disabled={session.status !== 'active'}
          className="min-h-11 self-start sm:self-auto flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-gray-400 hover:text-emerald-400 bg-gray-900/50 hover:bg-gray-900 border border-gray-700/50 hover:border-emerald-700/50 rounded-lg transition-colors disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:text-gray-400"
          title={session.status === 'active' ? t('agents.addAgentTitle') : t('agents.addAgentUnavailable')}
        >
          <Plus size={14} />
          {t('agents.addAgent')}
        </button>
      </div>

      {/* Add Agent Inline Form */}
      {showAddAgent && session.status === 'active' && (
        <div className="mb-4 p-4 bg-gray-900/70 border border-gray-700/50 rounded-lg space-y-3">
          <p className="text-xs text-gray-500">
            {t('agents.addAgentHelp')}
          </p>
          <div className="flex gap-3 items-end flex-wrap">
            <div className="min-w-[160px]">
              <label className="block text-xs text-gray-500 mb-1">{t('common.provider')}</label>
              <CustomSelect
                value={addProvider}
                onChange={setAddProvider}
                placeholder={t('common.selectProvider')}
                options={(providers.length > 0 ? providers : UNAVAILABLE_PROVIDER_FALLBACK).map(item => providerSelectOption(item, t))}
              />
            </div>
            <div className="flex-1 min-w-[180px]">
              <label className="block text-xs text-gray-500 mb-1">{t('agents.agentProfile')}</label>
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
                  placeholder={t('agents.profileExample')}
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
              {addingAgent ? t('agents.adding') : t('common.add')}
            </button>
          </div>
          {privilegedAdd && (
            <XHighAuthorizationBlock confirmed={addOwnerConfirmed} operatorSecret={addOperatorSecret} onConfirmedChange={setAddOwnerConfirmed} onOperatorSecretChange={setAddOperatorSecret}/>
          )}
          <div>
            <label className="block text-xs text-gray-500 mb-1">{t('common.project')} <span className="text-gray-600">({t('common.optional')})</span></label>
            <ProjectPicker projects={projects} value={addProjectId} onChange={setAddProjectId} />
            {projects.length === 0 && <p aria-live="polite" className="mt-1 text-xs text-amber-400">{t('agents.noProjectsLegacy')}</p>}
            <p data-testid="add-agent-resolved-working-directory" className="mt-1 min-w-0 break-all font-mono text-xs text-gray-500">{resolvedAddWorkingDirectory || t('agents.serverDefault')}</p>
          </div>
          {addError && <div role="alert" className="whitespace-pre-line rounded-lg border border-red-700/50 bg-red-950/40 px-3 py-2 text-sm text-red-300">{addError}</div>}
        </div>
      )}

      {activeSessionFeed.error && <p role="alert" className="text-xs text-red-300">{t('agents.refreshSessionFailed')}</p>}
      {activeSessionFeed.loading && activeSessionFeed.items.length === 0 ? <p className="text-sm text-gray-500">{t('agents.loading')}</p> : <div data-testid={`agent-session-agent-container-${session.id}`} className={sessionAgentViews[session.id] === 'grid' ? 'space-y-2 md:grid md:grid-cols-2 md:gap-2 md:space-y-0' : 'space-y-2'}>{activeSessionFeed.items.map(terminal => renderAgentCard(terminal, undefined, sessionAgentViews[session.id] === 'grid'))}</div>}
      {activeSessionFeed.nextOffset !== null && <div ref={activeAgentSentinelRef} className="flex justify-center pt-3"><button type="button" onClick={activeSessionFeed.loadMore} disabled={activeSessionFeed.loading} className="min-h-10 rounded-lg border border-gray-700 px-4 text-xs text-gray-300 hover:border-emerald-700 disabled:opacity-40">{activeSessionFeed.loading ? t('common.loading') : t('agents.loadMore', { loaded: activeSessionFeed.items.length, total: activeSessionFeed.total })}</button></div>}
      {activeSessionFeed.limitReached && <p className="pt-3 text-center text-xs text-gray-500">{t('agents.sessionLimit')}</p>}
    </div>
  )

  return (
    <div className="space-y-6">
      <div className="bg-gray-800/60 border border-gray-700/50 rounded-xl p-2" role="tablist" aria-label={t('agents.views')}>
        <div className="grid grid-cols-3 gap-1">
          {(['sessions', 'statuses', 'profiles'] as AgentViewMode[]).map(mode => (
            <button
              key={mode}
              role="tab"
              aria-selected={agentViewMode === mode}
              onClick={() => setView(mode)}
              className={`min-h-11 rounded-lg px-3 text-sm font-medium transition-colors ${agentViewMode === mode ? 'bg-emerald-600 text-white' : 'text-gray-400 hover:bg-gray-700/60 hover:text-gray-200'}`}
            >
              {t(`agents.view.${mode}` as TranslationKey)}
            </button>
          ))}
        </div>
      </div>

      {/* Sessions List */}
      {agentViewMode === 'sessions' && <div className="bg-gray-800/60 border border-gray-700/50 rounded-xl p-3 sm:p-5">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-1">
          <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">
            {t('agents.sessionsTitle', { count: sessionFeed.total })}
          </h3>
          <div className="flex flex-col sm:flex-row sm:items-center gap-2 w-full sm:w-auto">
            {(sessionFeed.total > 3 || sessionSearch) && (
              <div className="relative w-full sm:w-auto">
                <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-500" />
                <input
                  type="text"
                  value={sessionSearch}
                  onChange={e => setSessionSearch(e.target.value)}
                  placeholder={t('agents.filterSessions')}
                  className="bg-gray-900 border border-gray-700 text-gray-200 text-xs rounded-lg pl-8 pr-3 py-2.5 w-full sm:w-48 focus:border-emerald-500 focus:outline-none"
                />
              </div>
            )}
            <button
              onClick={openSpawnModal}
              className="min-h-11 justify-center flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
            >
              <Plus size={14} />
              {t('agents.createSession')}
            </button>
          </div>
        </div>
        <p className="text-xs text-gray-500 mb-4">
          {t('agents.sessionsHelp')}
        </p>
        {sessionFeed.error && <p role="alert" className="mb-3 text-xs text-red-300">{t('agents.refreshSessionsFailed')}</p>}
        {sessionFeed.loading && sessionFeed.items.length === 0 ? (
          <p className="text-gray-500 text-sm">{t('agents.loadingSessions')}</p>
        ) : sessionFeed.items.length === 0 ? (
          <p className="text-gray-500 text-sm">{t('agents.noSessions')}</p>
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
                      aria-label={t(expanded ? 'agents.collapse' : 'agents.expand', { name: displayName })}
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
                        {t('agents.count', { count: s.agent_count })}
                      </span>
                      <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs ${s.status === 'active' ? 'bg-emerald-900/50 text-emerald-400' : 'bg-gray-700 text-gray-400'}`}>
                        {t(sessionStatusTranslationKey(s.status))}
                      </span>
                    </div>
                    <div data-testid={`agent-session-actions-${s.id}`} className="flex items-center gap-2 self-end sm:self-auto shrink-0" onClick={event => event.stopPropagation()}>
                      <AgentViewControls value={sessionAgentViews[s.id] || 'list'} onChange={value => setSessionAgentViews(current => ({ ...current, [s.id]: value }))} />
                      <button
                        onClick={event => { event.stopPropagation(); setPendingDeleteSession(s) }}
                        className="min-w-11 min-h-11 inline-flex items-center justify-center text-gray-500 hover:text-red-400 transition-colors rounded-lg hover:bg-gray-800"
                        title={t('agents.deleteSession')}
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
            {sessionFeed.nextOffset !== null && <div ref={sessionSentinelRef} className="flex justify-center pt-3"><button type="button" onClick={sessionFeed.loadMore} disabled={sessionFeed.loading} className="min-h-11 rounded-lg border border-gray-700 px-5 text-xs text-gray-300 hover:border-emerald-700 disabled:opacity-40">{sessionFeed.loading ? t('common.loading') : t('agents.loadMoreSessions', { loaded: sessionFeed.items.length, total: sessionFeed.total })}</button></div>}
            {sessionFeed.limitReached && <p className="pt-3 text-center text-xs text-gray-500">{t('agents.matchedSessionLimit')}</p>}
          </div>
        )}
      </div>}

      {agentViewMode !== 'sessions' && (
        <div className="space-y-4">
          <div className="bg-gray-800/60 border border-gray-700/50 rounded-xl p-3 sm:p-5 space-y-4">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">{t(agentViewMode === 'statuses' ? 'agents.statusFilters' : 'agents.profileFilters')}</h3>
                <p className="text-xs text-gray-500 mt-1">{t(agentViewMode === 'statuses' ? 'agents.statusFiltersHelp' : 'agents.profileFiltersHelp')}</p>
              </div>
              {hasActiveFilters && <button onClick={clearCurrentFilters} className="min-h-11 px-3 py-2 text-xs font-medium text-gray-300 border border-gray-700 rounded-lg hover:border-emerald-700 hover:text-emerald-400">{t('agents.clearFilters')}</button>}
            </div>
            {agentViewMode === 'statuses' ? (
              <div className="grid gap-4 md:grid-cols-3">
                <fieldset className="min-w-0">
                  <legend className="text-xs font-medium text-gray-400 mb-2">{t('agents.homeShortcut')}</legend>
                  <div className="flex flex-wrap gap-2" aria-label={t('agents.homeFilters')}>
                    {(Object.keys(HOME_FILTER_KEYS) as HomeAgentFilter[]).map(value => <button key={value} aria-pressed={homeFilter === value} onClick={() => { const next = homeFilter === value ? null : value; setHomeFilter(next); updateUrlFilters({ homeFilter: next }) }} className={`min-h-9 rounded-lg border px-3 text-xs ${homeFilter === value ? 'border-emerald-600 bg-emerald-900/30 text-emerald-300' : 'border-gray-700 bg-gray-900/50 text-gray-400 hover:text-gray-200'}`}>{t(HOME_FILTER_KEYS[value])}</button>)}
                  </div>
                </fieldset>
                <fieldset className="min-w-0">
                  <legend className="text-xs font-medium text-gray-400 mb-2">{t('agents.controlActivity')}</legend>
                  <div className="flex flex-wrap gap-2" aria-label={t('agents.activityFilters')}>
                    {providerStatusChoices.map(value => <button key={value} aria-pressed={providerStatusFilters.includes(value)} onClick={() => { const next = toggleFilter(providerStatusFilters, value); setProviderStatusFilters(next); updateUrlFilters({ providerStatuses: next }) }} className={`min-h-9 rounded-lg border px-3 text-xs ${providerStatusFilters.includes(value) ? 'border-emerald-600 bg-emerald-900/30 text-emerald-300' : 'border-gray-700 bg-gray-900/50 text-gray-400 hover:text-gray-200'}`}>{t(statusTranslationKey(value))}</button>)}
                    {!filteredAgentFeed.loading && providerStatusChoices.length === 0 && <span className="text-xs text-gray-500">{t('agents.noActivity')}</span>}
                  </div>
                </fieldset>
                <fieldset className="min-w-0">
                  <legend className="text-xs font-medium text-gray-400 mb-2">{t('agents.workflowState')}</legend>
                  <div className="flex flex-wrap gap-2" aria-label={t('agents.workflowFilters')}>
                    {workflowStatusChoices.map(value => <button key={value} aria-pressed={workflowStatusFilters.includes(value)} onClick={() => { const next = toggleFilter(workflowStatusFilters, value); setWorkflowStatusFilters(next); updateUrlFilters({ workflowStates: next }) }} className={`min-h-9 rounded-lg border px-3 text-xs ${workflowStatusFilters.includes(value) ? 'border-emerald-600 bg-emerald-900/30 text-emerald-300' : 'border-gray-700 bg-gray-900/50 text-gray-400 hover:text-gray-200'}`}>{t(statusTranslationKey(`WORKFLOW_${value}`))}</button>)}
                    {!filteredAgentFeed.loading && workflowStatusChoices.length === 0 && <span className="text-xs text-gray-500">{t('agents.noWorkflowStates')}</span>}
                  </div>
                </fieldset>
              </div>
            ) : (
              <fieldset>
                <legend className="text-xs font-medium text-gray-400 mb-2">{t('agents.profiles')}</legend>
                <div className="flex flex-wrap gap-2" aria-label={t('agents.profileFilterLabel')}>
                  {canonicalProfileNames.map(name => <button key={name} aria-pressed={profileFilters.includes(name)} onClick={() => { const next = toggleFilter(profileFilters, name); setProfileFilters(next); updateUrlFilters({ profiles: next }) }} className={`min-h-9 rounded-lg border px-3 text-xs ${profileFilters.includes(name) ? 'border-emerald-600 bg-emerald-900/30 text-emerald-300' : 'border-gray-700 bg-gray-900/50 text-gray-400 hover:text-gray-200'}`}>{name}</button>)}
                  {!loadingProfiles && canonicalProfileNames.length === 0 && <span className="text-xs text-gray-500">{t('agents.noProfiles')}</span>}
                </div>
              </fieldset>
            )}
          </div>

          <div className="bg-gray-800/60 border border-gray-700/50 rounded-xl p-3 sm:p-5">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 mb-4">
              <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">{t('agents.matching', { loaded: matchingAgents.length, total: filteredAgentFeed.total })}</h3>
              <p className="text-xs text-gray-500">{t('agents.loaded', { agents: matchingAgents.length, sessions: matchingSessionIds.length })}</p>
            </div>
            {filteredAgentFeed.error && <p role="alert" className="mb-3 text-xs text-red-300">{t('agents.refreshFailed')}</p>}
            {filteredAgentFeed.loading && matchingAgents.length === 0 ? <p className="text-sm text-gray-500">{t('agents.loading')}</p> : matchingAgents.length === 0 ? (
              <div className="py-6 text-center"><p className="text-sm text-gray-400">{t(hasActiveFilters ? 'agents.noFilterMatches' : 'agents.noneAvailable')}</p>{hasActiveFilters && <button onClick={clearCurrentFilters} className="mt-3 min-h-11 px-3 text-xs font-medium text-emerald-400 hover:text-emerald-300">{t('agents.clearFilters')}</button>}</div>
            ) : (
              <div className="space-y-4">
                {matchingSessionIds.map(sessionId => {
                  const sessionName = matchingAgents.find(item => item.session_id === sessionId)?.session_name || sessionId
                  return (
                  <section key={sessionId} className="space-y-2" aria-label={t('agents.matchingIn', { name: sessionDisplayName(sessionName) })}>
                    <h4 className="text-xs font-mono text-gray-500 truncate" title={sessionDisplayName(sessionName)}>{sessionDisplayName(sessionName)}</h4>
                    {matchingAgents.filter(item => item.session_id === sessionId).map(item => renderAgentCard(item, sessionName))}
                  </section>
                  )
                })}
                {filteredAgentFeed.nextOffset !== null && <div ref={filteredAgentSentinelRef} className="flex justify-center pt-3"><button type="button" onClick={filteredAgentFeed.loadMore} disabled={filteredAgentFeed.loading} className="min-h-11 rounded-lg border border-gray-700 px-5 text-xs text-gray-300 hover:border-emerald-700 disabled:opacity-40">{filteredAgentFeed.loading ? t('common.loading') : t('agents.loadMore', { loaded: matchingAgents.length, total: filteredAgentFeed.total })}</button></div>}
                {filteredAgentFeed.limitReached && <p className="pt-3 text-center text-xs text-gray-500">{t('agents.matchingLimit')}</p>}
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
        title={t('agents.deleteTerminalTitle')}
        message={t('agents.deleteTerminalMessage')}
        details={pendingClose ? [
          { label: t('agents.terminalId'), value: pendingClose.id },
          { label: t('common.provider'), value: pendingClose.provider },
          { label: t('common.profile'), value: pendingClose.agent_profile || t('common.none') },
          { label: t('statistics.session'), value: sessionDisplayName(pendingClose.tmux_session) },
        ] : []}
        confirmLabel={t('agents.deleteTerminal')}
        variant="danger"
        loading={!!closingTerminal}
        onConfirm={handleDeleteTerminal}
        onCancel={() => setPendingClose(null)}
      />

      <ConfirmModal
        open={!!pendingDeleteSession}
        title={t('agents.deleteSessionTitle')}
        message={t('agents.deleteSessionMessage')}
        details={pendingDeleteSession ? [
          { label: t('statistics.session'), value: sessionDisplayName(pendingDeleteSession.name) },
          { label: t('agents.status'), value: t(sessionStatusTranslationKey(pendingDeleteSession.status)) },
        ] : []}
        confirmLabel={t('agents.deleteSessionTitle')}
        variant="danger"
        loading={!!deletingSession}
        onConfirm={handleDeleteSession}
        onCancel={() => setPendingDeleteSession(null)}
      />

      {/* Graceful Exit Confirmation Modal */}
      <ConfirmModal
        open={!!pendingExit}
        title={t('agents.gracefulExit')}
        message={t('agents.gracefulExitMessage')}
        details={pendingExit ? [
          { label: t('agents.terminalId'), value: pendingExit.id },
          { label: t('common.provider'), value: pendingExit.provider },
          { label: t('common.profile'), value: pendingExit.agent_profile || t('common.none') },
          { label: t('statistics.session'), value: sessionDisplayName(pendingExit.tmux_session) },
        ] : []}
        confirmLabel={t('agents.sendExit')}
        variant="warning"
        loading={!!exitingTerminal}
        onConfirm={handleExitTerminal}
        onCancel={() => setPendingExit(null)}
      />

      {pendingRecovery && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-6">
          <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={() => !recoverySubmitting && setPendingRecovery(null)} />
          <div role="dialog" aria-modal="true" aria-labelledby="recovery-takeover-title" className="relative max-h-[94vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-indigo-700/60 bg-gray-900 shadow-2xl shadow-black/60">
            <div className="flex items-start justify-between gap-4 border-b border-gray-700/60 p-4 sm:p-5">
              <div className="min-w-0">
                <h3 id="recovery-takeover-title" className="text-base font-semibold text-gray-100">{t('agents.recoverTitle')}</h3>
                <p className="mt-1 text-xs text-gray-400">{t('agents.recoverHelp')}</p>
              </div>
              <button type="button" aria-label={t('common.close')} disabled={recoverySubmitting} onClick={() => setPendingRecovery(null)} className="min-h-11 min-w-11 rounded-lg text-gray-400 hover:bg-gray-800 hover:text-gray-100 disabled:opacity-40"><X className="mx-auto" size={18}/></button>
            </div>
            <div className="space-y-4 p-4 sm:p-5">
              <dl className="grid gap-3 rounded-xl border border-gray-700/50 bg-gray-950/50 p-3 text-xs sm:grid-cols-2">
                <div><dt className="text-gray-500">{t('agents.terminalId')}</dt><dd className="mt-1 break-all font-mono text-gray-200">{pendingRecovery.id}</dd></div>
                <div><dt className="text-gray-500">{t('statistics.session')}</dt><dd className="mt-1 break-all text-gray-200">{sessionDisplayName(pendingRecovery.session_name)}</dd></div>
                <div><dt className="text-gray-500">{t('common.project')}</dt><dd className="mt-1 break-all text-gray-200">{pendingRecovery.project_name || pendingRecovery.projectId}</dd></div>
                <div><dt className="text-gray-500">{t('agents.status')}</dt><dd className="mt-1 text-gray-200">{t(statusTranslationKey(terminalBadgeStatus(pendingRecovery)))}</dd></div>
                <div className="sm:col-span-2"><dt className="text-gray-500">{t('agents.recoverWorktree')}</dt><dd className="mt-1 break-all font-mono text-gray-200">{pendingRecovery.launch_worktree}</dd></div>
              </dl>

              {!recoveryPreview && <div className="rounded-xl border border-amber-700/50 bg-amber-950/20 p-3">
                <label className="block text-xs text-amber-200/80">{t('agents.operatorSecret')}<input type="password" autoComplete="current-password" value={recoveryOperatorSecret} onChange={event => setRecoveryOperatorSecret(event.target.value)} className="mt-1 min-h-11 w-full rounded-lg border border-amber-700/60 bg-gray-950 px-3 text-sm text-gray-100 focus:border-amber-400 focus:outline-none" /></label>
                <button type="button" onClick={inspectRecoveryTakeover} disabled={!recoveryOperatorSecret || recoveryInspecting} className="mt-3 min-h-11 w-full rounded-lg bg-indigo-700 px-4 text-sm font-medium text-white hover:bg-indigo-600 disabled:opacity-40">{recoveryInspecting ? t('common.loading') : t('agents.recoverInspect')}</button>
              </div>}

              {recoveryPreview && <>
                <div role="status" className={`rounded-xl border p-3 text-sm ${recoveryPreview.eligible ? 'border-emerald-700/50 bg-emerald-950/20 text-emerald-200' : 'border-red-700/50 bg-red-950/30 text-red-200'}`}>
                  {recoveryPreview.eligible ? t('agents.recoverEligible') : t('agents.recoverBlocked', { reason: recoveryPreview.reason_code || t('status.unknown') })}
                </div>
                <div className="rounded-xl border border-gray-700/50 bg-gray-950/50 p-3 text-sm">
                  <p className="text-gray-400">{t('agents.recoverWorktree')}</p>
                  <p className="mt-1 text-gray-200">{recoveryPreview.worktree?.state === 'dirty' ? t('agents.recoverDirty') : recoveryPreview.worktree?.state === 'clean' ? t('agents.recoverClean') : t('agents.recoverUnknown')}</p>
                </div>
                {recoveryPreview.eligible && <>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div><label className="mb-1 block text-xs text-gray-500">{t('common.provider')}</label><CustomSelect value={recoveryProvider} onChange={setRecoveryProvider} placeholder={t('common.selectProvider')} options={(providers.length > 0 ? providers : UNAVAILABLE_PROVIDER_FALLBACK).map(item => providerSelectOption(item, t))}/></div>
                    <div><label className="mb-1 block text-xs text-gray-500">{t('agents.agentProfile')}</label><ProfilePicker value={recoveryProfile} onChange={setRecoveryProfile} profiles={profiles.filter(item => item.owner_authorization_required)}/></div>
                  </div>
                  <div className="rounded-xl border border-red-700/50 bg-red-950/20 p-3 text-sm text-red-100">
                    <p>{t('agents.recoverConsequence')}</p>
                    <label className="mt-3 flex min-h-11 items-center gap-2 text-xs"><input aria-label={t('agents.recoverConfirmAria')} type="checkbox" checked={recoveryConfirmed} onChange={event => setRecoveryConfirmed(event.target.checked)}/>{t('agents.recoverConfirm')}</label>
                  </div>
                  <button type="button" onClick={submitRecoveryTakeover} disabled={!recoveryConfirmed || !recoveryProfile || !recoveryProvider || recoverySubmitting} className="min-h-11 w-full rounded-lg bg-red-700 px-4 text-sm font-semibold text-white hover:bg-red-600 disabled:opacity-40">{recoverySubmitting ? t('agents.recovering') : t('agents.recoverSubmit')}</button>
                </>}
              </>}
              {recoveryError && <div role="alert" className="whitespace-pre-line rounded-lg border border-red-700/50 bg-red-950/40 px-3 py-2 text-sm text-red-300">{recoveryError}</div>}
            </div>
          </div>
        </div>
      )}

      {/* Create Session Modal */}
      {showSpawnModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setShowSpawnModal(false)} />
          <div className="relative bg-gray-800 border border-gray-700 rounded-2xl shadow-2xl shadow-black/50 w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto">
            {/* Modal header */}
            <div className="flex items-center justify-between p-5 border-b border-gray-700/50">
              <div>
                <h3 className="text-base font-semibold text-gray-200">{t('agents.createSession')}</h3>
                <p className="text-xs text-gray-500 mt-1">
                  {t('agents.createDescription')}
                </p>
              </div>
              <button
                onClick={() => setShowSpawnModal(false)}
                aria-label={t('agents.closeCreate')}
                className="p-1.5 text-gray-500 hover:text-gray-300 transition-colors rounded-lg hover:bg-gray-700/50"
              >
                <X size={18} />
              </button>
            </div>

            {/* Modal body */}
            <div className="p-5 space-y-4">
              <div>
                <label className="block text-xs text-gray-500 mb-1">{t('common.provider')}</label>
                <CustomSelect
                  value={provider}
                  onChange={setProvider}
                  placeholder={t('common.selectProvider')}
                  options={(providers.length > 0 ? providers : UNAVAILABLE_PROVIDER_FALLBACK).map(item => providerSelectOption(item, t))}
                />
              </div>

              <div>
                <label className="block text-xs text-gray-500 mb-1">{t('agents.agentProfile')}</label>
                {loadingProfiles ? (
                  <div className="bg-gray-900 border border-gray-700 text-gray-500 text-sm rounded-lg px-3 py-2.5">{t('agents.loadingProfiles')}</div>
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
                    placeholder={t('agents.profileExample')}
                    className="w-full bg-gray-900 border border-gray-700 text-gray-200 text-sm rounded-lg px-3 py-2.5 focus:border-emerald-500 focus:outline-none"
                  />
                )}
              </div>

              {privilegedSpawn && (
                <div role="alert" className="rounded-lg border border-amber-500/50 bg-amber-950/30 p-3 text-sm text-amber-100">
                  <div className="flex items-start gap-2">
                    <ShieldAlert size={18} className="mt-0.5 shrink-0 text-amber-300" aria-hidden="true" />
                    <div>
                      <p className="font-semibold">{t('agents.xhighTitle')}</p>
                      <p className="mt-1 text-xs text-amber-200/80">{t('agents.xhighHelp')}</p>
                    </div>
                  </div>
                  <label className="mt-3 flex min-h-11 items-center gap-2 text-xs">
                    <input aria-label={t('agents.xhighConfirmAria')} type="checkbox" checked={ownerConfirmed} onChange={event => setOwnerConfirmed(event.target.checked)} />
                    {t('agents.xhighConfirm')}
                  </label>
                  <label className="mt-2 block text-xs text-amber-200/80">{t('agents.operatorSecret')}
                    <input aria-label={t('agents.operatorSecret')} type="password" autoComplete="current-password" value={operatorSecret} onChange={event => setOperatorSecret(event.target.value)} className="mt-1 min-h-11 w-full rounded-lg border border-amber-700/60 bg-gray-950 px-3 text-sm text-gray-100 focus:border-amber-400 focus:outline-none" />
                  </label>
                </div>
              )}

              <div>
                <label className="block text-xs text-gray-500 mb-1">{t('agents.sessionName')} <span className="text-gray-600">({t('common.optional')})</span></label>
                <input
                  type="text"
                  value={sessionName}
                  onChange={e => setSessionName(e.target.value.replace(/\./g, '_'))}
                  onKeyDown={e => e.key === 'Enter' && handleCreate()}
                  placeholder={t('agents.sessionNameExample')}
                  className="w-full bg-gray-900 border border-gray-700 text-gray-200 text-sm font-mono rounded-lg px-3 py-2.5 focus:border-emerald-500 focus:outline-none"
                />
              </div>

              <div>
                <label className="block text-xs text-gray-500 mb-1">{t('common.project')} <span className="text-gray-600">({t('common.optional')})</span></label>
                <ProjectPicker projects={projects} value={projectId} onChange={setProjectId} />
                {selectedSpawnProject ? (
                  <div aria-live="polite" className="mt-1 space-y-1 text-xs text-gray-500"><p className="min-w-0 break-all"><span>{t('agents.projectSourceAuthority')}: </span><span className="font-mono" title={selectedSpawnProject.path}>{selectedSpawnProject.path}</span></p><p className="text-emerald-400/80">{t('agents.managedWorkspaceHelp')}</p></div>
                ) : projects.length === 0 ? (
                  <p aria-live="polite" className="mt-1 text-xs text-amber-400">{t('agents.noProjectsDefault')}</p>
                ) : null}
              </div>

              {/* Quick-pick profiles */}
              {profiles.length > 0 && (
                <div>
                  <label className="block text-xs text-gray-500 mb-2">{t('agents.quickPick')}</label>
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
                        <span className="block text-[10px] text-gray-500">{p.description || t('common.noDescription')}</span>
                        <span className="text-[10px] text-gray-600">{SOURCE_LABELS[p.source] ? t(SOURCE_LABELS[p.source]) : p.source}</span>
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
                {t('common.cancel')}
              </button>
              <button
                onClick={handleCreate}
                disabled={!profile.trim() || creating || (privilegedSpawn && (!ownerConfirmed || !operatorSecret))}
                aria-busy={creating}
                className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white text-sm font-medium px-5 py-2.5 rounded-lg transition-colors"
              >
                {creating ? <LoaderCircle size={14} className="animate-spin" /> : <Play size={14} />}
                {creating ? t('agents.creating') : t('agents.create')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
