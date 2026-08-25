import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { DocsShell } from '@/components/DocsShell'
import { canonicalUrl } from '@/lib/site'
import { getDoc, getDocs } from '@/lib/docs'
import { docsPath, isLocale, localeCopy, locales, translatedLocales } from '@/lib/locales'

export const dynamicParams = false

export function generateStaticParams() {
  return translatedLocales.flatMap(locale => getDocs('en').map(document => ({ locale, slug: document.slug })))
}

export async function generateMetadata({ params }: { params: Promise<{ locale: string; slug: string }> }): Promise<Metadata> {
  const { locale, slug } = await params
  if (!isLocale(locale) || locale === 'en') return {}
  const document = getDoc(slug, locale)
  if (!document) return {}
  const title = `${document.title} — ThreadCells ${localeCopy[locale].docs}`
  const canonical = canonicalUrl(docsPath(locale, slug))
  return {
    title,
    description: document.description,
    alternates: { canonical: canonical || undefined, languages: Object.fromEntries(locales.map(next => [localeCopy[next].htmlLang, canonicalUrl(docsPath(next, slug)) || docsPath(next, slug)])) },
    ...(canonical ? { openGraph: { title, description: document.description, url: canonical } } : {}),
  }
}

export default async function LocalizedDocPage({ params }: { params: Promise<{ locale: string; slug: string }> }) {
  const { locale, slug } = await params
  if (!isLocale(locale) || locale === 'en') notFound()
  const document = getDoc(slug, locale)
  if (!document) notFound()
  return <DocsShell document={document} documents={getDocs(locale)} />
}
