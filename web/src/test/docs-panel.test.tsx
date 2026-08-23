import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { DocsPanel } from '../components/DocsPanel'

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
})
