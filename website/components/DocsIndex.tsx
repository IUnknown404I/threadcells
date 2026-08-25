import { ArrowRight, Github } from '@/components/Icons'
import { DocsNavigation, type DocsNavigationItem } from '@/components/DocsNavigation'
import { docsByGroup, getDocs } from '@/lib/docs'
import { assetPath, site } from '@/lib/site'
import { docsPath, localeCopy, type Locale } from '@/lib/locales'

export function DocsIndex({ locale }: { locale: Locale }) {
  const copy = localeCopy[locale].docsUi
  const documents = getDocs(locale)
  const groups = docsByGroup(locale)
  const navigation: DocsNavigationItem[] = documents.map(({ slug, title, group }) => ({ slug, title, group }))
  return (
    <>
      <details className="docs-mobile-browser">
        <summary>{copy.browse}</summary>
        <DocsNavigation documents={navigation} locale={locale} idPrefix={`${locale}-mobile-index`} />
      </details>
      <div className="docs-layout docs-index-layout">
        <aside className="docs-sidebar"><DocsNavigation documents={navigation} locale={locale} idPrefix={`${locale}-desktop-index`} /></aside>
        <article className="docs-index">
          <p className="eyebrow">{copy.publicGuide} / {String(documents.length).padStart(2, '0')} {copy.articles}</p>
          <h1>{copy.indexTitle}<br /><span>{copy.indexAccent}</span></h1>
          <p className="docs-index-lede">{copy.indexDescription}</p>
          <div className="docs-start-path" aria-label={copy.startHere}>
            {documents.filter(document => ['overview', 'getting-started', 'installation', 'first-agent'].includes(document.slug)).map((document, index) => (
              <a key={document.slug} href={assetPath(docsPath(locale, document.slug))}><span>0{index + 1}</span><strong>{document.title}</strong><ArrowRight size={15} /></a>
            ))}
          </div>
          {groups.map(({ group, docs }) => (
            <section className="docs-group" key={group}>
              <div><h2>{group}</h2><span>{docs.length} {copy.article}{docs.length === 1 ? '' : locale === 'en' ? 's' : ''}</span></div>
              <div>{docs.map(document => <a key={document.slug} href={assetPath(docsPath(locale, document.slug))}><strong>{document.title}</strong><p>{document.description}</p><span>{copy.readArticle} <ArrowRight size={14} /></span></a>)}</div>
            </section>
          ))}
        </article>
        <aside className="docs-toc docs-index-aside">
          <strong>{copy.startHere}</strong>
          <p>{copy.quickSetupHint}</p>
          <a href={assetPath(docsPath(locale, 'getting-started'))}>{copy.openQuickSetup} <ArrowRight size={14} /></a>
          <a href={site.githubUrl} target="_blank" rel="noopener noreferrer"><Github size={14} /> {copy.repository}</a>
        </aside>
      </div>
    </>
  )
}
