import { SessionBoundaryAgent, SessionSummary } from '../api'
import { lifecycleBadgeStatus, StatusBadge } from './StatusBadge'

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
  label,
  agent,
  sessionId,
}: {
  label: 'First' | 'Last'
  agent: SessionBoundaryAgent | null
  sessionId: string
}) {
  return (
    <div
      data-testid={`session-status-${label.toLowerCase()}-${sessionId}`}
      className="inline-flex min-w-0 flex-wrap items-center gap-1.5"
    >
      <span className="text-[10px] font-semibold uppercase tracking-wide text-gray-400">{label}</span>
      {agent ? (
        <span data-terminal-id={agent.id} className="inline-flex min-w-0">
          <StatusBadge status={lifecycleBadgeStatus(
            agent.workflow_state,
            agent.activity,
            agent.lifecycle,
            agent.execution_state,
          )} workflowReason={agent.workflow_reason} />
        </span>
      ) : <span className="text-xs text-gray-400">—</span>}
    </div>
  )
}

export function SessionStatusSummary({ session }: { session: SessionSummary }) {
  const activities = orderedEntries(session.activity_counts, ACTIVITY_ORDER)
  const workflows = workflowCounts(session.workflow_counts)
  return (
    <div
      data-testid={`session-status-groups-${session.id}`}
      className="flex min-w-0 flex-1 flex-col gap-1.5"
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5">
        <BoundaryStatus label="First" agent={session.first_agent} sessionId={session.id} />
        <BoundaryStatus label="Last" agent={session.last_agent} sessionId={session.id} />
      </div>
      <div
        data-testid={`session-status-total-${session.id}`}
        className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1.5"
      >
        <span className="text-[10px] font-semibold uppercase tracking-wide text-gray-400">Total</span>
        <div data-testid={`session-status-badges-${session.id}`} className="flex min-w-0 flex-wrap items-center gap-1.5">
          {activities.map(([state, count]) => (
            <span key={`activity-${state}`} data-testid={`session-status-agent-${session.id}-${state}`} className="inline-flex items-center gap-1">
              <StatusBadge status={state} /><Count value={count} />
            </span>
          ))}
          {workflows.map(([state, count]) => (
            <span key={`workflow-${state}`} data-testid={`session-status-workflow-${session.id}-${state}`} className="inline-flex items-center gap-1 rounded-full border border-gray-700/50 px-1 py-0.5">
              <span className="pl-1 text-[10px] text-gray-300">Workflow ·</span>
              <StatusBadge status={`WORKFLOW_${state.toUpperCase()}`} /><Count value={count} />
            </span>
          ))}
          {!activities.length && !workflows.length && <span className="text-xs text-gray-400">No agents yet</span>}
        </div>
      </div>
    </div>
  )
}
