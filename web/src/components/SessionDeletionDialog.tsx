import type { SessionDeletionBlocker, SessionDeletionPreflight } from '../api'
import { useI18n, type TranslationKey } from '../i18n'
import { ConfirmModal } from './ConfirmModal'

const CATEGORY_KEYS: Record<string, TranslationKey> = {
  unfinished_workflows: 'sessionDeletion.category.workflows',
  queued_work: 'sessionDeletion.category.queued',
  pending_delivery: 'sessionDeletion.category.delivery',
  child_assignments: 'sessionDeletion.category.assignments',
  workflow_effects: 'sessionDeletion.category.effects',
  historical_indeterminate_effects: 'sessionDeletion.category.historicalUnknown',
  provider_execution: 'sessionDeletion.category.provider',
  writer_authority: 'sessionDeletion.category.writer',
  recovery_authority: 'sessionDeletion.category.recovery',
  runtime_authority: 'sessionDeletion.category.runtime',
  workspace_authority: 'sessionDeletion.category.workspace',
  external_delivery: 'sessionDeletion.category.externalDelivery',
  external_assignments: 'sessionDeletion.category.externalAssignments',
  plan_limit: 'sessionDeletion.category.planLimit',
  session_identity: 'sessionDeletion.category.sessionIdentity',
}

const REASON_KEYS: Record<string, TranslationKey> = {
  SESSION_RUNTIME_ACTIVE: 'sessionDeletion.reason.runtimeActive',
  SESSION_RUNTIME_AUTHORITY_UNPROVEN: 'sessionDeletion.reason.runtimeUnproven',
  SESSION_RECOVERY_EVIDENCE_PROTECTED: 'sessionDeletion.reason.recoveryEvidence',
  RUNTIME_DEATH_UNCONFIRMED: 'sessionDeletion.reason.runtimeUnproven',
  RUNTIME_RECOVERY_OPERATION_ACTIVE: 'sessionDeletion.reason.recoveryActive',
  PROVIDER_EXECUTION_ACTIVE: 'sessionDeletion.reason.providerActive',
  WRITER_LEASE_ACTIVE: 'sessionDeletion.reason.writerActive',
  RECOVERY_TAKEOVER_ACTIVE: 'sessionDeletion.reason.recoveryActive',
  RECOVERY_RECONCILIATION_REQUIRED: 'sessionDeletion.reason.recoveryRequired',
  INDETERMINATE_EFFECT: 'sessionDeletion.reason.indeterminateEffect',
  CLAIMED_EFFECT: 'sessionDeletion.reason.claimedEffect',
  PROVIDER_RECONNECT_ACTIVE: 'sessionDeletion.reason.reconnectActive',
  PROVIDER_RECONNECT_ATTEMPT_ACTIVE: 'sessionDeletion.reason.reconnectActive',
  PROVIDER_EXECUTION_STATE_UNSETTLED: 'sessionDeletion.reason.providerUnsettled',
  DIRECT_RESULT_ALREADY_CLAIMED: 'sessionDeletion.reason.resultClaimed',
  CROSS_SESSION_DELIVERY: 'sessionDeletion.reason.externalDelivery',
  CROSS_SESSION_ASSIGNMENT: 'sessionDeletion.reason.externalAssignment',
  WORKSPACE_STATE_NOT_RETIRABLE: 'sessionDeletion.reason.workspaceProtected',
  WORKSPACE_RETIREMENT_STATE_CONFLICT: 'sessionDeletion.reason.workspaceProtected',
  TERMINAL_WORKSPACE_CLEANUP_AUTHORITY_MISSING: 'sessionDeletion.reason.workspaceProtected',
  CANCELLATION_PLAN_TOO_LARGE: 'sessionDeletion.reason.planTooLarge',
  SESSION_IDENTITY_CHANGED: 'sessionDeletion.reason.sessionIdentity',
}

function blockerLabel(blocker: SessionDeletionBlocker, t: (key: TranslationKey, params?: Record<string, string | number>) => string) {
  return t(CATEGORY_KEYS[blocker.category] || 'sessionDeletion.category.other', { count: blocker.count })
}

export function SessionDeletionDialog({
  open,
  sessionName,
  statusLabel,
  preflight,
  loading,
  onConfirm,
  onCancel,
}: {
  open: boolean
  sessionName: string
  statusLabel: string
  preflight: SessionDeletionPreflight | null
  loading: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  const { t } = useI18n()
  if (!preflight) return null
  const retirement = preflight.deletion_mode === 'eligible_with_historical_indeterminate_retirement'
  const unsafe = preflight.deletion_mode === 'blocked_live_or_unsafe_authority'
  const cancellable = preflight.deletion_mode === 'eligible_with_cancellable_work'
  const message = unsafe
    ? t('sessionDeletion.unsafeMessage')
    : retirement
      ? t('sessionDeletion.historicalUnknownMessage')
    : cancellable
      ? t('sessionDeletion.unfinishedMessage')
      : t(preflight.requires_dirty_confirmation ? 'sessionDeletion.dirtyMessage' : 'sessionDeletion.normalMessage')
  const reasonCodes = [...new Set(preflight.unsafe_blockers.flatMap(blocker => blocker.reason_codes))]
  const counts = unsafe
    ? preflight.unsafe_blockers
    : retirement
      ? [...preflight.historical_indeterminate_blockers, ...preflight.cancellable_blockers]
      : preflight.cancellable_blockers

  return (
    <ConfirmModal
      open={open}
      title={t('sessionDeletion.title')}
      message={message}
      details={[
        { label: t('sessionDeletion.session'), value: sessionName },
        { label: t('sessionDeletion.status'), value: statusLabel },
      ]}
      confirmLabel={t(retirement ? 'sessionDeletion.retireAndDelete' : cancellable ? 'sessionDeletion.cancelAndDelete' : 'sessionDeletion.delete')}
      cancelLabel={t(unsafe ? 'common.close' : 'common.cancel')}
      variant="danger"
      loading={loading}
      showConfirm={!unsafe}
      onConfirm={onConfirm}
      onCancel={onCancel}
    >
      {counts.length > 0 && (
        <div data-testid="session-deletion-counts" className="rounded-lg border border-gray-700/50 bg-gray-800/50 p-3">
          <div className="space-y-1.5 text-sm" aria-live="polite">
            {preflight.current_queue_count > 0 && (
              <div className="flex items-center justify-between gap-4">
                <span className="text-gray-400">{t('sessionDeletion.currentQueue')}</span>
                <span className="font-mono text-gray-200">{preflight.current_queue_count}</span>
              </div>
            )}
            {retirement && <>
              <div className="flex items-center justify-between gap-4">
                <span className="text-gray-400">{t('sessionDeletion.activeAgents')}</span>
                <span className="font-mono text-gray-200">{preflight.active_runtime_count}</span>
              </div>
              <div className="flex items-center justify-between gap-4">
                <span className="text-gray-400">{t('sessionDeletion.activeExecutions')}</span>
                <span className="font-mono text-gray-200">{preflight.active_execution_count}</span>
              </div>
            </>}
            {counts.map(blocker => (
              <div key={`${blocker.disposition}:${blocker.category}`} className="flex items-center justify-between gap-4">
                <span className="text-gray-400">{blockerLabel(blocker, t)}</span>
                <span className="font-mono text-gray-200">{blocker.count}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      {preflight.requires_cancellation_confirmation && (
        <p className="text-sm leading-5 text-gray-300">
          {t('sessionDeletion.cancelCopy')}
        </p>
      )}
      {retirement && (
        <div data-testid="session-deletion-historical-indeterminate" role="status" className="rounded-lg border border-amber-700/40 bg-amber-950/20 p-3">
          <p className="text-sm leading-5 text-amber-100">{t('sessionDeletion.historicalUnknownWarning')}</p>
          <p className="mt-2 text-sm leading-5 text-gray-300">{t('sessionDeletion.permanentDeleteCopy')}</p>
        </div>
      )}
      {unsafe && (
        <div data-testid="session-deletion-unsafe" role="status" className="rounded-lg border border-amber-700/40 bg-amber-950/20 p-3">
          <p className="text-sm font-medium text-amber-300">{t('sessionDeletion.unsafeTitle')}</p>
          <ul className="mt-2 space-y-1 text-sm text-gray-300">
            {reasonCodes.map(reason => (
              <li key={reason}>• {t(REASON_KEYS[reason] || 'sessionDeletion.reason.unknown')}</li>
            ))}
          </ul>
        </div>
      )}
      {preflight.requires_dirty_confirmation && !unsafe && (
        <div className="rounded-lg border border-red-800/40 bg-red-950/20 p-3 text-sm text-red-200">
          {t('sessionDeletion.dirtyCounts', {
            modified: preflight.modified_files,
            untracked: preflight.untracked_files,
          })}
        </div>
      )}
    </ConfirmModal>
  )
}
