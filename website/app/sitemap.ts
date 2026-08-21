import type { MetadataRoute } from 'next'
import { getDocs } from '@/lib/docs'
import { site } from '@/lib/site'

export const dynamic = 'force-static'

export default function sitemap(): MetadataRoute.Sitemap {
  if (!site.siteUrl) return []
  return [
    { url: site.siteUrl, changeFrequency: 'monthly', priority: 1 },
    { url: `${site.siteUrl}/docs`, changeFrequency: 'monthly', priority: 0.9 },
    ...getDocs().map(document => ({ url: `${site.siteUrl}/docs/${document.slug}`, changeFrequency: 'monthly' as const, priority: document.group === 'Getting started' ? 0.85 : 0.7 })),
  ]
}
