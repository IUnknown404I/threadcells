import { ArrowUpRight, Book, Github } from '@/components/Icons'
import { Mark } from '@/components/Mark'
import { assetPath, site } from '@/lib/site'

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="footer-top">
        <a className="brand-link" href={assetPath('/#top')} aria-label="ThreadCells home"><Mark /></a>
        <p>Operational control for native CLI coding agents—on the machine you own.<small>Created and maintained by <a href={site.creator.url} target="_blank" rel="noopener noreferrer">{site.creator.name}</a>, with contributions from the ThreadCells community.</small></p>
        <nav aria-label="Footer navigation">
          <a href={site.githubUrl} target="_blank" rel="noopener noreferrer"><Github /> GitHub <ArrowUpRight size={14} /></a>
          <a href={site.docsUrl}><Book /> Docs <ArrowUpRight size={14} /></a>
          <a href="#top">Back to top <ArrowUpRight size={14} /></a>
        </nav>
      </div>
      <div className="footer-bottom">
        <span>Apache-2.0</span>
        <span>ThreadCells © 2026 · <a href={site.creator.url} target="_blank" rel="noopener noreferrer">{site.creator.name}</a></span>
        <p>Independent, unofficial downstream of AWS Labs CLI Agent Orchestrator. No AWS sponsorship or endorsement is implied.</p>
      </div>
    </footer>
  )
}
