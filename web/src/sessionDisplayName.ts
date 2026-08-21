/**
 * Convert the system-generated session namespace to its user-facing label.
 * Raw names remain authoritative for identity, API routes, selection, and actions.
 */
export function sessionDisplayName(rawName: string): string {
  return rawName.startsWith('cao-') ? rawName.slice(4) : rawName
}
