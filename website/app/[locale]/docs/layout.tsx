import { notFound } from 'next/navigation'
import { SiteFooter } from '@/components/SiteFooter'
import { SiteHeader } from '@/components/SiteHeader'
import { isLocale } from '@/lib/locales'

export default async function LocalizedDocsLayout({ children, params }: Readonly<{ children: React.ReactNode; params: Promise<{ locale: string }> }>) {
  const { locale } = await params
  if (!isLocale(locale) || locale === 'en') notFound()
  return <><SiteHeader locale={locale} routePath="/docs" /><main id="top" className="docs-main" lang={locale}>{children}</main><SiteFooter locale={locale} /></>
}
