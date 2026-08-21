'use client'

import { useEffect, useState } from 'react'
import { Check, Copy } from '@/components/Icons'

export function CodeBlock({ code, language }: { code: string; language?: string }) {
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!copied) return
    const timeout = window.setTimeout(() => setCopied(false), 1800)
    return () => window.clearTimeout(timeout)
  }, [copied])

  const copy = async () => {
    await navigator.clipboard.writeText(code)
    setCopied(true)
  }

  return (
    <div className="docs-code-block">
      <div className="docs-code-toolbar">
        <span>{language || 'text'}</span>
        <button type="button" onClick={() => void copy()} aria-label="Copy code block">{copied ? <Check size={14} /> : <Copy size={14} />}{copied ? 'Copied' : 'Copy'}</button>
      </div>
      <pre><code>{code}</code></pre>
    </div>
  )
}
