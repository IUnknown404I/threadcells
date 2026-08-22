import { useEffect, useRef, useState } from 'react'
import { KeyRound, LockKeyhole, ShieldCheck } from 'lucide-react'
import { api, OperatorSessionStatus } from '../api'

export type OperatorAccess = {
  status: OperatorSessionStatus | null
  loading: boolean
  busy: boolean
  expired: boolean
  error: string
  unlock: (secret: string) => Promise<boolean>
  lock: () => Promise<void>
  refresh: () => Promise<void>
}

export function useOperatorAccess(): OperatorAccess {
  const [status, setStatus] = useState<OperatorSessionStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [expired, setExpired] = useState(false)
  const [error, setError] = useState('')
  const wasAuthenticated = useRef(false)

  const refresh = async () => {
    try {
      const next = await api.getOperatorSession()
      setExpired(wasAuthenticated.current && next.configured && !next.authenticated)
      wasAuthenticated.current = next.authenticated
      setStatus(next)
      setError('')
    } catch (reason: any) {
      setError(reason.message || 'Operator authorization status is unavailable.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void refresh()
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void refresh()
    }, 15_000)
    return () => window.clearInterval(timer)
  }, [])

  const unlock = async (secret: string) => {
    setBusy(true)
    setError('')
    try {
      await api.createOperatorSession(secret)
      await refresh()
      return true
    } catch (reason: any) {
      setError(reason.message || 'Operator authentication failed.')
      await refresh()
      return false
    } finally {
      setBusy(false)
    }
  }

  const lock = async () => {
    setBusy(true)
    setError('')
    wasAuthenticated.current = false
    setExpired(false)
    try {
      await api.deleteOperatorSession()
      await refresh()
    } catch (reason: any) {
      setError(reason.message || 'Could not revoke operator authorization.')
    } finally {
      setBusy(false)
    }
  }

  return { status, loading, busy, expired, error, unlock, lock, refresh }
}

export function OperatorAccessCard({ access, compact = false }: { access: OperatorAccess; compact?: boolean }) {
  const [secret, setSecret] = useState('')
  const submit = async () => {
    const value = secret
    if (value.length < 5) return
    setSecret('')
    await access.unlock(value)
  }
  const status = access.status
  const minutes = status ? Math.max(1, Math.ceil(status.expires_in_seconds / 60)) : 0

  return <section aria-labelledby="operator-access-heading" className={`rounded-xl border ${compact ? 'p-3' : 'p-4'} ${status?.authenticated ? 'border-emerald-700/50 bg-emerald-950/20' : 'border-gray-700/60 bg-gray-800/60'}`}>
    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="flex min-w-0 gap-3">
        <div className={`mt-0.5 rounded-lg p-2 ${status?.authenticated ? 'bg-emerald-500/15 text-emerald-300' : 'bg-gray-900 text-gray-400'}`}>{status?.authenticated ? <ShieldCheck size={18} aria-hidden="true" /> : <LockKeyhole size={18} aria-hidden="true" />}</div>
        <div>
          <h2 id="operator-access-heading" className="text-sm font-semibold text-gray-100">Operator changes</h2>
          {access.loading ? <p className="mt-1 text-xs text-gray-500">Checking authorization…</p> : !status?.configured ? status?.configuration_state === 'invalid' ? <p className="mt-1 text-xs leading-5 text-amber-200">A verifier reference is present but cannot be used safely. Check that it is a valid canonical verifier owned by a distinct OS principal and is not group/world writable.</p> : <p className="mt-1 text-xs leading-5 text-amber-200">Not configured. An OS operator must provision a verifier and set <code className="rounded bg-gray-950 px-1 text-amber-100">{status?.verifier_reference || 'THREADCELLS_OPERATOR_VERIFIER_FILE'}</code> on the ThreadCells service.</p> : status.authenticated ? <p className="mt-1 text-xs text-emerald-200">Unlocked for this browser for about {minutes} minute{minutes === 1 ? '' : 's'}. The server keeps the authorization in an HttpOnly cookie.</p> : access.expired ? <p className="mt-1 text-xs leading-5 text-amber-200">Authorization expired. Unlock operator changes again to continue protected Settings work.</p> : <p className="mt-1 text-xs leading-5 text-gray-400">Locked. Enter the operator secret once to authorize protected Settings changes for up to {Math.round(status.session_ttl_seconds / 60)} minutes.</p>}
          <a href="/docs/resource-model#operator-authorization" className="mt-2 inline-flex min-h-9 items-center text-xs text-emerald-300 underline underline-offset-2">Setup and security details</a>
        </div>
      </div>
      {status?.authenticated && <button type="button" disabled={access.busy} onClick={() => void access.lock()} className="min-h-11 shrink-0 rounded-lg border border-emerald-700/50 px-3 text-xs text-emerald-200 disabled:opacity-40">Lock now</button>}
    </div>
    {status?.configured && !status.authenticated && <div className="mt-3 flex flex-col gap-2 sm:flex-row">
      <label className="min-w-0 flex-1 text-xs text-gray-400">Operator secret
        <input aria-label="Operator secret" type="password" autoComplete="new-password" minLength={5} maxLength={4096} value={secret} onChange={event => setSecret(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') void submit() }} className="mt-1 min-h-11 w-full rounded-lg border border-gray-700 bg-gray-950 px-3 text-sm text-gray-100 focus:border-emerald-500 focus:outline-none" />
      </label>
      <button type="button" disabled={secret.length < 5 || access.busy} onClick={() => void submit()} className="min-h-11 self-end rounded-lg bg-emerald-600 px-4 text-sm font-medium text-white disabled:opacity-40"><span className="inline-flex items-center gap-2"><KeyRound size={15} aria-hidden="true" />{access.busy ? 'Unlocking…' : 'Unlock operator changes'}</span></button>
    </div>}
    <p className="mt-2 text-[11px] leading-4 text-gray-500">Minimum 5 characters; a longer generated secret is strongly recommended. The secret is submitted only to the local server for verification. ThreadCells does not store it in browser persistence, prefill it, log it, or include it in exports.</p>
    {access.error && <p role="alert" className="mt-3 text-xs text-red-300">{access.error}</p>}
  </section>
}
