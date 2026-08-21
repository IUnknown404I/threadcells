import fs from 'node:fs'
import path from 'node:path'
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
}

type ManifestDocument = Omit<PublicDoc, 'description' | 'headings' | 'markdown'> & { title?: string }

const productRoot = path.resolve(process.env.THREADCELLS_PRODUCT_ROOT || path.join(process.cwd(), '..'))
const manifestPath = path.join(productRoot, 'docs', 'DOCS_MANIFEST.json')
const privateSegments = new Set(['agents', 'memory', 'handoffs', '.git'])
const repositoryFiles = new Set(['SECURITY.md', 'LICENSE', 'examples/threadcells-starter/README.md'])

function slugBase(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'section'
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

function rewriteLinks(markdown: string, source: string, slugs: Map<string, string>) {
  return markdown.replace(/\]\(([^)]+)\)/g, (whole, target: string) => {
    if (/^(?:https?:\/\/|mailto:|#)/.test(target)) return whole
    const [location, fragment] = target.split('#', 2)
    const resolved = path.resolve(path.dirname(source), location)
    const slug = slugs.get(resolved)
    if (slug) return `](/docs/${slug}${fragment ? `#${fragment}` : ''})`
    const repositoryPath = path.relative(productRoot, resolved).split(path.sep).join('/')
    if (!repositoryFiles.has(repositoryPath)) throw new Error(`Unpublished documentation link: ${repositoryPath}`)
    return `](${publicRepositoryUrl}/blob/main/${repositoryPath}${fragment ? `#${fragment}` : ''})`
  })
}

function loadDocs(): PublicDoc[] {
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8')) as { documents: ManifestDocument[] }
  const sources = new Map(manifest.documents.map(item => [safeSource(item.source), item.slug]))
  const docs = manifest.documents.map(item => {
    const source = safeSource(item.source)
    const original = fs.readFileSync(source, 'utf8')
    if (/\bTODO\b/i.test(original) || /[\u0400-\u04ff]/.test(original)) throw new Error(`Public documentation validation failed: ${item.source}`)
    const markdown = rewriteLinks(original, source, sources)
    return {
      ...item,
      title: item.title || markdownTitle(original, item.slug),
      description: description(original),
      headings: headingRows(original),
      markdown,
    }
  })
  return docs.sort((a, b) => a.order - b.order)
}

const documents = loadDocs()

export function getDocs() {
  return documents
}

export function getDoc(slug: string) {
  return documents.find(document => document.slug === slug) || null
}

export function docsByGroup() {
  const groups = new Map<string, PublicDoc[]>()
  for (const document of documents) groups.set(document.group, [...(groups.get(document.group) || []), document])
  return [...groups.entries()].map(([group, docs]) => ({ group, docs }))
}

export function docHref(slug: string) {
  return `/docs/${slug}`
}

export function slugHeading(value: string, seen: Map<string, number>) {
  const base = slugBase(value)
  const count = seen.get(base) || 0
  seen.set(base, count + 1)
  return count ? `${base}-${count}` : base
}
