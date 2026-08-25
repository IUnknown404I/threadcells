import { assetPath } from '@/lib/site'
import { localeCopy, type Locale } from '@/lib/locales'

export function Mark({ compact = false, horizontal = false, locale = 'en' }: { compact?: boolean; horizontal?: boolean; locale?: Locale }) {
  if (horizontal) {
    return (
      <span className="brand-mark-horizontal">
        <img src={assetPath('/threadcells-logo-horizontal-true-black.webp')} alt="" width="640" height="241" />
      </span>
    )
  }

  return (
    <span className={`brand-mark ${compact ? 'brand-mark-compact' : ''}`}>
      <img src={assetPath('/threadcells-symbol.webp')} alt="" width="52" height="52" />
      <span>
        <span className="brand-name">ThreadCells</span>
        {!compact && <span className="brand-subtitle">{localeCopy[locale].controlPlane}</span>}
      </span>
    </span>
  )
}
