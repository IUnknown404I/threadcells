import { describe, expect, it } from 'vitest'
import { CaoApiError, normalizeApiError } from '../api'

describe('CAO API error normalization', () => {
  it('turns a writer lease lock into actionable working-directory guidance', () => {
    const error = normalizeApiError(423, { detail: { reason_code: 'WORKTREE_WRITER_LEASE_HELD' } })
    expect(error.title).toBe('Working directory is locked')
    expect(error.description).toContain('Another active write-capable agent')
    expect(error.message).toContain('HTTP 423 · WORKTREE_WRITER_LEASE_HELD')
  })

  it('keeps a known capacity reason more specific than its 503 status', () => {
    const error = normalizeApiError(503, { detail: { reason_code: 'WORK_CONTEXT_CAPACITY_EXHAUSTED' } })
    expect(error.title).toBe('Capacity limit reached')
    expect(error.description).toContain('work slot')
    expect(error.message).toContain('WORK_CONTEXT_CAPACITY_EXHAUSTED')
  })

  it('uses meaningful structured validation detail for invalid requests', () => {
    const error = normalizeApiError(422, { detail: 'Session name may not contain a slash' })
    expect(error.title).toBe('Invalid request')
    expect(error.description).toBe('Session name may not contain a slash')
  })

  it('provides a server-error fallback when no backend detail exists', () => {
    const error = normalizeApiError(500, null)
    expect(error.title).toBe('ThreadCells server error')
    expect(error.description).toContain('unexpectedly')
  })

  it('keeps unknown reason codes as secondary technical metadata', () => {
    const error = normalizeApiError(503, { reason_code: 'FUTURE_CAUSE', detail: 'A safe backend explanation' })
    expect(error.title).toBe('ThreadCells service unavailable')
    expect(error.description).toBe('A safe backend explanation')
    expect(error.message).toContain('HTTP 503 · FUTURE_CAUSE')
  })

  it('shows a nested graceful-exit authority reason from FastAPI', () => {
    const error = normalizeApiError(409, { detail: { reason_code: 'EXIT_PANE_AMBIGUOUS', message: 'The terminal window has multiple panes' } })
    expect(error.description).toBe('The terminal window has multiple panes')
    expect(error.message).toContain('EXIT_PANE_AMBIGUOUS')
  })

  it('never exposes a raw numeric status as the only visible error content', () => {
    for (const status of [400, 423, 500, 503]) {
      const error: CaoApiError = normalizeApiError(status, null)
      expect(error.message).not.toBe(String(status))
      expect(error.title).not.toBe(String(status))
      expect(error.description.length).toBeGreaterThan(20)
    }
  })
})
