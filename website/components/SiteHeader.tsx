'use client'

import { Book, Github } from '@/components/Icons'
import { usePathname } from 'next/navigation'
import { Mark } from '@/components/Mark'
import { assetPath, basePath, site } from '@/lib/site'
import { docsPath, landingPath, localeCopy, locales, localizedPath, type Locale } from '@/lib/locales'
import { LanguageSelector } from '../../web/src/components/LanguageSelector'

export function SiteHeader({ locale = 'en', routePath = '/' }: { locale?: Locale; routePath?: string }) {
  const copy = localeCopy[locale]
  const pathname = usePathname() || routePath
  const withoutBase = basePath && pathname.startsWith(basePath) ? pathname.slice(basePath.length) || '/' : pathname
  const currentPrefix = locale === 'en' ? '' : `/${locale}`
  const semanticPath = currentPrefix && (withoutBase === currentPrefix || withoutBase.startsWith(`${currentPrefix}/`))
    ? withoutBase.slice(currentPrefix.length) || '/'
    : withoutBase
  const localeHref = (next: Locale) => assetPath(localizedPath(next, semanticPath))
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
          <LanguageSelector locale={locale} label={copy.language} hrefFor={localeHref} options={locales.map(code => ({ code, short: localeCopy[code].short, name: localeCopy[code].name, htmlLang: localeCopy[code].htmlLang }))} />
          <a className="header-icon-link header-docs-link" href={assetPath(docsPath(locale))} aria-label={copy.docs}><Book /> <span>{copy.docs}</span></a>
          <a className="header-icon-link header-github-link" href={site.githubUrl} target="_blank" rel="noopener noreferrer" aria-label={`ThreadCells — ${copy.github}`}><Github /> <span>{copy.github}</span></a>
          <span className="system-state"><i /> {copy.systemReady}</span>
        </div>
      </div>
    </header>
  )
}
