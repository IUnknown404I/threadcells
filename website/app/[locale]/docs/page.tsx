import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { DocsIndex } from '@/components/DocsIndex'
import { canonicalUrl } from '@/lib/site'
import { docsPath, isLocale, localeCopy, locales, translatedLocales } from '@/lib/locales'

export const dynamicParams = false

export function generateStaticParams() {
  return translatedLocales.map(locale => ({ locale }))
}

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const { locale } = await params
  if (!isLocale(locale) || locale === 'en') return {}
  const copy = localeCopy[locale]
  const canonical = canonicalUrl(docsPath(locale))
  return {
    title: `ThreadCells — ${copy.docs}`,
    description: copy.docsUi.indexDescription,
    alternates: { canonical: canonical || undefined, languages: Object.fromEntries(locales.map(next => [localeCopy[next].htmlLang, canonicalUrl(docsPath(next)) || docsPath(next)])) },
    openGraph: { title: `ThreadCells — ${copy.docs}`, description: copy.docsUi.indexDescription, ...(canonical ? { url: canonical } : {}) },
  }
}

export default async function LocalizedDocsIndex({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params
  if (!isLocale(locale) || locale === 'en') notFound()
  return <DocsIndex locale={locale} />
}
