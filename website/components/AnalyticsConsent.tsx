'use client'

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'

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

function startAnalytics() {
  const hostname = window.location.hostname
  const isLoopback = hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '::1'
  if (!isProduction() || isLoopback || window.__threadcellsAnalyticsStarted) return
  window.__threadcellsAnalyticsStarted = true
  window.dataLayer = window.dataLayer || []
  window.gtag = (...args: unknown[]) => { window.dataLayer?.push(args) }
  window.gtag('js', new Date())
  // `config` sends one automatic page_view. The window guard prevents a second
  // config call if React remounts this component or settings are saved again.
  window.gtag('config', measurementId)
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

  useEffect(() => {
    if (consent === 'accepted') startAnalytics()
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
    if (next === 'accepted') startAnalytics()
    setSettingsOpen(false)
  }

  return (
    <>
      {consent === null ? (
        <section className="consent-banner" role="dialog" aria-label="Analytics consent" aria-describedby="analytics-consent-copy">
          <p id="analytics-consent-copy">We use optional, privacy-respecting audience measurement only if you allow it. No Google Analytics request is made before your choice.</p>
          <div className="consent-actions">
            <button className="consent-button consent-button-secondary" type="button" onClick={() => save('declined')}>Decline</button>
            <button className="consent-button" type="button" onClick={() => save('accepted')}>Allow analytics</button>
            <button className="consent-link" type="button" onClick={() => setSettingsOpen(true)}>Privacy details</button>
          </div>
        </section>
      ) : null}
      <button className="privacy-settings" type="button" onClick={() => setSettingsOpen(true)}>Privacy &amp; analytics settings</button>
      <dialog ref={settings} className="privacy-dialog" aria-labelledby="privacy-dialog-title" onClose={() => setSettingsOpen(false)}>
        <div>
          <p className="privacy-kicker">PRIVACY NOTICE</p>
          <h2 id="privacy-dialog-title">Your analytics choice</h2>
          <p>ThreadCells has no account or contact form on this site. If you allow analytics, Google Analytics 4 receives standard visit and device information to help us understand site use. If you decline, it is not loaded.</p>
          <p>Your choice is saved only in this browser and can be changed here at any time.</p>
          <div className="consent-actions">
            <button className="consent-button consent-button-secondary" type="button" onClick={() => save('declined')}>Decline analytics</button>
            <button className="consent-button" type="button" onClick={() => save('accepted')}>Allow analytics</button>
            <button className="consent-link" type="button" onClick={() => setSettingsOpen(false)}>Close</button>
          </div>
        </div>
      </dialog>
    </>
  )
}
