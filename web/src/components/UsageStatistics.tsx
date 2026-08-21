import { useEffect, useState } from 'react'
import { BarChart3, RefreshCw } from 'lucide-react'
import { api, UsageAggregate, UsageStatistics as UsageStatisticsData } from '../api'
import { sessionDisplayName } from '../sessionDisplayName'

type AggregateKind = 'global' | 'terminal' | 'session' | 'project' | 'provider' | 'profile'

function TokenValue({ value, accent = false, strong = false }: { value: number | null | undefined; accent?: boolean; strong?: boolean }) {
  if (value == null) return <span className="text-[11px] text-gray-500">Not reported</span>
  return <span className={accent ? `font-semibold text-cyan-300 ${strong ? 'text-base' : ''}` : ''}>{value.toLocaleString()}</span>
}

function primaryLabel(row: UsageAggregate, kind: AggregateKind) {
  if (kind === 'project') return row.label || 'Unknown project'
  if (kind === 'session') {
    const label = sessionDisplayName(row.label || row.id || 'Unknown session')
    return row.legacy ? `Legacy session: ${label}` : label
  }
  return row.label || row.id || `Unknown ${kind}`
}

function AggregateTable({ title, rows, kind }: { title: string; rows: UsageAggregate[]; kind: AggregateKind }) {
  const global = kind === 'global'
  const scopeLabel = kind === 'terminal' ? 'Terminal' : kind === 'session' ? 'Session' : kind === 'project' ? 'Project' : kind === 'provider' ? 'Provider' : 'Profile'
  return (
    <section className="rounded-xl border border-gray-700/50 bg-gray-800/60 p-4">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1"><div><h3 className="text-sm font-semibold text-gray-200">{title}</h3>{kind === 'session' && <p className="mt-1 text-[11px] text-gray-500">Legacy records remain separate until an exact recorded session match proves their lifetime.</p>}</div>{!global && <p className="text-[11px] text-gray-500">Showing top 10 by total where reported.</p>}</div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[680px] text-left text-xs">
          <thead className="text-gray-500">
            <tr>{!global && <th className="pb-2 font-medium">{scopeLabel}</th>}<th className="pb-2 font-medium">Reports</th><th className="pb-2 font-medium">Input</th><th className="pb-2 font-medium">Cached input</th><th className="pb-2 font-medium">Output</th><th className="pb-2 font-medium">Reasoning</th><th className="pb-2 font-medium">Total</th></tr>
          </thead>
          <tbody className="divide-y divide-gray-700/40 text-gray-300">
            {rows.length ? rows.map((row, index) => <tr key={row.id || index}>{!global && <td className="py-2 pr-3"><div className="font-medium text-gray-200">{primaryLabel(row, kind)}</div>{kind === 'project' && row.id && <div className="mt-0.5 font-mono text-[10px] text-gray-500">{row.id}</div>}</td>}<td className="py-2 pr-3"><TokenValue value={row.provider_run_count} accent={global} /></td><td className="py-2 pr-3"><TokenValue value={row.input_tokens} accent={global} /></td><td className="py-2 pr-3"><TokenValue value={row.cached_input_tokens} accent={global} /></td><td className="py-2 pr-3"><TokenValue value={row.output_tokens} accent={global} /></td><td className="py-2 pr-3"><TokenValue value={row.reasoning_output_tokens} accent={global} /></td><td className="py-2"><TokenValue value={row.total_tokens} accent strong={global} /></td></tr>) : <tr><td colSpan={global ? 6 : 7} className="py-4 text-gray-500">No provider usage reports observed yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  )
}

export function UsageStatistics() {
  const [data, setData] = useState<UsageStatisticsData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = () => api.getUsageStatistics().then(setData).then(() => setError(null)).catch(() => setError('Usage statistics are unavailable.'))

  useEffect(() => { load() }, [])

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div><h2 className="flex items-center gap-2 text-lg font-semibold text-white"><BarChart3 size={20} className="text-cyan-400" /> Statistics</h2><p className="mt-1 text-sm text-amber-300">{data?.label || 'Provider-reported usage — not a billing statement'}</p><p className="mt-1 text-xs text-gray-500">Live and retained sessions are included from durable provider telemetry. Missing values are not estimated; cached and reasoning tokens remain subsets of input and output.</p></div>
        <button onClick={load} className="inline-flex min-h-10 items-center gap-2 rounded-lg bg-gray-700 px-3 text-xs text-gray-200 hover:bg-gray-600"><RefreshCw size={14} /> Refresh</button>
      </div>
      {error ? <div className="rounded-lg border border-red-900/60 bg-red-950/30 p-3 text-sm text-red-300">{error}</div> : null}
      <AggregateTable title="Global" rows={data ? [data.global] : []} kind="global" />
      <AggregateTable title="By terminal" rows={data?.terminals || []} kind="terminal" />
      <AggregateTable title="By session" rows={data?.sessions || []} kind="session" />
      <AggregateTable title="By project" rows={data?.projects || []} kind="project" />
      <AggregateTable title="By provider" rows={data?.providers || []} kind="provider" />
      <AggregateTable title="By profile" rows={data?.profiles || []} kind="profile" />
    </div>
  )
}
