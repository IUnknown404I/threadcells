import { create } from 'zustand'
import { api, OwnerLaunchGrant, Session, SessionDetail, TerminalMeta } from './api'
import { readStoredAppLocale, translate } from './i18n'

const appText = (key: Parameters<typeof translate>[1], params?: Parameters<typeof translate>[2]) => translate(readStoredAppLocale(), key, params)

// Only trigger React re-renders when data actually changed
function jsonEqual(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

interface Snackbar {
  type: 'success' | 'error' | 'info'
  message: string
}

interface Store {
  sessions: Session[]
  activeSession: string | null
  activeSessionDetail: SessionDetail | null
  connected: boolean
  snackbar: Snackbar | null
  terminalStatuses: Record<string, string>

  fetchSessions: () => Promise<void>
  selectSession: (name: string | null) => Promise<void>
  createSession: (provider: string, agentProfile: string, sessionName?: string, workingDirectory?: string, projectId?: string, ownerGrant?: OwnerLaunchGrant, workContextRequestId?: string) => Promise<void>
  deleteSession: (name: string) => Promise<void>
  showSnackbar: (snackbar: Snackbar) => void
  hideSnackbar: () => void
  setConnected: (connected: boolean) => void
  setTerminalStatus: (id: string, status: string) => void
  setTerminalStatuses: (statuses: Record<string, string>) => void
  clearTerminalStatuses: (ids: string[]) => void
}

export const useStore = create<Store>((set, get) => ({
  sessions: [],
  activeSession: null,
  activeSessionDetail: null,
  connected: false,
  snackbar: null,
  terminalStatuses: {},

  fetchSessions: async () => {
    try {
      const sessions = await api.listSessions()
      const prev = get()
      if (!prev.connected || !jsonEqual(prev.sessions, sessions)) {
        set({ sessions, connected: true })
      }
    } catch {
      if (get().connected) set({ connected: false })
    }
  },

  selectSession: async (name) => {
    if (!name) {
      set({ activeSession: null, activeSessionDetail: null })
      return
    }
    set({ activeSession: name })
    try {
      const detail = await api.getSession(name)
      if (!jsonEqual(get().activeSessionDetail, detail)) {
        set({ activeSessionDetail: detail })
      }
    } catch {
      set({ activeSessionDetail: null })
    }
  },

  createSession: async (provider, agentProfile, sessionName, workingDirectory, projectId, ownerGrant, workContextRequestId) => {
    try {
      if (ownerGrant) {
        await api.createSession(provider, agentProfile, sessionName, workingDirectory, projectId, ownerGrant, workContextRequestId)
      } else if (projectId) {
        await api.createSession(provider, agentProfile, sessionName, workingDirectory, projectId, undefined, workContextRequestId)
      } else {
        await api.createSession(provider, agentProfile, sessionName, workingDirectory)
      }
      get().showSnackbar({ type: 'success', message: appText('store.sessionCreated') })
      await get().fetchSessions()
    } catch (e: any) {
      get().showSnackbar({ type: 'error', message: e.message || appText('store.createFailed') })
      throw e
    }
  },

  deleteSession: async (name) => {
    try {
      await api.deleteSession(name)
      get().showSnackbar({ type: 'success', message: appText('store.sessionDeleted', { name }) })
      if (get().activeSession === name) {
        set({ activeSession: null, activeSessionDetail: null })
      }
      await get().fetchSessions()
    } catch (e: any) {
      get().showSnackbar({ type: 'error', message: e.message || appText('store.deleteFailed') })
    }
  },

  showSnackbar: (snackbar) => set({ snackbar }),
  hideSnackbar: () => set({ snackbar: null }),
  setConnected: (connected) => set({ connected }),
  setTerminalStatus: (id, status) =>
    set(state => {
      if (state.terminalStatuses[id] === status) return state
      return { terminalStatuses: { ...state.terminalStatuses, [id]: status } }
    }),
  setTerminalStatuses: (statuses) => set(state => {
    const next = { ...state.terminalStatuses, ...statuses }
    return jsonEqual(next, state.terminalStatuses) ? state : { terminalStatuses: next }
  }),
  clearTerminalStatuses: (ids) =>
    set(state => {
      const next: Record<string, string> = {}
      for (const id of ids) {
        if (state.terminalStatuses[id]) next[id] = state.terminalStatuses[id]
      }
      if (Object.keys(next).length === Object.keys(state.terminalStatuses).length) return state
      return { terminalStatuses: next }
    }),
}))
