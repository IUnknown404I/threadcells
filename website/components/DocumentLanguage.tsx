'use client'

import { useLayoutEffect } from 'react'
import { usePathname } from 'next/navigation'

const DOCUMENT_LOCALES = new Set(['ru', 'zh-CN', 'es', 'pt-BR', 'de', 'ja'])

function localeFromPathname(pathname: string): string {
  return pathname.split('/').find(segment => DOCUMENT_LOCALES.has(segment)) || 'en'
}

export function DocumentLanguage() {
  const pathname = usePathname()

  useLayoutEffect(() => {
    const locale = localeFromPathname(pathname)
    document.documentElement.lang = locale
    document.documentElement.dataset.threadcellsDocumentLanguage = locale
  }, [pathname])

  return null
}
