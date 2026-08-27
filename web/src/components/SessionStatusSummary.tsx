import type { ReactNode } from 'react'
import { SessionBoundaryAgent, SessionSummary } from '../api'
import { lifecycleBadgeStatus, StatusBadge } from './StatusBadge'
import { useI18n } from '../i18n'

const ACTIVITY_ORDER = ['processing', 'queued', 'ready', 'exited']
const WORKFLOW_ORDER = [
  'active', 'waiting', 'result_ready', 'owner_gate', 'completed',
  'cancelled', 'failed', 'incomplete', 'untracked',
]

function orderedEntries(counts: Record<string, number>, order: string[]) {
  return Object.entries(counts)
    .filter(([, count]) => count > 0)
    .sort(([left], [right]) => {
      const leftIndex = order.indexOf(left)
      const rightIndex = order.indexOf(right)
      if (leftIndex !== rightIndex) {
        if (leftIndex < 0) return 1
        if (rightIndex < 0) return -1
        return leftIndex - rightIndex
      }
      return left.localeCompare(right)
    })
}

function workflowCounts(counts: Record<string, number>) {
  const grouped = { ...counts }
  if (grouped.recoverable) {
    grouped.waiting = (grouped.waiting || 0) + grouped.recoverable
    delete grouped.recoverable
  }
  return orderedEntries(grouped, WORKFLOW_ORDER)
}

function Count({ value }: { value: number }) {
  return value > 1
    ? <span className="text-xs tabular-nums text-gray-300">×{value}</span>
    : null
}

function BoundaryStatus({
  kind,
  agent,
  sessionId,
}: {
  kind: 'first' | 'last'
  agent: SessionBoundaryAgent | null
  sessionId: string
}) {
  const { t } = useI18n()
  return (
    <div
      data-testid={`session-status-${kind}-${sessionId}`}
      className="inline-flex min-w-0 flex-wrap items-center gap-1.5"
    >
      <span className="text-[10px] font-semibold uppercase tracking-wide text-gray-400">{t(kind === 'first' ? 'common.first' : 'common.last')}</span>
      {agent ? (
        <span data-terminal-id={agent.id} className="inline-flex min-w-0">
          <StatusBadge status={lifecycleBadgeStatus(
            agent.workflow_state,
            agent.activity,
            agent.lifecycle,
            agent.execution_state,
          )} />
        </span>
      ) : <span className="text-xs text-gray-400">—</span>}
    </div>
  )
}

export function SessionStatusSummary({ session, trailing }: { session: SessionSummary; trailing?: ReactNode }) {
  const { t } = useI18n()
  const activities = orderedEntries(session.activity_counts, ACTIVITY_ORDER)
  const workflows = workflowCounts(session.workflow_counts)
  return (
    <div
      data-testid={`session-status-groups-${session.id}`}
      className="flex min-w-0 flex-1 flex-col gap-1.5"
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5">
        <BoundaryStatus kind="first" agent={session.first_agent} sessionId={session.id} />
        <BoundaryStatus kind="last" agent={session.last_agent} sessionId={session.id} />
      </div>
      <div
        data-testid={`session-status-total-${session.id}`}
        className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1.5"
      >
        <span className="text-[10px] font-semibold uppercase tracking-wide text-gray-400">{t('common.total')}</span>
        <div data-testid={`session-status-badges-${session.id}`} className="flex min-w-0 flex-wrap items-center gap-1.5">
          {activities.map(([state, count]) => (
            <span key={`activity-${state}`} data-testid={`session-status-agent-${session.id}-${state}`} className="inline-flex items-center gap-1">
              <StatusBadge status={state} /><Count value={count} />
            </span>
          ))}
          {workflows.map(([state, count]) => (
            <span key={`workflow-${state}`} data-testid={`session-status-workflow-${session.id}-${state}`} className="inline-flex items-center gap-1 rounded-full border border-gray-700/50 px-1 py-0.5">
              <span className="pl-1 text-[10px] text-gray-300">{t('common.workflowPrefix')}</span>
              <StatusBadge status={`WORKFLOW_${state.toUpperCase()}`} /><Count value={count} />
            </span>
          ))}
          {!activities.length && !workflows.length && <span className="text-xs text-gray-400">{t('agents.noneYet')}</span>}
        </div>
        {trailing && <div data-testid={`session-status-actions-${session.id}`} className="ml-auto inline-flex shrink-0 items-center">{trailing}</div>}
      </div>
    </div>
  )
}
