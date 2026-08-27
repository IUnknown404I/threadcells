import { useEffect, useState } from 'react'
import { BarChart3, RefreshCw } from 'lucide-react'
import { api, UsageAggregate, UsageStatistics as UsageStatisticsData } from '../api'
import { sessionDisplayName } from '../sessionDisplayName'
import { useI18n, type TranslationKey } from '../i18n'

type AggregateKind = 'global' | 'terminal' | 'session' | 'project' | 'provider' | 'profile'

function TokenValue({ value, accent = false, strong = false }: { value: number | null | undefined; accent?: boolean; strong?: boolean }) {
  const { locale, t } = useI18n()
  if (value == null) return <span className="text-[11px] text-gray-500">{t('common.notReported')}</span>
  return <span className={accent ? `font-semibold text-cyan-300 ${strong ? 'text-base' : ''}` : ''}>{value.toLocaleString(locale)}</span>
}

function primaryLabel(row: UsageAggregate, kind: AggregateKind, t: (key: TranslationKey, params?: Record<string, string | number>) => string) {
  if (kind === 'project') return row.label || t('statistics.unknownProject')
  if (kind === 'session') {
    const label = sessionDisplayName(row.label || row.id || t('statistics.unknownSession'))
    return row.legacy ? t('statistics.legacySession', { name: label }) : label
  }
  return row.label || row.id || t('statistics.unknownKind', { kind })
}

function AggregateTable({ title, rows, kind }: { title: string; rows: UsageAggregate[]; kind: AggregateKind }) {
  const { t } = useI18n()
  const global = kind === 'global'
  const scopeLabel = kind === 'terminal' ? t('statistics.terminal') : kind === 'session' ? t('statistics.session') : kind === 'project' ? t('statistics.project') : kind === 'provider' ? t('statistics.provider') : t('statistics.profile')
  return (
    <section className="rounded-xl border border-gray-700/50 bg-gray-800/60 p-4">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1"><div><h3 className="text-sm font-semibold text-gray-200">{title}</h3>{kind === 'session' && <p className="mt-1 text-[11px] text-gray-500">{t('statistics.legacyHelp')}</p>}</div>{!global && <p className="text-[11px] text-gray-500">{t('statistics.topTen')}</p>}</div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[680px] text-left text-xs">
          <thead className="text-gray-500">
            <tr>{!global && <th className="pb-2 font-medium">{scopeLabel}</th>}<th className="pb-2 font-medium">{t('statistics.reports')}</th><th className="pb-2 font-medium">{t('statistics.input')}</th><th className="pb-2 font-medium">{t('statistics.cachedInput')}</th><th className="pb-2 font-medium">{t('statistics.output')}</th><th className="pb-2 font-medium">{t('common.reasoning')}</th><th className="pb-2 font-medium">{t('common.total')}</th></tr>
          </thead>
          <tbody className="divide-y divide-gray-700/40 text-gray-300">
            {rows.length ? rows.map((row, index) => <tr key={row.id || index}>{!global && <td className="py-2 pr-3"><div className="font-medium text-gray-200">{primaryLabel(row, kind, t)}</div>{kind === 'project' && row.id && <div className="mt-0.5 font-mono text-[10px] text-gray-500">{row.id}</div>}</td>}<td className="py-2 pr-3"><TokenValue value={row.provider_run_count} accent={global} /></td><td className="py-2 pr-3"><TokenValue value={row.input_tokens} accent={global} /></td><td className="py-2 pr-3"><TokenValue value={row.cached_input_tokens} accent={global} /></td><td className="py-2 pr-3"><TokenValue value={row.output_tokens} accent={global} /></td><td className="py-2 pr-3"><TokenValue value={row.reasoning_output_tokens} accent={global} /></td><td className="py-2"><TokenValue value={row.total_tokens} accent strong={global} /></td></tr>) : <tr><td colSpan={global ? 6 : 7} className="py-4 text-gray-500">{t('statistics.noReports')}</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export function UsageStatistics() {
  const { t } = useI18n()
  const [data, setData] = useState<UsageStatisticsData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = () => api.getUsageStatistics().then(setData).then(() => setError(null)).catch(() => setError(t('statistics.unavailable')))

  useEffect(() => { load() }, [])

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div><h2 className="flex items-center gap-2 text-lg font-semibold text-white"><BarChart3 size={20} className="text-cyan-400" /> {t('statistics.title')}</h2><p className="mt-1 text-sm text-amber-300">{t('statistics.disclaimer')}</p><p className="mt-1 text-xs text-gray-500">{t('statistics.description')}</p></div>
        <button onClick={load} className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-gray-700 px-3 text-xs text-gray-200 hover:bg-gray-600"><RefreshCw size={14} /> {t('common.refresh')}</button>
      </div>
      {error ? <div className="rounded-lg border border-red-900/60 bg-red-950/30 p-3 text-sm text-red-300">{error}</div> : null}
      <AggregateTable title={t('statistics.global')} rows={data ? [data.global] : []} kind="global" />
      <AggregateTable title={t('statistics.byTerminal')} rows={data?.terminals || []} kind="terminal" />
      <AggregateTable title={t('statistics.bySession')} rows={data?.sessions || []} kind="session" />
      <AggregateTable title={t('statistics.byProject')} rows={data?.projects || []} kind="project" />
      <AggregateTable title={t('statistics.byProvider')} rows={data?.providers || []} kind="provider" />
      <AggregateTable title={t('statistics.byProfile')} rows={data?.profiles || []} kind="profile" />
    </div>
  )
}
