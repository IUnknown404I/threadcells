import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { api } from '../api'
import { AgentPanel } from '../components/AgentPanel'
import { DashboardHome } from '../components/DashboardHome'
import { useStore } from '../store'
import { sessionDisplayName } from '../sessionDisplayName'
import { installUiReadModelSpies } from './ui-read-model-mocks'

vi.mock('../components/TerminalView', () => ({ TerminalView: () => null }))

const session = (id: string, created_at: string) => ({ id, name: id, status: 'active', created_at })

describe('session creation and canonical ordering', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    useStore.setState({
      sessions: [],
      activeSession: null,
      activeSessionDetail: null,
      terminalStatuses: {},
      snackbar: null,
      connected: true,
    })
    vi.spyOn(api, 'listProviders').mockResolvedValue([{ name: 'kiro_cli', binary: 'kiro', installed: true }])
    vi.spyOn(api, 'listProfiles').mockResolvedValue([{ name: 'developer', description: '', source: 'built-in' }])
    vi.spyOn(api, 'listProjects').mockResolvedValue([])
    installUiReadModelSpies()
  })

  it('falls back to an installed provider when Codex is unavailable', async () => {
    const create = vi.spyOn(api, 'createSession').mockResolvedValue({} as never)
    render(<AgentPanel />)
    fireEvent.click(screen.getByText('Create Session & Spawn Agent'))
    await waitFor(() => expect(screen.queryByText('Loading profiles...')).not.toBeInTheDocument())
    fireEvent.click(screen.getByText('Select a profile...'))
    fireEvent.click(screen.getAllByText('developer')[0])
    expect(screen.getByPlaceholderText('e.g. THREADCELLS-UI-IMPLEMENTATION')).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('e.g. THREADCELLS-UI-IMPLEMENTATION'), { target: { value: '  CAO-UI-T1-IMPLEMENTATION  ' } })
    const spawnButtons = screen.getAllByText('Create Session')
    fireEvent.click(spawnButtons[spawnButtons.length - 1])

    await waitFor(() => expect(create).toHaveBeenCalledWith('kiro_cli', 'developer', 'CAO-UI-T1-IMPLEMENTATION', undefined))
  })

  it('uses canonical runtime availability and explains disabled provider CLIs', async () => {
    vi.spyOn(api, 'listProviders').mockResolvedValue([
      { name: 'codex', binary: 'codex', installed: true, available: true, availability: 'INSTALLED_AND_READY' },
      { name: 'kiro_cli', binary: 'kiro-cli', installed: true, available: true, availability: 'UNKNOWN' },
      { name: 'claude_code', binary: 'claude', installed: false, available: false, availability: 'NOT_INSTALLED' },
    ])
    render(<AgentPanel />)
    fireEvent.click(screen.getByText('Create Session & Spawn Agent'))
    await waitFor(() => expect(screen.queryByText('Loading profiles...')).not.toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'codex' }))

    expect(screen.getByText('CLI installed and ready')).toBeInTheDocument()
    expect(screen.getByText('CLI installed · Readiness unverified')).toBeInTheDocument()
    expect(screen.getByText('Provider CLI not installed')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /claude code/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /kiro cli/i })).not.toBeDisabled()
  })

  it('preserves interior spaces and Unicode while trimming only at submission', async () => {
    const create = vi.spyOn(api, 'createSession').mockResolvedValue({} as never)
    vi.spyOn(api, 'listProviders').mockResolvedValue([
      { name: 'kiro_cli', binary: 'kiro', installed: true },
      { name: 'codex', binary: 'codex', installed: true },
    ])
    render(<AgentPanel />)
    fireEvent.click(screen.getByText('Create Session & Spawn Agent'))
    await waitFor(() => expect(screen.queryByText('Loading profiles...')).not.toBeInTheDocument())
    fireEvent.click(screen.getByText('Select a profile...'))
    fireEvent.click(screen.getAllByText('developer')[0])
    const sessionName = screen.getByPlaceholderText('e.g. THREADCELLS-UI-IMPLEMENTATION')
    fireEvent.change(sessionName, { target: { value: '  CAO — Mobile UI Adaptation  ' } })
    expect(sessionName).toHaveValue('  CAO — Mobile UI Adaptation  ')
    fireEvent.click(screen.getAllByText('Create Session').slice(-1)[0])

    await waitFor(() => expect(create).toHaveBeenCalledWith(
      'codex',
      'developer',
      'CAO — Mobile UI Adaptation',
      undefined,
    ))
  })

  it('replaces every dot in a Create Session name', async () => {
    const create = vi.spyOn(api, 'createSession').mockResolvedValue({} as never)
    render(<AgentPanel />)
    fireEvent.click(screen.getByText('Create Session & Spawn Agent'))
    await waitFor(() => expect(screen.queryByText('Loading profiles...')).not.toBeInTheDocument())
    fireEvent.click(screen.getByText('Select a profile...'))
    fireEvent.click(screen.getAllByText('developer')[0])
    fireEvent.change(screen.getByPlaceholderText('e.g. THREADCELLS-UI-IMPLEMENTATION'), { target: { value: 'a.b.c' } })
    fireEvent.click(screen.getAllByText('Create Session').slice(-1)[0])
    await waitFor(() => expect(create).toHaveBeenCalledWith('kiro_cli', 'developer', 'a_b_c', undefined))
  })

  it('keeps a user-selected main Create Session provider', async () => {
    const create = vi.spyOn(api, 'createSession').mockResolvedValue({} as never)
    vi.spyOn(api, 'listProviders').mockResolvedValue([
      { name: 'codex', binary: 'codex', installed: true },
      { name: 'kiro_cli', binary: 'kiro', installed: true },
    ])
    render(<AgentPanel />)
    fireEvent.click(screen.getByText('Create Session & Spawn Agent'))
    await waitFor(() => expect(screen.queryByText('Loading profiles...')).not.toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'codex' }))
    fireEvent.click(screen.getByRole('button', { name: /^kiro cli/i }))
    fireEvent.click(screen.getByText('Select a profile...'))
    fireEvent.click(screen.getAllByText('developer')[0])
    fireEvent.click(screen.getAllByText('Create Session').slice(-1)[0])

    await waitFor(() => expect(create).toHaveBeenCalledWith('kiro_cli', 'developer', undefined, undefined))
  })

  it('uses the selected project without a working-directory override and updates the displayed path', async () => {
    const create = vi.spyOn(api, 'createSession').mockResolvedValue({} as never)
    vi.spyOn(api, 'listProjects').mockResolvedValue([
      { projectId: 'project-a', name: 'Project A', path: '/work/project-a', description: null, isDefault: true },
      { projectId: 'project-b', name: 'Project B', path: '/work/project-b', description: null, isDefault: false },
    ])
    render(<AgentPanel />)
    await screen.findByRole('button', { name: 'Create Session & Spawn Agent' })
    await waitFor(() => expect(api.listProjects).toHaveBeenCalled())
    fireEvent.click(screen.getByText('Create Session & Spawn Agent'))

    expect(await screen.findByText('/work/project-a')).toBeInTheDocument()
    expect(screen.queryByText('Working Directory')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Default · Project A' }))
    fireEvent.click(screen.getByText('Project B'))
    expect(screen.getByText('/work/project-b')).toBeInTheDocument()

    fireEvent.click(screen.getByText('Select a profile...'))
    fireEvent.click(screen.getAllByText('developer')[0])
    fireEvent.click(screen.getAllByText('Create Session').slice(-1)[0])
    await waitFor(() => expect(create).toHaveBeenCalledWith('kiro_cli', 'developer', undefined, undefined, 'project-b'))
  })

  it('omits a blank session name and shows duplicate-name errors in the modal', async () => {
    const create = vi.spyOn(api, 'createSession').mockRejectedValue(new Error("Session 'cao-duplicate' already exists"))
    render(<AgentPanel />)
    fireEvent.click(screen.getByText('Create Session & Spawn Agent'))
    await waitFor(() => expect(screen.queryByText('Loading profiles...')).not.toBeInTheDocument())
    fireEvent.click(screen.getByText('Select a profile...'))
    fireEvent.click(screen.getAllByText('developer')[0])
    fireEvent.change(screen.getByPlaceholderText('e.g. THREADCELLS-UI-IMPLEMENTATION'), { target: { value: '   ' } })
    const spawnButtons = screen.getAllByText('Create Session')
    fireEvent.click(spawnButtons[spawnButtons.length - 1])

    await waitFor(() => expect(create).toHaveBeenCalledWith('kiro_cli', 'developer', undefined, undefined))
    expect(screen.getByRole('alert')).toHaveTextContent("Session 'cao-duplicate' already exists")
  })

  it('keeps the UI responsive and sends one explicit Codex spawn while startup is pending', async () => {
    let resolveCreate!: () => void
    const create = vi.spyOn(api, 'createSession').mockImplementation(() => new Promise<void>(resolve => { resolveCreate = resolve }) as never)
    vi.spyOn(api, 'listProviders').mockResolvedValue([{ name: 'codex', binary: 'codex', installed: true }])
    render(<AgentPanel />)
    fireEvent.click(screen.getByText('Create Session & Spawn Agent'))
    await waitFor(() => expect(screen.queryByText('Loading profiles...')).not.toBeInTheDocument())
    fireEvent.click(screen.getByText('Select a profile...'))
    fireEvent.click(screen.getAllByText('developer')[0])
    const spawnButton = screen.getAllByText('Create Session').slice(-1)[0]
    fireEvent.click(spawnButton)
    fireEvent.click(spawnButton)

    expect(screen.getByRole('button', { name: 'Creating...' })).toHaveAttribute('aria-busy', 'true')
    expect(screen.queryByText('Working Directory')).not.toBeInTheDocument()
    expect(create).toHaveBeenCalledTimes(1)
    expect(create).toHaveBeenCalledWith('codex', 'developer', undefined, undefined)

    resolveCreate()
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Creating...' })).not.toBeInTheDocument())
  })

  it('requires structured operator authorization for a manual XHigh owner launch', async () => {
    const create = vi.spyOn(api, 'createSession').mockResolvedValue({} as never)
    const login = vi.spyOn(api, 'createOperatorSession').mockResolvedValue({ authenticated: true })
    const grant = { launch_id: 'launch-1', grant: 'one-use-token', expires_in_seconds: 120 }
    const issueGrant = vi.spyOn(api, 'createXHighGrant').mockResolvedValue(grant)
    vi.spyOn(api, 'listProviders').mockResolvedValue([{ name: 'codex', binary: 'codex', installed: true }])
    vi.spyOn(api, 'listProfiles').mockResolvedValue([
      {
        name: 'critical_sol_xhigh_owner',
        description: 'Exceptional owner executor',
        source: 'built-in',
        execution_mode: 'owner_executor',
        owner_authorization_required: true,
        revision_id: 'profile-rev-1',
      },
    ])

    render(<AgentPanel />)
    fireEvent.click(screen.getByText('Create Session & Spawn Agent'))
    await waitFor(() => expect(screen.queryByText('Loading profiles...')).not.toBeInTheDocument())
    fireEvent.click(screen.getByText('Select a profile...'))
    fireEvent.click(screen.getAllByText('critical_sol_xhigh_owner')[0])

    expect(screen.getByText('Exceptional XHigh owner-executor')).toBeInTheDocument()
    const submit = screen.getAllByRole('button', { name: 'Create Session' }).slice(-1)[0]
    expect(submit).toBeDisabled()
    fireEvent.click(screen.getByLabelText('Confirm exceptional XHigh launch'))
    fireEvent.change(screen.getByLabelText('Operator secret'), { target: { value: 'operator-passphrase' } })
    expect(submit).not.toBeDisabled()
    fireEvent.click(submit)

    await waitFor(() => expect(login).toHaveBeenCalledWith('operator-passphrase'))
    expect(issueGrant).toHaveBeenCalledWith({
      agent_profile: 'critical_sol_xhigh_owner',
      provider: 'codex',
      working_directory: undefined,
      requested_session_name: undefined,
      project_id: undefined,
      launch_mode: 'new_session',
      confirmed: true,
    })
    expect(create).toHaveBeenCalledWith(
      'codex',
      'critical_sol_xhigh_owner',
      undefined,
      undefined,
      '',
      grant,
    )
  })

  it('opens Create Session from a one-shot navigation intent without affecting Add Agent', async () => {
    const consumed = vi.fn()
    const { rerender } = render(<AgentPanel navigationIntent="create-session" onNavigationIntentConsumed={consumed} />)

    await screen.findByRole('heading', { name: 'Create Session & Spawn Agent' })
    expect(consumed).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('Add Agent')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    rerender(<AgentPanel navigationIntent={null} onNavigationIntentConsumed={consumed} />)
    expect(screen.queryByRole('heading', { name: 'Create Session & Spawn Agent' })).not.toBeInTheDocument()
    expect(consumed).toHaveBeenCalledTimes(1)
  })

  it('replaces the competing AgentPanel message input with a Workflow Composer shortcut', async () => {
    const terminal = { id: 'codex-terminal', tmux_session: 'cao-codex', tmux_window: '0', provider: 'codex', agent_profile: 'developer', last_active: null }
    useStore.setState({
      sessions: [session('cao-codex', '100')],
      activeSession: 'cao-codex',
      activeSessionDetail: { session: session('cao-codex', '100'), terminals: [terminal] },
    })
    render(<AgentPanel />)

    const card = await screen.findByTestId('agent-session-cao-codex')
    expect(card.querySelector('span.font-mono')).toHaveTextContent('codex')
    fireEvent.click(screen.getByRole('button', { name: 'Expand codex' }))
    expect(await screen.findByRole('button', { name: 'Open Workflow Composer' })).toBeInTheDocument()
    expect(screen.queryByText('Message agent...')).not.toBeInTheDocument()
    expect(screen.queryByPlaceholderText('Type a message...')).not.toBeInTheDocument()
  })

  it('keeps one selected session detail inline, movable, collapsible, and isolated from its actions', async () => {
    const sessionA = session('cao-inline-a', '200')
    const sessionB = session('cao-inline-b', '100')
    const terminalA = { id: 'inline-terminal-a', tmux_session: sessionA.name, tmux_window: '0', provider: 'codex', agent_profile: 'developer', last_active: null }
    const terminalB = { id: 'inline-terminal-b', tmux_session: sessionB.name, tmux_window: '0', provider: 'codex', agent_profile: 'reviewer', last_active: null }
    vi.spyOn(api, 'getSession').mockImplementation(async name => ({
      session: name === sessionA.name ? sessionA : sessionB,
      terminals: name === sessionA.name ? [terminalA] : [terminalB],
    }) as never)
    useStore.setState({
      sessions: [sessionA, sessionB],
      terminalStatuses: {
        [terminalB.id]: { lifecycle: 'exited', status: 'idle' },
      } as never,
    })
    render(<AgentPanel />)

    const aToggle = await screen.findByRole('button', { name: `Expand ${sessionDisplayName(sessionA.name)}` })
    fireEvent.keyDown(aToggle, { key: 'Enter' })
    const aDetail = await screen.findByTestId(`agent-session-detail-${sessionA.id}`)
    expect(within(screen.getByTestId(`agent-session-${sessionA.id}`)).getByTestId(`agent-session-detail-${sessionA.id}`)).toBe(aDetail)
    expect(screen.queryByTestId(`agent-session-detail-${sessionB.id}`)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: `Expand ${sessionDisplayName(sessionB.name)}` }))
    const bDetail = await screen.findByTestId(`agent-session-detail-${sessionB.id}`)
    expect(screen.queryByTestId(`agent-session-detail-${sessionA.id}`)).not.toBeInTheDocument()
    expect(within(screen.getByTestId(`agent-session-${sessionB.id}`)).getByTestId(`agent-session-detail-${sessionB.id}`)).toBe(bDetail)
    expect(screen.getByTestId(`agent-session-${sessionB.id}`).nextElementSibling).toBeNull()

    fireEvent.click(within(bDetail).getByTitle('Delete exited terminal history'))
    expect(screen.getByRole('heading', { name: 'Delete Exited Terminal' })).toBeInTheDocument()
    expect(screen.getByTestId(`agent-session-detail-${sessionB.id}`)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: `Collapse ${sessionDisplayName(sessionB.name)}` })).toHaveAttribute('aria-expanded', 'true')
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    fireEvent.keyDown(screen.getByRole('button', { name: `Collapse ${sessionDisplayName(sessionB.name)}` }), { key: ' ' })
    await waitFor(() => expect(screen.queryByTestId(`agent-session-detail-${sessionB.id}`)).not.toBeInTheDocument())
  })

  it('shows each Agents session total next to its status without disturbing row actions', async () => {
    const sessionA = { ...session('cao-count-a', '200'), status: 'active' }
    const sessionB = { ...session('cao-count-b', '100'), status: 'history' }
    const terminalsBySession = {
      [sessionA.name]: [
        { id: 'count-a-1', tmux_session: sessionA.name, tmux_window: '0', provider: 'codex', agent_profile: 'developer', last_active: null },
        { id: 'count-a-2', tmux_session: sessionA.name, tmux_window: '1', provider: 'codex', agent_profile: 'reviewer', last_active: null },
      ],
      [sessionB.name]: [
        { id: 'count-b-1', tmux_session: sessionB.name, tmux_window: '0', provider: 'codex', agent_profile: 'developer', last_active: null },
      ],
    }
    vi.spyOn(api, 'getSession').mockImplementation(async name => ({
      session: name === sessionA.name ? sessionA : sessionB,
      terminals: terminalsBySession[name as keyof typeof terminalsBySession],
    }) as never)
    useStore.setState({ sessions: [sessionA, sessionB] })
    render(<AgentPanel />)

    const rowA = await screen.findByTestId(`agent-session-${sessionA.id}`)
    const rowB = screen.getByTestId(`agent-session-${sessionB.id}`)
    const statusA = within(rowA).getByText('active')
    const countA = within(rowA).getByTestId(`agent-session-count-${sessionA.id}`)
    const statusB = within(rowB).getByText('history')
    const countB = within(rowB).getByTestId(`agent-session-count-${sessionB.id}`)

    expect(countA).toHaveTextContent('Agents: 2')
    expect(countB).toHaveTextContent('Agents: 1')
    expect(countA.nextElementSibling).toBe(statusA)
    expect(countB.nextElementSibling).toBe(statusB)
    expect(countA).toHaveClass('shrink-0', 'text-xs', 'text-gray-500')

    fireEvent.click(within(rowA).getByRole('button', { name: `Expand ${sessionDisplayName(sessionA.name)}` }))
    expect(await within(rowA).findByTestId(`agent-session-detail-${sessionA.id}`)).toBeInTheDocument()
    expect(within(rowA).getByTestId(`agent-session-count-${sessionA.id}`)).toHaveTextContent('Agents: 2')
    expect(within(rowA).getByTitle('Delete session')).toBeInTheDocument()
  })

  it('does not add a session-name field to Add Agent', async () => {
    useStore.setState({
      sessions: [session('cao-existing', '100')],
      activeSession: 'cao-existing',
      activeSessionDetail: { session: session('cao-existing', '100'), terminals: [] },
    })
    render(<AgentPanel />)
    fireEvent.click(await screen.findByRole('button', { name: 'Expand existing' }))
    fireEvent.click(await screen.findByText('Add Agent'))

    expect(screen.queryByText('Session name')).not.toBeInTheDocument()
  })

  it('blocks Add Agent truthfully for a historical session', async () => {
    const history = {
      ...session('lifetime-history', '100'),
      name: 'cao-history',
      status: 'history',
    }
    useStore.setState({ sessions: [history] })
    render(<AgentPanel />)

    fireEvent.click(await screen.findByRole('button', { name: 'Expand history' }))
    const add = screen.getByRole('button', { name: 'Add Agent' })
    expect(add).toBeDisabled()
    expect(add).toHaveAttribute('title', 'Historical sessions cannot accept new agents')
  })

  it('keeps ordinary Add Agent authorization-free and shows the inherited path as muted information', async () => {
    const terminal = { id: 'codex-terminal', tmux_session: 'cao-existing', tmux_window: '0', provider: 'codex', agent_profile: 'developer', last_active: null }
    const addTerminal = vi.spyOn(api, 'addTerminalToSession').mockResolvedValue({} as never)
    vi.spyOn(api, 'listProviders').mockResolvedValue([
      { name: 'kiro_cli', binary: 'kiro', installed: true },
      { name: 'codex', binary: 'codex', installed: true },
    ])
    vi.spyOn(api, 'getSession').mockResolvedValue({ session: session('cao-existing', '100'), terminals: [terminal] })
    const getSessionWorkingDirectory = vi.spyOn(api, 'getSessionWorkingDirectory').mockResolvedValue({ working_directory: '/srv/session-root' })
    vi.spyOn(api, 'getWorkingDirectory').mockResolvedValue({ working_directory: '/srv/child-terminal' })
    useStore.setState({
      sessions: [session('cao-existing', '100')],
      activeSession: 'cao-existing',
      activeSessionDetail: { session: session('cao-existing', '100'), terminals: [terminal] },
    })
    render(<AgentPanel />)
    fireEvent.click(await screen.findByRole('button', { name: 'Expand existing' }))
    expect(getSessionWorkingDirectory).not.toHaveBeenCalled()
    fireEvent.click(screen.getByText('Add Agent'))
    await waitFor(() => expect(getSessionWorkingDirectory).toHaveBeenCalledWith('cao-existing'))

    const resolvedPath = screen.getByTestId('add-agent-resolved-working-directory')
    expect(resolvedPath).toHaveTextContent('/srv/session-root')
    expect(resolvedPath).toHaveClass('text-gray-500')
    expect(screen.queryByText('Working Directory', { exact: true })).not.toBeInTheDocument()
    expect(screen.queryByText('Exceptional XHigh owner-executor')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Operator secret')).not.toBeInTheDocument()
    expect(screen.getAllByRole('button').some(button => button.textContent?.includes('codex'))).toBe(true)
    fireEvent.click(screen.getByText('Select a profile...'))
    fireEvent.click(screen.getAllByText('developer')[0])
    fireEvent.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(addTerminal).toHaveBeenCalledWith('cao-existing', 'codex', 'developer', '/srv/session-root'))
  })

  it('keeps Add Agent project inheritance blank until deliberately selected and resolves its canonical path', async () => {
    const terminal = { id: 'codex-terminal', tmux_session: 'cao-existing', tmux_window: '0', provider: 'codex', agent_profile: 'developer', last_active: null }
    const addTerminal = vi.spyOn(api, 'addTerminalToSession').mockResolvedValue({} as never)
    vi.spyOn(api, 'listProjects').mockResolvedValue([
      { projectId: 'a', name: 'A', path: '/a', description: null, isDefault: true },
      { projectId: 'b', name: 'B', path: '/b', description: null, isDefault: false },
    ])
    vi.spyOn(api, 'getSession').mockResolvedValue({ session: session('cao-existing', '100'), terminals: [terminal] })
    vi.spyOn(api, 'getSessionWorkingDirectory').mockResolvedValue({ working_directory: '/legacy/a' })
    useStore.setState({ sessions: [session('cao-existing', '100')], activeSession: 'cao-existing', activeSessionDetail: { session: session('cao-existing', '100'), terminals: [terminal] } })
    render(<AgentPanel />)
    await waitFor(() => expect(api.listProjects).toHaveBeenCalled())
    fireEvent.click(await screen.findByRole('button', { name: 'Expand existing' }))
    expect(api.getSessionWorkingDirectory).not.toHaveBeenCalled()
    fireEvent.click(screen.getByText('Add Agent'))
    await waitFor(() => expect(api.getSessionWorkingDirectory).toHaveBeenCalledWith('cao-existing'))
    expect(screen.getByRole('button', { name: 'Select a project to work in…' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Default · A' })).not.toBeInTheDocument()
    expect(screen.getByTestId('add-agent-resolved-working-directory')).toHaveTextContent('/legacy/a')
    fireEvent.click(screen.getByRole('button', { name: 'Select a project to work in…' }))
    fireEvent.click(screen.getByText('B'))
    expect(screen.getByTestId('add-agent-resolved-working-directory')).toHaveTextContent('/b')
    fireEvent.click(screen.getByText('Select a profile...'))
    fireEvent.click(screen.getAllByText('developer')[0])
    fireEvent.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(addTerminal).toHaveBeenCalledWith('cao-existing', 'kiro_cli', 'developer', '/b', 'b'))
  })

  it('authorizes an XHigh Add Agent with the shared one-use existing-session grant and clears the secret', async () => {
    const existing = { ...session('lifetime-owner-existing', '100'), name: 'cao-owner-existing' }
    const terminal = { id: 'existing-agent', tmux_session: existing.name, tmux_window: '0', provider: 'codex', agent_profile: 'developer', last_active: null }
    const grant = { launch_id: 'add-launch-1', grant: 'one-use-add-token', expires_in_seconds: 60 }
    const login = vi.spyOn(api, 'createOperatorSession')
      .mockRejectedValueOnce(new Error('Not authorized: operator authentication failed'))
      .mockResolvedValue({ authenticated: true })
    const issueGrant = vi.spyOn(api, 'createXHighGrant').mockResolvedValue(grant)
    const addTerminal = vi.spyOn(api, 'addTerminalToSession').mockResolvedValue({} as never)
    vi.spyOn(api, 'listProviders').mockResolvedValue([{ name: 'codex', binary: 'codex', installed: true }])
    vi.spyOn(api, 'listProfiles').mockResolvedValue([
      { name: 'developer', description: 'Ordinary worker', source: 'built-in' },
      { name: 'critical_sol_xhigh_owner', description: 'Exceptional owner executor', source: 'built-in', execution_mode: 'owner_executor', owner_authorization_required: true },
    ])
    vi.spyOn(api, 'getSession').mockResolvedValue({ session: existing, terminals: [terminal] })
    vi.spyOn(api, 'getSessionWorkingDirectory').mockResolvedValue({ working_directory: '/srv/session-owner-root' })
    vi.spyOn(api, 'getWorkingDirectory').mockResolvedValue({ working_directory: '/srv/session-owner-root' })
    useStore.setState({ sessions: [existing], activeSession: existing.id, activeSessionDetail: { session: existing, terminals: [terminal] } })

    render(<AgentPanel />)
    fireEvent.click(await screen.findByRole('button', { name: 'Expand owner-existing' }))
    expect(api.getSessionWorkingDirectory).not.toHaveBeenCalled()
    fireEvent.click(screen.getByText('Add Agent'))
    await waitFor(() => expect(api.getSessionWorkingDirectory).toHaveBeenCalledWith(existing.id))
    fireEvent.click(screen.getByText('Select a profile...'))
    fireEvent.click(screen.getAllByText('critical_sol_xhigh_owner')[0])

    expect(screen.getByText('Exceptional XHigh owner-executor')).toBeInTheDocument()
    const submit = screen.getByRole('button', { name: 'Add' })
    expect(submit).toBeDisabled()
    fireEvent.click(screen.getByLabelText('Confirm exceptional XHigh launch'))
    expect(submit).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Operator secret'), { target: { value: 'wrong-secret' } })
    expect(submit).not.toBeDisabled()
    fireEvent.click(submit)

    expect(await screen.findByText(/operator authentication failed/)).toBeInTheDocument()
    expect(issueGrant).not.toHaveBeenCalled()
    expect(addTerminal).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText('Operator secret'), { target: { value: 'correct-operator-secret' } })
    fireEvent.click(submit)
    await waitFor(() => expect(addTerminal).toHaveBeenCalledWith(
      existing.id,
      'codex',
      'critical_sol_xhigh_owner',
      '/srv/session-owner-root',
      undefined,
      grant,
    ))
    expect(issueGrant).toHaveBeenCalledWith({
      agent_profile: 'critical_sol_xhigh_owner',
      provider: 'codex',
      working_directory: '/srv/session-owner-root',
      requested_session_name: existing.id,
      project_id: undefined,
      launch_mode: 'existing_session',
      confirmed: true,
    })
    expect(login).toHaveBeenNthCalledWith(1, 'wrong-secret')
    expect(login).toHaveBeenNthCalledWith(2, 'correct-operator-secret')
    await waitFor(() => expect(screen.queryByLabelText('Operator secret')).not.toBeInTheDocument())

    fireEvent.click(screen.getByText('Add Agent'))
    fireEvent.click(screen.getByText('Select a profile...'))
    fireEvent.click(screen.getAllByText('critical_sol_xhigh_owner')[0])
    expect(screen.getByLabelText('Operator secret')).toHaveValue('')
    expect(screen.getByLabelText('Confirm exceptional XHigh launch')).not.toBeChecked()
  })

  it('shows total and active provider processes on Home and keeps metrics navigable', async () => {
    const terminal = { id: 'live-terminal', tmux_session: 'cao-live', tmux_window: '0', provider: 'codex', agent_profile: 'developer', last_active: null }
    const completedTerminal = { id: 'completed-terminal', tmux_session: 'cao-live', tmux_window: '1', provider: 'codex', agent_profile: 'developer', last_active: null }
    const exitedTerminal = { id: 'exited-terminal', tmux_session: 'cao-live', tmux_window: '2', provider: 'codex', agent_profile: 'developer', last_active: null }
    const onNavigate = vi.fn()
    useStore.setState({ sessions: [session('cao-live', '100')] })
    vi.spyOn(api, 'getSession').mockResolvedValue({ session: session('cao-live', '100'), terminals: [terminal, completedTerminal, exitedTerminal] })
    vi.spyOn(api, 'getTerminalStatus').mockImplementation(async id => ({
      ...(id === terminal.id ? terminal : id === completedTerminal.id ? completedTerminal : exitedTerminal),
      status: id === completedTerminal.id ? 'completed' : 'idle',
      lifecycle: id === exitedTerminal.id ? 'exited' : 'running',
      workflow_state: id === completedTerminal.id ? 'completed' : 'active',
    }) as never)
    render(<DashboardHome onNavigate={onNavigate} />)

    await waitFor(() => expect(screen.getByRole('button', { name: 'View total agents' })).toHaveTextContent('3'))
    fireEvent.click(screen.getByRole('button', { name: 'View active agents' }))
    expect(onNavigate).toHaveBeenCalledWith({ tab: 'agents', filter: 'active' })
    fireEvent.click(screen.getByRole('button', { name: 'Create Session & Spawn Agent' }))
    expect(onNavigate).toHaveBeenLastCalledWith({ tab: 'agents', intent: 'create-session' })
  })

  it('shows a session project badge only for one complete exact terminal context and keeps Home copy English', async () => {
    const exact = session('cao-project-exact', '300')
    const noProject = session('cao-project-none', '200')
    const unresolved = session('cao-project-partial', '100')
    const mixed = session('cao-project-mixed', '50')
    const projectTerminal = (id: string, project_id = 'project-a', name = 'Project A', path = '/work/project-a') => ({ id, tmux_session: exact.name, tmux_window: '0', provider: 'codex', agent_profile: 'developer', last_active: null, project_id, project_name: name, project_path: path })
    const terminalsBySession = {
      [exact.name]: [projectTerminal('exact-a'), projectTerminal('exact-b')],
      [noProject.name]: [{ id: 'none-a', tmux_session: noProject.name, tmux_window: '0', provider: 'codex', agent_profile: 'developer', last_active: null }],
      [unresolved.name]: [{ ...projectTerminal('partial-a'), tmux_session: unresolved.name, project_path: null }],
      [mixed.name]: [projectTerminal('mixed-a'), { ...projectTerminal('mixed-b', 'project-b', 'Project B', '/work/project-b'), tmux_session: mixed.name }],
    }
    useStore.setState({ sessions: [exact, noProject, unresolved, mixed] })
    vi.spyOn(api, 'getSession').mockImplementation(async name => ({ session: { [exact.name]: exact, [noProject.name]: noProject, [unresolved.name]: unresolved, [mixed.name]: mixed }[name], terminals: terminalsBySession[name as keyof typeof terminalsBySession] }) as never)
    render(<DashboardHome onNavigate={() => {}} />)

    const exactHeader = await screen.findByTestId(`session-header-${exact.id}`)
    expect(within(exactHeader).getByText('Project: Project A')).toBeInTheDocument()
    expect(within(screen.getByTestId(`session-header-${noProject.id}`)).queryByText(/^Project:/)).not.toBeInTheDocument()
    expect(within(screen.getByTestId(`session-header-${unresolved.id}`)).queryByText(/^Project:/)).not.toBeInTheDocument()
    expect(within(screen.getByTestId(`session-header-${mixed.id}`)).queryByText(/^Project:/)).not.toBeInTheDocument()
    expect(screen.getAllByText('Sessions')).toHaveLength(2)
    expect(screen.getByText('Total agents')).toBeInTheDocument()
    expect(screen.getByText('Active agents')).toBeInTheDocument()
    expect(screen.getByText('Ready / waiting')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Filter sessions…')).toBeInTheDocument()
  })

  it('keeps the backend-provided newest-first order on Home', async () => {
    useStore.setState({ sessions: [session('cao-newest', '200'), session('cao-oldest', '100')] })
    vi.spyOn(api, 'getSession').mockResolvedValue({ session: session('cao-newest', '200'), terminals: [] })
    const { container } = render(<DashboardHome onNavigate={() => {}} />)

    await waitFor(() => expect(screen.getByText('newest')).toBeInTheDocument())
    const rendered = Array.from(container.querySelectorAll('span.font-mono')).map(node => node.textContent)
    expect(rendered.slice(0, 2)).toEqual(['newest', 'oldest'])

    useStore.getState().setTerminalStatus('unrelated-terminal', 'processing')
    expect(Array.from(container.querySelectorAll('span.font-mono')).map(node => node.textContent).slice(0, 2)).toEqual(['newest', 'oldest'])
  })

  it('toggles the Home accordion from its title surface and chevron while isolating controls and showing the selected surface', async () => {
    const terminal = { id: 'header-terminal', tmux_session: 'cao-header', tmux_window: '0', provider: 'codex', agent_profile: 'developer', last_active: null }
    useStore.setState({ sessions: [session('cao-header', '100')] })
    vi.spyOn(api, 'getSession').mockResolvedValue({ session: session('cao-header', '100'), terminals: [terminal] })
    render(<DashboardHome onNavigate={() => {}} />)

    await waitFor(() => expect(screen.getByTestId('session-header-cao-header')).toBeInTheDocument())
    const header = screen.getByTestId('session-header-cao-header')
    const card = screen.getByTestId('home-session-cao-header')
    expect(within(header).getByText('header')).not.toBeInstanceOf(HTMLButtonElement)
    expect(card).toHaveClass('bg-gray-800/60', 'border-gray-700/50')

    const titleSurface = within(header).getByRole('button', { name: 'Expand header' })
    fireEvent.click(titleSurface)
    expect(card).toHaveClass('bg-emerald-900/30', 'border-emerald-700/50')
    expect(await screen.findByTestId('session-agent-container')).toBeInTheDocument()

    fireEvent.keyDown(within(header).getByRole('button', { name: 'Collapse header' }), { key: ' ' })
    expect(card).toHaveClass('bg-gray-800/60', 'border-gray-700/50')

    fireEvent.keyDown(within(header).getByRole('button', { name: 'Expand header' }), { key: 'Enter' })
    expect(card).toHaveClass('bg-emerald-900/30', 'border-emerald-700/50')
    fireEvent.click(within(header).getByRole('button', { name: 'Collapse header using chevron' }))
    expect(card).toHaveClass('bg-gray-800/60', 'border-gray-700/50')

    fireEvent.click(within(header).getByRole('button', { name: 'Expand header using chevron' }))
    expect(card).toHaveClass('bg-emerald-900/30', 'border-emerald-700/50')

    fireEvent.click(within(header).getByRole('button', { name: 'Delete header' }))
    expect(screen.getByRole('heading', { name: 'Delete Session' })).toBeInTheDocument()
    expect(card).toHaveClass('bg-emerald-900/30', 'border-emerald-700/50')

    expect(screen.getByTestId('session-header-cao-header')).toBe(header)
    expect(within(header).getByRole('button', { name: 'Collapse header' })).toBeInTheDocument()
    expect(within(header).getByRole('button', { name: 'Collapse header using chevron' })).toBeInTheDocument()
  })

  it('keeps Home List/Grid in the status row and switches the same ordered agents', async () => {
    const terminals = [
      { id: 'z-created-first', tmux_session: 'cao-summary', tmux_window: '0', provider: 'codex', agent_profile: 'developer_sol_medium', last_active: '100' },
      { id: 'a-created-second', tmux_session: 'cao-summary', tmux_window: '1', provider: 'claude_code', agent_profile: 'reviewer_sol_high', last_active: '999' },
      { id: 'm-created-third', tmux_session: 'cao-summary', tmux_window: '2', provider: 'kiro_cli', agent_profile: 'developer_terra_medium', last_active: '200' },
    ]
    useStore.setState({ sessions: [session('cao-summary', '100')], terminalStatuses: { 'z-created-first': 'WORKFLOW_TERMINAL::Exited', 'a-created-second': 'WORKFLOW_OWNER_GATE::Ready', 'm-created-third': 'WORKFLOW_ACTIVE::Processing' } })
    vi.spyOn(api, 'getSession').mockResolvedValue({ session: session('cao-summary', '100'), terminals })
    render(<DashboardHome onNavigate={() => {}} />)

    const header = await screen.findByTestId('session-header-cao-summary')
    const summary = screen.getByLabelText('Session status')
    const actions = within(header).getByTestId('session-actions-cao-summary')
    const statusActions = screen.getByTestId('session-status-actions-cao-summary')
    const layoutControls = within(summary).getByRole('group', { name: 'Agent layout' })
    const list = within(summary).getByRole('button', { name: 'List view' })
    const grid = within(summary).getByRole('button', { name: 'Grid view' })
    const statusBadges = screen.getByTestId('session-status-badges-cao-summary')

    expect(header).toHaveClass('grid', 'grid-cols-[minmax(0,1fr)_auto]', 'sm:grid-cols-[minmax(0,1fr)_auto_auto]')
    expect(within(header).getByTestId('session-title-row-cao-summary')).toHaveClass('col-span-2', 'sm:col-span-1')
    expect(within(header).getByTestId('session-metadata-cao-summary')).toContainElement(within(header).getByText('3 agents'))
    expect(actions).toContainElement(within(header).getByRole('button', { name: 'Delete summary' }))
    expect(actions).toHaveClass('col-span-2', 'flex-wrap', 'justify-end', 'sm:col-span-1', 'sm:flex-nowrap')
    expect(within(actions).queryByRole('group', { name: 'Agent layout' })).not.toBeInTheDocument()
    expect(layoutControls).toHaveClass('inline-flex')
    expect(statusBadges.compareDocumentPosition(statusActions) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

    fireEvent.click(within(header).getByRole('button', { name: 'Expand summary' }))
    expect(await within(header).findByRole('button', { name: 'Collapse summary' })).toBeInTheDocument()
    expect(list).toHaveAttribute('aria-pressed', 'true')
    expect(list.className).toContain('h-9')
    expect(list.className).toContain('w-9')
    expect(grid).toHaveClass('inline-flex', 'h-9', 'w-9', 'items-center', 'justify-center')
    expect(screen.getByTestId('session-agent-container').previousElementSibling).toBeNull()
    const renderedAgentIds = () => within(screen.getByTestId('session-agent-container'))
      .getAllByTestId(/^agent-detail-card-/)
      .map(card => card.getAttribute('data-testid'))
    const expectedOrder = ['agent-detail-card-z-created-first', 'agent-detail-card-a-created-second', 'agent-detail-card-m-created-third']
    expect(renderedAgentIds()).toEqual(expectedOrder)
    for (const terminalId of ['z-created-first', 'a-created-second', 'm-created-third']) {
      const card = screen.getByTestId(`agent-detail-card-${terminalId}`)
      expect(within(card).getByTitle('Inbox')).toBeInTheDocument()
      expect(within(card).getByTitle('Output')).toBeInTheDocument()
      expect(within(card).getByRole('button', { name: 'Terminal' })).toBeInTheDocument()
      expect(within(card).getByTitle('Graceful Exit')).toBeInTheDocument()
      expect(within(card).getByTitle('Gracefully exit this terminal before deleting it')).toBeDisabled()
    }

    fireEvent.click(grid)
    expect(grid).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByTestId('session-agent-container')).toHaveClass('space-y-2', 'md:grid', 'md:grid-cols-2')
    expect(renderedAgentIds()).toEqual(expectedOrder)

    fireEvent.click(list)
    expect(list).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByTestId('session-agent-container')).toHaveClass('space-y-2')
    expect(screen.getByTestId('session-agent-container')).not.toHaveClass('md:grid')
    expect(renderedAgentIds()).toEqual(expectedOrder)

    fireEvent.click(within(header).getByRole('button', { name: 'Delete summary' }))
    expect(screen.getByRole('heading', { name: 'Delete Session' })).toBeInTheDocument()
    expect(within(header).getByRole('button', { name: 'Collapse summary' })).toBeInTheDocument()
  })

  it('keeps Agents session List/Grid preferences local without changing session state', async () => {
    const sessionA = session('cao-layout-a', '200')
    const sessionB = session('cao-layout-b', '100')
    const terminals = (prefix: string) => [
      { id: `z-${prefix}-first`, tmux_session: prefix, tmux_window: '0', provider: 'codex', agent_profile: 'developer_sol_medium', last_active: '100' },
      { id: `a-${prefix}-second`, tmux_session: prefix, tmux_window: '1', provider: 'claude_code', agent_profile: 'reviewer_sol_high', last_active: '999' },
      { id: `m-${prefix}-third`, tmux_session: prefix, tmux_window: '2', provider: 'kiro_cli', agent_profile: 'developer_terra_medium', last_active: '200' },
    ]
    vi.spyOn(api, 'getSession').mockImplementation(async name => ({
      session: name === sessionA.name ? sessionA : sessionB,
      terminals: terminals(name),
    }) as never)
    useStore.setState({ sessions: [sessionA, sessionB] })
    render(<AgentPanel />)

    const aCard = await screen.findByTestId(`agent-session-${sessionA.id}`)
    const bCard = screen.getByTestId(`agent-session-${sessionB.id}`)
    const aActions = within(aCard).getByTestId(`agent-session-actions-${sessionA.id}`)
    const bActions = within(bCard).getByTestId(`agent-session-actions-${sessionB.id}`)

    expect(within(aActions).getByRole('button', { name: 'List view' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(bActions).getByRole('button', { name: 'List view' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(within(aActions).getByRole('button', { name: 'Grid view' }))
    expect(screen.queryByTestId(`agent-session-detail-${sessionA.id}`)).not.toBeInTheDocument()

    fireEvent.click(within(aCard).getByRole('button', { name: `Expand ${sessionDisplayName(sessionA.name)}` }))
    const aContainer = await screen.findByTestId(`agent-session-agent-container-${sessionA.id}`)
    expect(aContainer).toHaveClass('md:grid', 'md:grid-cols-2')
    const cardOrder = (container: HTMLElement) => within(container).getAllByTestId(/^agent-detail-card-/).map(card => card.getAttribute('data-testid'))
    expect(cardOrder(aContainer)).toEqual([
      `agent-detail-card-z-${sessionA.name}-first`,
      `agent-detail-card-a-${sessionA.name}-second`,
      `agent-detail-card-m-${sessionA.name}-third`,
    ])

    fireEvent.click(within(bCard).getByRole('button', { name: `Expand ${sessionDisplayName(sessionB.name)}` }))
    const bContainer = await screen.findByTestId(`agent-session-agent-container-${sessionB.id}`)
    expect(bContainer).toHaveClass('space-y-2')
    expect(bContainer).not.toHaveClass('md:grid')
    expect(cardOrder(bContainer)).toEqual([
      `agent-detail-card-z-${sessionB.name}-first`,
      `agent-detail-card-a-${sessionB.name}-second`,
      `agent-detail-card-m-${sessionB.name}-third`,
    ])
    expect(within(bActions).getByRole('button', { name: 'List view' })).toHaveAttribute('aria-pressed', 'true')

    fireEvent.click(within(aCard).getByRole('button', { name: `Expand ${sessionDisplayName(sessionA.name)}` }))
    const reopenedA = await screen.findByTestId(`agent-session-agent-container-${sessionA.id}`)
    expect(reopenedA).toHaveClass('md:grid', 'md:grid-cols-2')
    expect(cardOrder(reopenedA)).toEqual([
      `agent-detail-card-z-${sessionA.name}-first`,
      `agent-detail-card-a-${sessionA.name}-second`,
      `agent-detail-card-m-${sessionA.name}-third`,
    ])
    expect(useStore.getState().sessions.map(item => ({ id: item.id, status: item.status }))).toEqual([
      { id: sessionA.id, status: 'active' },
      { id: sessionB.id, status: 'active' },
    ])
  })

  it('shows one durable aggregate for a one-agent session even when the session status differs', async () => {
    const one = { ...session('one-agent', '100'), status: 'active' }
    const agent = { id: 'only-agent', tmux_session: one.name, tmux_window: '0', provider: 'codex', agent_profile: 'developer', last_active: '999' }
    useStore.setState({ sessions: [one] })
    vi.spyOn(api, 'getSession').mockResolvedValue({ session: one, terminals: [agent] })
    vi.spyOn(api, 'getTerminalStatus').mockResolvedValue({ ...agent, status: 'idle', lifecycle: 'running', workflow_state: 'completed' } as never)

    render(<DashboardHome onNavigate={() => {}} />)

    const badges = await screen.findByTestId('session-status-badges-one-agent')
    expect(within(badges).getByText('Completed')).toBeInTheDocument()
    expect(within(badges).queryByText('×1')).not.toBeInTheDocument()
    expect(screen.getByTestId('session-status-first-one-agent')).toHaveAttribute('data-testid')
    expect(screen.getByTestId('session-status-first-one-agent').querySelector('[data-terminal-id]')).toHaveAttribute('data-terminal-id', 'only-agent')
    expect(screen.getByTestId('session-status-last-one-agent').querySelector('[data-terminal-id]')).toHaveAttribute('data-terminal-id', 'only-agent')
    expect(badges.querySelectorAll('[data-terminal-id]')).toHaveLength(0)
  })

  it('coalesces equal durable agent states into one truthful count', async () => {
    const equal = session('equal-statuses', '100')
    const agents = [
      { id: 'z-created-first', tmux_session: equal.name, tmux_window: 'first', provider: 'codex', agent_profile: 'developer', last_active: '100' },
      { id: 'a-active-latest', tmux_session: equal.name, tmux_window: 'middle', provider: 'codex', agent_profile: 'developer', last_active: '999' },
      { id: 'm-created-last', tmux_session: equal.name, tmux_window: 'last', provider: 'codex', agent_profile: 'developer', last_active: '200' },
    ]
    useStore.setState({ sessions: [equal] })
    vi.spyOn(api, 'getSession').mockResolvedValue({ session: equal, terminals: agents })
    vi.spyOn(api, 'getTerminalStatus').mockImplementation(async id => ({ ...agents.find(agent => agent.id === id), id, status: 'idle', lifecycle: 'running', workflow_state: 'waiting' }) as never)

    render(<DashboardHome onNavigate={() => {}} />)

    const badges = await screen.findByTestId('session-status-badges-equal-statuses')
    expect(within(badges).getByText('Waiting / Recoverable')).toBeInTheDocument()
    expect(within(screen.getByTestId('session-status-agent-equal-statuses-idle')).getByText('×3')).toBeInTheDocument()
    expect(within(screen.getByTestId('session-status-workflow-equal-statuses-waiting')).getByText('×3')).toBeInTheDocument()
    expect(within(badges).getAllByText('Waiting / Recoverable')).toHaveLength(1)
    expect(screen.getByTestId('session-status-first-equal-statuses').querySelector('[data-terminal-id]')).toHaveAttribute('data-terminal-id', 'z-created-first')
    expect(screen.getByTestId('session-status-last-equal-statuses').querySelector('[data-terminal-id]')).toHaveAttribute('data-terminal-id', 'm-created-last')
  })

  it('summarizes each mixed durable agent state without using the session status', async () => {
    const mixed = { ...session('mixed-statuses', '100'), status: 'detached' }
    const agents = [
      { id: 'z-first', tmux_session: mixed.name, tmux_window: 'first', provider: 'codex', agent_profile: 'developer', last_active: '100' },
      { id: 'a-changes-latest', tmux_session: mixed.name, tmux_window: 'second', provider: 'codex', agent_profile: 'developer', last_active: '999' },
      { id: 'n-third', tmux_session: mixed.name, tmux_window: 'third', provider: 'codex', agent_profile: 'developer', last_active: '300' },
      { id: 'm-last', tmux_session: mixed.name, tmux_window: 'last', provider: 'codex', agent_profile: 'developer', last_active: '400' },
    ]
    const states = {
      'z-first': { status: 'processing', workflow_state: 'active' },
      'a-changes-latest': { status: 'idle', workflow_state: 'owner_gate' },
      'n-third': { status: 'idle', workflow_state: 'waiting' },
      'm-last': { status: 'idle', workflow_state: 'failed' },
    }
    useStore.setState({ sessions: [mixed] })
    vi.spyOn(api, 'getSession').mockResolvedValue({ session: mixed, terminals: agents })
    vi.spyOn(api, 'getTerminalStatus').mockImplementation(async id => ({ ...agents.find(agent => agent.id === id), id, lifecycle: 'running', ...states[id as keyof typeof states] }) as never)

    render(<DashboardHome onNavigate={() => {}} />)

    const badges = await screen.findByTestId('session-status-badges-mixed-statuses')
    expect(within(badges).getByText('In progress / Active')).toBeInTheDocument()
    expect(within(badges).getByText('Needs owner decision')).toBeInTheDocument()
    expect(within(badges).getByText('Waiting / Recoverable')).toBeInTheDocument()
    expect(within(badges).getByText('Failed')).toBeInTheDocument()
    expect(within(badges).queryByText('×1')).not.toBeInTheDocument()
    expect(within(screen.getByTestId('session-status-agent-mixed-statuses-idle')).getByText('×3')).toBeInTheDocument()
    expect(screen.getByTestId('session-status-first-mixed-statuses').querySelector('[data-terminal-id]')).toHaveAttribute('data-terminal-id', 'z-first')
    expect(screen.getByTestId('session-status-last-mixed-statuses').querySelector('[data-terminal-id]')).toHaveAttribute('data-terminal-id', 'm-last')

    act(() => useStore.getState().setTerminalStatus('a-changes-latest', 'WORKFLOW_COMPLETED::Ready'))
    expect(within(badges).getByText('Failed')).toBeInTheDocument()
  })

  it('keeps long owner reasons out of every shared badge while preserving the Owner Decision panel', async () => {
    const ownerSession = session('cao-owner-badges', '100')
    const ownerTerminal = { id: 'owner-terminal', tmux_session: ownerSession.name, tmux_window: '0', provider: 'codex', agent_profile: 'developer', last_active: null }
    const reason = `Owner approval is required. ${'This intentionally very long durable explanation must stay in the dedicated Owner Decision panel. '.repeat(16)}`
    useStore.setState({ sessions: [ownerSession] })
    vi.spyOn(api, 'getSession').mockResolvedValue({ session: ownerSession, terminals: [ownerTerminal] })
    vi.spyOn(api, 'getTerminalStatus').mockResolvedValue({
      ...ownerTerminal,
      status: 'idle',
      execution_state: 'ready',
      lifecycle: 'running',
      workflow_state: 'owner_gate',
      workflow_status: 'owner_gate',
      workflow_reason: reason,
    } as never)

    const assertCategoricalBadges = (root: HTMLElement) => {
      const badges = Array.from(root.querySelectorAll<HTMLElement>('[data-status-badge]'))
      expect(badges.length).toBeGreaterThan(0)
      for (const badge of badges) {
        expect(badge).not.toHaveTextContent(reason)
        expect(badge.getAttribute('title') || '').not.toContain(reason)
        expect(badge.getAttribute('aria-label') || '').not.toContain(reason)
        for (const node of Array.from(badge.querySelectorAll<HTMLElement>('[title], [aria-label]'))) {
          expect(node.getAttribute('title') || '').not.toContain(reason)
          expect(node.getAttribute('aria-label') || '').not.toContain(reason)
        }
      }
    }

    const home = render(<DashboardHome onNavigate={() => {}} />)
    const first = await screen.findByTestId(`session-status-first-${ownerSession.id}`)
    const last = screen.getByTestId(`session-status-last-${ownerSession.id}`)
    const total = screen.getByTestId(`session-status-total-${ownerSession.id}`)
    for (const surface of [first, last, total]) {
      expect(within(surface).getByText('Needs owner decision')).toBeInTheDocument()
      assertCategoricalBadges(surface)
    }
    expect(screen.queryByText(reason)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Expand owner-badges' }))
    const detail = await screen.findByTestId(`agent-detail-card-${ownerTerminal.id}`)
    assertCategoricalBadges(detail)
    expect(within(screen.getByTestId(`owner-decision-${ownerTerminal.id}`)).getByText(reason, { exact: false })).toBeInTheDocument()
    home.unmount()

    render(<AgentPanel navigationSearch="?tab=agents&agentView=statuses" />)
    const agentCard = await screen.findByTestId(`agent-detail-card-${ownerTerminal.id}`)
    expect(within(agentCard).getByText('Needs owner decision')).toBeInTheDocument()
    assertCategoricalBadges(agentCard)
    expect(screen.queryByText(reason)).not.toBeInTheDocument()
  })

  it('keeps large session status rendering bounded to aggregate counts', async () => {
    const many = session('badge-many', '100')
    const few = session('badge-few', '99')
    const manyTerminals = Array.from({ length: 6 }, (_, index) => ({ id: `many-${index}`, tmux_session: many.name, tmux_window: String(index), provider: 'codex', agent_profile: 'developer', last_active: null }))
    const fewTerminals = Array.from({ length: 2 }, (_, index) => ({ id: `few-${index}`, tmux_session: few.name, tmux_window: String(index), provider: 'codex', agent_profile: 'developer', last_active: null }))
    useStore.setState({ sessions: [many, few] })
    vi.spyOn(api, 'getSession').mockImplementation(async name => ({ session: name === many.name ? many : few, terminals: name === many.name ? manyTerminals : fewTerminals }) as never)

    render(<DashboardHome onNavigate={() => {}} />)

    const manyBadges = await screen.findByTestId('session-status-badges-badge-many')
    const fewBadges = await screen.findByTestId('session-status-badges-badge-few')
    expect(within(manyBadges).getByText('Idle')).toBeInTheDocument()
    expect(within(screen.getByTestId('session-status-agent-badge-many-idle')).getByText('×6')).toBeInTheDocument()
    expect(within(screen.getByTestId('session-status-workflow-badge-many-untracked')).getByText('×6')).toBeInTheDocument()
    expect(within(screen.getByTestId('session-status-agent-badge-few-idle')).getByText('×2')).toBeInTheDocument()
    expect(within(screen.getByTestId('session-status-workflow-badge-few-untracked')).getByText('×2')).toBeInTheDocument()
    expect(manyBadges.querySelectorAll('[data-terminal-id]')).toHaveLength(0)
  })

  it('routes Home messaging to the multiline Inbox composer instead of query-based terminal input', async () => {
    const terminal = { id: 'inbox-terminal', tmux_session: 'cao-inbox', tmux_window: '0', provider: 'codex', agent_profile: 'developer', last_active: null }
    useStore.setState({ sessions: [session('cao-inbox', '100')] })
    vi.spyOn(api, 'getSession').mockResolvedValue({ session: session('cao-inbox', '100'), terminals: [terminal] })
    vi.spyOn(api, 'getTerminalStatus').mockResolvedValue({ ...terminal, status: 'idle', lifecycle: 'running', workflow_state: 'active' } as never)
    vi.spyOn(api, 'getInboxMessages').mockResolvedValue([])
    vi.spyOn(api, 'listDelegationResults').mockResolvedValue([])

    render(<DashboardHome onNavigate={() => {}} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Expand inbox' }))
    await screen.findByRole('button', { name: 'Message via Inbox' })
    expect(screen.queryByText('Message agent...')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Message via Inbox' }))
    expect(await screen.findByRole('textbox', { name: 'Inbox draft' })).toBeInTheDocument()
  })

  it('keeps the same backend-provided order on Agents', async () => {
    useStore.setState({ sessions: [session('cao-newest', '200'), session('cao-oldest', '100')] })
    const { container } = render(<AgentPanel />)

    await screen.findByText('newest')
    expect(Array.from(container.querySelectorAll('span.font-mono')).map(node => node.textContent).slice(0, 2)).toEqual(['newest', 'oldest'])
  })
})

describe('session deletion confirmation', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    useStore.setState({
      sessions: [{ ...session('lifetime-delete-me', '100'), name: 'cao-delete-me', status: 'history' }],
      activeSession: null,
      activeSessionDetail: null,
      terminalStatuses: {},
      snackbar: null,
      connected: true,
    })
    vi.spyOn(api, 'listProviders').mockResolvedValue([{ name: 'kiro_cli', binary: 'kiro', installed: true }])
    vi.spyOn(api, 'listProfiles').mockResolvedValue([{ name: 'developer', description: '', source: 'built-in' }])
    installUiReadModelSpies()
  })

  it('opens without deleting, cancels safely, then deletes once after reopening and confirming', async () => {
    const remove = vi.spyOn(useStore.getState(), 'deleteSession').mockResolvedValue()
    render(<AgentPanel />)

    fireEvent.click(await screen.findByTitle('Delete session'))
    expect(screen.getByRole('heading', { name: 'Delete Session' })).toBeInTheDocument()
    expect(remove).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('heading', { name: 'Delete Session' })).not.toBeInTheDocument()
    expect(remove).not.toHaveBeenCalled()

    fireEvent.click(screen.getByTitle('Delete session'))
    fireEvent.click(screen.getByRole('button', { name: 'Delete Session' }))
    await waitFor(() => expect(remove).toHaveBeenCalledTimes(1))
    expect(remove).toHaveBeenCalledWith('lifetime-delete-me')
  })

  it('prevents duplicate delete confirmations while the request is pending', async () => {
    let resolveDelete!: () => void
    const remove = vi.spyOn(useStore.getState(), 'deleteSession').mockImplementation(() => new Promise<void>(resolve => { resolveDelete = resolve }))
    render(<AgentPanel />)

    fireEvent.click(await screen.findByTitle('Delete session'))
    const confirm = screen.getByRole('button', { name: 'Delete Session' })
    fireEvent.click(confirm)
    fireEvent.click(confirm)

    expect(remove).toHaveBeenCalledTimes(1)
    expect(confirm).toBeDisabled()

    resolveDelete()
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Delete Session' })).not.toBeInTheDocument())
  })

  it('opens the terminal deletion confirmation only for an exited terminal', async () => {
    const terminal = { id: 'codex-terminal', tmux_session: 'cao-delete-me', tmux_window: '0', provider: 'codex', agent_profile: 'developer', last_active: null }
    const closeTerminal = vi.spyOn(api, 'deleteTerminal').mockResolvedValue({} as never)
    useStore.setState({
      activeSession: 'lifetime-delete-me',
      activeSessionDetail: { session: { ...session('lifetime-delete-me', '100'), name: 'cao-delete-me' }, terminals: [terminal] },
      terminalStatuses: {
        [terminal.id]: { lifecycle: 'exited', status: 'idle' },
      } as never,
    })
    render(<AgentPanel />)

    fireEvent.click(await screen.findByRole('button', { name: 'Expand delete-me' }))
    fireEvent.click(await screen.findByTitle('Delete exited terminal history'))
    expect(screen.getByRole('heading', { name: 'Delete Exited Terminal' })).toBeInTheDocument()
    expect(closeTerminal).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('heading', { name: 'Delete Exited Terminal' })).not.toBeInTheDocument()
    expect(closeTerminal).not.toHaveBeenCalled()
  })
})

describe('graceful exit authority feedback', () => {
  const terminal = { id: 'exit-terminal', tmux_session: 'cao-exit', tmux_window: '0', provider: 'codex', agent_profile: 'developer', last_active: null }
  const pending = {
    success: false,
    lifecycle: 'exit_pending' as const,
    outcome: 'exit_pending' as const,
    message: 'Exit command was delivered, but provider exit is not yet confirmed',
    command_delivered: true,
  }

  beforeEach(() => {
    vi.restoreAllMocks()
    useStore.setState({
      sessions: [session('cao-exit', '100')],
      activeSession: 'cao-exit',
      activeSessionDetail: { session: session('cao-exit', '100'), terminals: [terminal] },
      terminalStatuses: {},
      snackbar: null,
      connected: true,
    })
    vi.spyOn(api, 'listProviders').mockResolvedValue([{ name: 'codex', binary: 'codex', installed: true }])
    vi.spyOn(api, 'listProfiles').mockResolvedValue([{ name: 'developer', description: '', source: 'built-in' }])
    vi.spyOn(api, 'listProjects').mockResolvedValue([])
    vi.spyOn(api, 'getSession').mockResolvedValue({ session: session('cao-exit', '100'), terminals: [terminal] })
    vi.spyOn(api, 'getTerminalStatus').mockResolvedValue({ ...terminal, status: 'idle', lifecycle: 'running' } as never)
    installUiReadModelSpies()
  })

  it('keeps AgentPanel confirmation open when exit is not confirmed', async () => {
    vi.spyOn(api, 'exitTerminal').mockResolvedValue(pending)
    render(<AgentPanel />)

    fireEvent.click(await screen.findByRole('button', { name: 'Expand exit' }))
    fireEvent.click(await screen.findByTitle('Graceful exit'))
    fireEvent.click(screen.getByRole('button', { name: 'Send Exit' }))

    await waitFor(() => expect(useStore.getState().snackbar?.message).toBe(pending.message))
    expect(screen.getByRole('heading', { name: 'Graceful Exit' })).toBeInTheDocument()
  })

  it('keeps DashboardHome confirmation open when exit is not confirmed', async () => {
    vi.spyOn(api, 'exitTerminal').mockResolvedValue(pending)
    render(<DashboardHome onNavigate={() => {}} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Expand exit' }))
    fireEvent.click(await screen.findByTitle('Graceful Exit'))
    fireEvent.click(screen.getByRole('button', { name: 'Send Exit' }))

    await waitFor(() => expect(useStore.getState().snackbar?.message).toBe(pending.message))
    expect(screen.getByRole('heading', { name: 'Graceful Exit' })).toBeInTheDocument()
  })
})
