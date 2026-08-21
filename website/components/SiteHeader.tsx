import { Book, Github } from '@/components/Icons'
import { Mark } from '@/components/Mark'
import { assetPath, site } from '@/lib/site'

export function SiteHeader() {
  return (
    <header className="site-header">
      <div className="header-inner">
        <a href={assetPath('/#top')} className="brand-link" aria-label="ThreadCells home">
          <span className="desktop-brand"><Mark /></span>
          <span className="mobile-brand"><Mark compact /></span>
        </a>
        <nav aria-label="Primary navigation">
          <a href={assetPath('/#control-plane')}>Product</a>
          <a href={assetPath('/#how-it-works')}>How it works</a>
          <a href={assetPath('/#open-source')}>Open source</a>
        </nav>
        <div className="header-actions">
          <a className="header-icon-link header-docs-link" href={site.docsUrl}><Book /> <span>Docs</span></a>
          <a className="header-icon-link header-github-link" href={site.githubUrl} target="_blank" rel="noopener noreferrer" aria-label="ThreadCells on GitHub"><Github /> <span>GitHub</span></a>
          <span className="system-state"><i /> SYSTEM READY</span>
        </div>
      </div>
    </header>
  )
}
