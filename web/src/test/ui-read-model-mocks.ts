import { vi } from 'vitest'
import { AgentSummary, Session, SessionSummary, TerminalMeta, api } from '../api'
import { useStore } from '../store'

function page<T>(items: T[], params: { limit?: number; offset?: number }) {
  const offset = params.offset || 0
  const limit = params.limit || 100
  const selected = items.slice(offset, offset + limit)
  return {
    items: selected,
    total: items.length,
    limit,
    offset,
    next_offset: offset + selected.length < items.length ? offset + selected.length : null,
  }
}

function stateFor(terminalId: string): Record<string, any> {
  const stored = (useStore.getState().terminalStatuses as Record<string, any>)[terminalId]
  if (stored && typeof stored === 'object') return stored
  if (typeof stored !== 'string') return {}
  const [workflowLabel, activityLabel] = stored.split('::')
  const workflow = workflowLabel?.toLowerCase().replace(/^workflow_/, '')
  return {
    status: activityLabel?.toLowerCase(),
    workflow_state: workflow === 'terminal' ? 'completed' : workflow,
  }
}

async function statusFor(terminalId: string): Promise<Record<string, any>> {
  if (vi.isMockFunction(api.getTerminalStatus)) {
    try { return await api.getTerminalStatus(terminalId) as Record<string, any> }
    catch { /* retain the durable fixture state */ }
  }
  return stateFor(terminalId)
}

async function detailFor(session: Session): Promise<{ terminals: TerminalMeta[] }> {
  const active = useStore.getState().activeSessionDetail
  if (active?.session.id === session.id || active?.session.name === session.name) {
    return { terminals: active.terminals }
  }
  if (vi.isMockFunction(api.getSession)) {
    try { return await api.getSession(session.name) }
    catch { /* render the durable session shell */ }
  }
  return { terminals: [] }
}

async function allAgents(): Promise<AgentSummary[]> {
  const result: AgentSummary[] = []
  for (const session of useStore.getState().sessions) {
    const detail = await detailFor(session)
    for (const [creationIndex, terminal] of detail.terminals.entries()) {
      const state = await statusFor(terminal.id)
      const activity = String(state.activity || state.status || 'idle').toLowerCase()
      const lifecycleValue = String(state.lifecycle || 'running').toLowerCase()
      const lifecycle: AgentSummary['lifecycle'] = ['starting', 'running', 'exit_pending', 'exited', 'recovery_fenced'].includes(lifecycleValue)
        ? lifecycleValue as AgentSummary['lifecycle']
        : 'running'
      result.push({
        id: terminal.id,
        name: terminal.tmux_window,
        provider: terminal.provider,
        session_id: session.id,
        session_name: session.name,
        agent_profile: terminal.agent_profile,
        activity,
        execution_state: state.execution_state || (activity === 'processing' ? 'processing' : 'ready'),
        lifecycle,
        workflow_state: state.workflow_state || null,
        workflow_status: state.workflow_status || null,
        workflow_reason: state.workflow_reason || null,
        assignment_status: state.assignment_status || null,
        result_status: state.result_status || null,
        delivery_status: state.delivery_status || null,
        context_role: state.context_role || null,
        launch_worktree: state.launch_worktree || null,
        managed_worktree_kind: state.managed_worktree_kind || null,
        managed_worktree_commit: state.managed_worktree_commit || null,
        managed_worktree_branch: state.managed_worktree_branch || null,
        projectId: terminal.project_id || null,
        project_name: terminal.project_name || null,
        project_path: terminal.project_path || null,
        creation_order: Number((terminal as TerminalMeta & { creation_order?: number }).creation_order ?? creationIndex + 1),
        last_active: terminal.last_active,
      })
    }
  }
  return result
}

function counts(values: Array<string | null>) {
  return values.reduce<Record<string, number>>((result, value) => {
    const key = value || 'untracked'
    result[key] = (result[key] || 0) + 1
    return result
  }, {})
}

async function sessionSummaries(): Promise<SessionSummary[]> {
  const agents = await allAgents()
  return useStore.getState().sessions.map(session => {
    const members = agents.filter(agent => agent.session_id === session.id)
      .sort((left, right) => left.creation_order - right.creation_order || left.id.localeCompare(right.id))
    const exactProject = members.length > 0
      && members.every(agent => agent.projectId && agent.project_name && agent.project_path)
      && members.every(agent => agent.projectId === members[0].projectId)
    return {
      ...session,
      agent_count: members.length,
      active_agent_count: members.filter(agent => !agent.lifecycle || !['exited', 'recovery_fenced'].includes(agent.lifecycle)).length,
      workflow_counts: counts(members.map(agent => agent.workflow_state)),
      activity_counts: counts(members.map(agent => agent.activity)),
      project_name: exactProject ? members[0].project_name : null,
      last_active: members.map(agent => agent.last_active).filter(Boolean).sort().slice(-1)[0] || null,
      first_agent: members.length ? {
        id: members[0].id,
        activity: members[0].activity,
        execution_state: members[0].execution_state,
        lifecycle: members[0].lifecycle,
        workflow_state: members[0].workflow_state,
        workflow_reason: members[0].workflow_reason,
      } : null,
      last_agent: members.length ? {
        id: members[members.length - 1].id,
        activity: members[members.length - 1].activity,
        execution_state: members[members.length - 1].execution_state,
        lifecycle: members[members.length - 1].lifecycle,
        workflow_state: members[members.length - 1].workflow_state,
        workflow_reason: members[members.length - 1].workflow_reason,
      } : null,
    }
  })
}

export function installUiReadModelSpies() {
  vi.spyOn(api, 'listSessionSummaries').mockImplementation(async params => {
    const resolved = params || {}
    const query = resolved.query?.toLowerCase() || ''
    const items = (await sessionSummaries()).filter(item => item.name.toLowerCase().includes(query))
    return page(items, resolved)
  })
  vi.spyOn(api, 'listAgentSummaries').mockImplementation(async params => {
    const resolved = params || {}
    const all = await allAgents()
    const facets = {
      activities: [...new Set(all.map(item => item.activity).filter(Boolean) as string[])],
      workflow_states: [...new Set(all.map(item => item.workflow_state).filter(Boolean) as string[])],
      profiles: [...new Set(all.map(item => item.agent_profile).filter(Boolean) as string[])],
    }
    const query = resolved.query?.toLowerCase() || ''
    const items = all.filter(item => {
      if (resolved.sessionId && item.session_id !== resolved.sessionId) return false
      if (query && !`${item.id} ${item.name} ${item.agent_profile || ''}`.toLowerCase().includes(query)) return false
      if (resolved.activities?.length && !resolved.activities.includes(item.activity || '')) return false
      if (resolved.workflowStates?.length && !resolved.workflowStates.includes(item.workflow_state || '')) return false
      if (resolved.profiles?.length && !resolved.profiles.includes(item.agent_profile || '')) return false
      if (resolved.homeFilter === 'active' && item.lifecycle === 'exited') return false
      if (resolved.homeFilter === 'waiting' && (
        !item.workflow_state
        || ['owner_gate', 'cancelled', 'completed'].includes(item.workflow_state)
        || item.lifecycle === 'exited'
        || item.activity === 'processing'
      )) return false
      if (resolved.homeFilter === 'owner_gate' && item.workflow_state !== 'owner_gate') return false
      if (resolved.homeFilter === 'cancelled' && item.workflow_state !== 'cancelled') return false
      if (resolved.homeFilter === 'completed' && item.workflow_state !== 'completed') return false
      return true
    }).sort((left, right) => resolved.sessionId
      ? left.creation_order - right.creation_order || left.id.localeCompare(right.id)
      : 0)
    return { ...page(items, resolved), facets }
  })
  vi.spyOn(api, 'getUiOverview').mockImplementation(async () => {
    const agents = await allAgents()
    return {
      sessions: useStore.getState().sessions.length,
      agents: agents.length,
      active: agents.filter(item => !item.lifecycle || !['exited', 'recovery_fenced'].includes(item.lifecycle)).length,
      waiting: agents.filter(item => (
        Boolean(item.workflow_state)
        && !['owner_gate', 'cancelled', 'completed'].includes(item.workflow_state || '')
        && item.lifecycle !== 'exited'
        && item.activity !== 'processing'
      )).length,
      owner_gate: agents.filter(item => item.workflow_state === 'owner_gate').length,
      cancelled: agents.filter(item => item.workflow_state === 'cancelled').length,
      completed: agents.filter(item => item.workflow_state === 'completed').length,
    }
  })
}
