import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, Flow, InboxMessage } from '../api'
import { FlowsPanel } from '../components/FlowsPanel'
import { InboxPanel } from '../components/InboxPanel'

const flow = (enabled: boolean): Flow => ({
  name: 'release-review',
  file_path: '/flows/release-review.md',
  schedule: '0 * * * *',
  agent_profile: 'reviewer',
  provider: 'codex',
  script: null,
  last_run: null,
  next_run: null,
  enabled,
  prompt_template: 'Review the release.',
})

const message = (id: string, terminalId: string, body: string): InboxMessage => ({
  id,
  sender_id: 'child',
  receiver_id: terminalId,
  message: body,
  status: 'delivered',
  created_at: '2026-08-22T00:00:00Z',
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('operational request ownership', () => {
  it('prevents an older Flows poll from overwriting a completed mutation', async () => {
    vi.useFakeTimers()
    let resolveOldPoll!: (value: Flow[]) => void
    const oldPoll = new Promise<Flow[]>(resolve => { resolveOldPoll = resolve })
    const list = vi.spyOn(api, 'listFlows')
      .mockResolvedValueOnce([flow(true)])
      .mockImplementationOnce(() => oldPoll)
      .mockResolvedValueOnce([flow(false)])
    vi.spyOn(api, 'listProfiles').mockResolvedValue([])
    vi.spyOn(api, 'listProviders').mockResolvedValue([])
    vi.spyOn(api, 'listProjects').mockResolvedValue([])
    vi.spyOn(api, 'disableFlow').mockResolvedValue({} as never)

    render(<FlowsPanel />)
    await act(async () => { await Promise.resolve() })
    expect(screen.getByText('enabled')).toBeInTheDocument()

    await act(async () => { await vi.advanceTimersByTimeAsync(5_000) })
    expect(list).toHaveBeenCalledTimes(2)
    fireEvent.click(screen.getByTitle('Disable flow'))
    await act(async () => { await Promise.resolve(); await Promise.resolve() })
    expect(list).toHaveBeenCalledTimes(3)
    expect(screen.getByText('disabled')).toBeInTheDocument()

    await act(async () => { resolveOldPoll([flow(true)]); await Promise.resolve() })
    expect(screen.getByText('disabled')).toBeInTheDocument()
    expect(screen.queryByText('enabled')).not.toBeInTheDocument()
  })

  it('does not present a cold-load failure as an empty Flows registry', async () => {
    vi.spyOn(api, 'listFlows').mockRejectedValue(new Error('offline'))
    vi.spyOn(api, 'listProfiles').mockResolvedValue([])
    vi.spyOn(api, 'listProviders').mockResolvedValue([])
    vi.spyOn(api, 'listProjects').mockResolvedValue([])
    render(<FlowsPanel />)
    expect(await screen.findByRole('alert')).toHaveTextContent('temporarily unavailable')
    expect(screen.queryByText('No flows configured.')).not.toBeInTheDocument()
  })

  it('exposes Flow details and the create dialog to keyboard semantics', async () => {
    vi.spyOn(api, 'listFlows').mockResolvedValue([flow(true)])
    vi.spyOn(api, 'listProfiles').mockResolvedValue([])
    vi.spyOn(api, 'listProviders').mockResolvedValue([])
    vi.spyOn(api, 'listProjects').mockResolvedValue([])
    render(<FlowsPanel />)
    const detailToggle = await screen.findByRole('button', { name: /release-review.*Every hour/i })
    expect(detailToggle).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(detailToggle)
    expect(detailToggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Review the release.')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Create Flow' }))
    expect(screen.getByRole('dialog', { name: 'Create Flow' })).toBeInTheDocument()
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('cannot commit a previous terminal Inbox response after selection changes', async () => {
    let resolveTerminalA!: (value: InboxMessage[]) => void
    const terminalA = new Promise<InboxMessage[]>(resolve => { resolveTerminalA = resolve })
    vi.spyOn(api, 'getInboxMessages').mockImplementation(terminalId => (
      terminalId === 'terminal-a'
        ? terminalA
        : Promise.resolve([message('b1', 'terminal-b', 'message for terminal B')])
    ))
    vi.spyOn(api, 'listDelegationResults').mockResolvedValue([])
    const view = render(<InboxPanel terminalId="terminal-a" onClose={() => {}} />)
    view.rerender(<InboxPanel terminalId="terminal-b" onClose={() => {}} />)
    expect(await screen.findByText('message for terminal B')).toBeInTheDocument()

    await act(async () => {
      resolveTerminalA([message('a1', 'terminal-a', 'stale message for terminal A')])
      await Promise.resolve()
    })
    await waitFor(() => expect(screen.queryByText('stale message for terminal A')).not.toBeInTheDocument())
    expect(screen.getByText('message for terminal B')).toBeInTheDocument()
  })
})
