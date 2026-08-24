import { useState, useEffect } from 'react'
import { api, AgentDirsSettings, OrchestrationCapacity, AgentProfileInfo, Project, RuntimeBranding } from '../api'
import { useStore } from '../store'
import { Activity, FolderOpen, Save, Plus, X, RefreshCw, CheckCircle, Maximize2, Minimize2, Upload, RotateCcw, Pencil, Star, Trash2 } from 'lucide-react'
import { ConfirmModal } from './ConfirmModal'
import { OperatorAccessCard, useOperatorAccess } from './OperatorAccess'

export function SettingsPanel() {
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
      // Merge all configured dirs into a single flat list, deduped
      const allDirs = [
        ...Object.values(s.agent_dirs),
        ...s.extra_dirs,
      ].filter((d, i, arr) => d && arr.indexOf(d) === i)
      setDirs(allDirs)
    } catch {
      showSnackbar({ type: 'error', message: 'Failed to load settings' })
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
      showSnackbar({ type: 'success', message: 'Capacity policy updated; decreases drain without terminating active work' })
    } catch (error: any) { showSnackbar({ type: 'error', message: error.message || 'Capacity update failed' }); await operatorAccess.refresh() }
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
      // Send all dirs as extra_dirs — the backend will scan all of them
      const result = await api.setAgentDirs({ extra_dirs: dirs })
      setSettings(result)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
      showSnackbar({ type: 'success', message: 'Settings saved' })
      refreshProfiles()
    } catch (e: any) {
      showSnackbar({ type: 'error', message: e.message || 'Failed to save' })
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
      showSnackbar({ type: 'success', message: 'Project registered' })
    } catch (error: any) { showSnackbar({ type: 'error', message: error.message || 'Failed to register project' }) }
    finally { setProjectBusy(false) }
  }

  const makeDefault = async (project: Project) => {
    try { await api.setDefaultProject(project.projectId); await refreshProjects(); showSnackbar({ type: 'success', message: `${project.name} is now the default project` }) }
    catch (error: any) { showSnackbar({ type: 'error', message: error.message || 'Failed to change default project' }) }
  }

  const confirmProjectDelete = async () => {
    if (!pendingProjectDelete) return
    setProjectBusy(true)
    try { await api.deleteProject(pendingProjectDelete.projectId); await refreshProjects(); setPendingProjectDelete(null); showSnackbar({ type: 'success', message: 'Project removed from the registry' }) }
    catch (error: any) { showSnackbar({ type: 'error', message: error.message || 'Failed to remove project' }) }
    finally { setProjectBusy(false) }
  }

  const saveProjectEdit = async () => {
    if (!editingProject) return
    setProjectBusy(true)
    try {
      await api.updateProject(editingProject.projectId, { name: editingProject.name, path: editingProject.path, description: editingProject.description || null })
      setEditingProject(null); await refreshProjects(); showSnackbar({ type: 'success', message: 'Project updated' })
    } catch (error: any) { showSnackbar({ type: 'error', message: error.message || 'Failed to update project' }) }
    finally { setProjectBusy(false) }
  }

  const saveBranding = async () => {
    setBrandingBusy(true)
    try { setBranding(await api.updateBranding({ title: brandingTitle, subtitle: brandingSubtitle })); showSnackbar({ type: 'success', message: 'Branding updated' }) }
    catch (error: any) { showSnackbar({ type: 'error', message: error.message || 'Failed to update branding' }) }
    finally { setBrandingBusy(false) }
  }
  const uploadLogo = async (file?: File) => {
    if (!file) return
    setBrandingBusy(true)
    try { setBranding(await api.uploadBrandingLogo(file)); showSnackbar({ type: 'success', message: 'Runtime logo updated' }) }
    catch (error: any) { showSnackbar({ type: 'error', message: error.message || 'Logo upload failed' }) }
    finally { setBrandingBusy(false) }
  }
  const resetLogo = async () => {
    setBrandingBusy(true)
    try { setBranding(await api.resetBrandingLogo()); showSnackbar({ type: 'success', message: 'Runtime logo reset' }) }
    catch (error: any) { showSnackbar({ type: 'error', message: error.message || 'Logo reset failed' }) }
    finally { setBrandingBusy(false) }
  }

  if (!settings) {
    return <div className="text-gray-400 text-sm py-8 text-center">Loading settings...</div>
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
              Orchestration Capacity
            </h3>
          </div>
          <div className="flex items-center gap-2">{capacity && (
            <span
              aria-label={`Resource health ${capacity.resource_state}`}
              className={`text-xs font-semibold px-2.5 py-1 rounded-full ${
                capacity.resource_state === 'GREEN'
                  ? 'bg-emerald-500/15 text-emerald-300'
                  : capacity.resource_state === 'YELLOW'
                    ? 'bg-amber-500/15 text-amber-300'
                    : 'bg-red-500/15 text-red-300'
              }`}
            >
              {capacity.resource_state}
            </span>
          )}<button type="button" onClick={openCapacity} className="min-h-11 rounded-lg border border-gray-700 px-3 text-xs text-gray-300 hover:border-emerald-600 hover:text-emerald-300">Configure</button></div>
        </div>
        <p className="text-xs text-gray-400 mb-4">
          Effective persisted limits and live utilization. The 5 / 3 / 2 / 1 values are a host recommendation, not hardcoded runtime authority; decreases drain and never terminate active work.
        </p>
        {capacity ? (
          <div className="space-y-3">
            {capacity.reasons.length > 0 && (
              <section
                aria-label={`${capacity.resource_state} resource health reasons`}
                className={`rounded-lg border px-3 py-3 ${capacity.resource_state === 'RED' ? 'border-red-700/50 bg-red-950/20' : 'border-amber-700/50 bg-amber-950/20'}`}
              >
                <h4 className={`text-xs font-semibold ${capacity.resource_state === 'RED' ? 'text-red-200' : 'text-amber-200'}`}>Health drivers</h4>
                <ul className="mt-1 list-disc space-y-1 pl-5 text-xs text-gray-300">
                  {capacity.reasons.map(reason => <li key={reason} title={reason}>{capacityReasonLabel(reason)}</li>)}
                </ul>
              </section>
            )}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <dl className="contents" aria-label="Orchestration capacity details">
                <CapacityItem label="Resident supervisors / owners" value={`${capacity.resident_supervisors.active} / ${capacity.resident_supervisors.limit}`} detail={capacity.resident_supervisors.certain ? `${capacity.resident_supervisors.available} available; top-level conductors remain resident${capacity.resident_supervisors.draining ? '; draining' : ''}` : 'inventory unavailable; admission closed'} />
                <CapacityItem label="Provider executions" value={`${capacity.provider_executions.active} / ${capacity.provider_executions.limit}`} detail={capacity.provider_executions.certain ? `${capacity.provider_executions.available} available; active model turns only${capacity.provider_executions.draining ? '; draining' : ''}` : 'inventory unavailable; admission closed'} />
                <CapacityItem label="Work contexts" value={`${capacity.work_contexts.active} / ${capacity.work_contexts.limit}`} detail={capacity.work_contexts.certain ? `${capacity.work_contexts.available} available; delegated resident workers/reviewers${capacity.work_contexts.draining ? '; draining' : ''}` : 'inventory unavailable; admission closed'} />
                <CapacityItem label="Heavy executions" value={`${capacity.heavy_executions.active} / ${capacity.heavy_executions.limit}`} detail={`${capacity.heavy_executions.available} available${capacity.heavy_executions.draining ? '; draining' : ''}`} />
                <CapacityItem label="Memory available" value={`${capacity.memory.available_mib} MiB`} detail={`Pressure avg10 ${capacity.memory_pressure.some_avg10}`} />
                <CapacityItem label="Root disk" value={`${capacity.root_disk.used_percent}% used`} detail={`${capacity.root_disk.state ? `${capacity.root_disk.state} · ` : ''}${capacity.root_disk.free_gib} GiB free`} />
                {capacity.heavy_executions.waiting !== null && (
                  <CapacityItem label="Heavy waiting" value={String(capacity.heavy_executions.waiting)} detail="Kernel-backed queue" />
                )}
              </dl>
              <div className="grid grid-cols-1 gap-3 sm:col-span-2 sm:grid-cols-2">
                <CapacityItem label="CPU load" value={`${capacity.cpu_load.one_minute.toFixed(2)} / ${capacity.cpu_load.cpu_count} CPUs`} detail="1m Linux load average" />
                <ProfileCard count={profileCount} onClick={() => setProfilesOpen(true)} />
              </div>
            </div>
          </div>
        ) : (
          <p className="text-sm text-gray-400 py-3">Operational status is temporarily unavailable.</p>
        )}
      </div>

      <div className="bg-gray-800/60 border border-gray-700/50 rounded-xl p-5">
        <div className="flex items-center justify-between gap-3 mb-2"><h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">Projects</h3><span className="text-xs text-gray-400">{projects.length} registered</span></div>
        <p className="text-xs text-gray-400 mb-4">Projects provide the server-authoritative path and context for new agents and flows. Removing a project never deletes its directory or historical launch records.</p>
        {projects.length === 0 ? <div role="alert" className="mb-4 rounded-lg border border-amber-700/40 bg-amber-950/20 px-3 py-3 text-sm text-amber-200">No projects are configured. New launches can still use a legacy working directory.</div> : <div className="space-y-2 mb-4">{projects.map(project => <div key={project.projectId} className="flex flex-col gap-2 rounded-lg border border-gray-700/40 bg-gray-900/50 px-3 py-3 sm:flex-row sm:items-center"><div className="min-w-0 flex-1"><div className="text-sm text-gray-200">{project.name} {project.isDefault && <span className="ml-1 text-xs text-emerald-400">Default</span>}</div><div className="truncate font-mono text-xs text-gray-400" title={project.path}>{project.path}</div>{project.description && <div className="text-xs text-gray-400 mt-1">{project.description}</div>}</div><div className="flex gap-1"><button type="button" aria-label={`Edit ${project.name}`} title="Edit project" onClick={() => setEditingProject({ ...project })} className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg bg-gray-800 text-gray-300 transition-colors hover:bg-gray-700 hover:text-white"><Pencil size={16} aria-hidden="true" /></button><button type="button" aria-label={`Set ${project.name} as default`} title={project.isDefault ? 'Default project' : 'Set as default'} onClick={() => makeDefault(project)} disabled={project.isDefault} className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg bg-gray-800 text-amber-300 transition-colors hover:bg-gray-700 disabled:cursor-default disabled:opacity-35"><Star size={16} aria-hidden="true" fill={project.isDefault ? 'currentColor' : 'none'} /></button><button type="button" aria-label={`Remove ${project.name}`} title="Remove project" onClick={() => setPendingProjectDelete(project)} className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg text-red-300 transition-colors hover:bg-red-950/30 hover:text-red-200"><Trash2 size={16} aria-hidden="true" /></button></div></div>)}</div>}
        <button type="button" onClick={() => setCreateProjectOpen(true)} className="min-h-11 inline-flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500"><Plus size={14} /> Register New Project</button>
      </div>

      <div className="bg-gray-800/60 border border-gray-700/50 rounded-xl p-5">
        <div className="flex items-center justify-between gap-3 mb-2"><h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">Runtime Branding</h3>{branding && <img src={branding.logoUrl} alt="Runtime logo preview" className="h-9 w-9 rounded-lg object-cover" />}</div>
        <p className="text-xs text-gray-400 mb-4">Update the live application title, subtitle, and PNG or WebP logo. Changes apply without a rebuild or restart.</p>
        <div className="grid gap-2 sm:grid-cols-2"><input aria-label="Runtime title" value={brandingTitle} onChange={e => setBrandingTitle(e.target.value)} className="bg-gray-900 border border-gray-700 text-gray-200 text-sm rounded-lg px-3 py-2.5" /><input aria-label="Runtime subtitle" value={brandingSubtitle} onChange={e => setBrandingSubtitle(e.target.value)} className="bg-gray-900 border border-gray-700 text-gray-200 text-sm rounded-lg px-3 py-2.5" /></div>
        <div className="mt-3 flex flex-wrap items-center gap-2"><button type="button" onClick={saveBranding} disabled={brandingBusy} className="min-h-11 inline-flex items-center gap-2 rounded-lg bg-emerald-600 px-4 text-sm font-medium text-white disabled:opacity-40"><Save size={15} aria-hidden="true" /> Save branding</button><label className="min-h-11 cursor-pointer rounded-lg bg-gray-700 px-4 inline-flex items-center gap-2 text-sm text-gray-100"><Upload size={15} aria-hidden="true" /><span>Upload PNG or WebP</span><input aria-label="Upload runtime logo" type="file" accept="image/png,image/webp" className="sr-only" onChange={e => uploadLogo(e.target.files?.[0])} /></label><button type="button" onClick={resetLogo} disabled={!branding?.customLogo || brandingBusy} className="min-h-11 inline-flex items-center gap-2 rounded-lg px-3 text-sm text-gray-300 hover:bg-gray-700 disabled:opacity-40"><RotateCcw size={15} aria-hidden="true" /> Reset logo</button></div>
      </div>

      {/* Agent Profile Directories */}
      <div className="bg-gray-800/60 border border-gray-700/50 rounded-xl p-5">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-1 mb-4">
          <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">
            Agent Profile Directories
          </h3>
          {profileCount !== null && (
            <span className="text-xs text-gray-400">{profileCount} profiles discovered</span>
          )}
        </div>
        <p className="text-xs text-gray-400 mb-2">
          Add directories where your agent profile <code className="text-gray-400">.md</code> files are stored.
          ThreadCells scans all directories and makes profiles available to every provider.
        </p>
        <p className="text-xs text-emerald-400/70 mb-5">
          Install built-in profiles with: <code className="bg-gray-900 px-1.5 py-0.5 rounded text-emerald-300">threadcells install developer</code>
        </p>

        {dirs.length > 0 && (
          <div className="space-y-2 mb-4">
            {dirs.map((dir, i) => (
              <div key={i} className="flex items-center gap-2 bg-gray-900/50 border border-gray-700/30 rounded-lg px-3 py-2.5 min-w-0">
                <FolderOpen size={14} className="text-emerald-500 shrink-0" />
                <span className="text-sm text-gray-300 font-mono flex-1 truncate" title={dir}>{dir}</span>
                <button
                  onClick={() => removeDir(i)}
                  className="min-w-11 min-h-11 -my-2 inline-flex items-center justify-center text-gray-400 hover:text-red-400 transition-colors shrink-0 rounded-lg hover:bg-gray-800"
                  title="Remove directory"
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
            <p className="text-gray-400 text-sm">No directories configured.</p>
            <p className="text-gray-400 text-xs mt-1">Add a directory below to start discovering agent profiles.</p>
          </div>
        )}

        <div className="flex flex-col sm:flex-row gap-2">
          <input
            type="text"
            value={newDir}
            onChange={e => setNewDir(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && addDir()}
            placeholder="/path/to/agent-profiles"
            className="flex-1 bg-gray-900 border border-gray-700 text-gray-200 text-sm rounded-lg px-3 py-2.5 font-mono focus:border-emerald-500 focus:outline-none"
          />
          <button
            onClick={addDir}
            disabled={!newDir.trim()}
            className="min-h-11 justify-center flex items-center gap-1.5 bg-gray-700 hover:bg-gray-600 disabled:opacity-40 text-white text-sm px-4 py-2.5 rounded-lg transition-colors"
          >
            <Plus size={14} /> Add
          </button>
        </div>
      </div>

      {/* Actions */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3">
        <button
          onClick={handleSave}
          disabled={saving}
          className="min-h-11 justify-center flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-60 text-white text-sm font-medium px-5 py-2.5 rounded-lg transition-colors"
        >
          {saved ? <CheckCircle size={16} /> : <Save size={16} />}
          {saving ? 'Saving...' : saved ? 'Saved' : 'Save Settings'}
        </button>
        <button
          onClick={() => { refreshProfiles(); showSnackbar({ type: 'info', message: 'Refreshing profiles...' }) }}
          className="min-h-11 justify-center flex items-center gap-2 bg-gray-700 hover:bg-gray-600 text-white text-sm px-4 py-2.5 rounded-lg transition-colors"
        >
          <RefreshCw size={14} /> Refresh Profiles
        </button>
      </div>
      {capacityOpen && <div className="fixed inset-0 z-[60] flex items-center justify-center p-3"><div className="absolute inset-0 bg-black/60" onClick={() => setCapacityOpen(false)}/><div role="dialog" aria-modal="true" aria-labelledby="capacity-configure-title" className="relative max-h-[calc(100dvh-1.5rem)] w-full max-w-lg overflow-y-auto rounded-xl border border-gray-700 bg-gray-900 p-5 shadow-2xl"><div className="flex items-start justify-between"><div><h3 id="capacity-configure-title" className="font-semibold text-white">Configure orchestration capacity</h3><p className="mt-1 text-xs text-gray-400">Increases apply immediately. Decreases enter draining state until existing leases and resident contexts finish.</p></div><button aria-label="Close capacity configuration" onClick={() => setCapacityOpen(false)} className="min-h-11 min-w-11 text-gray-400"><X/></button></div><div className="mt-4 grid gap-3 sm:grid-cols-2">{Object.entries(capacityDraft).map(([key, value]) => <label key={key} className="text-xs text-gray-400">{key.replace(/^max_/, '').replace(/_/g, ' ')}<input aria-label={key} type="number" min={key === 'max_resident_supervisors' ? 2 : 1} max={50} value={value} onChange={event => setCapacityDraft(current => ({ ...current, [key]: Number(event.target.value) }))} className="mt-1 min-h-11 w-full rounded-lg border border-gray-700 bg-gray-950 px-3 text-sm"/></label>)}</div><div className={`mt-4 rounded-lg border px-3 py-2 text-xs ${operatorAccess.status?.authenticated ? 'border-emerald-700/40 bg-emerald-950/20 text-emerald-200' : 'border-amber-700/40 bg-amber-950/20 text-amber-200'}`}>{operatorAccess.status?.authenticated ? 'Operator changes are unlocked for this browser.' : 'Unlock operator changes on the General page before applying capacity settings.'}</div><div className="mt-5 flex justify-end gap-2"><button className="min-h-11 rounded-lg px-4 text-sm text-gray-300" onClick={() => setCapacityOpen(false)}>Cancel</button><button disabled={!operatorAccess.status?.authenticated || capacityBusy} className="min-h-11 rounded-lg bg-emerald-600 px-4 text-sm text-white disabled:opacity-40" onClick={saveCapacity}>{capacityBusy ? 'Saving…' : 'Apply capacity'}</button></div></div></div>}
      {profilesOpen && <div className="fixed inset-0 z-[60] flex items-center justify-center p-3"><div className="absolute inset-0 bg-black/60" onClick={() => setProfilesOpen(false)} /><div role="dialog" aria-modal="true" aria-label="Profiles" className={`relative flex max-h-[90dvh] w-full flex-col overflow-hidden bg-gray-900 shadow-2xl ${profilesFullscreen ? 'fixed inset-0 max-w-none rounded-none' : 'max-w-2xl rounded-xl border border-gray-700/50'}`}><div className="flex items-center justify-between border-b border-gray-700/50 px-4 py-3"><h3 className="font-semibold text-white">Profiles ({profiles.length})</h3><div className="flex gap-1"><button aria-label={profilesFullscreen ? 'Exit fullscreen' : 'Fullscreen'} onClick={() => setProfilesFullscreen(v => !v)} className="min-h-11 min-w-11 text-gray-400">{profilesFullscreen ? <Minimize2 /> : <Maximize2 />}</button><button aria-label="Close" onClick={() => setProfilesOpen(false)} className="min-h-11 min-w-11 text-gray-400"><X /></button></div></div><div className="flex-1 overflow-auto p-4 space-y-2">{profiles.map(profile => <div key={`${profile.source}-${profile.name}`} className="rounded-lg border border-gray-700/40 bg-gray-800/60 p-3"><div className="font-mono text-sm text-emerald-300">{profile.name}</div><p className="mt-1 text-xs text-gray-400">{profile.description || 'No description provided'}</p></div>)}</div></div></div>}
      <ConfirmModal open={!!pendingProject} title="Register project" message={pendingProject?.createDirectory ? 'ThreadCells will create only the final directory component and then register this project.' : 'ThreadCells will register this existing directory as a project.'} details={pendingProject ? [{ label: 'Name', value: pendingProject.name }, { label: 'Path', value: pendingProject.path }] : []} confirmLabel="Register project" variant="warning" loading={projectBusy} onConfirm={confirmProjectCreate} onCancel={() => setPendingProject(null)} />
      <ConfirmModal open={!!pendingProjectDelete} title="Remove project" message="This removes only the registry entry. The project directory and historical launch context will remain." details={pendingProjectDelete ? [{ label: 'Project', value: pendingProjectDelete.name }, { label: 'Path', value: pendingProjectDelete.path }] : []} confirmLabel="Remove project" variant="danger" loading={projectBusy} onConfirm={confirmProjectDelete} onCancel={() => setPendingProjectDelete(null)} />
      {createProjectOpen && <div className="fixed inset-0 z-[60] flex items-center justify-center p-3 sm:p-4"><div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setCreateProjectOpen(false)} /><div role="dialog" aria-modal="true" aria-labelledby="register-project-title" className="relative w-full max-w-lg max-h-[calc(100dvh-1.5rem)] overflow-y-auto rounded-xl border border-gray-700/50 bg-gray-900 shadow-2xl"><div className="flex items-start justify-between gap-3 border-b border-gray-700/50 p-4 sm:p-5"><div><h3 id="register-project-title" className="text-base font-semibold text-white">Register New Project</h3><p className="mt-1 text-sm text-gray-400">Add a server-authoritative project path and its operational context.</p></div><button type="button" aria-label="Close register new project" onClick={() => setCreateProjectOpen(false)} className="min-h-11 min-w-11 shrink-0 rounded-lg text-gray-400 hover:bg-gray-800 hover:text-white"><X size={16} /></button></div><div className="space-y-4 p-4 sm:p-5"><label className="block text-xs text-gray-400">Name<input aria-label="New project name" value={projectName} onChange={e => setProjectName(e.target.value)} placeholder="Project name" className="mt-1 min-h-11 w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2.5 text-sm text-gray-200 focus:border-emerald-500 focus:outline-none" /></label><label className="block text-xs text-gray-400">Path<input aria-label="New project path" value={projectPath} onChange={e => setProjectPath(e.target.value)} placeholder="/absolute/project/path" className="mt-1 min-h-11 w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2.5 font-mono text-sm text-gray-200 focus:border-emerald-500 focus:outline-none" /></label><label className="block text-xs text-gray-400">Description <span className="text-gray-400">(optional)</span><textarea aria-label="New project description" value={projectDescription} onChange={e => setProjectDescription(e.target.value)} placeholder="Description (optional)" className="mt-1 w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2.5 text-sm text-gray-200 focus:border-emerald-500 focus:outline-none" /></label><p className="-mt-2 text-xs text-gray-400">Used both as a human-readable description and as project-scoped operational guidance for agents and flows.</p><label className="flex items-center gap-2 text-xs text-gray-400"><input type="checkbox" checked={createProjectDirectory} onChange={e => setCreateProjectDirectory(e.target.checked)} /> Create the final directory if it does not exist</label></div><div className="flex flex-col-reverse gap-3 border-t border-gray-700/50 bg-gray-800/30 p-4 sm:flex-row sm:items-center sm:justify-end sm:p-5"><button type="button" onClick={() => setCreateProjectOpen(false)} className="min-h-11 rounded-lg bg-gray-800 px-4 text-sm font-medium text-gray-300 hover:bg-gray-700">Cancel</button><button type="button" onClick={requestProjectCreate} disabled={!projectName.trim() || !projectPath.trim()} className="min-h-11 inline-flex items-center justify-center gap-2 rounded-lg bg-emerald-600 px-4 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-40"><Plus size={14} /> Register project</button></div></div></div>}
      <ConfirmModal open={!!editingProject} title="Edit project" message="This updates only the registry metadata. Existing terminal and flow history remains unchanged." confirmLabel="Save project" variant="warning" loading={projectBusy} onConfirm={saveProjectEdit} onCancel={() => setEditingProject(null)}>
        <label className="block text-xs text-gray-400">Name<input aria-label="Project name" value={editingProject?.name || ''} onChange={e => setEditingProject(current => current ? { ...current, name: e.target.value } : current)} className="mt-1 w-full bg-gray-900 border border-gray-700 text-gray-200 text-sm rounded-lg px-3 py-2" /></label>
        <label className="block text-xs text-gray-400">Path<input aria-label="Project path" value={editingProject?.path || ''} onChange={e => setEditingProject(current => current ? { ...current, path: e.target.value } : current)} className="mt-1 w-full bg-gray-900 border border-gray-700 text-gray-200 text-sm rounded-lg px-3 py-2 font-mono" /></label>
        <label className="block text-xs text-gray-400">Description<textarea aria-label="Project description" value={editingProject?.description || ''} onChange={e => setEditingProject(current => current ? { ...current, description: e.target.value || null } : current)} className="mt-1 w-full bg-gray-900 border border-gray-700 text-gray-200 text-sm rounded-lg px-3 py-2" /></label>
        <p className="text-xs text-gray-400">Used both as a human-readable description and as project-scoped operational guidance for agents and flows.</p>
      </ConfirmModal>
    </div>
  )
}

const CAPACITY_REASON_LABELS: Record<string, string> = {
  ROOT_DISK_PRESSURE: 'Root disk usage reached a pressure threshold',
  DISK_CRITICAL: 'Root disk usage reached the CRITICAL threshold',
  root_free_below_green: 'Root disk free space is below the GREEN target',
  memory_below_red: 'Available memory is below the RED floor',
  memory_below_green: 'Available memory is below the GREEN target',
  critical_memory_pressure: 'Memory PSI full pressure reached the RED threshold',
  sustained_memory_pressure: 'Memory PSI pressure reached the YELLOW threshold',
}

function capacityReasonLabel(reason: string) {
  return CAPACITY_REASON_LABELS[reason] || reason
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
  return (
    <button type="button" aria-label="Profiles" onClick={onClick} className="rounded-lg border border-gray-700/30 bg-gray-900/50 p-1 text-left transition-colors hover:border-emerald-700/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400">
      <div className="h-full rounded-md border border-emerald-500/25 px-3 py-2">
        <div className="text-xs text-gray-400">Profiles</div>
        <div className="mt-1 text-sm font-medium text-gray-200">{count ?? '—'}</div>
        <p className="mt-0.5 text-xs text-gray-400">Available agent profiles and their descriptions</p>
        <p className="mt-2 text-[11px] text-gray-400">click me -&gt;</p>
      </div>
    </button>
  )
}
