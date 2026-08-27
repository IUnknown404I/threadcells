import type { MouseEvent } from 'react'

export type LanguageOption<Locale extends string> = {
  code: Locale
  short: string
  name: string
  htmlLang: string
}

export function LanguageSelector<Locale extends string>({
  locale,
  options,
  label,
  hrefFor,
  onSelect,
  className = 'language-menu',
}: {
  locale: Locale
  options: readonly LanguageOption<Locale>[]
  label: string
  hrefFor?: (locale: Locale) => string
  onSelect?: (locale: Locale) => void
  className?: string
}) {
  const current = options.find(option => option.code === locale) || options[0]
  const choose = (event: MouseEvent, next: Locale) => {
    if (!onSelect || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    event.preventDefault()
    onSelect(next)
    const details = event.currentTarget.closest('details')
    if (details) details.open = false
  }
  return <details className={className}>
    <summary aria-label={label}>{current.short}</summary>
    <div role="menu" aria-label={label}>
      {options.map(option => <a
        key={option.code}
        role="menuitem"
        href={hrefFor?.(option.code) || '#'}
        hrefLang={option.htmlLang}
        aria-current={option.code === locale ? 'page' : undefined}
        onClick={event => choose(event, option.code)}
      >{option.name}</a>)}
    </div>
  </details>
}
