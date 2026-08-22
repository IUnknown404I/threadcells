import { useEffect, useState } from 'react'
import { CheckCircle2, Radio, Send, ShieldCheck } from 'lucide-react'
import { api, TelegramSettings as TelegramSettingsState } from '../api'
import { ConfirmModal } from './ConfirmModal'
import { OperatorAccessCard, useOperatorAccess } from './OperatorAccess'

const RESULT_LABELS: Record<string, string> = {
  connection_ok: 'Connection check passed',
  connection_failed: 'Connection check failed',
  test_sent: 'Test notification sent',
  test_failed: 'Test notification failed',
  not_configured: 'Configuration incomplete',
}

export function TelegramSettings() {
  const access = useOperatorAccess()
  const [settings, setSettings] = useState<TelegramSettingsState | null>(null)
  const [enabled, setEnabled] = useState(false)
  const [chatId, setChatId] = useState('')
  const [threadId, setThreadId] = useState('')
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState<'save' | 'check' | 'test' | 'clear' | null>(null)
  const [confirmClear, setConfirmClear] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const apply = (value: TelegramSettingsState) => {
    setSettings(value)
    setEnabled(value.enabled)
    setChatId(value.chat_id || '')
    setThreadId(value.message_thread_id ? String(value.message_thread_id) : '')
  }

  const load = async () => {
    try { apply(await api.getTelegramSettings()) }
    catch (reason: any) { setError(reason.message || 'Could not load Telegram settings') }
  }

  useEffect(() => { void load() }, [])

  const run = async (kind: 'save' | 'check' | 'test' | 'clear') => {
    setError(''); setNotice(''); setBusy(kind)
    try {
      if (kind === 'save') {
        if (enabled && !chatId.trim()) throw new Error('Enter a Telegram chat ID before enabling notifications.')
        if (enabled && !settings?.token_configured && !token.trim()) throw new Error('Enter a Telegram bot token before enabling notifications.')
        const next = await api.updateTelegramSettings({
          enabled,
          chat_id: chatId.trim() || null,
          message_thread_id: threadId.trim() ? Number(threadId) : null,
          bot_token: token.trim() || null,
          clear_bot_token: false,
        })
        apply(next); setToken(''); setNotice('Telegram settings saved.')
      } else if (kind === 'check') {
        const result = await api.checkTelegramConnection()
        setNotice(result.ok ? 'Telegram connection check passed.' : 'Telegram connection check failed safely.')
        await load()
      } else if (kind === 'test') {
        const result = await api.sendTelegramTest()
        setNotice(result.ok ? 'Test notification sent.' : 'Test notification was not sent.')
        await load()
      } else {
        const next = await api.updateTelegramSettings({
          enabled: false,
          chat_id: chatId.trim() || null,
          message_thread_id: threadId.trim() ? Number(threadId) : null,
          bot_token: null,
          clear_bot_token: true,
        })
        apply(next); setToken(''); setNotice('Telegram bot token cleared and notifications disabled.')
      }
    } catch (reason: any) {
      setError(reason.message || `Telegram ${kind} failed`)
      await access.refresh()
    } finally { setBusy(null) }
  }

  if (!settings) return <p className="py-8 text-center text-sm text-gray-400">Loading Telegram settings…</p>
  const configured = settings.token_configured && Boolean(settings.chat_id)
  const statusLabel = settings.configuration_state === 'enabled'
    ? 'Enabled'
    : settings.configuration_state === 'disabled'
      ? 'Configured · disabled'
      : settings.configuration_state === 'invalid'
        ? 'Secret storage needs attention'
        : 'Not configured'

  return <section className="min-w-0 space-y-5" aria-labelledby="telegram-settings-heading">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0"><h1 id="telegram-settings-heading" className="text-xl font-semibold text-white">Telegram notifications</h1><p className="mt-1 max-w-3xl text-sm leading-6 text-gray-400">One installation-global destination for low-noise owner attention, top-level completion, and top-level failure notifications. It is independent of the selected Project.</p></div>
      <span className={`shrink-0 rounded-full px-2.5 py-1 text-xs ${settings.configuration_state === 'enabled' ? 'bg-emerald-500/10 text-emerald-300' : settings.configuration_state === 'invalid' ? 'bg-red-500/10 text-red-300' : 'bg-gray-800 text-gray-400'}`}>{statusLabel}</span>
    </div>
    <OperatorAccessCard access={access} compact />
    <div className="min-w-0 space-y-5 rounded-xl border border-gray-700/60 bg-gray-800/60 p-4 sm:p-5">
      <label className="flex min-h-11 min-w-0 items-center justify-between gap-4 rounded-lg border border-gray-700 bg-gray-900/60 px-3"><span className="min-w-0"><span className="block text-sm font-medium text-gray-200">Enabled</span><span className="block text-xs leading-5 text-gray-400">Disabling delivery keeps the configured destination and token.</span></span><input aria-label="Enable Telegram notifications" type="checkbox" checked={enabled} onChange={event => setEnabled(event.target.checked)} className="h-5 w-5 shrink-0 accent-emerald-500"/></label>
      <div className="grid min-w-0 gap-4 md:grid-cols-2">
        <label className="min-w-0 text-sm text-gray-300">Chat ID / destination
          <input aria-label="Telegram chat ID" value={chatId} onChange={event => setChatId(event.target.value)} autoComplete="off" placeholder="-1001234567890 or @channel" className="mt-1 min-h-11 w-full min-w-0 rounded-lg border border-gray-700 bg-gray-950 px-3 font-mono text-sm text-gray-200 focus:border-emerald-500 focus:outline-none"/>
          <span className="mt-1 block text-xs leading-5 text-gray-400">The global chat or channel destination used for every Project.</span>
        </label>
        <label className="min-w-0 text-sm text-gray-300">Topic / message-thread ID <span className="text-gray-400">(optional)</span>
          <input aria-label="Telegram topic ID" type="number" min="1" value={threadId} onChange={event => setThreadId(event.target.value)} placeholder="Leave empty for the main chat" className="mt-1 min-h-11 w-full min-w-0 rounded-lg border border-gray-700 bg-gray-950 px-3 font-mono text-sm text-gray-200 focus:border-emerald-500 focus:outline-none"/>
          <span className="mt-1 block text-xs leading-5 text-gray-400">Used as Telegram&apos;s message_thread_id when the destination is a forum topic.</span>
        </label>
      </div>
      <label className="block min-w-0 text-sm text-gray-300">Bot token
        <input aria-label="Telegram bot token" type="password" value={token} onChange={event => setToken(event.target.value)} autoComplete="new-password" placeholder={settings.token_configured ? 'Configured — enter a new token to replace it' : 'Enter the bot token'} className="mt-1 min-h-11 w-full min-w-0 rounded-lg border border-gray-700 bg-gray-950 px-3 font-mono text-sm text-gray-200 focus:border-emerald-500 focus:outline-none"/>
        <span className="mt-1 block text-xs leading-5 text-gray-400">The token is stored in ThreadCells-owned restrictive secret storage. It is never returned by this API or saved in browser storage.</span>
      </label>
      <div className="flex flex-wrap gap-2">
        <button disabled={!access.status?.authenticated || busy !== null} onClick={() => void run('save')} className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-emerald-600 px-4 text-sm font-medium text-white disabled:opacity-40"><ShieldCheck size={15}/>{busy === 'save' ? 'Saving…' : 'Save settings'}</button>
        <button disabled={!access.status?.authenticated || busy !== null || !settings.token_configured} onClick={() => void run('check')} className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-gray-700 px-4 text-sm text-gray-300 disabled:opacity-40"><Radio size={15}/>{busy === 'check' ? 'Checking…' : 'Check connection'}</button>
        <button disabled={!access.status?.authenticated || busy !== null || !configured} onClick={() => void run('test')} className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-gray-700 px-4 text-sm text-gray-300 disabled:opacity-40"><Send size={15}/>{busy === 'test' ? 'Sending…' : 'Send test notification'}</button>
        <button disabled={!access.status?.authenticated || busy !== null || settings.token_state === 'missing'} onClick={() => setConfirmClear(true)} className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-red-800/70 px-4 text-sm text-red-300 disabled:opacity-40">Clear bot token</button>
      </div>
    </div>
    <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-gray-800 bg-gray-900/40 p-4 text-xs text-gray-400">
      <CheckCircle2 size={15} className="shrink-0 text-emerald-400"/><span className="shrink-0 text-gray-400">Last safe result:</span><span className="min-w-0 break-words">{settings.last_result ? RESULT_LABELS[settings.last_result] : 'No check or test has run'}{settings.last_result_at ? ` · ${new Date(settings.last_result_at).toLocaleString()}` : ''}</span>
    </div>
    {notice && <p role="status" className="rounded-lg border border-emerald-800/40 bg-emerald-950/20 p-3 text-sm text-emerald-200">{notice}</p>}
    {error && <p role="alert" className="break-words rounded-lg border border-red-700/50 bg-red-950/30 p-3 text-sm text-red-300">{error}</p>}
    <ConfirmModal
      open={confirmClear}
      title="Clear Telegram bot token"
      message="This removes the stored credential and disables Telegram notifications. The destination fields are retained."
      confirmLabel="Clear token"
      onConfirm={() => { setConfirmClear(false); void run('clear') }}
      onCancel={() => setConfirmClear(false)}
    />
  </section>
}
