import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api'
import { SettingsPanel } from '../components/SettingsPanel'

describe('Settings orchestration capacity', () => {
  afterEach(() => vi.restoreAllMocks())

  it('shows effective limits separately from live utilization and has no policy controls', async () => {
    vi.spyOn(api, 'getOperatorSession').mockResolvedValue({ configured: true, authenticated: false, expires_in_seconds: 0, session_ttl_seconds: 300, verifier_reference: 'THREADCELLS_OPERATOR_VERIFIER_FILE' })
    vi.spyOn(api, 'getAgentDirs').mockResolvedValue({ agent_dirs: {}, extra_dirs: [] })
    vi.spyOn(api, 'listProfiles').mockResolvedValue([{ name: 'developer', description: 'Default development profile', source: 'built-in' }])
    vi.spyOn(api, 'getOrchestrationCapacity').mockResolvedValue({
      resource_state: 'GREEN',
      reasons: [],
      resident_supervisors: { active: 5, limit: 5, available: 0, certain: true },
      provider_executions: { active: 2, limit: 3, available: 1, certain: true },
      provider_contexts: { active: 2, limit: 3, available: 1, certain: true },
      work_contexts: { active: 1, limit: 2, available: 1, certain: true },
      heavy_executions: { active: 1, limit: 1, available: 0, waiting: null },
      memory: { available_mib: 2048, swap_total_mib: 0, swap_free_mib: 0 },
      root_disk: { used_percent: 42.5, free_gib: 25.25 },
      memory_pressure: { some_avg10: 0, full_avg10: 0 },
      cpu_load: { one_minute: 1.75, cpu_count: 8 },
      housekeeping: { ok: true },
    })
    render(<SettingsPanel />)
    await screen.findByText('Orchestration Capacity')
    expect(screen.getByLabelText('Resource health GREEN')).toBeInTheDocument()
    expect(screen.getByText('2 / 3')).toBeInTheDocument()
    expect(screen.getByText('5 / 5')).toBeInTheDocument()
    expect(screen.getByText('1 / 2')).toBeInTheDocument()
    expect(screen.getByText('1 / 1')).toBeInTheDocument()
    expect(screen.getByText('2048 MiB')).toBeInTheDocument()
    expect(screen.getByText('42.5% used')).toBeInTheDocument()
    expect(screen.getByText('1.75 / 8 CPUs')).toBeInTheDocument()
    expect(screen.getByText('1m Linux load average')).toBeInTheDocument()
    const profiles = screen.getByRole('button', { name: /^Profiles$/ })
    const finalRow = screen.getByText('CPU load').parentElement?.parentElement
    expect(finalRow).toContainElement(profiles)
    expect(finalRow).toHaveClass('sm:col-span-2', 'sm:grid-cols-2')
    expect(screen.getByLabelText('Orchestration capacity details')).not.toContainElement(profiles)
    expect(profiles).toHaveClass('text-left')
    expect(profiles).toHaveTextContent('1')
    expect(profiles).toHaveTextContent('click me ->')
    fireEvent.click(profiles)
    expect(await screen.findByRole('dialog', { name: 'Profiles' })).toHaveTextContent('Default development profile')
    expect(screen.queryByRole('button', { name: /apply resource/i })).not.toBeInTheDocument()
    expect(screen.getByText('Provider executions')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'About this build' })).not.toBeInTheDocument()
    expect(screen.queryByText('Provider contexts')).not.toBeInTheDocument()
    await waitFor(() => expect(api.getOrchestrationCapacity).toHaveBeenCalledTimes(1))
  })

  it('shows the authoritative RED reason when root disk health is only YELLOW', async () => {
    vi.spyOn(api, 'getOperatorSession').mockResolvedValue({ configured: true, authenticated: false, expires_in_seconds: 0, session_ttl_seconds: 300, verifier_reference: 'THREADCELLS_OPERATOR_VERIFIER_FILE' })
    vi.spyOn(api, 'getAgentDirs').mockResolvedValue({ agent_dirs: {}, extra_dirs: [] })
    vi.spyOn(api, 'listProfiles').mockResolvedValue([])
    vi.spyOn(api, 'getOrchestrationCapacity').mockResolvedValue({
      resource_state: 'RED',
      reasons: ['critical_memory_pressure', 'NEW_RESOURCE_SIGNAL'],
      resident_supervisors: { active: 0, limit: 5, available: 5, certain: true },
      provider_executions: { active: 0, limit: 3, available: 3, certain: true },
      work_contexts: { active: 0, limit: 2, available: 2, certain: true },
      heavy_executions: { active: 0, limit: 1, available: 1, waiting: null },
      memory: { available_mib: 2048, swap_total_mib: 0, swap_free_mib: 0 },
      root_disk: { state: 'YELLOW', used_percent: 77.5, free_gib: 10.62 },
      memory_pressure: { some_avg10: 6, full_avg10: 1 },
      cpu_load: { one_minute: 0.5, cpu_count: 4 },
      housekeeping: null,
    })

    render(<SettingsPanel />)

    expect(await screen.findByLabelText('Resource health RED')).toBeInTheDocument()
    const reasons = screen.getByLabelText('RED resource health reasons')
    expect(reasons).toHaveTextContent('Memory PSI full pressure reached the RED threshold')
    expect(reasons).toHaveTextContent('NEW_RESOURCE_SIGNAL')
    expect(screen.getByText('YELLOW · 10.62 GiB free')).toBeInTheDocument()
  })

  it('keeps the project list separate from registration and preserves project guidance in both forms', async () => {
    vi.spyOn(api, 'getOperatorSession').mockResolvedValue({ configured: false, authenticated: false, expires_in_seconds: 0, session_ttl_seconds: 300, verifier_reference: 'THREADCELLS_OPERATOR_VERIFIER_FILE' })
    const project = { projectId: 'project-a', name: 'Project A', path: '/workspace/project-a', description: 'Existing guidance', isDefault: true }
    vi.spyOn(api, 'getAgentDirs').mockResolvedValue({ agent_dirs: {}, extra_dirs: [] })
    vi.spyOn(api, 'listProfiles').mockResolvedValue([])
    vi.spyOn(api, 'getOrchestrationCapacity').mockRejectedValue(new Error('not needed'))
    vi.spyOn(api, 'listProjects').mockResolvedValue([project])
    vi.spyOn(api, 'getBranding').mockResolvedValue({ title: 'ThreadCells', subtitle: 'Multi-agent control plane', logoUrl: '/threadcells-symbol.png', customLogo: false })
    const create = vi.spyOn(api, 'createProject').mockResolvedValue({} as never)
    const update = vi.spyOn(api, 'updateProject').mockResolvedValue({ ...project, name: 'ThreadCells core' })
    render(<SettingsPanel />)

    expect(await screen.findByText('Project A')).toBeInTheDocument()
    expect(screen.queryByPlaceholderText('Project name')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Register New Project' }))

    const createDialog = screen.getByRole('dialog', { name: 'Register New Project' })
    expect(within(createDialog).getByText('Used both as a human-readable description and as project-scoped operational guidance for agents and flows.')).toBeInTheDocument()
    fireEvent.change(within(createDialog).getByLabelText('New project name'), { target: { value: 'Project B' } })
    fireEvent.change(within(createDialog).getByLabelText('New project path'), { target: { value: '/workspace/project-b' } })
    fireEvent.change(within(createDialog).getByLabelText('New project description'), { target: { value: 'New guidance' } })
    fireEvent.click(within(createDialog).getByRole('button', { name: 'Register project' }))

    const confirmDialog = screen.getByRole('dialog', { name: 'Register project' })
    fireEvent.click(within(confirmDialog).getByRole('button', { name: 'Register project' }))
    await waitFor(() => expect(create).toHaveBeenCalledWith({ name: 'Project B', path: '/workspace/project-b', description: 'New guidance', createDirectory: false, isDefault: false }))

    expect(screen.getByRole('button', { name: 'Set Project A as default' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Remove Project A' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Edit Project A' }))
    const editDialog = await screen.findByRole('dialog', { name: 'Edit project' })
    expect(editDialog).toHaveTextContent('Used both as a human-readable description and as project-scoped operational guidance for agents and flows.')
    fireEvent.change(within(editDialog).getByLabelText('Project name'), { target: { value: 'ThreadCells core' } })
    fireEvent.click(within(editDialog).getByRole('button', { name: 'Save project' }))
    await waitFor(() => expect(update).toHaveBeenCalledWith('project-a', { name: 'ThreadCells core', path: '/workspace/project-a', description: 'Existing guidance' }))
  })
})
