import type { MetadataRoute } from 'next'
import { getDocs } from '@/lib/docs'
import { docsPath, locales, localizedPath } from '@/lib/locales'
import { site } from '@/lib/site'

export const dynamic = 'force-static'

export default function sitemap(): MetadataRoute.Sitemap {
  if (!site.siteUrl) return []
  return locales.flatMap(locale => [
    { url: `${site.siteUrl}${localizedPath(locale) === '/' ? '' : localizedPath(locale)}`, changeFrequency: 'monthly' as const, priority: locale === 'en' ? 1 : 0.9 },
    { url: `${site.siteUrl}${docsPath(locale)}`, changeFrequency: 'monthly' as const, priority: 0.9 },
    ...getDocs(locale).map(document => ({ url: `${site.siteUrl}${docsPath(locale, document.slug)}`, changeFrequency: 'monthly' as const, priority: document.group === 'Getting started' ? 0.85 : 0.7 })),
  ])
}
