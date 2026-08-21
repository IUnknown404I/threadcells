import type { Metadata, Viewport } from 'next'
import { GeistMono } from 'geist/font/mono'
import { GeistSans } from 'geist/font/sans'
import { absoluteAssetUrl, assetPath, site } from '@/lib/site'
import './globals.css'

const socialImage = absoluteAssetUrl('/media/threadcells-social.png')

export const metadata: Metadata = {
  title: site.title,
  description: site.description,
  applicationName: site.name,
  authors: [{ name: site.creator.name, url: site.creator.url }],
  creator: site.creator.name,
  category: 'developer tools',
  keywords: [
    'coding agents',
    'CLI coding agents',
    'self-hosted coding agent orchestration',
    'coding-agent operations console',
    'managed worktrees',
    'agent control plane',
  ],
  ...(site.siteUrl
    ? {
        metadataBase: new URL(site.siteUrl),
        alternates: { canonical: site.siteUrl },
      }
    : {}),
  openGraph: {
    type: 'website',
    title: site.title,
    description: site.description,
    siteName: site.name,
    ...(site.siteUrl ? { url: site.siteUrl } : {}),
    ...(socialImage ? { images: [{ url: socialImage, width: 1200, height: 630, alt: 'ThreadCells coding-agent operations console' }] } : {}),
  },
  twitter: {
    card: 'summary_large_image',
    title: site.title,
    description: site.description,
    ...(socialImage ? { images: [socialImage] } : {}),
  },
  icons: {
    icon: [
      { url: assetPath('/favicon.ico') },
      { url: assetPath('/favicon-32x32.png'), sizes: '32x32', type: 'image/png' },
      { url: assetPath('/favicon-16x16.png'), sizes: '16x16', type: 'image/png' },
    ],
    apple: [{ url: assetPath('/apple-touch-icon.png'), sizes: '180x180', type: 'image/png' }],
  },
  manifest: assetPath('/site.webmanifest'),
  robots: { index: true, follow: true },
}

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  colorScheme: 'dark',
  themeColor: '#070a08',
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body>{children}</body>
    </html>
  )
}
