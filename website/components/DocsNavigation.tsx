'use client'

import { useMemo, useState } from 'react'
import { assetPath } from '@/lib/site'

export type DocsNavigationItem = { slug: string; title: string; group: string }

export function DocsNavigation({ documents, activeSlug, idPrefix }: { documents: DocsNavigationItem[]; activeSlug?: string; idPrefix: string }) {
  const [query, setQuery] = useState('')
  const normalized = query.trim().toLowerCase()
  const groups = useMemo(() => {
    const filtered = normalized ? documents.filter(document => `${document.title} ${document.group}`.toLowerCase().includes(normalized)) : documents
    const result = new Map<string, DocsNavigationItem[]>()
    for (const document of filtered) result.set(document.group, [...(result.get(document.group) || []), document])
    return [...result.entries()]
  }, [documents, normalized])

  return (
    <div className="docs-navigation">
      <label htmlFor={`${idPrefix}-docs-search`}>Search documentation</label>
      <input id={`${idPrefix}-docs-search`} type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Search articles…" autoComplete="off" />
      <nav aria-label="Documentation articles">
        {groups.map(([group, docs]) => (
          <section key={group}>
            <h2>{group}</h2>
            <ul>{docs.map(document => <li key={document.slug}><a href={assetPath(`/docs/${document.slug}`)} aria-current={document.slug === activeSlug ? 'page' : undefined}>{document.title}</a></li>)}</ul>
          </section>
        ))}
        {groups.length === 0 && <p className="docs-search-empty">No matching articles.</p>}
      </nav>
    </div>
  )
}
