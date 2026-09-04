import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import {
  APP_LOCALE_STORAGE_KEY,
  I18nProvider,
  appLocales,
  catalogPlaceholders,
  catalogs,
  readStoredAppLocale,
  translate,
  translatePlural,
  useI18n,
} from '../i18n'
import { ProviderOutcomeNotice } from '../components/ProviderOutcomeNotice'

function LocaleProbe({ raw }: { raw?: string }) {
  const { locale, setLocale, t } = useI18n()
  return <div>
    <output aria-label="locale">{locale}</output>
    <span>{t('nav.settings')}</span>
    {raw && <pre data-testid="raw-content">{raw}</pre>}
    <button type="button" onClick={() => setLocale('en')}>English</button>
    <button type="button" onClick={() => setLocale('ru')}>Русский</button>
  </div>
}

describe('authenticated application locale contract', () => {
  beforeEach(() => localStorage.clear())
  afterEach(() => {
    localStorage.clear()
    document.documentElement.lang = 'en'
  })

  it('uses English for a fresh preference even when the browser language is Russian', () => {
    const original = Object.getOwnPropertyDescriptor(window.navigator, 'language')
    Object.defineProperty(window.navigator, 'language', { configurable: true, value: 'ru-RU' })
    render(<I18nProvider><LocaleProbe /></I18nProvider>)
    expect(screen.getByLabelText('locale')).toHaveTextContent('en')
    expect(screen.getByText('Settings')).toBeInTheDocument()
    expect(document.documentElement).toHaveAttribute('lang', 'en')
    if (original) Object.defineProperty(window.navigator, 'language', original)
  })

  it('falls back to English for an invalid stored locale', () => {
    localStorage.setItem(APP_LOCALE_STORAGE_KEY, 'de')
    expect(readStoredAppLocale()).toBe('en')
    render(<I18nProvider><LocaleProbe /></I18nProvider>)
    expect(screen.getByLabelText('locale')).toHaveTextContent('en')
  })

  it('falls back to English when access to browser storage is denied', () => {
    const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      get: () => { throw new DOMException('denied', 'SecurityError') },
    })
    try {
      expect(readStoredAppLocale()).toBe('en')
      render(<I18nProvider><LocaleProbe /></I18nProvider>)
      expect(screen.getByLabelText('locale')).toHaveTextContent('en')
    } finally {
      if (descriptor) Object.defineProperty(globalThis, 'localStorage', descriptor)
    }
  })

  it('switches immediately and persists the opt-in Russian preference across reloads', () => {
    const first = render(<I18nProvider><LocaleProbe /></I18nProvider>)
    fireEvent.click(screen.getByRole('button', { name: 'Русский' }))
    expect(screen.getByLabelText('locale')).toHaveTextContent('ru')
    expect(screen.getByText('Настройки')).toBeInTheDocument()
    expect(document.documentElement).toHaveAttribute('lang', 'ru')
    expect(localStorage.getItem(APP_LOCALE_STORAGE_KEY)).toBe('ru')

    first.unmount()
    render(<I18nProvider><LocaleProbe /></I18nProvider>)
    expect(screen.getByLabelText('locale')).toHaveTextContent('ru')
    fireEvent.click(screen.getByRole('button', { name: 'English' }))
    expect(localStorage.getItem(APP_LOCALE_STORAGE_KEY)).toBe('en')
  })

  it('changes chrome only and preserves raw machine/provider/result content byte-for-byte', () => {
    const raw = 'reason_code=TERMINAL_RUNTIME_ACTIVE\nprovider=codex\nresult: Привет <id-123>'
    render(<I18nProvider><LocaleProbe raw={raw} /></I18nProvider>)
    const before = screen.getByTestId('raw-content').textContent
    fireEvent.click(screen.getByRole('button', { name: 'Русский' }))
    expect(screen.getByTestId('raw-content').textContent).toBe(before)
  })

  it('localizes provider content-unavailable chrome without rendering provider detail', () => {
    const first = render(
      <I18nProvider>
        <ProviderOutcomeNotice code="PROVIDER_CONTENT_UNAVAILABLE" />
      </I18nProvider>,
    )
    expect(screen.getByText('Provider response unavailable')).toBeInTheDocument()
    expect(screen.queryByText(/cyber_policy|This content can't be shown/i)).not.toBeInTheDocument()

    first.unmount()
    localStorage.setItem(APP_LOCALE_STORAGE_KEY, 'ru')
    render(
      <I18nProvider>
        <ProviderOutcomeNotice code="PROVIDER_CONTENT_UNAVAILABLE" />
      </I18nProvider>,
    )
    expect(screen.getByText('Ответ провайдера недоступен')).toBeInTheDocument()
    expect(screen.getByText(/Состояние рабочего процесса сохранено/)).toBeInTheDocument()
  })
})

describe('typed catalog contract', () => {
  it('supports only en and ru with complete key parity', () => {
    expect(appLocales).toEqual(['en', 'ru'])
    expect(Object.keys(catalogs.ru).sort()).toEqual(Object.keys(catalogs.en).sort())
  })

  it('keeps interpolation placeholders identical in both catalogs', () => {
    for (const key of Object.keys(catalogs.en) as Array<keyof typeof catalogs.en>) {
      expect(catalogPlaceholders(catalogs.ru[key]), key).toEqual(catalogPlaceholders(catalogs.en[key]))
    }
    expect(translate('ru', 'home.loadMoreAgents', { loaded: 2, total: 5 })).toContain('2')
  })

  it('labels the loaded Output interval explicitly in both locales', () => {
    const params = { start: '39.0 MiB', end: '39.5 MiB', total: '39.5 MiB' }
    expect(translate('en', 'output.loadedRange', params)).toBe(
      'Loaded 39.0 MiB–39.5 MiB of 39.5 MiB',
    )
    expect(translate('ru', 'output.loadedRange', params)).toBe(
      'Загружено 39.0 MiB–39.5 MiB из 39.5 MiB',
    )
  })

  it('uses Russian plural categories through Intl.PluralRules', () => {
    expect(translatePlural('ru', 'sessions', 1)).toBe('1 сессия')
    expect(translatePlural('ru', 'sessions', 2)).toBe('2 сессии')
    expect(translatePlural('ru', 'sessions', 5)).toBe('5 сессий')
    expect(translatePlural('ru', 'sessions', 21)).toBe('21 сессия')
  })
})
