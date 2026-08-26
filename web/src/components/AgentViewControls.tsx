import { Grid2X2, List } from 'lucide-react'
import { useI18n } from '../i18n'

export type AgentViewLayout = 'list' | 'grid'

export function AgentViewControls({ value, onChange }: {
  value: AgentViewLayout
  onChange: (value: AgentViewLayout) => void
}) {
  const { t } = useI18n()
  const controls = [
    { value: 'list' as const, label: t('layout.listView'), title: t('layout.list'), icon: List },
    { value: 'grid' as const, label: t('layout.gridView'), title: t('layout.grid'), icon: Grid2X2 },
  ]
  return (
    <div className="inline-flex shrink-0 items-center gap-1" role="group" aria-label={t('layout.agent')}>
      {controls.map(({ value: controlValue, label, title, icon: Icon }) => (
        <button
          key={controlValue}
          type="button"
          aria-label={label}
          aria-pressed={value === controlValue}
          onClick={() => onChange(controlValue)}
          className={`inline-flex h-9 w-9 items-center justify-center rounded-md transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${value === controlValue ? 'bg-emerald-600 text-white' : 'text-gray-400 hover:bg-gray-800 hover:text-gray-200'}`}
          title={title}
        >
          <Icon size={15} aria-hidden="true" />
        </button>
      ))}
    </div>
  )
}
