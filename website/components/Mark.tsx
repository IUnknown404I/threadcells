import { assetPath } from '@/lib/site'

export function Mark({ compact = false, horizontal = false, locale = 'en' }: { compact?: boolean; horizontal?: boolean; locale?: 'en' | 'ru' }) {
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
        {!compact && <span className="brand-subtitle">{locale === 'ru' ? 'ПАНЕЛЬ УПРАВЛЕНИЯ' : 'CONTROL PLANE'}</span>}
      </span>
    </span>
  )
}
