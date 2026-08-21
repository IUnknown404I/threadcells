import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api'
import { AgentPanel } from '../components/AgentPanel'
import { ProfilePicker } from '../components/ProfilePicker'
import { SettingsPanel } from '../components/SettingsPanel'

const profile = { name: 'developer', description: 'Implements and fixes code.', source: 'built-in' as const }

describe('canonical profile metadata UI', () => {
  afterEach(() => vi.restoreAllMocks())

  it('renders the API-provided description in the searchable picker', () => {
    render(<ProfilePicker profiles={[profile]} value="" onChange={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Select a profile...' }))
    expect(screen.getByText(profile.description)).toBeInTheDocument()
  })

  it('uses the same canonical English description in Spawn quick pick and Settings', async () => {
    vi.spyOn(api, 'listProfiles').mockResolvedValue([profile])
    vi.spyOn(api, 'listProviders').mockResolvedValue([{ name: 'codex', binary: 'codex', installed: true }])
    vi.spyOn(api, 'listProjects').mockResolvedValue([])
    const { unmount } = render(<AgentPanel />)
    fireEvent.click(screen.getByRole('button', { name: 'Create Session & Spawn Agent' }))
    expect(await screen.findByText(profile.description)).toBeInTheDocument()
    unmount()

    vi.spyOn(api, 'getAgentDirs').mockResolvedValue({ agent_dirs: {}, extra_dirs: [] })
    vi.spyOn(api, 'getOrchestrationCapacity').mockResolvedValue({ resource_state: 'GREEN', reasons: [], resident_supervisors: { active: 0, limit: 5, available: 5, certain: true }, provider_executions: { active: 0, limit: 3, available: 3, certain: true }, work_contexts: { active: 0, limit: 2, available: 2, certain: true }, heavy_executions: { active: 0, limit: 1, available: 1, waiting: null }, memory: { available_mib: 1, swap_total_mib: 0, swap_free_mib: 0 }, root_disk: { used_percent: 1, free_gib: 1 }, memory_pressure: { some_avg10: 0, full_avg10: 0 }, cpu_load: { one_minute: 0, cpu_count: 1 }, housekeeping: null })
    render(<SettingsPanel />)
    await screen.findByText('Available agent profiles and their descriptions')
    fireEvent.click(screen.getByText('Available agent profiles and their descriptions'))
    expect(await screen.findByRole('dialog', { name: 'Profiles' })).toHaveTextContent(profile.description)
  })

  it('uses the same canonical description and English modal copy in Settings', async () => {
    vi.spyOn(api, 'listProfiles').mockResolvedValue([profile])
    vi.spyOn(api, 'getAgentDirs').mockResolvedValue({ agent_dirs: {}, extra_dirs: [] })
    vi.spyOn(api, 'getOrchestrationCapacity').mockResolvedValue({ resource_state: 'GREEN', reasons: [], resident_supervisors: { active: 0, limit: 5, available: 5, certain: true }, provider_executions: { active: 0, limit: 3, available: 3, certain: true }, work_contexts: { active: 0, limit: 2, available: 2, certain: true }, heavy_executions: { active: 0, limit: 1, available: 1, waiting: null }, memory: { available_mib: 1, swap_total_mib: 0, swap_free_mib: 0 }, root_disk: { used_percent: 1, free_gib: 1 }, memory_pressure: { some_avg10: 0, full_avg10: 0 }, cpu_load: { one_minute: 0, cpu_count: 1 }, housekeeping: null })
    render(<SettingsPanel />)
    await screen.findByText('Available agent profiles and their descriptions')
    fireEvent.click(screen.getByText('Available agent profiles and their descriptions'))
    expect(await screen.findByRole('dialog', { name: 'Profiles' })).toHaveTextContent(profile.description)
    expect(screen.getByRole('button', { name: 'Fullscreen' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument()
  })
})
