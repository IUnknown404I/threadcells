import { useI18n, type TranslationKey } from '../i18n'

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
  labelKey: TranslationKey
  dotClass: string
  bgClass: string
  textClass: string
  pulse?: boolean
}

const STATUS_CONFIG: Record<string, StatusStyle> = {
  WORKFLOW_COMPLETED: {
    labelKey: 'status.workflow.completed',
    dotClass: 'bg-purple-400',
    bgClass: 'bg-purple-400/10',
    textClass: 'text-purple-400',
  },
  WORKFLOW_ACTIVE: { labelKey: 'status.workflow.active', dotClass: 'bg-blue-400', bgClass: 'bg-blue-400/10', textClass: 'text-blue-400', pulse: true },
  WORKFLOW_OPEN: { labelKey: 'status.workflow.open', dotClass: 'bg-blue-400', bgClass: 'bg-blue-400/10', textClass: 'text-blue-400' },
  WORKFLOW_WAITING: { labelKey: 'status.workflow.waiting', dotClass: 'bg-amber-400', bgClass: 'bg-amber-400/10', textClass: 'text-amber-400' },
  WORKFLOW_RECOVERABLE: { labelKey: 'status.workflow.waiting', dotClass: 'bg-amber-400', bgClass: 'bg-amber-400/10', textClass: 'text-amber-400' },
  WORKFLOW_RESULT_READY: { labelKey: 'status.workflow.resultReady', dotClass: 'bg-sky-400', bgClass: 'bg-sky-400/10', textClass: 'text-sky-400' },
  WORKFLOW_OWNER_GATE: { labelKey: 'status.workflow.ownerGate', dotClass: 'bg-amber-400', bgClass: 'bg-amber-400/10', textClass: 'text-amber-400' },
  WORKFLOW_CANCELLED: { labelKey: 'status.workflow.cancelled', dotClass: 'bg-red-400', bgClass: 'bg-red-400/10', textClass: 'text-red-400' },
  WORKFLOW_INCOMPLETE: { labelKey: 'status.workflow.incomplete', dotClass: 'bg-red-400', bgClass: 'bg-red-400/10', textClass: 'text-red-400' },
  WORKFLOW_FAILED: { labelKey: 'status.workflow.failed', dotClass: 'bg-red-400', bgClass: 'bg-red-400/10', textClass: 'text-red-400' },
  IDLE: {
    labelKey: 'status.idle',
    dotClass: 'bg-emerald-400',
    bgClass: 'bg-emerald-400/10',
    textClass: 'text-emerald-400',
  },
  PROCESSING: {
    labelKey: 'status.processing',
    dotClass: 'bg-blue-400',
    bgClass: 'bg-blue-400/10',
    textClass: 'text-blue-400',
    pulse: true,
  },
  QUEUED: {
    labelKey: 'status.queued',
    dotClass: 'bg-amber-400',
    bgClass: 'bg-amber-400/10',
    textClass: 'text-amber-400',
  },
  WAITINGPROVIDERSLOT: { labelKey: 'status.waitingProvider', dotClass: 'bg-amber-400', bgClass: 'bg-amber-400/10', textClass: 'text-amber-400' },
  WAITINGCHILDRETIREMENT: { labelKey: 'status.waitingChildRetirement', dotClass: 'bg-amber-400', bgClass: 'bg-amber-400/10', textClass: 'text-amber-400' },
  WAITINGRESOURCERECOVERY: { labelKey: 'status.waitingResourceRecovery', dotClass: 'bg-amber-400', bgClass: 'bg-amber-400/10', textClass: 'text-amber-400' },
  WAITINGRUNTIMERECOVERY: { labelKey: 'status.waitingRuntimeRecovery', dotClass: 'bg-amber-400', bgClass: 'bg-amber-400/10', textClass: 'text-amber-400' },
  WAITINGWORKFLOWCONTINUATION: { labelKey: 'status.waitingWorkflowContinuation', dotClass: 'bg-amber-400', bgClass: 'bg-amber-400/10', textClass: 'text-amber-400' },
  READY: {
    labelKey: 'status.ready',
    dotClass: 'bg-emerald-400',
    bgClass: 'bg-emerald-400/10',
    textClass: 'text-emerald-400',
  },
  EXITED: {
    labelKey: 'status.exited',
    dotClass: 'bg-purple-400',
    bgClass: 'bg-purple-400/10',
    textClass: 'text-purple-400',
  },
  WORKFLOW_UNTRACKED: {
    labelKey: 'status.untracked',
    dotClass: 'bg-gray-400',
    bgClass: 'bg-gray-400/10',
    textClass: 'text-gray-400',
  },
  COMPLETED: {
    labelKey: 'status.providerReady',
    dotClass: 'bg-purple-400',
    bgClass: 'bg-purple-400/10',
    textClass: 'text-purple-400',
  },
  WAITING_USER_ANSWER: {
    labelKey: 'status.awaitingInput',
    dotClass: 'bg-amber-400',
    bgClass: 'bg-amber-400/10',
    textClass: 'text-amber-400',
  },
  ERROR: {
    labelKey: 'status.error',
    dotClass: 'bg-red-400',
    bgClass: 'bg-red-400/10',
    textClass: 'text-red-400',
  },
}

const UNKNOWN_CONFIG: StatusStyle = {
  labelKey: 'status.unknown',
  dotClass: 'bg-gray-500',
  bgClass: 'bg-gray-500/10',
  textClass: 'text-gray-500',
}

export function statusTranslationKey(status: string | null | undefined): TranslationKey {
  const normalized = status?.toUpperCase()
  return (normalized && (STATUS_CONFIG[normalized] || STATUS_CONFIG[normalized.replace(/_/g, '')])?.labelKey) || UNKNOWN_CONFIG.labelKey
}

export function sessionStatusTranslationKey(status: string | null | undefined): TranslationKey {
  return status === 'active' ? 'status.session.active'
    : status === 'history' ? 'status.session.history'
    : statusTranslationKey(status)
}

export function resourceStateTranslationKey(state: string | null | undefined): TranslationKey {
  const normalized = state?.toLowerCase()
  if (normalized === 'green' || normalized === 'yellow' || normalized === 'red' || normalized === 'critical') {
    return `status.resource.${normalized}` as TranslationKey
  }
  return 'status.unknown'
}

export function StatusBadge({ status, workflowState }: { status: TerminalStatus, workflowState?: string | null }) {
  const { t } = useI18n()
  const [storedPrimary, providerDiagnostic] = typeof status === 'string' ? status.split('::', 2) : [status, undefined]
  const normalized = workflowState ? `WORKFLOW_${workflowState.toUpperCase()}` : (storedPrimary ? storedPrimary.toUpperCase() : null)
  const config = (normalized && STATUS_CONFIG[normalized]) || UNKNOWN_CONFIG

  if (normalized?.startsWith('WORKFLOW_') && providerDiagnostic) {
    const activity = providerDiagnostic.toUpperCase()
    const activityConfig = activity === 'PROCESSING' ? STATUS_CONFIG.PROCESSING : activity === 'QUEUED' ? STATUS_CONFIG.QUEUED : activity === 'EXITED' ? { ...STATUS_CONFIG.COMPLETED, labelKey: 'status.exited' as TranslationKey } : STATUS_CONFIG[activity] || { ...STATUS_CONFIG.IDLE, labelKey: 'status.ready' as TranslationKey }
    return <span data-status-badge className="inline-flex flex-wrap items-center gap-1.5"><span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full ${activityConfig.bgClass}`}><span className={`w-2 h-2 rounded-full ${activityConfig.dotClass} ${activityConfig.pulse ? 'animate-pulse' : ''}`} /><span className={`text-xs font-medium ${activityConfig.textClass}`}>{t(activityConfig.labelKey)}</span></span><span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full ${config.bgClass} ${config.textClass}`}><span className="text-[10px]">{t('common.workflowPrefix')}</span><span className="text-xs font-medium">{t(config.labelKey)}</span></span></span>
  }

  return (
    <span data-status-badge className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full ${config.bgClass}`}>
      <span className={`w-2 h-2 rounded-full ${config.dotClass} ${config.pulse ? 'animate-pulse' : ''}`} />
      <span className={`text-xs font-medium ${config.textClass}`}>{t(config.labelKey)}</span>
      {providerDiagnostic && <span className="text-[10px] text-gray-500">{t('common.providerPrefix')} {providerDiagnostic}</span>}
    </span>
  )
}
