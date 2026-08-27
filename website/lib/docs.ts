import { createHash } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { docsPath, localeCopy, type Locale } from '@/lib/locales'
import { publicRepositoryUrl } from '@/lib/site'

export type DocHeading = { level: number; text: string; anchor: string }
export type PublicDoc = {
  slug: string
  group: string
  order: number
  source: string
  title: string
  description: string
  headings: DocHeading[]
  markdown: string
  locale: Locale
}

type ManifestDocument = Omit<PublicDoc, 'description' | 'headings' | 'markdown' | 'locale'> & { title?: string }

const productRoot = path.resolve(process.env.THREADCELLS_PRODUCT_ROOT || path.join(process.cwd(), '..'))
const manifestPath = path.join(productRoot, 'docs', 'DOCS_MANIFEST.json')
const privateSegments = new Set(['agents', 'memory', 'handoffs', '.git'])
const repositoryFiles = new Set(['SECURITY.md', 'LICENSE', 'examples/threadcells-starter/README.md'])

function slugBase(value: string) {
  return value
    .normalize('NFKD')
    .toLowerCase()
    .replace(/\p{Mark}/gu, '')
    .replace(/[^\p{Letter}\p{Number}]+/gu, '-')
    .replace(/^-|-$/g, '') || 'section'
}

function headingRows(markdown: string): DocHeading[] {
  const seen = new Map<string, number>()
  const rows: DocHeading[] = []
  for (const match of markdown.matchAll(/^(#{2,4})\s+(.+?)\s*$/gm)) {
    const base = slugBase(match[2])
    const count = seen.get(base) || 0
    seen.set(base, count + 1)
    rows.push({ level: match[1].length, text: match[2], anchor: count ? `${base}-${count}` : base })
  }
  return rows
}

function markdownTitle(markdown: string, fallback: string) {
  return markdown.match(/^#\s+(.+?)\s*$/m)?.[1] || fallback
}

function plainText(markdown: string) {
  return markdown
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/[`*_~>#]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

function description(markdown: string) {
  const paragraphs = markdown.split(/\n\s*\n/)
  const candidate = paragraphs.find(block => {
    const value = block.trim()
    return value && !/^(?:#|```|[-*+]\s|\d+\.\s|\||>)/.test(value)
  }) || ''
  const value = plainText(candidate)
  return value.length > 180 ? `${value.slice(0, 177).trimEnd()}…` : value
}

function safeSource(source: string) {
  const parts = source.split(/[\\/]/)
  if (path.isAbsolute(source) || parts.includes('..') || parts.some(part => privateSegments.has(part))) {
    throw new Error(`Unsafe public documentation source: ${source}`)
  }
  const resolved = path.resolve(productRoot, source)
  if (!resolved.startsWith(`${productRoot}${path.sep}`) || path.extname(resolved).toLowerCase() !== '.md' || !fs.existsSync(resolved)) {
    throw new Error(`Missing public documentation source: ${source}`)
  }
  return resolved
}

function sourceFingerprint(source: string) {
  return `sha256:${createHash('sha256').update(fs.readFileSync(source)).digest('hex')}`
}

function translatedMarkdown(locale: Locale, item: ManifestDocument, canonicalSource: string) {
  const translation = path.join(productRoot, 'docs', locale, `${item.slug}.md`)
  if (!fs.existsSync(translation)) throw new Error(`Missing ${locale} documentation translation: ${item.slug}`)
  const value = fs.readFileSync(translation, 'utf8')
  const match = value.match(/^---\n([\s\S]*?)\n---\n([\s\S]*)$/)
  if (!match) throw new Error(`Invalid ${locale} documentation metadata: ${item.slug}`)
  const metadata = Object.fromEntries(match[1].split('\n').map(line => {
    const separator = line.indexOf(':')
    if (separator < 1) throw new Error(`Invalid ${locale} documentation metadata: ${item.slug}`)
    return [line.slice(0, separator).trim(), line.slice(separator + 1).trim()]
  }))
  if (
    metadata.slug !== item.slug ||
    metadata.source !== item.source ||
    metadata.source_sha256 !== sourceFingerprint(canonicalSource)
  ) {
    throw new Error(`Stale or mismatched ${locale} documentation translation: ${item.slug}`)
  }
  return match[2]
}

function rewriteLinks(markdown: string, source: string, slugs: Map<string, string>, locale: Locale) {
  return markdown.replace(/\]\(([^)]+)\)/g, (whole, target: string) => {
    if (/^(?:https?:\/\/|mailto:|#)/.test(target)) return whole
    if (target.startsWith('/media/screenshots/')) {
      const media = path.join(productRoot, 'website', 'public', target.replace(/^\/+/, ''))
      if (!fs.existsSync(media)) throw new Error('Missing public documentation media: ' + target)
      return whole
    }
    const [location, fragment] = target.split('#', 2)
    const resolved = path.resolve(path.dirname(source), location)
    const slug = slugs.get(resolved)
    if (slug) return `](${docsPath(locale, slug)}${fragment ? `#${fragment}` : ''})`
    const repositoryPath = path.relative(productRoot, resolved).split(path.sep).join('/')
    if (!repositoryFiles.has(repositoryPath)) throw new Error(`Unpublished documentation link: ${repositoryPath}`)
    return `](${publicRepositoryUrl}/blob/main/${repositoryPath}${fragment ? `#${fragment}` : ''})`
  })
}

const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8')) as { documents: ManifestDocument[] }
const sources = new Map(manifest.documents.map(item => [safeSource(item.source), item.slug]))
const cache = new Map<Locale, PublicDoc[]>()
const localizationPlaceholder = /(?<![\p{L}\p{N}_])(?:TODO|TBD|TRANSLATE(?:D|\s+ME)?)(?![\p{L}\p{N}_])/u
const loremPlaceholder = /(?<![\p{L}\p{N}_])LOREM\s+IPSUM(?![\p{L}\p{N}_])/iu

function loadDocs(locale: Locale): PublicDoc[] {
  const existing = cache.get(locale)
  if (existing) return existing
  const docs = manifest.documents.map(item => {
    const source = safeSource(item.source)
    const canonical = fs.readFileSync(source, 'utf8')
    if (/\bTODO\b/i.test(canonical) || /[\u0400-\u04ff]/.test(canonical)) throw new Error(`Public documentation validation failed: ${item.source}`)
    const original = locale === 'en' ? canonical : translatedMarkdown(locale, item, source)
    if (locale !== 'en' && (localizationPlaceholder.test(original) || loremPlaceholder.test(original))) throw new Error(`Public localization validation failed: ${locale}/${item.slug}`)
    return {
      ...item,
      locale,
      group: localeCopy[locale].docsUi.groups[item.group] || item.group,
      title: locale === 'en' && item.title ? item.title : markdownTitle(original, item.slug),
      description: description(original),
      headings: headingRows(original),
      markdown: rewriteLinks(original, source, sources, locale),
    }
  }).sort((a, b) => a.order - b.order)
  cache.set(locale, docs)
  return docs
}

export function getDocs(locale: Locale = 'en') {
  return loadDocs(locale)
}

export function getDoc(slug: string, locale: Locale = 'en') {
  return loadDocs(locale).find(document => document.slug === slug) || null
}

export function docsByGroup(locale: Locale = 'en') {
  const groups = new Map<string, PublicDoc[]>()
  for (const document of loadDocs(locale)) groups.set(document.group, [...(groups.get(document.group) || []), document])
  return [...groups.entries()].map(([group, docs]) => ({ group, docs }))
}

export function docHref(slug: string, locale: Locale = 'en') {
  return docsPath(locale, slug)
}

export function slugHeading(value: string, seen: Map<string, number>) {
  const base = slugBase(value)
  const count = seen.get(base) || 0
  seen.set(base, count + 1)
  return count ? `${base}-${count}` : base
}
