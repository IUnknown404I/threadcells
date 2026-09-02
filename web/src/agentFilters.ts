export type AgentViewMode = 'sessions' | 'statuses' | 'profiles'
export type HomeAgentFilter = 'all' | 'active' | 'waiting' | 'owner_gate' | 'cancelled' | 'completed'

export interface AgentFilterState {
  view: AgentViewMode
  providerStatuses: string[]
  workflowStates: string[]
  profiles: string[]
  homeFilter: HomeAgentFilter | null
}

export interface AgentStatusLike {
  lifecycle?: string | null
  workflow_state?: string | null
  status?: string | null
}

export const DEFAULT_AGENT_FILTER_STATE: AgentFilterState = {
  view: 'sessions',
  providerStatuses: [],
  workflowStates: [],
  profiles: [],
  homeFilter: null,
}

export const HOME_FILTER_LABELS: Record<HomeAgentFilter, string> = {
  all: 'All agents',
  active: 'Active agents',
  waiting: 'Ready / waiting',
  owner_gate: 'Needs attention',
  cancelled: 'Cancelled',
  completed: 'Completed',
}

function listParam(params: URLSearchParams, name: string): string[] {
  return (params.get(name) || '').split(',').map(value => value.trim()).filter(Boolean)
}

function stableList(values: string[]): string[] {
  return Array.from(new Set(values)).sort()
}

export function parseAgentFilterState(search: string): AgentFilterState {
  const params = new URLSearchParams(search)
  const view = params.get('agentView')
  const homeFilter = params.get('agentFilter')
  return {
    view: view === 'statuses' || view === 'profiles' || view === 'sessions' ? view : 'sessions',
    providerStatuses: stableList(listParam(params, 'providerStatus')),
    workflowStates: stableList(listParam(params, 'workflowState')),
    profiles: stableList(listParam(params, 'agentProfile')),
    homeFilter: homeFilter === 'all' || homeFilter === 'active' || homeFilter === 'waiting' || homeFilter === 'owner_gate' || homeFilter === 'cancelled' || homeFilter === 'completed' ? homeFilter : null,
  }
}

export function applyAgentFilterState(params: URLSearchParams, state: AgentFilterState): URLSearchParams {
  const next = new URLSearchParams(params)
  for (const key of ['agentView', 'providerStatus', 'workflowState', 'agentProfile', 'agentFilter']) next.delete(key)
  if (state.view !== 'sessions') next.set('agentView', state.view)
  if (state.providerStatuses.length) next.set('providerStatus', stableList(state.providerStatuses).join(','))
  if (state.workflowStates.length) next.set('workflowState', stableList(state.workflowStates).join(','))
  if (state.profiles.length) next.set('agentProfile', stableList(state.profiles).join(','))
  if (state.homeFilter) next.set('agentFilter', state.homeFilter)
  return next
}

export function matchesHomeAgentFilter(terminal: AgentStatusLike, filter: HomeAgentFilter): boolean {
  switch (filter) {
    case 'all':
      return true
    case 'active':
      return terminal.lifecycle === 'running' && terminal.workflow_state !== 'completed'
    case 'waiting':
      // This is the former Home `WORKFLOW_*::Ready` badge predicate, expressed
      // against the same raw values so it can also drive the Agents projection.
      return Boolean(terminal.workflow_state)
        && !['owner_gate', 'cancelled', 'completed'].includes(terminal.workflow_state || '')
        && !['exited', 'recovery_fenced'].includes(terminal.lifecycle || '')
        && terminal.status !== 'processing'
    case 'owner_gate':
    case 'cancelled':
    case 'completed':
      return terminal.workflow_state === filter
  }
}

export function matchesStatusFilters(
  terminal: AgentStatusLike,
  filters: Pick<AgentFilterState, 'providerStatuses' | 'workflowStates' | 'homeFilter'>,
): boolean {
  const providerMatches = filters.providerStatuses.length === 0 || (terminal.status !== null && terminal.status !== undefined && filters.providerStatuses.includes(terminal.status))
  const workflowMatches = filters.workflowStates.length === 0 || (terminal.workflow_state !== null && terminal.workflow_state !== undefined && filters.workflowStates.includes(terminal.workflow_state))
  const homeMatches = !filters.homeFilter || matchesHomeAgentFilter(terminal, filters.homeFilter)
  return providerMatches && workflowMatches && homeMatches
}

export function homeAgentFilterState(filter: HomeAgentFilter): AgentFilterState {
  return { ...DEFAULT_AGENT_FILTER_STATE, view: 'statuses', homeFilter: filter }
}
