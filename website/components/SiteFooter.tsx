import { ArrowUpRight, Book, Github } from '@/components/Icons'
import { Mark } from '@/components/Mark'
import { assetPath, site } from '@/lib/site'
import { docsPath, landingPath, localeCopy, type Locale } from '@/lib/locales'

export function SiteFooter({ locale = 'en' }: { locale?: Locale }) {
  const copy = localeCopy[locale]
  const home = assetPath(landingPath(locale, '#top'))
  return (
    <footer className="site-footer">
      <div className="footer-top">
        <a className="brand-link footer-brand" href={home} aria-label={copy.homeLabel}><Mark horizontal /></a>
        <p>{copy.footer}<small>{copy.creator}</small></p>
        <nav aria-label={copy.footerNav}>
          <a href={site.githubUrl} target="_blank" rel="noopener noreferrer"><Github /> GitHub <ArrowUpRight size={14} /></a>
          <a href={assetPath(docsPath(locale))}><Book /> {copy.docs} <ArrowUpRight size={14} /></a>
          <a href="#top">{copy.back} <ArrowUpRight size={14} /></a>
        </nav>
      </div>
      <div className="footer-bottom">
        <span>Apache-2.0</span>
        <span>ThreadCells © 2026 · <a href={site.creator.url} target="_blank" rel="noopener noreferrer">{site.creator.name}</a></span>
        <p>{copy.downstream}</p>
      </div>
    </footer>
  )
}
