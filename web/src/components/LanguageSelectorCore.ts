export type LanguageOption<Locale extends string> = {
  code: Locale
  short: string
  name: string
  htmlLang: string
}

export type LanguageSelectorProps<Locale extends string> = {
  locale: Locale
  options: readonly LanguageOption<Locale>[]
  label: string
  hrefFor?: (locale: Locale) => string
  onSelect?: (locale: Locale) => void
  className?: string
}

type SelectorClickEvent = {
  button: number
  metaKey: boolean
  ctrlKey: boolean
  shiftKey: boolean
  altKey: boolean
  preventDefault: () => void
  currentTarget: { closest: (selector: string) => HTMLDetailsElement | null }
}

export type LanguageSelectorElementFactory<Element> = (
  type: string,
  props: Record<string, unknown> | null,
  ...children: Array<Element | string>
) => Element

export function renderLanguageSelector<Locale extends string, Element>(
  element: LanguageSelectorElementFactory<Element>,
  {
    locale,
    options,
    label,
    hrefFor,
    onSelect,
    className = 'language-menu',
  }: LanguageSelectorProps<Locale>,
): Element {
  const current = options.find(option => option.code === locale) || options[0]
  const choose = (event: SelectorClickEvent, next: Locale) => {
    if (!onSelect || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    event.preventDefault()
    onSelect(next)
    const details = event.currentTarget.closest('details')
    if (details) details.open = false
  }
  return element(
    'details',
    { className },
    element('summary', { 'aria-label': label }, current.short),
    element(
      'div',
      { role: 'menu', 'aria-label': label },
      ...options.map(option => element(
        'a',
        {
          key: option.code,
          role: 'menuitem',
          href: hrefFor?.(option.code) || '#',
          hrefLang: option.htmlLang,
          'aria-current': option.code === locale ? 'page' : undefined,
          onClick: (event: SelectorClickEvent) => choose(event, option.code),
        },
        option.name,
      )),
    ),
  )
}
