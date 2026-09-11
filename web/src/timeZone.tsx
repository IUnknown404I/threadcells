import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'
import type { AppLocale } from './i18n'

export const APP_TIME_ZONE_STORAGE_KEY = 'threadcells.app.timeZone'
export const AUTO_TIME_ZONE = 'auto'
export type TimeZonePreference = typeof AUTO_TIME_ZONE | string

const ABSOLUTE_TIMESTAMP = /(?:[zZ]|[+-]\d{2}:?\d{2})$/

export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

export function isValidTimeZone(value: unknown): value is string {
  if (typeof value !== 'string' || !value || value !== value.trim()) return false
  try {
    new Intl.DateTimeFormat('en', { timeZone: value }).format(0)
    return true
  } catch {
    return false
  }
}

export function readStoredTimeZone(storage?: Pick<Storage, 'getItem'>): TimeZonePreference {
  try {
    const value = (storage ?? globalThis.localStorage)?.getItem(APP_TIME_ZONE_STORAGE_KEY)
    return value === AUTO_TIME_ZONE || isValidTimeZone(value) ? value : AUTO_TIME_ZONE
  } catch {
    return AUTO_TIME_ZONE
  }
}

export function resolvedTimeZone(preference: TimeZonePreference): string {
  return preference === AUTO_TIME_ZONE ? browserTimeZone() : preference
}

/**
 * Format only timestamps whose source string carries an explicit UTC/offset
 * contract. Ambiguous wall-clock strings stay byte-for-byte visible instead of
 * being silently reinterpreted in the browser's local zone.
 */
export function formatAbsoluteTimestamp(
  value: string | null | undefined,
  locale: AppLocale,
  timeZone: string,
): string {
  if (!value) return '—'
  if (!ABSOLUTE_TIMESTAMP.test(value)) return value
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime()) || !isValidTimeZone(timeZone)) return value
  return new Intl.DateTimeFormat(locale, {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone,
  }).format(parsed)
}

type TimeZoneValue = {
  preference: TimeZonePreference
  timeZone: string
  browserZone: string
  setPreference: (preference: TimeZonePreference) => boolean
}

const defaultBrowserZone = browserTimeZone()
const TimeZoneContext = createContext<TimeZoneValue>({
  preference: AUTO_TIME_ZONE,
  timeZone: defaultBrowserZone,
  browserZone: defaultBrowserZone,
  setPreference: () => false,
})

export function TimeZoneProvider({ children }: { children: ReactNode }) {
  const [preference, setPreferenceState] = useState<TimeZonePreference>(() => readStoredTimeZone())
  const detected = browserTimeZone()
  const setPreference = useCallback((next: TimeZonePreference) => {
    if (next !== AUTO_TIME_ZONE && !isValidTimeZone(next)) return false
    setPreferenceState(next)
    try { localStorage.setItem(APP_TIME_ZONE_STORAGE_KEY, next) } catch { /* best-effort browser preference */ }
    return true
  }, [])
  const value = useMemo<TimeZoneValue>(() => ({
    preference,
    timeZone: preference === AUTO_TIME_ZONE ? detected : preference,
    browserZone: detected,
    setPreference,
  }), [detected, preference, setPreference])
  return <TimeZoneContext.Provider value={value}>{children}</TimeZoneContext.Provider>
}

export function useTimeZone(): TimeZoneValue {
  return useContext(TimeZoneContext)
}
