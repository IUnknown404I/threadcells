import { describe, expect, it } from 'vitest'
import { applyAgentFilterState, homeAgentFilterState, matchesHomeAgentFilter, matchesStatusFilters, parseAgentFilterState } from '../agentFilters'

const agents = [
  { lifecycle: 'running' as const, workflow_state: 'active' as const, status: 'idle' },
  { lifecycle: 'running' as const, workflow_state: 'owner_gate' as const, status: 'idle' },
  { lifecycle: 'running' as const, workflow_state: 'waiting' as const, status: 'idle' },
  { lifecycle: 'exited' as const, workflow_state: 'cancelled' as const, status: 'idle' },
  { lifecycle: 'running' as const, workflow_state: 'completed' as const, status: 'completed' },
  { lifecycle: 'running' as const, workflow_state: 'active' as const, status: 'processing' },
]

describe('canonical agent filters', () => {
  it('keeps every Home count equal to its preconfigured Agents status predicate', () => {
    expect(agents.filter(agent => matchesHomeAgentFilter(agent, 'all'))).toHaveLength(6)
    expect(agents.filter(agent => matchesHomeAgentFilter(agent, 'active'))).toHaveLength(4)
    expect(agents.filter(agent => matchesHomeAgentFilter(agent, 'waiting'))).toHaveLength(4)
    expect(agents.filter(agent => matchesHomeAgentFilter(agent, 'owner_gate'))).toHaveLength(1)
    expect(agents.filter(agent => matchesHomeAgentFilter(agent, 'cancelled'))).toHaveLength(1)
    expect(agents.filter(agent => matchesHomeAgentFilter(agent, 'completed'))).toHaveLength(1)
    for (const filter of ['all', 'active', 'waiting', 'owner_gate', 'cancelled', 'completed'] as const) {
      const state = homeAgentFilterState(filter)
      expect(agents.filter(agent => matchesStatusFilters(agent, state))).toHaveLength(agents.filter(agent => matchesHomeAgentFilter(agent, filter)).length)
    }
  })

  it('round-trips a deterministic URL state without hidden filter values', () => {
    const state = { ...homeAgentFilterState('owner_gate'), providerStatuses: ['processing', 'idle'], workflowStates: ['active'], profiles: [] }
    const search = applyAgentFilterState(new URLSearchParams('tab=agents'), state).toString()
    expect(search).toBe('tab=agents&agentView=statuses&providerStatus=idle%2Cprocessing&workflowState=active&agentFilter=owner_gate')
    expect(parseAgentFilterState(`?${search}`)).toEqual({ ...state, providerStatuses: ['idle', 'processing'] })
  })
})
