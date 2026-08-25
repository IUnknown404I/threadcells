import { SiteFooter } from '@/components/SiteFooter'
import { SiteHeader } from '@/components/SiteHeader'

export default function DocsLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <>
      <SiteHeader routePath="/docs" />
      <main id="top" className="docs-main" lang="en">{children}</main>
      <SiteFooter />
    </>
  )
}
