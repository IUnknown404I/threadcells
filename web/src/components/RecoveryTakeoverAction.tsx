import { useEffect, useState } from 'react'
import { ShieldAlert, X } from 'lucide-react'
import { AgentProfileInfo, AgentSummary, ProviderInfo, RecoveryTakeoverCapability, RecoveryTakeoverPreview, api } from '../api'
import { useI18n } from '../i18n'
import { providerIsAvailable, providerSelectOption } from '../providerAvailability'
import { sessionDisplayName } from '../sessionDisplayName'
import { useStore } from '../store'
import { lifecycleBadgeStatus, statusTranslationKey } from './StatusBadge'
import { CustomSelect } from './CustomSelect'
import { ProfilePicker } from './ProfilePicker'

const FALLBACK_PROVIDERS = ['kiro_cli', 'claude_code', 'q_cli', 'codex', 'gemini_cli', 'kimi_cli', 'copilot_cli']
const UNAVAILABLE_PROVIDER_FALLBACK = FALLBACK_PROVIDERS.map(name => ({
  name,
  binary: null,
  installed: false,
  available: false,
  availability: 'UNKNOWN' as const,
}))

function defaultProvider(providers: ProviderInfo[]): string {
  return providers.find(provider => provider.name === 'codex' && providerIsAvailable(provider))?.name
    || providers.find(providerIsAvailable)?.name
    || 'codex'
}

export function RecoveryTakeoverAction({
  agent,
  capability,
  onCompleted,
  className,
}: {
  agent: AgentSummary
  capability?: RecoveryTakeoverCapability
  onCompleted: () => void
  className: string
}) {
  const { t } = useI18n()
  const { showSnackbar } = useStore()
  const [open, setOpen] = useState(false)
  const [preview, setPreview] = useState<RecoveryTakeoverPreview | null>(null)
  const [operatorSecret, setOperatorSecret] = useState('')
  const [profiles, setProfiles] = useState<AgentProfileInfo[]>([])
  const [providers, setProviders] = useState<ProviderInfo[]>([])
  const [profile, setProfile] = useState('critical_sol_xhigh_owner')
  const [provider, setProvider] = useState('codex')
  const [confirmed, setConfirmed] = useState(false)
  const [inspecting, setInspecting] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    let disposed = false
    void Promise.all([api.listProfiles(), api.listProviders()]).then(([nextProfiles, nextProviders]) => {
      if (disposed) return
      setProfiles(nextProfiles)
      setProviders(nextProviders)
      setProvider(defaultProvider(nextProviders))
    }).catch(() => {
      if (!disposed) {
        setProfiles([])
        setProviders([])
      }
    })
    return () => { disposed = true }
  }, [open])

  useEffect(() => {
    if (capability?.eligible === true) return
    // A reconciliation can revoke a previously visible capability while the
    // dialog is open. Drop every client-side preview/confirmation so a later
    // eligible generation cannot revive stale authority in the UI.
    setOpen(false)
    setPreview(null)
    setOperatorSecret('')
    setConfirmed(false)
    setError(null)
  }, [capability?.eligible])

  if (capability?.eligible !== true) return null

  const close = () => {
    if (submitting) return
    setOpen(false)
    setPreview(null)
    setOperatorSecret('')
    setConfirmed(false)
    setError(null)
  }

  const openDialog = () => {
    setPreview(null)
    setOperatorSecret('')
    setProfile('critical_sol_xhigh_owner')
    setProvider(defaultProvider(providers))
    setConfirmed(false)
    setError(null)
    setOpen(true)
  }

  const inspect = async () => {
    if (!operatorSecret || inspecting) return
    setInspecting(true)
    setError(null)
    try {
      await api.createOperatorSession(operatorSecret)
      setPreview(await api.getRecoveryTakeoverPreview(agent.id))
    } catch (reason: any) {
      setError(reason.message || t('agents.recoverFailed'))
    } finally {
      setOperatorSecret('')
      setInspecting(false)
    }
  }

  const submit = async () => {
    if (!preview?.eligible || !preview.terminal || !confirmed || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      const ownerGrant = await api.createXHighGrant({
        agent_profile: profile,
        provider,
        project_id: preview.terminal.project_id,
        launch_mode: 'recovery_takeover',
        target_terminal_id: agent.id,
        expected_authority_generation: preview.terminal.writer_authority_generation,
        expected_runtime_generation: preview.terminal.runtime_generation,
        confirmed: true,
      })
      const takeover = await api.createRecoveryTakeover(agent.id, {
        request_id: crypto.randomUUID(),
        expected_authority_generation: preview.terminal.writer_authority_generation,
        expected_runtime_generation: preview.terminal.runtime_generation,
        agent_profile: profile,
        provider,
        owner_grant_launch_id: ownerGrant.launch_id,
      }, ownerGrant)
      if (takeover.state !== 'completed') {
        throw new Error(t('agents.recoverPending', { state: takeover.state }))
      }
      showSnackbar({ type: 'success', message: t('agents.recoverSucceeded', { id: takeover.new_terminal_id }) })
      setOpen(false)
      onCompleted()
    } catch (reason: any) {
      setError(reason.message || t('agents.recoverFailed'))
    } finally {
      setSubmitting(false)
    }
  }

  return <>
    <button type="button" onClick={openDialog} className={className} title={t('agents.recoverTitle')}>
      <ShieldAlert size={14}/>{t('agents.recoverTakeover')}
    </button>
    {open && <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-6">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={close}/>
      <div role="dialog" aria-modal="true" aria-labelledby="recovery-takeover-title" className="relative max-h-[94vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-indigo-700/60 bg-gray-900 shadow-2xl shadow-black/60">
        <div className="flex items-start justify-between gap-4 border-b border-gray-700/60 p-4 sm:p-5">
          <div className="min-w-0"><h3 id="recovery-takeover-title" className="text-base font-semibold text-gray-100">{t('agents.recoverTitle')}</h3><p className="mt-1 text-xs text-gray-400">{t('agents.recoverHelp')}</p></div>
          <button type="button" aria-label={t('common.close')} disabled={submitting} onClick={close} className="min-h-11 min-w-11 rounded-lg text-gray-400 hover:bg-gray-800 hover:text-gray-100 disabled:opacity-40"><X className="mx-auto" size={18}/></button>
        </div>
        <div className="space-y-4 p-4 sm:p-5">
          <dl className="grid gap-3 rounded-xl border border-gray-700/50 bg-gray-950/50 p-3 text-xs sm:grid-cols-2">
            <div><dt className="text-gray-500">{t('agents.terminalId')}</dt><dd className="mt-1 break-all font-mono text-gray-200">{agent.id}</dd></div>
            <div><dt className="text-gray-500">{t('statistics.session')}</dt><dd className="mt-1 break-all text-gray-200">{sessionDisplayName(agent.session_name)}</dd></div>
            <div><dt className="text-gray-500">{t('common.project')}</dt><dd className="mt-1 break-all text-gray-200">{agent.project_name || agent.projectId}</dd></div>
            <div><dt className="text-gray-500">{t('agents.status')}</dt><dd className="mt-1 text-gray-200">{t(statusTranslationKey(lifecycleBadgeStatus(agent.workflow_state, agent.activity, agent.lifecycle, agent.execution_state)))}</dd></div>
            <div className="sm:col-span-2"><dt className="text-gray-500">{t('agents.recoverWorktree')}</dt><dd className="mt-1 break-all font-mono text-gray-200">{agent.launch_worktree}</dd></div>
          </dl>

          {!preview && <div className="rounded-xl border border-amber-700/50 bg-amber-950/20 p-3">
            <label className="block text-xs text-amber-200/80">{t('agents.operatorSecret')}<input type="password" autoComplete="current-password" value={operatorSecret} onChange={event => setOperatorSecret(event.target.value)} className="mt-1 min-h-11 w-full rounded-lg border border-amber-700/60 bg-gray-950 px-3 text-sm text-gray-100 focus:border-amber-400 focus:outline-none"/></label>
            <button type="button" onClick={inspect} disabled={!operatorSecret || inspecting} className="mt-3 min-h-11 w-full rounded-lg bg-indigo-700 px-4 text-sm font-medium text-white hover:bg-indigo-600 disabled:opacity-40">{inspecting ? t('common.loading') : t('agents.recoverInspect')}</button>
          </div>}

          {preview && <>
            <div role="status" className={`rounded-xl border p-3 text-sm ${preview.eligible ? 'border-emerald-700/50 bg-emerald-950/20 text-emerald-200' : 'border-red-700/50 bg-red-950/30 text-red-200'}`}>{preview.eligible ? t('agents.recoverEligible') : t('agents.recoverBlocked', { reason: preview.reason_code || t('status.unknown') })}</div>
            <div className="rounded-xl border border-gray-700/50 bg-gray-950/50 p-3 text-sm"><p className="text-gray-400">{t('agents.recoverWorktree')}</p><p className="mt-1 text-gray-200">{preview.worktree?.state === 'dirty' ? t('agents.recoverDirty') : preview.worktree?.state === 'clean' ? t('agents.recoverClean') : t('agents.recoverUnknown')}</p></div>
            {preview.eligible && <>
              <div className="grid gap-3 sm:grid-cols-2">
                <div><label className="mb-1 block text-xs text-gray-500">{t('common.provider')}</label><CustomSelect value={provider} onChange={setProvider} placeholder={t('common.selectProvider')} options={(providers.length > 0 ? providers : UNAVAILABLE_PROVIDER_FALLBACK).map(item => providerSelectOption(item, t))}/></div>
                <div><label className="mb-1 block text-xs text-gray-500">{t('agents.agentProfile')}</label><ProfilePicker value={profile} onChange={setProfile} profiles={profiles.filter(item => item.owner_authorization_required)}/></div>
              </div>
              <div className="rounded-xl border border-red-700/50 bg-red-950/20 p-3 text-sm text-red-100"><p>{t('agents.recoverConsequence')}</p><label className="mt-3 flex min-h-11 items-center gap-2 text-xs"><input aria-label={t('agents.recoverConfirmAria')} type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)}/>{t('agents.recoverConfirm')}</label></div>
              <button type="button" onClick={submit} disabled={!confirmed || !profile || !provider || submitting} className="min-h-11 w-full rounded-lg bg-red-700 px-4 text-sm font-semibold text-white hover:bg-red-600 disabled:opacity-40">{submitting ? t('agents.recovering') : t('agents.recoverSubmit')}</button>
            </>}
          </>}
          {error && <div role="alert" className="whitespace-pre-line rounded-lg border border-red-700/50 bg-red-950/40 px-3 py-2 text-sm text-red-300">{error}</div>}
        </div>
      </div>
    </div>}
  </>
}
