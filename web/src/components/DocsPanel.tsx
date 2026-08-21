import { useEffect, useMemo, useState } from 'react'
import { ChevronLeft, ChevronRight, Copy, Search, Menu, X } from 'lucide-react'

type Heading = { level: string; text: string; anchor: string }
type Doc = { slug: string; group: string; order: number; title: string; markdown: string; headings: Heading[] }
type Bundle = { product: string; version: string; commit: string; documents: Doc[] }

const safeHref = (value: string) => /^(https?:\/\/|mailto:|#|\/docs(?:\/|$))/.test(value) ? value : '#'
const anchor = (value: string) => value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '')

function Inline({ value }: { value: string }) {
  const nodes = value.split(/(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]*\))/g)
  return <>{nodes.map((node, i) => {
    const link = /^\[([^\]]+)\]\(([^)]*)\)$/.exec(node)
    if (link) return <a key={i} className="text-emerald-300 underline underline-offset-2" href={safeHref(link[2])} target={link[2].startsWith('http') ? '_blank' : undefined} rel={link[2].startsWith('http') ? 'noreferrer' : undefined}>{link[1]}</a>
    if (node.startsWith('`')) return <code key={i} className="break-words rounded bg-gray-800 px-1.5 py-0.5 text-emerald-200">{node.slice(1, -1)}</code>
    if (node.startsWith('**')) return <strong key={i} className="font-semibold text-gray-100">{node.slice(2, -2)}</strong>
    return node
  })}</>
}

function Markdown({ markdown }: { markdown: string }) {
  const lines = markdown.replace(/<[^>]*>/g, '').split('\n')
  const out: React.ReactNode[] = []; let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (line.startsWith('```')) { const code: string[] = []; const lang = line.slice(3); i++; while (i < lines.length && !lines[i].startsWith('```')) code.push(lines[i++]); i++; const text = code.join('\n'); out.push(<pre key={i} className="group relative my-4 max-w-full overflow-x-auto rounded-lg border border-gray-700 bg-[#101622] p-4 text-sm"><button className="absolute right-2 top-2 rounded bg-gray-700 px-2 py-1 text-xs opacity-70 hover:opacity-100" onClick={() => navigator.clipboard?.writeText(text)} aria-label="Copy code"><Copy size={13}/></button><code data-language={lang}>{text}</code></pre>); continue }
    const heading = /^(#{1,4})\s+(.+)$/.exec(line)
    if (heading) { const level = heading[1].length; const id = anchor(heading[2]); const Tag = (`h${level}` as keyof JSX.IntrinsicElements); out.push(<Tag key={i} id={id} className={["text-2xl", "text-xl", "text-lg", "text-base"][level-1] + ' mt-7 mb-3 scroll-mt-28 font-semibold text-white'}><a href={'#'+id} className="hover:text-emerald-300">{heading[2]}</a></Tag>); i++; continue }
    if (/^[-*]\s+/.test(line)) { const items: string[]=[]; while(i<lines.length && /^[-*]\s+/.test(lines[i])) items.push(lines[i++].replace(/^[-*]\s+/,'')); out.push(<ul key={i} className="my-3 list-disc space-y-1 pl-6">{items.map((x,j)=><li key={j}><Inline value={x}/></li>)}</ul>); continue }
    if (/^\d+\.\s+/.test(line)) { const items: string[]=[]; while(i<lines.length && /^\d+\.\s+/.test(lines[i])) items.push(lines[i++].replace(/^\d+\.\s+/,'')); out.push(<ol key={i} className="my-3 list-decimal space-y-1 pl-6">{items.map((x,j)=><li key={j}><Inline value={x}/></li>)}</ol>); continue }
    if (line.startsWith('|') && i + 1 < lines.length && /^\|?\s*:?-+/.test(lines[i + 1])) { const rows: string[][]=[]; const cells=(value:string)=>value.replace(/^\||\|$/g,'').split('|').map(cell=>cell.trim()); const headers=cells(line); i+=2; while(i<lines.length && lines[i].startsWith('|')) rows.push(cells(lines[i++])); out.push(<div key={i} className="my-5 max-w-full overflow-x-auto rounded-lg border border-gray-700"><table className="min-w-full border-collapse text-left text-sm"><thead className="bg-gray-800/80 text-gray-100"><tr>{headers.map((cell,j)=><th key={j} className="whitespace-nowrap border-b border-gray-700 px-3 py-2 font-semibold"><Inline value={cell}/></th>)}</tr></thead><tbody>{rows.map((row,j)=><tr key={j} className="border-b border-gray-800 last:border-0">{row.map((cell,k)=><td key={k} className="px-3 py-2 align-top text-gray-300"><Inline value={cell}/></td>)}</tr>)}</tbody></table></div>); continue }
    if (/^>\s?/.test(line)) { out.push(<blockquote key={i} className="my-4 border-l-4 border-emerald-500 bg-emerald-950/20 px-4 py-3 text-gray-300"><Inline value={line.replace(/^>\s?/, '')}/></blockquote>); i++; continue }
    if (!line.trim()) { i++; continue }
    out.push(<p key={i} className="my-3 leading-7 text-gray-300"><Inline value={line}/></p>); i++
  }
  return <article className="min-w-0 max-w-none break-words">{out}</article>
}

export function DocsPanel() {
  const [bundle, setBundle] = useState<Bundle | null>(null); const [query, setQuery] = useState(''); const [menu, setMenu] = useState(false); const [pathname, setPathname] = useState(location.pathname)
  const slug = pathname.split('/').filter(Boolean)[1] || 'overview'
  useEffect(() => { fetch('/docs-bundle.json').then(r => r.ok ? r.json() : Promise.reject()).then(setBundle).catch(() => setBundle(null)) }, [])
  const document = bundle?.documents.find(d => d.slug === slug)
  const ordered = useMemo(() => [...(bundle?.documents || [])].sort((a, b) => a.order - b.order), [bundle])
  const position = ordered.findIndex(d => d.slug === slug)
  const previous = position > 0 ? ordered[position - 1] : undefined
  const next = position >= 0 && position < ordered.length - 1 ? ordered[position + 1] : undefined
  const filtered = useMemo(() => (bundle?.documents.filter(d => !query || `${d.title} ${d.markdown}`.toLowerCase().includes(query.toLowerCase())) || []).sort((a, b) => a.order - b.order), [bundle, query])
  const groups = [...new Set(filtered.map(d => d.group))]
  const select = (next: string) => { history.pushState({}, '', `/docs/${next}`); dispatchEvent(new PopStateEvent('popstate')); setMenu(false); scrollTo({ top: 0, behavior: 'smooth' }) }
  useEffect(() => { const refresh = () => setPathname(location.pathname); addEventListener('popstate', refresh); return () => removeEventListener('popstate', refresh) }, [])
  if (!bundle) return <div className="py-16 text-center text-gray-400">Loading packaged documentation…</div>
  const navigation = <aside className="w-full shrink-0 lg:w-60"><div className="relative mb-3"><Search size={16} className="absolute left-3 top-3 text-gray-500"/><input aria-label="Search documentation" className="w-full rounded-lg border border-gray-700 bg-gray-900 py-2 pl-9 pr-3 text-sm outline-none focus:border-emerald-500" value={query} onChange={e=>setQuery(e.target.value)} placeholder="Search docs" /></div>{groups.map(group=><section key={group} className="mb-4"><h2 className="mb-1 text-xs font-semibold uppercase tracking-wider text-gray-500">{group}</h2>{filtered.filter(d=>d.group===group).map(d=><button key={d.slug} onClick={()=>select(d.slug)} className={'block w-full rounded px-2 py-1.5 text-left text-sm '+(d.slug===slug?'bg-emerald-600 text-white':'text-gray-300 hover:bg-gray-800')}>{d.title}</button>)}</section>)}</aside>
  return <div className="min-w-0"><div className="mb-4 flex items-center justify-between lg:hidden"><button onClick={()=>setMenu(!menu)} className="flex items-center gap-2 rounded border border-gray-700 px-3 py-2 text-sm"><Menu size={16}/> Browse docs</button>{menu && <button onClick={()=>setMenu(false)} aria-label="Close documentation navigation"><X size={16}/></button>}</div>{menu && <div className="mb-5 rounded border border-gray-700 bg-gray-900 p-3 lg:hidden">{navigation}</div>}<div className="flex gap-8"><div className="hidden lg:block">{navigation}</div><section className="min-w-0 flex-1 rounded-xl border border-gray-800 bg-gray-900/40 p-5 sm:p-8">{document ? <><p className="mb-5 text-xs font-semibold uppercase tracking-wider text-emerald-400/80">{document.group}</p><Markdown markdown={document.markdown}/><nav aria-label="Documentation pages" className="mt-10 grid gap-3 border-t border-gray-800 pt-5 sm:grid-cols-2">{previous ? <button className="flex min-w-0 items-center gap-2 rounded-lg border border-gray-700 p-3 text-left text-sm text-gray-300 hover:border-emerald-600 hover:text-white" onClick={()=>select(previous.slug)}><ChevronLeft size={16} className="shrink-0"/><span><span className="block text-xs text-gray-500">Previous</span>{previous.title}</span></button> : <span/>}{next && <button className="flex min-w-0 items-center justify-end gap-2 rounded-lg border border-gray-700 p-3 text-right text-sm text-gray-300 hover:border-emerald-600 hover:text-white" onClick={()=>select(next.slug)}><span><span className="block text-xs text-gray-500">Next</span>{next.title}</span><ChevronRight size={16} className="shrink-0"/></button>}</nav><footer className="mt-5 text-xs text-gray-500">ThreadCells {bundle.version} · docs build {bundle.commit.slice(0,12)}</footer></> : <div><h1 className="text-2xl font-semibold">Document not found</h1><p className="mt-2 text-gray-400">This build does not publish that document.</p><button className="mt-4 text-emerald-300 underline" onClick={()=>select('overview')}>Open Start here</button></div>}</section>{document && document.headings.length > 0 && <aside className="hidden w-44 shrink-0 xl:block"><p className="mb-2 text-xs uppercase text-gray-500">On this page</p>{document.headings.map(h=><a className="mb-2 block text-xs text-gray-400 hover:text-emerald-300" style={{paddingLeft:Math.max(0,(Number(h.level)-2)*8)}} href={'#'+h.anchor} key={h.anchor}>{h.text}</a>)}</aside>}</div></div>
}
