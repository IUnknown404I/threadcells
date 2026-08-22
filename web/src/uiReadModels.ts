import { useCallback, useEffect, useRef, useState } from 'react'
import { AgentSummary, AgentSummaryPage, PageResult, SessionSummary, UiOverview, api } from './api'

type FetchPage<T, P extends PageResult<T>> = (
  page: { limit: number; offset: number }, signal: AbortSignal,
) => Promise<P>

function useProgressiveReadModel<T extends { id: string }, P extends PageResult<T>>(
  key: string, pageSize: number, maxItems: number, pollMs: number,
  fetchPage: FetchPage<T, P>, enabled = true,
) {
  const fetchPageRef = useRef(fetchPage)
  fetchPageRef.current = fetchPage
  const [items, setItems] = useState<T[]>([])
  const [total, setTotal] = useState(0)
  const [nextOffset, setNextOffset] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [latestPage, setLatestPage] = useState<P | null>(null)
  const generationRef = useRef(0)
  const inFlightRef = useRef(false)
  const nextOffsetRef = useRef<number | null>(null)
  const itemsRef = useRef<T[]>([])
  const controllerRef = useRef<AbortController | null>(null)
  useEffect(() => { nextOffsetRef.current = nextOffset }, [nextOffset])
  useEffect(() => { itemsRef.current = items }, [items])

  const request = useCallback(async (mode: 'initial' | 'append' | 'refresh') => {
    if (inFlightRef.current || !enabled) return
    const offset = mode === 'append' ? nextOffsetRef.current : 0
    if (mode === 'append' && offset === null) return
    const requestOffset = offset || 0
    const requestLimit = mode === 'refresh'
      ? Math.min(maxItems, Math.max(pageSize, itemsRef.current.length)) : pageSize
    const generation = generationRef.current
    const controller = new AbortController()
    controllerRef.current = controller
    inFlightRef.current = true
    if (mode !== 'refresh') setLoading(true)
    try {
      const page = await fetchPageRef.current(
        { limit: requestLimit, offset: requestOffset }, controller.signal,
      )
      if (generation !== generationRef.current) return
      setError(null)
      setLatestPage(page)
      setTotal(page.total)
      if (mode === 'append') {
        setItems(previous => {
          const known = new Set(previous.map(item => item.id))
          return [...previous, ...page.items.filter(item => !known.has(item.id))].slice(0, maxItems)
        })
        setNextOffset(requestOffset + page.items.length >= maxItems ? null : page.next_offset)
      } else {
        setItems(page.items.slice(0, maxItems))
        setNextOffset(page.items.length >= maxItems ? null : page.next_offset)
      }
    } catch (reason) {
      if ((reason as { name?: string })?.name !== 'AbortError') {
        setError(reason instanceof Error ? reason.message : 'Unable to load this page')
      }
    } finally {
      if (generation === generationRef.current) setLoading(false)
      if (controllerRef.current === controller) controllerRef.current = null
      if (generation === generationRef.current) inFlightRef.current = false
    }
  }, [enabled, maxItems, pageSize])

  useEffect(() => {
    generationRef.current += 1
    controllerRef.current?.abort()
    inFlightRef.current = false
    nextOffsetRef.current = null
    setItems([]); setTotal(0); setNextOffset(null); setLoading(enabled); setError(null)
    if (!enabled) return () => { generationRef.current += 1; controllerRef.current?.abort() }
    void request('initial')
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void request('refresh')
    }, pollMs)
    return () => { generationRef.current += 1; controllerRef.current?.abort(); window.clearInterval(timer) }
  }, [enabled, key, pollMs, request])

  return {
    items, total, nextOffset, loading, error, latestPage,
    loadMore: useCallback(() => { void request('append') }, [request]),
    reload: useCallback(() => { void request('initial') }, [request]),
    limitReached: items.length >= maxItems && total > items.length,
  }
}

export function useSessionSummaryFeed(query: string, enabled = true) {
  return useProgressiveReadModel<SessionSummary, PageResult<SessionSummary>>(
    query.trim(), 10, 100, 10_000,
    (page, signal) => api.listSessionSummaries({ ...page, query: query.trim() }, signal), enabled,
  )
}

export function useUiOverview(enabled = true) {
  const [overview, setOverview] = useState<UiOverview | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!enabled) return
    let controller: AbortController | null = null
    let inFlight = false
    let disposed = false
    const refresh = async () => {
      if (inFlight || document.visibilityState !== 'visible') return
      inFlight = true; controller = new AbortController()
      try {
        const value = await api.getUiOverview(controller.signal)
        if (!disposed) { setOverview(value); setError(null) }
      } catch (reason) {
        if (!disposed && (reason as { name?: string })?.name !== 'AbortError') {
          setError(reason instanceof Error ? reason.message : 'Unable to load overview')
        }
      } finally { inFlight = false }
    }
    void refresh()
    const timer = window.setInterval(refresh, 5_000)
    return () => { disposed = true; controller?.abort(); window.clearInterval(timer) }
  }, [enabled])
  return { overview, error }
}

export interface AgentSummaryQuery {
  sessionId?: string; query?: string; activities?: string[]; workflowStates?: string[]
  profiles?: string[]; homeFilter?: string | null; refreshKey?: number
}

export function useAgentSummaryFeed(query: AgentSummaryQuery, enabled = true) {
  const key = enabled ? JSON.stringify(query) : 'disabled'
  return useProgressiveReadModel<AgentSummary, AgentSummaryPage>(
    key, 40, 100, 5_000,
    (page, signal) => api.listAgentSummaries({ ...page, ...query }, signal), enabled,
  )
}

export function useNearViewport(loadMore: () => void, enabled: boolean) {
  const callbackRef = useRef(loadMore)
  callbackRef.current = loadMore
  const sentinelRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const sentinel = sentinelRef.current
    if (!sentinel || !enabled || typeof IntersectionObserver === 'undefined') return
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) callbackRef.current()
    }, { rootMargin: '400px 0px' })
    observer.observe(sentinel)
    return () => observer.disconnect()
  }, [enabled])
  return sentinelRef
}
