import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { LandingPage } from '@/app/page'
import { canonicalUrl } from '@/lib/site'
import { isLocale, localeAlternates, localeCopy, translatedLocales, type Locale } from '@/lib/locales'

export const dynamicParams = false

export function generateStaticParams() { return translatedLocales.map(locale => ({ locale })) }

export async function generateMetadata({ params }: { params: Promise<{ locale: string }> }): Promise<Metadata> {
  const { locale: value } = await params
  if (!isLocale(value) || value === 'en') return {}
  const locale: Locale = value
  const copy = localeCopy[locale]
  const canonical = canonicalUrl(`/${locale}`)
  const paths = localeAlternates('/')
  return {
    title: copy.title,
    description: copy.description,
    alternates: { canonical: canonical || undefined, languages: Object.fromEntries(Object.entries(paths).map(([language, path]) => [language, canonicalUrl(path) || path])) },
    openGraph: { title: copy.title, description: copy.description, ...(canonical ? { url: canonical } : {}) },
    twitter: { title: copy.title, description: copy.description },
  }
}

export default async function LocalizedLanding({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params
  if (!isLocale(locale) || locale === 'en') notFound()
  return <LandingPage locale={locale} />
}
