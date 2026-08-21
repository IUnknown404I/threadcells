import { SiteFooter } from '@/components/SiteFooter'
import { SiteHeader } from '@/components/SiteHeader'

export default function DocsLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <>
      <SiteHeader />
      <main id="top" className="docs-main">{children}</main>
      <SiteFooter />
    </>
  )
}
