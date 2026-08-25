import { Book, Github } from '@/components/Icons'
import { Mark } from '@/components/Mark'
import { assetPath, site } from '@/lib/site'
import { docsPath, landingPath, localeCopy, locales, localizedPath, type Locale } from '@/lib/locales'

export function SiteHeader({ locale = 'en', routePath = '/' }: { locale?: Locale; routePath?: string }) {
  const copy = localeCopy[locale]
  const localeHref = (next: Locale) => assetPath(localizedPath(next, routePath))
  return (
    <header className="site-header">
      <div className="header-inner">
        <a href={assetPath(landingPath(locale, '#top'))} className="brand-link" aria-label={copy.homeLabel}>
          <span className="desktop-brand"><Mark locale={locale} /></span>
          <span className="mobile-brand"><Mark compact locale={locale} /></span>
        </a>
        <nav aria-label={copy.primaryNav}>
          <a href={assetPath(landingPath(locale, '#control-plane'))}>{copy.nav[0]}</a>
          <a href={assetPath(landingPath(locale, '#how-it-works'))}>{copy.nav[1]}</a>
          <a href={assetPath(landingPath(locale, '#open-source'))}>{copy.nav[2]}</a>
        </nav>
        <div className="header-actions">
          <details className="language-menu">
            <summary aria-label={copy.language}>{copy.code}</summary>
            <div role="menu" aria-label={copy.language}>
              {locales.map(next => <a key={next} role="menuitem" href={localeHref(next)} aria-current={next === locale ? 'page' : undefined}>{localeCopy[next].name}</a>)}
            </div>
          </details>
          <a className="header-icon-link header-docs-link" href={assetPath(docsPath(locale))} aria-label={copy.docs}><Book /> <span>{copy.docs}</span></a>
          <a className="header-icon-link header-github-link" href={site.githubUrl} target="_blank" rel="noopener noreferrer" aria-label={`ThreadCells — ${copy.github}`}><Github /> <span>{copy.github}</span></a>
          <span className="system-state"><i /> {copy.systemReady}</span>
        </div>
      </div>
    </header>
  )
}
