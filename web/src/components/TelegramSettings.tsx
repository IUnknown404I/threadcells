import { useEffect, useState } from 'react'
import { CheckCircle2, Radio, Send, ShieldCheck } from 'lucide-react'
import { api, TelegramSettings as TelegramSettingsState } from '../api'
import { ConfirmModal } from './ConfirmModal'
import { OperatorAccessCard, useOperatorAccess } from './OperatorAccess'
import { useI18n, type TranslationKey } from '../i18n'

const RESULT_LABELS: Record<string, TranslationKey> = {
  connection_ok: 'telegram.connectionOk',
  connection_failed: 'telegram.connectionFailed',
  test_sent: 'telegram.testSent',
  test_failed: 'telegram.testFailed',
  not_configured: 'telegram.configurationIncomplete',
}

export function TelegramSettings() {
  const { locale, t } = useI18n()
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
    catch (reason: any) { setError(reason.message || t('telegram.loadFailed')) }
  }

  useEffect(() => { void load() }, [])

  const run = async (kind: 'save' | 'check' | 'test' | 'clear') => {
    setError(''); setNotice(''); setBusy(kind)
    try {
      if (kind === 'save') {
        if (enabled && !chatId.trim()) throw new Error(t('telegram.chatRequired'))
        if (enabled && !settings?.token_configured && !token.trim()) throw new Error(t('telegram.tokenRequired'))
        const next = await api.updateTelegramSettings({
          enabled,
          chat_id: chatId.trim() || null,
          message_thread_id: threadId.trim() ? Number(threadId) : null,
          bot_token: token.trim() || null,
          clear_bot_token: false,
        })
        apply(next); setToken(''); setNotice(t('telegram.saved'))
      } else if (kind === 'check') {
        const result = await api.checkTelegramConnection()
        setNotice(result.ok ? t('telegram.connectionPassed') : t('telegram.connectionFailedSafely'))
        await load()
      } else if (kind === 'test') {
        const result = await api.sendTelegramTest()
        setNotice(result.ok ? t('telegram.testSentNotice') : t('telegram.testNotSent'))
        await load()
      } else {
        const next = await api.updateTelegramSettings({
          enabled: false,
          chat_id: chatId.trim() || null,
          message_thread_id: threadId.trim() ? Number(threadId) : null,
          bot_token: null,
          clear_bot_token: true,
        })
        apply(next); setToken(''); setNotice(t('telegram.cleared'))
      }
    } catch (reason: any) {
      setError(reason.message || t('telegram.operationFailed'))
      await access.refresh()
    } finally { setBusy(null) }
  }

  if (!settings) return <p className="py-8 text-center text-sm text-gray-400">{t('telegram.loading')}</p>
  const configured = settings.token_configured && Boolean(settings.chat_id)
  const statusLabel = settings.configuration_state === 'enabled'
    ? t('common.enabled')
    : settings.configuration_state === 'disabled'
      ? t('telegram.configuredDisabled')
      : settings.configuration_state === 'invalid'
        ? t('telegram.secretAttention')
        : t('telegram.notConfigured')

  return <section className="min-w-0 space-y-5" aria-labelledby="telegram-settings-heading">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0"><h1 id="telegram-settings-heading" className="text-xl font-semibold text-white">{t('telegram.title')}</h1><p className="mt-1 max-w-3xl text-sm leading-6 text-gray-400">{t('telegram.description')}</p></div>
      <span className={`shrink-0 rounded-full px-2.5 py-1 text-xs ${settings.configuration_state === 'enabled' ? 'bg-emerald-500/10 text-emerald-300' : settings.configuration_state === 'invalid' ? 'bg-red-500/10 text-red-300' : 'bg-gray-800 text-gray-400'}`}>{statusLabel}</span>
    </div>
    <OperatorAccessCard access={access} compact />
    <div className="min-w-0 space-y-5 rounded-xl border border-gray-700/60 bg-gray-800/60 p-4 sm:p-5">
      <label className="flex min-h-11 min-w-0 items-center justify-between gap-4 rounded-lg border border-gray-700 bg-gray-900/60 px-3"><span className="min-w-0"><span className="block text-sm font-medium text-gray-200">{t('common.enabled')}</span><span className="block text-xs leading-5 text-gray-400">{t('telegram.disableHelp')}</span></span><input aria-label={t('telegram.enableLabel')} type="checkbox" checked={enabled} onChange={event => setEnabled(event.target.checked)} className="h-5 w-5 shrink-0 accent-emerald-500"/></label>
      <div className="grid min-w-0 gap-4 md:grid-cols-2">
        <label className="min-w-0 text-sm text-gray-300">{t('telegram.chat')}
          <input aria-label={t('telegram.chatLabel')} value={chatId} onChange={event => setChatId(event.target.value)} autoComplete="off" placeholder={t('telegram.chatPlaceholder')} className="mt-1 min-h-11 w-full min-w-0 rounded-lg border border-gray-700 bg-gray-950 px-3 font-mono text-sm text-gray-200 focus:border-emerald-500 focus:outline-none"/>
          <span className="mt-1 block text-xs leading-5 text-gray-400">{t('telegram.chatHelp')}</span>
        </label>
        <label className="min-w-0 text-sm text-gray-300">{t('telegram.topic')} <span className="text-gray-400">({t('common.optional')})</span>
          <input aria-label={t('telegram.topicLabel')} type="number" min="1" value={threadId} onChange={event => setThreadId(event.target.value)} placeholder={t('telegram.topicPlaceholder')} className="mt-1 min-h-11 w-full min-w-0 rounded-lg border border-gray-700 bg-gray-950 px-3 font-mono text-sm text-gray-200 focus:border-emerald-500 focus:outline-none"/>
          <span className="mt-1 block text-xs leading-5 text-gray-400">{t('telegram.topicHelp')}</span>
        </label>
      </div>
      <label className="block min-w-0 text-sm text-gray-300">{t('telegram.token')}
        <input aria-label={t('telegram.tokenLabel')} type="password" value={token} onChange={event => setToken(event.target.value)} autoComplete="new-password" placeholder={settings.token_configured ? t('telegram.tokenConfigured') : t('telegram.tokenEnter')} className="mt-1 min-h-11 w-full min-w-0 rounded-lg border border-gray-700 bg-gray-950 px-3 font-mono text-sm text-gray-200 focus:border-emerald-500 focus:outline-none"/>
        <span className="mt-1 block text-xs leading-5 text-gray-400">{t('telegram.tokenHelp')}</span>
      </label>
      <div className="flex flex-wrap gap-2">
        <button disabled={!access.status?.authenticated || busy !== null} onClick={() => void run('save')} className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-emerald-600 px-4 text-sm font-medium text-white disabled:opacity-40"><ShieldCheck size={15}/>{busy === 'save' ? t('common.saving') : t('telegram.save')}</button>
        <button disabled={!access.status?.authenticated || busy !== null || !settings.token_configured} onClick={() => void run('check')} className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-gray-700 px-4 text-sm text-gray-300 disabled:opacity-40"><Radio size={15}/>{busy === 'check' ? t('common.checking') : t('telegram.check')}</button>
        <button disabled={!access.status?.authenticated || busy !== null || !configured} onClick={() => void run('test')} className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-gray-700 px-4 text-sm text-gray-300 disabled:opacity-40"><Send size={15}/>{busy === 'test' ? t('common.sending') : t('telegram.sendTest')}</button>
        <button disabled={!access.status?.authenticated || busy !== null || settings.token_state === 'missing'} onClick={() => setConfirmClear(true)} className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-red-800/70 px-4 text-sm text-red-300 disabled:opacity-40">{t('telegram.clear')}</button>
      </div>
    </div>
    <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-gray-800 bg-gray-900/40 p-4 text-xs text-gray-400">
      <CheckCircle2 size={15} className="shrink-0 text-emerald-400"/><span className="shrink-0 text-gray-400">{t('telegram.lastResult')}</span><span className="min-w-0 break-words">{settings.last_result && RESULT_LABELS[settings.last_result] ? t(RESULT_LABELS[settings.last_result]) : t('telegram.noResult')}{settings.last_result_at ? ` · ${new Date(settings.last_result_at).toLocaleString(locale)}` : ''}</span>
    </div>
    {notice && <p role="status" className="rounded-lg border border-emerald-800/40 bg-emerald-950/20 p-3 text-sm text-emerald-200">{notice}</p>}
    {error && <p role="alert" className="break-words rounded-lg border border-red-700/50 bg-red-950/30 p-3 text-sm text-red-300">{error}</p>}
    <ConfirmModal
      open={confirmClear}
      title={t('telegram.clearTitle')}
      message={t('telegram.clearMessage')}
      confirmLabel={t('telegram.clearConfirm')}
      onConfirm={() => { setConfirmClear(false); void run('clear') }}
      onCancel={() => setConfirmClear(false)}
    />
  </section>
}
