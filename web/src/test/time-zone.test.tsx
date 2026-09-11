import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { TimeZoneSettingsCard } from '../components/SettingsPanel'
import { I18nProvider } from '../i18n'
import {
  APP_TIME_ZONE_STORAGE_KEY,
  AUTO_TIME_ZONE,
  formatAbsoluteTimestamp,
  isValidTimeZone,
  readStoredTimeZone,
  TimeZoneProvider,
} from '../timeZone'

describe('browser time-zone preference', () => {
  afterEach(() => localStorage.removeItem(APP_TIME_ZONE_STORAGE_KEY))

  it('preserves ambiguous server values and formats only explicit absolute timestamps', () => {
    expect(formatAbsoluteTimestamp('2026-01-15 12:00:00', 'en', 'Asia/Tokyo')).toBe('2026-01-15 12:00:00')
    expect(formatAbsoluteTimestamp('2026-01-15T12:00:00', 'en', 'Asia/Tokyo')).toBe('2026-01-15T12:00:00')
    expect(formatAbsoluteTimestamp('2026-01-15T12:00:00Z', 'en', 'Asia/Tokyo')).toMatch(/9:00\sPM/i)
    expect(formatAbsoluteTimestamp('2026-01-15T12:00:00+00:00', 'en', 'UTC')).toMatch(/12:00\sPM/i)
  })

  it('accepts IANA zones without synthesizing an invalid stored preference', () => {
    expect(isValidTimeZone('Europe/Berlin')).toBe(true)
    expect(isValidTimeZone(' Not/AZone ')).toBe(false)
    expect(readStoredTimeZone({ getItem: () => 'Not/AZone' })).toBe(AUTO_TIME_ZONE)
    expect(readStoredTimeZone({ getItem: () => 'America/New_York' })).toBe('America/New_York')
  })

  it('persists a manual zone, previews it, and restores it after remount', () => {
    const view = render(<I18nProvider><TimeZoneProvider><TimeZoneSettingsCard /></TimeZoneProvider></I18nProvider>)
    fireEvent.change(screen.getByLabelText('Time zone mode'), { target: { value: 'manual' } })
    fireEvent.change(screen.getByLabelText('IANA time zone'), { target: { value: 'Invalid/Zone' } })
    expect(screen.getByRole('alert')).toHaveTextContent('Enter a valid IANA time zone')
    expect(screen.getByRole('button', { name: 'Apply time zone' })).toBeDisabled()

    fireEvent.change(screen.getByLabelText('IANA time zone'), { target: { value: 'Asia/Tokyo' } })
    fireEvent.click(screen.getByRole('button', { name: 'Apply time zone' }))
    expect(localStorage.getItem(APP_TIME_ZONE_STORAGE_KEY)).toBe('Asia/Tokyo')
    expect(screen.getByRole('status')).toHaveTextContent('Saved in this browser')
    expect(screen.getByText(/Asia\/Tokyo/)).toHaveTextContent(/9:00\sPM/i)

    view.unmount()
    render(<I18nProvider><TimeZoneProvider><TimeZoneSettingsCard /></TimeZoneProvider></I18nProvider>)
    expect(screen.getByLabelText('Time zone mode')).toHaveValue('manual')
    expect(screen.getByLabelText('IANA time zone')).toHaveValue('Asia/Tokyo')
  })
})
