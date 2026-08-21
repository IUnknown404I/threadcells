import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api'
import { TelegramSettings } from '../components/TelegramSettings'

const settings = {
  schema_version: 1 as const,
  enabled: false,
  chat_id: '-1001234567890',
  message_thread_id: 77,
  token_configured: true,
  token_state: 'configured' as const,
  configuration_state: 'disabled' as const,
  last_result: null,
  last_result_at: null,
  updated_at: '2026-08-21T12:00:00',
}

describe('Telegram settings', () => {
  afterEach(() => vi.restoreAllMocks())

  const operator = (authenticated = true) => vi.spyOn(api, 'getOperatorSession').mockResolvedValue({
    configured: true,
    authenticated,
    expires_in_seconds: authenticated ? 240 : 0,
    session_ttl_seconds: 300,
    verifier_reference: 'THREADCELLS_OPERATOR_VERIFIER_FILE',
  })

  it('presents one global destination and never renders the configured secret', async () => {
    operator()
    vi.spyOn(api, 'getTelegramSettings').mockResolvedValue(settings)
    render(<TelegramSettings />)

    expect(await screen.findByRole('heading', { name: 'Telegram notifications' })).toBeInTheDocument()
    expect(screen.getByText(/independent of the selected Project/)).toBeInTheDocument()
    expect(screen.getByLabelText('Telegram chat ID')).toHaveValue('-1001234567890')
    expect(screen.getByLabelText('Telegram topic ID')).toHaveValue(77)
    expect(screen.getByLabelText('Telegram bot token')).toHaveAttribute('type', 'password')
    expect(screen.getByLabelText('Telegram bot token')).toHaveValue('')
    expect(screen.getByText(/never returned by this API or saved in browser storage/)).toBeInTheDocument()
  })

  it('sets or replaces a token only through the privileged save action', async () => {
    operator()
    vi.spyOn(api, 'getTelegramSettings').mockResolvedValue(settings)
    const update = vi.spyOn(api, 'updateTelegramSettings').mockResolvedValue({
      ...settings,
      enabled: true,
      configuration_state: 'enabled',
    })
    render(<TelegramSettings />)
    await screen.findByRole('heading', { name: 'Telegram notifications' })

    fireEvent.click(screen.getByLabelText('Enable Telegram notifications'))
    fireEvent.change(screen.getByLabelText('Telegram bot token'), { target: { value: '123456789:abcdefghijklmnopqrstuvwxyz' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }))

    await waitFor(() => expect(update).toHaveBeenCalledWith({
      enabled: true,
      chat_id: '-1001234567890',
      message_thread_id: 77,
      bot_token: '123456789:abcdefghijklmnopqrstuvwxyz',
      clear_bot_token: false,
    }))
    expect(screen.getByLabelText('Telegram bot token')).toHaveValue('')
    expect(screen.getByText('Telegram settings saved.')).toBeInTheDocument()
  })

  it('keeps connection check and test delivery explicit', async () => {
    operator()
    vi.spyOn(api, 'getTelegramSettings').mockResolvedValue(settings)
    const check = vi.spyOn(api, 'checkTelegramConnection').mockResolvedValue({ ok: true, status: 'connected' })
    const send = vi.spyOn(api, 'sendTelegramTest').mockResolvedValue({ ok: true, status: 'sent' })
    render(<TelegramSettings />)
    await screen.findByRole('heading', { name: 'Telegram notifications' })

    expect(check).not.toHaveBeenCalled()
    expect(send).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Check connection' }))
    await waitFor(() => expect(check).toHaveBeenCalledTimes(1))
    fireEvent.click(screen.getByRole('button', { name: 'Send test notification' }))
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1))
  })

  it('confirms token clearing, disables delivery, and retains the destination', async () => {
    operator()
    vi.spyOn(api, 'getTelegramSettings').mockResolvedValue({
      ...settings,
      enabled: true,
      configuration_state: 'enabled',
    })
    const update = vi.spyOn(api, 'updateTelegramSettings').mockResolvedValue({
      ...settings,
      enabled: false,
      token_configured: false,
      token_state: 'missing',
      configuration_state: 'not_configured',
    })
    render(<TelegramSettings />)
    await screen.findByRole('heading', { name: 'Telegram notifications' })

    fireEvent.click(screen.getByRole('button', { name: 'Clear bot token' }))
    expect(screen.getByRole('dialog', { name: 'Clear Telegram bot token' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Clear token' }))

    await waitFor(() => expect(update).toHaveBeenCalledWith({
      enabled: false,
      chat_id: '-1001234567890',
      message_thread_id: 77,
      bot_token: null,
      clear_bot_token: true,
    }))
    expect(screen.getByLabelText('Telegram chat ID')).toHaveValue('-1001234567890')
    expect(screen.getByLabelText('Telegram topic ID')).toHaveValue(77)
    expect(screen.getByText('Telegram bot token cleared and notifications disabled.')).toBeInTheDocument()
  })

  it('keeps protected actions unavailable while operator changes are locked', async () => {
    operator(false)
    vi.spyOn(api, 'getTelegramSettings').mockResolvedValue(settings)
    render(<TelegramSettings />)

    expect(await screen.findByRole('button', { name: 'Save settings' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Check connection' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Send test notification' })).toBeDisabled()
  })
})
