import { ArrowUpRight, Book, Github } from '@/components/Icons'
import { Mark } from '@/components/Mark'
import { assetPath, site } from '@/lib/site'
import { localeCopy, type Locale } from '@/lib/locales'

export function SiteFooter({ locale = 'en' }: { locale?: Locale }) {
  const copy = localeCopy[locale]
  const home = assetPath(`${locale === 'ru' ? '/ru' : ''}/#top`)
  return (
    <footer className="site-footer">
      <div className="footer-top">
        <a className="brand-link footer-brand" href={home} aria-label={locale === 'ru' ? 'Главная ThreadCells' : 'ThreadCells home'}><Mark horizontal /></a>
        <p>{copy.footer}<small>{locale === 'ru' ? <>Создано и поддерживается <a href={site.creator.url} target="_blank" rel="noopener noreferrer">{site.creator.name}</a> при участии сообщества ThreadCells.</> : <>Created and maintained by <a href={site.creator.url} target="_blank" rel="noopener noreferrer">{site.creator.name}</a>, with contributions from the ThreadCells community.</>}</small></p>
        <nav aria-label={copy.footerNav}>
          <a href={site.githubUrl} target="_blank" rel="noopener noreferrer"><Github /> GitHub <ArrowUpRight size={14} /></a>
          <a href={site.docsUrl}><Book /> {copy.docs} <ArrowUpRight size={14} /></a>
          <a href="#top">{copy.back} <ArrowUpRight size={14} /></a>
        </nav>
      </div>
      <div className="footer-bottom">
        <span>Apache-2.0</span>
        <span>ThreadCells © 2026 · <a href={site.creator.url} target="_blank" rel="noopener noreferrer">{site.creator.name}</a></span>
        <p>{locale === 'ru' ? 'AWS не спонсирует и не участвует в нём.' : 'Independent, unofficial downstream of AWS Labs CLI Agent Orchestrator. No AWS sponsorship or endorsement is implied.'}</p>
      </div>
    </footer>
  )
}
