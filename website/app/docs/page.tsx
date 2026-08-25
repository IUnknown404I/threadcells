import type { Metadata } from 'next'
import { DocsIndex } from '@/components/DocsIndex'
import { canonicalUrl } from '@/lib/site'
import { localeAlternates, localeCopy, locales } from '@/lib/locales'

const copy = localeCopy.en
const canonical = canonicalUrl('/docs')

export const metadata: Metadata = {
  title: `ThreadCells ${copy.docs}`,
  description: copy.docsUi.indexDescription,
  alternates: { canonical: canonical || undefined, languages: Object.fromEntries(locales.map(locale => [localeCopy[locale].htmlLang, canonicalUrl(localeAlternates('/docs')[localeCopy[locale].htmlLang]) || localeAlternates('/docs')[localeCopy[locale].htmlLang]])) },
  openGraph: { title: `ThreadCells ${copy.docs}`, description: copy.docsUi.indexDescription, ...(canonical ? { url: canonical } : {}) },
}

export default function DocsIndexPage() { return <DocsIndex locale="en" /> }
