import { afterEach, describe, expect, it, vi } from 'vitest'
import { buildSafeDiagnosticReport, copyDiagnosticReport, downloadDiagnosticReport } from '../diagnosticReport'

describe('safe diagnostic report', () => {
  afterEach(() => vi.restoreAllMocks())

  it('copies only explicitly allowlisted scalar fields', () => {
    const report = buildSafeDiagnosticReport({
      version: '0.4.1-alpha',
      revision: 'abcdef0',
      uiState: 'disconnected',
      reasonCode: 'RECOVERY_REQUIRED',
      correlationId: 'corr_123456',
      ...({ cookie: 'secret', nested: { resume_token: 'private' } } as object),
    })
    expect(report).toContain('version: 0.4.1-alpha')
    expect(report).toContain('reason_code: RECOVERY_REQUIRED')
    expect(report).toContain('correlation_id: corr_123456')
    expect(report).not.toContain('secret')
    expect(report).not.toContain('resume_token')
    expect(report).not.toContain('nested')
  })

  it('uses the clipboard and reports unavailable clipboard truthfully', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    expect(await copyDiagnosticReport('safe')).toBe(true)
    expect(writeText).toHaveBeenCalledWith('safe')
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined })
    expect(await copyDiagnosticReport('safe')).toBe(false)
  })

  it('exports the same text without adding unknown data', () => {
    const click = vi.fn()
    vi.spyOn(document, 'createElement').mockReturnValue({ click } as unknown as HTMLAnchorElement)
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:safe')
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
    downloadDiagnosticReport('safe')
    expect(click).toHaveBeenCalledOnce()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:safe')
  })
})
