import { assetPath } from '@/lib/site'

export function Mark({ compact = false, horizontal = false }: { compact?: boolean; horizontal?: boolean }) {
  if (horizontal) {
    return (
      <span className="brand-mark-horizontal">
        <img src={assetPath('/threadcells-logo-horizontal.webp')} alt="" width="640" height="241" />
      </span>
    )
  }

  return (
    <span className={`brand-mark ${compact ? 'brand-mark-compact' : ''}`}>
      <img src={assetPath('/threadcells-symbol.webp')} alt="" width="52" height="52" />
      <span>
        <span className="brand-name">ThreadCells</span>
        {!compact && <span className="brand-subtitle">CONTROL PLANE</span>}
      </span>
    </span>
  )
}
