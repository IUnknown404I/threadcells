type TerminalStatus = 'IDLE' | 'PROCESSING' | 'COMPLETED' | 'WAITING_USER_ANSWER' | 'ERROR' | string | null

/** Build the UI badge value from the terminal-status API's durable projection. */
export function lifecycleBadgeStatus(
  workflowState: string | null | undefined,
  providerStatus: TerminalStatus,
  providerLifecycle?: string | null,
  executionState?: string | null,
): string {
  if (!workflowState) return providerStatus || 'unknown'
  const provider = providerLifecycle === 'exited'
    ? 'Exited'
    : executionState === 'processing' ? 'Processing'
    : executionState === 'queued_provider_execution' ? 'WaitingProviderSlot'
    : executionState === 'waiting_child_retirement' ? 'WaitingChildRetirement'
    : executionState === 'waiting_resource_recovery' ? 'WaitingResourceRecovery'
    : executionState === 'waiting_runtime_recovery' ? 'WaitingRuntimeRecovery'
    : executionState === 'waiting_workflow_continuation' ? 'WaitingWorkflowContinuation'
    : providerStatus === 'processing' ? 'Processing' : 'Ready'
  return `WORKFLOW_${workflowState.toUpperCase()}::${provider}`
}

interface StatusStyle {
  label: string
  dotClass: string
  bgClass: string
  textClass: string
  pulse?: boolean
}

const STATUS_CONFIG: Record<string, StatusStyle> = {
  WORKFLOW_COMPLETED: {
    label: 'Completed',
    dotClass: 'bg-purple-400',
    bgClass: 'bg-purple-400/10',
    textClass: 'text-purple-400',
  },
  WORKFLOW_ACTIVE: { label: 'In progress / Active', dotClass: 'bg-blue-400', bgClass: 'bg-blue-400/10', textClass: 'text-blue-400', pulse: true },
  WORKFLOW_OPEN: { label: 'Open', dotClass: 'bg-blue-400', bgClass: 'bg-blue-400/10', textClass: 'text-blue-400' },
  WORKFLOW_WAITING: { label: 'Waiting / Recoverable', dotClass: 'bg-amber-400', bgClass: 'bg-amber-400/10', textClass: 'text-amber-400' },
  WORKFLOW_RECOVERABLE: { label: 'Waiting / Recoverable', dotClass: 'bg-amber-400', bgClass: 'bg-amber-400/10', textClass: 'text-amber-400' },
  WORKFLOW_RESULT_READY: { label: 'Result ready', dotClass: 'bg-sky-400', bgClass: 'bg-sky-400/10', textClass: 'text-sky-400' },
  WORKFLOW_OWNER_GATE: { label: 'Needs owner decision', dotClass: 'bg-amber-400', bgClass: 'bg-amber-400/10', textClass: 'text-amber-400' },
  WORKFLOW_CANCELLED: { label: 'Cancelled', dotClass: 'bg-red-400', bgClass: 'bg-red-400/10', textClass: 'text-red-400' },
  WORKFLOW_INCOMPLETE: { label: 'Incomplete', dotClass: 'bg-red-400', bgClass: 'bg-red-400/10', textClass: 'text-red-400' },
  WORKFLOW_FAILED: { label: 'Failed', dotClass: 'bg-red-400', bgClass: 'bg-red-400/10', textClass: 'text-red-400' },
  IDLE: {
    label: 'Idle',
    dotClass: 'bg-emerald-400',
    bgClass: 'bg-emerald-400/10',
    textClass: 'text-emerald-400',
  },
  PROCESSING: {
    label: 'Processing',
    dotClass: 'bg-blue-400',
    bgClass: 'bg-blue-400/10',
    textClass: 'text-blue-400',
    pulse: true,
  },
  QUEUED: {
    label: 'Queued',
    dotClass: 'bg-amber-400',
    bgClass: 'bg-amber-400/10',
    textClass: 'text-amber-400',
  },
  WAITINGPROVIDERSLOT: { label: 'Queued · Waiting for provider slot', dotClass: 'bg-amber-400', bgClass: 'bg-amber-400/10', textClass: 'text-amber-400' },
  WAITINGCHILDRETIREMENT: { label: 'Queued · Waiting for child retirement', dotClass: 'bg-amber-400', bgClass: 'bg-amber-400/10', textClass: 'text-amber-400' },
  WAITINGRESOURCERECOVERY: { label: 'Queued · Waiting for resource recovery', dotClass: 'bg-amber-400', bgClass: 'bg-amber-400/10', textClass: 'text-amber-400' },
  WAITINGRUNTIMERECOVERY: { label: 'Queued · Waiting for runtime recovery', dotClass: 'bg-amber-400', bgClass: 'bg-amber-400/10', textClass: 'text-amber-400' },
  WAITINGWORKFLOWCONTINUATION: { label: 'Queued · Waiting for workflow continuation', dotClass: 'bg-amber-400', bgClass: 'bg-amber-400/10', textClass: 'text-amber-400' },
  READY: {
    label: 'Ready',
    dotClass: 'bg-emerald-400',
    bgClass: 'bg-emerald-400/10',
    textClass: 'text-emerald-400',
  },
  EXITED: {
    label: 'Exited',
    dotClass: 'bg-purple-400',
    bgClass: 'bg-purple-400/10',
    textClass: 'text-purple-400',
  },
  WORKFLOW_UNTRACKED: {
    label: 'Untracked',
    dotClass: 'bg-gray-400',
    bgClass: 'bg-gray-400/10',
    textClass: 'text-gray-400',
  },
  COMPLETED: {
    label: 'Provider Ready',
    dotClass: 'bg-purple-400',
    bgClass: 'bg-purple-400/10',
    textClass: 'text-purple-400',
  },
  WAITING_USER_ANSWER: {
    label: 'Awaiting Input',
    dotClass: 'bg-amber-400',
    bgClass: 'bg-amber-400/10',
    textClass: 'text-amber-400',
  },
  ERROR: {
    label: 'Error',
    dotClass: 'bg-red-400',
    bgClass: 'bg-red-400/10',
    textClass: 'text-red-400',
  },
}

const UNKNOWN_CONFIG: StatusStyle = {
  label: 'Unknown',
  dotClass: 'bg-gray-500',
  bgClass: 'bg-gray-500/10',
  textClass: 'text-gray-500',
}

export function StatusBadge({ status, workflowState }: { status: TerminalStatus, workflowState?: string | null }) {
  const [storedPrimary, providerDiagnostic] = typeof status === 'string' ? status.split('::', 2) : [status, undefined]
  const normalized = workflowState ? `WORKFLOW_${workflowState.toUpperCase()}` : (storedPrimary ? storedPrimary.toUpperCase() : null)
  const config = (normalized && STATUS_CONFIG[normalized]) || UNKNOWN_CONFIG

  if (normalized?.startsWith('WORKFLOW_') && providerDiagnostic) {
    const activity = providerDiagnostic.toUpperCase()
    const activityConfig = activity === 'PROCESSING' ? STATUS_CONFIG.PROCESSING : activity === 'QUEUED' ? STATUS_CONFIG.QUEUED : activity === 'EXITED' ? { ...STATUS_CONFIG.COMPLETED, label: 'Exited' } : STATUS_CONFIG[activity] || { ...STATUS_CONFIG.IDLE, label: 'Ready' }
    return <span data-status-badge className="inline-flex flex-wrap items-center gap-1.5"><span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full ${activityConfig.bgClass}`}><span className={`w-2 h-2 rounded-full ${activityConfig.dotClass} ${activityConfig.pulse ? 'animate-pulse' : ''}`} /><span className={`text-xs font-medium ${activityConfig.textClass}`}>{activityConfig.label}</span></span><span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full ${config.bgClass} ${config.textClass}`}><span className="text-[10px]">Workflow ·</span><span className="text-xs font-medium">{config.label}</span></span></span>
  }

  return (
    <span data-status-badge className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full ${config.bgClass}`}>
      <span className={`w-2 h-2 rounded-full ${config.dotClass} ${config.pulse ? 'animate-pulse' : ''}`} />
      <span className={`text-xs font-medium ${config.textClass}`}>{config.label}</span>
      {providerDiagnostic && <span className="text-[10px] text-gray-500">Provider {providerDiagnostic}</span>}
    </span>
  )
}
