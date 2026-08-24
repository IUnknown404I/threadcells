import { Book, Github } from '@/components/Icons'
import { Mark } from '@/components/Mark'
import { assetPath, site } from '@/lib/site'
import { localeCopy, type Locale } from '@/lib/locales'

export function SiteHeader({ locale = 'en' }: { locale?: Locale }) {
  const copy = localeCopy[locale]
  const localeHref = (next: Locale) => assetPath(`${next === 'ru' ? '/ru' : ''}/`)
  return (
    <header className="site-header">
      <div className="header-inner">
        <a href={assetPath(`${locale === 'ru' ? '/ru' : ''}/#top`)} className="brand-link" aria-label="ThreadCells home">
          <span className="desktop-brand"><Mark /></span>
          <span className="mobile-brand"><Mark compact /></span>
        </a>
        <nav aria-label="Primary navigation">
          <a href={assetPath(`${locale === 'ru' ? '/ru' : ''}/#control-plane`)}>{copy.nav[0]}</a>
          <a href={assetPath(`${locale === 'ru' ? '/ru' : ''}/#how-it-works`)}>{copy.nav[1]}</a>
          <a href={assetPath(`${locale === 'ru' ? '/ru' : ''}/#open-source`)}>{copy.nav[2]}</a>
        </nav>
        <div className="header-actions">
          <details className="language-menu">
            <summary aria-label={copy.language}>{copy.code}</summary>
            <div role="menu" aria-label={copy.language}>
              {(['en', 'ru'] as Locale[]).map(next => <a key={next} role="menuitem" href={localeHref(next)} aria-current={next === locale ? 'page' : undefined}>{localeCopy[next].name}</a>)}
            </div>
          </details>
          <a className="header-icon-link header-docs-link" href={site.docsUrl} aria-label={copy.docs}><Book /> <span>{copy.docs}</span></a>
          <a className="header-icon-link header-github-link" href={site.githubUrl} target="_blank" rel="noopener noreferrer" aria-label="ThreadCells on GitHub"><Github /> <span>{copy.github}</span></a>
          <span className="system-state"><i /> SYSTEM READY</span>
        </div>
      </div>
    </header>
  )
}
