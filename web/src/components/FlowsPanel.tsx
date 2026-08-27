import { useCallback, useState, useEffect, useRef } from 'react'
import { api, Flow, AgentProfileInfo, Project, ProviderInfo } from '../api'
import { useStore } from '../store'
import { ConfirmModal } from './ConfirmModal'
import { Clock, Play, Trash2, Plus, ChevronDown, ChevronRight, Loader2, X } from 'lucide-react'
import { CustomSelect } from './CustomSelect'
import { ProfilePicker } from './ProfilePicker'
import { ProjectPicker } from './ProjectPicker'
import { providerSelectOption } from '../providerAvailability'
import { useI18n, type TranslationKey } from '../i18n'

const SCHEDULE_PRESETS = [
  { labelKey: 'flows.every5' as TranslationKey, cron: '*/5 * * * *' },
  { labelKey: 'flows.every15' as TranslationKey, cron: '*/15 * * * *' },
  { labelKey: 'flows.hourly' as TranslationKey, cron: '0 * * * *' },
  { labelKey: 'flows.every6Hours' as TranslationKey, cron: '0 */6 * * *' },
  { labelKey: 'flows.daily9' as TranslationKey, cron: '0 9 * * *' },
  { labelKey: 'flows.weekdays9' as TranslationKey, cron: '0 9 * * 1-5' },
  { labelKey: 'flows.weekly' as TranslationKey, cron: '0 9 * * 1' },
  { labelKey: 'flows.monthly' as TranslationKey, cron: '0 0 1 * *' },
]

const CUSTOM_CRON_VALUE = '__custom__'

function cronToLabel(cron: string, t: (key: TranslationKey) => string): string {
  const preset = SCHEDULE_PRESETS.find(p => p.cron === cron)
  return preset ? t(preset.labelKey) : cron
}

export function FlowsPanel() {
  const { locale, t } = useI18n()
  const { showSnackbar } = useStore()

  // Flow list state
  const [flows, setFlows] = useState<Flow[]>([])
  const [loading, setLoading] = useState(true)
  const [hasLoaded, setHasLoaded] = useState(false)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [togglingFlow, setTogglingFlow] = useState<string | null>(null)
  const [runningFlow, setRunningFlow] = useState<string | null>(null)
  const [loadError, setLoadError] = useState('')

  // Delete confirmation state
  const [pendingDelete, setPendingDelete] = useState<Flow | null>(null)
  const [deleting, setDeleting] = useState(false)

  // Create modal state
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [name, setName] = useState('')
  const [schedule, setSchedule] = useState('')
  const [scheduleMode, setScheduleMode] = useState<'preset' | 'custom'>('preset')
  const [agentProfile, setAgentProfile] = useState('')
  const [provider, setProvider] = useState('codex')
  const [promptTemplate, setPromptTemplate] = useState('')
  const [creating, setCreating] = useState(false)
  const [projectId, setProjectId] = useState('')

  // Profiles & providers for dropdowns
  const [profiles, setProfiles] = useState<AgentProfileInfo[]>([])
  const [providers, setProviders] = useState<ProviderInfo[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const flowGenerationRef = useRef(0)
  const flowInFlightRef = useRef<number | null>(null)
  const flowControllerRef = useRef<AbortController | null>(null)
  const createButtonRef = useRef<HTMLButtonElement>(null)
  const dialogRef = useRef<HTMLDivElement>(null)

  const fetchFlows = useCallback(async (supersede = false) => {
    if (flowInFlightRef.current !== null && !supersede) return
    if (supersede) flowControllerRef.current?.abort()
    const generation = ++flowGenerationRef.current
    const controller = new AbortController()
    flowControllerRef.current = controller
    flowInFlightRef.current = generation
    try {
      const data = await api.listFlows(controller.signal)
      if (generation !== flowGenerationRef.current) return
      setFlows(data)
      setLoadError('')
      setHasLoaded(true)
    } catch (reason) {
      if (generation === flowGenerationRef.current && (reason as { name?: string })?.name !== 'AbortError') {
        setLoadError(reason instanceof Error ? reason.message : 'FLOW_REFRESH_FAILED')
      }
    } finally {
      if (generation === flowGenerationRef.current) {
        flowInFlightRef.current = null
        flowControllerRef.current = null
        setLoading(false)
      }
    }
  }, [])

  useEffect(() => {
    void fetchFlows(true)
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void fetchFlows()
    }, 5_000)
    api.listProfiles()
      .then(p => setProfiles(p))
      .catch(() => {})
    api.listProviders()
      .then(p => setProviders(p))
      .catch(() => {})
    api.listProjects()
      .then(p => setProjects(p))
      .catch(() => setProjects([]))
    return () => {
      flowGenerationRef.current += 1
      flowControllerRef.current?.abort()
      flowControllerRef.current = null
      flowInFlightRef.current = null
      window.clearInterval(timer)
    }
  }, [fetchFlows])

  useEffect(() => {
    if (!showCreateModal) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        setShowCreateModal(false)
        return
      }
      if (event.key !== 'Tab' || !dialogRef.current) return
      const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
      ))
      if (!focusable.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      createButtonRef.current?.focus()
    }
  }, [showCreateModal])

  const resetForm = () => {
    setName('')
    setSchedule('')
    setScheduleMode('preset')
    setAgentProfile('')
    setProvider('codex')
    setPromptTemplate('')
    setProjectId(projects.find(project => project.isDefault)?.projectId || '')
  }

  const handleCreate = async () => {
    if (!name.trim() || !schedule.trim() || !agentProfile.trim() || !promptTemplate.trim()) return
    setCreating(true)
    try {
      await api.createFlow({
        name: name.trim(),
        schedule: schedule.trim(),
        agent_profile: agentProfile.trim(),
        provider: provider || undefined,
        prompt_template: promptTemplate,
        ...(projectId ? { projectId } : {}),
      })
      showSnackbar({ type: 'success', message: t('flows.created', { name: name.trim() }) })
      resetForm()
      setShowCreateModal(false)
      await fetchFlows(true)
    } catch (e: any) {
      showSnackbar({ type: 'error', message: e.message || t('flows.createFailed') })
    } finally {
      setCreating(false)
    }
  }

  const handleToggle = async (flow: Flow) => {
    setTogglingFlow(flow.name)
    try {
      if (flow.enabled) {
        await api.disableFlow(flow.name)
        showSnackbar({ type: 'success', message: t('flows.disabledNotice', { name: flow.name }) })
      } else {
        await api.enableFlow(flow.name)
        showSnackbar({ type: 'success', message: t('flows.enabledNotice', { name: flow.name }) })
      }
      await fetchFlows(true)
    } catch (e: any) {
      showSnackbar({ type: 'error', message: e.message || t('flows.toggleFailed') })
    } finally {
      setTogglingFlow(null)
    }
  }

  const handleRun = async (flow: Flow) => {
    setRunningFlow(flow.name)
    try {
      const result = await api.runFlow(flow.name)
      showSnackbar({ type: 'success', message: result.executed ? t('flows.launched', { name: flow.name }) : t('flows.completedNoLaunch', { name: flow.name }) })
      await fetchFlows(true)
    } catch (e: any) {
      showSnackbar({ type: 'error', message: e.message || t('flows.runFailed') })
    } finally {
      setRunningFlow(null)
    }
  }

  const handleDelete = async () => {
    if (!pendingDelete) return
    setDeleting(true)
    try {
      await api.deleteFlow(pendingDelete.name)
      showSnackbar({ type: 'success', message: t('flows.removed', { name: pendingDelete.name }) })
      await fetchFlows(true)
    } catch (e: any) {
      showSnackbar({ type: 'error', message: e.message || t('flows.deleteFailed') })
    } finally {
      setDeleting(false)
      setPendingDelete(null)
    }
  }

  if (loading) {
    return <div className="text-gray-400 text-sm py-8 text-center">{t('flows.loading')}</div>
  }

  if (!hasLoaded && loadError) {
    return <div role="alert" className="rounded-xl border border-amber-700/50 bg-amber-950/20 p-6 text-center text-sm text-amber-100"><p>{t('flows.unavailable')}</p><button type="button" onClick={() => { setLoading(true); void fetchFlows(true) }} className="mt-4 min-h-11 rounded-lg border border-amber-600 px-4 font-medium hover:bg-amber-900/30">{t('common.retry')}</button></div>
  }

  const scheduleSelectOptions = [
    ...SCHEDULE_PRESETS.map(p => ({
      value: p.cron,
      label: t(p.labelKey),
      sublabel: p.cron,
    })),
    { value: CUSTOM_CRON_VALUE, label: t('flows.customCron'), sublabel: t('flows.customCronHelp') },
  ]

  return (
    <div className="space-y-6">
      {loadError && <p role="alert" className="rounded-lg border border-amber-700/50 bg-amber-950/20 p-3 text-sm text-amber-200">{t('flows.refreshWarning')}</p>}
      {/* Flow List */}
      <div className="bg-gray-800/60 border border-gray-700/50 rounded-xl p-3 sm:p-5">
        <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">
            {t('flows.automated', { count: flows.length })}
          </h3>
          <button
            ref={createButtonRef}
            onClick={() => { resetForm(); setShowCreateModal(true) }}
            className="flex min-h-11 items-center justify-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500"
          >
            <Plus size={14} />
            {t('flows.create')}
          </button>
        </div>

        {flows.length === 0 ? (
          <div className="text-center py-8">
            <Clock size={32} className="mx-auto text-gray-400 mb-3" />
            <p className="text-gray-400 text-sm">{t('flows.empty')}</p>
            <p className="text-gray-400 text-xs mt-1">
              {t('flows.emptyHelp')} <code className="text-emerald-400">cao flow add &lt;file.md&gt;</code>
            </p>
          </div>
        ) : (
          <div className="space-y-2">
            {flows.map(f => (
              <div key={f.name} className="bg-gray-900/50 border border-gray-700/30 rounded-lg">
                {/* Row header */}
                <div className="flex flex-col gap-3 p-3 transition-colors hover:bg-gray-800/50 sm:flex-row sm:items-center sm:justify-between">
                  <button
                    type="button"
                    className="flex min-w-0 flex-1 flex-wrap items-center gap-3 rounded-lg text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
                    onClick={() => setExpanded(expanded === f.name ? null : f.name)}
                    aria-expanded={expanded === f.name}
                    aria-controls={`flow-details-${encodeURIComponent(f.name)}`}
                  >
                    <Clock size={14} className="text-gray-400 shrink-0" />
                    <span className="text-sm text-gray-200 font-medium truncate">{f.name}</span>
                    <span className="text-xs text-gray-400 shrink-0" title={f.schedule}>
                      {cronToLabel(f.schedule, t)}
                    </span>
                    <span className="text-xs text-gray-400 shrink-0">{f.agent_profile}</span>
                    {f.provider && (
                      <span className="text-xs text-gray-400 shrink-0">{f.provider}</span>
                    )}
                    <span className={`text-xs px-2 py-0.5 rounded-full shrink-0 ${f.enabled ? 'bg-emerald-900/50 text-emerald-400' : 'bg-gray-700 text-gray-400'}`}>
                      {f.enabled ? t('flows.enabled') : t('flows.disabled')}
                    </span>
                  </button>

                  <div className="grid w-full shrink-0 grid-cols-[44px_1fr_44px_24px] items-center gap-2 sm:ml-3 sm:flex sm:w-auto">
                    {/* Toggle enable/disable */}
                    <button
                      onClick={e => { e.stopPropagation(); handleToggle(f) }}
                      disabled={togglingFlow === f.name}
                      className={`relative inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg transition-colors ${
                        f.enabled ? 'bg-emerald-600' : 'bg-gray-600'
                      } ${togglingFlow === f.name ? 'opacity-50' : ''}`}
                      title={f.enabled ? t('flows.disable') : t('flows.enable')}
                    >
                      {togglingFlow === f.name ? (
                        <Loader2 size={12} className="absolute left-1/2 -translate-x-1/2 animate-spin text-white" />
                      ) : (
                        <span className={`inline-block h-4 w-4 rounded-full bg-white shadow ${f.enabled ? 'ring-2 ring-emerald-200' : ''}`} />
                      )}
                    </button>

                    {/* Run Now */}
                    <button
                      onClick={e => { e.stopPropagation(); handleRun(f) }}
                      disabled={runningFlow === f.name}
                      className="flex min-h-11 items-center justify-center gap-1.5 rounded-lg bg-emerald-600 px-2.5 py-1.5 text-xs font-medium text-white transition-colors hover:bg-emerald-500 disabled:opacity-40"
                      title={t('flows.runNow')}
                    >
                      {runningFlow === f.name ? (
                        <Loader2 size={12} className="animate-spin" />
                      ) : (
                        <Play size={12} />
                      )}
                      {runningFlow === f.name ? t('flows.running') : t('flows.run')}
                    </button>

                    {/* Delete */}
                    <button
                      onClick={e => { e.stopPropagation(); setPendingDelete(f) }}
                      className="inline-flex min-h-11 min-w-11 items-center justify-center rounded text-gray-400 transition-colors hover:text-red-400"
                      title={t('flows.delete')}
                    >
                      <Trash2 size={14} />
                    </button>

                    <button type="button" onClick={() => setExpanded(expanded === f.name ? null : f.name)} aria-expanded={expanded === f.name} aria-controls={`flow-details-${encodeURIComponent(f.name)}`} aria-label={t(expanded === f.name ? 'flows.collapse' : 'flows.expand', { name: f.name })} className="inline-flex min-h-11 min-w-6 items-center justify-center rounded text-gray-400 hover:text-gray-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400">{expanded === f.name ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</button>
                  </div>
                </div>

                {/* Expanded details */}
                {expanded === f.name && (
                  <div id={`flow-details-${encodeURIComponent(f.name)}`} className="px-3 pb-3 text-xs text-gray-300 space-y-3 border-t border-gray-700/30 pt-3">
                    <div className="grid grid-cols-1 gap-x-6 gap-y-1 sm:grid-cols-2">
                      <div>{t('flows.schedule')} <span className="text-gray-300 font-mono">{f.schedule}</span></div>
                      <div>{t('common.provider')}: <span className="text-gray-300">{f.provider || 'default'}</span></div>
                      <div>{t('common.profile')}: <span className="text-gray-300">{f.agent_profile}</span></div>
                      {f.project_name && <div>{t('common.project')}: <span className="text-gray-300">{f.project_name}</span></div>}
                      {f.project_path && <div className="break-all sm:col-span-2">{t('flows.projectPath')} <span className="text-gray-300 font-mono">{f.project_path}</span></div>}
                      <div>{t('flows.lastRun')} <span className="text-gray-300">{f.last_run ? new Date(f.last_run).toLocaleString(locale) : t('flows.never')}</span></div>
                      <div>{t('flows.nextRun')} <span className="text-gray-300">{f.next_run ? new Date(f.next_run).toLocaleString(locale) : t('flows.notApplicable')}</span></div>
                      {f.file_path && (
                        <div className="break-all sm:col-span-2">{t('flows.file')} <span className="text-gray-300 font-mono">{f.file_path}</span></div>
                      )}
                    </div>
                    {f.prompt_template && (
                      <div>
                        <div className="text-[11px] text-gray-400 uppercase tracking-wider mb-1.5">{t('flows.prompt')}</div>
                        <div className="bg-gray-950/60 border border-gray-700/30 rounded-lg p-3 text-sm text-gray-300 font-mono whitespace-pre-wrap leading-relaxed max-h-48 overflow-y-auto">
                          {f.prompt_template}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Create Flow Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setShowCreateModal(false)} />
          <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="create-flow-title" className="relative bg-gray-800 border border-gray-700 rounded-2xl shadow-2xl shadow-black/50 w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto">
            {/* Modal header */}
            <div className="flex items-center justify-between p-5 border-b border-gray-700/50">
              <div>
                <h3 id="create-flow-title" className="text-base font-semibold text-gray-200">{t('flows.create')}</h3>
                <p className="text-xs text-gray-400 mt-1">
                  {t('flows.createDescription')}
                </p>
              </div>
              <button
                onClick={() => setShowCreateModal(false)}
                aria-label={t('flows.closeCreate')}
                className="p-1.5 text-gray-400 hover:text-gray-300 transition-colors rounded-lg hover:bg-gray-700/50"
              >
                <X size={18} />
              </button>
            </div>

            {/* Modal body */}
            <div className="p-5 space-y-4">
              <div>
                <label className="block text-xs text-gray-400 mb-1">{t('common.name')}</label>
                <input
                  type="text"
                  value={name}
                  onChange={e => setName(e.target.value)}
                  placeholder={t('flows.namePlaceholder')}
                  className="w-full bg-gray-900 border border-gray-700 text-gray-200 text-sm rounded-lg px-3 py-2.5 focus:border-emerald-500 focus:outline-none"
                  autoFocus
                />
              </div>

              <div>
                <label className="block text-xs text-gray-400 mb-1">{t('flows.schedule').replace(/:$/, '')}</label>
                <CustomSelect
                  value={scheduleMode === 'custom' ? CUSTOM_CRON_VALUE : schedule}
                  onChange={val => {
                    if (val === CUSTOM_CRON_VALUE) {
                      setScheduleMode('custom')
                      setSchedule('')
                    } else {
                      setScheduleMode('preset')
                      setSchedule(val)
                    }
                  }}
                  placeholder={t('flows.pickSchedule')}
                  options={scheduleSelectOptions}
                />
                {scheduleMode === 'custom' && (
                  <input
                    type="text"
                    value={schedule}
                    onChange={e => setSchedule(e.target.value)}
                    placeholder="*/30 * * * *"
                    className="w-full mt-2 bg-gray-900 border border-gray-700 text-gray-200 text-sm rounded-lg px-3 py-2.5 font-mono focus:border-emerald-500 focus:outline-none"
                    autoFocus
                  />
                )}
                {schedule && (
                  <p className="text-[11px] text-emerald-500/70 mt-1.5">
                    {cronToLabel(schedule, t)}{scheduleMode === 'custom' && schedule ? ` — ${schedule}` : ''}
                  </p>
                )}
              </div>

              <div className="flex gap-3">
                <div className="flex-1">
                  <label className="block text-xs text-gray-400 mb-1">{t('flows.agentProfile')}</label>
                  <ProfilePicker profiles={profiles} value={agentProfile} onChange={setAgentProfile} disabled={profiles.length === 0} />
                </div>
                <div className="flex-1">
                  <label className="block text-xs text-gray-400 mb-1">{t('common.provider')}</label>
                  <CustomSelect
                    value={provider}
                    onChange={setProvider}
                    placeholder={t('common.default')}
                    options={providers.map(item => providerSelectOption(item, t))}
                  />
                </div>
              </div>

              <div>
                <label className="block text-xs text-gray-400 mb-1">{t('common.project')} <span className="text-gray-400">({t('common.optional')})</span></label>
                <ProjectPicker projects={projects} value={projectId} onChange={setProjectId} />
                {projects.length === 0 && <p aria-live="polite" className="mt-1 text-xs text-amber-400">{t('flows.noProjects')}</p>}
              </div>

              <div>
                <label className="block text-xs text-gray-400 mb-1">{t('flows.prompt')}</label>
                <textarea
                  value={promptTemplate}
                  onChange={e => setPromptTemplate(e.target.value)}
                  placeholder={t('flows.promptPlaceholder')}
                  rows={5}
                  className="w-full bg-gray-900 border border-gray-700 text-gray-200 text-sm rounded-lg px-3 py-2.5 font-mono focus:border-emerald-500 focus:outline-none resize-y"
                />
              </div>
            </div>

            {/* Modal footer */}
            <div className="flex items-center justify-end gap-3 p-5 border-t border-gray-700/50">
              <button
                onClick={() => setShowCreateModal(false)}
                className="px-4 py-2 text-sm text-gray-400 hover:text-gray-200 transition-colors"
              >
                {t('common.cancel')}
              </button>
              <button
                onClick={handleCreate}
                disabled={!name.trim() || !schedule.trim() || !agentProfile.trim() || !promptTemplate.trim() || creating}
                className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white text-sm font-medium px-5 py-2.5 rounded-lg transition-colors"
              >
                {creating ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
                {creating ? t('flows.creating') : t('flows.create')}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Confirmation Modal */}
      <ConfirmModal
        open={!!pendingDelete}
        title={t('flows.deleteTitle')}
        message={t('flows.deleteMessage')}
        details={pendingDelete ? [
          { label: t('common.name'), value: pendingDelete.name },
          { label: t('flows.schedule').replace(/:$/, ''), value: pendingDelete.schedule },
          { label: t('common.profile'), value: pendingDelete.agent_profile },
          { label: t('common.provider'), value: pendingDelete.provider || 'default' },
        ] : []}
        confirmLabel={t('flows.deleteTitle')}
        variant="danger"
        loading={deleting}
        onConfirm={handleDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  )
}
