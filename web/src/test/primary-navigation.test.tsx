import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { PrimaryNavigation } from '../components/PrimaryNavigation'

class ResizeObserverStub {
  observe() {}
  disconnect() {}
}

beforeEach(() => {
  vi.restoreAllMocks()
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 0 })
  window.matchMedia = vi.fn().mockReturnValue({ matches: false })
})

describe('PrimaryNavigation', () => {
  it('keeps route links, exposes the active route, and does not use tab semantics', () => {
    const navigate = vi.fn()
    render(<PrimaryNavigation tab="home" sessions={0} navigate={navigate} />)

    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
    expect(screen.queryByRole('tab')).not.toBeInTheDocument()
    expect(screen.getAllByRole('link').map(link => link.textContent)).toEqual([
      'Home', 'Agents', 'Flows', 'Statistics', 'Docs', 'Settings',
    ])
    expect(screen.getByRole('link', { name: 'Home' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: 'Docs' })).toHaveAttribute('href', '/docs')

    fireEvent.click(screen.getByRole('link', { name: 'Docs' }))
    expect(navigate).toHaveBeenCalledWith('docs')
  })

  it('keeps fixed arrow slots throughout overflow and disables unavailable directions', async () => {
    render(<PrimaryNavigation tab="home" sessions={0} navigate={() => {}} />)
    const rail = screen.getByTestId('primary-navigation-rail')
    let position = 0
    Object.defineProperties(rail, {
      clientWidth: { configurable: true, get: () => 240 },
      scrollWidth: { configurable: true, get: () => 600 },
      scrollLeft: { configurable: true, get: () => position },
    })
    const scrollBy = vi.fn(({ left }: ScrollToOptions) => { position += left || 0 })
    Object.defineProperty(rail, 'scrollBy', { configurable: true, value: scrollBy })

    fireEvent.scroll(rail)
    const next = await screen.findByRole('button', { name: 'Show next application sections' })
    const previous = screen.getByRole('button', { name: 'Show previous application sections' })
    expect(next).toHaveAttribute('aria-controls', 'primary-navigation-rail')
    expect(previous).toBeDisabled()
    expect(previous).toHaveAttribute('tabindex', '-1')
    expect(next).not.toBeDisabled()
    expect(next).toHaveAttribute('tabindex', '0')
    expect(previous.parentElement).toHaveClass('w-7')
    expect(next.parentElement).toHaveClass('w-7')
    next.focus()
    expect(next).toHaveFocus()
    fireEvent.click(next)
    expect(scrollBy).toHaveBeenCalledWith(expect.objectContaining({ behavior: 'smooth', left: 180 }))

    position = 360
    fireEvent.scroll(rail)
    await waitFor(() => expect(previous).not.toBeDisabled())
    expect(previous).toHaveAttribute('tabindex', '0')
    expect(next).toBeDisabled()
    expect(next).toHaveAttribute('tabindex', '-1')
  })

  it('uses instant motion when the user requests reduced motion', async () => {
    window.matchMedia = vi.fn().mockReturnValue({ matches: true })
    render(<PrimaryNavigation tab="home" sessions={0} navigate={() => {}} />)
    const rail = screen.getByTestId('primary-navigation-rail')
    Object.defineProperties(rail, {
      clientWidth: { configurable: true, get: () => 240 },
      scrollWidth: { configurable: true, get: () => 600 },
      scrollLeft: { configurable: true, get: () => 0 },
    })
    const scrollBy = vi.fn()
    Object.defineProperty(rail, 'scrollBy', { configurable: true, value: scrollBy })

    fireEvent.scroll(rail)
    fireEvent.click(await screen.findByRole('button', { name: 'Show next application sections' }))
    expect(scrollBy).toHaveBeenCalledWith(expect.objectContaining({ behavior: 'auto' }))
  })

  it('reveals the active route only inside the horizontal rail on viewport resize', () => {
    const documentScroll = vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
    render(<PrimaryNavigation tab="settings" sessions={0} navigate={() => {}} />)
    const rail = screen.getByTestId('primary-navigation-rail')
    const active = screen.getByRole('link', { name: 'Settings' })
    const scrollBy = vi.fn()
    Object.defineProperties(rail, {
      clientWidth: { configurable: true, get: () => 240 },
      scrollWidth: { configurable: true, get: () => 600 },
      scrollLeft: { configurable: true, get: () => 0 },
      scrollBy: { configurable: true, value: scrollBy },
      getBoundingClientRect: { configurable: true, value: () => ({ left: 40, right: 280 }) },
    })
    Object.defineProperty(active, 'getBoundingClientRect', { configurable: true, value: () => ({ left: 310, right: 410 }) })

    fireEvent(window, new Event('resize'))

    expect(scrollBy).toHaveBeenCalledWith({ behavior: 'smooth', left: 130 })
    expect(documentScroll).not.toHaveBeenCalled()
  })
})
