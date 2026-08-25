import { ArrowRight, Github } from '@/components/Icons'
import { DocsArticle } from '@/components/DocsArticle'
import { DocsNavigation, type DocsNavigationItem } from '@/components/DocsNavigation'
import type { PublicDoc } from '@/lib/docs'
import { assetPath, site } from '@/lib/site'
import { docsPath, localeCopy, locales } from '@/lib/locales'

export function DocsShell({ document, documents }: { document: PublicDoc; documents: PublicDoc[] }) {
  const copy = localeCopy[document.locale].docsUi
  const index = documents.findIndex(item => item.slug === document.slug)
  const previous = index > 0 ? documents[index - 1] : null
  const next = index < documents.length - 1 ? documents[index + 1] : null
  const navigation: DocsNavigationItem[] = documents.map(({ slug, title, group }) => ({ slug, title, group }))

  return (
    <>
      <details className="docs-mobile-browser">
        <summary>{copy.browse}</summary>
        <DocsNavigation documents={navigation} locale={document.locale} activeSlug={document.slug} idPrefix="mobile" />
      </details>
      <div className="docs-layout">
        <aside className="docs-sidebar"><DocsNavigation documents={navigation} locale={document.locale} activeSlug={document.slug} idPrefix="desktop" /></aside>
        <article className="docs-article">
          <div className="docs-breadcrumb"><a href={assetPath(docsPath(document.locale))}>{localeCopy[document.locale].docs}</a><span>/</span><span>{document.group}</span></div>
          <div className="docs-language-switch" aria-label={localeCopy[document.locale].language}>
            {locales.map(locale => <a key={locale} href={assetPath(docsPath(locale, document.slug))} hrefLang={localeCopy[locale].htmlLang} aria-current={locale === document.locale ? 'page' : undefined}>{localeCopy[locale].short}</a>)}
          </div>
          <DocsArticle markdown={document.markdown} locale={document.locale} />
          <nav className="docs-pagination" aria-label={copy.paginationLabel}>
            {previous ? <a href={assetPath(docsPath(document.locale, previous.slug))}><span>{copy.previous}</span><strong>{previous.title}</strong></a> : <span />}
            {next ? <a href={assetPath(docsPath(document.locale, next.slug))}><span>{copy.next}</span><strong>{next.title}</strong><ArrowRight size={15} /></a> : <span />}
          </nav>
          <div className="docs-community-note">{localeCopy[document.locale].creator} <a href={site.githubUrl} target="_blank" rel="noopener noreferrer"><Github size={14} /> {copy.viewRepository}</a></div>
        </article>
        <aside className="docs-toc" aria-label={copy.onThisPage}>
          <strong>{copy.onThisPage}</strong>
          <ol>{document.headings.map(heading => <li key={heading.anchor} className={`toc-level-${heading.level}`}><a href={`#${heading.anchor}`}>{heading.text}</a></li>)}</ol>
        </aside>
      </div>
    </>
  )
}
