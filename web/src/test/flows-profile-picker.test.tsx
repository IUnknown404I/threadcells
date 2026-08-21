import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api'
import { FlowsPanel } from '../components/FlowsPanel'

const profile = { name: 'developer_terra_high', description: 'Complex defects, debugging, and substantial bounded refactors', source: 'built-in' as const }
const existingFlow = { name: 'existing-flow', file_path: '/flows/existing.md', schedule: '0 * * * *', agent_profile: profile.name, provider: 'kiro_cli', script: null, last_run: null, next_run: null, enabled: true, prompt_template: 'existing prompt' }

function setup(flows = [existingFlow]) {
  vi.spyOn(api, 'listFlows').mockResolvedValue(flows)
  vi.spyOn(api, 'listProfiles').mockResolvedValue([profile])
  vi.spyOn(api, 'listProviders').mockResolvedValue([{ name: 'kiro_cli', binary: 'kiro', installed: true }, { name: 'codex', binary: 'codex', installed: true }])
}

describe('Create Flow canonical defaults and profile selection', () => {
  afterEach(() => vi.restoreAllMocks())

  it('defaults each new flow to codex without changing an existing flow provider', async () => {
    setup()
    render(<FlowsPanel />)
    await screen.findByText('existing-flow')
    expect(screen.getByText('kiro_cli')).toBeInTheDocument()
    fireEvent.click(screen.getAllByRole('button', { name: 'Create Flow' }).slice(-1)[0])
    expect(screen.getByRole('button', { name: 'codex' })).toBeInTheDocument()
    fireEvent.click(screen.getByText('Cancel'))
    fireEvent.click(screen.getAllByRole('button', { name: 'Create Flow' }).slice(-1)[0])
    expect(screen.getByRole('button', { name: 'codex' })).toBeInTheDocument()
  })

  it('uses the shared searchable ProfilePicker and submits its canonical selection unchanged', async () => {
    setup([])
    const create = vi.spyOn(api, 'createFlow').mockResolvedValue({} as never)
    render(<FlowsPanel />)
    await screen.findByText('No flows configured.')
    fireEvent.click(screen.getByRole('button', { name: 'Create Flow' }))
    fireEvent.click(screen.getByRole('button', { name: 'Select a profile...' }))
    fireEvent.change(screen.getByPlaceholderText('Search profiles...'), { target: { value: 'TERRA_HIGH' } })
    expect(screen.getByText(profile.description)).toBeInTheDocument()
    fireEvent.click(screen.getByText(profile.name))
    fireEvent.change(screen.getByPlaceholderText('my-daily-review'), { target: { value: 'daily-review' } })
    fireEvent.click(screen.getByRole('button', { name: 'Pick a schedule...' }))
    fireEvent.click(screen.getByText('Every hour'))
    fireEvent.change(screen.getByPlaceholderText('Describe what this flow should do...'), { target: { value: 'Review the repository.' } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Create Flow' }).slice(-1)[0])
    await waitFor(() => expect(create).toHaveBeenCalledWith({ name: 'daily-review', schedule: '0 * * * *', agent_profile: profile.name, provider: 'codex', prompt_template: 'Review the repository.' }))
    expect(screen.queryByPlaceholderText('e.g. developer')).not.toBeInTheDocument()
  })
})
