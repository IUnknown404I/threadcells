import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { appLocales, catalogs, type AppLocale, type TranslationKey } from './catalogs'

export { appLocales, catalogs, type AppLocale, type TranslationKey } from './catalogs'

export const APP_LOCALE_STORAGE_KEY = 'threadcells.app.locale'

type Params = Record<string, string | number>
type PluralStem = 'sessions' | 'agents' | 'resources' | 'releases' | 'operator.unlocked' | 'housekeeping.protectedItems'

export function isAppLocale(value: unknown): value is AppLocale {
  return typeof value === 'string' && (appLocales as readonly string[]).includes(value)
}

export function readStoredAppLocale(storage?: Pick<Storage, 'getItem'>): AppLocale {
  try {
    // Accessing window.localStorage can itself throw (for example when browser
    // storage is denied), so resolve the default inside the guarded boundary.
    const value = (storage ?? globalThis.localStorage)?.getItem(APP_LOCALE_STORAGE_KEY)
    return isAppLocale(value) ? value : 'en'
  } catch {
    return 'en'
  }
}

function interpolate(value: string, params?: Params): string {
  if (!params) return value
  return value.replace(/\{([a-zA-Z0-9_]+)\}/g, (match, name: string) => Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match)
}

export function translate(locale: AppLocale, key: TranslationKey, params?: Params): string {
  return interpolate(catalogs[locale][key] ?? catalogs.en[key], params)
}

export function translatePlural(locale: AppLocale, stem: PluralStem, count: number): string {
  const category = new Intl.PluralRules(locale).select(count)
  const key = `${stem}.${category}` as TranslationKey
  const fallback = `${stem}.other` as TranslationKey
  return translate(locale, key in catalogs[locale] ? key : fallback, { count })
}

type I18nValue = {
  locale: AppLocale
  setLocale: (locale: AppLocale) => void
  t: (key: TranslationKey, params?: Params) => string
  tp: (stem: PluralStem, count: number) => string
}

const DEFAULT_I18N: I18nValue = {
  locale: 'en',
  setLocale: () => {},
  t: (key, params) => translate('en', key, params),
  tp: (stem, count) => translatePlural('en', stem, count),
}

const I18nContext = createContext<I18nValue>(DEFAULT_I18N)

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<AppLocale>(() => readStoredAppLocale())
  const setLocale = useCallback((next: AppLocale) => {
    if (!isAppLocale(next)) return
    setLocaleState(next)
    try { localStorage.setItem(APP_LOCALE_STORAGE_KEY, next) } catch { /* preference persistence is best-effort */ }
  }, [])

  useEffect(() => {
    document.documentElement.lang = locale
  }, [locale])

  const value = useMemo<I18nValue>(() => ({
    locale,
    setLocale,
    t: (key, params) => translate(locale, key, params),
    tp: (stem, count) => translatePlural(locale, stem, count),
  }), [locale, setLocale])

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

export function useI18n(): I18nValue {
  return useContext(I18nContext)
}

export function Translation({ id, params }: { id: TranslationKey; params?: Params }) {
  const { t } = useI18n()
  return <>{t(id, params)}</>
}

export function catalogPlaceholders(value: string): string[] {
  return [...value.matchAll(/\{([a-zA-Z0-9_]+)\}/g)].map(match => match[1]).sort()
}
