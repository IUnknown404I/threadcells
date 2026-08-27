import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { DocsPanel } from '../components/DocsPanel'
import { I18nProvider, useI18n } from '../i18n'

const bundle = { product: 'ThreadCells', version: '0.1.0a1', commit: 'abcdef0123456789', documents: [
  { slug: 'getting-started', group: 'Getting started', order: 1, title: 'Quick setup', markdown: '# Quick setup\n\nUse `threadcells`.\n\n```bash\nthreadcells --help\n```', headings: [] },
  { slug: 'security-model', group: 'Safety', order: 2, title: 'Security model', markdown: '# Security model\n\n**No raw HTML.**\n\n| State | Meaning |\n| --- | --- |\n| Safe | Escaped |\n\n1. Inspect\n2. Verify\n\n<script>alert(1)</script>', headings: [] },
] }
bundle.documents[0].markdown += '\n\n![Live ThreadCells Home](/media/screenshots/threadcells-home.webp)'

describe('DocsPanel', () => {
  it('renders only a packaged document, searches it, and deep-links without raw HTML', async () => {
    history.replaceState({}, '', '/docs/getting-started')
    vi.stubGlobal('scrollTo', vi.fn())
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => bundle }))
    render(<DocsPanel />)
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Quick setup' })).toBeInTheDocument())
    expect(screen.getByText('threadcells --help')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Live ThreadCells Home' })).toHaveAttribute('src', '/media/screenshots/threadcells-home.webp')
    expect(document.querySelector('script')).toBeNull()
    fireEvent.change(screen.getByLabelText('Search documentation'), { target: { value: 'security' } })
    expect(screen.getByRole('button', { name: 'Security model' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Security model' }))
    expect(location.pathname).toBe('/docs/security-model')
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('list')).toBeInTheDocument()
    expect(screen.getByText('No raw HTML.').tagName).toBe('STRONG')
    expect(screen.getByRole('button', { name: /previous quick setup/i })).toBeInTheDocument()
  })

  it('renders en and ru from the same packaged slug and preserves its URL and anchor on locale switch', async () => {
    const localizedBundle = {
      schema: 2,
      product: 'ThreadCells',
      version: '0.3.0a3',
      commit: 'abcdef0123456789',
      locales: {
        en: [{ slug: 'getting-started', group: 'Getting started', order: 1, title: 'Quick setup', markdown: '# Quick setup\n\nEnglish body.', headings: [] }],
        ru: [{ slug: 'getting-started', group: 'Getting started', order: 1, title: 'Быстрый старт', markdown: '# Быстрый старт\n\nРусский текст.', headings: [] }],
      },
    }
    function Switcher() {
      const { setLocale } = useI18n()
      return <button type="button" onClick={() => setLocale('ru')}>Русский</button>
    }
    localStorage.clear()
    history.replaceState({}, '', '/docs/getting-started#install')
    vi.stubGlobal('scrollTo', vi.fn())
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => localizedBundle }))
    render(<I18nProvider><Switcher/><DocsPanel /></I18nProvider>)
    await screen.findByRole('heading', { name: 'Quick setup' })

    fireEvent.click(screen.getByRole('button', { name: 'Русский' }))
    expect(await screen.findByRole('heading', { name: 'Быстрый старт' })).toBeInTheDocument()
    expect(screen.getByText('Русский текст.')).toBeInTheDocument()
    expect(location.pathname).toBe('/docs/getting-started')
    expect(location.hash).toBe('#install')
  })
})
