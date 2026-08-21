import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { DashboardHome } from '../components/DashboardHome'
import { AgentPanel } from '../components/AgentPanel'
import { api, TerminalMeta } from '../api'
import { useStore } from '../store'

vi.mock('../components/TerminalView', () => ({ TerminalView: () => null }))

const session = (id: string) => ({ id, name: id, status: 'active', created_at: '1' })
const terminal = (id: string, sessionName = 'session-0'): TerminalMeta => ({ id, tmux_session: sessionName, tmux_window: '0', provider: 'codex', agent_profile: 'developer', last_active: null })
const status = (id: string, value = 'idle') => ({ id, name: id, provider: 'codex', session_name: 'session-0', agent_profile: 'developer', status: value, lifecycle: 'running' as const, workflow_state: 'active' as const, last_active: null })

beforeEach(() => {
  vi.restoreAllMocks()
  useStore.setState({ sessions: [], activeSession: null, activeSessionDetail: null, terminalStatuses: {}, connected: true, snackbar: null })
  vi.spyOn(api, 'listProfiles').mockResolvedValue([])
  vi.spyOn(api, 'listProviders').mockResolvedValue([])
  vi.spyOn(api, 'getTerminalStatus').mockImplementation(async id => status(id) as never)
})

describe('Home render stability', () => {
  it.each([
    { label: 'one session', names: ['session-0'] },
    { label: 'multiple sessions', names: ['session-0', 'session-1', 'session-2'] },
  ])('expands only the canonical first Home session for $label', async ({ names }) => {
    const sessions = names.map(session)
    useStore.setState({ sessions, connected: true })
    vi.spyOn(api, 'getSession').mockImplementation(async name => ({
      session: session(name),
      terminals: [terminal(`terminal-${name}`, name)],
    }) as never)

    render(<DashboardHome onNavigate={() => {}} />)

    expect(await screen.findByRole('button', { name: `Collapse ${names[0]}` })).toHaveAttribute('aria-expanded', 'true')
    for (const name of names.slice(1)) {
      expect(screen.getByRole('button', { name: `Expand ${name}` })).toHaveAttribute('aria-expanded', 'false')
      expect(screen.queryByTestId(`agent-detail-card-terminal-${name}`)).not.toBeInTheDocument()
    }
  })

  it('keeps a successful empty Home visit empty and does not auto-open a later session', async () => {
    useStore.setState({ sessions: [], connected: true })
    vi.spyOn(api, 'getSession').mockImplementation(async name => ({
      session: session(name),
      terminals: [terminal(`terminal-${name}`, name)],
    }) as never)

    render(<DashboardHome onNavigate={() => {}} />)
    expect(await screen.findByText('No active sessions.')).toBeInTheDocument()

    act(() => useStore.setState({ sessions: [session('session-later')] }))
    expect(await screen.findByRole('button', { name: 'Expand session-later' })).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByTestId('agent-detail-card-terminal-session-later')).not.toBeInTheDocument()
  })

  it('rerenders polling status changes without remounting unchanged expanded cards and retains owner collapse state through filtering', async () => {
    const first = terminal('terminal-1')
    const second = terminal('terminal-2')
    useStore.setState({ sessions: [session('session-0')] })
    vi.spyOn(api, 'getSession').mockResolvedValue({ session: session('session-0'), terminals: [first, second] })

    render(<DashboardHome onNavigate={() => {}} />)
    const card = await screen.findByTestId('agent-detail-card-terminal-1')
    fireEvent.click(screen.getAllByRole('button', { name: 'Collapse session-0' })[0])
    expect(screen.queryByTestId('agent-detail-card-terminal-1')).not.toBeInTheDocument()

    act(() => useStore.getState().setTerminalStatuses({ 'terminal-1': 'Processing' }))
    expect(screen.getByRole('button', { name: 'Expand session-0' })).toBeInTheDocument()
    const filter = screen.getByPlaceholderText('Filter sessions...')
    fireEvent.change(filter, { target: { value: 'no-match' } })
    expect(screen.getByText('No active sessions.')).toBeInTheDocument()
    fireEvent.change(filter, { target: { value: '' } })
    expect(screen.getByRole('button', { name: 'Expand session-0' })).toBeInTheDocument()

    fireEvent.click(screen.getAllByRole('button', { name: 'Expand session-0' })[0])
    const reopened = await screen.findByTestId('agent-detail-card-terminal-1')
    act(() => useStore.getState().setTerminalStatuses({ 'terminal-1': 'Ready' }))
    expect(screen.getByTestId('agent-detail-card-terminal-1')).toBe(reopened)
    expect(card).not.toBe(reopened)
  }, 15000)

  it('initially mounts only the first session detail subtree for a 100 x 20 fixture', async () => {
    const sessions = Array.from({ length: 100 }, (_, index) => session(`session-${index}`))
    const details = new Map(sessions.map((item, index) => [item.name, Array.from({ length: 20 }, (_, terminalIndex) => terminal(`terminal-${index}-${terminalIndex}`, item.name))]))
    useStore.setState({ sessions })
    vi.spyOn(api, 'getSession').mockImplementation(async name => ({ session: session(name), terminals: details.get(name) || [] }) as never)

    render(<DashboardHome onNavigate={() => {}} />)
    await screen.findByTestId('agent-detail-card-terminal-0-19')
    expect(screen.getAllByTestId(/agent-detail-card-/)).toHaveLength(20)
    expect(screen.queryByTestId('agent-detail-card-terminal-1-0')).not.toBeInTheDocument()

    const stableCard = screen.getByTestId('agent-detail-card-terminal-0-0')
    act(() => useStore.getState().setTerminalStatuses({ 'terminal-0-0': 'Processing' }))
    expect(screen.getByTestId('agent-detail-card-terminal-0-0')).toBe(stableCard)

    fireEvent.change(screen.getByPlaceholderText('Filter sessions...'), { target: { value: 'session-1' } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Expand session-1' })[0])
    await screen.findByTestId('agent-detail-card-terminal-1-19')
    expect(screen.getAllByTestId(/agent-detail-card-/)).toHaveLength(20)
  }, 15000)
})

describe('Agents polling stability', () => {
  it('commits a batched status response once and ignores a late response from a replaced detail', async () => {
    let resolveOlder!: (value: ReturnType<typeof status>) => void
    const older = new Promise<ReturnType<typeof status>>(resolve => { resolveOlder = resolve })
    useStore.setState({
      sessions: [session('session-0'), session('session-1')],
      activeSession: 'session-0',
      activeSessionDetail: { session: session('session-0'), terminals: [terminal('old-terminal')] },
    })
    vi.spyOn(api, 'getTerminalStatus').mockImplementation(id => id === 'old-terminal' ? older as never : Promise.resolve(status(id)) as never)
    const commits: Record<string, string>[] = []
    const unsubscribe = useStore.subscribe(next => commits.push(next.terminalStatuses))
    const view = render(<AgentPanel />)

    await waitFor(() => expect(api.getTerminalStatus).toHaveBeenCalledWith('old-terminal'))
    act(() => useStore.setState({ activeSession: 'session-1', activeSessionDetail: { session: session('session-1'), terminals: [terminal('new-terminal', 'session-1')] } }))
    await waitFor(() => expect(api.getTerminalStatus).toHaveBeenCalledWith('new-terminal'))
    await act(async () => resolveOlder(status('old-terminal', 'completed')))
    await waitFor(() => expect(useStore.getState().terminalStatuses['new-terminal']).toBe('WORKFLOW_ACTIVE::Ready'))
    expect(useStore.getState().terminalStatuses['old-terminal']).toBeUndefined()
    expect(commits.filter(value => value['new-terminal'] === 'WORKFLOW_ACTIVE::Ready')).toHaveLength(1)
    unsubscribe()
    view.unmount()
  })
})
