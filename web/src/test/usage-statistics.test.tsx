import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { api } from '../api'
import { UsageStatistics } from '../components/UsageStatistics'

describe('UsageStatistics', () => {
  it('labels truthful provider telemetry and shows bounded, readable aggregation surfaces', async () => {
    const aggregate = (total_tokens: number | null, input_tokens: number | null = null, cached_input_tokens: number | null = null, output_tokens: number | null = null) => ({
      provider_run_count: 1,
      input_tokens,
      cached_input_tokens,
      cache_write_input_tokens: 99,
      output_tokens,
      reasoning_output_tokens: output_tokens === null ? null : 2,
      total_tokens,
    })
    vi.spyOn(api, 'getUsageStatistics').mockResolvedValue({
      label: 'Provider-reported usage — not a billing statement',
      global: aggregate(null),
      terminals: [{ id: 'terminal-1', ...aggregate(15, 10, 2, 5) }],
      sessions: [
        { id: 'session-1', label: 'Current session', ...aggregate(15, 10, 2, 5) },
        { id: 'legacy-session-record:1', label: 'cao-reused', legacy: true, ...aggregate(10, 8, null, 2) },
      ],
      projects: [
        { id: 'project-1', label: 'Readable project', ...aggregate(15, 10, 2, 5) },
        { id: 'project-removed', label: 'Unknown project', ...aggregate(null) },
      ],
      providers: [{ id: 'codex', label: 'codex', ...aggregate(15, 10, 2, 5) }],
      profiles: [{ id: 'developer', label: 'developer', ...aggregate(15, 10, 2, 5) }],
    })

    render(<UsageStatistics />)

    await waitFor(() => expect(screen.getByText(/Provider-reported usage/)).toBeInTheDocument())
    expect(screen.getByText('By terminal')).toBeInTheDocument()
    expect(screen.getByText('By session')).toBeInTheDocument()
    expect(screen.getByText('Legacy session: reused')).toBeInTheDocument()
    expect(screen.getByText(/Legacy records remain separate/)).toBeInTheDocument()
    expect(screen.getByText('By project')).toBeInTheDocument()
    expect(screen.getByText('By provider')).toBeInTheDocument()
    expect(screen.getByText('By profile')).toBeInTheDocument()
    expect(screen.getAllByText('Showing top 10 by total where reported.')).toHaveLength(5)
    expect(screen.getByText('Readable project')).toBeInTheDocument()
    expect(screen.getByText('Unknown project')).toBeInTheDocument()
    expect(screen.getByText('project-1')).toHaveClass('font-mono')
    expect(screen.getAllByText('Not reported')).not.toHaveLength(0)
    expect(screen.getAllByText('Not reported')[0]).toHaveClass('text-gray-500', 'text-[11px]')
    expect(screen.getAllByText('Reasoning')).not.toHaveLength(0)
    expect(screen.queryByText('Cache write')).not.toBeInTheDocument()
    expect(screen.getAllByText('15').every(total => total.classList.contains('text-cyan-300'))).toBe(true)
    expect(screen.queryByText('Scope')).not.toBeInTheDocument()
    expect(screen.queryByText('Unattributed')).not.toBeInTheDocument()
  })
})
