export type SafeDiagnosticInput = {
  version: string
  revision: string
  uiState: 'connected' | 'disconnected'
  reasonCode?: string | null
  correlationId?: string | null
  providerId?: string | null
  profileId?: string | null
}

const safe = (value: unknown, pattern: RegExp, fallback = 'unavailable') =>
  typeof value === 'string' && pattern.test(value) ? value : fallback

/** Build a deterministic report from named scalar fields only. Unknown/nested input is unreachable. */
export function buildSafeDiagnosticReport(input: SafeDiagnosticInput): string {
  const lines = [
    'ThreadCells safe diagnostic summary',
    'schema: 1',
    `version: ${safe(input.version, /^[A-Za-z0-9._+-]{1,64}$/)}`,
    `revision: ${safe(input.revision, /^[A-Za-z0-9 ._()+-]{1,128}$/)}`,
    `ui_state: ${input.uiState === 'connected' ? 'connected' : 'disconnected'}`,
  ]
  if (input.reasonCode) lines.push(`reason_code: ${safe(input.reasonCode, /^[A-Z0-9_]{3,128}$/)}`)
  if (input.correlationId) lines.push(`correlation_id: ${safe(input.correlationId, /^[A-Za-z0-9_-]{6,128}$/)}`)
  if (input.providerId) lines.push(`provider_id: ${safe(input.providerId, /^[A-Za-z0-9._-]{1,128}$/)}`)
  if (input.profileId) lines.push(`profile_id: ${safe(input.profileId, /^[A-Za-z0-9._-]{1,128}$/)}`)
  return `${lines.join('\n')}\n`
}

export async function copyDiagnosticReport(report: string): Promise<boolean> {
  if (!navigator.clipboard?.writeText) return false
  await navigator.clipboard.writeText(report)
  return true
}

export function downloadDiagnosticReport(report: string): void {
  const url = URL.createObjectURL(new Blob([report], { type: 'text/plain;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = 'threadcells-diagnostic.txt'
  link.click()
  URL.revokeObjectURL(url)
}
