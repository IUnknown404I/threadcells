import type { CSSProperties } from 'react'
import { ZoomableImage } from '@/components/ZoomableImage'
import { assetPath } from '@/lib/site'

type ProductShotProps = {
  src: string
  alt: string
  label: string
  detail: string
  className?: string
  eager?: boolean
  width?: number
  height?: number
  stateLabel?: string
}

export function ProductShot({ src, alt, label, detail, className = '', eager = false, width = 1440, height = 960, stateLabel = 'ISOLATED SYNTHETIC STATE' }: ProductShotProps) {
  const resolvedSrc = assetPath(src)
  return (
    <figure className={`product-shot ${className}`}>
      <div className="shot-chrome" aria-hidden="true">
        <span className="window-dots"><i /><i /><i /></span>
        <span>127.0.0.1 / THREADCELLS</span>
        <span>{stateLabel}</span>
      </div>
      <div className="shot-media" style={{ '--shot-aspect': `${width} / ${height}` } as CSSProperties}>
        <ZoomableImage src={resolvedSrc} alt={alt} eager={eager} width={width} height={height} />
      </div>
      <figcaption><strong>{label}</strong><span>{detail}</span></figcaption>
    </figure>
  )
}
