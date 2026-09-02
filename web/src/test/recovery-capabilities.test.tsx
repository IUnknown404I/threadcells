import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AgentSummary, api } from '../api'
import { RecoveryTakeoverAction } from '../components/RecoveryTakeoverAction'
import { useRecoveryTakeoverCapabilities } from '../recoveryCapabilities'

const agent = {
  id: 'a11ce001',
  name: 'owner',
  provider: 'codex',
  session_id: 'session-1',
  session_name: 'cao-session-1',
  agent_profile: 'critical_sol_xhigh_owner',
  activity: 'idle',
  execution_state: 'ready',
  lifecycle: 'running',
  workflow_state: 'completed',
  workflow_status: 'completed',
  workflow_reason: null,
  assignment_status: null,
  result_status: null,
  delivery_status: null,
  context_role: 'supervisor',
  launch_worktree: '/managed/worktree',
  managed_worktree_kind: 'supervisor',
  managed_worktree_commit: 'a'.repeat(40),
  managed_worktree_branch: 'threadcells/session-1',
  projectId: 'project-1',
  project_name: 'Project',
  project_path: '/source',
  creation_order: 1,
  last_active: null,
} satisfies AgentSummary

function Harness({ refreshKey }: { refreshKey: number }) {
  const capabilities = useRecoveryTakeoverCapabilities([agent], refreshKey)
  return <span>{capabilities[agent.id]?.eligible ? 'eligible' : capabilities[agent.id] ? 'blocked' : 'pending'}</span>
}

describe('recovery action capability projection', () => {
  afterEach(() => vi.restoreAllMocks())

  it('converges from blocked to eligible after reconciliation refresh', async () => {
    const capability = vi.spyOn(api, 'getRecoveryTakeoverCapabilities')
      .mockResolvedValueOnce({
        capabilities: [{ terminal_id: agent.id, eligible: false, reason_code: 'RECOVERY_HEALTHY_RUNTIME_ACTIVE' }],
      })
      .mockResolvedValueOnce({
        capabilities: [{ terminal_id: agent.id, eligible: true, reason_code: null }],
      })

    const view = render(<Harness refreshKey={0}/>)
    expect(await screen.findByText('blocked')).toBeInTheDocument()
    view.rerender(<Harness refreshKey={1}/>)
    expect(await screen.findByText('eligible')).toBeInTheDocument()
    await waitFor(() => expect(capability).toHaveBeenNthCalledWith(2, [agent.id], expect.any(AbortSignal)))
  })

  it('fails closed when the capability inventory request fails', async () => {
    vi.spyOn(api, 'getRecoveryTakeoverCapabilities').mockRejectedValue(new Error('inventory unavailable'))
    render(<Harness refreshKey={0}/>)
    await waitFor(() => expect(api.getRecoveryTakeoverCapabilities).toHaveBeenCalled())
    expect(screen.getByText('pending')).toBeInTheDocument()
  })

  it('drops an open dialog when refreshed authority becomes ineligible', async () => {
    vi.spyOn(api, 'listProfiles').mockResolvedValue([])
    vi.spyOn(api, 'listProviders').mockResolvedValue([])
    const view = render(<RecoveryTakeoverAction
      agent={agent}
      capability={{ terminal_id: agent.id, eligible: true, reason_code: null }}
      onCompleted={() => {}}
      className="action"
    />)
    fireEvent.click(screen.getByRole('button', { name: 'Recover agent' }))
    expect(screen.getByRole('dialog', { name: 'Recover supervisor authority' })).toBeInTheDocument()

    view.rerender(<RecoveryTakeoverAction
      agent={agent}
      capability={{
        terminal_id: agent.id,
        eligible: false,
        reason_code: 'RECOVERY_HEALTHY_RUNTIME_ACTIVE',
      }}
      onCompleted={() => {}}
      className="action"
    />)
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    view.rerender(<RecoveryTakeoverAction
      agent={agent}
      capability={{ terminal_id: agent.id, eligible: true, reason_code: null }}
      onCompleted={() => {}}
      className="action"
    />)
    expect(screen.getByRole('button', { name: 'Recover agent' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
