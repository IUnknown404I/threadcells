import { useEffect, useMemo, useState } from 'react'
import { BookOpen, Boxes, CheckCircle2, Clock3, Database, HardDrive, HeartHandshake, Info, Search, ShieldCheck, Sparkles } from 'lucide-react'
import { api, HousekeepingSettings, OrchestrationCapacity, ProviderSettings, RegistryRecord } from '../api'
import { BUILD_IDENTITY } from '../buildIdentity'
import { providerRuntimeLabel } from '../providerAvailability'
import { OperatorAccessCard, useOperatorAccess } from './OperatorAccess'
import { SettingsPanel } from './SettingsPanel'
import { TelegramSettings } from './TelegramSettings'

export type SettingsSection = 'general' | 'profiles' | 'providers' | 'housekeeping' | 'telegram' | 'about'

const SECTIONS: Array<{ key: SettingsSection; label: string; path: string }> = [
  { key: 'general', label: 'General', path: '/settings' },
  { key: 'profiles', label: 'Profiles', path: '/settings/profiles' },
  { key: 'providers', label: 'Providers', path: '/settings/providers' },
  { key: 'housekeeping', label: 'Housekeeping', path: '/settings/housekeeping' },
  { key: 'telegram', label: 'Telegram', path: '/settings/telegram' },
  { key: 'about', label: 'About', path: '/settings/about' },
]

function Artifact({ value, label = 'Technical details' }: { value: unknown; label?: string }) {
  if (!value) return null
  return <details className="rounded-xl border border-gray-700/60 bg-gray-900/50 p-3"><summary className="min-h-9 cursor-pointer text-xs font-medium text-gray-400">{label}</summary><pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap break-all text-xs text-gray-300">{JSON.stringify(value, null, 2)}</pre></details>
}

function ProfilesSettings() {
  const access = useOperatorAccess()
  const [profiles, setProfiles] = useState<RegistryRecord[]>([])
  const [document, setDocument] = useState('')
  const [duplicate, setDuplicate] = useState(false)
  const [search, setSearch] = useState('')
  const [kind, setKind] = useState<'all' | 'built-in' | 'custom'>('all')
  const [advanced, setAdvanced] = useState(false)
  const [result, setResult] = useState<unknown>(null)
  const [error, setError] = useState('')
  const load = () => api.listRegistryProfiles(true).then(setProfiles).catch(reason => setError(reason.message))
  useEffect(() => { void load() }, [])
  const visible = useMemo(() => profiles.filter(profile => {
    const matchesKind = kind === 'all' || (kind === 'built-in' ? profile.built_in : !profile.built_in)
    const haystack = `${profile.profile_id} ${profile.display_name} ${profile.description || ''} ${profile.document.model || ''}`.toLowerCase()
    return matchesKind && haystack.includes(search.trim().toLowerCase())
  }), [profiles, search, kind])
  const parsed = () => JSON.parse(document) as Record<string, unknown>
  const act = async (operation: () => Promise<unknown>, needsOperator = false) => {
    setError('')
    if (needsOperator && !access.status?.authenticated) { setError('Unlock operator changes before modifying the registry.'); return }
    try { setResult(await operation()); await load() } catch (reason: any) { setError(reason.message || 'Profile operation failed') }
  }
  const edit = async (profile: RegistryRecord) => {
    try {
      const exported = await api.exportProfile(profile.profile_id!)
      setDocument(JSON.stringify(exported.document, null, 2))
      setDuplicate(profile.built_in)
      setAdvanced(true)
    } catch (reason: any) { setError(reason.message || 'Could not load the profile document') }
  }
  return <section className="space-y-4" aria-labelledby="profiles-settings-heading">
    <div><h1 id="profiles-settings-heading" className="text-xl font-semibold text-white">Profile Registry</h1><p className="mt-1 text-sm text-gray-400">Discover immutable built-ins and manage custom profile revisions without changing historical launch snapshots.</p></div>
    <OperatorAccessCard access={access} compact />
    <div className="flex flex-col gap-2 rounded-xl border border-gray-700/60 bg-gray-800/60 p-3 sm:flex-row sm:items-center">
      <label className="relative min-w-0 flex-1"><span className="sr-only">Search profiles</span><Search size={15} aria-hidden="true" className="absolute left-3 top-3.5 text-gray-500"/><input aria-label="Search profiles" value={search} onChange={event => setSearch(event.target.value)} placeholder="Search by ID, description, or model" className="min-h-11 w-full rounded-lg border border-gray-700 bg-gray-950 pl-9 pr-3 text-sm text-gray-100 focus:border-emerald-500 focus:outline-none"/></label>
      <select aria-label="Profile type" value={kind} onChange={event => setKind(event.target.value as typeof kind)} className="min-h-11 rounded-lg border border-gray-700 bg-gray-950 px-3 text-sm"><option value="all">All profiles</option><option value="built-in">Built-in</option><option value="custom">Custom</option></select>
      <span className="px-2 text-xs text-gray-500">{visible.length} of {profiles.length}</span>
    </div>
    <div className="grid gap-3 lg:grid-cols-2">{visible.map(profile => <article key={profile.profile_id} className="rounded-xl border border-gray-700/60 bg-gray-800/60 p-4"><div className="flex items-start justify-between gap-3"><div className="min-w-0"><h2 className="truncate font-mono text-sm text-emerald-300">{profile.profile_id}</h2><p className="mt-1 text-xs leading-5 text-gray-400">{profile.description}</p></div><span className="shrink-0 rounded-full bg-gray-900 px-2 py-1 text-[11px] text-gray-400">{profile.built_in ? 'Built-in' : 'Custom'} · r{profile.revision_number}</span></div><dl className="mt-3 grid grid-cols-2 gap-2 rounded-lg bg-gray-900/50 p-3 text-xs"><div><dt className="text-gray-600">Provider</dt><dd className="mt-1 text-gray-300">{String(profile.document.provider_config_id || '—')}</dd></div><div><dt className="text-gray-600">Model</dt><dd className="mt-1 break-all text-gray-300">{String(profile.document.model || 'Provider default')}</dd></div><div><dt className="text-gray-600">Reasoning</dt><dd className="mt-1 capitalize text-gray-300">{String(profile.document.reasoning_level || 'default')}</dd></div><div><dt className="text-gray-600">Execution role</dt><dd className="mt-1 text-gray-300">{String(profile.document.execution_mode).replace(/_/g, ' ')}</dd></div></dl><div className="mt-3 flex flex-wrap gap-2"><button className="min-h-11 rounded-lg bg-gray-700 px-3 text-xs" onClick={() => void act(() => api.previewProfile(profile.profile_id!))}>Resolved preview</button><button className="min-h-11 rounded-lg bg-gray-700 px-3 text-xs" onClick={() => void edit(profile)}>{profile.built_in ? 'Duplicate' : 'Edit revision'}</button><button className="min-h-11 rounded-lg bg-gray-700 px-3 text-xs" onClick={() => void act(() => api.exportProfile(profile.profile_id!))}>Redacted export</button>{!profile.built_in && <button disabled={!access.status?.authenticated} className="min-h-11 rounded-lg border border-gray-600 px-3 text-xs disabled:opacity-40" onClick={() => void act(() => api.setProfileEnabled(profile.profile_id!, !profile.enabled), true)}>{profile.enabled ? 'Disable' : 'Enable'}</button>}</div></article>)}</div>
    {!visible.length && <div className="rounded-xl border border-dashed border-gray-700 p-8 text-center text-sm text-gray-500">No profiles match this filter.</div>}
    <section className="rounded-xl border border-gray-700/60 bg-gray-800/60"><button type="button" aria-expanded={advanced} onClick={() => setAdvanced(value => !value)} className="flex min-h-12 w-full items-center justify-between px-4 text-left"><span><span className="block text-sm font-semibold text-gray-200">Advanced import and validation</span><span className="mt-0.5 block text-xs text-gray-500">JSON artifacts and AI generation guidance</span></span><span className="text-gray-500">{advanced ? '−' : '+'}</span></button>{advanced && <div className="space-y-3 border-t border-gray-700/50 p-4"><textarea aria-label="Profile JSON" value={document} onChange={event => setDocument(event.target.value)} rows={10} className="w-full rounded-lg border border-gray-700 bg-gray-950 p-3 font-mono text-xs text-gray-200" placeholder="Paste a ProfileDefinition V1 JSON document"/><label className="flex min-h-11 items-center gap-3 text-xs text-gray-400"><input type="checkbox" checked={duplicate} onChange={event => setDuplicate(event.target.checked)} className="h-4 w-4 accent-emerald-500"/>Duplicate a built-in into a new custom ID</label><div className="flex flex-wrap gap-2"><button className="min-h-11 rounded-lg bg-gray-700 px-4 text-sm" onClick={() => void act(() => api.validateProfile(parsed()))}>Validate</button><button disabled={!access.status?.authenticated} className="min-h-11 rounded-lg bg-emerald-600 px-4 text-sm text-white disabled:opacity-40" onClick={() => void act(() => api.importProfile(parsed(), duplicate), true)}>Import revision</button><button className="min-h-11 rounded-lg px-4 text-sm text-emerald-300" onClick={() => void act(() => api.getProfileAiPrompt())}><span className="inline-flex items-center gap-2"><Sparkles size={14}/>AI generation prompt</span></button></div></div>}</section>
    {error && <p role="alert" className="rounded-lg border border-red-700/50 bg-red-950/30 p-3 text-sm text-red-300">{error}</p>}<Artifact value={result}/>
  </section>
}

function ProvidersSettings() {
  const access = useOperatorAccess()
  const [settings, setSettings] = useState<ProviderSettings | null>(null)
  const [document, setDocument] = useState('')
  const [advanced, setAdvanced] = useState(false)
  const [result, setResult] = useState<unknown>(null)
  const [error, setError] = useState('')
  const load = () => api.getProviderSettings().then(setSettings).catch(reason => setError(reason.message))
  useEffect(() => { void load() }, [])
  const act = async (operation: () => Promise<unknown>, needsOperator = false) => { setError(''); if (needsOperator && !access.status?.authenticated) { setError('Unlock operator changes before modifying provider settings.'); return } try { setResult(await operation()); await load() } catch (reason: any) { setError(reason.message || 'Provider operation failed') } }
  return (
    <section className="space-y-4" aria-labelledby="providers-settings-heading">
      <div>
        <h1 id="providers-settings-heading" className="text-xl font-semibold text-white">Provider Adapters</h1>
        <p className="mt-1 text-sm text-gray-400">Adapter code availability and provider CLI readiness are separate. Spawn Agent uses this same runtime preflight.</p>
      </div>
      <OperatorAccessCard access={access} compact />
      <div className="grid gap-3 lg:grid-cols-2">
        {settings?.configurations.map(config => {
          const adapter = settings.adapters.find(item => item.adapter_id === config.document.adapter_id)
          const runtime = config.runtime || adapter?.runtime
          const availability = runtime?.availability
          const runtimeColor = availability === 'INSTALLED_AND_READY'
            ? 'bg-emerald-500/10 text-emerald-300'
            : availability === 'INSTALLED_BUT_UNHEALTHY'
              ? 'bg-red-500/10 text-red-300'
              : availability === 'NOT_INSTALLED'
                ? 'bg-gray-900 text-gray-400'
                : 'bg-amber-500/10 text-amber-300'
          return (
            <article key={config.config_id} className="rounded-xl border border-gray-700/60 bg-gray-800/60 p-4">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <h2 className="font-mono text-sm text-emerald-300">{config.config_id}</h2>
                <div className="flex flex-wrap gap-1.5">
                  <span className="rounded-full bg-cyan-500/10 px-2 py-1 text-[11px] text-cyan-300">
                    {adapter?.source === 'built-in' ? 'Built-in adapter' : 'Installed adapter package'}
                  </span>
                  {runtime && <span className={`rounded-full px-2 py-1 text-[11px] ${runtimeColor}`}>{providerRuntimeLabel(runtime)}</span>}
                </div>
              </div>
              <p className="mt-2 text-xs leading-5 text-gray-400">{String(adapter?.description || config.display_name)}</p>
              <p className="mt-2 text-xs text-gray-500">Adapter {String(config.document.adapter_id)} · API {String(adapter?.plugin_api_version || settings.api_version)} · r{config.revision_number}</p>
              <div className="mt-3 flex gap-2">
                <button className="min-h-11 rounded-lg bg-gray-700 px-3 text-xs" onClick={() => void act(() => api.preflightProvider(config.config_id!))}>Refresh preflight</button>
                <button className="min-h-11 rounded-lg bg-gray-700 px-3 text-xs" onClick={() => void act(() => api.exportProvider(config.config_id!))}>Redacted export</button>
              </div>
            </article>
          )
        })}
      </div>
      {settings?.load_failures.length ? <Artifact value={settings.load_failures} label="Adapter load diagnostics" /> : null}
      <section className="rounded-xl border border-gray-700/60 bg-gray-800/60">
        <button type="button" aria-expanded={advanced} onClick={() => setAdvanced(value => !value)} className="flex min-h-12 w-full items-center justify-between px-4 text-left">
          <span><span className="block text-sm font-semibold text-gray-200">Advanced provider import</span><span className="text-xs text-gray-500">Validated declarative configuration only</span></span><span>{advanced ? '−' : '+'}</span>
        </button>
        {advanced && <div className="space-y-3 border-t border-gray-700/50 p-4"><textarea aria-label="Provider configuration JSON" value={document} onChange={event => setDocument(event.target.value)} rows={8} className="w-full rounded-lg border border-gray-700 bg-gray-950 p-3 font-mono text-xs"/><div className="flex flex-wrap gap-2"><button disabled={!access.status?.authenticated} className="min-h-11 rounded-lg bg-emerald-600 px-4 text-sm disabled:opacity-40" onClick={() => void act(() => api.importProvider(JSON.parse(document)), true)}>Import revision</button><button className="min-h-11 rounded-lg px-4 text-sm text-emerald-300" onClick={() => void act(() => api.getProviderAiPrompt())}>AI generation prompt</button></div></div>}
      </section>
      {error && <p role="alert" className="rounded-lg border border-red-700/50 bg-red-950/30 p-3 text-sm text-red-300">{error}</p>}
      <Artifact value={result} />
    </section>
  )
}

type PolicySpec = { key: string; label: string; description: string; fields: Array<{ key: string; label: string; unit: 'hours' | 'days' | 'count' }> }
const POLICY_SPECS: PolicySpec[] = [
  { key: 'logs', label: 'Logs', description: 'Compress older logs, then remove closed logs after their retention window.', fields: [{ key: 'compress_after_minutes', label: 'Compress logs after', unit: 'hours' }, { key: 'retain_minutes', label: 'Retain closed logs for', unit: 'days' }] },
  { key: 'attachments', label: 'Attachments', description: 'Active-terminal attachments remain protected.', fields: [{ key: 'retain_minutes', label: 'Retain attachments for', unit: 'days' }] },
  { key: 'ephemeral', label: 'Temporary artifacts', description: 'Only marker-owned, expired temporary artifacts are eligible.', fields: [] },
  { key: 'browser_cache', label: 'Browser cache', description: 'Preserve active browser processes and their profiles.', fields: [{ key: 'retain_minutes', label: 'Retain browser cache for', unit: 'days' }] },
  { key: 'package_cache', label: 'Package cache', description: 'Prune only configured caches when their package process is idle.', fields: [] },
  { key: 'releases', label: 'Releases', description: 'Reference-aware cleanup preserves active, rollback, and known-good releases.', fields: [{ key: 'retain_count', label: 'Keep recent known-good releases', unit: 'count' }, { key: 'retain_minutes', label: 'Retain unreferenced releases for', unit: 'days' }] },
  { key: 'backups', label: 'Backups', description: 'Protected inventory only. Housekeeping never deletes backups.', fields: [] },
]

const bytes = (value: unknown) => { const amount = Number(value || 0); if (amount < 1024) return `${amount} B`; if (amount < 1024 ** 2) return `${(amount / 1024).toFixed(1)} KiB`; if (amount < 1024 ** 3) return `${(amount / 1024 ** 2).toFixed(1)} MiB`; return `${(amount / 1024 ** 3).toFixed(2)} GiB` }
const unitValue = (minutes: number, unit: 'hours' | 'days' | 'count') => unit === 'hours' ? minutes / 60 : unit === 'days' ? minutes / 1440 : minutes
const backendValue = (value: number, unit: 'hours' | 'days' | 'count') => Math.max(1, Math.round(value * (unit === 'hours' ? 60 : unit === 'days' ? 1440 : 1)))
const humanInterval = (value: string) => { const match = /^(\d+)([mhd])$/.exec(value); if (!match) return value; const unit = match[2] === 'm' ? 'minute' : match[2] === 'h' ? 'hour' : 'day'; return `Every ${match[1]} ${unit}${match[1] === '1' ? '' : 's'}` }

function HousekeepingReport({ report, diskState }: { report: Record<string, any> | null; diskState: string }) {
  if (!report || report.status === 'never_run') return <div className="rounded-xl border border-dashed border-gray-700 p-6 text-center"><Clock3 className="mx-auto text-gray-600"/><p className="mt-2 text-sm text-gray-300">Housekeeping has not run yet.</p><p className="mt-1 text-xs text-gray-500">Build a plan first to inspect what can be reclaimed safely.</p></div>
  const skipped = Number(report.skipped_open || 0) + Number(report.skipped_unknown || 0)
  const started = report.started_at ? new Date(report.started_at).toLocaleString() : 'Not recorded by current backend'
  const completed = report.completed_at ? new Date(report.completed_at).toLocaleString() : 'Recorded by server'
  const duration = report.duration_seconds === undefined ? 'Not recorded by current backend' : `${Number(report.duration_seconds).toFixed(1)} seconds`
  const classes = [
    ['Logs compressed', report.logs_compressed], ['Logs removed', report.logs_deleted], ['Attachments', report.attachments_deleted], ['Temporary artifacts', report.ephemeral_resources_removed], ['Browser cache', report.browser_revisions_removed], ['Package caches', report.cache_pruned],
  ]
  return <div className="space-y-3"><div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"><Summary label="Result" value={report.ok === false ? 'Completed with issues' : 'Completed'} /><Summary label="Started" value={started} /><Summary label="Completed" value={completed} /><Summary label="Duration" value={duration} /><Summary label="Reclaimed total" value={bytes(report.freed_bytes)} /><Summary label="Resulting disk health" value={diskState} /></div><div className="grid gap-3 lg:grid-cols-2"><div className="rounded-xl border border-gray-700/60 bg-gray-800/60 p-4"><h3 className="text-sm font-semibold text-gray-200">Reclaimed by class</h3><dl className="mt-3 grid grid-cols-2 gap-2">{classes.map(([label, value]) => <div key={String(label)} className="rounded-lg bg-gray-900/50 p-2"><dt className="text-xs text-gray-500">{label}</dt><dd className="mt-1 text-sm text-gray-200">{Number(value || 0)}</dd></div>)}</dl></div><div className="rounded-xl border border-gray-700/60 bg-gray-800/60 p-4"><h3 className="text-sm font-semibold text-gray-200">Protection and errors</h3><p className="mt-3 text-sm text-gray-300">{skipped} protected or skipped item{skipped === 1 ? '' : 's'}</p>{report.execution_failures?.length ? <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-red-300">{report.execution_failures.map((item: any, index: number) => <li key={index}>{item.reason_code || 'Execution failure'}</li>)}</ul> : <p className="mt-2 text-xs text-gray-500">No execution failures recorded.</p>}{report.warnings?.length ? <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-amber-200">{report.warnings.map((warning: string) => <li key={warning}>{warning.replace(/_/g, ' ')}</li>)}</ul> : null}</div></div><Artifact value={report} label="Raw report"/></div>
}

function Summary({ label, value, detail }: { label: string; value: string; detail?: string }) { return <div className="rounded-xl border border-gray-700/60 bg-gray-800/60 p-4"><dt className="text-xs text-gray-500">{label}</dt><dd className="mt-1 text-base font-medium text-gray-100">{value}</dd>{detail && <dd className="mt-1 text-xs text-gray-500">{detail}</dd>}</div> }

function HousekeepingSettingsPage() {
  const access = useOperatorAccess()
  const [settings, setSettings] = useState<HousekeepingSettings | null>(null)
  const [capacity, setCapacity] = useState<OrchestrationCapacity | null>(null)
  const [mode, setMode] = useState<'frequent' | 'weekly' | 'pressure'>('frequent')
  const [plan, setPlan] = useState<Record<string, any> | null>(null)
  const [report, setReport] = useState<Record<string, any> | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const load = () => Promise.all([api.getHousekeepingSettings(), api.getHousekeepingReport(), api.getOrchestrationCapacity()]).then(([value, latest, resources]) => { setSettings(value); setReport(latest); setCapacity(resources) }).catch(reason => setError(reason.message))
  useEffect(() => { void load() }, [])
  const save = async () => { if (!settings || !access.status?.authenticated) return; setError(''); try { setSettings(await api.updateHousekeepingSettings(settings)) } catch (reason: any) { setError(reason.message || 'Could not save Housekeeping settings'); await access.refresh() } }
  const buildPlan = async () => { setError(''); try { setPlan(await api.getHousekeepingPlan(mode)) } catch (reason: any) { setError(reason.message || 'Could not build a Housekeeping plan') } }
  const run = async () => { if (!plan || !access.status?.authenticated) return; setRunning(true); setError(''); try { setReport(await api.runHousekeeping(plan.mode, false, plan.plan_id)); setPlan(null); await load() } catch (reason: any) { setError(reason.message || 'Housekeeping failed'); await access.refresh() } finally { setRunning(false) } }
  const updatePolicy = (category: string, key: string, value: boolean | number) => { if (!settings) return; setSettings({ ...settings, policy: { ...settings.policy, [category]: { ...settings.policy[category], [key]: value } } }) }
  if (!settings) return <p className="text-gray-500">Loading Housekeeping settings…</p>
  const used = capacity?.root_disk.used_percent ?? 0
  const diskState = used >= 92 ? 'RED · disk critical' : used >= 85 ? 'RED' : used >= 70 ? 'YELLOW' : 'GREEN'
  const actionable = plan?.candidates?.filter((candidate: any) => candidate.action !== 'preserve') || []
  const protectedCount = plan?.candidates?.length ? plan.candidates.length - actionable.length : 0
  const reclaimByClass = actionable.reduce((current: Record<string, number>, candidate: any) => ({ ...current, [candidate.category]: (current[candidate.category] || 0) + Number(candidate.estimated_reclaim_bytes || 0) }), {})
  const frequent = /^(\d+)([mhd])$/.exec(settings.schedule.frequent) || ['', '6', 'h']
  const weekly = /^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) (\d\d:\d\d) UTC$/.exec(settings.schedule.weekly) || ['', 'Sun', '04:00']
  return <section className="space-y-5" aria-labelledby="housekeeping-settings-heading"><div><h1 id="housekeeping-settings-heading" className="text-xl font-semibold text-white">Housekeeping</h1><p className="mt-1 text-sm text-gray-400">Understand disk pressure, inspect a safe cleanup plan, and execute only after ThreadCells revalidates every candidate.</p></div><div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"><Summary label="Disk health" value={diskState} detail={capacity ? `${used}% used · ${capacity.root_disk.free_gib} GiB free` : 'Resource status unavailable'} /><Summary label="Safely reclaimable" value={plan ? bytes(plan.reclaimable_bytes) : 'Build a plan'} detail={plan ? `${actionable.length} actionable candidates` : 'No estimate is guessed before planning'} /><Summary label="Current state" value={running ? 'Running' : 'Idle'} detail={running ? 'The validated operation is in progress' : 'No operation started from this page'} /><Summary label="Last run" value={report?.status === 'never_run' ? 'Never' : report ? (report.ok === false ? 'Completed with issues' : 'Completed') : 'Unavailable'} /><Summary label="Next run" value={humanInterval(settings.schedule.frequent)} detail={`${settings.schedule.weekly} · pressure recovery on RED`} /><Summary label="Backups" value="Protected" detail="Inventory only; never deleted" /></div><OperatorAccessCard access={access}/><section aria-labelledby="cleanup-policy-heading"><div className="mb-3"><h2 id="cleanup-policy-heading" className="text-base font-semibold text-gray-100">Cleanup policy</h2><p className="mt-1 text-xs text-gray-500">Human units are converted to the canonical minute/count representation when saved.</p></div><div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{POLICY_SPECS.map(spec => { const policy = settings.policy[spec.key]; const protectedClass = spec.key === 'backups'; return <fieldset key={spec.key} className="rounded-xl border border-gray-700/60 bg-gray-800/60 p-4"><legend className="sr-only">{spec.label}</legend><div className="flex items-start justify-between gap-3"><div><h3 className="font-medium text-gray-100">{spec.label}</h3><p className="mt-1 text-xs leading-5 text-gray-500">{spec.description}</p></div><label className={`relative inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center ${protectedClass ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'}`}><span className="sr-only">Enable {spec.label}</span><input type="checkbox" checked={Boolean(policy.enabled)} disabled={protectedClass} onChange={event => updatePolicy(spec.key, 'enabled', event.target.checked)} className="peer sr-only"/><span className="h-6 w-11 rounded-full bg-gray-700 transition peer-checked:bg-emerald-600 peer-focus-visible:ring-2 peer-focus-visible:ring-emerald-400 after:absolute after:ml-0.5 after:mt-0.5 after:h-5 after:w-5 after:rounded-full after:bg-white after:transition peer-checked:after:translate-x-5"/></label></div>{spec.fields.length > 0 && <div className="mt-4 space-y-3">{spec.fields.map(field => <label key={field.key} className="block text-xs text-gray-400">{field.label}<div className="mt-1 flex"><input aria-label={`${spec.label} ${field.label}`} type="number" min={1} max={field.unit === 'count' ? 100 : 365} step={field.unit === 'count' ? 1 : 0.5} value={unitValue(Number(policy[field.key]), field.unit)} onChange={event => updatePolicy(spec.key, field.key, backendValue(Number(event.target.value), field.unit))} className="min-h-11 min-w-0 flex-1 rounded-l-lg border border-gray-700 bg-gray-950 px-3 text-sm"/><span className="flex min-h-11 items-center rounded-r-lg border border-l-0 border-gray-700 bg-gray-900 px-3 text-xs text-gray-500">{field.unit}</span></div></label>)}</div>}{protectedClass && <span className="mt-3 inline-flex rounded-full bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-300">Protected · inventory only</span>}</fieldset>})}</div></section><section className="rounded-xl border border-gray-700/60 bg-gray-800/60 p-4" aria-labelledby="schedule-heading"><h2 id="schedule-heading" className="text-base font-semibold text-gray-100">Schedule</h2><p className="mt-1 text-xs text-gray-500">Frequent lightweight maintenance, one weekly deep pass, and automatic recovery when disk health is RED.</p><div className="mt-4 grid gap-4 md:grid-cols-3"><fieldset><legend className="text-sm font-medium text-gray-200">Frequent maintenance</legend><p className="mt-1 text-xs text-gray-500">Runs at the configured interval.</p><div className="mt-3 flex"><input aria-label="Frequent schedule value" type="number" min={1} value={frequent[1]} onChange={event => setSettings({ ...settings, schedule: { ...settings.schedule, frequent: `${event.target.value}${frequent[2]}` } })} className="min-h-11 min-w-0 flex-1 rounded-l-lg border border-gray-700 bg-gray-950 px-3"/><select aria-label="Frequent schedule unit" value={frequent[2]} onChange={event => setSettings({ ...settings, schedule: { ...settings.schedule, frequent: `${frequent[1]}${event.target.value}` } })} className="min-h-11 rounded-r-lg border border-l-0 border-gray-700 bg-gray-950 px-3"><option value="m">minutes</option><option value="h">hours</option><option value="d">days</option></select></div></fieldset><fieldset><legend className="text-sm font-medium text-gray-200">Weekly maintenance</legend><p className="mt-1 text-xs text-gray-500">A deeper pass in UTC.</p><div className="mt-3 flex"><select aria-label="Weekly schedule day" value={weekly[1]} onChange={event => setSettings({ ...settings, schedule: { ...settings.schedule, weekly: `${event.target.value} ${weekly[2]} UTC` } })} className="min-h-11 flex-1 rounded-l-lg border border-gray-700 bg-gray-950 px-2">{['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].map(day => <option key={day}>{day}</option>)}</select><input aria-label="Weekly schedule time" type="time" value={weekly[2]} onChange={event => setSettings({ ...settings, schedule: { ...settings.schedule, weekly: `${weekly[1]} ${event.target.value} UTC` } })} className="min-h-11 min-w-0 flex-1 rounded-r-lg border border-l-0 border-gray-700 bg-gray-950 px-2"/></div></fieldset><div><h3 className="text-sm font-medium text-gray-200">Disk pressure</h3><p className="mt-1 text-xs leading-5 text-gray-500">Recovery runs automatically on RED disk health and still counts against Heavy capacity without rejecting its own recovery.</p><span className="mt-3 inline-flex rounded-full bg-red-500/10 px-2 py-1 text-xs text-red-300">On RED</span></div></div><button disabled={!access.status?.authenticated} className="mt-4 min-h-11 rounded-lg bg-emerald-600 px-4 text-sm font-medium text-white disabled:opacity-40" onClick={() => void save()}>Save policy and schedule</button></section><section className="rounded-xl border border-gray-700/60 bg-gray-800/60 p-4" aria-labelledby="manual-operation-heading"><h2 id="manual-operation-heading" className="text-base font-semibold text-gray-100">Manual operation</h2><ol className="mt-2 flex flex-col gap-1 text-xs text-gray-500 sm:flex-row sm:items-center"><li className="text-gray-300">1. Build dry-run plan</li><li aria-hidden="true" className="hidden sm:block">→</li><li className={plan ? 'text-gray-300' : ''}>2. Inspect candidates</li><li aria-hidden="true" className="hidden sm:block">→</li><li className={plan ? 'text-gray-300' : ''}>3. Execute with revalidation</li></ol><div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-end"><label className="text-xs text-gray-400">Cleanup class<select value={mode} onChange={event => { setMode(event.target.value as typeof mode); setPlan(null) }} className="mt-1 block min-h-11 w-full rounded-lg border border-gray-700 bg-gray-950 px-3 sm:w-auto"><option value="frequent">Frequent</option><option value="weekly">Weekly</option><option value="pressure">Disk pressure</option></select></label><button className="min-h-11 rounded-lg bg-gray-700 px-4 text-sm" onClick={() => void buildPlan()}>Build dry-run plan</button><button disabled={!plan || !access.status?.authenticated || running} className="min-h-11 rounded-lg bg-amber-600 px-4 text-sm font-medium text-white disabled:opacity-40" onClick={() => void run()}>{running ? 'Running…' : 'Execute inspected plan safely'}</button></div>{plan && <div className="mt-4 rounded-xl border border-emerald-800/40 bg-emerald-950/10 p-4"><div className="grid gap-3 sm:grid-cols-3"><Summary label="Reclaimable" value={bytes(plan.reclaimable_bytes)}/><Summary label="Actionable" value={String(actionable.length)}/><Summary label="Protected / skipped" value={String(protectedCount)}/></div><div className="mt-3 flex flex-wrap gap-2">{Object.entries(reclaimByClass).map(([category, amount]) => <span key={category} className="rounded-full bg-gray-900 px-2.5 py-1 text-xs text-gray-300">{POLICY_SPECS.find(spec => spec.key === category)?.label || category}: {bytes(amount)}</span>)}</div>{plan.warnings?.length ? <ul className="mt-3 list-disc pl-5 text-xs text-amber-200">{plan.warnings.map((warning: string) => <li key={warning}>{warning.replace(/_/g, ' ')}</li>)}</ul> : null}<details className="mt-3"><summary className="min-h-9 cursor-pointer text-xs text-emerald-300">Inspect candidate reasons</summary><div className="mt-2 max-h-72 overflow-auto rounded-lg border border-gray-700"><table className="w-full text-left text-xs"><thead className="sticky top-0 bg-gray-900 text-gray-500"><tr><th className="p-2">Class</th><th className="p-2">Candidate identity</th><th className="p-2">Action</th><th className="p-2">Size</th><th className="p-2">Reason</th></tr></thead><tbody>{plan.candidates.map((candidate: any) => <tr key={candidate.canonical_identity} className="border-t border-gray-800"><td className="p-2 text-gray-300">{POLICY_SPECS.find(spec => spec.key === candidate.category)?.label || candidate.category}</td><td className="break-all p-2 font-mono text-gray-400">{candidate.canonical_identity}</td><td className="p-2 text-gray-300">{candidate.action}</td><td className="p-2 text-gray-400">{bytes(candidate.estimated_reclaim_bytes)}</td><td className="p-2 text-gray-500">{String(candidate.protection_reason || candidate.retention_reason).replace(/_/g, ' ')}</td></tr>)}</tbody></table></div></details><p className="mt-3 text-xs text-gray-500">Execution rebuilds the plan under the canonical lock and revalidates identity, fingerprint, references, and protection for every candidate.</p></div>}</section><section aria-labelledby="latest-report-heading"><h2 id="latest-report-heading" className="mb-3 text-base font-semibold text-gray-100">Latest report</h2><HousekeepingReport report={report} diskState={diskState}/></section>{error && <p role="alert" className="rounded-lg border border-red-700/50 bg-red-950/30 p-3 text-sm text-red-300">{error}</p>}</section>
}

function AboutSettings() {
  const principles = [
    ['Operational truth', 'Real lifecycle, capacity, and completion state take precedence over optimistic status.', <ShieldCheck size={18}/>],
    ['Native agents', 'Provider CLIs remain native and inspectable instead of being hidden behind a synthetic runtime.', <Boxes size={18}/>],
    ['Controlled parallelism', 'Independent Provider, Work, and Heavy limits protect the host while useful work continues.', <HardDrive size={18}/>],
    ['Durable results', 'Results, launch snapshots, owner gates, and history survive process restarts.', <Database size={18}/>],
    ['Owner authority', 'Exceptional launches, production activation, and publication remain explicit owner decisions.', <HeartHandshake size={18}/>],
    ['Self-hosted control', 'ThreadCells is designed for one trusted Linux host and a loopback-first access boundary.', <CheckCircle2 size={18}/>],
  ] as const
  return <article className="space-y-5" aria-labelledby="about-heading"><header className="overflow-hidden rounded-2xl border border-emerald-800/40 bg-gradient-to-br from-emerald-950/40 via-gray-900 to-gray-900 p-5 sm:p-7"><div className="flex flex-col gap-5 sm:flex-row sm:items-center"><img src="/threadcells-symbol.png" alt="ThreadCells symbol" className="h-20 w-20 rounded-2xl object-cover shadow-lg"/><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h1 id="about-heading" className="text-2xl font-semibold text-white">ThreadCells</h1><span className="rounded-full bg-amber-500/10 px-2 py-1 text-xs text-amber-200">Technical preview</span></div><p className="mt-2 max-w-2xl text-sm leading-6 text-gray-300">A self-hosted coding-agent operations console for coordinating native CLI agents while keeping machine control, resource limits, durable results, and owner decisions visible.</p><dl className="mt-4 flex flex-col gap-2 text-xs text-gray-400 sm:flex-row sm:gap-6"><div><dt className="inline text-gray-600">Version </dt><dd className="inline font-mono text-gray-300">{BUILD_IDENTITY.version}</dd></div><div className="min-w-0"><dt className="inline text-gray-600">Source </dt><dd className="inline break-all font-mono text-gray-300">{BUILD_IDENTITY.revision}</dd></div></dl></div></div></header><section className="grid gap-4 lg:grid-cols-2"><div className="rounded-xl border border-gray-700/60 bg-gray-800/60 p-5"><h2 className="text-base font-semibold text-gray-100">What ThreadCells is</h2><p className="mt-2 text-sm leading-6 text-gray-400">ThreadCells brings sessions, terminals, managed worktrees, provider execution, profiles, capacity, workflows, and operational evidence into one local control plane for advanced solo developers and trusted small teams.</p></div><div className="rounded-xl border border-gray-700/60 bg-gray-800/60 p-5"><h2 className="text-base font-semibold text-gray-100">Why it exists</h2><p className="mt-2 text-sm leading-6 text-gray-400">It grew from the practical need to coordinate multiple native CLI coding agents while retaining operational truth, machine control, safe worktrees, resource limits, and durable results.</p></div></section><section aria-labelledby="principles-heading"><h2 id="principles-heading" className="text-base font-semibold text-gray-100">Principles</h2><div className="mt-3 grid gap-3 md:grid-cols-2 lg:grid-cols-3">{principles.map(([title, copy, icon]) => <div key={title} className="rounded-xl border border-gray-700/60 bg-gray-800/60 p-4"><div className="text-emerald-300">{icon}</div><h3 className="mt-3 text-sm font-medium text-gray-100">{title}</h3><p className="mt-1 text-xs leading-5 text-gray-500">{copy}</p></div>)}</div></section><section className="grid gap-4 lg:grid-cols-2"><div className="rounded-xl border border-gray-700/60 bg-gray-800/60 p-5"><h2 className="text-base font-semibold text-gray-100">Created and maintained by</h2><p className="mt-2 text-sm text-gray-300">Subaev Ruslan</p><p className="mt-1 text-xs leading-5 text-gray-500">Created with contributions from the ThreadCells community.</p></div><div className="rounded-xl border border-gray-700/60 bg-gray-800/60 p-5"><h2 className="text-base font-semibold text-gray-100">Open source</h2><p className="mt-2 text-sm text-gray-300">Licensed under Apache-2.0.</p><div className="mt-3 flex flex-wrap gap-3"><a href="/docs" className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-emerald-600 px-4 text-sm text-white"><BookOpen size={15}/>Documentation</a><a href="/docs/provenance" className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-gray-700 px-4 text-sm text-gray-300"><Info size={15}/>Provenance</a></div></div></section><footer className="rounded-xl border border-gray-800 bg-gray-900/40 p-4 text-xs leading-5 text-gray-600">ThreadCells is an independent, unofficial downstream of AWS Labs CLI Agent Orchestrator and is not sponsored or endorsed by Amazon Web Services. Required attribution and changes from upstream are documented in the packaged provenance materials.</footer></article>
}

export function ControlPlaneSettings({ section, navigate }: { section: SettingsSection; navigate: (section: SettingsSection) => void }) {
  return <div className="min-w-0 space-y-5"><nav aria-label="Settings sections" className="grid grid-cols-2 gap-2 rounded-xl border border-gray-700/50 bg-gray-800/60 p-2 sm:grid-cols-3 lg:grid-cols-6">{SECTIONS.map(item => <a key={item.key} href={item.path} aria-current={section === item.key ? 'page' : undefined} onClick={event => { if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return; event.preventDefault(); navigate(item.key) }} className={`flex min-h-11 min-w-0 items-center justify-center rounded-lg px-3 text-sm ${section === item.key ? 'bg-emerald-600 text-white' : 'text-gray-400 hover:bg-gray-700'}`}>{item.label}</a>)}</nav>{section === 'profiles' ? <ProfilesSettings/> : section === 'providers' ? <ProvidersSettings/> : section === 'housekeeping' ? <HousekeepingSettingsPage/> : section === 'telegram' ? <TelegramSettings/> : section === 'about' ? <AboutSettings/> : <SettingsPanel/>}</div>
}
