import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, type InteractionItem, type InteractionPage, type SessionSummary } from '../api'
import { DashboardHome } from '../components/DashboardHome'
import { InteractionHistoryDrawer } from '../components/InteractionHistoryDrawer'
import { I18nProvider } from '../i18n'
import { useStore } from '../store'

vi.mock('../components/TerminalView', () => ({ TerminalView: () => null }))

function interaction(overrides: Partial<InteractionItem> = {}): InteractionItem {
  return {
    id: 'workflow-turn:0001',
    interaction_type: 'workflow_turn',
    task_type: 'external_input',
    source: { kind: 'operator', terminal_id: 'ui', target_terminal_id: 'agent-1' },
    input_preview: 'Inspect the durable lifecycle',
    created_at: '2026-09-07T08:00:00Z',
    updated_at: '2026-09-07T08:01:00Z',
    current: true,
    queue: { state: 'queued', wait_reason: 'provider_capacity', admission_pending: true },
    workflow: {
      id: 1,
      turn_id: 10,
      status: 'open',
      reason: null,
      turn_state: 'queued',
      turn_kind: 'external_input',
      provider_outcome_code: null,
      provider_outcome_detail: null,
      effect_kind: null,
      effect_state: null,
      turn_count: 1,
      superseded_turn_count: 0,
    },
    result: { id: null, status: null, summary: null, available: false },
    delivery: { status: null, pending: false, acknowledged: false },
    final_disposition: null,
    diagnostics: { interaction_id: 'workflow-turn:0001', durable_id: '10', assignment_id: null },
    ...overrides,
  }
}

function page(items: InteractionItem[], nextCursor: string | null = null): InteractionPage {
  return {
    items,
    total: items.length,
    limit: 20,
    next_cursor: nextCursor,
    snapshot_at: '2026-09-07T09:00:00Z',
  }
}

function session(id: string, queue: number): SessionSummary {
  return {
    id,
    name: id,
    status: 'active',
    created_at: '2026-09-07T08:00:00Z',
    agent_count: 1,
    active_agent_count: 1,
    workflow_counts: { active: 1 },
    activity_counts: { ready: 1 },
    project_name: null,
    last_active: '2026-09-07T08:00:00Z',
    first_agent: { id: `${id}-agent`, activity: 'ready', execution_state: 'ready', lifecycle: 'running', workflow_state: 'active', workflow_reason: null },
    last_agent: { id: `${id}-agent`, activity: 'ready', execution_state: 'ready', lifecycle: 'running', workflow_state: 'active', workflow_reason: null },
    current_queue_count: queue,
  }
}

describe('InteractionHistoryDrawer', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
  })

  afterEach(() => vi.useRealTimers())

  it('loads Current Queue immediately and History only after the operator opens it', async () => {
    const list = vi.spyOn(api, 'listInteractions').mockImplementation(async params => (
      params.mode === 'history'
        ? page([
          interaction({ id: 'inbox:0002', interaction_type: 'inbox', current: false, final_disposition: 'delivered', queue: { state: 'delivered', wait_reason: null, admission_pending: false } }),
          interaction({ id: 'workflow-turn:0003', current: false, final_disposition: 'processed', queue: { state: 'sent', wait_reason: null, admission_pending: false } }),
        ])
        : page([interaction()])
    ))

    render(<I18nProvider><InteractionHistoryDrawer sessionId="session-1" sessionName="cao-session-1" onClose={() => {}} /></I18nProvider>)

    expect(await screen.findByText('provider capacity')).toBeInTheDocument()
    expect(list).toHaveBeenCalledTimes(1)
    expect(list.mock.calls[0][0]).toMatchObject({ mode: 'current', sessionId: 'session-1' })

    fireEvent.click(screen.getByRole('tab', { name: 'History' }))
    expect(await screen.findByText('Inbox message')).toBeInTheDocument()
    expect(screen.getByText('Processed')).toBeInTheDocument()
    expect(list).toHaveBeenCalledTimes(2)
    expect(list.mock.calls[1][0]).toMatchObject({ mode: 'history', sessionId: 'session-1' })
  })

  it('paginates Current Queue without duplicating durable interactions', async () => {
    const first = interaction({ id: 'workflow-turn:0001' })
    const second = interaction({ id: 'workflow-turn:0002' })
    vi.spyOn(api, 'listInteractions').mockImplementation(async params => (
      params.cursor
        ? { ...page([first, second]), total: 2 }
        : { ...page([first], 'current-cursor'), total: 2 }
    ))

    render(<I18nProvider><InteractionHistoryDrawer sessionId="session-1" sessionName="cao-session-1" onClose={() => {}} /></I18nProvider>)

    fireEvent.click(await screen.findByRole('button', { name: 'Load more current work' }))
    await waitFor(() => expect(screen.getAllByTestId(/^interaction-workflow-turn:/)).toHaveLength(2))
  })

  it('preserves expansion and scroll across live Current Queue refresh', async () => {
    vi.useFakeTimers()
    let calls = 0
    vi.spyOn(api, 'listInteractions').mockImplementation(async () => {
      calls += 1
      return page([interaction({ queue: { state: calls > 1 ? 'claimed' : 'queued', wait_reason: 'provider_capacity', admission_pending: true } })])
    })

    render(<I18nProvider><InteractionHistoryDrawer sessionId="session-1" sessionName="cao-session-1" onClose={() => {}} /></I18nProvider>)
    await act(async () => {})
    fireEvent.click(screen.getByRole('button', { name: 'Show details' }))
    expect(screen.getByText('Inspect the durable lifecycle')).toBeInTheDocument()
    const panel = screen.getByRole('tabpanel')
    panel.scrollTop = 96

    await act(async () => { await vi.advanceTimersByTimeAsync(5_000) })

    expect(calls).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('Inspect the durable lifecycle')).toBeInTheDocument()
    expect(panel.scrollTop).toBe(96)
  })

  it('loads canonical result content only when its grouped interaction expands', async () => {
    const item = interaction({
      id: 'assignment:0003',
      interaction_type: 'delegation',
      current: false,
      queue: { state: 'result_acknowledged', wait_reason: null, admission_pending: false },
      result: { id: 'result-3', status: 'complete', summary: 'Reviewed revision', available: true },
      delivery: { status: 'result_acknowledged', pending: false, acknowledged: true },
      final_disposition: 'acknowledged',
    })
    vi.spyOn(api, 'listInteractions').mockImplementation(async params => page(params.mode === 'history' ? [item] : []))
    const readResult = vi.spyOn(api, 'getDelegationResult').mockResolvedValue({
      id: 'result-3', delegation_kind: 'assign', status: 'complete', delivery_status: 'result_acknowledged', authorship: 'child_submission', document: { summary: 'Reviewed revision', body_markdown: 'Canonical body' }, created_at: item.created_at, finalized_at: item.updated_at,
    })

    render(<I18nProvider><InteractionHistoryDrawer sessionId="session-1" sessionName="cao-session-1" initialMode="history" onClose={() => {}} /></I18nProvider>)
    expect(await screen.findByText('Reviewed revision')).toBeInTheDocument()
    expect(readResult).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Show details' }))
    expect(await screen.findByText('Canonical body')).toBeInTheDocument()
    expect(readResult).toHaveBeenCalledOnce()
  })

  it('renders operator-retired effects as unknown outcomes without a fabricated result', async () => {
    const retired = interaction({
      id: 'effect:00000000000000000042',
      interaction_type: 'effect',
      task_type: 'handoff',
      current: false,
      queue: {
        state: 'operator_retired_indeterminate',
        wait_reason: null,
        admission_pending: false,
      },
      workflow: {
        id: 7,
        turn_id: 11,
        status: 'cancelled',
        reason: 'operator retirement fixture',
        turn_state: 'cancelled',
        turn_kind: 'external_input',
        provider_outcome_code: null,
        provider_outcome_detail: null,
        effect_kind: 'handoff',
        effect_state: 'operator_retired_indeterminate',
        turn_count: 0,
        superseded_turn_count: 0,
      },
      result: { id: null, status: null, summary: null, available: false },
      final_disposition: 'operator_retired_unknown_outcome',
    })
    vi.spyOn(api, 'listInteractions').mockImplementation(async params => (
      page(params.mode === 'history' ? [retired] : [])
    ))

    render(<I18nProvider><InteractionHistoryDrawer sessionId="session-1" sessionName="cao-session-1" initialMode="history" onClose={() => {}} /></I18nProvider>)

    expect(await screen.findByText('Outcome unknown · retired by operator')).toBeInTheDocument()
    expect(screen.getByText('No canonical result exists for this interaction.')).toBeInTheDocument()
  })

  it('renders a bounded handoff timeout as a known History disposition', async () => {
    const timeout = interaction({
      id: 'workflow-turn:00000000000000000011',
      interaction_type: 'effect',
      task_type: 'await_handoff',
      current: false,
      queue: { state: 'sent', wait_reason: null, admission_pending: false },
      workflow: {
        id: 7,
        turn_id: 11,
        status: 'open',
        reason: null,
        turn_state: 'sent',
        turn_kind: 'assigned_result',
        provider_outcome_code: null,
        provider_outcome_detail: null,
        effect_kind: 'await_handoff',
        effect_state: 'wait_timeout',
        turn_count: 0,
        superseded_turn_count: 0,
      },
      result: { id: null, status: null, summary: null, available: false },
      final_disposition: 'wait_slice_expired',
    })
    vi.spyOn(api, 'listInteractions').mockImplementation(async params => (
      page(params.mode === 'history' ? [timeout] : [])
    ))

    render(<I18nProvider><InteractionHistoryDrawer sessionId="session-1" sessionName="cao-session-1" initialMode="history" onClose={() => {}} /></I18nProvider>)

    expect(await screen.findByText('Wait slice expired')).toBeInTheDocument()
    expect(screen.getByText('Wait for handoff')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Show details' }))
    expect(screen.getByText('await_handoff · wait_timeout')).toBeInTheDocument()
    expect(screen.getAllByText('No canonical result exists for this interaction.')).toHaveLength(2)
  })

  it('is a responsive modal drawer with trapped initial focus and Escape close', async () => {
    vi.spyOn(api, 'listInteractions').mockResolvedValue(page([]))
    const onClose = vi.fn()
    const { container } = render(<I18nProvider><InteractionHistoryDrawer sessionId="session-1" sessionName="cao-session-1" onClose={onClose} /></I18nProvider>)
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveClass('inset-0', 'sm:left-auto')
    await waitFor(() => {
      const closeButtons = screen.getAllByRole('button', { name: 'Close' })
      expect(closeButtons[closeButtons.length - 1]).toHaveFocus()
    })
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledOnce()
    expect(container.querySelector('[role="dialog"]')).toBeInTheDocument()
  })
})

describe('session queue indicator', () => {
  it('shows the canonical positive count and omits zero-count noise', async () => {
    vi.restoreAllMocks()
    useStore.setState({ sessions: [], activeSession: null, activeSessionDetail: null, terminalStatuses: {}, connected: true, snackbar: null })
    vi.spyOn(api, 'getUiOverview').mockResolvedValue({ sessions: 2, agents: 2, active: 2, waiting: 2, owner_gate: 0, cancelled: 0, completed: 0 })
    vi.spyOn(api, 'listSessionSummaries').mockResolvedValue({ items: [session('queued-session', 3), session('empty-session', 0)], total: 2, limit: 10, offset: 0, next_offset: null })

    render(<I18nProvider><DashboardHome onNavigate={() => {}} /></I18nProvider>)

    expect(await screen.findByTestId('session-queue-count-queued-session')).toHaveTextContent('Queued: 3')
    expect(screen.queryByTestId('session-queue-count-empty-session')).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Open work and interaction history' })).toHaveLength(2)
  })
})
