import { useEffect, useMemo, useRef, useState } from 'react'
import { BookOpen, Boxes, CheckCircle2, Clock3, Database, HardDrive, HeartHandshake, Info, Loader2, Search, ShieldCheck, Sparkles, Trash2 } from 'lucide-react'
import { api, CaoApiError, FullCleanupPlan, HousekeepingPlan, HousekeepingSettings, OrchestrationCapacity, ProviderSettings, RegistryRecord } from '../api'
import { BUILD_IDENTITY } from '../buildIdentity'
import { providerRuntimeLabel } from '../providerAvailability'
import { OperatorAccessCard, useOperatorAccess } from './OperatorAccess'
import { ConfirmModal } from './ConfirmModal'
import { SettingsPanel } from './SettingsPanel'
import { TelegramSettings } from './TelegramSettings'
import { useI18n, type TranslationKey } from '../i18n'
import { resourceStateTranslationKey } from './StatusBadge'

export type SettingsSection = 'general' | 'profiles' | 'providers' | 'housekeeping' | 'telegram' | 'about'

const SECTIONS: Array<{ key: SettingsSection; labelKey: TranslationKey; path: string }> = [
  { key: 'general', labelKey: 'settings.section.general', path: '/settings' },
  { key: 'profiles', labelKey: 'settings.section.profiles', path: '/settings/profiles' },
  { key: 'providers', labelKey: 'settings.section.providers', path: '/settings/providers' },
  { key: 'housekeeping', labelKey: 'settings.section.housekeeping', path: '/settings/housekeeping' },
  { key: 'telegram', labelKey: 'settings.section.telegram', path: '/settings/telegram' },
  { key: 'about', labelKey: 'settings.section.about', path: '/settings/about' },
]

function Artifact({ value, label }: { value: unknown; label?: string }) {
  const { t } = useI18n()
  if (!value) return null
  return <details className="rounded-xl border border-gray-700/60 bg-gray-900/50 p-3">
<summary className="min-h-9 cursor-pointer text-xs font-medium text-gray-400">{label || t('settings.technicalDetails')}</summary>
<pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap break-all text-xs text-gray-300">{JSON.stringify(value, null, 2)}</pre>
</details>
}

function ProfilesSettings() {
  const { t } = useI18n()
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
    if (needsOperator && !access.status?.authenticated) { setError(t('registry.unlock')); return }
    try { setResult(await operation()); await load() } catch (reason: any) { setError(reason.message || t('registry.operationFailed')) }
  }
  const edit = async (profile: RegistryRecord) => {
    try {
      const exported = await api.exportProfile(profile.profile_id!)
      setDocument(JSON.stringify(exported.document, null, 2))
      setDuplicate(profile.built_in)
      setAdvanced(true)
    } catch (reason: any) { setError(reason.message || t('registry.loadDocumentFailed')) }
  }
  return <section className="space-y-4" aria-labelledby="profiles-settings-heading">
    <div>
<h1 id="profiles-settings-heading" className="text-xl font-semibold text-white">{t('registry.title')}</h1>
<p className="mt-1 text-sm text-gray-400">{t('registry.help')}</p>
</div>
    <OperatorAccessCard access={access} compact />
    <div className="flex flex-col gap-2 rounded-xl border border-gray-700/60 bg-gray-800/60 p-3 sm:flex-row sm:items-center">
      <label className="relative min-w-0 flex-1">
<span className="sr-only">{t('registry.search')}</span>
<Search size={15} aria-hidden="true" className="absolute left-3 top-3.5 text-gray-400"/>
<input aria-label={t('registry.search')} value={search} onChange={event => setSearch(event.target.value)} placeholder={t('registry.searchPlaceholder')} className="min-h-11 w-full rounded-lg border border-gray-700 bg-gray-950 pl-9 pr-3 text-sm text-gray-100 focus:border-emerald-500 focus:outline-none"/>
</label>
      <select aria-label={t('registry.type')} value={kind} onChange={event => setKind(event.target.value as typeof kind)} className="min-h-11 rounded-lg border border-gray-700 bg-gray-950 px-3 text-sm">
<option value="all">{t('registry.all')}</option>
<option value="built-in">{t('registry.builtIn')}</option>
<option value="custom">{t('registry.custom')}</option>
</select>
      <span className="px-2 text-xs text-gray-400">{t('registry.count', { visible: visible.length, total: profiles.length })}</span>
    </div>
    <div className="grid gap-3 lg:grid-cols-2">{visible.map(profile => <article key={profile.profile_id} className="rounded-xl border border-gray-700/60 bg-gray-800/60 p-4">
<div className="flex items-start justify-between gap-3">
<div className="min-w-0">
<h2 className="truncate font-mono text-sm text-emerald-300">{profile.profile_id}</h2>
<p className="mt-1 text-xs leading-5 text-gray-400">{profile.description}</p>
</div>
<span className="shrink-0 rounded-full bg-gray-900 px-2 py-1 text-[11px] text-gray-400">{t(profile.built_in ? 'registry.builtIn' : 'registry.custom')} · r{profile.revision_number}</span>
</div>
<dl className="mt-3 grid grid-cols-2 gap-2 rounded-lg bg-gray-900/50 p-3 text-xs">
<div>
<dt className="text-gray-400">{t('common.provider')}</dt>
<dd className="mt-1 text-gray-300">{String(profile.document.provider_config_id || '—')}</dd>
</div>
<div>
<dt className="text-gray-400">{t('common.model')}</dt>
<dd className="mt-1 break-all text-gray-300">{String(profile.document.model || t('registry.providerDefault'))}</dd>
</div>
<div>
<dt className="text-gray-400">{t('common.reasoning')}</dt>
<dd className="mt-1 capitalize text-gray-300">{String(profile.document.reasoning_level || t('common.default'))}</dd>
</div>
<div>
<dt className="text-gray-400">{t('registry.executionRole')}</dt>
<dd className="mt-1 text-gray-300">{String(profile.document.execution_mode).replace(/_/g, ' ')}</dd>
</div>
</dl>
<div className="mt-3 flex flex-wrap gap-2">
<button className="min-h-11 rounded-lg bg-gray-700 px-3 text-xs" onClick={() => void act(() => api.previewProfile(profile.profile_id!))}>{t('registry.preview')}</button>
<button className="min-h-11 rounded-lg bg-gray-700 px-3 text-xs" onClick={() => void edit(profile)}>{t(profile.built_in ? 'registry.duplicate' : 'registry.editRevision')}</button>
<button className="min-h-11 rounded-lg bg-gray-700 px-3 text-xs" onClick={() => void act(() => api.exportProfile(profile.profile_id!))}>{t('registry.redactedExport')}</button>{!profile.built_in && <button disabled={!access.status?.authenticated} className="min-h-11 rounded-lg border border-gray-600 px-3 text-xs disabled:opacity-40" onClick={() => void act(() => api.setProfileEnabled(profile.profile_id!, !profile.enabled), true)}>{t(profile.enabled ? 'registry.disable' : 'common.enable')}</button>}</div>
</article>)}</div>
    {!visible.length && <div className="rounded-xl border border-dashed border-gray-700 p-8 text-center text-sm text-gray-400">{t('registry.noMatches')}</div>}
    <section className="rounded-xl border border-gray-700/60 bg-gray-800/60">
<button type="button" aria-expanded={advanced} onClick={() => setAdvanced(value => !value)} className="flex min-h-12 w-full items-center justify-between px-4 text-left">
<span>
<span className="block text-sm font-semibold text-gray-200">{t('registry.advanced')}</span>
<span className="mt-0.5 block text-xs text-gray-400">{t('registry.advancedHelp')}</span>
</span>
<span className="text-gray-400">{advanced ? '−' : '+'}</span>
</button>{advanced && <div className="space-y-3 border-t border-gray-700/50 p-4">
<textarea aria-label={t('registry.profileJson')} value={document} onChange={event => setDocument(event.target.value)} rows={10} className="w-full rounded-lg border border-gray-700 bg-gray-950 p-3 font-mono text-xs text-gray-200" placeholder={t('registry.jsonPlaceholder')}/>
<label className="flex min-h-11 items-center gap-3 text-xs text-gray-400">
<input type="checkbox" checked={duplicate} onChange={event => setDuplicate(event.target.checked)} className="h-4 w-4 accent-emerald-500"/>{t('registry.duplicateBuiltIn')}</label>
<div className="flex flex-wrap gap-2">
<button className="min-h-11 rounded-lg bg-gray-700 px-4 text-sm" onClick={() => void act(() => api.validateProfile(parsed()))}>{t('registry.validate')}</button>
<button disabled={!access.status?.authenticated} className="min-h-11 rounded-lg bg-emerald-600 px-4 text-sm text-white disabled:opacity-40" onClick={() => void act(() => api.importProfile(parsed(), duplicate), true)}>{t('registry.importRevision')}</button>
<button className="min-h-11 rounded-lg px-4 text-sm text-emerald-300" onClick={() => void act(() => api.getProfileAiPrompt())}>
<span className="inline-flex items-center gap-2">
<Sparkles size={14}/>{t('registry.aiPrompt')}</span>
</button>
</div>
</div>}</section>
    {error && <p role="alert" className="rounded-lg border border-red-700/50 bg-red-950/30 p-3 text-sm text-red-300">{error}</p>}<Artifact value={result}/>
  </section>
}

function ProvidersSettings() {
  const { t } = useI18n()
  const access = useOperatorAccess()
  const [settings, setSettings] = useState<ProviderSettings | null>(null)
  const [document, setDocument] = useState('')
  const [advanced, setAdvanced] = useState(false)
  const [result, setResult] = useState<unknown>(null)
  const [error, setError] = useState('')
  const load = () => api.getProviderSettings().then(setSettings).catch(reason => setError(reason.message))
  useEffect(() => { void load() }, [])
  const act = async (operation: () => Promise<unknown>, needsOperator = false) => { setError(''); if (needsOperator && !access.status?.authenticated) { setError(t('providers.unlock')); return } try { setResult(await operation()); await load() } catch (reason: any) { setError(reason.message || t('providers.operationFailed')) } }
  return (
    <section className="space-y-4" aria-labelledby="providers-settings-heading">
      <div>
        <h1 id="providers-settings-heading" className="text-xl font-semibold text-white">{t('providers.title')}</h1>
        <p className="mt-1 text-sm text-gray-400">{t('providers.help')}</p>
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
                    {t(adapter?.source === 'built-in' ? 'providers.builtInAdapter' : 'providers.installedAdapter')}
                  </span>
                  {runtime && <span className={`rounded-full px-2 py-1 text-[11px] ${runtimeColor}`}>{providerRuntimeLabel(runtime, t)}</span>}
                </div>
              </div>
              <p className="mt-2 text-xs leading-5 text-gray-400">{String(adapter?.description || config.display_name)}</p>
              <p className="mt-2 text-xs text-gray-400">{t('providers.adapter')} {String(config.document.adapter_id)} · API {String(adapter?.plugin_api_version || settings.api_version)} · r{config.revision_number}</p>
              <div className="mt-3 flex gap-2">
                <button className="min-h-11 rounded-lg bg-gray-700 px-3 text-xs" onClick={() => void act(() => api.preflightProvider(config.config_id!))}>{t('providers.refreshPreflight')}</button>
                <button className="min-h-11 rounded-lg bg-gray-700 px-3 text-xs" onClick={() => void act(() => api.exportProvider(config.config_id!))}>{t('registry.redactedExport')}</button>
              </div>
            </article>
          )
        })}
      </div>
      {settings?.load_failures.length ? <Artifact value={settings.load_failures} label={t('providers.loadDiagnostics')} /> : null}
      <section className="rounded-xl border border-gray-700/60 bg-gray-800/60">
        <button type="button" aria-expanded={advanced} onClick={() => setAdvanced(value => !value)} className="flex min-h-12 w-full items-center justify-between px-4 text-left">
          <span>
<span className="block text-sm font-semibold text-gray-200">{t('providers.advanced')}</span>
<span className="text-xs text-gray-400">{t('providers.advancedHelp')}</span>
</span>
<span>{advanced ? '−' : '+'}</span>
        </button>
        {advanced && <div className="space-y-3 border-t border-gray-700/50 p-4">
<textarea aria-label={t('providers.configurationJson')} value={document} onChange={event => setDocument(event.target.value)} rows={8} className="w-full rounded-lg border border-gray-700 bg-gray-950 p-3 font-mono text-xs"/>
<div className="flex flex-wrap gap-2">
<button disabled={!access.status?.authenticated} className="min-h-11 rounded-lg bg-emerald-600 px-4 text-sm disabled:opacity-40" onClick={() => void act(() => api.importProvider(JSON.parse(document)), true)}>{t('registry.importRevision')}</button>
<button className="min-h-11 rounded-lg px-4 text-sm text-emerald-300" onClick={() => void act(() => api.getProviderAiPrompt())}>{t('registry.aiPrompt')}</button>
</div>
</div>}
      </section>
      {error && <p role="alert" className="rounded-lg border border-red-700/50 bg-red-950/30 p-3 text-sm text-red-300">{error}</p>}
      <Artifact value={result} />
    </section>
  )
}

type PolicySpec = { key: string; labelKey: TranslationKey; descriptionKey: TranslationKey; fields: Array<{ key: string; labelKey: TranslationKey; unit: 'hours' | 'days' | 'count' }> }
const POLICY_SPECS: PolicySpec[] = [
  { key: 'logs', labelKey: 'housekeeping.class.logs', descriptionKey: 'housekeeping.class.logsHelp', fields: [{ key: 'compress_after_minutes', labelKey: 'housekeeping.field.compressLogs', unit: 'hours' }, { key: 'retain_minutes', labelKey: 'housekeeping.field.retainLogs', unit: 'days' }] },
  { key: 'attachments', labelKey: 'housekeeping.class.attachments', descriptionKey: 'housekeeping.class.attachmentsHelp', fields: [{ key: 'retain_minutes', labelKey: 'housekeeping.field.retainAttachments', unit: 'days' }] },
  { key: 'ephemeral', labelKey: 'housekeeping.class.ephemeral', descriptionKey: 'housekeeping.class.ephemeralHelp', fields: [] },
  { key: 'browser_cache', labelKey: 'housekeeping.class.browser', descriptionKey: 'housekeeping.class.browserHelp', fields: [{ key: 'retain_minutes', labelKey: 'housekeeping.field.retainBrowser', unit: 'days' }] },
  { key: 'package_cache', labelKey: 'housekeeping.class.package', descriptionKey: 'housekeeping.class.packageHelp', fields: [] },
  { key: 'releases', labelKey: 'housekeeping.class.releases', descriptionKey: 'housekeeping.class.releasesHelp', fields: [{ key: 'retain_count', labelKey: 'housekeeping.field.keepReleases', unit: 'count' }, { key: 'retain_minutes', labelKey: 'housekeeping.field.retainReleases', unit: 'days' }] },
  { key: 'backups', labelKey: 'housekeeping.class.backups', descriptionKey: 'housekeeping.class.backupsHelp', fields: [] },
]

const bytes = (value: unknown) => { const amount = Number(value || 0); if (amount < 1024) return `${amount} B`; if (amount < 1024 ** 2) return `${(amount / 1024).toFixed(1)} KiB`; if (amount < 1024 ** 3) return `${(amount / 1024 ** 2).toFixed(1)} MiB`; return `${(amount / 1024 ** 3).toFixed(2)} GiB` }
const unitValue = (minutes: number, unit: 'hours' | 'days' | 'count') => unit === 'hours' ? minutes / 60 : unit === 'days' ? minutes / 1440 : minutes
const backendValue = (value: number, unit: 'hours' | 'days' | 'count') => Math.max(1, Math.round(value * (unit === 'hours' ? 60 : unit === 'days' ? 1440 : 1)))
const humanInterval = (value: string, locale: string) => { const match = /^(\d+)([mhd])$/.exec(value); if (!match) return value; const units = locale === 'ru' ? { m: 'мин', h: 'ч', d: 'дн' } : { m: 'min', h: 'hr', d: 'day' }; return locale === 'ru' ? `Каждые ${match[1]} ${units[match[2] as keyof typeof units]}` : `Every ${match[1]} ${units[match[2] as keyof typeof units]}` }
const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] as const
type Weekday = (typeof WEEKDAYS)[number]
const weekdayKey = (day: Weekday) => `housekeeping.weekday.${day}` as TranslationKey

function housekeepingWarning(warning: string, t: (key: TranslationKey) => string): { message: string; diagnosticId?: string } {
  const [reason, ...detail] = warning.split(':')
  const diagnosticId = detail.join(':') || undefined
  const retirementCopy: Record<string, TranslationKey> = {
    retirement_cleanup_claim_unknown: 'housekeeping.warning.claimUnknown',
    retirement_cleanup_identity_unproven: 'housekeeping.warning.identityUnproven',
    retirement_cleanup_intent_invalid: 'housekeeping.warning.intentInvalid',
    retirement_cleanup_inventory_uncertain: 'housekeeping.warning.inventoryUncertain',
    retirement_cleanup_pending_inventory_uncertain: 'housekeeping.warning.pendingInventoryUncertain',
    retirement_cleanup_unconfirmed: 'housekeeping.warning.unconfirmed',
    retirement_cleanup_finalization_raced: 'housekeeping.warning.finalizationRaced',
  }
  return {
    message: retirementCopy[reason] ? t(retirementCopy[reason]) : reason,
    diagnosticId,
  }
}

function HousekeepingWarnings({ warnings, className = '' }: { warnings: string[]; className?: string }) {
  const { t } = useI18n()
  if (!warnings.length) return null
  return <ul className={`list-disc space-y-1 pl-5 text-xs text-amber-200 ${className}`}>{warnings.map(warning => { const display = housekeepingWarning(warning, t); return <li key={warning}>
<span>{display.message}</span>{display.diagnosticId && <span className="ml-1 text-amber-300/80">{t('housekeeping.diagnosticId')} <code className="break-all">{display.diagnosticId}</code>
</span>}</li> })}</ul>
}

function HousekeepingReport({ report, diskState }: { report: Record<string, any> | null; diskState: string }) {
  const { t, tp, locale } = useI18n()
  if (!report || report.status === 'never_run') return <div className="rounded-xl border border-dashed border-gray-700 p-6 text-center">
<Clock3 className="mx-auto text-gray-400"/>
<p className="mt-2 text-sm text-gray-300">{t('housekeeping.noReport')}</p>
<p className="mt-1 text-xs text-gray-400">{t('housekeeping.noReportHelp')}</p>
</div>
  const protectedResources = Array.isArray(report.protected_resources) ? report.protected_resources : []
  const executionSkips = Array.isArray(report.execution_skips) ? report.execution_skips : []
  const skipped = protectedResources.length + executionSkips.length
  const started = report.started_at ? new Date(report.started_at).toLocaleString(locale) : t('housekeeping.notRecorded')
  const completed = report.completed_at ? new Date(report.completed_at).toLocaleString(locale) : t('housekeeping.recordedServer')
  const duration = report.duration_seconds === undefined ? t('housekeeping.notRecorded') : t('housekeeping.seconds', { count: Number(report.duration_seconds).toFixed(1) })
  const classes = Object.entries(report.reclaimed_bytes_by_class || {})
  const completedWithIssues = report.ok === false || report.completed_with_issues === true
  const fullCounts = Number(report.cache_pruned || 0) + Number(report.reproducible_caches_removed || 0) + Number(report.browser_revisions_removed || 0) + Number(report.ephemeral_resources_removed || 0) + Number(report.build_artifacts_removed || 0)
  return <div className="space-y-3">
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      <Summary label={t('housekeeping.result')} value={t(completedWithIssues ? 'housekeeping.completedIssues' : 'status.workflow.completed')} />
      <Summary label={t('housekeeping.started')} value={started} />
      <Summary label={t('housekeeping.completedAt')} value={completed} />
      <Summary label={t('housekeeping.duration')} value={duration} />
      <Summary label={t('housekeeping.reclaimedTotal')} value={bytes(report.freed_bytes)} detail={report.observed_disk_free_delta === undefined ? undefined : t('housekeeping.observedDelta', { size: bytes(report.observed_disk_free_delta) })} />
      <Summary label={t('housekeeping.resultingHealth')} value={t(resourceStateTranslationKey(diskState))} />
    </div>
    {report.full_cleanup && <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Summary label={t('housekeeping.plannedReclaim')} value={bytes(report.reclaimable_bytes)} detail={tp('resources', Number(report.planned_candidates || 0))} />
      <Summary label={t('housekeeping.diskBeforeAfter')} value={`${bytes(report.disk_before)} / ${bytes(report.disk_after)}`} />
      <Summary label={t('housekeeping.releasesRemoved')} value={String(Number(report.releases_removed || 0))} detail={t('housekeeping.rollback', { state: t(report.rollback_available === false ? 'housekeeping.rollback.none' : report.rollback_available === true ? 'housekeeping.rollback.available' : 'housekeeping.rollback.unproven') })} />
      <Summary label={t('housekeeping.activeRelease')} value={report.active_release || t('housekeeping.authorityUnproven')} />
      <Summary label={t('housekeeping.worktreesRetired')} value={String(Number(report.worktrees_retired || 0))} />
      <Summary label={t('housekeeping.cachesRemoved')} value={String(fullCounts)} />
      <Summary label={t('housekeeping.logsRemoved')} value={String(Number(report.logs_deleted || 0))} />
      <Summary label={t('housekeeping.protectedSkipped')} value={String(skipped)} />
    </div>}
    <div className="grid gap-3 lg:grid-cols-2">
      <div className="rounded-xl border border-gray-700/60 bg-gray-800/60 p-4">
<h3 className="text-sm font-semibold text-gray-200">{t('housekeeping.reclaimedByClass')}</h3>{classes.length ? <dl className="mt-3 grid grid-cols-2 gap-2">{classes.map(([label, value]) => <div key={label} className="rounded-lg bg-gray-900/50 p-2">
<dt className="text-xs text-gray-400">{label}</dt>
<dd className="mt-1 text-sm text-gray-200">{bytes(Number(value || 0))}</dd>
</div>)}</dl> : <p className="mt-3 text-xs text-gray-400">{t('housekeeping.noClassReclaim')}</p>}</div>
      <div className="rounded-xl border border-gray-700/60 bg-gray-800/60 p-4">
<h3 className="text-sm font-semibold text-gray-200">{t('housekeeping.protectionErrors')}</h3>
<p className="mt-3 text-sm text-gray-300">{tp('housekeeping.protectedItems', skipped)}</p>{protectedResources.length ? <ul className="mt-2 max-h-40 list-disc space-y-1 overflow-auto pl-5 text-xs text-amber-200">{protectedResources.map((item: any, index: number) => <li key={`protected-${index}`}>{item.canonical_identity ? `${item.canonical_identity}: ` : ''}{item.reason || t('housekeeping.protected')}{item.category ? ` · ${String(item.category)}` : ''}{item.bytes === undefined ? '' : ` · ${bytes(Number(item.bytes || 0))}`}</li>)}</ul> : null}{executionSkips.length ? <ul className="mt-2 max-h-40 list-disc space-y-1 overflow-auto pl-5 text-xs text-amber-200">{executionSkips.map((item: any, index: number) => <li key={`skip-${index}`}>{item.candidate ? `${item.candidate}: ` : ''}{item.reason_code || 'EXECUTION_SKIPPED'}</li>)}</ul> : null}{report.execution_failures?.length ? <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-red-300">{report.execution_failures.map((item: any, index: number) => <li key={`failure-${index}`}>{item.reason_code || 'EXECUTION_FAILURE'}</li>)}</ul> : <p className="mt-2 text-xs text-gray-400">{t('housekeeping.noFailures')}</p>}<HousekeepingWarnings warnings={report.warnings || []} className="mt-2" />
</div>
    </div>
    <Artifact value={report} label={t('housekeeping.rawReport')}/>
  </div>
}

function Summary({ label, value, detail }: { label: string; value: string; detail?: string }) { return <div className="rounded-xl border border-gray-700/60 bg-gray-800/60 p-4">
<dt className="text-xs text-gray-400">{label}</dt>
<dd className="mt-1 text-base font-medium text-gray-100">{value}</dd>{detail && <dd className="mt-1 text-xs text-gray-400">{detail}</dd>}</div> }

function planningError(reason: unknown, fallback: string) {
  return reason instanceof CaoApiError ? `${reason.title}. ${reason.description}` : fallback
}

function HousekeepingSettingsPage() {
  const { t, tp, locale } = useI18n()
  const access = useOperatorAccess()
  const [settings, setSettings] = useState<HousekeepingSettings | null>(null)
  const [capacity, setCapacity] = useState<OrchestrationCapacity | null>(null)
  const [mode, setMode] = useState<'frequent' | 'weekly' | 'pressure'>('frequent')
  const [plan, setPlan] = useState<HousekeepingPlan | null>(null)
  const [report, setReport] = useState<Record<string, any> | null>(null)
  const [running, setRunning] = useState(false)
  const [executionBlock, setExecutionBlock] = useState<'changed' | 'busy' | null>(null)
  const [fullPlan, setFullPlan] = useState<FullCleanupPlan | null>(null)
  const [fullReport, setFullReport] = useState<Record<string, any> | null>(null)
  const [fullRunning, setFullRunning] = useState(false)
  const [fullConfirm, setFullConfirm] = useState(false)
  const [fullError, setFullError] = useState('')
  const [retireDirtyWorktrees, setRetireDirtyWorktrees] = useState(false)
  const [error, setError] = useState('')
  const [planning, setPlanning] = useState<'normal' | 'full' | null>(null)
  const planningRef = useRef<'normal' | 'full' | null>(null)
  const planningAbortRef = useRef<AbortController | null>(null)
  const mountedRef = useRef(true)
  const load = () => Promise.all([api.getHousekeepingSettings(), api.getHousekeepingReport(), api.getOrchestrationCapacity()]).then(([value, latest, resources]) => { setSettings(value); setReport(latest); setCapacity(resources) }).catch(reason => setError(reason.message))
  useEffect(() => { void load() }, [])
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      planningAbortRef.current?.abort()
      planningAbortRef.current = null
      planningRef.current = null
    }
  }, [])
  const save = async () => { if (!settings || !access.status?.authenticated) return; setError(''); try { setSettings(await api.updateHousekeepingSettings(settings)) } catch (reason: any) { setError(reason.message || t('housekeeping.saveFailed')); await access.refresh() } }
  const startPlanning = (kind: 'normal' | 'full') => {
    if (planningRef.current) return null
    const controller = new AbortController()
    planningRef.current = kind
    planningAbortRef.current = controller
    setPlanning(kind)
    return controller
  }
  const finishPlanning = (controller: AbortController) => {
    if (planningAbortRef.current !== controller) return
    planningAbortRef.current = null
    planningRef.current = null
    if (mountedRef.current) setPlanning(null)
  }
  const buildPlan = async () => {
    const controller = startPlanning('normal')
    if (!controller) return
    setError('')
    setPlan(null)
    setExecutionBlock(null)
    try {
      const nextPlan = await api.getHousekeepingPlan(mode, controller.signal)
      if (!controller.signal.aborted && mountedRef.current) setPlan(nextPlan)
    } catch (reason) {
      if (!controller.signal.aborted && mountedRef.current) {
        setPlan(null)
        setError(planningError(reason, t('housekeeping.planFailed')))
      }
    } finally {
      finishPlanning(controller)
    }
  }
  const run = async () => {
    if (!plan || !access.status?.authenticated) return
    setRunning(true)
    setError('')
    try {
      setReport(await api.runHousekeeping(plan.mode, false, plan.plan_id))
      setPlan(null)
      setExecutionBlock(null)
      await load()
    } catch (reason: any) {
      if (reason instanceof CaoApiError && reason.reasonCode === 'HOUSEKEEPING_PLAN_CHANGED') {
        setPlan(null)
        setExecutionBlock('changed')
        setError(t('housekeeping.planChanged'))
      } else if (reason instanceof CaoApiError && reason.reasonCode === 'HOUSEKEEPING_BUSY') {
        setExecutionBlock('busy')
        setError(t('housekeeping.lockBusy'))
      } else {
        setError(reason.message || t('housekeeping.failed'))
      }
      await access.refresh()
    } finally {
      setRunning(false)
    }
  }
  const buildFullPlan = async () => {
    const controller = startPlanning('full')
    if (!controller) return
    setFullError('')
    setFullReport(null)
    setFullPlan(null)
    try {
      const nextPlan = await api.getFullCleanupPlan(
        controller.signal,
        retireDirtyWorktrees,
      )
      if (!controller.signal.aborted && mountedRef.current) setFullPlan(nextPlan)
    } catch (reason) {
      if (!controller.signal.aborted && mountedRef.current) {
        setFullPlan(null)
        setFullError(planningError(reason, t('housekeeping.fullPlanFailed')))
      }
    } finally {
      finishPlanning(controller)
    }
  }
  const runFull = async () => {
    if (!fullPlan || !access.status?.authenticated) return
    setFullConfirm(false)
    setFullRunning(true)
    setFullError('')
    try {
      const result = await api.runFullCleanup(fullPlan.plan_id, retireDirtyWorktrees)
      setFullReport(result)
      setReport(result)
      setFullPlan(null)
      await load()
    } catch (reason: any) {
      if (reason instanceof CaoApiError && reason.reasonCode === 'HOUSEKEEPING_PLAN_CHANGED') {
        setFullPlan(null)
        setFullError(t('housekeeping.fullPlanChanged'))
      } else if (reason instanceof CaoApiError && ['FULL_CLEANUP_NOT_IDLE', 'FULL_CLEANUP_IDLE_INVENTORY_UNKNOWN'].includes(String(reason.reasonCode))) {
        setFullPlan(null)
        setFullError(reason.message)
      } else {
        setFullError(reason.message || t('housekeeping.fullFailed'))
      }
      await access.refresh()
    } finally { setFullRunning(false) }
  }
  const updatePolicy = (category: string, key: string, value: boolean | number) => { if (!settings) return; setSettings({ ...settings, policy: { ...settings.policy, [category]: { ...settings.policy[category], [key]: value } } }) }
  if (!settings) return <p className="text-gray-400">{t('housekeeping.loading')}</p>
  const used = capacity?.root_disk.used_percent ?? 0
  const diskState = capacity?.root_disk.state || (used >= 92 ? 'CRITICAL' : used >= 85 ? 'RED' : used >= 70 ? 'YELLOW' : 'GREEN')
  const planCandidates = Array.isArray(plan?.candidates) ? plan.candidates : []
  const planWarnings = Array.isArray(plan?.warnings) ? plan.warnings : []
  const actionable = planCandidates.filter(candidate => candidate.action !== 'preserve')
  const protectedCount = planCandidates.length - actionable.length
  const classSummaries = plan?.class_summaries || {}
  const reclaimByClass = Object.fromEntries(Object.entries(classSummaries).filter(([, value]) => value.reclaimable_bytes > 0).map(([category, value]) => [category, value.reclaimable_bytes]))
  const protectedByClass = Object.fromEntries(Object.entries(classSummaries).filter(([, value]) => value.preserved_bytes > 0).map(([category, value]) => [category, value.preserved_bytes]))
  const invalidPlanReason = plan && (
    plan.schema_version !== 1
    || !/^[0-9a-f]{64}$/.test(plan.plan_id)
    || plan.mode !== mode
    || !Array.isArray(plan.candidates)
  ) ? t('housekeeping.disabled.invalid') : null
  const executeDisabledReason = planning
    ? t('housekeeping.disabled.waitInventory')
    : running
      ? t('housekeeping.disabled.executing')
    : executionBlock === 'changed'
      ? t('housekeeping.disabled.stale')
      : executionBlock === 'busy'
        ? t('housekeeping.lockBusy')
        : !plan
          ? t('housekeeping.disabled.noPlan')
          : invalidPlanReason
            ? invalidPlanReason
            : actionable.length === 0
              ? t('housekeeping.disabled.noCandidates')
              : access.loading
                ? t('housekeeping.disabled.checkingAuth')
                : !access.status?.configured
                  ? t('housekeeping.disabled.notConfigured')
                  : !access.status.authenticated
                    ? t('housekeeping.disabled.unlock')
                    : null
  const frequent = /^(\d+)([mhd])$/.exec(settings.schedule.frequent) || ['', '6', 'h']
  const weekly = /^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) (\d\d:\d\d) UTC$/.exec(settings.schedule.weekly) || ['', 'Sun', '04:00']
  const fullCandidates = Array.isArray(fullPlan?.candidates) ? fullPlan.candidates : []
  const fullActionable = fullCandidates.filter(candidate => candidate.action !== 'preserve')
  const fullProtected = fullCandidates.length - fullActionable.length
  const fullClasses = fullPlan?.class_summaries || {}
  const fullDisabledReason = planning
    ? t('housekeeping.full.disabled.waitInventory')
    : fullRunning
      ? t('housekeeping.full.disabled.executing')
    : !fullPlan
      ? t('housekeeping.full.disabled.noPlan')
      : !fullPlan.idle_gate.eligible
        ? t('housekeeping.full.disabled.notIdle')
        : fullActionable.length === 0
          ? t('housekeeping.full.disabled.noCandidates')
          : access.loading
            ? t('housekeeping.full.disabled.checkingAuth')
            : !access.status?.configured
              ? t('housekeeping.full.disabled.notConfigured')
              : !access.status.authenticated
                ? t('housekeeping.full.disabled.unlock')
                : null
  const fullCleanupDanger = <>
    <section className="overflow-hidden rounded-xl border border-red-800/70 bg-red-950/20" aria-labelledby="full-cleanup-heading">
      <div className="border-b border-red-900/60 p-4 sm:p-5">
        <div className="flex items-start gap-3">
          <span className="rounded-lg bg-red-900/50 p-2 text-red-300">
<Trash2 size={18} aria-hidden="true" />
</span>
          <div className="min-w-0">
            <h2 id="full-cleanup-heading" className="text-base font-semibold text-red-100">{t('housekeeping.full.title')}</h2>
            <p className="mt-1 text-xs leading-5 text-red-200/80">{t('housekeeping.full.help')}</p>
          </div>
        </div>
        <p className="mt-3 rounded-lg border border-red-800/60 bg-red-950/40 p-3 text-sm font-medium text-red-100">{!fullPlan || fullPlan.release_state.active_only_expected ? t('housekeeping.full.activeOnly') : t('housekeeping.full.ambiguous', { count: fullPlan.release_state.protected_non_active_releases })}</p>
      </div>
      <div className="space-y-4 p-4 sm:p-5">
        <label className="flex min-h-11 items-start gap-3 rounded-lg border border-red-900/60 bg-red-950/30 p-3 text-xs leading-5 text-red-100">
          <input
            type="checkbox"
            checked={retireDirtyWorktrees}
            onChange={event => {
              setRetireDirtyWorktrees(event.target.checked)
              setFullPlan(null)
              setFullReport(null)
            }}
            className="mt-0.5 h-4 w-4 shrink-0 accent-red-500"
          />
          <span><span className="block font-medium">{t('housekeeping.full.retireDirty')}</span><span className="mt-0.5 block text-red-200/70">{t('housekeeping.full.retireDirtyHelp')}</span></span>
        </label>
        <div className="flex flex-col gap-3 sm:flex-row">
          <button aria-busy={planning === 'full'} disabled={Boolean(planning) || fullRunning || running} className="min-h-11 rounded-lg bg-gray-700 px-4 text-sm disabled:opacity-50" onClick={() => void buildFullPlan()}>{planning === 'full' ? <span className="inline-flex items-center gap-2">
<Loader2 size={15} className="animate-spin" aria-hidden="true"/>{t('housekeeping.full.building')}</span> : t('housekeeping.full.build')}</button>
          <button aria-describedby={fullDisabledReason ? 'full-cleanup-disabled-reason' : undefined} disabled={Boolean(fullDisabledReason)} className="min-h-11 rounded-lg bg-red-600 px-4 text-sm font-semibold text-white disabled:opacity-40" onClick={() => setFullConfirm(true)}>{fullRunning ? t('housekeeping.full.running') : t('housekeeping.full.delete')}</button>
        </div>
        {planning === 'full' && <p role="status" className="text-xs leading-5 text-gray-300">{t('housekeeping.full.scanning')}</p>}
        {fullDisabledReason && <p id="full-cleanup-disabled-reason" className="text-xs leading-5 text-amber-200">{fullDisabledReason}</p>}
        {fullError && <p role="alert" className="rounded-lg border border-red-700/50 bg-red-950/40 p-3 text-sm text-red-200">{fullError}</p>}
        {fullPlan && <div className="space-y-4 rounded-xl border border-red-900/60 bg-gray-950/40 p-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Summary label={t('housekeeping.full.estimated')} value={bytes(fullPlan.reclaimable_bytes)} />
            <Summary label={t('common.resources')} value={String(fullActionable.length)} />
            <Summary label={t('housekeeping.full.releasesDelete')} value={String(fullPlan.release_state.releases_to_delete)} detail={t('housekeeping.full.rollbackCount', { count: fullPlan.release_state.rollback_releases_to_delete })} />
            <Summary label={t('housekeeping.full.idleGate')} value={t(fullPlan.idle_gate.eligible ? 'status.ready' : 'housekeeping.full.blocked')} detail={t('housekeeping.full.agentCounts', { ready: fullPlan.idle_gate.ready_agents, exited: fullPlan.idle_gate.exited_agents })} />
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {['session_workspaces', 'worktrees', 'build_artifact', 'reproducible_cache', 'package_cache', 'logs', 'ephemeral', 'browser_cache', 'releases'].map(category => <Summary key={category} label={category} value={bytes(fullClasses[category]?.reclaimable_bytes || 0)} detail={tp('resources', fullClasses[category]?.actionable_count || 0)} />)}
            <Summary label={t('housekeeping.protected')} value={String(fullProtected)} detail={t('housekeeping.full.requiredProtected')} />
          </div>
          {!fullPlan.idle_gate.eligible && <ul className="list-disc space-y-1 pl-5 text-xs text-amber-200">{fullPlan.idle_gate.blockers.map((item, index) => <li key={`${item.terminal_id}-${item.reason_code}-${index}`}>{item.terminal_id ? `${t('housekeeping.full.agentBlocker', { id: item.terminal_id })} ` : ''}{item.reason_code}</li>)}</ul>}
          <HousekeepingWarnings warnings={fullPlan.warnings || []} />
          <details>
<summary className="min-h-9 cursor-pointer text-xs text-red-200">{t('housekeeping.full.inspect')}</summary>
<div className="mt-2 max-h-72 overflow-auto rounded-lg border border-gray-700">
<table className="w-full min-w-[34rem] text-left text-xs">
<thead className="sticky top-0 bg-gray-900 text-gray-400">
<tr>
<th className="p-2">{t('common.class')}</th>
<th className="p-2">{t('common.identity')}</th>
<th className="p-2">{t('common.action')}</th>
<th className="p-2">{t('housekeeping.full.reclaim')}</th>
<th className="p-2">{t('common.reason')}</th>
</tr>
</thead>
<tbody>{fullCandidates.map(candidate => <tr key={candidate.canonical_identity} className="border-t border-gray-800">
<td className="p-2">{candidate.category}</td>
<td className="break-all p-2 font-mono text-gray-400">{candidate.canonical_identity}</td>
<td className="p-2">{candidate.action}</td>
<td className="p-2">{bytes(candidate.estimated_reclaim_bytes)}</td>
<td className="p-2 text-gray-400">{String(candidate.protection_reason || candidate.retention_reason)}</td>
</tr>)}</tbody>
</table>
</div>
</details>
        </div>}
        {fullReport && <HousekeepingReport report={fullReport} diskState={diskState} />}
      </div>
    </section>
    <ConfirmModal open={fullConfirm} title={t('housekeeping.full.confirmTitle')} message={t(fullPlan?.release_state.active_only_expected ? 'housekeeping.full.confirmActive' : 'housekeeping.full.confirmAmbiguous')} details={fullPlan ? [{ label: t('housekeeping.full.estimated'), value: bytes(fullPlan.reclaimable_bytes) }, { label: t('common.resources'), value: String(fullActionable.length) }, { label: t('housekeeping.full.activePreserved'), value: fullPlan.release_state.active_release || t('housekeeping.authorityUnproven') }] : []} confirmLabel={t('housekeeping.full.run')} variant="danger" loading={fullRunning} onConfirm={() => void runFull()} onCancel={() => setFullConfirm(false)}>
      <p className="text-xs leading-5 text-gray-300">{t('housekeeping.full.preserved')}</p>
      <p className="text-xs leading-5 text-red-200">{t('housekeeping.full.permanent')}</p>
    </ConfirmModal>
  </>
  return <section className="space-y-5" aria-labelledby="housekeeping-settings-heading">
<div>
<h1 id="housekeeping-settings-heading" className="text-xl font-semibold text-white">{t('housekeeping.title')}</h1>
<p className="mt-1 text-sm text-gray-400">{t('housekeeping.description')}</p>
</div>
<div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
<Summary label={t('housekeeping.diskHealth')} value={t(resourceStateTranslationKey(diskState))} detail={capacity ? t('housekeeping.diskUsed', { percent: used, gib: capacity.root_disk.free_gib }) : t('housekeeping.resourceUnavailable')} />
<Summary label={t('housekeeping.safelyReclaimable')} value={plan ? bytes(plan.reclaimable_bytes) : t('housekeeping.buildPlan')} detail={plan ? t('housekeeping.actionableCandidates', { count: actionable.length }) : t('housekeeping.noEstimate')} />
<Summary label={t('housekeeping.currentState')} value={t(running ? 'housekeeping.running' : 'status.idle')} detail={t(running ? 'housekeeping.operationProgress' : 'housekeeping.noOperation')} />
<Summary label={t('housekeeping.lastRun')} value={report?.status === 'never_run' ? t('housekeeping.never') : report ? t(report.ok === false || report.completed_with_issues ? 'housekeeping.completedIssues' : 'status.workflow.completed') : t('output.unavailable')} />
<Summary label={t('housekeeping.nextRun')} value={humanInterval(settings.schedule.frequent, locale)} detail={`${t(weekdayKey(weekly[1] as Weekday))} ${weekly[2]} UTC · ${t('housekeeping.pressureRecovery')}`} />
<Summary label={t('housekeeping.backups')} value={t('housekeeping.protected')} detail={t('housekeeping.inventoryOnly')} />
</div>
<OperatorAccessCard access={access}/>
<section aria-labelledby="cleanup-policy-heading">
<div className="mb-3">
<h2 id="cleanup-policy-heading" className="text-base font-semibold text-gray-100">{t('housekeeping.policy')}</h2>
<p className="mt-1 text-xs text-gray-400">{t('housekeeping.policyHelp')}</p>
</div>
<div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{POLICY_SPECS.map(spec => { const policy = settings.policy[spec.key]; const protectedClass = spec.key === 'backups'; return <fieldset key={spec.key} className="rounded-xl border border-gray-700/60 bg-gray-800/60 p-4">
<legend className="sr-only">{t(spec.labelKey)}</legend>
<div className="flex items-start justify-between gap-3">
<div>
<h3 className="font-medium text-gray-100">{t(spec.labelKey)}</h3>
<p className="mt-1 text-xs leading-5 text-gray-400">{t(spec.descriptionKey)}</p>
</div>
<label className={`relative inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center ${protectedClass ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'}`}>
<span className="sr-only">{t('housekeeping.enableClass', { name: t(spec.labelKey) })}</span>
<input type="checkbox" checked={Boolean(policy.enabled)} disabled={protectedClass} onChange={event => updatePolicy(spec.key, 'enabled', event.target.checked)} className="peer sr-only"/>
<span className="h-6 w-11 rounded-full bg-gray-700 transition peer-checked:bg-emerald-600 peer-focus-visible:ring-2 peer-focus-visible:ring-emerald-400 after:absolute after:ml-0.5 after:mt-0.5 after:h-5 after:w-5 after:rounded-full after:bg-white after:transition peer-checked:after:translate-x-5"/>
</label>
</div>{spec.fields.length > 0 && <div className="mt-4 space-y-3">{spec.fields.map(field => <label key={field.key} className="block text-xs text-gray-400">{t(field.labelKey)}<div className="mt-1 flex">
<input aria-label={`${t(spec.labelKey)} ${t(field.labelKey)}`} type="number" min={1} max={field.unit === 'count' ? 100 : 365} step={field.unit === 'count' ? 1 : 0.5} value={unitValue(Number(policy[field.key]), field.unit)} onChange={event => updatePolicy(spec.key, field.key, backendValue(Number(event.target.value), field.unit))} className="min-h-11 min-w-0 flex-1 rounded-l-lg border border-gray-700 bg-gray-950 px-3 text-sm"/>
<span className="flex min-h-11 items-center rounded-r-lg border border-l-0 border-gray-700 bg-gray-900 px-3 text-xs text-gray-400">{t(`housekeeping.unit.${field.unit}` as TranslationKey)}</span>
</div>
</label>)}</div>}{protectedClass && <span className="mt-3 inline-flex rounded-full bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-300">{t('housekeeping.protectedInventory')}</span>}</fieldset>})}</div>
</section>
<section className="rounded-xl border border-gray-700/60 bg-gray-800/60 p-4" aria-labelledby="schedule-heading">
<h2 id="schedule-heading" className="text-base font-semibold text-gray-100">{t('housekeeping.schedule')}</h2>
<p className="mt-1 text-xs text-gray-400">{t('housekeeping.scheduleHelp')}</p>
<div className="mt-4 grid gap-4 md:grid-cols-3">
<fieldset>
<legend className="text-sm font-medium text-gray-200">{t('housekeeping.frequentMaintenance')}</legend>
<p className="mt-1 text-xs text-gray-400">{t('housekeeping.frequentHelp')}</p>
<div className="mt-3 flex">
<input aria-label={t('housekeeping.frequentValue')} type="number" min={1} value={frequent[1]} onChange={event => setSettings({ ...settings, schedule: { ...settings.schedule, frequent: `${event.target.value}${frequent[2]}` } })} className="min-h-11 min-w-0 flex-1 rounded-l-lg border border-gray-700 bg-gray-950 px-3"/>
<select aria-label={t('housekeeping.frequentUnit')} value={frequent[2]} onChange={event => setSettings({ ...settings, schedule: { ...settings.schedule, frequent: `${frequent[1]}${event.target.value}` } })} className="min-h-11 rounded-r-lg border border-l-0 border-gray-700 bg-gray-950 px-3">
<option value="m">{t('housekeeping.unit.minutes')}</option>
<option value="h">{t('housekeeping.unit.hours')}</option>
<option value="d">{t('housekeeping.unit.days')}</option>
</select>
</div>
</fieldset>
<fieldset>
<legend className="text-sm font-medium text-gray-200">{t('housekeeping.weeklyMaintenance')}</legend>
<p className="mt-1 text-xs text-gray-400">{t('housekeeping.weeklyHelp')}</p>
<div className="mt-3 flex">
<select aria-label={t('housekeeping.weeklyDay')} value={weekly[1]} onChange={event => setSettings({ ...settings, schedule: { ...settings.schedule, weekly: `${event.target.value} ${weekly[2]} UTC` } })} className="min-h-11 flex-1 rounded-l-lg border border-gray-700 bg-gray-950 px-2">{WEEKDAYS.map(day => <option key={day} value={day}>{t(weekdayKey(day))}</option>)}</select>
<input aria-label={t('housekeeping.weeklyTime')} type="time" value={weekly[2]} onChange={event => setSettings({ ...settings, schedule: { ...settings.schedule, weekly: `${weekly[1]} ${event.target.value} UTC` } })} className="min-h-11 min-w-0 flex-1 rounded-r-lg border border-l-0 border-gray-700 bg-gray-950 px-2"/>
</div>
</fieldset>
<div>
<h3 className="text-sm font-medium text-gray-200">{t('housekeeping.diskPressure')}</h3>
<p className="mt-1 text-xs leading-5 text-gray-400">{t('housekeeping.diskPressureHelp')}</p>
<span className="mt-3 inline-flex rounded-full bg-red-500/10 px-2 py-1 text-xs text-red-300">{t('housekeeping.onRed')}</span>
</div>
</div>
<button disabled={!access.status?.authenticated} className="mt-4 min-h-11 rounded-lg bg-emerald-600 px-4 text-sm font-medium text-white disabled:opacity-40" onClick={() => void save()}>{t('housekeeping.savePolicy')}</button>
</section>
<section className="rounded-xl border border-gray-700/60 bg-gray-800/60 p-4" aria-labelledby="manual-operation-heading">
<h2 id="manual-operation-heading" className="text-base font-semibold text-gray-100">{t('housekeeping.manual')}</h2>
<ol className="mt-2 flex flex-col gap-1 text-xs text-gray-400 sm:flex-row sm:items-center">
<li className="text-gray-300">{t('housekeeping.stepPlan')}</li>
<li aria-hidden="true" className="hidden sm:block">→</li>
<li className={plan ? 'text-gray-300' : ''}>{t('housekeeping.stepInspect')}</li>
<li aria-hidden="true" className="hidden sm:block">→</li>
<li className={plan ? 'text-gray-300' : ''}>{t('housekeeping.stepExecute')}</li>
</ol>
<div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-end">
<label className="text-xs text-gray-400">{t('housekeeping.cleanupClass')}<select value={mode} onChange={event => { setMode(event.target.value as typeof mode); setPlan(null); setExecutionBlock(null) }} className="mt-1 block min-h-11 w-full rounded-lg border border-gray-700 bg-gray-950 px-3 sm:w-auto">
<option value="frequent">{t('housekeeping.mode.frequent')}</option>
<option value="weekly">{t('housekeeping.mode.weekly')}</option>
<option value="pressure">{t('housekeeping.mode.pressure')}</option>
</select>
</label>
<button aria-busy={planning === 'normal'} disabled={Boolean(planning) || running || fullRunning} className="min-h-11 rounded-lg bg-gray-700 px-4 text-sm disabled:opacity-50" onClick={() => void buildPlan()}>{planning === 'normal' ? <span className="inline-flex items-center gap-2">
<Loader2 size={15} className="animate-spin" aria-hidden="true"/>{t('housekeeping.buildingPlan')}</span> : t('housekeeping.buildDryRun')}</button>
<button aria-describedby={executeDisabledReason ? 'housekeeping-execute-reason' : undefined} disabled={Boolean(executeDisabledReason)} className="min-h-11 rounded-lg bg-amber-600 px-4 text-sm font-medium text-white disabled:opacity-40" onClick={() => void run()}>{running ? t('housekeeping.running') : t('housekeeping.execute')}</button>
</div>{planning === 'normal' && <p role="status" className="mt-2 text-xs leading-5 text-gray-300">{t('housekeeping.scanning')}</p>}{executeDisabledReason && <p id="housekeeping-execute-reason" className="mt-2 text-xs leading-5 text-amber-200">{executeDisabledReason}</p>}{plan && <div className="mt-4 rounded-xl border border-emerald-800/40 bg-emerald-950/10 p-4">
<div className="grid gap-3 sm:grid-cols-3">
<Summary label={t('housekeeping.reclaimable')} value={bytes(plan.reclaimable_bytes)}/>
<Summary label={t('housekeeping.actionable')} value={String(actionable.length)}/>
<Summary label={t('housekeeping.protectedSkipped')} value={String(protectedCount)}/>
</div>
<p className="mt-3 text-xs font-medium text-gray-300">{t('housekeeping.safeByClass')}</p>
<div className="mt-2 flex flex-wrap gap-2">{Object.entries(reclaimByClass).map(([category, amount]) => { const spec = POLICY_SPECS.find(item => item.key === category); return <span key={category} className="rounded-full bg-gray-900 px-2.5 py-1 text-xs text-gray-300">{spec ? t(spec.labelKey) : category}: {bytes(amount)}</span> })}</div>
<p className="mt-3 text-xs font-medium text-gray-300">{t('housekeeping.protectedByClass')}</p>
<div className="mt-2 flex flex-wrap gap-2">{Object.entries(protectedByClass).map(([category, amount]) => { const spec = POLICY_SPECS.find(item => item.key === category); return <span key={category} className="rounded-full bg-gray-900 px-2.5 py-1 text-xs text-gray-400">{spec ? t(spec.labelKey) : category}: {bytes(amount)}</span> })}</div>
<HousekeepingWarnings warnings={planWarnings} className="mt-3" />
<details className="mt-3">
<summary className="min-h-9 cursor-pointer text-xs text-emerald-300">{t('housekeeping.inspectReasons')}</summary>
<div className="mt-2 max-h-72 overflow-auto rounded-lg border border-gray-700">
<table className="w-full text-left text-xs">
<thead className="sticky top-0 bg-gray-900 text-gray-400">
<tr>
<th className="p-2">{t('common.class')}</th>
<th className="p-2">{t('housekeeping.candidateIdentity')}</th>
<th className="p-2">{t('common.action')}</th>
<th className="p-2">{t('common.sizeReclaim')}</th>
<th className="p-2">{t('common.reason')}</th>
</tr>
</thead>
<tbody>{planCandidates.map(candidate => <tr key={candidate.canonical_identity} className="border-t border-gray-800">
<td className="p-2 text-gray-300">{(() => { const spec = POLICY_SPECS.find(item => item.key === candidate.category); return spec ? t(spec.labelKey) : candidate.category })()}</td>
<td className="break-all p-2 font-mono text-gray-400">{candidate.canonical_identity}</td>
<td className="p-2 text-gray-300">{candidate.action}</td>
<td className="p-2 text-gray-400">{bytes(candidate.bytes)}{candidate.action !== 'preserve' && candidate.estimated_reclaim_bytes !== candidate.bytes ? ` / ${bytes(candidate.estimated_reclaim_bytes)}` : ''}</td>
<td className="p-2 text-gray-400">{String(candidate.protection_reason || candidate.retention_reason)}</td>
</tr>)}</tbody>
</table>
</div>
</details>
<p className="mt-3 text-xs text-gray-400">{t('housekeeping.executionRevalidates')}</p>
</div>}</section>
<section aria-labelledby="latest-report-heading">
<h2 id="latest-report-heading" className="mb-3 text-base font-semibold text-gray-100">{t('housekeeping.latestReport')}</h2>
<HousekeepingReport report={report} diskState={diskState}/>
</section>{error && <p role="alert" className="rounded-lg border border-red-700/50 bg-red-950/30 p-3 text-sm text-red-300">{error}</p>}{fullCleanupDanger}</section>
}

function AboutSettings() {
  const { t } = useI18n()
  const principles = [
    [t('about.operationalTruth'), t('about.operationalTruthCopy'), <ShieldCheck size={18}/>],
    [t('about.nativeAgents'), t('about.nativeAgentsCopy'), <Boxes size={18}/>],
    [t('about.parallelism'), t('about.parallelismCopy'), <HardDrive size={18}/>],
    [t('about.durableResults'), t('about.durableResultsCopy'), <Database size={18}/>],
    [t('about.ownerAuthority'), t('about.ownerAuthorityCopy'), <HeartHandshake size={18}/>],
    [t('about.selfHosted'), t('about.selfHostedCopy'), <CheckCircle2 size={18}/>],
  ] as const
  return <article className="space-y-5" aria-labelledby="about-heading">
<header className="overflow-hidden rounded-2xl border border-emerald-800/40 bg-gradient-to-br from-emerald-950/40 via-gray-900 to-gray-900 p-5 sm:p-7">
<div className="flex flex-col gap-5 sm:flex-row sm:items-center">
<img src="/threadcells-symbol.png" alt={t('about.symbol')} className="h-20 w-20 rounded-2xl object-cover shadow-lg"/>
<div className="min-w-0">
<div className="flex flex-wrap items-center gap-2">
<h1 id="about-heading" className="text-2xl font-semibold text-white">ThreadCells</h1>
<span className="rounded-full bg-amber-500/10 px-2 py-1 text-xs text-amber-200">{t('about.preview')}</span>
</div>
<p className="mt-2 max-w-2xl text-sm leading-6 text-gray-300">{t('about.description')}</p>
<dl className="mt-4 flex flex-col gap-2 text-xs text-gray-400 sm:flex-row sm:gap-6">
<div>
<dt className="inline text-gray-400">{t('common.version')} </dt>
<dd className="inline font-mono text-gray-300">{BUILD_IDENTITY.version}</dd>
</div>
<div className="min-w-0">
<dt className="inline text-gray-400">{t('common.source')} </dt>
<dd className="inline break-all font-mono text-gray-300">{BUILD_IDENTITY.revision}</dd>
</div>
</dl>
</div>
</div>
</header>
<section className="grid gap-4 lg:grid-cols-2">
<div className="rounded-xl border border-gray-700/60 bg-gray-800/60 p-5">
<h2 className="text-base font-semibold text-gray-100">{t('about.what')}</h2>
<p className="mt-2 text-sm leading-6 text-gray-400">{t('about.whatCopy')}</p>
</div>
<div className="rounded-xl border border-gray-700/60 bg-gray-800/60 p-5">
<h2 className="text-base font-semibold text-gray-100">{t('about.why')}</h2>
<p className="mt-2 text-sm leading-6 text-gray-400">{t('about.whyCopy')}</p>
</div>
</section>
<section aria-labelledby="principles-heading">
<h2 id="principles-heading" className="text-base font-semibold text-gray-100">{t('about.principles')}</h2>
<div className="mt-3 grid gap-3 md:grid-cols-2 lg:grid-cols-3">{principles.map(([title, copy, icon]) => <div key={title} className="rounded-xl border border-gray-700/60 bg-gray-800/60 p-4">
<div className="text-emerald-300">{icon}</div>
<h3 className="mt-3 text-sm font-medium text-gray-100">{title}</h3>
<p className="mt-1 text-xs leading-5 text-gray-400">{copy}</p>
</div>)}</div>
</section>
<section className="grid gap-4 lg:grid-cols-2">
<div className="rounded-xl border border-gray-700/60 bg-gray-800/60 p-5">
<h2 className="text-base font-semibold text-gray-100">{t('about.maintainedBy')}</h2>
<p className="mt-2 text-sm text-gray-300">Subaev Ruslan</p>
<p className="mt-1 text-xs leading-5 text-gray-400">{t('about.community')}</p>
</div>
<div className="rounded-xl border border-gray-700/60 bg-gray-800/60 p-5">
<h2 className="text-base font-semibold text-gray-100">{t('about.openSource')}</h2>
<p className="mt-2 text-sm text-gray-300">{t('about.license')}</p>
<div className="mt-3 flex flex-wrap gap-3">
<a href="/docs" className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-emerald-600 px-4 text-sm text-white">
<BookOpen size={15}/>{t('about.documentation')}</a>
<a href="/docs/provenance" className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-gray-700 px-4 text-sm text-gray-300">
<Info size={15}/>{t('about.provenance')}</a>
</div>
</div>
</section>
<footer className="rounded-xl border border-gray-800 bg-gray-900/40 p-4 text-xs leading-5 text-gray-400">{t('about.attribution')}</footer>
</article>
}

export function ControlPlaneSettings({ section, navigate }: { section: SettingsSection; navigate: (section: SettingsSection) => void }) {
  const { t } = useI18n()
  return <div className="min-w-0 space-y-5">
<nav aria-label={t('settings.sections')} className="grid grid-cols-2 gap-2 rounded-xl border border-gray-700/50 bg-gray-800/60 p-2 sm:grid-cols-3 lg:grid-cols-6">{SECTIONS.map(item => <a key={item.key} href={item.path} aria-current={section === item.key ? 'page' : undefined} onClick={event => { if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return; event.preventDefault(); navigate(item.key) }} className={`flex min-h-11 min-w-0 items-center justify-center rounded-lg px-3 text-sm ${section === item.key ? 'bg-emerald-600 text-white' : 'text-gray-400 hover:bg-gray-700'}`}>{t(item.labelKey)}</a>)}</nav>{section === 'profiles' ? <ProfilesSettings/> : section === 'providers' ? <ProvidersSettings/> : section === 'housekeeping' ? <HousekeepingSettingsPage/> : section === 'telegram' ? <TelegramSettings/> : section === 'about' ? <AboutSettings/> : <SettingsPanel/>}</div>
}
