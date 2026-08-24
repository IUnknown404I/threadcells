import type { Metadata } from 'next'
import { LandingPage } from '@/app/page'
import { canonicalUrl } from '@/lib/site'
import { localeCopy } from '@/lib/locales'

const copy = localeCopy.ru
const russianUrl = canonicalUrl('/ru')
const englishUrl = canonicalUrl('/')

export const metadata: Metadata = {
  title: copy.title,
  description: copy.description,
  alternates: {
    canonical: russianUrl || undefined,
    languages: { en: englishUrl || '/', ru: russianUrl || '/ru/' },
  },
  openGraph: { title: copy.title, description: copy.description, ...(russianUrl ? { url: russianUrl } : {}) },
  twitter: { title: copy.title, description: copy.description },
}

export default function RussianLanding() { return <LandingPage locale="ru" /> }
