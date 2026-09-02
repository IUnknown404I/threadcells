import { describe, it, expect, vi, beforeEach, afterAll } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { StatusBadge, lifecycleBadgeStatus } from '../components/StatusBadge'
import { SessionStatusSummary } from '../components/SessionStatusSummary'
import { ErrorBoundary } from '../components/ErrorBoundary'
import { ConfirmModal } from '../components/ConfirmModal'
import { resultLifecycleLabel, OwnerMessageBody } from '../components/InboxPanel'
import { InboxPanel } from '../components/InboxPanel'
import { OutputViewer } from '../components/OutputViewer'
import { api } from '../api'
import { APP_LOCALE_STORAGE_KEY, I18nProvider } from '../i18n'

describe('StatusBadge', () => {
  it('renders idle status', () => {
    render(<StatusBadge status="idle" />)
    expect(screen.getByText('Idle')).toBeInTheDocument()
  })

  it('renders processing status', () => {
    render(<StatusBadge status="processing" />)
    expect(screen.getByText('Processing')).toBeInTheDocument()
  })

  it('does not project provider completion as durable workflow completion', () => {
    render(<StatusBadge status="completed" />)
    expect(screen.getByText('Provider Ready')).toBeInTheDocument()
  })

  it('renders error status', () => {
    render(<StatusBadge status="error" />)
    expect(screen.getByText('Error')).toBeInTheDocument()
  })

  it('renders waiting_user_answer status', () => {
    render(<StatusBadge status="waiting_user_answer" />)
    expect(screen.getByText('Awaiting Input')).toBeInTheDocument()
  })

  it('renders null status as unknown', () => {
    render(<StatusBadge status={null} />)
    expect(screen.getByText('Unknown')).toBeInTheDocument()
  })

  it.each([
    ['owner_gate', 'Needs owner decision'],
    ['waiting', 'Waiting / Recoverable'],
    ['recoverable', 'Waiting / Recoverable'],
    ['result_ready', 'Result ready'],
    ['completed', 'Completed'],
    ['incomplete', 'Incomplete'],
    ['failed', 'Failed'],
    ['cancelled', 'Cancelled'],
  ])('uses durable workflow state %s over provider ready', (workflowState, label) => {
    render(<StatusBadge status="completed" workflowState={workflowState} />)
    expect(screen.getByText(label)).toBeInTheDocument()
  })

  it('keeps provider state as a secondary diagnostic when workflow state is primary', () => {
    render(<StatusBadge status="WORKFLOW_OWNER_GATE::Ready" />)
    expect(screen.getByText('Needs owner decision')).toBeInTheDocument()
    expect(screen.getByText('Ready')).toBeInTheDocument()
  })

  it('distinguishes durable provider-slot queueing from active processing', () => {
    render(
      <StatusBadge
        status={lifecycleBadgeStatus(
          'open',
          'completed',
          'running',
          'queued_provider_execution',
        )}
      />
    )
    expect(screen.getByText('Queued · Waiting for provider slot')).toBeInTheDocument()
    expect(screen.getByText('Open')).toBeInTheDocument()
  })

  it.each([
    ['waiting_child_retirement', 'Queued · Waiting for child retirement'],
    ['waiting_resource_recovery', 'Queued · Waiting for resource recovery'],
    ['waiting_runtime_recovery', 'Queued · Waiting for runtime recovery'],
    ['waiting_workflow_continuation', 'Queued · Waiting for workflow continuation'],
  ])('renders the exact durable wait mapping %s', (executionState, label) => {
    render(
      <StatusBadge
        status={lifecycleBadgeStatus('open', 'completed', 'running', executionState)}
      />
    )
    expect(screen.getByText(label)).toBeInTheDocument()
  })

  it('keeps owner-gate badges categorical and leaves the durable reason to detail surfaces', () => {
    const reason = `Owner approval is required. ${'This intentionally long durable explanation belongs outside every status badge. '.repeat(12)}`
    const { container } = render(
      <div>
        <StatusBadge status="WORKFLOW_OWNER_GATE::Ready" />
        <p>{reason}</p>
      </div>
    )
    const badge = container.querySelector('[data-status-badge]')
    expect(badge).toHaveTextContent('ReadyWorkflow ·Needs owner decision')
    expect(badge).not.toHaveTextContent(reason)
    expect(badge).not.toHaveAttribute('title')
    expect(badge).not.toHaveAttribute('aria-label')
    expect(screen.getByText((_, element) => element?.tagName === 'P' && element.textContent === reason)).toBeInTheDocument()
  })

  it('uses an active durable turn to override a momentary provider Ready observation', () => {
    render(
      <StatusBadge
        status={lifecycleBadgeStatus('open', 'completed', 'running', 'processing')}
      />
    )
    expect(screen.getByText('Processing')).toBeInTheDocument()
    expect(screen.queryByText('Ready')).not.toBeInTheDocument()
  })

  it.each([
    ['exited', 'Exited'],
    ['recovery_fenced', 'Replaced during recovery'],
  ])('keeps known terminal lifecycle %s when no workflow was tracked', (lifecycle, label) => {
    render(
      <StatusBadge
        status={lifecycleBadgeStatus(null, null, lifecycle)}
      />
    )
    expect(screen.getByText(label)).toBeInTheDocument()
    expect(screen.queryByText('Unknown')).not.toBeInTheDocument()
  })

  it('renders the canonical underscored recovery lifecycle without degrading to Unknown', () => {
    render(<StatusBadge status="recovery_fenced" />)
    expect(screen.getByText('Replaced during recovery')).toBeInTheDocument()
    expect(screen.queryByText('Unknown')).not.toBeInTheDocument()
  })

  it('renders the natural Russian terminal recovery wording', () => {
    localStorage.setItem(APP_LOCALE_STORAGE_KEY, 'ru')
    const view = render(<I18nProvider><StatusBadge status="recovery_fenced" /></I18nProvider>)
    expect(screen.getByText('Заменён при восстановлении')).toBeInTheDocument()
    view.unmount()
    localStorage.removeItem(APP_LOCALE_STORAGE_KEY)
  })

  it.each([
    [{ status: 'completed', lifecycle: 'running', workflow_state: 'open' }, 'Ready', 'Open'],
    [{ status: 'processing', lifecycle: 'running', workflow_state: 'open' }, 'Processing', 'Open'],
    [{ status: 'completed', lifecycle: 'running', workflow_state: 'owner_gate' }, 'Ready', 'Needs owner decision'],
    [{ status: 'processing', lifecycle: 'running', workflow_state: 'owner_gate' }, 'Processing', 'Needs owner decision'],
    [{ status: 'completed', lifecycle: 'exited', workflow_state: 'completed' }, 'Exited', 'Completed'],
  ])('projects actual provider and workflow states independently', (terminal, primary, workflow) => {
    render(<StatusBadge status={lifecycleBadgeStatus(terminal.workflow_state, terminal.status, terminal.lifecycle)} />)
    expect(screen.getByText(primary)).toBeInTheDocument()
    expect(screen.getByText(workflow)).toBeInTheDocument()
  })

  it.each([
    [{ status: 'completed', lifecycle: 'running', workflow_state: 'waiting' }, 'Waiting / Recoverable'],
    [{ status: 'completed', lifecycle: 'running', workflow_state: 'recoverable' }, 'Waiting / Recoverable'],
    [{ status: 'completed', lifecycle: 'running', workflow_state: 'result_ready' }, 'Result ready'],
    [{ status: 'completed', lifecycle: 'running', workflow_state: 'owner_gate' }, 'Needs owner decision'],
    [{ status: 'completed', lifecycle: 'running', workflow_state: 'completed' }, 'Completed'],
    [{ status: 'completed', lifecycle: 'running', workflow_state: 'incomplete' }, 'Incomplete'],
    [{ status: 'completed', lifecycle: 'running', workflow_state: 'failed' }, 'Failed'],
    [{ status: 'completed', lifecycle: 'running', workflow_state: 'cancelled' }, 'Cancelled'],
  ])('renders canonical API workflow state over Provider Ready', (terminal, label) => {
    render(
      <StatusBadge
        status={lifecycleBadgeStatus(terminal.workflow_state, terminal.status, terminal.lifecycle)}
      />
    )
    expect(screen.getByText(label)).toBeInTheDocument()
    expect(screen.getByText('Ready')).toBeInTheDocument()
  })
})

describe('SessionStatusSummary recovery lifecycle', () => {
  const recoveryAgent = {
    id: 'recovery-agent',
    activity: 'recovery_fenced',
    execution_state: 'recovery_fenced',
    lifecycle: 'recovery_fenced',
    workflow_state: null,
    workflow_reason: null,
  }

  const summary = (activityCounts: Record<string, number>, workflowCounts: Record<string, number> = { untracked: 1 }) => ({
    id: 'recovery-session',
    name: 'cao-recovery-session',
    status: 'history',
    created_at: null,
    agent_count: Object.values(activityCounts).reduce((total, count) => total + count, 0),
    active_agent_count: 0,
    activity_counts: activityCounts,
    workflow_counts: workflowCounts,
    project_name: null,
    last_active: null,
    first_agent: recoveryAgent,
    last_agent: recoveryAgent,
  })

  it('keeps a recovery-only session aggregate known while leaving its workflow untracked', () => {
    render(<SessionStatusSummary session={summary({ recovery_fenced: 1 }) as never} />)
    const total = screen.getByTestId('session-status-total-recovery-session')
    expect(within(total).getByText('Replaced during recovery')).toBeInTheDocument()
    expect(within(total).getByText('Untracked')).toBeInTheDocument()
    expect(within(total).queryByText('Unknown')).not.toBeInTheDocument()
  })

  it('uses the grammatically distinct Russian terminal and session labels', () => {
    localStorage.setItem(APP_LOCALE_STORAGE_KEY, 'ru')
    const view = render(
      <I18nProvider><SessionStatusSummary session={summary({ recovery_fenced: 1 }) as never} /></I18nProvider>,
    )
    expect(within(screen.getByTestId('session-status-first-recovery-session')).getByText('Заменён при восстановлении')).toBeInTheDocument()
    expect(within(screen.getByTestId('session-status-total-recovery-session')).getByText('Заменена при восстановлении')).toBeInTheDocument()
    view.unmount()
    localStorage.removeItem(APP_LOCALE_STORAGE_KEY)
  })

  it('orders mixed known historical states deterministically and preserves exited-only state', () => {
    const mixed = summary({ exited: 1, recovery_fenced: 1 }, { untracked: 2 })
    const view = render(<SessionStatusSummary session={mixed as never} />)
    const badges = screen.getByTestId('session-status-badges-recovery-session')
    const activityLabels = Array.from(
      badges.querySelectorAll<HTMLElement>('[data-testid^="session-status-agent-"]'),
    ).map(node => node.textContent)
    expect(activityLabels).toEqual(['Replaced during recovery', 'Exited'])
    expect(within(badges).getByText('Untracked')).toBeInTheDocument()
    view.unmount()

    render(<SessionStatusSummary session={summary({ exited: 1 }) as never} />)
    expect(within(screen.getByTestId('session-status-total-recovery-session')).getByText('Exited')).toBeInTheDocument()
  })

  it('reserves Unknown for an unrecognized aggregate state', () => {
    render(<SessionStatusSummary session={summary({ inventory_uncertain: 1 }) as never} />)
    expect(within(screen.getByTestId('session-status-total-recovery-session')).getByText('Unknown')).toBeInTheDocument()
  })
})

describe('result lifecycle labels', () => {
  it('humanizes durable result and delivery state', () => {
    expect(resultLifecycleLabel('awaiting', 'handoff_awaiting_result')).toBe('Waiting for result')
    expect(resultLifecycleLabel('complete', 'result_delivered')).toBe('Delivered')
    expect(resultLifecycleLabel('complete', 'result_acknowledged')).toBe('Incorporated / Acknowledged')
    expect(resultLifecycleLabel('complete', 'result_queued')).toBe('Result ready')
    expect(resultLifecycleLabel('complete', 'handoff_result_failed')).toBe('Delivery failed')
    expect(resultLifecycleLabel('incomplete', 'handoff_result_failed')).toBe('Incomplete')
    expect(resultLifecycleLabel('cancelled', 'handoff_result_failed')).toBe('Cancelled')
  })
})

describe('OwnerMessageBody', () => {
  it('keeps short and multiline Unicode messages complete', () => {
    render(<OwnerMessageBody message={'Привет\n\nмир 🌍'} />)
    expect(screen.getAllByText((_, element) => element?.textContent === 'Привет\n\nмир 🌍').some(element => element.tagName === 'P')).toBe(true)
    expect(screen.queryByText('Show more…')).not.toBeInTheDocument()
  })

  it('expands long messages independently and restores their previews', () => {
    const first = `Первый ${'я'.repeat(1030)}`
    const second = `Второй ${'ю'.repeat(1030)}`
    render(<><OwnerMessageBody message={first} /><OwnerMessageBody message={second} /></>)
    expect(screen.getAllByText('Show more…')).toHaveLength(2)
    fireEvent.click(screen.getAllByText('Show more…')[0])
    expect(screen.getByText(first)).toBeInTheDocument()
    expect(screen.getByText('Show less')).toBeInTheDocument()
    expect(screen.getAllByText('Show more…')).toHaveLength(1)
    fireEvent.click(screen.getByText('Show less'))
    expect(screen.getAllByText('Show more…')).toHaveLength(2)
  })
})

describe('ErrorBoundary', () => {
  // Suppress console.error for intentional error throws
  const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

  afterAll(() => consoleSpy.mockRestore())

  function ThrowingComponent(): JSX.Element {
    throw new Error('Test error')
  }

  it('catches errors and shows fallback', () => {
    render(
      <ErrorBoundary>
        <ThrowingComponent />
      </ErrorBoundary>
    )
    expect(screen.getByText(/something went wrong/i)).toBeInTheDocument()
  })

  it('renders children when no error', () => {
    render(
      <ErrorBoundary>
        <div>Hello</div>
      </ErrorBoundary>
    )
    expect(screen.getByText('Hello')).toBeInTheDocument()
  })
})

describe('ConfirmModal', () => {
  it('renders when open', () => {
    render(
      <ConfirmModal
        open={true}
        title="Delete Item"
        message="Are you sure?"
        details={[]}
        confirmLabel="Delete"
        variant="danger"
        loading={false}
        onConfirm={() => {}}
        onCancel={() => {}}
      />
    )
    expect(screen.getByText('Delete Item')).toBeInTheDocument()
    expect(screen.getByText('Are you sure?')).toBeInTheDocument()
    expect(screen.getByText('Delete')).toBeInTheDocument()
    expect(screen.getByText('Cancel')).toBeInTheDocument()
  })

  it('does not render when closed', () => {
    render(
      <ConfirmModal
        open={false}
        title="Delete Item"
        message="Are you sure?"
        details={[]}
        confirmLabel="Delete"
        variant="danger"
        loading={false}
        onConfirm={() => {}}
        onCancel={() => {}}
      />
    )
    expect(screen.queryByText('Delete Item')).not.toBeInTheDocument()
  })

  it('shows details when provided', () => {
    render(
      <ConfirmModal
        open={true}
        title="Confirm"
        message="Check details"
        details={[{ label: 'Name', value: 'test-flow' }, { label: 'Schedule', value: '0 9 * * *' }]}
        confirmLabel="OK"
        variant="danger"
        loading={false}
        onConfirm={() => {}}
        onCancel={() => {}}
      />
    )
    expect(screen.getByText('Name')).toBeInTheDocument()
    expect(screen.getByText('test-flow')).toBeInTheDocument()
    expect(screen.getByText('Schedule')).toBeInTheDocument()
  })

  it('shows loading state', () => {
    render(
      <ConfirmModal
        open={true}
        title="Deleting"
        message="Please wait"
        details={[]}
        confirmLabel="Delete"
        variant="danger"
        loading={true}
        onConfirm={() => {}}
        onCancel={() => {}}
      />
    )
    const button = screen.getByText('Working…').closest('button')
    expect(button).toBeDisabled()
  })
})

describe('OutputViewer fullscreen', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(api, 'getTerminalOutput').mockResolvedValue({
      output: `${'long output\n'.repeat(80)}https://example.invalid/${'a'.repeat(320)}\n/very/long/${'path-segment/'.repeat(60)}`,
    } as never)
  })

  it('uses the shared body-centered modal loading state', () => {
    vi.mocked(api.getTerminalOutput).mockReturnValue(new Promise(() => {}))
    render(<OutputViewer terminalId="terminal-loading" onClose={() => {}} />)

    const loader = screen.getByRole('status')
    expect(loader).toHaveTextContent('Loading terminal output')
    expect(loader).toHaveClass('w-full', 'flex-1', 'self-stretch', 'items-center', 'justify-center', 'min-h-48')
    expect(loader.closest('[role="dialog"]')).toHaveAccessibleName('Terminal output')
  })

  it('expands without refetching and Escape exits fullscreen before closing', async () => {
    const onClose = vi.fn()
    render(<OutputViewer terminalId="terminal-with-a-long-id" onClose={onClose} />)

    await waitFor(() => expect(api.getTerminalOutput).toHaveBeenCalledWith('terminal-with-a-long-id', 'last'))
    fireEvent.click(screen.getByRole('button', { name: 'Fullscreen' }))
    expect(screen.getByRole('button', { name: 'Exit fullscreen' })).toBeInTheDocument()
    expect(api.getTerminalOutput).toHaveBeenCalledTimes(1)

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.getByRole('button', { name: 'Fullscreen' })).toBeInTheDocument()
    expect(onClose).not.toHaveBeenCalled()

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('wraps long output without horizontal scrolling while toggling fullscreen', async () => {
    render(<OutputViewer terminalId="terminal-1" onClose={() => {}} />)
    await screen.findByText(/long output/)

    const output = screen.getByTestId('terminal-output-surface')
    expect(output).toHaveClass(
      'w-full',
      'max-w-full',
      'min-w-0',
      'whitespace-pre-wrap',
      'overflow-x-hidden',
      'overflow-y-auto',
      '[overflow-wrap:anywhere]',
      '[word-break:break-word]',
    )
    expect(output).not.toHaveClass('whitespace-pre', 'overflow-auto', 'max-w-none')

    fireEvent.click(screen.getByRole('button', { name: 'Fullscreen' }))
    expect(screen.getByText(/long output/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Exit fullscreen' }))
    expect(screen.getByText(/long output/)).toBeInTheDocument()
  })

  it('shows a truthful cleaned-output state for retained history', async () => {
    vi.mocked(api.getTerminalOutput).mockResolvedValueOnce({
      output: '',
      mode: 'last',
      availability: 'unavailable',
      reason_code: 'DURABLE_OUTPUT_UNAVAILABLE',
    })

    render(<OutputViewer terminalId="terminal-cleaned" onClose={() => {}} />)

    expect(await screen.findByText('Output unavailable')).toBeInTheDocument()
    expect(screen.getByText(/durable log may have been cleaned by Housekeeping/)).toBeInTheDocument()
    expect(screen.queryByText('No output available')).not.toBeInTheDocument()
  })
})

describe('InboxPanel composer and fullscreen', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(api, 'getInboxMessages').mockResolvedValue([])
    vi.spyOn(api, 'listDelegationResults').mockResolvedValue([])
  })

  it('uses the same body-centered loading state without moving its header or composer', () => {
    vi.mocked(api.getInboxMessages).mockReturnValue(new Promise(() => {}))
    vi.mocked(api.listDelegationResults).mockReturnValue(new Promise(() => {}))
    render(<InboxPanel terminalId="terminal-loading" onClose={() => {}} />)

    const loader = screen.getByRole('status')
    expect(loader).toHaveTextContent('Loading inbox messages')
    expect(loader).toHaveClass('w-full', 'flex-1', 'self-stretch', 'items-center', 'justify-center', 'min-h-48')
    expect(screen.getByText('Agent Inbox')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Inbox draft' })).toBeInTheDocument()
  })

  it('keeps a multiline draft while entering fullscreen and exits fullscreen before closing', async () => {
    const onClose = vi.fn()
    render(<InboxPanel terminalId="terminal-1" onClose={onClose} />)

    const draft = await screen.findByRole('textbox', { name: 'Inbox draft' })
    fireEvent.change(draft, { target: { value: 'first line\nsecond line' } })
    fireEvent.click(screen.getByRole('button', { name: 'Fullscreen' }))
    expect(screen.getByRole('button', { name: 'Exit fullscreen' })).toBeInTheDocument()
    expect(draft).toHaveValue('first line\nsecond line')

    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.getByRole('button', { name: 'Fullscreen' })).toBeInTheDocument()
    expect(onClose).not.toHaveBeenCalled()
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('sends the intact multiline draft only through the Inbox JSON API', async () => {
    const send = vi.spyOn(api, 'sendInboxMessage').mockResolvedValue({ success: true })
    render(<InboxPanel terminalId="terminal-1" onClose={() => {}} />)

    const draft = await screen.findByRole('textbox', { name: 'Inbox draft' })
    fireEvent.change(draft, { target: { value: 'first line\nsecond line — ✓' } })
    fireEvent.keyDown(draft, { key: 'Enter', ctrlKey: true })

    await waitFor(() => expect(send).toHaveBeenCalledWith('terminal-1', 'ui', 'first line\nsecond line — ✓'))
  })
})
