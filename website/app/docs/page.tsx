import type { Metadata } from 'next'
import { ArrowRight, Github } from '@/components/Icons'
import { DocsNavigation, type DocsNavigationItem } from '@/components/DocsNavigation'
import { canonicalUrl, assetPath, site } from '@/lib/site'
import { docsByGroup, getDocs } from '@/lib/docs'

const title = 'ThreadCells documentation'
const description = 'Install, configure, operate, secure, and troubleshoot ThreadCells using the public guide built from the same curated documentation as the product.'
const canonical = canonicalUrl('/docs')

export const metadata: Metadata = {
  title,
  description,
  ...(canonical ? { alternates: { canonical }, openGraph: { title, description, url: canonical } } : {}),
}

export default function DocsIndex() {
  const documents = getDocs()
  const groups = docsByGroup()
  const navigation: DocsNavigationItem[] = documents.map(({ slug, title: documentTitle, group }) => ({ slug, title: documentTitle, group }))

  return (
    <>
      <details className="docs-mobile-browser">
        <summary>Browse docs</summary>
        <DocsNavigation documents={navigation} idPrefix="mobile-index" />
      </details>
      <div className="docs-layout docs-index-layout">
        <aside className="docs-sidebar"><DocsNavigation documents={navigation} idPrefix="desktop-index" /></aside>
        <article className="docs-index">
          <p className="eyebrow">PUBLIC GUIDE / {String(documents.length).padStart(2, '0')} ARTICLES</p>
          <h1>Operate ThreadCells<br /><span>without repository archaeology.</span></h1>
          <p className="docs-index-lede">This is the complete public guide for the current ThreadCells product: from a first local session to providers, workflows, capacity, remote access, backups, and incident recovery.</p>
          <div className="docs-start-path" aria-label="Recommended getting started path">
            {documents.filter(document => ['overview', 'getting-started', 'installation', 'first-agent'].includes(document.slug)).map((document, index) => (
              <a key={document.slug} href={assetPath(`/docs/${document.slug}`)}><span>0{index + 1}</span><strong>{document.title}</strong><ArrowRight size={15} /></a>
            ))}
          </div>
          {groups.map(({ group, docs }) => (
            <section className="docs-group" key={group}>
              <div><h2>{group}</h2><span>{docs.length} article{docs.length === 1 ? '' : 's'}</span></div>
              <div>{docs.map(document => <a key={document.slug} href={assetPath(`/docs/${document.slug}`)}><strong>{document.title}</strong><p>{document.description}</p><span>Read article <ArrowRight size={14} /></span></a>)}</div>
            </section>
          ))}
        </article>
        <aside className="docs-toc docs-index-aside">
          <strong>Start here</strong>
          <p>Quick Setup is the fastest supported path. Concepts and architecture can wait until after your first useful agent.</p>
          <a href={assetPath('/docs/getting-started')}>Open Quick Setup <ArrowRight size={14} /></a>
          <a href={site.githubUrl} target="_blank" rel="noopener noreferrer"><Github size={14} /> GitHub repository</a>
        </aside>
      </div>
    </>
  )
}
