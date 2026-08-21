import { ArrowRight, Github } from '@/components/Icons'
import { DocsArticle } from '@/components/DocsArticle'
import { DocsNavigation, type DocsNavigationItem } from '@/components/DocsNavigation'
import type { PublicDoc } from '@/lib/docs'
import { assetPath, site } from '@/lib/site'

export function DocsShell({ document, documents }: { document: PublicDoc; documents: PublicDoc[] }) {
  const index = documents.findIndex(item => item.slug === document.slug)
  const previous = index > 0 ? documents[index - 1] : null
  const next = index < documents.length - 1 ? documents[index + 1] : null
  const navigation: DocsNavigationItem[] = documents.map(({ slug, title, group }) => ({ slug, title, group }))

  return (
    <>
      <details className="docs-mobile-browser">
        <summary>Browse docs</summary>
        <DocsNavigation documents={navigation} activeSlug={document.slug} idPrefix="mobile" />
      </details>
      <div className="docs-layout">
        <aside className="docs-sidebar"><DocsNavigation documents={navigation} activeSlug={document.slug} idPrefix="desktop" /></aside>
        <article className="docs-article">
          <div className="docs-breadcrumb"><a href={assetPath('/docs')}>Docs</a><span>/</span><span>{document.group}</span></div>
          <DocsArticle markdown={document.markdown} />
          <nav className="docs-pagination" aria-label="Previous and next articles">
            {previous ? <a href={assetPath(`/docs/${previous.slug}`)}><span>Previous</span><strong>{previous.title}</strong></a> : <span />}
            {next ? <a href={assetPath(`/docs/${next.slug}`)}><span>Next</span><strong>{next.title}</strong><ArrowRight size={15} /></a> : <span />}
          </nav>
          <div className="docs-community-note">Created and maintained by <a href={site.creator.url} target="_blank" rel="noopener noreferrer">{site.creator.name}</a>, with contributions from the ThreadCells community. <a href={site.githubUrl} target="_blank" rel="noopener noreferrer"><Github size={14} /> View the repository</a></div>
        </article>
        <aside className="docs-toc" aria-label="On this page">
          <strong>On this page</strong>
          <ol>{document.headings.map(heading => <li key={heading.anchor} className={`toc-level-${heading.level}`}><a href={`#${heading.anchor}`}>{heading.text}</a></li>)}</ol>
        </aside>
      </div>
    </>
  )
}
