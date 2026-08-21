import { useEffect, useRef, useState } from 'react'

export interface SearchPickerItem { value: string; label: string; detail?: string }

export function SearchPicker({ items, value, onChange, placeholder, searchPlaceholder, emptyLabel, disabled }: {
  items: SearchPickerItem[]; value: string; onChange: (value: string) => void
  placeholder: string; searchPlaceholder: string; emptyLabel: string; disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const matches = items.filter(item => `${item.label} ${item.detail || ''}`.toLowerCase().includes(query.trim().toLowerCase()))
  useEffect(() => { if (open) inputRef.current?.focus() }, [open])
  const select = (next: string) => { onChange(next); setQuery(''); setOpen(false) }
  const selected = items.find(item => item.value === value)
  return <div className="relative"><button type="button" disabled={disabled} onClick={() => setOpen(current => !current)} className="min-h-11 w-full rounded-lg border border-gray-700 bg-gray-900 px-3 text-left text-sm text-gray-200 disabled:opacity-50">{selected?.label || placeholder}</button>{open && <div className="absolute z-50 mt-1 w-full overflow-hidden rounded-lg border border-gray-700 bg-gray-900 shadow-xl"><input ref={inputRef} value={query} onChange={event => setQuery(event.target.value)} onKeyDown={event => { if (event.key === 'Escape') setOpen(false); if (event.key === 'Enter' && matches[0]) select(matches[0].value) }} placeholder={searchPlaceholder} className="w-full border-b border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-200 outline-none" /><div className="max-h-56 overflow-auto">{matches.length ? matches.map(item => <button key={item.value} type="button" aria-selected={value === item.value} onClick={() => select(item.value)} className="block min-h-11 w-full px-3 text-left hover:bg-gray-800"><span className="block text-sm text-gray-200">{item.label}</span>{item.detail && <span className="block text-xs text-gray-500">{item.detail}</span>}</button>) : <p className="px-3 py-3 text-sm text-gray-500">{emptyLabel}</p>}</div></div>}</div>
}
