import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api'
import { useUiOverview } from '../uiReadModels'

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('operational polling', () => {
  it('does not poll the overview while the browser tab is hidden', async () => {
    vi.useFakeTimers()
    let visibility: DocumentVisibilityState = 'visible'
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => visibility,
    })
    const getOverview = vi.spyOn(api, 'getUiOverview').mockResolvedValue({
      sessions: 1,
      agents: 1,
      active: 1,
      waiting: 0,
      owner_gate: 0,
      cancelled: 0,
      completed: 0,
    })

    const view = renderHook(() => useUiOverview())
    await act(async () => { await Promise.resolve() })
    expect(getOverview).toHaveBeenCalledTimes(1)

    visibility = 'hidden'
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000) })
    expect(getOverview).toHaveBeenCalledTimes(1)

    visibility = 'visible'
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000) })
    expect(getOverview).toHaveBeenCalledTimes(2)
    view.unmount()
  })
})
