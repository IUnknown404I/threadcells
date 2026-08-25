import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { DocsShell } from '@/components/DocsShell'
import { canonicalUrl } from '@/lib/site'
import { docsPath, localeCopy, locales } from '@/lib/locales'
import { getDoc, getDocs } from '@/lib/docs'

export const dynamicParams = false

export function generateStaticParams() {
  return getDocs().map(document => ({ slug: document.slug }))
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const { slug } = await params
  const document = getDoc(slug)
  if (!document) return {}
  const title = `${document.title} — ThreadCells Docs`
  const canonical = canonicalUrl(`/docs/${slug}`)
  return {
    title,
    description: document.description,
    alternates: { canonical: canonical || undefined, languages: Object.fromEntries(locales.map(locale => [localeCopy[locale].htmlLang, canonicalUrl(docsPath(locale, slug)) || docsPath(locale, slug)])) },
    ...(canonical ? { openGraph: { title, description: document.description, url: canonical } } : {}),
  }
}

export default async function DocPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params
  const document = getDoc(slug)
  if (!document) notFound()
  return <DocsShell document={document} documents={getDocs()} />
}
