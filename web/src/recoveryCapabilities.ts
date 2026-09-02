import { useEffect, useState } from 'react'
import { AgentSummary, RecoveryTakeoverCapability, api } from './api'

export function useRecoveryTakeoverCapabilities(agents: AgentSummary[], refreshKey = 0) {
  const terminalIds = Array.from(new Set(
    agents
      .filter(agent => agent.context_role === 'supervisor' && Boolean(agent.projectId))
      .map(agent => agent.id),
  )).sort()
  const terminalKey = terminalIds.join(',')
  const [capabilities, setCapabilities] = useState<Record<string, RecoveryTakeoverCapability>>({})

  useEffect(() => {
    if (!terminalIds.length) {
      setCapabilities({})
      return
    }
    let disposed = false
    let inFlight = false
    let controller: AbortController | null = null
    const refresh = async () => {
      if (disposed || inFlight || document.visibilityState !== 'visible') return
      inFlight = true
      controller = new AbortController()
      try {
        const result = await api.getRecoveryTakeoverCapabilities(terminalIds, controller.signal)
        if (!disposed) {
          setCapabilities(Object.fromEntries(result.capabilities.map(item => [item.terminal_id, item])))
        }
      } catch (reason) {
        if (!disposed && (reason as { name?: string })?.name !== 'AbortError') {
          // A missing/uncertain capability is deliberately fail-closed.
          setCapabilities({})
        }
      } finally {
        inFlight = false
      }
    }
    void refresh()
    const timer = window.setInterval(refresh, 5_000)
    return () => {
      disposed = true
      controller?.abort()
      window.clearInterval(timer)
    }
  // terminalKey is the stable identity of this bounded capability request.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [terminalKey, refreshKey])

  return capabilities
}
