import { lazy, useEffect, useState, Suspense } from 'react'
import { useStore } from './store'
import { api, RuntimeBranding } from './api'
import { ErrorBoundary } from './components/ErrorBoundary'
import { DashboardHome, HomeNavigation } from './components/DashboardHome'
import type { SettingsSection } from './components/ControlPlaneSettings'
import { BookOpen, CheckCircle, ExternalLink, Github, Info, Wifi, WifiOff, XCircle } from 'lucide-react'
import { applyAgentFilterState, homeAgentFilterState } from './agentFilters'
import { NAVIGATION_ITEMS, PrimaryNavigation, type TabKey } from './components/PrimaryNavigation'
import { useUiOverview } from './uiReadModels'

const AgentPanel = lazy(() => import('./components/AgentPanel').then(module => ({ default: module.AgentPanel })))
const FlowsPanel = lazy(() => import('./components/FlowsPanel').then(module => ({ default: module.FlowsPanel })))
const ControlPlaneSettings = lazy(() => import('./components/ControlPlaneSettings').then(module => ({ default: module.ControlPlaneSettings })))
const UsageStatistics = lazy(() => import('./components/UsageStatistics').then(module => ({ default: module.UsageStatistics })))
const DocsPanel = lazy(() => import('./components/DocsPanel').then(module => ({ default: module.DocsPanel })))

const PRODUCT_LINKS = {
  github: 'https://github.com/IUnknown404I/threadcells',
  landing: 'https://iunknown404i.github.io/threadcells/',
}

function Snackbar() {
  const { snackbar, hideSnackbar } = useStore()

  useEffect(() => {
    if (snackbar) {
      const timer = setTimeout(hideSnackbar, 3000)
      return () => clearTimeout(timer)
    }
  }, [snackbar, hideSnackbar])

  if (!snackbar) return null

  const colors = {
    success: 'bg-emerald-600 border-emerald-500',
    error: 'bg-red-600 border-red-500',
    info: 'bg-blue-600 border-blue-500',
  }
  const icons = {
    success: <CheckCircle size={18} />,
    error: <XCircle size={18} />,
    info: <Info size={18} />,
  }

  return (
    <div role="alert" className={`fixed bottom-3 left-3 right-3 sm:left-auto sm:right-4 sm:max-w-md z-50 px-4 py-3 rounded-lg border shadow-lg flex items-start gap-2 text-white ${colors[snackbar.type]}`}>
      {icons[snackbar.type]}
      <span className="text-sm">{snackbar.message}</span>
    </div>
  )
}

export default function App() {
  const readTab = (): TabKey => {
    if (window.location.pathname === '/docs' || window.location.pathname.startsWith('/docs/')) return 'docs'
    if (window.location.pathname === '/settings' || window.location.pathname.startsWith('/settings/')) return 'settings'
    const value = new URLSearchParams(window.location.search).get('tab')
    return NAVIGATION_ITEMS.some(tab => tab.key === value) ? value as TabKey : 'home'
  }
  const [tab, setTab] = useState<TabKey>(readTab)
  const [navigationSearch, setNavigationSearch] = useState(window.location.search)
  const readSettingsSection = (): SettingsSection => {
    const value = window.location.pathname.split('/')[2]
    return ['profiles', 'providers', 'housekeeping', 'telegram', 'about'].includes(value) ? value as SettingsSection : 'general'
  }
  const [settingsSection, setSettingsSection] = useState<SettingsSection>(readSettingsSection)
  const [agentIntent, setAgentIntent] = useState<'create-session' | null>(null)
  const { connected, setConnected } = useStore()
  const overviewState = useUiOverview()
  const sessionCount = overviewState.overview?.sessions || 0
  const [branding, setBranding] = useState<RuntimeBranding>({ title: 'ThreadCells', subtitle: 'Multi-agent control plane', logoUrl: '/threadcells-symbol.png', customLogo: false })

  useEffect(() => {
    if (overviewState.overview) setConnected(!overviewState.error)
  }, [overviewState.overview, overviewState.error, setConnected])

  useEffect(() => {
    const onPopState = () => {
      setTab(readTab())
      setNavigationSearch(window.location.search)
      setSettingsSection(readSettingsSection())
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  const navigate = (nextTab: TabKey, search = window.location.search) => {
    const url = new URL(window.location.href)
    if (nextTab === 'docs') {
      url.pathname = '/docs'
      url.search = ''
      window.history.pushState({}, '', url)
      setTab(nextTab); setNavigationSearch('')
      return
    }
    if (nextTab === 'settings') {
      url.pathname = '/settings'
      url.search = ''
      window.history.pushState({}, '', url)
      setTab(nextTab); setSettingsSection('general'); setNavigationSearch('')
      return
    }
    url.pathname = '/'
    const params = new URLSearchParams(search)
    if (nextTab === 'home') params.delete('tab')
    else params.set('tab', nextTab)
    url.search = params.toString()
    window.history.pushState({}, '', url)
    setTab(nextTab)
    setNavigationSearch(url.search)
  }

  const navigateSettings = (section: SettingsSection) => {
    const url = new URL(window.location.href)
    url.pathname = section === 'general' ? '/settings' : `/settings/${section}`
    url.search = ''
    window.history.pushState({}, '', url)
    setTab('settings')
    setSettingsSection(section)
    setNavigationSearch('')
  }

  const navigateHome = (destination: HomeNavigation) => {
    if (typeof destination === 'string') {
      navigate(destination as TabKey)
      return
    }
    if (destination.intent === 'create-session') {
      setAgentIntent('create-session')
      navigate('agents')
      return
    }
    const params = applyAgentFilterState(new URLSearchParams(window.location.search), homeAgentFilterState(destination.filter!))
    navigate('agents', params.toString())
  }

  useEffect(() => {
    let active = true
    const refresh = () => api.getBranding().then(value => { if (active) setBranding(value) }).catch(() => {})
    refresh()
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') refresh()
    }, 5000)
    return () => { active = false; window.clearInterval(timer) }
  }, [])

  useEffect(() => {
    document.title = branding.title
    let icon = document.querySelector<HTMLLinkElement>('link[rel="icon"]')
    if (!icon) { icon = document.createElement('link'); icon.rel = 'icon'; document.head.appendChild(icon) }
    icon.href = branding.logoUrl
  }, [branding])

  // Keyboard shortcuts: Alt+1-4
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.altKey && e.key >= '1' && e.key <= String(NAVIGATION_ITEMS.length)) {
        e.preventDefault()
        navigate(NAVIGATION_ITEMS[parseInt(e.key) - 1].key)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [navigate])

  return (
    <div className="flex min-h-[100dvh] flex-col bg-[#0f0f14] text-gray-200">
      {/* Header */}
      <header className="border-b border-gray-800 bg-gray-900/80 backdrop-blur-sm sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 py-0 flex items-center justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <img src={branding.logoUrl} alt={`${branding.title} logo`} className="h-16 w-16 rounded-lg object-cover" />
            <div className="min-w-0">
              <h1 className="text-base sm:text-lg font-bold text-white truncate" title={branding.title}>{branding.title}</h1>
              <p className="hidden text-[10px] uppercase tracking-[0.18em] text-emerald-400 sm:block">{branding.subtitle}</p>
            </div>
          </div>
          <div className="flex items-center gap-3 shrink-0">
            <span className="hidden sm:inline text-xs text-gray-500">{sessionCount} session{sessionCount !== 1 ? 's' : ''}</span>
            <div className="flex items-center gap-1.5" title={connected ? 'Connected' : 'Disconnected'}>
              {connected ? (
                <Wifi size={14} className="text-emerald-400" />
              ) : (
                <WifiOff size={14} className="text-red-400" />
              )}
              <span className={`text-xs ${connected ? 'text-emerald-400' : 'text-red-400'}`}>
                {connected ? 'Live' : 'Offline'}
              </span>
            </div>
          </div>
        </div>
      </header>

      {/* Tab Bar */}
      <div className="border-b border-gray-800">
        <div className="max-w-7xl mx-auto px-3 sm:px-6">
          <PrimaryNavigation tab={tab} sessions={sessionCount} navigate={navigate} />
        </div>
      </div>

      {/* Content */}
      <main className="mx-auto w-full max-w-7xl min-w-0 flex-1 px-4 py-4 sm:px-6 sm:py-6">
        <ErrorBoundary>
          <Suspense fallback={<div className="text-gray-500 text-sm py-12 text-center">Loading...</div>}>
            {tab === 'home' && <DashboardHome onNavigate={navigateHome} overviewState={overviewState} />}
            {tab === 'agents' && <AgentPanel navigationSearch={navigationSearch} navigationIntent={agentIntent} onNavigationIntentConsumed={() => setAgentIntent(null)} />}
            {tab === 'flows' && <FlowsPanel />}
            {tab === 'statistics' && <UsageStatistics />}
            {tab === 'settings' && <ControlPlaneSettings section={settingsSection} navigate={navigateSettings} />}
            {tab === 'docs' && <DocsPanel />}
          </Suspense>
        </ErrorBoundary>
      </main>

      <footer className="mt-6 border-t border-gray-800 bg-[#0c1421]">
        <div className="mx-auto flex w-full max-w-7xl flex-col items-center justify-between gap-3 px-4 py-[2px] text-center text-xs sm:px-6 md:flex-row md:text-left">
          <div className="flex min-w-0 flex-col items-center gap-2 sm:flex-row sm:items-center sm:gap-4">
            <img src="/threadcells-logo-horizontal.png" alt="ThreadCells" className="h-24 w-auto max-w-[20rem] shrink-0 rounded-md object-contain" />
            <div className="min-w-0">
              <p className="text-sm text-gray-200">Contributions are welcome.</p>
              <p className="mt-1 text-blue-200/60">© 2026 ThreadCells</p>
            </div>
          </div>
          <nav aria-label="Product links" className="flex flex-wrap items-center justify-center gap-x-3 gap-y-1 text-blue-100/70">
            <a href={PRODUCT_LINKS.github} target="_blank" rel="noreferrer" className="inline-flex min-h-9 items-center gap-1.5 hover:text-emerald-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"><Github size={14} aria-hidden="true" /> GitHub</a>
            <a href="/docs" className="inline-flex min-h-9 items-center gap-1.5 hover:text-emerald-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"><BookOpen size={14} aria-hidden="true" /> Docs</a>
            <span aria-hidden="true" className="inline-flex h-9 items-center text-blue-200/40">·</span>
            <a href={PRODUCT_LINKS.landing} target="_blank" rel="noreferrer" className="inline-flex min-h-9 items-center gap-1 hover:text-emerald-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400">ThreadCells <ExternalLink size={13} aria-hidden="true" /></a>
          </nav>
        </div>
      </footer>

      <Snackbar />
    </div>
  )
}
