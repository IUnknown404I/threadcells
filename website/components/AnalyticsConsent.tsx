'use client'

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { analyticsCopy } from '@/components/analytics-copy'
import { locales, type Locale } from '@/lib/locales'

const measurementId = 'G-WWBZSZ4N7T'
const consentKey = 'threadcells.analytics-consent.v1'

declare global {
  interface Window {
    __threadcellsAnalyticsStarted?: boolean
    dataLayer?: unknown[]
    gtag?: (...args: unknown[]) => void
  }
}

type Consent = 'accepted' | 'declined' | null

const isProduction = () => process.env.NODE_ENV === 'production'

function analyticsIsAllowedHere() {
  // A static export is also built in CI and previewed from arbitrary hosts. Only
  // the canonical Pages host can send production analytics.
  return isProduction() && window.location.hostname === 'iunknown404i.github.io'
}

function localeForPathname(pathname: string): Locale {
  const locale = pathname.split('/').filter(Boolean)[0]
  return locales.includes(locale as Locale) ? locale as Locale : 'en'
}

function pageLocation() {
  // GA should receive the public route, never a query or fragment that could
  // contain sensitive values. Hash navigation is not a separate page view.
  return `${window.location.origin}${window.location.pathname}`
}

function readConsent(): Consent {
  try {
    const value = window.localStorage.getItem(consentKey)
    return value === 'accepted' || value === 'declined' ? value : null
  } catch {
    return null
  }
}

function subscribeToConsent(onChange: () => void) {
  window.addEventListener('storage', onChange)
  return () => window.removeEventListener('storage', onChange)
}

function startAnalytics(consent: Consent) {
  if (!analyticsIsAllowedHere() || window.__threadcellsAnalyticsStarted) return
  window.__threadcellsAnalyticsStarted = true
  window.dataLayer = window.dataLayer || []
  // Google Tag consumes its command queue as Arguments objects, matching the
  // canonical gtag snippet. Plain arrays load the library but do not dispatch.
  window.gtag = function gtag() {
    // eslint-disable-next-line prefer-rest-params
    window.dataLayer?.push(arguments)
  }
  // This must precede config. A saved choice is applied before config; a new
  // visitor still starts denied and receives only the cookieless page signal.
  window.gtag('consent', 'default', {
    analytics_storage: consent === 'accepted' ? 'granted' : 'denied',
    ad_storage: 'denied',
    ad_user_data: 'denied',
    ad_personalization: 'denied',
  })
  window.gtag('js', new Date())
  // Config emits the only logical page view for this full static-page load.
  // Consent updates below deliberately do not invoke config again.
  window.gtag('config', measurementId, { page_location: pageLocation() })
  const script = document.createElement('script')
  script.async = true
  script.src = `https://www.googletagmanager.com/gtag/js?id=${measurementId}`
  script.dataset.threadcellsAnalytics = measurementId
  document.head.append(script)
}

export function AnalyticsConsent() {
  const [consentVersion, setConsentVersion] = useState(0)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const settings = useRef<HTMLDialogElement>(null)
  const currentConsent = useCallback(() => {
    void consentVersion
    return readConsent()
  }, [consentVersion])
  const consent = useSyncExternalStore(subscribeToConsent, currentConsent, () => null)
  const copy = analyticsCopy[localeForPathname(typeof window === 'undefined' ? '/' : window.location.pathname)]

  useEffect(() => {
    startAnalytics(consent)
    if (window.gtag) {
      window.gtag('consent', 'update', {
        analytics_storage: consent === 'accepted' ? 'granted' : 'denied',
        ad_storage: 'denied',
        ad_user_data: 'denied',
        ad_personalization: 'denied',
      })
    }
  }, [consent])

  useEffect(() => {
    const dialog = settings.current
    if (!dialog) return
    if (settingsOpen && !dialog.open) dialog.showModal()
    if (!settingsOpen && dialog.open) dialog.close()
  }, [settingsOpen])

  const save = (next: Exclude<Consent, null>) => {
    try {
      window.localStorage.setItem(consentKey, next)
    } catch {
      // Consent remains active for this page even when storage is unavailable.
    }
    setConsentVersion(version => version + 1)
    startAnalytics(next)
    window.gtag?.('consent', 'update', {
      analytics_storage: next === 'accepted' ? 'granted' : 'denied',
      ad_storage: 'denied',
      ad_user_data: 'denied',
      ad_personalization: 'denied',
    })
    setSettingsOpen(false)
  }

  return (
    <>
      {consent === null ? (
        <section className="consent-banner" role="dialog" aria-labelledby="analytics-consent-title" aria-describedby="analytics-consent-copy">
          <h2 id="analytics-consent-title">{copy.title}</h2>
          <p id="analytics-consent-copy">{copy.text}</p>
          <div className="consent-actions">
            <button className="consent-button consent-button-secondary" type="button" onClick={() => save('declined')}>{copy.decline}</button>
            <button className="consent-button" type="button" onClick={() => save('accepted')}>{copy.allow}</button>
            <button className="consent-link" type="button" onClick={() => setSettingsOpen(true)}>{copy.details}</button>
          </div>
        </section>
      ) : null}
      <button className="privacy-settings" type="button" onClick={() => setSettingsOpen(true)}>{copy.settings}</button>
      <dialog ref={settings} className="privacy-dialog" aria-labelledby="privacy-dialog-title" onClose={() => setSettingsOpen(false)}>
        <div>
          <p className="privacy-kicker">PRIVACY NOTICE</p>
          <h2 id="privacy-dialog-title">{copy.choice}</h2>
          <p>{copy.notice}</p>
          <div className="consent-actions">
            <button className="consent-button consent-button-secondary" type="button" onClick={() => save('declined')}>{copy.decline}</button>
            <button className="consent-button" type="button" onClick={() => save('accepted')}>{copy.allow}</button>
            <button className="consent-link" type="button" onClick={() => setSettingsOpen(false)}>{copy.close}</button>
          </div>
        </div>
      </dialog>
    </>
  )
}
