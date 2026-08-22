import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { DashboardHome } from '../components/DashboardHome'
import { AgentPanel } from '../components/AgentPanel'
import { AgentSummary, AgentSummaryPage, PageResult, SessionSummary, api } from '../api'
import { useStore } from '../store'

vi.mock('../components/TerminalView', () => ({ TerminalView: () => null }))

const overview = { sessions: 2, agents: 2, active: 2, waiting: 0, owner_gate: 0, cancelled: 0, completed: 0 }

function session(id: string, agents = 1): SessionSummary {
  return {
    id,
    name: id,
    status: 'active',
    created_at: '2026-08-22T00:00:00Z',
    agent_count: agents,
    active_agent_count: agents,
    workflow_counts: { active: agents },
    activity_counts: { idle: agents },
    project_name: null,
    last_active: '2026-08-22T00:00:00Z',
    first_agent: { id: `${id}-agent-0`, activity: 'idle', execution_state: 'ready', lifecycle: 'running', workflow_state: 'active' },
    last_agent: { id: `${id}-agent-${agents - 1}`, activity: 'idle', execution_state: 'ready', lifecycle: 'running', workflow_state: 'active' },
  }
}

function agent(id: string, sessionId = 'session-0'): AgentSummary {
  return {
    id,
    name: id,
    provider: 'codex',
    session_id: sessionId,
    session_name: sessionId,
    agent_profile: 'developer',
    activity: 'idle',
    execution_state: 'ready',
    lifecycle: 'running',
    workflow_state: 'active',
    workflow_status: null,
    assignment_status: null,
    result_status: null,
    delivery_status: null,
    context_role: null,
    launch_worktree: null,
    managed_worktree_kind: null,
    managed_worktree_commit: null,
    managed_worktree_branch: null,
    projectId: null,
    project_name: null,
    project_path: null,
    creation_order: 1,
    last_active: '2026-08-22T00:00:00Z',
  }
}

function page<T>(items: T[], limit = 10, offset = 0): PageResult<T> {
  const selected = items.slice(offset, offset + limit)
  return {
    items: selected,
    total: items.length,
    limit,
    offset,
    next_offset: offset + selected.length < items.length ? offset + selected.length : null,
  }
}

function agentPage(items: AgentSummary[], limit = 40, offset = 0): AgentSummaryPage {
  return {
    ...page(items, limit, offset),
    facets: { activities: ['idle'], workflow_states: ['active'], profiles: ['developer'] },
  }
}

beforeEach(() => {
  vi.restoreAllMocks()
  useStore.setState({ sessions: [], activeSession: null, activeSessionDetail: null, terminalStatuses: {}, connected: true, snackbar: null })
  vi.spyOn(api, 'getUiOverview').mockResolvedValue(overview)
  vi.spyOn(api, 'listProfiles').mockResolvedValue([])
  vi.spyOn(api, 'listProviders').mockResolvedValue([])
  vi.spyOn(api, 'listProjects').mockResolvedValue([])
  vi.spyOn(api, 'getSession').mockRejectedValue(new Error('legacy session fan-out is forbidden'))
  vi.spyOn(api, 'getTerminalStatus').mockRejectedValue(new Error('per-terminal polling is forbidden'))
})

describe('Home render stability', () => {
  it.each([
    { label: 'one session', names: ['session-0'] },
    { label: 'multiple sessions', names: ['session-0', 'session-1', 'session-2'] },
  ])('keeps session details collapsed until the operator expands one for $label', async ({ names }) => {
    const sessions = names.map(name => session(name))
    vi.spyOn(api, 'listSessionSummaries').mockResolvedValue(page(sessions))
    const listAgents = vi.spyOn(api, 'listAgentSummaries').mockImplementation(async params => {
      const resolved = params || {}
      const sessionId = resolved.sessionId || 'session-0'
      return agentPage([agent(`terminal-${sessionId}`, sessionId)])
    })

    render(<DashboardHome onNavigate={() => {}} />)

    for (const name of names) {
      expect(await screen.findByRole('button', { name: `Expand ${name}` })).toHaveAttribute('aria-expanded', 'false')
    }
    expect(listAgents).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: `Expand ${names[0]}` }))
    expect(await screen.findByTestId(`agent-detail-card-terminal-${names[0]}`)).toBeInTheDocument()
    expect(listAgents).toHaveBeenCalledTimes(1)
    expect(listAgents.mock.calls[0][0]).toMatchObject({ sessionId: names[0], limit: 40, offset: 0 })
    expect(api.getSession).not.toHaveBeenCalled()
    expect(api.getTerminalStatus).not.toHaveBeenCalled()
  })

  it('renders a successful empty durable snapshot without inventing a live session', async () => {
    vi.spyOn(api, 'listSessionSummaries').mockResolvedValue(page([]))
    const listAgents = vi.spyOn(api, 'listAgentSummaries').mockResolvedValue(agentPage([]))

    render(<DashboardHome onNavigate={() => {}} />)

    expect(await screen.findByText('No matching sessions.')).toBeInTheDocument()
    expect(listAgents).not.toHaveBeenCalled()
  })

  it('bounds a 100 x 20 history snapshot and fetches agents only for an expanded session', async () => {
    const sessions = Array.from({ length: 100 }, (_, index) => session(`session-${index}`, 20))
    const listSessions = vi.spyOn(api, 'listSessionSummaries').mockImplementation(async params => {
      const resolved = params || {}
      return page(sessions, resolved.limit, resolved.offset)
    })
    const listAgents = vi.spyOn(api, 'listAgentSummaries').mockImplementation(async params => {
      const resolved = params || {}
      const sessionId = resolved.sessionId || 'session-0'
      const agents = Array.from({ length: 20 }, (_, index) => agent(`terminal-${sessionId}-${index}`, sessionId))
      return agentPage(agents, resolved.limit, resolved.offset)
    })

    render(<DashboardHome onNavigate={() => {}} />)

    expect(await screen.findByTestId('home-session-session-9')).toBeInTheDocument()
    expect(screen.queryByTestId('home-session-session-10')).not.toBeInTheDocument()
    expect(listSessions).toHaveBeenCalledTimes(1)
    expect(listAgents).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Expand session-0' }))
    expect(await screen.findByTestId('agent-detail-card-terminal-session-0-19')).toBeInTheDocument()
    expect(screen.getAllByTestId(/agent-detail-card-/)).toHaveLength(20)
    expect(listAgents).toHaveBeenCalledTimes(1)
    expect(api.getSession).not.toHaveBeenCalled()
    expect(api.getTerminalStatus).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Expand session-1' }))
    expect(await screen.findByTestId('agent-detail-card-terminal-session-1-19')).toBeInTheDocument()
    expect(screen.queryByTestId('agent-detail-card-terminal-session-0-0')).not.toBeInTheDocument()
    expect(listAgents).toHaveBeenCalledTimes(2)
    expect(screen.getAllByTestId('session-agent-container')).toHaveLength(1)
  })
})

describe('Agents batched read-model stability', () => {
  it('ignores a late response from a replaced session and preserves the newer snapshot', async () => {
    const sessions = [session('session-0'), session('session-1')]
    vi.spyOn(api, 'listSessionSummaries').mockResolvedValue(page(sessions))
    let resolveOlder!: (value: AgentSummaryPage) => void
    const older = new Promise<AgentSummaryPage>(resolve => { resolveOlder = resolve })
    let olderSignal: AbortSignal | undefined
    vi.spyOn(api, 'listAgentSummaries').mockImplementation((params, signal) => {
      if (params?.sessionId === 'session-0') {
        olderSignal = signal
        return older
      }
      return Promise.resolve(agentPage([agent('new-terminal', 'session-1')]))
    })

    render(<AgentPanel />)
    fireEvent.click(await screen.findByRole('button', { name: 'Expand session-0' }))
    await waitFor(() => expect(api.listAgentSummaries).toHaveBeenCalledWith(
      expect.objectContaining({ sessionId: 'session-0' }), expect.any(AbortSignal),
    ))

    fireEvent.click(screen.getByRole('button', { name: 'Expand session-1' }))
    expect(await screen.findByTestId('agent-detail-card-new-terminal')).toBeInTheDocument()
    expect(olderSignal?.aborted).toBe(true)

    await act(async () => resolveOlder(agentPage([agent('old-terminal', 'session-0')])))
    expect(screen.queryByTestId('agent-detail-card-old-terminal')).not.toBeInTheDocument()
    expect(screen.getByTestId('agent-detail-card-new-terminal')).toBeInTheDocument()
    expect(api.getSession).not.toHaveBeenCalled()
    expect(api.getTerminalStatus).not.toHaveBeenCalled()
  })
})
