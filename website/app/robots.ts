import type { MetadataRoute } from 'next'
import { site } from '@/lib/site'

export const dynamic = 'force-static'

export default function robots(): MetadataRoute.Robots {
  return {
    rules: { userAgent: '*', allow: '/' },
    ...(site.siteUrl ? { sitemap: `${site.siteUrl}/sitemap.xml`, host: site.siteUrl } : {}),
  }
}
