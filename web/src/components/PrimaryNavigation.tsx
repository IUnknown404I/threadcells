import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { BarChart3, BookOpen, Bot, ChevronLeft, ChevronRight, Clock, Home, Settings } from 'lucide-react'
import { useI18n, type TranslationKey } from '../i18n'

export type TabKey = 'home' | 'agents' | 'flows' | 'statistics' | 'settings' | 'docs'

export const NAVIGATION_ITEMS: { key: TabKey; labelKey: TranslationKey; icon: ReactNode }[] = [
  { key: 'home', labelKey: 'nav.home', icon: <Home size={16} /> },
  { key: 'agents', labelKey: 'nav.agents', icon: <Bot size={16} /> },
  { key: 'flows', labelKey: 'nav.flows', icon: <Clock size={16} /> },
  { key: 'statistics', labelKey: 'nav.statistics', icon: <BarChart3 size={16} /> },
  { key: 'docs', labelKey: 'nav.docs', icon: <BookOpen size={16} /> },
  { key: 'settings', labelKey: 'nav.settings', icon: <Settings size={16} /> },
]

function motionBehavior(): ScrollBehavior {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
}

export function PrimaryNavigation({ tab, sessions, navigate }: { tab: TabKey; sessions: number; navigate: (tab: TabKey) => void }) {
  const { t } = useI18n()
  const scrollRef = useRef<HTMLDivElement>(null)
  const [scrollState, setScrollState] = useState({ overflow: false, previous: false, next: false })

  const updateScrollState = useCallback(() => {
    const rail = scrollRef.current
    if (!rail) return
    const maximum = Math.max(0, rail.scrollWidth - rail.clientWidth)
    const next = { overflow: maximum > 1, previous: rail.scrollLeft > 1, next: rail.scrollLeft < maximum - 1 }
    setScrollState(current => current.overflow === next.overflow && current.previous === next.previous && current.next === next.next ? current : next)
  }, [])

  const revealActive = useCallback(() => {
    const rail = scrollRef.current
    const active = rail?.querySelector<HTMLElement>('[aria-current="page"]')
    if (rail && active) {
      const railRect = rail.getBoundingClientRect()
      const activeRect = active.getBoundingClientRect()
      const leftDelta = activeRect.left < railRect.left
        ? activeRect.left - railRect.left
        : activeRect.right > railRect.right
          ? activeRect.right - railRect.right
          : 0
      if (Math.abs(leftDelta) > 1) rail.scrollBy({ left: leftDelta, behavior: motionBehavior() })
    }
    requestAnimationFrame(updateScrollState)
  }, [updateScrollState])

  useLayoutEffect(() => {
    const rail = scrollRef.current
    if (!rail) return
    const onWindowResize = () => revealActive()
    const observer = new ResizeObserver(updateScrollState)
    observer.observe(rail)
    rail.addEventListener('scroll', updateScrollState, { passive: true })
    window.addEventListener('resize', onWindowResize)
    revealActive()
    return () => {
      observer.disconnect()
      rail.removeEventListener('scroll', updateScrollState)
      window.removeEventListener('resize', onWindowResize)
    }
  }, [revealActive, updateScrollState])

  useEffect(() => { revealActive() }, [tab, revealActive])

  // Arrow controls consume rail width only after they render. Re-measure on
  // that state transition so the fit/overflow decision reaches a stable value.
  useLayoutEffect(() => {
    requestAnimationFrame(updateScrollState)
  }, [scrollState.overflow, updateScrollState])

  const hrefFor = (key: TabKey) => {
    if (key === 'docs') return '/docs'
    if (key === 'settings') return '/settings'
    const url = new URL(window.location.href)
    url.pathname = '/'
    if (key === 'home') url.searchParams.delete('tab')
    else url.searchParams.set('tab', key)
    return `${url.pathname}${url.search}`
  }

  const advance = (direction: -1 | 1) => {
    const rail = scrollRef.current
    rail?.scrollBy({ left: direction * Math.max(rail.clientWidth * 0.75, 160), behavior: motionBehavior() })
  }

  return (
    <nav className="flex min-w-0 items-center gap-1 py-2" aria-label={t('nav.sections')}>
      {scrollState.overflow && <span className="flex h-7 w-7 shrink-0"><button type="button" onClick={() => advance(-1)} disabled={!scrollState.previous} tabIndex={scrollState.previous ? 0 : -1} className={`flex h-7 w-7 items-center justify-center rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${scrollState.previous ? 'text-gray-400 hover:bg-gray-800/70 hover:text-white' : 'pointer-events-none opacity-0'}`} aria-controls="primary-navigation-rail" aria-label={t('nav.previous')}><ChevronLeft size={14} aria-hidden="true" /></button></span>}
      <div id="primary-navigation-rail" ref={scrollRef} className="primary-navigation-rail min-w-0 flex-1 overflow-x-auto overscroll-x-contain scroll-smooth" data-testid="primary-navigation-rail">
        <div className="grid w-full min-w-[920px] grid-cols-[repeat(6,minmax(150px,1fr))] gap-1">
          {NAVIGATION_ITEMS.map((item, index) => <a key={item.key} href={hrefFor(item.key)} aria-current={tab === item.key ? 'page' : undefined} onClick={event => {
            if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
            event.preventDefault()
            navigate(item.key)
          }} className={`flex min-h-11 min-w-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg border px-2 py-2 text-sm font-semibold transition-[color,background-color,border-color,box-shadow] duration-200 sm:gap-2 ${tab === item.key ? 'border-emerald-400/50 bg-gradient-to-r from-emerald-600 to-emerald-500 text-white shadow-lg shadow-emerald-500/25' : 'border-gray-700/70 bg-gray-900/55 text-gray-300 shadow-sm hover:border-gray-600 hover:bg-gray-800 hover:text-white'}`} title={`Alt+${index + 1}`}>
            {item.icon}{t(item.labelKey)}{item.key === 'agents' && sessions > 0 && <span className={`rounded-full px-1.5 py-0.5 text-xs ${tab === item.key ? 'bg-white/20' : 'bg-gray-700'}`}>{sessions}</span>}
          </a>)}
        </div>
      </div>
      {scrollState.overflow && <span className="flex h-7 w-7 shrink-0"><button type="button" onClick={() => advance(1)} disabled={!scrollState.next} tabIndex={scrollState.next ? 0 : -1} className={`flex h-7 w-7 items-center justify-center rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 ${scrollState.next ? 'text-gray-400 hover:bg-gray-800/70 hover:text-white' : 'pointer-events-none opacity-0'}`} aria-controls="primary-navigation-rail" aria-label={t('nav.next')}><ChevronRight size={14} aria-hidden="true" /></button></span>}
    </nav>
  )
}
