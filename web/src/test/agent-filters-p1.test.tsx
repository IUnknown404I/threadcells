import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { api } from '../api'
import { AgentPanel } from '../components/AgentPanel'
import { useStore } from '../store'

const sessions = [
  { id: 'session-a', name: 'session-a', status: 'active', created_at: '1' },
  { id: 'session-b', name: 'session-b', status: 'active', created_at: '2' },
]
const terminals = {
  'session-a': [
    { id: 'agent-a', tmux_session: 'session-a', tmux_window: '0', provider: 'codex', agent_profile: 'developer', last_active: null },
    { id: 'agent-b', tmux_session: 'session-a', tmux_window: '1', provider: 'codex', agent_profile: 'reviewer', last_active: null },
  ],
  'session-b': [
    { id: 'agent-c', tmux_session: 'session-b', tmux_window: '0', provider: 'claude_code', agent_profile: 'reviewer', last_active: null },
  ],
}
const statuses = {
  'agent-a': { status: 'idle', workflow_state: 'active' },
  'agent-b': { status: 'processing', workflow_state: 'active' },
  'agent-c': { status: 'idle', workflow_state: 'waiting' },
}

describe('Agents filters P1', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    window.history.replaceState({}, '', '/')
    useStore.setState({ sessions, activeSession: null, activeSessionDetail: null, terminalStatuses: {}, snackbar: null, connected: true })
    vi.spyOn(api, 'listProviders').mockResolvedValue([{ name: 'codex', binary: 'codex', installed: true }])
    vi.spyOn(api, 'listProfiles').mockResolvedValue([
      { name: 'developer', description: 'Builds features', source: 'built-in' },
      { name: 'reviewer', description: 'Reviews changes', source: 'local' },
    ])
    vi.spyOn(api, 'listProjects').mockResolvedValue([])
    vi.spyOn(api, 'getSession').mockImplementation(async name => ({ session: sessions.find(session => session.name === name)!, terminals: terminals[name as keyof typeof terminals] }) as never)
    vi.spyOn(api, 'getTerminalStatus').mockImplementation(async id => ({ id, name: id, provider: 'codex', session_name: '', agent_profile: null, lifecycle: 'running', last_active: null, ...statuses[id as keyof typeof statuses] }) as never)
  })

  it('preserves the Sessions view and projects exact provider/workflow OR-within, AND-across semantics', async () => {
    render(<AgentPanel />)
    expect(screen.getByRole('tab', { name: 'Sessions' })).toHaveAttribute('aria-selected', 'true')
    expect(api.getSession).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('tab', { name: 'Statuses' }))
    await screen.findByRole('button', { name: 'idle' })
    fireEvent.click(screen.getByRole('button', { name: 'idle' }))
    fireEvent.click(screen.getByRole('button', { name: 'processing' }))
    fireEvent.click(screen.getAllByRole('button', { name: 'active' })[0])

    expect(screen.getByText('Found 2 agents in 1 sessions')).toBeInTheDocument()
    expect(screen.getByTestId('agent-detail-card-agent-a')).toBeInTheDocument()
    expect(screen.getByTestId('agent-detail-card-agent-b')).toBeInTheDocument()
    expect(screen.queryByTestId('agent-detail-card-agent-c')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'idle' }))
    fireEvent.click(screen.getAllByRole('button', { name: 'active' })[0])
    fireEvent.click(screen.getAllByRole('button', { name: 'waiting' })[0])
    expect(screen.getByText('No agents match the selected filters.')).toBeInTheDocument()
    fireEvent.click(screen.getAllByRole('button', { name: 'Clear filters' })[0])
    expect(screen.getByText('Found 3 agents in 2 sessions')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: 'Sessions' }))
    expect(screen.getByRole('tab', { name: 'Sessions' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('Sessions (2)')).toBeInTheDocument()
  })

  it('keeps profile multi-select state per view and retains filtered action handlers', async () => {
    const close = vi.spyOn(api, 'deleteTerminal').mockResolvedValue({ success: true })
    render(<AgentPanel />)
    fireEvent.click(screen.getByRole('tab', { name: 'Profiles' }))
    await screen.findByRole('button', { name: 'developer' })
    fireEvent.click(screen.getByRole('button', { name: 'developer' }))
    expect(screen.getByText('Found 1 agents in 1 sessions')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'reviewer' }))
    expect(screen.getByText('Found 3 agents in 2 sessions')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: 'Statuses' }))
    await screen.findByRole('button', { name: 'idle' })
    fireEvent.click(screen.getByRole('tab', { name: 'Profiles' }))
    expect(screen.getByRole('button', { name: 'developer' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'reviewer' })).toHaveAttribute('aria-pressed', 'true')

    fireEvent.click(screen.getAllByTitle('Close terminal')[0])
    expect(screen.getByRole('heading', { name: 'Close Terminal' })).toBeInTheDocument()
    expect(close).not.toHaveBeenCalled()
  })

  it('hydrates a Home status shortcut from the URL with its visible selected filter and exact result count', async () => {
    render(<AgentPanel navigationSearch="?tab=agents&agentView=statuses&agentFilter=active" />)

    await screen.findByRole('button', { name: 'Active agents' })
    expect(screen.getByRole('tab', { name: 'Statuses' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('button', { name: 'Active agents' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText('Found 3 agents in 2 sessions')).toBeInTheDocument()
  })
})
