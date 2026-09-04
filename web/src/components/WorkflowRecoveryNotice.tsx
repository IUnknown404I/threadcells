import { Clock3 } from 'lucide-react'
import { AgentSummary, RecoveryTakeoverCapability } from '../api'
import { useI18n, type TranslationKey } from '../i18n'
import { statusTranslationKey } from './StatusBadge'

const AUTHORITY_REASON_KEYS: Record<string, TranslationKey> = {
  RECOVERY_HEALTHY_RUNTIME_ACTIVE: 'agents.queueRecovery.runtimeActive',
  RECOVERY_PROVIDER_EXECUTION_ACTIVE: 'agents.queueRecovery.runtimeActive',
  RECOVERY_RUNTIME_OPERATION_ACTIVE: 'agents.queueRecovery.runtimeActive',
  RECOVERY_CHILD_WORK_ACTIVE: 'agents.queueRecovery.authorityPending',
  RECOVERY_PRIVILEGED_EFFECT_UNRESOLVED: 'agents.queueRecovery.authorityPending',
  RECOVERY_GENUINE_OWNER_GATE: 'agents.queueRecovery.ownerDecision',
}

export function WorkflowRecoveryNotice({
  agent,
  capability,
}: {
  agent: AgentSummary
  capability?: RecoveryTakeoverCapability
}) {
  const { t } = useI18n()
  const queued = agent.queued_task_count || 0
  if (agent.workflow_status !== 'open' || queued < 1) return null

  const agentStatus = t(statusTranslationKey(
    agent.lifecycle === 'exited' || agent.lifecycle === 'recovery_fenced'
      ? agent.lifecycle
      : agent.execution_state || agent.activity,
  ))
  const reasonKey = capability?.reason_code
    ? AUTHORITY_REASON_KEYS[capability.reason_code] || 'agents.queueRecovery.safeGate'
    : null

  return <div data-testid={`workflow-queue-recovery-${agent.id}`} role="status" className="rounded-lg border border-blue-700/40 bg-blue-950/20 p-3 text-xs text-blue-100">
    <div className="flex items-start gap-2">
      <Clock3 size={14} className="mt-0.5 shrink-0 text-blue-300"/>
      <div>
        <p className="font-medium">{t('agents.queueRecovery.summary', {
          agent: agentStatus,
          workflow: t('status.workflow.open'),
          count: queued,
        })}</p>
        <p className="mt-1 text-blue-200/80">{t('agents.queueRecovery.automatic')}</p>
        {capability?.eligible === false && reasonKey && (
          <p className="mt-1 text-blue-200/70">{t(reasonKey)}</p>
        )}
      </div>
    </div>
  </div>
}
