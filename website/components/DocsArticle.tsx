import { Children, isValidElement, type ReactElement, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CodeBlock } from '@/components/CodeBlock'
import { ZoomableImage } from '@/components/ZoomableImage'
import { assetPath } from '@/lib/site'
import { slugHeading } from '@/lib/docs'
import { localeCopy, type Locale } from '@/lib/locales'

function textContent(value: ReactNode): string {
  return Children.toArray(value).map(child => typeof child === 'string' || typeof child === 'number' ? String(child) : isValidElement<{ children?: ReactNode }>(child) ? textContent(child.props.children) : '').join('')
}

function external(href: string) {
  return /^(?:https?:\/\/|mailto:)/.test(href)
}

export function DocsArticle({ markdown, locale }: { markdown: string; locale: Locale }) {
  const seen = new Map<string, number>()
  const heading = (level: 2 | 3 | 4) => function Heading({ children }: { children?: ReactNode }) {
    const text = textContent(children)
    const id = slugHeading(text, seen)
    const Tag = `h${level}` as const
    return <Tag id={id}><a href={`#${id}`} aria-label={`${localeCopy[locale].docsUi.linkTo} ${text}`}>{children}</a></Tag>
  }

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        h2: heading(2),
        h3: heading(3),
        h4: heading(4),
        a: ({ href = '', children }) => {
          const resolved = href.startsWith('/') ? assetPath(href) : href
          return <a href={resolved} {...(external(href) ? { target: '_blank', rel: 'noopener noreferrer' } : {})}>{children}</a>
        },
        pre: ({ children }) => {
          const child = Children.only(children) as ReactElement<{ children?: ReactNode; className?: string }>
          const code = String(child.props.children || '').replace(/\n$/, '')
          const language = child.props.className?.match(/language-([^\s]+)/)?.[1]
          return <CodeBlock code={code} language={language} />
        },
        img: ({ src = '', alt = '' }) => {
          const value = typeof src === 'string' ? src : ''
          return <ZoomableImage src={value.startsWith('/') ? assetPath(value) : value} alt={alt} width={1440} height={960} className="docs-image" />
        },
      }}
    >
      {markdown}
    </ReactMarkdown>
  )
}
