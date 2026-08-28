import { MessageSquareWarning } from 'lucide-react'
import { useI18n } from '../i18n'

export function ProviderOutcomeNotice({ code }: { code?: string | null }) {
  const { t } = useI18n()
  if (code !== 'PROVIDER_CONTENT_UNAVAILABLE') return null

  return <div data-testid="provider-content-unavailable" role="status" className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-100">
    <MessageSquareWarning size={15} className="mt-0.5 shrink-0 text-amber-400" />
    <span><strong className="font-semibold">{t('providerOutcome.title')}</strong> {t('providerOutcome.description')}</span>
  </div>
}
