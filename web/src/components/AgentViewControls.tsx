import { Grid2X2, List } from 'lucide-react'

export type AgentViewLayout = 'list' | 'grid'

export function AgentViewControls({ value, onChange }: {
  value: AgentViewLayout
  onChange: (value: AgentViewLayout) => void
}) {
  const controls = [
    { value: 'list' as const, label: 'List view', icon: List },
    { value: 'grid' as const, label: 'Grid view', icon: Grid2X2 },
  ]
  return (
    <div className="inline-flex shrink-0 items-center gap-1" role="group" aria-label="Agent layout">
      {controls.map(({ value: controlValue, label, icon: Icon }) => (
        <button
          key={controlValue}
          type="button"
          aria-label={label}
          aria-pressed={value === controlValue}
          onClick={() => onChange(controlValue)}
          className={`inline-flex h-9 w-9 items-center justify-center rounded-md transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${value === controlValue ? 'bg-emerald-600 text-white' : 'text-gray-400 hover:bg-gray-800 hover:text-gray-200'}`}
          title={controlValue === 'list' ? 'List' : 'Grid'}
        >
          <Icon size={15} aria-hidden="true" />
        </button>
      ))}
    </div>
  )
}
