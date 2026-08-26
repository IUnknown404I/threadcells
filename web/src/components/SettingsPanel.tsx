import { useState, useEffect } from 'react'
import { api, AgentDirsSettings, OrchestrationCapacity, AgentProfileInfo, Project, RuntimeBranding } from '../api'
import { useStore } from '../store'
import { Activity, FolderOpen, Save, Plus, X, RefreshCw, CheckCircle, Maximize2, Minimize2, Upload, RotateCcw, Pencil, Star, Trash2 } from 'lucide-react'
import { ConfirmModal } from './ConfirmModal'
import { OperatorAccessCard, useOperatorAccess } from './OperatorAccess'
import { useI18n, type TranslationKey } from '../i18n'
import { resourceStateTranslationKey } from './StatusBadge'

const CAPACITY_FIELD_KEYS: Record<string, TranslationKey> = {
  max_resident_supervisors: 'settings.capacity.maxResident',
  max_provider_executions: 'settings.capacity.maxProvider',
  max_work_contexts: 'settings.capacity.maxContexts',
  max_heavy_execution_slots: 'settings.capacity.maxHeavy',
}

export function SettingsPanel() {
  const { t } = useI18n()
  const operatorAccess = useOperatorAccess()
  const [settings, setSettings] = useState<AgentDirsSettings | null>(null)
  const [dirs, setDirs] = useState<string[]>([])
  const [newDir, setNewDir] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [profileCount, setProfileCount] = useState<number | null>(null)
  const [profiles, setProfiles] = useState<AgentProfileInfo[]>([])
  const [profilesOpen, setProfilesOpen] = useState(false)
  const [profilesFullscreen, setProfilesFullscreen] = useState(false)
  const [capacity, setCapacity] = useState<OrchestrationCapacity | null>(null)
  const [capacityOpen, setCapacityOpen] = useState(false)
  const [capacityDraft, setCapacityDraft] = useState({ max_resident_supervisors: 5, max_provider_executions: 3, max_work_contexts: 2, max_heavy_execution_slots: 1 })
  const [capacityBusy, setCapacityBusy] = useState(false)
  const [projects, setProjects] = useState<Project[]>([])
  const [createProjectOpen, setCreateProjectOpen] = useState(false)
  const [projectName, setProjectName] = useState('')
  const [projectPath, setProjectPath] = useState('')
  const [projectDescription, setProjectDescription] = useState('')
  const [createProjectDirectory, setCreateProjectDirectory] = useState(false)
  const [pendingProject, setPendingProject] = useState<{ name: string; path: string; description: string; createDirectory: boolean } | null>(null)
  const [pendingProjectDelete, setPendingProjectDelete] = useState<Project | null>(null)
  const [projectBusy, setProjectBusy] = useState(false)
  const [editingProject, setEditingProject] = useState<Project | null>(null)
  const [branding, setBranding] = useState<RuntimeBranding | null>(null)
  const [brandingTitle, setBrandingTitle] = useState('ThreadCells')
  const [brandingSubtitle, setBrandingSubtitle] = useState('Multi-agent control plane')
  const [brandingBusy, setBrandingBusy] = useState(false)
  const { showSnackbar } = useStore()

  const load = async () => {
    try {
      const s = await api.getAgentDirs()
      setSettings(s)
      setDirs(s.extra_dirs)
    } catch {
      showSnackbar({ type: 'error', message: t('settings.loadFailed') })
    }
  }

  const refreshProfiles = async () => {
    try {
      const profiles = await api.listProfiles()
      setProfileCount(profiles.length)
      setProfiles(profiles)
    } catch {}
  }

  const refreshCapacity = async () => {
    try {
      setCapacity(await api.getOrchestrationCapacity())
    } catch {
      setCapacity(null)
    }
  }

  const openCapacity = () => {
    if (capacity) setCapacityDraft({
      max_resident_supervisors: capacity.resident_supervisors.limit,
      max_provider_executions: capacity.provider_executions.limit,
      max_work_contexts: capacity.work_contexts.limit,
      max_heavy_execution_slots: capacity.heavy_executions.limit,
    })
    setCapacityOpen(true)
  }

  const saveCapacity = async () => {
    if (!operatorAccess.status?.authenticated) return
    setCapacityBusy(true)
    try {
      setCapacity(await api.updateOrchestrationCapacity(capacityDraft))
      setCapacityOpen(false)
      showSnackbar({ type: 'success', message: t('settings.capacity.updated') })
    } catch (error: any) { showSnackbar({ type: 'error', message: error.message || t('settings.capacity.updateFailed') }); await operatorAccess.refresh() }
    finally { setCapacityBusy(false) }
  }

  const refreshProjects = async () => {
    try { setProjects(await api.listProjects()) } catch { setProjects([]) }
  }
  const refreshBranding = async () => {
    try { const value = await api.getBranding(); setBranding(value); setBrandingTitle(value.title); setBrandingSubtitle(value.subtitle) } catch { setBranding(null) }
  }

  useEffect(() => {
    load()
    refreshProfiles()
    refreshCapacity()
    refreshProjects()
    refreshBranding()
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void refreshCapacity()
    }, 10000)
    return () => window.clearInterval(timer)
  }, [])

  const handleSave = async () => {
    setSaving(true)
    setSaved(false)
    try {
      const result = await api.setAgentDirs({ extra_dirs: dirs })
      setSettings(result)
      setDirs(result.extra_dirs)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
      showSnackbar({ type: 'success', message: t('settings.saved') })
      refreshProfiles()
    } catch (e: any) {
      showSnackbar({ type: 'error', message: e.message || t('settings.saveFailed') })
    } finally {
      setSaving(false)
    }
  }

  const addDir = () => {
    const trimmed = newDir.trim()
    if (trimmed && !dirs.includes(trimmed)) {
      setDirs([...dirs, trimmed])
      setNewDir('')
    }
  }

  const removeDir = (idx: number) => {
    setDirs(dirs.filter((_, i) => i !== idx))
  }

  const requestProjectCreate = () => {
    const name = projectName.trim(); const path = projectPath.trim()
    if (!name || !path) return
    setPendingProject({ name, path, description: projectDescription.trim(), createDirectory: createProjectDirectory })
    setCreateProjectOpen(false)
  }

  const confirmProjectCreate = async () => {
    if (!pendingProject) return
    setProjectBusy(true)
    try {
      await api.createProject({ ...pendingProject, isDefault: projects.length === 0 })
      setProjectName(''); setProjectPath(''); setProjectDescription(''); setCreateProjectDirectory(false); setPendingProject(null)
      await refreshProjects()
      showSnackbar({ type: 'success', message: t('settings.projects.registeredNotice') })
    } catch (error: any) { showSnackbar({ type: 'error', message: error.message || t('settings.projects.registerFailed') }) }
    finally { setProjectBusy(false) }
  }

  const makeDefault = async (project: Project) => {
    try { await api.setDefaultProject(project.projectId); await refreshProjects(); showSnackbar({ type: 'success', message: t('settings.projects.defaultNotice', { name: project.name }) }) }
    catch (error: any) { showSnackbar({ type: 'error', message: error.message || t('settings.projects.defaultFailed') }) }
  }

  const confirmProjectDelete = async () => {
    if (!pendingProjectDelete) return
    setProjectBusy(true)
    try { await api.deleteProject(pendingProjectDelete.projectId); await refreshProjects(); setPendingProjectDelete(null); showSnackbar({ type: 'success', message: t('settings.projects.removedNotice') }) }
    catch (error: any) { showSnackbar({ type: 'error', message: error.message || t('settings.projects.removeFailed') }) }
    finally { setProjectBusy(false) }
  }

  const saveProjectEdit = async () => {
    if (!editingProject) return
    setProjectBusy(true)
    try {
      await api.updateProject(editingProject.projectId, { name: editingProject.name, path: editingProject.path, description: editingProject.description || null })
      setEditingProject(null); await refreshProjects(); showSnackbar({ type: 'success', message: t('settings.projects.updated') })
    } catch (error: any) { showSnackbar({ type: 'error', message: error.message || t('settings.projects.updateFailed') }) }
    finally { setProjectBusy(false) }
  }

  const saveBranding = async () => {
    setBrandingBusy(true)
    try { setBranding(await api.updateBranding({ title: brandingTitle, subtitle: brandingSubtitle })); showSnackbar({ type: 'success', message: t('settings.branding.updated') }) }
    catch (error: any) { showSnackbar({ type: 'error', message: error.message || t('settings.branding.updateFailed') }) }
    finally { setBrandingBusy(false) }
  }
  const uploadLogo = async (file?: File) => {
    if (!file) return
    setBrandingBusy(true)
    try { setBranding(await api.uploadBrandingLogo(file)); showSnackbar({ type: 'success', message: t('settings.branding.logoUpdated') }) }
    catch (error: any) { showSnackbar({ type: 'error', message: error.message || t('settings.branding.uploadFailed') }) }
    finally { setBrandingBusy(false) }
  }
  const resetLogo = async () => {
    setBrandingBusy(true)
    try { setBranding(await api.resetBrandingLogo()); showSnackbar({ type: 'success', message: t('settings.branding.logoReset') }) }
    catch (error: any) { showSnackbar({ type: 'error', message: error.message || t('settings.branding.resetFailed') }) }
    finally { setBrandingBusy(false) }
  }

  if (!settings) {
    return <div className="text-gray-400 text-sm py-8 text-center">{t('settings.loading')}</div>
  }

  return (
    <div className="space-y-6">
      <OperatorAccessCard access={operatorAccess} />
      {/* Effective policy and live operational utilization are read-only. */}
      <div className="bg-gray-800/60 border border-gray-700/50 rounded-xl p-5">
        <div className="flex items-center justify-between gap-3 mb-4">
          <div className="flex items-center gap-2">
            <Activity size={16} className="text-emerald-400" />
            <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">
              {t('settings.capacity.title')}
            </h3>
          </div>
          <div className="flex items-center gap-2">{capacity && (
            <span
              aria-label={t('settings.capacity.health', { state: t(resourceStateTranslationKey(capacity.resource_state)) })}
              className={`text-xs font-semibold px-2.5 py-1 rounded-full ${
                capacity.resource_state === 'GREEN'
                  ? 'bg-emerald-500/15 text-emerald-300'
                  : capacity.resource_state === 'YELLOW'
                    ? 'bg-amber-500/15 text-amber-300'
                    : 'bg-red-500/15 text-red-300'
              }`}
            >
              {t(resourceStateTranslationKey(capacity.resource_state))}
            </span>
          )}<button type="button" onClick={openCapacity} className="min-h-11 rounded-lg border border-gray-700 px-3 text-xs text-gray-300 hover:border-emerald-600 hover:text-emerald-300">{t('settings.capacity.configure')}</button></div>
        </div>
        <p className="text-xs text-gray-400 mb-4">
          {t('settings.capacity.help')}
        </p>
        {capacity ? (
          <div className="space-y-3">
            {capacity.reasons.length > 0 && (
              <section
                aria-label={t('settings.capacity.reasonAria', { state: t(resourceStateTranslationKey(capacity.resource_state)) })}
                className={`rounded-lg border px-3 py-3 ${capacity.resource_state === 'RED' ? 'border-red-700/50 bg-red-950/20' : 'border-amber-700/50 bg-amber-950/20'}`}
              >
                <h4 className={`text-xs font-semibold ${capacity.resource_state === 'RED' ? 'text-red-200' : 'text-amber-200'}`}>{t('settings.capacity.drivers')}</h4>
                <ul className="mt-1 list-disc space-y-1 pl-5 text-xs text-gray-300">
                  {capacity.reasons.map(reason => <li key={reason} title={reason}>{capacityReasonLabel(reason, t)}</li>)}
                </ul>
              </section>
            )}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <dl className="contents" aria-label={t('settings.capacity.details')}>
                <CapacityItem label={t('settings.capacity.resident')} value={`${capacity.resident_supervisors.active} / ${capacity.resident_supervisors.limit}`} detail={capacity.resident_supervisors.certain ? t('settings.capacity.residentDetail', { count: capacity.resident_supervisors.available, draining: capacity.resident_supervisors.draining ? t('settings.capacity.draining') : '' }) : t('settings.capacity.inventoryUnavailable')} />
                <CapacityItem label={t('settings.capacity.providers')} value={`${capacity.provider_executions.active} / ${capacity.provider_executions.limit}`} detail={capacity.provider_executions.certain ? t('settings.capacity.providerDetail', { count: capacity.provider_executions.available, draining: capacity.provider_executions.draining ? t('settings.capacity.draining') : '' }) : t('settings.capacity.inventoryUnavailable')} />
                <CapacityItem label={t('settings.capacity.contexts')} value={`${capacity.work_contexts.active} / ${capacity.work_contexts.limit}`} detail={capacity.work_contexts.certain ? t('settings.capacity.contextDetail', { count: capacity.work_contexts.available, draining: capacity.work_contexts.draining ? t('settings.capacity.draining') : '' }) : t('settings.capacity.inventoryUnavailable')} />
                <CapacityItem label={t('settings.capacity.heavy')} value={`${capacity.heavy_executions.active} / ${capacity.heavy_executions.limit}`} detail={`${t('settings.capacity.available', { count: capacity.heavy_executions.available })}${capacity.heavy_executions.draining ? t('settings.capacity.draining') : ''}`} />
                <CapacityItem label={t('settings.capacity.memory')} value={`${capacity.memory.available_mib} MiB`} detail={t('settings.capacity.pressure', { value: capacity.memory_pressure.some_avg10 })} />
                <CapacityItem label={t('settings.capacity.disk')} value={t('settings.capacity.used', { percent: capacity.root_disk.used_percent })} detail={t('settings.capacity.free', { state: capacity.root_disk.state ? `${t(resourceStateTranslationKey(capacity.root_disk.state))} · ` : '', gib: capacity.root_disk.free_gib })} />
                {capacity.heavy_executions.waiting !== null && (
                  <CapacityItem label={t('settings.capacity.waiting')} value={String(capacity.heavy_executions.waiting)} detail={t('settings.capacity.kernelQueue')} />
                )}
              </dl>
              <div className="grid grid-cols-1 gap-3 sm:col-span-2 sm:grid-cols-2">
                <CapacityItem label={t('settings.capacity.cpu')} value={`${capacity.cpu_load.one_minute.toFixed(2)} / ${capacity.cpu_load.cpu_count} CPUs`} detail={t('settings.capacity.loadAverage')} />
                <ProfileCard count={profileCount} onClick={() => setProfilesOpen(true)} />
              </div>
            </div>
          </div>
        ) : (
          <p className="text-sm text-gray-400 py-3">{t('settings.capacity.unavailable')}</p>
        )}
      </div>

      <div className="bg-gray-800/60 border border-gray-700/50 rounded-xl p-5">
        <div className="flex items-center justify-between gap-3 mb-2"><h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">{t('settings.projects.title')}</h3><span className="text-xs text-gray-400">{t('settings.projects.registered', { count: projects.length })}</span></div>
        <p className="text-xs text-gray-400 mb-4">{t('settings.projects.help')}</p>
        {projects.length === 0 ? <div role="alert" className="mb-4 rounded-lg border border-amber-700/40 bg-amber-950/20 px-3 py-3 text-sm text-amber-200">{t('settings.projects.none')}</div> : <div className="space-y-2 mb-4">{projects.map(project => <div key={project.projectId} className="flex flex-col gap-2 rounded-lg border border-gray-700/40 bg-gray-900/50 px-3 py-3 sm:flex-row sm:items-center"><div className="min-w-0 flex-1"><div className="text-sm text-gray-200">{project.name} {project.isDefault && <span className="ml-1 text-xs text-emerald-400">{t('settings.projects.default')}</span>}</div><div className="truncate font-mono text-xs text-gray-400" title={project.path}>{project.path}</div>{project.description && <div className="text-xs text-gray-400 mt-1">{project.description}</div>}</div><div className="flex gap-1"><button type="button" aria-label={t('settings.projects.editNamed', { name: project.name })} title={t('settings.projects.edit')} onClick={() => setEditingProject({ ...project })} className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg bg-gray-800 text-gray-300 transition-colors hover:bg-gray-700 hover:text-white"><Pencil size={16} aria-hidden="true" /></button><button type="button" aria-label={t('settings.projects.setDefaultNamed', { name: project.name })} title={project.isDefault ? t('settings.projects.defaultProject') : t('settings.projects.setDefault')} onClick={() => makeDefault(project)} disabled={project.isDefault} className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg bg-gray-800 text-amber-300 transition-colors hover:bg-gray-700 disabled:cursor-default disabled:opacity-35"><Star size={16} aria-hidden="true" fill={project.isDefault ? 'currentColor' : 'none'} /></button><button type="button" aria-label={t('settings.projects.removeNamed', { name: project.name })} title={t('settings.projects.remove')} onClick={() => setPendingProjectDelete(project)} className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg text-red-300 transition-colors hover:bg-red-950/30 hover:text-red-200"><Trash2 size={16} aria-hidden="true" /></button></div></div>)}</div>}
        <button type="button" onClick={() => setCreateProjectOpen(true)} className="min-h-11 inline-flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500"><Plus size={14} /> {t('settings.projects.registerNew')}</button>
      </div>

      <div className="bg-gray-800/60 border border-gray-700/50 rounded-xl p-5">
        <div className="flex items-center justify-between gap-3 mb-2"><h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">{t('settings.branding.title')}</h3>{branding && <img src={branding.logoUrl} alt={t('settings.branding.logoPreview')} className="h-9 w-9 rounded-lg object-cover" />}</div>
        <p className="text-xs text-gray-400 mb-4">{t('settings.branding.help')}</p>
        <div className="grid gap-2 sm:grid-cols-2"><input aria-label={t('settings.branding.runtimeTitle')} value={brandingTitle} onChange={e => setBrandingTitle(e.target.value)} className="bg-gray-900 border border-gray-700 text-gray-200 text-sm rounded-lg px-3 py-2.5" /><input aria-label={t('settings.branding.runtimeSubtitle')} value={brandingSubtitle} onChange={e => setBrandingSubtitle(e.target.value)} className="bg-gray-900 border border-gray-700 text-gray-200 text-sm rounded-lg px-3 py-2.5" /></div>
        <div className="mt-3 flex flex-wrap items-center gap-2"><button type="button" onClick={saveBranding} disabled={brandingBusy} className="min-h-11 inline-flex items-center gap-2 rounded-lg bg-emerald-600 px-4 text-sm font-medium text-white disabled:opacity-40"><Save size={15} aria-hidden="true" /> {t('settings.branding.save')}</button><label className="min-h-11 cursor-pointer rounded-lg bg-gray-700 px-4 inline-flex items-center gap-2 text-sm text-gray-100"><Upload size={15} aria-hidden="true" /><span>{t('settings.branding.upload')}</span><input aria-label={t('settings.branding.uploadAria')} type="file" accept="image/png,image/webp" className="sr-only" onChange={e => uploadLogo(e.target.files?.[0])} /></label><button type="button" onClick={resetLogo} disabled={!branding?.customLogo || brandingBusy} className="min-h-11 inline-flex items-center gap-2 rounded-lg px-3 text-sm text-gray-300 hover:bg-gray-700 disabled:opacity-40"><RotateCcw size={15} aria-hidden="true" /> {t('settings.branding.reset')}</button></div>
      </div>

      {/* Agent Profile Directories */}
      <div className="bg-gray-800/60 border border-gray-700/50 rounded-xl p-5">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-1 mb-4">
          <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">
            {t('settings.profiles.directories')}
          </h3>
          {profileCount !== null && (
            <span className="text-xs text-gray-400">{t('settings.profiles.discovered', { count: profileCount })}</span>
          )}
        </div>
        <p className="text-xs text-gray-400 mb-2">
          {t('settings.profiles.directoriesHelp')}
        </p>
        <p className="text-xs text-emerald-400/70 mb-5">
          {t('settings.profiles.installHelp')} <code className="bg-gray-900 px-1.5 py-0.5 rounded text-emerald-300">threadcells install developer</code>
        </p>

        {Object.entries(settings.agent_dirs).length > 0 && (
          <div className="mb-4 space-y-2" aria-label={t('settings.profiles.managed')}>
            <p className="text-xs font-medium text-gray-300">{t('settings.profiles.managedTitle')}</p>
            {Object.entries(settings.agent_dirs).map(([provider, dir]) => (
              <div key={provider} className="flex min-w-0 items-center gap-2 rounded-lg border border-gray-700/30 bg-gray-900/50 px-3 py-2.5">
                <FolderOpen size={14} className="shrink-0 text-gray-400" />
                <span className="min-w-0 flex-1 truncate font-mono text-sm text-gray-300" title={dir}>{dir}</span>
                <span className="shrink-0 text-xs text-gray-400">{provider} · {t('settings.profiles.readOnly')}</span>
              </div>
            ))}
          </div>
        )}

        {dirs.length > 0 && (
          <div className="space-y-2 mb-4" aria-label={t('settings.profiles.additional')}>
            <p className="text-xs font-medium text-gray-300">{t('settings.profiles.additionalTitle')}</p>
            {dirs.map((dir, i) => (
              <div key={dir} className="flex items-center gap-2 bg-gray-900/50 border border-gray-700/30 rounded-lg px-3 py-2.5 min-w-0">
                <FolderOpen size={14} className="text-emerald-500 shrink-0" />
                <span className="text-sm text-gray-300 font-mono flex-1 truncate" title={dir}>{dir}</span>
                <button
                  onClick={() => removeDir(i)}
                  className="min-w-11 min-h-11 -my-2 inline-flex items-center justify-center text-gray-400 hover:text-red-400 transition-colors shrink-0 rounded-lg hover:bg-gray-800"
                  title={t('settings.profiles.removeDirectory', { path: dir })}
                  aria-label={t('settings.profiles.removeDirectory', { path: dir })}
                >
                  <X size={14} />
                </button>
              </div>
            ))}
          </div>
        )}

        {dirs.length === 0 && (
          <div className="text-center py-6 mb-4 bg-gray-900/30 border border-dashed border-gray-700 rounded-lg">
            <FolderOpen size={24} className="mx-auto text-gray-400 mb-2" />
            <p className="text-gray-400 text-sm">{t('settings.profiles.none')}</p>
            <p className="text-gray-400 text-xs mt-1">{t('settings.profiles.noneHelp')}</p>
          </div>
        )}

        <div className="flex flex-col sm:flex-row gap-2">
          <input
            type="text"
            value={newDir}
            onChange={e => setNewDir(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && addDir()}
            placeholder={t('settings.profiles.pathPlaceholder')}
            className="flex-1 bg-gray-900 border border-gray-700 text-gray-200 text-sm rounded-lg px-3 py-2.5 font-mono focus:border-emerald-500 focus:outline-none"
          />
          <button
            onClick={addDir}
            disabled={!newDir.trim()}
            className="min-h-11 justify-center flex items-center gap-1.5 bg-gray-700 hover:bg-gray-600 disabled:opacity-40 text-white text-sm px-4 py-2.5 rounded-lg transition-colors"
          >
            <Plus size={14} /> {t('common.add')}
          </button>
        </div>
        <p className="mt-2 text-xs text-gray-400">{t('settings.profiles.removalHelp')}</p>
      </div>

      {/* Actions */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3">
        <button
          onClick={handleSave}
          disabled={saving}
          className="min-h-11 justify-center flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-60 text-white text-sm font-medium px-5 py-2.5 rounded-lg transition-colors"
        >
          {saved ? <CheckCircle size={16} /> : <Save size={16} />}
          {saving ? t('settings.saving') : saved ? t('settings.savedLabel') : t('settings.save')}
        </button>
        <button
          onClick={() => { refreshProfiles(); showSnackbar({ type: 'info', message: t('settings.profiles.refreshing') }) }}
          className="min-h-11 justify-center flex items-center gap-2 bg-gray-700 hover:bg-gray-600 text-white text-sm px-4 py-2.5 rounded-lg transition-colors"
        >
          <RefreshCw size={14} /> {t('settings.profiles.refresh')}
        </button>
      </div>
      {capacityOpen && <div className="fixed inset-0 z-[60] flex items-center justify-center p-3"><div className="absolute inset-0 bg-black/60" onClick={() => setCapacityOpen(false)}/><div role="dialog" aria-modal="true" aria-labelledby="capacity-configure-title" className="relative max-h-[calc(100dvh-1.5rem)] w-full max-w-lg overflow-y-auto rounded-xl border border-gray-700 bg-gray-900 p-5 shadow-2xl"><div className="flex items-start justify-between"><div><h3 id="capacity-configure-title" className="font-semibold text-white">{t('settings.capacity.configureTitle')}</h3><p className="mt-1 text-xs text-gray-400">{t('settings.capacity.configureHelp')}</p></div><button aria-label={t('settings.capacity.close')} onClick={() => setCapacityOpen(false)} className="min-h-11 min-w-11 text-gray-400"><X/></button></div><div className="mt-4 grid gap-3 sm:grid-cols-2">{Object.entries(capacityDraft).map(([key, value]) => <label key={key} className="text-xs text-gray-400">{t(CAPACITY_FIELD_KEYS[key])}<input aria-label={key} type="number" min={key === 'max_resident_supervisors' ? 2 : 1} max={50} value={value} onChange={event => setCapacityDraft(current => ({ ...current, [key]: Number(event.target.value) }))} className="mt-1 min-h-11 w-full rounded-lg border border-gray-700 bg-gray-950 px-3 text-sm"/></label>)}</div><div className={`mt-4 rounded-lg border px-3 py-2 text-xs ${operatorAccess.status?.authenticated ? 'border-emerald-700/40 bg-emerald-950/20 text-emerald-200' : 'border-amber-700/40 bg-amber-950/20 text-amber-200'}`}>{t(operatorAccess.status?.authenticated ? 'settings.capacity.unlocked' : 'settings.capacity.unlockHelp')}</div><div className="mt-5 flex justify-end gap-2"><button className="min-h-11 rounded-lg px-4 text-sm text-gray-300" onClick={() => setCapacityOpen(false)}>{t('common.cancel')}</button><button disabled={!operatorAccess.status?.authenticated || capacityBusy} className="min-h-11 rounded-lg bg-emerald-600 px-4 text-sm text-white disabled:opacity-40" onClick={saveCapacity}>{capacityBusy ? t('common.saving') : t('settings.capacity.apply')}</button></div></div></div>}
      {profilesOpen && <div className="fixed inset-0 z-[60] flex items-center justify-center p-3"><div className="absolute inset-0 bg-black/60" onClick={() => setProfilesOpen(false)} /><div role="dialog" aria-modal="true" aria-label={t('settings.profiles.title')} className={`relative flex max-h-[90dvh] w-full flex-col overflow-hidden bg-gray-900 shadow-2xl ${profilesFullscreen ? 'fixed inset-0 max-w-none rounded-none' : 'max-w-2xl rounded-xl border border-gray-700/50'}`}><div className="flex items-center justify-between border-b border-gray-700/50 px-4 py-3"><h3 className="font-semibold text-white">{t('settings.profiles.title')} ({profiles.length})</h3><div className="flex gap-1"><button aria-label={profilesFullscreen ? t('common.exitFullscreen') : t('common.fullscreen')} onClick={() => setProfilesFullscreen(v => !v)} className="min-h-11 min-w-11 text-gray-400">{profilesFullscreen ? <Minimize2 /> : <Maximize2 />}</button><button aria-label={t('common.close')} onClick={() => setProfilesOpen(false)} className="min-h-11 min-w-11 text-gray-400"><X /></button></div></div><div className="flex-1 overflow-auto p-4 space-y-2">{profiles.map(profile => <div key={`${profile.source}-${profile.name}`} className="rounded-lg border border-gray-700/40 bg-gray-800/60 p-3"><div className="font-mono text-sm text-emerald-300">{profile.name}</div><p className="mt-1 text-xs text-gray-400">{profile.description || t('common.noDescription')}</p></div>)}</div></div></div>}
      <ConfirmModal open={!!pendingProject} title={t('settings.projects.registerTitle')} message={t(pendingProject?.createDirectory ? 'settings.projects.createDirectoryMessage' : 'settings.projects.existingDirectoryMessage')} details={pendingProject ? [{ label: t('common.name'), value: pendingProject.name }, { label: t('common.path'), value: pendingProject.path }] : []} confirmLabel={t('settings.projects.registerTitle')} variant="warning" loading={projectBusy} onConfirm={confirmProjectCreate} onCancel={() => setPendingProject(null)} />
      <ConfirmModal open={!!pendingProjectDelete} title={t('settings.projects.remove')} message={t('settings.projects.removeMessage')} details={pendingProjectDelete ? [{ label: t('common.project'), value: pendingProjectDelete.name }, { label: t('common.path'), value: pendingProjectDelete.path }] : []} confirmLabel={t('settings.projects.remove')} variant="danger" loading={projectBusy} onConfirm={confirmProjectDelete} onCancel={() => setPendingProjectDelete(null)} />
      {createProjectOpen && <div className="fixed inset-0 z-[60] flex items-center justify-center p-3 sm:p-4"><div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setCreateProjectOpen(false)} /><div role="dialog" aria-modal="true" aria-labelledby="register-project-title" className="relative w-full max-w-lg max-h-[calc(100dvh-1.5rem)] overflow-y-auto rounded-xl border border-gray-700/50 bg-gray-900 shadow-2xl"><div className="flex items-start justify-between gap-3 border-b border-gray-700/50 p-4 sm:p-5"><div><h3 id="register-project-title" className="text-base font-semibold text-white">{t('settings.projects.registerNew')}</h3><p className="mt-1 text-sm text-gray-400">{t('settings.projects.registerDescription')}</p></div><button type="button" aria-label={t('settings.projects.closeRegister')} onClick={() => setCreateProjectOpen(false)} className="min-h-11 min-w-11 shrink-0 rounded-lg text-gray-400 hover:bg-gray-800 hover:text-white"><X size={16} /></button></div><div className="space-y-4 p-4 sm:p-5"><label className="block text-xs text-gray-400">{t('common.name')}<input aria-label={t('settings.projects.newName')} value={projectName} onChange={e => setProjectName(e.target.value)} placeholder={t('settings.projects.namePlaceholder')} className="mt-1 min-h-11 w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2.5 text-sm text-gray-200 focus:border-emerald-500 focus:outline-none" /></label><label className="block text-xs text-gray-400">{t('common.path')}<input aria-label={t('settings.projects.newPath')} value={projectPath} onChange={e => setProjectPath(e.target.value)} placeholder={t('settings.projects.pathPlaceholder')} className="mt-1 min-h-11 w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2.5 font-mono text-sm text-gray-200 focus:border-emerald-500 focus:outline-none" /></label><label className="block text-xs text-gray-400">{t('common.description')} <span className="text-gray-400">({t('common.optional')})</span><textarea aria-label={t('settings.projects.newDescription')} value={projectDescription} onChange={e => setProjectDescription(e.target.value)} placeholder={t('settings.projects.descriptionPlaceholder')} className="mt-1 w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2.5 text-sm text-gray-200 focus:border-emerald-500 focus:outline-none" /></label><p className="-mt-2 text-xs text-gray-400">{t('settings.projects.guidanceHelp')}</p><label className="flex items-center gap-2 text-xs text-gray-400"><input type="checkbox" checked={createProjectDirectory} onChange={e => setCreateProjectDirectory(e.target.checked)} /> {t('settings.projects.createDirectory')}</label></div><div className="flex flex-col-reverse gap-3 border-t border-gray-700/50 bg-gray-800/30 p-4 sm:flex-row sm:items-center sm:justify-end sm:p-5"><button type="button" onClick={() => setCreateProjectOpen(false)} className="min-h-11 rounded-lg bg-gray-800 px-4 text-sm font-medium text-gray-300 hover:bg-gray-700">{t('common.cancel')}</button><button type="button" onClick={requestProjectCreate} disabled={!projectName.trim() || !projectPath.trim()} className="min-h-11 inline-flex items-center justify-center gap-2 rounded-lg bg-emerald-600 px-4 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-40"><Plus size={14} /> {t('settings.projects.registerTitle')}</button></div></div></div>}
      <ConfirmModal open={!!editingProject} title={t('settings.projects.edit')} message={t('settings.projects.editMessage')} confirmLabel={t('settings.projects.save')} variant="warning" loading={projectBusy} onConfirm={saveProjectEdit} onCancel={() => setEditingProject(null)}>
        <label className="block text-xs text-gray-400">{t('common.name')}<input aria-label={t('settings.projects.nameAria')} value={editingProject?.name || ''} onChange={e => setEditingProject(current => current ? { ...current, name: e.target.value } : current)} className="mt-1 w-full bg-gray-900 border border-gray-700 text-gray-200 text-sm rounded-lg px-3 py-2" /></label>
        <label className="block text-xs text-gray-400">{t('common.path')}<input aria-label={t('settings.projects.pathAria')} value={editingProject?.path || ''} onChange={e => setEditingProject(current => current ? { ...current, path: e.target.value } : current)} className="mt-1 w-full bg-gray-900 border border-gray-700 text-gray-200 text-sm rounded-lg px-3 py-2 font-mono" /></label>
        <label className="block text-xs text-gray-400">{t('common.description')}<textarea aria-label={t('settings.projects.descriptionAria')} value={editingProject?.description || ''} onChange={e => setEditingProject(current => current ? { ...current, description: e.target.value || null } : current)} className="mt-1 w-full bg-gray-900 border border-gray-700 text-gray-200 text-sm rounded-lg px-3 py-2" /></label>
        <p className="text-xs text-gray-400">{t('settings.projects.guidanceHelp')}</p>
      </ConfirmModal>
    </div>
  )
}

const CAPACITY_REASON_LABELS: Record<string, TranslationKey> = {
  ROOT_DISK_PRESSURE: 'settings.capacity.reason.diskPressure',
  DISK_CRITICAL: 'settings.capacity.reason.diskCritical',
  root_free_below_green: 'settings.capacity.reason.diskGreen',
  memory_below_red: 'settings.capacity.reason.memoryRed',
  memory_below_green: 'settings.capacity.reason.memoryGreen',
  critical_memory_pressure: 'settings.capacity.reason.memoryCritical',
  sustained_memory_pressure: 'settings.capacity.reason.memoryYellow',
}

function capacityReasonLabel(reason: string, t: (key: TranslationKey) => string) {
  return CAPACITY_REASON_LABELS[reason] ? t(CAPACITY_REASON_LABELS[reason]) : reason
}

function CapacityItem({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="bg-gray-900/50 border border-gray-700/30 rounded-lg px-3 py-3">
      <dt className="text-xs text-gray-400">{label}</dt>
      <dd className="text-sm text-gray-200 font-medium mt-1">{value}</dd>
      <dd className="text-xs text-gray-400 mt-0.5">{detail}</dd>
    </div>
  )
}

function ProfileCard({ count, onClick }: { count: number | null; onClick: () => void }) {
  const { t } = useI18n()
  return (
    <button type="button" aria-label={t('settings.profiles.title')} onClick={onClick} className="rounded-lg border border-gray-700/30 bg-gray-900/50 p-1 text-left transition-colors hover:border-emerald-700/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400">
      <div className="h-full rounded-md border border-emerald-500/25 px-3 py-2">
        <div className="text-xs text-gray-400">{t('settings.profiles.title')}</div>
        <div className="mt-1 text-sm font-medium text-gray-200">{count ?? '—'}</div>
        <p className="mt-0.5 text-xs text-gray-400">{t('settings.profiles.description')}</p>
        <p className="mt-2 text-[11px] text-gray-400">{t('settings.profiles.click')}</p>
      </div>
    </button>
  )
}
